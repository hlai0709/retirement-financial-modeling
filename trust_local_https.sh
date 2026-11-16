#!/bin/bash
# One-time setup: trust local HTTPS certificates in Safari (requires your Mac password).
set -euo pipefail
if ! command -v mkcert >/dev/null 2>&1; then
  echo "Installing mkcert..."
  brew install mkcert
fi
mkcert -install
DATA_DIR="$HOME/Library/Application Support/RetirementPlanner"
mkdir -p "$DATA_DIR"
mkcert -key-file "$DATA_DIR/localhost.key" -cert-file "$DATA_DIR/localhost.crt" 127.0.0.1 localhost
echo 4 > "$DATA_DIR/localhost_cert_version"
echo "Done. Safari should now trust https://127.0.0.1:8765/"