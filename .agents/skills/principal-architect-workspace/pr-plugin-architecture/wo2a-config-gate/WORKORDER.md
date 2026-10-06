---
name: plugin-architecture-wo2a
description: Handoff instructions for WO2a — load_config + Gate A rewrite in pre_tool_gate.py
complexity: architect
---

# WO2a — Config Resolution + Gate A Protection

> You are an expert senior software engineer and execution-focused worker agent. You are NOT the Principal Architect. Do not make high-level architectural decisions, do not branch, and do not plan. Your task is strictly limited to executing the checklist below.

## Objective

Rewrite `load_config()` in `pre_tool_gate.py` to find configs from sidecars via `workspacePaths`, add `_find_daemon_dir()` helper, extend Gate A to prefix-protect the entire daemon install directory, update `HOST_SECURITY_SCRIPTS`, fix `_repo_root()`, fix `main()` stdin handling, and update `github_url_gate.py` to pass `workspace_paths`.

## Strict Boundary Box

- **IN SCOPE:** `daemon/workspace-plugin/bug-fixer/scripts/pre_tool_gate.py`, `daemon/workspace-plugin/bug-fixer/scripts/github_url_gate.py`, new tests in `tests/`
- **DO NOT** modify any dispatcher scripts (WO3)
- **DO NOT** modify `install.sh`, `enroll.sh`, or any `.sh` scripts (WO4)
- **DO NOT** modify `safe_ingest.py`, `safe_post.py`, `injection_scanner.py`, `integrity_monitor.py`, `bugfixer_api.py` (WO2b)
- **DO NOT** delete any files
- **DO NOT** change Gate B logic (command evaluation) beyond adding PREFIX-aware matching

## Key File: `daemon/workspace-plugin/bug-fixer/scripts/pre_tool_gate.py` (~2906 lines)

### Key line numbers (post-WO1 move):
- L138-151: `HOST_SECURITY_SCRIPTS`
- L159-182: `_derive_workspace_paths()`
- L185-218: `normalise_config()`
- L221-262: `load_config()`
- L305-327: `evaluate_gate_a()`
- L789-845: `_ProtectedPaths` class
- L2088-2097: `_repo_root()`
- L2196-2202: `evaluate_pretool_call()`
- L2775-2806: `main()`

## Line-Item Instructions

### 1. Add `_find_daemon_dir()` helper (NEW — add near L155)

```python
def _find_daemon_dir(script_dir: Path) -> Path | None:
    """Find the daemon plugin root by matching name == 'bug-fixer-daemon'.

    Must check the manifest name, not just existence, because the workspace
    plugin also has a plugin.json (name: 'bug-fixer').
    """
    for ancestor in [script_dir.parent, script_dir.parent.parent,
                     script_dir.parent.parent.parent]:
        manifest = ancestor / "plugin.json"
        if manifest.is_file():
            try:
                data = json.loads(manifest.read_text())
                if data.get("name") == "bug-fixer-daemon":
                    return ancestor
            except (json.JSONDecodeError, OSError):
                continue
    return None
```

### 2. Add `integrity_monitor.py` to `HOST_SECURITY_SCRIPTS` (L138)

```python
HOST_SECURITY_SCRIPTS = frozenset({
    "pre_tool_gate.py", "safe_ingest.py", "safe_post.py",
    "injection_scanner.py", "shell_analysis.py", "github_url_gate.py",
    "integrity_monitor.py",
})
```

### 3. Rewrite `load_config()` (L221-262)

Replace with sidecar-aware version. **CRITICAL:** preserve `normalise_config()`, `_loaded_from` stamping, fail-closed on bad paths. The function signature changes to accept `workspace_paths`:

```python
def load_config(
    config_path: str | Path | None = None,
    workspace_paths: list[str] | None = None,
) -> dict[str, Any]:
```

