"""Unit tests for triage_dispatcher T2 approval detection and deduplication logic.

Tests the pending approval heuristic, PA review prefix filtering, comment-ID
deduplication persistence, and main() spawn integration.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPTS_DIR = (
    Path(__file__).resolve().parent.parent
    / "daemon"
    / "workspace-plugin"
    / "bug-fixer"
    / "scripts"
)
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import triage_dispatcher  # noqa: E402
from triage_dispatcher import (  # noqa: E402
    BOT_COMMENT_PREFIXES,
    find_pending_approvals,
    find_untriaged,
    load_seen_approvals,
    record_dispatched_approvals,
    save_seen_approvals,
)


# ---- Fixture factories ----

def _issue(number, labels=None, comments=None):
    return {
        "number": number,
        "title": f"Issue #{number}",
        "body": "test body",
        "labels": [{"name": lbl} for lbl in (labels or [])],
        "comments": comments or [],
    }


def _comment(body, author="matthewmcneill", comment_id=None):
    data = {"author": {"login": author}, "body": body}
    if comment_id is not None:
        data["id"] = comment_id
    return data


class TestPendingApprovalDetection(unittest.TestCase):
    """Tests for the T2 approval heuristic and dedup in triage_dispatcher."""

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
            comments=[_comment("[BUG-FIXER] Proposed approach for #1:\n\nReply with approved.")],
        )]
        self.assertEqual(find_pending_approvals(issues), [])

    def test_approach_review_with_human_reply_is_detected(self):
        """Human replied after bot — should trigger a triage spawn."""
        issues = [_issue(
            67,
            labels=["todo", "bug-fixer:human:needs-approach-review"],
            comments=[
                _comment("[BUG-FIXER] Proposed approach for #67:\n\nReply with approved."),
                _comment("Sounds good to me. Approved", comment_id="IC_67"),
            ],
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
                _comment("Approved", comment_id="IC_10"),
            ],
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
            ],
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
                    comments=[_comment(f"{prefix} Some automated content.")],
                )]
                self.assertEqual(find_pending_approvals(issues), [])

    def test_principal_architect_review_comment_is_ignored(self):
        """PA review comments must not be mistaken for human replies."""
        issues = [_issue(
            42,
            labels=["bug-fixer:human:needs-approach-review"],
            comments=[
                _comment("[BUG-FIXER] Proposed approach for #42:"),
                _comment(
                    "## \U0001f3db Principal Architect Review\n\nLooks solid.",
                    comment_id="IC_pa",
                ),
            ],
        )]
        self.assertEqual(find_pending_approvals(issues), [])

    def test_already_dispatched_comment_id_is_deduplicated(self):
        """An issue whose latest comment ID was already dispatched is skipped."""
        issues = [_issue(
            67,
            labels=["bug-fixer:human:needs-approach-review"],
            comments=[
                _comment("[BUG-FIXER] Proposed approach for #67:"),
                _comment("Can we clarify the migration path?", comment_id="IC_comment_1"),
            ],
        )]
        result = find_pending_approvals(issues, seen_approvals={"67": "IC_comment_1"})
        self.assertEqual(result, [])

    def test_newer_human_comment_retriggers_detection(self):
        """A subsequent human reply with a new comment ID is dispatched again."""
        issues = [_issue(
            67,
            labels=["bug-fixer:human:needs-approach-review"],
            comments=[
                _comment("[BUG-FIXER] Proposed approach for #67:"),
                _comment("Can we clarify?", comment_id="IC_comment_1"),
                _comment("Got it — Approved", comment_id="IC_comment_2"),
            ],
        )]
        result = find_pending_approvals(issues, seen_approvals={"67": "IC_comment_1"})
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["number"], 67)

    def test_empty_or_missing_comment_id_does_not_false_match(self):
        """Empty/missing comment IDs never match stale entries and are not recorded."""
        with tempfile.TemporaryDirectory() as tmpdir:
            seen_file = Path(tmpdir) / "seen.json"
            issues = [
                _issue(
                    10,
                    labels=["bug-fixer:human:needs-approach-review"],
                    comments=[_comment("Approved", comment_id="")],
                ),
                _issue(
                    11,
                    labels=["bug-fixer:human:needs-approach-review"],
                    comments=[_comment("Approved")],
                ),
            ]
            # Even if seen_approvals somehow had "", neither should be skipped.
            result = find_pending_approvals(issues, seen_approvals={"10": "", "11": ""})
            self.assertEqual([i["number"] for i in result], [10, 11])

            seen = record_dispatched_approvals(issues, {}, seen_file=seen_file)
            self.assertEqual(seen, {})
            self.assertEqual(load_seen_approvals(seen_file), {})

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
                       _comment("Looks good, approved.", comment_id="IC_3"),
                   ]),
            # Already T1 — not pending
            _issue(4, labels=["bug-fixer:engineer:needs-fix"]),
        ]
        result = find_pending_approvals(issues)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["number"], 3)


class TestSeenApprovalsPersistence(unittest.TestCase):
    """Tests for load_seen_approvals, save_seen_approvals, and record_dispatched_approvals."""

    def test_round_trip_persistence_and_record(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            seen_file = Path(tmpdir) / "nested" / "approval_dispatched_comments.json"
            self.assertEqual(load_seen_approvals(seen_file), {})

            save_seen_approvals({"12": "IC_12"}, seen_file=seen_file)
            self.assertEqual(load_seen_approvals(seen_file), {"12": "IC_12"})

            issues = [
                _issue(
                    67,
                    labels=["bug-fixer:human:needs-approach-review"],
                    comments=[_comment("Approved", comment_id="IC_67")],
                ),
            ]
            seen = load_seen_approvals(seen_file)
            updated = record_dispatched_approvals(issues, seen, seen_file=seen_file)
            self.assertEqual(updated, {"12": "IC_12", "67": "IC_67"})
            self.assertEqual(load_seen_approvals(seen_file), {"12": "IC_12", "67": "IC_67"})

    def test_corrupt_or_non_dict_seen_file_returns_empty(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            seen_file = Path(tmpdir) / "approval_dispatched_comments.json"

            seen_file.write_text("{not valid json", encoding="utf-8")
            self.assertEqual(load_seen_approvals(seen_file), {})

            seen_file.write_text('["not", "a", "dict"]', encoding="utf-8")
            self.assertEqual(load_seen_approvals(seen_file), {})


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
                _comment("Approved", comment_id="IC_67"),
            ],
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
                       _comment("Approved", comment_id="IC_67"),
                   ]),
        ]
        untriaged = find_untriaged(issues)
        pending = find_pending_approvals(issues)
        self.assertEqual(len(untriaged), 1)
        self.assertEqual(len(pending), 1)
        should_spawn = bool(untriaged or pending)
        self.assertTrue(should_spawn)


class TestTriageDispatcherMainDedupIntegration(unittest.TestCase):
    """Integration tests verifying main() persists and honors seen comment IDs."""

    def _sample_issues(self):
        return [_issue(
            67,
            labels=["bug-fixer:human:needs-approach-review"],
            comments=[
                _comment("[BUG-FIXER] Proposed approach for #67:"),
                _comment("Needs one small clarification", comment_id="IC_clarify_1"),
            ],
        )]

    def test_main_persists_seen_comment_and_skips_second_tick(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            state_dir = Path(tmpdir) / "state"
            state_dir.mkdir(parents=True, exist_ok=True)
            cfg_file = Path(tmpdir) / "config.json"
            cfg_file.write_text('{"target_repo": "owner/repo"}', encoding="utf-8")
            seen_file = state_dir / "approval_dispatched_comments.json"

            issues_json = json.dumps(self._sample_issues())
            spawn_calls = []

            def fake_run(cmd):
                if "issue-list" in cmd:
                    return subprocess.CompletedProcess(cmd, 0, stdout=issues_json, stderr="")
                if "agentapi" in cmd:
                    spawn_calls.append(cmd)
                    payload = {"response": {"newConversation": {"conversationId": "conv-123"}}}
                    return subprocess.CompletedProcess(
                        cmd, 0, stdout=json.dumps(payload), stderr=""
                    )
                return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

            cooldown_file = state_dir / "triage_cooldown.json"
            failure_file = state_dir / "triage_failure_count"
            cfg = {"target_repo": "owner/repo", "triage_model": "flash"}

            with patch.dict(os.environ, {"ANTIGRAVITY_PROJECT_ID": "proj-1"}), \
                 patch.object(triage_dispatcher, "WORKSPACE_CONFIG", cfg_file), \
                 patch.object(triage_dispatcher, "CONFIG_FILE", cfg_file), \
                 patch.object(triage_dispatcher, "config", cfg), \
                 patch.object(triage_dispatcher, "TARGET_REPO", "owner/repo"), \
                 patch.object(triage_dispatcher, "STATE_DIR", state_dir), \
                 patch.object(triage_dispatcher, "TRIAGE_COOLDOWN_FILE", cooldown_file), \
                 patch.object(triage_dispatcher, "TRIAGE_FAILURE_FILE", failure_file), \
                 patch.object(triage_dispatcher, "DISPATCHES_DIR", state_dir / "dispatches"), \
                 patch.object(triage_dispatcher, "APPROVAL_SEEN_FILE", seen_file), \
                 patch.object(triage_dispatcher, "agentapi_is_reachable", return_value=True), \
                 patch.object(triage_dispatcher, "record_dispatch") as mock_record, \
                 patch.object(triage_dispatcher, "run", side_effect=fake_run):
                # First tick: spawns triage and records comment ID
                triage_dispatcher.main()
                self.assertEqual(len(spawn_calls), 1)
                mock_record.assert_called_once()
                self.assertEqual(load_seen_approvals(seen_file), {"67": "IC_clarify_1"})

                # Second tick with unchanged comment: exits cleanly without spawning
                with self.assertRaises(SystemExit) as ctx:
                    triage_dispatcher.main()
                self.assertEqual(ctx.exception.code, 0)
                self.assertEqual(len(spawn_calls), 1)

    def test_main_does_not_record_seen_when_spawn_fails(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            state_dir = Path(tmpdir) / "state"
            state_dir.mkdir(parents=True, exist_ok=True)
            cfg_file = Path(tmpdir) / "config.json"
            cfg_file.write_text('{"target_repo": "owner/repo"}', encoding="utf-8")
            seen_file = state_dir / "approval_dispatched_comments.json"

            issues_json = json.dumps(self._sample_issues())

            def fake_run(cmd):
                if "issue-list" in cmd:
                    return subprocess.CompletedProcess(cmd, 0, stdout=issues_json, stderr="")
                return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="connection refused")

            cooldown_file = state_dir / "triage_cooldown.json"
            failure_file = state_dir / "triage_failure_count"
            cfg = {"target_repo": "owner/repo", "triage_model": "flash"}

            with patch.dict(os.environ, {"ANTIGRAVITY_PROJECT_ID": "proj-1"}), \
                 patch.object(triage_dispatcher, "WORKSPACE_CONFIG", cfg_file), \
                 patch.object(triage_dispatcher, "CONFIG_FILE", cfg_file), \
                 patch.object(triage_dispatcher, "config", cfg), \
                 patch.object(triage_dispatcher, "TARGET_REPO", "owner/repo"), \
                 patch.object(triage_dispatcher, "STATE_DIR", state_dir), \
                 patch.object(triage_dispatcher, "TRIAGE_COOLDOWN_FILE", cooldown_file), \
                 patch.object(triage_dispatcher, "TRIAGE_FAILURE_FILE", failure_file), \
                 patch.object(triage_dispatcher, "DISPATCHES_DIR", state_dir / "dispatches"), \
                 patch.object(triage_dispatcher, "APPROVAL_SEEN_FILE", seen_file), \
                 patch.object(triage_dispatcher, "agentapi_is_reachable", return_value=True), \
                 patch.object(triage_dispatcher, "record_dispatch") as mock_record, \
                 patch.object(triage_dispatcher, "run", side_effect=fake_run):
                triage_dispatcher.main()
                mock_record.assert_not_called()
                self.assertFalse(seen_file.exists())


if __name__ == "__main__":
    unittest.main()
