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
        r = sel([change("sqlglot/lineage.py")])
        self.assertTrue(r.fell_back)
        self.assertIn("coverage missing", r.reasons[0])

    def test_narrow_change_with_coverage(self):
        r = sel([change("sqlglot/lineage.py")], covered={"tests/test_diff.py::TestDiff::test_simple"})
        self.assertFalse(r.fell_back)
        # convention file (test_lineage.py) + coverage hit (diff)
        self.assertEqual(r.numbers, {9, 10})

    def test_narrow_change_without_coverage_balanced(self):
        r = sel([change("sqlglot/lineage.py")], mode="balanced")
        self.assertFalse(r.fell_back)
        self.assertEqual(r.numbers, {10})

    def test_coverage_ids_not_in_tests_are_ignored(self):
        r = sel([change("sqlglot/lineage.py")], covered={"tests/nope.py::X::y"}, mode="balanced")
        self.assertEqual(r.numbers, {10})
        self.assertTrue(sel([change("sqlglot/lineage.py")], covered={"tests/nope.py::X::y"}).fell_back)

    def test_impacted_test_files_are_selected(self):
        r = sel(
            [change("sqlglot/lineage.py")],
            impacted={"sqlglot/lineage.py", "tests/test_transpile.py"},
            mode="balanced",
        )
        self.assertEqual(r.numbers, {10, 11})

    def test_optimizer_pass_selects_optimizer_and_executor(self):
        r = sel([change("sqlglot/optimizer/eliminate_joins.py")], mode="balanced")
        self.assertTrue({5, 6, 7, 10} <= r.numbers)
        self.assertNotIn(8, r.numbers)

    def test_module_without_any_known_tests_falls_back(self):
        r = sel([change("sqlglot/brand_new_area.py")], mode="balanced")
        self.assertTrue(r.fell_back)

    def test_added_source_module_falls_back_unless_aggressive(self):
        c = change("sqlglot/lineage.py", status="added", old_path=None)
        self.assertEqual(sel([c], mode="balanced").numbers, ALL)
        self.assertEqual(sel([c], mode="aggressive").numbers, {10})

    def test_comment_only_python_change_is_not_harmless(self):
        c = change("sqlglot/lineage.py")
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
            [change("sqlglot/lineage.py")],
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


# A second synthetic suite for dependency / false-negative cases.
SUITE2 = [
    T(0, "tests/dialects/test_presto.py::TestPresto::test_presto"),
    T(1, "tests/dialects/test_trino.py::TestTrino::test_trino"),
    T(2, "tests/dialects/test_athena.py::TestAthena::test_athena"),
    T(3, "tests/dialects/test_dune.py::TestDune::test_dune"),
    T(4, "tests/dialects/test_teradata.py::TestTeradata::test_teradata"),
    T(5, "tests/dialects/test_tsql.py::TestTSQL::test_tsql"),
    T(6, "tests/dialects/test_fabric.py::TestFabric::test_fabric"),
    T(7, "tests/test_optimizer.py::TestOptimizer::test_pushdown_predicates", 2.0),
    T(8, "tests/test_executor.py::TestExecutor::test_execute_tpch", 5.0),
    T(9, "tests/test_parser.py::TestParser::test_max_nodes", 30.0),
    T(10, "tests/test_diff.py::TestDiff::test_simple"),
    T(11, "tests/test_jsonpath.py::TestJsonpath::test_cts_file"),
    T(12, "tests/test_transpile.py::TestTranspile::test_identity"),
    T(13, "tests/dialects/test_spark.py::TestSpark::test_spark"),
    T(14, "tests/dialects/test_databricks.py::TestDatabricks::test_databricks"),
    T(15, "tests/dialects/test_hive.py::TestHive::test_hive"),
    T(16, "tests/test_lineage.py::TestLineage::test_lineage"),
]
ALL2 = {t["number"] for t in SUITE2}


