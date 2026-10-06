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

REPO_SLUG="${TARGET_REPO//\//-}"
SIDECAR_DIR="$INSTALL_DIR/sidecars/$REPO_SLUG"

mkdir -p "$SIDECAR_DIR/state"

# Resolve repo_dir to absolute path
TARGET_REPO_DIR="$(cd "$TARGET_REPO_DIR" 2>/dev/null && pwd || echo "$TARGET_REPO_DIR")"
if [[ ! -d "$TARGET_REPO_DIR" ]]; then
  echo "ERROR: repo directory does not exist: $TARGET_REPO_DIR"
  exit 1
fi

# 1. Build config.json
CONFIG_PATH="$SIDECAR_DIR/config.json"

if [[ "$MIGRATE_STATE" == "true" ]]; then
  OLD_STATE_DIR="$TARGET_REPO_DIR/.agents/plugins/bug-fixer-loop-workspace"
  OLD_CONFIG="$OLD_STATE_DIR/config.json"

  if [[ -f "$OLD_CONFIG" ]]; then
    echo "Migrating config from $OLD_CONFIG"
    # Use old config as base, ensuring repo_dir and target_repo are set
    python3 - "$OLD_CONFIG" "$CONFIG_PATH" "$TARGET_REPO" "$TARGET_REPO_DIR" <<'PY'
import json, sys
old_path, new_path, repo, repo_dir = sys.argv[1:]
with open(old_path) as f:
    cfg = json.load(f)
# Ensure required fields
cfg["target_repo"] = repo
cfg["repo_dir"] = repo_dir
# Normalise legacy key names
if "trajectory_review_interval_min" in cfg and "trajectory_interval_min" not in cfg:
    cfg["trajectory_interval_min"] = cfg.pop("trajectory_review_interval_min")
with open(new_path, "w") as f:
    json.dump(cfg, f, indent=2)
    f.write("\n")
PY
  else
    echo "No old config at $OLD_CONFIG — creating default"
    MIGRATE_STATE=false
  fi

  # Copy state files (claims, dispatches, pr_completions)
  if [[ -d "$OLD_STATE_DIR" ]]; then
    echo "Migrating state files from $OLD_STATE_DIR"
    for f in "$OLD_STATE_DIR"/{claims,dispatches,pr_completions.jsonl}; do
      [[ -e "$f" ]] && cp -R "$f" "$SIDECAR_DIR/state/" && echo "  copied $(basename "$f")"
    done
  fi
fi

if [[ "$MIGRATE_STATE" != "true" ]] && [[ ! -f "$CONFIG_PATH" ]]; then
  # Create default config only if no config exists
  cat > "$CONFIG_PATH" <<JSON
{
  "target_repo": "$TARGET_REPO",
  "repo_dir": "$TARGET_REPO_DIR",
  "spec_file_path": "docs/design_specification.md",
  "protected_files": [],
  "test_command": "",
  "maintainer": "",
  "trusted_users": [],
  "triage_interval_min": 60,
  "engineering_interval_min": 10,
  "architect_interval_min": 15,
  "trajectory_interval_min": 1440,
  "triage_model": "flash",
  "engineering_model": "pro",
  "architect_model": "pro",
  "trajectory_model": "flash"
}
JSON
elif [[ "$MIGRATE_STATE" != "true" ]] && [[ -f "$CONFIG_PATH" ]]; then
  echo "Config already exists at $CONFIG_PATH — keeping it"
fi
echo "Config: $CONFIG_PATH"

# 2. Output sidecar.json
SIDECAR_PATH="$SIDECAR_DIR/sidecar.json"
python3 - "$SIDECAR_PATH" "$INSTALL_DIR" "$TARGET_REPO" "$TARGET_REPO_DIR" "$CONFIG_PATH" "$SIDECAR_DIR/state" <<'PY'
import json, sys, os
out_path, install_dir, repo, repo_dir, config_path, state_dir = sys.argv[1:]
sidecar = {
    "builtin": "schedule",
    "args": ["*/5 * * * *", "python3", f"{install_dir}/scripts/tick.py"],
    "restart_policy": "always",
    "display_name": f"Bug Fixer \u2014 {repo}",
    "agent_permissions": {
        "workspace_uris": [f"file://{repo_dir}"]
    },
    "env": {
        "BUG_FIXER_CONFIG": config_path,
        "BUG_FIXER_STATE_DIR": state_dir,
        "PATH": os.environ.get("PATH", "/usr/bin:/bin:/usr/local/bin"),
        "ANTIGRAVITY_PROJECT_ID": repo
    }
}
with open(out_path, "w") as f:
    json.dump(sidecar, f, indent=2)
    f.write("\n")
PY
echo "Created sidecar: $SIDECAR_PATH"

# 3. Create or merge consumer .agents/plugins.json
PLUGINS_JSON="$TARGET_REPO_DIR/.agents/plugins.json"
mkdir -p "$(dirname "$PLUGINS_JSON")"

python3 - "$PLUGINS_JSON" <<'PY'
import json, os, sys
path = sys.argv[1]
entry = {"path": "~/.gemini/config/plugins/bug-fixer-daemon/workspace-plugin"}

data = {"entries": []}
if os.path.exists(path):
    try:
        with open(path, "r") as f:
            data = json.load(f)
    except Exception:
        pass

if "entries" not in data:
    data["entries"] = []

if entry not in data["entries"]:
    data["entries"].append(entry)

with open(path, "w") as f:
    json.dump(data, f, indent=2)
    f.write("\n")
PY
echo "Updated consumer plugins.json: $PLUGINS_JSON"

echo "Enrollment complete."
