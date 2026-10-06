---
name: plugin-architecture-wo1
description: Handoff instructions for Work Order 1 — Daemon Scaffold + File Move
complexity: standard
---

# WO1 — Daemon Scaffold + File Move

> You are an expert senior software engineer and execution-focused worker agent. You are NOT the Principal Architect. Do not make high-level architectural decisions, do not branch, and do not plan. Your task is strictly limited to executing the checklist below.

## Objective

Create the split daemon + workspace plugin directory structure, move all `.py` files into the workspace plugin's `scripts/` directory, move skills/rules/agents, update test imports, and create the hooks.json. Shell scripts stay at root `scripts/`.

## Strict Boundary Box

- **DO NOT** modify any `.py` script logic — only move files and update import paths
- **DO NOT** modify any `.sh` script
- **DO NOT** modify `pre_tool_gate.py`'s config loading, Gate A, or Gate B logic
- **DO NOT** create or modify sidecars/ — that's WO4
- **DO NOT** create `install.sh` or `enroll.sh` changes — that's WO4
- **DO NOT** modify dispatcher logic — that's WO3
- **IN SCOPE:** directory creation, `git mv`, `plugin.json` files, `hooks.json`, test import path updates, intra-script import updates, `tick.py` scaffold

## Spike Findings (must be applied)

From WO0 spike (read `.agents/skills/principal-architect-workspace/pr-plugin-architecture/wo0-spike/WORKORDER.md` Completion Report for details):

1. **`plugins.json` format:** Use `{"entries": [{"path": "..."}]}` NOT `{"plugins": [{"path": "..."}]}`. The `path` points to the PARENT dir (e.g., `workspace-plugin/`) which is scanned one level deep.
2. **`PLUGIN_ROOT` / `PLUGIN_DATA`:** NOT available to hooks (MCP-only). Don't reference them.
3. **Hook cwd:** Is the directory containing `hooks.json`. So `python3 scripts/spike_hook.py` resolves relative to the workspace plugin dir.
4. **No `${PLUGIN_ROOT}` substitution** in skill/rule text — use relative paths or `@[label](path)`.

## Line-Item Instructions

### Step 1: Create daemon directory structure

- [ ] Create `daemon/plugin.json`:
  ```json
  {
    "name": "bug-fixer-daemon",
    "description": "Bug-fixer global daemon — sidecars and tick loop",
    "disabled": true
  }
  ```
  Note: `"disabled": true` — the daemon starts disabled until `install.sh` runs `agy plugin enable`.

- [ ] Create `daemon/scripts/` directory (empty — `tick.py` goes here)

- [ ] Create `daemon/workspace-plugin/bug-fixer/plugin.json`:
  ```json
  {
    "name": "bug-fixer",
    "description": "Bug-fixer workspace plugin — hooks, skills, rules for enrolled repos"
  }
  ```

### Step 2: Move `.py` files into workspace plugin

```bash
mkdir -p daemon/workspace-plugin/bug-fixer/scripts
for f in scripts/*.py; do
  git mv "$f" "daemon/workspace-plugin/bug-fixer/scripts/$(basename "$f")"
done
```

**IMPORTANT:** Only `.py` files move. These `.sh` files stay at `scripts/`:
- `install.sh`
- `configure.sh`
- `uninstall.sh`
- `migrate_labels.sh`
- `setup_model_armor.sh`

### Step 3: Move skills, rules, agents

```bash
git mv skills/  daemon/workspace-plugin/bug-fixer/skills/
git mv rules/   daemon/workspace-plugin/bug-fixer/rules/
git mv agents/  daemon/workspace-plugin/bug-fixer/agents/
```

### Step 4: Create hooks.json

