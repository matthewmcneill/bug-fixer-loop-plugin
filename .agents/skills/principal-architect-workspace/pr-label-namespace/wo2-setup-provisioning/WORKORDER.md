---
name: label-namespace-wo2
description: Handoff instructions for Work Order 2 — Setup & Provisioning (configure.sh)
complexity: standard
---

# WO2 — Setup & Provisioning (configure.sh)

**Parent Project:** `pr-label-namespace` ([PROJECT.md](../PROJECT.md))
**Branch:** `label-namespace/wo2-setup-provisioning` (base: `label-namespace/wo1-config-install`)

---

## Role Definition

> You are an expert senior software engineer and execution-focused worker agent. You are NOT the Principal Architect. Do not make high-level architectural decisions, do not create new branches, and do not plan. Your task is strictly limited to executing the checklist below.

## Objective

Update `scripts/configure.sh` to use the new `bug-fixer:<actor>:<name>` routed-segment label namespace in all label definitions, provisioning logic, dry-run classifier, and label-apply logic.

## Canonical Label Mapping

Reference this table for every substitution:

| Old Label | New Label |
|---|---|
| `auto-fixable` | `bug-fixer:engineer:needs-fix` |
| `human-required` | `bug-fixer:human:required` |
| `needs-approach-review` | `bug-fixer:human:needs-approach-review` |
| `project-candidate` | `bug-fixer:human:project-candidate` |
| `input-needed` | `bug-fixer:human:input-needed` |
| `status:in-progress` | `bug-fixer:engineer:in-progress` |
| `bug-fixer` | `bug-fixer:engineer:pr` |
| `bug-fixer-health` | `bug-fixer:system:health` |
| `spec-change-request` | `bug-fixer:human:spec-change-request` |
| `needs-iteration` | `bug-fixer:engineer:needs-iteration` |
| `architect-approved` | `bug-fixer:architect:approved` |
| `needs-human-intervention` | `bug-fixer:human:needs-intervention` |
| `discovered-in-review` | `bug-fixer:triage:discovered-in-review` |
| `status:awaiting-review` | `bug-fixer:architect:awaiting-review` |

## Strict Boundary Box

- **ONLY** modify `scripts/configure.sh` — no other files
- **DO NOT** touch `scripts/install.sh`, `config.json`, or any other config (already done in WO1)
- **DO NOT** touch dispatchers, skills, rules, or docs
- **DO NOT** modify the shell script's control flow, logic structure, or comments — only update label string values
- **DO NOT** rename or move any files
- Labels NOT owned by bug-fixer (`P1-blocker`, `hardware-required`, `S`, `L`, `XL`, effort labels) remain **unchanged**

## Tooling Constraints

- Run `bash -n scripts/configure.sh` to syntax-check after editing
- Run `python3 -m unittest discover -s tests -v` to verify no test regressions

## Line-Item Instructions

### Section 1: `LABELS_META` associative array (~lines 353-363)

- [ ] Update every key and description in the `LABELS_META` array:
  ```bash
  declare -A LABELS_META=(
    ["bug-fixer:engineer:needs-fix"]="0E8A16:Issues eligible for autonomous fixing"
    ["bug-fixer:human:required"]="D93F0B:Issues requiring human intervention or architectural review"
    ["bug-fixer:human:needs-approach-review"]="FBCA04:Fix approach requires human maintainer sign-off"
    ["bug-fixer:human:project-candidate"]="5319E7:Large multi-step initiative candidate"
    ["bug-fixer:human:input-needed"]="D4C5F9:Waiting for human input or clarification"
    ["bug-fixer:engineer:in-progress"]="1D76DB:Currently being worked on by an agent or human"
    ["bug-fixer:engineer:pr"]="0075CA:Managed by autonomous bug-fixer loop"
    ["bug-fixer:system:health"]="C5DEF5:Bug fixer health, metrics, or diagnostic report"
    ["bug-fixer:human:spec-change-request"]="F9D0C4:Proposed modification to design specification"
    ["bug-fixer:engineer:needs-iteration"]="E4E669:PR needs engineering rework after architect review"
    ["bug-fixer:architect:approved"]="0E8A16:Architect approved the PR for merge"
    ["bug-fixer:architect:awaiting-review"]="BFD4F2:PR ready for architect review"
    ["bug-fixer:human:needs-intervention"]="D93F0B:Architect escalated — needs human resolution"
    ["bug-fixer:triage:discovered-in-review"]="d4c5f9:Auto-filed during PR review, requires human approval before auto-fix"
  )
  ```

### Section 2: `REQUIRED_LABELS` array (~lines 365-375)

- [ ] Replace the `REQUIRED_LABELS` array with the full expanded list (matching the keys from `LABELS_META` above, minus `discovered-in-review` which was handled separately — but now include it in the main list):
  ```bash
  REQUIRED_LABELS=(
    "bug-fixer:engineer:needs-fix"
    "bug-fixer:human:required"
    "bug-fixer:human:needs-approach-review"
    "bug-fixer:human:project-candidate"
    "bug-fixer:human:input-needed"
    "bug-fixer:engineer:in-progress"
    "bug-fixer:engineer:pr"
    "bug-fixer:system:health"
    "bug-fixer:human:spec-change-request"
    "bug-fixer:engineer:needs-iteration"
    "bug-fixer:architect:approved"
    "bug-fixer:architect:awaiting-review"
    "bug-fixer:human:needs-intervention"
    "bug-fixer:triage:discovered-in-review"
  )
  ```

