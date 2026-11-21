"""Test selection policy for the RWX Blast Radius track.

Public contract (TASKS.md, unchanged)::

    select_tests(changes, tests, impacted_files, covered_test_ids,
                 *, analysis_uncertain=False) -> set[int]

It returns tests.json ``number`` values. The safe fallback is *every* test
number. Two optional, keyword-only extras do not change the contract:

* ``mode=`` "safe" (default) | "balanced" | "aggressive"
* ``explain_selection(...)`` returns a ``Selection`` (numbers, fell_back,
  reasons) so a caller can log *why* a selection fell back to all tests.

Inputs are plain dicts following the FileChange contract; this module does not
import ``analyzer.contracts`` or any other analysis module and uses only the
standard library. It never reads ``sample/answers.json``, never looks at commit
ids, and has no table keyed by patch content or edit kind. The only embedded
knowledge is a set of static maps derived from the *pinned* sqlglot commit by
``derive_static_maps`` (regenerate with
``python3 analyzer/test_selector.py --derive <sqlglot> <tests.json>``):

* a source dependency graph that follows module-level imports, imports placed
  inside functions (e.g. ``generator.py`` importing ``dialects.tsql``), lazy
  re-exports (PEP 562 ``__getattr__`` tables, the dialect class registry) and
  by-name dialect use (``build_formatted_time(..., "teradata")``);
* the derived core: every module the parse/generate machinery uses, directly
  or through such lazy imports;
* dialect inheritance / dependency between dialects;
* which tests name a dialect, which tests load which fixture, which test files
  import which module, and which test modules are imported by other tests.

Policy summary (details in ``explain_selection``):

1. Global fallbacks to ALL tests: ``analysis_uncertain``, no changes parsed,
   any change flagged ``uncertain`` or with status binary/unknown/unexpected,
   unsafe paths, broad refactors (many files or lines).
2. Each changed path is classified: docs (harmless), CI config, build/config,
   test-infrastructure Python, shared test modules, a collected test file, a
   fixture data file, a core sqlglot module, another known sqlglot module, a
   new sqlglot module, or unknown.
3. Unknown paths, config, test infrastructure, core modules (hand list plus
   derived core), modules used by a core module, source modules missing from
   the pinned map, deleted/renamed source modules and "structural" edits
   (module-level imports, ``__all__``, removed or renamed def/class) fall back
   to ALL (some relaxed by the mode, see below).
4. Otherwise a source change selects, for the module and every module that
   uses it (transitively): naming-convention test files, test files importing
   it, and for every dialect reached (plus dialects built on it) that
   dialect's tests and the tests naming it; unioned with coverage hits
   (``covered_test_ids``), test files in ``impacted_files`` and tests of
   source modules in ``impacted_files``.
5. In safe mode a source change with no coverage hit at all is treated as
   "coverage missing" and falls back to ALL.

Modes:
* safe        - as above. Coverage only ever adds tests.
* balanced    - source changes may be selected from the static maps without
                coverage; fixture data selects only the tests that load that
                fixture; shared test modules select their importing test
                files; CI-only files are harmless; uncollected test modules
                are ignored; a new source module that nothing references
                selects nothing for itself.
* aggressive  - balanced, plus: core modules use coverage hits (if present)
                instead of ALL; when coverage is present it replaces dialect
                name mentions outside tests/dialects/; added/deleted/renamed
                and structural source edits are treated like modifications;
                near-universal impacted sets are ignored; broad-refactor
                limits are doubled. Not recommended without coverage.
"""

from __future__ import annotations

import logging
import posixpath
import re
import sys
from typing import Iterable, NamedTuple

LOG = logging.getLogger("analyzer.test_selector")

MODES = ("safe", "balanced", "aggressive")

# --------------------------------------------------------------------------
# Path classification tables (hand-curated from the sqlglot layout at the
# pinned commit; documented so reviewers can challenge each entry).
# --------------------------------------------------------------------------

# Modules whose change can plausibly break any test: the tokenizer, parser,
# generator, expression tree, dialect base, shared helpers, and optimizer
# modules imported by many source modules (scope: 31 importers,
# annotate_types: 26, transforms: 22, trie: 12, schema: 10, time: 7,
# simplify: 7 incl. dialect generators, ...). Importer counts were measured on
# the pinned commit's static imports.
CORE_MODULES = frozenset(
    {
        "sqlglot/__init__.py",
        "sqlglot/__main__.py",
        "sqlglot/_typing.py",
        "sqlglot/errors.py",
        "sqlglot/helper.py",
        "sqlglot/tokens.py",
        "sqlglot/tokenizer_core.py",
        "sqlglot/trie.py",
        "sqlglot/parser.py",
        "sqlglot/generator.py",
        "sqlglot/time.py",
        "sqlglot/transforms.py",
        "sqlglot/schema.py",
        "sqlglot/jsonpath.py",
        "sqlglot/dialects/__init__.py",
        "sqlglot/dialects/dialect.py",
        "sqlglot/parsers/__init__.py",
        "sqlglot/parsers/base.py",
        "sqlglot/generators/__init__.py",
        "sqlglot/typing/__init__.py",
        "sqlglot/optimizer/__init__.py",
        "sqlglot/optimizer/scope.py",
        "sqlglot/optimizer/annotate_types.py",
        "sqlglot/optimizer/simplify.py",
        "sqlglot/optimizer/helpers.py",
        "sqlglot/optimizer/normalize_identifiers.py",
        "sqlglot/optimizer/qualify_columns.py",
        # Imported lazily (inside functions) by core modules, so static import
        # graphs miss them: serde by Expression.dump/load/__reduce__ (pickle),
        # normalize and canonicalize by transforms.py.
        "sqlglot/serde.py",
        "sqlglot/optimizer/normalize.py",
        "sqlglot/optimizer/canonicalize.py",
    }
)
# Every module of the expression tree is re-exported through ``exp`` and used
# by every parse/generate call.
CORE_PREFIXES = ("sqlglot/expressions/",)

DIALECT_PACKAGES = ("dialects", "parsers", "generators", "typing")
DIALECT_NON_DIALECT_STEMS = frozenset({"__init__", "dialect", "base"})

# Non-core sqlglot modules whose test files are not found by naming
# convention or static test imports.
MODULE_EXTRA_TEST_FILES = {
    "sqlglot/planner.py": ("tests/test_executor.py",),
    "sqlglot/generators/python.py": ("tests/test_executor.py",),
    "sqlglot/lineage.py": ("tests/test_lineage.py",),
    "sqlglot/diff.py": ("tests/test_diff.py",),
    "sqlglot/anonymize.py": ("tests/test_anonymize.py",),
}
# Every optimizer pass is reached through optimizer.optimize(), which the
# executor, lineage and optimizer tests drive.
PACKAGE_EXTRA_TEST_FILES = {
    "sqlglot/optimizer/": (
        "tests/test_optimizer.py",
        "tests/test_executor.py",
        "tests/test_lineage.py",
        "tests/test_optimize_cross_joins.py",
        "tests/test_simplify_between.py",
    ),
    "sqlglot/executor/": ("tests/test_executor.py",),
}
# Cheap tests that exercise dialect loading in general.
DIALECT_INFRA_TEST_FILES = (
    "tests/dialects/test_dialect.py",
    "tests/test_dialect_imports.py",
    "tests/test_dialect_entry_points.py",
)

# Test-side Python that pytest loads implicitly or that other tests import.
# Test modules imported by other test modules (tests/helpers.py,
# tests/dialects/test_dialect.py with its Validator) are derived into
# SHARED_TEST_IMPORTERS.
TEST_INFRA_BASENAMES = frozenset({"conftest.py", "__init__.py", "helpers.py", "leakcheck.py"})

# Modules that only re-export or register other modules. Their imports say
# nothing about who *uses* a module, so they are not followed as importers.
REGISTRY_MODULES = frozenset(
    {"sqlglot/__init__.py", "sqlglot/dialects/__init__.py", "sqlglot/optimizer/__init__.py"}
)
# Entry points of the parse/generate machinery every test goes through. The
# derived core is everything they use, statically or through imports placed
# inside functions.
CORE_SEEDS = (
    "sqlglot/parser.py",
    "sqlglot/generator.py",
    "sqlglot/tokens.py",
    "sqlglot/dialects/dialect.py",
    "sqlglot/expressions/__init__.py",
)

DOC_SUFFIXES = (".md", ".rst", ".markdown")
DOC_BASENAME_PREFIXES = ("readme", "changelog", "license", "authors", "contributing", "code_of_conduct")
DOC_DIRS = ("docs/", "posts/")
CI_PREFIXES = (".github/",)
CONFIG_BASENAMES = frozenset(
    {
        "setup.py", "setup.cfg", "pyproject.toml", "tox.ini", "pytest.ini", "Makefile",
        "MANIFEST.in", "conftest.py", ".coveragerc", "mypy.ini", ".pre-commit-config.yaml",
        "Dockerfile", "requirements.txt", "requirements.lock", "noxfile.py",
    }
)
CONFIG_SUFFIXES = (".toml", ".cfg", ".ini", ".lock")
CONFIG_DIR_PREFIXES = ("sqlglotrs/", "sqlglotc/", "scripts/", "benchmarks/")

KNOWN_STATUSES = frozenset({"added", "deleted", "modified", "renamed", "copied"})
FALLBACK_STATUSES = frozenset({"binary", "unknown"})

# Broad refactor limits (doubled in aggressive mode).
BROAD_MAX_FILES = 12
BROAD_MAX_CHANGED_LINES = 600
# A selection worth more than this share of the full suite's seconds is
# promoted to ALL: it costs about the same and removes the false-negative risk.
PROMOTE_TO_ALL_SHARE = 0.85
# aggressive: ignore impacted_files when it names at least this share of the
# test files (an import graph through sqlglot/__init__ reaches everything).
UNINFORMATIVE_IMPACT_SHARE = 0.9

