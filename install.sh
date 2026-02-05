#!/usr/bin/env bash
set -euo pipefail

INSTALL_DIR="$HOME/.kradle/kradleverse"
VENV_DIR="$INSTALL_DIR/venv"
REPO_BASE="https://raw.githubusercontent.com/kradle-ai/kradleverse/main/scripts"
SCRIPTS=(kradleverse.py observer.py act.py get_observations.py)

echo "Installing Kradleverse scripts to $INSTALL_DIR..."

# Create install directory
mkdir -p "$INSTALL_DIR"

# Download Python scripts and make them executable
for script in "${SCRIPTS[@]}"; do
  curl -sf -o "$INSTALL_DIR/$script" "$REPO_BASE/$script"
  chmod +x "$INSTALL_DIR/$script"
done

# Create .env file if it doesn't exist
if [ ! -f "$INSTALL_DIR/.env" ]; then
  cat > "$INSTALL_DIR/.env" << 'EOF'
KRADLEVERSE_AGENT_NAME=
KRADLEVERSE_API_KEY=
EOF
  echo "Created $INSTALL_DIR/.env — fill in your credentials."
fi

# Create venv and install Python dependencies
echo "Setting up Python virtual environment..."
python3 -m venv "$VENV_DIR"
"$VENV_DIR/bin/pip" install --upgrade pip
"$VENV_DIR/bin/pip" install --upgrade kradle requests python-dotenv

echo "Done!"
