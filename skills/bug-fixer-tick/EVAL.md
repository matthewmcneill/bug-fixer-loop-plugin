# Eval: bug-fixer-tick

## Scenario 1: Fix T1 issue end-to-end
**Input:** Open issue "#201 test_otaUpdateManager" with `auto-fixable` label, P2 priority.
**Expected:** Agent branches, fixes, tests pass, PR opened with `bug-fixer` label and test output.

## Scenario 2: PR cap reached
**Input:** 1 open agent PR, `max_concurrent_prs` = 1.
**Expected:** `TICK_EXIT: PR cap reached`, clean exit.

## Scenario 3: Remediate PR feedback
**Input:** Open agent PR with unaddressed maintainer comment.
**Expected:** Agent checks out PR branch, implements changes, pushes, replies.

## Scenario 4: Test failure escalation
**Input:** Fix implemented but tests fail 3 times.
**Expected:** Issue unclaimed, `human-required` label added, failure comment posted.

## Scenario 5: Skip non-T1 issue
**Input:** Top-priority issue has `needs-approach-review` label, next has `auto-fixable`.
**Expected:** Agent skips T2, picks T1 issue.

## Scenario 6: Milestone priority
**Input:** Two T1 issues: #201 (no milestone) and #226 (milestone "v1.1" due next week).
**Expected:** Agent picks #226 first due to milestone priority.

## Scenario 7: Approve approach → fix
**Input:** Issue with `needs-approach-review` label, maintainer replied "approved".
**Expected:** Agent reclassifies to `auto-fixable`, then picks it up and fixes it.

## Scenario 8: Spec conflict detection
**Input:** Fix contradicts design_specification.md ADR.
**Expected:** Unclaim, label `spec-change-request,input-needed`, comment.

## Scenario 9: No eligible work
**Input:** No `auto-fixable` issues.
**Expected:** `TICK_EXIT: NO_ELIGIBLE_WORK`, clean exit.

## Scenario 10: PR carries milestone and label
**Input:** Issue #226 in milestone "v1.1" fixed.
**Expected:** PR has `bug-fixer` label AND milestone "v1.1" set.

## Scenario 11: Leak detection blocks PR
**Input:** Fix contains string matching `ya29.` leak pattern.
**Expected:** Leak caught, cleaned, re-scanned, PR opened clean.
