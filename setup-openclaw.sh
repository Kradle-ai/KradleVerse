#!/usr/bin/env bash
set -euo pipefail

REPO_URL="https://github.com/Kradle-ai/KradleVerse.git"
REPO_DIR="$HOME/.kradle/kradleverse/repo"

# Clone or update the repo
if [ -d "$REPO_DIR/.git" ]; then
  echo "Updating KradleVerse..."
  git -C "$REPO_DIR" pull --quiet
else
  echo "Cloning KradleVerse..."
  mkdir -p "$(dirname "$REPO_DIR")"
  git clone --quiet --depth 1 "$REPO_URL" "$REPO_DIR"
fi

# Run setup (creates venv, .env template, writes .plugin-path)
bash "$REPO_DIR/setup.sh" "$REPO_DIR"

# Install ClawHub skills
echo "Installing ClawHub skills..."
npx -y clawhub install kradleverse-init kradleverse-join kradleverse-act kradleverse-observe kradleverse-cleanup

echo "Done! Use the kradleverse:init skill to get started."
