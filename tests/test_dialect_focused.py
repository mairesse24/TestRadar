import json
from pathlib import Path
import unittest

from analyzer.test_selector import explain_selection


class FocusedDialectTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        catalog = Path(__file__).resolve().parents[1] / "sample" / "tests.json"
        cls.tests = json.loads(catalog.read_bytes())["tests"]

    @staticmethod
    def change(path):
        return {
            "path": path, "old_path": path, "new_path": path, "status": "modified",
            "old_lines": [50], "new_lines": [50], "added_text": ["        return value"],
            "removed_text": ["        return other_value"], "uncertain": False,
        }

    def test_direct_dialect_can_narrow_and_coverage_only_adds(self):
        changes = [self.change("sqlglot/parsers/bigquery.py")]
        baseline = explain_selection(changes, self.tests, mode="balanced")
        focused = explain_selection(changes, self.tests, mode="dialect-focused")
        self.assertFalse(focused.fell_back)
        self.assertLess(focused.numbers, baseline.numbers)
        additional = next(test for test in self.tests if test["number"] in baseline.numbers - focused.numbers)
        with_coverage = explain_selection(
            changes, self.tests, covered_test_ids={additional["id"]}, mode="dialect-focused",
        )
        self.assertEqual(with_coverage.numbers, focused.numbers | {additional["number"]})

    def test_mixed_and_core_changes_keep_balanced_policy(self):
        dialect = self.change("sqlglot/parsers/bigquery.py")
        fixture = self.change("tests/fixtures/optimizer/simplify.sql")
        core = self.change("sqlglot/parser.py")
        for changes in ([dialect, fixture], [dialect, core], [core]):
            with self.subTest(changes=changes):
                self.assertEqual(
                    explain_selection(changes, self.tests, mode="dialect-focused"),
                    explain_selection(changes, self.tests, mode="balanced"),
                )

    def test_uncertainty_still_forces_full_suite(self):
        outcome = explain_selection(
            [self.change("sqlglot/parsers/bigquery.py")], self.tests,
            analysis_uncertain=True, mode="dialect-focused",
        )
        self.assertTrue(outcome.fell_back)
        self.assertEqual(len(outcome.numbers), len(self.tests))
