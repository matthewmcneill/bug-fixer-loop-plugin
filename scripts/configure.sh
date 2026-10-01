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
TRIAGE_ONLY=false

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
  --triage-only                 Skip configuration, just run triage preview
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
    --triage-only)
      TRIAGE_ONLY=true
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

# --triage-only: Load existing config and jump straight to triage preview
if [[ "${TRIAGE_ONLY}" == "true" ]]; then
  if [[ ! -f "${CONFIG_FILE}" ]]; then
    echo "Error: ${CONFIG_FILE} not found. Run ./scripts/configure.sh first." >&2
    exit 1
  fi
  TARGET_REPO=$(python3 -c "import json; print(json.load(open('${CONFIG_FILE}')).get('target_repo',''))")
  if [[ -z "${TARGET_REPO}" ]]; then
    echo "Error: target_repo not set in config. Run ./scripts/configure.sh first." >&2
    exit 1
  fi
  SPEC_FILE_PATH=$(python3 -c "import json; print(json.load(open('${CONFIG_FILE}')).get('spec_file_path','docs/design_specification.md'))")
  echo "================================================================"
  echo "  Bug Fixer Loop — Triage Preview (${TARGET_REPO})"
  echo "================================================================"
  # Fall through to Step 8 triage code below via a trick:
  # We set a marker and skip to the triage section
  ASSUME_YES=false
  # Jump straight to triage — the Step 8 code is at the bottom of this script
fi

if [[ "${TRIAGE_ONLY}" == "false" ]]; then
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

# Ensure discovered-in-review label exists on target repo (for tech debt discovery chain)
gh label create "discovered-in-review" \
  --repo "${TARGET_REPO}" \
  --color "d4c5f9" \
  --description "Auto-filed during PR review, requires human approval before auto-fix" \
  2>/dev/null || true

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

# Default model tiers
if "triage_model" not in config:
    config["triage_model"] = "flash"
if "engineering_model" not in config:
    config["engineering_model"] = config.get("tick_model", "pro")
if "architect_model" not in config:
    config["architect_model"] = "pro"
if "trajectory_model" not in config:
    config["trajectory_model"] = "flash"

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

fi  # end of TRIAGE_ONLY == false block

# Step 8: Dry-Run Triage — Classify all open issues for review
echo ""
echo "================================================================"
echo "  Step 8: Issue Triage Preview (Dry Run)"
echo "================================================================"
echo ""

DRY_RUN_SKIP=false
if [[ "${ASSUME_YES}" == "true" ]]; then
  echo "  Skipping interactive triage preview in non-interactive mode."
  echo "  Run './scripts/configure.sh --triage-only' later to preview."
  DRY_RUN_SKIP=true
fi

if [[ "${DRY_RUN_SKIP}" == "false" ]]; then
  echo "  Fetching open issues from ${TARGET_REPO}..."
  echo ""

  # Fetch open issues via gh (safe_ingest may not be configured yet for first run)
  ISSUES_JSON=$(gh issue list --repo "${TARGET_REPO}" --state open --limit 100 \
    --json number,title,labels,milestone,body 2>/dev/null || echo "[]")

  ISSUE_COUNT=$(echo "${ISSUES_JSON}" | python3 -c "import sys,json; print(len(json.load(sys.stdin)))" 2>/dev/null || echo "0")

  if [[ "${ISSUE_COUNT}" == "0" ]]; then
    echo "  No open issues found. Triage preview skipped."
  else
    echo "  Found ${ISSUE_COUNT} open issue(s). Classifying..."
    echo ""

    # Use Python to classify each issue into the 5 tiers
    python3 - "${ISSUES_JSON}" "${TARGET_REPO}" "${SPEC_FILE_PATH}" <<'CLASSIFY_EOF'
import sys
import json

issues = json.loads(sys.argv[1])
target_repo = sys.argv[2]
spec_file = sys.argv[3]

exclusion_labels = {"human-required", "hardware-required", "P1-blocker"}
hardware_keywords = {"pin", "gpio", "sensor", "antenna", "soldering", "oscilloscope", "multimeter"}
auto_labels = {"auto-fixable", "needs-approach-review", "input-needed", "project-candidate", "human-required"}

# Effort labels — if present, use them as strong signals
small_effort_labels = {"S", "S-effort", "P3-docs-dx", "P4-enhancement"}
large_effort_labels = {"L", "L-effort", "XL", "XL-effort"}

