---
name: plugin-architecture-wo4
description: Handoff instructions for WO4 — install.sh rewrite + enroll.sh/unenroll.sh
complexity: architect
---

# WO4 — install.sh + enroll.sh / unenroll.sh

> You are an expert senior software engineer and execution-focused worker agent. You are NOT the Principal Architect. Do not make high-level architectural decisions, do not branch, and do not plan. Your task is strictly limited to executing the checklist below.

## Objective

Rewrite `install.sh` to deploy the `daemon/` directory to `~/.gemini/config/plugins/bug-fixer-daemon/`, create `enroll.sh` (from `configure.sh`) that sets up per-repo sidecar config and `.agents/plugins.json`, and create `unenroll.sh`.

## Strict Boundary Box

- **IN SCOPE:** `scripts/install.sh`, new `scripts/enroll.sh`, new `scripts/unenroll.sh`, delete `scripts/configure.sh`
- **DO NOT** modify any Python scripts
- **DO NOT** modify `daemon/` directory contents (WO1-WO3 done)
- **DO NOT** create or modify actual sidecars — that happens at runtime
- **DO NOT** modify test files

## Context

- The `daemon/` directory (created in WO1) contains the plugin structure to install
- `scripts/configure.sh` (747 lines) is the current interactive wizard — use it as a template for `enroll.sh`
- The install target is `~/.gemini/config/plugins/bug-fixer-daemon/`
- `rsync -a --delete --exclude='sidecars/' daemon/ "$INSTALL_DIR/"` — preserves per-repo data

## Key Spike Finding

From WO0: `plugins.json` format must be:
```json
{
  "entries": [
    {"path": "~/.gemini/config/plugins/bug-fixer-daemon/workspace-plugin"}
  ]
}
```
**NOT** `{"plugins": [...]}`. The `path` points to the PARENT dir containing the plugin, scanned one level deep.

## Line-Item Instructions

### 1. Rewrite `scripts/install.sh`

- [ ] Read the current `scripts/install.sh` (439 lines) to understand what it does
- [ ] Rewrite it to:

```bash
#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
INSTALL_DIR="$HOME/.gemini/config/plugins/bug-fixer-daemon"

echo "Installing bug-fixer daemon plugin..."
echo "  Source: $REPO_DIR/daemon/"
echo "  Target: $INSTALL_DIR/"

# Create target (sidecars/ persists per-repo data)
mkdir -p "$INSTALL_DIR/sidecars"

# Sync daemon structure, preserving sidecars/
rsync -a --delete --exclude='sidecars/' "$REPO_DIR/daemon/" "$INSTALL_DIR/"

# Enable the plugin
if command -v agy &>/dev/null; then
    agy plugin enable bug-fixer-daemon 2>/dev/null || true
    echo "  Plugin enabled via 'agy plugin enable'"
else
    echo "  Note: 'agy' CLI not found — enable manually in settings"
fi

echo ""
echo "Installed successfully."
echo "Next: ./scripts/enroll.sh -r <owner/repo> -d <repo-dir>"
```

Keep any existing functionality from the current install.sh that's still relevant (e.g., dependency checks, label migration prompts), but adapt it for the new layout.

### 2. Create `scripts/enroll.sh`

- [ ] Read `scripts/configure.sh` to understand the existing interactive wizard
- [ ] Create `scripts/enroll.sh` based on `configure.sh`, adapted for new layout:

Key changes from configure.sh:
- **Output config to:** `$INSTALL_DIR/sidecars/<repo-slug>/config.json`
  - `repo-slug` is the repo name slug (e.g., `matthewmcneill--bug-fixer-loop-plugin`)
  - `repo_dir` is a REQUIRED key in the config
- **Output sidecar to:** `$INSTALL_DIR/sidecars/<repo-slug>/sidecar.json`
  - Include `BUG_FIXER_CONFIG` and `BUG_FIXER_STATE_DIR` in env block
  - Include `agent_permissions.workspace_uris` pointing to the repo
- **Create state dir:** `$INSTALL_DIR/sidecars/<repo-slug>/state/`
- **Create consumer `.agents/plugins.json`** in the repo if it doesn't exist:
  ```json
  {
    "entries": [
      {"path": "~/.gemini/config/plugins/bug-fixer-daemon/workspace-plugin"}
    ]
  }
  ```
  If it already exists, merge the entry (check if already present first).
- **Convert cron → interval:** existing `*_cron` keys → `*_interval_min` flat keys
- **Support `--migrate-state` flag:** copies old state from `.agents/plugins/bug-fixer-loop-workspace/` to new `state/` dir

Required flags:
```
Usage: enroll.sh -r <owner/repo> -d <repo-dir> [--migrate-state]
```

The sidecar.json should look like:
```json
{
  "builtin": "schedule",
  "args": ["*/5 * * * *", "python3", "<install-dir>/scripts/tick.py"],
  "restart_policy": "always",
  "display_name": "Bug Fixer — <owner/repo>",
  "agent_permissions": {
    "workspace_uris": ["file://<repo-dir>"]
  },
  "env": {
    "BUG_FIXER_CONFIG": "<install-dir>/sidecars/<slug>/config.json",
    "BUG_FIXER_STATE_DIR": "<install-dir>/sidecars/<slug>/state"
  }
}
```

### 3. Create `scripts/unenroll.sh`

- [ ] ~40-50 lines. Takes `-r <owner/repo>` or `-d <repo-dir>`:

```bash
#!/usr/bin/env bash
set -euo pipefail
# Parse args: -r owner/repo or -d /path/to/repo
# Derive slug
# Check for in-flight claims in state/claims/
# Remove $INSTALL_DIR/sidecars/<slug>/
# Remove entry from <repo>/.agents/plugins.json
# Report
```

### 4. Delete `scripts/configure.sh`

- [ ] `git rm scripts/configure.sh`
  
Note: The logic lives on in `enroll.sh`. The old `configure.sh` is superseded.

## Verification Requirement

1. **Syntax check both scripts:**
   ```bash
   bash -n scripts/install.sh && echo "install.sh OK"
   bash -n scripts/enroll.sh && echo "enroll.sh OK"
   bash -n scripts/unenroll.sh && echo "unenroll.sh OK"
   ```

2. **Test suite still passes:**
   ```bash
   python3 -m unittest discover -s tests -t . -v
   ```

3. **Commit** with message: `[WO4] Rewrite install.sh, create enroll.sh + unenroll.sh`

## Issue Surfacing

If you discover a defect, upstream bug, or concern *outside your boundary box*, surface it in your Completion Report below — do not file issues directly.

---

## Completion Report
<!-- Written by the worker at task completion -->
**Status:** Completed
**Changes Made:**
- Rewrote `scripts/install.sh` to sync the `daemon/` directory to `~/.gemini/config/plugins/bug-fixer-daemon/`.
- Created `scripts/enroll.sh` per the instructions, adapting the layout (config, sidecar, plugin JSON).
- Created `scripts/unenroll.sh` to handle sidecar and `.agents/plugins.json` cleanup.
- Deleted `scripts/configure.sh` (`git rm`).
- All scripts are executable and pass `bash -n`.

**Defects / Outside Boundary Box Concerns:**
- **Test Failure:** `tests.test_dispatcher_models.TestDispatcherModels.test_configure_script_contains_default_models` throws a `FileNotFoundError` because `scripts/configure.sh` was deleted, as instructed. I was unable to fix this since I am explicitly forbidden from modifying test files. The test needs to be updated to check `enroll.sh` instead.
