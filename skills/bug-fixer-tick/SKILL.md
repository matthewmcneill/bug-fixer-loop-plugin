---
name: bug-fixer-tick
description: >-
  Execute one cycle of the autonomous bug fixer loop. Picks the highest-priority
  auto-fixable issue, implements the fix, runs tests, and opens a PR. Triggered
  by the tick sidecar every 2 hours, or manually with "fix bugs" or "run bug fixer".
---

# Bug Fixer Tick (Execution Engine)

This skill executes one cycle of the autonomous bug fixer loop. It picks the highest-priority eligible issue, implements a fix, tests it, and opens a pull request.

## Workflow

## Step 0: Configuration Guard
Read `config.json` from the plugin directory. If `target_repo` is empty or the file is missing, output `CONFIG_INCOMPLETE: Plugin not configured. Run scripts/configure.sh first.` and exit immediately. Do not proceed with any GitHub operations.

1. **Load Configuration:** Read `config.json` from the plugin directory. Extract all fields.

2. **Concurrency Guard:** Query open PRs authored by the agent:
   ```bash
   gh pr list --repo <target_repo> --author @me --state open --json number,title,labels
   ```
   Count the PRs that have `pr_prefix` in the title. If this count is >= `max_concurrent_prs`, output `TICK_EXIT: PR cap reached (<count>/<max>). Waiting for merge.` and exit cleanly.

3. **Check for PR Feedback (Highest Priority):** For each open agent PR:
   - Query comments and reviews via: `python3 <plugin_dir>/scripts/safe_ingest.py pr-comments <number>`
   - Look for any unaddressed maintainer comments (comments from `maintainer` or `trusted_users` that do not have a subsequent `[BUG-FIXER]` reply).
   - If found: check out the PR branch, implement the requested changes, run tests, push the changes, reply with `[BUG-FIXER] Addressed review feedback in <sha>`, and exit this tick.

4. **Select Issue:** Query open, auto-fixable issues:
   ```bash
   python3 <plugin_dir>/scripts/safe_ingest.py issue-list --repo <target_repo> --label auto-fixable --state open --json number,title,labels,body,priority,priorityRank
   ```
   Pick the first issue sorted by priority rank (ascending), then by issue number (ascending, FIFO).

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
   - Run `python3 <plugin_dir>/scripts/pre_tool_gate.py --scan-only .` for leak check.
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
      The PR body must include: a link to the issue, what was changed, why it was changed, full test output, and the leak scan result.
    - Remove the `status:in-progress` label from the issue.

12. **Fast Exit:** If Step 4 finds no eligible issues, output `TICK_EXIT: NO_ELIGIBLE_WORK` and exit cleanly. Never invent work, create issues, or expand scope.
