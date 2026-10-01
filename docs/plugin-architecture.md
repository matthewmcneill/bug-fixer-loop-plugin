# Antigravity Plugin Architecture Guide

> Lessons learned from building and deploying the `bug-fixer-loop` plugin.  
> This document captures the practical architecture of Antigravity plugins — what works, what breaks, and why.

---

## Table of Contents

- [Core Concepts](#core-concepts)
- [Directory Layout](#directory-layout)
- [Discovery & Loading](#discovery--loading)
- [The Immutable Plugin Root Principle](#the-immutable-plugin-root-principle)
- [Configuration Management](#configuration-management)
- [Hooks](#hooks)
- [Rules](#rules)
- [Skills](#skills)
- [Sidecars & Dispatchers](#sidecars--dispatchers)
  - [How Antigravity Discovers Sidecars](#how-antigravity-discovers-sidecars)
  - [Sidecar Manifest](#sidecar-manifest-sidecarjson)
  - [Sidecar Execution Model](#sidecar-execution-model)
  - [The `env` Block is Critical](#the-env-block-is-critical)
  - [Sidecar Templates vs Installed Sidecars](#sidecar-templates-vs-installed-sidecars)
  - [Sidecar Lifecycle & Failure Modes](#sidecar-lifecycle--failure-modes)
  - [Dispatcher Architecture](#dispatcher-architecture-the-non-llm-control-plane)
- [Circuit Breakers](#circuit-breakers)
- [Installation Architecture](#installation-architecture)
- [Anti-Patterns](#anti-patterns-learned-the-hard-way)
- [Multi-Project Support](#multi-project-support)
- [Checklist: Shipping a Plugin](#checklist-shipping-a-plugin)

---

## Core Concepts

An Antigravity **plugin** is a namespaced, shareable bundle that packages **skills**, **rules**, **hooks**, and optionally **MCP server configs** and **sidecar definitions** into a single deployable unit. Plugins are the recommended way to distribute complex agent behaviours.

A plugin has two distinct concerns:

| Concern | Where it lives | Mutable? | Committed? |
|---------|---------------|:--------:|:----------:|
| **Plugin code** (scripts, skills, rules, hooks templates) | `.agents/plugins/<name>/` | ❌ Immutable | ✅ Submodule |
| **Instance state** (config, claims, cooldown files) | `.agents/plugins/<name>-workspace/` | ✅ Mutable | Config: ✅ / State: ❌ |

The plugin directory is a **git submodule** (or standalone clone). It is treated as read-only after installation. All mutable state — configuration, runtime locks, circuit breaker files — lives in a separate **workspace directory** alongside it.

---

## Directory Layout

### Standard Layout (Consumer Project)

```
my-project/                                         ← REPO_DIR (derivable)
├── .agents/
│   ├── plugins/
│   │   ├── bug-fixer-loop-plugin/                  ← PLUGIN_DIR (git submodule, immutable)
│   │   │   ├── plugin.json                         ← Manifest (required by Antigravity)
│   │   │   ├── hooks.json                          ← Hook templates with <plugin_dir> placeholders
│   │   │   ├── rules/
│   │   │   │   └── AGENTS.md                       ← Plugin rules (generic, not project-specific)
│   │   │   ├── scripts/
│   │   │   │   ├── install.sh
│   │   │   │   ├── uninstall.sh
│   │   │   │   ├── configure.sh
│   │   │   │   ├── engineering_dispatcher.py
│   │   │   │   ├── triage_dispatcher.py
│   │   │   │   ├── trajectory_dispatcher.py
│   │   │   │   ├── safe_ingest.py
│   │   │   │   └── pre_tool_gate.py
│   │   │   ├── skills/
│   │   │   │   ├── bug-fixer-engineering/SKILL.md
│   │   │   │   ├── bug-fixer-triage/SKILL.md
│   │   │   │   └── ...
│   │   │   ├── sidecars/                           ← Sidecar templates with placeholders
│   │   │   │   ├── bug-fixer-engineering-loop/sidecar.json
│   │   │   │   ├── bug-fixer-triage-loop/sidecar.json
│   │   │   │   ├── bug-fixer-pr-review-loop/sidecar.json
│   │   │   │   └── bug-fixer-trajectory-loop/sidecar.json
│   │   │   └── agents/
│   │   │       └── leak-auditor.md
│   │   │
│   │   └── bug-fixer-loop-workspace/               ← STATE_DIR (mutable, per-project)
│   │       ├── config.json                         ← Instance config (committed ✅)
│   │       ├── .gitignore                          ← Ignores runtime state only
│   │       ├── claims/                             ← Lock files (gitignored)
│   │       ├── cooldown.json                       ← Circuit breaker state (gitignored)
│   │       └── failure_count                       ← Consecutive failure counter (gitignored)
│   │
│   ├── hooks.json                                  ← Workspace-scoped hooks (resolved paths)
│   └── rules/
│       ├── bug-fixer-AGENTS.md                     ← Plugin rules (installed here)
│       └── 05-pio-lock.md                          ← Project-specific rules (human-authored)
│
├── src/
├── docs/
└── ...
```

### Key Principle: Path Derivation

Every path is derivable from the config file's own location:

```
config.json is at:  <repo>/.agents/plugins/bug-fixer-loop-workspace/config.json
                     │
                     ├── parent     = STATE_DIR  (.../bug-fixer-loop-workspace/)
                     ├── parent²    = plugins/
                     ├── parent³    = .agents/
                     └── parent⁴   = REPO_DIR
                     
                     STATE_DIR sibling = PLUGIN_DIR (.../bug-fixer-loop-plugin/)
```

This means `repo_dir` and `plugin_dir` **never need to be stored in config**. Scripts derive them at runtime:

```python
CONFIG_FILE = Path("...workspace/config.json").resolve()
STATE_DIR = CONFIG_FILE.parent
REPO_DIR = STATE_DIR.parent.parent.parent
PLUGIN_DIR = STATE_DIR.parent / "bug-fixer-loop-plugin"
```

---

## Discovery & Loading

Antigravity discovers plugins by traversing the `.agents/plugins/` directory tree. Each plugin directory must contain a `plugin.json` manifest:

```json
{
  "name": "bug-fixer-loop"
}
```

When discovered and enabled, Antigravity automatically ingests:
- **Skills** from `plugins/<name>/skills/` (progressive disclosure — name + description loaded, full content on activation)
- **Rules** from `plugins/<name>/rules/` (merged into active rule set)
- **Hooks** from `plugins/<name>/hooks.json` (registered for lifecycle events)
- **MCP servers** from `plugins/<name>/mcp_config.json` (launched and tools exposed)

### Loading Priority (highest → lowest)

1. **Workspace project** — `.agents/` walking up from CWD to repo root
2. **Declared configs** — explicit `skills.json` or `plugins.json`
3. **Global discovery** — `~/.gemini/config/`
4. **Built-in customisations** — bundled with Antigravity
5. **Global declared configs** — global JSON configs

Workspace-scoped customisations always win over global ones.

### Enabling / Disabling

Plugin state is recorded in `~/.gemini/config/config.json`:

```json
{
  "plugins": {
    "bug-fixer-loop-plugin": { "enabled": true }
  }
}
```

Toggle via CLI: `agy plugin enable bug-fixer-loop` / `agy plugin disable bug-fixer-loop`.

---

## The Immutable Plugin Root Principle

> **The plugin directory is read-only after installation.**

This is the single most important architectural rule. The plugin directory (`bug-fixer-loop-plugin/`) is typically a git submodule. Nothing should write to it:

- ❌ No `config.json` with instance values
- ❌ No runtime state files (claims, locks, cooldowns)
- ❌ No `install.sh` writing back into it
- ❌ No sed/templating modifying files in-place

Scripts, skills, and agents are **used in-place** — never copied to global directories. The plugin root is the single source of truth for code.

---

## Configuration Management

### What Goes in `config.json`

The workspace config contains **project settings only** — no paths, no machine-specific values:

```json
{
  "$schema": "bug-fixer-loop-config-v1",
  "target_repo": "owner/repo-name",
  "maintainer": "github-login",
  "trusted_users": [],
  "spec_file_path": "docs/design_specification.md",
  "protected_files": ["docs/design_specification.md"],
  "leak_patterns": ["ya29\\.[A-Za-z0-9_-]+", "AKIA[0-9A-Z]{16}"],
  "max_concurrent_prs": 4,
  "test_command": "pio test -e native",
  "lint_command": "",
  "pr_prefix": "[BugFixer]",
  "branch_prefix": "fix",
  "triage_cron": "0 * * * *",
  "engineering_cron": "*/10 * * * *",
  "architect_cron": "*/15 * * * *",
  "trajectory_review_cron": "0 18 * * *",
  "triage_model": "flash",
  "engineering_model": "pro",
  "architect_model": "pro",
  "trajectory_model": "flash"
}
```

This file is **committed to the consumer repo** — it documents the project's bug-fixer setup for the whole team.

### What Gets Gitignored

Only runtime state is gitignored (via `.agents/plugins/bug-fixer-loop-workspace/.gitignore`):

```gitignore
claims/
cooldown.json
failure_count
triage_cooldown.json
triage_failure_count
```

### Config Discovery in Scripts

Scripts find the config through a standard search:

```python
# Dispatchers (invoked from known location within plugin):
WORKSPACE_CONFIG = PLUGIN_DIR / "../../bug-fixer-loop-workspace/config.json"

# CLI tools (invoked from anywhere):
candidates = [
    plugin_dir.parent / "bug-fixer-loop-workspace" / "config.json",   # standard layout
    Path.cwd() / ".agents/plugins/bug-fixer-loop-workspace/config.json",  # cwd fallback
]

# Override via environment:
BUG_FIXER_CONFIG=/path/to/config.json python3 scripts/safe_ingest.py ...
```

---

## Hooks

Hooks are lifecycle interceptors that run external commands at specific points in the agent's execution. The plugin defines hooks in `hooks.json` with **placeholder paths**:

```json
{
  "bug-fixer-file-gate": {
    "PreToolUse": [{
      "matcher": "write_to_file|replace_file_content",
      "hooks": [{
        "type": "command",
        "command": "python3 <plugin_dir>/scripts/pre_tool_gate.py",
        "timeout": 10
      }]
    }]
  }
}
```

### Path Resolution

The `<plugin_dir>` placeholder is resolved by `install.sh` at install time, producing a workspace-scoped `hooks.json` at `.agents/hooks.json`.

**Critical rule:** Hooks must be installed to the **workspace** `.agents/hooks.json`, never to the global `~/.gemini/config/hooks.json`. A broken global hook bricks every project on the machine.

### Hook Working Directory

The hook command's working directory is set to the directory containing `hooks.json`. If hooks are installed at `.agents/hooks.json`, the cwd is `.agents/`.

### PreToolUse Contract

The `pre_tool_gate.py` hook receives tool call details on stdin and returns a decision on stdout:

```json
// stdin (from Antigravity)
{
  "toolCall": {"name": "write_to_file", "args": {"TargetFile": "/path/to/file.py"}},
  "conversationId": "...",
  "workspacePaths": ["/path/to/repo"]
}

// stdout (from hook script)
{"decision": "allow"}     // or "deny" with "reason"
```

---

## Rules

Plugin rules live at `plugins/<name>/rules/AGENTS.md`. They are **generic** — applicable to any project using the plugin.

**Project-specific rules** (e.g., PlatformIO MCP lock protocol, framework-specific testing rules) belong in the consumer project's `.agents/rules/`, not in the plugin.

Example separation:

| Rule | Where | Why |
|------|-------|-----|
| "Never auto-merge PRs" | Plugin `rules/AGENTS.md` | Universal to all bug-fixer consumers |
| "Use PlatformIO MCP for builds" | Consumer `.agents/rules/05-pio-lock.md` | Only applies to PlatformIO projects |
| "Run `npm test` before PRs" | Consumer `.agents/rules/` | Only applies to Node.js projects |

The plugin's Step 9 (Verify) defers to project rules:
> *"Respect any project-level rules about build/test tooling. If the project rules mandate using MCP server tools instead of CLI commands, follow them."*

---

## Skills

Skills are multi-step agent procedures defined in `skills/<name>/SKILL.md`. They are **used in-place** from the plugin directory — never copied to global config.

Antigravity discovers skills via progressive disclosure:
1. Only the skill's **name and description** (from YAML frontmatter) are loaded into context
2. The **full content** is loaded only when the agent or user activates the skill

### Skill Path References

Skills reference plugin scripts using `PLUGIN_DIR`, which is resolved at runtime:

```markdown
## Step 0: Configuration Guard
PLUGIN_DIR="$(cd "$(dirname "<this_skill_file>")/../.." && pwd)"
STATE_DIR="<workspace_root>/.agents/plugins/bug-fixer-loop-workspace"
```

---

## Sidecars & Dispatchers

Sidecars are **background processes managed by Antigravity's runtime**. They are the mechanism for running recurring automated tasks — scheduled builds, periodic issue triage, cron-driven health checks — without requiring a user to be actively chatting.

### How Antigravity Discovers Sidecars

Antigravity scans specific directories on startup for `sidecar.json` files:

```
~/.gemini/antigravity/sidecars/<name>/sidecar.json    ← Antigravity 2.0
~/.gemini/jetski/sidecars/<name>/sidecar.json          ← Jetski / IDE
```

> **These directories are inherently global.** There is no workspace-scoped sidecar discovery. This is the one place where the "nothing outside the plugin" rule can't fully apply — sidecars must be written to these runtime directories or Antigravity won't find them.

Each `<name>/` subdirectory represents one sidecar process. Antigravity reads the `sidecar.json` manifest and manages the process lifecycle.

### Sidecar Manifest (`sidecar.json`)

A sidecar manifest defines what to run, how to schedule it, and what environment to provide:

```json
{
  "builtin": "schedule",
  "args": ["*/10 * * * *", "/path/to/scripts/engineering_dispatcher.py"],
  "restart_policy": "always",
  "display_name": "Bug Fixer Engineering (owner/repo)",
  "description": "Checks for eligible issues every 10 min.",
  "env": {
    "ANTIGRAVITY_PROJECT_ID": "project-uuid",
    "PATH": "/usr/local/bin:/usr/bin:...",
    "BUG_FIXER_CONFIG": "/path/to/workspace/config.json"
  }
}
```

| Field | Type | Description |
|-------|------|-------------|
| `builtin` | string | The sidecar type. `"schedule"` runs on a cron schedule. |
| `args` | array | For `schedule`: `[cron_expression, script_path]`. The script is invoked by the scheduler on each cron tick. |
| `restart_policy` | string | `"always"` restarts the sidecar if it crashes. |
| `display_name` | string | Human-readable name shown in the Antigravity UI/dashboard. |
| `description` | string | Shown in sidecar listings. |
| `env` | object | Environment variables injected into the process. **Critical** — see below. |

### Sidecar Execution Model

When a `schedule` sidecar fires:

1. **Antigravity's scheduler** wakes up at the cron interval
2. **Forks a subprocess** running the script specified in `args[1]`
3. The script runs as a **plain Python process** — it is NOT an LLM conversation
4. The script's `stdout`/`stderr` go to Antigravity's sidecar logs
5. The script is expected to **exit quickly** (seconds, not minutes)
6. If the script needs to do LLM work, it spawns a **new conversation** via `agentapi new-conversation`
7. The `restart_policy: "always"` means the scheduler continues firing on the next cron tick even if the script exits with an error

### The `env` Block is Critical

**Sidecars do NOT inherit the user's shell environment.** The sidecar process gets a minimal environment from Antigravity's runtime. Without an explicit `env` block:

- **`PATH` is minimal** — `agentapi`, `python3`, `gh`, and other CLI tools won't be found
- **`ANTIGRAVITY_PROJECT_ID` is unset** — `agentapi new-conversation` fails because it doesn't know which project to spawn into
- **`BUG_FIXER_CONFIG` is unset** — scripts can't find the workspace config

This was the root cause of a production failure: the sidecar templates shipped without `env`, so the installed sidecars pointed to scripts that couldn't find `agentapi` on the `PATH`. All 9 sidecar configs (3 sidecars × 3 directories) were silently failing on every cron tick.

#### Required `env` Variables

| Variable | Purpose | How to resolve |
|----------|---------|---------------|
| `ANTIGRAVITY_PROJECT_ID` | Identifies the project for `agentapi` conversation spawning | Detected at install time from `~/.gemini/config/projects/*.json` or from the active Antigravity context |
| `PATH` | Full `PATH` including `~/.gemini/antigravity/bin/` (for `agentapi`) and `/usr/local/bin` (for `python3`, `gh`) | Snapshot of `$PATH` at install time |
| `BUG_FIXER_CONFIG` | Path to the workspace config file | Set to `<repo>/.agents/plugins/bug-fixer-loop-workspace/config.json` |

#### Belt-and-Suspenders `PATH` Fix

Even with the `env` block, the dispatchers have a runtime fallback:

```python
# Ensure agentapi is on PATH — sidecar scheduler may not inherit user's shell PATH
AGENTAPI_DIR = Path.home() / ".gemini" / "antigravity" / "bin"
if AGENTAPI_DIR.is_dir():
    os.environ["PATH"] = str(AGENTAPI_DIR) + os.pathsep + os.environ.get("PATH", "")
```

This is defensive — the `env` block in the sidecar template is the proper solution, but keeping both provides resilience against `PATH` being partially set.

### Sidecar Templates vs Installed Sidecars

The plugin repo contains **template** sidecar files with placeholders. The installer resolves them to absolute paths:

```
plugin-repo/sidecars/bug-fixer-engineering-loop/sidecar.json           ← Template (placeholders)
~/.gemini/antigravity/sidecars/bug-fixer-engineering-loop/sidecar.json  ← Installed (resolved)
```

Placeholders resolved by `install.sh`:

| Placeholder | Resolved to | Example |
|-------------|-------------|---------|
| `<engineering_cron>` | Cron expression from config | `*/10 * * * *` |
| `<triage_cron>` | Cron expression from config | `0 * * * *` |
| `<architect_cron>` | Cron expression from config | `*/15 * * * *` |
| `<trajectory_review_cron>` | Cron expression from config | `0 18 * * *` |
| `<plugin_dir>` | Absolute path to the plugin directory | `/Users/me/project/.agents/plugins/bug-fixer-loop-plugin` |
| `<owner/repo>` | GitHub `owner/repo` slug | `matthewmcneill/esp32-app-foundation` |
| `<project_id>` | `ANTIGRAVITY_PROJECT_ID` for conversation spawning | `a1b2c3d4-...` |
| `<path>` | Full `$PATH` snapshot from install time | `/usr/local/bin:/usr/bin:...` |
| `<config_path>` | Absolute path to workspace `config.json` | `/Users/me/project/.agents/plugins/bug-fixer-loop-workspace/config.json` |

### Sidecar Lifecycle & Failure Modes

Understanding sidecar failure modes is essential for debugging:

| Failure Mode | Symptom | Root Cause | Fix |
|-------------|---------|-----------|-----|
| Script not found | Sidecar fires but nothing happens (silent fail) | `<plugin_dir>` resolved to wrong path, or plugin dir deleted/moved | Re-run `install.sh` to re-resolve paths |
| `agentapi` not found | Script runs but can't spawn conversations | `PATH` doesn't include `~/.gemini/antigravity/bin/` | Add `PATH` to sidecar `env` block |
| Project ID missing | `agentapi new-conversation` returns empty/error | `ANTIGRAVITY_PROJECT_ID` not set in `env` | Add `ANTIGRAVITY_PROJECT_ID` to sidecar `env` block |
| Quota exhausted | Conversations spawn but immediately fail; labels churn on GitHub | LLM API quota exceeded | Implement circuit breaker (see below) |
| Config not found | Script exits with "No workspace config found" | Config path changed or workspace dir deleted | Re-run `install.sh` or set `BUG_FIXER_CONFIG` env var |
| Global hook bricked | Every tool call in every project is blocked | Broken absolute path in `~/.gemini/config/hooks.json` pointing to deleted script | Never write hooks globally; use workspace-scoped hooks only |
| All sidecars point to missing paths | All 9 configs (3 × 3 dirs) silently fail | `install.sh` resolved paths against a different directory layout than the final one | Verify paths after install; re-run if plugin moves |

### The Three Plugin Sidecars

| Sidecar | Default Schedule | Script | What it Does |
|---------|:---------------:|--------|-------------|
| `bug-fixer-engineering-loop` | `*/10 * * * *` | `engineering_dispatcher.py` | Checks for `auto-fixable` issues, claims them, spawns fix conversations |
| `bug-fixer-triage-loop` | `0 * * * *` | `triage_dispatcher.py` | Checks for unlabelled issues, spawns triage conversations to classify them |
| `bug-fixer-pr-review-loop` | `<architect_cron>` | `architect_dispatcher.py` | Polls for un-reviewed draft PRs, spawns architect review conversations |
| `bug-fixer-trajectory-loop` | `0 18 * * *` | `trajectory_dispatcher.py` | Reviews recent bug-fixer transcripts for quality, posts trajectory analysis |

---

### Dispatcher Architecture (The Non-LLM Control Plane)

Dispatchers are the Python scripts invoked by sidecars. They are **pure control plane logic** — they never call the LLM themselves. Their sole job is to decide whether there's work to do and, if so, spawn an LLM conversation to handle it.

#### Why This Matters

Running an LLM conversation costs money and burns quota. If the sidecar directly launched an LLM conversation on every cron tick, you'd spend tokens even when there's nothing to do. The dispatcher pattern keeps costs at zero when idle:

```
Cron fires → Dispatcher runs  → No eligible issues → Exit immediately (cost: $0)
Cron fires → Dispatcher runs  → 2 eligible issues  → Spawn 2 conversations (cost: ~$0.50)
```

#### Dispatcher Flow (Engineering — the most complex)

```
1.  Load config from workspace (WORKSPACE_CONFIG only — no plugin root fallback)
2.  Derive REPO_DIR, STATE_DIR, PLUGIN_DIR from config file location
3.  Check circuit breaker — if cooldown.json exists and not expired, exit immediately
4.  Recover stale claims (3-layer detection):
    a. Claim file exists but has no conversation_id (spawn failed mid-write)
    b. Claim file older than TTL (conversation ran too long or was killed)
    c. Conversation exists but is in terminal state (idle/errored/cancelled)
5.  Count active bug-fixer PRs via safe_ingest.py — if >= max_concurrent, exit
6.  Query GitHub for eligible issues (via safe_ingest.py):
    - Has "auto-fixable" label
    - Not in exclusion_labels (e.g., "human-required", "P1-blocker", "hardware-required")
    - Not already claimed (no active claim file)
    - Sorted by priority (milestone boost, label priority ranking)
7.  For each eligible issue (up to max_concurrent - active_prs):
    a. Write claim file to STATE_DIR/claims/<issue_number>.json (atomic lock)
    b. Add "status:in-progress" label to the GitHub issue
    c. Build the skill prompt (read SKILL.md, inject config values)
    d. Spawn LLM conversation via: agentapi new-conversation --model=<tier> --title=<title> <prompt>
    e. Capture conversation_id from agentapi stdout
    f. Update claim file with conversation_id
    g. If spawn fails: remove claim file, remove label, log error
8.  Track spawn success/failure for circuit breaker
9.  Log summary and exit
```

#### Spawning Conversations via `agentapi`

The dispatchers use `agentapi new-conversation` to launch LLM sessions:

```python
cmd = [
    "agentapi", "new-conversation",
    f"--model={model}",
    f"--title={title}",
    prompt,
]
r = subprocess.run(cmd, capture_output=True, text=True)
```

The `agentapi` tool is Antigravity's CLI for programmatic conversation management. It:
- Creates a new conversation in the Antigravity runtime
- Passes an explicit `--model` tier (`flash_lite`, `flash`, `pro`) based on role defaults or triage complexity labels, avoiding inheritance of arbitrary IDE defaults
- Injects the prompt containing instructions, paths, and issue/PR context
- Returns a JSON object with `conversationId` that can be used to monitor progress
- Requires `ANTIGRAVITY_PROJECT_ID` to know which project context to use

A successful spawn returns:
```json
{"conversationId": "ec33ebf9-0cba-4100-8142-c61503f6c587"}
```

A failed spawn (quota exhausted, missing env vars, etc.) returns a non-zero exit code and error text on stderr.

#### Stale Claim Recovery

The engineering dispatcher implements 3-layer stale claim detection to handle conversations that crash, timeout, or are killed:

```python
# Layer 1: Claim file has no conversation ID (spawn failed mid-write)
if not claim_data.get("conversation_id"):
    recover(claim)

# Layer 2: Claim file older than TTL (conversation ran too long)
if claim_age > CLAIM_TTL_SECONDS:
    recover(claim)

# Layer 3: Conversation exists but is in terminal state
status = get_conversation_status(conversation_id)
if status in ("idle", "errored", "cancelled"):
    recover(claim)
```

Recovery means:
1. Remove the claim file from `STATE_DIR/claims/`
2. Remove the `status:in-progress` label from the GitHub issue
3. The issue becomes eligible for the next engineering cycle

#### Claim File Format

```json
{
  "issue_number": 42,
  "conversation_id": "ec33ebf9-0cba-4100-8142-c61503f6c587",
  "claimed_at": "2026-09-27T14:30:00+00:00",
  "branch": "fix/42-sensor-timeout"
}
```

---

## Circuit Breakers

When the LLM quota runs out, dispatchers can enter a catastrophic failure loop:

1. **Engineering loop fires** (every 10 minutes)
2. **Finds eligible issues**, writes claim files, adds `status:in-progress` labels
3. **Spawns `agentapi new-conversation`** — all calls fail (quota exhausted)
4. **Removes claim files**, removes labels (cleanup on failure)
5. **Next cycle fires** — same issues are eligible again
6. **Labels churn** on GitHub (added/removed every 10 minutes), user is spammed with dozens of failing conversation notifications
7. **Repeat indefinitely** until quota is restored or the sidecar is manually stopped

### Implementation

Each dispatcher tracks consecutive spawn failures using two files in the workspace state directory:

| File | Purpose | Format |
|------|---------|--------|
| `failure_count` | Number of consecutive cycles where all spawns failed | Plain text integer |
| `cooldown.json` | Present only when cooling down | JSON with `reason`, `entered_at`, `expires_at`, `expires_at_iso` |

#### Startup Check

```python
COOLDOWN_FILE = STATE_DIR / "cooldown.json"
FAILURE_COUNT_FILE = STATE_DIR / "failure_count"
MAX_CONSECUTIVE_FAILURES = 3
COOLDOWN_SECONDS = 3600  # 1 hour

# Circuit breaker: check cooldown before doing any work
if COOLDOWN_FILE.exists():
    try:
        cooldown = json.loads(COOLDOWN_FILE.read_text())
        if time.time() < cooldown.get("expires_at", 0):
            log(f"ENGINEERING_EXIT: Cooling down until {cooldown['expires_at_iso']} — "
                f"reason: {cooldown['reason']}")
            sys.exit(0)
        # Cooldown expired, clean up and resume normal operation
        COOLDOWN_FILE.unlink(missing_ok=True)
        FAILURE_COUNT_FILE.unlink(missing_ok=True)
    except (json.JSONDecodeError, OSError):
        COOLDOWN_FILE.unlink(missing_ok=True)
```

#### Post-Spawn Tracking

```python
# After the spawn loop completes:
if spawned == 0 and len(eligible) > 0:
    # Had work to do but couldn't spawn anything — this is a failure
    try:
        count = int(FAILURE_COUNT_FILE.read_text().strip()) + 1 \
            if FAILURE_COUNT_FILE.exists() else 1
    except (ValueError, OSError):
        count = 1
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    FAILURE_COUNT_FILE.write_text(str(count))
    if count >= MAX_CONSECUTIVE_FAILURES:
        expires = time.time() + COOLDOWN_SECONDS
        COOLDOWN_FILE.write_text(json.dumps({
            "reason": f"{count} consecutive spawn failures",
            "entered_at": datetime.now(timezone.utc).isoformat(),
            "expires_at": expires,
            "expires_at_iso": datetime.fromtimestamp(expires, timezone.utc).isoformat(),
        }))
        log(f"Circuit breaker tripped after {count} consecutive failures. "
            f"Cooling down for {COOLDOWN_SECONDS}s.")
else:
    # At least one spawn succeeded, or no eligible work — reset counter
    FAILURE_COUNT_FILE.unlink(missing_ok=True)
```

#### Cooldown File Format

```json
{
  "reason": "3 consecutive spawn failures",
  "entered_at": "2026-09-27T14:30:00+00:00",
  "expires_at": 1727447400.0,
  "expires_at_iso": "2026-09-27T15:30:00+00:00"
}
```

#### Behaviour Summary

| Cycle # | Eligible Issues | Spawned | Failure Count | Action |
|:------:|:--------------:|:-------:|:-------------:|--------|
| 1 | 3 | 0 (quota fail) | 1 | Continue |
| 2 | 3 | 0 (quota fail) | 2 | Continue |
| 3 | 3 | 0 (quota fail) | 3 | **Trip breaker** — write `cooldown.json`, stop for 1 hour |
| 4–9 | — | — | — | Exit immediately (cooldown active) |
| 10 | 3 | 2 (quota restored) | 0 | Reset counter, normal operation resumes |
| 11 | 0 | 0 (no work) | 0 | No increment — counter only tracks "had work but couldn't do it" |

## Installation Architecture

### What `install.sh` Does

1. Creates workspace config at `<repo>/.agents/plugins/bug-fixer-loop-workspace/config.json`
2. Installs workspace-scoped hooks at `<repo>/.agents/hooks.json`
3. Copies plugin rules to `<repo>/.agents/rules/`
4. Writes resolved sidecar configs to Antigravity runtime directories
5. Creates `.gitignore` for runtime state

### What `install.sh` Does NOT Do

- ❌ Write to `~/.gemini/config/scripts/`
- ❌ Write to `~/.gemini/config/skills/`
- ❌ Write to `~/.gemini/config/rules/`
- ❌ Write to `~/.gemini/config/agents/`
- ❌ Write to `~/.gemini/config/hooks.json`
- ❌ Copy scripts, skills, or agents anywhere
- ❌ Modify any file in the plugin directory

### Uninstallation

`scripts/uninstall.sh` removes all artefacts from global directories. It's safe to run — only removes known plugin artefacts, never user files.

---

## Anti-Patterns (Learned the Hard Way)

### 1. Writing to Global Config

**Problem:** `install.sh` wrote hooks, rules, scripts, and skills to `~/.gemini/config/`. A broken global hook bricked every project on the machine. When an agent ran `git clean` and deleted the plugin directory, the global hook pointed to a missing script, blocking all tool execution across the entire installation.

**Fix:** Only write workspace-scoped artefacts. Scripts and skills are used in-place.

### 2. Absolute Paths in Config

**Problem:** `config.json` contained `/Users/mcneillm/Documents/Projects/...` — completely non-portable. Can't be committed, can't be shared.

**Fix:** Derive paths from the config file's own location. The standardised directory layout makes every path computable at runtime.

### 3. Instance Values in Plugin Root

**Problem:** `config.json` with `target_repo`, `repo_dir`, and `maintainer` lived in the plugin root. This meant:
- Dirty submodule after configuration
- Instance values committed to the plugin repo
- Only one project could use the plugin at a time

**Fix:** Config lives in the workspace directory (sibling of the plugin submodule). Plugin root contains no instance-specific files.

### 4. No Circuit Breaker

**Problem:** When LLM quota ran out, the engineering dispatcher fired every 10 minutes, spawned conversations that all failed, churned issue labels on GitHub, and spammed the user with failing conversations.

**Fix:** Track consecutive spawn failures. After 3 failures, write a cooldown file and stop dispatching for 1 hour.

### 5. Project-Specific Rules in Generic Plugin

**Problem:** Rule 16 ("PlatformIO MCP Lock Discipline") was in the plugin's `rules/AGENTS.md`. Every consumer — including Node.js, Rails, or plain Python projects — inherited a PlatformIO-specific rule.

**Fix:** Replace with a generic "Respect Project Testing Rules" rule. PlatformIO specifics go in the consumer's `.agents/rules/`.

### 6. Copying Files Instead of Using In-Place

**Problem:** `install.sh` copied skills to `~/.gemini/config/skills/` AND `~/.gemini/antigravity/skills/`, with `sed` templating to bake in absolute paths. This created 12+ duplicate skill directories that drifted out of sync with the source.

**Fix:** Skills are used directly from the plugin directory. Antigravity discovers them via the plugin's `plugin.json` manifest.

### 7. Missing Sidecar `env` Block

**Problem:** Sidecar templates had no `env` field. The sidecar scheduler doesn't inherit the user's shell `PATH`, so `agentapi` couldn't be found.

**Fix:** Add explicit `env` block with `ANTIGRAVITY_PROJECT_ID`, `PATH`, and `BUG_FIXER_CONFIG`.

---

## Multi-Project Support

The workspace-scoped architecture supports multiple projects using the same plugin simultaneously:

```
~/projects/project-a/
  .agents/plugins/
    bug-fixer-loop-plugin/           → git submodule (same repo)
    bug-fixer-loop-workspace/
      config.json                    → target_repo: owner/project-a

~/projects/project-b/
  .agents/plugins/
    bug-fixer-loop-plugin/           → git submodule (same repo)
    bug-fixer-loop-workspace/
      config.json                    → target_repo: owner/project-b
```

Each project has its own config, its own claims directory, its own circuit breaker state. No conflicts. The only shared global artefacts are the sidecar runtime configs (which is how Antigravity discovers sidecars).

---

## Checklist: Shipping a Plugin

- [ ] `plugin.json` manifest exists in plugin root
- [ ] Plugin root contains **no instance-specific files** (no `config.json` with real values)
- [ ] All paths in templates use `<plugin_dir>`, `<owner/repo>`, etc. — never absolute paths
- [ ] `hooks.json` uses `<plugin_dir>` placeholder, resolved by installer
- [ ] Rules are generic — project-specific rules belong in consumer's `.agents/rules/`
- [ ] Skills reference `PLUGIN_DIR` and `STATE_DIR`, not hardcoded paths
- [ ] Sidecar templates include `env` block with `ANTIGRAVITY_PROJECT_ID`, `PATH`
- [ ] `install.sh` writes ONLY to consumer's `.agents/` (workspace-scoped)
- [ ] `install.sh` does NOT write to `~/.gemini/config/` (global)
- [ ] `install.sh` does NOT modify any file in the plugin directory
- [ ] Dispatchers have circuit breaker logic for quota exhaustion
- [ ] Dispatchers derive `REPO_DIR` from config file location, not from config fields
- [ ] `uninstall.sh` exists to clean up any global artefacts
- [ ] `.gitignore` in workspace dir ignores runtime state but NOT `config.json`
- [ ] Tests use temp config fixtures, not files from plugin root
