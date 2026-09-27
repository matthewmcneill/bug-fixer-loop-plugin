---
name: bug-fixer
description: >-
  The bug fixer agent — show status, triage summary, pending actions, and
  where human input is needed. Triggered by "bug fixer", "bug fixer status",
  "what's the bug fixer doing", "show me the triage", "what needs my attention",
  "morning briefing", "fix bugs", or "run bug fixer". Also covers filing issues
  against the plugin itself, reporting plugin bugs, contributing improvements,
  proposing new capabilities, or understanding the contribution process.
  Triggered by "file a plugin issue", "report a bug-fixer bug", "bug-fixer
  defect", "contribute to the plugin", "improve the bug fixer", "plugin
  enhancement", "propose a feature", "submit a fix", "contribution protocol",
  "how do I file an issue", "plugin issue template".
---

# Bug Fixer Status Dashboard

This skill provides a human-friendly summary of the bug-fixer-loop plugin's
current state. Run it when you sit down at the bench to see what's happened,
what's pending, and where your input is needed.

## Detailed References

This skill uses a progressive disclosure model. Based on your query, load and
read the corresponding reference file:

| Topic | Read |
|---|---|
| **Filing and triaging plugin issues** | `references/issue-management.md` |
| **Contributing changes or proposing new capabilities** | `references/contribution-protocol.md` |

## Workflow

### Step 0: Load Configuration
This skill is part of the `bug-fixer-loop` plugin. Resolve two directories:

**Plugin directory** (scripts, rules — global/shared):
```bash
PLUGIN_DIR="$(cd "$(dirname "<this_skill_file>")/../.." && pwd)"
```

**State directory** (repo-specific config — in the workspace):
```bash
STATE_DIR="<workspace_root>/.agents/plugins/bug-fixer-loop-workspace"
```

Read `${STATE_DIR}/config.json`. Extract `target_repo`, `maintainer`,
`max_concurrent_prs`, `active_milestone`. If the file is missing or `target_repo`
is empty, output `CONFIG_INCOMPLETE: Plugin not configured. Run ${PLUGIN_DIR}/scripts/configure.sh first.` and exit.

Use `${PLUGIN_DIR}` for scripts and `${STATE_DIR}` for config and local state.

### Step 1: Issues Awaiting Your Input

These are the issues where the bug fixer is blocked on you.

**T2 — Approach Reviews (needs your `approved`):**
```bash
python3 <plugin_dir>/scripts/safe_ingest.py issue-list --repo <target_repo> --label needs-approach-review --state open --json number,title,labels,milestone,comments
```
For each issue, find the `[BUG-FIXER] Proposed approach` comment and summarise:
- Issue number and title
- The proposed approach (1-2 line summary)
- How long it's been waiting

**T3 — Clarification Requests (needs your answer):**
```bash
python3 <plugin_dir>/scripts/safe_ingest.py issue-list --repo <target_repo> --label input-needed --state open --json number,title,labels,comments
```
For each, show the question the agent asked.

**Format as a table:**
```
🔔 ACTION REQUIRED — <count> issue(s) need your input:

  #     Type                  Waiting    Title
  ───── ───────────────────── ────────── ──────────────────────────────
  #241  Approach Review       2h         sample-app: _locationConfigured gate...
  #183  Approach Review       1d         [Audit F9] setInsecure() in OTA...
```

If nothing needs input, output: `✅ No items need your attention right now.`

### Step 2: Open Agent PRs

Query PRs authored by the agent:
```bash
gh pr list --repo <target_repo> --author @me --state open --json number,title,headRefName,labels,createdAt,reviews,comments
```

For each, report:
- PR number, title, branch
- Status: `Waiting for review` / `Changes requested` / `Approved`
- Age (time since created)
- Whether there's unaddressed review feedback

**Format:**
```
📋 Open PRs — <count> agent PR(s):

  #     Status              Age    Title
  ───── ─────────────────── ────── ──────────────────────────────
  #42   Waiting for review  3h     [BUG-FIXER] Fix #234: Move Bluetooth...
```

### Step 3: Triage Summary

Show the current issue classification breakdown:
```bash
python3 <plugin_dir>/scripts/safe_ingest.py issue-list --repo <target_repo> --state open --json number,title,labels,milestone
```

