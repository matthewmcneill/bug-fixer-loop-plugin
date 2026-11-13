"""Unit tests for Antigravity 1.5.8 staged workspace plugin migration."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
DAEMON_DIR = REPO_ROOT / "daemon"
WS_PLUGIN_DIR = DAEMON_DIR / "workspace-plugin" / "bug-fixer"
WS_SCRIPTS_DIR = WS_PLUGIN_DIR / "scripts"
DAEMON_SCRIPTS_DIR = DAEMON_DIR / "scripts"

for _p in (str(WS_SCRIPTS_DIR), str(DAEMON_SCRIPTS_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import injection_scanner  # noqa: E402
import integrity_monitor  # noqa: E402
import manage  # noqa: E402
import pre_tool_gate  # noqa: E402
import safe_post  # noqa: E402
import security_audit  # noqa: E402


class TestManifestsAndRules(unittest.TestCase):
    def test_manifests_hooks_and_frontmatter(self):
        self.assertFalse((DAEMON_DIR / "hooks.json").exists())

        daemon_mf = json.loads((DAEMON_DIR / "plugin.json").read_text(encoding="utf-8"))
        ws_mf = json.loads((WS_PLUGIN_DIR / "plugin.json").read_text(encoding="utf-8"))
        self.assertEqual(daemon_mf.get("name"), "bug-fixer-daemon")
        self.assertEqual(ws_mf.get("name"), "bug-fixer")
        self.assertRegex(str(daemon_mf.get("version") or ""), r"^\d+\.\d+\.\d+")
        self.assertEqual(daemon_mf.get("version"), ws_mf.get("version"))

        hooks_text = (WS_PLUGIN_DIR / "hooks.json").read_text(encoding="utf-8")
        self.assertNotIn("workspace-plugin/bug-fixer/", hooks_text)
        hooks_data = json.loads(hooks_text)
        self.assertIn("bug-fixer-file-gate", hooks_data)
        matcher = hooks_data["bug-fixer-file-gate"]["PreToolUse"][0]["matcher"]
        for expected_tool in (
            "write_file",
            "edit_file",
            "sed_file",
            "notebook_edit",
            "write_to_file",
            "replace_file_content",
            "multi_replace_file_content",
        ):
            self.assertIn(expected_tool, matcher.split("|"))

        for group in hooks_data.values():
            for entries in group.values():
                for entry in entries:
                    for hook in entry["hooks"]:
                        self.assertIn("./scripts/", hook["command"])

        rules_text = (WS_PLUGIN_DIR / "rules" / "github-reads.md").read_text(encoding="utf-8")
        self.assertTrue(rules_text.startswith("---\n"))
        self.assertRegex(rules_text, r"\A---\n(?:.*\n)*?trigger:\s*always_on\n(?:.*\n)*?---\n")


class TestStagedDiscoveryAndFallbacks(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()).resolve()
        self.fake_home = self.tmp / "home"
        self.daemon_dir = (
            self.fake_home / ".gemini" / "config" / "plugins" / "bug-fixer-daemon"
        )
        (self.daemon_dir / "scripts").mkdir(parents=True)
        (self.daemon_dir / "scripts" / "manage.py").write_text("# manage\n", encoding="utf-8")
        (self.daemon_dir / "plugin.json").write_text(
            json.dumps({"name": "bug-fixer-daemon", "version": "0.2.0"}),
            encoding="utf-8",
        )
        self.repo_dir = self.tmp / "consumer"
        self.staged_scripts = (
            self.repo_dir / ".agents" / "plugins" / "bug-fixer" / "scripts"
        )
        self.staged_scripts.mkdir(parents=True)
        (self.repo_dir / ".agents" / "plugins" / "bug-fixer" / "plugin.json").write_text(
            json.dumps({"name": "bug-fixer", "version": "0.2.0"}),
            encoding="utf-8",
        )
        self.sc_dir = self.daemon_dir / "sidecars" / "o--r"
        self.sc_dir.mkdir(parents=True)
        self.cfg_path = self.sc_dir / "config.json"
        self.cfg_path.write_text(
            json.dumps({"target_repo": "o/r", "repo_dir": str(self.repo_dir)}),
            encoding="utf-8",
        )

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def test_find_daemon_dir_and_root_from_staged_scripts(self):
        with mock.patch("pathlib.Path.home", return_value=self.fake_home):
            self.assertEqual(
                pre_tool_gate._find_daemon_dir(self.staged_scripts),
                self.daemon_dir,
            )
            self.assertEqual(
                safe_post._find_daemon_dir(self.staged_scripts),
                self.daemon_dir,
            )
            fake_im = self.staged_scripts / "integrity_monitor.py"
            fake_im.write_text("# im\n", encoding="utf-8")
            with mock.patch.object(integrity_monitor, "__file__", str(fake_im)):
                self.assertEqual(integrity_monitor._find_daemon_root(), self.daemon_dir)

    def test_no_polluting_relative_fallbacks_when_unconfigured(self):
        staged_post = self.staged_scripts / "safe_post.py"
        staged_post.write_text("# post\n", encoding="utf-8")
        staged_audit = self.staged_scripts / "security_audit.py"
        staged_audit.write_text("# audit\n", encoding="utf-8")
        env = {k: v for k, v in os.environ.items() if k != "BUG_FIXER_CONFIG"}
        with (
            mock.patch.dict(os.environ, env, clear=True),
            mock.patch("pathlib.Path.home", return_value=self.fake_home),
            mock.patch.object(safe_post, "__file__", str(staged_post)),
            mock.patch.object(security_audit, "__file__", str(staged_audit)),
            mock.patch.object(os, "getcwd", return_value=str(self.repo_dir)),
            mock.patch.object(Path, "cwd", return_value=self.repo_dir),
        ):
            cands = safe_post._candidate_configs()
            self.assertIn(self.cfg_path, cands)

            tmp_root = Path(tempfile.gettempdir()).resolve()
            snap_file = integrity_monitor._get_snapshot_file("conv1")
            self.assertTrue(snap_file.resolve().is_relative_to(tmp_root))
            cache_path = injection_scanner._get_cache_path()
            self.assertTrue(cache_path.resolve().is_relative_to(self.sc_dir.resolve()))

        empty_home = self.tmp / "empty_home"
        empty_home.mkdir()
        other_dir = self.tmp / "other"
        other_dir.mkdir()
        with (
            mock.patch.dict(os.environ, env, clear=True),
            mock.patch("pathlib.Path.home", return_value=empty_home),
            mock.patch.object(security_audit, "__file__", str(staged_audit)),
            mock.patch.object(os, "getcwd", return_value=str(other_dir)),
            mock.patch.object(Path, "cwd", return_value=other_dir),
        ):
            tmp_root = Path(tempfile.gettempdir()).resolve()
            log_path = security_audit._get_audit_log_path()
            self.assertTrue(log_path.resolve().is_relative_to(tmp_root))
            cache_path = injection_scanner._get_cache_path()
            self.assertTrue(cache_path.resolve().is_relative_to(tmp_root))


class TestGateAAndGateBStaged(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()).resolve()
        self.repo_dir = self.tmp / "repo"
        self.repo_dir.mkdir()
        self.staged_scripts = (
            self.repo_dir / ".agents" / "plugins" / "bug-fixer" / "scripts"
        )
        self.staged_scripts.mkdir(parents=True)
        for s in ("safe_ingest.py", "safe_post.py", "pre_tool_gate.py"):
            (self.staged_scripts / s).write_text("# host\n", encoding="utf-8")

        self.daemon_dir = self.tmp / "daemon"
        (self.daemon_dir / "scripts").mkdir(parents=True)
        (self.daemon_dir / "scripts" / "manage.py").write_text("# manage\n", encoding="utf-8")
        (self.daemon_dir / "plugin.json").write_text(
            json.dumps({"name": "bug-fixer-daemon", "version": "0.2.0"}),
            encoding="utf-8",
        )
        daemon_ws_scripts = (
            self.daemon_dir / "workspace-plugin" / "bug-fixer" / "scripts"
        )
        daemon_ws_scripts.mkdir(parents=True)
        (daemon_ws_scripts / "safe_ingest.py").write_text("# host\n", encoding="utf-8")
        (daemon_ws_scripts / "safe_post.py").write_text("# host\n", encoding="utf-8")

        self.cfg_path = self.daemon_dir / "sidecars" / "o--r" / "config.json"
        self.cfg_path.parent.mkdir(parents=True)
        self.cfg = {
            "target_repo": "o/r",
            "repo_dir": str(self.repo_dir),
            "spec_file_path": "docs/spec.md",
            "protected_files": ["docs/spec.md"],
            "_loaded_from": str(self.cfg_path),
        }
        self.cfg_path.write_text(json.dumps(self.cfg), encoding="utf-8")

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def test_gate_a_protects_staged_files_across_modern_tools(self):
        rule_target = self.repo_dir / ".agents" / "plugins" / "bug-fixer" / "rules" / "r.md"
        cases = [
            ("write_file", {"Path": ".agents/plugins/bug-fixer/hooks.json"}),
            ("edit_file", {"file_path": ".agents/plugins/bug-fixer/plugin.json"}),
            ("sed_file", {"TargetFile": ".agents/plugins/bug-fixer/scripts/pre_tool_gate.py"}),
            ("notebook_edit", {"NotebookPath": str(self.repo_dir / ".agents" / "plugins.json")}),
            ("write_file", {"Path": str(rule_target)}),
        ]
        for tool_name, args in cases:
            with self.subTest(tool=tool_name, args=args):
                res = pre_tool_gate.evaluate_pretool_call(
                    {
                        "toolCall": {"name": tool_name, "args": args},
                        "workspacePaths": [str(self.repo_dir)],
                    },
                    config=self.cfg,
                )
                self.assertEqual(res["decision"], "deny")

    def test_gate_b_trusts_both_staged_and_daemon_host_scripts(self):
        daemon_ingest = (
            self.daemon_dir / "workspace-plugin" / "bug-fixer" / "scripts" / "safe_ingest.py"
        )
        evil_ingest = self.tmp / "safe_ingest.py"
        evil_ingest.write_text("# evil\n", encoding="utf-8")
        with (
            mock.patch.object(pre_tool_gate, "_SCRIPTS_DIR", self.staged_scripts),
            mock.patch.object(
                pre_tool_gate, "_find_daemon_dir", return_value=self.daemon_dir
            ),
        ):
            for allowed_cmd in (
                "python3 .agents/plugins/bug-fixer/scripts/safe_ingest.py issue-list",
                f"python3 {daemon_ingest} issue-list",
            ):
                res = pre_tool_gate.evaluate_pretool_call(
                    {
                        "toolCall": {
                            "name": "run_command",
                            "args": {
                                "CommandLine": allowed_cmd,
                                "Cwd": str(self.repo_dir),
                            },
                        },
                        "workspacePaths": [str(self.repo_dir)],
                    },
                    config=self.cfg,
                )
                self.assertEqual(res["decision"], "allow", allowed_cmd)

            blocked = pre_tool_gate.evaluate_pretool_call(
                {
                    "toolCall": {
                        "name": "run_command",
                        "args": {
                            "CommandLine": f"python3 {evil_ingest} issue-list",
                            "Cwd": str(self.repo_dir),
                        },
                    },
                    "workspacePaths": [str(self.repo_dir)],
                },
                config=self.cfg,
            )
            self.assertEqual(blocked["decision"], "deny")

    def test_doctor_and_lifecycle_script_gating(self):
        plugin_checkout = self.tmp / "bug-fixer-repo"
        (plugin_checkout / "daemon" / "scripts").mkdir(parents=True)
        (plugin_checkout / "daemon" / "plugin.json").write_text(
            json.dumps({"name": "bug-fixer-daemon", "version": "0.2.0"}),
            encoding="utf-8",
        )
        (plugin_checkout / "daemon" / "scripts" / "manage.py").write_text(
            "# manage\n", encoding="utf-8"
        )
        (plugin_checkout / "daemon" / "workspace-plugin" / "bug-fixer").mkdir(parents=True)
        (
            plugin_checkout / "daemon" / "workspace-plugin" / "bug-fixer" / "plugin.json"
        ).write_text(json.dumps({"name": "bug-fixer", "version": "0.2.0"}), encoding="utf-8")
        (plugin_checkout / "scripts").mkdir(parents=True)
        (plugin_checkout / "scripts" / "install.sh").write_text(
            "#!/bin/sh\n", encoding="utf-8"
        )

        # Unrelated consumer repo's own scripts/install.sh must NOT be misclassified
        (self.repo_dir / "scripts").mkdir()
        (self.repo_dir / "scripts" / "install.sh").write_text(
            "#!/bin/sh\n", encoding="utf-8"
        )

        manage_bin = self.daemon_dir / "scripts" / "manage.py"
        with (
            mock.patch.object(pre_tool_gate, "_SCRIPTS_DIR", self.staged_scripts),
            mock.patch.object(
                pre_tool_gate, "_find_daemon_dir", return_value=self.daemon_dir
            ),
            mock.patch.object(pre_tool_gate, "_is_autonomous_session", return_value=False),
        ):
            res_ro = pre_tool_gate.evaluate_pretool_call(
                {
                    "toolCall": {
                        "name": "run_command",
                        "args": {"CommandLine": f"python3 {manage_bin} doctor"},
                    },
                    "conversationId": "interactive",
                },
                config=self.cfg,
            )
            self.assertEqual(res_ro["decision"], "allow")

            res_fix = pre_tool_gate.evaluate_pretool_call(
                {
                    "toolCall": {
                        "name": "run_command",
                        "args": {"CommandLine": f"python3 {manage_bin} doctor --fix"},
                    },
                    "conversationId": "interactive",
                },
                config=self.cfg,
            )
            self.assertEqual(res_fix["decision"], "force_ask")

            res_install = pre_tool_gate.evaluate_pretool_call(
                {
                    "toolCall": {
                        "name": "run_command",
                        "args": {
                            "CommandLine": "./scripts/install.sh",
                            "Cwd": str(plugin_checkout),
                        },
                    },
                    "conversationId": "interactive",
                },
                config=self.cfg,
            )
            self.assertEqual(res_install["decision"], "force_ask")

            res_chained = pre_tool_gate.evaluate_pretool_call(
                {
                    "toolCall": {
                        "name": "run_command",
                        "args": {
                            "CommandLine": "./scripts/install.sh && echo pwned",
                            "Cwd": str(plugin_checkout),
                        },
                    },
                    "conversationId": "interactive",
                },
                config=self.cfg,
            )
            self.assertEqual(res_chained["decision"], "deny")

            res_consumer = pre_tool_gate.evaluate_pretool_call(
                {
                    "toolCall": {
                        "name": "run_command",
                        "args": {
                            "CommandLine": "./scripts/install.sh",
                            "Cwd": str(self.repo_dir),
                        },
                    },
                    "conversationId": "interactive",
                },
                config=self.cfg,
            )
            self.assertEqual(res_consumer["decision"], "allow")

        with (
            mock.patch.object(pre_tool_gate, "_SCRIPTS_DIR", self.staged_scripts),
            mock.patch.object(
                pre_tool_gate, "_find_daemon_dir", return_value=self.daemon_dir
            ),
            mock.patch.object(pre_tool_gate, "_is_autonomous_session", return_value=True),
        ):
            res_auto_fix = pre_tool_gate.evaluate_pretool_call(
                {
                    "toolCall": {
                        "name": "run_command",
                        "args": {"CommandLine": f"python3 {manage_bin} doctor --fix"},
                    },
                    "conversationId": "auto-1",
                },
                config=self.cfg,
            )
            self.assertEqual(res_auto_fix["decision"], "deny")

            res_auto_install = pre_tool_gate.evaluate_pretool_call(
                {
                    "toolCall": {
                        "name": "run_command",
                        "args": {
                            "CommandLine": "./scripts/install.sh",
                            "Cwd": str(plugin_checkout),
                        },
                    },
                    "conversationId": "auto-1",
                },
                config=self.cfg,
            )
            self.assertEqual(res_auto_install["decision"], "deny")


class TestLeakExclusions(unittest.TestCase):
    def test_normalise_rel_scan_path_preserves_dot_agents(self):
        self.assertEqual(
            pre_tool_gate._normalise_rel_scan_path(
                "./.agents/plugins/bug-fixer/scripts/pre_tool_gate.py"
            ),
            ".agents/plugins/bug-fixer/scripts/pre_tool_gate.py",
        )
        self.assertIn(
            ".agents/plugins/bug-fixer/",
            pre_tool_gate.RegexBackend.DEFAULT_REGEX_EXCLUDE_PATHS,
        )

    def test_betterleaks_skips_staged_plugin_dir(self):
        tmpdir = Path(tempfile.mkdtemp()).resolve()
        try:
            findings = [
                {
                    "File": "./.agents/plugins/bug-fixer/hooks.json",
                    "StartLine": 1,
                    "RuleID": "test-rule",
                    "Match": "REDACTED",
                },
                {
                    "File": str(tmpdir / ".agents" / "plugins" / "bug-fixer" / "scripts" / "x.py"),
                    "StartLine": 2,
                    "RuleID": "test-rule",
                    "Match": "REDACTED",
                },
                {
                    "File": "src/app.py",
                    "StartLine": 9,
                    "RuleID": "test-rule",
                    "Match": "REDACTED",
                },
            ]
            fake_proc = mock.Mock(returncode=1, stdout=json.dumps(findings), stderr="")
            backend = pre_tool_gate.BetterleaksBackend()
            with (
                mock.patch("subprocess.run", return_value=fake_proc),
                mock.patch("shutil.which", return_value="/usr/local/bin/betterleaks"),
            ):
                violations = backend.scan(str(tmpdir), pre_tool_gate.ScanScope.TREE)
            self.assertEqual(backend.excluded_count, 2)
            self.assertEqual(len(violations), 1)
            self.assertEqual(violations[0].path, "src/app.py")
        finally:
            shutil.rmtree(tmpdir)


class TestManageStagingAndDoctor(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()).resolve()
        self.daemon = self.tmp / "daemon"
        (self.daemon / "scripts").mkdir(parents=True)
        (self.daemon / "plugin.json").write_text(
            json.dumps({"name": "bug-fixer-daemon", "version": "0.2.0"}),
            encoding="utf-8",
        )
        ws = self.daemon / "workspace-plugin" / "bug-fixer"
        (ws / "scripts").mkdir(parents=True)
        (ws / "scripts" / "__pycache__").mkdir()
        (ws / "scripts" / "__pycache__" / "x.pyc").write_bytes(b"pyc")
        (ws / "plugin.json").write_text(
            json.dumps({"name": "bug-fixer", "version": "0.2.0"}),
            encoding="utf-8",
        )
        (ws / "hooks.json").write_text(
            json.dumps(
                {
                    "gate": {
                        "PreToolUse": [
                            {
                                "matcher": "write_file",
                                "hooks": [
                                    {"type": "command", "command": "python3 ./scripts/g.py"}
                                ],
                            }
                        ]
                    }
                }
            ),
            encoding="utf-8",
        )
        (ws / "scripts" / "g.py").write_text("print('hi')\n", encoding="utf-8")

        self.repo = self.tmp / "repo"
        self.repo.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=self.repo, check=True)
        subprocess.run(
            ["git", "config", "user.email", "test@example.com"], cwd=self.repo, check=True
        )
        subprocess.run(
            ["git", "config", "user.name", "Tester"], cwd=self.repo, check=True
        )
        (self.repo / "README.md").write_text("hi\n", encoding="utf-8")
        subprocess.run(["git", "add", "README.md"], cwd=self.repo, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=self.repo, check=True)

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def test_enroll_doctor_worktree_and_unenroll(self):
        wt = self.tmp / "wt"
        subprocess.run(
            ["git", "-C", str(self.repo), "worktree", "add", "-q", str(wt), "-b", "wt-branch"],
            check=True,
        )
        sibling_plugins_json = wt / ".agents" / "plugins.json"
        sibling_plugins_json.parent.mkdir(parents=True)
        sibling_plugins_json.write_text(
            json.dumps(
                {
                    "entries": [
                        {
                            "path": "~/.gemini/config/plugins/bug-fixer-daemon/"
                            "workspace-plugin/bug-fixer"
                        },
                        {"path": "~/.gemini/plugins/other-plugin"},
                    ]
                }
            ),
            encoding="utf-8",
        )
        with (
            mock.patch.object(manage, "DAEMON_DIR", self.daemon),
            mock.patch.object(manage, "SIDECARS_DIR", self.daemon / "sidecars"),
        ):
            rc = manage.cmd_enroll(
                argparse.Namespace(repo="o/r", dir=str(wt), project_id="proj-1")
            )
            self.assertEqual(rc, 0)
            staged = wt / ".agents" / "plugins" / "bug-fixer"
            self.assertTrue(staged.is_dir())
            self.assertFalse(staged.is_symlink())
            self.assertFalse((staged / "scripts" / "__pycache__").exists())
            pj = json.loads(sibling_plugins_json.read_text(encoding="utf-8"))
            self.assertEqual(pj["entries"], [{"path": "~/.gemini/plugins/other-plugin"}])

            exclude = manage._resolve_git_exclude_path(wt)
            self.assertIsNotNone(exclude)
            exc_lines = set(exclude.read_text(encoding="utf-8").splitlines())
            self.assertIn(".agents/plugins/bug-fixer/", exc_lines)
            self.assertIn(".agents/plugins.json", exc_lines)

            # Introduce drift, symlink, stray daemon/hooks.json, and empty plugins.json
            (self.daemon / "hooks.json").write_text("{}\n", encoding="utf-8")
            shutil.rmtree(staged)
            target_dir = self.tmp / "symlink-target"
            target_dir.mkdir()
            (target_dir / "untouched.txt").write_text("safe", encoding="utf-8")
            os.symlink(target_dir, staged)
            sibling_plugins_json.write_text(json.dumps({"entries": []}), encoding="utf-8")

            self.assertEqual(
                manage.cmd_doctor(argparse.Namespace(fix=False, repo=None)), 1
            )
            self.assertEqual(
                manage.cmd_doctor(argparse.Namespace(fix=True, repo=None)), 0
            )
            self.assertFalse((self.daemon / "hooks.json").exists())
            self.assertTrue(staged.is_dir())
            self.assertFalse(staged.is_symlink())
            self.assertFalse(sibling_plugins_json.exists())
            self.assertEqual((target_dir / "untouched.txt").read_text(encoding="utf-8"), "safe")
            self.assertEqual(
                manage.cmd_doctor(argparse.Namespace(fix=False, repo=None)), 0
            )

            # Refresh updates drifted staged file
            (staged / "hooks.json").write_text("{}", encoding="utf-8")
            self.assertEqual(
                manage.cmd_doctor(argparse.Namespace(fix=False, repo=None)), 1
            )
            self.assertEqual(manage.cmd_refresh(argparse.Namespace()), 0)
            self.assertEqual(
                manage.cmd_doctor(argparse.Namespace(fix=False, repo=None)), 0
            )

            # Unenroll removes staged dir cleanly
            self.assertEqual(manage.cmd_unenroll(argparse.Namespace(repo="o/r")), 0)
            self.assertFalse(staged.exists())


class TestIntegrityMonitorStagedAndLifecycle(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()).resolve()
        self.plugin_repo = self.tmp / "bug-fixer-loop-plugin"
        daemon = self.plugin_repo / "daemon"
        (daemon / "scripts").mkdir(parents=True)
        (daemon / "scripts" / "manage.py").write_text("# manage\n", encoding="utf-8")
        (daemon / "plugin.json").write_text(
            json.dumps({"name": "bug-fixer-daemon", "version": "0.2.0"}),
            encoding="utf-8",
        )
        ws = daemon / "workspace-plugin" / "bug-fixer"
        (ws / "scripts").mkdir(parents=True)
        (ws / "plugin.json").write_text(
            json.dumps({"name": "bug-fixer", "version": "0.2.0"}),
            encoding="utf-8",
        )
        (ws / "hooks.json").write_text('{"a": {}}\n', encoding="utf-8")
        (daemon / "hooks.json").write_text('{"legacy": {}}\n', encoding="utf-8")
        (self.plugin_repo / "scripts").mkdir()
        (self.plugin_repo / "scripts" / "install.sh").write_text(
            "#!/bin/sh\n", encoding="utf-8"
        )

        self.consumer = self.tmp / "consumer"
        self.staged = self.consumer / ".agents" / "plugins" / "bug-fixer"
        (self.staged / "scripts").mkdir(parents=True)
        (self.staged / "hooks.json").write_text('{"old": {}}\n', encoding="utf-8")
        (self.consumer / ".agents" / "plugins.json").write_text(
            json.dumps({"entries": []}), encoding="utf-8"
        )

        self.sidecar_dir = daemon / "sidecars" / "o--r"
        self.sidecar_dir.mkdir(parents=True)
        self.cfg_path = self.sidecar_dir / "config.json"
        self.cfg_path.write_text(
            json.dumps({"target_repo": "o/r", "repo_dir": str(self.consumer)}),
            encoding="utf-8",
        )

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def test_verify_accepts_staged_manage_and_lifecycle_and_reverts_tampering(self):
        daemon = self.plugin_repo / "daemon"
        manage_py = daemon / "scripts" / "manage.py"
        with (
            mock.patch.dict(os.environ, {"BUG_FIXER_CONFIG": str(self.cfg_path)}),
            mock.patch.object(integrity_monitor, "_find_daemon_root", return_value=daemon),
            mock.patch.object(integrity_monitor, "_is_autonomous_session", return_value=False),
        ):
            integrity_monitor.snapshot("c1")
            (self.staged / "hooks.json").write_text('{"new": {}}\n', encoding="utf-8")
            (self.consumer / ".agents" / "plugins.json").unlink()
            integrity_monitor.verify(
                "c1",
                stdin_data={
                    "toolCall": {
                        "name": "run_command",
                        "args": {"CommandLine": f"python3 {manage_py} doctor --fix"},
                    }
                },
            )
            self.assertEqual(
                (self.staged / "hooks.json").read_text(encoding="utf-8"), '{"new": {}}\n'
            )
            self.assertFalse((self.consumer / ".agents" / "plugins.json").exists())

            # Interactive ./scripts/install.sh may delete daemon/hooks.json & update staged files
            integrity_monitor.snapshot("c2")
            (daemon / "hooks.json").unlink()
            (self.staged / "hooks.json").write_text('{"v2": {}}\n', encoding="utf-8")
            integrity_monitor.verify(
                "c2",
                stdin_data={
                    "workspacePaths": [str(self.plugin_repo)],
                    "toolCall": {
                        "name": "run_command",
                        "args": {"CommandLine": "./scripts/install.sh"},
                    },
                },
            )
            self.assertFalse((daemon / "hooks.json").exists())
            self.assertEqual(
                (self.staged / "hooks.json").read_text(encoding="utf-8"), '{"v2": {}}\n'
            )

            # Tampering is reverted
            integrity_monitor.snapshot("c3")
            (self.staged / "hooks.json").write_text("TAMPERED", encoding="utf-8")
            integrity_monitor.verify(
                "c3",
                stdin_data={
                    "toolCall": {
                        "name": "run_command",
                        "args": {"CommandLine": "python3 /tmp/evil.py"},
                    }
                },
            )
            self.assertEqual(
                (self.staged / "hooks.json").read_text(encoding="utf-8"), '{"v2": {}}\n'
            )
