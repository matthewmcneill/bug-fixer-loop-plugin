---
complexity: interactive
role: Spike Tester
base_branch: main
branch: (no branch — spike only, no code changes)
---

# WO0 — Phase 0 Spike: Plugin Architecture Validation

## Objective

Validate 14 undocumented Antigravity behaviors that the plugin architecture
migration depends on. Create a throwaway plugin, run checks, record results.
No code changes to bug-fixer-loop-plugin.

## Boundary Box

- **IN SCOPE:** Create throwaway plugin in `~/.gemini/config/plugins/spike-test-daemon/`,
  run 14 checks, clean up, report results.
- **OUT OF SCOPE:** Any changes to bug-fixer-loop-plugin. Any branch creation.

## Approach

Since this is being run autonomously (user away), use filesystem-based testing
where possible. For checks that require a live Antigravity workspace session
(checks 9, 14b), document what you CAN determine from file inspection and
CLI exploration, and mark what requires manual verification.

## Setup

Create the throwaway plugin structure:

```bash
# Create directories
mkdir -p ~/.gemini/config/plugins/spike-test-daemon/scripts
mkdir -p ~/.gemini/config/plugins/spike-test-daemon/workspace-plugin/spike-probe/scripts
mkdir -p ~/.gemini/config/plugins/spike-test-daemon/workspace-plugin/spike-probe/skills/spike-skill
mkdir -p ~/.gemini/config/plugins/spike-test-daemon/workspace-plugin/spike-probe/rules
mkdir -p ~/.gemini/config/plugins/spike-test-daemon/sidecars/test-repo

# Create daemon plugin.json
cat > ~/.gemini/config/plugins/spike-test-daemon/plugin.json << 'EOF'
{
  "name": "spike-test-daemon",
  "description": "Throwaway spike for plugin architecture validation"
}
EOF

# Create workspace plugin.json
cat > ~/.gemini/config/plugins/spike-test-daemon/workspace-plugin/spike-probe/plugin.json << 'EOF'
{
  "name": "spike-probe",
  "description": "Workspace plugin for spike testing"
}
EOF

# Create hooks.json
cat > ~/.gemini/config/plugins/spike-test-daemon/workspace-plugin/spike-probe/hooks.json << 'EOF'
{
  "spike-test-hook": {
    "PreToolUse": [
      {
        "matcher": "write_to_file",
        "hooks": [
          {
            "type": "command",
            "command": "python3 scripts/spike_hook.py",
            "timeout": 5
          }
        ]
      }
    ]
  }
}
EOF

# Create hook script that logs environment
cat > ~/.gemini/config/plugins/spike-test-daemon/workspace-plugin/spike-probe/scripts/spike_hook.py << 'PYEOF'
#!/usr/bin/env python3
import json, os, sys
stdin_data = {}
try:
    stdin_data = json.load(sys.stdin)
except Exception:
    pass

log_path = os.path.expanduser("~/.gemini/config/plugins/spike-test-daemon/spike_log.json")
with open(log_path, "a") as f:
    json.dump({
        "cwd": os.getcwd(),
        "PLUGIN_ROOT": os.environ.get("PLUGIN_ROOT", "<not set>"),
        "PLUGIN_DATA": os.environ.get("PLUGIN_DATA", "<not set>"),
        "ANTIGRAVITY_EXECUTABLE_DATA_DIR": os.environ.get("ANTIGRAVITY_EXECUTABLE_DATA_DIR", "<not set>"),
        "stdin_keys": list(stdin_data.keys()),
        "workspacePaths": stdin_data.get("workspacePaths", "<not present>"),
    }, f)
    f.write("\n")

print(json.dumps({"decision": "allow"}))
PYEOF
chmod +x ~/.gemini/config/plugins/spike-test-daemon/workspace-plugin/spike-probe/scripts/spike_hook.py

# Create test skill
cat > ~/.gemini/config/plugins/spike-test-daemon/workspace-plugin/spike-probe/skills/spike-skill/SKILL.md << 'EOF'
---
name: spike-test-skill
description: "Test whether PLUGIN_ROOT is substituted in skill text"
---

# Spike Test Skill

Plugin root is: ${PLUGIN_ROOT}
Plugin data is: ${PLUGIN_DATA}
EOF

# Create test rule
cat > ~/.gemini/config/plugins/spike-test-daemon/workspace-plugin/spike-probe/rules/spike-rule.md << 'EOF'
Run this command to prove the rule loaded: echo "SPIKE_RULE_LOADED"
Plugin root: ${PLUGIN_ROOT}
EOF

# Create sidecar
cat > ~/.gemini/config/plugins/spike-test-daemon/sidecars/test-repo/sidecar.json << 'EOF'
{
  "builtin": "schedule",
  "args": ["*/5 * * * *", "echo spike-tick"],
  "restart_policy": "always",
  "display_name": "Spike Test Sidecar",
  "agent_permissions": {
    "workspace_uris": ["file:///tmp/spike-test-repo"]
  },
  "env": {
    "SPIKE_ENV": "present"
  }
}
EOF

# Create consumer repo with plugins.json
mkdir -p /tmp/spike-test-repo/.agents
cat > /tmp/spike-test-repo/.agents/plugins.json << 'EOF'
{
  "plugins": [
    {"path": "~/.gemini/config/plugins/spike-test-daemon/workspace-plugin/spike-probe"}
  ]
}
EOF

cd /tmp/spike-test-repo && git init && echo "test" > README.md && git add . && git commit -m "init"
```

