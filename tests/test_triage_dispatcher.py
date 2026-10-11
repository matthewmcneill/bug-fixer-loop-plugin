"""Unit tests for triage_dispatcher T2 approval detection logic.

Tests the pending approval heuristic that prevents approved T2 issues
from becoming dead-ends when no untriaged issues exist.
"""

import unittest

# The detection logic is inline in the dispatcher script (module-level),
# so we test the heuristic as a pure function extracted here.


# ---- Extracted constants (must match triage_dispatcher.py) ----

BOT_COMMENT_PREFIXES = (
    "[BUG-FIXER]",
    "[BUG-FIXER-TRIAGE]",
    "[ANTIGRAVITY]",
    "[CONTEXT]",
)

CLASSIFICATION_LABELS = {
    "bug-fixer:engineer:needs-fix",
    "bug-fixer:human:needs-approach-review",
    "bug-fixer:human:input-needed",
    "bug-fixer:human:project-candidate",
    "bug-fixer:human:required",
}

SKIP_LABELS = CLASSIFICATION_LABELS | {
    "bug-fixer:engineer:in-progress",
}


# ---- Helper: replicate the dispatcher's detection logic ----

def find_pending_approvals(issues: list[dict]) -> list[dict]:
    """Detect T2 issues where a human has replied after the bot's approach comment."""
    pending = []
    for issue in issues:
        label_names = {l.get("name", "") for l in issue.get("labels", [])}
        if "bug-fixer:human:needs-approach-review" not in label_names:
            continue
        comments = issue.get("comments", [])
        if not comments:
            continue
        last_body = (comments[-1].get("body") or "").lstrip()
        if not any(last_body.startswith(prefix) for prefix in BOT_COMMENT_PREFIXES):
            pending.append(issue)
    return pending


def find_untriaged(issues: list[dict]) -> list[dict]:
    """Replicate the dispatcher's untriaged-issue filter."""
    untriaged = []
    for issue in issues:
        label_names = {l.get("name", "") for l in issue.get("labels", [])}
        if label_names & SKIP_LABELS:
            continue
        if "bug-fixer:architect:awaiting-review" in label_names and label_names & CLASSIFICATION_LABELS:
            continue
        untriaged.append(issue)
    return untriaged


# ---- Fixture factories ----

def _issue(number, labels=None, comments=None):
    return {
        "number": number,
        "title": f"Issue #{number}",
        "body": "test body",
        "labels": [{"name": l} for l in (labels or [])],
        "comments": comments or [],
    }


def _comment(body, author="matthewmcneill"):
    return {"author": {"login": author}, "body": body}


class TestPendingApprovalDetection(unittest.TestCase):
    """Tests for the T2 approval heuristic in triage_dispatcher."""

    def test_no_issues_returns_empty(self):
        self.assertEqual(find_pending_approvals([]), [])

    def test_issue_without_approach_review_label_is_skipped(self):
        issues = [_issue(1, labels=["bug"])]
        self.assertEqual(find_pending_approvals(issues), [])

    def test_approach_review_with_no_comments_is_skipped(self):
        """No comments means no one has replied — nothing to transition."""
        issues = [_issue(1, labels=["bug-fixer:human:needs-approach-review"])]
        self.assertEqual(find_pending_approvals(issues), [])

    def test_approach_review_with_only_bot_comment_is_skipped(self):
        """Bot posted the approach review but no human replied yet."""
        issues = [_issue(
            1,
            labels=["bug-fixer:human:needs-approach-review"],
            comments=[_comment("[BUG-FIXER] Proposed approach for #1:\n\nReply with approved.")]
        )]
        self.assertEqual(find_pending_approvals(issues), [])

    def test_approach_review_with_human_reply_is_detected(self):
        """Human replied after bot — should trigger a triage spawn."""
        issues = [_issue(
            67,
            labels=["todo", "bug-fixer:human:needs-approach-review"],
            comments=[
                _comment("[BUG-FIXER] Proposed approach for #67:\n\nReply with approved."),
                _comment("Sounds good to me. Approved"),
            ]
        )]
        result = find_pending_approvals(issues)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["number"], 67)

    def test_simple_approved_reply_is_detected(self):
        """A bare 'Approved' comment triggers detection."""
        issues = [_issue(
            10,
            labels=["bug-fixer:human:needs-approach-review"],
            comments=[
                _comment("[BUG-FIXER] Proposed approach for #10:"),
                _comment("Approved"),
            ]
        )]
        result = find_pending_approvals(issues)
        self.assertEqual(len(result), 1)

    def test_bot_reply_after_human_does_not_trigger(self):
        """If bot commented last (e.g. re-posting), no trigger."""
        issues = [_issue(
            10,
            labels=["bug-fixer:human:needs-approach-review"],
            comments=[
                _comment("[BUG-FIXER] Proposed approach for #10:"),
                _comment("Approved"),
                _comment("[BUG-FIXER] Re-evaluated: still pending."),
            ]
        )]
        result = find_pending_approvals(issues)
        self.assertEqual(len(result), 0)

    def test_all_bot_prefix_variants_are_recognized(self):
        """All BOT_COMMENT_PREFIXES suppress the trigger."""
        for prefix in BOT_COMMENT_PREFIXES:
            with self.subTest(prefix=prefix):
                issues = [_issue(
                    1,
                    labels=["bug-fixer:human:needs-approach-review"],
                    comments=[_comment(f"{prefix} Some automated content.")]
                )]
                self.assertEqual(find_pending_approvals(issues), [])

    def test_multiple_issues_mixed(self):
        """Only issues meeting the criteria are returned."""
        issues = [
            # Untriaged — no classification label
            _issue(1, labels=["bug"]),
            # T2 with bot-only comment — not pending
            _issue(2,
                   labels=["bug-fixer:human:needs-approach-review"],
                   comments=[_comment("[BUG-FIXER] Approach review")]),
            # T2 with human reply — pending
            _issue(3,
                   labels=["bug-fixer:human:needs-approach-review"],
                   comments=[
                       _comment("[BUG-FIXER] Approach review"),
                       _comment("Looks good, approved."),
                   ]),
            # Already T1 — not pending
            _issue(4, labels=["bug-fixer:engineer:needs-fix"]),
        ]
        result = find_pending_approvals(issues)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["number"], 3)