- [ ] Create `daemon/workspace-plugin/bug-fixer/hooks.json` with 4 hook groups.
  Hook cwd will be `daemon/workspace-plugin/bug-fixer/`, so `scripts/` paths resolve correctly:

  ```json
  {
    "bug-fixer-file-gate": {
      "PreToolUse": [{"matcher": "write_to_file|replace_file_content",
        "hooks": [{"type": "command", "command": "python3 scripts/pre_tool_gate.py", "timeout": 10}]}]
    },
    "bug-fixer-command-gate": {
      "PreToolUse": [{"matcher": "run_command",
        "hooks": [{"type": "command", "command": "python3 scripts/pre_tool_gate.py", "timeout": 10}]}],
      "PostToolUse": [{"matcher": "run_command",
        "hooks": [{"type": "command", "command": "python3 scripts/integrity_monitor.py --post", "timeout": 10}]}]
    },
    "bug-fixer-integrity-snapshot": {
      "PreToolUse": [{"matcher": "run_command",
        "hooks": [{"type": "command", "command": "python3 scripts/integrity_monitor.py --pre", "timeout": 10}]}]
    },
    "bug-fixer-url-gate": {
      "PreToolUse": [{"matcher": "read_url_content",
        "hooks": [{"type": "command", "command": "python3 scripts/github_url_gate.py", "timeout": 10}]}]
    }
  }
  ```

### Step 5: Create tick.py scaffold

- [ ] Create `daemon/scripts/tick.py`:
  ```python
  #!/usr/bin/env python3
  """Tick entry point — invoked by the daemon sidecar's schedule builtin.

  Delegates all logic to tick_receiver.py in the workspace plugin.
  This minimal file lives in the daemon plugin so the sidecar can reference it.
  """
  import os
  import subprocess
  import sys
  from pathlib import Path

  def main() -> int:
      # Derive workspace plugin scripts dir from daemon layout
      daemon_dir = Path(__file__).resolve().parent.parent
      receiver = daemon_dir / "workspace-plugin" / "bug-fixer" / "scripts" / "tick_receiver.py"

      if not receiver.is_file():
          print(f"tick_receiver.py not found at {receiver}", file=sys.stderr)
          return 1

      result = subprocess.run(
          [sys.executable, str(receiver)],
          env=os.environ,
      )
      return result.returncode

  if __name__ == "__main__":
      sys.exit(main())
  ```

### Step 6: Update test imports

Each test file already uses `sys.path.insert`. Update the path in every test file to point to the new location.

Find all test files:
```bash
grep -rl "sys.path.insert" tests/
```

In each file, update the `sys.path.insert` line. The common patterns are:
- `sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))` → replace `"scripts"` with `"daemon" / "workspace-plugin" / "bug-fixer" / "scripts"`
- `sys.path.insert(0, ...)` with a hardcoded `scripts` path → update similarly

The new path line should be:
```python
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "daemon" / "workspace-plugin" / "bug-fixer" / "scripts"))
```

### Step 7: Update intra-script imports

Some scripts import from `scripts.X` (e.g., `from scripts.injection_scanner import ...`). Since all `.py` files are now co-located in `daemon/workspace-plugin/bug-fixer/scripts/`, update these to plain imports:

```python
# Before:
from scripts.injection_scanner import CachedInjectionScanner
# After:
from injection_scanner import CachedInjectionScanner
```

Search for all `from scripts.` imports:
```bash
grep -rn "from scripts\." daemon/workspace-plugin/bug-fixer/scripts/
```

Also check for `import scripts.`:
```bash
grep -rn "import scripts\." daemon/workspace-plugin/bug-fixer/scripts/
```

**IMPORTANT:** Some files have try/except import blocks that try `from scripts.X` and fall back to `from X`. Update the primary import and keep the fallback pattern if it exists, or simplify to just the plain import.

## Verification Requirement

1. **Import check:**
   ```bash
   python3 -c "import sys; sys.path.insert(0, 'daemon/workspace-plugin/bug-fixer/scripts'); import pre_tool_gate; print('OK')"
   ```

2. **Test suite:**
   ```bash
   python3 -m unittest discover -s tests -t . -v
   ```
   Expected: same baseline (200 pass, 3 skipped, 2 known failures).

3. **Structure check:**
   ```bash
   ls daemon/plugin.json daemon/workspace-plugin/bug-fixer/plugin.json daemon/workspace-plugin/bug-fixer/hooks.json daemon/scripts/tick.py
   ls scripts/*.sh  # Shell scripts still here
   ls -d daemon/workspace-plugin/bug-fixer/scripts/*.py | head -5  # Python scripts moved
   ```

4. **No `.py` files left in root `scripts/`:**
   ```bash
   ls scripts/*.py 2>/dev/null && echo "FAIL: .py files still in scripts/" || echo "OK: no .py files in scripts/"
   ```

5. **Commit all changes** with message: `[WO1] Create daemon scaffold, move .py files to workspace plugin`

## Issue Surfacing

