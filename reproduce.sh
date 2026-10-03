#!/usr/bin/env bash
set -euo pipefail
PACKAGE_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$PACKAGE_DIR"
export PYTHONPATH="$PACKAGE_DIR/src"
PYTHON_BIN="${LACUNA_PYTHON:-$PACKAGE_DIR/.venv/bin/python}"
if [[ ! -x "$PYTHON_BIN" ]]; then PYTHON_BIN=python3; fi
exec "$PYTHON_BIN" scripts/reproduce.py "$@"
