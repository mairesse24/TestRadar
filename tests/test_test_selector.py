"""Unit tests for analyzer/test_selector.py.

Run with ``python3 -m unittest tests.test_test_selector -v`` or
``python3 -m unittest discover -s tests -p 'test_test_selector.py' -v``
(pytest also collects it). The module is loaded by file path so the tests do
not need ``analyzer/__init__.py``.
"""

import importlib.util
import json
import os
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_SPEC = importlib.util.spec_from_file_location(
    "analyzer_test_selector", ROOT / "analyzer" / "test_selector.py"
)
ts = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(ts)


def change(path, status="modified", old_path="same", new_path="same", uncertain=False, lines=1):
    return {
        "path": path,
        "old_path": path if old_path == "same" else old_path,
        "new_path": path if new_path == "same" else new_path,
        "status": status,
        "old_lines": list(range(10, 10 + lines)),
        "new_lines": list(range(10, 10 + lines)),
        "added_text": ["x = 1"] * lines,
        "removed_text": ["x = 2"] * lines,
        "uncertain": uncertain,
    }


def T(number, test_id, seconds=0.01):
    return {"number": number, "id": test_id, "file": test_id.split("::")[0], "seconds": seconds}


# A small synthetic suite shaped like sqlglot's.
SUITE = [
    T(0, "tests/dialects/test_postgres.py::TestPostgres::test_postgres"),
    T(1, "tests/dialects/test_postgres.py::TestPostgres::test_ddl"),
    T(2, "tests/dialects/test_redshift.py::TestRedshift::test_redshift"),
    T(3, "tests/dialects/test_duckdb.py::TestDuckDB::test_duckdb"),
    T(4, "tests/dialects/test_dialect.py::TestDialect::test_enum"),
    T(5, "tests/test_optimizer.py::TestOptimizer::test_simplify", 2.0),
    T(6, "tests/test_optimizer.py::TestOptimizer::test_pushdown_predicates"),
    T(7, "tests/test_executor.py::TestExecutor::test_execute_tpch", 5.0),
    T(8, "tests/test_parser.py::TestParser::test_max_nodes", 30.0),
    T(9, "tests/test_diff.py::TestDiff::test_simple"),
    T(10, "tests/test_lineage.py::TestLineage::test_lineage"),
    T(11, "tests/test_transpile.py::TestTranspile::test_identity"),
    T(12, "tests/test_serde.py::TestSerde::test_serde"),
    T(13, "tests/dialects/test_materialize.py::TestMaterialize::test_materialize"),
    T(14, "tests/test_dialect_imports.py::TestDialectImports::test_dialect_import_no_deadlock"),
    T(15, "tests/test_generator.py::TestGenerator::test_generator_memory", 6.0),
    T(16, "tests/dialects/test_risingwave.py::TestRisingWave::test_risingwave"),
]
ALL = {t["number"] for t in SUITE}


def sel(changes, covered=(), impacted=(), mode="safe", **kw):
    return ts.explain_selection(changes, SUITE, set(impacted), set(covered), mode=mode, **kw)


class GlobalFallbacks(unittest.TestCase):
    def test_analysis_uncertain(self):
        r = sel([change("tests/test_diff.py")], analysis_uncertain=True)
        self.assertTrue(r.fell_back)
        self.assertEqual(r.numbers, ALL)
        self.assertEqual(
            ts.select_tests([change("README.md")], SUITE, set(), set(), analysis_uncertain=True), ALL
        )

    def test_uncertain_change(self):
        r = sel([change("tests/test_diff.py", uncertain=True)], mode="aggressive")
        self.assertTrue(r.fell_back)
        self.assertEqual(r.numbers, ALL)

    def test_binary_and_unknown_status(self):
        for status in ("binary", "unknown", "weird", None):
            with self.subTest(status=status):
                r = sel([change("tests/fixtures/optimizer/x.sql", status=status)], mode="balanced")
                self.assertEqual(r.numbers, ALL)
                self.assertTrue(r.fell_back)

    def test_no_changes(self):
        self.assertEqual(sel([]).numbers, ALL)

    def test_unknown_path(self):
        for path in ("weird/thing.py", "sqlglotrs/src/lib.rs", "something.bin"):
            with self.subTest(path=path):
                self.assertEqual(sel([change(path)], mode="aggressive").numbers, ALL)

    def test_unsafe_paths(self):
        for path in ("../etc/passwd", "/abs/sqlglot/parser.py", "sqlglot/../../x.py"):
            with self.subTest(path=path):
                r = sel([change(path)], mode="balanced")
                self.assertEqual(r.numbers, ALL)

    def test_config_files(self):
        for path in ("setup.py", "pyproject.toml", "setup.cfg", "requirements.txt", "Makefile", "tests/conftest.py"):
            with self.subTest(path=path):
                self.assertEqual(sel([change(path)], mode="aggressive").numbers, ALL)

    def test_broad_refactor_many_files(self):
        changes = [change(f"tests/fixtures/optimizer/pushdown_predicates.sql") for _ in range(13)]
        r = sel(changes, mode="balanced")
        self.assertTrue(r.fell_back)
        self.assertIn("broad", r.reasons[0])

    def test_broad_refactor_many_lines(self):
        r = sel([change("tests/test_diff.py", lines=700)], mode="balanced")
        self.assertTrue(r.fell_back)

    def test_malformed_change(self):
        self.assertEqual(sel(["not a dict"]).numbers, ALL)
        self.assertEqual(sel([{"status": "modified"}]).numbers, ALL)

    def test_unknown_mode_is_safe(self):
        self.assertEqual(sel([change("README.md")], mode="yolo").numbers, ALL)


