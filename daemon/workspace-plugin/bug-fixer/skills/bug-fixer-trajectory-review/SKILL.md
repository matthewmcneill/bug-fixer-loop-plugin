---
name: bug-fixer-trajectory-review
description: >-
  Daily review of the bug-fixer loop's trajectory — identifies recurring
  failure patterns, stale claims, and quality trends. Triggered by the
  bug-fixer-trajectory-loop sidecar daily at 18:00, or manually with
  "review bug fixer trajectory".
---

# Trajectory Review

This skill performs a daily review of the bug-fixer's performance to catch recurring failure patterns and monitor quality trends.

## Workflow

1. **Scan Recent Transcripts:** 
   Scan recent sidecar transcripts in `~/.gemini/antigravity/brain/` for conversations matching the `Bug Fixer Triage` and `Bug Fixer Engineering` display names from the last 24 hours.

2. **Identify Failure Patterns:**
   Analyze the transcripts for the following issues:
   - **Repeated test failures**: If tests fail repeatedly on the same module, suggest the module needs human attention.
   - **Stale claims**: Issues claimed (`bug-fixer:engineer:in-progress`) but no PR opened within 4 hours trigger a stale claim alert.
   - **Unaddressed PR feedback**: PR feedback not addressed within 24 hours requires escalation.
   - **Churn**: If the same issue is attempted multiple times without success, add the `bug-fixer:human:required` label.

3. **Analyze Quality Trends:**
   Track the following metrics:
   - Issues triaged
   - PRs opened
   - PRs merged
   - PRs rejected
   - Test pass rate
   - **Architect Review Efficiency**: Read the `${BUG_FIXER_STATE_DIR}/pr_completions.jsonl` ledger to calculate and report:
     - Average architect review iterations per PR (total rounds before approval)
     - Iteration count broken down by engineer model
     - Human intervention rate (percentage of PRs needing intervention or rejected)
   Compare these metrics to the previous day if history exists.

4. **Architect Review Efficiency:**
   Read the completion ledger at `${BUG_FIXER_STATE_DIR}/pr_completions.jsonl`. Each line is a JSON object recording a completed PR cycle with fields: `pr`, `issue`, `model`, `type`, `architect_rounds`, `outcome`, `completed_at`.

   Produce the following analysis over **7-day** and **30-day** rolling windows:

   **a) Per-PR iteration detail (last 7 days):**
   List each completed PR with its iteration count, outcome, and engineer model:
   ```
   PR #284: 3 iterations → approved    (engineer: pro)
   PR #283: 2 iterations → approved    (engineer: flash)
   PR #281: 5 iterations → HUMAN REQ   (engineer: flash_lite)
   ```

   **b) Aggregate metrics (7-day and 30-day):**
   - Average architect review rounds to approval
   - First-pass approval rate (% of PRs approved with only 1 architect round)
   - Human intervention rate (% of PRs with outcome `human_intervention`)
   - Total PRs completed

   **c) Breakdown by engineer model:**
   For each model tier (`flash_lite`, `flash`, `pro`), report:
   - Number of PRs
   - Average iterations to approval
   - First-pass approval rate
   - Human intervention rate

   **d) Trends:**
   Compare the 7-day rolling metrics to the previous 7-day window. Flag:
   - Rising average iterations (engineer quality declining)
   - Rising human intervention rate (loop reliability declining)
   - Any model tier with >50% human intervention rate

   If the ledger file does not exist or is empty, skip this section and note that no completion data is available yet.

5. **Output Diagnostic Report:** 
   Output a diagnostic report as a structured summary. If critical patterns are detected (e.g., >3 consecutive failures, stale claims, rising human intervention rate, any model with >50% intervention rate), create a GitHub issue with the label `bug-fixer:system:health` describing the pattern so the maintainer is alerted.

## Workspace

- **Temporary files**: Use the workspace tmp directory path provided by the dispatcher for all scratch files and intermediate output. Stale files (>12h) are purged automatically on each daily run.
- **Completion ledger**: `${BUG_FIXER_STATE_DIR}/pr_completions.jsonl` — append-only JSONL file written by `sweep_dispatches()` in `liveness.py` when a dispatch completes. This is the primary data source for architect review efficiency metrics.

