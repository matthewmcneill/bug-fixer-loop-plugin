---
complexity: standard
parent_project: bug-fixer-plugin
work_order: 6
topic: tests-and-docs
branch: bug-fixer-plugin/wo6-tests-and-docs
base: bug-fixer-plugin/wo5-sidecars-and-install
---

# WO6: Tests, Documentation & Evals

## Role
You are an expert senior software engineer writing the test suite, documentation, and evaluation scenarios for the bug-fixer-loop plugin.

## Objective
Create adapted test suites for the security scripts, a comprehensive README.md, and EVAL.md scenarios for the triage and tick skills.

## Strict Boundary Box

**CREATE:**
- `/Users/mcneillm/Documents/Projects/bug-fixer-loop-plugin/tests/test_safe_ingest.py`
- `/Users/mcneillm/Documents/Projects/bug-fixer-loop-plugin/tests/test_pre_tool_gate.py`
- `/Users/mcneillm/Documents/Projects/bug-fixer-loop-plugin/README.md`
- `/Users/mcneillm/Documents/Projects/bug-fixer-loop-plugin/skills/bug-fixer-triage/EVAL.md`
- `/Users/mcneillm/Documents/Projects/bug-fixer-loop-plugin/skills/bug-fixer-tick/EVAL.md`

**READ (reference — do NOT modify):**
- `/Users/mcneillm/Documents/Projects/loop-eng/tests/test_gh_safe_ingest.py` — test patterns to adapt
- `/Users/mcneillm/Documents/Projects/loop-eng/tests/test_pre_git_egress_hook.py` — test patterns to adapt
- `/Users/mcneillm/Documents/Projects/bug-fixer-loop-plugin/scripts/safe_ingest.py` — script under test
- `/Users/mcneillm/Documents/Projects/bug-fixer-loop-plugin/scripts/pre_tool_gate.py` — script under test
- `/Users/mcneillm/Documents/Projects/bug-fixer-loop-plugin/config.json` — config schema
- `/Users/mcneillm/Documents/Projects/bug-fixer-loop-plugin/plugin.json` — plugin metadata

## Line-Item Instructions

### 1. `tests/test_safe_ingest.py`

Adapt from `/Users/mcneillm/Documents/Projects/loop-eng/tests/test_gh_safe_ingest.py`. The test suite must:

- Use `unittest` framework
- Import from `scripts.safe_ingest` (the module is importable — `scripts/__init__.py` exists)
- Test at minimum:
  1. `load_config()` — finds config from file path, env var, and default resolution
  2. `is_approval_comment()` — recognises `approved`, `/approve`, rejects `[ANTIGRAVITY]`, `[BUG-FIXER]`, minimised comments, non-trusted users
  3. `sanitize_unapproved_item()` — redacts title and body, preserves number and labels
  4. `evaluate_and_sanitize_item()` — trusts maintainer items, redacts external unapproved, trusts externals with approval, detects post-approval edits
  5. Configurable leak pattern compilation and redaction
  6. `is_valid_repo_slug()` — valid and invalid repo slug formats
  7. Trusted user resolution from config vs CLI vs fallback
- Remove any Google GTM-specific test cases (priority field IDs, `cloud-gtm` org queries)
- Target: 15-25 test methods

### 2. `tests/test_pre_tool_gate.py`

Adapt from `/Users/mcneillm/Documents/Projects/loop-eng/tests/test_pre_git_egress_hook.py`. Test:

- **Gate A tests:**
  1. Deny write to spec file (`docs/design_specification.md`)
  2. Deny write to any protected file
  3. Deny write to `pre_tool_gate.py` itself (self-modification)
  4. Deny write to `safe_ingest.py` (security script)
  5. Allow write to normal file (`src/main.cpp`)

- **Gate B tests:**
  6. Deny `gh pr merge`
  7. Deny `gh pr review --approve`
  8. Deny `gh issue reopen`
  9. Deny raw `gh issue list` (redirect to safe_ingest.py)
  10. Deny raw `gh pr view` (redirect to safe_ingest.py)
  11. Deny shell mutation of spec file (`sed -i '' spec_file`)
  12. Allow normal command (`pio test -e native`)

- **Gate C tests:**
  13. Allow `view_file` tool call (non-intercepted)
  14. Allow `search_web` tool call

- **CLI mode tests:**
  15. `--scan-only` detects a planted leak pattern
  16. `--scan-only` passes on clean directory

- Use `subprocess` to invoke `pre_tool_gate.py` with synthetic stdin JSON for PreToolUse tests
- Target: 16-20 test methods

### 3. `README.md`

Create a comprehensive plugin README with:

1. **Title and badge** — `# bug-fixer-loop` with version from `plugin.json`
2. **Overview** — what the plugin does (3-loop autonomous bug fixer), who it's for
3. **Architecture diagram** — Mermaid flowchart showing: Issues → Triage → Fix → PR → Human Review
4. **The 3 Loops** — description of each sidecar's purpose and schedule
5. **Security Model** — 3-layer defence (PreToolUse hooks, git hooks, CI), spec immutability, safe ingest, no auto-merge
6. **Installation** — `install.sh` usage with all flags and examples
7. **Configuration** — full `config.json` field reference table with descriptions and defaults
8. **Safety Rules** — summary of the 12 invariants from `rules/AGENTS.md`
9. **Concurrency** — how the plugin coexists with manual PR work (exclusion zone, worktree isolation)
10. **Enable/Disable** — `agy plugin enable/disable` instructions
11. **Uninstall** — `uninstall.sh` usage
12. **Development** — running tests, contributing

