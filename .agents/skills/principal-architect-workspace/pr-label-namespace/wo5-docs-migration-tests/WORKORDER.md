---
name: label-namespace-wo5
description: Handoff instructions for Work Order 5 — Docs, Migration & Tests
complexity: standard
---

# WO5 — Docs, Migration & Tests

**Parent Project:** `pr-label-namespace` ([PROJECT.md](../PROJECT.md))
**Branch:** `label-namespace/wo5-docs-migration-tests` (base: `label-namespace/wo4-skills-rules`)

---

## Role Definition

> You are an expert senior software engineer and execution-focused worker agent. You are NOT the Principal Architect. Do not make high-level architectural decisions, do not create new branches, and do not plan. Your task is strictly limited to executing the checklist below.

## Objective

Update documentation (README, plugin-architecture), verify/finalise the new design doc (`docs/label-taxonomy.md`), create a migration utility script for existing target repos, and verify the test suite passes.

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

## Strict Boundary Box

- **ONLY** modify/create these files:
  1. `README.md`
  2. `docs/plugin-architecture.md`
  3. `docs/label-taxonomy.md` (already exists — verify accuracy, update if needed)
  4. `scripts/migrate_labels.sh` (NEW — create this file)
- **DO NOT** touch config files, dispatchers, skills, rules, or install/configure scripts
- **DO NOT** modify `tests/test_safe_ingest.py` — it references `bug-fixer` only as a repo slug, not a label
- **DO NOT** touch historical workorders

## Tooling Constraints

- Run `bash -n scripts/migrate_labels.sh` to syntax-check the new migration script
- Run `python3 -m unittest discover -s tests -v` to verify no test regressions

## Line-Item Instructions

### 1. `README.md` (~11 references)

Key areas to update:
- [ ] **Line 68** (mermaid diagram): `auto-fixable vs human-required` in the TriageSidecar node → update to new label names
- [ ] **Lines 76-77** (mermaid diagram): `Labels auto-fixable` and `Labels human-required` edge text → update
- [ ] **Line 99**: prose `auto-fixable` and `human-required` → new names
- [ ] **Line 107**: prose `auto-fixable` → `bug-fixer:engineer:needs-fix`
- [ ] **Line 118**: `needs-human-intervention` → `bug-fixer:human:needs-intervention`
- [ ] **Lines 223-225** (config table): Update `exclusion_labels`, `auto_fixable_labels`, and `required_labels` example values to new names
- [ ] **Line 260**: prose `human-required` → `bug-fixer:human:required`

### 2. `docs/plugin-architecture.md` (~6 references)

Key areas to update:
- [ ] **Line 458**: `auto-fixable` in sidecar description → `bug-fixer:engineer:needs-fix`
- [ ] **Lines 490-491**: `auto-fixable` and `human-required` in issue eligibility description → new names
- [ ] **Line 496**: `status:in-progress` → `bug-fixer:engineer:in-progress`
- [ ] **Line 554**: `status:in-progress` → `bug-fixer:engineer:in-progress`
- [ ] **Line 575**: `status:in-progress` → `bug-fixer:engineer:in-progress`

### 3. `docs/label-taxonomy.md` — Verify & Update

This file was created earlier as a design reference. Now that all WOs are (nearly) complete:
- [ ] **Verify accuracy** of the label registry, state machine descriptions, and dispatcher routing table against the actual code changes made in WO1-WO4.
- [ ] **Update if needed** — fix any discrepancies found.
- [ ] **No structural changes** — the document's organisation is correct.

### 4. `scripts/migrate_labels.sh` (NEW)

Create a shell script that renames labels on an existing target repo. Requirements:

