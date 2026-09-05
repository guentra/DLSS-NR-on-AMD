"""Package integrity and user-owned DLSS-NR weights (stdlib, no downloads)."""
from pathlib import Path
import hashlib
import json
import re
import struct


def sha256(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda:f.read(1024*1024),b''):h.update(block)
    return h.hexdigest()


def verify_assets(package_root):
    root=Path(package_root)/'assets'
    try:manifest=json.loads((root/'manifest.json').read_text())
    except (OSError,ValueError) as e:raise RuntimeError(f'Cannot read asset manifest: {e}') from e
    if not isinstance(manifest,dict):raise RuntimeError('Empty/invalid manifest')
    files=manifest.get('files')
    if not isinstance(files,dict) or not files:raise RuntimeError('Empty/invalid manifest')
    for name,expected in files.items():
        if not isinstance(name,str) or not re.fullmatch(r'[A-Za-z0-9_.-]+',name) or name in ('.','..'):
            raise RuntimeError('Disallowed path in manifest')
        if not isinstance(expected,str) or not re.fullmatch('[0-9a-f]{64}',expected):
            raise RuntimeError('Invalid checksum in manifest')
        p=root/name
        if p.is_symlink() or not p.is_file() or sha256(p)!=expected:
            raise RuntimeError(f'Missing/modified asset: {p}')
    return manifest


def validate_weights(path):
    """Validate actual W1 format: header/table offsets, names, all blob ranges.
    Content authenticity is not implied; this must be the user's legitimate file.
    """
    p=Path(path)
    if not p.is_file():raise RuntimeError(f'Weights file not found: {p}')
    size=p.stat().st_size
    if size<16 or size>4*1024**3:raise RuntimeError('Invalid weights size')
    with p.open('rb') as f:
        header=f.read(16)
        if header[:8]!=b'DLSSNRW1':raise RuntimeError('Invalid weights: missing DLSSNRW1 signature')
        count,start=struct.unpack_from('<II',header,8)
        if not 1<=count<=8192 or not 16<=start<=min(size,8*1024**2):
            raise RuntimeError('Weights index out of bounds')
        table=f.read(start-16)
    position=0; ranges=[]; names=set()
    for _ in range(count):
        if position>=len(table):raise RuntimeError('Truncated weights index')
        length=table[position];position+=1
        if not length or position+length+16>len(table):raise RuntimeError('Truncated weights entry')
        raw=table[position:position+length];position+=length
        try:name=raw.rstrip(b'\0').decode('ascii')
        except UnicodeDecodeError as e:raise RuntimeError('Invalid weight name') from e
        if not name or name in names or '\0' in name:raise RuntimeError('Empty/duplicate weight name')
        names.add(name)
        offset,length=struct.unpack_from('<QQ',table,position);position+=16
        if not length or offset>size-start or length>size-start-offset:
            raise RuntimeError('Weights data truncated/out of bounds')
        ranges.append((offset,offset+length))
    if position!=len(table):raise RuntimeError('Inconsistent weights table size')
    ranges.sort()
    if any(a[1]>b[0] for a,b in zip(ranges,ranges[1:])):raise RuntimeError('Overlapping weights')
    if ranges[-1][1]!=size-start:raise RuntimeError('Inconsistent weights end of file')
    return {'path':str(p.resolve()),'blobs':count,'bytes':size,'sha256':sha256(p)}
