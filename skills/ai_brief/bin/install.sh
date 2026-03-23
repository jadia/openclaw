#!/usr/bin/env bash
set -euo pipefail

BASE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "Setting up virtual environment for ai_brief..."
cd "$BASE_DIR"

if [ ! -d ".venv" ] || [ ! -f ".venv/bin/pip" ]; then
  echo "Creating virtual environment..."
  rm -rf .venv
  
  # Try to use virtualenv if available, fallback to python3 -m virtualenv
  if command -v virtualenv >/dev/null 2>&1; then
    VENV_CMD="virtualenv .venv"
  else
    VENV_CMD="python3 -m virtualenv .venv"
  fi

  if ! $VENV_CMD; then
    echo -e "\n[!] ERROR: Failed to create virtual environment."
    echo "Make sure you have virtualenv installed:"
    echo "    pip install virtualenv"
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
