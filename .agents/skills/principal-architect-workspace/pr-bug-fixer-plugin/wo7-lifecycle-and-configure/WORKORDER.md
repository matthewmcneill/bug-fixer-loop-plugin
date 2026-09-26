---
complexity: standard
parent_project: bug-fixer-plugin
work_order: 7
topic: lifecycle-and-configure
branch: bug-fixer-plugin/wo7-lifecycle-and-configure
base: bug-fixer-plugin/wo6-tests-and-docs
---

# WO7: Lifecycle, Configuration Wizard & Skill Guards

## Role
You are an expert senior software engineer implementing the install → configure → start lifecycle for the bug-fixer-loop plugin.

## Objective
1. Ship the plugin disabled by default (`plugin.json`)
2. Create a `scripts/configure.sh` interactive wizard that validates config, tests connectivity, creates labels, and sets up the worktree
3. Add config guards to the triage and tick skills so they exit cleanly if config is incomplete
4. Update README.md with the new lifecycle

## Strict Boundary Box

**MODIFY:**
- `/Users/mcneillm/Documents/Projects/bug-fixer-loop-plugin/plugin.json` — add `"disabled": true`
- `/Users/mcneillm/Documents/Projects/bug-fixer-loop-plugin/skills/bug-fixer-triage/SKILL.md` — add Step 0 config guard
- `/Users/mcneillm/Documents/Projects/bug-fixer-loop-plugin/skills/bug-fixer-tick/SKILL.md` — add Step 0 config guard
- `/Users/mcneillm/Documents/Projects/bug-fixer-loop-plugin/README.md` — add lifecycle section, update installation instructions
- `/Users/mcneillm/Documents/Projects/bug-fixer-loop-plugin/config.json` — add `"worktree_path"` field

**CREATE:**
- `/Users/mcneillm/Documents/Projects/bug-fixer-loop-plugin/scripts/configure.sh`

## Line-Item Instructions

### 1. `plugin.json` — Ship Disabled
Add `"disabled": true` so the plugin is off after installation until explicitly enabled:
```json
{
  "name": "bug-fixer-loop",
  "version": "0.1.0",
  "disabled": true,
  "description": "Autonomous bug fixer loop — a 3-loop background agent that triages, fixes, and PRs small bugs and TODOs while you focus on major feature work."
}
```

### 2. `config.json` — Add New Fields
Add these fields to the config schema:
```json
{
  "worktree_path": "",
  "required_labels": [
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
}
```

### 3. `scripts/configure.sh` — Interactive Wizard

Create a bash script (`set -euo pipefail`) that walks the user through configuration:

```
================================================================
  Bug Fixer Loop — Configuration Wizard
================================================================
```

**Step 1: Gather Settings**
- Prompt for `target_repo` (or accept from `--repo` flag): `Enter target repository (owner/repo):`
- Prompt for `repo_dir` (or accept from `--repo-dir` flag): `Enter local checkout path:` (default: auto-detect from `gh repo view <repo> --json path` or `~/projects/<repo_name>`)
- Prompt for `maintainer` (or accept from `--maintainer` flag): `Enter maintainer GitHub login:` (default: `gh api user -q .login`)
- Prompt for `spec_file_path`: `Enter spec file path:` (default: `docs/design_specification.md`)
- Prompt for `test_command`: `Enter test command:` (default: `pio test -e native`)
- Prompt for `lint_command` (optional): `Enter lint command (blank to skip):`

**Step 2: Validate Connectivity**
- Check `gh auth status` — fail with clear error if not authenticated
- Check repo access: `gh repo view <target_repo>` — fail if no access
- Check repo_dir exists — warn if not, offer to clone

**Step 3: Validate Spec File**
- Check `<repo_dir>/<spec_file_path>` exists — warn if not found

**Step 4: Test the Test Command**
- Run `<test_command>` in `<repo_dir>` — report pass/fail but don't block (tests might need hardware)

