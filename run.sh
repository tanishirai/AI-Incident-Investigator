#!/usr/bin/env bash
set -euo pipefail
if [[ ! -f .venv/bin/activate ]]; then
  echo "Environment missing. Run ./setup.sh first." >&2
  exit 1
fi
source .venv/bin/activate
streamlit run app.py
