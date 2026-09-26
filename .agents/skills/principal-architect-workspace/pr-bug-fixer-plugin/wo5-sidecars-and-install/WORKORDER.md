---
complexity: standard
parent_project: bug-fixer-plugin
work_order: 5
topic: sidecars-and-install
branch: bug-fixer-plugin/wo5-sidecars-and-install
base: bug-fixer-plugin/wo4-skills
---

# WO5: Sidecars & Install Script

## Role
You are an expert senior software engineer creating the background scheduling layer and deployment automation for the bug-fixer-loop plugin.

## Objective
Create 3 Antigravity sidecar configurations (JSON files that schedule the skills as background cron loops) and an `install.sh` / `uninstall.sh` pair that deploys the entire plugin to a user's Antigravity configuration.

## Strict Boundary Box

**CREATE:**
- `/Users/mcneillm/Documents/Projects/bug-fixer-loop-plugin/sidecars/bug-fixer-triage/sidecar.json`
- `/Users/mcneillm/Documents/Projects/bug-fixer-loop-plugin/sidecars/bug-fixer-tick/sidecar.json`
- `/Users/mcneillm/Documents/Projects/bug-fixer-loop-plugin/sidecars/trajectory-review/sidecar.json`
- `/Users/mcneillm/Documents/Projects/bug-fixer-loop-plugin/scripts/install.sh`
- `/Users/mcneillm/Documents/Projects/bug-fixer-loop-plugin/scripts/uninstall.sh`

**READ (reference — do NOT modify):**
- `/Users/mcneillm/Documents/Projects/loop-eng/sidecars/engineering-loop/sidecar.json` — reference sidecar format
- `/Users/mcneillm/Documents/Projects/loop-eng/sidecars/issue-review-loop/sidecar.json` — reference
- `/Users/mcneillm/Documents/Projects/loop-eng/sidecars/trajectory-review/sidecar.json` — reference
- `/Users/mcneillm/Documents/Projects/loop-eng/scripts/install.sh` — reference install script
- `/Users/mcneillm/Documents/Projects/bug-fixer-loop-plugin/config.json` — config schema

## Line-Item Instructions

### 1. `sidecars/bug-fixer-triage/sidecar.json`

Schedules the triage skill hourly. Use the Antigravity sidecar format from the reference repo. Key fields:

```json
{
  "builtin": "schedule",
  "args": [
    "<triage_cron>",
    "Run the bug-fixer-triage skill for repository <owner/repo>. Read the skill at <plugin_dir>/skills/bug-fixer-triage/SKILL.md. Use <plugin_dir>/scripts/safe_ingest.py for all GitHub reads. Configuration is at <plugin_dir>/config.json."
  ],
  "restart_policy": "always",
  "display_name": "Bug Fixer Triage (<owner/repo>)",
  "description": "Hourly triage of open GitHub issues — classifies as auto-fixable or human-required.",
  "env": {},
  "agent_permissions": {
    "workspace_uris": ["file://<repo_dir>"],
    "access_grants": ["command(*)"]
  }
}
```

Use `<owner/repo>`, `<repo_dir>`, `<plugin_dir>`, and `<maintainer>` as template placeholders — the install script will substitute them.

### 2. `sidecars/bug-fixer-tick/sidecar.json`

Schedules the tick (fix) skill every 2 hours. Same format. The prompt must instruct the agent to:
- Read the bug-fixer-tick SKILL.md
- Use safe_ingest.py for GitHub reads
- Use config.json for all parameters
- Run in the repo directory as its workspace

Use cron `<tick_cron>` as placeholder.

### 3. `sidecars/trajectory-review/sidecar.json`

Schedules the trajectory review daily at 18:00. Same format. Use `<trajectory_review_cron>` placeholder.

### 4. `scripts/install.sh`

Create a deployment script that provisions the plugin for a specific repo. Adapt from the loop-eng `install.sh` but simplified for 3 loops (not 7). The script must:

