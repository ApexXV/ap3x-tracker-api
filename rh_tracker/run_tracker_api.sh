#!/usr/bin/env bash
set -e

SCRIPT_DIR="/home/ap3x/scripts/rh_tracker"
VENV_PYTHON="/home/ap3x/rh_env/bin/python"

cd "$SCRIPT_DIR"

exec "$VENV_PYTHON" -B -m uvicorn \
  --app-dir /home/ap3x/scripts/rh_tracker \
  tracker_api:app \
  --host 127.0.0.1 \
  --port 8000
