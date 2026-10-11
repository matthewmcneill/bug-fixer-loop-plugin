#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
INSTALL_DIR="$HOME/.gemini/config/plugins/bug-fixer-daemon"
DEFAULT_REPO="matthewmcneill/bug-fixer-loop-plugin"

FROM_RELEASE=""
CLEANUP_TMP=""

cleanup() {
    if [[ -n "$CLEANUP_TMP" && -d "$CLEANUP_TMP" ]]; then
        rm -rf "$CLEANUP_TMP"
    fi
}
trap cleanup EXIT

while [[ $# -gt 0 ]]; do
    case "$1" in
        --from-release)
            if [[ $# -ge 2 && "$2" != -* ]]; then
                FROM_RELEASE="$2"
                shift 2
            else
                FROM_RELEASE="latest"
                shift 1
            fi
            ;;
        --from-release=*)
            FROM_RELEASE="${1#*=}"
            if [[ -z "$FROM_RELEASE" ]]; then
                FROM_RELEASE="latest"
            fi
            shift 1
            ;;
        -h|--help)
            echo "Usage: $0 [OPTIONS]"
            echo ""
            echo "Options:"
            echo "  --from-release [TAG|URL]  Install from GitHub Release artifact (default: latest)"
            echo "  -h, --help                Show this help message"
            exit 0
            ;;
        *)
            echo "Unknown option: $1" >&2
            exit 1
            ;;
    esac
done

if [[ -n "$FROM_RELEASE" ]]; then
    TMP_DIR="$(mktemp -d)"
    CLEANUP_TMP="$TMP_DIR"
    
    if [[ "$FROM_RELEASE" =~ ^(https?|file):// ]]; then
        RELEASE_URL="$FROM_RELEASE"
    elif [[ -f "$FROM_RELEASE" ]]; then
        RELEASE_URL="file://$(cd "$(dirname "$FROM_RELEASE")" && pwd)/$(basename "$FROM_RELEASE")"
    elif [[ "$FROM_RELEASE" == "latest" ]]; then
        RELEASE_URL="https://github.com/${DEFAULT_REPO}/releases/latest/download/bug-fixer-daemon.tar.gz"
    else
        TAG="$FROM_RELEASE"
        if [[ "$TAG" =~ ^[0-9] ]]; then
            TAG="v$TAG"
        fi
        RELEASE_URL="https://github.com/${DEFAULT_REPO}/releases/download/${TAG}/bug-fixer-daemon.tar.gz"
    fi

    if [[ "$RELEASE_URL" =~ \.zip$ ]]; then
        ARCHIVE_FILE="$TMP_DIR/bug-fixer-daemon.zip"
    else
        ARCHIVE_FILE="$TMP_DIR/bug-fixer-daemon.tar.gz"
    fi

    echo "Downloading release artifact..."
    echo "  URL: $RELEASE_URL"

    if command -v curl &>/dev/null; then
        curl -sSL -f "$RELEASE_URL" -o "$ARCHIVE_FILE" || {
            echo "Error: Failed to download release artifact from $RELEASE_URL" >&2
            exit 1
        }
    elif command -v wget &>/dev/null; then
        wget -q -O "$ARCHIVE_FILE" "$RELEASE_URL" || {
            echo "Error: Failed to download release artifact from $RELEASE_URL" >&2
            exit 1
        }
    else
        echo "Error: curl or wget required to download release artifact" >&2
        exit 1
    fi

    EXTRACT_DIR="$TMP_DIR/extracted"
    mkdir -p "$EXTRACT_DIR"

    if [[ "$ARCHIVE_FILE" =~ \.zip$ ]]; then
        unzip -q -o "$ARCHIVE_FILE" -d "$EXTRACT_DIR"
    else
        tar -xzf "$ARCHIVE_FILE" -C "$EXTRACT_DIR"
    fi

    SOURCE_DIR="$EXTRACT_DIR"
else
    if [[ -d "$REPO_DIR/daemon" ]]; then
        SOURCE_DIR="$REPO_DIR/daemon"
    else
        echo "Error: Local daemon directory not found at $REPO_DIR/daemon" >&2
        echo "To install directly from GitHub release without cloning, run:" >&2
        echo "  $0 --from-release" >&2
        exit 1
    fi
fi

echo "Installing bug-fixer daemon plugin..."
echo "  Source: $SOURCE_DIR/"
echo "  Target: $INSTALL_DIR/"

# Create target (sidecars/ persists per-repo data)
mkdir -p "$INSTALL_DIR/sidecars"

# Sync daemon structure, preserving sidecars/
rsync -a --delete --exclude='sidecars/' "$SOURCE_DIR/" "$INSTALL_DIR/"

# Refresh existing sidecars (updates paths, preserves per-repo values like project ID)
if command -v python3 &>/dev/null && [[ -d "$INSTALL_DIR/sidecars" && -f "$INSTALL_DIR/scripts/manage.py" ]]; then
    echo "  Refreshing existing sidecars..."
    python3 "$INSTALL_DIR/scripts/manage.py" refresh-sidecars 2>&1 | sed 's/^/  /'
fi

# Enable the plugin
if command -v agy &>/dev/null; then
    agy plugin enable bug-fixer-daemon 2>/dev/null || true
    echo "  Plugin enabled via 'agy plugin enable'"
else
    echo "  Note: 'agy' CLI not found — enable manually in Automations panel or settings"
fi

echo ""
echo "Installed successfully."
echo "To enroll repos, use the bug-fixer-manage skill from within each target workspace."
