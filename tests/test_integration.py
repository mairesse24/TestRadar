import contextlib
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from analyzer.coverage_map import CoverageMap
from analyzer.dependency_graph import DependencyGraph
from selector import analyze, run


SOURCE = "sqlglot/dialects/postgres.py"
NODE = "tests/dialects/test_postgres.py::test_parse"
PATCH = f"""diff --git a/{SOURCE} b/{SOURCE}
--- a/{SOURCE}
+++ b/{SOURCE}
@@ -1 +1 @@
-old
+new
"""
TESTS = [
    {"number": 0, "id": NODE, "file": "tests/dialects/test_postgres.py", "seconds": 1},
    {"number": 1, "id": "tests/test_schema.py::test_schema", "file": "tests/test_schema.py", "seconds": 2},
]


class IntegrationTests(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        (self.root / "commits").mkdir()
        self.patch_file = self.root / "commits/change.patch"
        self.patch_file.write_text(PATCH, encoding="utf-8")
        (self.root / "tests.json").write_text(json.dumps({"tests": TESTS}), encoding="utf-8")

    def test_positive_but_partial_coverage_preserves_full_suite(self):
        graph = DependencyGraph({SOURCE: set()})
        coverage = CoverageMap({SOURCE: {NODE}}, {TESTS[1]["id"]})
        for mode in ("safe", "balanced"):
            with self.subTest(mode=mode):
                selection, reasons = analyze(self.patch_file, TESTS, graph, coverage, mode)
                self.assertEqual(reasons, ["coverage"])
                self.assertTrue(selection.fell_back)
                self.assertEqual(selection.numbers, {0, 1})

    def test_global_graph_uncertainty_is_reported_for_known_path(self):
        graph = DependencyGraph({SOURCE: set()}, uncertain=True)
        coverage = CoverageMap({SOURCE: {NODE}}, set())
        selection, reasons = analyze(self.patch_file, TESTS, graph, coverage, "safe")
        self.assertEqual(reasons, ["dependencies", "coverage"])
        self.assertEqual(selection.numbers, {0, 1})

    def test_corrupt_and_missing_maps_and_unreadable_patch_keep_stdout_json(self):
        (self.root / "dependencies.json").write_text("{broken", encoding="utf-8")
        self.patch_file.write_bytes(b"\xff")
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            run(self.root, self.root, "safe")
        self.assertEqual([json.loads(line) for line in stdout.getvalue().splitlines()], [
            {"commit": "change", "workers": [[0, 1]]},
        ])
        self.assertIn("Dependency map unavailable", stderr.getvalue())
        self.assertIn("Coverage map unavailable", stderr.getvalue())
        self.assertIn("full-suite fallback (patch, dependencies, coverage)", stderr.getvalue())
        self.assertIn("full_suite_fallbacks=1", stderr.getvalue())
