#!/usr/bin/env bash
set -euo pipefail
root=$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
python_bin=${PYTHON:-python3}
if ! command -v "$python_bin" >/dev/null 2>&1; then
    printf '%s\n' 'Python 3.10+ is required. Install it or set PYTHON=/path/to/python3.' >&2
    exit 2
fi
exec "$python_bin" -B "$root/installer.py" "$@"
