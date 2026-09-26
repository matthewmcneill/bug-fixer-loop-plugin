---
complexity: standard
parent_project: bug-fixer-plugin
work_order: 3
topic: pretool-hooks
branch: bug-fixer-plugin/wo3-pretool-hooks
base: bug-fixer-plugin/wo2-security-scripts
---

# WO3: PreToolUse Gate & Antigravity Hooks

## Role
You are an expert senior software engineer creating the Antigravity PreToolUse enforcement layer for the bug-fixer-loop plugin.

## Objective
Create two files:
1. `scripts/pre_tool_gate.py` — adapted from the reference `pre_git_egress_hook.py`, providing configurable spec immutability, protected file enforcement, leak scanning, and forbidden command blocking
2. `hooks.json` — the Antigravity lifecycle hooks configuration that wires `pre_tool_gate.py` into the PreToolUse pipeline

## Strict Boundary Box

**CREATE:**
- `/Users/mcneillm/Documents/Projects/bug-fixer-loop-plugin/scripts/pre_tool_gate.py`
- `/Users/mcneillm/Documents/Projects/bug-fixer-loop-plugin/hooks.json`

**READ (reference — do NOT modify):**
- `/Users/mcneillm/Documents/Projects/loop-eng/scripts/pre_git_egress_hook.py` — source to adapt
- `/Users/mcneillm/Documents/Projects/bug-fixer-loop-plugin/config.json` — config schema
- `/Users/mcneillm/Documents/Projects/bug-fixer-loop-plugin/scripts/safe_ingest.py` — sibling script (for redirect references)

**DO NOT** modify any other files.

## Context: Antigravity hooks.json Contract

The `hooks.json` file sits at the plugin root. It is a JSON object where each top-level key is a named hook. For `PreToolUse` events:

```json
{
  "hook-name": {
    "PreToolUse": [
      {
        "matcher": "tool_name_regex",
        "hooks": [
          {
            "type": "command",
            "command": "python3 /path/to/pre_tool_gate.py",
            "timeout": 10
          }
        ]
      }
    ]
  }
}
```

The hook script receives JSON on **stdin** containing `toolCall.name` and `toolCall.args`, and must output JSON on **stdout**:
- `{"decision": "allow"}` — permit the tool call
- `{"decision": "deny", "reason": "..."}` — block with explanation
- `{"decision": "ask", "reason": "..."}` — prompt user for permission

## Context: What the Original Script Does

`pre_git_egress_hook.py` operates in dual mode:
1. **PreToolUse JSON interceptor** — reads tool call from stdin, decides allow/deny
2. **Git hook / CLI mode** — `--scan-only`, `--stamp-receipt`, `--git-hook`

For this plugin, we primarily need the **PreToolUse interceptor** mode. The git hook mode is a nice-to-have but secondary.

## Line-Item Instructions

### 1. Configuration Loading
Reuse the same pattern as `safe_ingest.py`:
- `load_config(config_path)` function
- `--config` CLI flag, `BUG_FIXER_CONFIG` env var, or default resolution
- Read `spec_file_path`, `protected_files`, `leak_patterns` from config

### 2. PreToolUse Interceptor (Primary Mode)
When invoked without CLI flags (stdin is not a TTY), read JSON from stdin and evaluate:

**Gate A — File Write Protection:**
For `write_to_file` and `replace_file_content` tool calls:
- Block if `TargetFile` matches `spec_file_path` from config → deny with "Specification file is immutable"
- Block if `TargetFile` matches any path in `protected_files` array → deny with "Protected file cannot be modified"
- Block if `TargetFile` matches the script's own path or `safe_ingest.py` → deny with "Host security script cannot be modified"
- Otherwise: allow

**Gate B — Command Execution Protection:**
For `run_command` tool calls, inspect `CommandLine`:
- Block `gh pr merge`, `gh stack merge` → "Auto-merge is prohibited"
- Block `gh pr review --approve` → "Self-approval is prohibited"
- Block `gh issue reopen` → "Issue reopening is prohibited"
- Block commands posting `approved` or `/approve` comments → "Self-approval injection blocked"
- Block raw `gh issue list`, `gh issue view`, `gh pr list`, `gh pr view` → deny with "Use safe_ingest.py instead of raw gh commands"
- Block shell mutations (`sed -i`, `>`, `tee`, `rm`, `cp`, `mv`) targeting spec file or protected files → "Protected file shell mutation blocked"
- Otherwise: allow

**Gate C — Default Allow:**
For all other tool calls: allow

### 3. CLI Scan Mode (Secondary)
Support `--scan-only <directory>` flag that:
- Walks all tracked files (`git ls-files`) in the directory
- Checks each line against compiled `leak_patterns` from config
- Reports any matches with file, line number, and pattern
- Exits 0 if clean, 1 if leaks found

### 4. Receipt Stamp Mode
Support `--stamp-receipt` to:
- Compute `git write-tree` SHA
- Write `{"status": "PASS", "tree_sha": "<sha>", "timestamp": "<iso>"}` to `.git/leak_audit_receipt.json`