Count issues by tier label and output:
```
📊 Issue Triage:

  T1 auto-fixable:          6 issue(s) — agent will fix these
  T2 needs-approach-review: 4 issue(s) — waiting for your approval
  T3 input-needed:          0 issue(s) — waiting for clarification
  T4 project-candidate:    13 issue(s) — too large, use /principal-architect
  T5 human-required:       10 issue(s) — agent skips these
  Unclassified:             0 issue(s) — next triage will classify
```

### Step 4: Recent Activity

Scan recent sidecar transcripts in `~/.gemini/antigravity/brain/` for bug-fixer
conversations from the last 24 hours. Summarise:
- Issues triaged (count)
- Issues fixed (PRs opened)
- PRs merged
- Test failures encountered
- Any `human-required` escalations

**Format:**
```
📈 Last 24 Hours:

  Issues triaged:    3
  PRs opened:        1 (#42 — Fix #234)
  PRs merged:        0
  Test failures:     0
  Escalations:       0
```

If no recent activity, output: `No bug-fixer activity in the last 24 hours.`

### Step 5: Milestone Progress

If `active_milestone` is set or any milestone has issues:
```bash
gh api repos/<target_repo>/milestones --jq '.[] | {title, due_on, open_issues, closed_issues, state}'
```

Show progress for active milestones:
```
🎯 Milestones:

  next-beta:  3 open / 12 closed  (due: 2026-10-15)  — 80% complete
  backlog:   18 open / 45 closed  (no due date)
```

### Step 6: Health Check

Report any concerns:
- ⚠️ Stale claims: issues with `status:in-progress` but no PR opened in >4 hours
- ⚠️ Unaddressed feedback: agent PRs with review comments older than 24 hours
- ⚠️ Repeated failures: same issue attempted multiple times
- ⚠️ Plugin disabled: if the plugin is currently disabled, remind the user

If all healthy: `✅ Bug fixer is healthy. No concerns.`

### Output Format

Combine all sections into a single dashboard:

```
═══════════════════════════════════════════════════════════════
  Bug Fixer Loop — Status Dashboard
  Repository: matthewmcneill/esp32-app-foundation
  Last updated: 2026-09-26T10:30:00Z
═══════════════════════════════════════════════════════════════

[Step 1 output — Action Required]

[Step 2 output — Open PRs]

[Step 3 output — Triage Summary]

[Step 4 output — Recent Activity]

[Step 5 output — Milestone Progress]

[Step 6 output — Health Check]

═══════════════════════════════════════════════════════════════
  Tip: Reply 'approved' on any T2 issue to unlock it for fixing.
  Run 'bug fixer status' anytime for this dashboard.
═══════════════════════════════════════════════════════════════
```

## Trigger Reference

> The `description` frontmatter is intentionally lean — it is loaded into every
> session and is what selects this skill. This section is the exhaustive
> vocabulary catalogue; it is read only *after* the skill is invoked, so it
> carries no per-session cost and is never truncated. Use it to confirm scope
> and to recognise the full range of phrasings this skill covers.

- **Status / dashboard**: "bug fixer", "bug fixer status", "what's the bug
  fixer doing", "show me the triage", "what needs my attention", "morning
  briefing", "fix bugs", "run bug fixer", "what PRs are open", "stale claims",
  "health check", "milestone progress".

- **Issue management**: "file a plugin issue", "report a bug-fixer bug",
  "bug-fixer defect", "plugin bug", "raise a plugin issue", "file a GitHub
  issue against the plugin", "misclassification", "false positive",
  "safe_ingest bug", "pre_tool_gate bug", "triage bug", "tick crash",
  "orphaned claim", "sidecar didn't fire", "issue template",
  "P1-blocker", "P2-correctness", "P3-docs-dx", "found-in:", "backfill",
  "component:triage", "component:tick", "component:safe-ingest".
  → Read `references/issue-management.md` for the full process.

- **Contributing & proposals**: "contribute to the plugin", "improve the bug
  fixer", "submit a fix", "open a pull request", "propose a feature", "plugin
  enhancement", "suggest an improvement", "new skill", "new sidecar",
  "contribution protocol", "how do I contribute", "PR mechanics",
  "branch naming", "test requirements", "co-update duty", "protected files",
  "skill co-update".
  → Read `references/contribution-protocol.md` for the full process.
