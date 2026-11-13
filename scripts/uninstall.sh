#!/usr/bin/env bash
# uninstall.sh — Remove bug-fixer daemon plugin and associated artifacts
set -euo pipefail

INSTALL_DIR="$HOME/.gemini/config/plugins/bug-fixer-daemon"
CONFIG_FILE="$HOME/.gemini/config/config.json"
FORCE=false

usage() {
  cat <<HELP
Usage: ./scripts/uninstall.sh [--force]

Uninstall the bug-fixer daemon plugin.
If enrolled repositories exist in sidecars/, you must pass --force to remove them,
or run './scripts/unenroll.sh -r <owner/repo> -d <repo-dir>' for each repository first.

Options:
  --force, -f    Force removal even if enrolled sidecars exist
  --help, -h     Show this help message
HELP
  exit 0
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --force|-f)
      FORCE=true
      shift
      ;;
    --help|-h)
      usage
      ;;
    *)
      echo "Unknown option: $1"
      usage
      ;;
  esac
done

echo "Bug Fixer Daemon — Uninstaller"
echo "================================"

# Check if sidecars directory exists and is non-empty
if [[ -d "$INSTALL_DIR/sidecars" ]] && [ "$(ls -A "$INSTALL_DIR/sidecars" 2>/dev/null)" ]; then
  if [[ "$FORCE" != "true" ]]; then
    echo "Error: Enrolled sidecars detected in $INSTALL_DIR/sidecars/"
    echo "Uninstalling will delete per-repo configuration and state."
    echo ""
    echo "Recommended: Run './scripts/unenroll.sh -r <owner/repo> -d <repo-dir>' for each enrolled repository."
    echo "To remove everything anyway, rerun with: ./scripts/uninstall.sh --force"
    exit 1
  else
    echo "Warning: --force specified. Removing all enrolled sidecars and state."
  fi
fi

# Disable plugin if agy CLI is present
if command -v agy &>/dev/null; then
  agy plugin disable bug-fixer-daemon 2>/dev/null || true
  echo "Disabled bug-fixer-daemon via 'agy plugin disable'"
fi

# Clean up staged workspace plugins in any enrolled repos before removing INSTALL_DIR
if [[ -d "$INSTALL_DIR/sidecars" && -f "$INSTALL_DIR/scripts/manage.py" ]]; then
  python3 - "$INSTALL_DIR/scripts" "$INSTALL_DIR/sidecars" <<'PY'
import json
import sys
from pathlib import Path

scripts_dir, sidecars_dir = sys.argv[1], Path(sys.argv[2])
sys.path.insert(0, scripts_dir)
import manage

for sc in sorted(sidecars_dir.iterdir()):
    cfg_path = sc / "config.json"
    if not cfg_path.is_file():
        continue
    try:
        repo_dir = json.loads(cfg_path.read_text(encoding="utf-8")).get("repo_dir", "")
    except (json.JSONDecodeError, OSError):
        continue
    if repo_dir and Path(repo_dir).is_dir():
        if manage._unstage_workspace_plugin(repo_dir):
            print(
                f"Removed staged workspace plugin: {Path(repo_dir) / '.agents' / 'plugins' / 'bug-fixer'}"
            )
        pj = manage._remove_bug_fixer_from_plugins_json(repo_dir)
        if pj in ("updated", "unlinked"):
            print(f"Cleaned consumer plugins.json in {repo_dir} ({pj})")
PY
fi

# Remove plugin directory
if [[ -d "$INSTALL_DIR" ]]; then
  rm -rf "$INSTALL_DIR"
  echo "Removed plugin directory: $INSTALL_DIR"
else
  echo "Plugin directory not found: $INSTALL_DIR"
fi

# Clean stale entries from ~/.gemini/config/config.json
if [[ -f "$CONFIG_FILE" ]]; then
  python3 - <<PY
import json
import os

path = "$CONFIG_FILE"
try:
    with open(path, "r") as f:
        data = json.load(f)
    changed = False
    if "bug-fixer-daemon" in data:
        del data["bug-fixer-daemon"]
        changed = True
    plugins = data.get("plugins")
    if isinstance(plugins, dict) and "bug-fixer-daemon" in plugins:
        del plugins["bug-fixer-daemon"]
        changed = True
    if changed:
        with open(path, "w") as f:
            json.dump(data, f, indent=2)
            f.write("\n")
        print("Cleaned bug-fixer-daemon key from:", path)
except Exception as e:
    print(f"Warning: Could not update {path}: {e}")
PY
fi

# Clean up legacy global artifacts if present (backward compatibility)
for f in "$HOME/.gemini/config/scripts/safe_ingest.py" \
         "$HOME/.gemini/config/scripts/pre_tool_gate.py"; do
  if [[ -f "$f" ]]; then
    chmod u+w "$f" 2>/dev/null || true
    rm -f "$f"
    echo "Removed legacy script: $f"
  fi
done

for skill in bug-fixer bug-fixer-engineering bug-fixer-tick bug-fixer-triage test-quality-audit bug-fixer-trajectory-review trajectory-review systematic-debugging; do
  for base in "$HOME/.gemini/config/skills" "$HOME/.gemini/antigravity/skills"; do
    if [[ -d "$base/$skill" ]]; then
      rm -rf "$base/$skill"
      echo "Removed legacy skill: $base/$skill"
    fi
  done
done

for base in "$HOME/.gemini/config/agents" "$HOME/.gemini/antigravity/agents"; do
  if [[ -f "$base/leak-auditor.md" ]]; then
    rm -f "$base/leak-auditor.md"
    echo "Removed legacy agent: $base/leak-auditor.md"
  fi
done

for sidecar in bug-fixer-engineering-loop bug-fixer-triage-loop bug-fixer-pr-review-loop bug-fixer-trajectory-loop bug-fixer-tick bug-fixer-triage bug-fixer-architect trajectory-review; do
  for base in "$HOME/.gemini/config/sidecars" "$HOME/.gemini/antigravity/sidecars" "$HOME/.gemini/jetski/sidecars"; do
    if [[ -d "$base/$sidecar" ]]; then
      rm -rf "$base/$sidecar"
      echo "Removed legacy sidecar: $base/$sidecar"
    fi
  done
done

echo ""
echo "Done. Daemon uninstalled successfully."