def sel2(changes, covered=(), impacted=(), mode="safe", **kw):
    return ts.explain_selection(changes, SUITE2, set(impacted), set(covered), mode=mode, **kw)


class DerivedDependencyMaps(unittest.TestCase):
    """The static maps must capture imports that a naive scan misses."""

    def test_function_level_import_is_recorded(self):
        self.assertIn("sqlglot/generator.py", ts.LAZY_SOURCE_IMPORTERS["sqlglot/dialects/tsql.py"])
        self.assertIn("sqlglot/expressions/core.py", ts.LAZY_SOURCE_IMPORTERS["sqlglot/serde.py"])

    def test_derived_core_includes_lazily_used_modules(self):
        for path in ("sqlglot/serde.py", "sqlglot/transforms.py", "sqlglot/dialects/tsql.py",
                     "sqlglot/optimizer/simplify.py", "sqlglot/optimizer/scope.py"):
            with self.subTest(path=path):
                self.assertIn(path, ts.DERIVED_CORE_MODULES)
        for path in ("sqlglot/lineage.py", "sqlglot/dialects/postgres.py", "sqlglot/optimizer/eliminate_joins.py"):
            with self.subTest(path=path):
                self.assertNotIn(path, ts.DERIVED_CORE_MODULES)

    def test_modules_run_by_package_import_are_core(self):
        # ``import sqlglot`` executes sqlglot/diff.py, so an import-time
        # failure there breaks every test.
        self.assertIn("sqlglot/diff.py", ts.DERIVED_CORE_MODULES)
        r = sel2([change("sqlglot/diff.py")], mode="balanced")
        self.assertTrue(r.fell_back)
        self.assertEqual(r.numbers, ALL2)

    def test_lazy_reexport_is_resolved(self):
        # executor imports ``optimize`` through optimizer/__init__'s __getattr__ table
        self.assertIn("sqlglot/executor/__init__.py", ts.SOURCE_IMPORTERS["sqlglot/optimizer/optimizer.py"])

    def test_by_name_dialect_use_is_a_dependency(self):
        # parsers/presto.py formats time with the "teradata" dialect by name
        self.assertIn("teradata", ts.DIALECT_BASES["presto"])
        self.assertIn("sqlglot/parsers/presto.py", ts.SOURCE_IMPORTERS["sqlglot/dialects/teradata.py"])

    def test_registry_modules_are_not_importers(self):
        for importers in ts.SOURCE_IMPORTERS.values():
            self.assertFalse(set(importers) & ts.REGISTRY_MODULES)

    def test_shared_test_modules_are_derived(self):
        self.assertIn("tests/dialects/test_dialect.py", ts.SHARED_TEST_IMPORTERS)
        self.assertGreater(len(ts.SHARED_TEST_IMPORTERS["tests/dialects/test_dialect.py"]), 20)


class SharedAndDynamic(unittest.TestCase):
    def test_lazily_shared_dialect_is_all_even_with_coverage(self):
        for path in ("sqlglot/dialects/tsql.py", "sqlglot/generators/tsql.py"):
            for mode in ("safe", "balanced"):
                with self.subTest(path=path, mode=mode):
                    r = sel2([change(path)], covered={SUITE2[5]["id"]}, mode=mode)
                    self.assertTrue(r.fell_back)
                    self.assertEqual(r.numbers, ALL2)
        r = sel2([change("sqlglot/dialects/tsql.py")])
        self.assertIn("sqlglot/generator.py", r.reasons[0])

    def test_by_name_dialect_dependency_reaches_dependents(self):
        r = sel2([change("sqlglot/dialects/teradata.py")], mode="balanced")
        self.assertFalse(r.fell_back)
        # presto uses teradata by name; trino/athena/dune build on presto
        self.assertTrue({0, 1, 2, 3, 4} <= r.numbers, r.numbers)

    def test_dialect_used_inside_optimizer_selects_optimizer_tests(self):
        # optimizer/pushdown_predicates.py imports dialects.presto inside a function
        r = sel2([change("sqlglot/dialects/presto.py")], mode="balanced")
        self.assertTrue({0, 1, 2, 3, 7, 8} <= r.numbers, r.numbers)
        self.assertNotIn(9, r.numbers)
        self.assertTrue(any("pushdown_predicates" in x for x in r.reasons))

    def test_cyclic_dialect_dependencies_terminate(self):
        r = sel2([change("sqlglot/parsers/spark2.py")], mode="balanced")
        self.assertTrue({13, 14} <= r.numbers)  # spark2 <-> spark reference each other

    def test_shared_test_helper_balanced_selects_importers(self):
        r = sel2([change("tests/helpers.py")], mode="balanced")
        self.assertEqual(r.numbers, ALL2)  # helpers.py is test infrastructure
        r = sel2([change("tests/helpers.py")])
        self.assertTrue(r.fell_back)


