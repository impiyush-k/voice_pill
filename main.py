"""
Voice Pill — Root Launcher Entry Point
======================================
Forwards execution to src/main.py while keeping the root clean.
"""

import os
import sys

_PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
_SRC_DIR = os.path.join(_PROJECT_ROOT, "src")

if _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)

from main import main

if __name__ == "__main__":
    main()
