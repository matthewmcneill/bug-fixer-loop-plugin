"""Unit tests for the Consumer Hook API (bugfixer_api.py)."""

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.bugfixer_api import BugFixer, SessionContext, _find_dispatch, _load_config


class TestSessionContextDefaults(unittest.TestCase):
    """SessionContext has safe defaults for all fields."""

    def test_default_is_not_autonomous(self):
        ctx = SessionContext()
        self.assertFalse(ctx.is_autonomous)

    def test_default_fields_are_none_or_empty(self):
        ctx = SessionContext()
        self.assertIsNone(ctx.session_type)
        self.assertIsNone(ctx.issue_number)
        self.assertIsNone(ctx.pr_number)
        self.assertIsNone(ctx.worktree_path)
        self.assertIsNone(ctx.target_repo)
        self.assertEqual(ctx.config, {})

    def test_frozen(self):
        ctx = SessionContext()
        with self.assertRaises(AttributeError):
            ctx.is_autonomous = True


class TestFindDispatch(unittest.TestCase):
    """Low-level dispatch file lookup."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.workspace = Path(self.tmp) / "bug-fixer-loop-workspace"
        self.workspace.mkdir()
        self.dispatches = self.workspace / "dispatches"
        self.dispatches.mkdir()

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def _write_dispatch(self, conv_id: str, dispatch_type: str, **kwargs):
        data = {
            "conversation_id": conv_id,
            "type": dispatch_type,
            **kwargs,
        }
        (self.dispatches / f"{conv_id}.json").write_text(json.dumps(data))

    def test_direct_filename_lookup(self):
        self._write_dispatch("abc-123", "fresh_fix", issue_number=42)
        result = _find_dispatch(self.workspace, "abc-123")
        self.assertIsNotNone(result)
        self.assertEqual(result["conversation_id"], "abc-123")
        self.assertEqual(result["type"], "fresh_fix")
        self.assertEqual(result["issue_number"], 42)

    def test_not_found_returns_none(self):
        result = _find_dispatch(self.workspace, "nonexistent-id")
        self.assertIsNone(result)

    def test_empty_conversation_id_returns_none(self):
        self._write_dispatch("abc-123", "fresh_fix")
        result = _find_dispatch(self.workspace, "")
        self.assertIsNone(result)

    def test_no_dispatches_dir_returns_none(self):
        shutil.rmtree(self.dispatches)
        result = _find_dispatch(self.workspace, "abc-123")
        self.assertIsNone(result)

    def test_corrupt_dispatch_file_skipped(self):
        (self.dispatches / "bad-id.json").write_text("not valid json {{")
        self._write_dispatch("good-id", "triage")
        # Bad file doesn't crash, good file is still found
        self.assertIsNone(_find_dispatch(self.workspace, "bad-id"))
        self.assertIsNotNone(_find_dispatch(self.workspace, "good-id"))

    def test_fallback_scan_finds_misnamed_file(self):
        """If the file isn't named {conv_id}.json, the scan path still works."""
        data = {"conversation_id": "real-id-456", "type": "architect_review", "pr_number": 7}
        (self.dispatches / "some-other-name.json").write_text(json.dumps(data))
        result = _find_dispatch(self.workspace, "real-id-456")
        self.assertIsNotNone(result)
        self.assertEqual(result["type"], "architect_review")


