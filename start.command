#!/bin/bash
# LingQ Import Manager — macOS launcher
#
# Double-click this file in Finder to start the app.
# The browser will open automatically.
#
# FIRST TIME ONLY: macOS may block an unsigned script.
# If that happens, right-click the file → Open → Open.
# After that, double-clicking will work normally.

cd "$(dirname "$0")"

# ── Set up virtual environment if missing ────────────────────────────────── #
if [ ! -f ".venv/bin/activate" ]; then
  echo "First-time setup: creating virtual environment..."
  python3 -m venv .venv
  source .venv/bin/activate
  pip install -r requirements.txt
  playwright install chromium
  echo ""
  echo "Setup complete!"
  echo ""
fi

source .venv/bin/activate
python lingq_app.py
