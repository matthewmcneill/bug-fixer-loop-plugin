---
name: bug-fixer-manage
description: >
  Manage the bug-fixer daemon: enroll/unenroll repos, check loop status, view
  logs, update config, reset stuck state, sync labels. Use when the user
  mentions enrolling, unenrolling, managing, configuring, checking status of,
  troubleshooting, or resetting the bug-fixer system.
---

# Bug-Fixer Management Skill

## MANAGE_SCRIPT

```
~/.gemini/config/plugins/bug-fixer-daemon/scripts/manage.py
```

All commands below use: `python3 <MANAGE_SCRIPT> <subcommand> [args]`

---

## Read-Only Commands (run directly, report output)

| Command | Example |
|---------|---------|
| **status** | `python3 <MANAGE_SCRIPT> status [--repo owner/repo]` |
| **logs** | `python3 <MANAGE_SCRIPT> logs triage [--repo owner/repo] [-n 50]` |
| **config** | `python3 <MANAGE_SCRIPT> config [--repo owner/repo]` |

---

## Mutable Commands — Deterministic HITL via `PreToolUse` (`force_ask`)

> **IMPORTANT**: Mutable `manage.py` operations (`enroll`, `unenroll`, `config --set`, `reset`, `labels`, `refresh-sidecars`) are gated deterministically by `pre_tool_gate.py`.
> - In **interactive sessions**, `pre_tool_gate.py` returns `{"decision": "force_ask", ...}`, which triggers the native Antigravity tool-execution confirmation prompt on the `run_command` call itself.
> - In **autonomous sessions** (sidecar / dispatched agents), `pre_tool_gate.py` returns `{"decision": "deny", ...}` — autonomous loops cannot execute management mutations.
> - **Do NOT call `ask_question` simply to confirm a `manage.py` command** before running `run_command` (doing so creates a redundant double-confirmation prompt). Only use `ask_question` if a required parameter is genuinely missing or ambiguous (e.g., multiple enrolled repos when the user didn't specify which one).
> - Security-critical configuration keys (`protected_files`, `spec_file_path`, `repo_dir`, `target_repo`, `maintainer`, `trusted_users`, `scanning`, `leak_patterns`, `scan_exclude_paths`) are blocked from `config --set` and must be edited directly in `config.json` by the maintainer.

### Pattern

1. **Gather** all required values (read env vars, inspect repo — read-only)
2. **Clarify** via `ask_question` *only* if the user's intent or target parameter is ambiguous
3. **Execute** the standalone `python3 ~/.gemini/config/plugins/bug-fixer-daemon/scripts/manage.py <subcommand> ...` command directly — the `PreToolUse` hook will present the native `force_ask` confirmation to the user

### Mutable command reference

| Command | Args | Notes |
|---------|------|-------|
| **enroll** | `--repo SLUG --dir PATH --project-id ID` | Must run from target workspace |
| **unenroll** | `--repo SLUG` | Checks for active claims |
| **config --set** | `--set KEY=VALUE --repo SLUG` | Operational keys only (allowlisted) |
| **reset** | `--cooldowns / --locks / --dispatches / --all [--repo SLUG]` | Clears stuck state |
| **labels --sync** | `--repo SLUG` | Creates missing GitHub labels |

---

## Enrollment Procedure

Enroll **must** be run from within the **target workspace** because
`ANTIGRAVITY_PROJECT_ID` is workspace-specific.

### Step 1 — Gather values (read-only)

Run these commands to collect the required values:

```bash
# Project ID (automatically set in this agent session)
echo $ANTIGRAVITY_PROJECT_ID

# Repo slug (from git remote)
git remote get-url origin
# Parse owner/repo from the URL (strip .git suffix, extract last two path segments)

# Repo directory (current workspace root)
git rev-parse --show-toplevel
```

### Step 2 — Execute (native `force_ask` confirmation)

Run the standalone enrollment command (the `PreToolUse` gate will prompt the user to approve execution):

```bash
python3 ~/.gemini/config/plugins/bug-fixer-daemon/scripts/manage.py enroll \
  --repo <owner/repo> \
  --dir <repo-dir> \
  --project-id <project-id>
```

### Step 3 — Post-enrollment guidance

After successful enrollment, tell the user:
1. Edit the config file (path shown in output) to set `maintainer`, `test_command`,
   `protected_files`, and `spec_file_path`
2. Consider running `labels --sync` if this is a fresh repository
3. The sidecar will start ticking within 5 minutes
4. Use `status` to verify loops are running

---

## Other Mutable Operations

### Config update
1. Read current config: `python3 <MANAGE_SCRIPT> config --repo owner/repo`
2. Run update (native `force_ask` prompt confirms execution): `python3 <MANAGE_SCRIPT> config --set engineering_interval_min=15 --repo owner/repo`

### Reset
1. Run `status` to identify the problem (cooldowns, stale locks, stuck dispatches)
2. Run reset (native `force_ask` prompt confirms execution): `python3 <MANAGE_SCRIPT> reset --cooldowns --repo owner/repo`

### Unenroll
1. Run unenroll (native `force_ask` prompt confirms execution): `python3 <MANAGE_SCRIPT> unenroll --repo owner/repo`

### Labels
1. Run label sync (native `force_ask` prompt confirms execution): `python3 <MANAGE_SCRIPT> labels --sync --repo owner/repo`

---

## Troubleshooting

If loops are not running:
1. Run `status` — check for ❌ or missing Project ID
2. Run `logs <loop>` — check last error messages
3. Common fixes:
   - **ANTIGRAVITY_PROJECT_ID missing**: Re-enroll from the target workspace
   - **Cooldown active**: Reset cooldowns
   - **Stale locks**: Reset locks
   - **agentapi unreachable**: Antigravity IDE must be running