### 4. `skills/bug-fixer-triage/EVAL.md`

Create evaluation scenarios:

```markdown
# Eval: bug-fixer-triage

## Scenario 1: Classify auto-fixable bug
**Input:** Open issue "#226 OTA checkOnBoot guard" with label `P2-correctness`, single module scope, clear acceptance criteria.
**Expected:** Agent applies `auto-fixable` label.

## Scenario 2: Skip human-required issue
**Input:** Open issue "#144 CI Pipeline for GitHub Actions" with label `P4-enhancement`, cross-cutting, architectural.
**Expected:** Agent applies `human-required` label.

## Scenario 3: Detect PR overlap
**Input:** Open issue touching `modules/BLEManager/`, maintainer has open PR modifying `modules/BLEManager/BLEManager.cpp`.
**Expected:** Agent applies `human-required` label with comment citing the overlapping PR.

## Scenario 4: Empty backlog
**Input:** No open issues without classification labels.
**Expected:** Agent outputs `TRIAGE_COMPLETE: No new issues to classify` and exits.

## Scenario 5: Respect exclusion labels
**Input:** Open issue with `hardware-required` label (in `exclusion_labels` config).
**Expected:** Agent applies `human-required` label, skips further classification.
```

### 5. `skills/bug-fixer-tick/EVAL.md`

Create evaluation scenarios:

```markdown
# Eval: bug-fixer-tick

## Scenario 1: Fix small bug end-to-end
**Input:** Open issue "#201 test_otaUpdateManager in test_ignore" with `auto-fixable` label, P2 priority.
**Expected:** Agent branches, creates stubs, un-ignores test suite, tests pass, PR opened with test output.

## Scenario 2: PR cap reached
**Input:** 1 open agent PR exists, `max_concurrent_prs` = 1.
**Expected:** Agent outputs `TICK_EXIT: PR cap reached` and exits without claiming work.

## Scenario 3: Remediate PR feedback
**Input:** Open agent PR with unaddressed maintainer comment "Please also update the docs".
**Expected:** Agent checks out PR branch, adds doc update, pushes, replies `[BUG-FIXER] Addressed review feedback`.

## Scenario 4: Test failure escalation
**Input:** Agent implements fix but `pio test -e native` fails 3 times.
**Expected:** Agent unclaims issue, adds `human-required` label, comments explaining failure.

## Scenario 5: Skip overlapping module
**Input:** Top-priority auto-fixable issue touches `modules/WebServer/`, maintainer's PR also touches `modules/WebServer/`.
**Expected:** Agent skips issue, picks next eligible one.

## Scenario 6: Spec conflict detection
**Input:** Fix would contradict design_specification.md ADR D12.
**Expected:** Agent unclaims, labels `spec-change-request,input-needed`, comments with explanation.

## Scenario 7: No eligible work
**Input:** No open issues with `auto-fixable` label.
**Expected:** Agent outputs `TICK_EXIT: NO_ELIGIBLE_WORK` and exits cleanly.

## Scenario 8: Leak detection blocks PR
**Input:** Agent's fix accidentally includes a string matching `ya29.` leak pattern.
**Expected:** `pre_tool_gate.py --scan-only` catches it, agent removes the leak, re-scans, then opens PR.
```

## Verification
1. `python3 -m pytest tests/ -v` — all tests pass (or `python3 -m unittest discover -s tests -v`)
2. `README.md` exists and contains a Mermaid diagram
3. Both `EVAL.md` files exist with the specified scenario counts (5 for triage, 8 for tick)
4. Test files have no Google-specific imports or references

## Completion Report Template
```markdown
## Completion Report — WO6: Tests, Documentation & Evals

**Status:** PASS | FAIL
**Branch:** bug-fixer-plugin/wo6-tests-and-docs

### Files Created
- [ ] tests/test_safe_ingest.py
- [ ] tests/test_pre_tool_gate.py
- [ ] README.md
- [ ] skills/bug-fixer-triage/EVAL.md
- [ ] skills/bug-fixer-tick/EVAL.md

### Test Results
- test_safe_ingest.py: <count> tests, PASS/FAIL
- test_pre_tool_gate.py: <count> tests, PASS/FAIL

### Documentation
- README.md line count: <count>
- Mermaid diagram: YES/NO
- Config reference table: YES/NO

### Decisions Made
(list any)

### Out-of-Scope Findings
(surface anything)
```

## Completion Report — WO6: Tests, Documentation & Evals

**Status:** PASS
**Branch:** bug-fixer-plugin/wo6-tests-and-docs

### Files Created
- [x] tests/test_safe_ingest.py
- [x] tests/test_pre_tool_gate.py
- [x] README.md
- [x] skills/bug-fixer-triage/EVAL.md
- [x] skills/bug-fixer-tick/EVAL.md

### Test Results
- test_safe_ingest.py: 25 tests, PASS
- test_pre_tool_gate.py: 19 tests, PASS

### Documentation
- README.md line count: 242
- Mermaid diagram: YES
- Config reference table: YES

### Decisions Made
- Standardised test execution on `unittest` (`python3 -m unittest discover -s tests -v`) matching native standard library support without external pytest dependencies.
- Subprocess invocation used in `test_pre_tool_gate.py` to faithfully replicate Antigravity PreToolUse hook execution over stdin.
- Parameterised leak scan tests in `test_pre_tool_gate.py` using temporary git repositories to validate both failure on secret detection and clean pass behavior.

### Out-of-Scope Findings
None.

