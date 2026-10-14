"""Unit tests for engineering_dispatcher worktree removal delegation."""

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

from engineering_dispatcher import remove_worktree, log


class TestEngineeringDispatcherRemoveWorktree(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp(prefix="test_eng_wt_")
        self.repo_dir = Path(self.tmp_dir) / "repo"
        self.worktrees_dir = self.repo_dir / "worktrees"
        self.worktrees_dir.mkdir(parents=True, exist_ok=True)
        self.wt_path = self.worktrees_dir / "issue-75"
        self.wt_path.mkdir(parents=True, exist_ok=True)

        self.repo_patcher = patch("engineering_dispatcher.REPO_DIR", self.repo_dir)
        self.wt_dir_patcher = patch("engineering_dispatcher.WORKTREES_DIR", self.worktrees_dir)
        self.repo_patcher.start()
        self.wt_dir_patcher.start()

    def tearDown(self):
        self.repo_patcher.stop()
        self.wt_dir_patcher.stop()
        if Path(self.tmp_dir).exists():
            shutil.rmtree(self.tmp_dir, ignore_errors=True)

    @patch("engineering_dispatcher._remove_worktree")
    def test_remove_worktree_delegates_to_liveness(self, mock_remove_wt):
        """engineering_dispatcher.remove_worktree delegates to liveness.remove_worktree with context."""
        remove_worktree(self.wt_path)
        mock_remove_wt.assert_called_once_with(
            self.wt_path, repo_dir=self.repo_dir, worktrees_dir=self.worktrees_dir, log_fn=log
        )

    @patch("liveness._run")
    def test_remove_worktree_end_to_end_success(self, mock_run):
        """End-to-end delegation through liveness removes the worktree."""
        def fake_git_remove(cmd):
            shutil.rmtree(self.wt_path, ignore_errors=True)
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

        mock_run.side_effect = fake_git_remove

        self.assertTrue(self.wt_path.exists())
        remove_worktree(self.wt_path)
        self.assertFalse(self.wt_path.exists())


if __name__ == "__main__":
    unittest.main()

