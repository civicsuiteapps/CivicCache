#!/usr/bin/env bash
#
# Install (or reinstall) CivicCache launchd agents.
#
# Usage:
#   bash scripts/install_launchd.sh          # Install all agents
#   bash scripts/install_launchd.sh --remove # Unload and remove all agents
#
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
LAUNCH_AGENTS_DIR="$HOME/Library/LaunchAgents"
PLISTS=(
    "com.civiccache.daily"
    "com.civiccache.weekly"
    "com.civiccache.monthly"
)

mkdir -p "$LAUNCH_AGENTS_DIR"

if [ "${1:-}" = "--remove" ]; then
    echo "Removing CivicCache launchd agents..."
    for name in "${PLISTS[@]}"; do
        plist="$LAUNCH_AGENTS_DIR/${name}.plist"
        if [ -f "$plist" ]; then
            launchctl bootout "gui/$(id -u)/$name" 2>/dev/null || true
            rm -f "$plist"
            echo "  Removed $name"
        fi
    done
    echo "Done."
    exit 0
fi

echo "Installing CivicCache launchd agents..."
echo "  Source: $PROJECT_DIR/launchd/"
echo "  Target: $LAUNCH_AGENTS_DIR/"
echo ""

for name in "${PLISTS[@]}"; do
    src="$PROJECT_DIR/launchd/${name}.plist"
    dst="$LAUNCH_AGENTS_DIR/${name}.plist"

    if [ ! -f "$src" ]; then
        echo "  SKIP  $name (source not found)"
        continue
    fi

    # Unload if already loaded
    launchctl bootout "gui/$(id -u)/$name" 2>/dev/null || true

    # Symlink (not copy) so edits to repo are picked up
    ln -sf "$src" "$dst"

    # Load
    launchctl bootstrap "gui/$(id -u)" "$dst"
    echo "  OK    $name"
done

echo ""
echo "Verify with: launchctl list | grep civiccache"
launchctl list | grep civiccache 2>/dev/null || echo "  (no agents currently running)"
