#!/usr/bin/env bash
#
# build-mac.sh — Cairn Mac distribution build
#
# Assembles the Cairn plugin folder, code-signs all binaries, creates a
# drag-install DMG, and submits it for Apple notarization.
#
# Prerequisites:
#   - Xcode Command Line Tools (codesign, hdiutil, xcrun notarytool)
#   - Apple Developer ID Application certificate installed in the keychain
#   - `npm run package:python` already run (packaging/dist/workers exists)
#   - `npm run build:ui` already run (dist/ exists)
#
# Required env vars:
#   APPLE_DEVELOPER_ID    e.g. "Developer ID Application: Aidan Mascaro (TEAMID)"
#   APPLE_TEAM_ID         10-char Apple Developer Team ID
#   APPLE_ID              Apple ID email for notarytool
#   APPLE_APP_PASSWORD    App-specific password for notarytool
#                         (create at appleid.apple.com → Security → App-specific passwords)
#
# Optional env vars:
#   CAIRN_SKIP_NOTARIZE=1   Build + sign without notarizing (fast local testing)
#   CAIRN_SKIP_SIGN=1       Skip signing entirely (CI without certs)
#
# Output:
#   packaging/release/mac/Cairn-<version>-mac.dmg   (notarized, stapled)
#
# Run from the project root:
#   bash packaging/build-mac.sh

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PACKAGING="$PROJECT_ROOT/packaging"
STAGE="$PACKAGING/stage/mac"
RELEASE="$PACKAGING/release/mac"
VERSION="$(node -p "require('./package.json').version")"
DMG_NAME="Cairn-${VERSION}-mac.dmg"
WORKERS_BIN="$PACKAGING/dist/workers"
ENTITLEMENTS="$PACKAGING/entitlements.mac.plist"

log() { echo "→ $*"; }
err() { echo "✗ $*" >&2; exit 1; }

# ── Pre-flight ────────────────────────────────────────────────────────────────

[[ -f "$WORKERS_BIN" ]] || err "packaging/dist/workers not found. Run: npm run package:python"
[[ -d "$PROJECT_ROOT/dist" ]] || err "dist/ not found. Run: npm run build:ui"
[[ -f "$PROJECT_ROOT/src/orchestrator/WorkflowIntegration.node" ]] || \
  err "WorkflowIntegration.node missing. Run: npm run install-plugin to copy it."

# ── Stage plugin folder ───────────────────────────────────────────────────────

log "Staging plugin files…"
rm -rf "$STAGE"
mkdir -p "$STAGE/Cairn/src/orchestrator"
mkdir -p "$STAGE/Cairn/resources"

cp "$PROJECT_ROOT/manifest.xml"                                 "$STAGE/Cairn/manifest.xml"
cp "$PROJECT_ROOT/package.json"                                 "$STAGE/Cairn/package.json"
cp "$PROJECT_ROOT/src/orchestrator/main.js"                     "$STAGE/Cairn/src/orchestrator/main.js"
cp "$PROJECT_ROOT/src/orchestrator/preload.js"                  "$STAGE/Cairn/src/orchestrator/preload.js"
cp "$PROJECT_ROOT/src/orchestrator/selects-learn.js"            "$STAGE/Cairn/src/orchestrator/selects-learn.js"
cp -R "$PROJECT_ROOT/src/orchestrator/prompts"                  "$STAGE/Cairn/src/orchestrator/prompts"
cp "$PROJECT_ROOT/src/orchestrator/WorkflowIntegration.node"    "$STAGE/Cairn/src/orchestrator/WorkflowIntegration.node"
cp -R "$PROJECT_ROOT/dist"                                      "$STAGE/Cairn/dist"

# Minimal node_modules — production deps only (chokidar, zod, and the
# Anthropic SDK with its runtime dependency closure for taste learning)
mkdir -p "$STAGE/Cairn/node_modules"
for mod in chokidar zod @anthropic-ai/sdk @opentelemetry/api standardwebhooks @stablelib/base64 fast-sha256 json-schema-to-ts @babel/runtime ts-algebra; do
  if [[ -d "$PROJECT_ROOT/node_modules/$mod" ]]; then
    mkdir -p "$(dirname "$STAGE/Cairn/node_modules/$mod")"
    cp -R "$PROJECT_ROOT/node_modules/$mod" "$STAGE/Cairn/node_modules/$mod"
  fi
done

# PyInstaller workers binary → resources/workers
cp "$WORKERS_BIN" "$STAGE/Cairn/resources/workers"
chmod +x "$STAGE/Cairn/resources/workers"

