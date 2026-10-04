---
name: scanning-infra-review-fixes
description: Fix all observations from PR #46 review
complexity: architect
---

# Review Fixes — PR #46 Observations

## Role Definition
> You are an expert senior software engineer and execution-focused worker agent. You are NOT the Principal Architect. Do not make high-level architectural decisions, do not branch, and do not plan. Your task is strictly limited to executing the checklist below.

## Objective
Fix all observations raised in the PR #46 code review. Every item must be addressed — no deferrals.

## Strict Boundary Box
- Modify `scripts/pre_tool_gate.py`, `scripts/safe_ingest.py`, `tests/test_pre_tool_gate.py`, `tests/test_safe_ingest.py`
- Do NOT touch dispatchers, skills, config files, or `github_url_gate.py`
- Do NOT change the `ScanBackend` protocol interface

## CRITICAL WARNING — pre_tool_gate.py is its own PreToolUse hook
`scripts/pre_tool_gate.py` is registered in `.agents/hooks.json` as a PreToolUse hook. If you break its syntax, ALL subsequent tool calls fail. Use the safe editing approach: write to a temp file, validate with py_compile, then move.

## Tooling Constraints
- Run tests with: `python3 -m unittest discover -s tests -v`
- No external dependencies — stdlib only

## Line-Item Instructions

### 🔴 BLOCKING FIXES (items 1-3)

- [ ] **Fix 1: BetterleaksBackend CLI flags for v2**
  In `_build_cmd()`:
  - Replace `--report-format json` with `-o -` (or `--output -`) for JSON on stdout
  - Replace `dir` with `fs` for tree scanning (v2 renamed the command)
  - Replace `--exit-code 0` with `--exit-code 1` (v2 default, needed for non-zero on findings)
  
  In `scan()`:
  - **Fail closed**: Remove the bare `except Exception: return []` and `except json.JSONDecodeError: return []`. Instead:
    - On `CalledProcessError`: check if exit code is the "findings found" code (typically 1) and parse stdout. For any other non-zero exit, raise a `RuntimeError` with the stderr message.
    - On `JSONDecodeError` or empty output: raise `RuntimeError("Betterleaks produced unparseable output — failing closed")`
    - On any other exception: raise, don't swallow

- [ ] **Fix 2: Parse v2 JSON envelope correctly**
  The v2 output is `{"schema_version": "1", "findings": [...], "scan": {...}}`, NOT a list.
  
  In `scan()`, replace `for match in data:` with:
  ```python
  if not isinstance(data, dict) or "findings" not in data:
      raise RuntimeError("Betterleaks output missing expected envelope structure")
  scan_state = data.get("scan", {}).get("state", "")
  if scan_state != "complete":
      raise RuntimeError(f"Betterleaks scan did not complete (state={scan_state})")
  findings = data.get("findings", [])
  ```
  
  Then for each finding, use the correct v2 field paths:
  ```python
  path = finding.get("location", {}).get("path", "")
  line = finding.get("location", {}).get("start_line", 0)
  rule = finding.get("rule_id", "unknown")
  redacted_match = finding.get("match", {}).get("value", "")
  ```

- [ ] **Fix 3: Default to betterleaks-if-installed**
  In `resolve_backend()`:
  - Remove the conditional that only defaults to betterleaks when `scanning` config exists
  - Always try betterleaks first (check if binary exists), fall back to regex if not found
  - Remove the duplicated unreachable fallback block (lines 731-734)
  
  Logic should be:
  ```python
  backend_name = secrets_cfg.get("backend", None)
  if backend_name is None:
      # No explicit choice: try betterleaks, fall back to regex
      try:
          subprocess.run(["betterleaks", "version"], ...)
          backend_name = "betterleaks"
      except (CalledProcessError, FileNotFoundError):
          backend_name = "regex"
  ```

### 🔴 BLOCKING FIX (item 4: egress gate coverage gaps)

- [ ] **Fix 4: Expand egress gate command coverage**
  Update `OUTBOUND_GH_CMD_REGEX` to cover:
  - `gh issue create`, `gh issue edit`, `gh issue comment`
  - `gh pr create`, `gh pr edit`, `gh pr comment`, `gh pr review`
  - `gh gist create`
  - `gh release create`
  - `gh api` (already covered)
  
  Updated regex:
  ```python
  OUTBOUND_GH_CMD_REGEX = re.compile(
      r"(?:^|[;&|]\s*|\s)gh\s+(?:"
      r"issue\s+(?:create|edit|comment)"
      r"|pr\s+(?:create|edit|comment|review)"
      r"|gist\s+create"
      r"|release\s+create"
      r"|api"
      r")\b"
  )
  ```
  
  In `extract_outbound_body()`:
  - Also extract `--title` content (scan titles too, not just bodies)
  - Handle `-F` (alias for `--field`) and `--raw-field` / `-f` for `gh api`
  - Handle `--body-file -` (stdin body): **deny** since content is unscannable. Return a sentinel like `"[UNSCANNABLE_STDIN_BODY]"` that will always match leak patterns, or add a special check in `check_egress_leaks`.
  - Handle chained commands: scan ALL body/title occurrences in the command string, not just the first

  In `check_egress_leaks()`:
  - Also scan extracted titles
  
  Add tests for the new commands and edge cases.

