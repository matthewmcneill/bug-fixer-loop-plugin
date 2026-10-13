"""Unit tests for WO2b changes: config and path resolution fixes across scripts."""

import json
import os
import shutil
import tempfile
import unittest
import unittest.mock
from unittest.mock import patch
from pathlib import Path
import sys

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "daemon" / "workspace-plugin" / "bug-fixer" / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import integrity_monitor
import safe_post
import injection_scanner
import safe_ingest
import bugfixer_api


class TestWO2bIntegrityMonitor(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.tmp_path = Path(os.path.realpath(self.tmp))
        self.sidecar_dir = self.tmp_path / "sidecar"
        self.sidecar_dir.mkdir()
        self.config_path = self.sidecar_dir / "config.json"
        self.config_path.write_text(json.dumps({"target_repo": "owner/repo"}))

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def test_get_snapshot_file_uses_env(self):
        with unittest.mock.patch.dict(os.environ, {"BUG_FIXER_CONFIG": str(self.config_path)}):
            snap_file = integrity_monitor._get_snapshot_file()
            self.assertEqual(snap_file, self.sidecar_dir / ".integrity_snapshot.json")

    def test_snapshot_and_verify_revert(self):
        # Create a dummy protected file inside sidecar_dir
        protected_file = self.sidecar_dir / "injection_cache.json"
        protected_file.write_text('{"initial": "data"}')

        with unittest.mock.patch.dict(os.environ, {"BUG_FIXER_CONFIG": str(self.config_path)}):
            # Take snapshot
            integrity_monitor.snapshot()

            snap_file = integrity_monitor._get_snapshot_file()
            snap_dir = integrity_monitor._get_snapshot_dir()
            self.assertTrue(snap_file.is_file())
            self.assertTrue(snap_dir.is_dir())

            # Tamper with the protected file
            protected_file.write_text('{"tampered": "hacked"}')

            # Verify and revert
            integrity_monitor.verify()

            # The file should be restored to its initial contents
            self.assertEqual(protected_file.read_text(), '{"initial": "data"}')

            # Snapshot and snapshot dir should be cleaned up
            self.assertFalse(snap_file.exists())
            self.assertFalse(snap_dir.exists())

    def test_verify_allows_sidecar_change_from_manage_enroll(self):
        """sidecar.json changes are accepted when command was manage.py enroll."""
        sidecar_file = self.sidecar_dir / "sidecar.json"
        sidecar_file.write_text('{"env": {}}')

        manage_payload = {
            "toolCall": {
                "name": "run_command",
                "args": {"CommandLine": "python3 ~/.gemini/config/plugins/bug-fixer-daemon/scripts/manage.py enroll --repo o/r --dir /tmp --project-id abc"}
            }
        }

        with unittest.mock.patch.dict(os.environ, {"BUG_FIXER_CONFIG": str(self.config_path)}):
            integrity_monitor.snapshot()
            # Legitimate mutation
            sidecar_file.write_text('{"env": {"ANTIGRAVITY_PROJECT_ID": "abc"}}')
            integrity_monitor.verify(stdin_data=manage_payload)
            # Should NOT be reverted
            data = json.loads(sidecar_file.read_text())
            self.assertEqual(data["env"]["ANTIGRAVITY_PROJECT_ID"], "abc")

    def test_verify_still_reverts_security_script_even_with_manage_command(self):
        """Security scripts are ALWAYS reverted, even if command was manage.py."""
        # Use the config file itself as a proxy for a "security-tier" protected file
        # that is NOT in MANAGEMENT_MUTABLE_BASENAMES — we need a file the monitor
        # actually tracks. Create a fake script file.
        scripts_dir = self.tmp_path / "scripts"
        scripts_dir.mkdir()
        fake_script = scripts_dir / "pre_tool_gate.py"
        fake_script.write_text("# original")

        # Patch _get_protected_files to include our fake script
        original_get = integrity_monitor._get_protected_files

        def patched_get():
            return original_get() + [str(fake_script)]

        manage_payload = {
            "toolCall": {
                "name": "run_command",
                "args": {"CommandLine": "python3 manage.py config --set key=val --repo o/r"}
            }
        }

        with unittest.mock.patch.dict(os.environ, {"BUG_FIXER_CONFIG": str(self.config_path)}):
            with unittest.mock.patch.object(integrity_monitor, "_get_protected_files", patched_get):
                integrity_monitor.snapshot()
                fake_script.write_text("# tampered")
                integrity_monitor.verify(stdin_data=manage_payload)
                # Security script MUST be reverted
                self.assertEqual(fake_script.read_text(), "# original")

    def test_verify_reverts_sidecar_when_not_manage_command(self):
        """sidecar.json changes are reverted when command is NOT manage.py."""
        sidecar_file = self.sidecar_dir / "sidecar.json"
        sidecar_file.write_text('{"env": {}}')

        evil_payload = {
            "toolCall": {
                "name": "run_command",
                "args": {"CommandLine": "python3 /tmp/evil.py"}
            }
        }

        with unittest.mock.patch.dict(os.environ, {"BUG_FIXER_CONFIG": str(self.config_path)}):
            integrity_monitor.snapshot()
            sidecar_file.write_text('{"env": {"TAMPERED": true}}')
            integrity_monitor.verify(stdin_data=evil_payload)
            # Must be reverted
            self.assertEqual(json.loads(sidecar_file.read_text()), {"env": {}})

    def test_verify_reverts_sidecar_without_stdin_data(self):
        """Backward compat: no stdin_data means all changes are reverted."""
        sidecar_file = self.sidecar_dir / "sidecar.json"
        sidecar_file.write_text('{"env": {}}')

        with unittest.mock.patch.dict(os.environ, {"BUG_FIXER_CONFIG": str(self.config_path)}):
            integrity_monitor.snapshot()
            sidecar_file.write_text('{"env": {"TAMPERED": true}}')
            integrity_monitor.verify()  # no stdin_data
            # Must be reverted
            self.assertEqual(json.loads(sidecar_file.read_text()), {"env": {}})

    def test_verify_reverts_when_manage_spoofed_in_comment_or_compound(self):
        """Spoofed manage.py tokens in comments, compounds, or -c flags must NOT be trusted."""
        sidecar_file = self.sidecar_dir / "sidecar.json"
        spoofed_commands = [
            "python3 /tmp/evil.py # manage.py config",
            "echo manage.py config && python3 /tmp/evil.py",
            "python3 -c 'open(\"sidecar.json\",\"w\")' manage.py config",
            "python3 manage.py config $(/tmp/evil.sh)",
            "python3 /tmp/manage.py enroll --repo o/r",
            "PATH=/tmp python3 ~/.gemini/config/plugins/bug-fixer-daemon/scripts/manage.py enroll",
            "env FOO=1 python3 ~/.gemini/config/plugins/bug-fixer-daemon/scripts/manage.py enroll",
            "python3 ~/.gemini/config/plugins/bug-fixer-daemon/scripts/manage.py config --show",
            "python3 ~/.gemini/config/plugins/bug-fixer-daemon/scripts/manage.py status",
        ]
        for cmd in spoofed_commands:
            with self.subTest(cmd=cmd):
                sidecar_file.write_text('{"env": {}}')
                payload = {
                    "toolCall": {
                        "name": "run_command",
                        "args": {"CommandLine": cmd},
                    }
                }
                with unittest.mock.patch.dict(os.environ, {"BUG_FIXER_CONFIG": str(self.config_path)}):
                    integrity_monitor.snapshot()
                    sidecar_file.write_text('{"env": {"TAMPERED": true}}')
                    integrity_monitor.verify(stdin_data=payload)
                    self.assertEqual(json.loads(sidecar_file.read_text()), {"env": {}})

    def test_was_trusted_management_command_canonical_paths(self):
        """Canonical manage.py paths with mutating subcommands are trusted."""
        repo_manage = (
            Path(__file__).resolve().parent.parent / "daemon" / "scripts" / "manage.py"
        )
        valid_commands = [
            f"python3 {repo_manage} enroll --repo o/r --dir /tmp --project-id p1",
            f"python3 -I -u {repo_manage} config --set engineering_interval_min=15 --repo o/r",
            "python3 ~/.gemini/config/plugins/bug-fixer-daemon/scripts/manage.py enroll --repo o/r --dir /tmp --project-id p1",
            "python3 ~/.gemini/config/plugins/bug-fixer-daemon/scripts/manage.py config --set=max_concurrent_agents=3 --repo o/r",
        ]
        for cmd in valid_commands:
            with self.subTest(cmd=cmd):
                payload = {
                    "toolCall": {
                        "name": "run_command",
                        "args": {"CommandLine": cmd},
                    }
                }
                self.assertTrue(integrity_monitor._was_trusted_management_command(payload))

    def test_protected_files_includes_repo_spec_protected_and_plugins_json(self):
        """_get_protected_files includes spec_file_path, protected_files, and .agents/plugins.json."""
        repo_dir = self.tmp_path / "target_repo"
        (repo_dir / "docs").mkdir(parents=True)
        (repo_dir / "src").mkdir(parents=True)
        (repo_dir / ".agents").mkdir(parents=True)
        spec_file = repo_dir / "docs" / "spec.md"
        prot_file = repo_dir / "src" / "critical.py"
        plugins_file = repo_dir / ".agents" / "plugins.json"
        spec_file.write_text("# Spec")
        prot_file.write_text("# Critical")
        plugins_file.write_text("{}")

        daemon_dir = self.tmp_path / "daemon"
        sc_dir = daemon_dir / "sidecars" / "owner--repo"
        sc_dir.mkdir(parents=True)
        (daemon_dir / "plugin.json").write_text(json.dumps({"name": "bug-fixer-daemon"}))
        cfg_file = sc_dir / "config.json"
        cfg_file.write_text(json.dumps({
            "target_repo": "owner/repo",
            "repo_dir": str(repo_dir),
            "spec_file_path": "docs/spec.md",
            "protected_files": ["src/critical.py"],
        }))

        with unittest.mock.patch.dict(os.environ, {"BUG_FIXER_CONFIG": ""}), \
             unittest.mock.patch("integrity_monitor._find_daemon_root", return_value=daemon_dir):
            monitored = integrity_monitor._get_protected_files(workspace_paths=[str(repo_dir)])
            self.assertIn(str(spec_file.resolve()), monitored)
            self.assertIn(str(prot_file.resolve()), monitored)
            self.assertIn(str(plugins_file.resolve()), monitored)
            snap_file = integrity_monitor._get_snapshot_file("conv1", workspace_paths=[str(repo_dir)])
            self.assertEqual(snap_file, sc_dir.resolve() / ".integrity_snapshot.conv1.json")

    def test_verify_logs_security_event_when_snapshot_missing(self):
        """verify() logs integrity_snapshot_missing security event if snapshot file was deleted."""
        with unittest.mock.patch.dict(os.environ, {"BUG_FIXER_CONFIG": str(self.config_path)}):
            snap_file = integrity_monitor._get_snapshot_file("missing-conv")
            snap_file.unlink(missing_ok=True)
            with unittest.mock.patch("security_audit.log_security_event") as mock_log:
                integrity_monitor.verify(
                    "missing-conv",
                    stdin_data={"toolCall": {"name": "run_command", "args": {"CommandLine": "rm -f x"}}},
                )
                mock_log.assert_called_once()
                self.assertEqual(mock_log.call_args[0][0], "integrity_snapshot_missing")

    def test_find_sidecar_config_deterministic_sorted_order(self):
        """_find_sidecar_config iterates sidecars in deterministic sorted order."""
        repo_dir = self.tmp_path / "shared_repo"
        repo_dir.mkdir(parents=True)
        daemon_dir = self.tmp_path / "daemon"
        for slug in ("z-sidecar", "a-sidecar"):
            sc = daemon_dir / "sidecars" / slug
            sc.mkdir(parents=True)
            (sc / "config.json").write_text(json.dumps({
                "target_repo": f"org/{slug}",
                "repo_dir": str(repo_dir),
            }))
        (daemon_dir / "plugin.json").write_text(json.dumps({"name": "bug-fixer-daemon"}))

        with unittest.mock.patch.dict(os.environ, {"BUG_FIXER_CONFIG": ""}), \
             unittest.mock.patch("integrity_monitor._find_daemon_root", return_value=daemon_dir):
            found = integrity_monitor._find_sidecar_config(workspace_paths=[str(repo_dir)])
            self.assertEqual(found, (daemon_dir / "sidecars" / "a-sidecar" / "config.json").resolve())

    def test_scan_with_builtin_floor_deduplicates_violations(self):
        """_scan_with_builtin_floor deduplicates violations across configured backend and built-in floor."""
        import pre_tool_gate

        v_shared = pre_tool_gate.ScanViolation(
            path="leak.py",
            line=1,
            rule=pre_tool_gate.BUILTIN_LEAK_PATTERNS[0],
            redacted_match="ya29…",
        )
        v_custom = pre_tool_gate.ScanViolation(
            path="leak.py",
            line=2,
            rule="CUSTOM_RULE",
            redacted_match="CUST…",
        )

        class FakeBackend:
            name = "fake"
            version = "1.0"

            def scan(self, repo_dir, scope, exclude_paths=None):
                return [v_shared, v_custom]

            def config_hash(self):
                return "deadbeef"

        with unittest.mock.patch.object(
            pre_tool_gate.RegexBackend, "scan", return_value=[v_shared]
        ):
            res = pre_tool_gate._scan_with_builtin_floor(
                str(self.tmp_path),
                pre_tool_gate.ScanScope.TREE,
                backend=FakeBackend(),
            )
            self.assertEqual(res, [v_shared, v_custom])


class TestWO1ManageConfigAllowlist(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.tmp_path = Path(os.path.realpath(self.tmp))
        self.daemon_dir = self.tmp_path / "daemon"
        self.sc_dir = self.daemon_dir / "sidecars" / "owner--repo"
        self.sc_dir.mkdir(parents=True)
        self.cfg_path = self.sc_dir / "config.json"
        self.cfg_path.write_text(json.dumps({
            "target_repo": "owner/repo",
            "maintainer": "alice",
            "protected_files": ["secret.txt"],
            "engineering_interval_min": 30,
        }))

        manage_dir = Path(__file__).resolve().parent.parent / "daemon" / "scripts"
        if str(manage_dir) not in sys.path:
            sys.path.insert(0, str(manage_dir))
        import manage as manage_mod
        self.manage = manage_mod

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def test_config_set_operational_key_succeeds(self):
        import argparse
        args = argparse.Namespace(
            repo="owner/repo",
            show=False,
            set="engineering_interval_min=20",
        )
        with unittest.mock.patch.object(self.manage, "DAEMON_DIR", self.daemon_dir), \
             unittest.mock.patch.object(self.manage, "SIDECARS_DIR", self.daemon_dir / "sidecars"):
            rc = self.manage.cmd_config(args)
        self.assertEqual(rc, 0)
        updated = json.loads(self.cfg_path.read_text())
        self.assertEqual(updated["engineering_interval_min"], 20)

    def test_config_set_security_critical_keys_rejected(self):
        import argparse
        blocked_kv = [
            "protected_files=[]",
            "maintainer=evil",
            "leak_patterns=[]",
            "spec_file_path=foo",
            "trusted_users=[\"evil\"]",
        ]
        for kv in blocked_kv:
            with self.subTest(kv=kv):
                args = argparse.Namespace(
                    repo="owner/repo",
                    show=False,
                    set=kv,
                )
                with unittest.mock.patch.object(self.manage, "DAEMON_DIR", self.daemon_dir), \
                     unittest.mock.patch.object(self.manage, "SIDECARS_DIR", self.daemon_dir / "sidecars"):
                    rc = self.manage.cmd_config(args)
                    self.assertEqual(rc, 1)
                # Ensure original config was not modified
                current = json.loads(self.cfg_path.read_text())
                self.assertEqual(current["maintainer"], "alice")
                self.assertEqual(current["protected_files"], ["secret.txt"])


class TestWO2bSafePost(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.tmp_path = Path(os.path.realpath(self.tmp))
        self.config_path = self.tmp_path / "config.json"
        self.config_path.write_text(json.dumps({"leak_patterns": ["SECRET_KEY_[0-9]+"]}))

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def test_candidate_configs_uses_env_first(self):
        with unittest.mock.patch.dict(os.environ, {"BUG_FIXER_CONFIG": str(self.config_path)}):
            candidates = safe_post._candidate_configs()
            self.assertTrue(len(candidates) > 0)
            self.assertEqual(candidates[0], self.config_path)


class TestWO2bInjectionScanner(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.tmp_path = Path(os.path.realpath(self.tmp))
        self.config_path = self.tmp_path / "config.json"
        self.config_path.write_text(json.dumps({}))

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def test_get_cache_path_from_env(self):
        with unittest.mock.patch.dict(os.environ, {"BUG_FIXER_CONFIG": str(self.config_path)}):
            cache_path = injection_scanner._get_cache_path()
            self.assertEqual(cache_path, self.tmp_path / "injection_cache.json")

    def test_get_cache_path_from_param(self):
        cache_path = injection_scanner._get_cache_path(str(self.config_path))
        self.assertEqual(cache_path, self.tmp_path / "injection_cache.json")


class TestWO2bSafeIngest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.tmp_path = Path(os.path.realpath(self.tmp))
        self.config_path = self.tmp_path / "config.json"
        self.config_path.write_text(json.dumps({"maintainer": "alice"}))

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def test_load_config_env(self):
        with unittest.mock.patch.dict(os.environ, {"BUG_FIXER_CONFIG": str(self.config_path)}):
            cfg = safe_ingest.load_config()
            self.assertEqual(cfg.get("maintainer"), "alice")

    def test_load_config_defaults_when_not_required(self):
        # Point to empty dir where no configs exist
        with unittest.mock.patch.dict(os.environ, {"BUG_FIXER_CONFIG": ""}), \
             unittest.mock.patch("safe_ingest._find_daemon_dir", return_value=None), \
             unittest.mock.patch("pathlib.Path.cwd", return_value=self.tmp_path):
            cfg = safe_ingest.load_config(require=False)
            self.assertIsInstance(cfg, dict)
            self.assertIn("scanning", cfg)

    def test_load_config_raises_when_required(self):
        with unittest.mock.patch.dict(os.environ, {"BUG_FIXER_CONFIG": ""}), \
             unittest.mock.patch("safe_ingest._find_daemon_dir", return_value=None), \
             unittest.mock.patch("pathlib.Path.cwd", return_value=self.tmp_path):
            with self.assertRaises(FileNotFoundError):
                safe_ingest.load_config(require=True)


class TestWO2bBugFixerAPI(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.tmp_path = Path(os.path.realpath(self.tmp))
        self.sidecar_dir = self.tmp_path / "sidecar"
        self.sidecar_dir.mkdir()
        self.state_dir = self.sidecar_dir / "state"
        self.state_dir.mkdir()
        self.dispatches_dir = self.state_dir / "dispatches"
        self.dispatches_dir.mkdir()

        self.config_file = self.sidecar_dir / "config.json"
        self.config_file.write_text(json.dumps({"target_repo": "test/repo"}))

        # Write a sample dispatch
        dispatch_data = {
            "conversation_id": "test-conv-123",
            "type": "fresh_fix",
            "issue_number": 100,
        }
        (self.dispatches_dir / "test-conv-123.json").write_text(json.dumps(dispatch_data))

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def test_find_dispatches_dir_via_state_dir_env(self):
        with unittest.mock.patch.dict(os.environ, {"BUG_FIXER_STATE_DIR": str(self.state_dir)}):
            found = bugfixer_api._find_dispatches_dir()
            self.assertEqual(found, self.dispatches_dir)

    def test_find_dispatches_dir_derived_from_config_env(self):
        with unittest.mock.patch.dict(os.environ, {
            "BUG_FIXER_CONFIG": str(self.config_file),
            "BUG_FIXER_STATE_DIR": "",
        }):
            found = bugfixer_api._find_dispatches_dir()
            self.assertEqual(found, self.dispatches_dir)

    def test_session_context_with_state_dispatches(self):
        with unittest.mock.patch.dict(os.environ, {
            "BUG_FIXER_CONFIG": str(self.config_file),
            "BUG_FIXER_STATE_DIR": str(self.state_dir),
        }):
            ctx = bugfixer_api.BugFixer.get_session_context("test-conv-123")
            self.assertTrue(ctx.is_autonomous)
            self.assertEqual(ctx.session_type, "fresh_fix")
            self.assertEqual(ctx.issue_number, 100)
            self.assertEqual(ctx.target_repo, "test/repo")

    def test_session_context_via_split_daemon_sidecars_without_env(self):
        daemon_dir = self.tmp_path / "daemon"
        sc_dir = daemon_dir / "sidecars" / "test--repo"
        sc_dispatches = sc_dir / "state" / "dispatches"
        sc_dispatches.mkdir(parents=True)
        (daemon_dir / "plugin.json").write_text(json.dumps({"name": "bug-fixer-daemon"}))
        (sc_dir / "config.json").write_text(json.dumps({"target_repo": "test/repo"}))
        (sc_dispatches / "conv-split-1.json").write_text(json.dumps({
            "conversation_id": "conv-split-1",
            "type": "architect_review",
            "pr_number": 77,
        }))

        with unittest.mock.patch.dict(os.environ, {
            "BUG_FIXER_CONFIG": "",
            "BUG_FIXER_STATE_DIR": "",
        }), unittest.mock.patch("bugfixer_api._find_daemon_dir", return_value=daemon_dir):
            ctx = bugfixer_api.BugFixer.get_session_context("conv-split-1")
            self.assertTrue(ctx.is_autonomous)
            self.assertEqual(ctx.session_type, "architect_review")
            self.assertEqual(ctx.pr_number, 77)
            self.assertEqual(ctx.target_repo, "test/repo")

    def test_multiple_sidecars_no_cwd_match_warns(self):
        import io
        daemon_dir = self.tmp_path / "daemon"
        for slug in ("repo-a", "repo-b"):
            sc = daemon_dir / "sidecars" / slug
            sc.mkdir(parents=True)
            (sc / "config.json").write_text(json.dumps({
                "target_repo": f"org/{slug}",
                "repo_dir": f"/nonexistent/{slug}",
            }))
        (daemon_dir / "plugin.json").write_text(json.dumps({"name": "bug-fixer-daemon"}))

        stderr_buf = io.StringIO()
        with unittest.mock.patch.dict(os.environ, {
            "BUG_FIXER_CONFIG": "",
            "BUG_FIXER_STATE_DIR": "",
        }), unittest.mock.patch("bugfixer_api._find_daemon_dir", return_value=daemon_dir), \
             unittest.mock.patch("sys.stderr", stderr_buf):
            ws = bugfixer_api._find_workspace_dir()
            self.assertIsNone(ws)
            self.assertIn("Multiple bug-fixer sidecars enrolled", stderr_buf.getvalue())


if __name__ == "__main__":
    unittest.main()
