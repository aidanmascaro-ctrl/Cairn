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
# Resolve scans the system-level path on this machine (confirmed by Snap Captions install).
# Folder name uses display name "Cairn", matching the Snap Captions convention.
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TARGET="/Library/Application Support/Blackmagic Design/DaVinci Resolve/Workflow Integration Plugins/Cairn"
SYSTEM_TARGET="$TARGET"
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
    # IMPORTANT — never run npm/vite under root. If it does, dist/ and any
    # newly-fetched node_modules entries become root-owned and subsequent
    # non-sudo `npm run build:ui` calls fail with EACCES on dist/assets/.
    # When the script is itself invoked with sudo, drop privileges back to the
    # invoking user for the build, then continue with the elevated copy step.
    if [[ $EUID -eq 0 ]]; then
        BUILD_USER="${SUDO_USER:-${USER:-}}"
        if [[ -z "$BUILD_USER" || "$BUILD_USER" == "root" ]]; then
            echo "✗ Refusing to run 'npm run build:ui' as root."
            echo "  Re-run this script without sudo, or set SUDO_USER to a real user."
            exit 1
        fi
        echo "→ Building UI as user '$BUILD_USER' (avoiding root-owned dist/)…"
        sudo -u "$BUILD_USER" npm run build:ui
    else
        echo "→ Building UI…"
        npm run build:ui
    fi
fi

INSTALL_PATH="$TARGET"
if [[ ! -w "$(dirname "$TARGET")" ]] && [[ $EUID -ne 0 ]]; then
    echo "✗ System plugin dir is not writable and you're not root."
    echo "  Re-run with: sudo bash packaging/install-dev-plugin.sh"
    exit 1
fi

echo "→ Target: $INSTALL_PATH"

mkdir -p "$(dirname "$INSTALL_PATH")"
rm -rf "$INSTALL_PATH"
mkdir -p "$INSTALL_PATH/src/orchestrator"
mkdir -p "$INSTALL_PATH/src/workers"

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

# Python workers (bridge.py, ingest.py, resolve_import.py, sidecar.py, resolve_api.py)
# Symlinked so source edits to .py files are live without reinstalling.
for f in "$PROJECT_ROOT/src/workers"/*.py; do
    [[ -f "$f" ]] && link_or_copy "$f" "$INSTALL_PATH/src/workers/$(basename "$f")"
done

# Built UI
if [[ -d "$PROJECT_ROOT/dist" ]]; then
    link_or_copy "$PROJECT_ROOT/dist" "$INSTALL_PATH/dist"
else
    echo "✗ dist/ not found. Run without --no-build or run 'npm run build:ui' first."
    exit 1
fi

# Native addon: Resolve's own file.
# Copied to BOTH the install path and the project source dir.
# The project source copy is needed because main.js is symlinked — Node.js
# resolves require('./WorkflowIntegration.node') relative to the real file
# location (project root), not the symlink location (install path).
cp "$WORKFLOW_NODE" "$INSTALL_PATH/src/orchestrator/WorkflowIntegration.node"
cp "$WORKFLOW_NODE" "$PROJECT_ROOT/src/orchestrator/WorkflowIntegration.node"

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
