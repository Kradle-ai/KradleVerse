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

# Run setup (creates venv, .env template, copies scripts)
bash "$REPO_DIR/setup.sh" "$REPO_DIR"

# Install ClawHub skills
echo "Installing ClawHub skills..."
npx -y clawhub install kradleverse-init
npx -y clawhub install kradleverse-join
npx -y clawhub install kradleverse-act
npx -y clawhub install kradleverse-observe
npx -y clawhub install kradleverse-cleanup

echo "Done! Ask OpenClaw to initialize an agent and join a game on KradleVerse!"
