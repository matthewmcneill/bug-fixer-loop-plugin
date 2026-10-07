#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
INSTALL_DIR="$HOME/.gemini/config/plugins/bug-fixer-daemon"

echo "Installing bug-fixer daemon plugin..."
echo "  Source: $REPO_DIR/daemon/"
echo "  Target: $INSTALL_DIR/"

# Create target (sidecars/ persists per-repo data)
mkdir -p "$INSTALL_DIR/sidecars"

# Sync daemon structure, preserving sidecars/
rsync -a --delete --exclude='sidecars/' "$REPO_DIR/daemon/" "$INSTALL_DIR/"

# Refresh existing sidecars (updates paths, preserves per-repo values like project ID)
if command -v python3 &>/dev/null && [[ -d "$INSTALL_DIR/sidecars" ]]; then
    echo "  Refreshing existing sidecars..."
    python3 "$INSTALL_DIR/scripts/manage.py" refresh-sidecars 2>&1 | sed 's/^/  /'
fi

# Enable the plugin
if command -v agy &>/dev/null; then
    agy plugin enable bug-fixer-daemon 2>/dev/null || true
    echo "  Plugin enabled via 'agy plugin enable'"
else
    echo "  Note: 'agy' CLI not found — enable manually in settings"
fi

echo ""
echo "Installed successfully."
echo "To enroll repos, use the bug-fixer-manage skill from within each target workspace."
