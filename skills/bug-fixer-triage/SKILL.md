---
name: bug-fixer-triage
description: >-
  Triage GitHub issues for the autonomous bug fixer. Classifies issues as
  auto-fixable or human-required. Triggered by the triage sidecar on a
  scheduled cron, or manually with "triage bug-fixer issues".
---

# Bug Fixer Triage

This skill classifies open issues as `auto-fixable` or `human-required` and enforces the active-PR exclusion zone to prevent the bug fixer from creating merge conflicts with the maintainer's ongoing work.

## Workflow

## Step 0: Configuration Guard
Read `config.json` from the plugin directory. If `target_repo` is empty or the file is missing, output `CONFIG_INCOMPLETE: Plugin not configured. Run scripts/configure.sh first.` and exit immediately. Do not proceed with any GitHub operations.

1. **Load Configuration:** Read `config.json` from the plugin directory. Extract `target_repo`, `maintainer`, `exclusion_labels`, `auto_fixable_labels`, `spec_file_path`.

2. **Query Open Issues:** Run the safe ingest proxy to list all open issues:
   ```bash
   python3 <plugin_dir>/scripts/safe_ingest.py issue-list --repo <target_repo> --state open --json number,title,labels,body,milestone,author,assignees,comments
   ```

3. **Query Maintainer's Active PRs:** Find the maintainer's open PRs and their changed files to build the **exclusion file set**:
   ```bash
   gh pr list --repo <target_repo> --author <maintainer> --state open --json number,title,headRefName,files
   ```
   The exclusion file set is the union of all files touched by these active PRs.

4. **Classify Each Unclassified Issue:**
   For each issue that does not yet have an `auto-fixable` or `human-required` label:

   a. **Skip if exclusion label present** — if the issue has any label found in `exclusion_labels`, immediately apply the `human-required` label and move to the next issue.

   b. **Estimate fix scope** — read the issue title, body, and existing labels. Use the repository's module structure and the design spec to determine which files or modules would likely need to be modified.

   c. **Check exclusion zone** — if any estimated fix files overlap with the exclusion file set, apply the `human-required` label and post this comment:
   `[BUG-FIXER] Skipped: overlaps with active PR #<number> (<branch>). Will re-evaluate when PR merges.`

   d. **Assess complexity** — classify the issue based on the following criteria:
      - **Auto-fixable**: Fix involves a single module, has clear acceptance criteria, is testable with existing host tests, requires no architectural decisions, and the estimated change is < 200 lines.
      - **Human-required**: Fix is cross-cutting, requires architectural judgment, requires physical hardware to verify, lacks clear acceptance criteria, touches the core kernel, or the estimated change is > 200 lines.

   e. **Apply label** — ensure idempotent label creation and add the chosen label:
   ```bash
   gh label create auto-fixable --repo <target_repo> --force
   gh issue edit <number> --repo <target_repo> --add-label auto-fixable
   # OR --add-label human-required
   ```

5. **Fast Exit Conditions:** If no open issues exist, or if all open issues are already classified, output `TRIAGE_COMPLETE: No new issues to classify` and exit cleanly without further action.

6. **Summary Report:** At the end of the run, output a summary table of all classifications made during this tick.
