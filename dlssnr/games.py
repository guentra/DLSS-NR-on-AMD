"""Read-only Steam, game and Proton inspection. Python 3.10+, no dependencies.

Evidence is not a compatibility guarantee. No executable is ever launched.
Binary readers use bounded seeks; even large game binaries are not read in full.
"""
from pathlib import Path
import os
import re
import struct
import warnings

MAX_TEXT = 4 * 1024 * 1024
MAX_FILES = 30000
MAX_DEPTH = 16
REQUIRED_WINE_SYMBOLS = frozenset((
    'd3dkmt_open_resource', 'd3dkmt_object_get_fd', 'd3dkmt_destroy_resource'))


def _error(message):
    return RuntimeError(message)


def parse_keyvalues(text):
    """Parse nested Valve KeyValues, retaining unknown backslash sequences.

    Only escaped quotes/backslashes are decoded; e.g. Windows \\new is not
    interpreted as a newline. Identifiers remain strings, including numeric IDs.
    """
    if len(text) > MAX_TEXT:
        raise _error('KeyValues too large; check the Steam file.')
    tokens = []
    i = 0
    while i < len(text):
        c = text[i]
        if c.isspace() or c == '\ufeff':
            i += 1
        elif text.startswith('//', i):
            end = text.find('\n', i)
            i = len(text) if end < 0 else end + 1
        elif c in '{}':
            tokens.append((c, c)); i += 1
        elif c == '"':
            i += 1
            value = []
            while i < len(text) and text[i] != '"':
                if text[i] == '\\' and i + 1 < len(text) and text[i+1] in '\\"':
                    i += 1
                value.append(text[i]); i += 1
            if i == len(text):
                raise _error('KeyValues: unclosed quote; check the Steam file.')
            tokens.append(('s', ''.join(value))); i += 1
        else:
            start = i
            while i < len(text) and not text[i].isspace() and text[i] not in '{}"':
                i += 1
            tokens.append(('s', text[start:i]))
    pos = 0
    def obj(depth, nested):
        nonlocal pos
        if depth > 64:
            raise _error('KeyValues: excessive nesting.')
        result = {}
        while pos < len(tokens):
            kind, key = tokens[pos]; pos += 1
            if kind == '}' and nested:
                return result
            if kind != 's' or pos >= len(tokens):
                raise _error('Malformed KeyValues; check the Steam file.')
            kind, value = tokens[pos]; pos += 1
            if kind == '{':
                value = obj(depth + 1, True)
            elif kind != 's':
                raise _error('KeyValues: missing value.')
            if key in result:
                raise _error('KeyValues: duplicate identifier: ' + key)
            result[key] = value
        if nested:
            raise _error('KeyValues: unclosed brace.')
        return result
    return obj(0, False)


def _kv(path):
    with Path(path).open('rb') as f:
        raw = f.read(MAX_TEXT + 1)
    if len(raw) > MAX_TEXT:
        raise _error('Steam file too large: ' + str(path))
    return parse_keyvalues(raw.decode('utf-8-sig'))


def _get(mapping, key, default=None):
    if not isinstance(mapping, dict):
        return default
    return next((v for k, v in mapping.items() if k.lower() == key.lower()), default)


def _roots(explicit=None):
    candidates = [Path(explicit).expanduser()] if explicit is not None else [
        Path.home()/'.local/share/Steam', Path.home()/'.steam/steam',
        Path.home()/'.var/app/com.valvesoftware.Steam/.local/share/Steam']
    result = []
    for p in candidates:
        if p.is_dir() and 'com.valvesoftware.Steam' in p.parts:
            warnings.warn('Steam Flatpak detected: check sandbox permissions for the game and runtime.', RuntimeWarning)
        if p.is_dir():
            p = p.resolve()
            if p not in result:
                result.append(p)
    return result