class TestLoadConfig(unittest.TestCase):
    """Config loading from workspace directory."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.workspace = Path(self.tmp) / "bug-fixer-loop-workspace"
        self.workspace.mkdir()

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def test_loads_valid_config(self):
        (self.workspace / "config.json").write_text(json.dumps({
            "target_repo": "owner/repo",
            "protected_files": ["docs/spec.md"],
        }))
        config = _load_config(self.workspace)
        self.assertEqual(config["target_repo"], "owner/repo")

    def test_missing_config_returns_empty(self):
        config = _load_config(self.workspace)
        self.assertEqual(config, {})

    def test_corrupt_config_returns_empty(self):
        (self.workspace / "config.json").write_text("not json {{")
        config = _load_config(self.workspace)
        self.assertEqual(config, {})


class TestBugFixerGetSessionContext(unittest.TestCase):
    """BugFixer.get_session_context() integration tests."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.workspace = Path(self.tmp) / "bug-fixer-loop-workspace"
        self.workspace.mkdir()
        self.dispatches = self.workspace / "dispatches"
        self.dispatches.mkdir()
        (self.workspace / "config.json").write_text(json.dumps({
            "target_repo": "owner/test-repo",
            "protected_files": ["docs/spec.md"],
            "test_command": "pytest",
        }))

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def _write_dispatch(self, conv_id: str, dispatch_type: str, **kwargs):
        data = {
            "conversation_id": conv_id,
            "type": dispatch_type,
            **kwargs,
        }
        (self.dispatches / f"{conv_id}.json").write_text(json.dumps(data))

    def _patch_workspace(self):
        return patch("scripts.bugfixer_api._find_workspace_dir",
                     return_value=self.workspace)

    def test_empty_conversation_id(self):
        with self._patch_workspace():
            ctx = BugFixer.get_session_context("")
        self.assertFalse(ctx.is_autonomous)

    def test_unknown_conversation_returns_not_autonomous(self):
        with self._patch_workspace():
            ctx = BugFixer.get_session_context("unknown-conv-id")
        self.assertFalse(ctx.is_autonomous)
        self.assertEqual(ctx.target_repo, "owner/test-repo")
        self.assertEqual(ctx.config["test_command"], "pytest")

    def test_matching_dispatch_returns_autonomous(self):
        self._write_dispatch("abc-123", "fresh_fix", issue_number=42,
                             worktree_path="/tmp/worktrees/issue-42")
        with self._patch_workspace():
            ctx = BugFixer.get_session_context("abc-123")
        self.assertTrue(ctx.is_autonomous)
        self.assertEqual(ctx.session_type, "fresh_fix")
        self.assertEqual(ctx.issue_number, 42)
        self.assertIsNone(ctx.pr_number)
        self.assertEqual(ctx.worktree_path, "/tmp/worktrees/issue-42")
        self.assertEqual(ctx.target_repo, "owner/test-repo")

    def test_pr_iteration_session(self):
        self._write_dispatch("pr-iter-1", "pr_iteration", pr_number=15,
                             worktree_path="/tmp/worktrees/pr-15")
        with self._patch_workspace():
            ctx = BugFixer.get_session_context("pr-iter-1")
        self.assertTrue(ctx.is_autonomous)
        self.assertEqual(ctx.session_type, "pr_iteration")
        self.assertIsNone(ctx.issue_number)
        self.assertEqual(ctx.pr_number, 15)

    def test_architect_review_session(self):
        self._write_dispatch("arch-1", "architect_review", pr_number=20)
        with self._patch_workspace():
            ctx = BugFixer.get_session_context("arch-1")
        self.assertTrue(ctx.is_autonomous)
        self.assertEqual(ctx.session_type, "architect_review")
        self.assertEqual(ctx.pr_number, 20)

    def test_triage_session(self):
        self._write_dispatch("triage-1", "triage")
        with self._patch_workspace():
            ctx = BugFixer.get_session_context("triage-1")
        self.assertTrue(ctx.is_autonomous)
        self.assertEqual(ctx.session_type, "triage")

    def test_trajectory_session(self):
        self._write_dispatch("traj-1", "trajectory")
        with self._patch_workspace():
            ctx = BugFixer.get_session_context("traj-1")
        self.assertTrue(ctx.is_autonomous)
        self.assertEqual(ctx.session_type, "trajectory")

    def test_reattach_session(self):
        self._write_dispatch("reattach-1", "reattach", issue_number=99)
        with self._patch_workspace():
            ctx = BugFixer.get_session_context("reattach-1")
        self.assertTrue(ctx.is_autonomous)
        self.assertEqual(ctx.session_type, "reattach")
        self.assertEqual(ctx.issue_number, 99)

    def test_no_workspace_returns_defaults(self):
        with patch("scripts.bugfixer_api._find_workspace_dir",
                   return_value=None):
            ctx = BugFixer.get_session_context("any-id")
        self.assertFalse(ctx.is_autonomous)
        self.assertIsNone(ctx.target_repo)
        self.assertEqual(ctx.config, {})


