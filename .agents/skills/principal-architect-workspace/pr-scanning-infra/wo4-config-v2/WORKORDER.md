---
name: scanning-infra-wo4
description: Handoff instructions for Work Order 4 — Config v2 scanning namespace
complexity: standard
---

# WO4 — Config v2 `scanning` Namespace

## Role Definition
> You are an expert senior software engineer and execution-focused worker agent. You are NOT the Principal Architect. Do not make high-level architectural decisions, do not branch, and do not plan. Your task is strictly limited to executing the checklist below.

## Objective
Add a `scanning` namespace to the config schema that nests the existing `leak_patterns` / `scan_exclude_paths` and adds backend-specific configuration. The old flat config must continue to work silently — no migration step required.

## Strict Boundary Box
- **ONLY** modify `scripts/pre_tool_gate.py`, `.agents/plugins/bug-fixer-loop-workspace/config.json`, and `tests/test_pre_tool_gate.py`
- Do NOT touch `safe_ingest.py`, dispatchers, skills, or `github_url_gate.py`
- Do NOT modify Gate A, Gate B, or egress scanning logic
- Do NOT add a `redact` config option (redaction is an invariant)

## CRITICAL WARNING — pre_tool_gate.py is its own PreToolUse hook
Use the safe editing approach: write to temp, validate with py_compile, move if valid.

## Tooling Constraints
- Run tests with: `python3 -m unittest discover -s tests -v`
- No external dependencies — stdlib only

## Line-Item Instructions

- [ ] `scripts/pre_tool_gate.py` — Update `load_config` or add a normalisation function `normalise_config(config: dict) -> dict` that reads both old and new formats:

  **Old format (must continue to work):**
  ```json
  {
    "$schema": "bug-fixer-loop-config-v1",
    "leak_patterns": ["ya29\\.[A-Za-z0-9_-]+", ...],
    "scan_exclude_paths": ["vendor/", ...]
  }
  ```
  
  **New format:**
  ```json
  {
    "$schema": "bug-fixer-loop-config-v2",
    "scanning": {
      "secrets": {
        "backend": "betterleaks",
        "patterns": ["ya29\\.[A-Za-z0-9_-]+", ...],
        "exclude_paths": ["vendor/", ...],
        "betterleaks": {
          "config_path": ".betterleaks.toml"
        },
        "trufflehog": {
          "verify": false
        }
      },
      "injection": {
        "enabled": false,
        "provider": "model_armor",
        "fail_open": true,
        "model_armor": {
          "project": "",
          "location": "us-central1",
          "service_account": ""
        }
      }
    }
  }
  ```
  
  The normalisation function:
  - If `scanning` key exists, use it directly
  - If not, read `leak_patterns` → `scanning.secrets.patterns`, `scan_exclude_paths` → `scanning.secrets.exclude_paths`
  - Always return a config dict where callers can access `config["scanning"]["secrets"]["patterns"]` etc.
  - The `$schema` field is informational — don't enforce version checks

- [ ] `scripts/pre_tool_gate.py` — Update `resolve_backend()` to read from the normalised config:
  - `config["scanning"]["secrets"]["backend"]` for backend name
  - `config["scanning"]["secrets"]["betterleaks"]["config_path"]` for betterleaks config
  - `config["scanning"]["secrets"]["patterns"]` for regex patterns (fallback)
  - Keep reading `config["leak_patterns"]` as a fallback for unnormalised configs

- [ ] `scripts/pre_tool_gate.py` — Add `.betterleaks.toml` and `.betterleaksignore` to `protected_files` handling. In Gate A (`evaluate_gate_a`), add these files to the deny list alongside the existing protected files. These files can silence findings, so they need the same protection as config files.

- [ ] `.agents/plugins/bug-fixer-loop-workspace/config.json` — Update to v2 format:
  ```json
  {
    "$schema": "bug-fixer-loop-config-v2",
    ...existing fields...,
    "scanning": {
      "secrets": {
        "backend": "betterleaks",
        "patterns": [
          "ya29\\.[A-Za-z0-9_-]+",
          "AKIA[0-9A-Z]{16}",
          "-----BEGIN (RSA |EC )?PRIVATE KEY-----"
        ],
        "exclude_paths": []
      },
      "injection": {
        "enabled": false,
        "provider": "model_armor",
        "fail_open": true,
        "model_armor": {
          "project": "",
          "location": "us-central1",
          "service_account": ""
        }
      }
    }
  }
  ```
  Remove the old top-level `leak_patterns` key (since we're updating the actual config file).

- [ ] `tests/test_pre_tool_gate.py` — Add tests:
  1. Test `normalise_config()` with old v1 format produces correct `scanning.secrets.patterns`
  2. Test `normalise_config()` with new v2 format passes through correctly
  3. Test `resolve_backend()` reads from normalised v2 config
  4. Test that `.betterleaks.toml` and `.betterleaksignore` are denied by Gate A
  5. Test backward compatibility: v1 config still works end-to-end with `scan_repository()`

## Verification Requirement
Run `python3 -m unittest discover -s tests -v` and confirm all tests pass.

## Issue Surfacing
If you discover a defect, upstream bug, or concern *outside your boundary box*, surface it in your Completion Report below — do not file issues directly.
