---
complexity: architect
parent_project: bug-fixer-plugin
work_order: 8
topic: milestones-and-routing
branch: bug-fixer-plugin/wo8-milestones-and-routing
base: bug-fixer-plugin/wo7-lifecycle-and-configure
---

# WO8: Milestone Awareness & 5-Tier Complexity Routing

## Role
You are an expert senior prompt engineer rewriting the triage skill to support milestone-aware prioritisation and a 5-tier issue classification system, replacing the binary auto-fixable/human-required model.

## Objective
1. Rewrite `bug-fixer-triage/SKILL.md` with 5-tier complexity routing and milestone awareness
2. Update `config.json` with milestone-related configuration fields
3. Update `bug-fixer-triage/EVAL.md` with new scenarios covering milestones and all 5 tiers

## Strict Boundary Box

**MODIFY:**
- `/Users/mcneillm/Documents/Projects/bug-fixer-loop-plugin/skills/bug-fixer-triage/SKILL.md` — rewrite classification system
- `/Users/mcneillm/Documents/Projects/bug-fixer-loop-plugin/skills/bug-fixer-triage/EVAL.md` — update scenarios
- `/Users/mcneillm/Documents/Projects/bug-fixer-loop-plugin/config.json` — add milestone config fields

**READ (do NOT modify):**
- `/Users/mcneillm/Documents/Projects/bug-fixer-loop-plugin/rules/AGENTS.md` — safety invariants
- `/Users/mcneillm/Documents/Projects/bug-fixer-loop-plugin/skills/bug-fixer-tick/SKILL.md` — understand what tick expects

## Line-Item Instructions

### 1. 5-Tier Classification System

Replace the binary `auto-fixable` / `human-required` classification with 5 tiers. The triage skill must classify each unclassified issue into exactly one:

| Tier | Label | Criteria | What Happens |
|:---|:---|:---|:---|
| **T1: Auto-fixable** | `auto-fixable` | Single module, clear acceptance criteria, testable with host tests, no architectural decisions, estimated <200 lines changed, no spec conflicts | Agent fixes autonomously, opens PR |
| **T2: Needs Approach Review** | `needs-approach-review` | Fixable by agent but non-trivial: multiple files in same module, ambiguous acceptance criteria, or touches sensitive code. Agent can reason about the fix but should confirm approach first | Agent posts `[BUG-FIXER] Proposed approach:` comment on the issue describing its plan. Waits for maintainer `approved` reply. On next triage cycle, if approved → reclassify as `auto-fixable` |
| **T3: Input Needed** | `input-needed` | Bug report is ambiguous, lacks repro steps, or has conflicting information. Agent cannot determine what the correct fix is | Agent posts `[BUG-FIXER] Clarification needed:` comment asking specific questions. Skips until human responds with clarification |
| **T4: Project Candidate** | `project-candidate` | Too large for a single PR: cross-module, requires new architecture, needs design decisions, estimated >500 lines, or is a multi-step feature | Agent posts `[BUG-FIXER] This issue is too large for autonomous fixing. Consider decomposing with /principal-architect.` Skips entirely |
| **T5: Human Required** | `human-required` | Hardware-dependent, requires physical testing, has exclusion labels, needs human judgement on tradeoffs, security-sensitive | Agent skips entirely |

**Classification logic in the skill (in order of checks):**
1. Has exclusion label? → T5
2. Overlaps with maintainer's active PR files? → T5 (with comment citing the overlapping PR)
3. Is hardware-dependent (mentions pins, sensors, physical devices, WiFi roaming)? → T5
4. Is ambiguous / lacks clear acceptance criteria? → T3
5. Is cross-module or estimated >500 lines? → T4
6. Is single-module but touches sensitive code or >200 lines? → T2
7. Otherwise → T1

### 2. Milestone Awareness

Add milestone-aware prioritisation to the triage skill:

**New config fields** (add to `config.json`):
```json
{
  "milestone_priority_boost": true,
  "active_milestone": ""
}
```

- `milestone_priority_boost`: When true, issues in milestones with due dates get priority over unassigned issues
- `active_milestone`: If set, issues in this specific milestone are prioritised above all others. If empty, auto-detect the nearest-due milestone.

**Milestone logic in triage:**

After classification, if the issue is T1 or T2:
1. Query milestones: `gh api repos/<target_repo>/milestones --jq '.[] | {number, title, due_on, open_issues, state}' `
2. If the issue belongs to a milestone:
   - Note it in the classification summary: `Milestone: <name> (due: <date>)`
   - If `milestone_priority_boost` is true, issues in the active milestone get sorted before non-milestone issues in the tick's issue selection
