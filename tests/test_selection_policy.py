"""Integration regression tests for the selectable selection policies.

They drive selector.py's analysis functions with real parse_patch output and
in-memory dependency and coverage artifacts, so each hard and soft signal is
exercised through the same path the container uses.
"""

import contextlib
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest import mock

from analyzer.coverage_map import CoverageMap
from analyzer.dependency_graph import DependencyGraph
import selector
from selector import analyze, analyze_selective, configured_policy, run


def T(number, node_id, seconds=1.0):
    return {"number": number, "id": node_id, "file": node_id.split("::")[0], "seconds": seconds}


TESTS = [
    T(0, "tests/dialects/test_postgres.py::TestPostgres::test_postgres"),
    T(1, "tests/dialects/test_redshift.py::TestRedshift::test_redshift"),
    T(2, "tests/test_executor.py::TestExecutor::test_execute_tpch", 30.0),
    T(3, "tests/dialects/test_duckdb.py::TestDuckDB::test_duckdb"),
    T(4, "tests/test_diff.py::TestDiff::test_simple"),
]
ALL = {test["number"] for test in TESTS}
POSTGRES_ONLY = {0, 1, 3}  # postgres family and the dialect tests that name it
GENERATOR = "sqlglot/generators/postgres.py"
DIALECT = "sqlglot/dialects/postgres.py"
SELECTIVE = ("safe", "balanced")


def patch_text(path, removed="        return y", added="        return x", header=None):
    return (header or f"diff --git a/{path} b/{path}\n--- a/{path}\n+++ b/{path}\n") + (
        f"@@ -5 +5 @@\n-{removed}\n+{added}\n"
    )


def graph(uncertain=False, extra=()):
    """A valid pinned-style graph. ``sqlglot/__init__.py`` is imported by every
    module, as in the real artifact, so expanding through it reaches all."""
    nodes = [
        "sqlglot/__init__.py", "sqlglot/dialects/__init__.py", GENERATOR, DIALECT,
        "sqlglot/executor/python.py", "tests/test_executor.py", "tests/test_diff.py",
        "tests/dialects/test_postgres.py", *extra,
    ]
    importers = {node: set() for node in nodes}
    importers["sqlglot/__init__.py"] = set(nodes) - {"sqlglot/__init__.py"}
    importers[GENERATOR] = {DIALECT, "sqlglot/__init__.py"}
    return DependencyGraph(importers, uncertain)


def coverage(hits=None, missing=()):
    return CoverageMap(hits or {}, set(missing))


class PolicyFixture(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)

    def write(self, text, name="change.patch"):
        path = self.root / name
        if isinstance(text, bytes):
            path.write_bytes(text)
        else:
            path.write_text(text, encoding="utf-8")
        return path

    def select(self, text, mode, deps=None, cov=None):
        deps = graph() if deps is None else deps
        cov = coverage() if cov is None else cov
        return analyze_selective(self.write(text), TESTS, deps, cov, mode)


class ValidSelectiveDecisions(PolicyFixture):
    def test_dialect_change_is_narrowed_despite_known_graph_limitation(self):
        # The real graph's global flag is set by dynamic loaders in the pinned
        # source; balanced relies on the selector's pinned maps instead.
        selection, hard = self.select(patch_text(GENERATOR), "balanced", deps=graph(uncertain=True))
        self.assertEqual(hard, [])
        self.assertFalse(selection.fell_back)
        self.assertEqual(selection.numbers, POSTGRES_ONLY)

    def test_fallback_policy_keeps_the_validated_full_suite(self):
        patch = self.write(patch_text(GENERATOR))
        selection, reasons = analyze(patch, TESTS, graph(uncertain=True), coverage(), "safe")
        self.assertTrue(selection.fell_back)
        self.assertEqual(selection.numbers, ALL)
        self.assertEqual(reasons, ["dependencies", "coverage"])

    def test_registry_modules_are_not_expanded(self):
        # Without the stop the closure would reach every node through
        # sqlglot/__init__.py and add tests/test_executor.py.
        selection, _ = self.select(patch_text(GENERATOR), "balanced")
        self.assertNotIn(2, selection.numbers)
        self.assertTrue(graph().impacted_files({GENERATOR}) >= {"tests/test_executor.py"})

    def test_documentation_only_change_runs_nothing(self):
        selection, hard = self.select(patch_text("docs/notes.md", "a", "b"), "balanced")
        self.assertEqual(hard, [])
        self.assertEqual(selection.numbers, set())
        self.assertFalse(selection.fell_back)


