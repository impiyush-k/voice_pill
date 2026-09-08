#!/usr/bin/env bash
# Voice Pill — Linux Launcher Script
# ==================================
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$SCRIPT_DIR"

if [ -f "./venv/bin/python" ]; then
    PYTHON_BIN="./venv/bin/python"
else
    PYTHON_BIN="python3"
fi

echo "Starting Voice Pill on Linux using $PYTHON_BIN..."
export PYTHONPATH="$SCRIPT_DIR/src:$PYTHONPATH"
exec "$PYTHON_BIN" src/main.py "$@"
