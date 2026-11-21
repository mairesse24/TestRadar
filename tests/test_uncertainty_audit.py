from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from analyzer.test_selector import explain_selection
from tools.audit_dependencies import audit


class UncertaintyAuditTests(unittest.TestCase):
    def inspect(self, files, changed):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            for path, source in files.items():
                target = root / path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(source, encoding="utf-8")
            return audit(root, list(files), changed)

    def test_external_import_alone_does_not_mark_graph_uncertain(self):
        path = "sqlglot/leaf.py"
        result = self.inspect({"sqlglot/__init__.py": "", path: "import optional_vendor"}, {path})
        self.assertEqual(result["external_imports"], ["optional_vendor"])
        self.assertEqual(result["findings"], [])
        self.assertFalse(result["uncertain_for_query"])

    def test_findings_outside_known_impact_still_block_narrowing(self):
        leaf = "sqlglot/leaf.py"
        loader = "sqlglot/loader.py"
        result = self.inspect({
            "sqlglot/__init__.py": "",
            leaf: "",
            loader: "import sqlglot.missing\n__import__(module_name)\n",
        }, {leaf})
        self.assertTrue(result["uncertain_for_query"])
        self.assertEqual({entry["kind"] for entry in result["findings"]}, {
            "unresolved_internal", "dynamic_or_unresolved_relative",
        })
        self.assertTrue(all(not entry["in_known_impact"] for entry in result["findings"]))
        affected = self.inspect({
            "sqlglot/__init__.py": "",
            leaf: "",
            loader: "from . import leaf\n__import__(module_name)\n",
        }, {leaf})
        self.assertTrue(affected["findings"][0]["in_known_impact"])

    def test_static_narrow_candidate_requires_clear_upstream_evidence(self):
        tests = [
            {"id": "tests/test_isolated.py::test_one", "file": "tests/test_isolated.py", "number": 0},
            {"id": "tests/test_unrelated.py::test_two", "file": "tests/test_unrelated.py", "number": 1},
        ]
        path = tests[0]["file"]
        changes = [{
            "path": path, "old_path": path, "new_path": path, "status": "modified",
            "old_lines": [5], "new_lines": [5], "added_text": ["    assert outcome"],
            "removed_text": ["    assert result"], "uncertain": False,
        }]
        for mode in ("safe", "balanced"):
            with self.subTest(mode=mode):
                candidate = explain_selection(changes, tests, {path}, set(), mode=mode)
                self.assertEqual(candidate.numbers, {0})
                self.assertFalse(candidate.fell_back)
                fallback = explain_selection(
                    changes, tests, {path}, set(), mode=mode, analysis_uncertain=True,
                )
                self.assertEqual(fallback.numbers, {0, 1})
                self.assertTrue(fallback.fell_back)
