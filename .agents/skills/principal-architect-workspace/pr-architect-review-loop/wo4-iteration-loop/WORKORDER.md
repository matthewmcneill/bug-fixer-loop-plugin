---
name: architect-loop-wo4
description: Handoff instructions for Work Order 4 - Updating the Tick Dispatcher for Iteration
complexity: architect
---

# WORKORDER 4: Update Tick Dispatcher

## Role Definition
You are an expert senior software engineer and execution-focused worker agent. You are NOT the Principal Architect. Do not make high-level architectural decisions, do not branch, and do not plan. Your task is strictly limited to executing the checklist below.

## Objective
Update `scripts/tick_dispatcher.py` to add a new step: it must detect PRs with the `needs-iteration` label, extract the Developer's conversation ID, and resume that conversation so the Developer can fix the Architect's findings.

## Strict Boundary Box
- DO NOT touch any existing skills.
- DO NOT touch `architect_dispatcher.py`.
- ONLY modify `scripts/tick_dispatcher.py`.

## Tooling Constraints
Use Python 3. Modify the existing file, being careful to integrate cleanly with the existing Steps (Step 1 is stale claim recovery, Step 2 is concurrency check, etc.).

## Line-Item Instructions
- [ ] In `scripts/tick_dispatcher.py`, insert a new step before Step 3 (Find eligible issues), maybe call it "Step 2.5: Handle needs-iteration PRs".
- [ ] Query GitHub for PRs with the `bug-fixer` and `needs-iteration` labels that are open.
- [ ] For each PR:
  - Fetch the PR body and extract `<!-- agy_dev_conv_id: <uuid> -->`.
  - Fetch the most recent review comments on the PR (specifically from the Architect agent).
  - Use `agentapi message --conversation-id <uuid> <prompt>` (or whatever the resume command is, matching `architect_dispatcher.py`) to resume the Developer agent. The prompt must include the Architect's review comments and instruct the Developer to push fixes to the PR branch.
  - Remove the `needs-iteration` label.
- [ ] Ensure the concurrency logic is respected (if a Developer is iterating, it probably doesn't consume a NEW slot, or maybe it does—just ensure it doesn't break the existing slot logic).

## Verification Requirement
Run `python3 scripts/tick_dispatcher.py` to ensure it doesn't syntax error.
**CRITICAL:** Before committing, verify the code and then commit the changes directly to the current branch.

## Issue Surfacing
If you discover a defect, upstream bug, or concern *outside your boundary box*, surface it in your Completion Report below.

---

## Completion Report
<!-- Written by the worker at task completion -->

### Status
Completed

### Changes Made
- Imported `re` in `scripts/tick_dispatcher.py`.
- Inserted Step 2.5 in `scripts/tick_dispatcher.py` to handle PRs with the `needs-iteration` label.
- Fetched PR body to extract the developer conversation ID.
- Fetched the latest Architect comments from the PR.
- Resumed the developer conversation via `agentapi message` and removed the `needs-iteration` label.
- Updated concurrency tracking (`active_others`) when a developer is resumed.

### Decisions Made
- Chose to extract the latest comment from the PR as the architect's review comment.
- Decided that a resumed developer session occupies a concurrency slot to respect `MAX_CONCURRENT`.

### Dead Ends & Warnings
None.

### Out-of-Scope Findings
None.

### Blockers
None.
