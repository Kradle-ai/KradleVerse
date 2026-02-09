#!/usr/bin/env bash
set -euo pipefail

# Usage: setup-generic.sh [skills_dir_name]
# skills_dir_name: directory name under $HOME for skills (default: .agents)
#   e.g. ".openclaw" installs skills to ~/.openclaw/skills/kradleverse/
#        ".agents"   installs skills to ~/.agents/skills/kradleverse/

SKILLS_BASE="${1:-.agents}"
REPO_URL="https://github.com/Kradle-ai/KradleVerse.git"
REPO_DIR="$HOME/.kradle/kradleverse/repo"

# Clone or update the repo
if [ -d "$REPO_DIR/.git" ]; then
  echo "Updating KradleVerse..."
  git -C "$REPO_DIR" pull --quiet
else
  echo "Cloning KradleVerse..."
  mkdir -p "$(dirname "$REPO_DIR")"
  git clone --quiet --depth 1 --single-branch "$REPO_URL" "$REPO_DIR"
fi

echo "Successfully cloned the repository to $REPO_DIR. Installing KradleVerse dependencies..."

# Run setup (creates venv, .env template, copies scripts)
bash "$REPO_DIR/setup.sh" "$REPO_DIR"

# Install skills
SKILLS_DIR="$HOME/$SKILLS_BASE/skills/kradleverse"

echo "Installing KradleVerse skills to ~/$SKILLS_BASE/skills/kradleverse/..."
for skill_dir in "$REPO_DIR"/skills/*/; do
  skill="$(basename "$skill_dir")"
  mkdir -p "$SKILLS_DIR/$skill"
  cp "$skill_dir/SKILL.md" "$SKILLS_DIR/$skill/SKILL.md"
done

echo "Done! Tell your AI agent to initialize an agent and join a game on KradleVerse!"
