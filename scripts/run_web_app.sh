#!/usr/bin/env bash
# Launch Retirement Planner with approved Liquid Glass HTML/CSS (pywebview).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
exec python3 main.py --web