# --------------------------------------------------------------------------
# Static maps derived from the pinned sqlglot commit
# (eb11aec4be8cb2122afe713a4816662c3f0fb2fa) by derive_static_maps().
# --------------------------------------------------------------------------
# BEGIN GENERATED
DIALECT_BASES: dict[str, tuple[str, ...]] = {
    'athena': ('hive', 'trino'),
    'databricks': ('spark',),
    'doris': ('mysql',),
    'drill': ('mysql',),
    'dune': ('trino',),
    'fabric': ('tsql',),
    'materialize': ('postgres',),
    'presto': ('hive', 'mysql', 'teradata'),
    'redshift': ('postgres',),
    'risingwave': ('postgres',),
    'singlestore': ('mysql',),
    'spark': ('hive', 'spark2'),
    'spark2': ('hive', 'spark'),
    'starrocks': ('mysql',),
    'trino': ('presto',),
}
DIALECT_TEST_FILE_MENTIONS: dict[str, tuple[str, ...]] = {
    'athena': ('tests/dialects/test_athena.py', 'tests/dialects/test_dialect.py', 'tests/dialects/test_postgres.py', 'tests/dialects/test_presto.py', 'tests/dialects/test_spark.py'),
    'bigquery': ('tests/dialects/test_bigquery.py', 'tests/dialects/test_clickhouse.py', 'tests/dialects/test_dialect.py', 'tests/dialects/test_duckdb.py', 'tests/dialects/test_exasol.py', 'tests/dialects/test_hive.py', 'tests/dialects/test_mysql.py', 'tests/dialects/test_oracle.py', 'tests/dialects/test_pipe_syntax.py', 'tests/dialects/test_postgres.py', 'tests/dialects/test_presto.py', 'tests/dialects/test_redshift.py', 'tests/dialects/test_singlestore.py', 'tests/dialects/test_snowflake.py', 'tests/dialects/test_spark.py', 'tests/dialects/test_sqlite.py', 'tests/dialects/test_starrocks.py', 'tests/dialects/test_teradata.py', 'tests/dialects/test_trino.py', 'tests/dialects/test_tsql.py'),
    'clickhouse': ('tests/dialects/test_bigquery.py', 'tests/dialects/test_clickhouse.py', 'tests/dialects/test_databricks.py', 'tests/dialects/test_dialect.py', 'tests/dialects/test_doris.py', 'tests/dialects/test_duckdb.py', 'tests/dialects/test_exasol.py', 'tests/dialects/test_hive.py', 'tests/dialects/test_mysql.py', 'tests/dialects/test_oracle.py', 'tests/dialects/test_postgres.py', 'tests/dialects/test_presto.py', 'tests/dialects/test_singlestore.py', 'tests/dialects/test_snowflake.py', 'tests/dialects/test_spark.py', 'tests/dialects/test_sqlite.py', 'tests/dialects/test_starrocks.py', 'tests/dialects/test_teradata.py', 'tests/dialects/test_tsql.py'),
    'databricks': ('tests/dialects/test_bigquery.py', 'tests/dialects/test_clickhouse.py', 'tests/dialects/test_databricks.py', 'tests/dialects/test_dialect.py', 'tests/dialects/test_duckdb.py', 'tests/dialects/test_exasol.py', 'tests/dialects/test_hive.py', 'tests/dialects/test_mysql.py', 'tests/dialects/test_oracle.py', 'tests/dialects/test_postgres.py', 'tests/dialects/test_presto.py', 'tests/dialects/test_redshift.py', 'tests/dialects/test_snowflake.py', 'tests/dialects/test_spark.py', 'tests/dialects/test_starrocks.py', 'tests/dialects/test_teradata.py', 'tests/dialects/test_trino.py', 'tests/dialects/test_tsql.py'),
    'dax': ('tests/dialects/test_dax.py', 'tests/dialects/test_dialect.py', 'tests/dialects/test_teradata.py'),
    'doris': ('tests/dialects/test_clickhouse.py', 'tests/dialects/test_dialect.py', 'tests/dialects/test_doris.py', 'tests/dialects/test_duckdb.py', 'tests/dialects/test_hive.py', 'tests/dialects/test_mysql.py', 'tests/dialects/test_oracle.py', 'tests/dialects/test_postgres.py', 'tests/dialects/test_presto.py', 'tests/dialects/test_spark.py'),
    'dremio': ('tests/dialects/test_bigquery.py', 'tests/dialects/test_databricks.py', 'tests/dialects/test_dialect.py', 'tests/dialects/test_dremio.py'),
    'drill': ('tests/dialects/test_bigquery.py', 'tests/dialects/test_dialect.py', 'tests/dialects/test_drill.py', 'tests/dialects/test_duckdb.py', 'tests/dialects/test_exasol.py', 'tests/dialects/test_hive.py', 'tests/dialects/test_mysql.py', 'tests/dialects/test_oracle.py', 'tests/dialects/test_postgres.py', 'tests/dialects/test_presto.py', 'tests/dialects/test_redshift.py', 'tests/dialects/test_snowflake.py', 'tests/dialects/test_teradata.py'),
    'druid': ('tests/dialects/test_druid.py',),
    'duckdb': ('tests/dialects/test_bigquery.py', 'tests/dialects/test_clickhouse.py', 'tests/dialects/test_databricks.py', 'tests/dialects/test_dax.py', 'tests/dialects/test_dialect.py', 'tests/dialects/test_doris.py', 'tests/dialects/test_dremio.py', 'tests/dialects/test_drill.py', 'tests/dialects/test_duckdb.py', 'tests/dialects/test_exasol.py', 'tests/dialects/test_hive.py', 'tests/dialects/test_materialize.py', 'tests/dialects/test_mysql.py', 'tests/dialects/test_oracle.py', 'tests/dialects/test_postgres.py', 'tests/dialects/test_presto.py', 'tests/dialects/test_redshift.py', 'tests/dialects/test_singlestore.py', 'tests/dialects/test_snowflake.py', 'tests/dialects/test_spark.py', 'tests/dialects/test_sqlite.py', 'tests/dialects/test_starrocks.py', 'tests/dialects/test_teradata.py', 'tests/dialects/test_trino.py', 'tests/dialects/test_tsql.py'),
    'dune': ('tests/dialects/test_dune.py', 'tests/dialects/test_teradata.py'),
    'exasol': ('tests/dialects/test_bigquery.py', 'tests/dialects/test_dialect.py', 'tests/dialects/test_duckdb.py', 'tests/dialects/test_exasol.py', 'tests/dialects/test_hive.py', 'tests/dialects/test_mysql.py', 'tests/dialects/test_oracle.py', 'tests/dialects/test_teradata.py'),
    'fabric': ('tests/dialects/test_fabric.py', 'tests/dialects/test_teradata.py'),
    'hive': ('tests/dialects/test_athena.py', 'tests/dialects/test_bigquery.py', 'tests/dialects/test_clickhouse.py', 'tests/dialects/test_databricks.py', 'tests/dialects/test_dialect.py', 'tests/dialects/test_dremio.py', 'tests/dialects/test_duckdb.py', 'tests/dialects/test_exasol.py', 'tests/dialects/test_hive.py', 'tests/dialects/test_mysql.py', 'tests/dialects/test_oracle.py', 'tests/dialects/test_postgres.py', 'tests/dialects/test_presto.py', 'tests/dialects/test_redshift.py', 'tests/dialects/test_singlestore.py', 'tests/dialects/test_snowflake.py', 'tests/dialects/test_spark.py', 'tests/dialects/test_starrocks.py', 'tests/dialects/test_tableau.py', 'tests/dialects/test_teradata.py', 'tests/dialects/test_trino.py', 'tests/dialects/test_tsql.py'),
    'materialize': ('tests/dialects/test_bigquery.py', 'tests/dialects/test_dialect.py', 'tests/dialects/test_materialize.py', 'tests/dialects/test_spark.py'),
    'mysql': ('tests/dialects/test_bigquery.py', 'tests/dialects/test_clickhouse.py', 'tests/dialects/test_databricks.py', 'tests/dialects/test_dialect.py', 'tests/dialects/test_doris.py', 'tests/dialects/test_drill.py', 'tests/dialects/test_duckdb.py', 'tests/dialects/test_exasol.py', 'tests/dialects/test_hive.py', 'tests/dialects/test_mysql.py', 'tests/dialects/test_oracle.py', 'tests/dialects/test_postgres.py', 'tests/dialects/test_presto.py', 'tests/dialects/test_redshift.py', 'tests/dialects/test_singlestore.py', 'tests/dialects/test_snowflake.py', 'tests/dialects/test_spark.py', 'tests/dialects/test_sqlite.py', 'tests/dialects/test_starrocks.py', 'tests/dialects/test_teradata.py', 'tests/dialects/test_tsql.py'),
    'oracle': ('tests/dialects/test_bigquery.py', 'tests/dialects/test_clickhouse.py', 'tests/dialects/test_dialect.py', 'tests/dialects/test_doris.py', 'tests/dialects/test_dremio.py', 'tests/dialects/test_duckdb.py', 'tests/dialects/test_exasol.py', 'tests/dialects/test_hive.py', 'tests/dialects/test_mysql.py', 'tests/dialects/test_oracle.py', 'tests/dialects/test_postgres.py', 'tests/dialects/test_presto.py', 'tests/dialects/test_redshift.py', 'tests/dialects/test_singlestore.py', 'tests/dialects/test_snowflake.py', 'tests/dialects/test_spark.py', 'tests/dialects/test_sqlite.py', 'tests/dialects/test_teradata.py', 'tests/dialects/test_tsql.py'),
    'postgres': ('tests/dialects/test_bigquery.py', 'tests/dialects/test_clickhouse.py', 'tests/dialects/test_databricks.py', 'tests/dialects/test_dialect.py', 'tests/dialects/test_doris.py', 'tests/dialects/test_dremio.py', 'tests/dialects/test_drill.py', 'tests/dialects/test_duckdb.py', 'tests/dialects/test_dune.py', 'tests/dialects/test_exasol.py', 'tests/dialects/test_fabric.py', 'tests/dialects/test_hive.py', 'tests/dialects/test_materialize.py', 'tests/dialects/test_mysql.py', 'tests/dialects/test_oracle.py', 'tests/dialects/test_postgres.py', 'tests/dialects/test_presto.py', 'tests/dialects/test_redshift.py', 'tests/dialects/test_singlestore.py', 'tests/dialects/test_snowflake.py', 'tests/dialects/test_spark.py', 'tests/dialects/test_sqlite.py', 'tests/dialects/test_starrocks.py', 'tests/dialects/test_teradata.py', 'tests/dialects/test_tsql.py'),
    'presto': ('tests/dialects/test_bigquery.py', 'tests/dialects/test_clickhouse.py', 'tests/dialects/test_databricks.py', 'tests/dialects/test_dialect.py', 'tests/dialects/test_dremio.py', 'tests/dialects/test_duckdb.py', 'tests/dialects/test_exasol.py', 'tests/dialects/test_hive.py', 'tests/dialects/test_mysql.py', 'tests/dialects/test_oracle.py', 'tests/dialects/test_postgres.py', 'tests/dialects/test_presto.py', 'tests/dialects/test_redshift.py', 'tests/dialects/test_singlestore.py', 'tests/dialects/test_snowflake.py', 'tests/dialects/test_spark.py', 'tests/dialects/test_starrocks.py', 'tests/dialects/test_tableau.py', 'tests/dialects/test_teradata.py', 'tests/dialects/test_trino.py', 'tests/dialects/test_tsql.py'),
    'prql': ('tests/dialects/test_dialect.py', 'tests/dialects/test_prql.py'),
    'redshift': ('tests/dialects/test_bigquery.py', 'tests/dialects/test_dialect.py', 'tests/dialects/test_duckdb.py', 'tests/dialects/test_exasol.py', 'tests/dialects/test_hive.py', 'tests/dialects/test_mysql.py', 'tests/dialects/test_oracle.py', 'tests/dialects/test_postgres.py', 'tests/dialects/test_presto.py', 'tests/dialects/test_redshift.py', 'tests/dialects/test_singlestore.py', 'tests/dialects/test_snowflake.py', 'tests/dialects/test_spark.py', 'tests/dialects/test_sqlite.py', 'tests/dialects/test_teradata.py', 'tests/dialects/test_tsql.py'),
    'risingwave': ('tests/dialects/test_bigquery.py', 'tests/dialects/test_dialect.py', 'tests/dialects/test_risingwave.py'),
    'singlestore': ('tests/dialects/test_dialect.py', 'tests/dialects/test_duckdb.py', 'tests/dialects/test_mysql.py', 'tests/dialects/test_postgres.py', 'tests/dialects/test_singlestore.py', 'tests/dialects/test_snowflake.py', 'tests/dialects/test_tsql.py'),
    'snowflake': ('tests/dialects/test_bigquery.py', 'tests/dialects/test_clickhouse.py', 'tests/dialects/test_databricks.py', 'tests/dialects/test_dialect.py', 'tests/dialects/test_doris.py', 'tests/dialects/test_drill.py', 'tests/dialects/test_duckdb.py', 'tests/dialects/test_exasol.py', 'tests/dialects/test_hive.py', 'tests/dialects/test_mysql.py', 'tests/dialects/test_oracle.py', 'tests/dialects/test_postgres.py', 'tests/dialects/test_presto.py', 'tests/dialects/test_redshift.py', 'tests/dialects/test_singlestore.py', 'tests/dialects/test_snowflake.py', 'tests/dialects/test_spark.py', 'tests/dialects/test_sqlite.py', 'tests/dialects/test_starrocks.py', 'tests/dialects/test_teradata.py', 'tests/dialects/test_trino.py', 'tests/dialects/test_tsql.py'),
    'solr': ('tests/dialects/test_solr.py',),
    'spark': ('tests/dialects/test_bigquery.py', 'tests/dialects/test_clickhouse.py', 'tests/dialects/test_databricks.py', 'tests/dialects/test_dialect.py', 'tests/dialects/test_dremio.py', 'tests/dialects/test_duckdb.py', 'tests/dialects/test_exasol.py', 'tests/dialects/test_hive.py', 'tests/dialects/test_materialize.py', 'tests/dialects/test_mysql.py', 'tests/dialects/test_oracle.py', 'tests/dialects/test_postgres.py', 'tests/dialects/test_presto.py', 'tests/dialects/test_redshift.py', 'tests/dialects/test_snowflake.py', 'tests/dialects/test_spark.py', 'tests/dialects/test_sqlite.py', 'tests/dialects/test_starrocks.py', 'tests/dialects/test_teradata.py', 'tests/dialects/test_trino.py', 'tests/dialects/test_tsql.py'),
    'spark2': ('tests/dialects/test_bigquery.py', 'tests/dialects/test_databricks.py', 'tests/dialects/test_dialect.py', 'tests/dialects/test_duckdb.py', 'tests/dialects/test_exasol.py', 'tests/dialects/test_hive.py', 'tests/dialects/test_mysql.py', 'tests/dialects/test_oracle.py', 'tests/dialects/test_postgres.py', 'tests/dialects/test_presto.py', 'tests/dialects/test_redshift.py', 'tests/dialects/test_singlestore.py', 'tests/dialects/test_snowflake.py', 'tests/dialects/test_spark.py', 'tests/dialects/test_tsql.py'),
    'sqlite': ('tests/dialects/test_bigquery.py', 'tests/dialects/test_clickhouse.py', 'tests/dialects/test_dialect.py', 'tests/dialects/test_doris.py', 'tests/dialects/test_duckdb.py', 'tests/dialects/test_exasol.py', 'tests/dialects/test_hive.py', 'tests/dialects/test_mysql.py', 'tests/dialects/test_postgres.py', 'tests/dialects/test_presto.py', 'tests/dialects/test_redshift.py', 'tests/dialects/test_snowflake.py', 'tests/dialects/test_sqlite.py', 'tests/dialects/test_teradata.py', 'tests/dialects/test_tsql.py'),
    'starrocks': ('tests/dialects/test_bigquery.py', 'tests/dialects/test_dialect.py', 'tests/dialects/test_duckdb.py', 'tests/dialects/test_mysql.py', 'tests/dialects/test_oracle.py', 'tests/dialects/test_postgres.py', 'tests/dialects/test_presto.py', 'tests/dialects/test_redshift.py', 'tests/dialects/test_snowflake.py', 'tests/dialects/test_starrocks.py', 'tests/dialects/test_teradata.py', 'tests/dialects/test_tsql.py'),
    'tableau': ('tests/dialects/test_dialect.py', 'tests/dialects/test_mysql.py', 'tests/dialects/test_oracle.py', 'tests/dialects/test_presto.py', 'tests/dialects/test_redshift.py', 'tests/dialects/test_tableau.py', 'tests/dialects/test_teradata.py'),
    'teradata': ('tests/dialects/test_databricks.py', 'tests/dialects/test_dialect.py', 'tests/dialects/test_duckdb.py', 'tests/dialects/test_exasol.py', 'tests/dialects/test_mysql.py', 'tests/dialects/test_oracle.py', 'tests/dialects/test_postgres.py', 'tests/dialects/test_presto.py', 'tests/dialects/test_redshift.py', 'tests/dialects/test_snowflake.py', 'tests/dialects/test_spark.py', 'tests/dialects/test_teradata.py', 'tests/dialects/test_tsql.py'),
    'trino': ('tests/dialects/test_athena.py', 'tests/dialects/test_bigquery.py', 'tests/dialects/test_clickhouse.py', 'tests/dialects/test_databricks.py', 'tests/dialects/test_dialect.py', 'tests/dialects/test_dremio.py', 'tests/dialects/test_duckdb.py', 'tests/dialects/test_dune.py', 'tests/dialects/test_exasol.py', 'tests/dialects/test_hive.py', 'tests/dialects/test_mysql.py', 'tests/dialects/test_postgres.py', 'tests/dialects/test_presto.py', 'tests/dialects/test_redshift.py', 'tests/dialects/test_snowflake.py', 'tests/dialects/test_spark.py', 'tests/dialects/test_sqlite.py', 'tests/dialects/test_teradata.py', 'tests/dialects/test_trino.py', 'tests/dialects/test_tsql.py'),
    'tsql': ('tests/dialects/test_bigquery.py', 'tests/dialects/test_clickhouse.py', 'tests/dialects/test_databricks.py', 'tests/dialects/test_dialect.py', 'tests/dialects/test_duckdb.py', 'tests/dialects/test_exasol.py', 'tests/dialects/test_fabric.py', 'tests/dialects/test_hive.py', 'tests/dialects/test_mysql.py', 'tests/dialects/test_oracle.py', 'tests/dialects/test_postgres.py', 'tests/dialects/test_presto.py', 'tests/dialects/test_redshift.py', 'tests/dialects/test_singlestore.py', 'tests/dialects/test_snowflake.py', 'tests/dialects/test_sqlite.py', 'tests/dialects/test_teradata.py', 'tests/dialects/test_tsql.py'),
}
DIALECT_TEST_ID_MENTIONS: dict[str, tuple[str, ...]] = {
    'athena': ('tests/test_anonymize.py::TestAnonymize::test_athena_known_function_kept', 'tests/test_dialect_imports.py::TestDialectImports::test_dialect_import_no_deadlock', 'tests/test_generator.py::TestGenerator::test_unsupported_property_is_dropped', 'tests/test_optimizer.py::TestOptimizer::test_annotate_funcs', 'tests/test_optimizer.py::TestOptimizer::test_pushdown_predicates'),
    'bigquery': ('tests/test_build.py::TestBuild::test_build', 'tests/test_executor.py::TestExecutor::test_integer_division_truncates_towards_zero', 'tests/test_executor.py::TestExecutor::test_nested_values', 'tests/test_expressions.py::TestExprs::test_data_type_builder', 'tests/test_expressions.py::TestExprs::test_function_building', 'tests/test_expressions.py::TestExprs::test_functions', 'tests/test_expressions.py::TestExprs::test_ref_type_builder', 'tests/test_expressions.py::TestExprs::test_replace_tables', 'tests/test_expressions.py::TestExprs::test_replace_tables_keeps_function_arguments', 'tests/test_expressions.py::TestExprs::test_table_name', 'tests/test_generator.py::TestGenerator::test_time_slice_origin', 'tests/test_lineage.py::TestLineage::test_unpivot_multi_column', 'tests/test_optimizer.py::TestOptimizer::test_annotate_dot_parts_of_non_column_roots', 'tests/test_optimizer.py::TestOptimizer::test_annotate_funcs', 'tests/test_optimizer.py::TestOptimizer::test_annotate_row_types', 'tests/test_optimizer.py::TestOptimizer::test_annotate_struct_subquery_values', 'tests/test_optimizer.py::TestOptimizer::test_annotate_table_as_struct_bigquery', 'tests/test_optimizer.py::TestOptimizer::test_annotate_types', 'tests/test_optimizer.py::TestOptimizer::test_bigquery_unnest_alias_shadowing', 'tests/test_optimizer.py::TestOptimizer::test_canonicalize_internal_names', 'tests/test_optimizer.py::TestOptimizer::test_case_sensitive_json_dot_access', 'tests/test_optimizer.py::TestOptimizer::test_correlated_subqueries_annotation', 'tests/test_optimizer.py::TestOptimizer::test_correlated_unnest_projection_types', 'tests/test_optimizer.py::TestOptimizer::test_expand_alias_refs', 'tests/test_optimizer.py::TestOptimizer::test_function_annotation', 'tests/test_optimizer.py::TestOptimizer::test_gen_bracket', 'tests/test_optimizer.py::TestOptimizer::test_nested_type_annotation', 'tests/test_optimizer.py::TestOptimizer::test_no_pseudocolumn_expansion', 'tests/test_optimizer.py::TestOptimizer::test_normalize_identifiers', 'tests/test_optimizer.py::TestOptimizer::test_null_coerce_annotation', 'tests/test_optimizer.py::TestOptimizer::test_optimize', 'tests/test_optimizer.py::TestOptimizer::test_pushdown_projection', 'tests/test_optimizer.py::TestOptimizer::test_qualify_columns', 'tests/test_optimizer.py::TestOptimizer::test_qualify_columns_struct_star_expansion_types', 'tests/test_optimizer.py::TestOptimizer::test_qualify_group_by_conflict_bigquery', 'tests/test_optimizer.py::TestOptimizer::test_qualify_order_by_expression_types', 'tests/test_optimizer.py::TestOptimizer::test_qualify_order_by_subquery_types', 'tests/test_optimizer.py::TestOptimizer::test_qualify_positional_references_types', 'tests/test_optimizer.py::TestOptimizer::test_qualify_preserves_unsupported_struct_star_operands', 'tests/test_optimizer.py::TestOptimizer::test_qualify_star_replace_types', 'tests/test_optimizer.py::TestOptimizer::test_qualify_struct_star_json_ordering', 'tests/test_optimizer.py::TestOptimizer::test_qualify_struct_star_source_types', 'tests/test_optimizer.py::TestOptimizer::test_qualify_tables', 'tests/test_optimizer.py::TestOptimizer::test_quote_identifiers', 'tests/test_optimizer.py::TestOptimizer::test_reannotate_struct_subquery_values', 'tests/test_optimizer.py::TestOptimizer::test_resolver_column_context', 'tests/test_optimizer.py::TestOptimizer::test_scope', 'tests/test_optimizer.py::TestOptimizer::test_set_operation_annotation', 'tests/test_optimizer.py::TestOptimizer::test_simplify', 'tests/test_optimizer.py::TestOptimizer::test_struct_annotation_bigquery', 'tests/test_optimizer.py::TestOptimizer::test_struct_field_case_sensitivity_annotation', 'tests/test_optimizer.py::TestOptimizer::test_union_annotation', 'tests/test_optimizer.py::TestOptimizer::test_unnest_annotation', 'tests/test_optimizer.py::TestOptimizer::test_unnest_offset_annotation', 'tests/test_optimizer.py::TestOptimizer::test_unnest_struct_field_annotation', 'tests/test_optimizer.py::TestOptimizer::test_unnest_type_trace_is_memoized', 'tests/test_parser.py::TestParser::test_conditionless_join', 'tests/test_parser.py::TestParser::test_parse_create_schema', 'tests/test_parser.py::TestParser::test_parse_drop_schema', 'tests/test_parser.py::TestParser::test_pivot_columns', 'tests/test_parser.py::TestParser::test_ref_type', 'tests/test_parser.py::TestParser::test_text_match_excludes_delimited_tokens', 'tests/test_parser.py::TestParser::test_transactions', 'tests/test_parser.py::TestParser::test_type_literals', 'tests/test_parser.py::TestParser::test_values_as_identifier', 'tests/test_parser.py::TestParser::test_window_clause_without_from', 'tests/test_schema.py::TestSchema::test_cache_normalization', 'tests/test_schema.py::TestSchema::test_schema_add_table_error_formatting', 'tests/test_schema.py::TestSchema::test_schema_normalization', 'tests/test_sqlglotc.py::TestSQLGlotC::test_build_removes_obsolete_extensions', 'tests/test_sqlglotc.py::TestSQLGlotC::test_editable_install_removes_obsolete_extensions', 'tests/test_tokens.py::TestTokens::test_jinja'),
    'clickhouse': ('tests/test_build.py::TestBuild::test_build', 'tests/test_expressions.py::TestExprs::test_is_type', 'tests/test_expressions.py::TestExprs::test_replace_tables_function_copy', 'tests/test_expressions.py::TestExprs::test_replace_tables_keeps_function_arguments', 'tests/test_optimizer.py::TestOptimizer::test_annotate_funcs', 'tests/test_optimizer.py::TestOptimizer::test_case_sensitive_json_dot_access', 'tests/test_optimizer.py::TestOptimizer::test_eliminate_positional_joins', 'tests/test_optimizer.py::TestOptimizer::test_expand_alias_refs', 'tests/test_optimizer.py::TestOptimizer::test_merge_any_join_inputs', 'tests/test_optimizer.py::TestOptimizer::test_merge_asof_join_inputs', 'tests/test_optimizer.py::TestOptimizer::test_merge_positional_join_inputs', 'tests/test_optimizer.py::TestOptimizer::test_nonnull_annotation', 'tests/test_optimizer.py::TestOptimizer::test_optimize_match_selecting_joins', 'tests/test_optimizer.py::TestOptimizer::test_optimize_positional_joins', 'tests/test_optimizer.py::TestOptimizer::test_pushdown_predicates', 'tests/test_optimizer.py::TestOptimizer::test_qualify_columns', 'tests/test_optimizer.py::TestOptimizer::test_resolver_column_context', 'tests/test_optimizer.py::TestOptimizer::test_simplify', 'tests/test_optimizer.py::TestOptimizer::test_struct_field_case_sensitivity_annotation', 'tests/test_optimizer.py::TestOptimizer::test_unnest_positional_join_predicates', 'tests/test_parser.py::TestParser::test_is_precedence', 'tests/test_parser.py::TestParser::test_parse_errors', 'tests/test_parser.py::TestParser::test_text_match_excludes_delimited_tokens', 'tests/test_parser.py::TestParser::test_union', 'tests/test_parser.py::TestParser::test_values_as_identifier', 'tests/test_schema.py::TestSchema::test_schema_normalization', 'tests/test_transpile.py::TestTranspile::test_sql_security', 'tests/test_transpile.py::TestTranspile::test_types'),
    'databricks': ('tests/test_expressions.py::TestExprs::test_table_name', 'tests/test_optimizer.py::TestOptimizer::test_annotate_funcs', 'tests/test_optimizer.py::TestOptimizer::test_annotate_types', 'tests/test_optimizer.py::TestOptimizer::test_case_sensitive_json_dot_access', 'tests/test_optimizer.py::TestOptimizer::test_simplify', 'tests/test_parser.py::TestParser::test_alter_set', 'tests/test_parser.py::TestParser::test_is_precedence', 'tests/test_parser.py::TestParser::test_parse_into_grant_principal', 'tests/test_parser.py::TestParser::test_text_match_excludes_delimited_tokens'),
    'dax': ('tests/test_generator.py::TestGenerator::test_unsupported_slice',),
    'doris': ('tests/test_parser.py::TestParser::test_is_precedence',),
    'dremio': ('tests/test_parser.py::TestParser::test_is_precedence',),
    'drill': ('tests/test_parser.py::TestParser::test_is_precedence',),
    'duckdb': ('tests/test_executor.py::TestExecutor::test_array_slice', 'tests/test_executor.py::TestExecutor::test_integer_division_truncates_towards_zero', 'tests/test_executor.py::TestExecutor::test_nested_values', 'tests/test_executor.py::TestExecutor::test_null_ordering_honors_nulls_first_and_dialect_defaults', 'tests/test_executor.py::TestExecutor::test_optimized_tpch', 'tests/test_expressions.py::TestExprs::test_data_type_builder', 'tests/test_expressions.py::TestExprs::test_function_building', 'tests/test_expressions.py::TestExprs::test_named_selects', 'tests/test_expressions.py::TestExprs::test_named_selects_long_by_name_chain', 'tests/test_expressions.py::TestExprs::test_named_selects_set_operations', 'tests/test_expressions.py::TestExprs::test_replace_tables_builtin_parent', 'tests/test_expressions.py::TestExprs::test_replace_tables_keeps_function_arguments', 'tests/test_generator.py::TestGenerator::test_unsupported_slice', 'tests/test_lineage.py::TestLineage::test_chained_pivots_consuming_alias_columns', 'tests/test_lineage.py::TestLineage::test_lineage_normalize', 'tests/test_lineage.py::TestLineage::test_pivot_with_subquery', 'tests/test_optimizer.py::TestOptimizer::test_annotate_dot_parts_of_non_column_roots', 'tests/test_optimizer.py::TestOptimizer::test_annotate_funcs', 'tests/test_optimizer.py::TestOptimizer::test_annotate_json_dot_parts_preserve_meta', 'tests/test_optimizer.py::TestOptimizer::test_annotate_row_types', 'tests/test_optimizer.py::TestOptimizer::test_annotate_semi_structured_dot_parts', 'tests/test_optimizer.py::TestOptimizer::test_annotate_struct_subquery_values', 'tests/test_optimizer.py::TestOptimizer::test_annotate_types', 'tests/test_optimizer.py::TestOptimizer::test_canonicalize_internal_names', 'tests/test_optimizer.py::TestOptimizer::test_case_sensitive_json_dot_access', 'tests/test_optimizer.py::TestOptimizer::test_eliminate_joins', 'tests/test_optimizer.py::TestOptimizer::test_eliminate_positional_joins', 'tests/test_optimizer.py::TestOptimizer::test_journal', 'tests/test_optimizer.py::TestOptimizer::test_map_annotation', 'tests/test_optimizer.py::TestOptimizer::test_merge_asof_join_inputs', 'tests/test_optimizer.py::TestOptimizer::test_merge_positional_join_inputs', 'tests/test_optimizer.py::TestOptimizer::test_merge_subqueries', 'tests/test_optimizer.py::TestOptimizer::test_nearest_joins', 'tests/test_optimizer.py::TestOptimizer::test_normalize_identifiers', 'tests/test_optimizer.py::TestOptimizer::test_optimize', 'tests/test_optimizer.py::TestOptimizer::test_optimize_correlated_all', 'tests/test_optimizer.py::TestOptimizer::test_optimize_distinct_on_predicates', 'tests/test_optimizer.py::TestOptimizer::test_optimize_in_nulls', 'tests/test_optimizer.py::TestOptimizer::test_optimize_match_selecting_joins', 'tests/test_optimizer.py::TestOptimizer::test_optimize_positional_joins', 'tests/test_optimizer.py::TestOptimizer::test_optimize_scalar_subquery_key_predicates', 'tests/test_optimizer.py::TestOptimizer::test_optimize_scalar_subquery_predicate_multiple_rows', 'tests/test_optimizer.py::TestOptimizer::test_optimize_scalar_subquery_predicates', 'tests/test_optimizer.py::TestOptimizer::test_optimize_snowflake_asof_without_on', 'tests/test_optimizer.py::TestOptimizer::test_optimize_special_joins', 'tests/test_optimizer.py::TestOptimizer::test_optimize_subqueries_in_aggregates', 'tests/test_optimizer.py::TestOptimizer::test_optimize_subqueries_in_join_on', 'tests/test_optimizer.py::TestOptimizer::test_optimize_uncorrelated_exists', 'tests/test_optimizer.py::TestOptimizer::test_pushdown_predicates', 'tests/test_optimizer.py::TestOptimizer::test_qualify_columns', 'tests/test_optimizer.py::TestOptimizer::test_qualify_columns__invalid', 'tests/test_optimizer.py::TestOptimizer::test_qualify_columns_using_after_outer_join', 'tests/test_optimizer.py::TestOptimizer::test_qualify_correlated_shadowed_struct_path', 'tests/test_optimizer.py::TestOptimizer::test_qualify_duckdb_collated_alias', 'tests/test_optimizer.py::TestOptimizer::test_qualify_group_by_conflict_bigquery', 'tests/test_optimizer.py::TestOptimizer::test_qualify_json_path_argument_order', 'tests/test_optimizer.py::TestOptimizer::test_qualify_order_by_alias_fallback', 'tests/test_optimizer.py::TestOptimizer::test_qualify_order_by_alias_in_subquery', 'tests/test_optimizer.py::TestOptimizer::test_qualify_order_by_alias_nodes', 'tests/test_optimizer.py::TestOptimizer::test_qualify_order_by_ambiguous_alias', 'tests/test_optimizer.py::TestOptimizer::test_qualify_order_by_source_columns', 'tests/test_optimizer.py::TestOptimizer::test_qualify_order_by_using_column', 'tests/test_optimizer.py::TestOptimizer::test_qualify_order_by_using_column_collision', 'tests/test_optimizer.py::TestOptimizer::test_qualify_parquet_alias_collision', 'tests/test_optimizer.py::TestOptimizer::test_qualify_semi_anti_join_alias_shadowing_column', 'tests/test_optimizer.py::TestOptimizer::test_qualify_semi_anti_join_column_aliases', 'tests/test_optimizer.py::TestOptimizer::test_qualify_struct_star_json_ordering', 'tests/test_optimizer.py::TestOptimizer::test_qualify_struct_star_source_types', 'tests/test_optimizer.py::TestOptimizer::test_qualify_unknown_tvf_table_reference', 'tests/test_optimizer.py::TestOptimizer::test_resolver_column_context', 'tests/test_optimizer.py::TestOptimizer::test_scope', 'tests/test_optimizer.py::TestOptimizer::test_simplify', 'tests/test_optimizer.py::TestOptimizer::test_simplify_string_comparisons', 'tests/test_optimizer.py::TestOptimizer::test_union_annotation', 'tests/test_optimizer.py::TestOptimizer::test_unnest_offset_annotation', 'tests/test_optimizer.py::TestOptimizer::test_unnest_positional_join_predicates', 'tests/test_optimizer.py::TestOptimizer::test_unpivot_cte_annotate_types', 'tests/test_optimizer.py::TestOptimizer::test_values_annotation', 'tests/test_parser.py::TestParser::test_is_precedence', 'tests/test_parser.py::TestParser::test_large_exponent_array_index', 'tests/test_parser.py::TestParser::test_parse_drop_schema', 'tests/test_parser.py::TestParser::test_pivot_columns', 'tests/test_parser.py::TestParser::test_ref_type', 'tests/test_parser.py::TestParser::test_text_match_excludes_delimited_tokens', 'tests/test_parser.py::TestParser::test_values_as_identifier', 'tests/test_parser.py::TestParser::test_window_clause_without_from', 'tests/test_schema.py::TestSchema::test_cache_does_not_retain_queries', 'tests/test_sqlglotc.py::TestSQLGlotC::test_build_removes_obsolete_extensions', 'tests/test_sqlglotc.py::TestSQLGlotC::test_editable_install_removes_obsolete_extensions', 'tests/test_transpile.py::TestTranspile::test_time'),
    'exasol': ('tests/test_generator.py::TestGenerator::test_unsupported_slice', 'tests/test_parser.py::TestParser::test_text_match_excludes_delimited_tokens'),
    'fabric': ('tests/test_generator.py::TestGenerator::test_unsupported_slice',),
    'hive': ('tests/test_build.py::TestBuild::test_build', 'tests/test_executor.py::TestExecutor::test_first', 'tests/test_expressions.py::TestExprs::test_cast', 'tests/test_expressions.py::TestExprs::test_eq', 'tests/test_expressions.py::TestExprs::test_function_building', 'tests/test_expressions.py::TestExprs::test_sql', 'tests/test_optimizer.py::TestOptimizer::test_annotate_funcs', 'tests/test_optimizer.py::TestOptimizer::test_scope', 'tests/test_parser.py::TestParser::test_alter_set', 'tests/test_parser.py::TestParser::test_command', 'tests/test_parser.py::TestParser::test_is_precedence', 'tests/test_transpile.py::TestTranspile::test_time', 'tests/test_transpile.py::TestTranspile::test_unsupported_level'),
    'materialize': ('tests/test_parser.py::TestParser::test_is_precedence',),
    'mysql': ('tests/test_anonymize.py::TestAnonymize::test_quotes_preserved', 'tests/test_anonymize.py::TestAnonymize::test_render', 'tests/test_anonymize.py::TestAnonymize::test_render_without_dialect_still_redacts', 'tests/test_anonymize.py::TestAnonymize::test_tokenize_error_keeps_delimiter', 'tests/test_build.py::TestBuild::test_build', 'tests/test_executor.py::TestExecutor::test_integer_division_floating_point', 'tests/test_executor.py::TestExecutor::test_integer_division_truncates_towards_zero', 'tests/test_executor.py::TestExecutor::test_null_ordering_honors_nulls_first_and_dialect_defaults', 'tests/test_expressions.py::TestExprs::test_eq_case_sensitive_text', 'tests/test_expressions.py::TestExprs::test_function_building', 'tests/test_generator.py::TestGenerator::test_time_slice_origin', 'tests/test_optimize_cross_joins.py::TestOptimizeCrossJoins::test_conditional_cross_joins', 'tests/test_optimizer.py::TestOptimizer::test_annotate_funcs', 'tests/test_optimizer.py::TestOptimizer::test_canonicalize', 'tests/test_optimizer.py::TestOptimizer::test_optimize', 'tests/test_optimizer.py::TestOptimizer::test_qualify_columns', 'tests/test_optimizer.py::TestOptimizer::test_simplify', 'tests/test_optimizer.py::TestOptimizer::test_typeddiv_annotation', 'tests/test_parser.py::TestParser::test_describe_followed_by_dot', 'tests/test_parser.py::TestParser::test_is_precedence', 'tests/test_parser.py::TestParser::test_text_match_excludes_delimited_tokens', 'tests/test_parser.py::TestParser::test_transactions', 'tests/test_parser.py::TestParser::test_window_clause_without_from', 'tests/test_sqlglotc.py::TestSQLGlotC::test_build_removes_obsolete_extensions', 'tests/test_sqlglotc.py::TestSQLGlotC::test_editable_install_removes_obsolete_extensions', 'tests/test_transforms.py::TestTransforms::test_remove_unique_constraints', 'tests/test_transpile.py::TestTranspile::test_comments', 'tests/test_transpile.py::TestTranspile::test_sql_security'),
    'oracle': ('tests/test_anonymize.py::TestAnonymize::test_hints_anonymized', 'tests/test_anonymize.py::TestAnonymize::test_quotes_preserved', 'tests/test_diff.py::TestDiff::test_dialect_aware_diff', 'tests/test_diff.py::TestDiff::test_window_functions', 'tests/test_executor.py::TestExecutor::test_scalar_functions', 'tests/test_lineage.py::TestLineage::test_ddl_lineage', 'tests/test_optimizer.py::TestOptimizer::test_qualify_columns', 'tests/test_parser.py::TestParser::test_is_precedence', 'tests/test_parser.py::TestParser::test_text_match_excludes_delimited_tokens', 'tests/test_transforms.py::TestTransforms::test_eliminate_join_marks'),
    'postgres': ('tests/test_anonymize.py::TestAnonymize::test_quotes_preserved', 'tests/test_anonymize.py::TestAnonymize::test_tokenize_error_keeps_delimiter', 'tests/test_build.py::TestBuild::test_build', 'tests/test_executor.py::TestExecutor::test_dpipe_source_dialect_coercion', 'tests/test_executor.py::TestExecutor::test_null_ordering_honors_nulls_first_and_dialect_defaults', 'tests/test_executor.py::TestExecutor::test_typed_division', 'tests/test_executor.py::TestExecutor::test_typed_division_of_null_is_null', 'tests/test_expressions.py::TestExprs::test_cast', 'tests/test_expressions.py::TestExprs::test_data_type_builder', 'tests/test_expressions.py::TestExprs::test_eq_case_sensitive_text', 'tests/test_expressions.py::TestExprs::test_replace_tables_keeps_function_arguments', 'tests/test_generator.py::TestGenerator::test_overlap_operator', 'tests/test_generator.py::TestGenerator::test_time_slice_origin', 'tests/test_generator.py::TestGenerator::test_unsupported_property_is_dropped', 'tests/test_optimizer.py::TestOptimizer::test_annotate_funcs', 'tests/test_optimizer.py::TestOptimizer::test_canonicalize', 'tests/test_optimizer.py::TestOptimizer::test_canonicalize_internal_names', 'tests/test_optimizer.py::TestOptimizer::test_nested_type_annotation', 'tests/test_optimizer.py::TestOptimizer::test_normalize_identifiers', 'tests/test_optimizer.py::TestOptimizer::test_optimize', 'tests/test_optimizer.py::TestOptimizer::test_optimize_distinct_on_predicates', 'tests/test_optimizer.py::TestOptimizer::test_pushdown_projection', 'tests/test_optimizer.py::TestOptimizer::test_pushdown_projections_keeps_recursive_cte_self_referenced_columns', 'tests/test_optimizer.py::TestOptimizer::test_pushdown_projections_prunes_non_self_referencing_ctes', 'tests/test_optimizer.py::TestOptimizer::test_qualify_columns', 'tests/test_optimizer.py::TestOptimizer::test_qualify_order_by_alias_in_subquery', 'tests/test_optimizer.py::TestOptimizer::test_qualify_order_by_ambiguous_alias', 'tests/test_optimizer.py::TestOptimizer::test_qualify_order_by_source_columns', 'tests/test_optimizer.py::TestOptimizer::test_qualify_order_by_using_column', 'tests/test_optimizer.py::TestOptimizer::test_qualify_order_by_using_column_collision', 'tests/test_optimizer.py::TestOptimizer::test_qualify_positional_columns_is_snowflake_only', 'tests/test_optimizer.py::TestOptimizer::test_qualify_tables', 'tests/test_optimizer.py::TestOptimizer::test_simplify', 'tests/test_optimizer.py::TestOptimizer::test_user_defined_type_annotation', 'tests/test_optimizer.py::TestOptimizer::test_values_annotation', 'tests/test_parser.py::TestParser::test_copy_command_fallback', 'tests/test_parser.py::TestParser::test_is_precedence', 'tests/test_parser.py::TestParser::test_large_exponent_array_index', 'tests/test_parser.py::TestParser::test_no_paren_functions_after_dot', 'tests/test_parser.py::TestParser::test_parse_intervals', 'tests/test_parser.py::TestParser::test_text_match_excludes_delimited_tokens', 'tests/test_parser.py::TestParser::test_values_as_identifier', 'tests/test_parser.py::TestParser::test_window_clause_without_from', 'tests/test_schema.py::TestSchema::test_cache_normalization', 'tests/test_schema.py::TestSchema::test_schema_add_table_error_formatting', 'tests/test_simplify_between.py::TestSimplifyBetween::test_symmetric_constant_bounds', 'tests/test_simplify_between.py::TestSimplifyBetween::test_symmetric_truth_values_and_grouping', 'tests/test_sqlglotc.py::TestSQLGlotC::test_build_removes_obsolete_extensions', 'tests/test_sqlglotc.py::TestSQLGlotC::test_editable_install_removes_obsolete_extensions'),
    'presto': ('tests/test_expressions.py::TestExprs::test_transform_simple', 'tests/test_expressions.py::TestExprs::test_transform_with_arguments', 'tests/test_optimizer.py::TestOptimizer::test_annotate_funcs', 'tests/test_optimizer.py::TestOptimizer::test_annotate_types', 'tests/test_optimizer.py::TestOptimizer::test_pushdown_predicates', 'tests/test_optimizer.py::TestOptimizer::test_qualify_columns', 'tests/test_optimizer.py::TestOptimizer::test_simplify', 'tests/test_optimizer.py::TestOptimizer::test_typeddiv_annotation', 'tests/test_parser.py::TestParser::test_transactions', 'tests/test_transpile.py::TestTranspile::test_index_offset', 'tests/test_transpile.py::TestTranspile::test_normalize_name', 'tests/test_transpile.py::TestTranspile::test_sql_security', 'tests/test_transpile.py::TestTranspile::test_time', 'tests/test_transpile.py::TestTranspile::test_unsupported_level', 'tests/test_transpile.py::TestTranspile::test_with'),
    'redshift': ('tests/test_expressions.py::TestExprs::test_data_type_builder', 'tests/test_optimizer.py::TestOptimizer::test_annotate_funcs', 'tests/test_optimizer.py::TestOptimizer::test_normalize_identifiers', 'tests/test_optimizer.py::TestOptimizer::test_qualify_columns', 'tests/test_optimizer.py::TestOptimizer::test_qualify_tables', 'tests/test_optimizer.py::TestOptimizer::test_simplify', 'tests/test_parser.py::TestParser::test_is_precedence', 'tests/test_parser.py::TestParser::test_pivot_columns', 'tests/test_parser.py::TestParser::test_text_match_excludes_delimited_tokens', 'tests/test_parser.py::TestParser::test_values_as_identifier', 'tests/test_transforms.py::TestTransforms::test_eliminate_join_marks'),
    'risingwave': ('tests/test_optimizer.py::TestOptimizer::test_qualify_columns', 'tests/test_optimizer.py::TestOptimizer::test_qualify_skips_unneeded_annotation', 'tests/test_optimizer.py::TestOptimizer::test_qualify_struct_star_long_cte_chain', 'tests/test_optimizer.py::TestOptimizer::test_qualify_struct_star_source_types', 'tests/test_parser.py::TestParser::test_is_precedence'),
    'singlestore': ('tests/test_expressions.py::TestExprs::test_eq_case_sensitive_text',),
    'snowflake': ('tests/test_anonymize.py::TestAnonymize::test_known_function_kept_when_passing_tokens', 'tests/test_anonymize.py::TestAnonymize::test_quotes_preserved', 'tests/test_anonymize.py::TestAnonymize::test_render', 'tests/test_anonymize.py::TestAnonymize::test_string_family_forms_anonymized', 'tests/test_dialect_imports.py::TestDialectImports::test_dialect_attribute_access_is_cached', 'tests/test_executor.py::TestExecutor::test_nested_values', 'tests/test_expressions.py::TestExprs::test_alias_with_placeholder', 'tests/test_expressions.py::TestExprs::test_column_expression', 'tests/test_expressions.py::TestExprs::test_data_type_builder', 'tests/test_expressions.py::TestExprs::test_ref_type_builder', 'tests/test_expressions.py::TestExprs::test_table_name', 'tests/test_expressions.py::TestExprs::test_to_dot', 'tests/test_generator.py::TestGenerator::test_fallback_function_sql', 'tests/test_generator.py::TestGenerator::test_fallback_function_var_args_sql', 'tests/test_lineage.py::TestLineage::test_chained_pivots', 'tests/test_lineage.py::TestLineage::test_chained_pivots_mixed', 'tests/test_lineage.py::TestLineage::test_chained_pivots_through_cte', 'tests/test_lineage.py::TestLineage::test_chained_pivots_with_alias_columns', 'tests/test_lineage.py::TestLineage::test_lineage_lateral_flatten', 'tests/test_lineage.py::TestLineage::test_lineage_normalize', 'tests/test_lineage.py::TestLineage::test_multiple_pivoted_sources', 'tests/test_lineage.py::TestLineage::test_table_udtf_snowflake', 'tests/test_lineage.py::TestLineage::test_unpivot', 'tests/test_lineage.py::TestLineage::test_unpivot_with_alias_columns', 'tests/test_lineage.py::TestLineage::test_unpivot_with_cte', 'tests/test_optimizer.py::TestOptimizer::test_annotate_funcs', 'tests/test_optimizer.py::TestOptimizer::test_annotate_json_dot_parts_preserve_meta', 'tests/test_optimizer.py::TestOptimizer::test_annotate_object_construct', 'tests/test_optimizer.py::TestOptimizer::test_annotate_types', 'tests/test_optimizer.py::TestOptimizer::test_canonicalize_internal_names', 'tests/test_optimizer.py::TestOptimizer::test_case_sensitive_json_dot_access', 'tests/test_optimizer.py::TestOptimizer::test_gen', 'tests/test_optimizer.py::TestOptimizer::test_manually_annotate_snowflake', 'tests/test_optimizer.py::TestOptimizer::test_merge_asof_join_inputs', 'tests/test_optimizer.py::TestOptimizer::test_multiple_pivots_annotate_types', 'tests/test_optimizer.py::TestOptimizer::test_normalize', 'tests/test_optimizer.py::TestOptimizer::test_normalize_identifiers', 'tests/test_optimizer.py::TestOptimizer::test_optimize', 'tests/test_optimizer.py::TestOptimizer::test_optimize_snowflake_asof_without_on', 'tests/test_optimizer.py::TestOptimizer::test_pushdown_predicates', 'tests/test_optimizer.py::TestOptimizer::test_pushdown_projection', 'tests/test_optimizer.py::TestOptimizer::test_qualify_columns', 'tests/test_optimizer.py::TestOptimizer::test_qualify_snowflake_positional_column_out_of_range', 'tests/test_optimizer.py::TestOptimizer::test_qualify_snowflake_positional_column_with_visible_schema', 'tests/test_optimizer.py::TestOptimizer::test_qualify_star_rename_quoted', 'tests/test_optimizer.py::TestOptimizer::test_qualify_star_replace_rename_unexpanded', 'tests/test_optimizer.py::TestOptimizer::test_qualify_tables', 'tests/test_optimizer.py::TestOptimizer::test_quote_identifiers', 'tests/test_optimizer.py::TestOptimizer::test_quotes', 'tests/test_optimizer.py::TestOptimizer::test_semistructured', 'tests/test_optimizer.py::TestOptimizer::test_simplify', 'tests/test_optimizer.py::TestOptimizer::test_udtf_annotation', 'tests/test_optimizer.py::TestOptimizer::test_unknown_pivot_columns', 'tests/test_optimizer.py::TestOptimizer::test_validate_columns', 'tests/test_parser.py::TestParser::test_describe_followed_by_dot', 'tests/test_parser.py::TestParser::test_is_precedence', 'tests/test_parser.py::TestParser::test_parse_create_schema', 'tests/test_parser.py::TestParser::test_parse_drop_schema', 'tests/test_parser.py::TestParser::test_parse_errors', 'tests/test_parser.py::TestParser::test_pivot_columns', 'tests/test_parser.py::TestParser::test_ref_type', 'tests/test_parser.py::TestParser::test_text_match_excludes_delimited_tokens', 'tests/test_parser.py::TestParser::test_values_as_identifier', 'tests/test_schema.py::TestSchema::test_cache_normalization', 'tests/test_schema.py::TestSchema::test_schema_add_table_error_formatting', 'tests/test_schema.py::TestSchema::test_schema_normalization', 'tests/test_sqlglotc.py::TestSQLGlotC::test_build_removes_obsolete_extensions', 'tests/test_sqlglotc.py::TestSQLGlotC::test_editable_install_removes_obsolete_extensions', 'tests/test_transpile.py::TestTranspile::test_comments'),
    'spark': ('tests/test_anonymize.py::TestAnonymize::test_hints_anonymized', 'tests/test_build.py::TestBuild::test_build', 'tests/test_expressions.py::TestExprs::test_convert', 'tests/test_expressions.py::TestExprs::test_data_type_builder', 'tests/test_expressions.py::TestExprs::test_expand', 'tests/test_expressions.py::TestExprs::test_expand_with_lazy_source_provider', 'tests/test_expressions.py::TestExprs::test_functions', 'tests/test_expressions.py::TestExprs::test_to_column', 'tests/test_optimizer.py::TestOptimizer::test_annotate_funcs', 'tests/test_optimizer.py::TestOptimizer::test_annotate_types', 'tests/test_optimizer.py::TestOptimizer::test_bracket_annotation', 'tests/test_optimizer.py::TestOptimizer::test_canonicalize_internal_names', 'tests/test_optimizer.py::TestOptimizer::test_map_annotation', 'tests/test_optimizer.py::TestOptimizer::test_merge_subqueries', 'tests/test_optimizer.py::TestOptimizer::test_normalize_identifiers', 'tests/test_optimizer.py::TestOptimizer::test_optimize', 'tests/test_optimizer.py::TestOptimizer::test_qualify_columns', 'tests/test_optimizer.py::TestOptimizer::test_qualify_tables', 'tests/test_optimizer.py::TestOptimizer::test_simplify', 'tests/test_optimizer.py::TestOptimizer::test_validate_columns', 'tests/test_parser.py::TestParser::test_alter_set', 'tests/test_parser.py::TestParser::test_describe_followed_by_dot', 'tests/test_parser.py::TestParser::test_is_precedence', 'tests/test_parser.py::TestParser::test_pivot_columns', 'tests/test_parser.py::TestParser::test_text_match_excludes_delimited_tokens', 'tests/test_sqlglotc.py::TestSQLGlotC::test_build_removes_obsolete_extensions', 'tests/test_sqlglotc.py::TestSQLGlotC::test_editable_install_removes_obsolete_extensions', 'tests/test_transpile.py::TestTranspile::test_time'),
    'spark2': ('tests/test_optimizer.py::TestOptimizer::test_annotate_funcs', 'tests/test_parser.py::TestParser::test_alter_set', 'tests/test_parser.py::TestParser::test_is_precedence', 'tests/test_transpile.py::TestTranspile::test_time'),
    'sqlite': ('tests/test_executor.py::TestExecutor::test_typed_division', 'tests/test_executor.py::TestExecutor::test_typed_division_of_null_is_null', 'tests/test_expressions.py::TestExprs::test_function_building', 'tests/test_generator.py::TestGenerator::test_unsupported_slice', 'tests/test_optimize_cross_joins.py::TestOptimizeCrossJoins::test_conditional_cross_joins', 'tests/test_optimizer.py::TestOptimizer::test_canonicalize', 'tests/test_optimizer.py::TestOptimizer::test_merge_subqueries_match', 'tests/test_optimizer.py::TestOptimizer::test_normalize_identifiers', 'tests/test_optimizer.py::TestOptimizer::test_simplify', 'tests/test_parser.py::TestParser::test_is_precedence', 'tests/test_parser.py::TestParser::test_parse_into_error', 'tests/test_parser.py::TestParser::test_parse_into_errors', 'tests/test_parser.py::TestParser::test_window_clause_without_from', 'tests/test_simplify_between.py::TestSimplifyBetween::test_symmetric_truth_values_and_grouping'),
    'starrocks': ('tests/test_generator.py::TestGenerator::test_time_slice_origin', 'tests/test_lineage.py::TestLineage::test_table_udtfs', 'tests/test_optimizer.py::TestOptimizer::test_qualify_columns', 'tests/test_optimizer.py::TestOptimizer::test_qualify_tables', 'tests/test_optimizer.py::TestOptimizer::test_starrocks_pivot_columns', 'tests/test_optimizer.py::TestOptimizer::test_unknown_pivot_columns', 'tests/test_parser.py::TestParser::test_is_precedence', 'tests/test_parser.py::TestParser::test_pivot_columns', 'tests/test_transpile.py::TestTranspile::test_sql_security'),
    'tableau': ('tests/test_generator.py::TestGenerator::test_unsupported_slice',),
    'teradata': ('tests/test_lineage.py::TestLineage::test_lineage_normalize', 'tests/test_optimizer.py::TestOptimizer::test_normalize_identifiers'),
    'trino': ('tests/test_executor.py::TestExecutor::test_dpipe_source_dialect_coercion', 'tests/test_optimizer.py::TestOptimizer::test_annotate_funcs', 'tests/test_optimizer.py::TestOptimizer::test_normalize_identifiers', 'tests/test_optimizer.py::TestOptimizer::test_pushdown_predicates', 'tests/test_optimizer.py::TestOptimizer::test_qualify_columns', 'tests/test_parser.py::TestParser::test_ref_type', 'tests/test_parser.py::TestParser::test_text_match_excludes_delimited_tokens', 'tests/test_transpile.py::TestTranspile::test_sql_security'),
    'tsql': ('tests/test_anonymize.py::TestAnonymize::test_quotes_preserved', 'tests/test_expressions.py::TestExprs::test_ref_type_builder', 'tests/test_generator.py::TestGenerator::test_unsupported_slice', 'tests/test_optimizer.py::TestOptimizer::test_annotate_funcs', 'tests/test_optimizer.py::TestOptimizer::test_canonicalize', 'tests/test_optimizer.py::TestOptimizer::test_simplify', 'tests/test_parser.py::TestParser::test_ref_type', 'tests/test_parser.py::TestParser::test_text_match_excludes_delimited_tokens', 'tests/test_schema.py::TestSchema::test_schema_add_table_error_formatting', 'tests/test_schema.py::TestSchema::test_schema_normalization'),
}
ALL_DIALECT_TEST_IDS: tuple[str, ...] = (
    'tests/test_generator.py::TestGenerator::test_generator_memory',
    'tests/test_time.py::TestTime::test_canonical_time_format_vocabulary',
)
FIXTURE_TEST_IDS: dict[str, tuple[str, ...]] = {
    'tests/fixtures/identity.sql': ('tests/test_serde.py::TestSerde::test_serde', 'tests/test_transpile.py::TestTranspile::test_identity'),
    'tests/fixtures/jsonpath/cts.json': ('tests/test_jsonpath.py::TestJsonpath::test_cts_file',),
    'tests/fixtures/optimizer/annotate_functions.sql': ('tests/test_optimizer.py::TestOptimizer::test_annotate_funcs',),
    'tests/fixtures/optimizer/annotate_types.sql': ('tests/test_optimizer.py::TestOptimizer::test_annotate_types',),
    'tests/fixtures/optimizer/canonicalize.sql': ('tests/test_optimizer.py::TestOptimizer::test_canonicalize',),
    'tests/fixtures/optimizer/canonicalize_internal_names.sql': ('tests/test_optimizer.py::TestOptimizer::test_canonicalize_internal_names',),
    'tests/fixtures/optimizer/eliminate_ctes.sql': ('tests/test_optimizer.py::TestOptimizer::test_eliminate_ctes',),
    'tests/fixtures/optimizer/eliminate_joins.sql': ('tests/test_optimizer.py::TestOptimizer::test_eliminate_joins',),
    'tests/fixtures/optimizer/eliminate_subqueries.sql': ('tests/test_optimizer.py::TestOptimizer::test_eliminate_subqueries',),
    'tests/fixtures/optimizer/isolate_table_selects.sql': ('tests/test_optimizer.py::TestOptimizer::test_isolate_table_selects',),
    'tests/fixtures/optimizer/merge_subqueries.sql': ('tests/test_optimizer.py::TestOptimizer::test_merge_subqueries',),
    'tests/fixtures/optimizer/normalize.sql': ('tests/test_optimizer.py::TestOptimizer::test_normalize',),
    'tests/fixtures/optimizer/normalize_identifiers.sql': ('tests/test_optimizer.py::TestOptimizer::test_normalize_identifiers',),
    'tests/fixtures/optimizer/optimize_joins.sql': ('tests/test_optimizer.py::TestOptimizer::test_optimize_joins',),
    'tests/fixtures/optimizer/optimizer.sql': ('tests/test_optimizer.py::TestOptimizer::test_optimize', 'tests/test_sqlglotc.py::TestSQLGlotC::test_build_removes_obsolete_extensions', 'tests/test_sqlglotc.py::TestSQLGlotC::test_editable_install_removes_obsolete_extensions'),
    'tests/fixtures/optimizer/pushdown_cte_alias_columns.sql': ('tests/test_optimizer.py::TestOptimizer::test_pushdown_cte_alias_columns',),
    'tests/fixtures/optimizer/pushdown_predicates.sql': ('tests/test_optimizer.py::TestOptimizer::test_pushdown_predicates',),
    'tests/fixtures/optimizer/pushdown_projections.sql': ('tests/test_optimizer.py::TestOptimizer::test_pushdown_projection',),
    'tests/fixtures/optimizer/qualify_columns.sql': ('tests/test_optimizer.py::TestOptimizer::test_qualify_columns',),
    'tests/fixtures/optimizer/qualify_columns__invalid.sql': ('tests/test_optimizer.py::TestOptimizer::test_qualify_columns__invalid',),
    'tests/fixtures/optimizer/qualify_columns__with_invisible.sql': ('tests/test_optimizer.py::TestOptimizer::test_qualify_columns__with_invisible',),
    'tests/fixtures/optimizer/qualify_columns_ddl.sql': ('tests/test_optimizer.py::TestOptimizer::test_qualify_columns',),
    'tests/fixtures/optimizer/qualify_tables.sql': ('tests/test_optimizer.py::TestOptimizer::test_qualify_tables',),
    'tests/fixtures/optimizer/quote_identifiers.sql': ('tests/test_optimizer.py::TestOptimizer::test_quote_identifiers',),
    'tests/fixtures/optimizer/simplify.sql': ('tests/test_optimizer.py::TestOptimizer::test_simplify',),
    'tests/fixtures/optimizer/tpc-ds/tpc-ds.sql': ('tests/test_optimizer.py::TestOptimizer::test_tpcds',),
    'tests/fixtures/optimizer/tpc-h/tpc-h.sql': ('tests/test_optimizer.py::TestOptimizer::test_tpch',),
    'tests/fixtures/optimizer/unnest_subqueries.sql': ('tests/test_optimizer.py::TestOptimizer::test_unnest_subqueries',),
    'tests/fixtures/partial.sql': ('tests/test_transpile.py::TestTranspile::test_partial',),
    'tests/fixtures/pretty.sql': ('tests/test_transpile.py::TestTranspile::test_pretty',),
}
FIXTURE_SETUP_FILES: dict[str, tuple[str, ...]] = {
    'tests/fixtures/optimizer/tpc-ds/call_center.csv.gz': ('tests/test_executor.py',),
    'tests/fixtures/optimizer/tpc-ds/catalog_page.csv.gz': ('tests/test_executor.py',),
    'tests/fixtures/optimizer/tpc-ds/catalog_returns.csv.gz': ('tests/test_executor.py',),
    'tests/fixtures/optimizer/tpc-ds/catalog_sales.csv.gz': ('tests/test_executor.py',),
    'tests/fixtures/optimizer/tpc-ds/customer.csv.gz': ('tests/test_executor.py',),
    'tests/fixtures/optimizer/tpc-ds/customer_address.csv.gz': ('tests/test_executor.py',),
    'tests/fixtures/optimizer/tpc-ds/customer_demographics.csv.gz': ('tests/test_executor.py',),
    'tests/fixtures/optimizer/tpc-ds/date_dim.csv.gz': ('tests/test_executor.py',),
    'tests/fixtures/optimizer/tpc-ds/household_demographics.csv.gz': ('tests/test_executor.py',),
    'tests/fixtures/optimizer/tpc-ds/income_band.csv.gz': ('tests/test_executor.py',),
    'tests/fixtures/optimizer/tpc-ds/inventory.csv.gz': ('tests/test_executor.py',),
    'tests/fixtures/optimizer/tpc-ds/item.csv.gz': ('tests/test_executor.py',),
    'tests/fixtures/optimizer/tpc-ds/promotion.csv.gz': ('tests/test_executor.py',),
    'tests/fixtures/optimizer/tpc-ds/reason.csv.gz': ('tests/test_executor.py',),
    'tests/fixtures/optimizer/tpc-ds/ship_mode.csv.gz': ('tests/test_executor.py',),
    'tests/fixtures/optimizer/tpc-ds/store.csv.gz': ('tests/test_executor.py',),
    'tests/fixtures/optimizer/tpc-ds/store_returns.csv.gz': ('tests/test_executor.py',),
    'tests/fixtures/optimizer/tpc-ds/store_sales.csv.gz': ('tests/test_executor.py',),
    'tests/fixtures/optimizer/tpc-ds/time_dim.csv.gz': ('tests/test_executor.py',),
    'tests/fixtures/optimizer/tpc-ds/tpc-ds.sql': ('tests/test_executor.py',),
    'tests/fixtures/optimizer/tpc-ds/warehouse.csv.gz': ('tests/test_executor.py',),
    'tests/fixtures/optimizer/tpc-ds/web_page.csv.gz': ('tests/test_executor.py',),
    'tests/fixtures/optimizer/tpc-ds/web_returns.csv.gz': ('tests/test_executor.py',),
    'tests/fixtures/optimizer/tpc-ds/web_sales.csv.gz': ('tests/test_executor.py',),
    'tests/fixtures/optimizer/tpc-ds/web_site.csv.gz': ('tests/test_executor.py',),
    'tests/fixtures/optimizer/tpc-h/customer.csv.gz': ('tests/test_executor.py',),
    'tests/fixtures/optimizer/tpc-h/lineitem.csv.gz': ('tests/test_executor.py',),
    'tests/fixtures/optimizer/tpc-h/nation.csv.gz': ('tests/test_executor.py',),
    'tests/fixtures/optimizer/tpc-h/orders.csv.gz': ('tests/test_executor.py',),
    'tests/fixtures/optimizer/tpc-h/part.csv.gz': ('tests/test_executor.py',),
    'tests/fixtures/optimizer/tpc-h/partsupp.csv.gz': ('tests/test_executor.py',),
    'tests/fixtures/optimizer/tpc-h/region.csv.gz': ('tests/test_executor.py',),
    'tests/fixtures/optimizer/tpc-h/supplier.csv.gz': ('tests/test_executor.py',),
    'tests/fixtures/optimizer/tpc-h/tpc-h.sql': ('tests/test_executor.py',),
}
MODULE_TEST_IMPORTERS: dict[str, tuple[str, ...]] = {
    'sqlglot/__init__.py': ('tests/dialects/test_athena.py', 'tests/dialects/test_bigquery.py', 'tests/dialects/test_clickhouse.py', 'tests/dialects/test_databricks.py', 'tests/dialects/test_dialect.py', 'tests/dialects/test_dremio.py', 'tests/dialects/test_duckdb.py', 'tests/dialects/test_exasol.py', 'tests/dialects/test_oracle.py', 'tests/dialects/test_postgres.py', 'tests/dialects/test_presto.py', 'tests/dialects/test_redshift.py', 'tests/dialects/test_singlestore.py', 'tests/dialects/test_snowflake.py', 'tests/dialects/test_spark.py', 'tests/dialects/test_sqlite.py', 'tests/dialects/test_starrocks.py', 'tests/dialects/test_teradata.py', 'tests/dialects/test_trino.py', 'tests/dialects/test_tsql.py', 'tests/test_build.py', 'tests/test_diff.py', 'tests/test_executor.py', 'tests/test_expressions.py', 'tests/test_generator.py', 'tests/test_lineage.py', 'tests/test_optimizer.py', 'tests/test_parser.py', 'tests/test_schema.py', 'tests/test_serde.py', 'tests/test_simplify_between.py', 'tests/test_transforms.py', 'tests/test_transpile.py'),
    'sqlglot/anonymize.py': ('tests/test_anonymize.py',),
    'sqlglot/dialects/__init__.py': ('tests/test_dialect_imports.py', 'tests/test_generator.py'),
    'sqlglot/dialects/athena.py': ('tests/test_dialect_imports.py',),
    'sqlglot/dialects/bigquery.py': ('tests/dialects/test_bigquery.py', 'tests/dialects/test_dialect.py', 'tests/test_tokens.py'),
    'sqlglot/dialects/clickhouse.py': ('tests/dialects/test_clickhouse.py',),
    'sqlglot/dialects/dialect.py': ('tests/dialects/test_bigquery.py', 'tests/dialects/test_dialect.py', 'tests/dialects/test_druid.py', 'tests/dialects/test_spark.py', 'tests/test_dialect_entry_points.py', 'tests/test_time.py'),
    'sqlglot/dialects/duckdb.py': ('tests/dialects/test_dialect.py', 'tests/dialects/test_duckdb.py'),
    'sqlglot/dialects/exasol.py': ('tests/dialects/test_exasol.py',),
    'sqlglot/dialects/hive.py': ('tests/dialects/test_dialect.py',),
    'sqlglot/dialects/mysql.py': ('tests/dialects/test_mysql.py',),
    'sqlglot/dialects/snowflake.py': ('tests/dialects/test_dialect.py',),
    'sqlglot/dialects/spark2.py': ('tests/dialects/test_dialect.py',),
    'sqlglot/diff.py': ('tests/test_diff.py',),
    'sqlglot/errors.py': ('tests/dialects/test_athena.py', 'tests/dialects/test_bigquery.py', 'tests/dialects/test_clickhouse.py', 'tests/dialects/test_databricks.py', 'tests/dialects/test_dialect.py', 'tests/dialects/test_doris.py', 'tests/dialects/test_dremio.py', 'tests/dialects/test_duckdb.py', 'tests/dialects/test_exasol.py', 'tests/dialects/test_hive.py', 'tests/dialects/test_mysql.py', 'tests/dialects/test_oracle.py', 'tests/dialects/test_postgres.py', 'tests/dialects/test_presto.py', 'tests/dialects/test_redshift.py', 'tests/dialects/test_snowflake.py', 'tests/dialects/test_spark.py', 'tests/dialects/test_sqlite.py', 'tests/dialects/test_starrocks.py', 'tests/dialects/test_teradata.py', 'tests/dialects/test_trino.py', 'tests/dialects/test_tsql.py', 'tests/test_errors.py', 'tests/test_executor.py', 'tests/test_expressions.py', 'tests/test_generator.py', 'tests/test_jsonpath.py', 'tests/test_optimizer.py', 'tests/test_parser.py', 'tests/test_schema.py', 'tests/test_tokens.py', 'tests/test_transpile.py'),
    'sqlglot/executor/__init__.py': ('tests/test_executor.py',),
    'sqlglot/executor/env.py': ('tests/test_executor.py',),
    'sqlglot/executor/python.py': ('tests/test_executor.py',),
    'sqlglot/executor/table.py': ('tests/test_executor.py',),
    'sqlglot/expressions/__init__.py': ('tests/dialects/test_athena.py', 'tests/dialects/test_bigquery.py', 'tests/dialects/test_clickhouse.py', 'tests/dialects/test_databricks.py', 'tests/dialects/test_dialect.py', 'tests/dialects/test_doris.py', 'tests/dialects/test_dremio.py', 'tests/dialects/test_duckdb.py', 'tests/dialects/test_dune.py', 'tests/dialects/test_exasol.py', 'tests/dialects/test_hive.py', 'tests/dialects/test_mysql.py', 'tests/dialects/test_oracle.py', 'tests/dialects/test_postgres.py', 'tests/dialects/test_presto.py', 'tests/dialects/test_redshift.py', 'tests/dialects/test_singlestore.py', 'tests/dialects/test_snowflake.py', 'tests/dialects/test_solr.py', 'tests/dialects/test_spark.py', 'tests/dialects/test_sqlite.py', 'tests/dialects/test_starrocks.py', 'tests/dialects/test_teradata.py', 'tests/dialects/test_trino.py', 'tests/dialects/test_tsql.py', 'tests/test_build.py', 'tests/test_diff.py', 'tests/test_executor.py', 'tests/test_expressions.py', 'tests/test_generator.py', 'tests/test_jsonpath.py', 'tests/test_optimize_cross_joins.py', 'tests/test_optimizer.py', 'tests/test_parser.py', 'tests/test_schema.py', 'tests/test_serde.py', 'tests/test_transforms.py'),
    'sqlglot/expressions/core.py': ('tests/test_generator.py', 'tests/test_serde.py'),
    'sqlglot/generator.py': ('tests/dialects/test_bigquery.py', 'tests/dialects/test_dialect.py', 'tests/dialects/test_duckdb.py', 'tests/dialects/test_postgres.py', 'tests/dialects/test_presto.py', 'tests/dialects/test_sqlite.py', 'tests/dialects/test_trino.py', 'tests/test_diff.py', 'tests/test_generator.py'),
    'sqlglot/generators/hive.py': ('tests/test_time.py',),
    'sqlglot/helper.py': ('tests/dialects/test_bigquery.py', 'tests/dialects/test_clickhouse.py', 'tests/dialects/test_duckdb.py', 'tests/dialects/test_postgres.py', 'tests/dialects/test_presto.py', 'tests/dialects/test_sqlite.py', 'tests/test_helper.py', 'tests/test_transpile.py'),
    'sqlglot/jsonpath.py': ('tests/test_jsonpath.py',),
    'sqlglot/lineage.py': ('tests/dialects/test_teradata.py', 'tests/test_lineage.py'),
    'sqlglot/optimizer/__init__.py': ('tests/test_optimizer.py',),
    'sqlglot/optimizer/annotate_types.py': ('tests/dialects/test_bigquery.py', 'tests/dialects/test_clickhouse.py', 'tests/dialects/test_dremio.py', 'tests/dialects/test_duckdb.py', 'tests/dialects/test_mysql.py', 'tests/dialects/test_postgres.py', 'tests/dialects/test_presto.py', 'tests/dialects/test_redshift.py', 'tests/dialects/test_snowflake.py', 'tests/dialects/test_spark.py', 'tests/dialects/test_sqlite.py', 'tests/dialects/test_teradata.py', 'tests/dialects/test_trino.py', 'tests/dialects/test_tsql.py', 'tests/test_optimizer.py', 'tests/test_schema.py', 'tests/test_serde.py'),
    'sqlglot/optimizer/canonicalize_internal_names.py': ('tests/test_optimizer.py',),
    'sqlglot/optimizer/journal.py': ('tests/test_optimizer.py',),
    'sqlglot/optimizer/normalize.py': ('tests/test_optimizer.py',),
    'sqlglot/optimizer/normalize_identifiers.py': ('tests/dialects/test_snowflake.py',),
    'sqlglot/optimizer/optimize_joins.py': ('tests/test_optimize_cross_joins.py',),
    'sqlglot/optimizer/optimizer.py': ('tests/dialects/test_postgres.py', 'tests/test_executor.py', 'tests/test_optimize_cross_joins.py', 'tests/test_simplify_between.py'),
    'sqlglot/optimizer/qualify.py': ('tests/dialects/test_athena.py', 'tests/dialects/test_bigquery.py', 'tests/dialects/test_clickhouse.py', 'tests/dialects/test_oracle.py', 'tests/dialects/test_singlestore.py', 'tests/dialects/test_snowflake.py', 'tests/dialects/test_spark.py', 'tests/dialects/test_teradata.py', 'tests/dialects/test_tsql.py', 'tests/test_lineage.py', 'tests/test_optimize_cross_joins.py', 'tests/test_optimizer.py', 'tests/test_schema.py'),
    'sqlglot/optimizer/qualify_columns.py': ('tests/dialects/test_clickhouse.py', 'tests/dialects/test_snowflake.py'),
    'sqlglot/optimizer/resolver.py': ('tests/test_optimizer.py',),
    'sqlglot/optimizer/scope.py': ('tests/dialects/test_clickhouse.py', 'tests/test_lineage.py', 'tests/test_optimizer.py', 'tests/test_schema.py'),
    'sqlglot/optimizer/simplify.py': ('tests/dialects/test_postgres.py', 'tests/test_simplify_between.py'),
    'sqlglot/parser.py': ('tests/dialects/test_bigquery.py', 'tests/dialects/test_dialect.py', 'tests/dialects/test_snowflake.py', 'tests/test_expressions.py', 'tests/test_parser.py', 'tests/test_transpile.py'),
    'sqlglot/parsers/base.py': ('tests/dialects/test_dialect.py',),
    'sqlglot/parsers/snowflake.py': ('tests/dialects/test_dialect.py', 'tests/dialects/test_snowflake.py', 'tests/test_generator.py'),
    'sqlglot/planner.py': ('tests/test_executor.py',),
    'sqlglot/schema.py': ('tests/dialects/test_redshift.py', 'tests/test_lineage.py', 'tests/test_optimizer.py', 'tests/test_schema.py'),
    'sqlglot/time.py': ('tests/test_time.py',),
    'sqlglot/tokens.py': ('tests/dialects/test_athena.py', 'tests/test_anonymize.py', 'tests/test_tokens.py'),
    'sqlglot/transforms.py': ('tests/test_transforms.py',),
    'sqlglot/typing/__init__.py': ('tests/test_typing.py',),
}
SOURCE_IMPORTERS: dict[str, tuple[str, ...]] = {
    'sqlglot/_typing.py': ('sqlglot/dialects/dialect.py', 'sqlglot/diff.py', 'sqlglot/expressions/builders.py', 'sqlglot/expressions/core.py', 'sqlglot/expressions/datatypes.py', 'sqlglot/expressions/dml.py', 'sqlglot/expressions/functions.py', 'sqlglot/expressions/query.py', 'sqlglot/generator.py', 'sqlglot/generators/snowflake.py', 'sqlglot/lineage.py', 'sqlglot/optimizer/annotate_types.py', 'sqlglot/optimizer/canonicalize_internal_names.py', 'sqlglot/optimizer/eliminate_ctes.py', 'sqlglot/optimizer/eliminate_joins.py', 'sqlglot/optimizer/eliminate_subqueries.py', 'sqlglot/optimizer/isolate_table_selects.py', 'sqlglot/optimizer/merge_subqueries.py', 'sqlglot/optimizer/normalize_identifiers.py', 'sqlglot/optimizer/optimize_joins.py', 'sqlglot/optimizer/pushdown_predicates.py', 'sqlglot/optimizer/pushdown_projections.py', 'sqlglot/optimizer/qualify.py', 'sqlglot/optimizer/qualify_columns.py', 'sqlglot/optimizer/qualify_tables.py', 'sqlglot/optimizer/scope.py', 'sqlglot/optimizer/unnest_subqueries.py', 'sqlglot/parser.py', 'sqlglot/parsers/bigquery.py', 'sqlglot/parsers/clickhouse.py', 'sqlglot/parsers/oracle.py', 'sqlglot/parsers/redshift.py', 'sqlglot/parsers/snowflake.py', 'sqlglot/parsers/tsql.py', 'sqlglot/schema.py', 'sqlglot/transforms.py', 'sqlglot/typing/spark2.py'),
    'sqlglot/dialects/athena.py': ('sqlglot/optimizer/pushdown_predicates.py',),
    'sqlglot/dialects/bigquery.py': ('sqlglot/typing/bigquery.py',),
    'sqlglot/dialects/clickhouse.py': ('sqlglot/typing/clickhouse.py',),
    'sqlglot/dialects/databricks.py': ('sqlglot/typing/databricks.py',),
    'sqlglot/dialects/dialect.py': ('sqlglot/_typing.py', 'sqlglot/anonymize.py', 'sqlglot/dialects/athena.py', 'sqlglot/dialects/bigquery.py', 'sqlglot/dialects/clickhouse.py', 'sqlglot/dialects/dax.py', 'sqlglot/dialects/doris.py', 'sqlglot/dialects/dremio.py', 'sqlglot/dialects/drill.py', 'sqlglot/dialects/druid.py', 'sqlglot/dialects/duckdb.py', 'sqlglot/dialects/exasol.py', 'sqlglot/dialects/fabric.py', 'sqlglot/dialects/hive.py', 'sqlglot/dialects/mysql.py', 'sqlglot/dialects/oracle.py', 'sqlglot/dialects/postgres.py', 'sqlglot/dialects/presto.py', 'sqlglot/dialects/prql.py', 'sqlglot/dialects/redshift.py', 'sqlglot/dialects/singlestore.py', 'sqlglot/dialects/snowflake.py', 'sqlglot/dialects/solr.py', 'sqlglot/dialects/spark.py', 'sqlglot/dialects/spark2.py', 'sqlglot/dialects/sqlite.py', 'sqlglot/dialects/starrocks.py', 'sqlglot/dialects/tableau.py', 'sqlglot/dialects/teradata.py', 'sqlglot/dialects/tsql.py', 'sqlglot/diff.py', 'sqlglot/executor/__init__.py', 'sqlglot/executor/python.py', 'sqlglot/executor/table.py', 'sqlglot/expressions/builders.py', 'sqlglot/expressions/core.py', 'sqlglot/expressions/datatypes.py', 'sqlglot/expressions/dml.py', 'sqlglot/expressions/query.py', 'sqlglot/generator.py', 'sqlglot/generators/bigquery.py', 'sqlglot/generators/clickhouse.py', 'sqlglot/generators/databricks.py', 'sqlglot/generators/doris.py', 'sqlglot/generators/dremio.py', 'sqlglot/generators/drill.py', 'sqlglot/generators/druid.py', 'sqlglot/generators/duckdb.py', 'sqlglot/generators/exasol.py', 'sqlglot/generators/hive.py', 'sqlglot/generators/mysql.py', 'sqlglot/generators/oracle.py', 'sqlglot/generators/postgres.py', 'sqlglot/generators/presto.py', 'sqlglot/generators/python.py', 'sqlglot/generators/redshift.py', 'sqlglot/generators/singlestore.py', 'sqlglot/generators/snowflake.py', 'sqlglot/generators/spark.py', 'sqlglot/generators/spark2.py', 'sqlglot/generators/sqlite.py', 'sqlglot/generators/starrocks.py', 'sqlglot/generators/tableau.py', 'sqlglot/generators/teradata.py', 'sqlglot/generators/trino.py', 'sqlglot/generators/tsql.py', 'sqlglot/jsonpath.py', 'sqlglot/lineage.py', 'sqlglot/optimizer/annotate_types.py', 'sqlglot/optimizer/canonicalize.py', 'sqlglot/optimizer/isolate_table_selects.py', 'sqlglot/optimizer/normalize_identifiers.py', 'sqlglot/optimizer/optimizer.py', 'sqlglot/optimizer/pushdown_predicates.py', 'sqlglot/optimizer/qualify.py', 'sqlglot/optimizer/qualify_columns.py', 'sqlglot/optimizer/qualify_tables.py', 'sqlglot/optimizer/resolver.py', 'sqlglot/optimizer/simplify.py', 'sqlglot/parser.py', 'sqlglot/parsers/athena.py', 'sqlglot/parsers/bigquery.py', 'sqlglot/parsers/clickhouse.py', 'sqlglot/parsers/databricks.py', 'sqlglot/parsers/doris.py', 'sqlglot/parsers/dremio.py', 'sqlglot/parsers/drill.py', 'sqlglot/parsers/duckdb.py', 'sqlglot/parsers/exasol.py', 'sqlglot/parsers/hive.py', 'sqlglot/parsers/mysql.py', 'sqlglot/parsers/oracle.py', 'sqlglot/parsers/postgres.py', 'sqlglot/parsers/presto.py', 'sqlglot/parsers/redshift.py', 'sqlglot/parsers/singlestore.py', 'sqlglot/parsers/snowflake.py', 'sqlglot/parsers/spark.py', 'sqlglot/parsers/spark2.py', 'sqlglot/parsers/starrocks.py', 'sqlglot/parsers/teradata.py', 'sqlglot/parsers/tsql.py', 'sqlglot/schema.py', 'sqlglot/tokens.py'),
    'sqlglot/dialects/doris.py': ('sqlglot/generators/doris.py',),
    'sqlglot/dialects/drill.py': ('sqlglot/generators/drill.py',),
    'sqlglot/dialects/duckdb.py': ('sqlglot/generators/duckdb.py', 'sqlglot/parsers/duckdb.py'),
    'sqlglot/dialects/fabric.py': ('sqlglot/generators/fabric.py',),
    'sqlglot/dialects/hive.py': ('sqlglot/dialects/athena.py', 'sqlglot/dialects/spark2.py', 'sqlglot/generators/athena.py', 'sqlglot/generators/hive.py', 'sqlglot/generators/presto.py', 'sqlglot/parsers/athena.py'),
    'sqlglot/dialects/mysql.py': ('sqlglot/dialects/doris.py', 'sqlglot/dialects/presto.py', 'sqlglot/dialects/singlestore.py', 'sqlglot/dialects/starrocks.py', 'sqlglot/parsers/mysql.py', 'sqlglot/parsers/singlestore.py'),
    'sqlglot/dialects/postgres.py': ('sqlglot/dialects/materialize.py', 'sqlglot/dialects/redshift.py', 'sqlglot/dialects/risingwave.py'),
    'sqlglot/dialects/presto.py': ('sqlglot/dialects/trino.py', 'sqlglot/generators/presto.py', 'sqlglot/optimizer/pushdown_predicates.py', 'sqlglot/parsers/presto.py'),
    'sqlglot/dialects/snowflake.py': ('sqlglot/typing/snowflake.py',),
    'sqlglot/dialects/spark.py': ('sqlglot/dialects/databricks.py', 'sqlglot/parsers/spark2.py'),
    'sqlglot/dialects/spark2.py': ('sqlglot/dialects/spark.py',),
    'sqlglot/dialects/teradata.py': ('sqlglot/parsers/presto.py',),
    'sqlglot/dialects/trino.py': ('sqlglot/dialects/athena.py', 'sqlglot/dialects/dune.py', 'sqlglot/generators/athena.py', 'sqlglot/parsers/athena.py'),
    'sqlglot/dialects/tsql.py': ('sqlglot/dialects/fabric.py', 'sqlglot/generator.py', 'sqlglot/generators/tsql.py', 'sqlglot/parsers/tsql.py'),
    'sqlglot/errors.py': ('sqlglot/_typing.py', 'sqlglot/anonymize.py', 'sqlglot/dialects/athena.py', 'sqlglot/dialects/dialect.py', 'sqlglot/executor/__init__.py', 'sqlglot/executor/python.py', 'sqlglot/expressions/builders.py', 'sqlglot/expressions/core.py', 'sqlglot/expressions/datatypes.py', 'sqlglot/expressions/query.py', 'sqlglot/generator.py', 'sqlglot/generators/bigquery.py', 'sqlglot/generators/exasol.py', 'sqlglot/jsonpath.py', 'sqlglot/lineage.py', 'sqlglot/optimizer/annotate_types.py', 'sqlglot/optimizer/isolate_table_selects.py', 'sqlglot/optimizer/normalize.py', 'sqlglot/optimizer/optimize_joins.py', 'sqlglot/optimizer/pushdown_projections.py', 'sqlglot/optimizer/qualify_columns.py', 'sqlglot/optimizer/resolver.py', 'sqlglot/optimizer/scope.py', 'sqlglot/parser.py', 'sqlglot/parsers/athena.py', 'sqlglot/schema.py', 'sqlglot/tokenizer_core.py', 'sqlglot/transforms.py'),
    'sqlglot/executor/context.py': ('sqlglot/executor/python.py',),
    'sqlglot/executor/env.py': ('sqlglot/executor/context.py', 'sqlglot/executor/python.py'),
    'sqlglot/executor/python.py': ('sqlglot/executor/__init__.py',),
    'sqlglot/executor/table.py': ('sqlglot/executor/__init__.py', 'sqlglot/executor/context.py', 'sqlglot/executor/python.py'),
    'sqlglot/expressions/__init__.py': ('sqlglot/_typing.py', 'sqlglot/dialects/bigquery.py', 'sqlglot/dialects/clickhouse.py', 'sqlglot/dialects/databricks.py', 'sqlglot/dialects/dialect.py', 'sqlglot/dialects/doris.py', 'sqlglot/dialects/duckdb.py', 'sqlglot/dialects/fabric.py', 'sqlglot/dialects/hive.py', 'sqlglot/dialects/mysql.py', 'sqlglot/dialects/oracle.py', 'sqlglot/dialects/postgres.py', 'sqlglot/dialects/presto.py', 'sqlglot/dialects/singlestore.py', 'sqlglot/dialects/snowflake.py', 'sqlglot/dialects/spark.py', 'sqlglot/dialects/spark2.py', 'sqlglot/dialects/sqlite.py', 'sqlglot/dialects/starrocks.py', 'sqlglot/dialects/tsql.py', 'sqlglot/diff.py', 'sqlglot/executor/__init__.py', 'sqlglot/executor/env.py', 'sqlglot/executor/python.py', 'sqlglot/expressions/builders.py', 'sqlglot/generator.py', 'sqlglot/generators/athena.py', 'sqlglot/generators/bigquery.py', 'sqlglot/generators/clickhouse.py', 'sqlglot/generators/databricks.py', 'sqlglot/generators/doris.py', 'sqlglot/generators/dremio.py', 'sqlglot/generators/drill.py', 'sqlglot/generators/druid.py', 'sqlglot/generators/duckdb.py', 'sqlglot/generators/dune.py', 'sqlglot/generators/exasol.py', 'sqlglot/generators/fabric.py', 'sqlglot/generators/hive.py', 'sqlglot/generators/materialize.py', 'sqlglot/generators/mysql.py', 'sqlglot/generators/oracle.py', 'sqlglot/generators/postgres.py', 'sqlglot/generators/presto.py', 'sqlglot/generators/python.py', 'sqlglot/generators/redshift.py', 'sqlglot/generators/risingwave.py', 'sqlglot/generators/singlestore.py', 'sqlglot/generators/snowflake.py', 'sqlglot/generators/spark.py', 'sqlglot/generators/spark2.py', 'sqlglot/generators/sqlite.py', 'sqlglot/generators/starrocks.py', 'sqlglot/generators/tableau.py', 'sqlglot/generators/teradata.py', 'sqlglot/generators/trino.py', 'sqlglot/generators/tsql.py', 'sqlglot/helper.py', 'sqlglot/jsonpath.py', 'sqlglot/lineage.py', 'sqlglot/optimizer/annotate_types.py', 'sqlglot/optimizer/canonicalize.py', 'sqlglot/optimizer/canonicalize_internal_names.py', 'sqlglot/optimizer/eliminate_joins.py', 'sqlglot/optimizer/eliminate_subqueries.py', 'sqlglot/optimizer/helpers.py', 'sqlglot/optimizer/isolate_table_selects.py', 'sqlglot/optimizer/journal.py', 'sqlglot/optimizer/merge_subqueries.py', 'sqlglot/optimizer/normalize.py', 'sqlglot/optimizer/normalize_identifiers.py', 'sqlglot/optimizer/optimize_joins.py', 'sqlglot/optimizer/optimizer.py', 'sqlglot/optimizer/pushdown_predicates.py', 'sqlglot/optimizer/pushdown_projections.py', 'sqlglot/optimizer/qualify.py', 'sqlglot/optimizer/qualify_columns.py', 'sqlglot/optimizer/qualify_tables.py', 'sqlglot/optimizer/resolver.py', 'sqlglot/optimizer/scope.py', 'sqlglot/optimizer/simplify.py', 'sqlglot/optimizer/unnest_subqueries.py', 'sqlglot/parser.py', 'sqlglot/parsers/athena.py', 'sqlglot/parsers/base.py', 'sqlglot/parsers/bigquery.py', 'sqlglot/parsers/clickhouse.py', 'sqlglot/parsers/databricks.py', 'sqlglot/parsers/dax.py', 'sqlglot/parsers/doris.py', 'sqlglot/parsers/dremio.py', 'sqlglot/parsers/drill.py', 'sqlglot/parsers/duckdb.py', 'sqlglot/parsers/exasol.py', 'sqlglot/parsers/fabric.py', 'sqlglot/parsers/hive.py', 'sqlglot/parsers/materialize.py', 'sqlglot/parsers/mysql.py', 'sqlglot/parsers/oracle.py', 'sqlglot/parsers/postgres.py', 'sqlglot/parsers/presto.py', 'sqlglot/parsers/prql.py', 'sqlglot/parsers/redshift.py', 'sqlglot/parsers/risingwave.py', 'sqlglot/parsers/singlestore.py', 'sqlglot/parsers/snowflake.py', 'sqlglot/parsers/solr.py', 'sqlglot/parsers/spark.py', 'sqlglot/parsers/spark2.py', 'sqlglot/parsers/sqlite.py', 'sqlglot/parsers/starrocks.py', 'sqlglot/parsers/tableau.py', 'sqlglot/parsers/teradata.py', 'sqlglot/parsers/trino.py', 'sqlglot/parsers/tsql.py', 'sqlglot/planner.py', 'sqlglot/schema.py', 'sqlglot/serde.py', 'sqlglot/transforms.py', 'sqlglot/typing/__init__.py', 'sqlglot/typing/bigquery.py', 'sqlglot/typing/clickhouse.py', 'sqlglot/typing/databricks.py', 'sqlglot/typing/duckdb.py', 'sqlglot/typing/hive.py', 'sqlglot/typing/mysql.py', 'sqlglot/typing/postgres.py', 'sqlglot/typing/presto.py', 'sqlglot/typing/redshift.py', 'sqlglot/typing/snowflake.py', 'sqlglot/typing/spark.py', 'sqlglot/typing/spark2.py', 'sqlglot/typing/sqlite.py', 'sqlglot/typing/tsql.py'),
    'sqlglot/expressions/aggregate.py': ('sqlglot/expressions/__init__.py', 'sqlglot/expressions/functions.py'),
    'sqlglot/expressions/array.py': ('sqlglot/expressions/__init__.py', 'sqlglot/expressions/builders.py', 'sqlglot/expressions/core.py', 'sqlglot/expressions/functions.py', 'sqlglot/expressions/query.py'),
    'sqlglot/expressions/builders.py': ('sqlglot/expressions/__init__.py',),
    'sqlglot/expressions/constraints.py': ('sqlglot/expressions/__init__.py', 'sqlglot/expressions/query.py'),
    'sqlglot/expressions/core.py': ('sqlglot/expressions/__init__.py', 'sqlglot/expressions/aggregate.py', 'sqlglot/expressions/array.py', 'sqlglot/expressions/builders.py', 'sqlglot/expressions/constraints.py', 'sqlglot/expressions/datatypes.py', 'sqlglot/expressions/ddl.py', 'sqlglot/expressions/dml.py', 'sqlglot/expressions/functions.py', 'sqlglot/expressions/json.py', 'sqlglot/expressions/math.py', 'sqlglot/expressions/properties.py', 'sqlglot/expressions/query.py', 'sqlglot/expressions/string.py', 'sqlglot/expressions/temporal.py', 'sqlglot/generator.py'),
    'sqlglot/expressions/datatypes.py': ('sqlglot/expressions/__init__.py', 'sqlglot/expressions/builders.py', 'sqlglot/expressions/core.py', 'sqlglot/expressions/functions.py', 'sqlglot/expressions/query.py', 'sqlglot/expressions/temporal.py'),
    'sqlglot/expressions/ddl.py': ('sqlglot/expressions/__init__.py', 'sqlglot/expressions/builders.py', 'sqlglot/expressions/dml.py', 'sqlglot/expressions/query.py'),
    'sqlglot/expressions/dml.py': ('sqlglot/expressions/__init__.py', 'sqlglot/expressions/builders.py'),
    'sqlglot/expressions/functions.py': ('sqlglot/expressions/__init__.py', 'sqlglot/expressions/builders.py'),
    'sqlglot/expressions/json.py': ('sqlglot/expressions/__init__.py', 'sqlglot/expressions/functions.py'),
    'sqlglot/expressions/math.py': ('sqlglot/expressions/__init__.py', 'sqlglot/expressions/functions.py'),
    'sqlglot/expressions/properties.py': ('sqlglot/expressions/__init__.py', 'sqlglot/expressions/query.py'),
    'sqlglot/expressions/query.py': ('sqlglot/expressions/__init__.py', 'sqlglot/expressions/array.py', 'sqlglot/expressions/builders.py', 'sqlglot/expressions/core.py', 'sqlglot/expressions/ddl.py', 'sqlglot/expressions/dml.py'),
    'sqlglot/expressions/string.py': ('sqlglot/expressions/__init__.py', 'sqlglot/expressions/functions.py'),
    'sqlglot/expressions/temporal.py': ('sqlglot/expressions/__init__.py', 'sqlglot/expressions/core.py', 'sqlglot/expressions/functions.py'),
    'sqlglot/generator.py': ('sqlglot/dialects/dialect.py', 'sqlglot/executor/env.py', 'sqlglot/generators/athena.py', 'sqlglot/generators/bigquery.py', 'sqlglot/generators/clickhouse.py', 'sqlglot/generators/dax.py', 'sqlglot/generators/dremio.py', 'sqlglot/generators/drill.py', 'sqlglot/generators/druid.py', 'sqlglot/generators/duckdb.py', 'sqlglot/generators/exasol.py', 'sqlglot/generators/hive.py', 'sqlglot/generators/materialize.py', 'sqlglot/generators/mysql.py', 'sqlglot/generators/oracle.py', 'sqlglot/generators/postgres.py', 'sqlglot/generators/presto.py', 'sqlglot/generators/prql.py', 'sqlglot/generators/python.py', 'sqlglot/generators/redshift.py', 'sqlglot/generators/risingwave.py', 'sqlglot/generators/singlestore.py', 'sqlglot/generators/snowflake.py', 'sqlglot/generators/solr.py', 'sqlglot/generators/spark.py', 'sqlglot/generators/spark2.py', 'sqlglot/generators/sqlite.py', 'sqlglot/generators/starrocks.py', 'sqlglot/generators/tableau.py', 'sqlglot/generators/teradata.py', 'sqlglot/generators/tsql.py', 'sqlglot/transforms.py'),
    'sqlglot/generators/athena.py': ('sqlglot/dialects/athena.py',),
    'sqlglot/generators/bigquery.py': ('sqlglot/dialects/bigquery.py', 'sqlglot/typing/bigquery.py'),
    'sqlglot/generators/clickhouse.py': ('sqlglot/dialects/clickhouse.py', 'sqlglot/typing/clickhouse.py'),
    'sqlglot/generators/databricks.py': ('sqlglot/dialects/databricks.py', 'sqlglot/typing/databricks.py'),
    'sqlglot/generators/dax.py': ('sqlglot/dialects/dax.py',),
    'sqlglot/generators/doris.py': ('sqlglot/dialects/doris.py',),
    'sqlglot/generators/dremio.py': ('sqlglot/dialects/dremio.py',),
    'sqlglot/generators/drill.py': ('sqlglot/dialects/drill.py',),
    'sqlglot/generators/druid.py': ('sqlglot/dialects/druid.py',),
    'sqlglot/generators/duckdb.py': ('sqlglot/dialects/duckdb.py', 'sqlglot/parsers/duckdb.py'),
    'sqlglot/generators/dune.py': ('sqlglot/dialects/dune.py',),
    'sqlglot/generators/exasol.py': ('sqlglot/dialects/exasol.py',),
    'sqlglot/generators/fabric.py': ('sqlglot/dialects/fabric.py',),
    'sqlglot/generators/hive.py': ('sqlglot/dialects/hive.py', 'sqlglot/generators/athena.py', 'sqlglot/generators/spark2.py'),
    'sqlglot/generators/materialize.py': ('sqlglot/dialects/materialize.py',),
    'sqlglot/generators/mysql.py': ('sqlglot/dialects/mysql.py', 'sqlglot/generators/doris.py', 'sqlglot/generators/drill.py', 'sqlglot/generators/singlestore.py', 'sqlglot/generators/starrocks.py', 'sqlglot/parsers/mysql.py', 'sqlglot/parsers/singlestore.py'),
    'sqlglot/generators/oracle.py': ('sqlglot/dialects/oracle.py',),
    'sqlglot/generators/postgres.py': ('sqlglot/dialects/postgres.py', 'sqlglot/generators/materialize.py', 'sqlglot/generators/redshift.py', 'sqlglot/generators/risingwave.py'),
    'sqlglot/generators/presto.py': ('sqlglot/dialects/presto.py', 'sqlglot/generators/trino.py'),
    'sqlglot/generators/prql.py': ('sqlglot/dialects/prql.py',),
    'sqlglot/generators/python.py': ('sqlglot/executor/python.py',),
    'sqlglot/generators/redshift.py': ('sqlglot/dialects/redshift.py',),
    'sqlglot/generators/risingwave.py': ('sqlglot/dialects/risingwave.py',),
    'sqlglot/generators/singlestore.py': ('sqlglot/dialects/singlestore.py',),
    'sqlglot/generators/snowflake.py': ('sqlglot/dialects/snowflake.py', 'sqlglot/typing/snowflake.py'),
    'sqlglot/generators/solr.py': ('sqlglot/dialects/solr.py',),
    'sqlglot/generators/spark.py': ('sqlglot/dialects/spark.py', 'sqlglot/generators/databricks.py', 'sqlglot/parsers/spark2.py'),
    'sqlglot/generators/spark2.py': ('sqlglot/dialects/spark2.py', 'sqlglot/generators/spark.py'),
    'sqlglot/generators/sqlite.py': ('sqlglot/dialects/sqlite.py',),
    'sqlglot/generators/starrocks.py': ('sqlglot/dialects/starrocks.py',),
    'sqlglot/generators/tableau.py': ('sqlglot/dialects/tableau.py',),
    'sqlglot/generators/teradata.py': ('sqlglot/dialects/teradata.py', 'sqlglot/parsers/presto.py'),
    'sqlglot/generators/trino.py': ('sqlglot/dialects/trino.py', 'sqlglot/generators/athena.py', 'sqlglot/generators/dune.py'),
    'sqlglot/generators/tsql.py': ('sqlglot/dialects/tsql.py', 'sqlglot/generators/fabric.py'),
    'sqlglot/helper.py': ('sqlglot/__main__.py', 'sqlglot/dialects/dialect.py', 'sqlglot/diff.py', 'sqlglot/errors.py', 'sqlglot/executor/__init__.py', 'sqlglot/executor/env.py', 'sqlglot/executor/table.py', 'sqlglot/expressions/__init__.py', 'sqlglot/expressions/array.py', 'sqlglot/expressions/builders.py', 'sqlglot/expressions/core.py', 'sqlglot/expressions/datatypes.py', 'sqlglot/expressions/ddl.py', 'sqlglot/expressions/dml.py', 'sqlglot/expressions/properties.py', 'sqlglot/expressions/query.py', 'sqlglot/generator.py', 'sqlglot/generators/bigquery.py', 'sqlglot/generators/clickhouse.py', 'sqlglot/generators/duckdb.py', 'sqlglot/generators/materialize.py', 'sqlglot/generators/postgres.py', 'sqlglot/generators/presto.py', 'sqlglot/generators/python.py', 'sqlglot/generators/redshift.py', 'sqlglot/generators/snowflake.py', 'sqlglot/generators/spark.py', 'sqlglot/generators/tsql.py', 'sqlglot/optimizer/annotate_types.py', 'sqlglot/optimizer/canonicalize.py', 'sqlglot/optimizer/canonicalize_internal_names.py', 'sqlglot/optimizer/eliminate_subqueries.py', 'sqlglot/optimizer/merge_subqueries.py', 'sqlglot/optimizer/normalize.py', 'sqlglot/optimizer/optimize_joins.py', 'sqlglot/optimizer/pushdown_projections.py', 'sqlglot/optimizer/qualify_tables.py', 'sqlglot/optimizer/resolver.py', 'sqlglot/optimizer/scope.py', 'sqlglot/optimizer/simplify.py', 'sqlglot/optimizer/unnest_subqueries.py', 'sqlglot/parser.py', 'sqlglot/parsers/bigquery.py', 'sqlglot/parsers/clickhouse.py', 'sqlglot/parsers/databricks.py', 'sqlglot/parsers/doris.py', 'sqlglot/parsers/dremio.py', 'sqlglot/parsers/drill.py', 'sqlglot/parsers/duckdb.py', 'sqlglot/parsers/exasol.py', 'sqlglot/parsers/hive.py', 'sqlglot/parsers/materialize.py', 'sqlglot/parsers/mysql.py', 'sqlglot/parsers/oracle.py', 'sqlglot/parsers/postgres.py', 'sqlglot/parsers/presto.py', 'sqlglot/parsers/prql.py', 'sqlglot/parsers/redshift.py', 'sqlglot/parsers/singlestore.py', 'sqlglot/parsers/snowflake.py', 'sqlglot/parsers/spark.py', 'sqlglot/parsers/spark2.py', 'sqlglot/parsers/sqlite.py', 'sqlglot/parsers/starrocks.py', 'sqlglot/parsers/tableau.py', 'sqlglot/parsers/teradata.py', 'sqlglot/parsers/trino.py', 'sqlglot/parsers/tsql.py', 'sqlglot/planner.py', 'sqlglot/schema.py', 'sqlglot/transforms.py', 'sqlglot/typing/__init__.py', 'sqlglot/typing/snowflake.py', 'sqlglot/typing/spark2.py'),
    'sqlglot/jsonpath.py': ('sqlglot/dialects/bigquery.py', 'sqlglot/dialects/dialect.py', 'sqlglot/dialects/hive.py', 'sqlglot/dialects/snowflake.py', 'sqlglot/generator.py'),
    'sqlglot/optimizer/annotate_types.py': ('sqlglot/dialects/bigquery.py', 'sqlglot/dialects/databricks.py', 'sqlglot/dialects/dialect.py', 'sqlglot/dialects/hive.py', 'sqlglot/executor/__init__.py', 'sqlglot/expressions/builders.py', 'sqlglot/generator.py', 'sqlglot/generators/bigquery.py', 'sqlglot/generators/duckdb.py', 'sqlglot/generators/postgres.py', 'sqlglot/generators/presto.py', 'sqlglot/generators/snowflake.py', 'sqlglot/optimizer/canonicalize.py', 'sqlglot/optimizer/optimizer.py', 'sqlglot/optimizer/qualify_columns.py', 'sqlglot/optimizer/simplify.py', 'sqlglot/parsers/bigquery.py', 'sqlglot/parsers/postgres.py', 'sqlglot/typing/__init__.py', 'sqlglot/typing/bigquery.py', 'sqlglot/typing/duckdb.py', 'sqlglot/typing/hive.py', 'sqlglot/typing/mysql.py', 'sqlglot/typing/postgres.py', 'sqlglot/typing/redshift.py', 'sqlglot/typing/snowflake.py', 'sqlglot/typing/spark2.py', 'sqlglot/typing/tsql.py'),
    'sqlglot/optimizer/canonicalize.py': ('sqlglot/optimizer/optimizer.py', 'sqlglot/transforms.py'),
    'sqlglot/optimizer/eliminate_ctes.py': ('sqlglot/optimizer/optimizer.py',),
    'sqlglot/optimizer/eliminate_joins.py': ('sqlglot/optimizer/optimizer.py', 'sqlglot/planner.py'),
    'sqlglot/optimizer/eliminate_subqueries.py': ('sqlglot/optimizer/optimizer.py',),
    'sqlglot/optimizer/helpers.py': ('sqlglot/optimizer/merge_subqueries.py', 'sqlglot/optimizer/pushdown_predicates.py', 'sqlglot/optimizer/pushdown_projections.py', 'sqlglot/optimizer/unnest_subqueries.py'),
    'sqlglot/optimizer/isolate_table_selects.py': ('sqlglot/optimizer/qualify.py',),
    'sqlglot/optimizer/journal.py': ('sqlglot/optimizer/eliminate_ctes.py', 'sqlglot/optimizer/pushdown_projections.py'),
    'sqlglot/optimizer/merge_subqueries.py': ('sqlglot/optimizer/optimizer.py',),
    'sqlglot/optimizer/normalize.py': ('sqlglot/optimizer/eliminate_joins.py', 'sqlglot/optimizer/optimizer.py', 'sqlglot/optimizer/pushdown_predicates.py', 'sqlglot/transforms.py'),
    'sqlglot/optimizer/normalize_identifiers.py': ('sqlglot/expressions/builders.py', 'sqlglot/lineage.py', 'sqlglot/optimizer/qualify.py', 'sqlglot/optimizer/qualify_tables.py', 'sqlglot/parser.py'),
    'sqlglot/optimizer/optimize_joins.py': ('sqlglot/optimizer/optimizer.py',),
    'sqlglot/optimizer/optimizer.py': ('sqlglot/executor/__init__.py',),
    'sqlglot/optimizer/pushdown_predicates.py': ('sqlglot/optimizer/optimizer.py',),
    'sqlglot/optimizer/pushdown_projections.py': ('sqlglot/optimizer/optimizer.py',),
    'sqlglot/optimizer/qualify.py': ('sqlglot/lineage.py', 'sqlglot/optimizer/optimizer.py'),
    'sqlglot/optimizer/qualify_columns.py': ('sqlglot/generators/tsql.py', 'sqlglot/optimizer/optimizer.py', 'sqlglot/optimizer/qualify.py'),
    'sqlglot/optimizer/qualify_tables.py': ('sqlglot/optimizer/qualify.py',),
    'sqlglot/optimizer/resolver.py': ('sqlglot/optimizer/qualify_columns.py',),
    'sqlglot/optimizer/scope.py': ('sqlglot/dialects/dialect.py', 'sqlglot/executor/python.py', 'sqlglot/expressions/builders.py', 'sqlglot/generator.py', 'sqlglot/generators/duckdb.py', 'sqlglot/generators/exasol.py', 'sqlglot/generators/fabric.py', 'sqlglot/generators/presto.py', 'sqlglot/generators/snowflake.py', 'sqlglot/generators/tsql.py', 'sqlglot/lineage.py', 'sqlglot/optimizer/annotate_types.py', 'sqlglot/optimizer/canonicalize_internal_names.py', 'sqlglot/optimizer/eliminate_ctes.py', 'sqlglot/optimizer/eliminate_joins.py', 'sqlglot/optimizer/eliminate_subqueries.py', 'sqlglot/optimizer/helpers.py', 'sqlglot/optimizer/isolate_table_selects.py', 'sqlglot/optimizer/merge_subqueries.py', 'sqlglot/optimizer/normalize.py', 'sqlglot/optimizer/pushdown_predicates.py', 'sqlglot/optimizer/pushdown_projections.py', 'sqlglot/optimizer/qualify_columns.py', 'sqlglot/optimizer/qualify_tables.py', 'sqlglot/optimizer/resolver.py', 'sqlglot/optimizer/simplify.py', 'sqlglot/optimizer/unnest_subqueries.py', 'sqlglot/parser.py', 'sqlglot/planner.py', 'sqlglot/transforms.py'),
    'sqlglot/optimizer/simplify.py': ('sqlglot/expressions/builders.py', 'sqlglot/generator.py', 'sqlglot/generators/clickhouse.py', 'sqlglot/optimizer/normalize.py', 'sqlglot/optimizer/optimizer.py', 'sqlglot/optimizer/pushdown_predicates.py', 'sqlglot/optimizer/qualify_columns.py'),
    'sqlglot/optimizer/unnest_subqueries.py': ('sqlglot/optimizer/optimizer.py',),
    'sqlglot/parser.py': ('sqlglot/dialects/dialect.py', 'sqlglot/optimizer/simplify.py', 'sqlglot/parsers/base.py', 'sqlglot/parsers/bigquery.py', 'sqlglot/parsers/clickhouse.py', 'sqlglot/parsers/databricks.py', 'sqlglot/parsers/dax.py', 'sqlglot/parsers/dremio.py', 'sqlglot/parsers/drill.py', 'sqlglot/parsers/druid.py', 'sqlglot/parsers/duckdb.py', 'sqlglot/parsers/exasol.py', 'sqlglot/parsers/hive.py', 'sqlglot/parsers/mysql.py', 'sqlglot/parsers/oracle.py', 'sqlglot/parsers/postgres.py', 'sqlglot/parsers/presto.py', 'sqlglot/parsers/prql.py', 'sqlglot/parsers/redshift.py', 'sqlglot/parsers/singlestore.py', 'sqlglot/parsers/snowflake.py', 'sqlglot/parsers/solr.py', 'sqlglot/parsers/spark2.py', 'sqlglot/parsers/sqlite.py', 'sqlglot/parsers/starrocks.py', 'sqlglot/parsers/tableau.py', 'sqlglot/parsers/teradata.py', 'sqlglot/parsers/trino.py', 'sqlglot/parsers/tsql.py'),
    'sqlglot/parsers/athena.py': ('sqlglot/dialects/athena.py',),
    'sqlglot/parsers/base.py': ('sqlglot/dialects/dialect.py',),
    'sqlglot/parsers/bigquery.py': ('sqlglot/dialects/bigquery.py', 'sqlglot/typing/bigquery.py'),
    'sqlglot/parsers/clickhouse.py': ('sqlglot/dialects/clickhouse.py', 'sqlglot/typing/clickhouse.py'),
    'sqlglot/parsers/databricks.py': ('sqlglot/dialects/databricks.py', 'sqlglot/typing/databricks.py'),
    'sqlglot/parsers/dax.py': ('sqlglot/dialects/dax.py',),
    'sqlglot/parsers/doris.py': ('sqlglot/dialects/doris.py', 'sqlglot/generators/doris.py'),
    'sqlglot/parsers/dremio.py': ('sqlglot/dialects/dremio.py',),
    'sqlglot/parsers/drill.py': ('sqlglot/dialects/drill.py',),
    'sqlglot/parsers/druid.py': ('sqlglot/dialects/druid.py',),
    'sqlglot/parsers/duckdb.py': ('sqlglot/dialects/duckdb.py', 'sqlglot/generators/duckdb.py'),
    'sqlglot/parsers/dune.py': ('sqlglot/dialects/dune.py',),
    'sqlglot/parsers/exasol.py': ('sqlglot/dialects/exasol.py', 'sqlglot/generators/exasol.py'),
    'sqlglot/parsers/fabric.py': ('sqlglot/dialects/fabric.py', 'sqlglot/generators/fabric.py'),
    'sqlglot/parsers/hive.py': ('sqlglot/dialects/hive.py', 'sqlglot/generators/hive.py', 'sqlglot/parsers/spark.py', 'sqlglot/parsers/spark2.py'),
    'sqlglot/parsers/materialize.py': ('sqlglot/dialects/materialize.py',),
    'sqlglot/parsers/mysql.py': ('sqlglot/dialects/mysql.py', 'sqlglot/parsers/doris.py', 'sqlglot/parsers/singlestore.py', 'sqlglot/parsers/starrocks.py'),
    'sqlglot/parsers/oracle.py': ('sqlglot/dialects/oracle.py',),
    'sqlglot/parsers/postgres.py': ('sqlglot/dialects/postgres.py', 'sqlglot/parsers/materialize.py', 'sqlglot/parsers/redshift.py', 'sqlglot/parsers/risingwave.py'),
    'sqlglot/parsers/presto.py': ('sqlglot/dialects/presto.py', 'sqlglot/parsers/trino.py'),
    'sqlglot/parsers/prql.py': ('sqlglot/dialects/prql.py',),
    'sqlglot/parsers/redshift.py': ('sqlglot/dialects/redshift.py',),
    'sqlglot/parsers/risingwave.py': ('sqlglot/dialects/risingwave.py',),
    'sqlglot/parsers/singlestore.py': ('sqlglot/dialects/singlestore.py',),
    'sqlglot/parsers/snowflake.py': ('sqlglot/dialects/snowflake.py', 'sqlglot/generators/snowflake.py', 'sqlglot/typing/snowflake.py'),
    'sqlglot/parsers/solr.py': ('sqlglot/dialects/solr.py',),
    'sqlglot/parsers/spark.py': ('sqlglot/dialects/spark.py', 'sqlglot/parsers/databricks.py', 'sqlglot/parsers/spark2.py'),
    'sqlglot/parsers/spark2.py': ('sqlglot/dialects/spark2.py', 'sqlglot/parsers/spark.py'),
    'sqlglot/parsers/sqlite.py': ('sqlglot/dialects/sqlite.py',),
    'sqlglot/parsers/starrocks.py': ('sqlglot/dialects/starrocks.py',),
    'sqlglot/parsers/tableau.py': ('sqlglot/dialects/tableau.py',),
    'sqlglot/parsers/teradata.py': ('sqlglot/dialects/teradata.py', 'sqlglot/generators/teradata.py', 'sqlglot/parsers/presto.py'),
    'sqlglot/parsers/trino.py': ('sqlglot/dialects/trino.py', 'sqlglot/parsers/athena.py', 'sqlglot/parsers/dune.py'),
    'sqlglot/parsers/tsql.py': ('sqlglot/dialects/tsql.py', 'sqlglot/generators/tsql.py', 'sqlglot/parsers/fabric.py'),
    'sqlglot/planner.py': ('sqlglot/executor/__init__.py', 'sqlglot/executor/python.py'),
    'sqlglot/schema.py': ('sqlglot/executor/__init__.py', 'sqlglot/executor/table.py', 'sqlglot/lineage.py', 'sqlglot/optimizer/annotate_types.py', 'sqlglot/optimizer/isolate_table_selects.py', 'sqlglot/optimizer/optimizer.py', 'sqlglot/optimizer/qualify.py', 'sqlglot/optimizer/qualify_columns.py', 'sqlglot/optimizer/resolver.py', 'sqlglot/optimizer/simplify.py'),
    'sqlglot/serde.py': ('sqlglot/expressions/core.py',),
    'sqlglot/time.py': ('sqlglot/dialects/dialect.py', 'sqlglot/generator.py', 'sqlglot/generators/hive.py', 'sqlglot/generators/tsql.py', 'sqlglot/parser.py', 'sqlglot/parsers/presto.py', 'sqlglot/parsers/tsql.py'),
    'sqlglot/tokenizer_core.py': ('sqlglot/expressions/core.py', 'sqlglot/tokens.py'),
    'sqlglot/tokens.py': ('sqlglot/anonymize.py', 'sqlglot/dialects/athena.py', 'sqlglot/dialects/bigquery.py', 'sqlglot/dialects/clickhouse.py', 'sqlglot/dialects/databricks.py', 'sqlglot/dialects/dax.py', 'sqlglot/dialects/dialect.py', 'sqlglot/dialects/dremio.py', 'sqlglot/dialects/drill.py', 'sqlglot/dialects/duckdb.py', 'sqlglot/dialects/exasol.py', 'sqlglot/dialects/fabric.py', 'sqlglot/dialects/hive.py', 'sqlglot/dialects/mysql.py', 'sqlglot/dialects/oracle.py', 'sqlglot/dialects/postgres.py', 'sqlglot/dialects/presto.py', 'sqlglot/dialects/prql.py', 'sqlglot/dialects/redshift.py', 'sqlglot/dialects/risingwave.py', 'sqlglot/dialects/singlestore.py', 'sqlglot/dialects/snowflake.py', 'sqlglot/dialects/solr.py', 'sqlglot/dialects/spark.py', 'sqlglot/dialects/spark2.py', 'sqlglot/dialects/sqlite.py', 'sqlglot/dialects/starrocks.py', 'sqlglot/dialects/tableau.py', 'sqlglot/dialects/teradata.py', 'sqlglot/dialects/trino.py', 'sqlglot/dialects/tsql.py', 'sqlglot/executor/python.py', 'sqlglot/generator.py', 'sqlglot/generators/snowflake.py', 'sqlglot/generators/sqlite.py', 'sqlglot/jsonpath.py', 'sqlglot/parser.py', 'sqlglot/parsers/athena.py', 'sqlglot/parsers/base.py', 'sqlglot/parsers/bigquery.py', 'sqlglot/parsers/clickhouse.py', 'sqlglot/parsers/databricks.py', 'sqlglot/parsers/dax.py', 'sqlglot/parsers/doris.py', 'sqlglot/parsers/dremio.py', 'sqlglot/parsers/drill.py', 'sqlglot/parsers/duckdb.py', 'sqlglot/parsers/exasol.py', 'sqlglot/parsers/hive.py', 'sqlglot/parsers/materialize.py', 'sqlglot/parsers/mysql.py', 'sqlglot/parsers/oracle.py', 'sqlglot/parsers/postgres.py', 'sqlglot/parsers/presto.py', 'sqlglot/parsers/prql.py', 'sqlglot/parsers/redshift.py', 'sqlglot/parsers/risingwave.py', 'sqlglot/parsers/singlestore.py', 'sqlglot/parsers/snowflake.py', 'sqlglot/parsers/solr.py', 'sqlglot/parsers/spark.py', 'sqlglot/parsers/spark2.py', 'sqlglot/parsers/sqlite.py', 'sqlglot/parsers/starrocks.py', 'sqlglot/parsers/teradata.py', 'sqlglot/parsers/trino.py', 'sqlglot/parsers/tsql.py'),
    'sqlglot/transforms.py': ('sqlglot/generator.py', 'sqlglot/generators/bigquery.py', 'sqlglot/generators/databricks.py', 'sqlglot/generators/drill.py', 'sqlglot/generators/duckdb.py', 'sqlglot/generators/fabric.py', 'sqlglot/generators/hive.py', 'sqlglot/generators/materialize.py', 'sqlglot/generators/mysql.py', 'sqlglot/generators/oracle.py', 'sqlglot/generators/postgres.py', 'sqlglot/generators/presto.py', 'sqlglot/generators/redshift.py', 'sqlglot/generators/snowflake.py', 'sqlglot/generators/spark.py', 'sqlglot/generators/spark2.py', 'sqlglot/generators/sqlite.py', 'sqlglot/generators/starrocks.py', 'sqlglot/generators/tableau.py', 'sqlglot/generators/teradata.py', 'sqlglot/generators/trino.py', 'sqlglot/generators/tsql.py'),
    'sqlglot/trie.py': ('sqlglot/dialects/dialect.py', 'sqlglot/dialects/presto.py', 'sqlglot/parser.py', 'sqlglot/parsers/duckdb.py', 'sqlglot/parsers/mysql.py', 'sqlglot/parsers/singlestore.py', 'sqlglot/parsers/snowflake.py', 'sqlglot/parsers/spark.py', 'sqlglot/parsers/teradata.py', 'sqlglot/schema.py', 'sqlglot/time.py', 'sqlglot/tokens.py'),
    'sqlglot/typing/__init__.py': ('sqlglot/dialects/dialect.py', 'sqlglot/optimizer/annotate_types.py', 'sqlglot/typing/bigquery.py', 'sqlglot/typing/clickhouse.py', 'sqlglot/typing/duckdb.py', 'sqlglot/typing/hive.py', 'sqlglot/typing/mysql.py', 'sqlglot/typing/postgres.py', 'sqlglot/typing/presto.py', 'sqlglot/typing/snowflake.py', 'sqlglot/typing/spark2.py', 'sqlglot/typing/sqlite.py', 'sqlglot/typing/tsql.py'),
    'sqlglot/typing/bigquery.py': ('sqlglot/dialects/bigquery.py',),
    'sqlglot/typing/clickhouse.py': ('sqlglot/dialects/clickhouse.py',),
    'sqlglot/typing/databricks.py': ('sqlglot/dialects/databricks.py',),
    'sqlglot/typing/duckdb.py': ('sqlglot/dialects/duckdb.py', 'sqlglot/generators/duckdb.py', 'sqlglot/parsers/duckdb.py'),
    'sqlglot/typing/hive.py': ('sqlglot/dialects/hive.py', 'sqlglot/generators/hive.py', 'sqlglot/typing/spark2.py'),
    'sqlglot/typing/mysql.py': ('sqlglot/dialects/mysql.py', 'sqlglot/parsers/mysql.py', 'sqlglot/parsers/singlestore.py'),
    'sqlglot/typing/postgres.py': ('sqlglot/dialects/postgres.py', 'sqlglot/typing/redshift.py'),
    'sqlglot/typing/presto.py': ('sqlglot/dialects/presto.py',),
    'sqlglot/typing/redshift.py': ('sqlglot/dialects/redshift.py',),
    'sqlglot/typing/snowflake.py': ('sqlglot/dialects/snowflake.py',),
    'sqlglot/typing/spark.py': ('sqlglot/dialects/spark.py', 'sqlglot/parsers/spark2.py', 'sqlglot/typing/databricks.py'),
    'sqlglot/typing/spark2.py': ('sqlglot/dialects/spark2.py', 'sqlglot/typing/spark.py'),
    'sqlglot/typing/sqlite.py': ('sqlglot/dialects/sqlite.py',),
    'sqlglot/typing/tsql.py': ('sqlglot/dialects/tsql.py',),
}
LAZY_SOURCE_IMPORTERS: dict[str, tuple[str, ...]] = {
    'sqlglot/dialects/athena.py': ('sqlglot/optimizer/pushdown_predicates.py',),
    'sqlglot/dialects/bigquery.py': ('sqlglot/typing/bigquery.py',),
    'sqlglot/dialects/clickhouse.py': ('sqlglot/typing/clickhouse.py',),
    'sqlglot/dialects/databricks.py': ('sqlglot/typing/databricks.py',),
    'sqlglot/dialects/doris.py': ('sqlglot/generators/doris.py',),
    'sqlglot/dialects/drill.py': ('sqlglot/generators/drill.py',),
    'sqlglot/dialects/duckdb.py': ('sqlglot/generators/duckdb.py', 'sqlglot/parsers/duckdb.py'),
    'sqlglot/dialects/fabric.py': ('sqlglot/generators/fabric.py',),
    'sqlglot/dialects/hive.py': ('sqlglot/generators/athena.py', 'sqlglot/generators/hive.py'),
    'sqlglot/dialects/mysql.py': ('sqlglot/parsers/mysql.py',),
    'sqlglot/dialects/presto.py': ('sqlglot/generators/presto.py', 'sqlglot/optimizer/pushdown_predicates.py', 'sqlglot/parsers/presto.py'),
    'sqlglot/dialects/snowflake.py': ('sqlglot/typing/snowflake.py',),
    'sqlglot/dialects/spark.py': ('sqlglot/parsers/spark2.py',),
    'sqlglot/dialects/teradata.py': ('sqlglot/parsers/presto.py',),
    'sqlglot/dialects/trino.py': ('sqlglot/generators/athena.py',),
    'sqlglot/dialects/tsql.py': ('sqlglot/generator.py', 'sqlglot/generators/tsql.py', 'sqlglot/parsers/tsql.py'),
    'sqlglot/expressions/__init__.py': ('sqlglot/expressions/builders.py',),
    'sqlglot/expressions/array.py': ('sqlglot/expressions/core.py',),
    'sqlglot/expressions/properties.py': ('sqlglot/expressions/query.py',),
    'sqlglot/expressions/temporal.py': ('sqlglot/expressions/core.py',),
    'sqlglot/generator.py': ('sqlglot/generators/spark2.py',),
    'sqlglot/generators/bigquery.py': ('sqlglot/typing/bigquery.py',),
    'sqlglot/generators/clickhouse.py': ('sqlglot/typing/clickhouse.py',),
    'sqlglot/generators/databricks.py': ('sqlglot/typing/databricks.py',),
    'sqlglot/generators/duckdb.py': ('sqlglot/parsers/duckdb.py',),
    'sqlglot/generators/mysql.py': ('sqlglot/parsers/mysql.py',),
    'sqlglot/generators/snowflake.py': ('sqlglot/typing/snowflake.py',),
    'sqlglot/generators/spark.py': ('sqlglot/parsers/spark2.py',),
    'sqlglot/generators/teradata.py': ('sqlglot/parsers/presto.py',),
    'sqlglot/optimizer/annotate_types.py': ('sqlglot/dialects/dialect.py', 'sqlglot/expressions/builders.py', 'sqlglot/generator.py', 'sqlglot/generators/bigquery.py', 'sqlglot/generators/duckdb.py', 'sqlglot/generators/postgres.py', 'sqlglot/generators/presto.py', 'sqlglot/generators/snowflake.py', 'sqlglot/parsers/bigquery.py', 'sqlglot/parsers/postgres.py'),
    'sqlglot/optimizer/canonicalize.py': ('sqlglot/transforms.py',),
    'sqlglot/optimizer/normalize.py': ('sqlglot/transforms.py',),
    'sqlglot/optimizer/normalize_identifiers.py': ('sqlglot/expressions/builders.py', 'sqlglot/parser.py'),
    'sqlglot/optimizer/qualify_columns.py': ('sqlglot/generators/tsql.py',),
    'sqlglot/optimizer/scope.py': ('sqlglot/expressions/builders.py', 'sqlglot/generator.py', 'sqlglot/transforms.py'),
    'sqlglot/optimizer/simplify.py': ('sqlglot/expressions/builders.py', 'sqlglot/generator.py'),
    'sqlglot/parsers/bigquery.py': ('sqlglot/typing/bigquery.py',),
    'sqlglot/parsers/clickhouse.py': ('sqlglot/typing/clickhouse.py',),
    'sqlglot/parsers/databricks.py': ('sqlglot/typing/databricks.py',),
    'sqlglot/parsers/doris.py': ('sqlglot/generators/doris.py',),
    'sqlglot/parsers/duckdb.py': ('sqlglot/generators/duckdb.py',),
    'sqlglot/parsers/fabric.py': ('sqlglot/generators/fabric.py',),
    'sqlglot/parsers/hive.py': ('sqlglot/generators/hive.py',),
    'sqlglot/parsers/snowflake.py': ('sqlglot/typing/snowflake.py',),
    'sqlglot/parsers/spark.py': ('sqlglot/parsers/spark2.py',),
    'sqlglot/parsers/teradata.py': ('sqlglot/parsers/presto.py',),
    'sqlglot/serde.py': ('sqlglot/expressions/core.py',),
    'sqlglot/transforms.py': ('sqlglot/generator.py',),
    'sqlglot/typing/duckdb.py': ('sqlglot/generators/duckdb.py', 'sqlglot/parsers/duckdb.py'),
    'sqlglot/typing/hive.py': ('sqlglot/generators/hive.py',),
    'sqlglot/typing/mysql.py': ('sqlglot/parsers/mysql.py',),
    'sqlglot/typing/spark.py': ('sqlglot/parsers/spark2.py',),
}
DERIVED_CORE_MODULES: tuple[str, ...] = (
    'sqlglot/_typing.py',
    'sqlglot/dialects/dialect.py',
    'sqlglot/dialects/tsql.py',
    'sqlglot/diff.py',
    'sqlglot/errors.py',
    'sqlglot/expressions/__init__.py',
    'sqlglot/expressions/aggregate.py',
    'sqlglot/expressions/array.py',
    'sqlglot/expressions/builders.py',
    'sqlglot/expressions/constraints.py',
    'sqlglot/expressions/core.py',
    'sqlglot/expressions/datatypes.py',
    'sqlglot/expressions/ddl.py',
    'sqlglot/expressions/dml.py',
    'sqlglot/expressions/functions.py',
    'sqlglot/expressions/json.py',
    'sqlglot/expressions/math.py',
    'sqlglot/expressions/properties.py',
    'sqlglot/expressions/query.py',
    'sqlglot/expressions/string.py',
    'sqlglot/expressions/temporal.py',
    'sqlglot/generator.py',
    'sqlglot/generators/tsql.py',
    'sqlglot/helper.py',
    'sqlglot/jsonpath.py',
    'sqlglot/optimizer/annotate_types.py',
    'sqlglot/optimizer/canonicalize.py',
    'sqlglot/optimizer/normalize.py',
    'sqlglot/optimizer/normalize_identifiers.py',
    'sqlglot/optimizer/qualify_columns.py',
    'sqlglot/optimizer/resolver.py',
    'sqlglot/optimizer/scope.py',
    'sqlglot/optimizer/simplify.py',
    'sqlglot/parser.py',
    'sqlglot/parsers/base.py',
    'sqlglot/parsers/tsql.py',
    'sqlglot/schema.py',
    'sqlglot/serde.py',
    'sqlglot/time.py',
    'sqlglot/tokenizer_core.py',
    'sqlglot/tokens.py',
    'sqlglot/transforms.py',
    'sqlglot/trie.py',
    'sqlglot/typing/__init__.py',
    'sqlglot/typing/tsql.py',
)
SHARED_TEST_IMPORTERS: dict[str, tuple[str, ...]] = {
    'tests/dialects/test_dialect.py': ('tests/dialects/test_athena.py', 'tests/dialects/test_bigquery.py', 'tests/dialects/test_clickhouse.py', 'tests/dialects/test_databricks.py', 'tests/dialects/test_dax.py', 'tests/dialects/test_doris.py', 'tests/dialects/test_dremio.py', 'tests/dialects/test_drill.py', 'tests/dialects/test_druid.py', 'tests/dialects/test_duckdb.py', 'tests/dialects/test_dune.py', 'tests/dialects/test_exasol.py', 'tests/dialects/test_fabric.py', 'tests/dialects/test_hive.py', 'tests/dialects/test_materialize.py', 'tests/dialects/test_mysql.py', 'tests/dialects/test_oracle.py', 'tests/dialects/test_pipe_syntax.py', 'tests/dialects/test_postgres.py', 'tests/dialects/test_presto.py', 'tests/dialects/test_prql.py', 'tests/dialects/test_redshift.py', 'tests/dialects/test_risingwave.py', 'tests/dialects/test_singlestore.py', 'tests/dialects/test_snowflake.py', 'tests/dialects/test_solr.py', 'tests/dialects/test_spark.py', 'tests/dialects/test_sqlite.py', 'tests/dialects/test_starrocks.py', 'tests/dialects/test_tableau.py', 'tests/dialects/test_teradata.py', 'tests/dialects/test_trino.py', 'tests/dialects/test_tsql.py'),
    'tests/helpers.py': ('tests/dialects/test_dialect.py', 'tests/test_executor.py', 'tests/test_generator.py', 'tests/test_jsonpath.py', 'tests/test_optimizer.py', 'tests/test_parser.py', 'tests/test_serde.py', 'tests/test_transpile.py'),
}
KNOWN_SOURCE_MODULES: tuple[str, ...] = (
    'sqlglot/__init__.py',
    'sqlglot/__main__.py',
    'sqlglot/_typing.py',
    'sqlglot/anonymize.py',
    'sqlglot/dialects/__init__.py',
    'sqlglot/dialects/athena.py',
    'sqlglot/dialects/bigquery.py',
    'sqlglot/dialects/clickhouse.py',
    'sqlglot/dialects/databricks.py',
    'sqlglot/dialects/dax.py',
    'sqlglot/dialects/dialect.py',
    'sqlglot/dialects/doris.py',
    'sqlglot/dialects/dremio.py',
    'sqlglot/dialects/drill.py',
    'sqlglot/dialects/druid.py',
    'sqlglot/dialects/duckdb.py',
    'sqlglot/dialects/dune.py',
    'sqlglot/dialects/exasol.py',
    'sqlglot/dialects/fabric.py',
    'sqlglot/dialects/hive.py',
    'sqlglot/dialects/materialize.py',
    'sqlglot/dialects/mysql.py',
    'sqlglot/dialects/oracle.py',
    'sqlglot/dialects/postgres.py',
    'sqlglot/dialects/presto.py',
    'sqlglot/dialects/prql.py',
    'sqlglot/dialects/redshift.py',
    'sqlglot/dialects/risingwave.py',
    'sqlglot/dialects/singlestore.py',
    'sqlglot/dialects/snowflake.py',
    'sqlglot/dialects/solr.py',
    'sqlglot/dialects/spark.py',
    'sqlglot/dialects/spark2.py',
    'sqlglot/dialects/sqlite.py',
    'sqlglot/dialects/starrocks.py',
    'sqlglot/dialects/tableau.py',
    'sqlglot/dialects/teradata.py',
    'sqlglot/dialects/trino.py',
    'sqlglot/dialects/tsql.py',
    'sqlglot/diff.py',
    'sqlglot/errors.py',
    'sqlglot/executor/__init__.py',
    'sqlglot/executor/context.py',
    'sqlglot/executor/env.py',
    'sqlglot/executor/python.py',
    'sqlglot/executor/table.py',
    'sqlglot/expressions/__init__.py',
    'sqlglot/expressions/aggregate.py',
    'sqlglot/expressions/array.py',
    'sqlglot/expressions/builders.py',
    'sqlglot/expressions/constraints.py',
    'sqlglot/expressions/core.py',
    'sqlglot/expressions/datatypes.py',
    'sqlglot/expressions/ddl.py',
    'sqlglot/expressions/dml.py',
    'sqlglot/expressions/functions.py',
    'sqlglot/expressions/json.py',
    'sqlglot/expressions/math.py',
    'sqlglot/expressions/properties.py',
    'sqlglot/expressions/query.py',
    'sqlglot/expressions/string.py',
    'sqlglot/expressions/temporal.py',
    'sqlglot/generator.py',
    'sqlglot/generators/__init__.py',
    'sqlglot/generators/athena.py',
    'sqlglot/generators/bigquery.py',
    'sqlglot/generators/clickhouse.py',
    'sqlglot/generators/databricks.py',
    'sqlglot/generators/dax.py',
    'sqlglot/generators/doris.py',
    'sqlglot/generators/dremio.py',
    'sqlglot/generators/drill.py',
    'sqlglot/generators/druid.py',
    'sqlglot/generators/duckdb.py',
    'sqlglot/generators/dune.py',
    'sqlglot/generators/exasol.py',
    'sqlglot/generators/fabric.py',
    'sqlglot/generators/hive.py',
    'sqlglot/generators/materialize.py',
    'sqlglot/generators/mysql.py',
    'sqlglot/generators/oracle.py',
    'sqlglot/generators/postgres.py',
    'sqlglot/generators/presto.py',
    'sqlglot/generators/prql.py',
    'sqlglot/generators/python.py',
    'sqlglot/generators/redshift.py',
    'sqlglot/generators/risingwave.py',
    'sqlglot/generators/singlestore.py',
    'sqlglot/generators/snowflake.py',
    'sqlglot/generators/solr.py',
    'sqlglot/generators/spark.py',
    'sqlglot/generators/spark2.py',
    'sqlglot/generators/sqlite.py',
    'sqlglot/generators/starrocks.py',
    'sqlglot/generators/tableau.py',
    'sqlglot/generators/teradata.py',
    'sqlglot/generators/trino.py',
    'sqlglot/generators/tsql.py',
    'sqlglot/helper.py',
    'sqlglot/jsonpath.py',
    'sqlglot/lineage.py',
    'sqlglot/optimizer/__init__.py',
    'sqlglot/optimizer/annotate_types.py',
    'sqlglot/optimizer/canonicalize.py',
    'sqlglot/optimizer/canonicalize_internal_names.py',
    'sqlglot/optimizer/eliminate_ctes.py',
    'sqlglot/optimizer/eliminate_joins.py',
    'sqlglot/optimizer/eliminate_subqueries.py',
    'sqlglot/optimizer/helpers.py',
    'sqlglot/optimizer/isolate_table_selects.py',
    'sqlglot/optimizer/journal.py',
    'sqlglot/optimizer/merge_subqueries.py',
    'sqlglot/optimizer/normalize.py',
    'sqlglot/optimizer/normalize_identifiers.py',
    'sqlglot/optimizer/optimize_joins.py',
    'sqlglot/optimizer/optimizer.py',
    'sqlglot/optimizer/pushdown_predicates.py',
    'sqlglot/optimizer/pushdown_projections.py',
    'sqlglot/optimizer/qualify.py',
    'sqlglot/optimizer/qualify_columns.py',
    'sqlglot/optimizer/qualify_tables.py',
    'sqlglot/optimizer/resolver.py',
    'sqlglot/optimizer/scope.py',
    'sqlglot/optimizer/simplify.py',
    'sqlglot/optimizer/unnest_subqueries.py',
    'sqlglot/parser.py',
    'sqlglot/parsers/__init__.py',
    'sqlglot/parsers/athena.py',
    'sqlglot/parsers/base.py',
    'sqlglot/parsers/bigquery.py',
    'sqlglot/parsers/clickhouse.py',
    'sqlglot/parsers/databricks.py',
    'sqlglot/parsers/dax.py',
    'sqlglot/parsers/doris.py',
    'sqlglot/parsers/dremio.py',
    'sqlglot/parsers/drill.py',
    'sqlglot/parsers/druid.py',
    'sqlglot/parsers/duckdb.py',
    'sqlglot/parsers/dune.py',
    'sqlglot/parsers/exasol.py',
    'sqlglot/parsers/fabric.py',
    'sqlglot/parsers/hive.py',
    'sqlglot/parsers/materialize.py',
    'sqlglot/parsers/mysql.py',
    'sqlglot/parsers/oracle.py',
    'sqlglot/parsers/postgres.py',
    'sqlglot/parsers/presto.py',
    'sqlglot/parsers/prql.py',
    'sqlglot/parsers/redshift.py',
    'sqlglot/parsers/risingwave.py',
    'sqlglot/parsers/singlestore.py',
    'sqlglot/parsers/snowflake.py',
    'sqlglot/parsers/solr.py',
    'sqlglot/parsers/spark.py',
    'sqlglot/parsers/spark2.py',
    'sqlglot/parsers/sqlite.py',
    'sqlglot/parsers/starrocks.py',
    'sqlglot/parsers/tableau.py',
    'sqlglot/parsers/teradata.py',
    'sqlglot/parsers/trino.py',
    'sqlglot/parsers/tsql.py',
    'sqlglot/planner.py',
    'sqlglot/schema.py',
    'sqlglot/serde.py',
    'sqlglot/time.py',
    'sqlglot/tokenizer_core.py',
    'sqlglot/tokens.py',
    'sqlglot/transforms.py',
    'sqlglot/trie.py',
    'sqlglot/typing/__init__.py',
    'sqlglot/typing/bigquery.py',
    'sqlglot/typing/clickhouse.py',
    'sqlglot/typing/databricks.py',
    'sqlglot/typing/duckdb.py',
    'sqlglot/typing/hive.py',
    'sqlglot/typing/mysql.py',
    'sqlglot/typing/postgres.py',
    'sqlglot/typing/presto.py',
    'sqlglot/typing/redshift.py',
    'sqlglot/typing/snowflake.py',
    'sqlglot/typing/spark.py',
    'sqlglot/typing/spark2.py',
    'sqlglot/typing/sqlite.py',
    'sqlglot/typing/tsql.py',
)
# END GENERATED