Support `--verify-receipt` to:
- Read `.git/leak_audit_receipt.json`
- Verify `tree_sha` matches current `git write-tree`
- Exit 0 if valid, 1 if stale/missing

### 5. hooks.json
Create the Antigravity hooks configuration with TWO named hooks:

```json
{
  "bug-fixer-file-gate": {
    "PreToolUse": [
      {
        "matcher": "write_to_file|replace_file_content",
        "hooks": [
          {
            "type": "command",
            "command": "python3 scripts/pre_tool_gate.py",
            "timeout": 10
          }
        ]
      }
    ]
  },
  "bug-fixer-command-gate": {
    "PreToolUse": [
      {
        "matcher": "run_command",
        "hooks": [
          {
            "type": "command",
            "command": "python3 scripts/pre_tool_gate.py",
            "timeout": 10
          }
        ]
      }
    ]
  }
}
```

### 6. Important: Keep It Focused
The original `pre_git_egress_hook.py` is 500+ lines with many Google-specific patterns. Your adaptation should be **significantly shorter** — focus on the PreToolUse interceptor and lean CLI modes. Target 300-500 lines. Remove all Google-internal leak patterns (use config instead).

## Verification
1. `python3 scripts/pre_tool_gate.py --help` shows usage
2. `python3 -c "import json; json.load(open('hooks.json'))"` validates JSON
3. Test PreToolUse mode with a synthetic deny case:
   ```bash
   echo '{"toolCall":{"name":"write_to_file","args":{"TargetFile":"docs/design_specification.md"}}}' | python3 scripts/pre_tool_gate.py --config config.json
   ```
   Should output `{"decision": "deny", "reason": "..."}`
4. Test PreToolUse mode with an allow case:
   ```bash
   echo '{"toolCall":{"name":"write_to_file","args":{"TargetFile":"src/main.cpp"}}}' | python3 scripts/pre_tool_gate.py --config config.json
   ```
   Should output `{"decision": "allow"}`
5. `hooks.json` contains both `bug-fixer-file-gate` and `bug-fixer-command-gate`
6. Script does NOT contain `cloud-gtm`, `sparkforge`, `@google.com`, `agentapi-gemini-next`

## Completion Report Template
```markdown
## Completion Report — WO3: PreToolUse Gate & Hooks

**Status:** PASS | FAIL
**Branch:** bug-fixer-plugin/wo3-pretool-hooks

### Files Created
- [ ] scripts/pre_tool_gate.py
- [ ] hooks.json

### Lines of Code
- pre_tool_gate.py: <count>

### Gates Implemented
- [ ] Gate A: Spec + protected file write blocking
- [ ] Gate B: Forbidden command blocking (merge, approve, reopen, raw gh, shell mutations)
- [ ] Gate C: Default allow for other tools

### CLI Modes
- [ ] --scan-only (leak scanning)
- [ ] --stamp-receipt / --verify-receipt
- [ ] --help

### Verification Results
- PreToolUse deny (spec file): PASS/FAIL
- PreToolUse allow (normal file): PASS/FAIL
- hooks.json valid: PASS/FAIL
- No Google terms: PASS/FAIL

### Decisions Made
(list any)

### Out-of-Scope Findings
(surface anything for later WOs)
```

## Completion Report — WO3: PreToolUse Gate & Hooks

**Status:** PASS
**Branch:** bug-fixer-plugin/wo3-pretool-hooks

### Files Created
- [x] scripts/pre_tool_gate.py
- [x] hooks.json

### Lines of Code
- pre_tool_gate.py: 487

### Gates Implemented
- [x] Gate A: Spec + protected file write blocking
- [x] Gate B: Forbidden command blocking (merge, approve, reopen, raw gh, shell mutations)
- [x] Gate C: Default allow for other tools

### CLI Modes
- [x] --scan-only (leak scanning)
- [x] --stamp-receipt / --verify-receipt
- [x] --help

### Verification Results
- PreToolUse deny (spec file): PASS
- PreToolUse allow (normal file): PASS
- hooks.json valid: PASS
- No Google terms: PASS

### Decisions Made
- Parameterised all spec and protected file checks against `config.json` (`spec_file_path`, `protected_files`) with fallback defaults.
- Retained host security boundary protections for `scripts/pre_tool_gate.py` and `scripts/safe_ingest.py` so agents cannot tamper with the security interception infrastructure.
- Compacted the adapted `pre_tool_gate.py` to 487 lines (within the 300-500 LOC target), removing all Google-internal references and patterns in favor of configuration-driven regex compilation.
- Implemented dual-mode execution: seamlessly handling stdin JSON interception when piped or not in a TTY, and CLI options (`--scan-only`, `--stamp-receipt`, `--verify-receipt`, `--git-hook`) when flags are provided.

### Out-of-Scope Findings
- WO4 will implement the `leak-auditor` subagent and core skills (`bug-fixer-triage`, `bug-fixer-tick`, etc.), which will consume the `--stamp-receipt` and `--verify-receipt` functionality before Git commits/PR creations.