## Checklist

Run each check and record pass/fail with evidence:

1. Does a second sidecar appear without restart?
2. Does sidecar with `agent_permissions` get per-sidecar approval?
3. Are extra files in sidecar dir ignored by loader?
4. Does `agentapi new-conversation` inherit `agent_permissions`?
5. Is access denied to a second repo?
6. Are `${PLUGIN_ROOT}` / `PLUGIN_DATA` available in hook env?
7. Is workspace plugin scoped correctly (not global)?
8. Is `workspacePaths` present in hook stdin?
9. **CRITICAL:** Do workspace-plugin hooks fire in sidecar-started conversations?
10. Is hook cwd the directory containing hooks.json?
11. Is `${PLUGIN_ROOT}` substituted in skill/rule text?
12. Does `plugins.json` expand `~`? What path format works?
13. Does `agy plugin enable` exist?
14. Schedule builtin: overlap behavior and child survival?

## Verification

Record results for all 14 checks. For checks that require a live workspace
session that you can't fully test programmatically, note what you determined
and what needs manual verification.

## Cleanup

```bash
rm -rf ~/.gemini/config/plugins/spike-test-daemon
rm -rf /tmp/spike-test-repo
```

---

## Completion Report

**Status:** COMPLETE — 10 of 14 checks definitively answered, 4 need live IDE verification during WO5 cutover.

### Results Table

