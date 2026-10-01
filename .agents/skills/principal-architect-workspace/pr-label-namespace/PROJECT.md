# PROJECT: Label Namespace Migration (`bug-fixer:` routed segments)

**Issue:** [#12 — Rename all bug-fixer tags to use "bug-fixer:" prefix](https://github.com/matthewmcneill/bug-fixer-loop-plugin/issues/12)
**Base Branch:** `main`
**Stack Head:** `label-namespace/wo5-docs-migration-tests` (when complete)
**Design Reference:** [`docs/label-taxonomy.md`](../../../docs/label-taxonomy.md)

## Objective

Rename all GitHub labels owned by the bug-fixer system to use a `bug-fixer:<actor>:<name>` routed-segment namespace, where the middle segment indicates **whose court the ball is in** (consumer/owner of the action). This makes the system's routing model transparent at a glance and enables powerful dashboard filtering.

## Architectural Decisions

### 1. Namespace Schema — Routed Segments

```
bug-fixer:<segment>:<name>
```

| Segment | Actor | Meaning |
|---|---|---|
| `engineer` | Engineering dispatcher | Engineering loop should act / is acting |
| `architect` | Architect dispatcher | Architect loop should act / has decided |
| `human` | Human operator | Requires human attention |
| `triage` | Triage dispatcher | Needs (re-)classification |
| `system` | System monitoring | System-level alerts |
| `complexity` | Model router | Model tier metadata (no actor) |
| `batch` | Bundle affinity | PR grouping metadata (no actor) |

### 2. Labels Explicitly Excluded from Rename

| Label(s) | Why |
|---|---|
| `security:prompt-injection` | Cross-system security taxonomy, not bug-fixer-owned |
| `priority:*`, `p0`–`p3`, `urgent`/`high`/`medium`/`low` | Target-repo human conventions |
| `hardware-required`, `P1-blocker` | Target-repo exclusion signals |
| `S`/`L`/`XL`-effort heuristics | Human-applied effort estimates |
| `component:*`, `P2-correctness`, etc. | Plugin-repo internal tracking |

### 3. Backward Compatibility Strategy

**Hard cutover.** `gh label create --force` renames in-place — open issues retain labels. Migration script handles existing target repos.

### 4. Historical Workorders

Archival workorders in `pr-bug-fixer-plugin/` and `pr-architect-review-loop/` will NOT be updated — they accurately reflect the system state when written.

---

## Canonical Label Mapping

> Single source of truth for the rename. All WOs reference this table.

| # | Old Label | New Label | Actor Segment |
|:---:|---|---|---|
| 1 | `auto-fixable` | `bug-fixer:engineer:needs-fix` | `engineer` |
| 2 | `status:in-progress` | `bug-fixer:engineer:in-progress` | `engineer` |
| 3 | `needs-iteration` | `bug-fixer:engineer:needs-iteration` | `engineer` |
| 4 | `bug-fixer` | `bug-fixer:engineer:pr` | `engineer` |
| 5 | `status:awaiting-review` | `bug-fixer:architect:awaiting-review` | `architect` |
| 6 | `architect-approved` | `bug-fixer:architect:approved` | `architect` |
| 7 | `human-required` | `bug-fixer:human:required` | `human` |
| 8 | `needs-approach-review` | `bug-fixer:human:needs-approach-review` | `human` |
| 9 | `input-needed` | `bug-fixer:human:input-needed` | `human` |
| 10 | `project-candidate` | `bug-fixer:human:project-candidate` | `human` |
| 11 | `needs-human-intervention` | `bug-fixer:human:needs-intervention` | `human` |
| 12 | `spec-change-request` | `bug-fixer:human:spec-change-request` | `human` |
| 13 | `discovered-in-review` | `bug-fixer:triage:discovered-in-review` | `triage` |
| 14 | `bug-fixer-health` | `bug-fixer:system:health` | `system` |
| 15 | `complexity:mechanical` | `bug-fixer:complexity:mechanical` | *(metadata)* |
| 16 | `complexity:standard` | `bug-fixer:complexity:standard` | *(metadata)* |
| 17 | `complexity:architect` | `bug-fixer:complexity:architect` | *(metadata)* |
| 18 | `batch:<slug>` | `bug-fixer:batch:<slug>` | *(metadata)* |

---

## Work Order Decomposition

### Ledger

| Status | WO | Topic | Complexity | Files | Est. Lines | Dependencies |
|---|---|---|---|---|---|---|
| [x] | [WO1 — Config & Install Defaults](wo1-config-install/WORKORDER.md) | Update config.json schemas and install.sh defaults | `standard` | 3 | ~35 | — |
| [x] | [WO2 — Setup & Provisioning](wo2-setup-provisioning/WORKORDER.md) | Update configure.sh label definitions and provisioning | `standard` | 1 | ~51 | WO1 |
| [x] | [WO3 — Dispatchers & Runtime](wo3-dispatchers-runtime/WORKORDER.md) | Update all dispatchers and liveness.py | `standard` | 5 | ~44 | WO2 |
| [x] | [WO4 — Skills, Rules & Prompts](wo4-skills-rules/WORKORDER.md) | Update all skill SKILL.md, EVAL.md, and rules files | `standard` | 9 | ~138 | WO3 |
| [x] | [WO5 — Docs, Migration & Tests](wo5-docs-migration-tests/WORKORDER.md) | Update README/docs, create migration utility, update tests, finalise label-taxonomy.md | `standard` | 4+ | ~26+ | WO4 |

### Dependency Chain (Linear Stack)

```
main
  └── label-namespace/wo1-config-install
        └── label-namespace/wo2-setup-provisioning
              └── label-namespace/wo3-dispatchers-runtime
                    └── label-namespace/wo4-skills-rules
                          └── label-namespace/wo5-docs-migration-tests  ← PR head
```

### Concurrency Analysis

All WOs touch different file groups with zero overlap. The stacked-branch model requires serialization. **Recommendation: serialize (Option A).**

---

## Modules & Components

| Module | Files | Touched By |
|---|---|---|
| `config` | `config.json`, `.agents/plugins/bug-fixer-loop-workspace/config.json` | WO1 |
| `install` | `scripts/install.sh` | WO1 |
| `setup` | `scripts/configure.sh` | WO2 |
| `dispatchers` | `scripts/engineering_dispatcher.py`, `scripts/triage_dispatcher.py`, `scripts/architect_dispatcher.py`, `scripts/trajectory_dispatcher.py` | WO3 |
| `liveness` | `scripts/liveness.py` | WO3 |
| `skills` | `skills/bug-fixer-engineering/SKILL.md`, `skills/bug-fixer-triage/SKILL.md`, `skills/architect-review/SKILL.md`, `skills/bug-fixer/SKILL.md`, `skills/bug-fixer-trajectory-review/SKILL.md` | WO4 |
| `evals` | `skills/bug-fixer-engineering/EVAL.md`, `skills/bug-fixer-triage/EVAL.md` | WO4 |
| `rules` | `rules/AGENTS.md`, `.agents/rules/bug-fixer-AGENTS.md` | WO4 |
| `docs` | `README.md`, `docs/plugin-architecture.md`, `docs/label-taxonomy.md` | WO5 |
| `tests` | `tests/test_dispatcher_models.py` | WO5 |
| `migration` | `scripts/migrate_labels.sh` (new) | WO5 |

---

## Approval Status

> **✅ APPROVED by human operator at 2026-10-01T20:45:04+01:00.**
> Decomposition, namespace schema (Option B — routed segments), and `auto-fixable` → `engineer:needs-fix` rename all approved.

---

## Session Log
<!-- Progressively enriched by the PA as WOs complete -->

*(No entries yet — project not started)*

### 2026-10-01T21:08Z — WO1 Complete: Config & Install Defaults
**Summary**: Updated label strings in `config.json`, workspace config, `install.sh` defaults, and triage preview JSON. Expanded `required_labels` from 9 to 14 entries to include previously unprovisioned labels (architect review, iteration, discovery).
**Cross-cutting decisions**: None — purely mechanical substitution.
**Surfaced findings**: Worker noted `MEMORY.md` was modified in working tree (expected — PA updated it pre-spawn).
**Dead ends**: None.

### 2026-10-01T21:14Z — WO2 Complete: Setup & Provisioning
**Summary**: Updated all 6 sections of `configure.sh`: LABELS_META (14 entries with colours/descriptions), REQUIRED_LABELS array, removed standalone `discovered-in-review` creation (folded into main loop), config write-back Python block, dry-run classifier, and label-apply logic.
**Cross-cutting decisions**: Expanded LABELS_META to include all 14 labels with appropriate colours — previously only 9 were defined and `discovered-in-review` was handled separately.
**Surfaced findings**: None.
**Dead ends**: None.

### 2026-10-01T21:18Z — WO3 Complete: Dispatchers & Runtime
**Summary**: Updated 44 label references across 5 runtime Python files — `engineering_dispatcher.py` (22), `triage_dispatcher.py` (9), `architect_dispatcher.py` (4), `trajectory_dispatcher.py` (3), `liveness.py` (6). All syntax checks pass, all 44 tests pass.
**Cross-cutting decisions**: None — mechanical string substitution.
**Surfaced findings**: None.
**Dead ends**: None.
