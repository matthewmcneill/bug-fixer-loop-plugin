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
