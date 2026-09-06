"""Isolated HIP7 discovery/probing and consent-only, user-local AMD core wheels.

No GPU kernels are launched. ``self_test=True`` explicitly enables a 256-byte
host/device round trip on each enumerated GPU. Wheels cannot supply a kernel
driver. The selected nightly core is not a promise of payload GPU support.
"""
from __future__ import annotations

import ctypes
import email.parser
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request
import zipfile

REQUIRED_SYMBOLS = (
    '__hipRegisterFatBinary', '__hipUnregisterFatBinary', '__hipRegisterFunction',
    '__hipRegisterVar', '__hipPushCallConfiguration', '__hipPopCallConfiguration',
    'hipGetDeviceCount', 'hipSetDevice', 'hipGetDevicePropertiesR0600',
    'hipDriverGetVersion', 'hipRuntimeGetVersion', 'hipMalloc', 'hipFree',
    'hipMemcpy', 'hipMemcpyAsync', 'hipMemcpyToSymbol', 'hipMemset',
    'hipMemsetAsync', 'hipLaunchKernel', 'hipDeviceSynchronize',
    'hipGetLastError', 'hipGetErrorString', 'hipEventCreate', 'hipEventRecord',
    'hipEventSynchronize', 'hipEventElapsedTime', 'hipImportExternalMemory',
    'hipExternalMemoryGetMappedBuffer', 'hipDestroyExternalMemory',
)
WHEEL_VERSION = '7.14.0a20260612'
WHEEL_URL = ('https://rocm.nightlies.amd.com/v2/gfx120X-all/'
             'rocm_sdk_core-7.14.0a20260612-py3-none-linux_x86_64.whl')
WHEEL_SHA256 = '15e800e79a1d510b7cb04e577e9da48aafd5d654191d82f63c19d882f979abd7'
MAX_WHEEL_BYTES = 512 * 1024 * 1024
MAX_EXPANDED_BYTES = 2 * 1024 * 1024 * 1024
MIN_FREE_BYTES = 3 * 1024 * 1024 * 1024
_DRIVER_REMEDY = ('Wheels cannot install the kernel driver. Ask your system administrator '
                  'to enable a supported amdgpu/KFD driver and read/write access to '
                  '/dev/kfd and /dev/dri/renderD* (render/video groups or device ACLs); '
                  'log in again after group changes. Containers/Steam must expose these devices.')


class DriverUnavailable(RuntimeError):
    """A different userspace wheel is not a remedy for this driver/device failure."""


class RunnerDependencyError(RuntimeError):
    """HIP loader/ABI failure confirmed inside the selected runner."""


def _driver_error() -> str | None:
    kfd = Path('/dev/kfd')
    if not kfd.exists():
        return 'Missing /dev/kfd. ' + _DRIVER_REMEDY
    if not os.access(kfd, os.R_OK | os.W_OK):
        return 'No read/write permission for /dev/kfd. ' + _DRIVER_REMEDY
    nodes = list(Path('/dev/dri').glob('renderD*'))
    if not nodes:
        return 'No DRM render nodes (/dev/dri/renderD*). ' + _DRIVER_REMEDY
    denied = [str(p) for p in nodes if not os.access(p, os.R_OK | os.W_OK)]
    if denied:
        return 'No read/write permission for DRM nodes: ' + ', '.join(denied) + '. ' + _DRIVER_REMEDY
    return None


def discover_roots(extra_roots=None, managed_root: Path | None = None) -> list[Path]:
    """Bounded, nonrecursive search roots; includes environment and venv layouts."""
    roots = [Path(p).expanduser() for p in (extra_roots or ())]
    roots += [Path(os.environ[k]).expanduser() for k in ('ROCM_PATH', 'HIP_PATH') if os.environ.get(k)]
    if managed_root is not None:
        roots.append(Path(managed_root).expanduser())
    roots += sorted(Path('/opt').glob('rocm*'), reverse=True)
    roots += [Path(p) for p in ('/usr/lib', '/usr/lib64', '/usr/local/lib', '/usr/local/lib64')]
    roots += sorted(Path('/usr/lib').glob('*-linux-gnu'))
    return list(dict.fromkeys(roots))