def _libraries(root):
    result = [root]
    path = root/'steamapps/libraryfolders.vdf'
    if not path.exists():
        path = root/'config/libraryfolders.vdf'
    if path.exists():
        try:
            folders = _get(_kv(path), 'libraryfolders', {})
            if not isinstance(folders, dict):
                raise _error('Invalid libraryfolders')
            for key, value in folders.items():
                if not re.fullmatch(r'[0-9]+', key):
                    continue
                value = _get(value, 'path') if isinstance(value, dict) else value
                if not isinstance(value, str) or not value or not Path(value).is_absolute():
                    warnings.warn('Steam library skipped: invalid path.', RuntimeWarning)
                    continue
                p = Path(value).resolve()
                if p.is_dir() and p not in result:
                    result.append(p)
        except (OSError, UnicodeError, RuntimeError, ValueError) as e:
            warnings.warn(f'Steam libraries skipped ({path}): {e}', RuntimeWarning)
    return result


def discover_games(steam_root=None):
    result, seen = [], set()
    for root in _roots(steam_root):
        for library in _libraries(root):
            for manifest in sorted((library/'steamapps').glob('appmanifest_*.acf')):
                try:
                    state = _get(_kv(manifest), 'appstate')
                    appid = _get(state, 'appid')
                    dirname = _get(state, 'installdir')
                    if not isinstance(appid, str) or not re.fullmatch(r'[0-9]+', appid):
                        raise _error('Invalid appid')
                    if manifest.name != f'appmanifest_{appid}.acf':
                        raise _error('appid does not match the manifest')
                    if not isinstance(dirname, str) or not dirname or Path(dirname).is_absolute() or '..' in Path(dirname).parts or '\\' in dirname:
                        raise _error('Invalid installdir')
                    common = (library/'steamapps/common').resolve()
                    game = (common/dirname).resolve()
                    if game == common or not game.is_relative_to(common) or not game.is_dir():
                        raise _error('Game directory missing or outside the library')
                    key = (appid, game)
                    if key in seen:
                        continue
                    name = _get(state, 'name', dirname)
                    if not isinstance(name, str):
                        raise _error('Invalid name')
                    result.append(dict(appid=appid, name=name, path=game,
                                       prefix=library/'steamapps/compatdata'/appid/'pfx', steam_root=root))
                    seen.add(key)
                except (OSError, UnicodeError, RuntimeError, ValueError) as e:
                    warnings.warn(f'Manifest skipped ({manifest}): {e}', RuntimeWarning)
    return result


class _Reader:
    def __init__(self, file):
        self.file = file
        self.size = os.fstat(file.fileno()).st_size

    def read(self, offset, size):
        if offset < 0 or size < 0 or size > 16*1024*1024 or offset > self.size or size > self.size-offset:
            raise _error('Truncated binary or invalid bounds.')
        self.file.seek(offset)
        data = self.file.read(size)
        if len(data) != size:
            raise _error('Binary truncated during reading.')
        return data

    def unpack(self, fmt, offset):
        return struct.unpack(fmt, self.read(offset, struct.calcsize(fmt)))

    def string(self, offset, end):
        data = self.read(offset, min(4096, end-offset))
        nul = data.find(b'\0')
        if nul < 0:
            raise _error('Binary string unterminated or too long.')
        try:
            return data[:nul].decode('ascii')
        except UnicodeError as e:
            raise _error('Non-ASCII binary identifier.') from e


