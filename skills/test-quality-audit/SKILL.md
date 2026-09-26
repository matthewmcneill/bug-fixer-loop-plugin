---
name: test-quality-audit
description: >-
  Audit test quality for bug-fixer PRs using a 6-point anti-reward-hacking
  rubric. Called inline by bug-fixer-tick before opening a PR, or manually
  with "audit test quality".
---

# Test Quality Audit

Use this 6-point anti-reward-hacking rubric to audit the quality of tests written by the bug-fixer before opening a PR. Tests must be genuine and robust, not superficial smoke tests.

## 6-Point Rubric

Evaluate the modified or newly added tests against the following criteria. An overall **PASS** requires all 6 points to pass. Output a pass/fail for each point with an explanation.

1. **Assertion Density:** Every test function must have at least one meaningful assertion. Fail if any test body lacks a real assertion (e.g., lacks `ASSERT_*`, `EXPECT_*`, `assertEqual`, `assertTrue`, or equivalent).

2. **Input Diversity:** Tests must cover at least 2 distinct input scenarios (e.g., happy path + edge case). Fail if all tests use identical inputs.

3. **Negative Testing:** At least one test must verify error handling, rejection, or boundary violations. Fail if all tests only test success/happy paths.

4. **No Tautological Assertions:** Assertions must not compare a value to itself (e.g., `EXPECT_EQ(x, x)`), assert the literal `true`, or test that a mock returns what it was explicitly configured to return without exercising the real underlying logic.

5. **Scope Alignment:** Tests must exercise the actual code that was changed in the PR, not unrelated modules. Fail if the new tests only cover untouched code.

6. **No Test Suppression:** The PR must not add entries to any `test_ignore` file, skip lists, or place `#if 0` (or similar) blocks around existing tests. Existing test coverage must not decrease.

## Output

Provide a clear pass/fail status for each point, accompanied by a brief explanation. If all 6 pass, output `OVERALL PASS`. If any fail, output `OVERALL FAIL` and require the tests to be improved.
