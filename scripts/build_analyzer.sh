#!/usr/bin/env bash
set -euo pipefail
PACKAGE_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PACKAGE_DIR"
docker build -f docker/analyzer.Dockerfile -t lacuna-analyzer:1.6.10 .
