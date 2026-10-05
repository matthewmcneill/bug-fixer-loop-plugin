"""Tests for safe_post.py — the egress proxy for GitHub write operations."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPT = os.path.join(os.path.dirname(__file__), "..", "scripts", "safe_post.py")


class TestSafePost(unittest.TestCase):
    """Test safe_post.py as a subprocess (the way it's actually used)."""

    def _run(self, *args, env=None) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, SCRIPT, *args],
            capture_output=True, text=True, env=env,
        )

    def _make_config(self, patterns):
        """Create a temp config file with given leak patterns.
        Returns the path. Caller must set BUG_FIXER_CONFIG in env."""
        f = tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False)
        json.dump({
            "scanning": {
                "secrets": {
                    "patterns": patterns
                }
            }
        }, f)
        f.close()
        return f.name

    def _env_with_config(self, config_path):
        """Return env dict with BUG_FIXER_CONFIG set."""
        env = os.environ.copy()
        env["BUG_FIXER_CONFIG"] = config_path
        return env

    def test_label_only_passes_without_scanning(self):
        """label-only should not require body scanning."""
        r = self._run("label-only", "issue", "99999",
                       "--repo", "test/test",
                       "--add-label", "test-label")
        self.assertNotIn("DENIED", r.stderr)

    def test_body_with_leak_is_denied(self):
        """Body containing a leak pattern should be denied."""
        config_path = self._make_config(["SECRETTOKEN"])
        try:
            r = self._run("pr-comment", "1",
                           "--repo", "test/test",
                           "--body", "This contains SECRETTOKEN in it",
                           env=self._env_with_config(config_path))
            self.assertEqual(r.returncode, 1)
            self.assertIn("DENIED", r.stderr)
        finally:
            os.unlink(config_path)

    def test_clean_body_not_denied(self):
        """Body without leaks should pass scanning (may fail at gh call)."""
        config_path = self._make_config(["SECRETTOKEN"])
        try:
            r = self._run("pr-comment", "1",
                           "--repo", "test/test",
                           "--body", "This is a clean comment",
                           env=self._env_with_config(config_path))
            self.assertNotIn("DENIED", r.stderr)
        finally:
            os.unlink(config_path)

    def test_title_with_leak_is_denied(self):
        """Title containing a leak pattern should also be denied."""
        config_path = self._make_config(["ya29\\.[A-Za-z0-9_-]+"])
        try:
            r = self._run("issue-create",
                           "--repo", "test/test",
                           "--title", "Token: ya29.ABC123DEF456",
                           "--body", "clean body",
                           env=self._env_with_config(config_path))
            self.assertEqual(r.returncode, 1)
            self.assertIn("DENIED", r.stderr)
        finally:
            os.unlink(config_path)

    def test_body_file_stdin_denied(self):
        """--body-file - should be denied (unscannable)."""
        r = self._run("pr-comment", "1",
                       "--repo", "test/test",
                       "--body-file", "-")
        self.assertEqual(r.returncode, 1)
        self.assertIn("unscannable", r.stderr.lower())

    def test_body_file_reads_and_scans(self):
        """--body-file should read the file and scan its contents."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as bf:
            bf.write("This contains SECRETTOKEN")
            body_path = bf.name

        config_path = self._make_config(["SECRETTOKEN"])
        try:
            r = self._run("pr-comment", "1",
                           "--repo", "test/test",
                           "--body-file", body_path,
                           env=self._env_with_config(config_path))
            self.assertEqual(r.returncode, 1)
            self.assertIn("DENIED", r.stderr)
        finally:
            os.unlink(body_path)
            os.unlink(config_path)

    def test_issue_edit_label_only_no_body(self):
        """issue-edit with only labels and no body should pass scanning."""
        r = self._run("issue-edit", "1",
                       "--repo", "test/test",
                       "--add-label", "bug")
        self.assertNotIn("DENIED", r.stderr)

    # --- Gate B parity tests ---

    def test_pr_review_approve_denied(self):
        """--approve on pr-review should be denied (self-approval)."""
        r = self._run("pr-review", "1",
                       "--repo", "test/test",
                       "--body", "looks good",
                       "--approve")
        self.assertEqual(r.returncode, 1)
        self.assertIn("self-approval", r.stderr.lower())

    def test_pr_review_request_changes_denied(self):
        """--request-changes on pr-review should be denied."""
        r = self._run("pr-review", "1",
                       "--repo", "test/test",
                       "--body", "needs work",
                       "--request-changes")
        self.assertEqual(r.returncode, 1)
        self.assertIn("self-approval", r.stderr.lower())

    def test_approval_injection_denied(self):
        """Posting 'approved' as a body should be denied (approval injection)."""
        r = self._run("issue-comment", "1",
                       "--repo", "test/test",
                       "--body", "approved")
        self.assertEqual(r.returncode, 1)
        self.assertIn("approval", r.stderr.lower())

    def test_approval_injection_lgtm_denied(self):
        """Posting 'LGTM' as a body should be denied."""
        r = self._run("issue-comment", "1",
                       "--repo", "test/test",
                       "--body", "LGTM")
        self.assertEqual(r.returncode, 1)
        self.assertIn("approval", r.stderr.lower())

    def test_approval_injection_slash_approve_denied(self):
        """Posting '/approve' as a body should be denied."""
        r = self._run("pr-comment", "1",
                       "--repo", "test/test",
                       "--body", "/approve")
        self.assertEqual(r.returncode, 1)
        self.assertIn("approval", r.stderr.lower())

    def test_remove_security_label_denied(self):
        """Removing security:* labels should be denied."""
        r = self._run("label-only", "issue", "1",
                       "--repo", "test/test",
                       "--remove-label", "security:prompt-injection")
        self.assertEqual(r.returncode, 1)
        self.assertIn("security label", r.stderr.lower())

    def test_remove_security_label_via_edit_denied(self):
        """Removing security:* labels via issue-edit should be denied."""
        r = self._run("issue-edit", "1",
                       "--repo", "test/test",
                       "--remove-label", "security:prompt-injection")
        self.assertEqual(r.returncode, 1)
        self.assertIn("security label", r.stderr.lower())

    def test_normal_body_not_approval_injection(self):
        """A body that contains 'approved' as part of text should not trigger."""
        config_path = self._make_config([])
        try:
            r = self._run("issue-comment", "1",
                           "--repo", "test/test",
                           "--body", "I approved the design in yesterday's meeting",
                           env=self._env_with_config(config_path))
            self.assertNotIn("approval", r.stderr.lower())
        finally:
            os.unlink(config_path)

    def test_invalid_env_config_uses_fallback(self):
        """When BUG_FIXER_CONFIG points to a nonexistent file, falls back to plugin config."""
        env = os.environ.copy()
        env["BUG_FIXER_CONFIG"] = "/nonexistent/path/config.json"
        # Plugin's own config exists, so a clean body should pass scanning
        r = self._run("pr-comment", "1",
                       "--repo", "test/test",
                       "--body", "This is a clean comment with no secrets",
                       env=env)
        # Should NOT be denied by fail-closed since fallback config exists
        self.assertNotIn("failing closed", r.stderr.lower())

    def test_fail_closed_no_config_reachable(self):
        """Verify load_leak_patterns returns catch-all when no config is reachable."""
        # Can't fully test as subprocess (DEFAULT_CONFIG exists in this repo),
        # so test the function directly with mocking
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "safe_post_mod",
            os.path.join(os.path.dirname(__file__), "..", "scripts", "safe_post_testable.py")
            if False else SCRIPT  # use the real script
        )
        # Direct import test
        import types
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
        try:
            import safe_post as sp_mod
            # Temporarily override DEFAULT_CONFIG
            orig = sp_mod.DEFAULT_CONFIG
            sp_mod.DEFAULT_CONFIG = Path("/nonexistent/default")
            orig_env = os.environ.get("BUG_FIXER_CONFIG")
            os.environ["BUG_FIXER_CONFIG"] = "/also/nonexistent"
            try:
                patterns = sp_mod.load_leak_patterns()
                self.assertEqual(patterns, [".*"], "Should return catch-all when no config reachable")
            finally:
                sp_mod.DEFAULT_CONFIG = orig
                if orig_env is not None:
                    os.environ["BUG_FIXER_CONFIG"] = orig_env
                elif "BUG_FIXER_CONFIG" in os.environ:
                    del os.environ["BUG_FIXER_CONFIG"]
        finally:
            sys.path.pop(0)
            if "safe_post" in sys.modules:
                del sys.modules["safe_post"]


class TestGateBBlocksSafePost(unittest.TestCase):
    """Gate B should deny agent run_command calls to safe_post.py."""

    def test_gate_b_denies_safe_post(self):
        """An agent running safe_post.py via run_command should be denied."""
        from pre_tool_gate import evaluate_gate_b
        result = evaluate_gate_b(
            "run_command",
            {"CommandLine": "python3 scripts/safe_post.py issue-comment 1 --repo test/test --body hello"},
            "docs/design_specification.md",
            [],
            {}
        )
        self.assertIsNotNone(result)
        self.assertEqual(result["decision"], "deny")
        self.assertIn("safe_post.py", result["reason"])

    def test_gate_b_allows_safe_ingest(self):
        """safe_ingest.py should NOT be blocked by this rule."""
        from pre_tool_gate import evaluate_gate_b
        result = evaluate_gate_b(
            "run_command",
            {"CommandLine": "python3 scripts/safe_ingest.py issue-view 1"},
            "docs/design_specification.md",
            [],
            {}
        )
        # Should not be denied (may be allowed or denied by other rules)
        if result:
            self.assertNotIn("safe_post.py", result.get("reason", ""))


class TestShellMutationRelativePaths(unittest.TestCase):
    """Gate A / check_shell_mutation should block both absolute and relative workspace paths."""

    def test_relative_config_blocked(self):
        """Writing to .agents/.../config.json with relative path should be blocked."""
        from pre_tool_gate import check_shell_mutation
        result = check_shell_mutation(
            'echo "{}" > .agents/plugins/bug-fixer-loop-workspace/config.json',
            "docs/design_specification.md",
            []
        )
        self.assertTrue(result, "Relative path write to config.json should be blocked")

    def test_relative_cache_blocked(self):
        """Writing to .agents/.../injection_cache.json with relative path should be blocked."""
        from pre_tool_gate import check_shell_mutation
        result = check_shell_mutation(
            'echo "{}" > .agents/plugins/bug-fixer-loop-workspace/injection_cache.json',
            "docs/design_specification.md",
            []
        )
        self.assertTrue(result, "Relative path write to injection_cache.json should be blocked")


if __name__ == "__main__":
    unittest.main()
