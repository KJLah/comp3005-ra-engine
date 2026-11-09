"""Step 1 of Project 2: the library is four independent layers that never
print or touch files."""
import ast
import os
import subprocess
import sys
import unittest

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
PKG = os.path.join(ROOT, "ra_engine")
FORBIDDEN_CALLS = {"print", "input", "open"}
LAYERS = ["ra_engine.lexer", "ra_engine.parser", "ra_engine.ast_nodes",
          "ra_engine.operators"]


def module_tree(filename):
    with open(os.path.join(PKG, filename), encoding="utf-8") as f:
        return ast.parse(f.read(), filename)


class Layers(unittest.TestCase):
    def test_no_printing_or_file_access(self):
        for filename in sorted(os.listdir(PKG)):
            if not filename.endswith(".py"):
                continue
            for node in ast.walk(module_tree(filename)):
                if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                        and node.func.id in FORBIDDEN_CALLS):
                    self.fail(f"{filename}:{node.lineno} calls {node.func.id}()")

    def test_operators_do_not_import_the_parse_tree(self):
        for node in ast.walk(module_tree("operators.py")):
            if isinstance(node, ast.ImportFrom):
                self.assertNotIn(node.module, ("ast_nodes", "parser", "lexer"),
                                 f"operators.py:{node.lineno} imports {node.module}")

    def test_each_layer_imports_on_its_own(self):
        for mod in LAYERS:
            p = subprocess.run([sys.executable, "-c", f"import {mod}"],
                               cwd=ROOT, capture_output=True, text=True)
            self.assertEqual(p.returncode, 0, f"{mod}: {p.stderr}")


if __name__ == "__main__":
    unittest.main()