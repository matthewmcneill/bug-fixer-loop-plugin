#!/usr/bin/env bash
# install.sh — Provision Bug Fixer Loop plugin for a target repository.
#
# Architecture: The plugin is SELF-CONTAINED. This script writes:
#   1. Workspace instance config  → <repo>/.agents/plugins/bug-fixer-loop-workspace/config.json
#   2. Workspace hooks            → <repo>/.agents/hooks.json (merged, not overwritten)
#   3. Workspace rules            → <repo>/.agents/rules/AGENTS.md
#   4. Sidecar stubs (global)     → ~/.gemini/config/sidecars/<name>--<repo-slug>/sidecar.json
#
# Sidecar stubs are the ONLY global write. They contain no plugin logic — just
# a cron schedule, absolute dispatcher path, and workspace_uris binding.
# Re-running install.sh repairs stubs if the plugin or repo directory moves.
# Skills, scripts, and agents are used in-place from the plugin directory.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLUGIN_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

# Sidecar discovery directory (where Antigravity's SidecarManager finds sidecars)
SIDECARS_DIR="${HOME}/.gemini/config/sidecars"

TARGET_REPO=""
TARGET_REPO_DIR=""
MAINTAINER_USER=""
SKILLS_ONLY=false

usage() {
  cat <<EOF
Usage: ./scripts/install.sh [OPTIONS]

Provisions the Bug Fixer Loop plugin for a target repository.
Plugin logic stays in-place. Only sidecar stubs are written globally.

Options:
  -r, --repo <owner/repo>       Target GitHub repository (required)
  -d, --repo-dir <path>         Local checkout path (required if not discoverable)
  -m, --maintainer <login>      Trusted maintainer (defaults to gh api user -q .login)
  --skills-only                 Write workspace config and hooks without configuring sidecars
  -h, --help                    Show this help message
EOF
  exit 0
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)
      usage
      ;;
    --skills-only)
      SKILLS_ONLY=true
      shift
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
    -m|--maintainer)
      if [[ $# -lt 2 ]]; then
        echo "Error: Option $1 requires an argument." >&2
        exit 1
      fi
      MAINTAINER_USER="$2"
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

if [[ -z "$TARGET_REPO" ]]; then
  echo "Error: Target repository (-r, --repo <owner/repo>) is required." >&2
  usage
fi

REPO_NAME="${TARGET_REPO##*/}"

# Discover repo dir if not specified
if [[ -z "$TARGET_REPO_DIR" ]]; then
  # Try common locations
  for candidate in \
    "${HOME}/Documents/Projects/${REPO_NAME}" \
    "${HOME}/projects/${REPO_NAME}" \
    "${HOME}/${REPO_NAME}"; do
    if [[ -d "$candidate" ]]; then
      TARGET_REPO_DIR="$candidate"
      break
    fi
  done
  if [[ -z "$TARGET_REPO_DIR" ]]; then
    echo "Error: Cannot auto-discover repo directory for '${REPO_NAME}'." >&2
    echo "       Please specify with -d, --repo-dir <path>." >&2
    exit 1
  fi
fi

if [[ ! -d "$TARGET_REPO_DIR" ]]; then
  echo "Error: Target repo directory '${TARGET_REPO_DIR}' does not exist." >&2
  exit 1
fi

if [[ -z "$MAINTAINER_USER" ]]; then
  if command -v gh >/dev/null 2>&1; then
    MAINTAINER_USER="$(gh api user -q .login 2>/dev/null || true)"
  fi
  if [[ -z "$MAINTAINER_USER" ]]; then
    MAINTAINER_USER="${USER:-maintainer}"
  fi
fi

# Workspace state directory (inside the consumer repo)
WORKSPACE_DIR="${TARGET_REPO_DIR}/.agents/plugins/bug-fixer-loop-workspace"

echo "================================================================="
echo "  Installing Bug Fixer Loop Plugin (workspace-scoped)"
echo "  Plugin Dir:    ${PLUGIN_DIR}"
echo "  Target:        ${TARGET_REPO}"
echo "  Repo Dir:      ${TARGET_REPO_DIR}"
echo "  Workspace Dir: ${WORKSPACE_DIR}"
echo "  Maintainer:    ${MAINTAINER_USER}"
echo "================================================================="


# ---------------------------------------------------------------------------
# Step 1: Create workspace instance config
# ---------------------------------------------------------------------------
echo ""
echo "--> Step 1: Creating workspace instance config..."
mkdir -p "${WORKSPACE_DIR}"

# Read defaults from the plugin template
WORKSPACE_CONFIG="${WORKSPACE_DIR}/config.json"

python3 - <<EOF
import json
from pathlib import Path

# Defaults for a fresh config (no template file needed in plugin root)
DEFAULTS = {
    "\$schema": "bug-fixer-loop-config-v1",
    "target_repo": "",
    "maintainer": "",
    "trusted_users": [],
    "spec_file_path": "docs/design_specification.md",
    "protected_files": ["docs/design_specification.md"],
    "leak_patterns": [
        "ya29\\\\.[A-Za-z0-9_-]+",
        "AKIA[0-9A-Z]{16}",
        "-----BEGIN (RSA |EC )?PRIVATE KEY-----"
    ],
    "max_concurrent_prs": 4,
    "exclusion_labels": ["human-required", "P1-blocker", "hardware-required"],
    "auto_fixable_labels": ["auto-fixable"],
    "required_labels": [
        "auto-fixable", "human-required", "needs-approach-review",
        "project-candidate", "input-needed", "status:in-progress",
        "bug-fixer", "bug-fixer-health", "spec-change-request"
    ],
    "test_command": "",
    "lint_command": "",
    "pr_prefix": "[BugFixer]",
    "branch_prefix": "fix",
    "triage_cron": "0 * * * *",
    "tick_cron": "*/10 * * * *",
    "trajectory_review_cron": "0 18 * * *",
    "milestone_priority_boost": True,
    "active_milestone": "",
    "additional_design_docs": []
}

workspace_config_path = Path("${WORKSPACE_CONFIG}")

# Start from defaults
config = dict(DEFAULTS)

# Merge with existing workspace config (preserve user customizations)
if workspace_config_path.is_file():
    try:
        with open(workspace_config_path) as f:
            existing = json.load(f)
        config.update(existing)
    except Exception:
        pass

# Set/override instance-specific values (no paths — those are derived at runtime)
config["target_repo"] = "${TARGET_REPO}"
config["maintainer"] = "${MAINTAINER_USER}"

with open(workspace_config_path, "w") as f:
    json.dump(config, f, indent=2)
    f.write("\n")
print(f"    - Written: {workspace_config_path}")
EOF


# ---------------------------------------------------------------------------
# Step 2: Install workspace-scoped hooks
# ---------------------------------------------------------------------------
echo ""
echo "--> Step 2: Installing workspace-scoped hooks..."

python3 - <<EOF
import json
from pathlib import Path

plugin_hooks_file = Path("${PLUGIN_DIR}/hooks.json.template")
plugin_dir = "${PLUGIN_DIR}"

with open(plugin_hooks_file) as f:
    plugin_hooks_text = f.read()

# Resolve <plugin_dir> placeholder to absolute path
plugin_hooks_text = plugin_hooks_text.replace("<plugin_dir>", plugin_dir)
plugin_hooks = json.loads(plugin_hooks_text)

# Only write to workspace-scoped hooks — NEVER to global config
dest_path = Path("${TARGET_REPO_DIR}/.agents/hooks.json")
dest_path.parent.mkdir(parents=True, exist_ok=True)

existing = {}
if dest_path.is_file():
    try:
        with open(dest_path) as f:
            existing = json.load(f)
    except Exception:
        existing = {}

existing.update(plugin_hooks)
with open(dest_path, "w") as f:
    json.dump(existing, f, indent=2)
    f.write("\n")
print(f"    - Installed hooks: {dest_path}")
EOF


# ---------------------------------------------------------------------------
# Step 3: Install workspace-scoped rules
# ---------------------------------------------------------------------------
echo ""
echo "--> Step 3: Installing workspace-scoped rules..."

if [[ -d "${TARGET_REPO_DIR}" ]]; then
  mkdir -p "${TARGET_REPO_DIR}/.agents/rules"
  repo_rule_file="${TARGET_REPO_DIR}/.agents/rules/bug-fixer-AGENTS.md"
  cp "${PLUGIN_DIR}/rules/AGENTS.md" "${repo_rule_file}"
  echo "    - Installed: ${repo_rule_file}"
else
  echo "    - Skipped: target repo dir does not exist"
fi


# ---------------------------------------------------------------------------
# Step 4: Configure sidecars
# ---------------------------------------------------------------------------
if [[ "$SKILLS_ONLY" = true ]]; then
  echo ""
  echo "--> Step 4: Skipping sidecars (--skills-only specified)."
else
  echo ""
  echo "--> Step 4: Configuring sidecars (Option 1: per-repo stubs)..."

  # Read cron schedules from workspace config (just written in Step 1)
  TRIAGE_CRON="$(python3 -c "import json; c=json.load(open('${WORKSPACE_CONFIG}')); print(c.get('triage_cron', '0 * * * *'))" 2>/dev/null || echo "0 * * * *")"
  TICK_CRON="$(python3 -c "import json; c=json.load(open('${WORKSPACE_CONFIG}')); print(c.get('tick_cron', '*/10 * * * *'))" 2>/dev/null || echo "*/10 * * * *")"
  TRAJECTORY_CRON="$(python3 -c "import json; c=json.load(open('${WORKSPACE_CONFIG}')); print(c.get('trajectory_review_cron', '0 18 * * *'))" 2>/dev/null || echo "0 18 * * *")"

  CURRENT_PATH="${PATH}"

  # Derive repo slug for sidecar directory namespacing (e.g. "esp32-app-foundation")
  REPO_SLUG="${REPO_NAME}"

  for sidecar_dir in "${PLUGIN_DIR}/sidecars"/*; do
    if [[ -d "${sidecar_dir}" && -f "${sidecar_dir}/sidecar.json" ]]; then
      sidecar_base="$(basename "${sidecar_dir}")"
      sidecar_name="${sidecar_base}--${REPO_SLUG}"
      echo "    - Configuring sidecar: ${sidecar_name}"

      # Resolve template placeholders with absolute paths
      dest_dir="${SIDECARS_DIR}/${sidecar_name}"
      mkdir -p "${dest_dir}"

      sed -e "s|<owner/repo>|${TARGET_REPO}|g" \
          -e "s|<target_repo>|${TARGET_REPO}|g" \
          -e "s|<repo_dir>|${TARGET_REPO_DIR}|g" \
          -e "s|<maintainer>|${MAINTAINER_USER}|g" \
          -e "s|<plugin_dir>|${PLUGIN_DIR}|g" \
          -e "s|<triage_cron>|${TRIAGE_CRON}|g" \
          -e "s|<tick_cron>|${TICK_CRON}|g" \
          -e "s|<trajectory_review_cron>|${TRAJECTORY_CRON}|g" \
          -e "s|<path>|${CURRENT_PATH}|g" \
          -e "s|<config_path>|${WORKSPACE_CONFIG}|g" \
          "${sidecar_dir}/sidecar.json" > "${dest_dir}/sidecar.json"

      echo "      -> ${dest_dir}/sidecar.json"
    fi
  done

  echo ""
  echo "    Sidecar stubs written to: ${SIDECARS_DIR}/"
  echo "    Re-run install.sh to repair absolute paths if the plugin or repo moves."
fi


# ---------------------------------------------------------------------------
# Step 5: Create .gitignore for workspace state
# ---------------------------------------------------------------------------
echo ""
echo "--> Step 5: Creating workspace .gitignore..."
GITIGNORE="${WORKSPACE_DIR}/.gitignore"
if [[ ! -f "$GITIGNORE" ]]; then
  cat > "$GITIGNORE" <<'GITIGNORE_EOF'
# Bug Fixer Loop — workspace state (runtime, not committed)
claims/
cooldown.json
failure_count
triage_cooldown.json
triage_failure_count
GITIGNORE_EOF
  echo "    - Created: ${GITIGNORE}"
else
  echo "    - Already exists: ${GITIGNORE}"
fi


# ---------------------------------------------------------------------------
# Step 6: Enable the plugin
# ---------------------------------------------------------------------------
# The submodule ships with "disabled": true in plugin.json to prevent
# Antigravity from auto-loading unresolved templates before install.sh runs.
# Write a workspace-scoped plugin.json override that enables it.
echo ""
echo "--> Step 6: Enabling plugin..."

PLUGIN_OVERRIDE="${WORKSPACE_DIR}/plugin.json"
cat > "${PLUGIN_OVERRIDE}" <<PLUGINJSON
{
  "name": "bug-fixer-loop",
  "version": "0.1.0",
  "disabled": false,
  "description": "Autonomous bug fixer loop — installed and configured for ${TARGET_REPO}."
}
PLUGINJSON
echo "    - Written: ${PLUGIN_OVERRIDE}"


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
echo ""
echo "================================================================="
echo "  Bug Fixer Loop Plugin — installation complete!"
echo ""
echo "  Workspace config: ${WORKSPACE_CONFIG}"
echo "  Workspace hooks:  ${TARGET_REPO_DIR}/.agents/hooks.json"
echo "  Workspace rules:  ${TARGET_REPO_DIR}/.agents/rules/bug-fixer-AGENTS.md"
if [[ "$SKILLS_ONLY" = false ]]; then
  echo "  Sidecars:         ${SIDECARS_DIR}/{bug-fixer-tick,bug-fixer-triage,trajectory-review}--${REPO_SLUG}"
fi
echo ""
echo "  Plugin source:    ${PLUGIN_DIR} (scripts, skills, agents used in-place)"
echo ""
echo "  Global writes:    Sidecar stubs only (cron + dispatcher path + workspace binding)"
echo "  Re-run to repair: ./scripts/install.sh -r ${TARGET_REPO} -d ${TARGET_REPO_DIR}"
echo "================================================================="
