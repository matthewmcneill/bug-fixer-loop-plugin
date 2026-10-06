import subprocess
import unittest
from unittest.mock import patch, MagicMock

import sys
from pathlib import Path
SCRIPT_DIR = Path(__file__).resolve().parent.parent / "daemon" / "workspace-plugin" / "bug-fixer" / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

from liveness import agentapi_is_reachable, check_orphaned_prs, _check_success
import json

class TestLiveness(unittest.TestCase):
    
    @patch('subprocess.run')
    def test_agentapi_is_reachable_success(self, mock_run):
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_run.return_value = mock_result
        
        self.assertTrue(agentapi_is_reachable())
        mock_run.assert_called_once_with(["agentapi", "--help"], capture_output=True, timeout=5)

    @patch('subprocess.run')
    def test_agentapi_is_reachable_failure_code(self, mock_run):
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_run.return_value = mock_result
        
        self.assertFalse(agentapi_is_reachable())

    @patch('subprocess.run')
    def test_agentapi_is_reachable_timeout(self, mock_run):
        mock_run.side_effect = subprocess.TimeoutExpired(cmd=["agentapi", "--help"], timeout=5)
        
        self.assertFalse(agentapi_is_reachable())

    @patch('subprocess.run')
    def test_agentapi_is_reachable_not_found(self, mock_run):
        mock_run.side_effect = FileNotFoundError("No such file or directory: 'agentapi'")
        
        self.assertFalse(agentapi_is_reachable())
        
    @patch('subprocess.run')
    def test_agentapi_is_reachable_permission_error(self, mock_run):
        mock_run.side_effect = PermissionError("Permission denied: 'agentapi'")
        
        self.assertFalse(agentapi_is_reachable())

    @patch("liveness._gh")
    def test_check_orphaned_prs_auto_heals_missing_system_pr_and_awaiting_review(self, mock_gh):
        mock_log = MagicMock()
        prs_output = json.dumps([
            {
                "number": 101,
                "title": "[BugFixer] Fix #42: Some bug",
                "labels": [],
            }
        ])
        mock_gh.side_effect = [
            subprocess.CompletedProcess(args=[], returncode=0, stdout=prs_output, stderr=""),
            subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr=""),  # add system:pr
            subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr=""),  # add awaiting-review
        ]

        check_orphaned_prs("owner/repo", mock_log)

        self.assertEqual(mock_gh.call_count, 3)
        self.assertEqual(mock_gh.call_args_list[0][0], ("pr", "list", "--repo", "owner/repo", "--state", "open", "--json", "number,title,labels"))
        self.assertEqual(mock_gh.call_args_list[1][0], ("pr", "edit", "101", "--repo", "owner/repo", "--add-label", "bug-fixer:system:pr"))
        self.assertEqual(mock_gh.call_args_list[2][0], ("pr", "edit", "101", "--repo", "owner/repo", "--add-label", "bug-fixer:architect:awaiting-review"))
        self.assertEqual(mock_log.call_count, 2)

    @patch("liveness._gh")
    def test_check_orphaned_prs_auto_heals_missing_awaiting_review(self, mock_gh):
        mock_log = MagicMock()
        prs_output = json.dumps([
            {
                "number": 102,
                "title": "[BugFixer] Fix #43: Another bug",
                "labels": [{"name": "bug-fixer:system:pr"}],
            }
        ])
        mock_gh.side_effect = [
            subprocess.CompletedProcess(args=[], returncode=0, stdout=prs_output, stderr=""),
            subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr=""),  # add awaiting-review
        ]

        check_orphaned_prs("owner/repo", mock_log)

        self.assertEqual(mock_gh.call_count, 2)
        self.assertEqual(mock_gh.call_args_list[1][0], ("pr", "edit", "102", "--repo", "owner/repo", "--add-label", "bug-fixer:architect:awaiting-review"))
        self.assertEqual(mock_log.call_count, 1)

    @patch("liveness._gh")
    def test_check_orphaned_prs_skips_when_status_present(self, mock_gh):
        mock_log = MagicMock()
        prs_output = json.dumps([
            {
                "number": 103,
                "title": "[BugFixer] Fix #44: Bug",
                "labels": [{"name": "bug-fixer:system:pr"}, {"name": "bug-fixer:architect:awaiting-review"}],
            },
            {
                "number": 104,
                "title": "[BugFixer] Fix #45: Bug",
                "labels": [{"name": "bug-fixer:system:pr"}, {"name": "bug-fixer:engineer:needs-iteration"}],
            },
            {
                "number": 105,
                "title": "[BugFixer] Fix #46: Bug",
                "labels": [{"name": "bug-fixer:system:pr"}, {"name": "bug-fixer:architect:approved"}],
            },
            {
                "number": 106,
                "title": "[BugFixer] Fix #47: Bug",
                "labels": [{"name": "bug-fixer:system:pr"}, {"name": "bug-fixer:human:needs-intervention"}],
            },
        ])
        mock_gh.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout=prs_output, stderr="")

        check_orphaned_prs("owner/repo", mock_log)

        self.assertEqual(mock_gh.call_count, 1)
        mock_log.assert_not_called()

    @patch("liveness._gh")
    def test_check_orphaned_prs_skips_non_bug_fixer_prs(self, mock_gh):
        mock_log = MagicMock()
        prs_output = json.dumps([
            {
                "number": 107,
                "title": "Feature: add new widget",
                "labels": [{"name": "enhancement"}],
            }
        ])
        mock_gh.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout=prs_output, stderr="")

        check_orphaned_prs("owner/repo", mock_log)

        self.assertEqual(mock_gh.call_count, 1)
        mock_log.assert_not_called()

    @patch("liveness.read_last_transcript_step")
    def test_check_success_trajectory_and_triage(self, mock_read_last):
        from liveness import _check_success
        
        # Test missing conv_id
        dispatch = {"type": "trajectory"}
        self.assertFalse(_check_success(dispatch, "owner/repo"))
        
        # Test no transcript step
        dispatch = {"type": "triage", "conversation_id": "123"}
        mock_read_last.return_value = None
        self.assertFalse(_check_success(dispatch, "owner/repo"))
        
        # Test active step (has tool_calls)
        mock_read_last.return_value = {"type": "PLANNER_RESPONSE", "tool_calls": [{"name": "foo"}]}
        self.assertFalse(_check_success(dispatch, "owner/repo"))
        
        # Test idle step (no tool_calls)
        mock_read_last.return_value = {"type": "PLANNER_RESPONSE"}
        self.assertTrue(_check_success(dispatch, "owner/repo"))

        # Test another step type
        mock_read_last.return_value = {"type": "USER_INPUT"}
        self.assertFalse(_check_success(dispatch, "owner/repo"))

    @patch("liveness._gh")
    def test_check_success_ignores_closed_issue(self, mock_gh):
        mock_gh.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="false\n", stderr=""
        )
        dispatch = {"type": "fresh_fix", "issue_number": 48}
        self.assertFalse(_check_success(dispatch, "owner/repo"))
        mock_gh.assert_called_with("issue", "view", "48", "--repo", "owner/repo", "--json", "state,labels", "--jq", '.state == "OPEN" and ([.labels[].name] | any(. == "bug-fixer:architect:awaiting-review"))')

if __name__ == '__main__':
    unittest.main()
