"""Tests for safe_post.py — the egress proxy for GitHub write operations."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "daemon" / "workspace-plugin" / "bug-fixer" / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))
SCRIPT = str(SCRIPTS_DIR / "safe_post.py")


class TestSafePost(unittest.TestCase):
    """Test safe_post.py as a subprocess (the way it's actually used)."""

    def setUp(self):
        self.default_config_path = self._make_config([])

    def tearDown(self):
        if os.path.exists(self.default_config_path):
            os.unlink(self.default_config_path)

    def _run(self, *args, env=None, **kwargs) -> subprocess.CompletedProcess:
        if env is None:
            env = self._env_with_config(self.default_config_path)
        return subprocess.run(
            [sys.executable, SCRIPT, *args],
            capture_output=True, text=True, env=env, **kwargs,
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

    def test_add_architect_approved_label_denied_pr_edit(self):
        """Adding bug-fixer:architect:approved via pr-edit must be denied."""
        r = self._run("pr-edit", "1",
                       "--repo", "test/test",
                       "--add-label", "bug-fixer:architect:approved")
        self.assertEqual(r.returncode, 1)
        self.assertIn("DENIED", r.stderr)
        self.assertIn("protected label", r.stderr.lower())

    def test_add_architect_approved_label_denied_label_only(self):
        """Adding bug-fixer:architect:approved via label-only must be denied."""
        r = self._run("label-only", "pr", "1",
                       "--repo", "test/test",
                       "--add-label", "bug-fixer:architect:approved")
        self.assertEqual(r.returncode, 1)
        self.assertIn("DENIED", r.stderr)
        self.assertIn("protected label", r.stderr.lower())

    def test_add_architect_approved_label_denied_issue_edit(self):
        """Adding bug-fixer:architect:approved via issue-edit must be denied."""
        r = self._run("issue-edit", "1",
                       "--repo", "test/test",
                       "--add-label", "bug-fixer:architect:approved")
        self.assertEqual(r.returncode, 1)
        self.assertIn("DENIED", r.stderr)

    def test_add_architect_approved_comma_separated_denied(self):
        """Comma-separated labels containing architect:approved must be denied."""
        r = self._run("pr-edit", "1",
                       "--repo", "test/test",
                       "--add-label", "bug-fixer:system:pr,bug-fixer:architect:approved")
        self.assertEqual(r.returncode, 1)
        self.assertIn("DENIED", r.stderr)

    def test_add_non_protected_label_allowed(self):
        """Adding non-protected labels must be allowed (may fail at gh)."""
        r = self._run("pr-edit", "1",
                       "--repo", "test/test",
                       "--add-label", "bug-fixer:architect:awaiting-review")
        # Should NOT be denied by our check (may fail at gh call, but that's OK)
        self.assertNotIn("protected label", r.stderr.lower())

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
                           "--title", "Token: ya29." + "ABC123DEF456",
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
        with tempfile.TemporaryDirectory() as td:
            fallback_ws = Path(td) / ".agents" / "plugins" / "bug-fixer-loop-workspace"
            fallback_ws.mkdir(parents=True)
            (fallback_ws / "config.json").write_text(json.dumps({
                "scanning": {"secrets": {"patterns": []}}
            }))
            env = os.environ.copy()
            env["BUG_FIXER_CONFIG"] = "/nonexistent/path/config.json"
            # Plugin's own config exists, so a clean body should pass scanning
            r = self._run("pr-comment", "1",
                           "--repo", "test/test",
                           "--body", "This is a clean comment with no secrets",
                           env=env, cwd=td)
            # Should NOT be denied by fail-closed since fallback config exists
            self.assertNotIn("failing closed", r.stderr.lower())

    def test_fail_closed_no_config_reachable(self):
        """load_leak_patterns returns built-in patterns when no workspace config is reachable."""
        sys.path.insert(0, str(SCRIPTS_DIR))
        orig_cwd = os.getcwd()
        try:
            import safe_post as sp_mod  # noqa: PLC0415 - imported after sys.path setup

            with tempfile.TemporaryDirectory() as outside_repo, \
                    patch.object(sp_mod, "DEFAULT_CONFIG", Path("/nonexistent/default")), \
                    patch.dict(os.environ, {"BUG_FIXER_CONFIG": "/also/nonexistent"}):
                os.chdir(outside_repo)
                patterns = sp_mod.load_leak_patterns()
                # Should have the built-in floor patterns, not [".*"]
                self.assertNotIn(".*", patterns)
                self.assertTrue(len(patterns) >= len(sp_mod.BUILTIN_LEAK_PATTERNS))
        finally:
            os.chdir(orig_cwd)
            sys.path.pop(0)
            sys.modules.pop("safe_post", None)


class TestGateBAllowsSafePost(unittest.TestCase):
    """Gate B should allow agent run_command calls to canonical safe_post.py.

    safe_post.py is the sanctioned egress proxy for all agents —
    it scans content for leaks before posting. Raw `gh` writes are
    blocked; canonical safe_post.py is the approved alternative.
    """

    def test_gate_b_allows_safe_post(self):
        """An agent running the canonical egress proxy via run_command should be allowed."""
        from pre_tool_gate import evaluate_gate_b
        result = evaluate_gate_b(
            "run_command",
            {"CommandLine": f"python3 {SCRIPT} issue-comment 1 --repo test/test --body hello"},
            "docs/design_specification.md",
            [],
            {}
        )
        # Should be allowed (None means no deny, or explicit allow)
        if result:
            self.assertEqual(result["decision"], "allow")

    def test_gate_b_allows_safe_ingest(self):
        """safe_ingest.py should NOT be blocked by this rule."""
        from pre_tool_gate import evaluate_gate_b
        safe_ingest_script = str(SCRIPTS_DIR / "safe_ingest.py")
        result = evaluate_gate_b(
            "run_command",
            {"CommandLine": f"python3 {safe_ingest_script} issue-view 1"},
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


class TestProxyAllowPrecision(unittest.TestCase):
    """Canonical safe_post.py is allowed for all agents. Globs and spoofed paths are denied."""

    def _gate_b(self, cmd):
        from pre_tool_gate import evaluate_gate_b
        return evaluate_gate_b("run_command", {"CommandLine": cmd},
                               "docs/design_specification.md", [], {})

    def test_cat_proxy_allowed(self):
        """cat scripts/safe_post.py should NOT be denied."""
        result = self._gate_b("cat scripts/safe_post.py")
        if result:
            self.assertNotEqual(result["decision"], "deny",
                                "cat should not be denied by the proxy rule")

    def test_grep_proxy_allowed(self):
        """grep ... safe_post.py should NOT be denied."""
        result = self._gate_b("grep -n 'def ' scripts/safe_post.py")
        if result:
            self.assertNotEqual(result["decision"], "deny",
                                "grep should not be denied by the proxy rule")

    def test_test_file_allowed(self):
        """python3 -m unittest tests/test_safe_post.py should NOT be denied."""
        result = self._gate_b("python3 -m unittest tests.test_safe_post -v")
        if result:
            self.assertNotEqual(result["decision"], "deny",
                                "running test_safe_post should not be denied")

    def test_direct_safe_post_allowed(self):
        """python3 <canonical safe_post.py> should be allowed."""
        result = self._gate_b(f"python3 {SCRIPT} issue-comment 1 --repo test/test --body hi")
        if result:
            self.assertNotEqual(result["decision"], "deny",
                                "canonical safe_post.py is the sanctioned write path for all agents")

    def test_glob_safe_post_denied(self):
        """Glob patterns targeting safe_post.py must be denied."""
        for cmd in (
            "python3 scripts/safe_pos?.py issue-comment 1 --repo test/test --body hi",
            "python3 scripts/safe_pos[t].py issue-comment 1 --repo test/test --body hi",
            "python3 scripts/safe_post* issue-comment 1 --repo test/test --body hi",
        ):
            with self.subTest(cmd=cmd):
                result = self._gate_b(cmd)
                self.assertIsNotNone(result)
                self.assertEqual(result["decision"], "deny",
                                 f"glob targeting safe_post.py must be denied: {cmd}")


if __name__ == "__main__":
    unittest.main()