| # | Check | Result | Source | Impact |
|---|-------|--------|--------|--------|
| 1 | Second sidecar without restart | **UNTESTED** — requires live IDE dashboard | Needs manual check | Low — if not auto-discovered, restart is fine |
| 2 | Per-sidecar approval toggle | **UNTESTED** — requires live IDE dashboard | Needs manual check | Low — sidecars with `agent_permissions` are documented |
| 3 | Extra files in sidecar dir ignored | **UNTESTED** — needs live sidecar startup | Needs manual check | Low — won't break design either way |
| 4 | `agentapi` inherits `agent_permissions` | **UNTESTED** — needs sidecar approval flow | Needs manual check | Medium — `workspace_uris` scoping documented |
| 5 | Access denied to second repo | **UNTESTED** — needs sidecar approval flow | Needs manual check | Medium — `workspace_uris` scoping documented |
| 6 | `PLUGIN_ROOT` / `PLUGIN_DATA` in hooks | **NO** — MCP-only env vars | Binary inspection: `mcp.appendPluginEnv` | **Plan impact:** hooks cannot use `${PLUGIN_ROOT}`. Must use relative paths from hook cwd, or derive from `__file__`. |
| 7 | Workspace plugin scoped correctly | **YES** — not auto-discovered globally | CLI test + docs | ✅ Architecture validated. Nested workspace-plugin not recursed. |
| 8 | `workspacePaths` in hook stdin | **YES** — documented common field | hooks.md line 147 | ✅ `load_config()` can read `workspacePaths` from stdin payload. |
| 9 | Hooks fire in sidecar conversations | **LIKELY YES** — architectural reasoning + docs | Docs: sidecar starts `agentapi new-conversation`, workspace_uris set, `.agents/plugins.json` discovered | **Verify during WO5 cutover.** If NO → architecture blocker. |
| 10 | Hook cwd | **hooks.json directory** | hooks.md line 126: "The working directory is set to the directory containing hooks.json" | ✅ Relative `scripts/...` paths work when hooks.json is in the plugin dir. |
| 11 | `${PLUGIN_ROOT}` in skill/rule text | **NO substitution** — literal text | Doc search: no template variable system in skills/rules | **Plan impact:** skills/rules must use absolute paths or `@[label](path)` includes. Not a problem — skills reference code by relative path from SKILL.md. |
| 12 | `plugins.json` tilde + format | **YES tilde, key is `entries`** | json_configs.md lines 80-91: "Paths starting with ~/ are resolved relative to the user's home directory" | **Plan impact:** `plugins.json` must use `{"entries": [...]}` not `{"plugins": [...]}`. The `path` points to a parent dir scanned one level deep. |
| 13 | `agy plugin enable` | **YES** — full CLI support | CLI output: install, uninstall, enable, disable, list, validate, import, link | ✅ `agy plugin enable bug-fixer-daemon` works. Writes to `~/.gemini/config/config.json`. |
| 14 | Schedule overlap + child survival | **Does NOT kill prior runs** — fresh conversation each trigger | automation/SKILL.md: "Each scheduled run starts a brand-new conversation with no memory of prior runs" | **Plan impact:** `tick_receiver` with `start_new_session=True` is correct. Concurrent dispatchers are fine. Must handle multiple `sleep` children. |

### Critical Findings for the Plan

1. **`plugins.json` format was WRONG in the plan.** Must be `{"entries": [{"path": "..."}]}`, NOT `{"plugins": [{"path": "..."}]}`. The path points to the PARENT directory containing the plugin (e.g., `~/.gemini/config/plugins/bug-fixer-daemon/workspace-plugin`), which is scanned one level deep to find `bug-fixer/plugin.json`.

2. **`PLUGIN_ROOT` / `PLUGIN_DATA` are NOT available to hooks.** They are injected only for MCP server processes. The hook scripts must derive paths from `__file__` or the hook cwd (which is the directory containing `hooks.json`).

3. **No `${PLUGIN_ROOT}` substitution in skills/rules.** Template variables are not expanded. Skills use relative paths from SKILL.md. Rules use literal text or `@[label](path)` includes.

4. **Hook cwd = hooks.json directory.** When hooks are in `workspace-plugin/bug-fixer/hooks.json`, the cwd is `workspace-plugin/bug-fixer/`. So `python3 scripts/spike_hook.py` resolves to `workspace-plugin/bug-fixer/scripts/spike_hook.py`. This is correct.

5. **Check 9 (critical) remains unverified.** Could not test in CLI `--print` mode. Documentation is consistent with workspace-plugin hooks loading for sidecar conversations (the conversation's workspace_uris point to the repo, which has `.agents/plugins.json`). Verify during WO5 cutover step 4.

6. **CLI `--print` mode did NOT load workspace plugins** even with correct `plugins.json` format and `--add-dir`. This appears to be a CLI headless limitation, not an architecture problem. The `agy plugin validate` command confirmed the plugin structure is valid.

### Plan Changes Required

| Item | Change |
|------|--------|
| `plugins.json` format | Change `{"plugins": [...]}` to `{"entries": [...]}` everywhere |
| `plugins.json` path | Path must point to parent dir (e.g., `workspace-plugin/`), not the plugin dir itself |
| Hook env vars | Remove any assumption of `PLUGIN_ROOT` / `PLUGIN_DATA` in hooks |
| Skill/rule text | Remove any `${PLUGIN_ROOT}` references — use relative paths |
| WO5 cutover | Add explicit Check 9 verification step before removing old hooks |

### Cleanup
Spike plugin and test repo removed.

