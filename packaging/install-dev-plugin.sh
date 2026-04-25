#!/usr/bin/env bash
#
# install-dev-plugin.sh — install Cairn as a Workflow Integration plugin for development.
#
# What it does:
#   1. Builds the Svelte UI (`npm run build:ui`)
#   2. Removes any previous install at the plugin target
#   3. Creates a fresh target folder
#   4. Symlinks (by default) or copies the plugin files into it
#   5. Copies WorkflowIntegration.node from Blackmagic's SamplePromisePlugin
#
# Flags:
#   --copy   Copy instead of symlink (useful before distributing a build)
#   --no-build   Skip the Vite build (use an existing dist/)
#
# After running, restart DaVinci Resolve and open
#   Workspace → Workflow Integrations → Cairn
#
# NEVER modifies source media. Operates only on the project tree and the
# Resolve plugin folder. Run from the project root.

set -euo pipefail

PLUGIN_ID="com.aidanmascaro.cairn"
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TARGET="${HOME}/Library/Application Support/Blackmagic Design/DaVinci Resolve/Workflow Integration Plugins/${PLUGIN_ID}"
SYSTEM_TARGET="/Library/Application Support/Blackmagic Design/DaVinci Resolve/Workflow Integration Plugins/${PLUGIN_ID}"
WORKFLOW_NODE="/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Workflow Integrations/Examples/SamplePromisePlugin/WorkflowIntegration.node"

DO_BUILD=1
MODE="symlink"
for arg in "$@"; do
    case "$arg" in
        --copy) MODE="copy" ;;
        --no-build) DO_BUILD=0 ;;
        -h|--help)
            grep '^#' "$0" | sed 's/^# //;s/^#//'
            exit 0
            ;;
        *) echo "Unknown flag: $arg"; exit 1 ;;
    esac
done

cd "$PROJECT_ROOT"

if [[ ! -f "$WORKFLOW_NODE" ]]; then
    echo "✗ Could not find WorkflowIntegration.node at:"
    echo "  $WORKFLOW_NODE"
    echo "  Is DaVinci Resolve Studio installed? The Developer examples"
    echo "  ship with Resolve Studio only (free tier does not include them)."
    exit 1
fi

if [[ $DO_BUILD -eq 1 ]]; then
    echo "→ Building UI…"
    npm run build:ui
fi

# The user-level path is preferred (no sudo needed); fall back to system path only if the user-level dir is locked off.
if [[ -w "$(dirname "$TARGET")" ]] || [[ ! -d "$(dirname "$TARGET")" ]]; then
    INSTALL_PATH="$TARGET"
else
    INSTALL_PATH="$SYSTEM_TARGET"
    echo "→ User-level plugin dir not writable; using system path (may require sudo)."
fi

echo "→ Target: $INSTALL_PATH"

mkdir -p "$(dirname "$INSTALL_PATH")"
rm -rf "$INSTALL_PATH"
mkdir -p "$INSTALL_PATH/src/orchestrator"

link_or_copy() {
    local src="$1"
    local dst="$2"
    if [[ "$MODE" == "symlink" ]]; then
        ln -sfn "$src" "$dst"
    else
        cp -R "$src" "$dst"
    fi
}

# Manifest + package.json (always copied — Resolve reads them directly)
cp "$PROJECT_ROOT/manifest.xml" "$INSTALL_PATH/manifest.xml"
cp "$PROJECT_ROOT/package.json" "$INSTALL_PATH/package.json"

# Electron main + preload
link_or_copy "$PROJECT_ROOT/src/orchestrator/main.js" "$INSTALL_PATH/src/orchestrator/main.js"
link_or_copy "$PROJECT_ROOT/src/orchestrator/preload.js" "$INSTALL_PATH/src/orchestrator/preload.js"

# Built UI
if [[ -d "$PROJECT_ROOT/dist" ]]; then
    link_or_copy "$PROJECT_ROOT/dist" "$INSTALL_PATH/dist"
else
    echo "✗ dist/ not found. Run without --no-build or run 'npm run build:ui' first."
    exit 1
fi

# Native addon: Resolve's own file, copied (not symlinked — we want a stable reference
# even if the user upgrades Resolve and the path shifts)
cp "$WORKFLOW_NODE" "$INSTALL_PATH/src/orchestrator/WorkflowIntegration.node"

# node_modules (Electron itself is required at runtime)
if [[ -d "$PROJECT_ROOT/node_modules" ]]; then
    link_or_copy "$PROJECT_ROOT/node_modules" "$INSTALL_PATH/node_modules"
else
    echo "⚠ node_modules/ missing — run 'npm install' before loading the plugin."
fi

echo
echo "✓ Installed Cairn ($MODE mode) at:"
echo "  $INSTALL_PATH"
echo
echo "Next: restart DaVinci Resolve, then open"
echo "  Workspace → Workflow Integrations → Cairn"
