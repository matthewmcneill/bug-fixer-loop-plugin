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
  - [Unified Tick Execution Model](#unified-tick-execution-model)
  - [Sidecar Lifecycle & Failure Modes](#sidecar-lifecycle--failure-modes)
  - [Dispatcher Architecture](#dispatcher-architecture-the-non-llm-control-plane)
- [Circuit Breakers](#circuit-breakers)
- [Installation Architecture](#installation-architecture)
- [Anti-Patterns](#anti-patterns-learned-the-hard-way)
- [Multi-Project Support](#multi-project-support)
- [Checklist: Shipping a Plugin](#checklist-shipping-a-plugin)

---

## Core Concepts

The bug-fixer architecture follows the **Split Daemon + Workspace Plugin** pattern.

Instead of cloning the plugin as a submodule into every consumer repo and maintaining separate global sidecars for every loop, the system splits cleanly into two distinct components:

| Component | Role | Where it lives | Scope |
|-----------|------|---------------|-------|
| **Global Daemon Plugin** (`bug-fixer-daemon`) | Scheduler host & background heartbeat runner | `~/.gemini/config/plugins/bug-fixer-daemon/` | Global (installed once per machine) |
| **Shared Workspace Plugin** (`bug-fixer`) | Execution bundle: hooks, rules, skills, agents, dispatchers | `~/.gemini/config/plugins/bug-fixer-daemon/workspace-plugin/bug-fixer/` | Workspace (loaded per-repo via `.agents/plugins.json`) |
| **Per-Repo Sidecars & State** | Cron sidecar, per-repo config, lock/claim state | `~/.gemini/config/plugins/bug-fixer-daemon/sidecars/<repo-slug>/` | Per-repo (managed by daemon) |

### Key Benefits
1. **Zero workspace clutter:** Consumer repositories do not need git submodules or copied scripts. They only need a lightweight reference in `.agents/plugins.json`.
2. **Single sidecar per repo:** Instead of 4 separate sidecars per enrolled repository (which multiplied quickly across repos), each enrolled repo has **one unified scheduled sidecar** running `tick.py`.
3. **Clean lifecycle management:** Enrolling and unenrolling repositories is performed via dedicated CLI scripts (`enroll.sh` and `unenroll.sh`) without touching source repos directly.

---

## Directory Layout

### 1. Source Repository Layout

```
bug-fixer-loop-plugin/ (Source Repository)
├── daemon/
│   ├── plugin.json                               ← Global daemon manifest
│   ├── scripts/
│   │   └── tick.py                              ← Minimal heartbeat runner
│   └── workspace-plugin/
│       └── bug-fixer/                            ← Workspace plugin bundle
│           ├── plugin.json                       ← Workspace plugin manifest
│           ├── hooks.json                        ← PreToolUse and PostToolUse security hooks
│           ├── rules/
│           │   ├── AGENTS.md                     ← Universal safety invariants
│           │   └── github-reads.md               ← Safe read guidelines
│           ├── skills/                           ← Agent skills (triage, engineering, etc.)
│           ├── agents/
│           │   └── leak-auditor.md               ← Leak audit agent definition
│           └── scripts/                          ← Dispatchers, gates, APIs
└── scripts/
    ├── install.sh                                ← Installs daemon to ~/.gemini/config/plugins/
    ├── enroll.sh                                 ← Enrolls a repository into the daemon
    ├── unenroll.sh                               ← Unenrolls a repository from the daemon
    └── uninstall.sh                              ← Uninstalls the daemon and cleans global state
```

### 2. Installed Daemon Layout (`~/.gemini/config/plugins/bug-fixer-daemon/`)

```
~/.gemini/config/plugins/bug-fixer-daemon/
├── plugin.json                                   ← Global daemon manifest
├── scripts/
│   └── tick.py                                   ← Sidecar tick entry point
├── sidecars/                                     ← Sidecar registry for enrolled repos
│   └── <owner--repo>/                            ← Slugified repository identifier
│       ├── sidecar.json                          ← Antigravity cron sidecar manifest (runs tick.py)
│       ├── config.json                           ← Per-repo settings (repo_dir, models, intervals)
│       └── state/                                ← Per-repo runtime state
│           ├── claims/                           ← Active issue lock files
│           ├── dispatches/                       ← Dispatch tracking records
│           ├── *.last                            ← Loop execution markers
│           └── *.lock                            ← Non-blocking process locks
└── workspace-plugin/
    └── bug-fixer/                                ← Shared workspace plugin directory
```

### 3. Consumer Repository Layout

```
my-project/                                       ← Enrolled repository
├── .agents/
│   └── plugins.json                              ← Declares dependency on workspace-plugin
├── src/
└── ...
```

---

## Discovery & Loading

### Consumer Repo: Declaring the Workspace Plugin

A consumer project enables the bug-fixer capabilities by declaring the workspace plugin in its `.agents/plugins.json` file:

```json
{
  "entries": [
    {
      "path": "~/.gemini/config/plugins/bug-fixer-daemon/workspace-plugin"
    }
  ]
}
```

> **Important:**
> - The JSON structure requires the `"entries"` key (an array of objects), **not** `"plugins"`.
> - The `"path"` field points to the directory containing the plugin (`workspace-plugin`), which Antigravity scans one level deep for plugin manifests (discovering `workspace-plugin/bug-fixer/plugin.json`).

When Antigravity opens the consumer project, it discovers `bug-fixer` and ingests:
- **Skills** from `workspace-plugin/bug-fixer/skills/`
- **Rules** from `workspace-plugin/bug-fixer/rules/`
- **Hooks** from `workspace-plugin/bug-fixer/hooks.json`
- **Agents** from `workspace-plugin/bug-fixer/agents/`

### Loading Priority (highest → lowest)

1. **Workspace project** — `.agents/` walking up from CWD to repo root
2. **Declared configs** — explicit `skills.json` or `plugins.json`
3. **Global discovery** — `~/.gemini/config/`
4. **Built-in customisations** — bundled with Antigravity
5. **Global declared configs** — global JSON configs

### Enabling / Disabling

The daemon plugin itself is registered globally in `~/.gemini/config/plugins/bug-fixer-daemon/`.

Its status is recorded in `~/.gemini/config/config.json`:

```json
{
  "plugins": {
    "bug-fixer-daemon": { "enabled": true }
  }
}
```

Toggle via CLI:
```bash
agy plugin enable bug-fixer-daemon
agy plugin disable bug-fixer-daemon
```

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
  "spec_file_path": "docs/plugin-architecture.md",
  "protected_files": ["docs/plugin-architecture.md"],
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

This file is generated per enrolled repository by `enroll.sh` at:
```
~/.gemini/config/plugins/bug-fixer-daemon/sidecars/<owner--repo>/config.json
```

### State Directory (`state/`)

Runtime execution state lives alongside configuration in `sidecars/<owner--repo>/state/`:

```
sidecars/<owner--repo>/state/
├── claims/                 # Issue claim lock files
├── dispatches/             # Spawn tracking records
├── *.last                  # Loop execution timestamps
├── *.lock                  # Inter-process mutex locks
├── *.log                   # Execution logs
├── cooldown.json           # Circuit breaker state (when tripped)
└── failure_count           # Consecutive failure counter
```

### Config Discovery in Scripts

Scripts and hooks discover the target configuration dynamically:

```python
# 1. Environment variable (set by sidecar env block):
config_path = os.environ.get("BUG_FIXER_CONFIG")

# 2. Workspace path resolution (PreToolUse hooks via stdin payload):
# The hook receives workspacePaths from Antigravity and matches against
# sidecar configs at ~/.gemini/config/plugins/bug-fixer-daemon/sidecars/*/config.json

# 3. Explicit flag override:
# python3 scripts/pre_tool_gate.py --config /path/to/config.json
```

---

## Hooks

Hooks are lifecycle interceptors that run external commands at specific points in the agent's execution. The workspace plugin defines hooks in `workspace-plugin/bug-fixer/hooks.json` using **clean relative paths**:

```json
{
  "bug-fixer-file-gate": {
    "PreToolUse": [{
      "matcher": "write_to_file|replace_file_content",
      "hooks": [{
        "type": "command",
        "command": "python3 scripts/pre_tool_gate.py",
        "timeout": 10
      }]
    }]
  }
}
```

### Loading via `plugins.json`

Because the consumer repository registers the workspace plugin via `.agents/plugins.json`, Antigravity loads these hooks directly. There is **no template placeholder resolution** and **no injection into the consumer's `.agents/hooks.json`**. The consumer repository's files remain untouched.

### Hook Working Directory

When Antigravity invokes a plugin hook, commands execute relative to the plugin root (`workspace-plugin/bug-fixer/`), making relative commands like `python3 scripts/pre_tool_gate.py` immediately resolve to the correct script.

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

### Consumer Hook API (`bugfixer_api.py`)

Consumer projects that host the bug-fixer plugin often have their own PreToolUse hooks enforcing project-specific safety rules. These hooks may need to apply **different decisions** depending on whether the session is autonomous (bug-fixer cron) or interactive (human at the bench).

The plugin provides `scripts/bugfixer_api.py` — a stable, namespaced API for consumer hooks:

```python
from bugfixer_api import BugFixer

payload = json.loads(sys.stdin.read())
ctx = BugFixer.get_session_context(payload.get("conversationId", ""))

if ctx.is_autonomous:
    # No human to approve — hard deny
    print(json.dumps({"decision": "deny", "reason": "Blocked in autonomous mode"}))
else:
    # Interactive — let the human decide
    print(json.dumps({"decision": "force_ask"}))
```

#### `SessionContext` Fields

| Field | Type | Description |
|-------|------|-------------|
| `is_autonomous` | `bool` | `True` if spawned by a bug-fixer dispatcher |
| `session_type` | `str \| None` | `'fresh_fix'`, `'reattach'`, `'pr_iteration'`, `'architect_review'`, `'triage'`, `'trajectory'` |
| `issue_number` | `int \| None` | GitHub issue number, if applicable |
| `pr_number` | `int \| None` | GitHub PR number, if applicable |
| `worktree_path` | `str \| None` | Path to the git worktree |
| `target_repo` | `str \| None` | GitHub `owner/repo` from config |
| `config` | `dict` | Full `config.json` snapshot |

#### `BugFixer` Methods

| Method | Returns | Description |
|--------|---------|-------------|
| `BugFixer.get_session_context(conv_id)` | `SessionContext` | Full session context lookup |
| `BugFixer.is_autonomous(conv_id)` | `bool` | Convenience shorthand |
| `BugFixer.get_config()` | `dict` | Plugin config without session lookup |

#### How It Works

The API reads existing dispatch tracking files (`STATE_DIR/dispatches/*.json`) written by all four dispatchers when they spawn conversations via `agentapi new-conversation`. No additional state files are created.

#### Discovery

`bugfixer_api.py` discovers the repository state directory automatically:

1. `BUG_FIXER_STATE_DIR` or `BUG_FIXER_CONFIG` environment variable
2. Scanning `~/.gemini/config/plugins/bug-fixer-daemon/sidecars/*/config.json` matching the target repository path

#### Consumer Hook Import Path

When the workspace plugin is registered via `.agents/plugins.json`, plugin scripts are in the shared workspace plugin (`~/.gemini/config/plugins/bug-fixer-daemon/workspace-plugin/bug-fixer/scripts/`).
Consumer hooks can import `bugfixer_api` by including that directory in `PYTHONPATH` or adding it to `sys.path`.


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

Skills resolve paths at runtime relative to the workspace plugin:

```markdown
## Step 0: Configuration Guard
PLUGIN_DIR="$(cd "$(dirname "<this_skill_file>")/../.." && pwd)"
STATE_DIR="${BUG_FIXER_STATE_DIR}"
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

Each `<name>/` subdirectory represents one sidecar process. In the bug-fixer architecture, **each enrolled repository has exactly one sidecar** located at:

```
~/.gemini/config/plugins/bug-fixer-daemon/sidecars/<owner--repo>/sidecar.json
```

### Sidecar Manifest (`sidecar.json`)

The sidecar manifest runs a 5-minute heartbeat tick:

```json
{
  "builtin": "schedule",
  "args": ["*/5 * * * *", "python3", "~/.gemini/config/plugins/bug-fixer-daemon/scripts/tick.py"],
  "restart_policy": "always",
  "display_name": "Bug Fixer — owner/repo",
  "agent_permissions": {
    "workspace_uris": ["file:///path/to/repo"]
  },
  "env": {
    "BUG_FIXER_CONFIG": "~/.gemini/config/plugins/bug-fixer-daemon/sidecars/owner--repo/config.json",
    "BUG_FIXER_STATE_DIR": "~/.gemini/config/plugins/bug-fixer-daemon/sidecars/owner--repo/state"
  }
}
```

| Field | Type | Description |
|-------|------|-------------|
| `builtin` | string | `"schedule"` runs on a cron schedule. |
| `args` | array | `["*/5 * * * *", "python3", ".../scripts/tick.py"]`. Invoked every 5 minutes. |
| `restart_policy` | string | `"always"` ensures the sidecar continues on future cron ticks. |
| `display_name` | string | Human-readable name shown in Antigravity dashboard. |
| `agent_permissions` | object | Declares `workspace_uris` allowing spawned agents access to the repository. |
| `env` | object | Environment variables providing `BUG_FIXER_CONFIG` and `BUG_FIXER_STATE_DIR`. |

### Unified Tick Execution Model

Instead of running 4 separate cron sidecars per enrolled repository (which multiplied quickly with multiple repositories), the unified sidecar delegates execution to `tick.py`:

```
Antigravity Cron (*/5)
       │
       ▼
daemon/scripts/tick.py
       │
       ▼
workspace-plugin/bug-fixer/scripts/tick_receiver.py
       ├── triage_dispatcher.py       (runs if elapsed >= triage_interval_min)
       ├── engineering_dispatcher.py  (runs if elapsed >= engineering_interval_min)
       ├── architect_dispatcher.py    (runs if elapsed >= architect_interval_min)
       └── trajectory_dispatcher.py   (runs if elapsed >= trajectory_interval_min)
```

1. **`tick.py`** runs in the daemon process and forwards execution to `tick_receiver.py` in the workspace plugin.
2. **`tick_receiver.py`** inspects the repository's `config.json` for loop intervals:
   - `triage_interval_min` (default: 60)
   - `engineering_interval_min` (default: 10)
   - `architect_interval_min` (default: 15)
   - `trajectory_interval_min` (default: 1440)
3. For each loop, `tick_receiver.py` checks `STATE_DIR/<name>.last` to see if the interval has elapsed.
4. If due, it acquires a non-blocking lock (`fcntl.flock`) on `STATE_DIR/<name>.lock`. If already running, it skips.
5. If lock is acquired, it touches `STATE_DIR/<name>.last` and launches the loop's dispatcher in the background, redirecting logs to `STATE_DIR/<name>.log`.

### Sidecar Lifecycle & Failure Modes

Understanding sidecar failure modes is essential for debugging:

| Failure Mode | Symptom | Root Cause | Fix |
|-------------|---------|-----------|-----|
| Script not found | Sidecar fires but nothing happens | `INSTALL_DIR` path moved or deleted | Re-run `install.sh` |
| `agentapi` not found | Script runs but can't spawn conversations | `PATH` doesn't include `~/.gemini/antigravity/bin/` | Dispatchers automatically add `~/.gemini/antigravity/bin/` to `PATH` |
| Quota exhausted | Conversations spawn but immediately fail; labels churn | LLM API quota exceeded | Circuit breaker trips after 3 failures (1-hour cooldown) |
| Missing config env | `tick_receiver.py` exits with `BUG_FIXER_CONFIG not set` | Sidecar `env` block missing `BUG_FIXER_CONFIG` | Re-run `enroll.sh` |
| In-flight lock | Loop does not run when due | Previous dispatcher run is still in progress holding lock | Normal operation; will run on next tick once lock is freed |


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
    - Has "bug-fixer:engineer:needs-fix" label
    - Not in exclusion_labels (e.g., "bug-fixer:human:required", "P1-blocker", "hardware-required")
    - Not already claimed (no active claim file)
    - Sorted by priority (milestone boost, label priority ranking)
7.  For each eligible issue (up to max_concurrent - active_prs):
    a. Write claim file to STATE_DIR/claims/<issue_number>.json (atomic lock)
    b. Add "bug-fixer:engineer:in-progress" label to the GitHub issue
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
2. Remove the `bug-fixer:engineer:in-progress` label from the GitHub issue
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
2. **Finds eligible issues**, writes claim files, adds `bug-fixer:engineer:in-progress` labels
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

### 1. `install.sh`
Deploys the daemon plugin to `~/.gemini/config/plugins/bug-fixer-daemon/`:
- Creates the target installation directory and `sidecars/` subfolder.
- Syncs `daemon/` using `rsync -a --delete --exclude='sidecars/'`, ensuring per-repo configs and runtime state in `sidecars/` persist across updates.
- Enables the plugin via `agy plugin enable bug-fixer-daemon` if the CLI is available.

### 2. `enroll.sh`
Enrolls a target repository:
```bash
./scripts/enroll.sh -r <owner/repo> -d <repo-dir> [--migrate-state]
```
- Creates `sidecars/<owner--repo>/` with `config.json` and `sidecar.json`.
- Initializes `state/` directory for lock files, markers, and claims.
- Merges or creates the consumer repository's `.agents/plugins.json`:
  ```json
  {
    "entries": [
      {
        "path": "~/.gemini/config/plugins/bug-fixer-daemon/workspace-plugin"
      }
    ]
  }
  ```
- Supports optional `--migrate-state` to copy legacy state from `.agents/plugins/bug-fixer-loop-workspace/`.

### 3. `unenroll.sh`
Safely unenrolls a repository:
```bash
./scripts/unenroll.sh -r <owner/repo> [-d <repo-dir>]
```
- Inspects `state/claims/` for active in-flight claims, refusing unenrollment if agents are running.
- Removes `sidecars/<owner--repo>/`.
- Removes the workspace-plugin entry from the consumer repository's `.agents/plugins.json`.

### 4. `uninstall.sh`
Uninstalls the daemon plugin:
```bash
./scripts/uninstall.sh [--force]
```
- Requires `--force` if `sidecars/` is non-empty to prevent accidental data loss.
- Disables the daemon plugin via `agy plugin disable bug-fixer-daemon`.
- Removes `~/.gemini/config/plugins/bug-fixer-daemon/`.
- Cleans stale entries from `~/.gemini/config/config.json`.
- Cleans legacy global artifacts.

---

## Anti-Patterns (Learned the Hard Way)

### 1. Writing to Global Config

**Problem:** Early versions wrote hooks, rules, scripts, and skills directly to `~/.gemini/config/`. A broken global hook bricked every project on the machine.

**Fix:** Use the split-daemon + workspace plugin architecture. Hooks and skills are scoped to enrolled workspaces via `.agents/plugins.json`.

### 2. Absolute Paths in Config

**Problem:** `config.json` contained `/Users/mcneillm/Documents/Projects/...` — completely non-portable.

**Fix:** Use standard config discovery via `BUG_FIXER_CONFIG` environment variable and `workspacePaths` matching against sidecar configurations.

### 3. Instance Values in Plugin Root

**Problem:** `config.json` lived in the plugin root, dirtying git and preventing multi-project usage.

**Fix:** Per-repo configs live in `~/.gemini/config/plugins/bug-fixer-daemon/sidecars/<owner--repo>/config.json`.

### 4. No Circuit Breaker

**Problem:** When LLM quota ran out, dispatchers entered rapid failure loops churning GitHub issue labels.

**Fix:** Track consecutive spawn failures. After 3 failures, write a cooldown file and stop dispatching for 1 hour.

### 5. Project-Specific Rules in Generic Plugin

**Problem:** Project-specific rules like PlatformIO MCP discipline were packaged in generic plugin rules.

**Fix:** Keep generic invariants in `rules/AGENTS.md`; project-specific tooling rules go in consumer `.agents/rules/`.

### 6. 4×N Sidecar Explosion

**Problem:** 4 separate sidecars per enrolled repository created dozens of background cron jobs.

**Fix:** Single 5-minute heartbeat sidecar per repo running `tick.py`, delegating to `tick_receiver.py` for interval-based dispatching.

---

## Multi-Project Support

The split-daemon architecture natively manages multiple repositories from a single installation:

```
~/.gemini/config/plugins/bug-fixer-daemon/
└── sidecars/
    ├── owner--project-a/
    │   ├── sidecar.json             ← Runs tick.py for project-a
    │   ├── config.json              ← Config for project-a
    │   └── state/                   ← State/claims for project-a
    └── owner--project-b/
        ├── sidecar.json             ← Runs tick.py for project-b
        ├── config.json              ← Config for project-b
        └── state/                   ← State/claims for project-b
```

Each consumer repository has `.agents/plugins.json` pointing to `~/.gemini/config/plugins/bug-fixer-daemon/workspace-plugin`. No submodules or duplicate script copies are required across consumer repos.

---

## Checklist: Shipping a Plugin

- [ ] Global daemon manifest exists at `daemon/plugin.json`
- [ ] Shared workspace plugin manifest exists at `daemon/workspace-plugin/bug-fixer/plugin.json`
- [ ] Hooks use clean relative commands (`python3 scripts/pre_tool_gate.py`)
- [ ] Consumer repos reference workspace-plugin via `.agents/plugins.json` with `"entries"` key
- [ ] Single sidecar per repo scheduled with `tick.py` and `env` containing `BUG_FIXER_CONFIG` and `BUG_FIXER_STATE_DIR`
- [ ] Sidecar manifest specifies `agent_permissions.workspace_uris` pointing to the repo
- [ ] `install.sh` uses `rsync --exclude='sidecars/'` to preserve per-repo configs and state
- [ ] `enroll.sh` and `unenroll.sh` handle repository lifecycle
- [ ] `uninstall.sh` requires `--force` when enrolled sidecars exist
- [ ] Dispatchers include circuit breaker logic for quota exhaustion
- [ ] Unit tests pass cleanly

