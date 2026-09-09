#!/bin/bash
# Build "JARVIS Hotkey.app" -- a background-only app that owns Cmd+Shift+J.
#
# An .app bundle rather than a bare executable, for two reasons that both matter
# at login time: SMAppService registers bundles, and LSUIElement is a bundle
# property. The first build of this helper was a plain CLI binary, which armed
# the hotkey without error and then never received a key event because a bare
# executable is not a registered application.
#
# Ad-hoc signed. No Developer ID, no entitlements, no TCC grants: the whole
# point of RegisterEventHotKey here is that it needs neither Accessibility nor
# Input Monitoring.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP="$ROOT/dist/JARVIS Hotkey.app"
BUNDLE_ID="local.jarvis.hotkey"

cd "$ROOT"
echo "building release binary"
swift build -c release

rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS"
mkdir -p "$APP/Contents/Resources"

cp ".build/release/JarvisHotkey" "$APP/Contents/MacOS/JarvisHotkey"
chmod 755 "$APP/Contents/MacOS/JarvisHotkey"

cat > "$APP/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key>            <string>JARVIS Hotkey</string>
  <key>CFBundleDisplayName</key>     <string>JARVIS Hotkey</string>
  <key>CFBundleExecutable</key>      <string>JarvisHotkey</string>
  <key>CFBundleIdentifier</key>      <string>local.jarvis.hotkey</string>
  <key>CFBundlePackageType</key>     <string>APPL</string>
  <key>CFBundleShortVersionString</key> <string>1.0</string>
  <key>CFBundleVersion</key>         <string>1</string>
  <key>LSMinimumSystemVersion</key>  <string>14.0</string>
  <!-- No Dock tile, no menu bar, no window. It exists to hold one key. -->
  <key>LSUIElement</key>             <true/>
  <!-- Nothing here needs the network, the microphone, or the filesystem
       beyond one socket and one lock file, so no usage descriptions and no
       entitlements are declared: a background app that asks for nothing
       cannot be granted anything by mistake. -->
</dict>
</plist>
PLIST

echo "signing (ad-hoc)"
codesign --force --sign - --identifier "$BUNDLE_ID" \
    --timestamp=none "$APP" >/dev/null 2>&1
codesign --verify --deep --strict "$APP"

echo "built $APP"
echo "  arch        : $(lipo -archs "$APP/Contents/MacOS/JarvisHotkey")"
echo "  LSUIElement : $(/usr/libexec/PlistBuddy -c 'Print :LSUIElement' "$APP/Contents/Info.plist")"
echo "  signature   : $(codesign -dv "$APP" 2>&1 | grep -o 'Signature=.*')"
