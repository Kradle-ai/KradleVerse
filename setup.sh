#!/usr/bin/env bash
set -euo pipefail

PLUGIN_ROOT="${1:-$(dirname "$0")}"
DATA_DIR="$HOME/.kradle/kradleverse"

# Install deps (fast no-op if already present)
pip install --user --quiet kradle requests python-dotenv 2>/dev/null || true

# Create data dir + .env template if missing
mkdir -p "$DATA_DIR"
if [ ! -f "$DATA_DIR/.env" ]; then
  printf 'KRADLEVERSE_AGENT_NAME=\nKRADLEVERSE_API_KEY=\n' > "$DATA_DIR/.env"
fi

# Write plugin path so skills can find the scripts
echo "$PLUGIN_ROOT" > "$DATA_DIR/.plugin-path"
