# Building Antigravity Plugins — A Practical Guide

> Everything we learned building and deploying production Antigravity plugins,
> distilled into reusable patterns. Based on empirical testing and hard-won
> lessons from the bug-fixer-loop plugin (October 2026).

---

## Table of Contents

- [Plugin Structure & Manifests](#plugin-structure--manifests)
- [The Split-Daemon Pattern](#the-split-daemon-pattern)
  - [Why Split?](#why-split)
  - [Directory Layout](#directory-layout)
  - [The Immutable Plugin Root Principle](#the-immutable-plugin-root-principle)
- [Global Install, Local Enrollment](#global-install-local-enrollment)
  - [Install Script Pattern](#install-script-pattern)
  - [Enrollment Script Pattern](#enrollment-script-pattern)
  - [Unenrollment Script Pattern](#unenrollment-script-pattern)
  - [Uninstall Script Pattern](#uninstall-script-pattern)
  - [Multi-Project Support](#multi-project-support)
- [Discovery & Loading](#discovery--loading)
  - [Consumer Repo Declaration](#consumer-repo-declaration)
  - [Loading Priority](#loading-priority)
  - [Enabling & Disabling](#enabling--disabling)
- [Hooks — Complete Reference](#hooks--complete-reference)
  - [hooks.json Structure](#hooksjson-structure)
  - [Hook Discovery Paths](#hook-discovery-paths)
  - [Hook Working Directory](#hook-working-directory)
  - [Hook Events — Full Reference](#hook-events--full-reference)
  - [injectSteps — Message Injection](#injectsteps--message-injection)
  - [PreToolUse — Gate & Rewrite](#pretooluse--gate--rewrite)
  - [PostToolUse — Observation](#posttooluse--observation)
  - [PostInvocation — Force Continue or Terminate](#postinvocation--force-continue-or-terminate)
  - [Stop — Prevent Turn Ending](#stop--prevent-turn-ending)
  - [hooks.json vs hooks/hooks.json](#hooksjson-vs-hookshooksjson)
  - [Dual-Host Plugins (Antigravity + Claude Code)](#dual-host-plugins-antigravity--claude-code)
  - [Empirical Hook Discovery Findings](#empirical-hook-discovery-findings)
- [Rules](#rules)
- [Skills](#skills)
- [Custom Agents](#custom-agents)
- [Sidecars](#sidecars)
  - [How Antigravity Discovers Sidecars](#how-antigravity-discovers-sidecars)
  - [Sidecar Manifest](#sidecar-manifest)
  - [Unified Tick Pattern](#unified-tick-pattern)
  - [Sidecar Failure Modes](#sidecar-failure-modes)
- [Dispatchers — The Non-LLM Control Plane](#dispatchers--the-non-llm-control-plane)
  - [Why Dispatchers Exist](#why-dispatchers-exist)
  - [Spawning Conversations via agentapi](#spawning-conversations-via-agentapi)
  - [Circuit Breakers](#circuit-breakers)
- [Configuration Management](#configuration-management)
  - [Config Discovery Patterns](#config-discovery-patterns)
  - [State Directory](#state-directory)
- [Anti-Patterns (Learned the Hard Way)](#anti-patterns-learned-the-hard-way)
- [Checklist: Shipping a Plugin](#checklist-shipping-a-plugin)

---

## Plugin Structure & Manifests

Every Antigravity plugin needs a `plugin.json` manifest at its root:

```json
{
  "name": "my-plugin",
  "description": "What this plugin does"
}
```

Antigravity discovers plugins from:
- `~/.gemini/config/plugins/<name>/plugin.json` (global)
- `<workspace>/.agents/plugins/<name>/plugin.json` (workspace-scoped)
- Paths declared in `<workspace>/.agents/plugins.json` (referenced)

A plugin directory can contain any combination of:
- `hooks.json` — lifecycle interceptors
- `rules/` — agent instructions (e.g., `rules/AGENTS.md`)
- `skills/` — multi-step agent procedures (`skills/<name>/SKILL.md`)
- `agents/` — custom agent profiles (`agents/<name>.md`)
- `scripts/` — supporting scripts invoked by hooks or skills
- `sidecars/` — background process definitions

---

## The Split-Daemon Pattern

### Why Split?

If your plugin needs background automation (cron jobs, scheduled tasks) and also needs to inject rules/skills/hooks into consumer workspaces, you face a tension:

- **Global plugins** (`~/.gemini/config/plugins/`) can host sidecars, but their hooks fire on *every* workspace
- **Workspace plugins** (`.agents/plugins/`) scope correctly, but can't host sidecars
- **Submodule cloning** pollutes every consumer repo and creates maintenance headaches

The **split-daemon pattern** resolves this cleanly:

| Component | Role | Where | Scope |
|-----------|------|-------|-------|
| **Global Daemon** | Scheduler, background heartbeat, sidecar host | `~/.gemini/config/plugins/<daemon>/` | Global (installed once per machine) |
| **Shared Workspace Plugin** | Execution bundle: hooks, rules, skills, agents, scripts | `~/.gemini/config/plugins/<daemon>/workspace-plugin/<name>/` | Workspace (loaded per-repo via `.agents/plugins.json`) |
| **Per-Repo Sidecars & State** | Cron sidecar, per-repo config, runtime state | `~/.gemini/config/plugins/<daemon>/sidecars/<repo-slug>/` | Per-repo |

### Directory Layout

```
my-plugin/  (Source Repository)
├── daemon/
│   ├── plugin.json                        ← Global daemon manifest
│   ├── hooks.json                         ← Global hooks (if needed)
│   ├── scripts/
│   │   └── tick.py                        ← Heartbeat entry point
│   └── workspace-plugin/
│       └── my-plugin/                     ← Shared workspace plugin
│           ├── plugin.json                ← Workspace plugin manifest
│           ├── hooks.json                 ← Per-workspace hooks
│           ├── rules/                     ← Agent rules
│           ├── skills/                    ← Agent skills
│           ├── agents/                    ← Custom agents
│           └── scripts/                   ← Dispatchers, gates, APIs
└── scripts/
    ├── install.sh                         ← Deploys daemon globally
    ├── enroll.sh                          ← Enrolls a repository
    ├── unenroll.sh                        ← Unenrolls a repository
    └── uninstall.sh                       ← Removes everything

~/.gemini/config/plugins/<daemon>/  (Installed)
├── plugin.json
├── hooks.json
├── scripts/tick.py
├── sidecars/
│   └── <owner--repo>/
│       ├── sidecar.json                   ← Cron schedule
│       ├── config.json                    ← Per-repo settings
│       └── state/                         ← Runtime state
└── workspace-plugin/<name>/               ← Shared plugin bundle
```

### The Immutable Plugin Root Principle

> **The source plugin directory is read-only after installation.**

The source repo (typically a git clone or submodule) must never be written to at runtime:

- ❌ No `config.json` with instance values
- ❌ No runtime state files (claims, locks, cooldowns)
- ❌ No `install.sh` writing back into the source
- ❌ No sed/templating modifying source files in-place

Scripts, skills, and agents are deployed via `rsync` to the install directory. The source repo is the single source of truth for *code*; instance data lives in `sidecars/<repo>/`.

---

## Global Install, Local Enrollment

This is the key lifecycle pattern: install the daemon **once** globally, then **enroll** individual repositories. Each repo gets its own config, state, and sidecar — but they all share the same plugin code.

### Install Script Pattern

```bash
#!/usr/bin/env bash
set -euo pipefail

INSTALL_DIR="$HOME/.gemini/config/plugins/my-daemon"

# Create target, preserving per-repo data
mkdir -p "$INSTALL_DIR/sidecars"

# Sync daemon code — exclude sidecars/ so per-repo data survives upgrades
rsync -a --delete --exclude='sidecars/' "$REPO_DIR/daemon/" "$INSTALL_DIR/"

# Enable the plugin
agy plugin enable my-daemon 2>/dev/null || true
```

**Key insight:** `rsync --exclude='sidecars/'` lets you ship code updates without destroying per-repo configuration and state. This is critical for upgrade safety.

### Enrollment Script Pattern

Enrollment creates per-repo infrastructure:

```bash
# 1. Slugify repo name for filesystem
REPO_SLUG="${TARGET_REPO//\//-}"  # owner/repo → owner--repo
SIDECAR_DIR="$INSTALL_DIR/sidecars/$REPO_SLUG"

# 2. Create per-repo directories
mkdir -p "$SIDECAR_DIR/state"

# 3. Generate per-repo config
cat > "$SIDECAR_DIR/config.json" <<JSON
{
  "target_repo": "$TARGET_REPO",
  "repo_dir": "$TARGET_REPO_DIR",
  "maintainer": "",
  ...
}
JSON

# 4. Generate sidecar manifest (cron schedule)
cat > "$SIDECAR_DIR/sidecar.json" <<JSON
{
  "builtin": "schedule",
  "args": ["*/5 * * * *", "python3", "$INSTALL_DIR/scripts/tick.py"],
  "restart_policy": "always",
  "display_name": "My Plugin — $TARGET_REPO",
  "agent_permissions": {
    "workspace_uris": ["file://$TARGET_REPO_DIR"]
  },
  "env": {
    "MY_PLUGIN_CONFIG": "$SIDECAR_DIR/config.json",
    "MY_PLUGIN_STATE_DIR": "$SIDECAR_DIR/state"
  }
}
JSON

# 5. Register workspace plugin in consumer repo
# This is how the consumer repo discovers your hooks, rules, skills
python3 -c "
import json, os, sys
path = '$TARGET_REPO_DIR/.agents/plugins.json'
entry = {'path': '~/.gemini/config/plugins/my-daemon/workspace-plugin'}
data = {'entries': []}
if os.path.exists(path):
    with open(path) as f: data = json.load(f)
if 'entries' not in data: data['entries'] = []
if entry not in data['entries']: data['entries'].append(entry)
with open(path, 'w') as f: json.dump(data, f, indent=2)
"
```

**Key insights:**
- The consumer repo only gets a lightweight `.agents/plugins.json` entry — no scripts, no git submodules
- Environment variables (`MY_PLUGIN_CONFIG`, `MY_PLUGIN_STATE_DIR`) give scripts a reliable way to find their config without path assumptions
- `agent_permissions.workspace_uris` scopes the sidecar to the enrolled repo

### Unenrollment Script Pattern

```bash
# 1. Check for active work (refuse if agents are mid-task)
if ls "$SIDECAR_DIR/state/claims/"*.json 2>/dev/null | head -1; then
    echo "ERROR: Active claims exist — wait for agents to finish"
    exit 1
fi

# 2. Remove sidecar directory
rm -rf "$SIDECAR_DIR"

# 3. Remove workspace-plugin entry from consumer repo's plugins.json
# (parse JSON, filter out the entry, write back)
```

### Uninstall Script Pattern

```bash
# Safety check — don't nuke enrolled repos accidentally
if [ -d "$INSTALL_DIR/sidecars" ] && [ "$(ls -A "$INSTALL_DIR/sidecars")" ]; then
    if [ "$1" != "--force" ]; then
        echo "ERROR: Enrolled repos exist. Unenroll first, or use --force"
        exit 1
    fi
fi

agy plugin disable my-daemon 2>/dev/null || true
rm -rf "$INSTALL_DIR"
```

### Multi-Project Support

The split-daemon architecture natively scales to multiple repositories:

```
~/.gemini/config/plugins/my-daemon/
└── sidecars/
    ├── owner--project-a/
    │   ├── sidecar.json        ← Independent cron schedule
    │   ├── config.json         ← Project-a settings
    │   └── state/              ← Project-a state
    └── owner--project-b/
        ├── sidecar.json        ← Independent cron schedule
        ├── config.json         ← Project-b settings
        └── state/              ← Project-b state
```

Each consumer repo's `.agents/plugins.json` points to the same shared workspace plugin. No submodules, no duplicated scripts. Enroll a new repo and it immediately inherits all hooks, rules, and skills.

---

## Discovery & Loading

### Consumer Repo Declaration

A consumer project enables your plugin by declaring it in `.agents/plugins.json`:

```json
{
  "entries": [
    {
      "path": "~/.gemini/config/plugins/my-daemon/workspace-plugin"
    }
  ]
}
```

> **Important:**
> - The JSON structure requires the `"entries"` key (an array of objects), **not** `"plugins"`
> - The `"path"` points to the directory *containing* the plugin. Antigravity scans one level deep for `plugin.json` manifests

When Antigravity opens the consumer project, it discovers your plugin and ingests its skills, rules, hooks, and agents.

### Loading Priority

From highest to lowest:

1. **Workspace project** — `.agents/` walking up from CWD to repo root
2. **Declared configs** — explicit `skills.json` or `plugins.json`
3. **Global discovery** — `~/.gemini/config/`
4. **Built-in customisations** — bundled with Antigravity
5. **Global declared configs** — global JSON configs

### Enabling & Disabling

The daemon plugin's state is recorded in `~/.gemini/config/config.json`:

```json
{
  "plugins": {
    "my-daemon": { "enabled": true }
  }
}
```

Toggle via CLI:
```bash
agy plugin enable my-daemon
agy plugin disable my-daemon
```

---

## Hooks — Complete Reference

Hooks are lifecycle interceptors that run external commands at specific points in the agent's execution.

### hooks.json Structure

```json
{
  "my-hook-group-name": {
    "PreToolUse": [{
      "matcher": "write_to_file|replace_file_content",
      "hooks": [{
        "type": "command",
        "command": "python3 scripts/my_gate.py",
        "timeout": 10
      }]
    }],
    "PostToolUse": [{
      "matcher": "run_command",
      "hooks": [{
        "type": "command",
        "command": "python3 scripts/my_observer.py",
        "timeout": 10
      }]
    }]
  }
}
```

`PreToolUse` and `PostToolUse` are **grouped with matchers** — they fire only for matching tool names. All other events (`SessionStart`, `PreInvocation`, `PostInvocation`, `Stop`) are **flat arrays** — they fire unconditionally.

### Hook Discovery Paths

Antigravity discovers `hooks.json` from three locations:

| Location | Path | Scope |
|----------|------|-------|
| **Workspace** | `<workspace>/.agents/hooks.json` | Project-scoped |
| **Global** | `~/.gemini/config/hooks.json` | All workspaces |
| **Plugin** | `<plugin-root>/hooks.json` | Depends on plugin scope |

### Hook Working Directory

When Antigravity invokes a plugin hook, commands execute **relative to the plugin root**. This means `python3 scripts/my_gate.py` resolves to `<plugin-root>/scripts/my_gate.py`.

### Hook Events — Full Reference

#### Common stdin Fields (All 6 Events)

```json
{
  "conversationId": "022ef0e8-...",
  "workspacePaths": ["/path/to/workspace"],
  "transcriptPath": "/path/to/brain/<conversationId>/.system_generated/logs/transcript.jsonl",
  "artifactDirectoryPath": "/path/to/brain/<conversationId>",
  "modelName": "..."
}
```

#### Per-Event Matrix

| Hook Event | Layout | When It Fires | Event-Specific stdin | Supported stdout | Notes |
|:---|:---|:---|:---|:---|:---|
| **SessionStart** | Flat array | Once per conversation, before first model call | *(common only)* | `injectSteps` | One-time session bootstrap |
| **PreInvocation** | Flat array | Before each model call in a turn | `invocationNum`, `initialNumSteps` | `injectSteps` | Fires N+1 times per N tool-call rounds. Prefer `ephemeralMessage` to avoid bloat |
| **PostInvocation** | Flat array | After each model call + tool calls complete | `invocationNum`, `initialNumSteps` | `injectSteps`, `terminationBehavior` | `"force_continue"` or `"terminate"` |
| **PreToolUse** | Grouped with `matcher` | Before matching tool calls | `stepIdx`, `toolCall` | `decision`, `reason`, `permissionOverrides`, `overwrite` | Most restrictive decision wins across hooks |
| **PostToolUse** | Grouped with `matcher` | After matching tool calls finish | `stepIdx`, `toolCall`, `error` | `{}` (observational) | `toolCall` IS passed on stdin despite docs omitting it |
| **Stop** | Flat array | When agent loop is about to end | `executionNum`, `terminationReason`, `error`, `fullyIdle` | `decision` (`continue`/`stop`), `reason` | Normal completion passes `"NO_TOOL_CALL"`. Continuation cap: 10 |

### injectSteps — Message Injection

`SessionStart`, `PreInvocation`, and `PostInvocation` all support `injectSteps` on stdout for injecting messages into the conversation:

#### ephemeralMessage (Single-Invocation Scope)

```json
{
  "injectSteps": [
    { "ephemeralMessage": "Transient context for this model call only." }
  ]
}
```

- Appends an `EPHEMERAL_MESSAGE` step. Visible only on the immediate invocation — excluded from subsequent model calls.
- **Best for:** Per-invocation dynamic context or status reminders.

#### userMessage (Persistent Session Scope)

```json
{
  "injectSteps": [
    { "userMessage": "Additional user-role message." }
  ]
}
```

- Appends a `USER_INPUT` step. Persists across subsequent invocations and turns.

#### systemMessage (Persistent Session Scope)

```json
{
  "injectSteps": [
    {
      "systemMessage": {
        "systemMessage": "System-level instruction injected by hook.",
        "metadata": { "source": "my-hook" }
      }
    }
  ]
}
```

- **Schema caveat:** Unlike `ephemeralMessage` and `userMessage` (plain strings), `systemMessage` **must be a nested object** containing a `"systemMessage"` string field. Passing a bare string fails.

#### toolCall inside injectSteps

- Documented in hooks.md under `PreInvocation` but empirically appears to be a no-op in current builds.

### PreToolUse — Gate & Rewrite

The most commonly used hook. Receives tool call details and returns a decision:

```json
// stdin
{
  "toolCall": {"name": "run_command", "args": {"CommandLine": "rm -rf /"}},
  "conversationId": "...",
  "workspacePaths": ["/path/to/repo"]
}

// stdout — allow
{"decision": "allow"}

// stdout — deny
{"decision": "deny", "reason": "Dangerous command blocked"}

// stdout — allow with rewrite
{"decision": "allow", "reason": "Normalized", "overwrite": {"CommandLine": "pytest -q"}}

// stdout — defer to user
{"decision": "force_ask"}
```

Non-zero exit code also aborts the tool call. Most restrictive decision wins when multiple hooks match.

### PostToolUse — Observation

Observational only — no decision output. Useful for monitoring, logging, or integrity checks.

```json
// stdout (acknowledgement only)
{}
```

### PostInvocation — Force Continue or Terminate

Control whether the agent continues after completing a round of tool calls:

```json
{
  "injectSteps": [{"ephemeralMessage": "Verify test output before finishing."}],
  "terminationBehavior": "force_continue"
}
```

### Stop — Prevent Turn Ending

Intercept the agent's attempt to stop. Continuation cap of 10 prevents infinite loops.

```json
{"decision": "continue", "reason": "Uncommitted changes; please lint first."}
```

### hooks.json vs hooks/hooks.json

> **At runtime, Antigravity ONLY loads `<plugin-root>/hooks.json`.** It never reads `<plugin-root>/hooks/hooks.json` directly.

Why `hooks/hooks.json` appears to work sometimes:

1. **Native Antigravity plugins** — `hooks.json` at the plugin root. This is the only path the runtime reads.
2. **Imported Claude Code / Gemini CLI extensions** — When you run `agy plugin install <path>` on a Claude Code plugin, the CLI's importer reads `<source>/hooks/hooks.json`, interpolates `${extensionPath}`, and writes the result to `<plugin-root>/hooks.json`.
3. **Native plugins with `agy plugin install`** — If a native Antigravity manifest exists, the directory is copied as-is **without** staging `hooks/hooks.json`.

### Dual-Host Plugins (Antigravity + Claude Code)

The hook schema differs between hosts:

| | Antigravity | Claude Code |
|---|---|---|
| **File** | `<plugin-root>/hooks.json` | `<plugin-root>/hooks/hooks.json` |
| **Top-level key** | `<hook-group-name>` map wrapping event arrays | `"hooks": { "<Event>": [...] }` |
| **cwd** | `<plugin-root>` | varies |

A dual-host repository can safely keep both files without collision.

### Empirical Hook Discovery Findings

From direct testing (October 2026):

- **Workspace plugin hooks via `plugins.json`:** Hooks on workspace plugins (nested under `workspace-plugin/`) were **NOT** discovered — the runtime does not load hooks from nested/referenced workspace plugins.
- **Daemon plugin hooks at root:** Hooks at `<daemon-plugin-root>/hooks.json` (the globally-installed plugin) **ARE** discovered and fire correctly.
- **Resolution:** Place `hooks.json` on the daemon plugin (global), use the workspace plugin (via `plugins.json`) only for skills, rules, and agents.

---

## Rules

Plugin rules live at `<plugin>/rules/AGENTS.md`. They should be **generic** — applicable to any project using the plugin.

**Project-specific rules** belong in the consumer project's `.agents/rules/`, not in the plugin.

| Rule | Where | Why |
|------|-------|-----|
| "Never auto-merge PRs" | Plugin `rules/AGENTS.md` | Universal to all consumers |
| "Use PlatformIO MCP for builds" | Consumer `.agents/rules/` | Only for PlatformIO projects |
| "Run `npm test` before PRs" | Consumer `.agents/rules/` | Only for Node.js projects |

**Defer to project rules for tooling choices.** Your plugin should say *"Run the configured test command"* not *"Run `pytest`"*.

---

## Skills

Skills are multi-step agent procedures defined in `skills/<name>/SKILL.md` with YAML frontmatter:

```yaml
---
name: my-skill
description: >
  What this skill does and when it should be triggered.
---

# My Skill

## Step 1: ...
```

Antigravity discovers skills via **progressive disclosure**:
1. Only the skill's **name and description** are loaded into context
2. The **full content** is loaded only when the agent or user activates the skill

Skills resolve paths at runtime relative to the workspace plugin — use `<this_skill_file>` relative navigation.

---

## Custom Agents

Agent definitions live at:
- `<plugin>/agents/<name>.md` (flat file)
- `<plugin>/agents/<name>/agent.md` (directory format)

### Required Frontmatter

```yaml
---
name: my-agent
description: What this agent does.
mainAgent: true    # defaults true — allows `agy --agent <name>`
subagent: true     # defaults true — allows `invoke_subagent`
hidden: true       # only hides from UI picker, NOT from resolution
model: flash       # must be tier name: inherit|flash_lite|flash|pro
---
```

### Common Traps (Silent Failures)

1. **Invalid `model:` value** — using a concrete model slug (e.g., `gemini-3-flash-agent`) instead of a tier name causes silent drop
2. **`mainAgent: false`** — hides from `agy --agent` and `agy agent` list
3. **`subagent: false`** — blocks `invoke_subagent` from finding it
4. **Missing `name` or `description`** — file silently ignored
5. **`agy agent` in untrusted dir** — use `agy --add-dir . agent` to force scan

### Key Clarifications

- `invoke_subagent` and `agy --agent` are NOT separate systems — both resolve the same `.md` files
- `define_subagent` is only for dynamic, conversation-scoped agents at runtime
- `hidden: true` does NOT prevent resolution — just hides from UI/prompt listing

---

## Sidecars

Sidecars are background processes managed by Antigravity's runtime — scheduled builds, periodic triage, cron-driven health checks — running without a user actively chatting.

### How Antigravity Discovers Sidecars

Antigravity scans specific directories on startup for `sidecar.json` files:

```
~/.gemini/antigravity/sidecars/<name>/sidecar.json    ← Antigravity 2.0
~/.gemini/jetski/sidecars/<name>/sidecar.json          ← Jetski / IDE
```

> **These directories are inherently global.** There is no workspace-scoped sidecar discovery. This is the one place where the "nothing outside the plugin" rule can't fully apply — sidecars must be written to these runtime directories.

In the split-daemon pattern, each enrolled repository has one sidecar at:
```
~/.gemini/config/plugins/<daemon>/sidecars/<owner--repo>/sidecar.json
```

### Sidecar Manifest

```json
{
  "builtin": "schedule",
  "args": ["*/5 * * * *", "python3", "~/.gemini/config/plugins/my-daemon/scripts/tick.py"],
  "restart_policy": "always",
  "display_name": "My Plugin — owner/repo",
  "agent_permissions": {
    "workspace_uris": ["file:///path/to/repo"]
  },
  "env": {
    "MY_PLUGIN_CONFIG": "path/to/config.json",
    "MY_PLUGIN_STATE_DIR": "path/to/state"
  }
}
```

| Field | Description |
|-------|-------------|
| `builtin` | `"schedule"` runs on a cron schedule |
| `args` | `["<cron>", "<command>", "<args>..."]` |
| `restart_policy` | `"always"` continues on future cron ticks |
| `display_name` | Human-readable name for the dashboard |
| `agent_permissions` | `workspace_uris` scopes agent access to the repo |
| `env` | Environment variables available to the spawned process |

### Unified Tick Pattern

Instead of N separate sidecars per enrolled repo (which multiplies quickly), use a single heartbeat sidecar that delegates to a tick receiver:

```
Antigravity Cron (*/5)
       │
       ▼
scripts/tick.py                          ← Daemon process
       │
       ▼
workspace-plugin/scripts/tick_receiver.py  ← Workspace plugin
       ├── triage_dispatcher.py     (runs if elapsed >= interval)
       ├── engineering_dispatcher.py
       ├── review_dispatcher.py
       └── audit_dispatcher.py
```

1. `tick.py` runs in the daemon and forwards to `tick_receiver.py` in the workspace plugin
2. `tick_receiver.py` checks `STATE_DIR/<loop>.last` timestamps against configured intervals
3. If a loop is due, acquires a non-blocking `flock` on `STATE_DIR/<loop>.lock`
4. If locked (already running), skip. If acquired, touch `.last` and launch the dispatcher

### Sidecar Failure Modes

| Failure | Symptom | Root Cause | Fix |
|---------|---------|------------|-----|
| Script not found | Sidecar fires, nothing happens | Install dir moved/deleted | Re-run `install.sh` |
| `agentapi` not found | Script runs, can't spawn conversations | Missing from `PATH` | Add `~/.gemini/antigravity/bin/` to `PATH` |
| Quota exhausted | Conversations spawn but fail; labels churn | LLM API quota exceeded | Circuit breaker (see below) |
| Missing config env | tick_receiver exits immediately | Sidecar `env` block incomplete | Re-run `enroll.sh` |
| In-flight lock | Loop doesn't run when due | Previous run still in progress | Normal — runs on next tick |

---

## Dispatchers — The Non-LLM Control Plane

Dispatchers are the scripts invoked by sidecars. They are **pure control plane logic** — they never call the LLM themselves. Their sole job is to decide whether there's work to do and, if so, spawn an LLM conversation to handle it.

### Why Dispatchers Exist

Running an LLM conversation costs money and burns quota. If the sidecar directly launched an LLM conversation on every cron tick, you'd spend tokens even when there's nothing to do:

```
Cron fires → Dispatcher runs  → No eligible work    → Exit (cost: $0)
Cron fires → Dispatcher runs  → 2 eligible items    → Spawn 2 conversations (cost: ~$0.50)
```

### Spawning Conversations via agentapi

```python
cmd = [
    "agentapi", "new-conversation",
    f"--model={model}",       # flash_lite, flash, pro
    f"--title={title}",
    prompt,
]
r = subprocess.run(cmd, capture_output=True, text=True)
```

Returns `{"conversationId": "ec33ebf9-..."}` on success. Non-zero exit on failure.

**Key:** Always pass an explicit `--model` tier rather than inheriting IDE defaults.

### Circuit Breakers

When LLM quota runs out, dispatchers can enter catastrophic failure loops — spawning, failing, cleaning up, and repeating on every cron tick. This churns labels and spams notifications.

**Pattern:**

```python
MAX_CONSECUTIVE_FAILURES = 3
COOLDOWN_SECONDS = 3600  # 1 hour

# On startup: check cooldown
if COOLDOWN_FILE.exists():
    cooldown = json.loads(COOLDOWN_FILE.read_text())
    if time.time() < cooldown["expires_at"]:
        sys.exit(0)  # Still cooling down
    COOLDOWN_FILE.unlink()  # Cooldown expired
    FAILURE_COUNT_FILE.unlink()

# After spawn loop: track failures
if spawned == 0 and len(eligible) > 0:
    count = increment_failure_count()
    if count >= MAX_CONSECUTIVE_FAILURES:
        write_cooldown_file(reason=f"{count} consecutive failures")
else:
    reset_failure_count()  # Success resets the counter
```

**Cooldown file format:**
```json
{
  "reason": "3 consecutive spawn failures",
  "entered_at": "2026-09-27T14:30:00+00:00",
  "expires_at": 1727447400.0,
  "expires_at_iso": "2026-09-27T15:30:00+00:00"
}
```

**Only count "had work but couldn't do it"** — idle cycles (no eligible work) should NOT increment the failure counter.

---

## Configuration Management

### Config Discovery Patterns

Scripts and hooks need to find per-repo config reliably. Use this priority:

```python
# 1. Environment variable (set by sidecar env block)
config_path = os.environ.get("MY_PLUGIN_CONFIG")

# 2. Workspace path matching (PreToolUse hooks via stdin payload)
# Match workspacePaths against sidecar configs:
# ~/.gemini/config/plugins/my-daemon/sidecars/*/config.json

# 3. Explicit flag override
# python3 scripts/my_gate.py --config /path/to/config.json
```

### State Directory

Runtime state lives alongside config in `sidecars/<repo>/state/`:

```
state/
├── claims/             # Active work item locks
├── dispatches/         # Spawn tracking records
├── *.last              # Loop execution timestamps
├── *.lock              # Inter-process mutex locks
├── *.log               # Execution logs
├── cooldown.json       # Circuit breaker state
└── failure_count       # Consecutive failure counter
```

---

## Anti-Patterns (Learned the Hard Way)

### 1. Writing Directly to Global Config

**Problem:** Wrote hooks, rules, and scripts to `~/.gemini/config/`. A broken global hook bricked every project on the machine.

**Fix:** Use the split-daemon + workspace plugin pattern. Scope hooks and skills to enrolled workspaces via `.agents/plugins.json`.

### 2. Absolute Paths in Config

**Problem:** `config.json` contained `/Users/someone/Documents/Projects/...` — completely non-portable.

**Fix:** Use environment variables (`MY_PLUGIN_CONFIG`) and `workspacePaths` matching. Let the sidecar's `env` block set paths at enrollment time.

### 3. Instance Values in Plugin Root

**Problem:** `config.json` lived in the source repo, dirtying git and preventing multi-project usage.

**Fix:** Per-repo configs in `sidecars/<owner--repo>/config.json`, generated at enrollment.

### 4. No Circuit Breaker

**Problem:** When LLM quota ran out, dispatchers entered rapid failure loops, churning GitHub labels every 10 minutes.

**Fix:** Track consecutive spawn failures. After 3 failures, write a cooldown file and stop dispatching for 1 hour.

### 5. Project-Specific Rules in Generic Plugin

**Problem:** Project-specific tooling rules (e.g., "use PlatformIO MCP") were packaged in the generic plugin.

**Fix:** Keep universal invariants in plugin `rules/AGENTS.md`. Project-specific rules go in consumer `.agents/rules/`.

### 6. N×M Sidecar Explosion

**Problem:** M separate sidecars per enrolled repository × N repos = dozens of cron jobs.

**Fix:** Single heartbeat sidecar per repo, delegating to a tick receiver for interval-based dispatching.

### 7. Hooks on Workspace Plugins

**Problem:** Placed `hooks.json` on the workspace plugin (loaded via `plugins.json`). Hooks were never discovered.

**Fix:** Place `hooks.json` on the daemon plugin (global install). Use the workspace plugin only for skills, rules, and agents.

---

## Checklist: Shipping a Plugin

- [ ] Global daemon manifest exists (`daemon/plugin.json`)
- [ ] Shared workspace plugin manifest exists (`daemon/workspace-plugin/<name>/plugin.json`)
- [ ] Hooks are on the daemon plugin root, not the workspace plugin
- [ ] Hooks use clean relative commands (`python3 scripts/my_gate.py`)
- [ ] Consumer repos reference workspace-plugin via `.agents/plugins.json` with `"entries"` key
- [ ] Single sidecar per repo using the unified tick pattern
- [ ] Sidecar manifests specify `agent_permissions.workspace_uris`
- [ ] Sidecar `env` block provides config and state paths
- [ ] `install.sh` uses `rsync --exclude='sidecars/'` to preserve per-repo data
- [ ] `enroll.sh` creates sidecar, config, state, and registers workspace plugin
- [ ] `unenroll.sh` checks for active work before removing
- [ ] `uninstall.sh` requires `--force` when repos are still enrolled
- [ ] Dispatchers include circuit breaker logic
- [ ] Config contains no absolute paths from the developer's machine
- [ ] Unit tests pass cleanly

---

## Antigravity Version Notes

Hook-relevant changes observed across Antigravity releases:

| Version | Change |
|---------|--------|
| **v1.1.9** | Fixed `PostToolUse` hooks firing on non-tool steps; added continuation cap for `Stop` hooks |
| **v1.1.10** | Updated hook ordering so `hooks.json` hooks run before built-in termination checks |
| **v1.2.1** | Added `excludeDefaultComponents: true` in custom agent frontmatter while preserving `PostInvocation` hooks |
| **v1.2.3–v1.2.4** | Fixed `/hooks` inspection for plugin-bundled hooks; fixed `hooks.json` being dropped during token budget truncation |
