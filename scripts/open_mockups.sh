#!/usr/bin/env bash
# Serve Liquid Glass HTML mockups locally (file:// often blocks the JS viewers).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
MOCKUPS="$ROOT/design/mockups"
PORT="${MOCKUP_PORT:-8765}"

if ! curl -sf "http://127.0.0.1:${PORT}/index.html" >/dev/null 2>&1; then
  echo "Starting mockup server on http://127.0.0.1:${PORT}/"
  (cd "$MOCKUPS" && python3 -m http.server "$PORT" >/dev/null 2>&1 &)
  sleep 0.4
fi

open "http://127.0.0.1:${PORT}/index.html"