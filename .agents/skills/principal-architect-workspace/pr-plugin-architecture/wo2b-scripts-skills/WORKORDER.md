---
name: plugin-architecture-wo2b
description: Handoff instructions for WO2b — fix other scripts' config/path resolution
complexity: standard
---

# WO2b — Other Scripts Config + Path Fixes

> You are an expert senior software engineer and execution-focused worker agent. You are NOT the Principal Architect. Do not make high-level architectural decisions, do not branch, and do not plan. Your task is strictly limited to executing the checklist below.

## Objective

Fix hardcoded `bug-fixer-loop-workspace` paths and sibling-vs-nested layout bugs in `safe_ingest.py`, `safe_post.py`, `injection_scanner.py`, `integrity_monitor.py`, and `bugfixer_api.py`. Keep backward compatibility via `BUG_FIXER_CONFIG` env var (set by enroll).

## Strict Boundary Box

- **IN SCOPE:** `daemon/workspace-plugin/bug-fixer/scripts/safe_ingest.py`, `safe_post.py`, `injection_scanner.py`, `integrity_monitor.py`, `bugfixer_api.py`
- **DO NOT** modify `pre_tool_gate.py` or `github_url_gate.py` (WO2a done)
- **DO NOT** modify dispatcher scripts (WO3)
- **DO NOT** modify shell scripts (WO4)
- **DO NOT** delete any files

## Context

After WO1, all `.py` scripts are at `daemon/workspace-plugin/bug-fixer/scripts/`. After WO2a, `pre_tool_gate.py` and `github_url_gate.py` are updated. The remaining scripts still have hardcoded paths that reference the old `.agents/plugins/bug-fixer-loop-workspace/` layout.

The new sidecar layout puts per-repo config at `daemon/sidecars/<repo>/config.json` with env var `BUG_FIXER_CONFIG` pointing to it. These scripts should:
1. First check `BUG_FIXER_CONFIG` env var
2. Fall back to legacy `.agents/plugins/bug-fixer-loop-workspace/config.json` paths
3. Use `_find_daemon_dir()` from `pre_tool_gate.py` (imported) for install-dir-relative paths

## Line-Item Instructions

### 1. Fix `integrity_monitor.py`

**Bug at L36:** `workspace = plugin_dir / ".agents" / "plugins" / "bug-fixer-loop-workspace"` — this is a nested lookup that will never resolve correctly.

**Bug at L71:** `SNAPSHOT_FILE = Path(__file__).resolve().parent.parent / ".agents" / "plugins" / "bug-fixer-loop-workspace" / ".integrity_snapshot.json"` — same nested bug.

**Fix:**
- [ ] Change snapshot storage to use `BUG_FIXER_CONFIG`'s parent directory (the sidecar dir):
  ```python
  def _get_snapshot_file() -> Path:
      """Snapshot file lives alongside the config in the sidecar dir."""
      config_env = os.environ.get("BUG_FIXER_CONFIG", "")
      if config_env and Path(config_env).is_file():
          return Path(config_env).resolve().parent / ".integrity_snapshot.json"
      # Legacy fallback
      return Path(__file__).resolve().parent.parent / ".integrity_snapshot.json"
  ```
- [ ] Remove the git-checkout revert code that assumed an install-dir git repo. Replace with snapshot-based revert: save a copy of protected files to `.integrity_snapshot/` (directory, not the `.json` file) and restore from there if changed.
- [ ] Keep legacy `bug-fixer-loop-workspace` fallback path for backward compat.

### 2. Fix `safe_post.py`

**Bug at L37:** `WORKSPACE_DIR = PLUGIN_DIR / ".agents" / "plugins" / "bug-fixer-loop-workspace"` — nested lookup, wrong.

**Bug at L66:** Same pattern in `_candidate_configs()`.

