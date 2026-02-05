#!/usr/bin/env bash
set -euo pipefail

PLUGIN_ROOT="${1:-$(dirname "$0")}"
DATA_DIR="$HOME/.kradle/kradleverse"
VENV_DIR="$DATA_DIR/venv"

# Find python
PYTHON=""
for cmd in python3 python; do
  if command -v "$cmd" &>/dev/null; then
    PYTHON="$cmd"
    break
  fi
done

if [ -z "$PYTHON" ]; then
  echo "kradleverse: python not found, skipping setup" >&2
  exit 0
fi

# Create data dir + .env template if missing
mkdir -p "$DATA_DIR"
if [ ! -f "$DATA_DIR/.env" ]; then
  printf 'KRADLEVERSE_AGENT_NAME=\nKRADLEVERSE_API_KEY=\n' > "$DATA_DIR/.env"
fi

# Create venv and install deps if needed
if [ ! -f "$VENV_DIR/bin/python" ] && [ ! -f "$VENV_DIR/Scripts/python.exe" ]; then
  "$PYTHON" -m venv "$VENV_DIR"
  "$VENV_DIR/bin/pip" install --quiet --upgrade pip 2>/dev/null || true
  "$VENV_DIR/bin/pip" install --quiet kradle requests python-dotenv 2>/dev/null || true
fi

# Write plugin path so skills can find the scripts
echo "$PLUGIN_ROOT" > "$DATA_DIR/.plugin-path"
