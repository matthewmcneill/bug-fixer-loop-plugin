# Antigravity Hooks & Plugin Discovery Reference

> This document captures empirically verified behavior of the Antigravity hook
> system and plugin/agent discovery mechanisms. Filed from direct testing and
> user-provided documentation (October 2026).

---

## 1. External Documentation & Reference Sources

1. **Bundled Customization Guide (`skills/agy-customizations/docs/hooks.md`)**
   - Shipped inside the Antigravity installation under the built-in `agy-customizations` skill (`~/.gemini/antigravity/builtin/skills/agy-customizations/docs/hooks.md`).
   - Covers `hooks.json` discovery paths (`~/.gemini/antigravity/hooks.json`, `<workspace>/.agents/hooks.json`, and `<plugin-root>/hooks.json`), execution order, `stdin`/`stdout` JSON schemas, and timeout configuration.

2. **CLI Slash Commands & Release Notes (`agy -p "/changelog"` & `agy -p "/hooks"`)**
   - You can inspect active hooks and release notes non-interactively via `agy -p "/hooks"` and `agy -p "/changelog"` (or with `--output-format json`, added in **v1.1.12**).
   - Notable hook changes documented in `agy -p "/changelog"`:
     - **v1.1.9:** Fixed `PostToolUse` hooks firing on non-tool steps (such as user input and model responses) and ignoring their configured `matcher`; added a continuation cap so `Stop` hooks that repeatedly block termination cannot hang a turn indefinitely.
     - **v1.1.10:** Updated hook ordering so hooks defined in `hooks.json` run before built-in termination checks, allowing `PostInvocation` hooks to observe the final invocation of a turn and `Stop` hooks to run reliably.
     - **v1.2.1:** Added `excludeDefaultComponents: true` in custom agent Markdown frontmatter while preserving `PostInvocation` hooks.
     - **v1.2.3 & v1.2.4:** Fixed `/hooks` inspection for hooks bundled inside enabled plugins and fixed `hooks.json` configurations being dropped when customization token budget truncation is active.

3. **Empirical Inspection (`stdin` & `transcript_full.jsonl`)**
   - Every hook receives a `transcriptPath` field on `stdin` pointing to `<appDataDir>/brain/<conversation-id>/.system_generated/logs/transcript.jsonl` (alongside `transcript_full.jsonl`). Inspecting `transcript_full.jsonl` after a hook emits JSON on `stdout` shows the exact step type and persistence behavior recorded in the session trajectory.

---

## 2. Hook Message Injection — `injectSteps` Support

`SessionStart`, `PreInvocation`, and `PostInvocation` all share the same `injectSteps` handler on `stdout` and support three working message types: **`ephemeralMessage`**, **`userMessage`**, and **`systemMessage`**.

### A. `ephemeralMessage` (Scalar String — Single-Invocation Scope)

```json
{
  "injectSteps": [
    {
      "ephemeralMessage": "Transient context for this model call only."
    }
  ]
}
```

- Appends an `EPHEMERAL_MESSAGE` step (`"source": "SYSTEM_SDK"`).
- Visible to the model on the immediate invocation only — excluded from subsequent model calls.
- **Best for:** Per-invocation dynamic context or status reminders.

### B. `userMessage` (Scalar String — Persistent Session Scope)

```json
{
  "injectSteps": [
    {
      "userMessage": "Additional user-role message."
    }
  ]
}
```

- Appends a `USER_INPUT` step (`"source": "SYSTEM_SDK"`).
- Persists in the conversation history across subsequent invocations and turns.

### C. `systemMessage` (Nested Object — Persistent Session Scope)

```json
{
  "injectSteps": [
    {
      "systemMessage": {
        "systemMessage": "System-level instruction injected by hook.",
        "metadata": {
          "source": "my-hook"
        }
      }
    }
  ]
}
```

- **Schema caveat:** Unlike `ephemeralMessage` and `userMessage` (which take a plain string), `systemMessage` **must be a nested object** containing a `"systemMessage"` string field (and an optional `"metadata"` key-value object). Passing a bare string fails JSON parsing.
- Persists in the conversation history across subsequent invocations and turns until compaction.

### D. `toolCall` inside `injectSteps`

- Documented in hooks.md under `PreInvocation` but empirically appears to be a no-op in current builds.

---

## 3. Complete Hook Event Reference

### Common `stdin` Fields (All 6 Events)

```json
{
  "conversationId": "022ef0e8-...",
  "workspacePaths": ["/path/to/workspace"],
  "transcriptPath": "/path/to/brain/<conversationId>/.system_generated/logs/transcript.jsonl",
  "artifactDirectoryPath": "/path/to/brain/<conversationId>",
  "modelName": "..."
}
```

### Per-Event Matrix

