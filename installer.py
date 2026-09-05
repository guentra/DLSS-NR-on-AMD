#!/usr/bin/env python3
"""Portable installer entry point; no third-party Python packages required."""
import sys

if sys.version_info < (3, 10):
    sys.exit('Python 3.10 or newer is required. Run with a supported Python interpreter.')
sys.dont_write_bytecode = True
from dlssnr.cli import main

if __name__ == '__main__':
    sys.exit(main())