class Selection(NamedTuple):
    numbers: set[int]
    fell_back: bool
    reasons: list[str]


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

_SAFE_PATH = re.compile(r"^[A-Za-z0-9_.\-/+@ ]+$")
_KNOWN_ROOTS = ("sqlglot/", "tests/", "docs/", "posts/", ".github/", "sqlglotrs/", "sqlglotc/")


def normalize_path(path: object) -> str | None:
    """Return a repo-relative posix path, or None when it is unusable/unsafe."""
    if not isinstance(path, str):
        return None
    p = path.strip().replace("\\", "/")
    if p in ("", "/dev/null", "dev/null"):
        return None
    while p.startswith("./"):
        p = p[2:]
    if p.startswith(("a/", "b/")) and p[2:].startswith(_KNOWN_ROOTS):
        # tolerate unstripped git prefixes when the remainder is a repo path
        p = p[2:]
    if p.startswith("/") or re.match(r"^[A-Za-z]:", p) or not _SAFE_PATH.match(p):
        return None
    norm = posixpath.normpath(p)
    if norm.startswith("..") or norm == ".":
        return None
    return norm


def _normalize_test_id(test_id: object) -> str | None:
    if not isinstance(test_id, str):
        return None
    t = test_id.strip().replace("\\", "/")
    while t.startswith("./"):
        t = t[2:]
    return t or None


