---
name: scanning-infra-wo3
description: Handoff instructions for Work Order 3 — Betterleaks v2 backend with regex fallback
complexity: architect
---

# WO3 — Betterleaks v2 Backend + Regex Fallback

## Role Definition
> You are an expert senior software engineer and execution-focused worker agent. You are NOT the Principal Architect. Do not make high-level architectural decisions, do not branch, and do not plan. Your task is strictly limited to executing the checklist below.

## Objective
Implement a `BetterleaksBackend` class that integrates Betterleaks v2 as the primary secret scanner behind the `ScanBackend` interface created in WO2. When Betterleaks is not installed, fall back gracefully to the regex backend with a visible `backend=regex (degraded)` note. This is the WO that upgrades the scanner from 3 regex patterns to hundreds of detectors.

## Strict Boundary Box
- **ONLY** modify `scripts/pre_tool_gate.py`, `tests/test_pre_tool_gate.py`, and `.agents/plugins/bug-fixer-loop-workspace/config.json`
- Do NOT touch `safe_ingest.py`, `github_url_gate.py`, dispatchers, or skills
- Do NOT modify Gate A, Gate B, or egress scanning logic
- Do NOT modify the `ScanBackend` protocol or `RegexBackend` class (those are from WO2)
- Do NOT install Betterleaks — the backend must handle its absence gracefully

## Tooling Constraints
- Run tests with: `python3 -m unittest discover -s tests -v`
- No external Python dependencies — stdlib only
- Betterleaks is called via `subprocess` — never imported as a library
- Tests must work without Betterleaks installed (mock the subprocess calls)

## Critical Security Constraints (from Issue #22 consensus)

These are non-negotiable. Failure to implement any of these is a blocking defect:

1. **Forced flags on every invocation:** `--validate=false --analyze=false --redact --no-banner`
2. **Strip environment variables:** Remove ALL `BETTERLEAKS_*` variables from the subprocess environment before calling betterleaks. `BETTERLEAKS_VALIDATE`, `BETTERLEAKS_ANALYZE`, `BETTERLEAKS_CONFIG_TOML` can all override CLI flags.
3. **Explicit config:** Always pass `--config <path>` — never rely on auto-discovery
4. **Local sources only:** Only use `git --staged` (for hooks) and `dir` / `git` (for tree/audit scans). Never use `github`, `url`, `s3`, or any network source.
5. **Never forward raw match text:** Parse the v2 JSON output and extract only `path`, `line`, `rule_id`, and the redacted match. The `Secret`, `Match`, `Raw` fields (whatever they're named in v2) must never appear in `ScanViolation` objects, stdout, logs, or receipts.

## Line-Item Instructions

- [ ] `scripts/pre_tool_gate.py` — Create `BetterleaksBackend` class implementing `ScanBackend`:
  ```python
  class BetterleaksBackend:
      name = "betterleaks"
      
      def __init__(self, config_path: str | None = None):
          # Detect betterleaks binary
          # Store config path
          
      @property
      def version(self) -> str:
          # Run betterleaks version and parse output
          # Cache the result
          
      def _build_env(self) -> dict[str, str]:
          # Copy os.environ, remove ALL keys starting with BETTERLEAKS_
          
      def _build_cmd(self, repo_dir: str, scope: ScanScope) -> list[str]:
          # Build the command with forced flags
          # scope=STAGED: betterleaks git --staged <repo_dir> ...
          # scope=TREE: betterleaks dir <repo_dir> ...
          # scope=HISTORY: betterleaks git <repo_dir> ...
          # Always include: --validate=false --analyze=false --redact
          #   --no-banner --report-format json --exit-code 0
          #   --config <config_path>
          
      def scan(self, repo_dir: str, scope: ScanScope,
               exclude_paths: list[str] | None = None) -> list[ScanViolation]:
          # Run betterleaks subprocess with cleaned env
          # Parse JSON output
          # Convert to ScanViolation objects (redacted only!)
          # Apply exclude_paths filtering
          
      def config_hash(self) -> str:
          # SHA-256 of the config file contents
  ```

- [ ] `scripts/pre_tool_gate.py` — Update `resolve_backend()`:
  - If config requests `"betterleaks"` (or it's the default when `scanning` config exists):
    - Check if `betterleaks` binary is available (try `subprocess.run(["betterleaks", "version"], ...)`)
    - If available: return `BetterleaksBackend(config_path)`
    - If not available: print `WARNING: betterleaks not found, falling back to regex (degraded)` to stderr and return `RegexBackend`
  - If config requests `"regex"`: return `RegexBackend` directly

- [ ] `.agents/plugins/bug-fixer-loop-workspace/config.json` — Do NOT add a `scanning` section yet (that's WO4/Config v2). The backend resolution should work with the *absence* of a `scanning` config — defaulting to regex when no scanning config exists, and to betterleaks when a scanning config exists with `backend: "betterleaks"`.

- [ ] `tests/test_pre_tool_gate.py` — Add tests:
  1. Test `BetterleaksBackend._build_env()` strips all `BETTERLEAKS_*` variables
  2. Test `BetterleaksBackend._build_cmd()` always includes the forced flags
  3. Test `BetterleaksBackend._build_cmd()` with `scope=STAGED` uses `git --staged`
  4. Test `BetterleaksBackend._build_cmd()` with `scope=TREE` uses `dir`
  5. Test `BetterleaksBackend.scan()` parses mock JSON output correctly (mock subprocess)
  6. Test that raw secret text from mock Betterleaks output is NOT present in `ScanViolation` objects
  7. Test `resolve_backend()` falls back to regex when betterleaks binary is not found
  8. Test `resolve_backend()` returns `BetterleaksBackend` when binary is found and config requests it

## Verification Requirement
Run `python3 -m unittest discover -s tests -v` and confirm all tests pass. All tests must pass **without Betterleaks installed** (use mocks for subprocess calls). Verify that no test output contains unredacted secret text.

## Issue Surfacing
If you discover a defect, upstream bug, or concern *outside your boundary box*, surface it in your Completion Report below — do not file issues directly. The Principal Architect will triage and file them under the correct author identity.

## Completion Report
**Status**: Completed and Verified
**Tests Passing**: Yes (134 tests passed)
**Defects/Concerns Surfaced**: None. The implementation works gracefully without Betterleaks installed by using the mock tests. `pre_tool_gate.py` import hygiene is maintained.