def pe_info(path):
    """Return machine (COFF integer), is_dll, imports, exports and section dicts.

    Import spelling is preserved. Only mapped, file-backed RVAs are accepted;
    virtual zero-fill cannot masquerade as a table. Normal and delay imports
    are parsed, not inferred from strings. Exports must reference an actual RVA.
    """
    try:
        with Path(path).open('rb') as f:
            r = _Reader(f)
            if r.read(0, 2) != b'MZ':
                raise _error('Missing DOS signature.')
            nt, = r.unpack('<I', 0x3c)
            if r.read(nt, 4) != b'PE\0\0':
                raise _error('Missing PE signature.')
            machine, count, _, _, _, optsize, flags = r.unpack('<HHIIIHH', nt+4)
            if not 1 <= count <= 96:
                raise _error('Invalid PE section count.')
            opt = nt+24
            r.read(opt, optsize)
            magic, = r.unpack('<H', opt)
            if magic == 0x20b:
                dirbase, numoff = 112, 108
                imagebase, = r.unpack('<Q', opt+24)
            elif magic == 0x10b:
                dirbase, numoff = 96, 92
                imagebase, = r.unpack('<I', opt+28)
            else:
                raise _error('Unknown PE optional header format.')
            if optsize < dirbase:
                raise _error('Truncated PE optional header.')
            ndirs, = r.unpack('<I', opt+numoff)
            if ndirs > 16 or dirbase+ndirs*8 > optsize:
                raise _error('Invalid PE directories.')
            headers, = r.unpack('<I', opt+60)
            if headers > r.size or headers < opt+optsize+count*40:
                raise _error('Invalid PE header size.')
            sections = []
            for i in range(count):
                raw = r.unpack('<8sIIIIIIHHI', opt+optsize+i*40)
                name, vsize, va, size, offset = raw[:5]
                if offset > r.size or size > r.size-offset or va+max(vsize, size) > 0x100000000:
                    raise _error('PE section outside the file.')
                sections.append(dict(name=name.rstrip(b'\0').decode('ascii', 'replace'), virtual_address=va,
                                     virtual_size=vsize, raw_offset=offset, raw_size=size, characteristics=raw[-1]))
            def mapped(va, size=1):
                if not va or size < 0:
                    raise _error('Zero or invalid PE RVA.')
                matches = []
                if va < headers and size <= headers-va:
                    matches.append((va, headers))
                for s in sections:
                    delta = va-s['virtual_address']
                    if 0 <= delta < s['raw_size'] and size <= s['raw_size']-delta:
                        matches.append((s['raw_offset']+delta, s['raw_offset']+s['raw_size']))
                if len(matches) != 1:
                    raise _error('Unmapped or ambiguous PE RVA.')
                return matches[0]
            def text(va):
                off, end = mapped(va)
                return r.string(off, end)
            def directory(index):
                if index >= ndirs:
                    return 0, 0
                va, size = r.unpack('<II', opt+dirbase+index*8)
                if bool(va) != bool(size):
                    raise _error('Incomplete PE directory.')
                if va:
                    mapped(va, size)
                return va, size
            imports = []
            for idx, stride in ((1, 20), (13, 32)):
                va, size = directory(idx)
                if not va:
                    continue
                if size < stride or size//stride > 4096:
                    raise _error('Invalid PE import table.')
                terminated = False
                for i in range(size//stride):
                    off, _ = mapped(va+i*stride, stride)
                    fields = r.unpack('<'+'I'*(stride//4), off)
                    if not any(fields):
                        terminated = True
                        break
                    if idx == 1:
                        nameva = fields[3]
                    else:
                        if fields[0] not in (0, 1):
                            raise _error('Invalid delay-import attributes.')
                        nameva = fields[1] if fields[0] & 1 else fields[1]-imagebase
                    name = text(nameva)
                    if not name:
                        raise _error('Unnamed PE import.')
                    if name not in imports:
                        imports.append(name)
                if not terminated:
                    raise _error('Unterminated PE import table.')
            exports = []
            va, size = directory(0)
            if va:
                if size < 40:
                    raise _error('Truncated PE export table.')
                fields = r.unpack('<IIHHIIIIIII', mapped(va, 40)[0])
                nfunc, nnames, funcs, names, ords = fields[6:]
                if nfunc > 100000 or nnames > nfunc:
                    raise _error('Invalid PE export count.')
                if nfunc:
                    funcoff = mapped(funcs, nfunc*4)[0]
                if nnames:
                    namesoff = mapped(names, nnames*4)[0]
                    ordsoff = mapped(ords, nnames*2)[0]
                for i in range(nnames):
                    ordinal, = r.unpack('<H', ordsoff+i*2)
                    if ordinal >= nfunc:
                        raise _error('Invalid PE ordinal.')
                    target, = r.unpack('<I', funcoff+ordinal*4)
                    nameva, = r.unpack('<I', namesoff+i*4)
                    name = text(nameva)
                    if target:
                        # Data exports may legitimately live in virtual zero-fill
                        # (.bss). Tables and strings still require file backing.
                        targets = [s for s in sections if 0 <= target-s['virtual_address'] < max(s['virtual_size'], s['raw_size'])]
                        if len(targets) != 1:
                            raise _error('PE export target outside a section or ambiguous.')
                        if va <= target < va+size:
                            text(target)  # validate forwarder, not a concrete export
                            continue
                        exports.append(name)
            bootstrap_path = None
            resource_va, resource_size = directory(2)
            if resource_va:
                def resource_entries(relative):
                    if relative < 0 or relative + 16 > resource_size:
                        raise _error('Resource directory outside its bounds.')
                    offset, _ = mapped(resource_va + relative, 16)
                    named, ids = r.unpack('<HH', offset + 12)
                    count = named + ids
                    if count > 4096 or relative + 16 + count * 8 > resource_size:
                        raise _error('Invalid resource directory entries.')
                    return [r.unpack('<II', mapped(resource_va + relative + 16 + i * 8, 8)[0])
                            for i in range(count)]
                types = dict(resource_entries(0))
                if types.get(10, 0) & 0x80000000:
                    rcdata = dict(resource_entries(types[10] & 0x7fffffff))
                    # Unreal BootstrapPackagedGame: target=RCDATA 201, arguments=202.
                    if 201 in rcdata and 202 in rcdata and rcdata[201] & 0x80000000:
                        targets = set()
                        for _, leaf in resource_entries(rcdata[201] & 0x7fffffff):
                            if leaf & 0x80000000 or leaf + 16 > resource_size:
                                raise _error('Invalid bootstrap resource leaf.')
                            address, length, _, _ = r.unpack('<IIII', mapped(resource_va + leaf, 16)[0])
                            if not 2 <= length <= 4096 or length % 2:
                                raise _error('Invalid bootstrap target length.')
                            raw = r.read(mapped(address, length)[0], length)
                            value = raw.decode('utf-16-le').rstrip('\0')
                            if '\0' in value:
                                raise _error('Invalid bootstrap target string.')
                            targets.add(value)
                        if len(targets) != 1:
                            raise _error('Ambiguous bootstrap target.')
                        bootstrap_path = targets.pop()
            return dict(machine=machine, is_dll=bool(flags & 0x2000), imports=imports,
                        exports=exports, sections=sections, bootstrap_path=bootstrap_path)
    except (OSError, ValueError, struct.error, RuntimeError) as e:
        raise _error(f'Invalid PE ({path}): {e}; choose a valid Windows binary.') from e


parse_pe = pe_info


def _walk(root):
    """Bounded walk, no directory symlink traversal or escaped file targets."""
    root = Path(root).resolve()
    pending = [(root, 0)]
    count = 0
    while pending:
        folder, depth = pending.pop()
        try:
            with os.scandir(folder) as it:
                for entry in it:
                    count += 1
                    if count > MAX_FILES:
                        raise _error('Directory tree too large; explicitly select a game subdirectory.')
                    p = Path(entry.path)
                    if entry.is_dir(follow_symlinks=False):
                        if depth >= MAX_DEPTH:
                            raise _error('Directory tree too deep; select a game subdirectory.')
                        pending.append((p, depth+1))
                        yield p
                    elif entry.is_file() and p.resolve().is_relative_to(root):
                        yield p
        except OSError as e:
            raise _error(f'Cannot read directory ({folder}): {e}') from e


def _excluded(path):
    name = path.stem.lower()
    if name in ('crs-handler', 'crs-uploader', 'unrealcefsubprocess'):
        return True
    return any(s in name for s in ('crashreport', 'crashpad', 'setup', 'launchhelper', 'unins', 'redist', 'easyanticheat', 'battleye'))


def _executable(path):
    if path.suffix.lower() != '.exe' or _excluded(path):
        return False
    try:
        info = pe_info(path)
        return info['machine'] == 0x8664 and not info['is_dll']
    except RuntimeError:
        return False


def find_executables(game_root):
    return sorted((p for p in _walk(game_root) if _executable(p)), key=lambda p: str(p).lower())


def select_executable(game_root, explicit=None):
    root = Path(game_root).expanduser().resolve()
    if explicit is not None:
        p = Path(explicit).expanduser()
        p = (p if p.is_absolute() else root/p).resolve()
        if not p.is_relative_to(root) or not _executable(p):
            raise _error('Invalid executable or outside the game; specify a PE x64 .exe in the game directory.')
        return _bootstrap_target(p)
    candidates = find_executables(root)
    shipping = [p for p in candidates if 'shipping' in p.stem.lower()]
    choices = shipping or candidates
    if len(choices) == 1:
        return _bootstrap_target(choices[0])
    if not choices:
        raise _error('No PE x64 executable found; specify the game directory or --exe.')
    raise _error('Multiple possible executables; specify --exe without guessing: ' + ', '.join(map(str, choices)))


def _bootstrap_target(path):
    target = pe_info(path).get('bootstrap_path')
    if target is None:
        return path
    relative = Path(target.replace('\\', '/'))
    if (relative.is_absolute() or '..' in relative.parts or ':' in target
            or any(ord(c) < 32 for c in target) or relative.suffix.lower() != '.exe'):
        raise _error('Unsafe Unreal bootstrap target; refusing installation.')
    actual = path.parent / relative
    if not actual.resolve().is_relative_to(path.parent.resolve()) or actual.is_symlink():
        raise _error('Unreal bootstrap target is outside the game or symlinked.')
    if not actual.is_file():
        raise _error(f'{path.name} is only an Unreal bootstrap launcher. Missing game executable: {actual}. '
                     'In Steam: Properties > Installed Files > Verify integrity of game files. '
                     'Do not install the mod beside the bootstrap launcher.')
    if not _executable(actual) or pe_info(actual).get('bootstrap_path') is not None:
        raise _error('Unreal bootstrap target is not a valid game executable.')
    return actual.resolve()


def inspect_game(exe):
    exe = Path(exe).expanduser().resolve()
    actual = _bootstrap_target(exe)
    if actual != exe:
        raise _error(f'This is an Unreal bootstrap launcher; select the actual game executable with --exe {actual}')
    info = pe_info(exe)
    if info['machine'] != 0x8664 or info['is_dll']:
        raise _error('The game must be a Windows PE x64 executable.')
    imports = list(info['imports'])
    # Typical UE layout: <game>/<project>/Binaries/Win64/<shipping.exe>.
    evidence_root = exe.parent
    if exe.parent.name.lower() == 'win64' and exe.parent.parent.name.lower() == 'binaries':
        evidence_root = exe.parent.parent.parent.parent
    fsr, anti = [], set()
    signatures = {'easyanticheat': 'EasyAntiCheat', 'easy anti cheat': 'EasyAntiCheat',
                  'battleye': 'BattlEye', 'beservice': 'BattlEye', 'beclient': 'BattlEye',
                  'ricochet': 'Ricochet', 'randgrid': 'Ricochet', 'vgk': 'Vanguard',
                  'vanguard': 'Vanguard', 'equ8': 'EQU8', 'faceit': 'FACEIT',
                  'xigncode': 'XIGNCODE', 'nprotect': 'nProtect', 'anticheatexpert': 'AntiCheatExpert'}
    for p in _walk(evidence_root):
        name = p.name.lower()
        if p.parent == exe.parent and p.suffix.lower() == '.dll':
            try:
                adjacent = pe_info(p)
                if adjacent['machine'] == 0x8664 and adjacent['is_dll']:
                    imports.extend(adjacent['imports'])
            except RuntimeError:
                pass
        if any(s in name for s in ('fsr', 'fidelityfx', 'ffx_')):
            fsr.append(str(p))
        for pattern, label in signatures.items():
            if pattern in name:
                anti.add(label)
    imports = sorted(set(imports), key=str.lower)
    lowered = {name.lower() for name in imports}
    for name in imports:
        low = name.lower()
        if any(s in low for s in ('fsr', 'fidelityfx', 'ffx_')):
            fsr.append('import: '+name)
        for pattern, label in signatures.items():
            if pattern in low:
                anti.add(label)
    return dict(exe=exe, imports=imports, version_loader='version.dll' in lowered,
                dx12=any(n == 'd3d12.dll' or n.startswith(('ffx_', 'amd_fidelityfx')) for n in lowered),
                fsr_evidence=sorted(set(fsr)), anti_cheat_evidence=sorted(anti))


def discover_protons(steam_root=None):
    result = []
    for root in _roots(steam_root):
        candidates = []
        for library in _libraries(root):
            candidates.extend((library/'steamapps/common').glob('Proton*'))
            candidates.extend((library/'compatibilitytools.d').glob('*'))
        for p in sorted(candidates):
            if any((p/layout/'bin/wine').is_file() for layout in ('files', 'dist')):
                p = p.resolve()
                if p not in result:
                    result.append(p)
    return result


def _elf_symbols(path):
    """Defined local STT_FUNC entries in ELF64 LE x86_64 SHT_SYMTAB only."""
    with Path(path).open('rb') as f:
        r = _Reader(f)
        ident = r.read(0, 16)
        if ident[:7] != b'\x7fELF\x02\x01\x01':
            raise _error('win32u must be little-endian ELF64.')
        h = r.unpack('<HHIQQQIHHHHHH', 16)
        if h[0] not in (2, 3) or h[1] != 62 or h[2] != 1 or h[7] != 64 or h[10] != 64 or not 1 <= h[11] <= 8192:
            raise _error('Invalid or unsupported ELF64 header.')
        shoff, count = h[5], h[11]
        r.read(shoff, count*64)
        sections = [r.unpack('<IIQQQQIIQQ', shoff+i*64) for i in range(count)]
        for s in sections:
            if s[1] != 8 and (s[4] > r.size or s[5] > r.size-s[4]):
                raise _error('ELF section outside the file.')
        found = set()
        for s in sections:
            if s[1] != 2:
                continue
            if s[9] != 24 or s[5] % 24 or s[5]//24 > 1000000 or not 0 < s[6] < count:
                raise _error('Invalid ELF symbol table.')
            strings = sections[s[6]]
            if strings[1] != 3:
                raise _error('Invalid ELF string table.')
            for i in range(s[5]//24):
                name, info, _, index, value, size = r.unpack('<IBBHQQ', s[4]+i*24)
                if name >= strings[5]:
                    raise _error('ELF symbol name outside the table.')
                if info != 2 or not 0 < index < count:
                    continue  # STB_LOCAL (0) + STT_FUNC (2), defined section
                section = sections[index]
                if section[1] != 1 or not section[2] & 4 or value < section[3] or value-section[3] >= section[5] or size > section[5]-(value-section[3]):
                    continue
                symbol = r.string(strings[4]+name, strings[4]+strings[5])
                if symbol in REQUIRED_WINE_SYMBOLS:
                    found.add(symbol)
        return found


def validate_proton(path):
    root = Path(path).expanduser().resolve()
    advice = 'Choose a compatible Proton (CachyOS tested) in Steam and specify its path; no automatic downloads.'
    try:
        base = next((root/layout for layout in ('files', 'dist') if (root/layout/'bin/wine').is_file()), None)
        if base is None:
            raise _error('Missing files/bin/wine or dist/bin/wine.')
        files = list(_walk(base))
        def pick(name, marker):
            matches = [p for p in files if p.name.lower() == name and marker in p.parts]
            if name == 'dxgi.dll':
                matches = [p for p in matches if 'dxvk' in p.parts]
            if len(matches) != 1:
                raise _error(f'{name}: missing or ambiguous x64 file ({len(matches)}).')
            return matches[0]
        ntdll = pick('ntdll.dll', 'x86_64-windows')
        win32u = pick('win32u.so', 'x86_64-unix')
        # Older Proton DXVK layouts use dxvk/x64 or lib64/wine/dxvk.
        dxgis = [p for p in files if p.name.lower() == 'dxgi.dll' and 'dxvk' in p.parts]
        dxgis = [p for p in dxgis if pe_info(p)['machine'] == 0x8664 and pe_info(p)['is_dll']]
        if len(dxgis) != 1:
            raise _error('Missing or ambiguous x64 DXVK dxgi.dll.')
        nt = pe_info(ntdll)
        if nt['machine'] != 0x8664 or not nt['is_dll'] or '__wine_get_unix_env' not in nt['exports']:
            raise _error('ntdll.dll does not provide the PE x64 export __wine_get_unix_env.')
        missing = REQUIRED_WINE_SYMBOLS - _elf_symbols(win32u)
        if missing:
            raise _error('Incompatible or stripped win32u.so: missing local SHT_SYMTAB functions: '+', '.join(sorted(missing)))
        return dict(root=root, wine=base/'bin/wine', ntdll=ntdll, win32u=win32u, dxgi=dxgis[0])
    except (OSError, RuntimeError, ValueError, struct.error) as e:
        raise _error(f'Incompatible Proton ({root}): {e} {advice}') from e
