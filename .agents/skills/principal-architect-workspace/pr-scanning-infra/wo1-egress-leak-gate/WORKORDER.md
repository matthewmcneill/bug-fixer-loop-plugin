---
name: scanning-infra-wo1
description: Handoff instructions for Work Order 1 — Egress leak gate on outbound gh bodies in pre_tool_gate
complexity: standard
---

# WO1 — Egress Leak Gate on Outbound `gh` Bodies

## Role Definition
> You are an expert senior software engineer and execution-focused worker agent. You are NOT the Principal Architect. Do not make high-level architectural decisions, do not branch, and do not plan. Your task is strictly limited to executing the checklist below.

## Objective
Extend the `run_command` PreToolUse gate in `pre_tool_gate.py` to scan outbound bodies in GitHub CLI commands for leak patterns before allowing them to execute. For a public repo, posting to GitHub is the primary exfiltration channel. Blocking secrets on the way out protects against every injection vector at once, including ones that bypass input scanning.

## Strict Boundary Box
- **ONLY** modify `scripts/pre_tool_gate.py` and `tests/test_pre_tool_gate.py`
- Do NOT touch `safe_ingest.py`, `github_url_gate.py`, config files, dispatchers, or skills
- Do NOT modify Gate A (file write protection) or existing Gate B command-blocking logic
- Do NOT change the stdin JSON protocol or the `evaluate_pretool_call` signature
- Add the egress scanning as a **new check within `evaluate_gate_b`**, after the existing command blocks but before the final `allow`

## Tooling Constraints
- Run tests with: `python3 -m unittest discover -s tests -v`
- No external dependencies — stdlib only

## Line-Item Instructions

- [x] `scripts/pre_tool_gate.py` — Add a new denial reason constant:
  ```python
  REASON_EGRESS_LEAK = (
      "Egress leak blocked: Outbound GitHub command body contains content "
      "matching configured leak patterns. Content not sent."
  )
  ```

- [x] `scripts/pre_tool_gate.py` — Create a new function `extract_outbound_body(cmd: str) -> str | None` that extracts the body content from outbound `gh` commands. It must handle:
  1. `--body "..."` and `--body '...'` and `-b "..."` flags (extract the quoted string)
  2. `-f body=...` and `--field body=...` flags
  3. `--body-file <path>` flag — read the file content at the specified path (if the file exists and is readable; return `None` if not)
  4. `echo "..." | gh ...` pipe patterns — extract the echo content
  Return the extracted body text, or `None` if no body is found.

- [x] `scripts/pre_tool_gate.py` — Create a new function `check_egress_leaks(cmd: str, leak_patterns: list[str]) -> bool` that:
  1. Checks if `cmd` matches outbound GitHub commands: `gh issue comment`, `gh pr create`, `gh pr edit`, `gh pr comment`, `gh api` (with body-containing flags)
  2. If matched, extracts the body via `extract_outbound_body(cmd)`
  3. Compiles `leak_patterns` and scans the extracted body
  4. Returns `True` if any pattern matches (i.e., should block)

- [x] `scripts/pre_tool_gate.py` — In `evaluate_gate_b()`, add an egress check after the existing shell mutation check (line ~238) but before the final `return {"decision": "allow"}`:
  ```python
  leak_patterns = list(config.get("leak_patterns", []))
  if check_egress_leaks(cmd, leak_patterns):
      return {"decision": "deny", "reason": REASON_EGRESS_LEAK}
  ```
  Note: `evaluate_gate_b` currently does not receive `config`. You will need to modify `evaluate_gate_b` to accept `config` as a parameter and update the call in `evaluate_pretool_call` accordingly.

- [x] `tests/test_pre_tool_gate.py` — Add tests:
  1. Test that `gh issue comment 42 --body "contains AKIAIOSFODNN7EXAMPLE"` is denied
  2. Test that `gh pr create --body "safe content"` is allowed
  3. Test that `gh pr comment 5 -b "has ya29.token-here"` is denied
  4. Test that `gh api repos/owner/repo/issues/1/comments -f body="AKIA1234567890ABCDEF"` is denied
  5. Test that `extract_outbound_body` correctly extracts from `--body`, `-b`, `-f body=`, and `--body-file` forms
  6. Test that a command with no outbound body is allowed (e.g., `gh pr list`)
  7. Test that non-`gh` commands with matching content are not affected (the check only applies to outbound `gh` commands)

## Verification Requirement
Run `python3 -m unittest discover -s tests -v` and confirm all tests pass (existing + new).

## Issue Surfacing
If you discover a defect, upstream bug, or concern *outside your boundary box*, surface it in your Completion Report below — do not file issues directly. The Principal Architect will triage and file them under the correct author identity.

## Completion Report
- **Status**: Complete
- **Changes Made**:
  1. `scripts/pre_tool_gate.py`:
     - Added denial reason constant `REASON_EGRESS_LEAK`.
     - Added `extract_outbound_body(cmd: str) -> str | None` supporting `--body`, `-b`, `-f body=`, `--field body=`, `--body-file` (with file read and error handling), and `echo "..." | gh ...` pipes.
     - Added `check_egress_leaks(cmd: str, leak_patterns: list[str]) -> bool` matching outbound GitHub commands (`gh issue comment`, `gh pr create`, `gh pr edit`, `gh pr comment`, `gh api`), extracting outbound bodies, and evaluating compiled regex leak patterns.
     - Updated `evaluate_gate_b` to accept `config: dict[str, Any] | None = None` and execute egress leak detection after shell mutation checks before returning allow.
     - Updated `evaluate_pretool_call` to pass `config` into `evaluate_gate_b`.
  2. `tests/test_pre_tool_gate.py`:
     - Added comprehensive unit tests covering:
       - Denying `gh issue comment 42 --body "contains AKIAIOSFODNN7EXAMPLE"`
       - Allowing `gh pr create --body "safe content"`
       - Denying `gh pr comment 5 -b "has ya29.token-here"`
       - Denying `gh api repos/owner/repo/issues/1/comments -f body="AKIA1234567890ABCDEF"`
       - Validating `extract_outbound_body` extraction across `--body`, `-b`, `-f body=`, `--body-file`, and echo pipes
       - Allowing outbound commands with no body (`gh pr checkout 42`) and confirming `check_egress_leaks` returns `False` for `gh pr list`
       - Verifying non-`gh` commands with leak content (`git commit`, `echo`, `grep`) are unaffected
       - Denying echo pipe leak egress (`echo ... | gh issue comment`)
       - Denying file-based leak egress (`gh issue comment --body-file ...`)
- **Verification**: Ran `python3 -m unittest discover -s tests -v` — all 119 tests passed successfully (110 existing + 9 new).
- **Defects/Concerns Surfaced**:
  - In checklist item 57.6, the example given `(e.g., gh pr list)` is intercepted earlier in Gate B by the existing `RAW_GH_READ_REGEX` rule (`REASON_RAW_GH`), which blocks direct `gh issue/pr list` and `gh issue/pr view` invocations. `check_egress_leaks("gh pr list", patterns)` correctly returns `False`, but whole-gate execution of `gh pr list` produces `REASON_RAW_GH`. Full-gate passage without an outbound body was verified using `gh pr checkout 42`.

