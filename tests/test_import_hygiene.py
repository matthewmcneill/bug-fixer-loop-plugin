"""Unit tests for import hygiene and preventing UnboundLocalError from local imports.

Python treats any import or assignment inside a function scope as a local
variable binding for the entire function scope, which shadows module-level
imports and can lead to UnboundLocalError (e.g. PLC0415).
"""

import ast
from pathlib import Path
import unittest


class FunctionScopedImportVisitor(ast.NodeVisitor):
    """Linear-time AST visitor that detects import statements inside functions."""

    def __init__(self, filename: str):
        super().__init__()
        self.filename = filename
        self.function_depth = 0
        self.current_function_names: list[str] = []
        self.violations: list[str] = []

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self.function_depth += 1
        self.current_function_names.append(node.name)
        try:
            self.generic_visit(node)
        finally:
            self.current_function_names.pop()
            self.function_depth -= 1

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self.function_depth += 1
        self.current_function_names.append(node.name)
        try:
            self.generic_visit(node)
        finally:
            self.current_function_names.pop()
            self.function_depth -= 1

    def visit_Import(self, node: ast.Import) -> None:
        if self.function_depth > 0:
            names = ", ".join(alias.name for alias in node.names)
            func = " -> ".join(self.current_function_names)
            self.violations.append(
                f"{self.filename}:{node.lineno} inside '{func}': {names}"
            )
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if self.function_depth > 0:
            mod = node.module or ""
            names = f"from {mod} import " + ", ".join(alias.name for alias in node.names)
            func = " -> ".join(self.current_function_names)
            self.violations.append(
                f"{self.filename}:{node.lineno} inside '{func}': {names}"
            )
        self.generic_visit(node)


class TestImportHygiene(unittest.TestCase):
    def setUp(self):
        self.repo_root = Path(__file__).resolve().parent.parent
        self.scripts_dir = self.repo_root / "scripts"

    def test_no_function_scoped_imports_in_scripts(self):
        """Verify no script files define imports inside function or method bodies."""
        all_violations = []
        py_files = sorted(self.scripts_dir.glob("*.py"))
        self.assertTrue(len(py_files) > 0, "No script files found to check")

        for py_file in py_files:
            rel_path = py_file.relative_to(self.repo_root)
            content = py_file.read_text(encoding="utf-8")
            tree = ast.parse(content, filename=str(py_file))

            visitor = FunctionScopedImportVisitor(str(rel_path))
            visitor.visit(tree)
            all_violations.extend(visitor.violations)

        self.assertEqual(
            all_violations,
            [],
            "Found function-scoped imports in scripts (violates PLC0415, risks UnboundLocalError):\n"
            + "\n".join(f"  - {v}" for v in all_violations),
        )

    def test_visitor_detects_nested_import_without_duplicates(self):
        """Ensure visitor handles nested functions in linear time without duplicate reports."""
        sample_code = (
            "import os\n"
            "\n"
            "def outer():\n"
            "    def inner():\n"
            "        import sys\n"
            "        pass\n"
        )
        tree = ast.parse(sample_code, filename="dummy.py")
        visitor = FunctionScopedImportVisitor("dummy.py")
        visitor.visit(tree)
        self.assertEqual(len(visitor.violations), 1)
        self.assertIn("inside 'outer -> inner': sys", visitor.violations[0])

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