class IncompleteAnalysisNeverSkips(unittest.TestCase):
    """Any sign of incomplete analysis must yield every test, in every mode."""

    INCOMPLETE = [
        ("analysis_uncertain", [change("sqlglot/lineage.py")], {"analysis_uncertain": True}),
        ("docs + analysis_uncertain", [change("README.md")], {"analysis_uncertain": True}),
        ("uncertain change", [change("sqlglot/lineage.py", uncertain=True)], {}),
        ("uncertain fixture", [change("tests/fixtures/identity.sql", uncertain=True)], {}),
        ("binary", [change("sqlglot/lineage.py", status="binary")], {}),
        ("unknown status", [change("sqlglot/lineage.py", status="unknown")], {}),
        ("missing status", [{"path": "sqlglot/lineage.py"}], {}),
        ("no path at all", [{"status": "modified"}], {}),
        ("None paths", [{"path": None, "old_path": None, "new_path": None, "status": "modified"}], {}),
        ("empty change list", [], {}),
        ("None change list", None, {}),
        ("unknown top-level path", [change("vendor/lib.py")], {}),
        ("unknown fixture", [change("tests/fixtures/new_dir/x.sql")], {}),
        ("test infrastructure", [change("tests/conftest.py")], {}),
        ("config", [change("pyproject.toml")], {}),
    ]

    def test_every_mode_returns_everything(self):
        for label, changes, kw in self.INCOMPLETE:
            for mode in ts.MODES:
                with self.subTest(case=label, mode=mode):
                    r = ts.explain_selection(changes, SUITE2, set(), set(), mode=mode, **kw)
                    self.assertTrue(r.fell_back)
                    self.assertEqual(r.numbers, ALL2)

    def test_safe_and_balanced_cases(self):
        cases = [
            ("unknown source module", [change("sqlglot/optimizer/not_in_map.py")]),
            ("deleted source", [change("sqlglot/lineage.py", status="deleted", new_path=None)]),
            ("renamed source", [change("sqlglot/lineage2.py", status="renamed", old_path="sqlglot/lineage.py")]),
            ("copied source", [change("sqlglot/lineage2.py", status="copied", old_path="sqlglot/lineage.py")]),
            ("core + narrow", [change("sqlglot/lineage.py"), change("sqlglot/parser.py")]),
        ]
        for label, changes in cases:
            for mode in ("safe", "balanced"):
                with self.subTest(case=label, mode=mode):
                    r = sel2(changes, mode=mode)
                    self.assertTrue(r.fell_back)
                    self.assertEqual(r.numbers, ALL2)

    def test_safe_needs_coverage_for_source(self):
        r = sel2([change("sqlglot/lineage.py")])
        self.assertEqual(r.numbers, ALL2)
        r = sel2([change("sqlglot/lineage.py")], covered={"tests/unknown.py::X::y", "  "})
        self.assertEqual(r.numbers, ALL2)
        self.assertIn("covered test ids", r.reasons[0])

    def test_coverage_never_removes_static_candidates(self):
        base = sel2([change("sqlglot/dialects/presto.py")], mode="balanced").numbers
        with_cov = sel2([change("sqlglot/dialects/presto.py")], covered={SUITE2[10]["id"]}, mode="balanced").numbers
        self.assertTrue(base <= with_cov)
        self.assertIn(10, with_cov)


