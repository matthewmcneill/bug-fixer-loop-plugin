---
name: bug-fixer-triage
description: >-
  Triage GitHub issues for the autonomous bug fixer. Classifies issues using a
  5-tier complexity routing system and handles milestone-aware prioritisation.
  Triggered by the triage sidecar on a scheduled cron, or manually with "triage bug-fixer issues".
---

# Bug Fixer Triage

This skill classifies open issues into 5 tiers of complexity, handles approach reviews for non-trivial bugs, enforces active-PR exclusion zones, and manages milestone-aware prioritisation.

## Workflow

## Step 0: Configuration Guard
This skill is part of the `bug-fixer-loop` plugin. Resolve:
```bash
PLUGIN_DIR="$(cd "$(dirname "<this_skill_file>")/../.." && pwd)"
STATE_DIR="<workspace_root>/.agents/plugins/bug-fixer-loop-workspace"
```
Read `${STATE_DIR}/config.json`. If `target_repo` is empty or the file is missing, output `CONFIG_INCOMPLETE: Plugin not configured. Run ${PLUGIN_DIR}/scripts/configure.sh first.` and exit immediately.

1. **Load Configuration:** Read `${STATE_DIR}/config.json`. Extract `target_repo`, `maintainer`, `exclusion_labels`, `milestone_priority_boost`, and `active_milestone`.

2. **Query Open Issues:** Run the safe ingest proxy to list all open issues:
   ```bash
   python3 ${PLUGIN_DIR}/scripts/safe_ingest.py issue-list --repo <target_repo> --state open --json number,title,labels,body,milestone,author,assignees,comments
   ```

3. **Query Maintainer's Active PRs:** Find the maintainer's open PRs and their changed files to build the **exclusion file set**:
   ```bash
   gh pr list --repo <target_repo> --author <maintainer> --state open --json number,title,headRefName,files
   ```
   The exclusion file set is the union of all files touched by these active PRs.

4. **Classify Each Unclassified Issue:**
   For each issue that does not yet have a classification label (`auto-fixable`, `needs-approach-review`, `input-needed`, `project-candidate`, `human-required`):

   a. **Has exclusion label?** If the issue has any label found in `exclusion_labels`, classify as `human-required` (T5) and skip.

   b. **Check exclusion zone (PR overlap):** Read the issue and estimate which files/modules would need modification. If any overlap with the exclusion file set, classify as `human-required` (T5) and post this comment:
   `[BUG-FIXER] Skipped: overlaps with active PR #<number> (<branch>). Will re-evaluate when PR merges.`

   c. **Hardware dependency:** If the issue mentions hardware specifics (pins, sensors, physical devices, WiFi roaming), classify as `human-required` (T5) and skip.

   d. **Ambiguity check:** If the bug report is ambiguous, lacks repro steps, or has conflicting information, classify as `input-needed` (T3). Post this comment and skip:
   `[BUG-FIXER] Clarification needed: <ask specific questions based on ambiguity>.`

   e. **Size / Scope check:** If the fix is cross-module, requires new architecture, needs design decisions, is estimated > 500 lines, or is a multi-step feature, classify as `project-candidate` (T4). Post this comment and skip:
   `[BUG-FIXER] This issue is too large for autonomous fixing. Consider decomposing with /principal-architect.`

   f. **Complexity check:** If the fix is single-module but touches sensitive code, has ambiguous acceptance criteria, or is estimated > 200 lines, classify as `needs-approach-review` (T2). Post a proposed approach comment and apply the label.
   Comment Template:
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

   g. **Otherwise:** Classify as `auto-fixable` (T1). Single module, clear acceptance criteria, testable with host tests, no architectural decisions, <200 lines, no spec conflicts. Apply the label.

5. **Handle Approach Reviews (T2 Re-evaluation):**
   For issues already labeled `needs-approach-review`:
   Check if the maintainer has replied with an approval comment (e.g. `approved`). If approved:
   - Remove `needs-approach-review` label
   - Add `auto-fixable` label (T1)

6. **Milestone Query & Prioritisation:**
   Query milestones to get their status:
   ```bash
   gh api repos/<target_repo>/milestones --jq '.[] | {number, title, due_on, open_issues, state}'
   ```
   For all T1 and T2 issues, evaluate milestones:
   - If the issue belongs to a milestone, note it in the classification summary: `Milestone: <name> (due: <date>)`.
   - If the issue has no milestone but clearly relates to an open milestone's theme, suggest the milestone in the classification comment.

7. **Priority Ordering for Tick (Documented in Output):**
   When summarizing the output for the tick skill, output the issues in this priority order (assuming `milestone_priority_boost` is true):
   1. Issues in `active_milestone` (by priority label, then issue number)
   2. Issues in other milestones with due dates (by due date ascending, then priority)
   3. Issues without milestones (by priority label, then issue number)

8. **Fast Exit Conditions:** If no open issues exist, or if all open issues are already classified, output `TRIAGE_COMPLETE: No new issues to classify` and exit cleanly.

9. **Summary Report:** At the end of the run, output a summary table of all classifications made during this tick, along with the prioritized list of T1/T2 issues for the bug fixer to work on.