class TestBugFixerConvenience(unittest.TestCase):
    """BugFixer.is_autonomous() and BugFixer.get_config() convenience methods."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.workspace = Path(self.tmp) / "bug-fixer-loop-workspace"
        self.workspace.mkdir()
        self.dispatches = self.workspace / "dispatches"
        self.dispatches.mkdir()
        (self.workspace / "config.json").write_text(json.dumps({
            "target_repo": "owner/repo",
            "engineering_model": "pro",
        }))

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def _patch_workspace(self):
        return patch("scripts.bugfixer_api._find_workspace_dir",
                     return_value=self.workspace)

    def test_is_autonomous_true(self):
        data = {"conversation_id": "auto-1", "type": "fresh_fix"}
        (self.dispatches / "auto-1.json").write_text(json.dumps(data))
        with self._patch_workspace():
            self.assertTrue(BugFixer.is_autonomous("auto-1"))

    def test_is_autonomous_false(self):
        with self._patch_workspace():
            self.assertFalse(BugFixer.is_autonomous("interactive-1"))

    def test_is_autonomous_agrees_with_context(self):
        data = {"conversation_id": "check-1", "type": "triage"}
        (self.dispatches / "check-1.json").write_text(json.dumps(data))
        with self._patch_workspace():
            ctx = BugFixer.get_session_context("check-1")
            shorthand = BugFixer.is_autonomous("check-1")
        self.assertEqual(ctx.is_autonomous, shorthand)

    def test_get_config(self):
        with self._patch_workspace():
            config = BugFixer.get_config()
        self.assertEqual(config["target_repo"], "owner/repo")
        self.assertEqual(config["engineering_model"], "pro")

    def test_get_config_no_workspace(self):
        with patch("scripts.bugfixer_api._find_workspace_dir",
                   return_value=None):
            config = BugFixer.get_config()
        self.assertEqual(config, {})


class TestWorkspaceDiscoveryViaEnvVar(unittest.TestCase):
    """BUG_FIXER_CONFIG env var discovery path."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.workspace = Path(self.tmp) / "bug-fixer-loop-workspace"
        self.workspace.mkdir()
        self.dispatches = self.workspace / "dispatches"
        self.dispatches.mkdir()
        self.config_file = self.workspace / "config.json"
        self.config_file.write_text(json.dumps({
            "target_repo": "env-var/repo",
        }))

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def test_env_var_points_to_config_file(self):
        data = {"conversation_id": "env-test", "type": "fresh_fix"}
        (self.dispatches / "env-test.json").write_text(json.dumps(data))
        with patch.dict(os.environ, {"BUG_FIXER_CONFIG": str(self.config_file)}):
            ctx = BugFixer.get_session_context("env-test")
        self.assertTrue(ctx.is_autonomous)
        self.assertEqual(ctx.target_repo, "env-var/repo")

    def test_env_var_points_to_directory(self):
        data = {"conversation_id": "env-dir", "type": "triage"}
        (self.dispatches / "env-dir.json").write_text(json.dumps(data))
        with patch.dict(os.environ, {"BUG_FIXER_CONFIG": str(self.workspace)}):
            ctx = BugFixer.get_session_context("env-dir")
        self.assertTrue(ctx.is_autonomous)


if __name__ == "__main__":
    unittest.main()
