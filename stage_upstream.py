#!/usr/bin/env python3
"""Stage the pinned upstream release for the ordered Linux bridge, not a game.

No downloads or runtime execution. Accepts only the independently inspected setup,
embedded DLL and original HLSL hashes. Never use the no-poll DLL without the
matching ordered vkd3d/HIP bridge. Stdlib only; PE validation uses dlssnr.games.
"""
import argparse
import hashlib
import json
from pathlib import Path
import struct
import tempfile

from dlssnr.games import pe_info

RELEASE_SHA = '87dbde90f1249ceb32e06ca4d68c34fd8b5d9d1130fd4ee4560a0eb33079b876'
PAYLOAD_SHA = 'e350e6137a5bb514f40465bafbd6d5411f2310b4ea9de2590f6229d5f9555212'
SHADER_SHA = 'cc20edf91b7d8df2e69868809765436c0c975c363760f27f46ff15d7c4d9e58d'
NO_POLL = '''
RWByteAddressBuffer flags : register(u0);
cbuffer C : register(b0) { uint mode; uint value; uint maxIter; uint pad; }
[numthreads(1, 1, 1)] void main() {
    if (mode == 0) { flags.Store(0, value); return; }
    flags.Store(16, 0);
}
'''


def sha(data):
    return hashlib.sha256(data).hexdigest()


def stage(setup):
    if sha(setup) != RELEASE_SHA:
        raise ValueError('Unverified setup: expected upstream v0.2.12 release SHA256.')
    if setup[-32:-16] != b'DLSSNR-SETUP-01\0':
        raise ValueError('Unrecognized setup footer.')
    size, ini_size = struct.unpack('<QQ', setup[-16:])
    offset = len(setup) - 32 - ini_size - size
    if offset <= 0 or size <= 0:
        raise ValueError('Invalid embedded payload bounds.')
    original = setup[offset:offset + size]
    if sha(original) != PAYLOAD_SHA:
        raise ValueError('Unverified embedded DLL.')
    with tempfile.TemporaryDirectory(prefix='dlssnr-stage-') as temp:
        path = Path(temp) / 'version.dll'
        path.write_bytes(original)
        pe = pe_info(path)
    sections = pe['sections']
    if (pe['machine'] != 0x8664 or not pe['is_dll'] or
            max(s['raw_offset'] + s['raw_size'] for s in sections) != size or
            not any(s['name'] == '.hip_fat' for s in sections)):
        raise ValueError('Invalid embedded HIP PE layout.')
    needle = b'globallycoherent RWByteAddressBuffer flags'
    if original.count(needle) != 1:
        raise ValueError('Missing or ambiguous HLSL anchor.')
    pos = original.index(needle)
    start = original.rfind(b'\0', 0, pos) + 1
    end = original.index(b'\0', pos)
    if sha(original[start:end]) != SHADER_SHA:
        raise ValueError('Original shader differs from the inspected contract.')
    section = next(s for s in sections if s['raw_offset'] <= start < s['raw_offset'] + s['raw_size'])
    if section['name'] != '.rdata' or end >= section['raw_offset'] + section['raw_size']:
        raise ValueError('Shader outside .rdata.')
    replacement = NO_POLL.encode().ljust(end - start)
    if len(replacement) != end - start:
        raise ValueError('Replacement shader too large.')
    result = original[:start] + replacement + original[end:]
    changed = [s['name'] for s in sections if
               original[s['raw_offset']:s['raw_offset'] + s['raw_size']] !=
               result[s['raw_offset']:s['raw_offset'] + s['raw_size']]]
    if changed != ['.rdata']:
        raise ValueError('Unexpected section modification.')
    return result, {'mod_version': '0.2.12', 'setup_sha256': RELEASE_SHA,
                    'original_payload_sha256': PAYLOAD_SHA, 'staged_sha256': sha(result),
                    'payload_offset': offset, 'payload_bytes': size,
                    'shader_offset': start, 'shader_bytes': end - start,
                    'original_shader_sha256': SHADER_SHA, 'patched_shader_sha256': sha(replacement),
                    'changed_sections': changed, 'native_code_unchanged': True,
                    'gameplay_verified': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('setup', type=Path)
    parser.add_argument('output_dir', type=Path, help='New staging directory; must not already exist')
    args = parser.parse_args()
    dll, report = stage(args.setup.read_bytes())
    args.output_dir.mkdir(parents=True, exist_ok=False)
    (args.output_dir / 'version.dll').write_bytes(dll)
    (args.output_dir / 'staging.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
