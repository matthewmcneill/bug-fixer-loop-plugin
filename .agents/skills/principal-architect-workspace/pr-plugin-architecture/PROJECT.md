# Plugin Architecture Migration — PROJECT.md

**Base Branch:** `scanning-infra/wo5-injection-classifier`
**PR Title:** `[Plugin Architecture]: Split daemon + workspace plugin with per-repo sidecars`

## Architectural Context

The bug-fixer-loop-plugin currently uses a hacked architecture where:
- Hooks are injected into `.agents/hooks.json` (bypassing plugin enablement gate)
- 4 standalone sidecars per repo are installed to `~/.gemini/config/sidecars/` (8 for 2 repos)
- The plugin ships `"disabled": true` in `plugin.json` (deliberate but prevents hook loading)
- Sidecar templates in `sidecars/` contain `agent_permissions.workspace_uris`
- Config resolution uses relative paths from `__file__` or `cwd` that break when moved

The target architecture follows the "Split Daemon + Workspace Plugin" pattern documented
in `repo-memory-plugin/docs/antigravity-plugin-architecture.md` and refined during
this design review (see `final-architecture.md` in brain session `6521d39f`):

1. **Global daemon plugin** (`bug-fixer-daemon`) — sidecars only, zero workspace overhead
2. **Shared workspace plugin** (`bug-fixer`) — hooks, skills, rules, agents. Loaded per-repo via `.agents/plugins.json`
3. **Per-repo sidecars** with `agent_permissions` — one sidecar per enrolled repo (N not 4×N)
4. **`tick.py` + `tick_receiver.py`** — minimal global heartbeat, all logic in workspace plugin
5. **Sidecar folders as registry** — no separate `registry.json`

## Status

- **Human approval of decomposition:** ⏳ PENDING
- **Current WO:** Planning

## Work Order Ledger

- **[ ] [wo0-spike](wo0-spike/WORKORDER.md):** Phase 0 spike — validate undocumented Antigravity behaviors with throwaway plugin (`interactive`)
- **[ ] [wo1-daemon-scaffold](wo1-daemon-scaffold/WORKORDER.md):** Create global daemon plugin structure with workspace plugin bundle (hooks, skills, rules, agents)
- **[ ] [wo2-hook-config-resolution](wo2-hook-config-resolution/WORKORDER.md):** Update `pre_tool_gate.py` and `github_url_gate.py` to resolve config via `workspacePaths` + sidecars scan
- **[ ] [wo3-tick-dispatchers](wo3-tick-dispatchers/WORKORDER.md):** Create `tick.py`, `tick_receiver.py`, adapt dispatchers to read repo from env vars
- **[ ] [wo4-install-enroll](wo4-install-enroll/WORKORDER.md):** Rewrite `install.sh` as `install` + `enroll`/`unenroll`/`enable`/`disable` commands
- **[ ] [wo5-consumer-cleanup](wo5-consumer-cleanup/WORKORDER.md):** Remove submodule, injected hooks, old sidecar stubs from esp32-app-foundation
- **[ ] [wo6-cleanup-docs-tests](wo6-cleanup-docs-tests/WORKORDER.md):** Delete legacy files, update README, add/update tests, uninstall script

## Concurrency & Conflict Analysis

All WOs are **strictly serialised** (stacked). Heavy overlap across files:

| File | WOs touching it |
|------|-----------------|
| `scripts/pre_tool_gate.py` | WO2 |
| `scripts/github_url_gate.py` | WO2 |
| `scripts/*_dispatcher.py` (×4) | WO3 |
| `scripts/install.sh` | WO4 |
| `scripts/configure.sh` | WO4 |
| `scripts/uninstall.sh` | WO6 |
| `plugin.json` | WO1 |
| `hooks.json.template` | WO6 (delete) |
| `sidecars/*/sidecar.json` | WO1 (restructure) |

**No parallel execution — strict sequential stacking.**

WO0 (spike) is `interactive` — not spawned as a subagent.

## Modules & Components

| Module | Files | WOs |
|--------|-------|-----|
| `daemon-scaffold` | `plugin.json`, workspace-plugin structure | WO1 |
| `hooks` | `hooks.json`, `pre_tool_gate.py`, `github_url_gate.py` | WO1, WO2 |
| `dispatchers` | `*_dispatcher.py`, `tick.py`, `tick_receiver.py` | WO3 |
| `install` | `install.sh`, `configure.sh`, `uninstall.sh` | WO4, WO6 |
| `consumer` | `esp32-app-foundation/.agents/` | WO5 |
| `tests` | `tests/test_*.py` | WO2, WO3, WO6 |
| `docs` | `README.md` | WO6 |

## Session Log

### Session 1 — 2026-10-05 — Architecture Design & Planning
- Audited current plugin architecture: hooks injected outside plugin, sidecars standalone, `disabled: true`
- Identified root cause: `agent_permissions.workspace_uris` on sidecar templates + deliberate `"disabled": true`
- Designed split daemon + workspace plugin pattern aligned with repo-memory architecture
- Second opinion review identified 9 corrections (all accepted)
- Refined to per-repo sidecars with tick.py + tick_receiver.py internal cron
- Eliminated external registry — sidecar folders are definitive source
- Adopted command naming: install/uninstall (machine), enroll/unenroll (repo), enable/disable (toggle)
- Phase 0 spike defined (8 checks for undocumented behaviors)
- Decomposition created, awaiting human approval
