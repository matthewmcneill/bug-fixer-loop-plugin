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
  if [[ -f "$INSTALL_DIR/scripts/manage.py" ]]; then
    python3 - "$INSTALL_DIR/scripts" "$TARGET_REPO_DIR" <<'PY'
import sys

scripts_dir, repo_dir = sys.argv[1], sys.argv[2]
sys.path.insert(0, scripts_dir)
import manage

if manage._unstage_workspace_plugin(repo_dir):
    print(f"Removed staged workspace plugin from {repo_dir}")
pj_status = manage._remove_bug_fixer_from_plugins_json(repo_dir)
if pj_status in ("updated", "unlinked"):
    print(f"Cleaned consumer plugins.json ({pj_status})")
PY
  else
    STAGED_DIR="$TARGET_REPO_DIR/.agents/plugins/bug-fixer"
    if [[ -L "$STAGED_DIR" ]]; then
      rm -f "$STAGED_DIR"
      echo "Removed staged workspace plugin symlink: $STAGED_DIR"
    elif [[ -d "$STAGED_DIR" ]]; then
      rm -rf "$STAGED_DIR"
      echo "Removed staged workspace plugin: $STAGED_DIR"
    fi
  fi
fi

echo "Unenrollment complete for $TARGET_REPO."
