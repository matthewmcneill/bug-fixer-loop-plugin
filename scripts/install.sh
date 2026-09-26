#!/usr/bin/env bash
# install.sh - Provision and configure Bug Fixer Loop plugin components
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
MAINTAINER_USER=""
SKILLS_ONLY=false

usage() {
  cat <<EOF
Usage: ./scripts/install.sh [OPTIONS]

Provisions and configures the Bug Fixer Loop plugin for a target repository.

Options:
  -r, --repo <owner/repo>       Target GitHub repository (required)
  -d, --repo-dir <path>         Local checkout path (defaults to ~/projects/<repo_name>)
  -m, --maintainer <login>      Trusted maintainer (defaults to gh api user -q .login)
  --skills-only                 Install skills, agents, and scripts without configuring sidecars
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

if [[ -z "$TARGET_REPO_DIR" ]]; then
  TARGET_REPO_DIR="${HOME}/projects/${REPO_NAME}"
fi

if [[ -z "$MAINTAINER_USER" ]]; then
  if command -v gh >/dev/null 2>&1; then
    MAINTAINER_USER="$(gh api user -q .login 2>/dev/null || true)"
  fi
  if [[ -z "$MAINTAINER_USER" ]]; then
    MAINTAINER_USER="${USER:-maintainer}"
  fi
fi

echo "================================================================="
echo "  Installing Bug Fixer Loop Plugin"
echo "  Plugin Dir: ${PLUGIN_DIR}"
echo "  Target:     ${TARGET_REPO} (${TARGET_REPO_DIR})"
echo "  Maintainer: ${MAINTAINER_USER}"
echo "================================================================="

# 1. Install Security Scripts (Read-Only 0555)
echo ""
echo "--> Step 1: Installing security scripts (0555)..."
mkdir -p "${CONFIG_SCRIPTS_DIR}"

for script_name in safe_ingest.py pre_tool_gate.py; do
  src_script="${PLUGIN_DIR}/scripts/${script_name}"
  if [[ -f "${src_script}" ]]; then
    target_path="${CONFIG_SCRIPTS_DIR}/${script_name}"
    chmod u+w "${target_path}" 2>/dev/null || true
    cp "${src_script}" "${target_path}"
    chmod 555 "${target_path}"
    echo "    - Installed & locked (0555): ${target_path}"
  else
    echo "    - Warning: Script not found: ${src_script}" >&2
  fi
done

# 2. Install Skills
echo ""
echo "--> Step 2: Installing skills..."
mkdir -p "${CONFIG_SKILLS_DIR}" "${ANTIGRAVITY_SKILLS_DIR}"

for skill_dir in "${PLUGIN_DIR}/skills"/*; do
  if [[ -d "${skill_dir}" ]]; then
    skill_name="$(basename "${skill_dir}")"
    echo "    - Installing skill: ${skill_name}"
    mkdir -p "${CONFIG_SKILLS_DIR}/${skill_name}"
    cp -R "${skill_dir}/"* "${CONFIG_SKILLS_DIR}/${skill_name}/"
    find "${CONFIG_SKILLS_DIR}/${skill_name}" -type f -name "*.md" -exec sed -i '' \
      -e "s|<owner/repo>|${TARGET_REPO}|g" \
      -e "s|<target_repo>|${TARGET_REPO}|g" \
      -e "s|<repo_dir>|${TARGET_REPO_DIR}|g" \
      -e "s|<maintainer>|${MAINTAINER_USER}|g" \
      -e "s|<plugin_dir>|${PLUGIN_DIR}|g" {} +

    if [[ "$(realpath "${CONFIG_SKILLS_DIR}" 2>/dev/null || echo "1")" != "$(realpath "${ANTIGRAVITY_SKILLS_DIR}" 2>/dev/null || echo "2")" ]]; then
      mkdir -p "${ANTIGRAVITY_SKILLS_DIR}/${skill_name}"
      cp -R "${CONFIG_SKILLS_DIR}/${skill_name}/"* "${ANTIGRAVITY_SKILLS_DIR}/${skill_name}/"
    fi
  fi
done

# 3. Install Agents
echo ""
echo "--> Step 3: Installing agents..."
mkdir -p "${CONFIG_AGENTS_DIR}" "${ANTIGRAVITY_AGENTS_DIR}"

for agent_file in "${PLUGIN_DIR}/agents"/*.md; do
  if [[ -f "${agent_file}" ]]; then
    agent_name="$(basename "${agent_file}")"
    echo "    - Installing agent: ${agent_name}"
    sed -e "s|<owner/repo>|${TARGET_REPO}|g" \
        -e "s|<target_repo>|${TARGET_REPO}|g" \
        -e "s|<repo_dir>|${TARGET_REPO_DIR}|g" \
        -e "s|<maintainer>|${MAINTAINER_USER}|g" \
        -e "s|<plugin_dir>|${PLUGIN_DIR}|g" \
        "${agent_file}" > "${CONFIG_AGENTS_DIR}/${agent_name}"

    if [[ "$(realpath "${CONFIG_AGENTS_DIR}" 2>/dev/null || echo "1")" != "$(realpath "${ANTIGRAVITY_AGENTS_DIR}" 2>/dev/null || echo "2")" ]]; then
      cp "${CONFIG_AGENTS_DIR}/${agent_name}" "${ANTIGRAVITY_AGENTS_DIR}/${agent_name}"
    fi
  fi
done

# 4. Install Hooks
echo ""
echo "--> Step 4: Installing hooks..."
python3 - <<EOF
import json
from pathlib import Path

plugin_hooks_file = "${PLUGIN_DIR}/hooks.json"
plugin_dir = "${PLUGIN_DIR}"

with open(plugin_hooks_file, "r") as f:
    plugin_hooks_text = f.read()

plugin_hooks_text = plugin_hooks_text.replace("scripts/pre_tool_gate.py", f"{plugin_dir}/scripts/pre_tool_gate.py")
plugin_hooks_text = plugin_hooks_text.replace("<plugin_dir>", plugin_dir)
plugin_hooks = json.loads(plugin_hooks_text)

dest_paths = [Path("${CONFIG_DIR}/hooks.json")]
target_repo_dir = Path("${TARGET_REPO_DIR}")
if target_repo_dir.is_dir():
    dest_paths.append(target_repo_dir / ".agents" / "hooks.json")

for dest_path in dest_paths:
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    existing = {}
    if dest_path.is_file():
        try:
            with open(dest_path, "r") as f:
                existing = json.load(f)
        except Exception:
            existing = {}
    existing.update(plugin_hooks)
    with open(dest_path, "w") as f:
        json.dump(existing, f, indent=2)
        f.write("\n")
    print(f"    - Installed hooks into: {dest_path}")
EOF

# 5. Install Rules
echo ""
echo "--> Step 5: Installing rules..."
mkdir -p "${CONFIG_RULES_DIR}"
target_rule_file="${CONFIG_RULES_DIR}/AGENTS.md"
cp "${PLUGIN_DIR}/rules/AGENTS.md" "${target_rule_file}"
echo "    - Installed rule: ${target_rule_file}"

if [[ -d "${TARGET_REPO_DIR}" ]]; then
  mkdir -p "${TARGET_REPO_DIR}/.agents/rules"
  repo_rule_file="${TARGET_REPO_DIR}/.agents/rules/AGENTS.md"
  cp "${PLUGIN_DIR}/rules/AGENTS.md" "${repo_rule_file}"
  echo "    - Installed rule: ${repo_rule_file}"
fi

# 6. Configure Sidecars
if [[ "$SKILLS_ONLY" = true ]]; then
  echo ""
  echo "--> Step 6: Skipping sidecars (--skills-only specified)."
else
  echo ""
  echo "--> Step 6: Configuring sidecars..."
  mkdir -p "${CONFIG_SIDECARS_DIR}" "${ANTIGRAVITY_SIDECARS_DIR}" "${JETSKI_SIDECARS_DIR}"

  CONFIG_FILE="${PLUGIN_DIR}/config.json"
  TRIAGE_CRON="$(python3 -c "import json; c=json.load(open('${CONFIG_FILE}')); print(c.get('triage_cron', '0 * * * *'))" 2>/dev/null || echo "0 * * * *")"
  TICK_CRON="$(python3 -c "import json; c=json.load(open('${CONFIG_FILE}')); print(c.get('tick_cron', '30 */2 * * *'))" 2>/dev/null || echo "30 */2 * * *")"
  TRAJECTORY_CRON="$(python3 -c "import json; c=json.load(open('${CONFIG_FILE}')); print(c.get('trajectory_review_cron', '0 18 * * *'))" 2>/dev/null || echo "0 18 * * *")"

  for sidecar_dir in "${PLUGIN_DIR}/sidecars"/*; do
    if [[ -d "${sidecar_dir}" && -f "${sidecar_dir}/sidecar.json" ]]; then
      sidecar_name="$(basename "${sidecar_dir}")"
      echo "    - Configuring sidecar: ${sidecar_name}"

      tmp_sidecar="$(mktemp)"
      sed -e "s|__HOME__|${HOME}|g" \
          -e "s|<owner/repo>|${TARGET_REPO}|g" \
          -e "s|<target_repo>|${TARGET_REPO}|g" \
          -e "s|<repo_dir>|${TARGET_REPO_DIR}|g" \
          -e "s|<maintainer>|${MAINTAINER_USER}|g" \
          -e "s|<plugin_dir>|${PLUGIN_DIR}|g" \
          -e "s|<triage_cron>|${TRIAGE_CRON}|g" \
          -e "s|<tick_cron>|${TICK_CRON}|g" \
          -e "s|<trajectory_review_cron>|${TRAJECTORY_CRON}|g" \
          "${sidecar_dir}/sidecar.json" > "${tmp_sidecar}"

      for dest_root in "${CONFIG_SIDECARS_DIR}" "${ANTIGRAVITY_SIDECARS_DIR}" "${JETSKI_SIDECARS_DIR}"; do
        mkdir -p "${dest_root}/${sidecar_name}"
        cp "${tmp_sidecar}" "${dest_root}/${sidecar_name}/sidecar.json"
        echo "      -> ${dest_root}/${sidecar_name}/sidecar.json"
      done
      rm -f "${tmp_sidecar}"
    fi
  done
fi

# 7. Update config.json
echo ""
echo "--> Step 7: Updating plugin config.json..."
python3 - <<EOF
import json
config_path = "${PLUGIN_DIR}/config.json"
with open(config_path, "r") as f:
    config = json.load(f)
config["target_repo"] = "${TARGET_REPO}"
config["repo_dir"] = "${TARGET_REPO_DIR}"
config["maintainer"] = "${MAINTAINER_USER}"
with open(config_path, "w") as f:
    json.dump(config, f, indent=2)
    f.write("\n")
EOF
echo "    - Updated ${PLUGIN_DIR}/config.json with target_repo, repo_dir, maintainer"

echo ""
echo "================================================================="
echo "  Bug Fixer Loop Plugin installation complete!"
echo "  - Security Scripts: ${CONFIG_SCRIPTS_DIR} (locked 0555)"
echo "  - Skills:           ${CONFIG_SKILLS_DIR}"
echo "  - Agents:           ${CONFIG_AGENTS_DIR}"
echo "  - Rules:            ${CONFIG_RULES_DIR}/AGENTS.md"
echo "  - Hooks:            ${CONFIG_DIR}/hooks.json"
if [[ "$SKILLS_ONLY" = false ]]; then
  echo "  - Sidecars:         ${CONFIG_SIDECARS_DIR} (Configured for: ${TARGET_REPO})"
fi
echo "  - Config:           ${PLUGIN_DIR}/config.json"
echo "================================================================="
