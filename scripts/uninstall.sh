#!/usr/bin/env bash
# uninstall.sh - Cleanup and removal of Bug Fixer Loop plugin components
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PLUGIN_DIR="${SCRIPT_DIR}"

CONFIG_DIR="${HOME}/.gemini/config"
CONFIG_SKILLS_DIR="${CONFIG_DIR}/skills"
CONFIG_AGENTS_DIR="${CONFIG_DIR}/agents"
CONFIG_SCRIPTS_DIR="${CONFIG_DIR}/scripts"
CONFIG_RULES_DIR="${CONFIG_DIR}/rules"
CONFIG_SIDECARS_DIR="${CONFIG_DIR}/sidecars"

ANTIGRAVITY_DIR="${HOME}/.gemini/antigravity"
ANTIGRAVITY_SKILLS_DIR="${ANTIGRAVITY_DIR}/skills"
ANTIGRAVITY_AGENTS_DIR="${ANTIGRAVITY_DIR}/agents"
ANTIGRAVITY_SIDECARS_DIR="${ANTIGRAVITY_DIR}/sidecars"

JETSKI_SIDECARS_DIR="${HOME}/.gemini/jetski/sidecars"

TARGET_REPO=""
TARGET_REPO_DIR=""

usage() {
  cat <<EOF
Usage: ./scripts/uninstall.sh [OPTIONS]

Removes deployed skills, agents, scripts, hooks, and sidecars for Bug Fixer Loop.
Preserves config.json settings.

Options:
  -r, --repo <owner/repo>       Target GitHub repository to remove deployment for
  -d, --repo-dir <path>         Local checkout path (defaults to ~/projects/<repo_name>)
  -h, --help                    Show this help message
EOF
  exit 0
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)
      usage
      ;;
    -r|--repo)
      if [[ $# -lt 2 ]]; then
        echo "Error: Option $1 requires an argument." >&2
        exit 1
      fi
      TARGET_REPO="$2"
      shift 2
      ;;
    -d|--repo-dir)
      if [[ $# -lt 2 ]]; then
        echo "Error: Option $1 requires an argument." >&2
        exit 1
      fi
      TARGET_REPO_DIR="$2"
      shift 2
      ;;
    *)
      if [[ -z "$TARGET_REPO" ]]; then
        TARGET_REPO="$1"
        shift
      else
        echo "Error: Unknown argument '$1'" >&2
        usage
      fi
      ;;
  esac
done

if [[ -n "$TARGET_REPO" && -z "$TARGET_REPO_DIR" ]]; then
  REPO_NAME="${TARGET_REPO##*/}"
  TARGET_REPO_DIR="${HOME}/projects/${REPO_NAME}"
fi

echo "================================================================="
echo "  Uninstalling Bug Fixer Loop Plugin"
if [[ -n "$TARGET_REPO" ]]; then
  echo "  Target Repo: ${TARGET_REPO}"
fi
if [[ -n "$TARGET_REPO_DIR" ]]; then
  echo "  Repo Dir:    ${TARGET_REPO_DIR}"
fi
echo "================================================================="

echo ""
echo "--> Removing deployed sidecars..."
for sidecar_name in bug-fixer-triage bug-fixer-tick trajectory-review; do
  for dest_root in "${CONFIG_SIDECARS_DIR}" "${ANTIGRAVITY_SIDECARS_DIR}" "${JETSKI_SIDECARS_DIR}"; do
    target_dir="${dest_root}/${sidecar_name}"
    if [[ -d "${target_dir}" ]]; then
      rm -rf "${target_dir}"
      echo "    - Removed sidecar: ${target_dir}"
    fi
  done
done

echo ""
echo "--> Removing deployed skills..."
for skill_name in bug-fixer-triage bug-fixer-tick test-quality-audit trajectory-review; do
  for dest_root in "${CONFIG_SKILLS_DIR}" "${ANTIGRAVITY_SKILLS_DIR}"; do
    target_dir="${dest_root}/${skill_name}"
    if [[ -d "${target_dir}" ]]; then
      rm -rf "${target_dir}"
      echo "    - Removed skill: ${target_dir}"
    fi
  done
done

echo ""
echo "--> Removing deployed agents..."
for agent_file in leak-auditor.md; do
  for dest_root in "${CONFIG_AGENTS_DIR}" "${ANTIGRAVITY_AGENTS_DIR}"; do
    target_file="${dest_root}/${agent_file}"
    if [[ -f "${target_file}" ]]; then
      rm -f "${target_file}"
      echo "    - Removed agent: ${target_file}"
    fi
  done
done

echo ""
echo "--> Removing deployed security scripts..."
for script_name in safe_ingest.py pre_tool_gate.py; do
  target_file="${CONFIG_SCRIPTS_DIR}/${script_name}"
  if [[ -f "${target_file}" ]]; then
    chmod u+w "${target_file}" 2>/dev/null || true
    rm -f "${target_file}"
    echo "    - Removed script: ${target_file}"
  fi
done

echo ""
echo "--> Removing hooks.json entries..."
python3 - <<EOF
import json
from pathlib import Path

hook_paths = [Path("${CONFIG_DIR}/hooks.json")]
target_repo_dir = "${TARGET_REPO_DIR}"
if target_repo_dir:
    hook_paths.append(Path(target_repo_dir) / ".agents" / "hooks.json")

for hook_path in hook_paths:
    if hook_path.is_file():
        try:
            with open(hook_path, "r") as f:
                data = json.load(f)
            removed = False
            for key in ["bug-fixer-file-gate", "bug-fixer-command-gate"]:
                if key in data:
                    del data[key]
                    removed = True
            if removed:
                if not data:
                    hook_path.unlink()
                    print(f"    - Removed empty hooks file: {hook_path}")
                else:
                    with open(hook_path, "w") as f:
                        json.dump(data, f, indent=2)
                        f.write("\n")
                    print(f"    - Cleaned bug-fixer entries from: {hook_path}")
        except Exception as e:
            print(f"    - Warning: Could not process {hook_path}: {e}")
EOF

echo ""
echo "--> Removing rules..."
if [[ -f "${CONFIG_RULES_DIR}/AGENTS.md" ]]; then
  rm -f "${CONFIG_RULES_DIR}/AGENTS.md"
  echo "    - Removed rule: ${CONFIG_RULES_DIR}/AGENTS.md"
fi

if [[ -n "$TARGET_REPO_DIR" && -f "${TARGET_REPO_DIR}/.agents/rules/AGENTS.md" ]]; then
  rm -f "${TARGET_REPO_DIR}/.agents/rules/AGENTS.md"
  echo "    - Removed rule: ${TARGET_REPO_DIR}/.agents/rules/AGENTS.md"
fi

echo ""
echo "================================================================="
echo "  Uninstallation complete!"
echo "  Note: ${PLUGIN_DIR}/config.json has been preserved."
echo "================================================================="
