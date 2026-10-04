---
name: scanning-infra-wo2
description: Handoff instructions for Work Order 2 — ScanBackend interface with scope and receipt metadata
complexity: standard
---

# WO2 — `ScanBackend` Interface with Scope + Receipt Metadata

## Role Definition
> You are an expert senior software engineer and execution-focused worker agent. You are NOT the Principal Architect. Do not make high-level architectural decisions, do not branch, and do not plan. Your task is strictly limited to executing the checklist below.

## Objective
Refactor `scan_repository()` in `pre_tool_gate.py` to use a pluggable `ScanBackend` protocol with explicit scan scope (`staged`, `tree`, `history`). Update the leak audit receipt to record which backend, version, and config hash produced the result, so a PASS from 3 regex patterns can't be confused with a PASS from hundreds of detectors.

## Strict Boundary Box
- **ONLY** modify `scripts/pre_tool_gate.py` and `tests/test_pre_tool_gate.py`
- Do NOT touch `safe_ingest.py`, `github_url_gate.py`, dispatchers, or skills
- Do NOT modify Gate A or Gate B logic
- Do NOT implement any external backends (Betterleaks, Gitleaks, Trufflehog) — those come in WO3. This WO creates the interface and refactors the existing regex scanner to use it.

## Tooling Constraints
- Run tests with: `python3 -m unittest discover -s tests -v`
- No external dependencies — stdlib only
- Use `typing.Protocol` for the backend interface (available in Python 3.8+)

## Line-Item Instructions

- [ ] `scripts/pre_tool_gate.py` — Define the scan scope enum and violation type:
  ```python
  from enum import Enum
  
  class ScanScope(Enum):
      STAGED = "staged"   # git diff --cached (pre-commit hook)
      TREE = "tree"       # git ls-files (full working tree)
      HISTORY = "history"  # git log (CI audit)
  
  class ScanViolation:
      def __init__(self, path: str, line: int, rule: str, redacted_match: str):
          self.path = path
          self.line = line
          self.rule = rule
          self.redacted_match = redacted_match
      
      def __str__(self) -> str:
          return f"{self.path}:{self.line} [{self.rule}] -> {self.redacted_match}"
  ```

- [ ] `scripts/pre_tool_gate.py` — Define the `ScanBackend` protocol:
  ```python
  from typing import Protocol
  
  class ScanBackend(Protocol):
      name: str       # e.g. "regex", "betterleaks"
      version: str    # e.g. "builtin", "2.0.0"
      
      def scan(self, repo_dir: str, scope: ScanScope, 
               exclude_paths: list[str] | None = None) -> list[ScanViolation]: ...
      
      def config_hash(self) -> str: ...  # SHA-256 of the active ruleset/config
  ```

- [ ] `scripts/pre_tool_gate.py` — Create `RegexBackend` class implementing `ScanBackend`:
  - Wraps the current `scan_repository()` logic
  - `name = "regex"`, `version = "builtin"`
  - `config_hash()` returns SHA-256 of the sorted, joined pattern strings
  - `scan()` with `scope=TREE` does the current `git ls-files` walk
  - `scan()` with `scope=STAGED` does `git diff --cached --name-only` walk (only scan staged files)
  - `scan()` with `scope=HISTORY` raises `NotImplementedError` (regex backend doesn't support history scanning)
  - Respects `exclude_paths` (uses the existing `scan_exclude_paths` prefix matching from #16)
  - Uses `ScanViolation` objects with redacted match text (from WO0a)

- [ ] `scripts/pre_tool_gate.py` — Create a factory function `resolve_backend(config: dict) -> ScanBackend`:
  - Reads `config.get("scanning", {}).get("secrets", {}).get("backend", "regex")`
  - For now, only `"regex"` is supported — returns `RegexBackend(patterns)`
  - If an unrecognised backend is requested, falls back to regex and prints a warning to stderr: `WARNING: Unknown scan backend '<name>', falling back to regex (degraded)`

- [ ] `scripts/pre_tool_gate.py` — Refactor `scan_repository()` to be a thin wrapper around `resolve_backend().scan()`:
  ```python
  def scan_repository(repo_dir: str, patterns: list[str],
                      exclude_paths: list[str] | None = None,
                      config: dict | None = None) -> list[str]:
  ```
  This preserves the existing call sites while routing through the new backend.

- [ ] `scripts/pre_tool_gate.py` — Update `stamp_receipt()` to include backend metadata:
  ```python
  payload = {
      "status": status,
      "tree_sha": tree_sha,
      "timestamp": datetime.now(UTC).isoformat(),
      "backend": backend.name,
      "backend_version": backend.version,
      "config_hash": backend.config_hash(),
      "validate": False,
  }
  ```
  Update the callers (`run_git_hook`, `main --scan-only`) to pass the backend info through.

- [ ] `scripts/pre_tool_gate.py` — Update `--scan-only` output to show backend:
  ```
  LEAK SCAN CLEAN (repo=..., backend=regex (degraded), 12 files scanned, 3 excluded)
  ```
  or:
  ```
  LEAK SCAN CLEAN (repo=..., backend=betterleaks v2.0.0, 12 files scanned, 3 excluded)
  ```

- [ ] `tests/test_pre_tool_gate.py` — Add tests:
  1. Test `RegexBackend.scan()` with `scope=TREE` matches existing `scan_repository` behaviour
  2. Test `RegexBackend.scan()` with `scope=STAGED` only scans staged files
  3. Test `RegexBackend.config_hash()` returns consistent SHA-256 for the same patterns
  4. Test `resolve_backend()` returns `RegexBackend` for `"regex"` and unknown backends
  5. Test that the receipt now includes `backend`, `backend_version`, and `config_hash` fields
  6. Test `ScanViolation.__str__()` format

## Verification Requirement
Run `python3 -m unittest discover -s tests -v` and confirm all tests pass (existing + new). Verify that the refactored `scan_repository` produces identical results to the pre-refactor version on this repository.

## Issue Surfacing
If you discover a defect, upstream bug, or concern *outside your boundary box*, surface it in your Completion Report below — do not file issues directly. The Principal Architect will triage and file them under the correct author identity.
