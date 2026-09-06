"""HIP fatbin ISA aliases for bundled upstream kernels. Stdlib only.

The v0.2.12 payload ships gfx1100/gfx1101/gfx1102/gfx1201 images. RX 9060(XT)
reports gfx1200; HIP then rejects the gfx1201 code object. The community fix
(danielblnc/DLSS-NR-on-AMD#79/#89) retags that image in place: bundle id and
note ``gfx1201`` → ``gfx1200``, ELF ``e_flags`` machine ``0x4e`` → ``0x48``.
This module applies that retag to the already-staged 0.2.12 ``version.dll`` at
install time. It does not load, replace, or ship an older setup.exe.
"""
from __future__ import annotations

import struct

TARGETS = frozenset(('gfx1100', 'gfx1101', 'gfx1102', 'gfx1200', 'gfx1201'))
BUNDLE_MAGIC = b'__CLANG_OFFLOAD_BUNDLE__'
EM_AMDGPU = 224
EF_AMDGPU_MACH = 0xff
EF_GFX1201 = 0x4e
EF_GFX1200 = 0x48


def arch_name(value):
    return (value or '').split(':', 1)[0]


def adapt_version_dll(data, arch):
    """Return payload bytes for ``arch``. Non-gfx1200 GPUs keep the original."""
    if not isinstance(data, (bytes, bytearray)):
        raise RuntimeError('version.dll payload must be bytes.')
    data = bytes(data)
    if arch_name(arch) != 'gfx1200':
        return data
    start, size = _hip_fat_range(data)
    blob = bytearray(data[start:start + size])
    if blob.count(b'gfx1201') == 0:
        if blob.count(b'gfx1200') == 0:
            raise RuntimeError('Bundled HIP kernels have no gfx1201 image to alias as gfx1200.')
        patched = bytes(data)
        _require_gfx1200_image(patched[start:start + size])
        return patched
    blob = blob.replace(b'gfx1201', b'gfx1200')
    _retag_gfx1200_elf(blob)
    _require_gfx1200_image(blob)
    if b'gfx1201' in blob:
        raise RuntimeError('gfx1201 remains in .hip_fat after the gfx1200 alias.')
    return data[:start] + bytes(blob) + data[start + size:]


def _hip_fat_range(data):
    if data[:2] != b'MZ' or len(data) < 0x40:
        raise RuntimeError('version.dll is not a PE image.')
    nt, = struct.unpack_from('<I', data, 0x3c)
    if nt + 24 > len(data) or data[nt:nt + 4] != b'PE\0\0':
        raise RuntimeError('version.dll is not a PE image.')
    count, optsize = struct.unpack_from('<HH', data, nt + 6)[0], struct.unpack_from('<H', data, nt + 20)[0]
    if not 1 <= count <= 96:
        raise RuntimeError('Invalid PE section count.')
    table = nt + 24 + optsize
    if table + count * 40 > len(data):
        raise RuntimeError('Truncated PE section table.')
    found = []
    for i in range(count):
        raw = data[table + i * 40:table + i * 40 + 40]
        name = raw[:8].split(b'\0', 1)[0].decode('ascii', 'replace')
        vsize, _va, rsize, offset = struct.unpack_from('<IIII', raw, 8)
        if offset > len(data) or rsize > len(data) - offset:
            raise RuntimeError('PE section outside the file.')
        if name == '.hip_fat':
            found.append((offset, min(vsize, rsize) if vsize else rsize))
    if len(found) != 1 or found[0][1] < len(BUNDLE_MAGIC) + 8:
        raise RuntimeError('version.dll is missing a usable .hip_fat section.')
    return found[0]


def _bundle_entries(blob):
    if not bytes(blob).startswith(BUNDLE_MAGIC):
        raise RuntimeError('.hip_fat is not a clang offload bundle.')
    n, = struct.unpack_from('<Q', blob, 24)
    if not 1 <= n <= 16:
        raise RuntimeError('Invalid clang offload bundle count.')
    pos = 32
    entries = []
    for _ in range(n):
        if pos + 24 > len(blob):
            raise RuntimeError('Truncated clang offload bundle.')
        offset, size, length = struct.unpack_from('<QQQ', blob, pos)
        pos += 24
        if length > 256 or pos + length > len(blob):
            raise RuntimeError('Invalid clang offload bundle id.')
        ident = bytes(blob[pos:pos + length])
        pos += length
        if size and (offset > len(blob) or size > len(blob) - offset):
            raise RuntimeError('Clang offload image outside .hip_fat.')
        entries.append((offset, size, ident))
    return entries


def _retag_gfx1200_elf(blob):
    retagged = 0
    for offset, size, ident in _bundle_entries(blob):
        if b'gfx1200' not in ident or size < 64:
            continue
        if bytes(blob[offset:offset + 5]) != b'\x7fELF\x02':
            raise RuntimeError('gfx1200 alias image is not ELF64.')
        machine, = struct.unpack_from('<H', blob, offset + 18)
        flags, = struct.unpack_from('<I', blob, offset + 48)
        if machine != EM_AMDGPU:
            raise RuntimeError('gfx1200 alias image is not an AMDGPU code object.')
        if (flags & EF_AMDGPU_MACH) != EF_GFX1201:
            if (flags & EF_AMDGPU_MACH) != EF_GFX1200:
                raise RuntimeError('gfx1200 alias image has an unexpected ELF machine id.')
            continue
        struct.pack_into('<I', blob, offset + 48, (flags & ~EF_AMDGPU_MACH) | EF_GFX1200)
        retagged += 1
    if retagged != 1:
        raise RuntimeError('Expected exactly one gfx1201 code object to alias as gfx1200.')


def _require_gfx1200_image(blob):
    matches = [ident for _offset, size, ident in _bundle_entries(blob) if b'gfx1200' in ident and size]
    if len(matches) != 1:
        raise RuntimeError('Bundled HIP kernels have no gfx1200-capable image.')
