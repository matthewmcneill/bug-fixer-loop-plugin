---
name: label-namespace-wo1
description: Handoff instructions for Work Order 1 — Config & Install Defaults
complexity: standard
---

# WO1 — Config & Install Defaults

**Parent Project:** `pr-label-namespace` ([PROJECT.md](../PROJECT.md))
**Branch:** `label-namespace/wo1-config-install` (base: `main`)

---

## Role Definition

> You are an expert senior software engineer and execution-focused worker agent. You are NOT the Principal Architect. Do not make high-level architectural decisions, do not create new branches, and do not plan. Your task is strictly limited to executing the checklist below.

## Objective

Update all label string literals in configuration files and install script defaults to use the new `bug-fixer:<actor>:<name>` routed-segment namespace. This WO establishes the foundational configuration that all subsequent WOs depend on.

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
| `complexity:mechanical` | `bug-fixer:complexity:mechanical` |
| `complexity:standard` | `bug-fixer:complexity:standard` |
| `complexity:architect` | `bug-fixer:complexity:architect` |
| `batch:<slug>` | `bug-fixer:batch:<slug>` |

## Strict Boundary Box

- **DO NOT** touch any files in `scripts/` other than `scripts/install.sh`
- **DO NOT** touch any files in `skills/`, `rules/`, `docs/`, or `tests/`
- **DO NOT** modify `scripts/safe_ingest.py` — its labels (`security:prompt-injection`, priority labels) are NOT being renamed
- **DO NOT** update historical workorders in `.agents/skills/principal-architect-workspace/pr-bug-fixer-plugin/` or `pr-architect-review-loop/`
- **DO NOT** rename or move any files
- **DO** keep the `required_labels` array complete — it must include ALL labels that `configure.sh` needs to provision on target repos. See the expanded list in the instructions below.

## Tooling Constraints

- Run `python3 -c "import json; json.load(open('config.json'))"` after editing config.json to validate JSON syntax
- Run `python3 -c "import json; json.load(open('.agents/plugins/bug-fixer-loop-workspace/config.json'))"` after editing workspace config to validate JSON syntax
- Run `bash -n scripts/install.sh` to syntax-check the shell script

## Line-Item Instructions

### 1. `config.json` (root)

- [ ] Update `required_labels` array — replace each label with its new name per the mapping table above.
- [ ] **Expand `required_labels`** to include labels that were previously missing but should be provisioned. The complete list should be:
  ```json
  "required_labels": [
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
- [ ] All other fields (`target_repo`, `repo_dir`, `worktree_path`, `maintainer`, `spec_file_path`, `test_command`, `lint_command`) remain **unchanged**.

### 2. `.agents/plugins/bug-fixer-loop-workspace/config.json`

- [ ] Update `exclusion_labels` array:
  - `"human-required"` → `"bug-fixer:human:required"` 
  - `"P1-blocker"` stays unchanged (not bug-fixer owned)
  - `"hardware-required"` stays unchanged (not bug-fixer owned)
- [ ] Update `auto_fixable_labels` array:
  - `"auto-fixable"` → `"bug-fixer:engineer:needs-fix"`
- [ ] Update `required_labels` array — same expanded list as `config.json` above.
- [ ] All other fields remain **unchanged**.

### 3. `scripts/install.sh` (~lines 155-192)

- [ ] Update the `DEFAULTS` Python dict (the `exclusion_labels`, `auto_fixable_labels`, and `required_labels` keys) to match the exact same values as in the workspace config above.
- [ ] Specifically:
  - Line 170: `"exclusion_labels": ["human-required", ...]` → `["bug-fixer:human:required", "P1-blocker", "hardware-required"]`
  - Line 171: `"auto_fixable_labels": ["auto-fixable"]` → `["bug-fixer:engineer:needs-fix"]`
  - Lines 172-176: `"required_labels": [...]` → the full expanded list
- [ ] All other keys in the DEFAULTS dict remain **unchanged**.

### 4. `matthewmcneill_bug-fixer-loop-plugin_triage_preview.json`

- [ ] Update line containing `"tier": "T1 auto-fixable"` → `"tier": "T1 engineer:needs-fix"`

## Verification Requirement

After making all changes:
1. Validate JSON syntax: `python3 -c "import json; json.load(open('config.json'))"`
2. Validate JSON syntax: `python3 -c "import json; json.load(open('.agents/plugins/bug-fixer-loop-workspace/config.json'))"`
3. Validate shell syntax: `bash -n scripts/install.sh`
4. Run the test suite: `python3 -m unittest discover -s tests -v`
5. Verify no accidental changes to files outside the boundary box: `git diff --name-only`

**CRITICAL:** Before committing, ask the user if they are happy with the changes.

## Issue Surfacing

If you discover a defect, upstream bug, or concern *outside your boundary box*, surface it in your Completion Report below — do not file issues directly. The Principal Architect will triage and file them under the correct author identity.

---

## Completion Report
<!-- Written by the worker at task completion -->

*(To be filled in by the worker upon completion)*
**Status:** ✅ Completed
**Summary of Changes:**
- `config.json`: Updated `required_labels` to match the new `bug-fixer:<actor>:<name>` namespace.
- `.agents/plugins/bug-fixer-loop-workspace/config.json`: Updated `exclusion_labels`, `auto_fixable_labels`, and `required_labels`.
- `scripts/install.sh`: Updated `DEFAULTS` dictionary for all three label configurations.
- `matthewmcneill_bug-fixer-loop-plugin_triage_preview.json`: Updated `tier` to `T1 engineer:needs-fix`.

**Validation Performed:**
- JSON syntax validation for both config files passed.
- Shell script syntax validation passed.
- All 44 Python tests passed.
- Boundary box verified.

**Issues Surfaced:**
- A regex pattern in `test_safe_ingest.py` triggered a warning (`WARNING: Invalid leak pattern regex '[invalid(regex': unterminated character set at position 0`) during test execution. This is outside the boundary box but is noted for future fix.