class StructuralAndNewModules(unittest.TestCase):
    def test_module_level_import_edit_is_all(self):
        c = change("sqlglot/lineage.py")
        c["added_text"] = ["from sqlglot.helper import nothing_here"]
        for mode in ("safe", "balanced"):
            with self.subTest(mode=mode):
                self.assertEqual(sel2([c], covered={SUITE2[10]["id"]}, mode=mode).numbers, ALL2)

    def test_function_level_import_edit_is_not_structural(self):
        c = change("sqlglot/lineage.py")
        c["added_text"] = ["    from sqlglot.optimizer import simplify"]
        self.assertFalse(sel2([c], mode="balanced").fell_back)

    def test_removed_or_renamed_definition_is_all(self):
        c = change("sqlglot/lineage.py")
        c["removed_text"] = ["def diff(source, target):"]
        c["added_text"] = ["def diff2(source, target):"]
        self.assertEqual(sel2([c], mode="balanced").numbers, ALL2)
        c["added_text"] = ["def diff(source, target, extra=None):"]
        self.assertFalse(sel2([c], mode="balanced").fell_back)

    def test_shared_class_state_mutation_is_all(self):
        for line in (
            '    PostgresParser.FUNCTIONS.pop("GET_BIT", None)',
            '    HiveGenerator.TYPE_MAPPING[exp.DType.TEXT] = "VARCHAR"',
            '    generator.Generator.TYPE_MAPPING[exp.DType.LONGTEXT] = "CLOB"',
            'exp.Column.output_name = property(lambda self: "")',
        ):
            c = change("sqlglot/lineage.py")
            c["added_text"] = [line]
            with self.subTest(line=line):
                self.assertEqual(sel2([c], mode="balanced").numbers, ALL2)

    def test_local_state_edits_are_not_shared_class_state(self):
        for line in (
            '        expression.args["kind"] = kind',
            "        self.TYPE_MAPPING = {}",
            "        if Parser.X == 1:",
            '    TYPE_MAPPING = {',
        ):
            c = change("sqlglot/lineage.py")
            c["added_text"] = [line]
            with self.subTest(line=line):
                self.assertFalse(sel2([c], mode="balanced").fell_back)

    def test_new_unreferenced_module(self):
        c = change("sqlglot/optimizer/extra_rules.py", status="added", old_path=None)
        c["removed_text"] = []
        self.assertEqual(sel2([c]).numbers, ALL2)
        r = sel2([c], mode="balanced")
        self.assertFalse(r.fell_back)
        self.assertEqual(r.numbers, set())
        self.assertIn("not referenced", r.reasons[0])

    def test_new_module_used_by_changed_module(self):
        new = change("sqlglot/optimizer/extra_rules.py", status="added", old_path=None)
        user = change("sqlglot/lineage.py")
        user["added_text"] = ["    from sqlglot.optimizer.extra_rules import drop_all"]
        r = sel2([new, user], mode="balanced")
        self.assertIn(16, r.numbers)
        self.assertTrue(any("used by other changed modules" in x for x in r.reasons))
        # the same wiring at module level is structural
        user["added_text"] = ["from sqlglot.optimizer.extra_rules import drop_all"]
        self.assertEqual(sel2([new, user], mode="balanced").numbers, ALL2)

    def test_aggressive_treats_rename_as_modification(self):
        c = change("sqlglot/lineage2.py", status="renamed", old_path="sqlglot/lineage.py")
        r = sel2([c], mode="aggressive")
        self.assertIn(16, r.numbers)


