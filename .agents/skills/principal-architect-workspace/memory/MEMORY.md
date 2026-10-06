# Principal Architect Memory
<!-- Last updated: 2026-10-05T22:15:00Z -->

## Active Projects

| Project / PR | Status | Modules Touched | Context Link |
| :--- | :--- | :--- | :--- |
| `pr-plugin-architecture` | **WO0/6** — Planning | daemon-scaffold, hooks, dispatchers, install, consumer, docs | [PROJECT.md](../pr-plugin-architecture/PROJECT.md) |

## Recently Completed (Last 5)

| Project | Date Completed | Key Outcome |
| :--- | :--- | :--- |
| `pr-scanning-infra` | 2026-10-05 | Hardened scanning pipeline — redaction, normalisation, egress gate, Betterleaks backend, injection classifier. PR #47. |
| `pr-label-namespace` | 2026-10-01 | Migrated GitHub label taxonomy to routed-segment namespace. |
| `pr-architect-review-loop` | 2026-09-29 | Implemented stateless, context-cached Architect Review Loop sidecar & skills. |

*Note: Once this table exceeds 5 entries, graduate the oldest to `PROJECT_INDEX.md`.*

## Known Modules

*   `config`: Plugin configuration (`config.json`, workspace config).
*   `install`: Installation scripts (`scripts/install.sh`).
*   `setup`: Target-repo provisioning (`scripts/enroll.sh`).
*   `dispatchers`: Core Python sidecar logic (`scripts/*_dispatcher.py`).
*   `liveness`: Health monitoring (`scripts/liveness.py`).
*   `skills`: Agent system prompts (`skills/*/SKILL.md`).
*   `evals`: Skill evaluation criteria (`skills/*/EVAL.md`).
*   `rules`: Agent operating rules (`rules/`, `.agents/rules/`).
*   `docs`: Documentation (`README.md`, `docs/`).
*   `tests`: Test suite (`tests/`).
*   `safe_ingest`: Security ingestion gate (`scripts/safe_ingest.py`).
*   `pre_tool_gate`: PreToolUse gate & leak scanner (`scripts/pre_tool_gate.py`).
*   `integrity_monitor`: Pre/post integrity checking (`scripts/integrity_monitor.py`).
*   `daemon-scaffold`: Global daemon plugin structure (`daemon/`).