# Signals that distinguish "mentions a topic" from "changes that system"
t4_multi_module_signals = [
    "cross-cutting", "ci pipeline", "ci/cd", "rewrite from scratch",
    "breaking change", "api redesign", "new module", "new framework",
]
t4_weak_signals = ["refactor", "migration", "architecture", "framework"]

# T2 signals — must indicate actual behaviour change, not just documentation
t2_behaviour_signals = [
    "race condition", "use-after-free", "uaf", "toctou", "deadlock",
    "boot loop", "kernel panic", "stack overflow", "heap corruption",
]
t2_weak_signals = ["security", "auth", "encryption", "certificate", "critical",
                    "boot", "initializ"]

# Small-scope indicators that should push toward T1 even if keywords match
small_scope_signals = [
    "rename", "typo", "comment", "document", "docstring", "doxygen",
    "log message", "label", "placeholder", "link", "url", "badge",
    "move", "reorder", "swap", "css", "style", "format", "indent",
]

results = []

for issue in issues:
    num = issue.get("number", "?")
    title = issue.get("title", "")
    body = issue.get("body", "") or ""
    labels = {l.get("name", "") for l in issue.get("labels", [])}
    milestone = issue.get("milestone")
    ms_title = milestone.get("title", "") if milestone else ""

    # Skip already-classified issues
    if labels & auto_labels:
        existing = (labels & auto_labels).pop()
        results.append({
            "number": num, "title": title, "tier": f"(already: {existing})",
            "reason": "Previously classified", "milestone": ms_title
        })
        continue

    text = (title + " " + body).lower()
    has_small_effort = bool(labels & small_effort_labels)
    has_large_effort = bool(labels & large_effort_labels)
    is_small_scope = any(sig in text for sig in small_scope_signals)
    body_len = len(body.strip())

    # Count how many distinct files/modules are mentioned
    import re
    file_mentions = len(set(re.findall(r'\b\w+\.(cpp|h|py|js|css|html|ini|json|md)\b', text)))
    module_mentions = len(set(re.findall(r'\b(?:module|manager|service|handler)\b', text)))

    # T5: exclusion labels (highest priority)
    if labels & exclusion_labels:
        tier = "T5 human-required"
        reason = f"Has exclusion label: {labels & exclusion_labels}"
    # T5: hardware-dependent (strict keywords only, not "roaming" which is a feature)
    elif any(kw in text for kw in hardware_keywords) and not is_small_scope:
        tier = "T5 human-required"
        reason = "Hardware-dependent (mentions physical components)"
    # T3: ambiguous / no clear criteria
    elif body_len < 30:
        tier = "T3 input-needed"
        reason = "Issue body too short / lacks clear acceptance criteria"
    # Small effort label overrides keyword escalation
    elif has_small_effort and is_small_scope:
        tier = "T1 auto-fixable"
        reason = "Small effort + small scope indicators"
    # T4: requires STRONG multi-module signals (not just keyword presence)
    elif any(kw in text for kw in t4_multi_module_signals):
        tier = "T4 project-candidate"
        reason = "Large scope / architectural work"
    elif any(kw in text for kw in t4_weak_signals) and not is_small_scope and (file_mentions > 3 or has_large_effort):
        tier = "T4 project-candidate"
        reason = f"Weak T4 signal + multi-file scope ({file_mentions} files mentioned)"
    # T2: must indicate actual behaviour change in sensitive area
    elif any(kw in text for kw in t2_behaviour_signals):
        tier = "T2 needs-approach-review"
        reason = "Behaviour change in sensitive code path"
    elif any(kw in text for kw in t2_weak_signals) and not is_small_scope and not has_small_effort:
        tier = "T2 needs-approach-review"
        reason = "Touches sensitive area — agent should confirm approach first"
    # T1: everything else (including weak-signal matches that are small scope)
    else:
        tier = "T1 auto-fixable"
        reason = "Clear scope, single module, testable"

    results.append({
        "number": num, "title": title[:60], "tier": tier,
        "reason": reason, "milestone": ms_title
    })

# Print table
print(f"  {'#':<6} {'Tier':<28} {'Milestone':<15} {'Title':<60}")
print(f"  {'─'*6} {'─'*28} {'─'*15} {'─'*60}")
for r in sorted(results, key=lambda x: x["tier"]):
    ms = r["milestone"] or "—"
    print(f"  {r['number']:<6} {r['tier']:<28} {ms:<15} {r['title']:<60}")

print()

# Summary counts
from collections import Counter
tier_counts = Counter(r["tier"] for r in results)
print("  Summary:")
for tier, count in sorted(tier_counts.items()):
    print(f"    {tier}: {count} issue(s)")
