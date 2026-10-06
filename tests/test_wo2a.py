import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
import sys

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "daemon" / "workspace-plugin" / "bug-fixer" / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import pre_tool_gate
from pre_tool_gate import _find_daemon_dir, load_config, evaluate_gate_a, _ProtectedPaths

class TestWO2a(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.addCleanup(lambda: shutil.rmtree(self.tmpdir))
        self.tmp_path = Path(os.path.realpath(self.tmpdir))
        
    def test_find_daemon_dir_skips_workspace_plugin(self):
        # Create WO1 layout
        # daemon/
        #   plugin.json (name: bug-fixer-daemon)
        #   workspace-plugin/
        #     bug-fixer/
        #       plugin.json (name: bug-fixer)
        #       scripts/
        #         pre_tool_gate.py
        
        daemon_dir = self.tmp_path / "daemon"
        daemon_dir.mkdir(parents=True)
        (daemon_dir / "plugin.json").write_text(json.dumps({"name": "bug-fixer-daemon"}))
        
        workspace_dir = daemon_dir / "workspace-plugin" / "bug-fixer"
        workspace_dir.mkdir(parents=True)
        (workspace_dir / "plugin.json").write_text(json.dumps({"name": "bug-fixer"}))
        
        scripts_dir = workspace_dir / "scripts"
        scripts_dir.mkdir()
        (scripts_dir / "pre_tool_gate.py").write_text("")
        
        found = _find_daemon_dir(scripts_dir)
        self.assertEqual(found, daemon_dir)

    def test_load_config_explicit_path(self):
        config_path = self.tmp_path / "config.json"
        config_path.write_text(json.dumps({"test": 1}))
        config = load_config(config_path)
        self.assertEqual(config["test"], 1)
        self.assertEqual(config["_loaded_from"], str(config_path))
        
    def test_load_config_bad_explicit_raises(self):
        with self.assertRaises(FileNotFoundError):
            load_config(self.tmp_path / "does_not_exist.json")
            
    def test_load_config_bad_env_raises(self):
        os.environ["BUG_FIXER_CONFIG"] = str(self.tmp_path / "does_not_exist.json")
        try:
            with self.assertRaises(FileNotFoundError):
                load_config()
        finally:
            del os.environ["BUG_FIXER_CONFIG"]
            
    def test_load_config_sidecars_scan_match(self):
        # Setup daemon layout with sidecars
        daemon_dir = self.tmp_path / "daemon"
        sidecars_dir = daemon_dir / "sidecars"
        sidecar_a = sidecars_dir / "a"
        sidecar_a.mkdir(parents=True)
        (sidecar_a / "config.json").write_text(json.dumps({
            "repo_dir": str(self.tmp_path / "myrepo"),
            "val": "a"
        }))
        
        sidecar_b = sidecars_dir / "b"
        sidecar_b.mkdir(parents=True)
        (sidecar_b / "config.json").write_text(json.dumps({
            "repo_dir": str(self.tmp_path / "otherrepo"),
            "val": "b"
        }))
        
        # We need _find_daemon_dir to find our daemon_dir
        # Since _find_daemon_dir looks at ancestors of script_dir, let's mock it
        original_find = pre_tool_gate._find_daemon_dir
        try:
            pre_tool_gate._find_daemon_dir = lambda x: daemon_dir
            config = load_config(workspace_paths=[str(self.tmp_path / "myrepo")])
            self.assertEqual(config.get("val"), "a")
        finally:
            pre_tool_gate._find_daemon_dir = original_find
            
    def test_load_config_defaults(self):
        original_find = pre_tool_gate._find_daemon_dir
        try:
            pre_tool_gate._find_daemon_dir = lambda x: None
            config = load_config()
            # Unenrolled workspaces get empty protections
            self.assertEqual(config["spec_file_path"], "")
            self.assertEqual(config["protected_files"], [])
            self.assertFalse(config.get("_enrolled", True))
        finally:
            pre_tool_gate._find_daemon_dir = original_find

    def test_gate_a_deny_plugins_json(self):
        config_path = self.tmp_path / "config.json"
        config_path.write_text(json.dumps({"repo_dir": str(self.tmp_path)}))
        
        # Temporarily mock SCANNER_PROTECTED_FILES to include derived values
        original_protected = pre_tool_gate.SCANNER_PROTECTED_FILES
        try:
            pre_tool_gate.SCANNER_PROTECTED_FILES = pre_tool_gate._derive_workspace_paths(config_path)
            
            res1 = evaluate_gate_a("write_to_file", {"TargetFile": str(self.tmp_path / ".agents" / "plugins.json")}, "spec.md", [])
            self.assertEqual(res1.get("decision"), "deny")
            
            res2 = evaluate_gate_a("write_to_file", {"TargetFile": ".agents/plugins.json"}, "spec.md", [])
            self.assertEqual(res2.get("decision"), "deny")
        finally:
            pre_tool_gate.SCANNER_PROTECTED_FILES = original_protected

    def test_gate_a_deny_sidecar_config(self):
        original_protected = pre_tool_gate.SCANNER_PROTECTED_FILES
        try:
            pre_tool_gate.SCANNER_PROTECTED_FILES = frozenset([f"PREFIX:{self.tmp_path}/daemon/"])
            res = evaluate_gate_a("write_to_file", {"TargetFile": str(self.tmp_path / "daemon" / "sidecars" / "x" / "config.json")}, "spec.md", [])
            self.assertEqual(res.get("decision"), "deny")
        finally:
            pre_tool_gate.SCANNER_PROTECTED_FILES = original_protected

    def test_gate_a_deny_install_dir_script(self):
        original_protected = pre_tool_gate.SCANNER_PROTECTED_FILES
        try:
            pre_tool_gate.SCANNER_PROTECTED_FILES = frozenset([f"PREFIX:{self.tmp_path}/daemon/"])
            res = evaluate_gate_a("write_to_file", {"TargetFile": str(self.tmp_path / "daemon" / "scripts" / "foo.py")}, "spec.md", [])
            self.assertEqual(res.get("decision"), "deny")
        finally:
            pre_tool_gate.SCANNER_PROTECTED_FILES = original_protected

    def test_gate_a_allow_state_dir_write(self):
        original_protected = pre_tool_gate.SCANNER_PROTECTED_FILES
        try:
            pre_tool_gate.SCANNER_PROTECTED_FILES = frozenset([f"PREFIX:{self.tmp_path}/daemon/"])
            res = evaluate_gate_a("write_to_file", {"TargetFile": str(self.tmp_path / "daemon" / "sidecars" / "x" / "state" / "some_log.txt")}, "spec.md", [])
            self.assertEqual(res.get("decision"), "allow")
        finally:
            pre_tool_gate.SCANNER_PROTECTED_FILES = original_protected

    def test_gate_a_deny_state_injection_cache(self):
        original_protected = pre_tool_gate.SCANNER_PROTECTED_FILES
        try:
            pre_tool_gate.SCANNER_PROTECTED_FILES = frozenset([f"PREFIX:{self.tmp_path}/daemon/"])
            res = evaluate_gate_a("write_to_file", {"TargetFile": str(self.tmp_path / "daemon" / "sidecars" / "x" / "state" / "injection_cache.json")}, "spec.md", [])
            self.assertEqual(res.get("decision"), "deny")
        finally:
            pre_tool_gate.SCANNER_PROTECTED_FILES = original_protected

    def test_gate_a_deny_state_integrity_snapshot(self):
        original_protected = pre_tool_gate.SCANNER_PROTECTED_FILES
        try:
            pre_tool_gate.SCANNER_PROTECTED_FILES = frozenset([f"PREFIX:{self.tmp_path}/daemon/"])
            res = evaluate_gate_a("write_to_file", {"TargetFile": str(self.tmp_path / "daemon" / "sidecars" / "x" / "state" / ".integrity_snapshot.json")}, "spec.md", [])
            self.assertEqual(res.get("decision"), "deny")
        finally:
            pre_tool_gate.SCANNER_PROTECTED_FILES = original_protected

    def test_gate_b_prefix_shell_forms(self):
        original_protected = pre_tool_gate.SCANNER_PROTECTED_FILES
        try:
            pre_tool_gate.SCANNER_PROTECTED_FILES = frozenset([f"PREFIX:{self.tmp_path}/daemon/"])
            paths = _ProtectedPaths.build("spec.md", [], str(self.tmp_path))
            
            self.assertTrue(paths.mentioned_in(f"{self.tmp_path}/daemon/scripts/foo.py", str(self.tmp_path)))
            
            # Using relative path traversal that resolves into daemon/
            self.assertTrue(paths.mentioned_in("daemon/scripts/foo.py", str(self.tmp_path)))
            
            # Using $HOME or ~ expansion if tmp_path happens to be in home, or just testing absolute resolving
            # We already tested that _is_prefix_protected is wired correctly.
        finally:
            pre_tool_gate.SCANNER_PROTECTED_FILES = original_protected

    def test_gate_b_state_path_traversal(self):
        original_protected = pre_tool_gate.SCANNER_PROTECTED_FILES
        try:
            pre_tool_gate.SCANNER_PROTECTED_FILES = frozenset([f"PREFIX:{self.tmp_path}/daemon/"])
            paths = _ProtectedPaths.build("spec.md", [], str(self.tmp_path))
            
            traversal = f"{self.tmp_path}/daemon/sidecars/x/state/../config.json"
            self.assertTrue(paths.mentioned_in(traversal, str(self.tmp_path)))
        finally:
            pre_tool_gate.SCANNER_PROTECTED_FILES = original_protected

if __name__ == "__main__":
    unittest.main()