3. If the issue has no milestone but clearly relates to an open milestone's theme, suggest the milestone in the classification comment

**Priority ordering for tick (document this in triage output):**
1. Issues in `active_milestone` (by priority label, then issue number)
2. Issues in other milestones with due dates (by due date ascending, then priority)
3. Issues without milestones (by priority label, then issue number)

### 3. Approach-Review Lifecycle in Triage

When an issue is classified as T2 (`needs-approach-review`):

1. The agent reads the issue body, the spec file, and relevant code
2. Posts a comment:
   ```
   [BUG-FIXER] Proposed approach for #<number>:
   
   **Problem:** <1-2 sentence summary>
   
   **Proposed fix:**
   - <specific change 1>
   - <specific change 2>
   
   **Files to modify:**
   - `<path/to/file1>`
   - `<path/to/file2>`
   
   **Risk assessment:** <low/medium/high>
   
   Reply with `approved` to proceed, or provide alternative direction.
   ```
3. Applies `needs-approach-review` label
4. On subsequent triage cycles: check if the maintainer has replied with an approval comment (same `is_approval_comment` logic as safe_ingest.py). If approved:
   - Remove `needs-approach-review` label
   - Add `auto-fixable` label
   - The tick skill will pick it up on the next cycle

### 4. Updated EVAL.md

Rewrite `skills/bug-fixer-triage/EVAL.md` with scenarios covering all 5 tiers plus milestones:

```markdown
# Eval: bug-fixer-triage

## Scenario 1: T1 — Auto-fixable small bug
**Input:** Issue "#201 test_otaUpdateManager in test_ignore", single module, clear criteria.
**Expected:** Classified as `auto-fixable` (T1).

## Scenario 2: T2 — Needs approach review
**Input:** Issue "#226 OTA checkOnBoot guard", single module but touches initialisation sequence.
**Expected:** Classified as `needs-approach-review` (T2), approach comment posted.

## Scenario 3: T2 → T1 — Approved approach
**Input:** Issue with `needs-approach-review` label, maintainer replied "approved".
**Expected:** Reclassified to `auto-fixable`, ready for tick.

## Scenario 4: T3 — Input needed
**Input:** Issue with vague description, no repro steps.
**Expected:** Classified as `input-needed` (T3), clarification comment posted.

## Scenario 5: T4 — Project candidate
**Input:** Issue "#144 CI Pipeline for GitHub Actions", cross-cutting, 500+ lines estimated.
**Expected:** Classified as `project-candidate` (T4), /principal-architect comment posted.

## Scenario 6: T5 — Human required (exclusion label)
**Input:** Issue with `hardware-required` label.
**Expected:** Classified as `human-required` (T5), skipped.

## Scenario 7: T5 — Human required (PR overlap)
**Input:** Issue touching `modules/BLEManager/`, maintainer PR active on same module.
**Expected:** Classified as `human-required` (T5), comment citing overlapping PR.

## Scenario 8: Milestone prioritisation
**Input:** Two T1 issues: #201 (no milestone) and #226 (Milestone "v1.1", due next week).
**Expected:** #226 appears first in priority ordering.

## Scenario 9: Active milestone focus
**Input:** `active_milestone` set to "v1.1", issues in "v1.1" and "v2.0".
**Expected:** Only "v1.1" issues get priority boost, "v2.0" issues ordered normally.

## Scenario 10: Empty backlog
**Input:** No unclassified issues.
**Expected:** `TRIAGE_COMPLETE: No new issues to classify`, clean exit.
```

## Verification
1. Triage SKILL.md contains all 5 tiers with labels and criteria
2. Triage SKILL.md contains milestone query and priority ordering logic
3. Triage SKILL.md contains approach-review comment template and re-check logic
4. config.json contains `milestone_priority_boost` and `active_milestone` fields
5. EVAL.md has 10 scenarios covering all 5 tiers and milestones
6. No references to the old binary classification (no "auto-fixable or human-required" as the only two options)

## Completion Report — WO8: Milestones & 5-Tier Routing

**Status:** PASS
**Branch:** bug-fixer-plugin/wo8-milestones-and-routing

### Files Modified
- [x] skills/bug-fixer-triage/SKILL.md (5-tier + milestones)
- [x] skills/bug-fixer-triage/EVAL.md (10 scenarios)
- [x] config.json (milestone fields)

### Verification
- 5 tiers present: PASS
- Milestone logic present: PASS
- Approach-review template: PASS
- 10 eval scenarios: PASS

### Decisions Made
- Replaced the binary logic with the 5 tier logic directly in the SKILL.md file, adding milestone prioritization correctly.
