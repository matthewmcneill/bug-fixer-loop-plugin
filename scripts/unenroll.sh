#!/usr/bin/env bash
set -euo pipefail

INSTALL_DIR="$HOME/.gemini/config/plugins/bug-fixer-daemon"

TARGET_REPO=""
TARGET_REPO_DIR=""

usage() {
  cat <<EOF
Usage: ./scripts/unenroll.sh -r <owner/repo> [-d <repo-dir>]
EOF
  exit 0
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    -r)
      TARGET_REPO="$2"
      shift 2
      ;;
    -d)
      TARGET_REPO_DIR="$2"
      shift 2
      ;;
    *)
      usage
      ;;
  esac
done

if [[ -z "$TARGET_REPO" ]]; then
  usage
fi

REPO_SLUG="${TARGET_REPO//\//--}"
SIDECAR_DIR="$INSTALL_DIR/sidecars/$REPO_SLUG"

if [[ -d "$SIDECAR_DIR/state/claims" ]]; then
  if [ "$(ls -A $SIDECAR_DIR/state/claims 2>/dev/null)" ]; then
    echo "Warning: In-flight claims found in $SIDECAR_DIR/state/claims/"
    echo "Please wait for agents to finish or clear them manually."
    exit 1
  fi
fi

if [[ -d "$SIDECAR_DIR" ]]; then
  rm -rf "$SIDECAR_DIR"
  echo "Removed sidecar directory: $SIDECAR_DIR"
else
  echo "Sidecar directory not found: $SIDECAR_DIR"
fi

if [[ -n "$TARGET_REPO_DIR" ]]; then
  PLUGINS_JSON="$TARGET_REPO_DIR/.agents/plugins.json"
  if [[ -f "$PLUGINS_JSON" ]]; then
    python3 - <<EOF
import json
import os

path = "$PLUGINS_JSON"
try:
    with open(path, "r") as f:
        data = json.load(f)
    
    entries = data.get("entries", [])
    new_entries = [e for e in entries if e.get("path") != "~/.gemini/config/plugins/bug-fixer-daemon/workspace-plugin"]
    
    data["entries"] = new_entries
    
    with open(path, "w") as f:
        json.dump(data, f, indent=2)
except Exception as e:
    print(f"Error updating {path}: {e}")
EOF
    echo "Removed entry from $PLUGINS_JSON"
  fi

  # Also clean up injected hooks
  HOOKS_JSON="$TARGET_REPO_DIR/.agents/hooks.json"
  if [[ -f "$HOOKS_JSON" ]]; then
    python3 - <<EOF2
import json
path = "$HOOKS_JSON"
try:
    with open(path, "r") as f:
        data = json.load(f)
    bf_keys = [k for k in data if k.startswith("bug-fixer-")]
    for k in bf_keys:
        del data[k]
    with open(path, "w") as f:
        json.dump(data, f, indent=2)
        f.write("\n")
    if bf_keys:
        print(f"Removed {len(bf_keys)} bug-fixer hooks from {path}")
except Exception as e:
    print(f"Error updating {path}: {e}")
EOF2
  fi
fi

echo "Unenrollment complete for $TARGET_REPO."
