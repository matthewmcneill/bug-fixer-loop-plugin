#!/usr/bin/env bash
# configure.sh - Interactive configuration wizard for Bug Fixer Loop plugin
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PLUGIN_DIR="${SCRIPT_DIR}"
CONFIG_FILE="${PLUGIN_DIR}/config.json"

TARGET_REPO=""
TARGET_REPO_DIR=""
MAINTAINER_USER=""
SPEC_FILE_PATH=""
TEST_COMMAND=""
LINT_COMMAND=""
WORKTREE_PATH=""
NO_WORKTREE=false
ASSUME_YES=false

usage() {
  cat <<EOF
Usage: ./scripts/configure.sh [OPTIONS]

Interactive configuration wizard for the Bug Fixer Loop plugin.

Options:
  -r, --repo <owner/repo>       Target GitHub repository (e.g. owner/repo)
  -d, --repo-dir <path>         Local checkout path of target repository
  -m, --maintainer <login>      Trusted maintainer GitHub login
  --spec-file <path>            Path to design specification file (default: docs/design_specification.md)
  --test-command <cmd>          Test command to run (default: pio test -e native)
  --lint-command <cmd>          Lint command to run (optional)
  --worktree-path <path>        Path for isolated git worktree
  --no-worktree                 Skip creating git worktree
  -y, --yes                     Non-interactive mode (accept defaults)
  -h, --help                    Show this help message
EOF
  exit 0
}