class CoreAndCoverage(unittest.TestCase):
    def test_core_module_change_is_all(self):
        for path in (
            "sqlglot/parser.py",
            "sqlglot/tokens.py",
            "sqlglot/generator.py",
            "sqlglot/expressions/ddl.py",
            "sqlglot/dialects/dialect.py",
            "sqlglot/helper.py",
            "sqlglot/__init__.py",
            "sqlglot/serde.py",
            "sqlglot/optimizer/scope.py",
        ):
            with self.subTest(path=path):
                for mode in ("safe", "balanced"):
                    r = sel([change(path)], covered={SUITE[0]["id"]}, mode=mode)
                    self.assertEqual(r.numbers, ALL)
                    self.assertTrue(r.fell_back)

    def test_core_aggressive_needs_coverage(self):
        self.assertEqual(sel([change("sqlglot/parser.py")], mode="aggressive").numbers, ALL)
        r = sel(
            [change("sqlglot/parser.py")],
            covered={"tests/test_parser.py::TestParser::test_max_nodes"},
            mode="aggressive",
        )
        self.assertFalse(r.fell_back)
        self.assertIn(8, r.numbers)

    def test_missing_coverage_falls_back_in_safe_mode(self):
        r = sel([change("sqlglot/diff.py")])
        self.assertTrue(r.fell_back)
        self.assertIn("coverage missing", r.reasons[0])

    def test_narrow_change_with_coverage(self):
        r = sel([change("sqlglot/diff.py")], covered={"tests/test_lineage.py::TestLineage::test_lineage"})
        self.assertFalse(r.fell_back)
        # convention file (test_diff.py) + coverage hit (lineage)
        self.assertEqual(r.numbers, {9, 10})

    def test_narrow_change_without_coverage_balanced(self):
        r = sel([change("sqlglot/diff.py")], mode="balanced")
        self.assertFalse(r.fell_back)
        self.assertEqual(r.numbers, {9})

    def test_coverage_ids_not_in_tests_are_ignored(self):
        r = sel([change("sqlglot/diff.py")], covered={"tests/nope.py::X::y"}, mode="balanced")
        self.assertEqual(r.numbers, {9})
        self.assertTrue(sel([change("sqlglot/diff.py")], covered={"tests/nope.py::X::y"}).fell_back)

    def test_impacted_test_files_are_selected(self):
        r = sel(
            [change("sqlglot/diff.py")],
            impacted={"sqlglot/diff.py", "tests/test_transpile.py"},
            mode="balanced",
        )
        self.assertEqual(r.numbers, {9, 11})

    def test_optimizer_pass_selects_optimizer_and_executor(self):
        r = sel([change("sqlglot/optimizer/eliminate_joins.py")], mode="balanced")
        self.assertTrue({5, 6, 7, 10} <= r.numbers)
        self.assertNotIn(8, r.numbers)

    def test_module_without_any_known_tests_falls_back(self):
        r = sel([change("sqlglot/brand_new_area.py")], mode="balanced")
        self.assertTrue(r.fell_back)

    def test_added_source_module_falls_back_unless_aggressive(self):
        c = change("sqlglot/diff.py", status="added", old_path=None)
        self.assertEqual(sel([c], mode="balanced").numbers, ALL)
        self.assertEqual(sel([c], mode="aggressive").numbers, {9})

    def test_comment_only_python_change_is_not_harmless(self):
        c = change("sqlglot/diff.py")
        c["added_text"] = ["# just a comment"]
        c["removed_text"] = []
        self.assertTrue(sel([c], mode="balanced").numbers)
        self.assertEqual(sel([c]).numbers, ALL)