def _change_paths(change: dict) -> list[str | None]:
    raw = []
    for key in ("path", "old_path", "new_path"):
        value = change.get(key)
        if value is not None:
            raw.append(value)
    if not raw:
        return [None]
    return sorted({normalize_path(v) for v in raw}, key=lambda x: (x is None, x or ""))


def _text_lines(change: dict, key: str) -> list[str]:
    value = change.get(key)
    if not isinstance(value, list):
        return []
    return [line for line in value if isinstance(line, str)]


def _changed_line_count(change: dict) -> int:
    counts = []
    for a, b in (("added_text", "removed_text"), ("new_lines", "old_lines")):
        x, y = change.get(a), change.get(b)
        if isinstance(x, list) or isinstance(y, list):
            counts.append(len(x or []) + len(y or []))
    return max(counts) if counts else 0


_MODULE_IMPORT = re.compile(r"^(from\s+[\w.]+\s+import\b|import\s+\w)")
_DEF_OR_CLASS = re.compile(r"^\s*(?:async\s+)?(def|class)\s+(\w+)")


def _structural_reason(change: dict) -> str | None:
    """Edits that can break *importing* a module, not just calling it.

    A module-level import line, a renamed/removed top-level name, or an
    ``__all__`` edit can raise at import time; through ``sqlglot/__init__``
    that fails every test, so such edits are never narrowed.
    """
    added = _text_lines(change, "added_text")
    removed = _text_lines(change, "removed_text")
    for line in added + removed:
        if _MODULE_IMPORT.match(line) or line.startswith("__all__"):
            return "module-level import or __all__ edit"
    defined_added = {m.group(2) for m in map(_DEF_OR_CLASS.match, added) if m}
    defined_removed = {m.group(2) for m in map(_DEF_OR_CLASS.match, removed) if m}
    if defined_removed - defined_added:
        return "def/class removed or renamed: " + ", ".join(sorted(defined_removed - defined_added))
    return None


