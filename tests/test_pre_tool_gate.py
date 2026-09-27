import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from typing import Any

from scripts.pre_tool_gate import (
    REASON_APPROVAL_INJECTION,
    REASON_AUTO_MERGE,
    REASON_HOST_SCRIPT,
    REASON_ISSUE_REOPEN,
    REASON_PROTECTED_FILE,
    REASON_RAW_GH,
    REASON_SELF_APPROVAL,
    REASON_SHELL_MUTATION,
    REASON_SPEC_IMMUTABLE,
)

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
GATE_SCRIPT = PLUGIN_ROOT / "scripts" / "pre_tool_gate.py"

# Test config (config.json no longer lives in plugin root — it's in the consumer workspace)
TEST_CONFIG = {
    "spec_file_path": "docs/design_specification.md",
    "protected_files": ["docs/design_specification.md"],
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
                "args": {"TargetFile": "docs/design_specification.md"},
            }
        }
        res = self._run_gate_stdin(payload)
        self.assertEqual(res["decision"], "deny")
        self.assertEqual(res["reason"], REASON_SPEC_IMMUTABLE)

        payload_replace = {
            "toolCall": {
                "name": "replace_file_content",
                "args": {"TargetFile": "/path/to/docs/design_specification.md"},
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
            "sed -i '' 's/old/new/' docs/design_specification.md",
            "cat patch.txt > docs/design_specification.md",
            "rm docs/design_specification.md",
            "cp new.md docs/design_specification.md",
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
            "python3 scripts/safe_ingest.py issue-list",
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
                "args": {"AbsolutePath": "/path/to/docs/design_specification.md"},
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
            self.assertIn("AKIAIOSFODNN7EXAMPLE", proc.stdout)

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


if __name__ == "__main__":
    unittest.main()
