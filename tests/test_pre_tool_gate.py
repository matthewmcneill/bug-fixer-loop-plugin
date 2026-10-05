from contextlib import redirect_stderr
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import unittest.mock
from typing import Any

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "daemon" / "workspace-plugin" / "bug-fixer" / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from pre_tool_gate import (
    REASON_APPROVAL_INJECTION,
    REASON_AUTO_MERGE,
    REASON_EGRESS_LEAK,
    REASON_HOST_SCRIPT,
    REASON_ISSUE_REOPEN,
    REASON_PROTECTED_FILE,
    REASON_RAW_GH,
    REASON_SELF_APPROVAL,
    REASON_SHELL_MUTATION,
    REASON_SPEC_IMMUTABLE,
    RegexBackend,
    ScanBackend,
    ScanScope,
    ScanViolation,
    check_egress_leaks,
    extract_outbound_body,
    resolve_backend,
    scan_repository,
    normalise_config,
    stamp_receipt,
    verify_receipt,
)

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
GATE_SCRIPT = PLUGIN_ROOT / "daemon" / "workspace-plugin" / "bug-fixer" / "scripts" / "pre_tool_gate.py"

# Test config (config.json no longer lives in plugin root — it's in the consumer workspace)
TEST_CONFIG = {
    "spec_file_path": "docs/plugin-architecture.md",
    "protected_files": ["docs/plugin-architecture.md"],
    "leak_patterns": [
        "ya29\\.[A-Za-z0-9_-]+",
        "AKIA[0-9A-Z]{16}",
        "-----BEGIN (RSA |EC )?PRIVATE KEY-----",
    ],
}


