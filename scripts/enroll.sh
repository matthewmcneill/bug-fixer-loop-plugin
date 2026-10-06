#!/usr/bin/env bash
set -euo pipefail

INSTALL_DIR="$HOME/.gemini/config/plugins/bug-fixer-daemon"

TARGET_REPO=""
TARGET_REPO_DIR=""
MIGRATE_STATE=false

usage() {
  cat <<HELP
Usage: ./scripts/enroll.sh -r <owner/repo> -d <repo-dir> [--migrate-state]
HELP
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
    --migrate-state)
      MIGRATE_STATE=true
      shift
      ;;
    *)
      usage
      ;;
  esac
done

if [[ -z "$TARGET_REPO" || -z "$TARGET_REPO_DIR" ]]; then
  usage
fi

REPO_SLUG="${TARGET_REPO//\//--}"
SIDECAR_DIR="$INSTALL_DIR/sidecars/$REPO_SLUG"

mkdir -p "$SIDECAR_DIR/state"

# 1. Output config.json
CONFIG_PATH="$SIDECAR_DIR/config.json"
cat > "$CONFIG_PATH" <<JSON
{
  "target_repo": "$TARGET_REPO",
  "repo_dir": "$TARGET_REPO_DIR",
  "spec_file_path": "docs/plugin-architecture.md",
  "protected_files": ["docs/plugin-architecture.md"],
  "test_command": "pio test -e native",
  "triage_interval_min": 60,
  "engineering_interval_min": 10,
  "architect_interval_min": 15,
  "trajectory_review_interval_min": 1440,
  "triage_model": "flash",
  "engineering_model": "pro",
  "architect_model": "pro",
  "trajectory_model": "flash"
}
JSON
echo "Created config: $CONFIG_PATH"

# 2. Output sidecar.json
SIDECAR_PATH="$SIDECAR_DIR/sidecar.json"
cat > "$SIDECAR_PATH" <<JSON
{
  "builtin": "schedule",
  "args": ["*/5 * * * *", "python3", "$INSTALL_DIR/scripts/tick.py"],
  "restart_policy": "always",
  "display_name": "Bug Fixer — $TARGET_REPO",
  "agent_permissions": {
    "workspace_uris": ["file://$TARGET_REPO_DIR"]
  },
  "env": {
    "BUG_FIXER_CONFIG": "$CONFIG_PATH",
    "BUG_FIXER_STATE_DIR": "$SIDECAR_DIR/state"
  }
}
JSON
echo "Created sidecar: $SIDECAR_PATH"

# 3. Create or merge consumer .agents/plugins.json
PLUGINS_JSON="$TARGET_REPO_DIR/.agents/plugins.json"
mkdir -p "$(dirname "$PLUGINS_JSON")"

python3 - <<PY
import json
import os

path = "$PLUGINS_JSON"
entry = {"path": "~/.gemini/config/plugins/bug-fixer-daemon/workspace-plugin"}

data = {"entries": []}
if os.path.exists(path):
    try:
        with open(path, "r") as f:
            data = json.load(f)
    except:
        pass

if "entries" not in data:
    data["entries"] = []

if entry not in data["entries"]:
    data["entries"].append(entry)

with open(path, "w") as f:
    json.dump(data, f, indent=2)
PY
echo "Updated consumer plugins.json: $PLUGINS_JSON"

# 4. Migrate state if requested
if [[ "$MIGRATE_STATE" == "true" ]]; then
  OLD_STATE_DIR="$TARGET_REPO_DIR/.agents/plugins/bug-fixer-loop-workspace"
  if [[ -d "$OLD_STATE_DIR" ]]; then
    echo "Migrating state from $OLD_STATE_DIR to $SIDECAR_DIR/state/"
    cp -R "$OLD_STATE_DIR/"* "$SIDECAR_DIR/state/" || true
  else
    echo "No old state found at $OLD_STATE_DIR"
  fi
fi

echo "Enrollment complete."
