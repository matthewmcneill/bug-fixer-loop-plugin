---
complexity: standard
parent_project: bug-fixer-plugin
work_order: 9
topic: tick-enhancements
branch: bug-fixer-plugin/wo9-tick-enhancements
base: bug-fixer-plugin/wo8-milestones-and-routing
---

# WO9: Tick Skill Enhancements — 5-Tier Support, Milestones, Labels

## Role
You are an expert senior prompt engineer updating the tick (fix) skill to work with the new 5-tier classification system and milestone awareness.

## Objective
1. Update `bug-fixer-tick/SKILL.md` to handle T1 and T2 issues, milestone tagging, `bug-fixer` label, and approach-review prerequisites
2. Update `bug-fixer-tick/EVAL.md` with new scenarios
3. Update `rules/AGENTS.md` with new label-related rules

## Strict Boundary Box

**MODIFY:**
- `/Users/mcneillm/Documents/Projects/bug-fixer-loop-plugin/skills/bug-fixer-tick/SKILL.md` — update for 5-tier, milestones, labels
- `/Users/mcneillm/Documents/Projects/bug-fixer-loop-plugin/skills/bug-fixer-tick/EVAL.md` — update scenarios
- `/Users/mcneillm/Documents/Projects/bug-fixer-loop-plugin/rules/AGENTS.md` — add label rules

**READ (do NOT modify):**
- `/Users/mcneillm/Documents/Projects/bug-fixer-loop-plugin/skills/bug-fixer-triage/SKILL.md` — understand the 5-tier system (may have been updated by WO8 already, or read the WO8 WORKORDER for the design)
- `/Users/mcneillm/Documents/Projects/bug-fixer-loop-plugin/.agents/skills/principal-architect-workspace/pr-bug-fixer-plugin/wo8-milestones-and-routing/WORKORDER.md` — the 5-tier design specification

## Line-Item Instructions

### 1. Tick Skill — Issue Selection Updates

**Replace the current Step 4 (Select Issue)** with a more sophisticated selection that respects:

a. **Only T1 issues are eligible for immediate fixing.** The tick skill MUST NOT pick up `needs-approach-review`, `input-needed`, `project-candidate`, or `human-required` issues. Only `auto-fixable` issues are eligible.

b. **Milestone-aware priority ordering:**
   1. First: `auto-fixable` issues in the `active_milestone` (from config), sorted by priority label then issue number
   2. Then: `auto-fixable` issues in other milestones with due dates, sorted by due date ascending
   3. Finally: `auto-fixable` issues with no milestone, sorted by priority label then issue number

c. **Query milestones** when selecting: `gh api repos/<target_repo>/milestones --jq '.[] | {number, title, due_on}'` and cross-reference with the issue's milestone field.

### 2. Tick Skill — Approach-Review Prerequisite Check

Insert a new check between the current "Check for PR Feedback" (Step 3) and "Select Issue" (Step 4):

```markdown
### Step 3.5: Check for Approved Approaches
Query issues with the `needs-approach-review` label:
```bash
python3 <plugin_dir>/scripts/safe_ingest.py issue-list --repo <target_repo> --label needs-approach-review --state open --json number,title,labels,comments
```
For each issue, check if the maintainer has posted an approval comment (contains `approved` or `/approve` from a trusted user). If found:
- Remove `needs-approach-review` label
- Add `auto-fixable` label
- Log: `[BUG-FIXER] Approach approved for #<number>, now eligible for fixing.`

This ensures previously-reviewed issues enter the auto-fixable queue.
```

### 3. Tick Skill — PR Enhancements

Update **Step 11 (Open PR)** with:

a. **Apply `bug-fixer` label to the PR:**
   ```bash
   gh pr edit <pr_number> --repo <target_repo> --add-label bug-fixer
   ```

b. **Carry over milestone:** If the resolved issue belongs to a milestone, set the same milestone on the PR:
   ```bash
   gh pr edit <pr_number> --repo <target_repo> --milestone "<milestone_title>"
   ```

c. **PR body enhancements** — the PR body must now also include:
   - Issue tier: `**Classification:** T1 (auto-fixable)` or `T2 (approach approved by @maintainer)`
   - Milestone: `**Milestone:** <name>` if applicable
   - The `bug-fixer` label must be listed

### 4. Rules — New Label Rules

Add to `rules/AGENTS.md`:

```markdown
## 13. Bug-Fixer Label
You MUST apply the `bug-fixer` label to every PR you create. This distinguishes
agent-authored PRs from human PRs.