class TestUntriagedFilterExcludesT2(unittest.TestCase):
    """Verify that the untriaged filter correctly skips T2 issues,
    confirming that the pending_approvals path is necessary."""

    def test_approach_review_issue_is_not_untriaged(self):
        """This is the root cause: T2 issues are filtered out of `untriaged`."""
        issues = [_issue(67, labels=["bug-fixer:human:needs-approach-review"])]
        self.assertEqual(find_untriaged(issues), [])

    def test_unlabeled_issue_is_untriaged(self):
        issues = [_issue(1, labels=["bug"])]
        self.assertEqual(len(find_untriaged(issues)), 1)

    def test_needs_fix_issue_is_not_untriaged(self):
        issues = [_issue(1, labels=["bug-fixer:engineer:needs-fix"])]
        self.assertEqual(find_untriaged(issues), [])


class TestSpawnDecision(unittest.TestCase):
    """Test that the combined spawn gate works: spawn if either
    untriaged OR pending_approvals is non-empty."""

    def test_no_work_no_spawn(self):
        issues = [
            _issue(1, labels=["bug-fixer:engineer:needs-fix"]),
            _issue(2, labels=["bug-fixer:human:needs-approach-review"],
                   comments=[_comment("[BUG-FIXER] Approach review")]),
        ]
        untriaged = find_untriaged(issues)
        pending = find_pending_approvals(issues)
        should_spawn = bool(untriaged or pending)
        self.assertFalse(should_spawn)

    def test_only_untriaged_spawns(self):
        issues = [_issue(1, labels=["bug"])]
        untriaged = find_untriaged(issues)
        pending = find_pending_approvals(issues)
        should_spawn = bool(untriaged or pending)
        self.assertTrue(should_spawn)

    def test_only_pending_approval_spawns(self):
        """The fix: pending approvals alone are enough to spawn."""
        issues = [_issue(
            67,
            labels=["bug-fixer:human:needs-approach-review"],
            comments=[
                _comment("[BUG-FIXER] Proposed approach"),
                _comment("Approved"),
            ]
        )]
        untriaged = find_untriaged(issues)
        pending = find_pending_approvals(issues)
        self.assertEqual(len(untriaged), 0)
        self.assertEqual(len(pending), 1)
        should_spawn = bool(untriaged or pending)
        self.assertTrue(should_spawn)

    def test_both_untriaged_and_pending_spawns(self):
        issues = [
            _issue(1, labels=["bug"]),
            _issue(67,
                   labels=["bug-fixer:human:needs-approach-review"],
                   comments=[
                       _comment("[BUG-FIXER] Proposed approach"),
                       _comment("Approved"),
                   ]),
        ]
        untriaged = find_untriaged(issues)
        pending = find_pending_approvals(issues)
        self.assertEqual(len(untriaged), 1)
        self.assertEqual(len(pending), 1)
        should_spawn = bool(untriaged or pending)
        self.assertTrue(should_spawn)


if __name__ == "__main__":
    unittest.main()
