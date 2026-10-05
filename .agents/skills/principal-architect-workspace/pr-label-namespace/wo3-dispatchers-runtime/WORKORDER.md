---
name: label-namespace-wo3
description: Handoff instructions for Work Order 3 — Dispatchers & Runtime
complexity: standard
---

# WO3 — Dispatchers & Runtime

**Parent Project:** `pr-label-namespace` ([PROJECT.md](../PROJECT.md))
**Branch:** `label-namespace/wo3-dispatchers-runtime` (base: `label-namespace/wo2-setup-provisioning`)

---

## Role Definition

> You are an expert senior software engineer and execution-focused worker agent. You are NOT the Principal Architect. Do not make high-level architectural decisions, do not create new branches, and do not plan. Your task is strictly limited to executing the checklist below.

## Objective

Update all runtime Python scripts (dispatchers and liveness monitor) to use the new `bug-fixer:<actor>:<name>` label namespace in `gh` CLI commands, string comparisons, log messages, and prompt text.

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
| `complexity:` (prefix) | `bug-fixer:complexity:` (prefix) |

## Strict Boundary Box

- **ONLY** modify files in `scripts/`: `engineering_dispatcher.py`, `triage_dispatcher.py`, `architect_dispatcher.py`, `trajectory_dispatcher.py`, `liveness.py`
- **DO NOT** touch `scripts/configure.sh`, `scripts/install.sh`, `scripts/safe_ingest.py`, or any config files
- **DO NOT** touch skills, rules, docs, or tests
- **DO NOT** change any program logic, control flow, or function signatures — only update label string literals
- **IMPORTANT**: Some label references appear in **log messages, comments, and prompt strings** (f-strings passed to agents). These must also be updated for consistency, even though they don't affect runtime behaviour.

## Tooling Constraints

- Run `python3 -m py_compile scripts/engineering_dispatcher.py` (and each file) to syntax-check
- Run `python3 -m unittest discover -s tests -v` to verify no test regressions

## Line-Item Instructions

### 1. `scripts/engineering_dispatcher.py` (~22 references)

- [ ] **Line 159**: `name.startswith("complexity:")` → `name.startswith("bug-fixer:complexity:")`
- [ ] **Line 244**: `"--remove-label", "status:in-progress"` → `"--remove-label", "bug-fixer:engineer:in-progress"`
- [ ] **Line 285**: prompt text `status:in-progress` → `bug-fixer:engineer:in-progress`
- [ ] **Line 289**: prompt text `status:awaiting-review` → `bug-fixer:architect:awaiting-review`
- [ ] **Line 302**: `"--remove-label", "status:in-progress"` → `"--remove-label", "bug-fixer:engineer:in-progress"`
- [ ] **Line 326**: `"--label", "status:in-progress"` → `"--label", "bug-fixer:engineer:in-progress"`
- [ ] **Line 342**: comment `needs-iteration` → `bug-fixer:engineer:needs-iteration`
- [ ] **Line 346**: `"--label", "bug-fixer,needs-iteration"` → `"--label", "bug-fixer:engineer:pr,bug-fixer:engineer:needs-iteration"`
- [ ] **Line 353**: log message `needs-iteration` → `bug-fixer:engineer:needs-iteration`
- [ ] **Lines 396-397**: prompt text referencing `needs-iteration` label — update label name in the `gh pr edit --remove-label` instruction
- [ ] **Line 416**: log message `needs-iteration` → `bug-fixer:engineer:needs-iteration`
- [ ] **Lines 428-429**: prompt text referencing `needs-iteration` label — same as 396-397
- [ ] **Line 450**: comment `needs-iteration` → `bug-fixer:engineer:needs-iteration`
- [ ] **Line 456**: `labels_to_restore=["needs-iteration"]` → `labels_to_restore=["bug-fixer:engineer:needs-iteration"]`
- [ ] **Line 470**: `"--label", "auto-fixable"` → `"--label", "bug-fixer:engineer:needs-fix"`
- [ ] **Line 482**: `"status:in-progress"` and `"status:awaiting-review"` in the filter — update both
- [ ] **Line 510**: `"--add-label", "status:in-progress"` → `"--add-label", "bug-fixer:engineer:in-progress"`
- [ ] **Line 532**: prompt text `status:in-progress` → `bug-fixer:engineer:in-progress`
- [ ] **Line 536**: prompt text `status:awaiting-review` → `bug-fixer:architect:awaiting-review`
- [ ] **Line 549**: `"--remove-label", "status:in-progress"` → `"--remove-label", "bug-fixer:engineer:in-progress"`

### 2. `scripts/triage_dispatcher.py` (~9 references)