def discover_runtimes(extra_roots=None, managed_root: Path | None = None) -> list[Path]:
    found = []
    for root in discover_roots(extra_roots, managed_root):
        if root.is_file():
            candidates = [root]
        else:
            dirs = [root, root/'lib', root/'lib64', root/'_rocm_sdk_core/lib']
            dirs += list(root.glob('lib/python*/site-packages/_rocm_sdk_core/lib'))
            dirs += list(root.glob('lib64/python*/site-packages/_rocm_sdk_core/lib'))
            candidates = [p for d in dirs for p in sorted(d.glob('libamdhip64.so*'))]
        for candidate in candidates:
            if (re.fullmatch(r'libamdhip64\.so(?:\.7(?:\.[\w.-]+)?)?', candidate.name)
                    and candidate.is_file()):
                resolved = candidate.resolve()
                if resolved not in found:
                    found.append(resolved)
    return found


def probe_runtime(lib: Path, timeout=30, *, self_test=False) -> dict:
    """Load in a disposable interpreter; native crashes and hangs stay isolated."""
    if not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout <= 0:
        raise ValueError('Probe timeout must be positive and finite')
    lib = Path(lib).expanduser().resolve()
    if not lib.is_file():
        raise RuntimeError(f'HIP library does not exist: {lib}; supply an actual libamdhip64.so.7')
    argv = [sys.executable, '-I', str(Path(__file__).resolve()), '--probe', str(lib)]
    if self_test:
        argv.append('--self-test')
    env = dict(os.environ)
    # Test the runtime's own RPATH, not accidental Torch/Conda/global loader state.
    for key in ('LD_LIBRARY_PATH', 'LD_PRELOAD', 'LD_AUDIT'):
        env.pop(key, None)
    try:
        process = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, env=env)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f'HIP probe timed out after {timeout}s: {lib}; check the GPU driver') from exc
    except OSError as exc:
        raise RuntimeError(f'Cannot start isolated HIP probe: {exc}') from exc
    try:
        # HIP may write diagnostics to stdout; our record is explicitly delimited.
        output = process.stdout
        if 'DLSSNR_PROBE_JSON=' in output:
            output = output.rsplit('DLSSNR_PROBE_JSON=', 1)[1].splitlines()[0]
        result = json.loads(output)
    except (ValueError, IndexError) as exc:
        raise RuntimeError(f'HIP probe failed (exit {process.returncode}): {lib}; '
                           f'{process.stderr[-2000:] or process.stdout[-2000:] or "native crash/no output"}') from exc
    if not isinstance(result, dict):
        raise RuntimeError('HIP probe returned invalid data')
    if 'error' in result:
        kind = DriverUnavailable if result.get('kind') == 'driver' else RuntimeError
        raise kind(result['error'])
    if process.returncode:
        raise RuntimeError(f'HIP child exited {process.returncode}: {process.stderr[-2000:]}')
    if not isinstance(result.get('runtime_version'), int):
        raise RuntimeError('HIP probe returned an invalid runtime version')
    if result['runtime_version'] // 10000000 != 7:
        raise RuntimeError('HIP7 is required (runtime major must be >=7 and <8)')
    if not result.get('devices'):
        raise DriverUnavailable('HIP reports no devices. Check visibility filters. ' + _DRIVER_REMEDY)
    return result


