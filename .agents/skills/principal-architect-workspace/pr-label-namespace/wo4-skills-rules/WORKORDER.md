---
name: label-namespace-wo4
description: Handoff instructions for Work Order 4 — Skills, Rules & Prompts
complexity: standard
---

# WO4 — Skills, Rules & Prompts

**Parent Project:** `pr-label-namespace` ([PROJECT.md](../PROJECT.md))
**Branch:** `label-namespace/wo4-skills-rules` (base: `label-namespace/wo3-dispatchers-runtime`)

---

## Role Definition

> You are an expert senior software engineer and execution-focused worker agent. You are NOT the Principal Architect. Do not make high-level architectural decisions, do not create new branches, and do not plan. Your task is strictly limited to executing the checklist below.

## Objective

Update all agent-facing skill SKILL.md files, EVAL.md evaluation criteria files, and agent rules files to use the new `bug-fixer:<actor>:<name>` label namespace. These are markdown files containing label references in prose, backtick-quoted code snippets, and `gh` CLI examples.

## Canonical Label Mapping

| Old Label | New Label |
|---|---|
| `auto-fixable` | `bug-fixer:engineer:needs-fix` |
| `human-required` | `bug-fixer:human:required` |
| `needs-approach-review` | `bug-fixer:human:needs-approach-review` |
| `project-candidate` | `bug-fixer:human:project-candidate` |
| `input-needed` | `bug-fixer:human:input-needed` |
| `status:in-progress` | `bug-fixer:engineer:in-progress` |
| `status:awaiting-review` | `bug-fixer:architect:awaiting-review` |
| `bug-fixer` (as label) | `bug-fixer:engineer:pr` |
| `bug-fixer-health` | `bug-fixer:system:health` |
| `spec-change-request` | `bug-fixer:human:spec-change-request` |
| `needs-iteration` | `bug-fixer:engineer:needs-iteration` |
| `architect-approved` | `bug-fixer:architect:approved` |
| `needs-human-intervention` | `bug-fixer:human:needs-intervention` |
| `discovered-in-review` | `bug-fixer:triage:discovered-in-review` |
| `complexity:mechanical` | `bug-fixer:complexity:mechanical` |
| `complexity:standard` | `bug-fixer:complexity:standard` |
| `complexity:architect` | `bug-fixer:complexity:architect` |
| `batch:<slug>` | `bug-fixer:batch:<slug>` |

**IMPORTANT — Triage tier parenthetical updates:**
When updating tier classification labels in parenthetical text like `T1 (auto-fixable)`, update to the NEW label's short name preserving the actor segment: `T1 (engineer:needs-fix)`. The `bug-fixer:` prefix is omitted in prose parentheticals for readability since the context is already clear. For example:
- `T1 (auto-fixable)` → `T1 (engineer:needs-fix)`
- `T2 (needs-approach-review)` → `T2 (human:needs-approach-review)`
- `T3 (input-needed)` → `T3 (human:input-needed)`
- `T4 (project-candidate)` → `T4 (human:project-candidate)`
- `T5 (human-required)` → `T5 (human:required)`

But in `gh` CLI commands, `--label` flags, `[BUG-FIXER-TRIAGE]` comment bodies, and backtick-quoted label names, use the FULL label name including `bug-fixer:` prefix.

## Strict Boundary Box

- **ONLY** modify these 9 files:
  1. `skills/bug-fixer-engineering/SKILL.md`
  2. `skills/bug-fixer-engineering/EVAL.md`
  3. `skills/bug-fixer-triage/SKILL.md`
  4. `skills/bug-fixer-triage/EVAL.md`
  5. `skills/architect-review/SKILL.md`
  6. `skills/bug-fixer/SKILL.md`
  7. `skills/bug-fixer-trajectory-review/SKILL.md`
  8. `rules/AGENTS.md`
  9. `.agents/rules/bug-fixer-AGENTS.md`
