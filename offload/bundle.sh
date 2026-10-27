#!/bin/bash
# Builds Offload.app — a real menu-bar app bundle. Launched through Launch Services
# (`open`), it runs in your normal GUI session, which macOS requires before it lets an
# app unmount/eject disks. A bare binary started from a shell gets "not privileged".
#
#   ./bundle.sh            -> build/Offload.app
#   open build/Offload.app
set -euo pipefail
cd "$(dirname "$0")"

BUNDLE_ID="com.aidanmascaro.cairn.offload"
APP="build/Offload.app"

swift build -c release --product Offload
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS"
cp ".build/release/Offload" "$APP/Contents/MacOS/Offload"

cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleIdentifier</key><string>${BUNDLE_ID}</string>
  <key>CFBundleName</key><string>Offload</string>
  <key>CFBundleExecutable</key><string>Offload</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>0.1.0</string>
  <key>CFBundleVersion</key><string>1</string>
  <key>LSMinimumSystemVersion</key><string>14.0</string>
  <key>NSAppleEventsUsageDescription</key><string>Offload uses Finder to show your card or working folder in the window you already have open.</string>
  <!-- Menu-bar only: no Dock icon. -->
  <key>LSUIElement</key><true/>
</dict>
</plist>
PLIST

# Signed with the self-signed "Offload Local" certificate in the login keychain, so macOS
# remembers permissions (Allow prompts) across rebuilds — an ad-hoc signature changes with
# every build. Falls back to ad-hoc on a Mac without that certificate.
if security find-identity -p codesigning | grep -q '"Offload Local"'; then
  codesign --force --sign "Offload Local" "$APP"
else
  codesign --force --sign - "$APP"
fi

# One-time: carry settings (working folder, camera names, last shoot) over from the
# unbundled binary, whose preferences were stored under the name "Offload".
if ! defaults read "$BUNDLE_ID" rootPath >/dev/null 2>&1 && defaults read Offload >/dev/null 2>&1; then
  defaults export Offload - | defaults import "$BUNDLE_ID" -
  echo "Copied settings from the old 'Offload' preferences."
fi

echo "Built $APP"

# Install to ~/Applications — a stable home for the Login Item (build/ is wiped on
# every rebuild). Quit the running copy first so the new one replaces it cleanly.
INSTALLED="$HOME/Applications/Offload.app"
osascript -e "quit app id \"${BUNDLE_ID}\"" >/dev/null 2>&1 || true
sleep 1
mkdir -p "$HOME/Applications"
rm -rf "$INSTALLED"
ditto "$APP" "$INSTALLED"
echo "Installed $INSTALLED  (run: open \"$INSTALLED\")"