class Dialects(unittest.TestCase):
    def test_dialect_change_selects_own_and_inheriting_dialects(self):
        r = sel([change("sqlglot/generators/postgres.py")], mode="balanced")
        self.assertFalse(r.fell_back)
        # postgres, redshift / materialize / risingwave inherit from it
        self.assertTrue({0, 1, 2, 13, 16} <= r.numbers, r.numbers)
        # dialect infrastructure + tests that iterate every dialect
        self.assertTrue({4, 14, 15} <= r.numbers)
        self.assertNotIn(8, r.numbers)

    def test_leaf_dialect_does_not_select_its_parent(self):
        r = sel([change("sqlglot/parsers/redshift.py")], mode="balanced")
        self.assertIn(2, r.numbers)
        self.assertNotIn(13, r.numbers)  # materialize is a sibling, not a child

    def test_dialect_change_with_coverage_in_safe_mode(self):
        r = sel([change("sqlglot/dialects/duckdb.py")], covered={SUITE[5]["id"]})
        self.assertFalse(r.fell_back)
        self.assertTrue({3, 5} <= r.numbers)

    def test_unknown_dialect_name_falls_back(self):
        r = sel([change("sqlglot/dialects/newsql.py")], mode="balanced")
        self.assertTrue(r.fell_back)


class TestSideChanges(unittest.TestCase):
    def test_test_file_change_selects_that_file(self):
        r = sel([change("tests/dialects/test_postgres.py")])
        self.assertFalse(r.fell_back)
        self.assertEqual(r.numbers, {0, 1})

    def test_deleted_test_file_selects_its_tests(self):
        c = change("tests/test_diff.py", status="deleted", new_path=None)
        self.assertEqual(sel([c]).numbers, {9})

    def test_renamed_test_file_selects_old_tests(self):
        c = change("tests/test_diff2.py", status="renamed", old_path="tests/test_diff.py")
        self.assertEqual(sel([c], mode="balanced").numbers, {9})
        self.assertEqual(sel([c]).numbers, ALL)  # new uncollected module: safe is conservative

    def test_shared_test_module(self):
        c = change("tests/dialects/test_dialect.py")
        self.assertEqual(sel([c]).numbers, ALL)
        r = sel([c], mode="balanced")
        self.assertEqual(r.numbers, {0, 1, 2, 3, 4, 13, 16})

    def test_test_helpers_fall_back(self):
        for path in ("tests/helpers.py", "tests/__init__.py", "tests/dialects/__init__.py", "tests/conftest.py"):
            with self.subTest(path=path):
                self.assertEqual(sel([change(path)], mode="aggressive").numbers, ALL)

    def test_fixture_data_change(self):
        c = change("tests/fixtures/optimizer/pushdown_predicates.sql")
        self.assertEqual(sel([c]).numbers, {5, 6})  # safe: whole consumer file
        self.assertEqual(sel([c], mode="balanced").numbers, {6})  # only the loading test

    def test_fixture_loaded_in_setup_selects_whole_file(self):
        c = change("tests/fixtures/optimizer/tpc-h/lineitem.csv.gz")
        self.assertEqual(sel([c], mode="balanced").numbers, {7})

    def test_unknown_fixture_falls_back(self):
        c = change("tests/fixtures/optimizer/brand_new.sql")
        self.assertEqual(sel([c], mode="balanced").numbers, ALL)


class HarmlessChanges(unittest.TestCase):
    def test_docs_only_is_empty(self):
        for path in ("README.md", "CHANGELOG.md", "docs/sqlglot/index.html", "posts/intro.md", "LICENSE"):
            with self.subTest(path=path):
                r = sel([change(path)])
                self.assertEqual(r.numbers, set())
                self.assertFalse(r.fell_back)

    def test_docs_plus_code_is_not_empty(self):
        r = sel([change("README.md"), change("tests/test_diff.py")])
        self.assertEqual(r.numbers, {9})

    def test_ci_config(self):
        c = change(".github/workflows/ci.yml")
        self.assertEqual(sel([c]).numbers, ALL)
        self.assertEqual(sel([c], mode="balanced").numbers, set())

    def test_markdown_inside_package_is_not_harmless(self):
        self.assertEqual(sel([change("sqlglot/notes.md")]).numbers, ALL)


