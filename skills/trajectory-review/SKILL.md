---
name: trajectory-review
description: >-
  Daily review of the bug-fixer loop's trajectory — identifies recurring
  failure patterns, stale claims, and quality trends. Triggered by the
  trajectory-review sidecar daily at 18:00, or manually with
  "review bug fixer trajectory".
---

# Trajectory Review

This skill performs a daily review of the bug-fixer's performance to catch recurring failure patterns and monitor quality trends.

## Workflow

1. **Scan Recent Transcripts:** 
   Scan recent sidecar transcripts in `~/.gemini/antigravity/brain/` for conversations matching the `bug-fixer-triage` and `bug-fixer-tick` display names from the last 24 hours.

2. **Identify Failure Patterns:**
   Analyze the transcripts for the following issues:
   - **Repeated test failures**: If tests fail repeatedly on the same module, suggest the module needs human attention.
   - **Stale claims**: Issues claimed (`status:in-progress`) but no PR opened within 4 hours trigger a stale claim alert.
   - **Unaddressed PR feedback**: PR feedback not addressed within 24 hours requires escalation.
   - **Churn**: If the same issue is attempted multiple times without success, add the `human-required` label.

3. **Analyze Quality Trends:**
   Track the following metrics:
   - Issues triaged
   - PRs opened
   - PRs merged
   - PRs rejected
   - Test pass rate
   Compare these metrics to the previous day if history exists.

4. **Output Diagnostic Report:** 
   Output a diagnostic report as a structured summary. If critical patterns are detected (e.g., >3 consecutive failures, stale claims), create a GitHub issue with the label `bug-fixer-health` describing the pattern so the maintainer is alerted.

## Workspace

- **Temporary files**: Use the workspace tmp directory path provided by the dispatcher for all scratch files and intermediate output. Stale files (>12h) are purged automatically on each daily run.