class MultiModuleAndRobustness(unittest.TestCase):
    def test_multi_module_union(self):
        r = sel2([change("sqlglot/lineage.py"), change("sqlglot/parsers/teradata.py")], mode="balanced")
        self.assertTrue({16, 0, 4} <= r.numbers)
        r2 = sel2([change("sqlglot/parsers/teradata.py"), change("sqlglot/lineage.py")], mode="balanced")
        self.assertEqual(r.numbers, r2.numbers)

    def test_impacted_paths_are_normalised(self):
        r = sel2([change("sqlglot/lineage.py")], impacted={"./tests\\test_jsonpath.py", "b/tests/test_diff.py"},
                 mode="balanced")
        self.assertTrue({10, 11, 16} <= r.numbers)

    def test_impacted_source_modules_add_their_tests(self):
        r = sel2([change("sqlglot/lineage.py")], impacted={"sqlglot/jsonpath.py", "sqlglot/lineage.py"},
                 mode="balanced")
        self.assertIn(16, r.numbers)  # lineage (non-core) -> tests/test_lineage.py
        self.assertNotEqual(r.numbers, ALL2)  # jsonpath is core: not used to expand

    def test_covered_ids_are_normalised(self):
        cov = {" tests\\test_jsonpath.py::TestJsonpath::test_cts_file ", "./tests/test_diff.py::TestDiff::test_simple[1]"}
        r = sel2([change("sqlglot/lineage.py")], covered=cov)
        self.assertFalse(r.fell_back)
        self.assertTrue({10, 11, 16} <= r.numbers)

    def test_path_variants(self):
        variants = [
            {"path": "sqlglot/lineage.py", "status": "modified"},
            {"old_path": "sqlglot/lineage.py", "new_path": "sqlglot/lineage.py", "status": "modified"},
            {"path": "./sqlglot\\lineage.py", "status": "modified", "added_text": None, "removed_text": None},
            {"path": "b/sqlglot/lineage.py", "old_path": "a/sqlglot/lineage.py", "status": "modified"},
        ]
        for c in variants:
            with self.subTest(change=c):
                self.assertEqual(sel2([c], mode="balanced").numbers, {4, 16})

    def test_index_cache_is_safe(self):
        suite = [dict(t) for t in SUITE2]
        first = ts.select_tests([change("sqlglot/lineage.py")], suite, set(), set(), mode="balanced")
        suite.append(T(17, "tests/test_lineage.py::TestLineage::test_more"))
        second = ts.select_tests([change("sqlglot/lineage.py")], suite, set(), set(), mode="balanced")
        self.assertEqual(first, {4, 16})
        self.assertEqual(second, {4, 16, 17})
        other = [dict(t) for t in SUITE2]
        self.assertEqual(ts.select_tests([change("sqlglot/lineage.py")], other, set(), set(), mode="balanced"), {4, 16})

    def test_deterministic_across_input_order(self):
        changes = [change("sqlglot/dialects/presto.py"), change("tests/fixtures/identity.sql"), change("sqlglot/lineage.py")]
        cov = [SUITE2[16]["id"], SUITE2[11]["id"]]
        outs = set()
        for i in range(6):
            order = changes[i % 3:] + changes[: i % 3]
            r = ts.explain_selection(order, SUITE2, set(reversed(cov)) if i % 2 else set(cov), set(cov),
                                     mode="balanced")
            outs.add(frozenset(r.numbers))
        self.assertEqual(len(outs), 1)


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

    def test_every_dialect_loop_in_a_dialect_file_is_all_dialect(self):
        # tests/dialects/test_spark.py and test_druid.py loop over Dialects.
        ids = set(ts.ALL_DIALECT_TEST_IDS)
        self.assertIn("tests/dialects/test_spark.py::TestSpark::test_strip_modifiers", ids)
        self.assertIn("tests/dialects/test_druid.py::TestDruid::test_druid", ids)

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
