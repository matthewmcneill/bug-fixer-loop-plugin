"""Unit tests for dispatcher model tier selection and configuration."""

import re
import unittest
import sys
from pathlib import Path
from unittest.mock import patch

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "daemon" / "workspace-plugin" / "bug-fixer" / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from engineering_dispatcher import (
    COMPLEXITY_MODEL_MAP,
    get_default_engineering_model,
    get_model_for_issue,
)


class TestDispatcherModels(unittest.TestCase):
    def setUp(self):
        self.repo_root = Path(__file__).resolve().parent.parent
        self.scripts_dir = self.repo_root / "scripts"
        self.py_scripts_dir = (
            self.repo_root / "daemon" / "workspace-plugin" / "bug-fixer" / "scripts"
        )

    def test_install_script_contains_default_models(self):
        """Verify install.sh defines the expected model tier defaults."""
        install_script = (self.scripts_dir / "install.sh").read_text()
        self.assertIn('"triage_model": "flash"', install_script)
        self.assertIn('"engineering_model": "pro"', install_script)
        self.assertIn('"architect_model": "pro"', install_script)
        self.assertIn('"trajectory_model": "flash"', install_script)

    def test_enroll_script_contains_default_models(self):
        """Verify enroll.sh provisions the default model tiers."""
        enroll_script = (self.scripts_dir / "enroll.sh").read_text()
        self.assertIn('"triage_model": "flash"', enroll_script)
        self.assertIn('"architect_model": "pro"', enroll_script)
        self.assertIn('"trajectory_model": "flash"', enroll_script)
        self.assertIn('"engineering_model": "pro"', enroll_script)

    def test_all_dispatchers_specify_model_flag_on_new_conversation(self):
        """Verify all agentapi new-conversation calls in dispatcher scripts pass --model."""
        dispatchers = [
            "triage_dispatcher.py",
            "engineering_dispatcher.py",
            "architect_dispatcher.py",
            "trajectory_dispatcher.py",
        ]
        for name in dispatchers:
            script_path = self.py_scripts_dir / name
            content = script_path.read_text()
            # Find all calls to agentapi new-conversation
            matches = re.findall(
                r'run\(\[.*?"agentapi",\s*"new-conversation".*?\]\)',
                content,
                re.DOTALL,
            )
            self.assertTrue(
                len(matches) > 0,
                f"No agentapi new-conversation found in {name}",
            )
            for m in matches:
                self.assertIn(
                    "--model=",
                    m,
                    f"Dispatcher {name} calls new-conversation without --model flag: {m}",
                )

    def test_engineering_dispatcher_get_model_for_issue_mapping(self):
        """Test complexity mapping and fallback in engineering_dispatcher using unittest.mock."""
        self.assertEqual(COMPLEXITY_MODEL_MAP.get("mechanical"), "flash_lite")
        self.assertEqual(COMPLEXITY_MODEL_MAP.get("standard"), "flash")
        self.assertEqual(COMPLEXITY_MODEL_MAP.get("architect"), "pro")

        # Test complexity label mappings
        self.assertEqual(
            get_model_for_issue({"labels": [{"name": "bug-fixer:complexity:mechanical"}]}),
            "flash_lite",
        )
        self.assertEqual(
            get_model_for_issue({"labels": [{"name": "bug-fixer:complexity:standard"}]}),
            "flash",
        )
        self.assertEqual(
            get_model_for_issue({"labels": [{"name": "bug-fixer:complexity:architect"}]}),
            "pro",
        )

        # Default fallback (no complexity label, empty config -> 'pro')
        with patch.dict("engineering_dispatcher.config", {}, clear=True):
            self.assertEqual(
                get_model_for_issue({"labels": [{"name": "bug"}]}),
                "pro",
            )
            self.assertEqual(get_default_engineering_model(), "pro")

        # Config with engineering_model override
        with patch.dict(
            "engineering_dispatcher.config",
            {"engineering_model": "flash_lite"},
            clear=True,
        ):
            self.assertEqual(
                get_model_for_issue({"labels": [{"name": "bug"}]}),
                "flash_lite",
            )
            self.assertEqual(get_default_engineering_model(), "flash_lite")

        # Config with legacy tick_model fallback
        with patch.dict(
            "engineering_dispatcher.config",
            {"tick_model": "flash"},
            clear=True,
        ):
            self.assertEqual(
                get_model_for_issue({"labels": [{"name": "bug"}]}),
                "flash",
            )
            self.assertEqual(get_default_engineering_model(), "flash")


if __name__ == "__main__":
    unittest.main()
