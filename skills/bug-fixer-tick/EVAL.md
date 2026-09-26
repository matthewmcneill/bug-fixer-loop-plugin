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
