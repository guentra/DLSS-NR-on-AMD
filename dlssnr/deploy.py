"""Conservative, per-game deployment; no probing, Steam edits, or game execution.

The caller validates game/Proton/HIP compatibility. This module verifies assets
and performs journalled file transactions. Bash is needed only by launch.sh.
Native bridge: retained per-game AND in a private content-addressed XDG store;
the latter avoids the ELF loader's unquotable whitespace-separated LD_PRELOAD.
Only a trusted local user may write the game directory. The journal is not a
cryptographic authority against that same user forging both files and hashes.
"""
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import stat
import tempfile

DLLS = ('version.dll', 'd3d12.dll', 'd3d12core.dll', 'amdhip64_7.dll')
BRIDGE = 'libdlssnr_hip_bridge.so'
INI = 'dlssnr_on_amd.ini'
WEIGHTS = 'dlssnr_on_amd_weights.bin'
STORE = '.dlssnr-linux'
TARGETS = DLLS + (INI, WEIGHTS, STORE + '/runtime/' + BRIDGE, STORE + '/launch.sh')
HASH = re.compile(r'[0-9a-f]{64}\Z')
NOTES = ['Injection requires explicit risk acceptance; no anti-cheat bypass.',
         'Native logs: .dlssnr-linux/logs/hip.log; PE logs retain legacy /tmp locations.',
         'Bridge cache is shared and retained on uninstall. LD_PRELOAD cache paths cannot contain whitespace or colon.',
         'Select the validated Proton in Steam yourself; paste launch_options manually.']


def _safe(path, *, directory=False, missing=False):
    """Reject symlinks in every component, not just the final filename."""
    path = Path(os.path.abspath(path))
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        try:
            info = current.lstat()
        except FileNotFoundError:
            if missing:
                return path
            raise RuntimeError(f'Missing path: {current}') from None
        wanted_dir = current != path or directory
        if stat.S_ISLNK(info.st_mode) or not (stat.S_ISDIR(info.st_mode) if wanted_dir else stat.S_ISREG(info.st_mode)):
            raise RuntimeError(f'Unsafe symlink or wrong path type: {current}')
    return path


def _exe(exe):
    path = _safe(exe)
    if path.suffix.lower() != '.exe':
        raise RuntimeError('Expected a game .exe')
    return path.parent.resolve() / path.name


