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
    if "bug-fixer-daemon" in data:
        del data["bug-fixer-daemon"]
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
