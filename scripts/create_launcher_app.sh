#!/usr/bin/env bash
# Dev-only thin launcher (points at local venv). Prefer ./scripts/build_app.sh for UX-6.
set -euo pipefail

echo "Note: build_app.sh produces a self-contained .app (recommended)."
echo "This script only builds a lightweight dev launcher."
echo ""

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
APP_NAME="Retirement Planner"
APP_DIR="$PROJECT_ROOT/dist/${APP_NAME}-dev.app"
MACOS_DIR="$APP_DIR/Contents/MacOS"
RES_DIR="$APP_DIR/Contents/Resources"
ICON_SRC="$PROJECT_ROOT/design/AppIcon.icns"
LAUNCHER_C="$PROJECT_ROOT/scripts/macos_launcher.c"

rm -rf "$APP_DIR"
mkdir -p "$MACOS_DIR" "$RES_DIR"

if [[ ! -f "$ICON_SRC" ]]; then
  echo "Building app icon..."
  "$PROJECT_ROOT/scripts/build_app_icon.sh"
fi
cp "$ICON_SRC" "$RES_DIR/AppIcon.icns"

sed \
  -e "s|/Users/henrylai/myprojects/Retirement_Financial_Modeling|$PROJECT_ROOT|g" \
  "$LAUNCHER_C" > "$MACOS_DIR/launcher_src.c"

clang -O2 -o "$MACOS_DIR/launch" "$MACOS_DIR/launcher_src.c"
rm -f "$MACOS_DIR/launcher_src.c"
chmod +x "$MACOS_DIR/launch"

cat > "$APP_DIR/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleDevelopmentRegion</key>
  <string>en</string>
  <key>CFBundleExecutable</key>
  <string>launch</string>
  <key>CFBundleIdentifier</key>
  <string>com.henrylai.retirementplanner.launcher</string>
  <key>CFBundleInfoDictionaryVersion</key>
  <string>6.0</string>
  <key>CFBundleName</key>
  <string>Retirement Planner (Dev)</string>
  <key>CFBundleDisplayName</key>
  <string>Retirement Planner (Dev)</string>
  <key>CFBundleIconFile</key>
  <string>AppIcon</string>
  <key>CFBundlePackageType</key>
  <string>APPL</string>
  <key>CFBundleShortVersionString</key>
  <string>1.0-dev</string>
  <key>CFBundleVersion</key>
  <string>1</string>
  <key>LSMinimumSystemVersion</key>
  <string>13.0</string>
  <key>NSHighResolutionCapable</key>
  <true/>
  <key>LSApplicationCategoryType</key>
  <string>public.app-category.finance</string>
</dict>
</plist>
PLIST

if command -v codesign >/dev/null 2>&1; then
  codesign --force --deep --sign - "$APP_DIR" 2>/dev/null || true
  xattr -cr "$APP_DIR" 2>/dev/null || true
fi

echo "Created dev launcher: $APP_DIR"
echo "For a portable bundle, run: ./scripts/build_app.sh"