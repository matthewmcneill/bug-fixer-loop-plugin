---
complexity: architect
parent_project: bug-fixer-plugin
work_order: 4
topic: skills
branch: bug-fixer-plugin/wo4-skills
base: bug-fixer-plugin/wo3-pretool-hooks
---

# WO4: Skills & Leak Auditor Agent

## Role
You are an expert senior software engineer and prompt engineer creating the intelligence layer of the bug-fixer-loop Antigravity plugin — 4 skills and 1 auditor agent.

## Objective
Create 4 Antigravity skills (SKILL.md files with YAML frontmatter) and 1 auditor agent (Markdown subagent definition) that together form the bug-fixer's autonomous loop: triage issues → fix & PR → audit test quality → review trajectory.

## Strict Boundary Box

**CREATE:**
- `/Users/mcneillm/Documents/Projects/bug-fixer-loop-plugin/skills/bug-fixer-triage/SKILL.md`
- `/Users/mcneillm/Documents/Projects/bug-fixer-loop-plugin/skills/bug-fixer-tick/SKILL.md`
- `/Users/mcneillm/Documents/Projects/bug-fixer-loop-plugin/skills/test-quality-audit/SKILL.md`
- `/Users/mcneillm/Documents/Projects/bug-fixer-loop-plugin/skills/trajectory-review/SKILL.md`
- `/Users/mcneillm/Documents/Projects/bug-fixer-loop-plugin/agents/leak-auditor.md`

**READ (reference — do NOT modify):**
- `/Users/mcneillm/Documents/Projects/loop-eng/skills/tick/SKILL.md` — core execution loop to adapt
- `/Users/mcneillm/Documents/Projects/loop-eng/skills/issue-review-loop/SKILL.md` — triage logic to adapt
- `/Users/mcneillm/Documents/Projects/loop-eng/skills/test-quality-loop/SKILL.md` — anti-reward-hacking rubric
- `/Users/mcneillm/Documents/Projects/loop-eng/skills/trajectory-review/SKILL.md` — daily review
- `/Users/mcneillm/Documents/Projects/loop-eng/agents/github-leak-auditor.md` — leak scanner agent
- `/Users/mcneillm/Documents/Projects/bug-fixer-loop-plugin/config.json` — config schema
- `/Users/mcneillm/Documents/Projects/bug-fixer-loop-plugin/rules/AGENTS.md` — safety rules

**DO NOT** modify any existing files.

## Context: Antigravity SKILL.md Format

Every SKILL.md must have YAML frontmatter with at minimum:
```yaml
---
name: skill-name
description: >-
  When to trigger this skill. Be specific and include key phrases.
---
```

The body is Markdown instructions that the agent reads when the skill is activated.

## Line-Item Instructions

### 1. `skills/bug-fixer-triage/SKILL.md`

**Purpose:** Classify open issues as `auto-fixable` or `human-required` and enforce the active-PR exclusion zone.

**Frontmatter:**
```yaml
---
name: bug-fixer-triage
description: >-
  Triage GitHub issues for the autonomous bug fixer. Classifies issues as
  auto-fixable or human-required. Triggered by the triage sidecar on a
  scheduled cron, or manually with "triage bug-fixer issues".
---
```

**Workflow (write these as numbered steps in the skill body):**

1. **Load Configuration:** Read `config.json` from the plugin directory. Extract `target_repo`, `maintainer`, `exclusion_labels`, `auto_fixable_labels`, `spec_file_path`.

2. **Query Open Issues:** Run `python3 <plugin_dir>/scripts/safe_ingest.py issue-list --repo <target_repo> --state open --json number,title,labels,body,milestone,author,assignees,comments` to get all open issues through the security proxy.

3. **Query Maintainer's Active PRs:** Run `gh pr list --repo <target_repo> --author <maintainer> --state open --json number,title,headRefName,files` to get the maintainer's open PRs and their changed files. Build an **exclusion file set** — the union of all files touched by the maintainer's active PRs.

