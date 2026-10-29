#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
python3 -m venv .venv 2>/dev/null || true
if [ -x .venv/bin/python ]; then
  .venv/bin/python -m pip install -q -r requirements.txt
  exec .venv/bin/python app.py
fi
python3 -m pip install -q -r requirements.txt
exec python3 app.py