def _native_probe(lib: Path, self_test: bool) -> dict:
    # Called ONLY in the child. Never guess hipDeviceProp_t field offsets.
    try:
        hip = ctypes.CDLL(str(lib), mode=os.RTLD_NOW | os.RTLD_LOCAL)
    except OSError as exc:
        raise RuntimeError(f'Cannot dlopen {lib}: {exc}. Missing ELF dependencies must be '
                           'provided by a complete HIP7 runtime or the host OS; no major-version '
                           'symlink aliases are supported.') from exc
    missing = [symbol for symbol in REQUIRED_SYMBOLS if not hasattr(hip, symbol)]
    if missing:
        raise RuntimeError('Missing required HIP bridge symbols: ' + ', '.join(missing))

    def function(name, args, restype=ctypes.c_int):
        try:
            fn = getattr(hip, name)
        except AttributeError as exc:
            raise RuntimeError(f'Missing public HIP metadata API: {name}') from exc
        fn.argtypes, fn.restype = args, restype
        return fn

    intp = ctypes.POINTER(ctypes.c_int)
    voidp = ctypes.c_void_p
    sizep = ctypes.POINTER(ctypes.c_size_t)
    errstr = function('hipGetErrorString', [ctypes.c_int], ctypes.c_char_p)

    def check(code, operation, driver=False):
        if code:
            detail = errstr(code)
            message = f'{operation} failed: HIP {code} ({detail.decode(errors="replace") if detail else "unknown"})'
            if driver:
                raise DriverUnavailable(message + '. Check HIP_VISIBLE_DEVICES/ROCR_VISIBLE_DEVICES filters. ' + _DRIVER_REMEDY)
            raise RuntimeError(message)

    version = ctypes.c_int()
    check(function('hipRuntimeGetVersion', [intp])(ctypes.byref(version)), 'hipRuntimeGetVersion')
    if version.value // 10000000 != 7:
        raise RuntimeError(f'HIP7 required, found runtime version {version.value}; HIP8 and major aliases are not supported')
    driver_error = _driver_error()
    if driver_error:
        raise DriverUnavailable(driver_error)
    count = ctypes.c_int()
    check(function('hipGetDeviceCount', [intp])(ctypes.byref(count)), 'hipGetDeviceCount', True)
    if not 0 < count.value <= 128:
        raise DriverUnavailable(f'HIP reports {count.value} devices. Check visibility filters. ' + _DRIVER_REMEDY)
    name_fn = function('hipDeviceGetName', [voidp, ctypes.c_int, ctypes.c_int])
    pci_fn = function('hipDeviceGetPCIBusId', [voidp, ctypes.c_int, ctypes.c_int])
    mem_fn = function('hipDeviceTotalMem', [sizep, ctypes.c_int])
    props_fn = function('hipGetDevicePropertiesR0600', [voidp, ctypes.c_int])
    devices = []
    for index in range(count.value):
        name, pci = ctypes.create_string_buffer(256), ctypes.create_string_buffer(64)
        memory = ctypes.c_size_t()
        # ABI R0600 is fixed; 64 KiB is deliberately oversized, bounded and zeroed.
        props = ctypes.create_string_buffer(65536)
        check(name_fn(name, len(name), index), 'hipDeviceGetName')
        check(pci_fn(pci, len(pci), index), 'hipDeviceGetPCIBusId')
        check(mem_fn(ctypes.byref(memory), index), 'hipDeviceTotalMem')
        check(props_fn(props, index), 'hipGetDevicePropertiesR0600')
        arches = set(re.findall(rb'(?<![A-Za-z0-9])gfx[0-9a-f]{3,5}(?=[:\x00])', props.raw))
        if len(arches) != 1:
            raise RuntimeError(f'Cannot unambiguously extract gfx architecture for device {index}')
        devices.append({'index': index, 'name': name.value.decode(errors='replace'),
                        'arch': arches.pop().decode('ascii'), 'pci_bus_id': pci.value.decode('ascii'),
                        'total_memory': memory.value})
        if self_test:
            check(function('hipSetDevice', [ctypes.c_int])(index), 'hipSetDevice')
            ptr = voidp()
            check(function('hipMalloc', [ctypes.POINTER(voidp), ctypes.c_size_t])(ctypes.byref(ptr), 256), 'hipMalloc')
            try:
                src = ctypes.create_string_buffer(bytes(range(256)), 256)
                dst = ctypes.create_string_buffer(256)
                copy = function('hipMemcpy', [voidp, voidp, ctypes.c_size_t, ctypes.c_int])
                check(copy(ptr, src, 256, 1), 'hipMemcpy host-to-device')
                check(copy(dst, ptr, 256, 2), 'hipMemcpy device-to-host')
                if src.raw != dst.raw:
                    raise RuntimeError(f'Memory copy mismatch on GPU {index}')
            finally:
                check(function('hipFree', [voidp])(ptr), 'hipFree')
    # RPATH resolves dependencies; no LD_LIBRARY_PATH injection is necessary.
    # Expose only actual loaded runtime directories for Steam bind mounts.
    runtime_root = lib.parent.parent if lib.parent.name in ('lib', 'lib64') else lib.parent
    loaded = {str(lib.parent)}
    try:
        for line in Path('/proc/self/maps').read_text().splitlines():
            fields = line.split(None, 5)
            if len(fields) == 6 and fields[5].startswith('/'):
                mapped = Path(fields[5].replace('\\040', ' '))
                if mapped.is_relative_to(runtime_root) and mapped.is_file():
                    loaded.add(str(mapped.parent))
    except OSError:
        pass
    return {'runtime_version': version.value, 'library': str(lib), 'library_dirs': [],
            'dependency_dirs': sorted(loaded), 'mount_roots': [str(runtime_root)],
            'devices': devices, 'self_test': 'passed' if self_test else 'not_run'}


def _run(argv, timeout):
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f'Runtime setup command failed: {argv[0]}: {exc}') from exc
    if proc.returncode:
        raise RuntimeError(f'Runtime setup failed (exit {proc.returncode}): {proc.stderr[-4000:]} {proc.stdout[-2000:]}')
    return proc