4. **For each unclassified issue** (no `auto-fixable` or `human-required` label):

   a. **Skip if exclusion label present** — if the issue has any label in `exclusion_labels`, apply `human-required` label and move on.

   b. **Estimate fix scope** — from the issue title, body, and labels, determine which files/modules would likely need modification. Use the repo's module structure and design spec to inform this.

   c. **Check exclusion zone** — if any estimated fix files overlap with the exclusion file set, apply `human-required` label with a comment: `[BUG-FIXER] Skipped: overlaps with active PR #<number> (<branch>). Will re-evaluate when PR merges.`

   d. **Assess complexity** — classify based on:
      - **Auto-fixable** if: single module, clear acceptance criteria, testable with host tests, no architectural decisions, estimated < 200 lines changed
      - **Human-required** if: cross-cutting, architectural judgment needed, hardware required, no clear acceptance criteria, touches core kernel, estimated > 200 lines

   e. **Apply label** — `gh label create auto-fixable --repo <target_repo> --force` (idempotent), then `gh issue edit <number> --repo <target_repo> --add-label auto-fixable` or `--add-label human-required`.

5. **Fast Exit Conditions:** If no open issues exist, or all issues are already classified, output `TRIAGE_COMPLETE: No new issues to classify` and exit cleanly.

6. **Summary Report:** Output a summary table of classifications made this run.

### 2. `skills/bug-fixer-tick/SKILL.md`

**Purpose:** The core execution engine — pick an issue, fix it, test it, PR it.

**Frontmatter:**
```yaml
---
name: bug-fixer-tick
description: >-
  Execute one cycle of the autonomous bug fixer loop. Picks the highest-priority
  auto-fixable issue, implements the fix, runs tests, and opens a PR. Triggered
  by the tick sidecar every 2 hours, or manually with "fix bugs" or "run bug fixer".
---
```

**Workflow:**

1. **Load Configuration:** Read `config.json`. Extract all fields.

2. **Concurrency Guard:** Run `gh pr list --repo <target_repo> --author @me --state open --json number,title,labels`. Count PRs with `pr_prefix` in the title. If count >= `max_concurrent_prs`, output `TICK_EXIT: PR cap reached (<count>/<max>). Waiting for merge.` and exit.

3. **Check for PR Feedback (Highest Priority):** For each open agent PR:
   - Query comments and reviews via `safe_ingest.py pr-comments <number>`
   - Look for unaddressed maintainer comments (comments from `maintainer` or `trusted_users` without a subsequent `[BUG-FIXER]` reply)
   - If found: check out the PR branch, implement the requested changes, run tests, push, reply with `[BUG-FIXER] Addressed review feedback in <sha>`, and exit this tick

4. **Select Issue:** Run `safe_ingest.py issue-list --repo <target_repo> --label auto-fixable --state open --json number,title,labels,body,priority,priorityRank`. Pick the first issue by priority rank (ascending), then by issue number (ascending, FIFO).

5. **Claim Issue:** Run `gh issue edit <number> --repo <target_repo> --add-label status:in-progress`. This acts as a lease — other agents (if any) see the claim.

6. **Branch:** `git checkout -b <branch_prefix>/issue-<number>-<slug> origin/main` where `<slug>` is a kebab-case version of the issue title (max 40 chars).

7. **Understand the Issue:** Read the issue body and any `[ISSUE-REVIEW]` execution contract comments. Read the project's spec file (`spec_file_path`) for relevant sections. Identify the exact files to modify.

8. **Implement the Fix:**
   - Make the minimal code changes to resolve the issue
   - Follow the project's coding style (check existing files in the same module)
   - Add or update tests to cover the fix
   - Ensure changes are within the module boundary — do NOT make cross-cutting changes

9. **Verify:**
   - Run `<test_command>` and capture full output
   - Run `<lint_command>` if configured
   - Run `python3 <plugin_dir>/scripts/pre_tool_gate.py --scan-only .` for leak check
   - All three must pass. If any fails, debug and retry up to 2 times. If still failing after retries, unclaim the issue (`--remove-label status:in-progress`), add label `human-required`, comment `[BUG-FIXER] Attempted fix but tests/lint failed after 3 attempts. Needs human attention.`, and exit.

