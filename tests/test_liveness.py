import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
import sys
from unittest.mock import patch, MagicMock

SCRIPT_DIR = Path(__file__).resolve().parent.parent / "daemon" / "workspace-plugin" / "bug-fixer" / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

from liveness import (
    agentapi_is_reachable,
    check_orphaned_prs,
    _check_success,
    remove_worktree,
    sweep_dispatches,
    _recover,
)

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


class TestLivenessRemoveWorktree(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp(prefix="test_liveness_wt_")
        self.repo_dir = Path(self.tmp_dir) / "repo"
        self.worktrees_dir = self.repo_dir / "worktrees"
        self.worktrees_dir.mkdir(parents=True, exist_ok=True)
        self.wt_path = self.worktrees_dir / "issue-77"
        self.wt_path.mkdir(parents=True, exist_ok=True)
        # Create residual file inside worktree directory (e.g., .pio-mcp-workspace)
        (self.wt_path / ".pio-mcp-workspace").mkdir(parents=True, exist_ok=True)
        (self.wt_path / ".pio-mcp-workspace" / "metadata.json").write_text("{}")

    def tearDown(self):
        if Path(self.tmp_dir).exists():
            shutil.rmtree(self.tmp_dir, ignore_errors=True)

    @patch("liveness._run")
    def test_remove_worktree_normal_git_success(self, mock_run):
        """When git worktree remove succeeds and deletes directory, fallback should not trigger."""
        mock_log = MagicMock()

        def fake_git_remove(cmd):
            shutil.rmtree(self.wt_path, ignore_errors=True)
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

        mock_run.side_effect = fake_git_remove

        self.assertTrue(self.wt_path.exists())
        remove_worktree(self.wt_path, log_fn=mock_log)

        self.assertFalse(self.wt_path.exists())
        mock_run.assert_called_once()
        for call_arg in mock_log.call_args_list:
            self.assertNotIn("falling back", call_arg[0][0])

    @patch("liveness._run")
    def test_remove_worktree_fallback_when_git_fails_and_dir_remains(self, mock_run):
        """When git worktree remove fails or metadata is pruned, fallback to shutil.rmtree removes directory and prunes."""
        mock_log = MagicMock()
        mock_run.return_value = subprocess.CompletedProcess(
            args=["git", "worktree", "remove"],
            returncode=128,
            stdout="",
            stderr="fatal: 'issue-77' is not a working tree",
        )

        self.assertTrue(self.wt_path.exists())
        remove_worktree(self.wt_path, log_fn=mock_log)

        self.assertFalse(self.wt_path.exists())
        self.assertEqual(mock_run.call_count, 2)
        mock_run.assert_any_call(["git", "-C", str(self.repo_dir.resolve()), "worktree", "prune"])
        fallback_logged = any(
            f"git worktree remove failed for {self.wt_path}, falling back to shutil.rmtree" in call_arg[0][0]
            for call_arg in mock_log.call_args_list
        )
        self.assertTrue(fallback_logged, f"Expected fallback log message, got: {mock_log.call_args_list}")

    @patch("liveness._run")
    def test_remove_worktree_nonexistent_path(self, mock_run):
        """When worktree path does not exist, neither git command nor fallback should run."""
        mock_log = MagicMock()
        nonexistent = self.worktrees_dir / "does-not-exist"
        remove_worktree(nonexistent, log_fn=mock_log)

        mock_run.assert_not_called()
        mock_log.assert_not_called()

    @patch("liveness._run")
    def test_remove_worktree_broken_symlink_fallback(self, mock_run):
        """When path is a broken symlink, it is not skipped by existence check and is unlinked with prune."""
        mock_log = MagicMock()
        broken_link = self.worktrees_dir / "broken-symlink"
        os.symlink("/nonexistent/target/path", broken_link)

        self.assertTrue(broken_link.is_symlink())
        self.assertFalse(broken_link.exists())

        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=128, stdout="", stderr="fatal: not a working tree"
        )

        remove_worktree(broken_link, log_fn=mock_log)

        self.assertFalse(broken_link.is_symlink())
        self.assertEqual(mock_run.call_count, 2)
        mock_run.assert_any_call(["git", "-C", str(self.repo_dir.resolve()), "worktree", "prune"])
        fallback_logged = any(
            f"git worktree remove failed for {broken_link}, falling back to filesystem removal" in call_arg[0][0]
            for call_arg in mock_log.call_args_list
        )
        self.assertTrue(fallback_logged, f"Expected filesystem removal log, got: {mock_log.call_args_list}")

    @patch("liveness._run")
    def test_remove_worktree_file_fallback(self, mock_run):
        """When path is a stray file rather than directory, it is unlinked with accurate log and pruned."""
        mock_log = MagicMock()
        file_path = self.worktrees_dir / "stray-file"
        file_path.write_text("dummy")

        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=128, stdout="", stderr=""
        )

        self.assertTrue(file_path.exists())
        remove_worktree(file_path, log_fn=mock_log)

        self.assertFalse(file_path.exists())
        self.assertEqual(mock_run.call_count, 2)
        mock_run.assert_any_call(["git", "-C", str(self.repo_dir.resolve()), "worktree", "prune"])
        fallback_logged = any(
            f"git worktree remove failed for {file_path}, falling back to filesystem removal" in call_arg[0][0]
            for call_arg in mock_log.call_args_list
        )
        self.assertTrue(fallback_logged, f"Expected filesystem removal log, got: {mock_log.call_args_list}")

    @patch("liveness._run")
    def test_remove_worktree_refuses_repo_root(self, mock_run):
        """Containment guard refuses to remove the repository root."""
        mock_log = MagicMock()
        remove_worktree(self.repo_dir, repo_dir=self.repo_dir, log_fn=mock_log)

        self.assertTrue(self.repo_dir.exists())
        mock_run.assert_not_called()
        self.assertTrue(
            any("Refusing to remove repository root" in call[0][0] for call in mock_log.call_args_list),
            f"Expected repo root refusal log, got: {mock_log.call_args_list}",
        )

    @patch("liveness._run")
    def test_remove_worktree_refuses_worktrees_dir(self, mock_run):
        """Containment guard refuses to remove the worktrees container directory."""
        mock_log = MagicMock()
        remove_worktree(self.worktrees_dir, repo_dir=self.repo_dir, log_fn=mock_log)

        self.assertTrue(self.worktrees_dir.exists())
        mock_run.assert_not_called()
        self.assertTrue(
            any("Refusing to remove worktrees directory" in call[0][0] for call in mock_log.call_args_list),
            f"Expected worktrees dir refusal log, got: {mock_log.call_args_list}",
        )

    @patch("liveness._run")
    def test_remove_worktree_refuses_path_outside_worktrees_dir(self, mock_run):
        """Containment guard refuses to remove paths outside WORKTREES_DIR."""
        mock_log = MagicMock()
        outside_dir = Path(self.tmp_dir) / "outside"
        outside_dir.mkdir(parents=True)

        remove_worktree(outside_dir, repo_dir=self.repo_dir, log_fn=mock_log)

        self.assertTrue(outside_dir.exists())
        mock_run.assert_not_called()
        self.assertTrue(
            any("Refusing to remove path outside WORKTREES_DIR" in call[0][0] for call in mock_log.call_args_list),
            f"Expected outside WORKTREES_DIR refusal log, got: {mock_log.call_args_list}",
        )

    @patch("liveness._run")
    def test_remove_worktree_infers_dirs_from_worktrees_structure(self, mock_run):
        """When repo_dir and worktrees_dir are omitted, they are inferred from path structure."""
        mock_log = MagicMock()
        def fake_git_remove(cmd):
            shutil.rmtree(self.wt_path, ignore_errors=True)
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

        mock_run.side_effect = fake_git_remove
        remove_worktree(self.wt_path, log_fn=mock_log)
        mock_run.assert_called_once()
        self.assertEqual(mock_run.call_args[0][0][2], str(self.repo_dir.resolve()))
        self.assertEqual(mock_run.call_args[0][0][6], str(self.wt_path))

    @patch("liveness._run")
    def test_remove_worktree_handles_relative_and_redundant_paths(self, mock_run):
        """Redundant path segments like '..' or '.' are resolved before directory inference."""
        mock_log = MagicMock()
        def fake_git_remove(cmd):
            shutil.rmtree(self.wt_path, ignore_errors=True)
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

        mock_run.side_effect = fake_git_remove
        relative_path = self.repo_dir / "worktrees" / ".." / "worktrees" / "issue-77"
        remove_worktree(relative_path, log_fn=mock_log)
        mock_run.assert_called_once()
        self.assertEqual(mock_run.call_args[0][0][2], str(self.repo_dir.resolve()))
        self.assertEqual(mock_run.call_args[0][0][6], str(relative_path))


    @patch("liveness.remove_worktree")
    @patch("liveness._append_to_ledger")
    @patch("liveness._check_success")
    def test_sweep_dispatches_calls_remove_worktree_on_success(
        self, mock_check_success, mock_ledger, mock_remove_wt
    ):
        """sweep_dispatches invokes remove_worktree when dispatch succeeds."""
        mock_log = MagicMock()
        dispatches_dir = Path(self.tmp_dir) / "dispatches"
        dispatches_dir.mkdir(parents=True, exist_ok=True)
        dispatch_file = dispatches_dir / "conv123.json"
        dispatch_data = {
            "conversation_id": "conv123",
            "type": "fresh_fix",
            "issue_number": 77,
            "worktree_path": str(self.wt_path),
        }
        dispatch_file.write_text(json.dumps(dispatch_data))

        mock_check_success.return_value = True

        sweep_dispatches(dispatches_dir, "owner/repo", mock_log)

        mock_remove_wt.assert_called_once_with(str(self.wt_path), log_fn=mock_log)
        self.assertFalse(dispatch_file.exists())

    @patch("liveness.remove_worktree")
    @patch("liveness._record_issue_dispatch_failure")
    @patch("liveness._gh")
    @patch("liveness.post_state_dump")
    @patch("liveness.extract_context_from_transcript")
    @patch("liveness.classify_blocker")
    @patch("liveness.is_in_error_loop")
    def test_recover_calls_remove_worktree_for_fresh_fix(
        self, mock_error_loop, mock_classify, mock_extract, mock_state_dump,
        mock_gh, mock_record_fail, mock_remove_wt
    ):
        """_recover invokes remove_worktree for fresh_fix recovery."""
        mock_log = MagicMock()
        mock_error_loop.return_value = False
        mock_classify.return_value = "timeout"
        mock_extract.return_value = {}

        dispatches_dir = Path(self.tmp_dir) / "dispatches"
        dispatches_dir.mkdir(parents=True, exist_ok=True)
        dispatch_file = dispatches_dir / "conv456.json"
        dispatch_data = {
            "conversation_id": "conv456",
            "type": "fresh_fix",
            "issue_number": 77,
            "worktree_path": str(self.wt_path),
            "resume_attempts": 2,  # MAX_RESUME_ATTEMPTS reached
        }
        dispatch_file.write_text(json.dumps(dispatch_data))

        _recover(dispatch_data, dispatches_dir, "owner/repo", None, mock_log)

        mock_remove_wt.assert_called_once_with(str(self.wt_path), log_fn=mock_log)


if __name__ == '__main__':
    unittest.main()

