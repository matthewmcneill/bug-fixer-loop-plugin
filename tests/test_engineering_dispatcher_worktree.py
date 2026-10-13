"""Unit tests for engineering_dispatcher worktree removal and cleanup fallback."""

import shutil
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

from engineering_dispatcher import remove_worktree


class TestRemoveWorktree(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp(prefix="test_wt_")
        self.repo_dir = Path(self.tmp_dir) / "repo"
        self.worktrees_dir = self.repo_dir / "worktrees"
        self.worktrees_dir.mkdir(parents=True, exist_ok=True)
        self.wt_path = self.worktrees_dir / "issue-75"
        self.wt_path.mkdir(parents=True, exist_ok=True)
        # Create residual file inside worktree directory (e.g., .pio-mcp-workspace)
        (self.wt_path / ".pio-mcp-workspace").mkdir(parents=True, exist_ok=True)
        (self.wt_path / ".pio-mcp-workspace" / "metadata.json").write_text("{}")

        self.repo_patcher = patch("engineering_dispatcher.REPO_DIR", self.repo_dir)
        self.wt_dir_patcher = patch("engineering_dispatcher.WORKTREES_DIR", self.worktrees_dir)
        self.repo_patcher.start()
        self.wt_dir_patcher.start()

    def tearDown(self):
        self.repo_patcher.stop()
        self.wt_dir_patcher.stop()
        if Path(self.tmp_dir).exists():
            shutil.rmtree(self.tmp_dir, ignore_errors=True)

    @patch("engineering_dispatcher.log")
    @patch("engineering_dispatcher.run")
    def test_remove_worktree_normal_git_success(self, mock_run, mock_log):
        """When git worktree remove succeeds and deletes directory, fallback should not trigger."""
        def fake_git_remove(cmd, check=False):
            # Simulate git successfully deleting the worktree directory
            shutil.rmtree(self.wt_path, ignore_errors=True)
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

        mock_run.side_effect = fake_git_remove

        self.assertTrue(self.wt_path.exists())
        remove_worktree(self.wt_path)

        self.assertFalse(self.wt_path.exists())
        mock_run.assert_called_once()
        # Verify fallback log was not called
        for call_arg in mock_log.call_args_list:
            self.assertNotIn("falling back", call_arg[0][0])

    @patch("engineering_dispatcher.log")
    @patch("engineering_dispatcher.run")
    def test_remove_worktree_fallback_when_git_fails_and_dir_remains(self, mock_run, mock_log):
        """When git worktree remove fails or metadata is pruned, fallback to shutil.rmtree removes directory."""
        # Git worktree remove fails (swallowed by check=False) and leaves directory on disk
        mock_run.return_value = subprocess.CompletedProcess(
            args=["git", "worktree", "remove"],
            returncode=128,
            stdout="",
            stderr="fatal: 'issue-75' is not a working tree",
        )

        self.assertTrue(self.wt_path.exists())
        remove_worktree(self.wt_path)

        # Directory should now be removed by shutil.rmtree
        self.assertFalse(self.wt_path.exists())
        mock_run.assert_called_once()

        # Fallback log message should have been recorded specifically for shutil.rmtree
        fallback_logged = any(
            f"git worktree remove failed for {self.wt_path}, falling back to shutil.rmtree" in call_arg[0][0]
            for call_arg in mock_log.call_args_list
        )
        self.assertTrue(fallback_logged, f"Expected fallback log message, got: {mock_log.call_args_list}")

    @patch("engineering_dispatcher.log")
    @patch("engineering_dispatcher.run")
    def test_remove_worktree_nonexistent_path(self, mock_run, mock_log):
        """When worktree path does not exist, neither git command nor fallback should run."""
        nonexistent = self.worktrees_dir / "does-not-exist"
        remove_worktree(nonexistent)

        mock_run.assert_not_called()
        mock_log.assert_not_called()

    @patch("engineering_dispatcher.log")
    @patch("engineering_dispatcher.run")
    def test_remove_worktree_file_fallback(self, mock_run, mock_log):
        """When path is a stray file rather than directory, it is unlinked with accurate log."""
        file_path = self.worktrees_dir / "stray-file"
        file_path.write_text("dummy")

        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=128, stdout="", stderr=""
        )

        self.assertTrue(file_path.exists())
        remove_worktree(file_path)

        self.assertFalse(file_path.exists())
        mock_run.assert_called_once()
        fallback_logged = any(
            f"git worktree remove failed for {file_path}, falling back to filesystem removal" in call_arg[0][0]
            for call_arg in mock_log.call_args_list
        )
        self.assertTrue(fallback_logged, f"Expected filesystem removal log, got: {mock_log.call_args_list}")

    @patch("engineering_dispatcher.log")
    @patch("engineering_dispatcher.run")
    def test_remove_worktree_refuses_repo_root(self, mock_run, mock_log):
        """Containment guard refuses to remove the repository root."""
        remove_worktree(self.repo_dir)

        self.assertTrue(self.repo_dir.exists())
        mock_run.assert_not_called()
        self.assertTrue(
            any("Refusing to remove repository root" in call[0][0] for call in mock_log.call_args_list),
            f"Expected repo root refusal log, got: {mock_log.call_args_list}",
        )

    @patch("engineering_dispatcher.log")
    @patch("engineering_dispatcher.run")
    def test_remove_worktree_refuses_worktrees_dir(self, mock_run, mock_log):
        """Containment guard refuses to remove the worktrees container directory."""
        remove_worktree(self.worktrees_dir)

        self.assertTrue(self.worktrees_dir.exists())
        mock_run.assert_not_called()
        self.assertTrue(
            any("Refusing to remove worktrees directory" in call[0][0] for call in mock_log.call_args_list),
            f"Expected worktrees dir refusal log, got: {mock_log.call_args_list}",
        )

    @patch("engineering_dispatcher.log")
    @patch("engineering_dispatcher.run")
    def test_remove_worktree_refuses_path_outside_worktrees_dir(self, mock_run, mock_log):
        """Containment guard refuses to remove paths outside WORKTREES_DIR."""
        outside_dir = Path(self.tmp_dir) / "outside"
        outside_dir.mkdir(parents=True)

        remove_worktree(outside_dir)

        self.assertTrue(outside_dir.exists())
        mock_run.assert_not_called()
        self.assertTrue(
            any("Refusing to remove path outside WORKTREES_DIR" in call[0][0] for call in mock_log.call_args_list),
            f"Expected outside WORKTREES_DIR refusal log, got: {mock_log.call_args_list}",
        )


if __name__ == "__main__":
    unittest.main()