| Hook Event | `hooks.json` Layout | When It Fires | Event-Specific `stdin` Fields | Supported `stdout` JSON Fields | Notes |
|:---|:---|:---|:---|:---|:---|
| **`SessionStart`** | Flat array | Once per conversation, before first model call | *(Common only)* | `injectSteps` | One-time session bootstrap. |
| **`PreInvocation`** | Flat array | Before each model call in a turn | `invocationNum`, `initialNumSteps` | `injectSteps` | Fires N+1 times per N tool-call rounds. Prefer `ephemeralMessage` to avoid bloat. |
| **`PostInvocation`** | Flat array | After each model call + tool calls complete | `invocationNum`, `initialNumSteps` | `injectSteps`, `terminationBehavior` | `"force_continue"` or `"terminate"`. Read transcript for model response. |
| **`PreToolUse`** | Grouped with `matcher` | Before matching tool calls | `stepIdx`, `toolCall` | `decision`, `reason`, `permissionOverrides`, `overwrite` | Most restrictive decision wins across hooks. Non-zero exit also aborts. |
| **`PostToolUse`** | Grouped with `matcher` | After matching tool calls finish | `stepIdx`, `toolCall`, `error` | `{}` (observational) | `toolCall` IS passed on stdin despite docs omitting it. |
| **`Stop`** | Flat array | When agent loop is about to end | `executionNum`, `terminationReason`, `error`, `fullyIdle` | `decision` (`continue`/`stop`), `reason` | Normal completion passes `"NO_TOOL_CALL"`. Continuation cap: 10. |

---

## 4. Quick Reference — Response Examples

### PreToolUse — Gate or rewrite:
```json
{"decision": "allow", "reason": "Normalized", "overwrite": {"CommandLine": "pytest -q"}}
{"decision": "deny", "reason": "Blocked by policy"}
```

### PostInvocation — Force continue:
```json
{
  "injectSteps": [{"ephemeralMessage": "Verify test output before finishing."}],
  "terminationBehavior": "force_continue"
}
```

### Stop — Prevent turn ending:
```json
{"decision": "continue", "reason": "Uncommitted changes; please lint first."}
```

---

## 5. Plugin Hook Discovery — `hooks.json` Paths

### Discovery Paths

Antigravity discovers `hooks.json` from three locations:

- **Workspace:** `<workspace>/.agents/hooks.json` (or `.agent/hooks.json`)
- **Global:** `~/.gemini/config/hooks.json`
- **Plugin:** `<plugin-root>/hooks.json` (e.g., `~/.gemini/config/plugins/<plugin-name>/hooks.json`
  or `<workspace>/.agents/plugins/<plugin-name>/hooks.json`)

### `<plugin>/hooks.json` vs `<plugin>/hooks/hooks.json`

> [!IMPORTANT]
> At runtime, Antigravity **ONLY** loads `<plugin-root>/hooks.json`.
> It never reads `<plugin-root>/hooks/hooks.json` directly.

Why `hooks/hooks.json` can appear to work in some contexts:

1. **Native Antigravity plugins** — `hooks.json` sits at the plugin root,
   alongside `plugin.json`. This is the only path the runtime loader reads.

2. **Imported Claude Code / Gemini CLI extensions** — When you run
   `agy plugin install <path>` or `agy plugin import [claude|gemini]` on a
   **pure Claude Code plugin** (containing `.claude-plugin/plugin.json` and no
   top-level `plugin.json`) or a **Gemini CLI extension** (`gemini-extension.json`),
   the CLI's foreign-plugin importer stages the extension by reading
   `<source>/hooks/hooks.json` (interpolating `${extensionPath}` and
   `${workspacePath}`) and writing the result to
   `~/.gemini/config/plugins/<name>/hooks.json` at the plugin root.

3. **If the directory already contains a native Antigravity manifest**
   (`plugin.json`, `.antigravity-plugin/plugin.json`, or
   `.gemini-plugin/plugin.json`), `agy plugin install` treats it as a native
   Antigravity plugin and copies the directory as-is **without** staging
   `hooks/hooks.json` into `hooks.json`.

### Dual-Host (Antigravity + Claude Code) Plugins

The hook schema differs between hosts:

| | Antigravity | Claude Code |
|---|---|---|
| **File** | `<plugin-root>/hooks.json` | `<plugin-root>/hooks/hooks.json` |
| **Top-level key** | `<hook-group-name>` map wrapping event arrays | `"hooks": { "<Event>": [...] }` |
| **cwd** | `<plugin-root>` | varies |

A dual-host repository can safely keep both files without collision.

### Empirical Verification (Check 9, October 2026)

- **Workspace plugin hooks via `plugins.json`:** Hooks on the workspace plugin
  (nested under `workspace-plugin/bug-fixer/`) were NOT discovered — the runtime
  does not load hooks from nested/referenced workspace plugins.
- **Daemon plugin hooks at root:** Hooks at `<daemon-plugin-root>/hooks.json`
  (the globally-installed plugin) ARE discovered and fire correctly.
- **Resolution:** Place `hooks.json` on the daemon plugin (global), use the
  workspace plugin (via `plugins.json`) only for skills, rules, and agents.

---

## 6. Custom Agent Discovery

### Agent File Locations
- `.agents/agents/<name>.md` (flat file)
- `.agents/agents/<name>/agent.md` (directory format)
- Both are supported.

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
1. **Invalid `model:` value** — using a concrete model slug (e.g., `gemini-3-flash-agent`) instead of a tier name causes silent drop.
2. **`mainAgent: false`** — hides from `agy --agent` and `agy agent` list.
3. **`subagent: false`** — blocks `invoke_subagent` from finding it.
4. **Missing `name` or `description`** — file silently ignored.
5. **`agy agent` in untrusted dir** — use `agy --add-dir . agent` to force scan.

### Key Clarifications
- `invoke_subagent` and `agy --agent` are NOT separate systems — both resolve the same `.md` files.
- `define_subagent` is only for dynamic, conversation-scoped agents at runtime.
- `hidden: true` does NOT prevent resolution — just hides from UI/prompt listing.
