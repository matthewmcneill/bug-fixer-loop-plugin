"""Unit tests for architect_dispatcher merge conflict detection."""

import json
import subprocess
import unittest
from unittest.mock import MagicMock, patch

from scripts.architect_dispatcher import check_merge_conflicts


class TestArchitectDispatcherConflictMonitor(unittest.TestCase):
    def setUp(self):
        self.target_repo = "matthewmcneill/bug-fixer-loop-plugin"

    @patch("scripts.architect_dispatcher.safe_post")
    @patch("scripts.architect_dispatcher.gh")
    def test_conflicting_pr_handling(self, mock_gh, mock_safe_post):
        """When an approved PR has CONFLICTING status, it should be re-routed to engineering."""
        # 1. gh pr list returns a conflicting PR
        list_output = json.dumps([
            {
                "number": 42,
                "title": "Fix memory leak in buffer",
                "mergeable": "CONFLICTING",
            }
        ])
        mock_gh.side_effect = [
            subprocess.CompletedProcess(args=[], returncode=0, stdout=list_output, stderr=""),  # pr list
            subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr=""),           # pr edit
            subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr=""),           # pr ready --undo
        ]
        mock_safe_post.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")

        check_merge_conflicts(self.target_repo)

        # Verify calls
        self.assertEqual(mock_gh.call_count, 3)
        self.assertEqual(mock_safe_post.call_count, 1)

        # Call 1: list approved PRs (via gh)
        call_1_args = mock_gh.call_args_list[0][0]
        self.assertEqual(call_1_args, (
            "pr", "list",
            "--repo", self.target_repo,
            "--label", "bug-fixer:system:pr",
            "--label", "bug-fixer:architect:approved",
            "--state", "open",
            "--json", "number,title,mergeable",
        ))

        # Call 2: post [ARCHITECT] comment (via safe_post)
        sp_args = mock_safe_post.call_args_list[0][0]
        self.assertEqual(sp_args[0:4], ("pr-comment", "42", "--repo", self.target_repo))
        self.assertEqual(sp_args[4], "--body")
        self.assertIn("[ARCHITECT] Merge conflict detected:", sp_args[5])

        # Call 3: update labels (via gh)
        call_2_args = mock_gh.call_args_list[1][0]
        self.assertEqual(call_2_args, (
            "pr", "edit", "42",
            "--repo", self.target_repo,
            "--remove-label", "bug-fixer:architect:approved",
            "--add-label", "bug-fixer:engineer:needs-iteration",
        ))

        # Call 4: revert to draft via gh pr ready --undo
        call_3_args = mock_gh.call_args_list[2][0]
        self.assertEqual(call_3_args, (
            "pr", "ready", "42",
            "--undo",
            "--repo", self.target_repo,
        ))

    @patch("scripts.architect_dispatcher.gh")
    def test_mergeable_pr_no_action(self, mock_gh):
        """When an approved PR is MERGEABLE, no mutation commands should be executed."""
        list_output = json.dumps([
            {
                "number": 43,
                "title": "Clean PR",
                "mergeable": "MERGEABLE",
            }
        ])
        mock_gh.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=list_output, stderr=""
        )

        check_merge_conflicts(self.target_repo)

        # Only the list query should have run
        self.assertEqual(mock_gh.call_count, 1)

    @patch("scripts.architect_dispatcher.gh")
    def test_unknown_pr_no_action(self, mock_gh):
        """When an approved PR has UNKNOWN mergeable status, retry next tick without mutations."""
        list_output = json.dumps([
            {
                "number": 44,
                "title": "Pending status calculation PR",
                "mergeable": "UNKNOWN",
            }
        ])
        mock_gh.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=list_output, stderr=""
        )

        check_merge_conflicts(self.target_repo)

        self.assertEqual(mock_gh.call_count, 1)

    @patch("scripts.architect_dispatcher.gh")
    def test_empty_approved_prs_list(self, mock_gh):
        """When no approved PRs exist, check exits cleanly without mutations."""
        mock_gh.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="[]", stderr=""
        )

        check_merge_conflicts(self.target_repo)

        self.assertEqual(mock_gh.call_count, 1)

    @patch("scripts.architect_dispatcher.safe_post")
    @patch("scripts.architect_dispatcher.gh")
    def test_mixed_prs_handling(self, mock_gh, mock_safe_post):
        """When multiple approved PRs exist with mixed statuses, only CONFLICTING is modified."""
        list_output = json.dumps([
            {"number": 101, "title": "Mergeable PR", "mergeable": "MERGEABLE"},
            {"number": 102, "title": "Conflicting PR", "mergeable": "CONFLICTING"},
            {"number": 103, "title": "Unknown PR", "mergeable": "UNKNOWN"},
        ])
        mock_gh.side_effect = [
            subprocess.CompletedProcess(args=[], returncode=0, stdout=list_output, stderr=""),
            subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr=""),  # edit
            subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr=""),  # undo ready
        ]
        mock_safe_post.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")

        check_merge_conflicts(self.target_repo)

        self.assertEqual(mock_gh.call_count, 3)
        self.assertEqual(mock_safe_post.call_count, 1)
        # Check that mutations were only for PR #102
        self.assertEqual(mock_safe_post.call_args_list[0][0][1], "102")  # safe_post comment
        self.assertEqual(mock_gh.call_args_list[1][0][2], "102")  # gh edit
        self.assertEqual(mock_gh.call_args_list[2][0][2], "102")  # gh ready --undo

    @patch("scripts.architect_dispatcher.gh")
    def test_query_failure_handled_gracefully(self, mock_gh):
        """If gh pr list fails with a non-zero exit code, error is handled without crashing."""
        mock_gh.return_value = subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr="API rate limit exceeded"
        )

        # Should not raise exception
        check_merge_conflicts(self.target_repo)
        self.assertEqual(mock_gh.call_count, 1)

    @patch("scripts.architect_dispatcher.gh")
    def test_invalid_json_handled_gracefully(self, mock_gh):
        """If gh pr list returns invalid JSON, error is handled without crashing."""
        mock_gh.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="not valid json", stderr=""
        )

        check_merge_conflicts(self.target_repo)
        self.assertEqual(mock_gh.call_count, 1)


if __name__ == "__main__":
    unittest.main()
