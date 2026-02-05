#!/usr/bin/env bash
set -euo pipefail

PLUGIN_ROOT="$(cd "${1:-$(dirname "$0")}" && pwd)"
DATA_DIR="$HOME/.kradle/kradleverse"
VENV_DIR="$DATA_DIR/venv"

# Create data dir + .env template if missing
mkdir -p "$DATA_DIR"
if [ ! -f "$DATA_DIR/.env" ]; then
  printf 'KRADLEVERSE_AGENT_NAME=\nKRADLEVERSE_API_KEY=\n' > "$DATA_DIR/.env"
fi

# Check if venv does not exist
if [ ! -f "$VENV_DIR/bin/python" ] && [ ! -f "$VENV_DIR/Scripts/python.exe" ]; then
  # Use uv if available
  if command -v uv &>/dev/null; then
    uv venv --quiet "$VENV_DIR"
    uv pip install --quiet --python "$VENV_DIR/bin/python" kradle requests python-dotenv
  else
    # Find python
    PYTHON=""
    for cmd in python3 python; do
      if command -v "$cmd" &>/dev/null; then
        PYTHON="$cmd"
        break
      fi
    done
    if [ -z "$PYTHON" ]; then
      echo "kradleverse: python not found — install uv or python3" >&2
      exit 0
    fi
    "$PYTHON" -m venv "$VENV_DIR"
    "$VENV_DIR/bin/pip" install --quiet kradle requests python-dotenv 2>/dev/null || true
  fi
fi

# Copy scripts to a fixed location
cp -r "$PLUGIN_ROOT/scripts/" "$DATA_DIR/scripts/"
