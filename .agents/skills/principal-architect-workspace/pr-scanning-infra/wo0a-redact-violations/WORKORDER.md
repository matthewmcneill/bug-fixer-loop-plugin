---
name: scanning-infra-wo0a
description: Handoff instructions for Work Order 0a — Redact matched secrets in scan_repository() violation output
complexity: mechanical
---

# WO0a — Redact Matched Secrets in `scan_repository()` Violation Output

## Role Definition
> You are an expert senior software engineer and execution-focused worker agent. You are NOT the Principal Architect. Do not make high-level architectural decisions, do not branch, and do not plan. Your task is strictly limited to executing the checklist below.

## Objective
Fix a security bug: `scan_repository()` in `scripts/pre_tool_gate.py` currently prints matched secret content (`line.strip()[:100]`) into violation output, which goes to stdout, agent context, and logs. Violations must report file location and rule only, with redacted match text.

## Strict Boundary Box
- **ONLY** modify `scripts/pre_tool_gate.py` (function `scan_repository`, lines 296–338) and `tests/test_pre_tool_gate.py`
- Do NOT touch `safe_ingest.py`, `github_url_gate.py`, config files, or any other scripts
- Do NOT refactor `scan_repository`'s signature or add new arguments — this is a minimal bug fix
- Do NOT modify the `HOST_SECURITY_SCRIPTS` set or any gate evaluation functions

## Tooling Constraints
- Run tests with: `python3 -m unittest discover -s tests -v`
- No external dependencies — stdlib only

## Line-Item Instructions
- [ ] `scripts/pre_tool_gate.py` — In `scan_repository()` (line 332–334), change the violation format from:
  ```python
  violations.append(f"{rel_path}:{lineno} [{pat.pattern}] -> {line.strip()[:100]}")
  ```
  to a redacted format that shows only the first 4 characters of the match followed by `…`:
  ```python
  match = pat.search(line)
  matched_text = match.group()
  redacted = matched_text[:4] + "…" if len(matched_text) > 4 else "****"
  violations.append(f"{rel_path}:{lineno} [{pat.pattern}] -> {redacted}")
  ```
  Ensure the raw line content is never included in the violation string.

- [ ] `tests/test_pre_tool_gate.py` — Add a test that:
  1. Creates a temp file containing a synthetic secret matching one of the configured patterns (e.g. `AKIAIOSFODNN7EXAMPLE`)
  2. Runs `scan_repository` against it
  3. Asserts the violation string contains the file path and rule pattern
  4. Asserts the violation string does NOT contain the full secret (assert `AKIAIOSFODNN7EXAMPLE` not in the violation)
  5. Asserts the violation string contains a redacted prefix (e.g. `AKIA…`)

## Verification Requirement
Run `python3 -m unittest discover -s tests -v` and confirm all tests pass (existing + new). Before committing, verify no test output contains unredacted secret text.

## Issue Surfacing
If you discover a defect, upstream bug, or concern *outside your boundary box*, surface it in your Completion Report below — do not file issues directly. The Principal Architect will triage and file them under the correct author identity.

## Completion Report
- **Status**: Completed & Verified
- **Completed At**: 2026-10-04T20:06:20Z
- **Changes Made**:
  1. Updated `scan_repository()` in `scripts/pre_tool_gate.py` to redact matched secret text to the first 4 characters followed by `…` (`matched_text[:4] + "…" if len(matched_text) > 4 else "****"`).
  2. Updated `tests/test_pre_tool_gate.py` (`test_cli_scan_only_detects_planted_leak`) and added `test_scan_repository_redacts_secrets` to assert correct redaction and absence of raw secret content.
- **Verification**: Ran `python3 -m unittest discover -s tests -v` — all 103 tests passed successfully.
- **Defects/Concerns Surfaced**: None.
