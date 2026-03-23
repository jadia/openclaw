#!/usr/bin/env bash
set -euo pipefail

BASE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "Setting up virtual environment for ai_brief..."
cd "$BASE_DIR"

if [ ! -d ".venv" ]; then
  python3 -m venv .venv
fi

source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

echo "Setup complete. The skill will use the isolated python environment automatically."
echo "Running initial setup to create state directories..."
.venv/bin/python bin/main.py --setup

echo -e "\nai_brief is ready!"