**Fix:**
- [ ] Update config resolution to check `BUG_FIXER_CONFIG` env first:
  ```python
  def _candidate_configs() -> list[Path]:
      """Config search order: env → sidecar scan → legacy."""
      candidates = []
      config_env = os.environ.get("BUG_FIXER_CONFIG", "")
      if config_env:
          candidates.append(Path(config_env))
      # Legacy paths (backward compat)
      script_dir = Path(__file__).resolve().parent
      plugin_dir = script_dir.parent
      candidates.append(plugin_dir / "config.json")  # co-located (old workspace layout)
      for top in (plugin_dir.parent,):
          workspace = top / "bug-fixer-loop-workspace" / "config.json"
          candidates.append(workspace)
      candidates.append(Path.cwd() / ".agents" / "plugins" / "bug-fixer-loop-workspace" / "config.json")
      return candidates
  ```

### 3. Fix `injection_scanner.py`

**Bug at L214-216:** Hardcoded `.agents/plugins/bug-fixer-loop-workspace` nested path.

**Fix:**
- [ ] Cache file location from `BUG_FIXER_CONFIG`'s parent:
  ```python
  def _get_cache_path(config_path: str | None = None) -> Path:
      config_env = os.environ.get("BUG_FIXER_CONFIG", "")
      if config_env and Path(config_env).is_file():
          return Path(config_env).resolve().parent / "injection_cache.json"
      if config_path and Path(config_path).is_file():
          return Path(config_path).resolve().parent / "injection_cache.json"
      # Legacy fallback
      return Path(__file__).resolve().parent.parent / "injection_cache.json"
  ```
- [ ] Update the `CachedInjectionScanner` to use `_get_cache_path()` instead of hardcoded path.

### 4. Fix `safe_ingest.py`

**Bug at L194-200:** Hardcoded config search with old workspace path.

