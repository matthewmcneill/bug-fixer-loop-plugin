# Eval: bug-fixer-triage

## Scenario 1: T1 — Auto-fixable small bug
**Input:** Issue "#201 test_otaUpdateManager in test_ignore", single module, clear criteria.
**Expected:** Classified as `auto-fixable` (T1).

## Scenario 2: T2 — Needs approach review
**Input:** Issue "#226 OTA checkOnBoot guard", single module but touches initialisation sequence.
**Expected:** Classified as `needs-approach-review` (T2), approach comment posted.

## Scenario 3: T2 → T1 — Approved approach
**Input:** Issue with `needs-approach-review` label, maintainer replied "approved".
**Expected:** Reclassified to `auto-fixable`, ready for tick.

## Scenario 4: T3 — Input needed
**Input:** Issue with vague description, no repro steps.
**Expected:** Classified as `input-needed` (T3), clarification comment posted.

## Scenario 5: T4 — Project candidate
**Input:** Issue "#144 CI Pipeline for GitHub Actions", cross-cutting, 500+ lines estimated.
**Expected:** Classified as `project-candidate` (T4), /principal-architect comment posted.

## Scenario 6: T5 — Human required (exclusion label)
**Input:** Issue with `hardware-required` label.
**Expected:** Classified as `human-required` (T5), skipped.

## Scenario 7: T5 — Human required (PR overlap)
**Input:** Issue touching `modules/BLEManager/`, maintainer PR active on same module.
**Expected:** Classified as `human-required` (T5), comment citing overlapping PR.

## Scenario 8: Milestone prioritisation
**Input:** Two T1 issues: #201 (no milestone) and #226 (Milestone "v1.1", due next week).
**Expected:** #226 appears first in priority ordering.

## Scenario 9: Active milestone focus
**Input:** `active_milestone` set to "v1.1", issues in "v1.1" and "v2.0".
**Expected:** Only "v1.1" issues get priority boost, "v2.0" issues ordered normally.

## Scenario 10: Empty backlog
**Input:** No unclassified issues.
**Expected:** `TRIAGE_COMPLETE: No new issues to classify`, clean exit.

## Scenario 11: Batch bundling — same audit group
**Input:** Three T1 issues from the same audit: #254 (std::function removal), #255 (bare new → nothrow), #256 (audit script fix). All share `audit` label, #254 and #255 overlap on BLEStack.cpp.
**Expected:** #254 and #255 grouped into `batch:memory-rules-compliance` (same spec section, overlapping files). #256 left unbatched or in a separate `batch:audit-script-fixes` (different change domain).

## Scenario 12: Batch bundling — different milestones blocks grouping
**Input:** Two T1 issues with same change pattern but different milestones.
**Expected:** NOT bundled — milestone mismatch prevents grouping.

## Scenario 13: Batch bundling — standalone T1 not forced into a batch
**Input:** One T1 issue with no affinity to any other open T1.
**Expected:** No batch label applied, issue remains standalone.

## Scenario 14: Re-bundling — new issue joins existing batch
**Input:** Existing batch `batch:doc-header-cleanup` with 3 issues. New T1 issue classified that matches the same pattern.
**Expected:** New issue added to existing batch (same label applied), grouping comment posted with updated sibling list.
