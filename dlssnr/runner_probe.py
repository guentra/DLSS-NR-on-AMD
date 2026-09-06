"""Bounded HIP loader check in a private Wine prefix, optionally inside Steam's SLR.

Does not launch the game or prove rendering, interop, or launcher configuration.
"""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

from . import assets, deploy, games, runtime


def validate_report(report, log, returncode):
    if report == 'ok' and returncode == 0:
        return 'ok'
    if report == 'version_error' or (report == 'load_error' and
            ('dlopen selected HIP ' in log and ' failed:' in log or 'missing hip' in log)):
        failures = [line for line in log.splitlines()
                    if ' failed:' in line or 'HIP NEEDED ' in line or 'missing ' in line]
        raise runtime.RunnerDependencyError('HIP cannot load in the selected runner: ' +
                                            ('\n'.join(dict.fromkeys(failures))[-3000:] or report))
    if report == 'no_devices':
        raise runtime.DriverUnavailable('Runner HIP reports no usable devices. ' + runtime._DRIVER_REMEDY)
    raise RuntimeError(f'Runner probe failed (exit {returncode}, result {report!r}); '
                       'not a confirmed missing-dependency error. ' + log[-3000:])


def runner_context(proton, steam_root=None):
    """Resolve the SLR app explicitly required by Proton, never guess a version."""
    root = Path(proton['root'])
    manifest = root / 'toolmanifest.vdf'
    if not manifest.is_file():
        return [], 'Wine runner (no Steam container declared)'
    info = games._get(games._kv(manifest), 'manifest', {})
    appid = games._get(info, 'require_tool_appid')
    if not appid:
        return [], 'Wine runner (no Steam container declared)'
    if not str(appid).isdigit():
        raise RuntimeError('Invalid required Steam runtime appid in runner manifest')
    for steam in games._roots(steam_root):
        for library in games._libraries(steam):
            acf = library / 'steamapps' / ('appmanifest_' + str(appid) + '.acf')
            if not acf.is_file():
                continue
            state = games._get(games._kv(acf), 'AppState', {})
            directory = games._get(state, 'installdir', '')
            if not isinstance(directory, str) or not directory or Path(directory).name != directory or directory in ('.', '..'):
                continue
            entry = library / 'steamapps/common' / directory / '_v2-entry-point'
            if entry.is_file() and os.access(entry, os.X_OK):
                return [str(entry), '--verb=run', '--'], 'Steam runtime ' + str(appid)
    raise RuntimeError(f'Required Steam Linux Runtime {appid} is unavailable; install it in Steam. '
                       'Cannot validate Proton loading; downloading ROCm would not fix this.')


def probe(package_root, rt, proton, cache_root, steam_root=None):
    root = Path(package_root)
    manifest = assets.verify_assets(root)
    for name in ('hip_probe.exe', 'libdlssnr_hip_bridge.so'):
        if name not in manifest['files']:
            raise RuntimeError('Package lacks the verified runner probe; re-extract the current archive')
    context, label = runner_context(proton, steam_root)
    wine = Path(proton['wine']).resolve()
    server = wine.parent / 'wineserver'
    if not server.is_file():
        raise RuntimeError('Runner wineserver missing; cannot safely shut down probe prefix')
    cache_root = deploy._safe(Path(cache_root).expanduser(), directory=True, missing=True)
    cache_root.mkdir(parents=True, exist_ok=True)
    folder = Path(tempfile.mkdtemp(prefix='runner-probe-', dir=cache_root))
    safe = True
    try:
        bridge_hash = manifest['files']['libdlssnr_hip_bridge.so']
        bridge = deploy._cache_path(bridge_hash)
        deploy._cache(root / 'assets/libdlssnr_hip_bridge.so', bridge_hash, bridge)
        shutil.copy2(root / 'assets/hip_probe.exe', folder / 'hip_probe.exe')
        env = dict(os.environ)
        for key in ('LD_PRELOAD', 'LD_LIBRARY_PATH', 'LD_AUDIT', 'WINEDLLOVERRIDES',
                    'WINEPREFIX', 'STEAM_COMPAT_DATA_PATH', 'STEAM_COMPAT_APP_ID',
                    'SteamAppId', 'SteamGameId', 'PRESSURE_VESSEL_APP_ID',
                    'WINESERVER', 'WINELOADER', 'WINEARCH'):
            env.pop(key, None)
        mounts = [str(folder), str(wine.parent.parent), str(bridge.parent),
                  str(Path(rt['library']).parent)] + list(rt.get('mount_roots', []))
        mounts = list(dict.fromkeys(deploy._mount(p) for p in mounts))
        env.update(WINEPREFIX=str(folder / 'prefix'), WINEDEBUG='-all',
                   WINEDLLOVERRIDES='version=b;amdhip64_7=n',
                   DLSSNR_HIP_LIBRARY=str(rt['library']), DLSSNR_HIP_LOG=str(folder / 'hip.log'),
                   LD_PRELOAD=str(bridge),
                   STEAM_COMPAT_MOUNTS=':'.join(mounts),
                   PRESSURE_VESSEL_FILESYSTEMS_RW=str(folder),
                   PRESSURE_VESSEL_FILESYSTEMS_RO=':'.join(mounts[1:]),
                   PRESSURE_VESSEL_VARIABLE_DIR=str(folder / 'slr-var'))
        command = context + [str(wine), str(folder / 'hip_probe.exe')]
        safe = False
        error = None
        result = None
        try:
            result = subprocess.run(command, cwd=folder, env=env, capture_output=True,
                                    text=True, timeout=90)
        except (OSError, subprocess.TimeoutExpired) as exc:
            error = RuntimeError(f'Runner probe could not complete: {exc}')
        finally:
            # Run shutdown inside the same container when one was selected.
            safe = False
            for option in ('-k', '-w'):
                try:
                    stopped = subprocess.run(context + [str(server), option], cwd=folder,
                                             env=env, capture_output=True, timeout=20)
                    if option == '-w':
                        safe = stopped.returncode == 0
                except (OSError, subprocess.TimeoutExpired):
                    pass
        if not safe:
            raise RuntimeError(f'Probe Wine shutdown unconfirmed; private prefix retained at {folder}. '
                               'Stop it before retrying. No runtime fallback attempted.')
        log = (folder / 'hip.log').read_text(errors='replace') if (folder / 'hip.log').is_file() else ''
        report = (folder / 'probe-result.txt').read_text().strip() if (folder / 'probe-result.txt').is_file() else ''
        if error:
            raise error
        if result is None:
            raise RuntimeError('Runner returned no probe result')
        validate_report(report, log + '\n' + result.stderr[-3000:], result.returncode)
        return {'passed': True, 'context': label, 'runner': str(proton['root']),
                'library': rt['library'], 'gameplay_verified': False}
    finally:
        if safe:
            shutil.rmtree(folder)
