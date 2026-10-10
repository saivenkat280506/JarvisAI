#!/usr/bin/env bash
# Build JARVIS.app with the project JARVIS.jpg as the Dock / Desktop icon.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MISC="$ROOT/miscellaneous"
SRC_JPG="$MISC/JARVIS.jpg"
APP="$MISC/desktop-launcher/JARVIS.app"
DESKTOP_APP="$HOME/Desktop/JARVIS.app"
ICONSET="$MISC/build/jarvis.iconset"
ICNS="$MISC/jarvis_icon.icns"

if [[ ! -f "$SRC_JPG" ]]; then
  echo "Missing logo: $SRC_JPG" >&2
  exit 1
fi

mkdir -p "$ICONSET" "$APP/Contents/MacOS" "$APP/Contents/Resources"

# Square PNG master, then every macOS icon size.
MASTER="$MISC/build/jarvis-master.png"
mkdir -p "$MISC/build"
sips -s format png "$SRC_JPG" --out "$MASTER" >/dev/null
sips -z 1024 1024 "$MASTER" --out "$MASTER" >/dev/null

make_icon() {
  local px="$1"
  local name="$2"
  sips -z "$px" "$px" "$MASTER" --out "$ICONSET/$name" >/dev/null
}

make_icon 16   icon_16x16.png
make_icon 32   icon_16x16@2x.png
make_icon 32   icon_32x32.png
make_icon 64   icon_32x32@2x.png
make_icon 128  icon_128x128.png
make_icon 256  icon_128x128@2x.png
make_icon 256  icon_256x256.png
make_icon 512  icon_256x256@2x.png
make_icon 512  icon_512x512.png
make_icon 1024 icon_512x512@2x.png

iconutil -c icns "$ICONSET" -o "$ICNS"
cp "$ICNS" "$APP/Contents/Resources/AppIcon.icns"

cat > "$APP/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key>
  <string>JARVIS</string>
  <key>CFBundleDisplayName</key>
  <string>JARVIS</string>
  <key>CFBundleIdentifier</key>
  <string>ai.jarvis.launcher</string>
  <key>CFBundleVersion</key>
  <string>1.0</string>
  <key>CFBundleShortVersionString</key>
  <string>1.0</string>
  <key>CFBundleExecutable</key>
  <string>JARVIS</string>
  <key>CFBundleIconFile</key>
  <string>AppIcon</string>
  <key>CFBundlePackageType</key>
  <string>APPL</string>
  <key>CFBundleInfoDictionaryVersion</key>
  <string>6.0</string>
  <key>LSMinimumSystemVersion</key>
  <string>12.0</string>
  <key>NSHighResolutionCapable</key>
  <true/>
  <key>LSApplicationCategoryType</key>
  <string>public.app-category.utilities</string>
  <key>LSUIElement</key>
  <true/>
  <key>NSSupportsAutomaticTermination</key>
  <false/>
  <key>LSRequiresNativeExecution</key>
  <true/>
  <key>LSArchitecturePriority</key>
  <array>
    <string>arm64</string>
  </array>
</dict>
</plist>
PLIST

cat > "$APP/Contents/MacOS/JARVIS" <<EXEC
#!/bin/zsh
set -u
# Resolve the repo root: absolute path baked in at build time, else derive it
# from the bundle location (miscellaneous/desktop-launcher/JARVIS.app).
ROOT="$ROOT"
if [[ ! -f "\$ROOT/scripts/jarvis-launcher.sh" ]]; then
  ROOT="\$(cd "\$(dirname "\$0")/../../../.." && pwd)"
fi
cd "\$ROOT" || exit 1
chmod +x "\$ROOT/scripts/jarvis-launcher.sh" 2>/dev/null || true
mkdir -p "\$ROOT/miscellaneous/logs"
# Force native ARM64. LaunchServices can start this script .app under Rosetta.
if [[ -x /usr/bin/arch ]]; then
  exec /usr/bin/arch -arm64 /bin/bash "\$ROOT/scripts/jarvis-launcher.sh" --start >>"\$ROOT/miscellaneous/logs/launcher.log" 2>&1
fi
exec /bin/bash "\$ROOT/scripts/jarvis-launcher.sh" --start >>"\$ROOT/miscellaneous/logs/launcher.log" 2>&1
EXEC
chmod +x "$APP/Contents/MacOS/JARVIS"

# Fresh Desktop copy so Finder picks up the icon.
rm -rf "$DESKTOP_APP"
rm -f "$HOME/Desktop/JARVIS.command"
ditto "$APP" "$DESKTOP_APP"
touch "$APP" "$DESKTOP_APP"

# Refresh Launch Services icon cache for this bundle.
/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister -f "$APP" >/dev/null 2>&1 || true
/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister -f "$DESKTOP_APP" >/dev/null 2>&1 || true

echo "Built $APP"
echo "Installed $DESKTOP_APP"
echo "Icon $ICNS"
