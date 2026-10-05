---
name: architect-loop-wo2
description: Handoff instructions for Work Order 2 - Creating the Architect Review Skill
complexity: standard
---

# WORKORDER 2: Create Architect Skill

## Role Definition
You are an expert senior software engineer and execution-focused worker agent. You are NOT the Principal Architect. Do not make high-level architectural decisions, do not branch, and do not plan. Your task is strictly limited to executing the checklist below.

## Objective
Create the `skills/architect-review/SKILL.md` file. This skill defines the adversarial review persona for the Principal Architect review loop, outlining how it should scrutinize PRs, and how it accepts or rejects them (including injecting its own conversation ID into the PR body).

## Strict Boundary Box
- DO NOT touch any existing skills.
- DO NOT touch any Python dispatcher scripts.
- ONLY create `skills/architect-review/SKILL.md`.

## Tooling Constraints
Use standard write commands to create the file.

## Line-Item Instructions
- [x] Create `skills/architect-review/SKILL.md`.
- [x] Include YAML frontmatter with `name: architect-review` and a brief description.
- [x] The skill must instruct the agent to act as a "Principal Architect" performing an adversarial review of a bug-fixer PR. It should look for technical debt, pointer instability, memory leaks, or missed architectural constraints.
- [x] Define the exact approval flow:
  - If the PR is perfect: Add the `architect-approved` label to the PR, mark it "Ready for review" (remove draft status), and exit.
- [x] Define the exact rejection flow:
  - If changes are needed:
    1. Add the `needs-iteration` label.
    2. Add a review comment detailing exactly what needs to be fixed.
    3. The agent MUST use the GitHub CLI or API to append this HTML comment to the very bottom of the PR body (preserving existing content): `<!-- agy_arch_conv_id: <this_conversation_id> -->`. Note that it must use its actual conversation ID.

## Verification Requirement
Verify the file exists and is well-formed Markdown.
**CRITICAL:** Before committing, verify the changes and then commit them. Since you are a subagent, verify the diff and then commit the changes directly to the current branch.

## Issue Surfacing
If you discover a defect, upstream bug, or concern *outside your boundary box*, surface it in your Completion Report below.

---

## Completion Report
<!-- Written by the worker at task completion -->

### Status
Completed

### Changes Made
- Created `skills/architect-review/SKILL.md`:
  - Added YAML frontmatter with `name: architect-review` and description.
  - Defined the adversarial Principal Architect persona and scrutiny criteria covering technical debt, pointer instability, memory leaks, and missed architectural constraints.
  - Defined the exact approval flow: add `architect-approved` label, mark PR ready for review (`gh pr ready`), submit approval review comment, and exit.
  - Defined the exact rejection flow: add `needs-iteration` label, post actionable architectural review comment detailing required fixes, and append/update `<!-- agy_arch_conv_id: <this_conversation_id> -->` at the bottom of the PR body preserving existing body content.
- Committed changes to branch `pr-architect-review-loop/wo2-architect-skill` (commit `78465e6`).

### Decisions Made
- Structured the skill with clear 4-pillar review rubrics (Technical Debt, Pointer Instability, Memory Leaks, Architectural Constraints) and exact command workflows for both approval and rejection.
- Provided explicit bash examples for reading and updating PR bodies with `gh pr edit --body` ensuring the conversation ID comment is preserved or updated cleanly.

### Dead Ends & Warnings
- None encountered.

### Out-of-Scope Findings
- None.

### Blockers
- None.
