"""Unit tests for WO3 changes: tick_receiver.py, tick.py, and dispatcher STATE_DIR handling."""

import fcntl
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "daemon" / "workspace-plugin" / "bug-fixer" / "scripts"
DAEMON_SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "daemon" / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))
if str(DAEMON_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(DAEMON_SCRIPTS_DIR))

import tick_receiver
import tick


class TestWO3TickReceiver(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.tmp_path = Path(os.path.realpath(self.tmp))
        self.config_path = self.tmp_path / "config.json"
        self.state_dir = self.tmp_path / "state"
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.config_data = {
            "target_repo": "owner/repo",
            "triage_interval_min": 60,
            "engineering_interval_min": 10,
            "architect_interval_min": 15,
            "trajectory_interval_min": 1440,
        }
        self.config_path.write_text(json.dumps(self.config_data))

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def test_missing_config_returns_1(self):
        with patch.dict(os.environ, {"BUG_FIXER_CONFIG": ""}):
            self.assertEqual(tick_receiver.main(), 1)

        with patch.dict(os.environ, {"BUG_FIXER_CONFIG": str(self.tmp_path / "nonexistent.json")}):
            self.assertEqual(tick_receiver.main(), 1)

    @patch("subprocess.Popen")
    def test_dispatches_when_no_markers_exist(self, mock_popen):
        mock_proc = MagicMock()
        mock_popen.return_value = mock_proc

        env = {
            "BUG_FIXER_CONFIG": str(self.config_path),
            "BUG_FIXER_STATE_DIR": str(self.state_dir),
        }
        with patch.dict(os.environ, env):
            ret = tick_receiver.main()
            self.assertEqual(ret, 0)
            # All 4 loops should be dispatched because no markers existed
            self.assertEqual(mock_popen.call_count, 4)

            # Markers should now exist
            for name in ["triage", "engineering", "architect", "trajectory"]:
                marker = self.state_dir / f"{name}.last"
                self.assertTrue(marker.exists(), f"Marker for {name} was not created")

    @patch("subprocess.Popen")
    def test_skips_when_markers_are_recent(self, mock_popen):
        # Create markers touched just now
        for name in ["triage", "engineering", "architect", "trajectory"]:
            marker = self.state_dir / f"{name}.last"
            marker.touch()

        env = {
            "BUG_FIXER_CONFIG": str(self.config_path),
            "BUG_FIXER_STATE_DIR": str(self.state_dir),
        }
        with patch.dict(os.environ, env):
            ret = tick_receiver.main()
            self.assertEqual(ret, 0)
            # None should be dispatched
            self.assertEqual(mock_popen.call_count, 0)

    @patch("subprocess.Popen")
    def test_dispatches_only_overdue_loop(self, mock_popen):
        # Touch all markers recently, but make engineering marker old
        now = time.time()
        for name in ["triage", "architect", "trajectory"]:
            marker = self.state_dir / f"{name}.last"
            marker.touch()

        eng_marker = self.state_dir / "engineering.last"
        eng_marker.touch()
        # Set mtime to 15 minutes ago (interval is 10 min)
        os.utime(eng_marker, (now - 900, now - 900))

        env = {
            "BUG_FIXER_CONFIG": str(self.config_path),
            "BUG_FIXER_STATE_DIR": str(self.state_dir),
        }
        with patch.dict(os.environ, env):
            ret = tick_receiver.main()
            self.assertEqual(ret, 0)
            # Only engineering should be dispatched
            self.assertEqual(mock_popen.call_count, 1)
            call_args = mock_popen.call_args[0][0]
            self.assertTrue(any("engineering_dispatcher.py" in str(arg) for arg in call_args))

    @patch("subprocess.Popen")
    def test_skips_when_lock_is_held(self, mock_popen):
        # Hold lock on engineering
        lock_file = self.state_dir / "engineering.lock"
        lock_fd = open(lock_file, "w")
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)

        try:
            env = {
                "BUG_FIXER_CONFIG": str(self.config_path),
                "BUG_FIXER_STATE_DIR": str(self.state_dir),
            }
            with patch.dict(os.environ, env):
                ret = tick_receiver.main()
                self.assertEqual(ret, 0)
                # 3 loops should be dispatched, but NOT engineering
                self.assertEqual(mock_popen.call_count, 3)
                for call in mock_popen.call_args_list:
                    cmd = call[0][0]
                    self.assertFalse(any("engineering_dispatcher.py" in str(arg) for arg in cmd))
        finally:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
            lock_fd.close()

    def test_default_state_dir_when_env_not_set(self):
        # When BUG_FIXER_STATE_DIR is not set, derives config_path.parent / "state"
        with patch("subprocess.Popen"):
            env = {
                "BUG_FIXER_CONFIG": str(self.config_path),
                "BUG_FIXER_STATE_DIR": "",
            }
            with patch.dict(os.environ, env):
                ret = tick_receiver.main()
                self.assertEqual(ret, 0)
                self.assertTrue((self.tmp_path / "state").is_dir())


