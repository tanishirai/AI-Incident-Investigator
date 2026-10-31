#!/usr/bin/env bash
set -euo pipefail
python3 --version
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
[[ -f .env ]] || cp .env.example .env
python evaluation/generate_dataset.py
python scripts/load_demo.py
echo "Setup complete. Start the app with ./run.sh"