- [ ] **Lines 23-27**: Update `CLASSIFICATION_LABELS` set:
  ```python
  CLASSIFICATION_LABELS = {
      "bug-fixer:engineer:needs-fix",
      "bug-fixer:human:needs-approach-review",
      "bug-fixer:human:input-needed",
      "bug-fixer:human:project-candidate",
      "bug-fixer:human:required",
  }
  ```
- [ ] **Line 121**: `"status:in-progress"` → `"bug-fixer:engineer:in-progress"` in `SKIP_LABELS`
- [ ] **Line 128**: comment `status:in-progress` → `bug-fixer:engineer:in-progress`
- [ ] **Line 131**: comment `status:awaiting-review` → `bug-fixer:architect:awaiting-review`
- [ ] **Line 133**: `"status:awaiting-review"` → `"bug-fixer:architect:awaiting-review"`

### 3. `scripts/architect_dispatcher.py` (~4 references)

- [ ] **Line 132**: `"--label", "bug-fixer"` → `"--label", "bug-fixer:engineer:pr"`
- [ ] **Line 165**: `"needs-iteration"` and `"architect-approved"` → `"bug-fixer:engineer:needs-iteration"` and `"bug-fixer:architect:approved"`
- [ ] **Line 193**: log message `needs-human-intervention` → `bug-fixer:human:needs-intervention`
- [ ] **Line 195**: `"--add-label", "needs-human-intervention"` → `"--add-label", "bug-fixer:human:needs-intervention"`

### 4. `scripts/trajectory_dispatcher.py` (~3 references)

- [ ] **Line 120**: `"auto-fixable"` → `"bug-fixer:engineer:needs-fix"` (in transcript detection strings)
- [ ] **Line 150**: `"--label", "status:in-progress"` → `"--label", "bug-fixer:engineer:in-progress"`
- [ ] **Line 156**: `"--label", "bug-fixer"` → `"--label", "bug-fixer:engineer:pr"`

### 5. `scripts/liveness.py` (~6 references)

- [ ] **Line 353**: `"status:awaiting-review"` → `"bug-fixer:architect:awaiting-review"` (in jq filter string)
- [ ] **Line 360**: comment `needs-iteration` → `bug-fixer:engineer:needs-iteration`
- [ ] **Line 363**: `"needs-iteration"` → `"bug-fixer:engineer:needs-iteration"` (in jq filter string)
- [ ] **Line 453**: `"--remove-label", "status:in-progress"` → `"--remove-label", "bug-fixer:engineer:in-progress"`
- [ ] **Line 465**: comment `needs-iteration` → `bug-fixer:engineer:needs-iteration`
- [ ] **Line 468**: log/summary text `needs-iteration` → `bug-fixer:engineer:needs-iteration`

## Verification Requirement

After making all changes:
1. Syntax-check all 5 files:
   ```bash
   python3 -m py_compile scripts/engineering_dispatcher.py
   python3 -m py_compile scripts/triage_dispatcher.py
   python3 -m py_compile scripts/architect_dispatcher.py
   python3 -m py_compile scripts/trajectory_dispatcher.py
   python3 -m py_compile scripts/liveness.py
   ```
2. Run tests: `python3 -m unittest discover -s tests -v`
3. Verify only the 5 dispatcher/liveness files are modified: `git diff --name-only`
4. Verify no old label names remain:
   ```bash
   grep -n '"auto-fixable"\|"human-required"\|"needs-approach-review"\|"project-candidate"\|"input-needed"\|"status:in-progress"\|"status:awaiting-review"\|"needs-iteration"\|"architect-approved"\|"needs-human-intervention"\|"discovered-in-review"' scripts/engineering_dispatcher.py scripts/triage_dispatcher.py scripts/architect_dispatcher.py scripts/trajectory_dispatcher.py scripts/liveness.py
   ```
   Also check the bare `"bug-fixer"` label (should NOT appear as a standalone label value, but may appear in other contexts like log prefixes or repo names — use judgment):
   ```bash
   grep -n '"bug-fixer"' scripts/engineering_dispatcher.py scripts/architect_dispatcher.py scripts/trajectory_dispatcher.py
   ```

**CRITICAL:** Before committing, ask the user if they are happy with the changes.

## Issue Surfacing

If you discover a defect, upstream bug, or concern *outside your boundary box*, surface it in your Completion Report below — do not file issues directly.

---

## Completion Report
<!-- Written by the worker at task completion -->

- Successfully updated all 44 label references across the 5 dispatcher and liveness Python scripts to the new `bug-fixer:<actor>:<name>` namespace.
- Preserved existing logic, string substitutions, and program control flow, updating only string literals (including comments, log messages, and prompt variables).
- Verified syntax against all 5 modified scripts with no errors.
- Verified all unit tests (`python3 -m unittest discover -s tests -v`) passed completely with no test regressions.
- Ensured no old label names remained by `grep`ing across the script directories.

Ready for next work order.