class TestPreToolGate(unittest.TestCase):
    def _run_gate_stdin(self, payload: dict[str, Any], config_path: str | None = None) -> dict[str, Any]:
        """Invokes pre_tool_gate.py via subprocess passing JSON payload on stdin."""
        cmd = [sys.executable, str(GATE_SCRIPT)]
        if config_path:
            cmd.extend(["--config", config_path])
        proc = subprocess.run(
            cmd,
            cwd=str(PLUGIN_ROOT),
            input=json.dumps(payload),
            text=True,
            capture_output=True,
            check=True,
        )
        return json.loads(proc.stdout.strip())

    # Gate A: File Write Protection Tests
    def test_gate_a_deny_write_to_spec_file(self):
        payload = {
            "toolCall": {
                "name": "write_to_file",
                "args": {"TargetFile": "docs/plugin-architecture.md"},
            }
        }
        res = self._run_gate_stdin(payload)
        self.assertEqual(res["decision"], "deny")
        self.assertEqual(res["reason"], REASON_SPEC_IMMUTABLE)

        payload_replace = {
            "toolCall": {
                "name": "replace_file_content",
                "args": {"TargetFile": "/path/to/docs/plugin-architecture.md"},
            }
        }
        res_replace = self._run_gate_stdin(payload_replace)
        self.assertEqual(res_replace["decision"], "deny")
        self.assertEqual(res_replace["reason"], REASON_SPEC_IMMUTABLE)

    def test_gate_a_deny_write_to_protected_file(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as tf:
            json.dump(
                {
                    "spec_file_path": "docs/spec.md",
                    "protected_files": ["hardware_config.h", "include/pins.h"],
                },
                tf,
            )
            cfg_path = tf.name
        try:
            payload = {
                "toolCall": {
                    "name": "write_to_file",
                    "args": {"TargetFile": "hardware_config.h"},
                }
            }
            res = self._run_gate_stdin(payload, config_path=cfg_path)
            self.assertEqual(res["decision"], "deny")
            self.assertEqual(res["reason"], REASON_PROTECTED_FILE)
        finally:
            os.unlink(cfg_path)

    def test_gate_a_deny_write_to_pre_tool_gate_self(self):
        for target in ("scripts/pre_tool_gate.py", "/full/path/to/scripts/pre_tool_gate.py", "pre_tool_gate.py"):
            payload = {
                "tool_name": "write_to_file",
                "tool_input": {"TargetFile": target},
            }
            res = self._run_gate_stdin(payload)
            self.assertEqual(res["decision"], "deny")
            self.assertEqual(res["reason"], REASON_HOST_SCRIPT)

    def test_gate_a_deny_write_to_safe_ingest(self):
        for target in ("scripts/safe_ingest.py", "/opt/plugin/scripts/safe_ingest.py", "safe_ingest.py"):
            payload = {
                "toolCall": {
                    "name": "replace_file_content",
                    "args": {"TargetFile": target},
                }
            }
            res = self._run_gate_stdin(payload)
            self.assertEqual(res["decision"], "deny")
            self.assertEqual(res["reason"], REASON_HOST_SCRIPT)

    def test_gate_a_allow_write_to_normal_file(self):
        for target in ("src/main.cpp", "include/app_state.h", "tests/test_fix.py"):
            payload = {
                "toolCall": {
                    "name": "write_to_file",
                    "args": {"TargetFile": target},
                }
            }
            res = self._run_gate_stdin(payload)
            self.assertEqual(res["decision"], "allow")

    # Gate B: Command Execution Protection Tests
    def test_gate_b_deny_gh_pr_merge(self):
        for cmd in ("gh pr merge 12 --squash", "gh pr merge --auto", "gh stack merge"):
            payload = {
                "toolCall": {
                    "name": "run_command",
                    "args": {"CommandLine": cmd},
                }
            }
            res = self._run_gate_stdin(payload)
            self.assertEqual(res["decision"], "deny")
            self.assertEqual(res["reason"], REASON_AUTO_MERGE)

    def test_gate_b_deny_gh_pr_review_approve(self):
        for cmd in ("gh pr review 42 --approve", "gh pr review --approve -b 'LGTM'"):
            payload = {
                "toolCall": {
                    "name": "run_command",
                    "args": {"CommandLine": cmd},
                }
            }
            res = self._run_gate_stdin(payload)
            self.assertEqual(res["decision"], "deny")
            self.assertEqual(res["reason"], REASON_SELF_APPROVAL)

    def test_gate_b_deny_gh_issue_reopen(self):
        payload = {
            "toolCall": {
                "name": "run_command",
                "args": {"CommandLine": "gh issue reopen 10"},
            }
        }
        res = self._run_gate_stdin(payload)
        self.assertEqual(res["decision"], "deny")
        self.assertEqual(res["reason"], REASON_ISSUE_REOPEN)

    def test_gate_b_deny_raw_gh_issue_list(self):
        for cmd in ("gh issue list", "gh issue list --state open", "items=$(gh issue list --json number)"):
            payload = {
                "toolCall": {
                    "name": "run_command",
                    "args": {"CommandLine": cmd},
                }
            }
            res = self._run_gate_stdin(payload)
            self.assertEqual(res["decision"], "deny")
            self.assertEqual(res["reason"], REASON_RAW_GH)

    def test_gate_b_deny_raw_gh_pr_view(self):
        for cmd in ("gh pr view 12", "gh pr view --json title,body"):
            payload = {
                "toolCall": {
                    "name": "run_command",
                    "args": {"CommandLine": cmd},
                }
            }
            res = self._run_gate_stdin(payload)
            self.assertEqual(res["decision"], "deny")
            self.assertEqual(res["reason"], REASON_RAW_GH)

    def test_gate_b_deny_shell_mutation_of_spec_file(self):
        for cmd in (
            "sed -i '' 's/old/new/' docs/plugin-architecture.md",
            "cat patch.txt > docs/plugin-architecture.md",
            "rm docs/plugin-architecture.md",
            "cp new.md docs/plugin-architecture.md",
        ):
            payload = {
                "toolCall": {
                    "name": "run_command",
                    "args": {"CommandLine": cmd},
                }
            }
            res = self._run_gate_stdin(payload)
            self.assertEqual(res["decision"], "deny")
            self.assertEqual(res["reason"], REASON_SHELL_MUTATION)

    def test_gate_b_deny_shell_mutation_of_security_script(self):
        for cmd in (
            "cat evil.py > scripts/pre_tool_gate.py",
            "rm scripts/safe_ingest.py",
            "mv /tmp/bad.py scripts/safe_ingest.py",
        ):
            payload = {
                "toolCall": {
                    "name": "run_command",
                    "args": {"CommandLine": cmd},
                }
            }
            res = self._run_gate_stdin(payload)
            self.assertEqual(res["decision"], "deny")
            self.assertEqual(res["reason"], REASON_SHELL_MUTATION)

    def test_gate_b_deny_approval_comment_injection(self):
        for cmd in (
            "gh issue comment 12 --body 'approved'",
            "gh issue comment 12 -b '/approve'",
            "gh pr comment 44 --body 'approved!'",
            "echo 'approved' | gh issue comment 12",
        ):
            payload = {
                "toolCall": {
                    "name": "run_command",
                    "args": {"CommandLine": cmd},
                }
            }
            res = self._run_gate_stdin(payload)
            self.assertEqual(res["decision"], "deny")
            self.assertEqual(res["reason"], REASON_APPROVAL_INJECTION)

    def test_gate_b_allow_normal_command(self):
        for cmd in (
            "pio test -e native",
            "python3 daemon/workspace-plugin/bug-fixer/scripts/safe_ingest.py issue-list",
            "git status",
            "git checkout -b fix/issue-100",
            "git add src/main.cpp",
            "git commit -m 'Fix null dereference in OTA check'",
        ):
            payload = {
                "toolCall": {
                    "name": "run_command",
                    "args": {"CommandLine": cmd},
                }
            }
            res = self._run_gate_stdin(payload)
            self.assertEqual(res["decision"], "allow")

    # Gate C: Non-Intercepted Tool Calls
    def test_gate_c_allow_view_file(self):
        payload = {
            "toolCall": {
                "name": "view_file",
                "args": {"AbsolutePath": "/path/to/docs/plugin-architecture.md"},
            }
        }
        res = self._run_gate_stdin(payload)
        self.assertEqual(res["decision"], "allow")

    def test_gate_c_allow_search_web(self):
        payload = {
            "toolCall": {
                "name": "search_web",
                "args": {"query": "ESP32 OTA rollback documentation"},
            }
        }
        res = self._run_gate_stdin(payload)
        self.assertEqual(res["decision"], "allow")

    # CLI Mode: --scan-only Tests
    def test_cli_scan_only_detects_planted_leak(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            subprocess.run(["git", "init"], cwd=temp_dir, check=True, capture_output=True)
            leak_file = Path(temp_dir) / "credentials.txt"
            leak_file.write_text("aws_key = AKIAIOSFODNN7EXAMPLE\n")
            subprocess.run(["git", "add", "credentials.txt"], cwd=temp_dir, check=True, capture_output=True)

            # Create temp config with leak patterns
            cfg_file = Path(temp_dir) / "test_config.json"
            cfg_file.write_text(json.dumps(TEST_CONFIG))

            cmd = [
                sys.executable,
                str(GATE_SCRIPT),
                "--scan-only",
                temp_dir,
                "--config",
                str(cfg_file),
            ]
            proc = subprocess.run(cmd, capture_output=True, text=True)
            self.assertEqual(proc.returncode, 1)
            self.assertIn("LEAK SCAN FAILED", proc.stdout)
            self.assertNotIn("AKIAIOSFODNN7EXAMPLE", proc.stdout)
            self.assertIn("AKIA…", proc.stdout)

    def test_scan_repository_redacts_secrets(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            subprocess.run(["git", "init"], cwd=temp_dir, check=True, capture_output=True)
            leak_file = Path(temp_dir) / "secret.txt"
            leak_file.write_text("secret is AKIAIOSFODNN7EXAMPLE\n")
            subprocess.run(["git", "add", "secret.txt"], cwd=temp_dir, check=True, capture_output=True)

            patterns = TEST_CONFIG["leak_patterns"]
            import pre_tool_gate
            violations, scanned, excluded = pre_tool_gate.scan_repository(temp_dir, patterns)
            self.assertEqual(len(violations), 1)
            self.assertIn("secret.txt", violations[0])
            self.assertIn("AKIA[0-9A-Z]{16}", violations[0])
            self.assertNotIn("AKIAIOSFODNN7EXAMPLE", violations[0])
            self.assertIn("AKIA…", violations[0])

    def test_cli_scan_only_passes_on_clean_directory(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            subprocess.run(["git", "init"], cwd=temp_dir, check=True, capture_output=True)
            clean_file = Path(temp_dir) / "hello.cpp"
            clean_file.write_text("#include <iostream>\nint main() { return 0; }\n")
            subprocess.run(["git", "add", "hello.cpp"], cwd=temp_dir, check=True, capture_output=True)

            # Create temp config with leak patterns
            cfg_file = Path(temp_dir) / "test_config.json"
            cfg_file.write_text(json.dumps(TEST_CONFIG))

            cmd = [
                sys.executable,
                str(GATE_SCRIPT),
                "--scan-only",
                temp_dir,
                "--config",
                str(cfg_file),
            ]
            proc = subprocess.run(cmd, capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0)
            self.assertIn("LEAK SCAN CLEAN", proc.stdout)

    def test_cli_receipt_stamp_and_verify(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            subprocess.run(["git", "init"], cwd=temp_dir, check=True, capture_output=True)
            clean_file = Path(temp_dir) / "main.cpp"
            clean_file.write_text("int x = 42;\n")
            subprocess.run(["git", "add", "main.cpp"], cwd=temp_dir, check=True, capture_output=True)

            stamp_cmd = [sys.executable, str(GATE_SCRIPT), "--repo", temp_dir, "--stamp-receipt"]
            stamp_proc = subprocess.run(stamp_cmd, capture_output=True, text=True)
            self.assertEqual(stamp_proc.returncode, 0)
            self.assertIn("STAMPED", stamp_proc.stdout)

            verify_cmd = [sys.executable, str(GATE_SCRIPT), "--repo", temp_dir, "--verify-receipt"]
            verify_proc = subprocess.run(verify_cmd, capture_output=True, text=True)
            self.assertEqual(verify_proc.returncode, 0)
            self.assertIn("Receipt verified", verify_proc.stdout)


    def test_scan_repository_excludes_tests(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            subprocess.run(["git", "init"], cwd=temp_dir, check=True, capture_output=True)
            
            leak_file = Path(temp_dir) / "tests" / "credentials.txt"
            leak_file.parent.mkdir(parents=True, exist_ok=True)
            leak_file.write_text("aws_key = AKIAIOSFODNN7EXAMPLE\\n")
            
            subprocess.run(["git", "add", "tests/credentials.txt"], cwd=temp_dir, check=True, capture_output=True)
            
            patterns = TEST_CONFIG["leak_patterns"]
            import pre_tool_gate
            violations, scanned, excluded = pre_tool_gate.scan_repository(temp_dir, patterns, exclude_paths=["tests/"])
            self.assertEqual(len(violations), 0)
            self.assertEqual(excluded, 1)
            self.assertEqual(scanned, 0)

    def test_scan_repository_excludes_do_not_suppress_others(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            subprocess.run(["git", "init"], cwd=temp_dir, check=True, capture_output=True)
            
            leak_file1 = Path(temp_dir) / "tests" / "credentials.txt"
            leak_file1.parent.mkdir(parents=True, exist_ok=True)
            leak_file1.write_text("aws_key = AKIAIOSFODNN7EXAMPLE\\n")
            
            leak_file2 = Path(temp_dir) / "src" / "credentials.txt"
            leak_file2.parent.mkdir(parents=True, exist_ok=True)
            leak_file2.write_text("aws_key = AKIAIOSFODNN7EXAMPLE\\n")
            
            subprocess.run(["git", "add", "tests/credentials.txt", "src/credentials.txt"], cwd=temp_dir, check=True, capture_output=True)
            
            patterns = TEST_CONFIG["leak_patterns"]
            import pre_tool_gate
            violations, scanned, excluded = pre_tool_gate.scan_repository(temp_dir, patterns, exclude_paths=["tests/"])
            self.assertEqual(len(violations), 1)
            self.assertIn("src/credentials.txt", violations[0])
            self.assertEqual(excluded, 1)
            self.assertEqual(scanned, 1)

    def test_cli_scan_only_exclude_pattern(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            subprocess.run(["git", "init"], cwd=temp_dir, check=True, capture_output=True)
            
            leak_file = Path(temp_dir) / "tests" / "credentials.txt"
            leak_file.parent.mkdir(parents=True, exist_ok=True)
            leak_file.write_text("aws_key = AKIAIOSFODNN7EXAMPLE\\n")
            subprocess.run(["git", "add", "tests/credentials.txt"], cwd=temp_dir, check=True, capture_output=True)

            cfg_file = Path(temp_dir) / "test_config.json"
            cfg = TEST_CONFIG.copy()
            cfg["scan_exclude_paths"] = []
            cfg_file.write_text(json.dumps(cfg))

            cmd = [
                sys.executable,
                str(GATE_SCRIPT),
                "--scan-only",
                temp_dir,
                "--config",
                str(cfg_file),
                "--exclude-pattern",
                "tests/"
            ]
            proc = subprocess.run(cmd, capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0)
            self.assertIn("LEAK SCAN CLEAN", proc.stdout)
            self.assertIn("1 excluded", proc.stdout)

    # Egress Leak Gate Tests (WO1)
    def test_egress_leak_deny_gh_issue_comment(self):
        payload = {
            "toolCall": {
                "name": "run_command",
                "args": {"CommandLine": 'gh issue comment 42 --body "contains AKIAIOSFODNN7EXAMPLE"'},
            }
        }
        res = self._run_gate_stdin(payload)
        self.assertEqual(res["decision"], "deny")
        self.assertEqual(res["reason"], REASON_EGRESS_LEAK)

    def test_egress_leak_allow_gh_pr_create_safe(self):
        payload = {
            "toolCall": {
                "name": "run_command",
                "args": {"CommandLine": 'gh pr create --body "safe content"'},
            }
        }
        res = self._run_gate_stdin(payload)
        self.assertEqual(res["decision"], "allow")

    def test_egress_leak_deny_gh_pr_comment_flag_b(self):
        payload = {
            "toolCall": {
                "name": "run_command",
                "args": {"CommandLine": 'gh pr comment 5 -b "has ya29.token-here"'},
            }
        }
        res = self._run_gate_stdin(payload)
        self.assertEqual(res["decision"], "deny")
        self.assertEqual(res["reason"], REASON_EGRESS_LEAK)

    def test_egress_leak_deny_gh_api_field_body(self):
        payload = {
            "toolCall": {
                "name": "run_command",
                "args": {
                    "CommandLine": 'gh api repos/owner/repo/issues/1/comments -f body="AKIA1234567890ABCDEF"'
                },
            }
        }
        res = self._run_gate_stdin(payload)
        self.assertEqual(res["decision"], "deny")
        self.assertEqual(res["reason"], REASON_EGRESS_LEAK)

    def test_extract_outbound_body_forms(self):
        self.assertEqual(
            extract_outbound_body('gh issue comment 42 --body "hello world"'),
            "hello world",
        )
        self.assertEqual(
            extract_outbound_body("gh pr comment 5 -b 'short flag body'"),
            "short flag body",
        )
        self.assertEqual(
            extract_outbound_body('gh api repos/owner/repo/issues/1/comments -f body="api body"'),
            "api body",
        )
        with tempfile.NamedTemporaryFile("w", delete=False) as tf:
            tf.write("file content for body")
            temp_path = tf.name
        try:
            self.assertEqual(
                extract_outbound_body(f"gh issue comment 42 --body-file {temp_path}"),
                "file content for body",
            )
            self.assertIsNone(
                extract_outbound_body("gh issue comment 42 --body-file /nonexistent/file/path.txt")
            )
        finally:
            os.unlink(temp_path)

        # Pipe echo form
        self.assertEqual(
            extract_outbound_body('echo "piped message" | gh issue comment 42'),
            "piped message",
        )
        # Command with no body
        self.assertIsNone(extract_outbound_body("gh pr list"))

    def test_egress_leak_allow_command_with_no_outbound_body(self):
        # check_egress_leaks returns False for commands with no outbound body
        self.assertFalse(check_egress_leaks("gh pr list", TEST_CONFIG["leak_patterns"]))
        # Outbound gh command without a body is allowed through the gate
        payload = {
            "toolCall": {
                "name": "run_command",
                "args": {"CommandLine": "gh pr checkout 42"},
            }
        }
        res = self._run_gate_stdin(payload)
        self.assertEqual(res["decision"], "allow")

    def test_egress_leak_non_gh_commands_unaffected(self):
        # Non-gh commands containing secret-matching text are not affected
        for cmd in (
            'echo "contains AKIAIOSFODNN7EXAMPLE"',
            'grep "AKIAIOSFODNN7EXAMPLE" /path/to/file',
        ):
            self.assertFalse(check_egress_leaks(cmd, TEST_CONFIG["leak_patterns"]))
            payload = {
                "toolCall": {
                    "name": "run_command",
                    "args": {"CommandLine": cmd},
                }
            }
            res = self._run_gate_stdin(payload)
            self.assertEqual(res["decision"], "allow")

    def test_egress_leak_deny_echo_pipe(self):
        payload = {
            "toolCall": {
                "name": "run_command",
                "args": {"CommandLine": 'echo "contains AKIAIOSFODNN7EXAMPLE" | gh issue comment 42'},
            }
        }
        res = self._run_gate_stdin(payload)
        self.assertEqual(res["decision"], "deny")
        self.assertEqual(res["reason"], REASON_EGRESS_LEAK)

    def test_egress_leak_deny_body_file(self):
        with tempfile.NamedTemporaryFile("w", delete=False) as tf:
            tf.write("leaked key AKIAIOSFODNN7EXAMPLE in file")
            temp_path = tf.name
        try:
            payload = {
                "toolCall": {
                    "name": "run_command",
                    "args": {"CommandLine": f"gh issue comment 42 --body-file {temp_path}"},
                }
            }
            res = self._run_gate_stdin(payload)
            self.assertEqual(res["decision"], "deny")
            self.assertEqual(res["reason"], REASON_EGRESS_LEAK)
        finally:
            os.unlink(temp_path)

    # ScanBackend & WO2 Tests
    def test_regex_backend_scan_tree_matches_scan_repository(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            subprocess.run(["git", "init"], cwd=temp_dir, check=True, capture_output=True)
            f1 = Path(temp_dir) / "file1.txt"
            f1.write_text("clean line\n")
            f2 = Path(temp_dir) / "file2.txt"
            f2.write_text("aws_key = AKIAIOSFODNN7EXAMPLE\n")
            subprocess.run(["git", "add", "file1.txt", "file2.txt"], cwd=temp_dir, check=True, capture_output=True)

            patterns = TEST_CONFIG["leak_patterns"]
            backend = RegexBackend(patterns)
            backend_violations = backend.scan(temp_dir, ScanScope.TREE)

            repo_violations, scanned, excluded = scan_repository(temp_dir, patterns)

            self.assertEqual(len(backend_violations), len(repo_violations))
            self.assertEqual([str(v) for v in backend_violations], repo_violations)
            self.assertEqual(backend.scanned_count, scanned)
            self.assertEqual(backend.excluded_count, excluded)

    def test_regex_backend_scan_staged_only_scans_staged_files(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            subprocess.run(["git", "init"], cwd=temp_dir, check=True, capture_output=True)
            # Commit an initial file
            init_file = Path(temp_dir) / "init.txt"
            init_file.write_text("init\n")
            subprocess.run(["git", "add", "init.txt"], cwd=temp_dir, check=True, capture_output=True)
            subprocess.run(["git", "commit", "-m", "initial commit"], cwd=temp_dir, check=True, capture_output=True)

            # Modify init.txt with a leak but DO NOT stage it
            init_file.write_text("modified_unstaged = AKIAIOSFODNN7EXAMPLE\n")

            # Create staged.txt with a leak and stage it
            staged_file = Path(temp_dir) / "staged.txt"
            staged_file.write_text("staged = AKIAIOSFODNN7EXAMPLE\n")
            subprocess.run(["git", "add", "staged.txt"], cwd=temp_dir, check=True, capture_output=True)

            patterns = TEST_CONFIG["leak_patterns"]
            backend = RegexBackend(patterns)

            # Staged scan should ONLY find the staged file
            staged_violations = backend.scan(temp_dir, ScanScope.STAGED)
            self.assertEqual(len(staged_violations), 1)
            self.assertEqual(staged_violations[0].path, "staged.txt")

            # Tree scan should find both because init.txt is tracked and staged.txt is added
            tree_violations = backend.scan(temp_dir, ScanScope.TREE)
            self.assertEqual(len(tree_violations), 2)
            tree_paths = {v.path for v in tree_violations}
            self.assertEqual(tree_paths, {"init.txt", "staged.txt"})

    def test_regex_backend_config_hash_consistency(self):
        patterns1 = ["ya29\\.[A-Za-z0-9_-]+", "AKIA[0-9A-Z]{16}"]
        patterns2 = ["AKIA[0-9A-Z]{16}", "ya29\\.[A-Za-z0-9_-]+"]

        backend1 = RegexBackend(patterns1)
        backend2 = RegexBackend(patterns2)

        hash1 = backend1.config_hash()
        hash2 = backend2.config_hash()

        self.assertEqual(hash1, hash2)
        self.assertEqual(len(hash1), 64)  # 64 hex characters for SHA-256

        backend3 = RegexBackend(["DIFFERENT_PATTERN"])
        self.assertNotEqual(hash1, backend3.config_hash())

    def test_resolve_backend_returns_regex_for_regex_and_unknown(self):
        # Default config without scanning block
        b1 = resolve_backend({})
        self.assertIsInstance(b1, RegexBackend)
        self.assertEqual(b1.name, "regex")
        self.assertEqual(b1.version, "builtin")

        # Explicit regex backend
        cfg_regex = {"scanning": {"secrets": {"backend": "regex"}}, "leak_patterns": ["pat1"]}
        b2 = resolve_backend(cfg_regex)
        self.assertIsInstance(b2, RegexBackend)
        self.assertEqual(b2.name, "regex")
        self.assertEqual(b2.patterns, ["pat1"])

        # Unknown backend falls back to regex and prints warning to stderr
        cfg_unknown = {"scanning": {"secrets": {"backend": "unknown_scanner"}}}
        with io.StringIO() as fake_stderr, redirect_stderr(fake_stderr):
            b3 = resolve_backend(cfg_unknown)
            warning_output = fake_stderr.getvalue()

        self.assertIsInstance(b3, RegexBackend)
        self.assertEqual(b3.name, "regex")
        self.assertIn(
            "WARNING: Unknown scan backend 'unknown_scanner', falling back to regex (degraded)",
            warning_output,
        )

    def test_receipt_includes_backend_metadata(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            subprocess.run(["git", "init"], cwd=temp_dir, check=True, capture_output=True)
            f = Path(temp_dir) / "main.cpp"
            f.write_text("int x = 1;\n")
            subprocess.run(["git", "add", "main.cpp"], cwd=temp_dir, check=True, capture_output=True)

            backend = RegexBackend(["pat1", "pat2"])
            ret = stamp_receipt(temp_dir, "PASS", backend=backend)
            self.assertEqual(ret, 0)

            receipt_file = Path(temp_dir) / ".git" / "leak_audit_receipt.json"
            self.assertTrue(receipt_file.is_file())
            data = json.loads(receipt_file.read_text(encoding="utf-8"))

            self.assertEqual(data["status"], "PASS")
            self.assertIn("tree_sha", data)
            self.assertIn("timestamp", data)
            self.assertEqual(data["backend"], "regex")
            self.assertEqual(data["backend_version"], "builtin")
            self.assertEqual(data["config_hash"], backend.config_hash())
            self.assertIs(data["validate"], False)

    def test_scan_violation_str_format(self):
        violation = ScanViolation(
            path="src/config.py",
            line=42,
            rule="AKIA[0-9A-Z]{16}",
            redacted_match="AKIA…",
        )
        self.assertEqual(str(violation), "src/config.py:42 [AKIA[0-9A-Z]{16}] -> AKIA…")

    def test_regex_backend_history_scope_raises_not_implemented(self):
        backend = RegexBackend()
        with self.assertRaises(NotImplementedError):
            backend.scan(".", ScanScope.HISTORY)

    def test_cli_scan_only_output_shows_backend(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            subprocess.run(["git", "init"], cwd=temp_dir, check=True, capture_output=True)
            clean_file = Path(temp_dir) / "hello.cpp"
            clean_file.write_text("int main() { return 0; }\n")
            subprocess.run(["git", "add", "hello.cpp"], cwd=temp_dir, check=True, capture_output=True)

            cfg_file = Path(temp_dir) / "test_config.json"
            cfg_file.write_text(json.dumps(TEST_CONFIG))

            cmd = [
                sys.executable,
                str(GATE_SCRIPT),
                "--scan-only",
                temp_dir,
                "--config",
                str(cfg_file),
            ]
            proc = subprocess.run(cmd, capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0)
            self.assertIn("backend=regex (degraded)", proc.stdout)


    # BetterleaksBackend Tests
    def test_betterleaks_build_env_strips_vars(self):
        from pre_tool_gate import BetterleaksBackend
        import os
        os.environ['BETTERLEAKS_VALIDATE'] = '1'
        os.environ['BETTERLEAKS_SOMETHING'] = 'abc'
        os.environ['OTHER_VAR'] = 'xyz'
        
        backend = BetterleaksBackend()
        env = backend._build_env()
        
        self.assertNotIn('BETTERLEAKS_VALIDATE', env)
        self.assertNotIn('BETTERLEAKS_SOMETHING', env)
        self.assertIn('OTHER_VAR', env)

    def test_betterleaks_build_cmd_flags(self):
        from pre_tool_gate import BetterleaksBackend, ScanScope
        backend = BetterleaksBackend(config_path='/fake/path.toml')
        cmd = backend._build_cmd('/repo', ScanScope.TREE)
        
        expected_flags = [
            '--validate=false',
            '--analyze=false',
            '--redact',
            '--no-banner',
            '-o', '-',
            '--exit-code', '1',
            '--ignore-file', '/dev/null',
            '--config', '/fake/path.toml'
        ]
        
        for flag in expected_flags:
            self.assertIn(flag, cmd)

    def test_betterleaks_build_cmd_scopes(self):
        from pre_tool_gate import BetterleaksBackend, ScanScope
        backend = BetterleaksBackend()
        
        cmd_staged = backend._build_cmd('/repo', ScanScope.STAGED)
        self.assertEqual(cmd_staged[:4], ['betterleaks', 'git', '--staged', '/repo'])
        
        cmd_tree = backend._build_cmd('/repo', ScanScope.TREE)
        self.assertEqual(cmd_tree[:3], ['betterleaks', 'fs', '/repo'])

    @unittest.mock.patch('subprocess.check_output')
    def test_betterleaks_scan_parses_json(self, mock_check_output):
        from pre_tool_gate import BetterleaksBackend, ScanScope
        mock_output = '''{
            "scan": {"state": "complete", "files_scanned": 12},
            "findings": [
                {"location": {"path": "src/main.py", "start_line": 10}, "rule_id": "AWS_KEY", "match": {"value": "AKIA..."}},
                {"location": {"path": "src/test.py", "start_line": 20}, "rule_id": "GCP_KEY", "match": {"value": "ya29..."}}
            ]
        }'''
        mock_check_output.return_value = mock_output
        
        backend = BetterleaksBackend()
        violations = backend.scan('/repo', ScanScope.TREE)
        
        self.assertEqual(len(violations), 2)
        self.assertEqual(violations[0].path, "src/main.py")
        self.assertEqual(violations[0].line, 10)
        self.assertEqual(violations[0].rule, "AWS_KEY")
        self.assertEqual(violations[0].redacted_match, "AKIA...")

    @unittest.mock.patch('subprocess.check_output')
    def test_betterleaks_scan_no_raw_secret(self, mock_check_output):
        from pre_tool_gate import BetterleaksBackend, ScanScope
        mock_output = '''{
            "scan": {"state": "complete", "files_scanned": 1},
            "findings": [
                {
                    "location": {"path": "secret.txt", "start_line": 1}, 
                    "rule_id": "SECRET", 
                    "match": {"value": "supe..."}
                }
            ]
        }'''
        mock_check_output.return_value = mock_output
        
        backend = BetterleaksBackend()
        violations = backend.scan('/repo', ScanScope.TREE)
        
        self.assertEqual(len(violations), 1)
        self.assertNotIn("super_secret_raw_text", violations[0].redacted_match)
        self.assertNotIn("super_secret_raw_text", str(violations[0]))
        self.assertEqual(violations[0].redacted_match, "supe...")

    @unittest.mock.patch('subprocess.run')
    def test_resolve_backend_fallback_to_regex(self, mock_run):
        from pre_tool_gate import resolve_backend, RegexBackend
        import subprocess
        # Simulate betterleaks not found
        mock_run.side_effect = FileNotFoundError()
        
        config = {"scanning": {"secrets": {"backend": "betterleaks"}}}
        
        import io, sys
        with io.StringIO() as fake_stderr, unittest.mock.patch('sys.stderr', fake_stderr):
            backend = resolve_backend(config)
            warning_output = fake_stderr.getvalue()
        
        self.assertIsInstance(backend, RegexBackend)
        self.assertIn("WARNING: betterleaks not found", warning_output)

    @unittest.mock.patch('subprocess.run')
    def test_resolve_backend_returns_betterleaks(self, mock_run):
        from pre_tool_gate import resolve_backend
        from pre_tool_gate import BetterleaksBackend
        # Simulate betterleaks found
        mock_run.return_value = None
        
        config = {"scanning": {"secrets": {"backend": "betterleaks", "config": "my.toml"}}}
        backend = resolve_backend(config)
        
        self.assertIsInstance(backend, BetterleaksBackend)
        self.assertEqual(backend.config_path, "my.toml")

    # WO4: Config v2 Scanning Namespace Tests
    def test_normalise_config_v1_format(self):
        v1_config = {
            "$schema": "bug-fixer-loop-config-v1",
            "leak_patterns": ["ya29\\.[A-Za-z0-9_-]+", "AKIA[0-9A-Z]{16}"],
            "scan_exclude_paths": ["vendor/", "tests/"],
        }
        norm = normalise_config(v1_config)
        self.assertIn("scanning", norm)
        self.assertIn("secrets", norm["scanning"])
        self.assertEqual(
            norm["scanning"]["secrets"]["patterns"],
            ["ya29\\.[A-Za-z0-9_-]+", "AKIA[0-9A-Z]{16}"],
        )
        self.assertEqual(
            norm["scanning"]["secrets"]["exclude_paths"],
            ["vendor/", "tests/"],
        )
        # Backwards compatibility checks
        self.assertEqual(
            norm["leak_patterns"],
            ["ya29\\.[A-Za-z0-9_-]+", "AKIA[0-9A-Z]{16}"],
        )
        self.assertEqual(
            norm["scan_exclude_paths"],
            ["vendor/", "tests/"],
        )

    def test_normalise_config_v2_format(self):
        v2_config = {
            "$schema": "bug-fixer-loop-config-v2",
            "scanning": {
                "secrets": {
                    "backend": "betterleaks",
                    "patterns": ["ya29\\.[A-Za-z0-9_-]+"],
                    "exclude_paths": ["vendor/"],
                    "betterleaks": {
                        "config_path": ".betterleaks.toml",
                    },
                    "trufflehog": {
                        "verify": False,
                    },
                },
                "injection": {
                    "enabled": False,
                    "provider": "model_armor",
                    "fail_open": True,
                    "model_armor": {
                        "project": "",
                        "location": "us-central1",
                        "service_account": "",
                    },
                },
            },
        }
        norm = normalise_config(v2_config)
        self.assertEqual(norm["scanning"], v2_config["scanning"])
        self.assertEqual(norm["scanning"]["secrets"]["backend"], "betterleaks")
        self.assertEqual(norm["scanning"]["secrets"]["patterns"], ["ya29\\.[A-Za-z0-9_-]+"])
        self.assertEqual(norm["scanning"]["secrets"]["exclude_paths"], ["vendor/"])
        self.assertEqual(
            norm["scanning"]["secrets"]["betterleaks"]["config_path"],
            ".betterleaks.toml",
        )
        self.assertFalse(norm["scanning"]["secrets"]["trufflehog"]["verify"])
        self.assertFalse(norm["scanning"]["injection"]["enabled"])

    @unittest.mock.patch("subprocess.run")
    def test_resolve_backend_reads_normalised_v2_config(self, mock_run):
        from pre_tool_gate import BetterleaksBackend, RegexBackend, resolve_backend

        mock_run.return_value = None

        v2_config = {
            "$schema": "bug-fixer-loop-config-v2",
            "scanning": {
                "secrets": {
                    "backend": "betterleaks",
                    "patterns": ["my-secret-pattern"],
                    "betterleaks": {
                        "config_path": ".betterleaks.toml",
                    },
                },
            },
        }
        backend = resolve_backend(v2_config)
        self.assertIsInstance(backend, BetterleaksBackend)
        self.assertEqual(backend.config_path, ".betterleaks.toml")

        # Fallback to regex when backend="regex" using v2 schema patterns
        v2_regex_config = {
            "scanning": {
                "secrets": {
                    "backend": "regex",
                    "patterns": ["custom-regex-pattern"],
                },
            },
        }
        regex_backend = resolve_backend(v2_regex_config)
        self.assertIsInstance(regex_backend, RegexBackend)
        self.assertEqual(regex_backend.patterns, ["custom-regex-pattern"])

    def test_gate_a_deny_betterleaks_config_files(self):
        for target in (
            ".betterleaks.toml",
            ".betterleaksignore",
            "sub/.betterleaks.toml",
            "/abs/path/.betterleaksignore",
        ):
            payload_write = {
                "toolCall": {
                    "name": "write_to_file",
                    "args": {"TargetFile": target},
                }
            }
            res_write = self._run_gate_stdin(payload_write)
            self.assertEqual(res_write["decision"], "deny")
            self.assertEqual(res_write["reason"], REASON_PROTECTED_FILE)

            payload_replace = {
                "toolCall": {
                    "name": "replace_file_content",
                    "args": {"TargetFile": target},
                }
            }
            res_replace = self._run_gate_stdin(payload_replace)
            self.assertEqual(res_replace["decision"], "deny")
            self.assertEqual(res_replace["reason"], REASON_PROTECTED_FILE)

    def test_v1_config_backward_compatibility_scan_repository(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            subprocess.run(["git", "init"], cwd=temp_dir, check=True, capture_output=True)

            leak_file = Path(temp_dir) / "leak.txt"
            leak_file.write_text("secret = AKIAIOSFODNN7EXAMPLE\n")

            test_leak_file = Path(temp_dir) / "tests" / "test_leak.txt"
            test_leak_file.parent.mkdir(parents=True, exist_ok=True)
            test_leak_file.write_text("test_secret = AKIAIOSFODNN7EXAMPLE\n")

            subprocess.run(
                ["git", "add", "leak.txt", "tests/test_leak.txt"],
                cwd=temp_dir,
                check=True,
                capture_output=True,
            )

            v1_config = {
                "$schema": "bug-fixer-loop-config-v1",
                "leak_patterns": ["AKIA[0-9A-Z]{16}"],
                "scan_exclude_paths": ["tests/"],
            }

            import pre_tool_gate

            violations, scanned, excluded = pre_tool_gate.scan_repository(
                temp_dir,
                patterns=v1_config["leak_patterns"],
                exclude_paths=v1_config["scan_exclude_paths"],
                config=v1_config,
            )
            self.assertEqual(len(violations), 1)
            self.assertIn("leak.txt", violations[0])
            self.assertEqual(excluded, 1)
            self.assertEqual(scanned, 1)


if __name__ == "__main__":
    unittest.main()


class TestPR47Fixes(unittest.TestCase):
    """Tests for fixes from PR #47 round 2."""

    def test_stdin_body_file_denied_with_title(self):
        """--body-file - combined with --title should still be denied."""
        from pre_tool_gate import check_egress_leaks
        # Even with a title, --body-file - should trigger denial
        result = check_egress_leaks(
            'gh issue create --title hello --body-file -',
            ["ya29\\.[A-Za-z0-9_-]+"]
        )
        self.assertTrue(result, "stdin body should be detected even with --title")

    def test_config_json_protected_by_gate_a(self):
        """config.json should be denied by Gate A."""
        from pre_tool_gate import evaluate_gate_a
        result = evaluate_gate_a(
            "write_to_file",
            {"TargetFile": ".agents/plugins/bug-fixer-loop-workspace/config.json"},
            "docs/design_specification.md",
            [],
        )
        self.assertEqual(result["decision"], "deny")

    def test_injection_cache_protected_by_gate_a(self):
        """injection_cache.json should be denied by Gate A."""
        from pre_tool_gate import evaluate_gate_a
        result = evaluate_gate_a(
            "write_to_file",
            {"TargetFile": ".agents/plugins/bug-fixer-loop-workspace/injection_cache.json"},
            "docs/design_specification.md",
            [],
        )
        self.assertEqual(result["decision"], "deny")

    def test_shell_mutation_allows_other_config_json(self):
        """check_shell_mutation should NOT block writes to src/app/config.json."""
        from pre_tool_gate import check_shell_mutation
        result = check_shell_mutation(
            'echo "{}" > src/app/config.json',
            "docs/design_specification.md",
            []
        )
        self.assertFalse(result, "Should not block writes to unrelated config.json files")

    def test_shell_mutation_blocks_workspace_config(self):
        """check_shell_mutation SHOULD block writes to the workspace config."""
        from pre_tool_gate import check_shell_mutation, SCANNER_PROTECTED_FILES
        # Find the config.json path in SCANNER_PROTECTED_FILES
        config_paths = [p for p in SCANNER_PROTECTED_FILES if p.endswith("config.json")]
        self.assertTrue(config_paths, "SCANNER_PROTECTED_FILES should contain a config.json path")
        for config_path in config_paths:
            result = check_shell_mutation(
                f'echo "{{}}" > {config_path}',
                "docs/design_specification.md",
                []
            )
            self.assertTrue(result, f"Should block writes to {config_path}")

    def test_betterleaks_does_not_use_default_excludes(self):
        """BetterleaksBackend should NOT apply DEFAULT_REGEX_EXCLUDE_PATHS."""
        from pre_tool_gate import BetterleaksBackend
        backend = BetterleaksBackend()
        # The scan method should not populate exclude_paths with defaults
        # We can't run betterleaks, but we can verify the attribute isn't set
        self.assertFalse(hasattr(backend, 'DEFAULT_REGEX_EXCLUDE_PATHS'))

    def test_betterleaks_version_mismatch_raises(self):
        """Version pin mismatch should raise RuntimeError."""
        from pre_tool_gate import BetterleaksBackend
        import subprocess
        # Only test if betterleaks is installed
        try:
            subprocess.run(["betterleaks", "version"], capture_output=True, check=True)
        except (FileNotFoundError, subprocess.CalledProcessError):
            self.skipTest("betterleaks not installed")
        with self.assertRaises(RuntimeError) as ctx:
            BetterleaksBackend(expected_version="0.0.0-impossible")
        self.assertIn("version mismatch", str(ctx.exception))

    def test_betterleaks_checksum_mismatch_raises(self):
        """Checksum mismatch should raise RuntimeError."""
        from pre_tool_gate import BetterleaksBackend
        import subprocess
        try:
            subprocess.run(["betterleaks", "version"], capture_output=True, check=True)
        except (FileNotFoundError, subprocess.CalledProcessError):
            self.skipTest("betterleaks not installed")
        with self.assertRaises(RuntimeError) as ctx:
            BetterleaksBackend(expected_checksum="0000000000000000000000000000000000000000000000000000000000000000")
        self.assertIn("checksum mismatch", str(ctx.exception))

    def test_betterleaks_no_pin_no_error(self):
        """Without version/checksum config, init should not raise."""
        from pre_tool_gate import BetterleaksBackend
        import subprocess
        try:
            subprocess.run(["betterleaks", "version"], capture_output=True, check=True)
        except (FileNotFoundError, subprocess.CalledProcessError):
            self.skipTest("betterleaks not installed")
        # Should not raise
        backend = BetterleaksBackend()
        self.assertEqual(backend.name, "betterleaks")