**Step 5: Create Required Labels**
- For each label in `required_labels`, run `gh label create "<label>" --repo <target_repo> --force --description "<description>"` (idempotent)
- Use appropriate colours: `auto-fixable` → green, `human-required` → red, `needs-approach-review` → yellow, `project-candidate` → purple, etc.

**Step 6: Set Up Worktree** (optional but recommended)
- Ask: `Create isolated git worktree for agent operation? (recommended) [Y/n]:`
- If yes: `git -C <repo_dir> worktree add <worktree_path> main` where `worktree_path` defaults to `<repo_dir>-bug-fixer-worktree`
- Write `worktree_path` to config

**Step 7: Write Config**
- Update `config.json` with all gathered values
- Print summary of what was configured

**Step 8: Print Next Steps**
```
================================================================
  Configuration complete!
  
  To start the bug fixer:
    agy plugin enable bug-fixer-loop
  
  To stop:
    agy plugin disable bug-fixer-loop
  
  To reconfigure:
    ./scripts/configure.sh
================================================================
```

Accept flags for non-interactive use: `--repo`, `--repo-dir`, `--maintainer`, `--spec-file`, `--test-command`, `--lint-command`, `--no-worktree`, `-y` (accept defaults).

### 4. Skill Config Guards

**In `bug-fixer-triage/SKILL.md`**, insert as the FIRST step (before current Step 1):

```markdown
## Step 0: Configuration Guard
Read `config.json` from the plugin directory. If `target_repo` is empty or the file is missing, output `CONFIG_INCOMPLETE: Plugin not configured. Run scripts/configure.sh first.` and exit immediately. Do not proceed with any GitHub operations.
```

**In `bug-fixer-tick/SKILL.md`**, insert as the FIRST step (before current Step 1):

```markdown
## Step 0: Configuration Guard
Read `config.json` from the plugin directory. If `target_repo` is empty or the file is missing, output `CONFIG_INCOMPLETE: Plugin not configured. Run scripts/configure.sh first.` and exit immediately. Do not proceed with any GitHub operations.
```

### 5. README.md Updates

Add a **Getting Started** lifecycle section after the Overview:

```markdown
## Getting Started

### 1. Install
```bash
./scripts/install.sh
```
The plugin ships **disabled** by default. No sidecars will run until you configure and enable it.

### 2. Configure
```bash
./scripts/configure.sh --repo <owner/repo> --repo-dir <path>
```
The wizard validates connectivity, creates labels, and optionally sets up an isolated worktree.

### 3. Enable
```bash
agy plugin enable bug-fixer-loop
```
The 3 background sidecars will now run on their configured schedules.

### 4. Pause / Resume
```bash
agy plugin disable bug-fixer-loop   # pause
agy plugin enable bug-fixer-loop    # resume
```
```

Also update the Installation section to reference `configure.sh`.

## Verification
1. `plugin.json` contains `"disabled": true`
2. `scripts/configure.sh --help` runs without error
3. Both skills contain "Configuration Guard" as Step 0
4. README.md contains "Getting Started" lifecycle section
5. `config.json` contains `worktree_path` and `required_labels` fields

## Completion Report Template
```markdown
## Completion Report — WO7: Lifecycle & Configuration

**Status:** PASS | FAIL
**Branch:** bug-fixer-plugin/wo7-lifecycle-and-configure

### Files Modified
- [ ] plugin.json (disabled: true)
- [ ] config.json (new fields)
- [ ] skills/bug-fixer-triage/SKILL.md (config guard)
- [ ] skills/bug-fixer-tick/SKILL.md (config guard)
- [ ] README.md (lifecycle section)

### Files Created
- [ ] scripts/configure.sh

### Verification
- plugin.json disabled: PASS/FAIL
- configure.sh --help: PASS/FAIL
- Config guards present: PASS/FAIL
- README lifecycle: PASS/FAIL

### Decisions Made
(list any)
```
