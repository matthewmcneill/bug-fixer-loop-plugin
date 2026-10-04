# Principal Architect Memory
<!-- Last updated: 2026-10-04T19:35:00Z -->

## Active Programs
| Program | Plan | Status |
|---|---|---|

## Active Projects

| Project / PR | Status | Modules Touched | Context Link |
| :--- | :--- | :--- | :--- |
| `pr-scanning-infra` | **7/7 COMPLETE** — [PR #47](https://github.com/matthewmcneill/bug-fixer-loop-plugin/pull/47) (replaces #46) | pre_tool_gate, safe_ingest, config, tests, injection_scanner, triage_dispatcher | [PROJECT.md](../pr-scanning-infra/PROJECT.md) |

## Recently Completed (Last 5)

| Project | Date Completed | Key Outcome |
| :--- | :--- | :--- |
| `pr-label-namespace` | 2026-10-01 | Migrated GitHub label taxonomy to routed-segment namespace. |
| `pr-architect-review-loop` | 2026-09-29 | Implemented stateless, context-cached Architect Review Loop sidecar & skills. |

*Note: Once this table exceeds 5 entries, graduate the oldest to `PROJECT_INDEX.md`.*

## Known Modules

*   `config`: Plugin configuration (`config.json`, workspace config).
*   `install`: Installation scripts (`scripts/install.sh`).
*   `setup`: Target-repo provisioning (`scripts/configure.sh`).
*   `dispatchers`: Core Python sidecar logic (`scripts/*_dispatcher.py`).
*   `liveness`: Health monitoring (`scripts/liveness.py`).
*   `skills`: Agent system prompts (`skills/*/SKILL.md`).
*   `evals`: Skill evaluation criteria (`skills/*/EVAL.md`).
*   `rules`: Agent operating rules (`rules/`, `.agents/rules/`).
*   `docs`: Documentation (`README.md`, `docs/`).
*   `tests`: Test suite (`tests/`).
*   `safe_ingest`: Security ingestion gate (`scripts/safe_ingest.py`).
*   `pre_tool_gate`: PreToolUse gate & leak scanner (`scripts/pre_tool_gate.py`).