def _open(path):
    path = _safe(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    if not stat.S_ISREG(os.fstat(fd).st_mode):
        os.close(fd)
        raise RuntimeError(f'Not a regular file: {path}')
    return os.fdopen(fd, 'rb')


def _digest(path):
    with _open(path) as stream:
        digest = hashlib.sha256()
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
        return digest.hexdigest()


def _bytes(path):
    with _open(path) as stream:
        return stream.read()


def _sync(path):
    fd = os.open(_safe(path, directory=True), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


@contextmanager
def _lock(directory):
    # Lock the existing canonical directory inode: no stale claim, no lock file
    # unlink race, no per-game debris. Linux flock supports directory fds.
    fd = os.open(_safe(directory, directory=True), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError('Another deployment transaction is running') from None
        yield
    finally:
        os.close(fd)


def _atomic_copy(source, target, expected=None, mode=None, staging=None):
    target = _safe(target, missing=True)
    staging = _safe(staging or target.parent, directory=True)
    fd, temporary = tempfile.mkstemp(prefix='.copy-', dir=staging)
    temporary = Path(temporary)
    try:
        with _open(source) as src, os.fdopen(fd, 'wb') as dst:
            fd = -1
            info = os.fstat(src.fileno())
            digest = hashlib.sha256()
            for block in iter(lambda: src.read(1024 * 1024), b''):
                digest.update(block)
                dst.write(block)
            if expected is not None and digest.hexdigest() != expected:
                raise RuntimeError(f'Hash changed during copy: {source}')
            os.fchmod(dst.fileno(), (stat.S_IMODE(info.st_mode) & 0o777) if mode is None else mode)
            dst.flush()
            os.fsync(dst.fileno())
        _safe(target, missing=True)
        os.replace(temporary, target)
        _sync(target.parent)
        if staging != target.parent:
            _sync(staging)
    finally:
        if fd != -1:
            os.close(fd)
        if temporary.exists():
            temporary.unlink()


def _atomic_bytes(target, data, mode=0o600):
    target = _safe(target, missing=True)
    fd, name = tempfile.mkstemp(prefix='.write-', dir=target.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            os.fchmod(stream.fileno(), mode)
            stream.flush()
            os.fsync(stream.fileno())
        _safe(target, missing=True)
        os.replace(name, target)
        _sync(target.parent)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def _json(path):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise RuntimeError(f'duplicate JSON key: {key}')
            result[key] = value
        return result
    try:
        if _safe(path).stat().st_size > 1024 * 1024:
            raise RuntimeError('Manifest too large')
        return json.loads(_bytes(path), object_pairs_hook=pairs)
    except (ValueError, UnicodeError) as exc:
        raise RuntimeError(f'Invalid manifest: {exc}') from exc


def _journal(store, data):
    _atomic_bytes(store / 'manifest.json', json.dumps(data, sort_keys=True, indent=2).encode() + b'\n')


def _cmdline_matches(args, basename):
    def base(value):
        return value.replace('\\', '/').rsplit('/', 1)[-1].casefold()
    if not args:
        return False
    if base(args[0]) == basename.casefold():
        return True
    # Do not search arbitrary argv: installer --exe and env assignments are not games.
    return (base(args[0]) in ('wine', 'wine64', 'wine-preloader', 'wine64-preloader')
            and len(args) > 1 and base(args[1]) == basename.casefold())


def running_game(exe):
    for proc in Path('/proc').iterdir():
        if not proc.name.isdecimal() or int(proc.name) == os.getpid():
            continue
        try:
            args = (proc / 'cmdline').read_bytes().decode('utf-8', 'surrogateescape').rstrip('\0').split('\0')
            if _cmdline_matches(args, Path(exe).name):
                return True
        except (OSError, ValueError):
            continue
    return False


def _weights(path):
    with _open(path) as stream:
        size = os.fstat(stream.fileno()).st_size
        if stream.read(8) != b'DLSSNRW1' or size <= 8:
            raise RuntimeError('Invalid weights: expected DLSSNRW1 header and nonempty payload')
    # Deliberately no tensor/version claims: the parent owns full validation.
    return {'bytes': size, 'sha256': _digest(path)}


def _ini(data, index):
    try:
        text = data.decode('utf-8')
    except UnicodeError as exc:
        raise RuntimeError('NR INI must be UTF-8') from exc
    newline = '\r\n' if '\r\n' in text else '\n'
    values = {'Enabled': '1', 'Inline': '1', 'Interop': '1', 'HipDevice': str(index)}
    lines = text.splitlines(keepends=True)
    start = None
    end = len(lines)
    for i, line in enumerate(lines):
        section = re.match(r'^\s*\[([^]]+)\]', line)
        if section:
            if section[1].casefold() == 'dlssnronamd':
                if start is not None:
                    raise RuntimeError('duplicate NR INI section')
                start = i + 1
            elif start is not None and end == len(lines):
                end = i
    if start is None:
        if lines and not lines[-1].endswith(('\n', '\r')):
            lines[-1] += newline
        lines.append('[DlssNrOnAmd]' + newline)
        start = end = len(lines)
    seen = set()
    lookup = {key.casefold(): key for key in values}
    for i in range(start, end):
        match = re.match(r'^(\s*([A-Za-z][A-Za-z0-9_]*)\s*=\s*)([^;#\r\n]*)(.*?)(\r?\n)?$', lines[i])
        if match and match[2].casefold() in lookup:
            key = lookup[match[2].casefold()]
            if key in seen:
                raise RuntimeError(f'duplicate INI key: {key}')
            seen.add(key)
            suffix_space = match[3][len(match[3].rstrip()):]
            lines[i] = match[1] + values[key] + suffix_space + match[4] + (match[5] or '')
    additions = [key + '=' + value + newline for key, value in values.items() if key not in seen]
    if additions and end and not lines[end - 1].endswith(('\r', '\n')):
        lines[end - 1] += newline
    lines[end:end] = additions
    return ''.join(lines).encode('utf-8')


def _mount(path, loader=False):
    value = str(Path(path).absolute())
    if any(c in value for c in ':\n\r\0') or (loader and any(c.isspace() for c in value)):
        raise RuntimeError('Unsupported mount/LD_PRELOAD path: choose XDG_DATA_HOME without whitespace or colon for bridge cache')
    return value


def _cache_path(digest):
    base = Path(os.environ.get('XDG_DATA_HOME') or Path.home() / '.local/share')
    if not base.is_absolute():
        raise RuntimeError('XDG_DATA_HOME must be absolute')
    result = base / 'dlssnr-linux/native' / digest / 'bridge.so'
    _mount(result, loader=True)
    _safe(result, missing=True)
    return result


def _cache(source, digest, cache):
    # Existing ancestors must not be symlinks; the app's own subtree is private.
    for parent in reversed(cache.parent.parents):
        if not parent.exists():
            parent.mkdir(mode=0o700)
        _safe(parent, directory=True)
    cache.parent.mkdir(mode=0o700, exist_ok=True)
    for parent in (cache.parent, cache.parent.parent, cache.parent.parent.parent):
        info = _safe(parent, directory=True).stat()
        if info.st_uid != os.getuid() or info.st_mode & 0o022:
            raise RuntimeError(f'Insecure shared bridge cache: {parent}')
    with _lock(cache.parent):
        if cache.exists():
            info = _safe(cache).stat()
            if info.st_uid != os.getuid() or info.st_mode & 0o022 or info.st_nlink != 1:
                raise RuntimeError('Insecure bridge cache file')
            if _digest(cache) != digest:
                raise RuntimeError('Bridge cache hash mismatch; refusing overwrite')
        else:
            _atomic_copy(source, cache, digest, mode=0o600)


def _wrapper(exe, runtime, gpu, cache, bridge_hash=None):
    library = _mount(runtime['library'])
    if not Path(runtime['library']).is_absolute():
        raise RuntimeError('HIP library must be absolute')
    mounts = list(runtime.get('mount_roots', [])) + [str(exe.parent), str(cache.parent), str(Path(library).parent)]
    roots = runtime.get('roots', [])
    if not isinstance(roots, (list, tuple)) or not isinstance(runtime.get('library_dirs', []), (list, tuple)):
        raise RuntimeError('runtime roots/library_dirs must be lists')
    mounts += list(roots) + list(runtime.get('library_dirs', []))
    if runtime.get('root'):
        mounts.append(runtime['root'])
    mounts = list(dict.fromkeys(_mount(root) for root in mounts))
    q = shlex.quote
    assignments = {
        'DLSSNR_HIP_LIBRARY': library,
        'DLSSNR_HIP_LOG': str(exe.parent / STORE / 'logs/hip.log'),
        'DXVK_FILTER_DEVICE_NAME': gpu['name'],
        'VKD3D_FILTER_DEVICE_NAME': gpu['name'],
        'DLSSNR_NOPOSTHIST': '1',
        'VKD3D_NR_FLAG_HASH': '40bbb9064a7c18fb',
        'VKD3D_DEBUG': 'info',
        'VKD3D_LOG_FILE': 'Z:' + str(exe.parent / STORE / 'logs/vkd3d.log'),
    }
    lines = ['#!/bin/bash', 'set -e', '# Generated: no eval, no global Steam changes.']
    # Check bytes before exporting LD_PRELOAD: ld.so otherwise ignores missing DSOs.
    for path, digest in ((cache, bridge_hash or _digest(cache)),
                         (Path(library), _digest(Path(library)))):
        lines += [
            'required=' + q(str(path)),
            'if [[ ! -f "$required" || ! -r "$required" ]] || '
            '! actual=$(sha256sum < "$required") || '
            '[[ "${actual%% *}" != ' + q(digest) + ' ]]; then',
            '  printf "DLSSNR: required bridge/runtime missing or changed: %s; restore it or reinstall before launching.\\n" "$required" >&2',
            '  exit 1', 'fi',
        ]
    lines += ['export ' + key + '=' + q(value) for key, value in assignments.items()]
    lines += ['export STEAM_COMPAT_MOUNTS="${STEAM_COMPAT_MOUNTS:+$STEAM_COMPAT_MOUNTS:}"' + q(':'.join(mounts))]
    lines += ['export PRESSURE_VESSEL_FILESYSTEMS_RO="${PRESSURE_VESSEL_FILESYSTEMS_RO:+$PRESSURE_VESSEL_FILESYSTEMS_RO:}"' + q(':'.join(mounts))]
    # Drop just our names, including names in grouped overrides; preserve the rest.
    lines += [
        'kept=()', 'IFS=";" read -r -a entries <<< "${WINEDLLOVERRIDES-}"',
        'for entry in "${entries[@]}"; do',
        '  [[ "$entry" == *=* ]] || continue',
        '  names=${entry%%=*}; value=${entry#*=}',
        '  IFS="," read -r -a names_array <<< "$names"',
        '  for name in "${names_array[@]}"; do',
        '    clean=${name//[[:space:]]/}; clean=${clean,,}; clean=${clean#\\*}; clean=${clean%.dll}',
        '    case "$clean" in version|amdhip64_7|d3d12|d3d12core|d3dcompiler_47) ;;',
        '      *) kept+=("$name=$value") ;;', '    esac', '  done', 'done',
        'saved=$(IFS=";"; printf "%s" "${kept[*]}")',
        'export WINEDLLOVERRIDES="${saved:+$saved;}version=n,b;amdhip64_7=n;d3d12=n;d3d12core=n;d3dcompiler_47=b"',
        'export LD_PRELOAD=' + q(str(cache)) + '"${LD_PRELOAD:+:$LD_PRELOAD}"',
        'exec "$@"', '',
    ]
    return '\n'.join(lines).encode()


def _entry(path, installed, mode, preserve=False):
    path = _safe(path, missing=True)
    exists = path.exists()
    return {'sha256': installed, 'mode': mode,
            'original': _digest(path) if exists else None,
            'original_mode': stat.S_IMODE(path.stat().st_mode) & 0o777 if exists else None,
            'preserve': preserve, 'touched': False}


def _load(exe):
    store = _safe(exe.parent / STORE, directory=True)
    if not (store / 'manifest.json').exists():
        raise RuntimeError('Missing transaction manifest; run uninstall to recover an empty claim, otherwise review store manually')
    data = _json(store / 'manifest.json')
    def invalid():
        raise RuntimeError('Unsafe or tampered deployment manifest; refusing mutation')
    if type(data) is not dict or set(data) != {'schema', 'exe', 'state', 'files', 'request', 'cache', 'undo'}:
        invalid()
    if type(data['schema']) is not int or data['schema'] != 1 or data['exe'] != str(exe):
        invalid()
    if data['state'] not in ('preparing', 'installing', 'installed', 'uninstalling', 'removed'):
        invalid()
    if type(data['files']) is not dict or set(data['files']) != set(TARGETS):
        invalid()
    if type(data['request']) is not str or not HASH.fullmatch(data['request']):
        invalid()
    if type(data['cache']) is not str or not Path(data['cache']).is_absolute():
        invalid()
    _mount(data['cache'], loader=True)
    if type(data['undo']) is not dict or not set(data['undo']).issubset(TARGETS):
        invalid()
    for name, item in data['files'].items():
        if type(item) is not dict or set(item) != {'sha256', 'mode', 'original', 'original_mode', 'preserve', 'touched'}:
            invalid()
        for field in ('sha256', 'original'):
            if item[field] is None and field == 'original':
                continue
            if type(item[field]) is not str or not HASH.fullmatch(item[field]):
                invalid()
        for field in ('mode', 'original_mode'):
            if item[field] is None and field == 'original_mode' and item['original'] is None:
                continue
            if type(item[field]) is not int or not 0 <= item[field] <= 0o777:
                invalid()
        if type(item['preserve']) is not bool or type(item['touched']) is not bool:
            invalid()
        if (item['original'] is None) != (item['original_mode'] is None):
            invalid()
        if data['state'] == 'installed' and not item['preserve'] and not item['touched']:
            invalid()
        if data['state'] == 'preparing' and item['touched']:
            invalid()
        if item['preserve'] and (name != WEIGHTS or item['original'] != item['sha256'] or item['touched']):
            invalid()
        if name.startswith(STORE + '/') and item['original'] is not None:
            invalid()
        _safe(exe.parent / name, missing=True)
        _safe(store / 'backups' / Path(name).name, missing=True)
    for name, item in data['undo'].items():
        if type(item) is not dict or set(item) != {'sha256', 'mode'}:
            invalid()
        if item['sha256'] is not None and (type(item['sha256']) is not str or not HASH.fullmatch(item['sha256'])):
            invalid()
        if type(item['mode']) is not int or not 0 <= item['mode'] <= 0o777:
            invalid()
        _safe(store / 'undo' / Path(name).name, missing=True)
    return data


def _verify(exe, data, *, uninstall=False):
    store = exe.parent / STORE
    for name, item in data['files'].items():
        target = _safe(exe.parent / name, missing=True)
        current = _digest(target) if target.exists() else None
        if data['state'] == 'removed':
            if not item['preserve'] and current != item['original']:
                raise RuntimeError(f'Removal not complete: {name}; refusing to discard backups')
            continue
        if item['original'] is not None and not item['preserve'] and (item['touched'] or data['state'] == 'installed'):
            backup = store / 'backups' / Path(name).name
            if not backup.exists() or _digest(backup) != item['original']:
                raise RuntimeError(f'Original backup changed or missing: {name}')
        if item['preserve']:
            if not uninstall and current != item['sha256']:
                raise RuntimeError(f'Preserved weights changed: {name}')
            continue
        if data['state'] == 'installed':
            if uninstall and name == INI:
                continue  # The native overlay routinely rewrites this file.
            if current is not None and stat.S_IMODE(target.stat().st_mode) != item['mode']:
                raise RuntimeError(f'Deployed file mode changed: {name}')
            allowed = {item['sha256']}
        else:
            allowed = {item['original'], item['sha256']} if item['touched'] else {item['original']}
            if name == INI and uninstall and item['touched']:
                continue
        if current not in allowed:
            raise RuntimeError(f'Deployed file changed: {name}; refusing to clobber user changes')


def _restore(exe, data):
    store = exe.parent / STORE
    for name, item in reversed(list(data['files'].items())):
        if not item['touched'] or item['preserve']:
            continue
        target = _safe(exe.parent / name, missing=True)
        if item['original'] is None:
            if target.exists():
                target.unlink()
                _sync(target.parent)
        else:
            _atomic_copy(store / 'backups' / Path(name).name, target,
                         item['original'], item['original_mode'], store / 'stage')


def _cleanup(store, *, check_only=False):
    """Only known owned files; never rmtree or use paths from the journal."""
    _safe(store, directory=True)
    fixed = {'manifest.json', 'launch.sh'}
    subdirs = {'backups', 'stage', 'runtime', 'logs', 'undo'}
    # Preflight entire small tree before deleting anything; unknown data is retained.
    files = []
    dirs = []
    for child in store.iterdir():
        if child.name in subdirs:
            _safe(child, directory=True)
            dirs.append(child)
            for entry in child.iterdir():
                _safe(entry)
                allowed = (entry.name in {Path(name).name for name in TARGETS}
                           if child.name in ('backups', 'stage', 'undo') else
                           entry.name in ({BRIDGE} if child.name == 'runtime' else {'hip.log', 'vkd3d.log'}))
                if child.name == 'stage' and entry.name.startswith(('.copy-', '.write-')):
                    allowed = True
                if not allowed:
                    raise RuntimeError(f'Unknown file in deployment store, retain and review: {entry}')
                files.append(entry)
        else:
            _safe(child)
            if child.name not in fixed and not child.name.startswith('.write-'):
                raise RuntimeError(f'Unknown file in deployment store: {child}')
            files.append(child)
    if check_only:
        return
    # Keep the durable removed journal until all child directories are gone.
    manifest = store / 'manifest.json'
    for path in files:
        if path == manifest:
            continue
        path.unlink()
    for path in dirs:
        path.rmdir()
    _sync(store)
    if manifest.exists():
        manifest.unlink()
        _sync(store)
    store.rmdir()
    _sync(store.parent)


def _status(exe):
    store = exe.parent / STORE
    if not store.exists() and not store.is_symlink():
        return {'installed': False, 'valid': False, 'pending': False, 'notes': [], 'launch_options': None}
    try:
        data = _load(exe)
        if data['state'] != 'installed':
            return {'installed': False, 'valid': False, 'pending': True,
                    'notes': ['Pending transaction: run uninstall for recovery.'], 'launch_options': None}
        _verify(exe, data)
        bridge_hash = data['files'][STORE + '/runtime/' + BRIDGE]['sha256']
        if _digest(Path(data['cache'])) != bridge_hash:
            raise RuntimeError('Bridge cache changed')
        return {'installed': True, 'valid': True, 'pending': False, 'notes': list(NOTES),
                'launch_options': shlex.quote(str(store / 'launch.sh')) + ' %command%'}
    except (RuntimeError, OSError) as exc:
        return {'installed': True, 'valid': False, 'pending': True,
                'notes': [str(exc)], 'launch_options': None}


def status_game(exe):
    exe = _exe(exe)
    with _lock(exe.parent):
        return _status(exe)


def install_game(exe, package_root, runtime, gpu, proton, weights, *,
                 acknowledge_risk=False, replace_existing=False, dry_run=False):
    if not acknowledge_risk:
        raise RuntimeError('Explicit acknowledge_risk=True is required before injection; anti-cheat risk is not bypassed')
    exe = _exe(exe)
    with _lock(exe.parent):
        if running_game(exe):
            raise RuntimeError('Game is running; close it before deployment')
        store = exe.parent / STORE
        prior = None
        if store.exists() or store.is_symlink():
            prior = _load(exe)
            if prior['state'] != 'installed':
                raise RuntimeError('Pending transaction: run uninstall for recovery before install')
            _verify(exe, prior)
        if type(gpu.get('index')) is not int or gpu['index'] < 0 or not isinstance(gpu.get('name'), str) or not gpu['name']:
            raise RuntimeError('Select an explicit GPU index and name')
        assets = _safe(Path(package_root) / 'assets', directory=True)
        manifest = _json(assets / 'manifest.json')
        hashes = manifest.get('files', manifest) if type(manifest) is dict else None
        if type(hashes) is not dict:
            raise RuntimeError('Invalid asset manifest files map')
        for name in DLLS + (BRIDGE,):
            if type(hashes.get(name)) is not str or not HASH.fullmatch(hashes[name]) or _digest(assets / name) != hashes[name]:
                raise RuntimeError(f'Asset hash mismatch: {name}')
        weights = _safe(weights)
        weight_info = _weights(weights)
        cache = _cache_path(hashes[BRIDGE])
        wrapper = _wrapper(exe, runtime, gpu, cache, hashes[BRIDGE])
        request = hashlib.sha256(json.dumps({'assets': {name: hashes[name] for name in DLLS + (BRIDGE,)},
                                             'weights': weight_info, 'wrapper': wrapper.decode(),
                                             'gpu_index': gpu['index'], 'proton': str(proton.get('root', ''))}, sort_keys=True).encode()).hexdigest()
        if prior:
            if prior['request'] != request or not _status(exe)['valid']:
                raise RuntimeError('Existing deployment differs; uninstall before changing its configuration')
            return dict(_status(exe), idempotent=True, dry_run=dry_run)
        for name in TARGETS:
            _safe(exe.parent / name, missing=True)
        for name in DLLS:
            if (exe.parent / name).exists() and not replace_existing:
                raise RuntimeError(f'Existing {name}: explicit replace_existing=True required')
        ini_path = exe.parent / INI
        ini_data = _ini(_bytes(ini_path) if ini_path.exists() else b'', gpu['index'])
        sources = {name: assets / name for name in DLLS}
        sources[STORE + '/runtime/' + BRIDGE] = assets / BRIDGE
        sources[WEIGHTS] = weights
        payloads = {INI: ini_data, STORE + '/launch.sh': wrapper}
        files = {}
        for name in TARGETS:
            digest = hashlib.sha256(payloads[name]).hexdigest() if name in payloads else (weight_info['sha256'] if name == WEIGHTS else hashes[Path(name).name])
            mode = (stat.S_IMODE(ini_path.stat().st_mode) & 0o777 if name == INI and ini_path.exists() else
                    0o700 if name.endswith('/launch.sh') else 0o644)
            if name in sources:
                mode = stat.S_IMODE(sources[name].stat().st_mode) & 0o777
            files[name] = _entry(exe.parent / name, digest, mode, name == WEIGHTS and weights == exe.parent / WEIGHTS)
        data = {'schema': 1, 'exe': str(exe), 'state': 'preparing', 'files': files,
                'request': request, 'cache': str(cache), 'undo': {}}
        result = {'installed': False, 'valid': False, 'dry_run': True, 'notes': list(NOTES),
                  'launch_options': shlex.quote(str(store / 'launch.sh')) + ' %command%'}
        if dry_run:
            return result
        store.mkdir(mode=0o700)  # exclusive claim; never adopt an unknown directory
        _sync(exe.parent)
        try:
            _journal(store, data)
            for dirname in ('backups', 'stage', 'runtime', 'logs', 'undo'):
                (store / dirname).mkdir(mode=0o700)
            _sync(store)
            for name, item in files.items():
                if item['original'] is not None and not item['preserve']:
                    _atomic_copy(exe.parent / name, store / 'backups' / Path(name).name,
                                 item['original'], item['original_mode'], store / 'stage')
            for name, content in payloads.items():
                _atomic_bytes(store / 'stage' / Path(name).name, content, files[name]['mode'])
                sources[name] = store / 'stage' / Path(name).name
            _cache(assets / BRIDGE, hashes[BRIDGE], cache)
            data['state'] = 'installing'
            _journal(store, data)
            for name in TARGETS:
                item = files[name]
                if item['preserve']:
                    continue
                current = exe.parent / name
                if (_digest(current) if current.exists() else None) != item['original']:
                    raise RuntimeError(f'Target changed since backup: {name}')
                item['touched'] = True
                _journal(store, data)  # durable intent BEFORE rename, including current file
                _atomic_copy(sources[name], current, item['sha256'], item['mode'], store / 'stage')
            data['state'] = 'installed'
            _journal(store, data)
            _verify(exe, data)
        except Exception:
            try:
                # Preflight every target and backup before any rollback mutation.
                # A failed final verification must retain the same protection.
                recovery_data = dict(data, state='installing')
                _verify(exe, recovery_data)
                _restore(exe, data)
                data['state'] = 'removed'
                _journal(store, data)
                _cleanup(store)
            except Exception as recovery:
                raise RuntimeError(f'Rollback incomplete: {recovery}; run uninstall for recovery') from recovery
            raise
        return dict(_status(exe), idempotent=False, dry_run=False)


def uninstall_game(exe):
    exe = _exe(exe)
    with _lock(exe.parent):
        if running_game(exe):
            raise RuntimeError('Game is running; close it before uninstall')
        store = exe.parent / STORE
        if not store.exists() and not store.is_symlink():
            return {'installed': False, 'valid': False, 'notes': ['Nothing installed.']}
        _safe(store, directory=True)
        if not any(store.iterdir()):
            # Crash between mkdir and initial journal, or final unlink and rmdir.
            # No game files can be inferred/restored without a journal.
            store.rmdir()
            _sync(exe.parent)
            return {'installed': False, 'valid': False, 'pending': False,
                    'notes': ['Removed empty transaction claim; no game files changed.']}
        data = _load(exe)
        _cleanup(store, check_only=True)
        if data['state'] == 'removed':
            _verify(exe, data, uninstall=True)
            _cleanup(store)
            return {'installed': False, 'valid': False, 'notes': list(NOTES)}
        _verify(exe, data, uninstall=True)
        # Uninstall has its own durable undo snapshots, including overlay INI edits.
        for dirname in ('stage', 'undo'):
            (store / dirname).mkdir(mode=0o700, exist_ok=True)
            _safe(store / dirname, directory=True)
        if data['state'] != 'uninstalling':
            for name, item in data['files'].items():
                if not item['touched'] or item['preserve']:
                    continue
                target = exe.parent / name
                digest = _digest(target) if target.exists() else None
                mode = stat.S_IMODE(target.stat().st_mode) & 0o777 if target.exists() else 0o600
                if digest is not None:
                    _atomic_copy(target, store / 'undo' / Path(name).name, digest, mode, store / 'stage')
                data['undo'][name] = {'sha256': digest, 'mode': mode}
            previous = data['state']
            data['state'] = 'uninstalling'
            _journal(store, data)
        else:
            previous = 'installing'  # interrupted uninstall can safely roll forward
        try:
            _restore(exe, data)
        except Exception:
            try:
                for name, item in data['undo'].items():
                    target = _safe(exe.parent / name, missing=True)
                    if item['sha256'] is None:
                        if target.exists():
                            target.unlink()
                            _sync(target.parent)
                    else:
                        _atomic_copy(store / 'undo' / Path(name).name, target,
                                     item['sha256'], item['mode'], store / 'stage')
                data['state'] = previous
                _journal(store, data)
            except Exception as recovery:
                raise RuntimeError(f'Uninstall rollback incomplete: {recovery}; run uninstall for recovery') from recovery
            raise
        data['state'] = 'removed'
        _journal(store, data)
        _cleanup(store)
        return {'installed': False, 'valid': False, 'pending': False, 'notes': list(NOTES)}
