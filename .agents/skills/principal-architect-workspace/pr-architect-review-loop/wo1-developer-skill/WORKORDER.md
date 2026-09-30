---
name: architect-loop-wo1
description: Handoff instructions for Work Order 1 - Updating the Developer Skill
complexity: mechanical
---

# WORKORDER 1: Update Developer Skill

## Role Definition
You are an expert senior software engineer and execution-focused worker agent. You are NOT the Principal Architect. Do not make high-level architectural decisions, do not branch, and do not plan. Your task is strictly limited to executing the checklist below.

## Objective
Update the `bug-fixer-tick/SKILL.md` file to instruct the agent to open PRs as Drafts and to embed its Antigravity conversation ID in the PR body. This enables the Architect agent to perform a stateless adversarial review.

## Strict Boundary Box
- DO NOT touch any `.py` dispatcher files.
- DO NOT touch any other skills.
- ONLY modify `skills/bug-fixer-tick/SKILL.md`.

## Tooling Constraints
Use `sed` or `replace_file_content` to make the changes. Do not rewrite the entire file unless necessary.

## Line-Item Instructions
- [x] In `skills/bug-fixer-tick/SKILL.md`, under `11. **Open PR:**`:
  - Change the `gh pr create` command to include the `--draft` flag. `gh pr create --draft --repo ...`
  - In the PR body requirements list, add a bullet point stating that the agent MUST include the following HTML comment at the very end of the body: `<!-- agy_dev_conv_id: <this_conversation_id> -->`.
  - Add a note explicitly telling the agent that `<this_conversation_id>` must be replaced by its actual Conversation ID.

## Verification Requirement
Run `git diff` to ensure only the requested lines in `skills/bug-fixer-tick/SKILL.md` were modified.
**CRITICAL:** Before committing, you MUST ask the user if they are happy with the changes (or if in a headless subagent, verify the diff strictly matches the instructions, commit, and then notify me via message). Since you are a subagent, verify the diff and then commit the changes.

## Issue Surfacing
If you discover a defect, upstream bug, or concern *outside your boundary box*, surface it in your Completion Report below.

---

## Completion Report
<!-- Written by the worker at task completion -->

### Status
Completed

### Changes Made
- Modified `skills/bug-fixer-tick/SKILL.md` under Step 11 (`11. **Open PR:**`):
  - Changed `gh pr create` command to include the `--draft` flag.
  - Added a bullet item to the PR body requirement list requiring the agent to include `<!-- agy_dev_conv_id: <this_conversation_id> -->` at the end of the body, with a note that `<this_conversation_id>` must be replaced by its actual Conversation ID.
- Committed change to `pr-architect-review-loop/wo1-developer-skill` branch (commit `412110c`).

### Decisions Made
- Added the tracking comment directly to the end of the PR body requirements bulleted list to match existing bullet structure and clarity.

### Dead Ends & Warnings
- None encountered.

### Out-of-Scope Findings
- None.

### Blockers
- None.