- [ ] Accept target repo as argument: `./scripts/migrate_labels.sh <owner/repo>`
- [ ] Use `gh label create --force` for each rename (this renames in-place, preserving existing issues)
- [ ] Include a `--dry-run` flag that lists what would be renamed without executing
- [ ] Include the complete mapping from old → new label names
- [ ] Add colour and description for each new label (matching `LABELS_META` from `configure.sh`)
- [ ] After renaming, optionally delete orphaned old labels that `gh label create --force` doesn't clean up (if the old label name differs from the new one by more than case, a new label is created alongside the old — the old one must be explicitly deleted)
- [ ] Include header comments explaining purpose and usage
- [ ] Make the script executable: add proper shebang `#!/usr/bin/env bash` and set `set -euo pipefail`

Example structure:
```bash
#!/usr/bin/env bash
set -euo pipefail

# migrate_labels.sh — Rename bug-fixer labels on a target repo
# Usage: ./scripts/migrate_labels.sh <owner/repo> [--dry-run]
#
# This script renames old-style bug-fixer labels to the new
# bug-fixer:<actor>:<name> routed-segment namespace.
# Uses `gh label create --force` which renames in-place.

TARGET_REPO="${1:?Usage: migrate_labels.sh <owner/repo> [--dry-run]}"
DRY_RUN=false
[[ "${2:-}" == "--dry-run" ]] && DRY_RUN=true

declare -A RENAME_MAP=(
  ["auto-fixable"]="bug-fixer:engineer:needs-fix"
  ["human-required"]="bug-fixer:human:required"
  # ... all 18 mappings
)

declare -A LABELS_META=(
  ["bug-fixer:engineer:needs-fix"]="0E8A16:Issues eligible for autonomous fixing"
  # ... colours and descriptions for all labels
)

for old_label in "${!RENAME_MAP[@]}"; do
  new_label="${RENAME_MAP[$old_label]}"
  meta="${LABELS_META[$new_label]:-ededed:Managed by bug-fixer-loop}"
  color="${meta%%:*}"
  description="${meta#*:}"

  if [[ "$DRY_RUN" == "true" ]]; then
    echo "[DRY RUN] Would rename: '${old_label}' → '${new_label}' (${color})"
  else
    echo "Renaming: '${old_label}' → '${new_label}'"
    # Create new label (in-place if same name, or creates new one)
    gh label create "${new_label}" --repo "${TARGET_REPO}" --force \
      --color "${color}" --description "${description}" 2>/dev/null || true
    # Delete the old label if it differs from the new one
    gh label delete "${old_label}" --repo "${TARGET_REPO}" --yes 2>/dev/null || true
  fi
done

echo ""
echo "Migration complete. Verify at: https://github.com/${TARGET_REPO}/labels"
```

## Verification Requirement

After making all changes:
1. Syntax-check migration script: `bash -n scripts/migrate_labels.sh`
2. Run tests: `python3 -m unittest discover -s tests -v`
3. Verify only target files are modified/created: `git diff --name-only` and `git ls-files --others --exclude-standard`
4. Verify no old label names remain in README.md and docs/plugin-architecture.md:
   ```bash
   grep -n '"auto-fixable"\|"human-required"\|"needs-approach-review"\|"project-candidate"\|"input-needed"\|"status:in-progress"\|"status:awaiting-review"\|"bug-fixer-health"\|"spec-change-request"\|"needs-iteration"\|"architect-approved"\|"discovered-in-review"\|"needs-human-intervention"' README.md docs/plugin-architecture.md
   ```

**CRITICAL:** Before committing, ask the user if they are happy with the changes.

## Issue Surfacing

If you discover a defect, upstream bug, or concern *outside your boundary box*, surface it in your Completion Report below — do not file issues directly.

---

## Completion Report
<!-- Written by the worker at task completion -->

### Summary
- Updated `README.md` and `docs/plugin-architecture.md` with new `bug-fixer:` label namespaces.
- Verified `docs/label-taxonomy.md` (no structural changes needed as it was perfectly accurate).
- Created `scripts/migrate_labels.sh` using GitHub CLI (`gh`) to forcibly rename in-place, handling dry runs and cleaning up orphaned mismatched labels.
- Verified syntax, executed test suite (44 tests passed successfully), and ensured no old string literals remain in the docs.

### Discovered Issues / External Concerns
- None discovered. All execution constraints were met safely within the boundary box.
