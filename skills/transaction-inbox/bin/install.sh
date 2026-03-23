#!/usr/bin/env bash
# install.sh — Set up a local virtualenv for transaction-inbox skill.
# This skill uses only Python stdlib, so the venv is mostly for isolation.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKILL_DIR="$(dirname "$SCRIPT_DIR")"
VENV_DIR="$SKILL_DIR/.venv"

echo "==> Setting up transaction-inbox skill..."

if [ ! -d "$VENV_DIR" ]; then
    echo "    Creating virtualenv at $VENV_DIR"
    python3 -m venv "$VENV_DIR"
else
    echo "    Virtualenv already exists at $VENV_DIR"
fi

# Activate and install any requirements (currently stdlib-only)
source "$VENV_DIR/bin/activate"
pip install --quiet --upgrade pip

if [ -f "$SKILL_DIR/requirements.txt" ]; then
    pip install --quiet -r "$SKILL_DIR/requirements.txt"
fi

echo "==> transaction-inbox setup complete."
echo "    Run: .venv/bin/python bin/main.py --setup"
