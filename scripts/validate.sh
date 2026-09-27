#!/usr/bin/env bash
set -euo pipefail

# Invoke only through: bash .agent/run.sh bash scripts/validate.sh
for script in .agent/run.sh scripts/validate.sh; do
  bash -n "$script"
done
python -m pip install --quiet --no-cache-dir -r scripts/requirements-validation.txt
python scripts/validate.py