Resolution order:
1. Explicit `--config` arg → must exist → FileNotFoundError if not
2. `BUG_FIXER_CONFIG` env → must exist → FileNotFoundError if not  
3. Sidecar scan: find daemon dir via `_find_daemon_dir()`, iterate `sidecars/*/config.json`, match `repo_dir` against `workspace_paths` using `Path.is_relative_to()`. Skip configs where `repo_dir` is empty.
4. Legacy fallback: check `<workspace>/.agents/plugins/bug-fixer-loop-workspace/config.json` for each workspace path (keeps backward compat)
5. Defaults: `spec_file_path: "docs/plugin-architecture.md"` (the #43 fix path)

### 4. Rewrite `_derive_workspace_paths()` (L159-182)

Add `_find_daemon_dir()` call and PREFIX protection:

```python
def _derive_workspace_paths(loaded_config_path: Path | None = None) -> frozenset[str]:
    protected = set(_STATIC_PROTECTED)

    # Protect the loaded config's directory
    if loaded_config_path:
        ws_dir = loaded_config_path.resolve().parent
        protected.add(str(ws_dir / "config.json"))
        protected.add(str(ws_dir / "injection_cache.json"))
        protected.add(str(ws_dir / ".integrity_snapshot.json"))
        protected.add(str(ws_dir / "sidecar.json"))

    # Legacy relative paths
    protected.add(".agents/plugins/bug-fixer-loop-workspace/config.json")
    protected.add(".agents/plugins/bug-fixer-loop-workspace/injection_cache.json")

    # Protect .agents/plugins.json (both absolute and relative)
    protected.add(".agents/plugins.json")
    if loaded_config_path:
        repo_dir_str = None
        try:
            data = json.loads(loaded_config_path.read_text())
            repo_dir_str = data.get("repo_dir")
        except (json.JSONDecodeError, OSError):
            pass
        if repo_dir_str:
            protected.add(str(Path(repo_dir_str) / ".agents" / "plugins.json"))

    # Prefix-protect the entire daemon install directory
    daemon_dir = _find_daemon_dir(Path(__file__).resolve().parent)
    if daemon_dir:
        protected.add(f"PREFIX:{daemon_dir}/")  # trailing slash prevents matching bug-fixer-daemon2/

    return frozenset(protected)
```

### 5. Update `_ProtectedPaths` class (L789-845)

Add `protected_prefixes` list and PREFIX-aware matching:

In `_ProtectedPaths.build()` or `__init__`:
```python
self.protected_prefixes: list[str] = []
for entry in SCANNER_PROTECTED_FILES:
    if entry.startswith("PREFIX:"):
        self.protected_prefixes.append(entry[7:])
    else:
        # existing logic for adding to protected set
```

Add `_is_prefix_protected()` method:
```python
def _is_prefix_protected(self, path: str) -> bool:
    """Check if path falls under a prefix-protected directory.
    
    Exception: paths exactly matching <daemon>/sidecars/*/state/* are allowed
    EXCEPT injection_cache.json and .integrity_snapshot.json.
    """
    norm = os.path.normpath(os.path.realpath(path))
    for prefix in self.protected_prefixes:
        if norm.startswith(prefix):
            rel = norm[len(prefix):]
            parts = rel.split("/")
            # Allow writes under sidecars/<repo>/state/ (but not trust-sensitive files)
            if len(parts) >= 3 and parts[0] == "sidecars" and parts[2] == "state":
                basename = os.path.basename(norm)
                if basename in ("injection_cache.json", ".integrity_snapshot.json"):
                    return True   # still protected
                return False      # allow
            return True           # protected
    return False
```

Wire this into `mentioned_in()` — check prefix match in addition to exact path match.

### 6. Fix `_repo_root()` (L2088-2097)

Keep old workspace name check, remove dead sidecar-layout branch:
```python
if ws_dir.name in ("bug-fixer-loop-workspace",) \
        and ws_dir.parent.parent.name == ".agents":
    return str(ws_dir.parent.parent.parent)
# Note: sidecar layout doesn't need _repo_root() — config["repo_dir"] returned before this
```

### 7. Fix `main()` (L2775-2806) — stdin-mode guard

Only read stdin in hook mode. Other modes use stdin for different purposes:
```python
stdin_data = {}
workspace_paths = []
# Stdin carries hook JSON only in default (hook) mode.
# --git-hook, --scan-only, --stamp-receipt, --stamp-pass, --verify-receipt
# use stdin for other purposes.
_non_hook_modes = any([
    getattr(args, 'git_hook', False),
    getattr(args, 'scan_only', False),
    getattr(args, 'stamp_receipt', False),
    getattr(args, 'stamp_pass', False),
    getattr(args, 'verify_receipt', False),
])
if not _non_hook_modes:
    if not sys.stdin.isatty():
        try:
            raw = json.load(sys.stdin)
            if isinstance(raw, dict):
                stdin_data = raw
        except (json.JSONDecodeError, ValueError):
            # Invalid JSON from hook — deny (fail-closed)
            return _deny("Invalid JSON payload on stdin")

workspace_paths = stdin_data.get("workspacePaths", [])
config = load_config(args.config, workspace_paths=workspace_paths)
```

### 8. Update `github_url_gate.py` — pass workspace_paths

In `github_url_gate.py` `main()` (around L64), apply the same stdin-reading pattern and pass `workspace_paths` to `load_config()`:
```python
stdin_data = {}
if not sys.stdin.isatty():
    try:
        raw = json.load(sys.stdin)
        if isinstance(raw, dict):
            stdin_data = raw
    except (json.JSONDecodeError, ValueError):
        pass  # github_url_gate allows by default on bad stdin

workspace_paths = stdin_data.get("workspacePaths", [])
config = load_config(config_path, workspace_paths=workspace_paths)
```

### 9. Add tests

Create or update test files in `tests/` for:

- `test_find_daemon_dir_skips_workspace_plugin` — create real WO1 layout in tmpdir with both plugin.json files, verify daemon dir (not workspace) returned
- `test_load_config_explicit_path` — loads, stamps `_loaded_from`
- `test_load_config_bad_explicit_raises` — `FileNotFoundError`
- `test_load_config_bad_env_raises` — `FileNotFoundError`
- `test_load_config_sidecars_scan_match` — correct config, skips empty `repo_dir`
- `test_load_config_defaults` — correct `spec_file_path` is `docs/plugin-architecture.md`
- `test_gate_a_deny_plugins_json` — both relative and absolute
- `test_gate_a_deny_sidecar_config` — sidecar dir protected
- `test_gate_a_deny_install_dir_script` — prefix protection
- `test_gate_a_allow_state_dir_write` — state/ exception works
- `test_gate_a_deny_state_injection_cache` — injection_cache.json in state/ still protected
- `test_gate_a_deny_state_integrity_snapshot` — .integrity_snapshot.json in state/ still protected
- `test_gate_b_prefix_shell_forms` — test `~/.gemini/...`, `$HOME/.gemini/...`, `../../` forms
- `test_gate_b_state_path_traversal` — `sidecars/x/state/../config.json` → denied

## Deferred Fix Reminders (from user review)

These were flagged in the plan's "Deferred Fixes" section. Apply them NOW:

1. **State exception must use exact path matching**, not substring. Match `parts[0:3] == ["sidecars", *, "state"]` exactly (done in the code above).
2. **Trailing `/` on prefix** to prevent `bug-fixer-daemon2/` matching (done above).
3. **Stdin guard must cover ALL non-hook modes** including `--stamp-pass` and `--verify-receipt` (done above with `_non_hook_modes`).

## Verification Requirement

1. **Test suite:**
   ```bash
   python3 -m unittest discover -s tests -t . -v
   ```
   Expected: baseline + new tests pass. Only known failure: `injection_scanner.py:82` import.

2. **Import check:**
   ```bash
   python3 -c "import sys; sys.path.insert(0, 'daemon/workspace-plugin/bug-fixer/scripts'); import pre_tool_gate; print('OK')"
   ```

3. **Commit** with message: `[WO2a] Rewrite load_config + Gate A prefix protection`

## Issue Surfacing

If you discover a defect, upstream bug, or concern *outside your boundary box*, surface it in your Completion Report below — do not file issues directly.

---

## Completion Report
<!-- Written by the worker at task completion -->

### Status: COMPLETED
**Date:** 2026-10-05

**Summary of Changes:**
- Implemented `_find_daemon_dir()` checking `plugin.json` for name `bug-fixer-daemon`.
- Added `integrity_monitor.py` to `HOST_SECURITY_SCRIPTS`.
- Updated `load_config` to find sidecar `config.json` instances based on `workspace_paths` and a fallback mechanism to preserve legacy behavior.
- Implemented PREFIX-based path protection logic inside `_derive_workspace_paths()`, `_ProtectedPaths`, and `evaluate_gate_a()`, ensuring exceptions for `.integrity_snapshot.json` and `injection_cache.json` under `state/`.
- Cleaned up `_repo_root()` implementation.
- Hardened standard input consumption paths in `main()` for both `pre_tool_gate.py` and `github_url_gate.py`.
- Established a dedicated unit testing suite `tests/test_wo2a.py` with 14 comprehensive test cases covering config resolution behavior, host installation directory isolation, missing inputs, payload logic evaluation, and path manipulation validation.

**Verification Results:**
- `python3 -m unittest discover -s tests -t . -v` passed across all 219 tests (except the single documented known failure).
- Evaluated import hygiene successfully.
- Commited modifications to the `plugin-architecture/wo2a-config-gate` branch.

**Identified Issues (Out of Scope):**
- None identified.