**Fix:**
- [ ] Update `load_config()` (safe_ingest's own version, ~L180-219) to check `BUG_FIXER_CONFIG` env first, then legacy paths. Preserve `normalise_config()` call. Add `require=True` parameter for callers that need fail-closed behavior:
  ```python
  def load_config(config_path=None, require=False):
      """Load config, checking BUG_FIXER_CONFIG env first."""
      if config_path and Path(config_path).is_file():
          # explicit path
          ...
      env_path = os.environ.get("BUG_FIXER_CONFIG", "")
      if env_path and Path(env_path).is_file():
          # env var path
          ...
      # Legacy search paths
      ...
      if require:
          raise FileNotFoundError("No bug-fixer config found")
      return _defaults()
  ```

### 5. Fix `bugfixer_api.py`

**Bug at L75:** `_WORKSPACE_NAME = "bug-fixer-loop-workspace"` used to find dispatches.

**Fix:**
- [ ] Dispatches now live at `state/dispatches/`. The `BUG_FIXER_STATE_DIR` env var points to the state dir. Update dispatch lookup:
  ```python
  def _find_dispatches_dir() -> Path | None:
      state_dir = os.environ.get("BUG_FIXER_STATE_DIR", "")
      if state_dir and Path(state_dir).is_dir():
          return Path(state_dir) / "dispatches"
      # Legacy fallback
      ...
  ```
- [ ] Keep the legacy `_WORKSPACE_NAME` fallback for backward compat.

## Deferred Fix Reminders (from user review)

1. **Trust-sensitive files location:** Keep `injection_cache.json` and `.integrity_snapshot.json` in `sidecars/<repo>/` (NOT `state/`). Gate A prefix rule protects them automatically.
2. **`bugfixer_api` dispatches:** Must read `BUG_FIXER_STATE_DIR` or derive `<config dir>/state`.

## Verification Requirement

1. **Test suite:**
   ```bash
   python3 -m unittest discover -s tests -t . -v
   ```
   Expected: same or more tests pass, only known failure.

2. **Import check for each fixed script:**
   ```bash
   python3 -c "import sys; sys.path.insert(0, 'daemon/workspace-plugin/bug-fixer/scripts'); import safe_ingest; print('OK')"
   python3 -c "import sys; sys.path.insert(0, 'daemon/workspace-plugin/bug-fixer/scripts'); import safe_post; print('OK')"
   python3 -c "import sys; sys.path.insert(0, 'daemon/workspace-plugin/bug-fixer/scripts'); import injection_scanner; print('OK')"
   python3 -c "import sys; sys.path.insert(0, 'daemon/workspace-plugin/bug-fixer/scripts'); import integrity_monitor; print('OK')"
   python3 -c "import sys; sys.path.insert(0, 'daemon/workspace-plugin/bug-fixer/scripts'); import bugfixer_api; print('OK')"
   ```

3. **Commit** with message: `[WO2b] Fix config/path resolution in remaining scripts`

## Issue Surfacing

If you discover a defect, upstream bug, or concern *outside your boundary box*, surface it in your Completion Report below — do not file issues directly.

---

## Completion Report
<!-- Written by the worker at task completion -->

### Status: COMPLETED
**Date:** 2026-10-05

**Summary of Changes:**
- **`integrity_monitor.py`**:
  - Implemented `_get_snapshot_file()` to store `.integrity_snapshot.json` in the sidecar directory pointed to by `BUG_FIXER_CONFIG`, with fallback to legacy path.
  - Implemented directory snapshot backup via `_get_snapshot_dir()` (`.integrity_snapshot/`) in `snapshot()`, and snapshot restore in `verify()` when tampering is detected, removing the obsolete `git checkout` dependency.
  - Updated `_get_protected_files()` to resolve `config.json` and `injection_cache.json` from the sidecar directory first, followed by legacy candidate paths.
- **`safe_post.py`**:
  - Updated `_candidate_configs()` to check `BUG_FIXER_CONFIG` env var first before checking legacy candidate paths.
  - Preserved `WORKSPACE_DIR` and `DEFAULT_CONFIG` at module scope for backward compatibility and test stability.
- **`injection_scanner.py`**:
  - Implemented `_get_cache_path(config_path)` returning `injection_cache.json` alongside `BUG_FIXER_CONFIG`'s parent directory or explicit config path, with fallback to legacy path.
  - Updated `CachedInjectionScanner.__init__` to use `_get_cache_path()` when `cache_dir` is omitted.
- **`safe_ingest.py`**:
  - Added import `from pre_tool_gate import _find_daemon_dir`.
  - Implemented `_defaults()` and updated `load_config(config_path=None, require=False)` to check `BUG_FIXER_CONFIG` env var first, search sidecars via `_find_daemon_dir()`, check legacy paths, and support `require=True` (raising `FileNotFoundError` if not found).
- **`bugfixer_api.py`**:
  - Implemented `_find_dispatches_dir()` prioritizing `BUG_FIXER_STATE_DIR`, `<config dir>/state/dispatches`, and legacy fallback.
  - Updated `_find_dispatch()` to handle both `state/dispatches/` and direct `dispatches/` locations.
  - Updated `_find_workspace_dir()` and `BugFixer.get_session_context()` to resolve dispatches via `_find_dispatches_dir()`.
- **`tests/test_wo2b.py`**:
  - Created 11 comprehensive unit tests covering all modified resolution mechanisms and snapshot backup/restore behavior across all 5 scripts.

**Verification Results:**
- Import checks for all 5 scripts passed: `safe_ingest`, `safe_post`, `injection_scanner`, `integrity_monitor`, and `bugfixer_api`.
- Dedicated unit test suite `tests/test_wo2b.py` passed all 11 tests.
- Full test suite execution (`python3 -m unittest discover -s tests -t . -v`): 230 tests run, 226 passed, 3 skipped, 1 known baseline failure (`test_no_function_scoped_imports_in_scripts` failing on `injection_scanner.py:82 inside 'scan': random`). Zero regressions.
- Committed changes: `[WO2b] Fix config/path resolution in remaining scripts` (commit `5bd7ad4`).

**Identified Issues (Out of Scope):**
- `daemon/workspace-plugin/bug-fixer/scripts/injection_scanner.py:82`: Function-scoped import `import random` inside `scan()` method causes `tests/test_import_hygiene.py:test_no_function_scoped_imports_in_scripts` to fail. Recommend moving `import random` to module top-level in a hygiene/cleanup pass.