## 14. Milestone Inheritance
If the resolved issue belongs to a milestone, you MUST set the same milestone
on the PR.

## 15. Tier Respect
You MUST only fix issues labelled `auto-fixable` (T1). You MUST NOT fix issues
labelled `needs-approach-review`, `input-needed`, `project-candidate`, or
`human-required` unless they have been explicitly reclassified to `auto-fixable`
by the triage skill.
```

### 5. Updated EVAL.md

Rewrite `skills/bug-fixer-tick/EVAL.md` with scenarios covering the new features:

```markdown
# Eval: bug-fixer-tick

## Scenario 1: Fix T1 issue end-to-end
**Input:** Open issue "#201 test_otaUpdateManager" with `auto-fixable` label, P2 priority.
**Expected:** Agent branches, fixes, tests pass, PR opened with `bug-fixer` label and test output.

## Scenario 2: PR cap reached
**Input:** 1 open agent PR, `max_concurrent_prs` = 1.
**Expected:** `TICK_EXIT: PR cap reached`, clean exit.

## Scenario 3: Remediate PR feedback
**Input:** Open agent PR with unaddressed maintainer comment.
**Expected:** Agent checks out PR branch, implements changes, pushes, replies.

## Scenario 4: Test failure escalation
**Input:** Fix implemented but tests fail 3 times.
**Expected:** Issue unclaimed, `human-required` label added, failure comment posted.

## Scenario 5: Skip non-T1 issue
**Input:** Top-priority issue has `needs-approach-review` label, next has `auto-fixable`.
**Expected:** Agent skips T2, picks T1 issue.

## Scenario 6: Milestone priority
**Input:** Two T1 issues: #201 (no milestone) and #226 (milestone "v1.1" due next week).
**Expected:** Agent picks #226 first due to milestone priority.

## Scenario 7: Approve approach → fix
**Input:** Issue with `needs-approach-review` label, maintainer replied "approved".
**Expected:** Agent reclassifies to `auto-fixable`, then picks it up and fixes it.

## Scenario 8: Spec conflict detection
**Input:** Fix contradicts design_specification.md ADR.
**Expected:** Unclaim, label `spec-change-request,input-needed`, comment.

## Scenario 9: No eligible work
**Input:** No `auto-fixable` issues.
**Expected:** `TICK_EXIT: NO_ELIGIBLE_WORK`, clean exit.

## Scenario 10: PR carries milestone and label
**Input:** Issue #226 in milestone "v1.1" fixed.
**Expected:** PR has `bug-fixer` label AND milestone "v1.1" set.

## Scenario 11: Leak detection blocks PR
**Input:** Fix contains string matching `ya29.` leak pattern.
**Expected:** Leak caught, cleaned, re-scanned, PR opened clean.
```

## Verification
1. Tick SKILL.md filters only `auto-fixable` issues (not other tiers)
2. Tick SKILL.md contains milestone-aware priority ordering (3-level sort)
3. Tick SKILL.md contains Step 3.5 (approach-review check)
4. Tick SKILL.md applies `bug-fixer` label and milestone to PRs
5. `rules/AGENTS.md` contains rules 13, 14, 15
6. EVAL.md has 11 scenarios

## Completion Report Template
```markdown
## Completion Report — WO9: Tick Enhancements

**Status:** PASS | FAIL
**Branch:** bug-fixer-plugin/wo9-tick-enhancements

### Files Modified
- [ ] skills/bug-fixer-tick/SKILL.md
- [ ] skills/bug-fixer-tick/EVAL.md
- [ ] rules/AGENTS.md

### Verification
- T1-only filtering: PASS/FAIL
- Milestone ordering: PASS/FAIL
- Step 3.5 approach check: PASS/FAIL
- bug-fixer label on PR: PASS/FAIL
- 3 new rules: PASS/FAIL
- 11 eval scenarios: PASS/FAIL

### Decisions Made
(list any)
```