def _dialect_of(path: str) -> str | None:
    parts = path.split("/")
    if len(parts) == 3 and parts[0] == "sqlglot" and parts[1] in DIALECT_PACKAGES and parts[2].endswith(".py"):
        stem = parts[2][:-3]
        if stem not in DIALECT_NON_DIALECT_STEMS:
            return stem
    return None


def _build_dialect_children() -> dict[str, frozenset[str]]:
    children: dict[str, set[str]] = {}
    for child, bases in DIALECT_BASES.items():
        for base in bases:
            children.setdefault(base, set()).add(child)
    return {k: frozenset(v) for k, v in children.items()}


_DIALECT_CHILDREN = _build_dialect_children()
_DESCENDANTS_CACHE: dict[str, frozenset[str]] = {}
_REACH_CACHE: dict[str, frozenset[str]] = {}
_DERIVED_CORE = frozenset(DERIVED_CORE_MODULES)
_KNOWN_SOURCE = frozenset(KNOWN_SOURCE_MODULES)


def _dialect_descendants(name: str) -> frozenset[str]:
    cached = _DESCENDANTS_CACHE.get(name)
    if cached is None:
        seen: set[str] = set()
        stack = [name]
        while stack:
            for child in sorted(_DIALECT_CHILDREN.get(stack.pop(), ())):
                if child not in seen and child != name:
                    seen.add(child)
                    stack.append(child)
        cached = _DESCENDANTS_CACHE[name] = frozenset(seen)
    return cached