# ffmpeg binary → assets/bin/ffmpeg (if present)
if [[ -f "$PROJECT_ROOT/assets/bin/ffmpeg" ]]; then
  mkdir -p "$STAGE/Cairn/assets/bin"
  cp "$PROJECT_ROOT/assets/bin/ffmpeg" "$STAGE/Cairn/assets/bin/ffmpeg"
  chmod +x "$STAGE/Cairn/assets/bin/ffmpeg"
fi

# ── Code signing ─────────────────────────────────────────────────────────────

if [[ "${CAIRN_SKIP_SIGN:-0}" != "1" ]]; then
  : "${APPLE_DEVELOPER_ID:?APPLE_DEVELOPER_ID must be set}"

  log "Signing binaries with: $APPLE_DEVELOPER_ID"

  sign_binary() {
    local bin="$1"
    codesign \
      --sign "$APPLE_DEVELOPER_ID" \
      --options runtime \
      --entitlements "$ENTITLEMENTS" \
      --timestamp \
      --force \
      "$bin"
    log "  signed: $(basename "$bin")"
  }

  # Sign native addons and compiled binaries
  find "$STAGE/Cairn" -name "*.node" -exec bash -c 'sign_binary "$0"' {} \;
  sign_binary "$STAGE/Cairn/resources/workers"
  [[ -f "$STAGE/Cairn/assets/bin/ffmpeg" ]] && sign_binary "$STAGE/Cairn/assets/bin/ffmpeg"

  # Verify signatures
  codesign --verify --verbose=1 "$STAGE/Cairn/resources/workers"
  log "Signature verified ✓"
else
  log "CAIRN_SKIP_SIGN=1 — skipping code signing"
fi

# ── Create DMG ────────────────────────────────────────────────────────────────
# Drag-install model: user drags 'Cairn' folder to the Resolve plugin directory.
# The DMG contains:
#   • Cairn/    — the plugin folder
#   • A symlink to the Resolve Workflow Integration Plugins destination

log "Creating DMG…"
mkdir -p "$RELEASE"
DMG_PATH="$RELEASE/$DMG_NAME"
RESOLVE_PLUGINS="/Library/Application Support/Blackmagic Design/DaVinci Resolve/Workflow Integration Plugins"

# Build a temporary DMG source folder
DMG_SRC="$PACKAGING/stage/dmg_src"
rm -rf "$DMG_SRC"
mkdir -p "$DMG_SRC"
cp -R "$STAGE/Cairn" "$DMG_SRC/Cairn"
ln -s "$RESOLVE_PLUGINS" "$DMG_SRC/Install here →"

# Create writable DMG, then convert to compressed read-only
TMP_DMG="$PACKAGING/stage/tmp.dmg"
hdiutil create \
  -volname "Cairn ${VERSION}" \
  -srcfolder "$DMG_SRC" \
  -ov \
  -format UDRW \
  "$TMP_DMG"

hdiutil convert "$TMP_DMG" \
  -format UDZO \
  -imagekey zlib-level=9 \
  -o "$DMG_PATH"
rm -f "$TMP_DMG"

# Sign the DMG itself
if [[ "${CAIRN_SKIP_SIGN:-0}" != "1" ]]; then
  codesign \
    --sign "$APPLE_DEVELOPER_ID" \
    --timestamp \
    --force \
    "$DMG_PATH"
  log "DMG signed ✓"
fi

# ── Notarize ──────────────────────────────────────────────────────────────────

if [[ "${CAIRN_SKIP_NOTARIZE:-0}" != "1" && "${CAIRN_SKIP_SIGN:-0}" != "1" ]]; then
  : "${APPLE_TEAM_ID:?APPLE_TEAM_ID must be set}"
  : "${APPLE_ID:?APPLE_ID must be set}"
  : "${APPLE_APP_PASSWORD:?APPLE_APP_PASSWORD must be set}"

  log "Submitting DMG to Apple notarization service (this may take 1-5 min)…"
  xcrun notarytool submit "$DMG_PATH" \
    --apple-id "$APPLE_ID" \
    --team-id "$APPLE_TEAM_ID" \
    --password "$APPLE_APP_PASSWORD" \
    --wait

  log "Stapling notarization ticket…"
  xcrun stapler staple "$DMG_PATH"
  xcrun stapler validate "$DMG_PATH"
  log "Notarization complete ✓"
else
  log "CAIRN_SKIP_NOTARIZE=1 — skipping notarization"
fi

echo
echo "✓ Mac DMG ready: $DMG_PATH"
echo
echo "To install manually:"
echo "  1. Open $DMG_NAME"
echo "  2. Drag 'Cairn' to 'Install here →'"
echo "  3. Restart DaVinci Resolve"