class IncompleteCoverage(PolicyFixture):
    def test_partial_coverage_only_adds_in_balanced(self):
        cov = coverage({GENERATOR: {TESTS[4]["id"]}}, missing={TESTS[2]["id"]})
        selection, hard = self.select(patch_text(GENERATOR), "balanced", cov=cov)
        self.assertEqual(hard, [])
        self.assertEqual(selection.numbers, POSTGRES_ONLY | {4})

    def test_coverage_without_hits_never_removes_static_candidates(self):
        cov = coverage({"sqlglot/other.py": {TESTS[4]["id"]}})
        selection, _ = self.select(patch_text(GENERATOR), "balanced", cov=cov)
        self.assertTrue(POSTGRES_ONLY <= selection.numbers)

    def test_safe_ignores_incomplete_coverage_and_falls_back_for_source(self):
        cov = coverage({GENERATOR: {TESTS[0]["id"]}}, missing={TESTS[2]["id"]})
        selection, _ = self.select(patch_text(GENERATOR), "safe", cov=cov)
        self.assertTrue(selection.fell_back)
        self.assertEqual(selection.numbers, ALL)

    def test_safe_narrows_only_with_complete_coverage(self):
        cov = coverage({GENERATOR: {TESTS[0]["id"]}})
        selection, _ = self.select(patch_text(GENERATOR), "safe", cov=cov)
        self.assertFalse(selection.fell_back)
        self.assertTrue(POSTGRES_ONLY <= selection.numbers)

    def test_missing_coverage_artifact_is_a_hard_fallback(self):
        for mode in SELECTIVE:
            with self.subTest(mode=mode):
                patch = self.write(patch_text(GENERATOR))
                selection, hard = analyze_selective(patch, TESTS, graph(), None, mode)
                self.assertIn("coverage: map unavailable or invalid", hard)
                self.assertEqual(selection.numbers, ALL)


class UncertainGraphs(PolicyFixture):
    def test_missing_dependency_artifact_is_a_hard_fallback(self):
        for mode in SELECTIVE:
            with self.subTest(mode=mode):
                patch = self.write(patch_text(GENERATOR))
                selection, hard = analyze_selective(patch, TESTS, None, coverage(), mode)
                self.assertIn("dependencies: map unavailable or invalid", hard)
                self.assertEqual(selection.numbers, ALL)

    def test_existing_python_file_missing_from_graph_is_a_hard_fallback(self):
        path = "sqlglot/executor/table.py"
        for mode in SELECTIVE:
            with self.subTest(mode=mode):
                selection, hard = self.select(patch_text(path), mode)
                self.assertEqual(hard, [f"dependencies: {path} is not in the pinned dependency map"])
                self.assertEqual(selection.numbers, ALL)

    def test_non_python_paths_are_left_to_the_selector(self):
        # The graph covers Python files only; an unknown fixture still falls
        # back inside the selector.
        selection, hard = self.select(patch_text("tests/fixtures/unknown.sql", "a", "b"), "balanced")
        self.assertEqual(hard, [])
        self.assertTrue(selection.fell_back)

    def test_unknown_paths_fall_back(self):
        for path in ("setup.py", "sqlglotrs/src/lib.rs", "sqlglot/brand_new_area.py"):
            with self.subTest(path=path):
                selection, _ = self.select(patch_text(path), "balanced", deps=graph(extra=(path,)))
                self.assertEqual(selection.numbers, ALL)

    def test_dynamic_loading_or_import_edits_are_hard_fallbacks(self):
        for added in ("        module = importlib.import_module(name)",
                      "        __import__(name)",
                      "        value = eval(text)",
                      "        from sqlglot.optimizer import simplify"):
            with self.subTest(added=added):
                selection, hard = self.select(patch_text(GENERATOR, added=added), "balanced")
                self.assertTrue(hard and hard[0].startswith("patch: "))
                self.assertEqual(selection.numbers, ALL)


