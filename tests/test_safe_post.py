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

    def _make_config(self, patterns):
        """Create a temp config file with given leak patterns."""
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

    def test_label_only_passes_without_scanning(self):
        """label-only should not require body scanning."""
        r = self._run("label-only", "issue", "99999",
                       "--repo", "test/test",
                       "--add-label", "test-label")
        # Will fail at GitHub but NOT from scan denial
        self.assertNotIn("DENIED", r.stderr)

    def test_body_with_leak_is_denied(self):
        """Body containing a leak pattern should be denied."""
        config_path = self._make_config(["SECRETTOKEN"])
        try:
            r = self._run("--config", config_path,
                           "pr-comment", "1",
                           "--repo", "test/test",
                           "--body", "This contains SECRETTOKEN in it")
            self.assertEqual(r.returncode, 1)
            self.assertIn("DENIED", r.stderr)
        finally:
            os.unlink(config_path)

    def test_clean_body_not_denied(self):
        """Body without leaks should pass scanning (may fail at gh call)."""
        config_path = self._make_config(["SECRETTOKEN"])
        try:
            r = self._run("--config", config_path,
                           "pr-comment", "1",
                           "--repo", "test/test",
                           "--body", "This is a clean comment")
            self.assertNotIn("DENIED", r.stderr)
        finally:
            os.unlink(config_path)

    def test_title_with_leak_is_denied(self):
        """Title containing a leak pattern should also be denied."""
        config_path = self._make_config(["ya29\\.[A-Za-z0-9_-]+"])
        try:
            r = self._run("--config", config_path,
                           "issue-create",
                           "--repo", "test/test",
                           "--title", "Token: ya29.ABC123DEF456",
                           "--body", "clean body")
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
            r = self._run("--config", config_path,
                           "pr-comment", "1",
                           "--repo", "test/test",
                           "--body-file", body_path)
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
        # Only exact standalone "approved" / "LGTM" / "/approve" should trigger
        config_path = self._make_config([])
        try:
            r = self._run("--config", config_path,
                           "issue-comment", "1",
                           "--repo", "test/test",
                           "--body", "I approved the design in yesterday's meeting")
            self.assertNotIn("approval", r.stderr.lower())
        finally:
            os.unlink(config_path)

    def test_fail_closed_no_config(self):
        """Without any config, should fail closed (deny posts with content)."""
        r = self._run("--config", "/nonexistent/path/config.json",
                       "pr-comment", "1",
                       "--repo", "test/test",
                       "--body", "hello world")
        self.assertEqual(r.returncode, 1)
        self.assertIn("DENIED", r.stderr)


if __name__ == "__main__":
    unittest.main()