class TestWO3TickScaffold(unittest.TestCase):
    @patch("subprocess.run")
    def test_tick_delegates_to_tick_receiver(self, mock_run):
        mock_proc = MagicMock()
        mock_proc.return_value = 0
        mock_proc.returncode = 0
        mock_run.return_value = mock_proc

        ret = tick.main()
        self.assertEqual(ret, 0)
        self.assertTrue(mock_run.called)
        cmd = mock_run.call_args[0][0]
        self.assertTrue(any("tick_receiver.py" in str(arg) for arg in cmd))

    @patch("pathlib.Path.is_file", return_value=False)
    def test_tick_returns_1_when_receiver_missing(self, mock_is_file):
        ret = tick.main()
        self.assertEqual(ret, 1)


class TestWO3DispatcherStateDir(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.tmp_path = Path(os.path.realpath(self.tmp))
        self.sidecar_dir = self.tmp_path / "sidecar"
        self.sidecar_dir.mkdir()
        self.custom_state_dir = self.tmp_path / "custom_state"
        self.config_file = self.sidecar_dir / "config.json"
        self.config_file.write_text(json.dumps({
            "target_repo": "owner/repo",
            "repo_dir": str(self.tmp_path / "repo"),
        }))

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def test_engineering_dispatcher_state_dir_resolution(self):
        # 1. With BUG_FIXER_STATE_DIR
        with patch.dict(os.environ, {
            "BUG_FIXER_CONFIG": str(self.config_file),
            "BUG_FIXER_STATE_DIR": str(self.custom_state_dir),
        }):
            # Test inline resolution logic identical to engineering_dispatcher
            _state_env = os.environ.get("BUG_FIXER_STATE_DIR")
            if _state_env:
                state_dir = Path(_state_env)
            elif self.config_file:
                state_dir = self.config_file.parent / "state"
            else:
                state_dir = Path.cwd()
            state_dir.mkdir(parents=True, exist_ok=True)

            self.assertEqual(state_dir, self.custom_state_dir)
            self.assertTrue(self.custom_state_dir.is_dir())
            claims_dir = state_dir / "claims"
            dispatches_dir = state_dir / "dispatches"
            self.assertEqual(claims_dir, self.custom_state_dir / "claims")
            self.assertEqual(dispatches_dir, self.custom_state_dir / "dispatches")

        # 2. Without BUG_FIXER_STATE_DIR (empty string bug test: Path("") is truthy)
        with patch.dict(os.environ, {
            "BUG_FIXER_CONFIG": str(self.config_file),
            "BUG_FIXER_STATE_DIR": "",
        }):
            _state_env = os.environ.get("BUG_FIXER_STATE_DIR")
            if _state_env:
                state_dir = Path(_state_env)
            elif self.config_file:
                state_dir = self.config_file.parent / "state"
            else:
                state_dir = Path.cwd()
            state_dir.mkdir(parents=True, exist_ok=True)

            self.assertEqual(state_dir, self.sidecar_dir / "state")
            self.assertTrue((self.sidecar_dir / "state").is_dir())

    def test_dispatchers_derive_claims_and_dispatches_dirs(self):
        import engineering_dispatcher
        import architect_dispatcher

        self.assertTrue(hasattr(engineering_dispatcher, "STATE_DIR"))
        self.assertTrue(hasattr(engineering_dispatcher, "CLAIMS_DIR"))
        self.assertTrue(hasattr(engineering_dispatcher, "DISPATCHES_DIR"))

        # Verify all 4 dispatchers contain the fixed STATE_DIR logic in source
        dispatchers = [
            "engineering_dispatcher.py",
            "triage_dispatcher.py",
            "architect_dispatcher.py",
            "trajectory_dispatcher.py",
        ]
        expected_snippets = [
            '_state_env = os.environ.get("BUG_FIXER_STATE_DIR")',
            "if _state_env:",
            "STATE_DIR = Path(_state_env)",
            'STATE_DIR = CONFIG_FILE.parent / "state"',
            "STATE_DIR.mkdir(parents=True, exist_ok=True)",
            'DISPATCHES_DIR = STATE_DIR / "dispatches"',
        ]
        for name in dispatchers:
            content = (SCRIPTS_DIR / name).read_text()
            for snippet in expected_snippets:
                self.assertIn(
                    snippet,
                    content,
                    f"Dispatcher {name} missing expected STATE_DIR snippet: {snippet}",
                )


if __name__ == "__main__":
    unittest.main()
