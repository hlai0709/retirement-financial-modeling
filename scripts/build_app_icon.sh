#!/usr/bin/env bash
# Build design/AppIcon.icns from design/app-icon-1024.png (or app-icon-source.jpg).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ASSETS="$ROOT/design"
SRC_PNG="$ASSETS/app-icon-1024.png"
ICONSET="$ASSETS/AppIcon.iconset"

if [[ ! -f "$SRC_PNG" ]]; then
  echo "Missing $SRC_PNG — add a 1024×1024 PNG first."
  exit 1
fi

rm -rf "$ICONSET"
mkdir -p "$ICONSET"
sips -z 16 16     "$SRC_PNG" --out "$ICONSET/icon_16x16.png" >/dev/null
sips -z 32 32     "$SRC_PNG" --out "$ICONSET/icon_16x16@2x.png" >/dev/null
sips -z 32 32     "$SRC_PNG" --out "$ICONSET/icon_32x32.png" >/dev/null
sips -z 64 64     "$SRC_PNG" --out "$ICONSET/icon_32x32@2x.png" >/dev/null
sips -z 128 128   "$SRC_PNG" --out "$ICONSET/icon_128x128.png" >/dev/null
sips -z 256 256   "$SRC_PNG" --out "$ICONSET/icon_128x128@2x.png" >/dev/null
sips -z 256 256   "$SRC_PNG" --out "$ICONSET/icon_256x256.png" >/dev/null
sips -z 512 512   "$SRC_PNG" --out "$ICONSET/icon_256x256@2x.png" >/dev/null
sips -z 512 512   "$SRC_PNG" --out "$ICONSET/icon_512x512.png" >/dev/null
sips -z 1024 1024 "$SRC_PNG" --out "$ICONSET/icon_512x512@2x.png" >/dev/null
iconutil -c icns "$ICONSET" -o "$ASSETS/AppIcon.icns"
echo "Built: $ASSETS/AppIcon.icns"