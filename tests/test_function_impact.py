from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from analyzer.coverage_map import CoverageMap
from analyzer.dependency_graph import DependencyGraph
from analyzer.function_impact import classify
from analyzer.patch_parser import parse_patch
from selector import analyze_precision


FILE = "sqlglot/fixture.py"
OLD = "class Example:\n    def name(self):\n        return 'value'\n"
HIT = "tests/test_fixture.py::test_name"
OTHER = "tests/test_fixture.py::test_other"


def patch_text(old, new, line=3):
    return (f"diff --git a/{FILE} b/{FILE}\n--- a/{FILE}\n+++ b/{FILE}\n"
            f"@@ -{line} +{line} @@\n-{old}\n+{new}\n")


class FunctionImpactTests(unittest.TestCase):
    def setUp(self):
        storage = TemporaryDirectory()
        self.addCleanup(storage.cleanup)
        self.root = Path(storage.name)
        (self.root / FILE).parent.mkdir()
        (self.root / FILE).write_text(OLD, encoding="utf-8")

    def test_positioned_ast_and_scope_distinguish_equivalence_from_value_edit(self):
        cosmetic = patch_text("        return 'value'", '        return "value"')
        impact = classify(self.root, parse_patch(cosmetic))
        self.assertEqual(impact.functions, ((FILE, 2),))
        self.assertTrue(impact.equivalent)
        semantic = patch_text("        return 'value'", "        return 'other'")
        self.assertFalse(classify(self.root, parse_patch(semantic)).equivalent)

    def test_structure_unknown_source_and_directives_do_not_qualify(self):
        for change in (
            patch_text("    def name(self):", "    def name(self, arg):", 2),
            patch_text("class Example:", "class Other:", 1),
            patch_text("        return 'wrong'", "        return 'value'"),
            patch_text("        return 'value'", "        return 'value'  # noqa"),
            patch_text("        return 'value'", "        import sqlglot"),
        ):
            with self.subTest(change=change):
                self.assertFalse(classify(self.root, parse_patch(change)).equivalent)

    def test_only_verified_body_equivalence_can_use_function_observations(self):
        tests = [
            {"number": 0, "id": HIT, "file": "tests/test_fixture.py", "seconds": 1},
            {"number": 1, "id": OTHER, "file": "tests/test_fixture.py", "seconds": 1},
        ]
        coverage = CoverageMap({FILE: {HIT, OTHER}}, set(), {FILE: {2: {HIT}}})
        graph = DependencyGraph({FILE: set()})
        artifact = self.root / "coverage.json"
        coverage.save(artifact)
        coverage = CoverageMap.load(str(artifact))
        self.assertTrue(coverage.is_uncertain([]))
        self.assertEqual(coverage.tests_for_functions(((FILE, 2),)), {HIT})
        target = self.root / "change.patch"
        with patch("selector.SOURCES", self.root):
            target.write_text(patch_text("        return 'value'", '        return "value"'),
                              encoding="utf-8")
            self.assertEqual(analyze_precision(target, tests, graph, coverage)[0].numbers, {0})
            target.write_text(patch_text("        return 'value'", "        return 'other'"),
                              encoding="utf-8")
            self.assertEqual(analyze_precision(target, tests, graph, coverage)[0].numbers, {0, 1})