print()
print("  ℹ  This is a heuristic preview. For LLM-quality classification,")
print('     run "/bug-fixer" inside Antigravity after enabling the plugin.')

# Write results to a review file
review_path = sys.argv[2].replace("/", "_") + "_triage_preview.json"
with open(review_path, "w") as f:
    json.dump(results, f, indent=2)

CLASSIFY_EOF

    echo ""
    echo "  ─────────────────────────────────────────────────────────────"
    echo ""
    echo "  Review the classifications above."
    echo "  The triage preview has been saved for reference."
    echo ""

    if [[ "${ASSUME_YES}" == "false" ]]; then
      read -rp "  Apply these labels to GitHub issues now? [y/N]: " APPLY_LABELS
      if [[ "${APPLY_LABELS}" =~ ^[Yy]$ ]]; then
        echo ""
        echo "  Applying labels..."

        # Re-run classification and apply labels
        python3 - "${ISSUES_JSON}" "${TARGET_REPO}" <<'APPLY_EOF'
import sys
import json
import subprocess

issues = json.loads(sys.argv[1])
target_repo = sys.argv[2]

exclusion_labels = {"human-required", "hardware-required", "P1-blocker"}
hardware_keywords = {"pin", "gpio", "sensor", "antenna", "soldering", "oscilloscope", "multimeter"}
auto_labels = {"auto-fixable", "needs-approach-review", "input-needed", "project-candidate", "human-required"}
small_effort_labels = {"S", "S-effort", "P3-docs-dx", "P4-enhancement"}
large_effort_labels = {"L", "L-effort", "XL", "XL-effort"}
t4_multi_module_signals = ["cross-cutting", "ci pipeline", "ci/cd", "rewrite from scratch",
                           "breaking change", "api redesign", "new module", "new framework"]
t4_weak_signals = ["refactor", "migration", "architecture", "framework"]
t2_behaviour_signals = ["race condition", "use-after-free", "uaf", "toctou", "deadlock",
                        "boot loop", "kernel panic", "stack overflow", "heap corruption"]
t2_weak_signals = ["security", "auth", "encryption", "certificate", "critical", "boot", "initializ"]
small_scope_signals = ["rename", "typo", "comment", "document", "docstring", "doxygen",
                       "log message", "label", "placeholder", "link", "url", "badge",
                       "move", "reorder", "swap", "css", "style", "format", "indent"]

for issue in issues:
    num = issue.get("number", "")
    title = issue.get("title", "")
    body = issue.get("body", "") or ""
    labels = {l.get("name", "") for l in issue.get("labels", [])}

    if labels & auto_labels:
        continue  # already classified

    text = (title + " " + body).lower()
    has_small_effort = bool(labels & small_effort_labels)
    has_large_effort = bool(labels & large_effort_labels)
    is_small_scope = any(sig in text for sig in small_scope_signals)
    body_len = len(body.strip())

    import re
    file_mentions = len(set(re.findall(r'\b\w+\.(cpp|h|py|js|css|html|ini|json|md)\b', text)))

    if labels & exclusion_labels:
        label = "human-required"
    elif any(kw in text for kw in hardware_keywords) and not is_small_scope:
        label = "human-required"
    elif body_len < 30:
        label = "input-needed"
    elif has_small_effort and is_small_scope:
        label = "auto-fixable"
    elif any(kw in text for kw in t4_multi_module_signals):
        label = "project-candidate"
    elif any(kw in text for kw in t4_weak_signals) and not is_small_scope and (file_mentions > 3 or has_large_effort):
        label = "project-candidate"
    elif any(kw in text for kw in t2_behaviour_signals):
        label = "needs-approach-review"
    elif any(kw in text for kw in t2_weak_signals) and not is_small_scope and not has_small_effort:
        label = "needs-approach-review"
    else:
        label = "auto-fixable"

    subprocess.run(
        ["gh", "issue", "edit", str(num), "--repo", target_repo, "--add-label", label],
        capture_output=True, text=True
    )
    print(f"    #{num}: applied '{label}'")

APPLY_EOF

        echo ""
        echo "  ✓ Labels applied. You can review and adjust on GitHub before enabling."
      else
        echo ""
        echo "  Labels NOT applied. You can review the preview and apply them manually,"
        echo "  or let the triage sidecar classify issues automatically when enabled."
      fi
    fi
  fi
fi

# Step 9: Print Summary and Next Steps
if [[ "${TRIAGE_ONLY}" == "false" ]]; then
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
echo "  "
echo "  To re-run triage preview without reconfiguring:"
echo "    ./scripts/configure.sh --triage-only"
echo "================================================================"
fi