# Parse CLI arguments
while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)
      usage
      ;;
    -y|--yes)
      ASSUME_YES=true
      shift
      ;;
    --no-worktree)
      NO_WORKTREE=true
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
    --spec-file)
      if [[ $# -lt 2 ]]; then
        echo "Error: Option $1 requires an argument." >&2
        exit 1
      fi
      SPEC_FILE_PATH="$2"
      shift 2
      ;;
    --test-command)
      if [[ $# -lt 2 ]]; then
        echo "Error: Option $1 requires an argument." >&2
        exit 1
      fi
      TEST_COMMAND="$2"
      shift 2
      ;;
    --lint-command)
      if [[ $# -lt 2 ]]; then
        echo "Error: Option $1 requires an argument." >&2
        exit 1
      fi
      LINT_COMMAND="$2"
      shift 2
      ;;
    --worktree-path)
      if [[ $# -lt 2 ]]; then
        echo "Error: Option $1 requires an argument." >&2
        exit 1
      fi
      WORKTREE_PATH="$2"
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

echo "================================================================"
echo "  Bug Fixer Loop — Configuration Wizard"
echo "================================================================"

# Read initial values from config.json if present
EXISTING_TARGET_REPO=""
EXISTING_REPO_DIR=""
EXISTING_MAINTAINER=""
EXISTING_SPEC_FILE=""
EXISTING_TEST_CMD=""
EXISTING_LINT_CMD=""
EXISTING_WORKTREE_PATH=""

if [[ -f "${CONFIG_FILE}" ]]; then
  eval "$(python3 - <<EOF
import json
try:
    with open("${CONFIG_FILE}", "r") as f:
        cfg = json.load(f)
    print(f"EXISTING_TARGET_REPO='{cfg.get('target_repo', '')}'")
    print(f"EXISTING_REPO_DIR='{cfg.get('repo_dir', '')}'")
    print(f"EXISTING_MAINTAINER='{cfg.get('maintainer', '')}'")
    print(f"EXISTING_SPEC_FILE='{cfg.get('spec_file_path', '')}'")
    print(f"EXISTING_TEST_CMD='{cfg.get('test_command', '')}'")
    print(f"EXISTING_LINT_CMD='{cfg.get('lint_command', '')}'")
    print(f"EXISTING_WORKTREE_PATH='{cfg.get('worktree_path', '')}'")
except Exception:
    pass
EOF
)"
fi

# Step 1: Gather Settings
echo ""
echo "--> Step 1: Gathering Settings..."

# 1.1 Target Repo
if [[ -z "$TARGET_REPO" ]]; then
  TARGET_REPO="$EXISTING_TARGET_REPO"
fi

if [[ -z "$TARGET_REPO" ]]; then
  if [[ "$ASSUME_YES" = true || ! -t 0 ]]; then
    echo "Error: target_repo is required. Use --repo <owner/repo>" >&2
    exit 1
  fi
  while [[ -z "$TARGET_REPO" ]]; do
    read -r -p "Enter target repository (owner/repo): " TARGET_REPO
    if [[ -z "$TARGET_REPO" ]]; then
      echo "Target repository is required." >&2
    fi
  done
elif [[ "$ASSUME_YES" = false && -t 0 && -z "${2:-}" ]]; then
  # Only prompt if not set via explicit CLI flag and not -y
  read -r -p "Enter target repository (owner/repo) [${TARGET_REPO}]: " input_repo
  TARGET_REPO="${input_repo:-$TARGET_REPO}"
fi

REPO_NAME="${TARGET_REPO##*/}"

# 1.2 Local Checkout Path (repo_dir)
DEFAULT_REPO_DIR="$EXISTING_REPO_DIR"
if [[ -z "$DEFAULT_REPO_DIR" ]]; then
  if command -v gh >/dev/null 2>&1; then
    DEFAULT_REPO_DIR="$(gh repo view "$TARGET_REPO" --json path -q .path 2>/dev/null || true)"
  fi
fi
if [[ -z "$DEFAULT_REPO_DIR" ]]; then
  if [[ -d "${HOME}/Documents/Projects/${REPO_NAME}" ]]; then
    DEFAULT_REPO_DIR="${HOME}/Documents/Projects/${REPO_NAME}"
  elif [[ -d "${HOME}/projects/${REPO_NAME}" ]]; then
    DEFAULT_REPO_DIR="${HOME}/projects/${REPO_NAME}"
  else
    DEFAULT_REPO_DIR="${HOME}/projects/${REPO_NAME}"
  fi
fi

if [[ -z "$TARGET_REPO_DIR" ]]; then
  if [[ "$ASSUME_YES" = true || ! -t 0 ]]; then
    TARGET_REPO_DIR="$DEFAULT_REPO_DIR"
  else
    read -r -p "Enter local checkout path [${DEFAULT_REPO_DIR}]: " input_dir
    TARGET_REPO_DIR="${input_dir:-$DEFAULT_REPO_DIR}"
  fi
fi

# 1.3 Maintainer
DEFAULT_MAINTAINER="$EXISTING_MAINTAINER"
if [[ -z "$DEFAULT_MAINTAINER" ]]; then
  if command -v gh >/dev/null 2>&1; then
    DEFAULT_MAINTAINER="$(gh api user -q .login 2>/dev/null || true)"
  fi
fi
if [[ -z "$DEFAULT_MAINTAINER" ]]; then
  DEFAULT_MAINTAINER="${USER:-maintainer}"
fi

if [[ -z "$MAINTAINER_USER" ]]; then
  if [[ "$ASSUME_YES" = true || ! -t 0 ]]; then
    MAINTAINER_USER="$DEFAULT_MAINTAINER"
  else
    read -r -p "Enter maintainer GitHub login [${DEFAULT_MAINTAINER}]: " input_maint
    MAINTAINER_USER="${input_maint:-$DEFAULT_MAINTAINER}"
  fi
fi

# 1.4 Spec File Path
DEFAULT_SPEC="${EXISTING_SPEC_FILE:-docs/design_specification.md}"
if [[ -z "$SPEC_FILE_PATH" ]]; then
  if [[ "$ASSUME_YES" = true || ! -t 0 ]]; then
    SPEC_FILE_PATH="$DEFAULT_SPEC"
  else
    read -r -p "Enter spec file path [${DEFAULT_SPEC}]: " input_spec
    SPEC_FILE_PATH="${input_spec:-$DEFAULT_SPEC}"
  fi
fi

# 1.5 Test Command
DEFAULT_TEST="${EXISTING_TEST_CMD:-pio test -e native}"
if [[ -z "$TEST_COMMAND" ]]; then
  if [[ "$ASSUME_YES" = true || ! -t 0 ]]; then
    TEST_COMMAND="$DEFAULT_TEST"
  else
    read -r -p "Enter test command [${DEFAULT_TEST}]: " input_test
    TEST_COMMAND="${input_test:-$DEFAULT_TEST}"
  fi
fi

# 1.6 Lint Command (optional)
DEFAULT_LINT="$EXISTING_LINT_CMD"
if [[ -z "$LINT_COMMAND" && "$DEFAULT_LINT" != "" ]]; then
  LINT_COMMAND="$DEFAULT_LINT"
fi
if [[ -z "$LINT_COMMAND" ]]; then
  if [[ "$ASSUME_YES" = true || ! -t 0 ]]; then
    LINT_COMMAND="${DEFAULT_LINT:-}"
  else
    read -r -p "Enter lint command (blank to skip) [${DEFAULT_LINT}]: " input_lint
    LINT_COMMAND="${input_lint:-$DEFAULT_LINT}"
  fi
fi

# Step 2: Validate Connectivity
echo ""
echo "--> Step 2: Validating Connectivity..."

# 2.1 Check gh auth
if ! gh auth status >/dev/null 2>&1; then
  echo "Error: GitHub CLI is not authenticated. Please run 'gh auth login' first." >&2
  exit 1
fi
echo "    - GitHub CLI authenticated."

# 2.2 Check repo access
if ! gh repo view "$TARGET_REPO" >/dev/null 2>&1; then
  echo "Error: Cannot access GitHub repository '${TARGET_REPO}'. Please check repository name and permissions." >&2
  exit 1
fi
echo "    - Repository access verified for ${TARGET_REPO}."

# 2.3 Check repo_dir exists
if [[ ! -d "$TARGET_REPO_DIR" ]]; then
  echo "Warning: Target repo directory '${TARGET_REPO_DIR}' does not exist." >&2
  if [[ "$ASSUME_YES" = false && -t 0 ]]; then
    read -r -p "Would you like to clone ${TARGET_REPO} into ${TARGET_REPO_DIR} now? [y/N]: " do_clone
    do_clone="$(echo "${do_clone:-n}" | tr '[:upper:]' '[:lower:]')"
    if [[ "$do_clone" == "y" || "$do_clone" == "yes" ]]; then
      mkdir -p "$(dirname "$TARGET_REPO_DIR")"
      gh repo clone "$TARGET_REPO" "$TARGET_REPO_DIR"
      echo "    - Cloned ${TARGET_REPO} into ${TARGET_REPO_DIR}."
    fi
  fi
else
  echo "    - Local directory exists: ${TARGET_REPO_DIR}"
fi

# Step 3: Validate Spec File
echo ""
echo "--> Step 3: Validating Spec File..."
if [[ -f "${TARGET_REPO_DIR}/${SPEC_FILE_PATH}" ]]; then
  echo "    - Spec file found: ${TARGET_REPO_DIR}/${SPEC_FILE_PATH}"
else
  echo "Warning: Spec file not found at ${TARGET_REPO_DIR}/${SPEC_FILE_PATH}" >&2
fi

# Step 4: Test the Test Command
echo ""
echo "--> Step 4: Testing Test Command..."
if [[ -n "$TEST_COMMAND" && -d "$TARGET_REPO_DIR" ]]; then
  echo "    - Running: '${TEST_COMMAND}' in '${TARGET_REPO_DIR}'"
  if (cd "$TARGET_REPO_DIR" && eval "$TEST_COMMAND"); then
    echo "    - Test command PASSED."
  else
    echo "    - Test command FAILED (non-blocking: tests may require hardware or setup)."
  fi
else
  echo "    - Skipped testing test command (directory not present or command empty)."
fi

# Step 5: Create Required Labels
echo ""
echo "--> Step 5: Creating Required Labels in ${TARGET_REPO}..."

declare -A LABELS_META=(
  ["auto-fixable"]="0E8A16:Issues eligible for autonomous fixing"
  ["human-required"]="D93F0B:Issues requiring human intervention or architectural review"
  ["needs-approach-review"]="FBCA04:Fix approach requires human maintainer sign-off"
  ["project-candidate"]="5319E7:Large multi-step initiative candidate"
  ["input-needed"]="D4C5F9:Waiting for human input or clarification"
  ["status:in-progress"]="1D76DB:Currently being worked on by an agent or human"
  ["bug-fixer"]="0075CA:Managed by autonomous bug-fixer loop"
  ["bug-fixer-health"]="C5DEF5:Bug fixer health, metrics, or diagnostic report"
  ["spec-change-request"]="F9D0C4:Proposed modification to design specification"
)

REQUIRED_LABELS=(
  "auto-fixable"
  "human-required"
  "needs-approach-review"
  "project-candidate"
  "input-needed"
  "status:in-progress"
  "bug-fixer"
  "bug-fixer-health"
  "spec-change-request"
)

for label in "${REQUIRED_LABELS[@]}"; do
  meta="${LABELS_META[$label]:-ededed:Managed by bug-fixer-loop}"
  color="${meta%%:*}"
  description="${meta#*:}"
  echo "    - Ensuring label: '${label}' (${color})"
  gh label create "${label}" \
    --repo "${TARGET_REPO}" \
    --force \
    --description "${description}" \
    --color "${color}" >/dev/null 2>&1 || true
done
echo "    - Required labels configured."

# Step 6: Set Up Worktree
echo ""
echo "--> Step 6: Worktree Setup..."
CREATE_WORKTREE=true
if [[ "$NO_WORKTREE" = true ]]; then
  CREATE_WORKTREE=false
elif [[ -n "$WORKTREE_PATH" ]]; then
  CREATE_WORKTREE=true
elif [[ "$ASSUME_YES" = true || ! -t 0 ]]; then
  CREATE_WORKTREE=true
else
  read -r -p "Create isolated git worktree for agent operation? (recommended) [Y/n]: " wt_choice
  wt_choice="$(echo "${wt_choice:-y}" | tr '[:upper:]' '[:lower:]')"
  if [[ "$wt_choice" == "n" || "$wt_choice" == "no" ]]; then
    CREATE_WORKTREE=false
  fi
fi

if [[ "$CREATE_WORKTREE" = true ]]; then
  if [[ -z "$WORKTREE_PATH" ]]; then
    WORKTREE_PATH="${EXISTING_WORKTREE_PATH:-${TARGET_REPO_DIR}-bug-fixer-worktree}"
  fi

  if [[ -d "$WORKTREE_PATH" ]]; then
    echo "    - Worktree directory already exists at: ${WORKTREE_PATH}"
  elif [[ -d "$TARGET_REPO_DIR" ]] && git -C "$TARGET_REPO_DIR" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    echo "    - Creating worktree at: ${WORKTREE_PATH}"
    if git -C "$TARGET_REPO_DIR" worktree add "$WORKTREE_PATH" main >/dev/null 2>&1; then
      echo "    - Worktree created from 'main' at ${WORKTREE_PATH}"
    elif git -C "$TARGET_REPO_DIR" worktree add "$WORKTREE_PATH" HEAD >/dev/null 2>&1; then
      echo "    - Worktree created from HEAD at ${WORKTREE_PATH}"
    else
      echo "    - Warning: Failed to add worktree via git worktree add." >&2
    fi
  else
    echo "    - Target repo directory is not a git repository yet; set worktree path to ${WORKTREE_PATH}"
  fi
else
  WORKTREE_PATH=""
  echo "    - Worktree setup skipped."
fi

# Step 7: Write Config
echo ""
echo "--> Step 7: Writing Configuration..."
python3 - <<EOF
import json
from pathlib import Path

config_path = Path("${CONFIG_FILE}")
config = {}
if config_path.is_file():
    try:
        with open(config_path, "r") as f:
            config = json.load(f)
    except Exception:
        config = {}

config["target_repo"] = "${TARGET_REPO}"
config["repo_dir"] = "${TARGET_REPO_DIR}"
config["worktree_path"] = "${WORKTREE_PATH}"
config["maintainer"] = "${MAINTAINER_USER}"
config["spec_file_path"] = "${SPEC_FILE_PATH}"
config["test_command"] = "${TEST_COMMAND}"
config["lint_command"] = "${LINT_COMMAND}"

# Ensure required_labels list is complete
req_labels = [
    "auto-fixable",
    "human-required",
    "needs-approach-review",
    "project-candidate",
    "input-needed",
    "status:in-progress",
    "bug-fixer",
    "bug-fixer-health",
    "spec-change-request"
]
existing = config.get("required_labels", [])
for lbl in req_labels:
    if lbl not in existing:
        existing.append(lbl)
config["required_labels"] = existing

with open(config_path, "w") as f:
    json.dump(config, f, indent=2)
    f.write("\n")
EOF
echo "    - Updated ${CONFIG_FILE}."

# Step 8: Print Summary and Next Steps
echo ""
echo "================================================================"
echo "  Configuration Summary"
echo "================================================================"
echo "  Target Repo:    ${TARGET_REPO}"
echo "  Repo Directory: ${TARGET_REPO_DIR}"
echo "  Maintainer:     ${MAINTAINER_USER}"
echo "  Spec File:      ${SPEC_FILE_PATH}"
echo "  Test Command:   ${TEST_COMMAND}"
echo "  Lint Command:   ${LINT_COMMAND:-<none>}"
echo "  Worktree Path:  ${WORKTREE_PATH:-<none>}"
echo "  Config File:    ${CONFIG_FILE}"
echo "================================================================"
echo ""
echo "================================================================"
echo "  Configuration complete!"
echo "  "
echo "  To start the bug fixer:"
echo "    agy plugin enable bug-fixer-loop"
echo "  "
echo "  To stop:"
echo "    agy plugin disable bug-fixer-loop"
echo "  "
echo "  To reconfigure:"
echo "    ./scripts/configure.sh"
echo "================================================================"