class MalformedPatches(PolicyFixture):
    CASES = {
        "empty": "",
        "not a diff": "hello world\n",
        "bad hunk header": f"diff --git a/{GENERATOR} b/{GENERATOR}\n--- a/{GENERATOR}\n+++ b/{GENERATOR}\n@@ nonsense @@\n+x\n",
        "truncated hunk": f"diff --git a/{GENERATOR} b/{GENERATOR}\n--- a/{GENERATOR}\n+++ b/{GENERATOR}\n@@ -5,3 +5,3 @@\n-a\n+b\n",
        "path traversal": patch_text("../etc/passwd"),
        "binary": f"diff --git a/{GENERATOR} b/{GENERATOR}\nBinary files a/{GENERATOR} and b/{GENERATOR} differ\n",
    }

    def test_malformed_patches_fall_back_in_every_policy(self):
        for name, text in self.CASES.items():
            for mode in SELECTIVE:
                with self.subTest(case=name, mode=mode):
                    selection, hard = self.select(text, mode)
                    self.assertTrue(selection.fell_back)
                    self.assertEqual(selection.numbers, ALL)

    def test_undecodable_patch_falls_back(self):
        selection, hard = self.select(b"\xff\xfe\x00", "balanced")
        self.assertEqual(hard, ["patch: no file changes parsed"])
        self.assertEqual(selection.numbers, ALL)


class RunEndToEnd(PolicyFixture):
    def prepare(self):
        data = self.root / "data"
        (data / "commits").mkdir(parents=True)
        (data / "tests.json").write_text(json.dumps({"tests": TESTS}), encoding="utf-8")
        (data / "commits/narrow.patch").write_text(patch_text(GENERATOR), encoding="utf-8")
        (data / "commits/broken.patch").write_text("garbage\n", encoding="utf-8")
        maps = self.root / "maps"
        maps.mkdir()
        return data, maps

    def run_policy(self, data, maps, policy):
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            run(data, maps, policy)
        lines = [json.loads(line) for line in stdout.getvalue().splitlines()]
        return {line["commit"]: {n for w in line["workers"] for n in w} for line in lines}, stderr.getvalue()

    def test_missing_artifacts_fall_back_for_every_commit_and_policy(self):
        data, maps = self.prepare()
        for policy in selector.POLICIES:
            with self.subTest(policy=policy):
                chosen, log = self.run_policy(data, maps, policy)
                self.assertEqual(chosen, {"narrow": ALL, "broken": ALL})
                self.assertIn("full_suite_fallbacks=2", log)

    def test_valid_artifacts_narrow_only_in_selective_policies(self):
        data, maps = self.prepare()
        with mock.patch.object(selector, "load_analysis_maps", return_value=(graph(True), coverage())):
            chosen, _ = self.run_policy(data, maps, "balanced")
            self.assertEqual(chosen, {"narrow": POSTGRES_ONLY, "broken": ALL})
            chosen, _ = self.run_policy(data, maps, "fallback")
            self.assertEqual(chosen, {"narrow": ALL, "broken": ALL})

    def test_analysis_crash_runs_the_full_suite(self):
        data, maps = self.prepare()
        with mock.patch.object(selector, "load_analysis_maps", return_value=(graph(), coverage())), \
                mock.patch.object(selector, "explain_selection", side_effect=RuntimeError("boom")):
            chosen, log = self.run_policy(data, maps, "balanced")
        self.assertEqual(chosen, {"narrow": ALL, "broken": ALL})
        self.assertIn("analysis error: RuntimeError", log)

    def test_policy_configuration(self):
        self.assertEqual(configured_policy(None), selector.DEFAULT_POLICY)
        self.assertEqual(configured_policy("balanced"), "balanced")
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(configured_policy("aggressive"), selector.DEFAULT_POLICY)
        self.assertNotIn("aggressive", selector.POLICIES)


if __name__ == "__main__":
    unittest.main()
