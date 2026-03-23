#!/usr/bin/env bash
set -euo pipefail

BASE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "Setting up virtual environment for ai_brief..."
cd "$BASE_DIR"

if [ ! -d ".venv" ] || [ ! -f ".venv/bin/pip" ]; then
  echo "Creating virtual environment..."
  rm -rf .venv
  if ! python3 -m venv .venv; then
    echo -e "\n[!] ERROR: Failed to create virtual environment."
    echo "This is usually because the 'venv' package is missing on your system."
    if command -v apt-get >/dev/null 2>&1; then
      echo "On Debian/Ubuntu, fix this by running:"
      echo "    sudo apt install python3-venv"
    fi
    rm -rf .venv
    exit 1
  fi
fi

source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

echo "Setup complete. The skill will use the isolated python environment automatically."
echo "Running initial setup to create state directories..."
.venv/bin/python bin/main.py --setup

echo -e "\nai_brief is ready!"
