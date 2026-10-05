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


if __name__ == "__main__":
    unittest.main()
