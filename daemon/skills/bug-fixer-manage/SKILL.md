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

## Mutable Commands — MANDATORY HITL PATTERN

> **CRITICAL**: For ALL mutable operations, you MUST follow this exact pattern.
> Never skip the confirmation step.

### Pattern

1. **Gather** all required values (read env vars, inspect repo — read-only)
2. **Present** the exact command and all key values to the user via `ask_question`
3. **Wait** for the user to confirm
4. **Execute** the command only after confirmation

### Mutable command reference

| Command | Args | Notes |
|---------|------|-------|
| **enroll** | `--repo SLUG --dir PATH --project-id ID` | Must run from target workspace |
| **unenroll** | `--repo SLUG` | Checks for active claims |
| **config --set** | `--set KEY=VALUE --repo SLUG` | Type-inferred |
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

### Step 2 — Confirm with user

Call `ask_question` with a single question presenting:
- **Repository**: `owner/repo`
- **Directory**: `/absolute/path/to/repo`
- **Project ID**: `xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx`

Options: "Confirm enrollment" / "Cancel"

### Step 3 — Execute (only after confirmation)

```bash
python3 ~/.gemini/config/plugins/bug-fixer-daemon/scripts/manage.py enroll \
  --repo <owner/repo> \
  --dir <repo-dir> \
  --project-id <project-id>
```

### Step 4 — Post-enrollment guidance

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
2. Present proposed change via `ask_question`: "Set `engineering_interval_min` to 15?"
3. On confirm: `python3 <MANAGE_SCRIPT> config --set engineering_interval_min=15 --repo owner/repo`

### Reset
1. Run `status` to identify the problem (cooldowns, stale locks, stuck dispatches)
2. Present via `ask_question`: "Clear cooldowns for owner/repo?"
3. On confirm: `python3 <MANAGE_SCRIPT> reset --cooldowns --repo owner/repo`

### Unenroll
1. Present via `ask_question`: "Unenroll owner/repo from bug-fixer? This removes all sidecar data."
2. On confirm: `python3 <MANAGE_SCRIPT> unenroll --repo owner/repo`

### Labels
1. Present via `ask_question`: "Sync required labels to owner/repo?"
2. On confirm: `python3 <MANAGE_SCRIPT> labels --sync --repo owner/repo`

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
