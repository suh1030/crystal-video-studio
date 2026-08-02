#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"
if [ ! -d .venv ]; then
  python3 -m venv .venv
  .venv/bin/python -m pip install -r requirements.txt
fi
if [ "$#" -eq 0 ]; then
  .venv/bin/python web_server.py
else
  .venv/bin/python generate_video.py "$@"
fi
