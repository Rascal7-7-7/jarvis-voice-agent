#!/bin/zsh
# Stage "JARVIS HUD.app" into dist/. STAGING ONLY.
#
# Nothing here installs: the bundle is written under this project and is never
# copied to ~/Applications or /Applications, never registered as a login item,
# and no LaunchAgent is created for it. Phase 1C-prep explicitly stops short of
# all of that.
#
# Ad-hoc signature only (`codesign -s -`). No Developer ID, no notarization, no
# entitlements -- the HUD reads one JSON file and draws it, so it needs none,
# and an entitlement added "just in case" is permission the app keeps forever.
set -euo pipefail

ROOT="${0:A:h:h}"
cd "$ROOT"

APP_NAME="JARVIS HUD"
BUNDLE_ID="local.jarvis.hud"
EXECUTABLE="JarvisHUD"
VERSION="1.0.0"
BUILD="1"

DIST="$ROOT/dist"
APP="$DIST/$APP_NAME.app"
CONTENTS="$APP/Contents"

echo "==> release build"
swift build -c release

BIN=$(swift build -c release --show-bin-path)/"$EXECUTABLE"
[[ -f "$BIN" ]] || { echo "no executable at $BIN"; exit 1 }

echo "==> staging bundle"
rm -rf "$APP"
mkdir -p "$CONTENTS/MacOS" "$CONTENTS/Resources"
cp "$BIN" "$CONTENTS/MacOS/$EXECUTABLE"

cat > "$CONTENTS/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
	<key>CFBundleName</key>
	<string>$APP_NAME</string>
	<key>CFBundleDisplayName</key>
	<string>$APP_NAME</string>
	<key>CFBundleIdentifier</key>
	<string>$BUNDLE_ID</string>
	<key>CFBundleExecutable</key>
	<string>$EXECUTABLE</string>
	<key>CFBundlePackageType</key>
	<string>APPL</string>
	<key>CFBundleShortVersionString</key>
	<string>$VERSION</string>
	<key>CFBundleVersion</key>
	<string>$BUILD</string>
	<key>CFBundleInfoDictionaryVersion</key>
	<string>6.0</string>
	<key>LSMinimumSystemVersion</key>
	<string>14.0</string>

	<!-- Menu-bar agent: no Dock icon, no menu bar of its own. Every control
	     the app has lives in the MenuBarExtra, which stays reachable -- an
	     agent whose only affordance is a window it can hide would be
	     unquittable without Activity Monitor. -->
	<key>LSUIElement</key>
	<true/>

	<!-- The HUD reads a JSON file this user owns. It has no documents, no
	     URL schemes, no services, and asks for no privacy-gated resource, so
	     there is deliberately no NSUsageDescription of any kind here. -->
	<key>NSSupportsAutomaticTermination</key>
	<false/>
	<key>NSSupportsSuddenTermination</key>
	<false/>
	<key>NSHighResolutionCapable</key>
	<true/>
</dict>
</plist>
PLIST

# PkgInfo is legacy but cheap, and some tooling still looks for it.
printf 'APPL????' > "$CONTENTS/PkgInfo"

echo "==> ad-hoc signing"
codesign --force --sign - --timestamp=none "$APP"
codesign --verify --deep --strict --verbose=2 "$APP" 2>&1 | sed 's/^/    /'

echo "==> staged: $APP"
echo "    launch with:  open \"$APP\""
echo "    NOT installed. Phase 1C-prep does not install."
