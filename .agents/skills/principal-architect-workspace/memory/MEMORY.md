# Principal Architect Memory
<!-- Last updated: 2026-10-01T19:10:00Z -->

## Active Projects

| Project / PR | Status | Modules Touched | Context Link |
| :--- | :--- | :--- | :--- |
| `pr-label-namespace` | **5/5 COMPLETE** — ready for PR | config, install, setup, dispatchers, liveness, skills, evals, rules, docs, tests, migration | [PROJECT.md](../pr-label-namespace/PROJECT.md) |

## Recently Completed (Last 5)

| Project | Date Completed | Key Outcome |
| :--- | :--- | :--- |
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
