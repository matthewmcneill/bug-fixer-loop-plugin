---
name: plugin-architecture-wo6
description: Handoff instructions for WO6 — legacy cleanup, docs, tests
complexity: standard
---

# WO6 — Legacy Cleanup + Docs + Tests

> You are an expert senior software engineer and execution-focused worker agent. You are NOT the Principal Architect. Do not make high-level architectural decisions, do not branch, and do not plan. Your task is strictly limited to executing the checklist below.

## Objective

Delete legacy files, update `uninstall.sh`, update README.md and docs, fix the `configure.sh` test that now fails since configure.sh was deleted in WO4, and clean up root directory clutter.

## Strict Boundary Box

- **IN SCOPE:** Legacy file deletion, `scripts/uninstall.sh`, `README.md`, `docs/plugin-architecture.md`, test files
- **DO NOT** modify any Python scripts in `daemon/` 
- **DO NOT** modify `install.sh` or `enroll.sh`

## Line-Item Instructions

### 1. Delete legacy files (git rm)

- [ ] `git rm hooks.json.template`
- [ ] `git rm -r sidecars/` (old 4-sidecar template directory)
- [ ] `git rm plugin.json` (root plugin.json — the daemon's is at `daemon/plugin.json`)
- [ ] `git rm config.json` (root config.json — per-repo configs are now in sidecars)

### 2. Delete root clutter (git rm or just rm for untracked)

Check git status for each. Use `git rm` for tracked files, `rm` for untracked:
- [ ] `pr_52.json`
- [ ] `update_pr.sh`
- [ ] `update_pr2.sh`
- [ ] `approve_pr.sh`
- [ ] `test-repo/` directory
- [ ] `test-remote/` directory
- [ ] `matthewmcneill_bug-fixer-loop-plugin_triage_preview.json`

### 3. Update `scripts/uninstall.sh`

- [ ] Read the existing `scripts/uninstall.sh` and update it for the new layout:
  - Target: `~/.gemini/config/plugins/bug-fixer-daemon/`
  - Require `--force` if `sidecars/` is non-empty (warn about per-repo data loss)
  - Clean stale entries from `~/.gemini/config/config.json` (remove `bug-fixer-daemon` key)
  - Suggest running `unenroll.sh` first for each enrolled repo

### 4. Fix test for deleted `configure.sh`

- [ ] Find the test that references `scripts/configure.sh` (likely in `tests/test_liveness.py` or `tests/test_pre_tool_gate.py`):
  ```bash
  grep -rn "configure.sh" tests/
  ```
- [ ] Update it to reference `scripts/enroll.sh` instead, or remove the check if it's just verifying the file exists.

### 5. Fix `test_import_hygiene.py` for new file layout

- [ ] The import hygiene test scans `scripts/*.py` — but Python files are now at `daemon/workspace-plugin/bug-fixer/scripts/*.py`. Update the test to scan the new location.
  ```bash
  grep -n "scripts/" tests/test_import_hygiene.py
  ```

### 6. Update README.md

- [ ] Update installation instructions to reference `install.sh` + `enroll.sh` pattern
- [ ] Update the directory structure section to show the new `daemon/` layout
- [ ] Reference `unenroll.sh` for removing repos
- [ ] Keep existing feature descriptions, security model descriptions, etc.

### 7. Update `docs/plugin-architecture.md`

- [ ] Update for split-daemon + workspace plugin pattern
- [ ] Document the `enroll`/`unenroll` commands
- [ ] Document the new directory layout under `~/.gemini/config/plugins/bug-fixer-daemon/`
- [ ] Document the `plugins.json` format (`entries` key)

## Verification Requirement

1. **Test suite:**
   ```bash
   python3 -m unittest discover -s tests -t . -v
   ```
   The configure.sh test error should be fixed. Target: 0 errors, only known failure(s).

2. **No legacy files remain:**
   ```bash
   ls hooks.json.template sidecars/ plugin.json config.json 2>&1
   # Should all be "No such file or directory"
   ```

3. **Commit** with message: `[WO6] Delete legacy files, update docs + tests`

## Issue Surfacing

If you discover a defect, upstream bug, or concern *outside your boundary box*, surface it in your Completion Report below — do not file issues directly.

---

## Completion Report
<!-- Written by the worker at task completion -->
