#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"
if [ ! -d .venv ]; then
    python3 -m venv .venv
fi
. .venv/bin/activate
pip install -q -r app/requirements.txt
python run.py
