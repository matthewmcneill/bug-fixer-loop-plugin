---
name: architect-loop-wo3
description: Handoff instructions for Work Order 3 - Creating the Architect Dispatcher Sidecar
complexity: standard
---

# WORKORDER 3: Create Architect Dispatcher Sidecar

## Role Definition
You are an expert senior software engineer and execution-focused worker agent. You are NOT the Principal Architect. Do not make high-level architectural decisions, do not branch, and do not plan. Your task is strictly limited to executing the checklist below.

## Objective
Create `scripts/architect_dispatcher.py`, the cron sidecar that polls GitHub for un-reviewed Draft PRs, extracts hidden metadata, and spawns (or resumes) the Architect agent to review them.

## Strict Boundary Box
- DO NOT touch existing scripts (except `plugin.json` to register the new sidecar).
- DO NOT touch any skills.
- DO NOT modify `.agents/rules`.

## Tooling Constraints
Use Python 3. Follow the same lightweight bash-free structure used in `scripts/tick_dispatcher.py`.

## Line-Item Instructions
- [ ] Create `scripts/architect_dispatcher.py` (make it executable: `chmod +x`).
- [ ] Ensure it has the same config resolution logic at the top as `tick_dispatcher.py` (resolving `BUG_FIXER_CONFIG` and `AGENTAPI_DIR`).
- [ ] Implement the polling logic:
  - Query `gh pr list --repo <target_repo> --label "bug-fixer" --state open --draft --json number,title,labels,body`
  - Filter out any PRs that already have the `needs-iteration` or `architect-approved` labels.
- [ ] Implement the Agent spawning logic for each eligible PR:
  - Look for `<!-- agy_arch_conv_id: <uuid> -->` in the PR body.
  - Fetch the PR diff using `gh pr diff <number>`.
  - Construct a prompt instructing the Architect to run the `architect-review` skill on the diff.
  - If `agy_arch_conv_id` is missing: Spawn a new conversation using `agentapi new-conversation --title="Architect Review #<number>" <prompt>`.
  - If it is present: Resume the existing conversation. Assume the command is `agentapi message --conversation-id <uuid> <prompt>`. (If that fails, just fallback to `new-conversation`).
- [ ] Register the sidecar in `plugin.json` under the `sidecars` array. Name it `bug-fixer-architect`, set the command to `scripts/architect_dispatcher.py`, and give it a schedule (e.g., `"*/15 * * * *"`).

## Verification Requirement
Run `python3 scripts/architect_dispatcher.py` to ensure it doesn't syntax error (it might exit cleanly if no PRs exist).
**CRITICAL:** Before committing, verify the changes and then commit them. Since you are a subagent, verify the code and then commit the changes directly to the current branch.

## Issue Surfacing
If you discover a defect, upstream bug, or concern *outside your boundary box*, surface it in your Completion Report below.

---

## Completion Report
<!-- Written by the worker at task completion -->

### Status
Completed

### Changes Made
- Created `scripts/architect_dispatcher.py` to poll GitHub for Draft PRs needing architect review and spawn/resume conversations via `agentapi`.
- Ensured it uses the identical config resolution logic from `tick_dispatcher.py`.
- Registered `bug-fixer-architect` sidecar in `plugin.json` under `sidecars` array with a schedule of `*/15 * * * *`.
- Verified script runs without syntax errors and committed changes to current branch.

### Decisions Made
- Used regex to extract the conversation ID `<!-- agy_arch_conv_id: <uuid> -->`.
- Followed bash-free structure using subprocess for executing the `gh` and `agentapi` binaries.
- If resuming an existing conversation fails, the script will gracefully fallback to creating a new conversation.

### Dead Ends & Warnings
- None encountered.

### Out-of-Scope Findings
- None encountered.

### Blockers
- None encountered.