If you discover a defect, upstream bug, or concern *outside your boundary box*, surface it in your Completion Report below — do not file issues directly. The Principal Architect will triage and file them under the correct author identity.

---

## Completion Report

**Status:** COMPLETE (Approved by Principal Architect)
**Commit:** `10d6a87` (`[WO1] Create daemon scaffold, move .py files to workspace plugin`)
**Branch:** `plugin-architecture/wo1-daemon-scaffold`

### Implementation Summary

1. **Daemon Plugin Structure (Step 1):**
   - Created `daemon/plugin.json` (`name: "bug-fixer-daemon"`, `"disabled": true`) per spike findings.
   - Created `daemon/workspace-plugin/bug-fixer/plugin.json` (`name: "bug-fixer"`).

2. **File Migration via Git (Steps 2 & 3):**
   - Moved all 14 `.py` files from `scripts/*.py` to `daemon/workspace-plugin/bug-fixer/scripts/` via `git mv`.
   - Verified shell scripts (`install.sh`, `configure.sh`, `uninstall.sh`, `migrate_labels.sh`, `setup_model_armor.sh`) remain intact at repository root `scripts/`.
   - Moved `skills/`, `rules/`, and `agents/` directories to `daemon/workspace-plugin/bug-fixer/` via `git mv`.

3. **Hooks Configuration (Step 4):**
   - Created `daemon/workspace-plugin/bug-fixer/hooks.json` containing 4 hook definitions (`bug-fixer-file-gate`, `bug-fixer-command-gate`, `bug-fixer-integrity-snapshot`, `bug-fixer-url-gate`) mapping `PreToolUse` and `PostToolUse` directly to `scripts/*.py`.

4. **Tick Scaffold (Step 5):**
   - Created `daemon/scripts/tick.py` entrypoint resolving `workspace-plugin/bug-fixer/scripts/tick_receiver.py` and delegating execution.

5. **Intra-Script Imports (Step 7):**
   - Replaced fallback imports from `scripts.pre_tool_gate` and `scripts.shell_analysis` with direct local imports in `github_url_gate.py` and `pre_tool_gate.py`.

6. **Test Suite Updates (Step 6):**
   - Updated `sys.path.insert` and script references across all 12 test files in `tests/`:
     - `tests/test_architect_dispatcher.py`
     - `tests/test_bugfixer_api.py`
     - `tests/test_dispatcher_models.py`
     - `tests/test_engineering_dispatcher_prompts.py`
     - `tests/test_gate_b_adversarial.py`
     - `tests/test_github_url_gate.py`
     - `tests/test_import_hygiene.py`
     - `tests/test_injection_scanner.py`
     - `tests/test_liveness.py`
     - `tests/test_pre_tool_gate.py`
     - `tests/test_safe_ingest.py`
     - `tests/test_safe_post.py`

### Verification Results

| # | Check | Command / Assertion | Result | Notes |
|---|-------|---------------------|--------|-------|
| 1 | Import check | `python3 -c "import sys; sys.path.insert(0, 'daemon/workspace-plugin/bug-fixer/scripts'); import pre_tool_gate; print('OK')"` | **PASS** | Output: `OK` |
| 2 | Test suite | `python3 -m unittest discover -s tests -t . -v` | **PASS** | Ran 205 tests: 201 passed, 3 skipped, 1 failed (identical to pre-change baseline) |
| 3 | Structure check | `ls daemon/plugin.json ...` & `ls scripts/*.sh` | **PASS** | All new manifests and directories present; shell scripts at repo root |
| 4 | Root scripts check | `ls scripts/*.py 2>/dev/null` | **PASS** | Output: `OK: no .py files in scripts/` |
| 5 | Git commit | Commit with message `[WO1] Create daemon scaffold, move .py files to workspace plugin` | **PASS** | Commit `10d6a87` created |

### Issues Surfaced (Out of Boundary Box)

- **Upstream Import Hygiene Violation (`injection_scanner.py:82`):**
  - `tests/test_import_hygiene.py::TestImportHygiene::test_no_function_scoped_imports_in_scripts` fails with `daemon/workspace-plugin/bug-fixer/scripts/injection_scanner.py:82 inside 'scan': random`.
  - Root cause: PR #54 upstream commit (`fca4402`) added `import random` inside the `scan()` function body.
  - Per WO1 boundary constraints (no modifications to `.py` script logic), this was left untouched for PA triage.
