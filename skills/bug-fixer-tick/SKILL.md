---
name: bug-fixer-tick
description: >-
  Execute one cycle of the autonomous bug fixer loop. Picks the highest-priority
  auto-fixable issue respecting milestone order, implements the fix, runs tests,
  and opens a PR with the bug-fixer label. Triggered by the tick sidecar every 2
  hours, or manually with "fix bugs" or "run bug fixer".
---

# Bug Fixer Tick (Execution Engine)

This skill executes one cycle of the autonomous bug fixer loop. It checks for review feedback and approved approaches, selects the highest-priority eligible auto-fixable issue (with milestone awareness), implements a fix, tests it, and opens a pull request.

## Workflow

## Step 0: Configuration Guard
This skill is part of the `bug-fixer-loop` plugin. Resolve:
```bash
PLUGIN_DIR="$(cd "$(dirname "<this_skill_file>")/../.." && pwd)"
STATE_DIR="<workspace_root>/.agents/plugins/bug-fixer-loop-workspace"
```
Read `${STATE_DIR}/config.json`. If `target_repo` is empty or the file is missing, output `CONFIG_INCOMPLETE: Plugin not configured. Run ${PLUGIN_DIR}/scripts/configure.sh first.` and exit immediately.

1. **Load Configuration:** Read `${STATE_DIR}/config.json`. Extract all fields (`target_repo`, `maintainer`, `trusted_users`, `spec_file_path`, `test_command`, `lint_command`, `max_concurrent_prs`, `pr_prefix`, `branch_prefix`, `active_milestone`, `milestone_priority_boost`).

2. **Concurrency Guard:** Count issues the agent is actively developing:
   ```bash
   gh issue list --repo <target_repo> --label "status:in-progress" --state open --json number,title
   ```
   If this count is >= `max_concurrent_prs`, output `TICK_EXIT: Active development cap reached (<count>/<max>). Waiting for current fixes to complete.` and exit cleanly.

   > **Note:** The cap limits issues *being actively coded*, not open PRs waiting for review. Once a PR is opened and `status:in-progress` is removed from the issue, the slot is freed immediately — the agent does not wait for the human to merge.

3. **Check for PR Feedback (Highest Priority):** For each open agent PR:
   - Query comments and reviews via: `python3 ${PLUGIN_DIR}/scripts/safe_ingest.py pr-comments <number>`
   - Look for any unaddressed maintainer comments (comments from `maintainer` or `trusted_users` that do not have a subsequent `[BUG-FIXER]` reply).
   - If found: check out the PR branch, implement the requested changes, run tests, push the changes, reply with `[BUG-FIXER] Addressed review feedback in <sha>`, and exit this tick.

### Step 3.5: Check for Approved Approaches
Query issues with the `needs-approach-review` label:
```bash
python3 ${PLUGIN_DIR}/scripts/safe_ingest.py issue-list --repo <target_repo> --label needs-approach-review --state open --json number,title,labels,comments
```
For each issue, check if the maintainer has posted an approval comment (contains `approved` or `/approve` from a trusted user). If found:
- Remove `needs-approach-review` label
- Add `auto-fixable` label
- Log: `[BUG-FIXER] Approach approved for #<number>, now eligible for fixing.`

This ensures previously-reviewed issues enter the auto-fixable queue.

4. **Select Issue:**
   - **Only T1 issues are eligible for immediate fixing.** The tick skill MUST NOT pick up `needs-approach-review`, `input-needed`, `project-candidate`, or `human-required` issues. Only `auto-fixable` issues are eligible.
   - Query open, auto-fixable issues:
     ```bash
     python3 ${PLUGIN_DIR}/scripts/safe_ingest.py issue-list --repo <target_repo> --label auto-fixable --state open --json number,title,labels,body,milestone,priority,priorityRank
     ```
   - Query milestones when selecting:
     ```bash
     gh api repos/<target_repo>/milestones --jq '.[] | {number, title, due_on}'
     ```
     and cross-reference with the issue's milestone field.
   - Exclude issues with `status:in-progress` label.
   - **Milestone-aware priority ordering:**
     1. First: `auto-fixable` issues in the `active_milestone` (from config), sorted by priority label then issue number
     2. Then: `auto-fixable` issues in other milestones with due dates, sorted by due date ascending
     3. Finally: `auto-fixable` issues with no milestone, sorted by priority label then issue number
   - Pick the first issue from this sorted list.
   - Note if the issue was converted from T2 (`needs-approach-review` with maintainer approval) to accurately classify it in the PR body.

