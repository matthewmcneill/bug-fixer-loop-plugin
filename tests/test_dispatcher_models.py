"""Unit tests for dispatcher model tier selection and configuration."""

import ast
import json
import os
import re
import unittest
from pathlib import Path


class TestDispatcherModels(unittest.TestCase):
    def setUp(self):
        self.repo_root = Path(__file__).resolve().parent.parent
        self.scripts_dir = self.repo_root / "scripts"

    def test_install_script_contains_default_models(self):
        """Verify install.sh defines the expected model tier defaults."""
        install_script = (self.scripts_dir / "install.sh").read_text()
        self.assertIn('"triage_model": "flash"', install_script)
        self.assertIn('"engineering_model": "pro"', install_script)
        self.assertIn('"architect_model": "pro"', install_script)
        self.assertIn('"trajectory_model": "flash"', install_script)

    def test_configure_script_contains_default_models(self):
        """Verify configure.sh provisions the default model tiers."""
        configure_script = (self.scripts_dir / "configure.sh").read_text()
        self.assertIn('config["triage_model"] = "flash"', configure_script)
        self.assertIn('config["architect_model"] = "pro"', configure_script)
        self.assertIn('config["trajectory_model"] = "flash"', configure_script)
        self.assertIn('config["engineering_model"]', configure_script)

    def test_all_dispatchers_specify_model_flag_on_new_conversation(self):
        """Verify all agentapi new-conversation calls in dispatcher scripts pass --model."""
        dispatchers = [
            "triage_dispatcher.py",
            "engineering_dispatcher.py",
            "architect_dispatcher.py",
            "trajectory_dispatcher.py",
        ]
        for name in dispatchers:
            script_path = self.scripts_dir / name
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
        """Test complexity mapping and fallback in engineering_dispatcher."""
        # Read engineering_dispatcher.py and extract COMPLEXITY_MODEL_MAP and get_model_for_issue
        script_path = self.scripts_dir / "engineering_dispatcher.py"
        tree = ast.parse(script_path.read_text())

        # Extract COMPLEXITY_MODEL_MAP constant
        complexity_map = None
        for node in tree.body:
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id == "COMPLEXITY_MODEL_MAP":
                        complexity_map = ast.literal_eval(node.value)
        self.assertIsNotNone(complexity_map)
        self.assertEqual(complexity_map.get("mechanical"), "flash_lite")
        self.assertEqual(complexity_map.get("standard"), "flash")
        self.assertEqual(complexity_map.get("architect"), "pro")

        # Compile and execute get_model_for_issue in a controlled namespace
        def make_resolver(config_dict):
            ns = {
                "COMPLEXITY_MODEL_MAP": complexity_map,
                "config": config_dict,
            }
            func_code = None
            for node in tree.body:
                if isinstance(node, ast.FunctionDef) and node.name == "get_model_for_issue":
                    func_code = compile(ast.Module(body=[node], type_ignores=[]), "<ast>", "exec")
                    break
            self.assertIsNotNone(func_code)
            exec(func_code, ns)
            return ns["get_model_for_issue"]

        # Default config
        resolver = make_resolver({})
        self.assertEqual(
            resolver({"labels": [{"name": "complexity:mechanical"}]}),
            "flash_lite",
        )
        self.assertEqual(
            resolver({"labels": [{"name": "complexity:standard"}]}),
            "flash",
        )
        self.assertEqual(
            resolver({"labels": [{"name": "complexity:architect"}]}),
            "pro",
        )
        # No complexity label falls back to default 'pro'
        self.assertEqual(
            resolver({"labels": [{"name": "bug"}]}),
            "pro",
        )

        # Config with engineering_model override
        custom_resolver = make_resolver({"engineering_model": "flash_lite"})
        self.assertEqual(
            custom_resolver({"labels": [{"name": "bug"}]}),
            "flash_lite",
        )

        # Config with legacy tick_model fallback
        legacy_resolver = make_resolver({"tick_model": "flash"})
        self.assertEqual(
            legacy_resolver({"labels": [{"name": "bug"}]}),
            "flash",
        )


if __name__ == "__main__":
    unittest.main()