- **DO NOT** touch dispatchers, config files, scripts, docs, or tests
- **DO NOT** touch historical workorders in `.agents/skills/principal-architect-workspace/pr-*/`
- **DO NOT** change the YAML frontmatter of any skill file
- **DO NOT** change the structure, logic, or instructions — only update label string values
- `rules/AGENTS.md` and `.agents/rules/bug-fixer-AGENTS.md` should have IDENTICAL content after your changes

## Tooling Constraints

- Run `python3 -m unittest discover -s tests -v` to verify no test regressions (some tests may reference label names in assertions)

## Line-Item Instructions

### 1. `skills/bug-fixer-engineering/SKILL.md` (~41 references)

- [ ] Update ALL label references throughout the file. Key areas:
  - **YAML description/frontmatter**: DO NOT change
  - **Step 2 (Concurrency Cap)**: `status:in-progress` → `bug-fixer:engineer:in-progress`, `status:awaiting-review` → `bug-fixer:architect:awaiting-review`
  - **Step 2.5 (Iteration)**: `needs-iteration` → `bug-fixer:engineer:needs-iteration` in prose and `gh` commands
  - **Step 3 (T2 Approval)**: `needs-approach-review` → `bug-fixer:human:needs-approach-review`, `auto-fixable` → `bug-fixer:engineer:needs-fix`
  - **Step 4 (Issue Selection)**: `auto-fixable` → `bug-fixer:engineer:needs-fix` in queries, filters, prose; `status:in-progress` → `bug-fixer:engineer:in-progress`; `batch:*` → `bug-fixer:batch:*`
  - **Step 5 (Claim)**: `status:in-progress` → `bug-fixer:engineer:in-progress` in `gh` commands
  - **Step 8 (Bail-out)**: `status:in-progress` → `bug-fixer:engineer:in-progress`, `needs-approach-review` → `bug-fixer:human:needs-approach-review`
  - **Step 9 (Test failures)**: `status:in-progress` → `bug-fixer:engineer:in-progress`, `human-required` → `bug-fixer:human:required`
  - **Step 10 (Spec check)**: `spec-change-request,input-needed` → `bug-fixer:human:spec-change-request,bug-fixer:human:input-needed`
  - **Step 11 (PR)**: `bug-fixer` label → `bug-fixer:engineer:pr`, `batch:` → `bug-fixer:batch:`, `auto-fixable` → `bug-fixer:engineer:needs-fix`, `status:in-progress` → `bug-fixer:engineer:in-progress`, `status:awaiting-review` → `bug-fixer:architect:awaiting-review`
  - **Concurrency cap note**: `status:in-progress` → `bug-fixer:engineer:in-progress`

### 2. `skills/bug-fixer-engineering/EVAL.md` (~11 references)

- [ ] Update ALL label references in test scenarios and expected outputs

### 3. `skills/bug-fixer-triage/SKILL.md` (~38 references)

- [ ] Update ALL label references. Key areas:
  - **Step 4 (Classification)**: All tier labels in prose and `[BUG-FIXER-TRIAGE]` comment templates
  - **Step 4i (Complexity)**: `complexity:mechanical` → `bug-fixer:complexity:mechanical`, etc. Including `gh label create` examples
  - **Step 4j (Discovery gate)**: `discovered-in-review` → `bug-fixer:triage:discovered-in-review`, `needs-approach-review` → `bug-fixer:human:needs-approach-review`
  - **Step 5 (T2 Approval)**: `needs-approach-review` → `bug-fixer:human:needs-approach-review`, `auto-fixable` → `bug-fixer:engineer:needs-fix`, `discovered-in-review` → `bug-fixer:triage:discovered-in-review`, `complexity:architect` → `bug-fixer:complexity:architect`
  - **Step 9.5 (Batching)**: `batch:*` → `bug-fixer:batch:*` in all examples and `gh` commands