5. **Claim Issue:** Act as a lease to prevent other agents from picking up the same work:
   ```bash
   gh issue edit <number> --repo <target_repo> --add-label status:in-progress
   ```

6. **Branch:** Check out a new branch from `origin/main`:
   ```bash
   git checkout -b <branch_prefix>/issue-<number>-<slug> origin/main
   ```
   *(where `<slug>` is a kebab-case version of the issue title, max 40 chars)*

7. **Understand the Issue:** Read the issue body and any `[ISSUE-REVIEW]` execution contract comments. Read the project's spec file (`spec_file_path`) for relevant sections. Identify the exact files to modify.

8. **Implement the Fix:**
   - Make the minimal code changes necessary to resolve the issue.
   - Follow the project's coding style (check existing files in the same module).
   - Add or update tests to cover the fix.
   - Ensure changes remain within the module boundary — do NOT make cross-cutting changes.

9. **Verify:**
   - Run `<test_command>` and capture the full output.
   - Run `<lint_command>` if configured.
   - Run `python3 ${PLUGIN_DIR}/scripts/pre_tool_gate.py --scan-only .` for leak check.
   - All three commands must pass. If any fails, debug and retry up to 2 times. If tests or lint still fail after 3 total attempts, unclaim the issue (`--remove-label status:in-progress`), add the `human-required` label, comment `[BUG-FIXER] Attempted fix but tests/lint failed after 3 attempts. Needs human attention.`, and exit.

10. **Self-Review Against Spec:** Read the `spec_file_path` sections relevant to the modified modules. Verify the fix does not contradict any specification or ADR. If it does, unclaim the issue, label it `spec-change-request,input-needed`, comment explaining the conflict, and exit.

11. **Open PR:**
    - Stage and commit changes:
      ```bash
      git add -A
      git commit -m "<pr_prefix> Fix #<number>: <title>"
      ```
    - Push the branch:
      ```bash
      git push -u origin <branch>
      ```
    - Create the PR:
      ```bash
      gh pr create --repo <target_repo> --base main --title "<pr_prefix> Fix #<number>: <title>" --body "<body>"
      ```
      The PR body must include:
      - Link to the issue: `Resolves #<number>`
      - Issue tier: `**Classification:** T1 (auto-fixable)` or `T2 (approach approved by @maintainer)`
      - Milestone: `**Milestone:** <name>` if applicable
      - The `bug-fixer` label must be listed (e.g., `**Labels:** bug-fixer`)
      - What was changed and why it was changed
      - Full test output
      - Leak scan result
    - Apply `bug-fixer` label to the PR:
      ```bash
      gh pr edit <pr_number> --repo <target_repo> --add-label bug-fixer
      ```
    - Carry over milestone: If the resolved issue belongs to a milestone, set the same milestone on the PR:
      ```bash
      gh pr edit <pr_number> --repo <target_repo> --milestone "<milestone_title>"
      ```
    - Remove the `status:in-progress` label from the issue.

### Step 12: Loop or Exit

After successfully opening a PR, **loop back to Step 4** and pick the next eligible issue. Keep fixing issues until one of these conditions is met:

- **No eligible issues remain** → output `TICK_EXIT: NO_ELIGIBLE_WORK — opened <count> PR(s) this tick.` and exit.
- **Concurrency cap reached** → re-check `status:in-progress` count. If >= `max_concurrent_prs`, output `TICK_EXIT: Active development cap reached — opened <count> PR(s) this tick.` and exit.
- **A fix attempt fails** (tests fail after 3 retries) → unclaim that issue, then continue to the next eligible issue rather than exiting entirely.

> **Important:** Each fix must start from a fresh branch off `origin/main` (Step 6). Pull latest main before each iteration to pick up any changes merged since the tick started.

Never invent work, create issues, or expand scope.
