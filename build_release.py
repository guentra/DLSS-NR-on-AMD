#!/usr/bin/env python3
"""Build a deterministic personal-use bundle; stdlib only, explicit file allowlist.

No traversal/globbing: adding a file to the workspace never adds it to a release.
This does not confer redistribution rights or compile/load any bundled binary.
"""
import argparse
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import stat
import tarfile
import tempfile

ARCHIVE_ROOT = 'dlssnr-linux-portable'
PAYLOADS = (
    'version.dll', 'd3d12.dll', 'd3d12core.dll', 'amdhip64_7.dll',
    'libdlssnr_hip_bridge.so', 'dlssnr_on_amd_setup.exe', 'hip_probe.exe',
)
PACKAGE_FILES = (
    'installer.py', 'install.sh', 'build_release.py', 'stage_upstream.py', 'README.md',
    'THIRD-PARTY.md', 'PROVENANCE.json',
    'dlssnr/cli.py', 'dlssnr/assets.py', 'dlssnr/conversion.py',
    'dlssnr/deploy.py', 'dlssnr/games.py', 'dlssnr/kernels.py', 'dlssnr/runtime.py', 'dlssnr/runner_probe.py',
    'assets/manifest.json',
    'native/hip_bridge.c', 'native/hip_bridge.h',
    'native/hip_probe.c', 'native/hip_probe_kernel32.def',
    'native/nr_ordered.c', 'native/nr_ordered.h',
    'sources/README.md', 'sources/fetch_vkd3d.py',
    'sources/vkd3d-proton-ordered.patch', 'sources/vkd3d-submodules.json',
    'sources/cross-win64.ini',
    'sources/trampoline/amdhip64_7_pe.c', 'sources/trampoline/hip_bridge.h',
    'sources/trampoline/kernel32.def', 'sources/trampoline/ntdll.def',
    'licenses/vkd3d-proton-LICENSE', 'licenses/vkd3d-proton-COPYING',
    'licenses/vkd3d-proton-AUTHORS', 'licenses/vkd3d-dependency-notices.txt',
    'licenses/DLSS-NR-upstream-notice.txt',
) + tuple('assets/' + name for name in PAYLOADS)


def _read_regular(root, name):
    path = root
    for part in Path(name).parts:
        path = path / part
        if path.is_symlink():
            raise ValueError('Symlink in release input: ' + name)
    if not stat.S_ISREG(path.stat().st_mode):
        raise ValueError('Not a regular release input: ' + name)
    # O_NOFOLLOW also guards replacement of the final component after inspection.
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError('Not a regular release input: ' + name)
        return stream.read()


def build_release(package_root, output_dir=None, *, components_root=None):
    root = Path(package_root).resolve(strict=True)
    components = Path(components_root).resolve(strict=True) if components_root else root
    destination = Path(output_dir) if output_dir is not None else root / 'dist'
    # Snapshot bytes before validation/archive construction, avoiding a hash/copy race.
    component_files = {'assets/' + name for name in PAYLOADS}
    content = {name: _read_regular(components if name in component_files or
                                   name.startswith(('native/', 'sources/', 'licenses/')) else root, name)
               for name in sorted(PACKAGE_FILES)}
    manifest = json.loads(content['assets/manifest.json'])
    hashes = manifest.get('files')
    if not isinstance(hashes, dict) or set(hashes) != set(PAYLOADS):
        raise ValueError('Manifest must contain exactly the allowlisted payloads')
    for name, expected in hashes.items():
        if hashlib.sha256(content['assets/' + name]).hexdigest() != expected:
            raise ValueError('Payload SHA256 mismatch: ' + name)
    destination.mkdir(parents=True, exist_ok=True)
    archive = destination / (ARCHIVE_ROOT + '.tar.gz')
    checksum = destination / (archive.name + '.sha256')
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=destination, prefix='.release-', delete=False) as raw:
            temporary = Path(raw.name)
            with gzip.GzipFile(fileobj=raw, mode='wb', filename='', mtime=0, compresslevel=9) as compressed:
                with tarfile.open(fileobj=compressed, mode='w', format=tarfile.USTAR_FORMAT) as tar:
                    for name, data in content.items():
                        info = tarfile.TarInfo(ARCHIVE_ROOT + '/' + name)
                        info.size = len(data)
                        info.mode = 0o755 if name == 'install.sh' else 0o644
                        info.uid = info.gid = info.mtime = 0
                        info.uname = info.gname = ''
                        tar.addfile(info, io.BytesIO(data))
        digest = hashlib.sha256(temporary.read_bytes()).hexdigest()
        temporary.chmod(0o644)
        os.replace(temporary, archive)
        temporary = None
        with tempfile.NamedTemporaryFile(dir=destination, prefix='.checksum-', delete=False) as raw:
            temporary = Path(raw.name)
            raw.write((digest + '  ' + archive.name + '\n').encode('ascii'))
        temporary.chmod(0o644)
        os.replace(temporary, checksum)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return archive, checksum


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument('--output-dir', type=Path, help='default: ROOT/dist')
    parser.add_argument('--components-root', type=Path,
                        help='Existing verified release directory containing assets/native/sources/licenses')
    args = parser.parse_args(argv)
    try:
        archive, checksum = build_release(args.root, args.output_dir, components_root=args.components_root)
    except (OSError, ValueError) as error:
        parser.exit(1, 'Packaging failed: ' + str(error) + '\n')
    print(archive.resolve())
    print(checksum.resolve())
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
