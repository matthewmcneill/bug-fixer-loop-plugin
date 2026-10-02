"""Unit tests for import hygiene and preventing UnboundLocalError from local imports.

Python treats any import or assignment inside a function scope as a local
variable binding for the entire function scope, which shadows module-level
imports and can lead to UnboundLocalError (e.g. PLC0415).
"""

import ast
from pathlib import Path
import unittest


class TestImportHygiene(unittest.TestCase):
    def setUp(self):
        self.repo_root = Path(__file__).resolve().parent.parent
        self.scripts_dir = self.repo_root / "scripts"

    def test_no_function_scoped_imports_in_scripts(self):
        """Verify no script files define imports inside function or method bodies."""
        violations = []
        py_files = sorted(self.scripts_dir.glob("*.py"))
        self.assertTrue(len(py_files) > 0, "No script files found to check")

        for py_file in py_files:
            rel_path = py_file.relative_to(self.repo_root)
            content = py_file.read_text(encoding="utf-8")
            tree = ast.parse(content, filename=str(py_file))

            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    for child in ast.walk(node):
                        if isinstance(child, (ast.Import, ast.ImportFrom)):
                            if isinstance(child, ast.Import):
                                names = ", ".join(alias.name for alias in child.names)
                            else:
                                mod = child.module or ""
                                names = f"from {mod} import " + ", ".join(
                                    alias.name for alias in child.names
                                )
                            violations.append(
                                f"{rel_path}:{child.lineno} inside '{node.name}': {names}"
                            )

        self.assertEqual(
            violations,
            [],
            "Found function-scoped imports in scripts (violates PLC0415, risks UnboundLocalError):\n"
            + "\n".join(f"  - {v}" for v in violations),
        )

    def test_all_scripts_compile(self):
        """Verify all python scripts in scripts/ compile without syntax errors."""
        for py_file in sorted(self.scripts_dir.glob("*.py")):
            content = py_file.read_text(encoding="utf-8")
            try:
                compile(content, str(py_file), "exec")
            except SyntaxError as e:
                self.fail(f"Syntax error in {py_file.name}: {e}")

    def test_engineering_dispatcher_has_top_level_json_and_re(self):
        """Ensure engineering_dispatcher imports json and re at top-level."""
        eng_file = self.scripts_dir / "engineering_dispatcher.py"
        tree = ast.parse(eng_file.read_text(encoding="utf-8"), filename=str(eng_file))
        top_level_imports = set()
        for node in tree.body:
            if isinstance(node, ast.Import):
                for alias in node.names:
                    top_level_imports.add(alias.name)
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    top_level_imports.add(node.module)

        self.assertIn("json", top_level_imports)
        self.assertIn("re", top_level_imports)


if __name__ == "__main__":
    unittest.main()