### 🟠 SHOULD FIX (items 5-7)

- [ ] **Fix 5: Expand normalisation character coverage**
  In `safe_ingest.py`, update `UNICODE_HIDDEN_REGEX` to use `unicodedata.category`:
  
  Create a function:
  ```python
  def _strip_format_controls(text: str) -> str:
      """Strip Unicode format-control characters (category Cf) except ZWJ between emoji."""
      import unicodedata
      result = []
      for i, ch in enumerate(text):
          cat = unicodedata.category(ch)
          if cat == "Cf":
              # Keep ZWJ (U+200D) only between emoji (check if prev/next are emoji)
              if ch == '\u200d':
                  # Simple heuristic: keep if adjacent chars exist and are non-ASCII
                  prev_ch = text[i-1] if i > 0 else ''
                  next_ch = text[i+1] if i < len(text)-1 else ''
                  if prev_ch and ord(prev_ch) > 0x2000 and next_ch and ord(next_ch) > 0x2000:
                      result.append(ch)
                      continue
              continue  # strip all other Cf characters
          result.append(ch)
      return ''.join(result)
  ```
  
  Also add these to normalisation:
  - Strip unterminated HTML comments: from `<!--` to end of text if no closing `-->` found
  - Strip markdown comment idiom: `[//]: # (text)` — these render as nothing
  - Strip variation selectors U+FE00-FE0F and U+E0100-E01EF
  
  Update `normalise_visible_content()` to use the new function instead of the regex for Cf characters.

- [ ] **Fix 6: Make `has_hidden_content` actionable**
  In `safe_ingest.py`, in `evaluate_and_sanitize_item()`, when `has_hidden_content` is True:
  - Always include it in the returned dict regardless of `--json` field filtering
  - Add it to the `SAFE_STRUCTURAL_FIELDS` set (if not already there)
  - Set `approval_status` to `"HIDDEN_CONTENT"` so the calling code can act on it
  
  Check if `has_hidden_content` is already in `SAFE_STRUCTURAL_FIELDS`. If so, just ensure it's always present in the output when true.

- [ ] **Fix 7: Apply normalisation to missing ingestion paths**
  In `safe_ingest.py`:
  - In `sanitize_inline_pr_comments()`: apply `normalise_visible_content()` to each comment body after leak redaction
  - In `sanitize_unapproved_item()`: apply `normalise_visible_content()` to trusted users' comment bodies that pass through

### 🟠 SHOULD FIX (items 8-10)

- [ ] **Fix 8: WP3 acceptance criteria gaps**
  In `BetterleaksBackend`:
  - Add `.betterleaksignore` handling: always pass `--ignore-file /dev/null` (or a protected path) to prevent auto-discovery of repo-local ignore files
  - Fix `config_hash()`: when no config_path is given, try to get the hash from betterleaks itself (e.g. `betterleaks config hash` if available), or compute SHA-256 of the default embedded ruleset identifier string
  
  Note: Version pinning and checksum verification in install.sh is out of scope for this PR (install.sh is not touched). But add a TODO comment noting it's needed.

- [ ] **Fix 9: Fix "N files scanned" for Betterleaks**
  In the `scan()` method, `scanned_count` should count findings, not be used for "files scanned". Either:
  - Parse `scan.bytes_scanned` or `scan.files_scanned` from the v2 envelope if available
  - Or don't report file count for betterleaks (just omit it or say "N/A")

- [ ] **Fix 10: Change PR body from "Resolves #22" to "Part of #22"**
  This is a PR metadata fix. Just note this for the PA to update when pushing.

### 🟡 NITS

- [ ] **Nit: Remove duplicated unreachable fallback in resolve_backend** (covered by Fix 3)
- [ ] **Nit: Restore file mode 100755 on pre_tool_gate.py**
  ```bash
  chmod +x scripts/pre_tool_gate.py
  ```
- [ ] **Nit: Make ScanViolation a frozen dataclass**
  Replace the manual class with:
  ```python
  from dataclasses import dataclass
  
  @dataclass(frozen=True)
  class ScanViolation:
      path: str
      line: int
      rule: str
      redacted_match: str
      
      def __str__(self) -> str:
          return f"{self.path}:{self.line} [{self.rule}] -> {self.redacted_match}"
  ```

## Verification Requirement
Run `python3 -m unittest discover -s tests -v` and confirm all tests pass. Update any existing tests that break due to the fixes. Add new tests for new behaviour.

## Issue Surfacing
If you discover a defect, upstream bug, or concern *outside your boundary box*, surface it in your Completion Report below — do not file issues directly.