### Section 3: Separate `discovered-in-review` label creation (~lines 389-394)

- [ ] **Remove** the standalone `gh label create "discovered-in-review"` block (lines 389-394). It is now included in the `REQUIRED_LABELS` array and `LABELS_META`, so the main loop handles it.

### Section 4: Config write-back Python block (~lines 464-475)

- [ ] Update the `req_labels` list in the Python heredoc to use the new label names:
  ```python
  req_labels = [
      "bug-fixer:engineer:needs-fix",
      "bug-fixer:human:required",
      "bug-fixer:human:needs-approach-review",
      "bug-fixer:human:project-candidate",
      "bug-fixer:human:input-needed",
      "bug-fixer:engineer:in-progress",
      "bug-fixer:engineer:pr",
      "bug-fixer:system:health",
      "bug-fixer:human:spec-change-request",
      "bug-fixer:engineer:needs-iteration",
      "bug-fixer:architect:approved",
      "bug-fixer:architect:awaiting-review",
      "bug-fixer:human:needs-intervention",
      "bug-fixer:triage:discovered-in-review"
  ]
  ```

### Section 5: Dry-run classifier — first Python block (~lines 529-531, 570, 591-627)

- [ ] Update `exclusion_labels` set (line 529): `"human-required"` → `"bug-fixer:human:required"` (keep `"hardware-required"` and `"P1-blocker"` unchanged)
- [ ] Update `auto_labels` set (line 531): Replace all 5 classification labels with their new names:
  ```python
  auto_labels = {"bug-fixer:engineer:needs-fix", "bug-fixer:human:needs-approach-review", "bug-fixer:human:input-needed", "bug-fixer:human:project-candidate", "bug-fixer:human:required"}
  ```
- [ ] Update tier classification strings (lines 591-627) — every `tier = "T..."` and `label = "..."` assignment:
  - `"T5 human-required"` → `"T5 bug-fixer:human:required"`
  - `"T3 input-needed"` → `"T3 bug-fixer:human:input-needed"`
  - `"T1 auto-fixable"` → `"T1 bug-fixer:engineer:needs-fix"` (appears twice — lines 603 and 621)
  - `"T4 project-candidate"` → `"T4 bug-fixer:human:project-candidate"` (appears twice — lines 607 and 610)
  - `"T2 needs-approach-review"` → `"T2 bug-fixer:human:needs-approach-review"` (appears twice — lines 614 and 617)

### Section 6: Apply-labels — second Python block (~lines 677-679, 698, 711-727)

- [ ] Update `exclusion_labels` set (line 677): same as section 5
- [ ] Update `auto_labels` set (line 679): same as section 5
- [ ] Update `labels & auto_labels` skip check (line 698): no code change needed — the set comparison works with the new names
- [ ] Update label assignment strings (lines 711-727):
  - `label = "human-required"` → `label = "bug-fixer:human:required"` (appears twice — lines 711 and 713)
  - `label = "input-needed"` → `label = "bug-fixer:human:input-needed"` (line 715)
  - `label = "auto-fixable"` → `label = "bug-fixer:engineer:needs-fix"` (appears twice — lines 717 and 727)
  - `label = "project-candidate"` → `label = "bug-fixer:human:project-candidate"` (appears twice — lines 719 and 721)
  - `label = "needs-approach-review"` → `label = "bug-fixer:human:needs-approach-review"` (appears twice — lines 723 and 725)

## Verification Requirement

After making all changes:
1. Syntax-check: `bash -n scripts/configure.sh`
2. Run tests: `python3 -m unittest discover -s tests -v`
3. Verify only `scripts/configure.sh` is modified: `git diff --name-only`
4. Verify no old label names remain in the file (except in comments, and the non-bug-fixer labels like `P1-blocker`, `hardware-required`, etc.):
   ```bash
   grep -n '"auto-fixable"\|"human-required"\|"needs-approach-review"\|"project-candidate"\|"input-needed"\|"status:in-progress"\|"bug-fixer"\|"bug-fixer-health"\|"spec-change-request"\|"needs-iteration"\|"architect-approved"\|"discovered-in-review"\|"needs-human-intervention"\|"status:awaiting-review"' scripts/configure.sh
   ```
   This should return NO matches (the grep patterns are exact-quoted strings, so `"bug-fixer:engineer:pr"` won't match `"bug-fixer"` because of the quotes).

**CRITICAL:** Before committing, ask the user if they are happy with the changes.

## Issue Surfacing

If you discover a defect, upstream bug, or concern *outside your boundary box*, surface it in your Completion Report below — do not file issues directly. The Principal Architect will triage and file them under the correct author identity.

---

## Completion Report
<!-- Written by the worker at task completion -->

*(Filled by worker)*
- Changes successfully implemented in `scripts/configure.sh`.
- Labels translated to the new `bug-fixer:<actor>:<name>` namespace.
- Unittests passed and syntax checked.
- No old labels found.
- Ready for integration.
