#!/usr/bin/env bash
# Build a self-contained Retirement Planner.app (py2app) for double-click launch.
set -euo pipefail
cd "$(dirname "$0")/.."
PROJECT_ROOT="$PWD"
APP_NAME="Retirement Planner"
APP_PATH="dist/${APP_NAME}.app"
SUPPORT_DIR="$HOME/Library/Application Support/RetirementPlanner"

if [[ ! -d .venv ]]; then
  echo "Create a venv first:"
  echo "  python3 -m venv .venv && .venv/bin/pip install -r requirements.txt"
  exit 1
fi

# shellcheck disable=SC1091
source .venv/bin/activate

if [[ ! -f design/AppIcon.icns ]]; then
  echo "Building app icon…"
  ./scripts/build_app_icon.sh
fi

pip install -q py2app pywebview

echo "Cleaning previous py2app output…"
rm -rf build dist
mkdir -p dist

echo "Building ${APP_NAME}.app (standalone py2app — may take several minutes)…"
python setup.py py2app

if [[ ! -d "$APP_PATH" ]]; then
  echo "Build failed — dist app not found."
  exit 1
fi

APP_SIZE=$(du -sm "$APP_PATH" | awk '{print $1}')
if [[ "$APP_SIZE" -lt 40 ]]; then
  echo "Warning: bundle is only ${APP_SIZE}MB — expected a standalone py2app build (>40MB)."
fi

if command -v codesign >/dev/null 2>&1; then
  codesign --force --deep --sign - "$APP_PATH" 2>/dev/null || true
  xattr -cr "$APP_PATH" 2>/dev/null || true
fi

mkdir -p "$SUPPORT_DIR"
if [[ ! -f "$SUPPORT_DIR/.env" ]]; then
  if [[ -f .env ]]; then
    cp .env "$SUPPORT_DIR/.env"
    echo "Copied dev .env → $SUPPORT_DIR/.env"
  elif [[ -f .env.example ]]; then
    cp .env.example "$SUPPORT_DIR/.env"
    echo "Seeded $SUPPORT_DIR/.env from .env.example — edit API keys before using Plaid/FRED."
  fi
fi

echo ""
echo "Built: $PROJECT_ROOT/$APP_PATH (${APP_SIZE}MB)"
echo ""
echo "Install to Applications:"
echo "  rm -rf \"/Applications/${APP_NAME}.app\""
echo "  cp -R \"$APP_PATH\" /Applications/"
echo ""
echo "API keys (one time): edit"
echo "  \"$SUPPORT_DIR/.env\""
echo ""
echo "Then double-click Retirement Planner in Applications (no Terminal)."
echo "Logs: $SUPPORT_DIR/launcher.log"