def _source_reach(path: str) -> frozenset[str]:
    """Source modules that use ``path`` directly or transitively.

    Follows static imports, imports placed inside functions and by-name dialect
    lookups (``dialect="spark"``), as derived from the pinned commit.
    """
    cached = _REACH_CACHE.get(path)
    if cached is None:
        seen: set[str] = set()
        stack = [path]
        while stack:
            for importer in SOURCE_IMPORTERS.get(stack.pop(), ()):
                if importer not in seen and importer != path:
                    seen.add(importer)
                    stack.append(importer)
        cached = _REACH_CACHE[path] = frozenset(seen)
    return cached


def _is_core(path: str) -> bool:
    return path in CORE_MODULES or path in _DERIVED_CORE or path.startswith(CORE_PREFIXES)


def _is_doc(path: str) -> bool:
    base = posixpath.basename(path).lower()
    if path.startswith(("sqlglot/", "tests/")):
        # Markdown inside the package or tests could be read by code (docs
        # tests, package data); only plain top-level/docs prose is harmless.
        return False
    if path.lower().endswith(DOC_SUFFIXES):
        return True
    if path.startswith(DOC_DIRS):
        return True
    return "/" not in path and base.startswith(DOC_BASENAME_PREFIXES)


def _is_config(path: str) -> bool:
    base = posixpath.basename(path)
    if base in CONFIG_BASENAMES or base.startswith("requirements"):
        return True
    if path.startswith(CONFIG_DIR_PREFIXES):
        return True
    return "/" not in path and base.endswith(CONFIG_SUFFIXES)