def _download_wheel(destination: Path) -> str:
    """Bound bytes, elapsed time, TLS origin and digest before invoking pip."""
    start = time.monotonic()
    digest = hashlib.sha256()
    size = 0
    try:
        with urllib.request.urlopen(WHEEL_URL, timeout=30) as response, destination.open('wb') as out:
            final = urllib.parse.urlsplit(response.geturl())
            if final.scheme != 'https' or final.hostname != 'rocm.nightlies.amd.com':
                raise RuntimeError('AMD wheel redirected outside the official HTTPS origin')
            if int(response.headers.get('Content-Length', '0')) > MAX_WHEEL_BYTES:
                raise RuntimeError('AMD wheel exceeds download size limit')
            while True:
                block = response.read(1024 * 1024)
                if not block:
                    break
                size += len(block)
                if size > MAX_WHEEL_BYTES or time.monotonic() - start > 240:
                    raise RuntimeError('AMD wheel exceeded size/time download limit')
                digest.update(block)
                out.write(block)
        if digest.hexdigest() != WHEEL_SHA256:
            raise RuntimeError('AMD wheel SHA256 mismatch; refusing installation')
        return digest.hexdigest()
    except (OSError, ValueError) as exc:
        raise RuntimeError(f'AMD wheel download failed: {exc}') from exc


def _wheel_metadata(wheel: Path) -> dict:
    with zipfile.ZipFile(wheel) as archive:
        if sum(i.file_size for i in archive.infolist()) > MAX_EXPANDED_BYTES:
            raise RuntimeError('AMD wheel exceeds expanded size limit')
        names = archive.namelist()
        records = [n for n in names if n.endswith('.dist-info/METADATA')]
        if len(records) != 1 or archive.getinfo(records[0]).file_size > 1024 * 1024:
            raise RuntimeError('Invalid wheel metadata')
        metadata = email.parser.Parser().parsestr(archive.read(records[0]).decode())
        if metadata['Name'] != 'rocm-sdk-core' or metadata['Version'] != WHEEL_VERSION:
            raise RuntimeError('AMD wheel package/version does not match pinned metadata')
        requires = metadata.get_all('Requires-Dist', [])
        if requires:
            raise RuntimeError('Core wheel requires additional packages; refusing implicit installs: ' + ', '.join(requires))
        if '_rocm_sdk_core/lib/libamdhip64.so.7' not in names:
            raise RuntimeError('Core wheel does not contain libamdhip64.so.7')
        return {'name': metadata['Name'], 'version': metadata['Version'], 'requires_dist': requires}