### 4. `skills/bug-fixer-triage/EVAL.md` (~10 references)

- [ ] Update ALL label references in test scenarios and expected outputs

### 5. `skills/architect-review/SKILL.md` (~14 references)

- [ ] Key areas:
  - **Step 0 (PR selection)**: `"bug-fixer"` → `"bug-fixer:engineer:pr"` in `gh pr list` command
  - **Step 2.5 (Escalation)**: `needs-human-intervention` → `bug-fixer:human:needs-intervention`
  - **Step 3 (Approve)**: `architect-approved` → `bug-fixer:architect:approved`, `needs-iteration` → `bug-fixer:engineer:needs-iteration` in `gh` commands
  - **Step 3 (Reject)**: `needs-iteration` → `bug-fixer:engineer:needs-iteration`, `architect-approved` → `bug-fixer:architect:approved` in `gh` commands
  - **Discovery filing**: `discovered-in-review` → `bug-fixer:triage:discovered-in-review`
  - **Guardrails**: `auto-fixable` → `bug-fixer:engineer:needs-fix`

### 6. `skills/bug-fixer/SKILL.md` (~9 references)

- [ ] Update label references in dashboard query examples and summary output formatting

### 7. `skills/bug-fixer-trajectory-review/SKILL.md` (~3 references)

- [ ] Update `status:in-progress` → `bug-fixer:engineer:in-progress`, `human-required` → `bug-fixer:human:required`, `bug-fixer-health` → `bug-fixer:system:health`

### 8. `rules/AGENTS.md` (~6 references)

- [ ] Line 13: `spec-change-request,input-needed` → `bug-fixer:human:spec-change-request,bug-fixer:human:input-needed`
- [ ] Lines 59-60: `bug-fixer` label → `bug-fixer:engineer:pr`
- [ ] Lines 68-70: Update Rule 15 — `auto-fixable` → `bug-fixer:engineer:needs-fix`, and all T2-T5 labels to their new names

### 9. `.agents/rules/bug-fixer-AGENTS.md` (~6 references)

- [ ] **IDENTICAL changes to `rules/AGENTS.md`** — these two files must remain in sync

## Verification Requirement

After making all changes:
1. Run tests: `python3 -m unittest discover -s tests -v`
2. Verify only the 9 target files are modified: `git diff --name-only`
3. Verify no old label names remain in target files:
   ```bash
   grep -rn 'auto-fixable\|"human-required"\|"needs-approach-review"\|"project-candidate"\|"input-needed"\|"status:in-progress"\|"status:awaiting-review"\|"bug-fixer-health"\|"spec-change-request"\|"needs-iteration"\|"architect-approved"\|"discovered-in-review"\|"needs-human-intervention"' skills/bug-fixer-engineering/ skills/bug-fixer-triage/ skills/architect-review/ skills/bug-fixer/ skills/bug-fixer-trajectory-review/ rules/AGENTS.md .agents/rules/bug-fixer-AGENTS.md
   ```
4. Verify `rules/AGENTS.md` and `.agents/rules/bug-fixer-AGENTS.md` are identical:
   ```bash
   diff rules/AGENTS.md .agents/rules/bug-fixer-AGENTS.md
   ```

**CRITICAL:** Before committing, ask the user if they are happy with the changes.

## Issue Surfacing

If you discover a defect, upstream bug, or concern *outside your boundary box*, surface it in your Completion Report below — do not file issues directly.

---

## Completion Report
<!-- Written by the worker at task completion -->

*(To be filled in by the worker upon completion)*

### Completion Report
All 9 files were successfully updated with the new canonical label mapping.
The tests passed with 0 failures.
The files modified exactly match the boundary box constraints.
`rules/AGENTS.md` and `.agents/rules/bug-fixer-AGENTS.md` are identical.
The verification grep command returned no output, confirming all old labels have been replaced.

I am awaiting your approval.