def _convention_test_files(path: str, test_files: frozenset[str]) -> set[str]:
    """Test files named after a source module (several conventions, not one)."""
    parts = path.split("/")
    stem = parts[-1][:-3] if parts[-1].endswith(".py") else parts[-1]
    if stem == "__init__" and len(parts) >= 3:
        stem = parts[-2]
    names = {f"tests/test_{stem}.py", f"tests/dialects/test_{stem}.py"}
    if len(parts) >= 3:
        pkg = parts[-2]
        names.add(f"tests/test_{pkg}.py")
        names.add("tests/" + "/".join(parts[1:-1]) + f"/test_{stem}.py")
        names.add(f"tests/test_{pkg}_{stem}.py")
    return {n for n in names if n in test_files}


class _TestIndex(NamedTuple):
    all_numbers: frozenset[int]
    numbers_by_file: dict[str, frozenset[int]]
    number_by_id: dict[str, int]
    seconds: dict[int, float]
    total_seconds: float
    test_files: frozenset[str]


_INDEX_CACHE: dict[tuple, tuple[list, _TestIndex]] = {}


def _test_index(tests: list) -> _TestIndex:
    """Index tests.json entries once per tests list (cached by identity+shape)."""
    try:
        key = (id(tests), len(tests), repr(tests[0])[:200] if tests else "", repr(tests[-1])[:200] if tests else "")
    except Exception:  # unusual containers: just build it
        key = None
    if key is not None:
        hit = _INDEX_CACHE.get(key)
        if hit is not None and hit[0] is tests:
            return hit[1]
    by_file: dict[str, set[int]] = {}
    by_id: dict[str, int] = {}
    seconds: dict[int, float] = {}
    for test in tests or []:
        if not isinstance(test, dict):
            continue
        number = test.get("number")
        if isinstance(number, bool) or not isinstance(number, int):
            continue
        file = normalize_path(test.get("file"))
        test_id = _normalize_test_id(test.get("id"))
        if file is None and test_id and "::" in test_id:
            file = normalize_path(test_id.split("::", 1)[0])
        by_file.setdefault(file or "", set()).add(number)
        if test_id:
            by_id[test_id] = number
        s = test.get("seconds")
        seconds[number] = float(s) if isinstance(s, (int, float)) and not isinstance(s, bool) else 0.0
    index = _TestIndex(
        frozenset(seconds),
        {k: frozenset(v) for k, v in by_file.items()},
        by_id,
        seconds,
        sum(seconds.values()),
        frozenset(by_file),
    )
    if key is not None:
        if len(_INDEX_CACHE) >= 4:
            _INDEX_CACHE.clear()
        _INDEX_CACHE[key] = (tests, index)
    return index


# --------------------------------------------------------------------------
# Main policy
# --------------------------------------------------------------------------