class OutputGuarantees(unittest.TestCase):
    def test_contract_signature_and_types(self):
        out = ts.select_tests([change("tests/test_diff.py")], SUITE, set(), set())
        self.assertIsInstance(out, set)
        self.assertTrue(all(isinstance(n, int) for n in out))
        self.assertEqual(out, {9})

    def test_only_valid_numbers(self):
        # Static tables name tests absent from this suite; they must not leak.
        for path in ("sqlglot/generators/snowflake.py", "tests/fixtures/identity.sql", "sqlglot/optimizer/qualify.py"):
            with self.subTest(path=path):
                for mode in ts.MODES:
                    out = ts.select_tests([change(path)], SUITE, set(), set(), mode=mode)
                    self.assertTrue(out <= ALL)

    def test_deterministic(self):
        changes = [change("sqlglot/generators/postgres.py"), change("tests/fixtures/optimizer/simplify.sql")]
        first = sel(changes, mode="balanced")
        for _ in range(5):
            again = sel(list(reversed(changes)), mode="balanced")
            self.assertEqual(again.numbers, first.numbers)
            self.assertEqual(sorted(again.reasons), sorted(first.reasons))

    def test_never_raises(self):
        weird_suite = SUITE + [{"number": True}, {"number": "3"}, "junk", {"number": 99, "file": None}]
        out = ts.select_tests([change("sqlglot/parser.py")], weird_suite, set(), set())
        self.assertEqual(out, ALL | {99})
        out = ts.select_tests(None, SUITE, None, None)
        self.assertEqual(out, ALL)

    def test_promotes_near_total_selection_to_all(self):
        changes = [change("tests/test_parser.py"), change("tests/test_executor.py"), change("tests/test_generator.py")]
        r = sel(changes)
        self.assertTrue(r.fell_back)  # these three files are >85% of the synthetic suite's seconds
        self.assertEqual(r.numbers, ALL)

    def test_modes_are_monotone_on_examples(self):
        examples = [
            [change("sqlglot/diff.py")],
            [change("sqlglot/generators/postgres.py")],
            [change("tests/fixtures/optimizer/simplify.sql")],
            [change(".github/workflows/x.yml")],
        ]
        for changes in examples:
            with self.subTest(path=changes[0]["path"]):
                safe = sel(changes, mode="safe").numbers
                balanced = sel(changes, mode="balanced").numbers
                aggressive = sel(changes, mode="aggressive").numbers
                self.assertTrue(balanced <= safe)
                self.assertTrue(aggressive <= balanced)


class RealSampleTests(unittest.TestCase):
    """Uses the real sample/tests.json (never answers.json)."""

    @classmethod
    def setUpClass(cls):
        path = ROOT / "sample" / "tests.json"
        if not path.exists():
            raise unittest.SkipTest("sample/tests.json not present")
        cls.tests = json.loads(path.read_text(encoding="utf-8"))["tests"]
        cls.all = {t["number"] for t in cls.tests}

    def test_hand_built_changes_are_valid(self):
        cases = [
            [change("sqlglot/generators/clickhouse.py")],
            [change("sqlglot/parsers/postgres.py")],
            [change("sqlglot/optimizer/eliminate_joins.py")],
            [change("tests/fixtures/optimizer/simplify.sql")],
            [change("tests/dialects/test_duckdb.py")],
            [change("sqlglot/parser.py")],
            [change("README.md")],
        ]
        for changes in cases:
            for mode in ts.MODES:
                with self.subTest(path=changes[0]["path"], mode=mode):
                    out = ts.select_tests(changes, self.tests, {changes[0]["path"]}, set(), mode=mode)
                    self.assertIsInstance(out, set)
                    self.assertTrue(out <= self.all)

    def test_static_tables_reference_real_tests(self):
        ids = {t["id"] for t in self.tests}
        files = {t["file"] for t in self.tests}
        for table in (ts.DIALECT_TEST_ID_MENTIONS, ts.FIXTURE_TEST_IDS):
            for values in table.values():
                self.assertTrue(set(values) <= ids)
        self.assertTrue(set(ts.ALL_DIALECT_TEST_IDS) <= ids)
        for table in (ts.DIALECT_TEST_FILE_MENTIONS, ts.FIXTURE_SETUP_FILES, ts.MODULE_TEST_IMPORTERS):
            for values in table.values():
                self.assertTrue(set(values) <= files)

    def test_postgres_change_reaches_redshift_tests(self):
        out = ts.select_tests([change("sqlglot/generators/postgres.py")], self.tests, set(), set(), mode="balanced")
        redshift = {t["number"] for t in self.tests if t["file"] == "tests/dialects/test_redshift.py"}
        self.assertTrue(redshift <= out)
        self.assertLess(len(out), len(self.all))

    def test_safe_mode_without_coverage_is_all(self):
        out = ts.select_tests([change("sqlglot/generators/clickhouse.py")], self.tests, set(), set())
        self.assertEqual(out, self.all)

    @unittest.skipUnless(os.environ.get("SQLGLOT_SRC"), "set SQLGLOT_SRC to a pinned sqlglot checkout")
    def test_generated_tables_match_pinned_source(self):
        maps = ts.derive_static_maps(os.environ["SQLGLOT_SRC"], self.tests)
        for name, value in maps.items():
            with self.subTest(table=name):
                self.assertEqual(getattr(ts, name), value)


if __name__ == "__main__":
    unittest.main()