def _install_wheel(cache_root: Path) -> dict:
    from .deploy import _safe
    cache_root = _safe(Path(cache_root).expanduser(), directory=True, missing=True)
    if platform.system() != 'Linux' or platform.machine() != 'x86_64':
        raise RuntimeError('Pinned AMD core wheel requires Linux x86_64')
    cache_root.mkdir(parents=True, exist_ok=True)
    if shutil.disk_usage(cache_root).free < MIN_FREE_BYTES:
        raise RuntimeError('ROCm installation needs at least 3 GiB free space (wheel, extraction, venv)')
    target = cache_root/'rocm-venv'
    marker = target/'.dlssnr-managed'
    _safe(target, directory=True, missing=True)
    _safe(marker, missing=True)
    marker_text = 'DLSSNR managed ROCm venv\n'
    if target.exists() and (not marker.is_file()
            or target.stat().st_uid != os.getuid() or target.stat().st_mode & 0o022
            or marker.stat().st_uid != os.getuid() or marker.stat().st_mode & 0o022
            or marker.stat().st_nlink != 1 or marker.stat().st_size != len(marker_text)
            or marker.read_bytes() != marker_text.encode()):
        raise RuntimeError(f'Refusing to overwrite an unmanaged venv: {target}; use a fresh cache root')
    for name in ('bin', 'lib'):
        _safe(target/name, directory=True, missing=True)
    _safe(cache_root/'.runtime-install.lock', missing=True)
    import fcntl
    with (cache_root/'.runtime-install.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError('Another runtime installation is in progress') from exc
        with tempfile.TemporaryDirectory(prefix='wheel-', dir=cache_root) as temporary:
            wheel = Path(temporary)/WHEEL_URL.rsplit('/', 1)[1]
            digest = _download_wheel(wheel)
            metadata = _wheel_metadata(wheel)
            target.mkdir(exist_ok=True)
            if not marker.exists():
                with marker.open('x') as out:
                    out.write(marker_text)
            _run([sys.executable, '-I', '-m', 'venv', str(target)], 90)
            _run([str(target/'bin/python'), '-I', '-m', 'pip', '--isolated', 'install',
                  '--no-index', '--no-cache-dir', '--no-deps', '--only-binary=:all:',
                  '--force-reinstall', str(wheel)], 180)
            installed = list(target.glob('lib/python*/site-packages/rocm_sdk_core-*.dist-info/METADATA'))
            if len(installed) != 1:
                raise RuntimeError('Cannot verify installed core package metadata')
            actual = email.parser.Parser().parsestr(installed[0].read_text())
            if actual['Name'] != metadata['name'] or actual['Version'] != metadata['version']:
                raise RuntimeError('Installed core metadata differs from verified wheel')
            metadata.update(url=WHEEL_URL, sha256=digest, bytes=wheel.stat().st_size,
                            installed_metadata_sha256=hashlib.sha256(installed[0].read_bytes()).hexdigest())
            _write_json(target/'wheel-provenance.json', metadata)
            return metadata


def _write_json(path: Path, data: dict):
    from .deploy import _safe
    _safe(path, missing=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix='.' + path.name, dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as out:
            json.dump(data, out, indent=2)
            out.write('\n')
            out.flush()
            os.fsync(out.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def ensure_runtime(cache_root: Path, supplied: Path | None = None, allow_install=False,
                   runner_validator=None) -> dict:
    """Discover and validate, or install only with explicit consent. Raises RuntimeError.

    Successful state is recorded under cache_root, never in a game or global prefix.
    Explicit supplied paths are authoritative: failures do not silently fall back.
    """
    from .deploy import _safe
    cache_root = _safe(Path(cache_root).expanduser(), directory=True, missing=True)
    managed = cache_root/'rocm-venv'
    errors = []

    def validate_runner(result):
        if runner_validator is not None:
            result = dict(result, runner_probe=runner_validator(result))
        return result

    def record(result, wheel=None):
        if wheel is None and Path(result['library']).is_relative_to(managed):
            provenance = managed/'wheel-provenance.json'
            if provenance.is_file():
                wheel = json.loads(provenance.read_text())
        if wheel is not None:
            result = dict(result, wheel=wheel)
        _write_json(cache_root/'runtime-manifest.json', result)
        return result

    if supplied is not None:
        candidates = discover_runtimes([Path(supplied)]) if Path(supplied).is_dir() else [Path(supplied)]
        # Directory supply must never escape into automatically discovered roots.
        if Path(supplied).is_dir():
            base = Path(supplied).expanduser().resolve()
            candidates = [p for p in candidates if p.is_relative_to(base)]
        if not candidates:
            raise RuntimeError(f'No HIP7 library found in supplied path: {supplied}')
    else:
        candidates = discover_runtimes(managed_root=managed)
    for candidate in candidates:
        try:
            result = probe_runtime(candidate)
        except DriverUnavailable:
            raise
        except RuntimeError as exc:
            errors.append(str(exc))
            continue
        try:
            return record(validate_runner(result))
        except RunnerDependencyError as exc:
            errors.append(str(exc))
    if supplied is not None:
        raise RuntimeError('; '.join(errors))
    driver_error = _driver_error()
    if driver_error:
        raise DriverUnavailable(driver_error)
    if not allow_install:
        raise RuntimeError('No compatible HIP7 runtime found. Supply a complete HIP7 runtime or '
                           'give download consent with allow_install=True / --install-rocm. '
                           + '; '.join(errors))
    wheel = _install_wheel(cache_root)
    installed = [p for p in discover_runtimes(managed_root=managed) if p.is_relative_to(managed)]
    if not installed:
        raise RuntimeError('Installed AMD core wheel contains no discoverable HIP7 library')
    try:
        return record(validate_runner(probe_runtime(installed[0])), wheel)
    except RuntimeError as exc:
        raise RuntimeError(f'AMD core wheel installed but is not usable: {exc}. '
                           'No Torch/devel packages were installed. Supply a complete compatible '
                           'HIP7 runtime if host ELF dependencies are unavailable.') from exc


if __name__ == '__main__':
    if len(sys.argv) >= 3 and sys.argv[1] == '--probe':
        try:
            output = _native_probe(Path(sys.argv[2]).resolve(), '--self-test' in sys.argv[3:])
            code = 0
        except Exception as exc:
            output = {'error': str(exc), 'kind': 'driver' if isinstance(exc, DriverUnavailable) else 'runtime'}
            code = 1
        print('DLSSNR_PROBE_JSON=' + json.dumps(output), flush=True)
        sys.exit(code)
    sys.exit('Internal probe entry point. Use the installer runtime command or ensure_runtime().')