def explain_selection(
    changes: list[dict],
    tests: list[dict],
    impacted_files: set[str] | None = None,
    covered_test_ids: set[str] | None = None,
    *,
    analysis_uncertain: bool = False,
    mode: str = "safe",
) -> Selection:
    """Like select_tests but also says whether (and why) it fell back to ALL."""
    index = _test_index(tests if isinstance(tests, list) else list(tests or []))
    all_numbers = index.all_numbers
    numbers_by_file = index.numbers_by_file
    number_by_id = index.number_by_id
    test_files = index.test_files

    def everything(reason: str) -> Selection:
        LOG.info("select_tests -> ALL (%d tests): %s", len(all_numbers), reason)
        return Selection(set(all_numbers), True, [reason])

    if mode not in MODES:
        return everything(f"unknown mode {mode!r}")
    aggressive = mode == "aggressive"
    relaxed = mode in ("balanced", "aggressive")

    if analysis_uncertain:
        return everything("analysis_uncertain=True")
    if not isinstance(changes, list) or not changes:
        return everything("no file changes were parsed")

    impacted = set()
    for item in impacted_files or ():
        p = normalize_path(item)
        if p:
            impacted.add(p)
    covered: set[int] = set()
    unknown_cov = 0
    for item in covered_test_ids or ():
        tid = _normalize_test_id(item)
        number = number_by_id.get(tid) if tid else None
        if number is None and tid and "[" in tid:
            number = number_by_id.get(tid.split("[", 1)[0])
        if number is None:
            unknown_cov += 1
        else:
            covered.add(number)

    factor = 2 if aggressive else 1
    if len(changes) > BROAD_MAX_FILES * factor:
        return everything(f"broad change: {len(changes)} files")
    total_lines = sum(_changed_line_count(c) for c in changes if isinstance(c, dict))
    if total_lines > BROAD_MAX_CHANGED_LINES * factor:
        return everything(f"broad change: {total_lines} changed lines")

    selected: set[int] = set()
    reasons: list[str] = []
    files: set[str] = set()
    source_changes: list[str] = []
    added_modules: list[tuple[str, int]] = []
    change_texts = [
        "\n".join(_text_lines(c, "added_text")) if isinstance(c, dict) else "" for c in changes
    ]

    def add_files(names: Iterable[str]) -> None:
        for name in names:
            if name in numbers_by_file:
                files.add(name)

    def add_ids(ids: Iterable[str]) -> None:
        for test_id in ids:
            number = number_by_id.get(test_id)
            if number is not None:
                selected.add(number)

    def add_dialect(dialect: str) -> None:
        add_files([f"tests/dialects/test_{dialect}.py"])
        add_files(DIALECT_TEST_FILE_MENTIONS.get(dialect, ()))
        # aggressive: with coverage present, coverage replaces name mentions
        # outside tests/dialects/.
        if not (aggressive and covered):
            add_ids(DIALECT_TEST_ID_MENTIONS.get(dialect, ()))

    def add_module_tests(module: str) -> bool:
        """Tests tied to one non-core source module; True if any rule matched."""
        found = False
        for group in (
            _convention_test_files(module, test_files),
            MODULE_TEST_IMPORTERS.get(module, ()),
            MODULE_EXTRA_TEST_FILES.get(module, ()),
        ):
            if group:
                found = True
                add_files(group)
        for prefix, extra in PACKAGE_EXTRA_TEST_FILES.items():
            if module.startswith(prefix):
                found = True
                add_files(extra)
                add_files(MODULE_TEST_IMPORTERS.get(prefix + "__init__.py", ()))
        return found

    for position, change in enumerate(changes):
        if not isinstance(change, dict):
            return everything("malformed change record")
        if change.get("uncertain"):
            return everything(f"patch analysis uncertain for {change.get('path')!r}")
        status = change.get("status")
        if status in FALLBACK_STATUSES or status not in KNOWN_STATUSES:
            return everything(f"status {status!r} for {change.get('path')!r}")
        paths = _change_paths(change)
        if any(p is None for p in paths) and status not in ("added", "deleted"):
            return everything(f"unusable path in {change.get('path')!r}")
        paths = [p for p in paths if p]
        if not paths:
            return everything("change without a usable path")

        for path in paths:
            # ---- harmless prose -------------------------------------------
            if _is_doc(path):
                reasons.append(f"{path}: documentation, no tests")
                continue
            if path.startswith(CI_PREFIXES):
                if relaxed:
                    reasons.append(f"{path}: CI configuration, not used by the test run")
                    continue
                return everything(f"CI configuration change {path}")
            if _is_config(path):
                return everything(f"build/config change {path}")

            # ---- tests -----------------------------------------------------
            if path.startswith("tests/"):
                if path.startswith("tests/fixtures/"):
                    ids = FIXTURE_TEST_IDS.get(path, ())
                    setup_files = FIXTURE_SETUP_FILES.get(path, ())
                    if not ids and not setup_files:
                        return everything(f"fixture {path} has no known consumer")
                    # Loads in setUpClass/module scope break the whole file.
                    add_files(setup_files)
                    if relaxed:
                        add_ids(ids)
                    else:
                        add_files(i.split("::", 1)[0] for i in ids)
                    reasons.append(f"{path}: fixture consumers")
                    continue
                base = posixpath.basename(path)
                shared = SHARED_TEST_IMPORTERS.get(path)
                if shared:
                    if not relaxed:
                        return everything(f"shared test module {path}")
                    add_files(shared)
                    add_files([path])
                    reasons.append(f"{path}: shared test module, {len(shared)} importing test files")
                    continue
                if base in TEST_INFRA_BASENAMES or not base.endswith(".py"):
                    return everything(f"test infrastructure change {path}")
                if path in numbers_by_file:
                    add_files([path])
                    reasons.append(f"{path}: changed test file")
                    continue
                if relaxed and base.startswith("test_") and status in ("added", "copied", "modified", "renamed"):
                    reasons.append(f"{path}: test module not collected in tests.json")
                    continue
                return everything(f"unrecognised test module {path}")

            # ---- sqlglot source -------------------------------------------
            if path.startswith("sqlglot/"):
                if not path.endswith(".py"):
                    if path == "sqlglot/py.typed":
                        reasons.append(f"{path}: typing marker")
                        continue
                    return everything(f"non-Python package file {path}")
                known = path in _KNOWN_SOURCE
                if status == "added" and not known:
                    if not relaxed:
                        return everything(f"new source module {path}")
                    added_modules.append((path, position))
                    continue
                if status != "modified" and not aggressive:
                    return everything(f"{status} source module {path}")
                if not known and not aggressive:
                    return everything(f"source module {path} is not in the pinned dependency map")
                structural = _structural_reason(change)
                if structural and not aggressive:
                    return everything(f"{path}: {structural}")
                source_changes.append(path)
                if _is_core(path):
                    if aggressive and covered:
                        reasons.append(f"{path}: core module, coverage-driven (aggressive)")
                        continue
                    lazy = LAZY_SOURCE_IMPORTERS.get(path)
                    why = f" (used inside functions of {', '.join(lazy)})" if lazy and path not in CORE_MODULES else ""
                    return everything(f"core module {path}{why}")

                reach = _source_reach(path)
                reached_core = sorted(m for m in reach if _is_core(m))
                if reached_core and not aggressive:
                    return everything(f"{path} is used by core module {reached_core[0]}")
                matched = False
                dialects: set[str] = set()
                for module in sorted(reach | {path}):
                    dialect = _dialect_of(module)
                    if dialect is not None:
                        dialects.add(dialect)
                    elif module not in REGISTRY_MODULES:
                        matched = add_module_tests(module) or matched
                own = _dialect_of(path)
                if own is not None:
                    if own not in DIALECT_TEST_FILE_MENTIONS and f"tests/dialects/test_{own}.py" not in test_files:
                        return everything(f"unknown dialect module {path}")
                    dialects |= {own} | _dialect_descendants(own)
                for dialect in sorted(dialects):
                    dialects |= _dialect_descendants(dialect)
                if dialects:
                    matched = True
                    for dialect in sorted(dialects):
                        add_dialect(dialect)
                    add_ids(ALL_DIALECT_TEST_IDS)
                    add_files(DIALECT_INFRA_TEST_FILES)
                    if path.startswith("sqlglot/typing/"):
                        add_files(["tests/test_typing.py"])
                if not matched and not covered:
                    return everything(f"no known tests for module {path}")
                users = sorted(m for m in reach if _dialect_of(m) is None)
                note = f"; used by {', '.join(users[:4])}{'…' if len(users) > 4 else ''}" if users else ""
                reasons.append(
                    f"{path}: source module"
                    + (f", dialects {', '.join(sorted(dialects))}" if dialects else "")
                    + note
                )
                continue

            return everything(f"unknown path {path}")

    # ---- newly added source modules ---------------------------------------
    for path, position in added_modules:
        dotted = path[:-3].replace("/", ".").removesuffix(".__init__")
        stem = posixpath.basename(path)[:-3]
        other_text = "\n".join(t for i, t in enumerate(change_texts) if i != position)
        if dotted in other_text or re.search(r"\b%s\b" % re.escape(stem), other_text):
            # Reached only through the changed modules that import it; their
            # own selection covers the new code path.
            reasons.append(f"{path}: new module, used by other changed modules")
        else:
            reasons.append(f"{path}: new module not referenced by any change or pinned import")
        source_changes.append(path)

    # ---- coverage / dependency signals ------------------------------------
    if source_changes:
        if unknown_cov and not covered and not relaxed:
            return everything(f"none of {unknown_cov} covered test ids are in tests.json")
        if not covered and not relaxed:
            return everything("coverage missing for source change(s): " + ", ".join(source_changes))
        selected |= covered
    impacted_tests = {p for p in impacted if p in numbers_by_file}
    # Core modules named in impacted_files are not narrowed or expanded here:
    # core changes are decided from the pinned map above, and a graph that
    # walks through package __init__ re-exports names core modules for
    # almost any change.
    near_universal = aggressive and len(impacted_tests) >= UNINFORMATIVE_IMPACT_SHARE * max(1, len(test_files))
    if near_universal:
        reasons.append("impacted_files names almost every test file; ignored (aggressive)")
    else:
        files |= impacted_tests
        for p in sorted(impacted):
            if p.startswith("sqlglot/") and p.endswith(".py") and not _is_core(p) and p not in REGISTRY_MODULES:
                dialect = _dialect_of(p)
                if dialect is not None:
                    add_files([f"tests/dialects/test_{dialect}.py"])
                else:
                    add_module_tests(p)
    for name in files:
        selected |= numbers_by_file[name]

    selected &= all_numbers
    if not selected:
        if source_changes and not added_modules:
            return everything("source change but no candidate tests")
        LOG.info("select_tests -> none: %s", "; ".join(reasons))
        return Selection(set(), False, reasons or ["no change can affect tests"])

    chosen = sum(index.seconds[n] for n in selected)
    if index.total_seconds > 0 and chosen >= PROMOTE_TO_ALL_SHARE * index.total_seconds:
        return everything(f"selection already {chosen / index.total_seconds:.0%} of suite seconds")
    LOG.info("select_tests -> %d/%d tests: %s", len(selected), len(all_numbers), "; ".join(reasons))
    return Selection(selected, False, reasons)


def select_tests(
    changes: list[dict],
    tests: list[dict],
    impacted_files: set[str],
    covered_test_ids: set[str],
    *,
    analysis_uncertain: bool = False,
    mode: str = "safe",
) -> set[int]:
    """Return the tests.json numbers to run. Falls back to every test when unsure."""
    try:
        return explain_selection(
            changes,
            tests,
            impacted_files,
            covered_test_ids,
            analysis_uncertain=analysis_uncertain,
            mode=mode,
        ).numbers
    except Exception as error:  # never let a policy bug drop tests
        LOG.warning("select_tests failed (%s); running every test", error)
        return {
            t["number"]
            for t in tests or []
            if isinstance(t, dict) and isinstance(t.get("number"), int) and not isinstance(t.get("number"), bool)
        }


# --------------------------------------------------------------------------
# Offline derivation of the static maps (developer tool, never used at run
# time). Reads only the pinned sqlglot checkout and tests.json.
# --------------------------------------------------------------------------

def derive_static_maps(sqlglot_root: str, tests: list[dict]) -> dict:
    """Rebuild the GENERATED tables from a sqlglot checkout at the pinned commit."""
    import ast
    import glob
    import os

    def read(rel: str) -> str:
        with open(os.path.join(sqlglot_root, rel), encoding="utf-8") as handle:
            return handle.read()

    def exists(rel: str) -> bool:
        return os.path.isfile(os.path.join(sqlglot_root, rel))

    test_ids = {t["id"] for t in tests}
    test_files = sorted({t["file"] for t in tests})
    modules = sorted(
        os.path.relpath(p, sqlglot_root).replace(os.sep, "/")
        for p in glob.glob(os.path.join(sqlglot_root, "sqlglot/**/*.py"), recursive=True)
    )
    names = sorted(
        os.path.basename(p)[:-3]
        for p in modules
        if p.startswith("sqlglot/dialects/") and os.path.basename(p)[:-3] not in DIALECT_NON_DIALECT_STEMS
    )
    name_set = set(names)
    class_names: dict[str, str] = {}
    for name in names:
        m = re.search(r"^class (\w+)\(", read(f"sqlglot/dialects/{name}.py"), re.M)
        if m:
            class_names[name] = m.group(1)
    dialect_by_class = {v: k for k, v in class_names.items()}

    def module_file(dotted: str) -> str | None:
        rel = dotted.replace(".", "/")
        for cand in (rel + ".py", rel + "/__init__.py"):
            if exists(cand):
                return cand
        return None

    # Attribute -> defining module for lazy re-export packages (PEP 562
    # __getattr__ tables, TYPE_CHECKING imports, the dialect class registry).
    reexports: dict[str, dict[str, str]] = {}
    for package in sorted(REGISTRY_MODULES):
        table: dict[str, str] = {}
        tree = ast.parse(read(package))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                for alias in node.names:
                    target = module_file(f"{node.module}.{alias.name}") or module_file(node.module)
                    if target:
                        table[alias.asname or alias.name] = target
            if isinstance(node, ast.Dict):
                for key, value in zip(node.keys, node.values):
                    if (
                        isinstance(key, ast.Constant)
                        and isinstance(key.value, str)
                        and isinstance(value, ast.Tuple)
                        and value.elts
                        and isinstance(value.elts[0], ast.Constant)
                        and isinstance(value.elts[0].value, str)
                    ):
                        target = module_file(value.elts[0].value)
                        if target:
                            table[key.value] = target
        if package == "sqlglot/dialects/__init__.py":
            for cls, name in dialect_by_class.items():
                table[cls] = f"sqlglot/dialects/{name}.py"
        reexports[package] = table

    def resolve_import(node) -> set[str]:
        """Files an import statement makes available, resolving re-exports."""
        out: set[str] = set()
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("sqlglot"):
                    target = module_file(alias.name)
                    if target:
                        out.add(target)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            if not node.module.startswith("sqlglot"):
                return out
            base = module_file(node.module)
            for alias in node.names:
                sub = module_file(f"{node.module}.{alias.name}")
                if sub:
                    out.add(sub)
                elif base in reexports and alias.name in reexports[base]:
                    out.add(reexports[base][alias.name])
                elif base:
                    out.add(base)
        return out

    def function_node_ids(tree) -> set[int]:
        ids: set[int] = set()
        for fn in ast.walk(tree):
            if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                ids.update(id(n) for n in ast.walk(fn))
        return ids

    def family_files(name: str) -> list[str]:
        return [f"sqlglot/{pkg}/{name}.py" for pkg in DIALECT_PACKAGES if exists(f"sqlglot/{pkg}/{name}.py")]

    # 1. source import graph: static, in-function (lazy) and by-name dialect use
    static_edges: dict[str, set[str]] = {}
    lazy_edges: dict[str, set[str]] = {}
    for mod in modules:
        tree = ast.parse(read(mod))
        in_function = function_node_ids(tree)
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                for target in resolve_import(node) - {mod}:
                    bucket = lazy_edges if id(node) in in_function else static_edges
                    bucket.setdefault(mod, set()).add(target)
            if mod in REGISTRY_MODULES or mod == "sqlglot/dialects/dialect.py":
                continue
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value.lower() in name_set:
                # e.g. build_formatted_time(..., "teradata"), dialect="spark"
                for target in family_files(node.value.lower()):
                    if target != mod:
                        bucket = lazy_edges if id(node) in in_function else static_edges
                        bucket.setdefault(mod, set()).add(target)

    importers: dict[str, set[str]] = {}
    lazy_importers: dict[str, set[str]] = {}
    for src, targets in list(static_edges.items()) + list(lazy_edges.items()):
        if src in REGISTRY_MODULES:
            continue
        for target in targets:
            if target in REGISTRY_MODULES:
                continue
            importers.setdefault(target, set()).add(src)
    for src, targets in lazy_edges.items():
        if src in REGISTRY_MODULES:
            continue
        for target in targets - static_edges.get(src, set()) - REGISTRY_MODULES:
            lazy_importers.setdefault(target, set()).add(src)

    # 2. derived core: everything the parse/generate machinery uses, plus
    # what ``import sqlglot`` itself executes (an import-time failure in any
    # of those modules breaks every test).
    package_imports = static_edges.get("sqlglot/__init__.py", set()) - REGISTRY_MODULES
    core: set[str] = set(CORE_SEEDS) | package_imports
    stack = sorted(core)
    while stack:
        current = stack.pop()
        for target in static_edges.get(current, set()) | lazy_edges.get(current, set()):
            if target not in core and target not in REGISTRY_MODULES:
                core.add(target)
                stack.append(target)

    # 3. dialect -> dialects it builds on (imports or by-name use, any family file)
    def dialect_of(path: str) -> str | None:
        parts = path.split("/")
        if len(parts) == 3 and parts[1] in DIALECT_PACKAGES and parts[2][:-3] in name_set:
            return parts[2][:-3]
        return None

    bases: dict[str, set[str]] = {}
    for src, targets in list(static_edges.items()) + list(lazy_edges.items()):
        a = dialect_of(src)
        if a is None:
            continue
        for target in targets:
            b = dialect_of(target)
            if b is not None and b != a:
                bases.setdefault(a, set()).add(b)

    # 4. fixtures
    fixture_paths = sorted(
        os.path.relpath(p, sqlglot_root).replace(os.sep, "/")
        for p in glob.glob(os.path.join(sqlglot_root, "tests/fixtures/**/*"), recursive=True)
        if os.path.isfile(p)
    )

    def fixture_keys(path: str) -> set[str]:
        rel = path[len("tests/fixtures/"):]
        keys = {rel}
        if rel.endswith(".sql"):
            keys.add(rel[:-4])
            if rel.startswith("optimizer/"):
                keys.add(rel[len("optimizer/"):-4])
        directory = posixpath.dirname(rel)
        if directory.count("/") >= 1:  # e.g. optimizer/tpc-h (not plain "optimizer")
            keys.add(directory)
        base = posixpath.basename(rel)
        if "." in base and not base.endswith(".gz"):
            keys.add(base)
        return keys

    keys_by_fixture = {p: fixture_keys(p) for p in fixture_paths}
    fixture_dialects = {
        p: {d.lower() for d in re.findall(r"#\s*dialect:\s*(\w+)", read(p))}
        for p in fixture_paths
        if p.endswith(".sql")
    }

    def string_constants(node) -> set[str]:
        return {
            n.value.strip("/")
            for n in ast.walk(node)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)
        }

    def matched_fixtures(consts: set[str]) -> set[str]:
        return {p for p, keys in keys_by_fixture.items() if consts & keys}

    fixture_ids: dict[str, set[str]] = {}
    fixture_setup: dict[str, set[str]] = {}
    file_mentions: dict[str, set[str]] = {n: set() for n in names}
    id_mentions: dict[str, set[str]] = {n: set() for n in names}
    all_dialect_ids: set[str] = set()
    test_importers: dict[str, set[str]] = {}
    shared_tests: dict[str, set[str]] = {}

    for tf in test_files:
        src = read(tf)
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                for target in resolve_import(node):
                    test_importers.setdefault(target, set()).add(tf)
            # test modules imported by other test modules (shared helpers)
            mods = []
            if isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                mods = [node.module]
            elif isinstance(node, ast.Import):
                mods = [a.name for a in node.names]
            for mod in mods:
                if mod.startswith("tests."):
                    target = mod.replace(".", "/") + ".py"
                    if target != tf and exists(target):
                        shared_tests.setdefault(target, set()).add(tf)
        if tf.startswith("tests/dialects/"):
            for name in names:
                if re.search(r"[\"']%s[\"']" % re.escape(name), src, re.I) or (
                    class_names.get(name) and re.search(r"\b%s\b" % class_names[name], src)
                ):
                    file_mentions[name].add(tf)
        for node in tree.body:
            if isinstance(node, ast.ClassDef):
                for item in node.body:
                    is_test = isinstance(item, ast.FunctionDef) and item.name.startswith("test")
                    if not is_test and not (isinstance(item, ast.FunctionDef) and item.name == "check_file"):
                        for p in matched_fixtures(string_constants(item)):
                            fixture_setup.setdefault(p, set()).add(tf)
            elif not isinstance(node, (ast.FunctionDef, ast.Import, ast.ImportFrom)):
                for p in matched_fixtures(string_constants(node)):
                    fixture_setup.setdefault(p, set()).add(tf)
        for cls in (n for n in tree.body if isinstance(n, ast.ClassDef)):
            for fn in cls.body:
                if not (isinstance(fn, ast.FunctionDef) and fn.name.startswith("test")):
                    continue
                tid = f"{tf}::{cls.name}::{fn.name}"
                if tid not in test_ids:
                    continue
                consts = string_constants(fn)
                hits = matched_fixtures(consts)
                for p in hits:
                    fixture_ids.setdefault(p, set()).add(tid)
                if tf.startswith("tests/dialects/"):
                    continue
                segment = ast.get_source_segment(src, fn) or ""
                lowered = {c.lower() for c in consts}
                idents = {n.id for n in ast.walk(fn) if isinstance(n, ast.Name)}
                idents |= {n.attr for n in ast.walk(fn) if isinstance(n, ast.Attribute)}
                via_fixture = set().union(*(fixture_dialects.get(p, set()) for p in hits)) if hits else set()
                if re.search(r"\bDialects\b|Dialect\.classes|\bDIALECTS\b", segment):
                    all_dialect_ids.add(tid)
                for name in names:
                    if (
                        name in lowered
                        or class_names.get(name) in idents
                        or name in via_fixture
                        or re.search(r"[\"']%s[\"']" % re.escape(name), segment, re.I)
                        or re.search(r"dialect:\s*%s\b" % re.escape(name), segment, re.I)
                    ):
                        id_mentions[name].add(tid)

    def freeze(d: dict) -> dict:
        return {k: tuple(sorted(v)) for k, v in sorted(d.items()) if v}

    return {
        "DIALECT_BASES": freeze(bases),
        "DIALECT_TEST_FILE_MENTIONS": freeze(file_mentions),
        "DIALECT_TEST_ID_MENTIONS": freeze(id_mentions),
        "ALL_DIALECT_TEST_IDS": tuple(sorted(all_dialect_ids)),
        "FIXTURE_TEST_IDS": freeze(fixture_ids),
        "FIXTURE_SETUP_FILES": freeze(fixture_setup),
        "MODULE_TEST_IMPORTERS": freeze(test_importers),
        "SOURCE_IMPORTERS": freeze(importers),
        "LAZY_SOURCE_IMPORTERS": freeze(lazy_importers),
        "DERIVED_CORE_MODULES": tuple(sorted(core)),
        "SHARED_TEST_IMPORTERS": freeze(shared_tests),
        "KNOWN_SOURCE_MODULES": tuple(modules),
    }


_GENERATED_TYPES = {
    "DIALECT_BASES": "dict[str, tuple[str, ...]]",
    "DIALECT_TEST_FILE_MENTIONS": "dict[str, tuple[str, ...]]",
    "DIALECT_TEST_ID_MENTIONS": "dict[str, tuple[str, ...]]",
    "ALL_DIALECT_TEST_IDS": "tuple[str, ...]",
    "FIXTURE_TEST_IDS": "dict[str, tuple[str, ...]]",
    "FIXTURE_SETUP_FILES": "dict[str, tuple[str, ...]]",
    "MODULE_TEST_IMPORTERS": "dict[str, tuple[str, ...]]",
    "SOURCE_IMPORTERS": "dict[str, tuple[str, ...]]",
    "LAZY_SOURCE_IMPORTERS": "dict[str, tuple[str, ...]]",
    "DERIVED_CORE_MODULES": "tuple[str, ...]",
    "SHARED_TEST_IMPORTERS": "dict[str, tuple[str, ...]]",
    "KNOWN_SOURCE_MODULES": "tuple[str, ...]",
}


def _render_generated(maps: dict) -> str:
    lines = []
    for key, annotation in _GENERATED_TYPES.items():
        value = maps[key]
        if isinstance(value, dict):
            lines.append(f"{key}: {annotation} = {{")
            for k, v in value.items():
                lines.append(f"    {k!r}: {v!r},")
            lines.append("}")
        else:
            lines.append(f"{key}: {annotation} = (")
            for item in value:
                lines.append(f"    {item!r},")
            lines.append(")")
    return "\n".join(lines)


if __name__ == "__main__":  # pragma: no cover - developer tool
    import json

    if len(sys.argv) == 4 and sys.argv[1] == "--derive":
        with open(sys.argv[3], encoding="utf-8") as handle:
            tests_list = json.load(handle)["tests"]
        print(_render_generated(derive_static_maps(sys.argv[2], tests_list)))
    else:
        print("usage: test_selector.py --derive <sqlglot checkout> <tests.json>", file=sys.stderr)
        sys.exit(2)
