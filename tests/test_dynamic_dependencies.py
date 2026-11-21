import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from analyzer.dependency_graph import DependencyGraph


class DynamicDependencyTests(unittest.TestCase):
    def build(self, root, files, reversed_order=False):
        for path, source in files.items():
            target = root / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(source, encoding="utf-8")
        return DependencyGraph._from_tree(root, list(reversed(files) if reversed_order else files))

    def test_literal_and_relative_imports_resolve_and_traverse_cycle(self):
        files = {
            "sqlglot/__init__.py": "",
            "sqlglot/leaf.py": "from . import helper",
            "sqlglot/helper.py": "from importlib import import_module as load\nload('.leaf', 'sqlglot')",
            "sqlglot/consumer.py": "import importlib as loader\nloader.import_module('sqlglot.leaf')",
        }
        with TemporaryDirectory() as directory:
            graph = self.build(Path(directory), files)
            changed = {"sqlglot/leaf.py"}
            self.assertFalse(graph.is_uncertain(changed))
            self.assertEqual(graph.impacted_files(changed), set(files) - {"sqlglot/__init__.py"})
            self.assertEqual(graph.impacted_files({"sqlglot/__init__.py"}), set(files))

    def test_prefix_expands_candidates_without_discarding_uncertainty(self):
        files = {
            "sqlglot/__init__.py": "import importlib\nimportlib.import_module(f'{__name__}.{name}')",
            "sqlglot/leaf.py": "",
            "sqlglot/other.py": "",
        }
        with TemporaryDirectory() as directory:
            graph = self.build(Path(directory), files)
            changed = {"sqlglot/leaf.py"}
            self.assertTrue(graph.is_uncertain(changed))
            self.assertIn("sqlglot/__init__.py", graph.impacted_files(changed))

    def test_variable_or_shadowed_target_remains_uncertain(self):
        for loader in ("__import__(module_name)",
                       "import importlib\nimportlib.import_module = custom\nimportlib.import_module('sqlglot.leaf')",
                       "import importlib\nimportlib.import_module('.leaf', package_name)"):
            with self.subTest(loader=loader), TemporaryDirectory() as directory:
                graph = self.build(Path(directory), {
                    "sqlglot/__init__.py": "",
                    "sqlglot/leaf.py": "",
                    "sqlglot/loader.py": loader,
                })
                self.assertTrue(graph.is_uncertain({"sqlglot/leaf.py"}))
                self.assertNotIn("sqlglot/loader.py", graph.impacted_files({"sqlglot/leaf.py"}))

    def test_unresolved_dynamic_internal_and_invalid_source_are_explicit(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            graph = self.build(root, {
                "sqlglot/__init__.py": "",
                "sqlglot/loader.py": "__import__('sqlglot.missing')",
                "sqlglot/broken.py": "def broken(!!!",
            })
            evidence = root / "evidence.json"
            graph.save_evidence(evidence)
            findings = json.loads(evidence.read_text(encoding="utf-8"))["findings"]
            self.assertEqual({item["kind"] for item in findings}, {
                "unresolved_internal", "unsupported_source",
            })
            self.assertTrue(graph.is_uncertain({"sqlglot/__init__.py"}))

    def test_eval_method_calls_do_not_stand_in_for_builtin_eval(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            path = "sqlglot/executor.py"
            source = "class Context:\n    def run(self):\n        return self.eval(value)\n"
            graph = self.build(root, {"sqlglot/__init__.py": "", path: source})
            self.assertFalse(graph.is_uncertain({path}))
            graph = self.build(root, {"sqlglot/__init__.py": "", path: source + "\neval(data)\n"})
            self.assertTrue(graph.is_uncertain({path}))

    def test_dynamic_artifacts_are_deterministic_and_roundtrip(self):
        files = {
            "sqlglot/__init__.py": "",
            "sqlglot/leaf.py": "",
            "sqlglot/loader.py": "__import__('sqlglot.leaf')",
        }
        with TemporaryDirectory() as directory:
            root = Path(directory)
            first, second = self.build(root, files), self.build(root, files, reversed_order=True)
            for method in ("save", "save_evidence"):
                one, two = root / f"{method}-1.json", root / f"{method}-2.json"
                getattr(first, method)(one)
                getattr(second, method)(two)
                self.assertEqual(one.read_bytes(), two.read_bytes())
            loaded = DependencyGraph.load(str(root / "save-1.json"))
            self.assertEqual(loaded.impacted_files({"sqlglot/leaf.py"}), {
                "sqlglot/leaf.py", "sqlglot/loader.py",
            })
