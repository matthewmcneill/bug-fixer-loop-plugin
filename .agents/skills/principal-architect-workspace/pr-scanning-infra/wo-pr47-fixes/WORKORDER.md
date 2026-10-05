---
name: scanning-infra-pr47-fixes
description: Fix all PR #47 review observations — 9 items, no deferrals
complexity: architect
---

# PR #47 Review Fixes — All 9 Items

## Role Definition
> You are an expert senior software engineer and execution-focused worker agent. You are NOT the Principal Architect. Do not make high-level architectural decisions, do not branch, and do not plan. Your task is strictly limited to executing the checklist below.

## Objective
Fix all 9 observations from the PR #47 code review. No deferrals.

## CRITICAL WARNING — pre_tool_gate.py is its own PreToolUse hook
Use the safe editing approach: write to temp, validate with py_compile, move if valid.

## Tooling Constraints
- Run tests with: `python3 -m unittest discover -s tests -v`
- No external dependencies — stdlib only

## Line-Item Instructions

### 🔴 BLOCKING (items 1-4)

- [ ] **Fix 1: safe_ingest.py leak redaction broken**
  `safe_ingest.py` still reads `config.get("leak_patterns")` at line ~1407. The v2 config moved patterns to `scanning.secrets.patterns`, so `leak_patterns` is now `None` and secrets pass through unredacted.
  
  Fix: Import or duplicate `normalise_config()` from `pre_tool_gate.py` into `safe_ingest.py`, or create a shared utility. Apply it in `safe_ingest.load_config()` so all config access gets the normalised structure. Then update any code that reads `config.get("leak_patterns")` to also check `config.get("scanning", {}).get("secrets", {}).get("patterns", [])`.
  
  Add a test that loads the real v2 workspace config through `safe_ingest` and confirms patterns are populated.

- [ ] **Fix 2: 43 false positives in regex fallback**
  The new config sets `"exclude_paths": []`. When betterleaks isn't installed, the regex fallback runs with no exclusions and finds 43 matches in test files and workorder files.
  
  Fix: The regex backend should have sensible default exclusions when none are configured. Add a `DEFAULT_REGEX_EXCLUDE_PATHS` list containing at least `["tests/", ".agents/skills/principal-architect-workspace/"]`. When `exclude_paths` is empty AND the backend is regex (degraded mode), use these defaults. Betterleaks has its own ignore mechanism, so it doesn't need these.
  
  Update the config's `exclude_paths` to include the sensible defaults, or apply them in `RegexBackend` when the list is empty.

- [ ] **Fix 3: WO5 Model Armor wrong host and response paths**
  Three problems:
  
  a) **Wrong host**: PR has `modelarmor.{location}-aiplatform.googleapis.com`. Correct: `modelarmor.{location}.rep.googleapis.com`
  
  b) **Wrong result path**: PR reads `filterResults.piAndJailbreakFilterResult`. Correct: `filterResults["pi_and_jailbreak"]["piAndJailbreakFilterResult"]` (note the intermediate `pi_and_jailbreak` key)
  
  c) **Wrong confidence enum values**: PR uses `CONFIDENCE_HIGH`, `CONFIDENCE_MEDIUM`, `CONFIDENCE_LOW`. Correct: `HIGH`, `MEDIUM_AND_ABOVE`, `LOW_AND_ABOVE`
  
  d) Check `executionState == "EXECUTION_SUCCESS"` and treat anything else as an error
  
  e) **Create the `security:prompt-injection` label**: Add it to the `required_labels` list in config.json. Also check the return code of the `gh issue edit --add-label` command and fail closed for that item if labelling fails.
  
  f) Update the mock test fixtures to use the correct response structure.

- [ ] **Fix 4: Protect config.json in Gate A**
  Add `.agents/plugins/bug-fixer-loop-workspace/config.json` to Gate A protected files. Also:
  - Add `injection_scanner.py` to `HOST_SECURITY_SCRIPTS`
  - Add `SCANNER_PROTECTED_FILES` entries to `check_shell_mutation` deny patterns
  - Reject empty string or "/" entries in `exclude_paths` (an empty prefix matches everything)
  - Add the workspace config path pattern to `check_shell_mutation`

### 🟠 SHOULD FIX (items 5-9)

- [ ] **Fix 5: `--body-file -` stdin deny**
  The `[UNSCANNABLE_STDIN_BODY]` placeholder doesn't match leak patterns, so the gate allows it. Add a dedicated check: if the extracted body equals the unscannable sentinel, deny the command with a specific reason like `REASON_UNSCANNABLE_STDIN = "Egress blocked: --body-file - reads from stdin which cannot be scanned for leaks."`. Test this.

- [ ] **Fix 6: Betterleaks `fs` returns absolute paths**
  When `BetterleaksBackend` scans with `scope=TREE`, `fs` is given an absolute repo_dir, so paths come back absolute. Fix: either run with `cwd=repo_dir` and target `.`, or post-process the paths to be relative to `repo_dir`. Also note `fs` scans untracked/gitignored files unlike `git ls-files` — add a comment documenting this difference.

- [ ] **Fix 7: Report metadata fixes**
  - `scan.files_scanned` doesn't exist in v2 — remove or use a field that does exist, or just omit the count for betterleaks
  - `config_hash()`: use `scan.config_hash` from the report when available
  - Remove dead code after `return` in `config_hash()`
  - Fix double version prefix (`backend=betterleaks vdev`)

- [ ] **Fix 8: WO5 design gaps**
  a) **Persistent cache**: Replace in-memory dict with a JSON file cache at `.agents/plugins/bug-fixer-loop-workspace/injection_cache.json`. Read on startup, write after each scan. Key = SHA-256 of content.
  b) **Scan comments too**: Concatenate title + body + all comment bodies for the scan text
  c) **Skip redacted items**: Don't scan items whose body is `[REDACTED: UNAPPROVED EXTERNAL CONTENT]`
  d) **Fail at startup for misconfiguration**: If `enabled: true` but `project` or `service_account` is empty, raise at `InjectionScanner.__init__` time, not per-scan
  e) **Cover engineering and architect dispatchers**: Import and call the scanner in `engineering_dispatcher.py` and `architect_dispatcher.py` too (same pattern as triage)

- [ ] **Fix 9: Change "Resolves #22" to "Part of #22"**
  Note this for the PA to update the PR body.

### 🟡 NITS
- [ ] Add `.agents/skills/principal-architect-workspace/` to `.gitignore` (the workorder files cause regex false positives)

## Verification Requirement
Run `python3 -m unittest discover -s tests -v` and confirm all tests pass. Also run `python3 scripts/pre_tool_gate.py --scan-only` and verify it passes cleanly (0 violations) in regex fallback mode.

## Issue Surfacing
If you discover a defect, upstream bug, or concern *outside your boundary box*, surface it in your Completion Report below — do not file issues directly.