**Flags:**
- `-r, --repo <owner/repo>` — target GitHub repository (required)
- `-d, --repo-dir <path>` — local checkout path (defaults to `~/projects/<repo_name>`)
- `-m, --maintainer <login>` — trusted maintainer (defaults to `gh api user -q .login`)
- `--skills-only` — install skills/agents/scripts without sidecars
- `-h, --help` — usage

**Steps:**
1. **Install security scripts** — copy `scripts/safe_ingest.py` and `scripts/pre_tool_gate.py` to `~/.gemini/config/scripts/` with `chmod 555`
2. **Install skills** — copy `skills/*/` to `~/.gemini/config/skills/` and `~/.gemini/antigravity/skills/`, running `sed` to substitute `<owner/repo>`, `<repo_dir>`, `<maintainer>`, `<plugin_dir>` placeholders
3. **Install agents** — copy `agents/` to `~/.gemini/config/agents/` and `~/.gemini/antigravity/agents/`, same `sed` substitution
4. **Install hooks** — copy `hooks.json` to the appropriate location, substituting plugin dir paths
5. **Install rules** — copy `rules/AGENTS.md` to `~/.gemini/config/rules/` (or the target repo's `.agents/rules/`)
6. **Configure sidecars** (unless `--skills-only`) — copy `sidecars/*/sidecar.json` to `~/.gemini/config/sidecars/`, `~/.gemini/antigravity/sidecars/`, and `~/.gemini/jetski/sidecars/`, substituting all placeholders including cron schedules from `config.json`
7. **Update config.json** — write `target_repo`, `repo_dir`, `maintainer` into the plugin's `config.json`

**Important macOS compatibility:**
- Use `sed -i '' -e` (BSD sed), not `sed -i -e` (GNU sed)
- Use `set -euo pipefail`
- Detect plugin root via `SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"`

**Output:** Print a summary of installed components and locations.

### 5. `scripts/uninstall.sh`

Create a cleanup script that:
- Removes deployed skills, agents, scripts, and sidecars
- Removes hooks.json entries
- Does NOT delete `config.json` (preserve user settings)
- Prints what was removed

Same flags: `--repo <owner/repo>` to identify which deployment to remove.

## Verification
1. All 5 files exist
2. Each `sidecar.json` is valid JSON: `python3 -c "import json; json.load(open('...'))"` for each
3. `install.sh` runs with `--help` without error
4. `install.sh` contains all template placeholders (`<owner/repo>`, `<repo_dir>`, etc.)
5. `uninstall.sh` runs with `--help` without error
6. No hardcoded repo-specific values (no `matthewmcneill`, no `esp32-app-foundation`, no `cloud-gtm`)

## Completion Report — WO5: Sidecars & Install Script

**Status:** PASS
**Branch:** bug-fixer-plugin/wo5-sidecars-and-install

### Files Created
- [x] sidecars/bug-fixer-triage/sidecar.json
- [x] sidecars/bug-fixer-tick/sidecar.json
- [x] sidecars/trajectory-review/sidecar.json
- [x] scripts/install.sh
- [x] scripts/uninstall.sh

### Verification
- All sidecar JSON valid: PASS
- install.sh --help: PASS
- uninstall.sh --help: PASS
- No hardcoded repo values: PASS

### Decisions Made
- Used Antigravity builtin schedule JSON schema for sidecars with 2-element args array `[<cron>, <prompt>]`, configured with template placeholders for runtime deployment.
- Hardened security scripts with permissions `0555` and used atomic python JSON mutations for `config.json` updates and hook merging in `install.sh`.
- Added symlink-safe copying logic in `install.sh` when `~/.gemini/antigravity/skills` or `agents` symlink to `~/.gemini/config/`.
- Designed `uninstall.sh` to remove only the plugin's entries from `hooks.json` while safely preserving any other user-defined hooks and keeping `config.json` intact.

### Out-of-Scope Findings
- WO6 can add test coverage for `install.sh` and `uninstall.sh` simulating multi-environment directory layouts and flag permutations.

