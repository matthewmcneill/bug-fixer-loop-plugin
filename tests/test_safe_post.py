"""Tests for safe_post.py — the egress proxy for GitHub write operations."""
import json
import os
import subprocess
import sys
import tempfile
import unittest

SCRIPT = os.path.join(os.path.dirname(__file__), "..", "scripts", "safe_post.py")


class TestSafePost(unittest.TestCase):
    """Test safe_post.py as a subprocess (the way it's actually used)."""

    def _run(self, *args) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, SCRIPT, *args],
            capture_output=True, text=True,
        )

    def test_label_only_passes_without_scanning(self):
        """label-only should not require body scanning."""
        # This will fail to reach GitHub but should not exit(1) from scanning
        r = self._run("label-only", "issue", "99999",
                       "--repo", "test/test",
                       "--add-label", "test-label")
        # Will fail because the repo doesn't exist, but NOT from scan denial
        self.assertNotIn("DENIED", r.stderr)

    def test_body_with_leak_is_denied(self):
        """Body containing a leak pattern should be denied."""
        # Create a temp config with a pattern that matches "SECRETTOKEN"
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            json.dump({
                "scanning": {
                    "secrets": {
                        "patterns": ["SECRETTOKEN"]
                    }
                }
            }, f)
            config_path = f.name

        try:
            env = os.environ.copy()
            env["BUG_FIXER_CONFIG"] = config_path
            r = subprocess.run(
                [sys.executable, SCRIPT, "pr-comment", "1",
                 "--repo", "test/test",
                 "--body", "This contains SECRETTOKEN in it"],
                capture_output=True, text=True, env=env,
            )
            self.assertEqual(r.returncode, 1)
            self.assertIn("DENIED", r.stderr)
        finally:
            os.unlink(config_path)

    def test_clean_body_not_denied(self):
        """Body without leaks should pass scanning (may fail at gh call)."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            json.dump({
                "scanning": {
                    "secrets": {
                        "patterns": ["SECRETTOKEN"]
                    }
                }
            }, f)
            config_path = f.name

        try:
            env = os.environ.copy()
            env["BUG_FIXER_CONFIG"] = config_path
            r = subprocess.run(
                [sys.executable, SCRIPT, "pr-comment", "1",
                 "--repo", "test/test",
                 "--body", "This is a clean comment"],
                capture_output=True, text=True, env=env,
            )
            # Should NOT be denied by scanning
            self.assertNotIn("DENIED", r.stderr)
        finally:
            os.unlink(config_path)

    def test_title_with_leak_is_denied(self):
        """Title containing a leak pattern should also be denied."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            json.dump({
                "scanning": {
                    "secrets": {
                        "patterns": ["ya29\\.[A-Za-z0-9_-]+"]
                    }
                }
            }, f)
            config_path = f.name

        try:
            env = os.environ.copy()
            env["BUG_FIXER_CONFIG"] = config_path
            r = subprocess.run(
                [sys.executable, SCRIPT, "issue-create",
                 "--repo", "test/test",
                 "--title", "Token: ya29.ABC123DEF456",
                 "--body", "clean body"],
                capture_output=True, text=True, env=env,
            )
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

        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as cf:
            json.dump({
                "scanning": {"secrets": {"patterns": ["SECRETTOKEN"]}}
            }, cf)
            config_path = cf.name

        try:
            env = os.environ.copy()
            env["BUG_FIXER_CONFIG"] = config_path
            r = subprocess.run(
                [sys.executable, SCRIPT, "pr-comment", "1",
                 "--repo", "test/test",
                 "--body-file", body_path],
                capture_output=True, text=True, env=env,
            )
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


if __name__ == "__main__":
    unittest.main()
