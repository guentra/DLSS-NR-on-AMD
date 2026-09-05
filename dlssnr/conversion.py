"""Run upstream's original weight conversion in a private directory, never game.
The NVIDIA input is provided by the user and is never bundled or downloaded.
"""
from pathlib import Path
import json
import os
import shutil
import subprocess
import tempfile

from .assets import sha256, validate_weights, verify_assets

KNOWN_NVIDIA_SHA='e16bcf15e16e13f527491cdf7845b2fe6521a738d8f7c9c721866a8496e1fc8e'


def wine_path(path):
    p=str(Path(path).resolve())
    if any(c in p for c in ('\n','\r','\0',':','\\')):
        raise RuntimeError('Path cannot be represented in Wine')
    return 'Z:'+p.replace('/','\\')


def _shutdown(server, env):
    """Only a successful bounded wait confirms termination, not a kill request."""
    issues = []
    confirmed = False
    for option in ('-k', '-w'):
        try:
            result = subprocess.run([str(server), option], env=env,
                                    capture_output=True, timeout=15)
            if result.returncode:
                issues.append(f'wineserver {option} exited {result.returncode}')
            elif option == '-w':
                confirmed = True
        except (OSError, subprocess.TimeoutExpired) as exc:
            issues.append(f'wineserver {option}: {exc}')
    return confirmed, '; '.join(issues)


def convert_weights(package_root, nvidia_dll, proton, cache_root):
    from .deploy import _safe, _atomic_copy, _atomic_bytes
    source=Path(nvidia_dll).expanduser().resolve()
    if not source.is_file():raise RuntimeError(f'NVIDIA DLL not found: {source}')
    if sha256(source)!=KNOWN_NVIDIA_SHA:
        raise RuntimeError('Unrecognized NVIDIA DLL: only verified version 310.8.0.0 is accepted. '
                           'Supply already converted DLSSNRW1 weights with --weights if needed.')
    root=Path(package_root).resolve();verify_assets(root)
    wine=Path(proton['wine']).resolve()
    if not wine.is_file():raise RuntimeError('Proton Wine is missing')
    server=wine.parent/'wineserver'
    if not server.is_file():raise RuntimeError('Proton wineserver is missing; cannot confirm conversion shutdown')
    cache=_safe(Path(cache_root).expanduser()/'weights'/KNOWN_NVIDIA_SHA,
                directory=True, missing=True)
    cache.mkdir(parents=True,exist_ok=True)
    output=cache/'dlssnr_on_amd_weights.bin'
    for name in (output.name, '.convert.lock', 'conversion.log', 'conversion.json'):
        _safe(cache/name, missing=True)
    if output.exists():validate_weights(output);return output
    if shutil.disk_usage(cache).free<1024**3:raise RuntimeError('Conversion requires 1 GiB free space')
    import fcntl
    with (cache/'.convert.lock').open('a') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError as e:raise RuntimeError('Another conversion is already running') from e
        folder=Path(tempfile.mkdtemp(prefix='conversion-',dir=cache))
        safe_to_remove=True
        try:
            # Copy, not link, so an upstream write cannot alter the original.
            shutil.copy2(source,folder/'nvngx_dlssnr.dll')
            for name in ('dlssnr_on_amd_setup.exe','amdhip64_7.dll'):
                shutil.copy2(root/'assets'/name,folder/name)
            # Raw Proton prefixes may have an unusable builtin DXGI/wined3d chain.
            # Use the already validated distribution's DXVK, private to conversion.
            if proton.get('dxgi'):
                shutil.copy2(proton['dxgi'], folder/'dxgi.dll')
            env=os.environ.copy()
            for key in ('LD_PRELOAD','LD_LIBRARY_PATH','LD_AUDIT','WINEDLLOVERRIDES',
                        'STEAM_COMPAT_DATA_PATH','VKD3D_NR_FLAG_HASH'):
                env.pop(key,None)
            env.update(WINEPREFIX=str(folder/'prefix'),WINEDEBUG='-all',
                       WINEDLLOVERRIDES='amdhip64_7=n;version=b' + (';dxgi=n' if proton.get('dxgi') else ''))
            error=None
            run=None
            safe_to_remove=False
            try:
                run=subprocess.run([str(wine),str(folder/'dlssnr_on_amd_setup.exe'),wine_path(folder)],
                        cwd=folder,env=env,input='y\ny\ny\n\n',text=True,
                        stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=180)
                if run.returncode:
                    error=RuntimeError(f'Original conversion failed (exit {run.returncode}). Log: {cache / "conversion.log"}')
            except subprocess.TimeoutExpired as exc:
                error=RuntimeError('Conversion timed out after 180 s; no game changes')
                error.__cause__=exc
            except BaseException as exc:
                error=exc
            finally:
                safe_to_remove, diagnostic=_shutdown(server, env)
            if not safe_to_remove:
                raise RuntimeError(f'Wine termination not confirmed: {diagnostic}. '
                                   f'Staging retained at {folder}; stop this private Wine prefix '
                                   f'before removing it or retrying. '
                                   f'Original conversion error: {error or "none"}') from error
            if run is not None:
                _atomic_bytes(cache/'conversion.log', run.stdout.encode())
            if error is not None:
                raise error
            generated=_safe(folder/'dlssnr_on_amd_weights.bin', missing=True)
            if not generated.is_file():
                raise RuntimeError(f'Original conversion produced no weights. Log: {cache / "conversion.log"}')
            details=validate_weights(generated)
            # Exclusive random regular file, verified before atomic publication.
            # Never touch a legacy weights.tmp (which may point outside the cache).
            _atomic_copy(generated, output, details['sha256'])
            _atomic_bytes(cache/'conversion.json', json.dumps({'input_sha256':KNOWN_NVIDIA_SHA,
                    'weights':details,'source':'local conversion via original setup v0.2.11'},indent=2).encode())
        finally:
            if safe_to_remove:
                shutil.rmtree(folder)
    return output