10. **Self-Review Against Spec:** Read `spec_file_path` sections relevant to the modified modules. Verify the fix doesn't contradict any specification or ADR. If it does, unclaim, label `spec-change-request,input-needed`, comment explaining the conflict, and exit.

11. **Open PR:**
    - `git add -A && git commit -m "<pr_prefix> Fix #<number>: <title>"`
    - `git push -u origin <branch>`
    - Create PR with `gh pr create --repo <target_repo> --base main --title "<pr_prefix> Fix #<number>: <title>" --body "<body>"`
    - PR body must include: issue link, what was changed, why, full test output, leak scan result
    - Remove `status:in-progress` label from the issue

12. **Fast Exit:** If Step 4 finds no eligible issues, output `TICK_EXIT: NO_ELIGIBLE_WORK` and exit cleanly. Never invent work, create issues, or expand scope.

### 3. `skills/test-quality-audit/SKILL.md`

**Purpose:** Anti-reward-hacking rubric ensuring agent-written tests are genuine.

**Frontmatter:**
```yaml
---
name: test-quality-audit
description: >-
  Audit test quality for bug-fixer PRs using a 6-point anti-reward-hacking
  rubric. Called inline by bug-fixer-tick before opening a PR, or manually
  with "audit test quality".
---
```

**6-Point Rubric (adapt from loop-eng's `test-quality-loop`):**

Read `/Users/mcneillm/Documents/Projects/loop-eng/skills/test-quality-loop/SKILL.md` for the original rubric. Adapt it to be self-contained in this skill. The rubric checks:

1. **Assertion Density:** Every test function must have at least one meaningful assertion. Fail if any test body lacks `ASSERT_*`, `EXPECT_*`, `assertEqual`, `assertTrue`, or equivalent.

2. **Input Diversity:** Tests must cover at least 2 distinct input scenarios (happy path + edge case). Fail if all tests use identical inputs.

3. **Negative Testing:** At least one test must verify error handling / rejection / boundary violation. Fail if all tests only test success paths.

4. **No Tautological Assertions:** Assertions must not compare a value to itself (`EXPECT_EQ(x, x)`), assert literal `true`, or test that a mock returns what it was configured to return without exercising real logic.

5. **Scope Alignment:** Tests must exercise the code that was actually changed in the PR, not unrelated modules. Fail if new tests only cover untouched code.

6. **No Test Suppression:** The PR must not add entries to `test_ignore`, skip lists, or `#if 0` blocks around existing tests. Existing test coverage must not decrease.

**Output:** A pass/fail for each point with explanations. Overall PASS requires all 6 points to pass.

### 4. `skills/trajectory-review/SKILL.md`

**Purpose:** Daily review of the bug-fixer's performance to catch recurring failure patterns.

**Frontmatter:**
```yaml
---
name: trajectory-review
description: >-
  Daily review of the bug-fixer loop's trajectory — identifies recurring
  failure patterns, stale claims, and quality trends. Triggered by the
  trajectory-review sidecar daily at 18:00, or manually with
  "review bug fixer trajectory".
---
```

**Workflow (adapt from loop-eng's trajectory-review):**

Read `/Users/mcneillm/Documents/Projects/loop-eng/skills/trajectory-review/SKILL.md` for the original. Adapt to:

1. **Scan recent sidecar transcripts** in `~/.gemini/antigravity/brain/` for conversations matching `bug-fixer-triage` and `bug-fixer-tick` display names from the last 24 hours.

2. **Identify failure patterns:**
   - Repeated test failures on the same module → suggest the module needs human attention
   - Issues claimed (`status:in-progress`) but no PR opened within 4 hours → stale claim alert
   - PR feedback not addressed within 24 hours → escalation needed
   - Same issue attempted multiple times → add `human-required` label

3. **Quality trends:**
   - Track: issues triaged, PRs opened, PRs merged, PRs rejected, test pass rate
   - Compare to previous day if history exists

4. **Output a diagnostic report** as a structured summary. If critical patterns detected (>3 consecutive failures, stale claims), create a GitHub issue with label `bug-fixer-health` describing the pattern.

### 5. `agents/leak-auditor.md`

**Purpose:** Pre-commit leak scanner subagent invoked by `bug-fixer-tick` before opening a PR.

Read `/Users/mcneillm/Documents/Projects/loop-eng/agents/github-leak-auditor.md` for the original. Adapt to:

- Remove the Google-internal 4-point checklist (`go/`, `b/`, `cl/`, `*.corp.google.com`, `citc`, `piper`, `@google.com`)
- Use `pre_tool_gate.py --scan-only .` for deterministic scanning
- Use configurable leak patterns from `config.json`
- Retain the dual-key principle (deterministic scan + semantic review)
- Retain the receipt stamp/erasure workflow (`--stamp-receipt` / `--verify-receipt`)
- The agent file should define the subagent with YAML frontmatter:
  ```yaml
  ---
  name: leak-auditor
  type: subagent
  description: Pre-commit leak scanner with deterministic and semantic review.
  ---
  ```

## Verification
1. All 5 files exist at their specified paths
2. Each SKILL.md has valid YAML frontmatter with `name` and `description`
3. `bug-fixer-triage/SKILL.md` references `safe_ingest.py` (not raw `gh`)
4. `bug-fixer-tick/SKILL.md` references `safe_ingest.py`, `pre_tool_gate.py --scan-only`, `config.json`, and includes the concurrency guard, test verification, and PR feedback remediation steps
5. `test-quality-audit/SKILL.md` contains all 6 rubric points
6. `trajectory-review/SKILL.md` references sidecar transcript scanning
7. `agents/leak-auditor.md` references `pre_tool_gate.py --scan-only` and `config.json`
8. No files contain `cloud-gtm`, `sparkforge`, `custom-ge-demo`, `@google.com`

## Completion Report Template
```markdown
## Completion Report — WO4: Skills & Leak Auditor Agent

**Status:** PASS | FAIL
**Branch:** bug-fixer-plugin/wo4-skills

### Files Created
- [ ] skills/bug-fixer-triage/SKILL.md
- [ ] skills/bug-fixer-tick/SKILL.md
- [ ] skills/test-quality-audit/SKILL.md
- [ ] skills/trajectory-review/SKILL.md
- [ ] agents/leak-auditor.md

### Line Counts
- bug-fixer-triage: <count>
- bug-fixer-tick: <count>
- test-quality-audit: <count>
- trajectory-review: <count>
- leak-auditor: <count>

### Key Design Decisions
(list any decisions made during prompt engineering)

### Verification
- All files exist: PASS/FAIL
- Valid YAML frontmatter: PASS/FAIL
- safe_ingest.py referenced (not raw gh): PASS/FAIL
- 6-point rubric complete: PASS/FAIL
- No Google terms: PASS/FAIL

### Out-of-Scope Findings
(surface anything for later WOs)
```

## Completion Report — WO4: Skills & Leak Auditor Agent

**Status:** PASS
**Branch:** bug-fixer-plugin/wo4-skills

### Files Created
- [x] skills/bug-fixer-triage/SKILL.md
- [x] skills/bug-fixer-tick/SKILL.md
- [x] skills/test-quality-audit/SKILL.md
- [x] skills/trajectory-review/SKILL.md
- [x] agents/leak-auditor.md

### Line Counts
- bug-fixer-triage: 46
- bug-fixer-tick: 68
- test-quality-audit: 34
- trajectory-review: 36
- leak-auditor: 35

### Key Design Decisions
- Adapted the reference skills into a simplified 3-loop architecture, completely removing `cloud-gtm`, `sparkforge`, and internal terms.
- Refactored `safe_ingest.py` calls to be fully parameterised by `config.json`.
- Enforced the `pre_tool_gate.py` dual-key security model for both leak auditing and testing.

### Verification
- All files exist: PASS
- Valid YAML frontmatter: PASS
- safe_ingest.py referenced (not raw gh): PASS
- 6-point rubric complete: PASS
- No Google terms: PASS

### Out-of-Scope Findings
- None surfaced at this time; ready for Sidecars & Install (WO5).
