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
import ``analyzer.contracts`` or any other agent's module and uses only the
standard library. It never reads ``sample/answers.json``, never looks at commit
ids, and has no table keyed by patch content or edit kind. The only embedded
knowledge is a set of static maps derived from the *pinned* sqlglot commit
(dialect inheritance, which tests name a dialect, which tests load which
fixture file, which test files import which sqlglot module). They can be
regenerated with ``python3 analyzer/test_selector.py --derive <sqlglot> <tests.json>``.

Policy summary (details in ``explain_selection``):

1. Global fallbacks to ALL tests: ``analysis_uncertain``, no changes parsed,
   any change flagged ``uncertain`` or with status binary/unknown/unexpected,
   unsafe paths, broad refactors (many files or lines).
2. Each changed path is classified: docs (harmless), CI config, build/config,
   test-infrastructure Python (helpers/conftest/__init__/shared test modules),
   a collected test file, a fixture data file, a core sqlglot module, a dialect
   family module (dialects|parsers|generators|typing/<name>.py), another
   recognised sqlglot module, or unknown.
3. Unknown, config, test infrastructure and core changes fall back to ALL
   (except as relaxed by the mode, see below).
4. Otherwise the selection is the union of: tests of changed test files,
   fixture consumers, coverage hits (``covered_test_ids``), tests in impacted
   test files (``impacted_files``), naming-convention test files, direct test
   importers of the module (and of its package), and for dialects the dialect's
   own test file, the test files of dialects that inherit from it, and tests
   that name the dialect.
5. In safe mode a source change with no coverage hit at all is treated as
   "coverage missing" and falls back to ALL.

Modes:
* safe        - as above.
* balanced    - source changes may be selected heuristically without coverage;
                fixture data selects only the tests that load that fixture;
                the shared Validator module (tests/dialects/test_dialect.py)
                selects the dialect test files rather than everything; CI-only
                files are harmless; uncollected test modules are ignored.
* aggressive  - balanced, plus: core modules use coverage hits (if present)
                instead of ALL; when coverage is present it replaces dialect
                name mentions outside tests/dialects/; added/deleted/renamed
                source modules are treated like modifications; near-universal
                impacted sets are ignored; broad-refactor limits are doubled.
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

# Test-side Python that other tests import or that pytest loads implicitly.
TEST_INFRA_BASENAMES = frozenset({"conftest.py", "__init__.py", "helpers.py", "leakcheck.py"})
# Test modules imported by other test modules (Validator lives here and 33
# dialect test files import it). value = path prefix of its importers.
SHARED_TEST_MODULES = {"tests/dialects/test_dialect.py": "tests/dialects/"}

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
DIALECT_BASES: dict[str, tuple[str, ...]] = {'athena': ('hive', 'trino'),
 'databricks': ('spark',),
 'doris': ('mysql',),
 'drill': ('mysql',),
 'dune': ('trino',),
 'fabric': ('tsql',),
 'materialize': ('postgres',),
 'presto': ('hive', 'mysql'),
 'redshift': ('postgres',),
 'risingwave': ('postgres',),
 'singlestore': ('mysql',),
 'spark': ('hive', 'spark2'),
 'spark2': ('hive',),
 'starrocks': ('mysql',),
 'trino': ('presto',)}
DIALECT_TEST_FILE_MENTIONS: dict[str, tuple[str, ...]] = {'athena': ('tests/dialects/test_athena.py',
            'tests/dialects/test_dialect.py',
            'tests/dialects/test_postgres.py',
            'tests/dialects/test_presto.py',
            'tests/dialects/test_spark.py'),
 'bigquery': ('tests/dialects/test_bigquery.py',
              'tests/dialects/test_clickhouse.py',
              'tests/dialects/test_dialect.py',
              'tests/dialects/test_duckdb.py',
              'tests/dialects/test_exasol.py',
              'tests/dialects/test_hive.py',
              'tests/dialects/test_mysql.py',
              'tests/dialects/test_oracle.py',
              'tests/dialects/test_pipe_syntax.py',
              'tests/dialects/test_postgres.py',
              'tests/dialects/test_presto.py',
              'tests/dialects/test_redshift.py',
              'tests/dialects/test_singlestore.py',
              'tests/dialects/test_snowflake.py',
              'tests/dialects/test_spark.py',
              'tests/dialects/test_sqlite.py',
              'tests/dialects/test_starrocks.py',
              'tests/dialects/test_teradata.py',
              'tests/dialects/test_trino.py',
              'tests/dialects/test_tsql.py'),
 'clickhouse': ('tests/dialects/test_bigquery.py',
                'tests/dialects/test_clickhouse.py',
                'tests/dialects/test_databricks.py',
                'tests/dialects/test_dialect.py',
                'tests/dialects/test_doris.py',
                'tests/dialects/test_duckdb.py',
                'tests/dialects/test_exasol.py',
                'tests/dialects/test_hive.py',
                'tests/dialects/test_mysql.py',
                'tests/dialects/test_oracle.py',
                'tests/dialects/test_postgres.py',
                'tests/dialects/test_presto.py',
                'tests/dialects/test_singlestore.py',
                'tests/dialects/test_snowflake.py',
                'tests/dialects/test_spark.py',
                'tests/dialects/test_sqlite.py',
                'tests/dialects/test_starrocks.py',
                'tests/dialects/test_teradata.py',
                'tests/dialects/test_tsql.py'),
 'databricks': ('tests/dialects/test_bigquery.py',
                'tests/dialects/test_clickhouse.py',
                'tests/dialects/test_databricks.py',
                'tests/dialects/test_dialect.py',
                'tests/dialects/test_duckdb.py',
                'tests/dialects/test_exasol.py',
                'tests/dialects/test_hive.py',
                'tests/dialects/test_mysql.py',
                'tests/dialects/test_oracle.py',
                'tests/dialects/test_postgres.py',
                'tests/dialects/test_presto.py',
                'tests/dialects/test_redshift.py',
                'tests/dialects/test_snowflake.py',
                'tests/dialects/test_spark.py',
                'tests/dialects/test_starrocks.py',
                'tests/dialects/test_teradata.py',
                'tests/dialects/test_trino.py',
                'tests/dialects/test_tsql.py'),
 'dax': ('tests/dialects/test_dax.py', 'tests/dialects/test_teradata.py'),
 'doris': ('tests/dialects/test_clickhouse.py',
           'tests/dialects/test_dialect.py',
           'tests/dialects/test_doris.py',
           'tests/dialects/test_duckdb.py',
           'tests/dialects/test_hive.py',
           'tests/dialects/test_mysql.py',
           'tests/dialects/test_oracle.py',
           'tests/dialects/test_postgres.py',
           'tests/dialects/test_presto.py',
           'tests/dialects/test_spark.py'),
 'dremio': ('tests/dialects/test_bigquery.py',
            'tests/dialects/test_databricks.py',
            'tests/dialects/test_dialect.py',
            'tests/dialects/test_dremio.py'),
 'drill': ('tests/dialects/test_bigquery.py',
           'tests/dialects/test_dialect.py',
           'tests/dialects/test_drill.py',
           'tests/dialects/test_duckdb.py',
           'tests/dialects/test_exasol.py',
           'tests/dialects/test_hive.py',
           'tests/dialects/test_mysql.py',
           'tests/dialects/test_oracle.py',
           'tests/dialects/test_postgres.py',
           'tests/dialects/test_presto.py',
           'tests/dialects/test_redshift.py',
           'tests/dialects/test_snowflake.py',
           'tests/dialects/test_teradata.py'),
 'druid': ('tests/dialects/test_druid.py',),
 'duckdb': ('tests/dialects/test_bigquery.py',
            'tests/dialects/test_clickhouse.py',
            'tests/dialects/test_databricks.py',
            'tests/dialects/test_dax.py',
            'tests/dialects/test_dialect.py',
            'tests/dialects/test_doris.py',
            'tests/dialects/test_dremio.py',
            'tests/dialects/test_drill.py',
            'tests/dialects/test_duckdb.py',
            'tests/dialects/test_exasol.py',
            'tests/dialects/test_hive.py',
            'tests/dialects/test_materialize.py',
            'tests/dialects/test_mysql.py',
            'tests/dialects/test_oracle.py',
            'tests/dialects/test_postgres.py',
            'tests/dialects/test_presto.py',
            'tests/dialects/test_redshift.py',
            'tests/dialects/test_singlestore.py',
            'tests/dialects/test_snowflake.py',
            'tests/dialects/test_spark.py',
            'tests/dialects/test_sqlite.py',
            'tests/dialects/test_starrocks.py',
            'tests/dialects/test_teradata.py',
            'tests/dialects/test_trino.py',
            'tests/dialects/test_tsql.py'),
 'dune': ('tests/dialects/test_dune.py', 'tests/dialects/test_teradata.py'),
 'exasol': ('tests/dialects/test_bigquery.py',
            'tests/dialects/test_dialect.py',
            'tests/dialects/test_duckdb.py',
            'tests/dialects/test_exasol.py',
            'tests/dialects/test_hive.py',
            'tests/dialects/test_mysql.py',
            'tests/dialects/test_oracle.py',
            'tests/dialects/test_teradata.py'),
 'fabric': ('tests/dialects/test_fabric.py', 'tests/dialects/test_teradata.py'),
 'hive': ('tests/dialects/test_athena.py',
          'tests/dialects/test_bigquery.py',
          'tests/dialects/test_clickhouse.py',
          'tests/dialects/test_databricks.py',
          'tests/dialects/test_dialect.py',
          'tests/dialects/test_dremio.py',
          'tests/dialects/test_duckdb.py',
          'tests/dialects/test_exasol.py',
          'tests/dialects/test_hive.py',
          'tests/dialects/test_mysql.py',
          'tests/dialects/test_oracle.py',
          'tests/dialects/test_postgres.py',
          'tests/dialects/test_presto.py',
          'tests/dialects/test_redshift.py',
          'tests/dialects/test_singlestore.py',
          'tests/dialects/test_snowflake.py',
          'tests/dialects/test_spark.py',
          'tests/dialects/test_starrocks.py',
          'tests/dialects/test_tableau.py',
          'tests/dialects/test_teradata.py',
          'tests/dialects/test_tsql.py'),
 'materialize': ('tests/dialects/test_bigquery.py',
                 'tests/dialects/test_dialect.py',
                 'tests/dialects/test_materialize.py',
                 'tests/dialects/test_spark.py'),
 'mysql': ('tests/dialects/test_bigquery.py',
           'tests/dialects/test_clickhouse.py',
           'tests/dialects/test_databricks.py',
           'tests/dialects/test_dialect.py',
           'tests/dialects/test_doris.py',
           'tests/dialects/test_drill.py',
           'tests/dialects/test_duckdb.py',
           'tests/dialects/test_exasol.py',
           'tests/dialects/test_hive.py',
           'tests/dialects/test_mysql.py',
           'tests/dialects/test_oracle.py',
           'tests/dialects/test_postgres.py',
           'tests/dialects/test_presto.py',
           'tests/dialects/test_redshift.py',
           'tests/dialects/test_singlestore.py',
           'tests/dialects/test_snowflake.py',
           'tests/dialects/test_spark.py',
           'tests/dialects/test_sqlite.py',
           'tests/dialects/test_starrocks.py',
           'tests/dialects/test_teradata.py',
           'tests/dialects/test_tsql.py'),
 'oracle': ('tests/dialects/test_bigquery.py',
            'tests/dialects/test_clickhouse.py',
            'tests/dialects/test_dialect.py',
            'tests/dialects/test_doris.py',
            'tests/dialects/test_dremio.py',
            'tests/dialects/test_duckdb.py',
            'tests/dialects/test_exasol.py',
            'tests/dialects/test_hive.py',
            'tests/dialects/test_mysql.py',
            'tests/dialects/test_oracle.py',
            'tests/dialects/test_postgres.py',
            'tests/dialects/test_presto.py',
            'tests/dialects/test_redshift.py',
            'tests/dialects/test_singlestore.py',
            'tests/dialects/test_snowflake.py',
            'tests/dialects/test_spark.py',
            'tests/dialects/test_sqlite.py',
            'tests/dialects/test_teradata.py',
            'tests/dialects/test_tsql.py'),
 'postgres': ('tests/dialects/test_bigquery.py',
              'tests/dialects/test_clickhouse.py',
              'tests/dialects/test_databricks.py',
              'tests/dialects/test_dialect.py',
              'tests/dialects/test_doris.py',
              'tests/dialects/test_dremio.py',
              'tests/dialects/test_drill.py',
              'tests/dialects/test_duckdb.py',
              'tests/dialects/test_dune.py',
              'tests/dialects/test_exasol.py',
              'tests/dialects/test_fabric.py',
              'tests/dialects/test_hive.py',
              'tests/dialects/test_materialize.py',
              'tests/dialects/test_mysql.py',
              'tests/dialects/test_oracle.py',
              'tests/dialects/test_postgres.py',
              'tests/dialects/test_presto.py',
              'tests/dialects/test_redshift.py',
              'tests/dialects/test_singlestore.py',
              'tests/dialects/test_snowflake.py',
              'tests/dialects/test_spark.py',
              'tests/dialects/test_sqlite.py',
              'tests/dialects/test_starrocks.py',
              'tests/dialects/test_teradata.py',
              'tests/dialects/test_tsql.py'),
 'presto': ('tests/dialects/test_bigquery.py',
            'tests/dialects/test_clickhouse.py',
            'tests/dialects/test_databricks.py',
            'tests/dialects/test_dialect.py',
            'tests/dialects/test_dremio.py',
            'tests/dialects/test_duckdb.py',
            'tests/dialects/test_exasol.py',
            'tests/dialects/test_hive.py',
            'tests/dialects/test_mysql.py',
            'tests/dialects/test_oracle.py',
            'tests/dialects/test_postgres.py',
            'tests/dialects/test_presto.py',
            'tests/dialects/test_redshift.py',
            'tests/dialects/test_singlestore.py',
            'tests/dialects/test_snowflake.py',
            'tests/dialects/test_spark.py',
            'tests/dialects/test_starrocks.py',
            'tests/dialects/test_tableau.py',
            'tests/dialects/test_teradata.py',
            'tests/dialects/test_trino.py',
            'tests/dialects/test_tsql.py'),
 'prql': ('tests/dialects/test_prql.py',),
 'redshift': ('tests/dialects/test_bigquery.py',
              'tests/dialects/test_dialect.py',
              'tests/dialects/test_duckdb.py',
              'tests/dialects/test_exasol.py',
              'tests/dialects/test_hive.py',
              'tests/dialects/test_mysql.py',
              'tests/dialects/test_oracle.py',
              'tests/dialects/test_postgres.py',
              'tests/dialects/test_presto.py',
              'tests/dialects/test_redshift.py',
              'tests/dialects/test_singlestore.py',
              'tests/dialects/test_snowflake.py',
              'tests/dialects/test_spark.py',
              'tests/dialects/test_sqlite.py',
              'tests/dialects/test_teradata.py',
              'tests/dialects/test_tsql.py'),
 'risingwave': ('tests/dialects/test_bigquery.py',
                'tests/dialects/test_dialect.py',
                'tests/dialects/test_risingwave.py'),
 'singlestore': ('tests/dialects/test_dialect.py',
                 'tests/dialects/test_duckdb.py',
                 'tests/dialects/test_mysql.py',
                 'tests/dialects/test_postgres.py',
                 'tests/dialects/test_singlestore.py',
                 'tests/dialects/test_snowflake.py',
                 'tests/dialects/test_tsql.py'),
 'snowflake': ('tests/dialects/test_bigquery.py',
               'tests/dialects/test_clickhouse.py',
               'tests/dialects/test_databricks.py',
               'tests/dialects/test_dialect.py',
               'tests/dialects/test_doris.py',
               'tests/dialects/test_drill.py',
               'tests/dialects/test_duckdb.py',
               'tests/dialects/test_exasol.py',
               'tests/dialects/test_hive.py',
               'tests/dialects/test_mysql.py',
               'tests/dialects/test_oracle.py',
               'tests/dialects/test_postgres.py',
               'tests/dialects/test_presto.py',
               'tests/dialects/test_redshift.py',
               'tests/dialects/test_singlestore.py',
               'tests/dialects/test_snowflake.py',
               'tests/dialects/test_spark.py',
               'tests/dialects/test_sqlite.py',
               'tests/dialects/test_starrocks.py',
               'tests/dialects/test_teradata.py',
               'tests/dialects/test_trino.py',
               'tests/dialects/test_tsql.py'),
 'solr': ('tests/dialects/test_solr.py',),
 'spark': ('tests/dialects/test_bigquery.py',
           'tests/dialects/test_clickhouse.py',
           'tests/dialects/test_databricks.py',
           'tests/dialects/test_dialect.py',
           'tests/dialects/test_dremio.py',
           'tests/dialects/test_duckdb.py',
           'tests/dialects/test_exasol.py',
           'tests/dialects/test_hive.py',
           'tests/dialects/test_materialize.py',
           'tests/dialects/test_mysql.py',
           'tests/dialects/test_oracle.py',
           'tests/dialects/test_postgres.py',
           'tests/dialects/test_presto.py',
           'tests/dialects/test_redshift.py',
           'tests/dialects/test_snowflake.py',
           'tests/dialects/test_spark.py',
           'tests/dialects/test_sqlite.py',
           'tests/dialects/test_starrocks.py',
           'tests/dialects/test_teradata.py',
           'tests/dialects/test_trino.py',
           'tests/dialects/test_tsql.py'),
 'spark2': ('tests/dialects/test_bigquery.py',
            'tests/dialects/test_databricks.py',
            'tests/dialects/test_dialect.py',
            'tests/dialects/test_duckdb.py',
            'tests/dialects/test_exasol.py',
            'tests/dialects/test_hive.py',
            'tests/dialects/test_mysql.py',
            'tests/dialects/test_oracle.py',
            'tests/dialects/test_postgres.py',
            'tests/dialects/test_presto.py',
            'tests/dialects/test_redshift.py',
            'tests/dialects/test_singlestore.py',
            'tests/dialects/test_snowflake.py',
            'tests/dialects/test_spark.py',
            'tests/dialects/test_tsql.py'),
 'sqlite': ('tests/dialects/test_bigquery.py',
            'tests/dialects/test_clickhouse.py',
            'tests/dialects/test_dialect.py',
            'tests/dialects/test_doris.py',
            'tests/dialects/test_duckdb.py',
            'tests/dialects/test_exasol.py',
            'tests/dialects/test_hive.py',
            'tests/dialects/test_mysql.py',
            'tests/dialects/test_postgres.py',
            'tests/dialects/test_presto.py',
            'tests/dialects/test_redshift.py',
            'tests/dialects/test_snowflake.py',
            'tests/dialects/test_sqlite.py',
            'tests/dialects/test_teradata.py',
            'tests/dialects/test_tsql.py'),
 'starrocks': ('tests/dialects/test_bigquery.py',
               'tests/dialects/test_dialect.py',
               'tests/dialects/test_duckdb.py',
               'tests/dialects/test_mysql.py',
               'tests/dialects/test_oracle.py',
               'tests/dialects/test_postgres.py',
               'tests/dialects/test_presto.py',
               'tests/dialects/test_redshift.py',
               'tests/dialects/test_snowflake.py',
               'tests/dialects/test_starrocks.py',
               'tests/dialects/test_teradata.py',
               'tests/dialects/test_tsql.py'),
 'tableau': ('tests/dialects/test_dialect.py',
             'tests/dialects/test_mysql.py',
             'tests/dialects/test_oracle.py',
             'tests/dialects/test_presto.py',
             'tests/dialects/test_redshift.py',
             'tests/dialects/test_tableau.py',
             'tests/dialects/test_teradata.py'),
 'teradata': ('tests/dialects/test_databricks.py',
              'tests/dialects/test_dialect.py',
              'tests/dialects/test_duckdb.py',
              'tests/dialects/test_exasol.py',
              'tests/dialects/test_mysql.py',
              'tests/dialects/test_oracle.py',
              'tests/dialects/test_postgres.py',
              'tests/dialects/test_presto.py',
              'tests/dialects/test_redshift.py',
              'tests/dialects/test_snowflake.py',
              'tests/dialects/test_spark.py',
              'tests/dialects/test_teradata.py',
              'tests/dialects/test_tsql.py'),
 'trino': ('tests/dialects/test_athena.py',
           'tests/dialects/test_bigquery.py',
           'tests/dialects/test_clickhouse.py',
           'tests/dialects/test_databricks.py',
           'tests/dialects/test_dialect.py',
           'tests/dialects/test_dremio.py',
           'tests/dialects/test_duckdb.py',
           'tests/dialects/test_dune.py',
           'tests/dialects/test_exasol.py',
           'tests/dialects/test_hive.py',
           'tests/dialects/test_mysql.py',
           'tests/dialects/test_postgres.py',
           'tests/dialects/test_presto.py',
           'tests/dialects/test_redshift.py',
           'tests/dialects/test_snowflake.py',
           'tests/dialects/test_spark.py',
           'tests/dialects/test_sqlite.py',
           'tests/dialects/test_teradata.py',
           'tests/dialects/test_trino.py',
           'tests/dialects/test_tsql.py'),
 'tsql': ('tests/dialects/test_bigquery.py',
          'tests/dialects/test_clickhouse.py',
          'tests/dialects/test_databricks.py',
          'tests/dialects/test_dialect.py',
          'tests/dialects/test_duckdb.py',
          'tests/dialects/test_exasol.py',
          'tests/dialects/test_fabric.py',
          'tests/dialects/test_hive.py',
          'tests/dialects/test_mysql.py',
          'tests/dialects/test_oracle.py',
          'tests/dialects/test_postgres.py',
          'tests/dialects/test_presto.py',
          'tests/dialects/test_redshift.py',
          'tests/dialects/test_singlestore.py',
          'tests/dialects/test_snowflake.py',
          'tests/dialects/test_sqlite.py',
          'tests/dialects/test_teradata.py',
          'tests/dialects/test_tsql.py')}
DIALECT_TEST_ID_MENTIONS: dict[str, tuple[str, ...]] = {'athena': ('tests/test_anonymize.py::TestAnonymize::test_athena_known_function_kept',
            'tests/test_dialect_imports.py::TestDialectImports::test_dialect_import_no_deadlock',
            'tests/test_generator.py::TestGenerator::test_unsupported_property_is_dropped',
            'tests/test_optimizer.py::TestOptimizer::test_annotate_funcs',
            'tests/test_optimizer.py::TestOptimizer::test_pushdown_predicates'),
 'bigquery': ('tests/test_build.py::TestBuild::test_build',
              'tests/test_executor.py::TestExecutor::test_integer_division_truncates_towards_zero',
              'tests/test_executor.py::TestExecutor::test_nested_values',
              'tests/test_expressions.py::TestExprs::test_data_type_builder',
              'tests/test_expressions.py::TestExprs::test_function_building',
              'tests/test_expressions.py::TestExprs::test_functions',
              'tests/test_expressions.py::TestExprs::test_ref_type_builder',
              'tests/test_expressions.py::TestExprs::test_replace_tables',
              'tests/test_expressions.py::TestExprs::test_replace_tables_keeps_function_arguments',
              'tests/test_expressions.py::TestExprs::test_table_name',
              'tests/test_generator.py::TestGenerator::test_time_slice_origin',
              'tests/test_lineage.py::TestLineage::test_unpivot_multi_column',
              'tests/test_optimizer.py::TestOptimizer::test_annotate_dot_parts_of_non_column_roots',
              'tests/test_optimizer.py::TestOptimizer::test_annotate_funcs',
              'tests/test_optimizer.py::TestOptimizer::test_annotate_row_types',
              'tests/test_optimizer.py::TestOptimizer::test_annotate_struct_subquery_values',
              'tests/test_optimizer.py::TestOptimizer::test_annotate_table_as_struct_bigquery',
              'tests/test_optimizer.py::TestOptimizer::test_annotate_types',
              'tests/test_optimizer.py::TestOptimizer::test_bigquery_unnest_alias_shadowing',
              'tests/test_optimizer.py::TestOptimizer::test_canonicalize_internal_names',
              'tests/test_optimizer.py::TestOptimizer::test_case_sensitive_json_dot_access',
              'tests/test_optimizer.py::TestOptimizer::test_correlated_subqueries_annotation',
              'tests/test_optimizer.py::TestOptimizer::test_correlated_unnest_projection_types',
              'tests/test_optimizer.py::TestOptimizer::test_expand_alias_refs',
              'tests/test_optimizer.py::TestOptimizer::test_function_annotation',
              'tests/test_optimizer.py::TestOptimizer::test_gen_bracket',
              'tests/test_optimizer.py::TestOptimizer::test_nested_type_annotation',
              'tests/test_optimizer.py::TestOptimizer::test_no_pseudocolumn_expansion',
              'tests/test_optimizer.py::TestOptimizer::test_normalize_identifiers',
              'tests/test_optimizer.py::TestOptimizer::test_null_coerce_annotation',
              'tests/test_optimizer.py::TestOptimizer::test_optimize',
              'tests/test_optimizer.py::TestOptimizer::test_pushdown_projection',
              'tests/test_optimizer.py::TestOptimizer::test_qualify_columns',
              'tests/test_optimizer.py::TestOptimizer::test_qualify_columns_struct_star_expansion_types',
              'tests/test_optimizer.py::TestOptimizer::test_qualify_group_by_conflict_bigquery',
              'tests/test_optimizer.py::TestOptimizer::test_qualify_order_by_expression_types',
              'tests/test_optimizer.py::TestOptimizer::test_qualify_order_by_subquery_types',
              'tests/test_optimizer.py::TestOptimizer::test_qualify_positional_references_types',
              'tests/test_optimizer.py::TestOptimizer::test_qualify_preserves_unsupported_struct_star_operands',
              'tests/test_optimizer.py::TestOptimizer::test_qualify_star_replace_types',
              'tests/test_optimizer.py::TestOptimizer::test_qualify_struct_star_json_ordering',
              'tests/test_optimizer.py::TestOptimizer::test_qualify_struct_star_source_types',
              'tests/test_optimizer.py::TestOptimizer::test_qualify_tables',
              'tests/test_optimizer.py::TestOptimizer::test_quote_identifiers',
              'tests/test_optimizer.py::TestOptimizer::test_reannotate_struct_subquery_values',
              'tests/test_optimizer.py::TestOptimizer::test_resolver_column_context',
              'tests/test_optimizer.py::TestOptimizer::test_scope',
              'tests/test_optimizer.py::TestOptimizer::test_set_operation_annotation',
              'tests/test_optimizer.py::TestOptimizer::test_simplify',
              'tests/test_optimizer.py::TestOptimizer::test_struct_annotation_bigquery',
              'tests/test_optimizer.py::TestOptimizer::test_struct_field_case_sensitivity_annotation',
              'tests/test_optimizer.py::TestOptimizer::test_union_annotation',
              'tests/test_optimizer.py::TestOptimizer::test_unnest_annotation',
              'tests/test_optimizer.py::TestOptimizer::test_unnest_offset_annotation',
              'tests/test_optimizer.py::TestOptimizer::test_unnest_struct_field_annotation',
              'tests/test_optimizer.py::TestOptimizer::test_unnest_type_trace_is_memoized',
              'tests/test_parser.py::TestParser::test_conditionless_join',
              'tests/test_parser.py::TestParser::test_parse_create_schema',
              'tests/test_parser.py::TestParser::test_parse_drop_schema',
              'tests/test_parser.py::TestParser::test_pivot_columns',
              'tests/test_parser.py::TestParser::test_ref_type',
              'tests/test_parser.py::TestParser::test_text_match_excludes_delimited_tokens',
              'tests/test_parser.py::TestParser::test_transactions',
              'tests/test_parser.py::TestParser::test_type_literals',
              'tests/test_parser.py::TestParser::test_values_as_identifier',
              'tests/test_parser.py::TestParser::test_window_clause_without_from',
              'tests/test_schema.py::TestSchema::test_cache_normalization',
              'tests/test_schema.py::TestSchema::test_schema_add_table_error_formatting',
              'tests/test_schema.py::TestSchema::test_schema_normalization',
              'tests/test_sqlglotc.py::TestSQLGlotC::test_build_removes_obsolete_extensions',
              'tests/test_sqlglotc.py::TestSQLGlotC::test_editable_install_removes_obsolete_extensions',
              'tests/test_tokens.py::TestTokens::test_jinja'),
 'clickhouse': ('tests/test_build.py::TestBuild::test_build',
                'tests/test_expressions.py::TestExprs::test_is_type',
                'tests/test_expressions.py::TestExprs::test_replace_tables_function_copy',
                'tests/test_expressions.py::TestExprs::test_replace_tables_keeps_function_arguments',
                'tests/test_optimizer.py::TestOptimizer::test_annotate_funcs',
                'tests/test_optimizer.py::TestOptimizer::test_case_sensitive_json_dot_access',
                'tests/test_optimizer.py::TestOptimizer::test_eliminate_positional_joins',
                'tests/test_optimizer.py::TestOptimizer::test_expand_alias_refs',
                'tests/test_optimizer.py::TestOptimizer::test_merge_any_join_inputs',
                'tests/test_optimizer.py::TestOptimizer::test_merge_asof_join_inputs',
                'tests/test_optimizer.py::TestOptimizer::test_merge_positional_join_inputs',
                'tests/test_optimizer.py::TestOptimizer::test_nonnull_annotation',
                'tests/test_optimizer.py::TestOptimizer::test_optimize_match_selecting_joins',
                'tests/test_optimizer.py::TestOptimizer::test_optimize_positional_joins',
                'tests/test_optimizer.py::TestOptimizer::test_pushdown_predicates',
                'tests/test_optimizer.py::TestOptimizer::test_qualify_columns',
                'tests/test_optimizer.py::TestOptimizer::test_resolver_column_context',
                'tests/test_optimizer.py::TestOptimizer::test_simplify',
                'tests/test_optimizer.py::TestOptimizer::test_struct_field_case_sensitivity_annotation',
                'tests/test_optimizer.py::TestOptimizer::test_unnest_positional_join_predicates',
                'tests/test_parser.py::TestParser::test_is_precedence',
                'tests/test_parser.py::TestParser::test_parse_errors',
                'tests/test_parser.py::TestParser::test_text_match_excludes_delimited_tokens',
                'tests/test_parser.py::TestParser::test_union',
                'tests/test_parser.py::TestParser::test_values_as_identifier',
                'tests/test_schema.py::TestSchema::test_schema_normalization',
                'tests/test_transpile.py::TestTranspile::test_sql_security',
                'tests/test_transpile.py::TestTranspile::test_types'),
 'databricks': ('tests/test_expressions.py::TestExprs::test_table_name',
                'tests/test_optimizer.py::TestOptimizer::test_annotate_funcs',
                'tests/test_optimizer.py::TestOptimizer::test_annotate_types',
                'tests/test_optimizer.py::TestOptimizer::test_case_sensitive_json_dot_access',
                'tests/test_optimizer.py::TestOptimizer::test_simplify',
                'tests/test_parser.py::TestParser::test_alter_set',
                'tests/test_parser.py::TestParser::test_is_precedence',
                'tests/test_parser.py::TestParser::test_parse_into_grant_principal',
                'tests/test_parser.py::TestParser::test_text_match_excludes_delimited_tokens'),
 'dax': ('tests/test_generator.py::TestGenerator::test_unsupported_slice',),
 'doris': ('tests/test_parser.py::TestParser::test_is_precedence',),
 'dremio': ('tests/test_parser.py::TestParser::test_is_precedence',),
 'drill': ('tests/test_parser.py::TestParser::test_is_precedence',),
 'duckdb': ('tests/test_executor.py::TestExecutor::test_array_slice',
            'tests/test_executor.py::TestExecutor::test_integer_division_truncates_towards_zero',
            'tests/test_executor.py::TestExecutor::test_nested_values',
            'tests/test_executor.py::TestExecutor::test_null_ordering_honors_nulls_first_and_dialect_defaults',
            'tests/test_executor.py::TestExecutor::test_optimized_tpch',
            'tests/test_expressions.py::TestExprs::test_data_type_builder',
            'tests/test_expressions.py::TestExprs::test_function_building',
            'tests/test_expressions.py::TestExprs::test_named_selects',
            'tests/test_expressions.py::TestExprs::test_named_selects_long_by_name_chain',
            'tests/test_expressions.py::TestExprs::test_named_selects_set_operations',
            'tests/test_expressions.py::TestExprs::test_replace_tables_builtin_parent',
            'tests/test_expressions.py::TestExprs::test_replace_tables_keeps_function_arguments',
            'tests/test_generator.py::TestGenerator::test_unsupported_slice',
            'tests/test_lineage.py::TestLineage::test_chained_pivots_consuming_alias_columns',
            'tests/test_lineage.py::TestLineage::test_lineage_normalize',
            'tests/test_lineage.py::TestLineage::test_pivot_with_subquery',
            'tests/test_optimizer.py::TestOptimizer::test_annotate_dot_parts_of_non_column_roots',
            'tests/test_optimizer.py::TestOptimizer::test_annotate_funcs',
            'tests/test_optimizer.py::TestOptimizer::test_annotate_json_dot_parts_preserve_meta',
            'tests/test_optimizer.py::TestOptimizer::test_annotate_row_types',
            'tests/test_optimizer.py::TestOptimizer::test_annotate_semi_structured_dot_parts',
            'tests/test_optimizer.py::TestOptimizer::test_annotate_struct_subquery_values',
            'tests/test_optimizer.py::TestOptimizer::test_annotate_types',
            'tests/test_optimizer.py::TestOptimizer::test_canonicalize_internal_names',
            'tests/test_optimizer.py::TestOptimizer::test_case_sensitive_json_dot_access',
            'tests/test_optimizer.py::TestOptimizer::test_eliminate_joins',
            'tests/test_optimizer.py::TestOptimizer::test_eliminate_positional_joins',
            'tests/test_optimizer.py::TestOptimizer::test_journal',
            'tests/test_optimizer.py::TestOptimizer::test_map_annotation',
            'tests/test_optimizer.py::TestOptimizer::test_merge_asof_join_inputs',
            'tests/test_optimizer.py::TestOptimizer::test_merge_positional_join_inputs',
            'tests/test_optimizer.py::TestOptimizer::test_merge_subqueries',
            'tests/test_optimizer.py::TestOptimizer::test_nearest_joins',
            'tests/test_optimizer.py::TestOptimizer::test_normalize_identifiers',
            'tests/test_optimizer.py::TestOptimizer::test_optimize',
            'tests/test_optimizer.py::TestOptimizer::test_optimize_correlated_all',
            'tests/test_optimizer.py::TestOptimizer::test_optimize_distinct_on_predicates',
            'tests/test_optimizer.py::TestOptimizer::test_optimize_in_nulls',
            'tests/test_optimizer.py::TestOptimizer::test_optimize_match_selecting_joins',
            'tests/test_optimizer.py::TestOptimizer::test_optimize_positional_joins',
            'tests/test_optimizer.py::TestOptimizer::test_optimize_scalar_subquery_key_predicates',
            'tests/test_optimizer.py::TestOptimizer::test_optimize_scalar_subquery_predicate_multiple_rows',
            'tests/test_optimizer.py::TestOptimizer::test_optimize_scalar_subquery_predicates',
            'tests/test_optimizer.py::TestOptimizer::test_optimize_snowflake_asof_without_on',
            'tests/test_optimizer.py::TestOptimizer::test_optimize_special_joins',
            'tests/test_optimizer.py::TestOptimizer::test_optimize_subqueries_in_aggregates',
            'tests/test_optimizer.py::TestOptimizer::test_optimize_subqueries_in_join_on',
            'tests/test_optimizer.py::TestOptimizer::test_optimize_uncorrelated_exists',
            'tests/test_optimizer.py::TestOptimizer::test_pushdown_predicates',
            'tests/test_optimizer.py::TestOptimizer::test_qualify_columns',
            'tests/test_optimizer.py::TestOptimizer::test_qualify_columns__invalid',
            'tests/test_optimizer.py::TestOptimizer::test_qualify_columns_using_after_outer_join',
            'tests/test_optimizer.py::TestOptimizer::test_qualify_correlated_shadowed_struct_path',
            'tests/test_optimizer.py::TestOptimizer::test_qualify_duckdb_collated_alias',
            'tests/test_optimizer.py::TestOptimizer::test_qualify_group_by_conflict_bigquery',
            'tests/test_optimizer.py::TestOptimizer::test_qualify_json_path_argument_order',
            'tests/test_optimizer.py::TestOptimizer::test_qualify_order_by_alias_fallback',
            'tests/test_optimizer.py::TestOptimizer::test_qualify_order_by_alias_in_subquery',
            'tests/test_optimizer.py::TestOptimizer::test_qualify_order_by_alias_nodes',
            'tests/test_optimizer.py::TestOptimizer::test_qualify_order_by_ambiguous_alias',
            'tests/test_optimizer.py::TestOptimizer::test_qualify_order_by_source_columns',
            'tests/test_optimizer.py::TestOptimizer::test_qualify_order_by_using_column',
            'tests/test_optimizer.py::TestOptimizer::test_qualify_order_by_using_column_collision',
            'tests/test_optimizer.py::TestOptimizer::test_qualify_parquet_alias_collision',
            'tests/test_optimizer.py::TestOptimizer::test_qualify_semi_anti_join_alias_shadowing_column',
            'tests/test_optimizer.py::TestOptimizer::test_qualify_semi_anti_join_column_aliases',
            'tests/test_optimizer.py::TestOptimizer::test_qualify_struct_star_json_ordering',
            'tests/test_optimizer.py::TestOptimizer::test_qualify_struct_star_source_types',
            'tests/test_optimizer.py::TestOptimizer::test_qualify_unknown_tvf_table_reference',
            'tests/test_optimizer.py::TestOptimizer::test_resolver_column_context',
            'tests/test_optimizer.py::TestOptimizer::test_scope',
            'tests/test_optimizer.py::TestOptimizer::test_simplify',
            'tests/test_optimizer.py::TestOptimizer::test_simplify_string_comparisons',
            'tests/test_optimizer.py::TestOptimizer::test_union_annotation',
            'tests/test_optimizer.py::TestOptimizer::test_unnest_offset_annotation',
            'tests/test_optimizer.py::TestOptimizer::test_unnest_positional_join_predicates',
            'tests/test_optimizer.py::TestOptimizer::test_unpivot_cte_annotate_types',
            'tests/test_optimizer.py::TestOptimizer::test_values_annotation',
            'tests/test_parser.py::TestParser::test_is_precedence',
            'tests/test_parser.py::TestParser::test_large_exponent_array_index',
            'tests/test_parser.py::TestParser::test_parse_drop_schema',
            'tests/test_parser.py::TestParser::test_pivot_columns',
            'tests/test_parser.py::TestParser::test_ref_type',
            'tests/test_parser.py::TestParser::test_text_match_excludes_delimited_tokens',
            'tests/test_parser.py::TestParser::test_values_as_identifier',
            'tests/test_parser.py::TestParser::test_window_clause_without_from',
            'tests/test_schema.py::TestSchema::test_cache_does_not_retain_queries',
            'tests/test_sqlglotc.py::TestSQLGlotC::test_build_removes_obsolete_extensions',
            'tests/test_sqlglotc.py::TestSQLGlotC::test_editable_install_removes_obsolete_extensions',
            'tests/test_transpile.py::TestTranspile::test_time'),
 'exasol': ('tests/test_generator.py::TestGenerator::test_unsupported_slice',
            'tests/test_parser.py::TestParser::test_text_match_excludes_delimited_tokens'),
 'fabric': ('tests/test_generator.py::TestGenerator::test_unsupported_slice',),
 'hive': ('tests/test_build.py::TestBuild::test_build',
          'tests/test_executor.py::TestExecutor::test_first',
          'tests/test_expressions.py::TestExprs::test_cast',
          'tests/test_expressions.py::TestExprs::test_eq',
          'tests/test_expressions.py::TestExprs::test_function_building',
          'tests/test_expressions.py::TestExprs::test_sql',
          'tests/test_optimizer.py::TestOptimizer::test_annotate_funcs',
          'tests/test_optimizer.py::TestOptimizer::test_scope',
          'tests/test_parser.py::TestParser::test_alter_set',
          'tests/test_parser.py::TestParser::test_command',
          'tests/test_parser.py::TestParser::test_is_precedence',
          'tests/test_transpile.py::TestTranspile::test_time',
          'tests/test_transpile.py::TestTranspile::test_unsupported_level'),
 'materialize': ('tests/test_parser.py::TestParser::test_is_precedence',),
 'mysql': ('tests/test_anonymize.py::TestAnonymize::test_quotes_preserved',
           'tests/test_anonymize.py::TestAnonymize::test_render',
           'tests/test_anonymize.py::TestAnonymize::test_render_without_dialect_still_redacts',
           'tests/test_anonymize.py::TestAnonymize::test_tokenize_error_keeps_delimiter',
           'tests/test_build.py::TestBuild::test_build',
           'tests/test_executor.py::TestExecutor::test_integer_division_floating_point',
           'tests/test_executor.py::TestExecutor::test_integer_division_truncates_towards_zero',
           'tests/test_executor.py::TestExecutor::test_null_ordering_honors_nulls_first_and_dialect_defaults',
           'tests/test_expressions.py::TestExprs::test_eq_case_sensitive_text',
           'tests/test_expressions.py::TestExprs::test_function_building',
           'tests/test_generator.py::TestGenerator::test_time_slice_origin',
           'tests/test_optimize_cross_joins.py::TestOptimizeCrossJoins::test_conditional_cross_joins',
           'tests/test_optimizer.py::TestOptimizer::test_annotate_funcs',
           'tests/test_optimizer.py::TestOptimizer::test_canonicalize',
           'tests/test_optimizer.py::TestOptimizer::test_optimize',
           'tests/test_optimizer.py::TestOptimizer::test_qualify_columns',
           'tests/test_optimizer.py::TestOptimizer::test_simplify',
           'tests/test_optimizer.py::TestOptimizer::test_typeddiv_annotation',
           'tests/test_parser.py::TestParser::test_describe_followed_by_dot',
           'tests/test_parser.py::TestParser::test_is_precedence',
           'tests/test_parser.py::TestParser::test_text_match_excludes_delimited_tokens',
           'tests/test_parser.py::TestParser::test_transactions',
           'tests/test_parser.py::TestParser::test_window_clause_without_from',
           'tests/test_sqlglotc.py::TestSQLGlotC::test_build_removes_obsolete_extensions',
           'tests/test_sqlglotc.py::TestSQLGlotC::test_editable_install_removes_obsolete_extensions',
           'tests/test_transforms.py::TestTransforms::test_remove_unique_constraints',
           'tests/test_transpile.py::TestTranspile::test_comments',
           'tests/test_transpile.py::TestTranspile::test_sql_security'),
 'oracle': ('tests/test_anonymize.py::TestAnonymize::test_hints_anonymized',
            'tests/test_anonymize.py::TestAnonymize::test_quotes_preserved',
            'tests/test_diff.py::TestDiff::test_dialect_aware_diff',
            'tests/test_diff.py::TestDiff::test_window_functions',
            'tests/test_executor.py::TestExecutor::test_scalar_functions',
            'tests/test_lineage.py::TestLineage::test_ddl_lineage',
            'tests/test_optimizer.py::TestOptimizer::test_qualify_columns',
            'tests/test_parser.py::TestParser::test_is_precedence',
            'tests/test_parser.py::TestParser::test_text_match_excludes_delimited_tokens',
            'tests/test_transforms.py::TestTransforms::test_eliminate_join_marks'),
 'postgres': ('tests/test_anonymize.py::TestAnonymize::test_quotes_preserved',
              'tests/test_anonymize.py::TestAnonymize::test_tokenize_error_keeps_delimiter',
              'tests/test_build.py::TestBuild::test_build',
              'tests/test_executor.py::TestExecutor::test_dpipe_source_dialect_coercion',
              'tests/test_executor.py::TestExecutor::test_null_ordering_honors_nulls_first_and_dialect_defaults',
              'tests/test_executor.py::TestExecutor::test_typed_division',
              'tests/test_executor.py::TestExecutor::test_typed_division_of_null_is_null',
              'tests/test_expressions.py::TestExprs::test_cast',
              'tests/test_expressions.py::TestExprs::test_data_type_builder',
              'tests/test_expressions.py::TestExprs::test_eq_case_sensitive_text',
              'tests/test_expressions.py::TestExprs::test_replace_tables_keeps_function_arguments',
              'tests/test_generator.py::TestGenerator::test_overlap_operator',
              'tests/test_generator.py::TestGenerator::test_time_slice_origin',
              'tests/test_generator.py::TestGenerator::test_unsupported_property_is_dropped',
              'tests/test_optimizer.py::TestOptimizer::test_annotate_funcs',
              'tests/test_optimizer.py::TestOptimizer::test_canonicalize',
              'tests/test_optimizer.py::TestOptimizer::test_canonicalize_internal_names',
              'tests/test_optimizer.py::TestOptimizer::test_nested_type_annotation',
              'tests/test_optimizer.py::TestOptimizer::test_normalize_identifiers',
              'tests/test_optimizer.py::TestOptimizer::test_optimize',
              'tests/test_optimizer.py::TestOptimizer::test_optimize_distinct_on_predicates',
              'tests/test_optimizer.py::TestOptimizer::test_pushdown_projection',
              'tests/test_optimizer.py::TestOptimizer::test_pushdown_projections_keeps_recursive_cte_self_referenced_columns',
              'tests/test_optimizer.py::TestOptimizer::test_pushdown_projections_prunes_non_self_referencing_ctes',
              'tests/test_optimizer.py::TestOptimizer::test_qualify_columns',
              'tests/test_optimizer.py::TestOptimizer::test_qualify_order_by_alias_in_subquery',
              'tests/test_optimizer.py::TestOptimizer::test_qualify_order_by_ambiguous_alias',
              'tests/test_optimizer.py::TestOptimizer::test_qualify_order_by_source_columns',
              'tests/test_optimizer.py::TestOptimizer::test_qualify_order_by_using_column',
              'tests/test_optimizer.py::TestOptimizer::test_qualify_order_by_using_column_collision',
              'tests/test_optimizer.py::TestOptimizer::test_qualify_positional_columns_is_snowflake_only',
              'tests/test_optimizer.py::TestOptimizer::test_qualify_tables',
              'tests/test_optimizer.py::TestOptimizer::test_simplify',
              'tests/test_optimizer.py::TestOptimizer::test_user_defined_type_annotation',
              'tests/test_optimizer.py::TestOptimizer::test_values_annotation',
              'tests/test_parser.py::TestParser::test_copy_command_fallback',
              'tests/test_parser.py::TestParser::test_is_precedence',
              'tests/test_parser.py::TestParser::test_large_exponent_array_index',
              'tests/test_parser.py::TestParser::test_no_paren_functions_after_dot',
              'tests/test_parser.py::TestParser::test_parse_intervals',
              'tests/test_parser.py::TestParser::test_text_match_excludes_delimited_tokens',
              'tests/test_parser.py::TestParser::test_values_as_identifier',
              'tests/test_parser.py::TestParser::test_window_clause_without_from',
              'tests/test_schema.py::TestSchema::test_cache_normalization',
              'tests/test_schema.py::TestSchema::test_schema_add_table_error_formatting',
              'tests/test_simplify_between.py::TestSimplifyBetween::test_symmetric_constant_bounds',
              'tests/test_simplify_between.py::TestSimplifyBetween::test_symmetric_truth_values_and_grouping',
              'tests/test_sqlglotc.py::TestSQLGlotC::test_build_removes_obsolete_extensions',
              'tests/test_sqlglotc.py::TestSQLGlotC::test_editable_install_removes_obsolete_extensions'),
 'presto': ('tests/test_expressions.py::TestExprs::test_transform_simple',
            'tests/test_expressions.py::TestExprs::test_transform_with_arguments',
            'tests/test_optimizer.py::TestOptimizer::test_annotate_funcs',
            'tests/test_optimizer.py::TestOptimizer::test_annotate_types',
            'tests/test_optimizer.py::TestOptimizer::test_pushdown_predicates',
            'tests/test_optimizer.py::TestOptimizer::test_qualify_columns',
            'tests/test_optimizer.py::TestOptimizer::test_simplify',
            'tests/test_optimizer.py::TestOptimizer::test_typeddiv_annotation',
            'tests/test_parser.py::TestParser::test_transactions',
            'tests/test_transpile.py::TestTranspile::test_index_offset',
            'tests/test_transpile.py::TestTranspile::test_normalize_name',
            'tests/test_transpile.py::TestTranspile::test_sql_security',
            'tests/test_transpile.py::TestTranspile::test_time',
            'tests/test_transpile.py::TestTranspile::test_unsupported_level',
            'tests/test_transpile.py::TestTranspile::test_with'),
 'redshift': ('tests/test_expressions.py::TestExprs::test_data_type_builder',
              'tests/test_optimizer.py::TestOptimizer::test_annotate_funcs',
              'tests/test_optimizer.py::TestOptimizer::test_normalize_identifiers',
              'tests/test_optimizer.py::TestOptimizer::test_qualify_columns',
              'tests/test_optimizer.py::TestOptimizer::test_qualify_tables',
              'tests/test_optimizer.py::TestOptimizer::test_simplify',
              'tests/test_parser.py::TestParser::test_is_precedence',
              'tests/test_parser.py::TestParser::test_pivot_columns',
              'tests/test_parser.py::TestParser::test_text_match_excludes_delimited_tokens',
              'tests/test_parser.py::TestParser::test_values_as_identifier',
              'tests/test_transforms.py::TestTransforms::test_eliminate_join_marks'),
 'risingwave': ('tests/test_optimizer.py::TestOptimizer::test_qualify_columns',
                'tests/test_optimizer.py::TestOptimizer::test_qualify_skips_unneeded_annotation',
                'tests/test_optimizer.py::TestOptimizer::test_qualify_struct_star_long_cte_chain',
                'tests/test_optimizer.py::TestOptimizer::test_qualify_struct_star_source_types',
                'tests/test_parser.py::TestParser::test_is_precedence'),
 'singlestore': ('tests/test_expressions.py::TestExprs::test_eq_case_sensitive_text',),
 'snowflake': ('tests/test_anonymize.py::TestAnonymize::test_known_function_kept_when_passing_tokens',
               'tests/test_anonymize.py::TestAnonymize::test_quotes_preserved',
               'tests/test_anonymize.py::TestAnonymize::test_render',
               'tests/test_anonymize.py::TestAnonymize::test_string_family_forms_anonymized',
               'tests/test_dialect_imports.py::TestDialectImports::test_dialect_attribute_access_is_cached',
               'tests/test_executor.py::TestExecutor::test_nested_values',
               'tests/test_expressions.py::TestExprs::test_alias_with_placeholder',
               'tests/test_expressions.py::TestExprs::test_column_expression',
               'tests/test_expressions.py::TestExprs::test_data_type_builder',
               'tests/test_expressions.py::TestExprs::test_ref_type_builder',
               'tests/test_expressions.py::TestExprs::test_table_name',
               'tests/test_expressions.py::TestExprs::test_to_dot',
               'tests/test_generator.py::TestGenerator::test_fallback_function_sql',
               'tests/test_generator.py::TestGenerator::test_fallback_function_var_args_sql',
               'tests/test_lineage.py::TestLineage::test_chained_pivots',
               'tests/test_lineage.py::TestLineage::test_chained_pivots_mixed',
               'tests/test_lineage.py::TestLineage::test_chained_pivots_through_cte',
               'tests/test_lineage.py::TestLineage::test_chained_pivots_with_alias_columns',
               'tests/test_lineage.py::TestLineage::test_lineage_lateral_flatten',
               'tests/test_lineage.py::TestLineage::test_lineage_normalize',
               'tests/test_lineage.py::TestLineage::test_multiple_pivoted_sources',
               'tests/test_lineage.py::TestLineage::test_table_udtf_snowflake',
               'tests/test_lineage.py::TestLineage::test_unpivot',
               'tests/test_lineage.py::TestLineage::test_unpivot_with_alias_columns',
               'tests/test_lineage.py::TestLineage::test_unpivot_with_cte',
               'tests/test_optimizer.py::TestOptimizer::test_annotate_funcs',
               'tests/test_optimizer.py::TestOptimizer::test_annotate_json_dot_parts_preserve_meta',
               'tests/test_optimizer.py::TestOptimizer::test_annotate_object_construct',
               'tests/test_optimizer.py::TestOptimizer::test_annotate_types',
               'tests/test_optimizer.py::TestOptimizer::test_canonicalize_internal_names',
               'tests/test_optimizer.py::TestOptimizer::test_case_sensitive_json_dot_access',
               'tests/test_optimizer.py::TestOptimizer::test_gen',
               'tests/test_optimizer.py::TestOptimizer::test_manually_annotate_snowflake',
               'tests/test_optimizer.py::TestOptimizer::test_merge_asof_join_inputs',
               'tests/test_optimizer.py::TestOptimizer::test_multiple_pivots_annotate_types',
               'tests/test_optimizer.py::TestOptimizer::test_normalize',
               'tests/test_optimizer.py::TestOptimizer::test_normalize_identifiers',
               'tests/test_optimizer.py::TestOptimizer::test_optimize',
               'tests/test_optimizer.py::TestOptimizer::test_optimize_snowflake_asof_without_on',
               'tests/test_optimizer.py::TestOptimizer::test_pushdown_predicates',
               'tests/test_optimizer.py::TestOptimizer::test_pushdown_projection',
               'tests/test_optimizer.py::TestOptimizer::test_qualify_columns',
               'tests/test_optimizer.py::TestOptimizer::test_qualify_snowflake_positional_column_out_of_range',
               'tests/test_optimizer.py::TestOptimizer::test_qualify_snowflake_positional_column_with_visible_schema',
               'tests/test_optimizer.py::TestOptimizer::test_qualify_star_rename_quoted',
               'tests/test_optimizer.py::TestOptimizer::test_qualify_star_replace_rename_unexpanded',
               'tests/test_optimizer.py::TestOptimizer::test_qualify_tables',
               'tests/test_optimizer.py::TestOptimizer::test_quote_identifiers',
               'tests/test_optimizer.py::TestOptimizer::test_quotes',
               'tests/test_optimizer.py::TestOptimizer::test_semistructured',
               'tests/test_optimizer.py::TestOptimizer::test_simplify',
               'tests/test_optimizer.py::TestOptimizer::test_udtf_annotation',
               'tests/test_optimizer.py::TestOptimizer::test_unknown_pivot_columns',
               'tests/test_optimizer.py::TestOptimizer::test_validate_columns',
               'tests/test_parser.py::TestParser::test_describe_followed_by_dot',
               'tests/test_parser.py::TestParser::test_is_precedence',
               'tests/test_parser.py::TestParser::test_parse_create_schema',
               'tests/test_parser.py::TestParser::test_parse_drop_schema',
               'tests/test_parser.py::TestParser::test_parse_errors',
               'tests/test_parser.py::TestParser::test_pivot_columns',
               'tests/test_parser.py::TestParser::test_ref_type',
               'tests/test_parser.py::TestParser::test_text_match_excludes_delimited_tokens',
               'tests/test_parser.py::TestParser::test_values_as_identifier',
               'tests/test_schema.py::TestSchema::test_cache_normalization',
               'tests/test_schema.py::TestSchema::test_schema_add_table_error_formatting',
               'tests/test_schema.py::TestSchema::test_schema_normalization',
               'tests/test_sqlglotc.py::TestSQLGlotC::test_build_removes_obsolete_extensions',
               'tests/test_sqlglotc.py::TestSQLGlotC::test_editable_install_removes_obsolete_extensions',
               'tests/test_transpile.py::TestTranspile::test_comments'),
 'spark': ('tests/test_anonymize.py::TestAnonymize::test_hints_anonymized',
           'tests/test_build.py::TestBuild::test_build',
           'tests/test_expressions.py::TestExprs::test_convert',
           'tests/test_expressions.py::TestExprs::test_data_type_builder',
           'tests/test_expressions.py::TestExprs::test_expand',
           'tests/test_expressions.py::TestExprs::test_expand_with_lazy_source_provider',
           'tests/test_expressions.py::TestExprs::test_functions',
           'tests/test_expressions.py::TestExprs::test_to_column',
           'tests/test_optimizer.py::TestOptimizer::test_annotate_funcs',
           'tests/test_optimizer.py::TestOptimizer::test_annotate_types',
           'tests/test_optimizer.py::TestOptimizer::test_bracket_annotation',
           'tests/test_optimizer.py::TestOptimizer::test_canonicalize_internal_names',
           'tests/test_optimizer.py::TestOptimizer::test_map_annotation',
           'tests/test_optimizer.py::TestOptimizer::test_merge_subqueries',
           'tests/test_optimizer.py::TestOptimizer::test_normalize_identifiers',
           'tests/test_optimizer.py::TestOptimizer::test_optimize',
           'tests/test_optimizer.py::TestOptimizer::test_qualify_columns',
           'tests/test_optimizer.py::TestOptimizer::test_qualify_tables',
           'tests/test_optimizer.py::TestOptimizer::test_simplify',
           'tests/test_optimizer.py::TestOptimizer::test_validate_columns',
           'tests/test_parser.py::TestParser::test_alter_set',
           'tests/test_parser.py::TestParser::test_describe_followed_by_dot',
           'tests/test_parser.py::TestParser::test_is_precedence',
           'tests/test_parser.py::TestParser::test_pivot_columns',
           'tests/test_parser.py::TestParser::test_text_match_excludes_delimited_tokens',
           'tests/test_sqlglotc.py::TestSQLGlotC::test_build_removes_obsolete_extensions',
           'tests/test_sqlglotc.py::TestSQLGlotC::test_editable_install_removes_obsolete_extensions',
           'tests/test_transpile.py::TestTranspile::test_time'),
 'spark2': ('tests/test_optimizer.py::TestOptimizer::test_annotate_funcs',
            'tests/test_parser.py::TestParser::test_alter_set',
            'tests/test_parser.py::TestParser::test_is_precedence',
            'tests/test_transpile.py::TestTranspile::test_time'),
 'sqlite': ('tests/test_executor.py::TestExecutor::test_typed_division',
            'tests/test_executor.py::TestExecutor::test_typed_division_of_null_is_null',
            'tests/test_expressions.py::TestExprs::test_function_building',
            'tests/test_generator.py::TestGenerator::test_unsupported_slice',
            'tests/test_optimize_cross_joins.py::TestOptimizeCrossJoins::test_conditional_cross_joins',
            'tests/test_optimizer.py::TestOptimizer::test_canonicalize',
            'tests/test_optimizer.py::TestOptimizer::test_merge_subqueries_match',
            'tests/test_optimizer.py::TestOptimizer::test_normalize_identifiers',
            'tests/test_optimizer.py::TestOptimizer::test_simplify',
            'tests/test_parser.py::TestParser::test_is_precedence',
            'tests/test_parser.py::TestParser::test_parse_into_error',
            'tests/test_parser.py::TestParser::test_parse_into_errors',
            'tests/test_parser.py::TestParser::test_window_clause_without_from',
            'tests/test_simplify_between.py::TestSimplifyBetween::test_symmetric_truth_values_and_grouping'),
 'starrocks': ('tests/test_generator.py::TestGenerator::test_time_slice_origin',
               'tests/test_lineage.py::TestLineage::test_table_udtfs',
               'tests/test_optimizer.py::TestOptimizer::test_qualify_columns',
               'tests/test_optimizer.py::TestOptimizer::test_qualify_tables',
               'tests/test_optimizer.py::TestOptimizer::test_starrocks_pivot_columns',
               'tests/test_optimizer.py::TestOptimizer::test_unknown_pivot_columns',
               'tests/test_parser.py::TestParser::test_is_precedence',
               'tests/test_parser.py::TestParser::test_pivot_columns',
               'tests/test_transpile.py::TestTranspile::test_sql_security'),
 'tableau': ('tests/test_generator.py::TestGenerator::test_unsupported_slice',),
 'teradata': ('tests/test_lineage.py::TestLineage::test_lineage_normalize',
              'tests/test_optimizer.py::TestOptimizer::test_normalize_identifiers'),
 'trino': ('tests/test_executor.py::TestExecutor::test_dpipe_source_dialect_coercion',
           'tests/test_optimizer.py::TestOptimizer::test_annotate_funcs',
           'tests/test_optimizer.py::TestOptimizer::test_normalize_identifiers',
           'tests/test_optimizer.py::TestOptimizer::test_pushdown_predicates',
           'tests/test_optimizer.py::TestOptimizer::test_qualify_columns',
           'tests/test_parser.py::TestParser::test_ref_type',
           'tests/test_parser.py::TestParser::test_text_match_excludes_delimited_tokens',
           'tests/test_transpile.py::TestTranspile::test_sql_security'),
 'tsql': ('tests/test_anonymize.py::TestAnonymize::test_quotes_preserved',
          'tests/test_expressions.py::TestExprs::test_ref_type_builder',
          'tests/test_generator.py::TestGenerator::test_unsupported_slice',
          'tests/test_optimizer.py::TestOptimizer::test_annotate_funcs',
          'tests/test_optimizer.py::TestOptimizer::test_canonicalize',
          'tests/test_optimizer.py::TestOptimizer::test_simplify',
          'tests/test_parser.py::TestParser::test_ref_type',
          'tests/test_parser.py::TestParser::test_text_match_excludes_delimited_tokens',
          'tests/test_schema.py::TestSchema::test_schema_add_table_error_formatting',
          'tests/test_schema.py::TestSchema::test_schema_normalization')}
ALL_DIALECT_TEST_IDS: tuple[str, ...] = ('tests/test_generator.py::TestGenerator::test_generator_memory',
 'tests/test_time.py::TestTime::test_canonical_time_format_vocabulary')
FIXTURE_TEST_IDS: dict[str, tuple[str, ...]] = {'tests/fixtures/identity.sql': ('tests/test_serde.py::TestSerde::test_serde',
                                 'tests/test_transpile.py::TestTranspile::test_identity'),
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
 'tests/fixtures/optimizer/optimizer.sql': ('tests/test_optimizer.py::TestOptimizer::test_optimize',
                                            'tests/test_sqlglotc.py::TestSQLGlotC::test_build_removes_obsolete_extensions',
                                            'tests/test_sqlglotc.py::TestSQLGlotC::test_editable_install_removes_obsolete_extensions'),
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
 'tests/fixtures/pretty.sql': ('tests/test_transpile.py::TestTranspile::test_pretty',)}
FIXTURE_SETUP_FILES: dict[str, tuple[str, ...]] = {'tests/fixtures/optimizer/tpc-ds/call_center.csv.gz': ('tests/test_executor.py',),
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
 'tests/fixtures/optimizer/tpc-h/tpc-h.sql': ('tests/test_executor.py',)}
MODULE_TEST_IMPORTERS: dict[str, tuple[str, ...]] = {'sqlglot/__init__.py': ('tests/dialects/test_athena.py',
                         'tests/dialects/test_bigquery.py',
                         'tests/dialects/test_clickhouse.py',
                         'tests/dialects/test_databricks.py',
                         'tests/dialects/test_dialect.py',
                         'tests/dialects/test_doris.py',
                         'tests/dialects/test_dremio.py',
                         'tests/dialects/test_duckdb.py',
                         'tests/dialects/test_dune.py',
                         'tests/dialects/test_exasol.py',
                         'tests/dialects/test_hive.py',
                         'tests/dialects/test_mysql.py',
                         'tests/dialects/test_oracle.py',
                         'tests/dialects/test_postgres.py',
                         'tests/dialects/test_presto.py',
                         'tests/dialects/test_redshift.py',
                         'tests/dialects/test_singlestore.py',
                         'tests/dialects/test_snowflake.py',
                         'tests/dialects/test_solr.py',
                         'tests/dialects/test_spark.py',
                         'tests/dialects/test_sqlite.py',
                         'tests/dialects/test_starrocks.py',
                         'tests/dialects/test_teradata.py',
                         'tests/dialects/test_trino.py',
                         'tests/dialects/test_tsql.py',
                         'tests/test_build.py',
                         'tests/test_dialect_entry_points.py',
                         'tests/test_diff.py',
                         'tests/test_executor.py',
                         'tests/test_expressions.py',
                         'tests/test_generator.py',
                         'tests/test_jsonpath.py',
                         'tests/test_lineage.py',
                         'tests/test_optimize_cross_joins.py',
                         'tests/test_optimizer.py',
                         'tests/test_parser.py',
                         'tests/test_schema.py',
                         'tests/test_serde.py',
                         'tests/test_simplify_between.py',
                         'tests/test_transforms.py',
                         'tests/test_transpile.py'),
 'sqlglot/anonymize.py': ('tests/test_anonymize.py',),
 'sqlglot/dialects/__init__.py': ('tests/dialects/test_bigquery.py',
                                  'tests/dialects/test_clickhouse.py',
                                  'tests/dialects/test_dialect.py',
                                  'tests/test_dialect_imports.py',
                                  'tests/test_generator.py',
                                  'tests/test_tokens.py'),
 'sqlglot/dialects/dialect.py': ('tests/dialects/test_bigquery.py',
                                 'tests/dialects/test_druid.py',
                                 'tests/dialects/test_spark.py',
                                 'tests/test_dialect_entry_points.py',
                                 'tests/test_time.py'),
 'sqlglot/dialects/duckdb.py': ('tests/dialects/test_dialect.py', 'tests/dialects/test_duckdb.py'),
 'sqlglot/dialects/exasol.py': ('tests/dialects/test_exasol.py',),
 'sqlglot/dialects/mysql.py': ('tests/dialects/test_mysql.py',),
 'sqlglot/diff.py': ('tests/test_diff.py',),
 'sqlglot/errors.py': ('tests/dialects/test_athena.py',
                       'tests/dialects/test_clickhouse.py',
                       'tests/dialects/test_databricks.py',
                       'tests/dialects/test_spark.py',
                       'tests/dialects/test_starrocks.py',
                       'tests/dialects/test_tsql.py',
                       'tests/test_errors.py',
                       'tests/test_executor.py',
                       'tests/test_generator.py',
                       'tests/test_jsonpath.py',
                       'tests/test_optimizer.py',
                       'tests/test_parser.py',
                       'tests/test_schema.py',
                       'tests/test_tokens.py',
                       'tests/test_transpile.py'),
 'sqlglot/executor/__init__.py': ('tests/test_executor.py',),
 'sqlglot/executor/env.py': ('tests/test_executor.py',),
 'sqlglot/executor/python.py': ('tests/test_executor.py',),
 'sqlglot/executor/table.py': ('tests/test_executor.py',),
 'sqlglot/expressions/__init__.py': ('tests/dialects/test_clickhouse.py',
                                     'tests/dialects/test_mysql.py',
                                     'tests/test_generator.py',
                                     'tests/test_transforms.py'),
 'sqlglot/expressions/core.py': ('tests/test_generator.py', 'tests/test_serde.py'),
 'sqlglot/generator.py': ('tests/dialects/test_bigquery.py',
                          'tests/dialects/test_dialect.py',
                          'tests/dialects/test_duckdb.py',
                          'tests/dialects/test_postgres.py',
                          'tests/dialects/test_presto.py',
                          'tests/dialects/test_sqlite.py',
                          'tests/dialects/test_trino.py',
                          'tests/test_diff.py',
                          'tests/test_generator.py'),
 'sqlglot/generators/hive.py': ('tests/test_time.py',),
 'sqlglot/helper.py': ('tests/dialects/test_bigquery.py',
                       'tests/dialects/test_clickhouse.py',
                       'tests/dialects/test_duckdb.py',
                       'tests/dialects/test_postgres.py',
                       'tests/dialects/test_presto.py',
                       'tests/dialects/test_sqlite.py',
                       'tests/test_helper.py',
                       'tests/test_transpile.py'),
 'sqlglot/jsonpath.py': ('tests/test_jsonpath.py',),
 'sqlglot/lineage.py': ('tests/dialects/test_teradata.py', 'tests/test_lineage.py'),
 'sqlglot/optimizer/__init__.py': ('tests/dialects/test_clickhouse.py',
                                   'tests/dialects/test_postgres.py',
                                   'tests/test_executor.py',
                                   'tests/test_lineage.py',
                                   'tests/test_optimize_cross_joins.py',
                                   'tests/test_optimizer.py',
                                   'tests/test_simplify_between.py'),
 'sqlglot/optimizer/annotate_types.py': ('tests/dialects/test_bigquery.py',
                                         'tests/dialects/test_clickhouse.py',
                                         'tests/dialects/test_dremio.py',
                                         'tests/dialects/test_duckdb.py',
                                         'tests/dialects/test_mysql.py',
                                         'tests/dialects/test_postgres.py',
                                         'tests/dialects/test_presto.py',
                                         'tests/dialects/test_redshift.py',
                                         'tests/dialects/test_snowflake.py',
                                         'tests/dialects/test_spark.py',
                                         'tests/dialects/test_sqlite.py',
                                         'tests/dialects/test_teradata.py',
                                         'tests/dialects/test_trino.py',
                                         'tests/dialects/test_tsql.py',
                                         'tests/test_optimizer.py',
                                         'tests/test_schema.py',
                                         'tests/test_serde.py'),
 'sqlglot/optimizer/canonicalize_internal_names.py': ('tests/test_optimizer.py',),
 'sqlglot/optimizer/journal.py': ('tests/test_optimizer.py',),
 'sqlglot/optimizer/normalize.py': ('tests/test_optimizer.py',),
 'sqlglot/optimizer/normalize_identifiers.py': ('tests/dialects/test_snowflake.py',),
 'sqlglot/optimizer/optimize_joins.py': ('tests/test_optimize_cross_joins.py',),
 'sqlglot/optimizer/qualify.py': ('tests/dialects/test_athena.py',
                                  'tests/dialects/test_bigquery.py',
                                  'tests/dialects/test_clickhouse.py',
                                  'tests/dialects/test_oracle.py',
                                  'tests/dialects/test_singlestore.py',
                                  'tests/dialects/test_snowflake.py',
                                  'tests/dialects/test_spark.py',
                                  'tests/dialects/test_teradata.py',
                                  'tests/dialects/test_tsql.py',
                                  'tests/test_lineage.py',
                                  'tests/test_optimize_cross_joins.py',
                                  'tests/test_optimizer.py',
                                  'tests/test_schema.py'),
 'sqlglot/optimizer/qualify_columns.py': ('tests/dialects/test_clickhouse.py',
                                          'tests/dialects/test_snowflake.py'),
 'sqlglot/optimizer/resolver.py': ('tests/test_optimizer.py',),
 'sqlglot/optimizer/scope.py': ('tests/test_lineage.py',
                                'tests/test_optimizer.py',
                                'tests/test_schema.py'),
 'sqlglot/optimizer/simplify.py': ('tests/dialects/test_postgres.py',
                                   'tests/test_simplify_between.py'),
 'sqlglot/parser.py': ('tests/dialects/test_bigquery.py',
                       'tests/dialects/test_dialect.py',
                       'tests/dialects/test_snowflake.py',
                       'tests/test_expressions.py',
                       'tests/test_parser.py',
                       'tests/test_transpile.py'),
 'sqlglot/parsers/base.py': ('tests/dialects/test_dialect.py',),
 'sqlglot/parsers/snowflake.py': ('tests/dialects/test_dialect.py',
                                  'tests/dialects/test_snowflake.py',
                                  'tests/test_generator.py'),
 'sqlglot/planner.py': ('tests/test_executor.py',),
 'sqlglot/schema.py': ('tests/dialects/test_redshift.py',
                       'tests/test_lineage.py',
                       'tests/test_optimizer.py',
                       'tests/test_schema.py'),
 'sqlglot/time.py': ('tests/test_time.py',),
 'sqlglot/tokens.py': ('tests/dialects/test_athena.py',
                       'tests/test_anonymize.py',
                       'tests/test_tokens.py'),
 'sqlglot/transforms.py': ('tests/test_transforms.py',),
 'sqlglot/typing/__init__.py': ('tests/test_typing.py',)}
# END GENERATED


class Selection(NamedTuple):
    numbers: set[int]
    fell_back: bool
    reasons: list[str]


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

_SAFE_PATH = re.compile(r"^[A-Za-z0-9_.\-/+@ ]+$")


def normalize_path(path: object) -> str | None:
    """Return a repo-relative posix path, or None when it is unusable/unsafe."""
    if not isinstance(path, str):
        return None
    p = path.strip().replace("\\", "/")
    if p in ("", "/dev/null"):
        return None
    if p.startswith(("a/", "b/")) and not p.startswith(("a//", "b//")):
        # tolerate unstripped git prefixes only when the remainder looks like a repo path
        rest = p[2:]
        if rest.startswith(("sqlglot/", "tests/", "docs/")):
            p = rest
    while p.startswith("./"):
        p = p[2:]
    if p.startswith("/") or not _SAFE_PATH.match(p):
        return None
    norm = posixpath.normpath(p)
    if norm.startswith("..") or norm == ".":
        return None
    return norm


def _change_paths(change: dict) -> list[str | None]:
    raw = []
    for key in ("path", "old_path", "new_path"):
        value = change.get(key)
        if value is not None:
            raw.append(value)
    if not raw:
        return [None]
    return sorted({normalize_path(v) for v in raw}, key=lambda x: (x is None, x or ""))


def _changed_line_count(change: dict) -> int:
    counts = []
    for a, b in (("added_text", "removed_text"), ("new_lines", "old_lines")):
        x, y = change.get(a), change.get(b)
        if isinstance(x, list) or isinstance(y, list):
            counts.append(len(x or []) + len(y or []))
    return max(counts) if counts else 0


def _dialect_of(path: str) -> str | None:
    parts = path.split("/")
    if len(parts) == 3 and parts[0] == "sqlglot" and parts[1] in DIALECT_PACKAGES and parts[2].endswith(".py"):
        stem = parts[2][:-3]
        if stem not in DIALECT_NON_DIALECT_STEMS:
            return stem
    return None


def _dialect_descendants(name: str) -> set[str]:
    children: dict[str, set[str]] = {}
    for child, bases in DIALECT_BASES.items():
        for base in bases:
            children.setdefault(base, set()).add(child)
    seen: set[str] = set()
    stack = [name]
    while stack:
        for child in sorted(children.get(stack.pop(), ())):
            if child not in seen:
                seen.add(child)
                stack.append(child)
    return seen


def _is_core(path: str) -> bool:
    return path in CORE_MODULES or path.startswith(CORE_PREFIXES)


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


def _convention_test_files(path: str, test_files: set[str]) -> set[str]:
    """Test files named after a source module (several conventions, not one)."""
    parts = path.split("/")
    stem = parts[-1][:-3] if parts[-1].endswith(".py") else parts[-1]
    names = {f"tests/test_{stem}.py", f"tests/dialects/test_{stem}.py"}
    if len(parts) >= 3:
        pkg = parts[-2]
        names.add(f"tests/test_{pkg}.py")
        names.add("tests/" + "/".join(parts[1:-1]) + f"/test_{stem}.py")
        names.add(f"tests/test_{pkg}_{stem}.py")
    return {n for n in names if n in test_files}


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
    numbers_by_file: dict[str, set[int]] = {}
    number_by_id: dict[str, int] = {}
    seconds: dict[int, float] = {}
    all_numbers: set[int] = set()
    for test in tests or []:
        if not isinstance(test, dict):
            continue
        number = test.get("number")
        if isinstance(number, bool) or not isinstance(number, int):
            continue
        all_numbers.add(number)
        file = normalize_path(test.get("file")) or ""
        numbers_by_file.setdefault(file, set()).add(number)
        if isinstance(test.get("id"), str):
            number_by_id[test["id"]] = number
        s = test.get("seconds")
        seconds[number] = float(s) if isinstance(s, (int, float)) and not isinstance(s, bool) else 0.0
    test_files = set(numbers_by_file)

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

    impacted = {p for p in (normalize_path(x) for x in (impacted_files or ())) if p}
    covered = {number_by_id[i] for i in (covered_test_ids or ()) if isinstance(i, str) and i in number_by_id}
    unknown_cov = [i for i in (covered_test_ids or ()) if not (isinstance(i, str) and i in number_by_id)]

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

    def add_files(names: Iterable[str]) -> None:
        for name in names:
            if name in numbers_by_file:
                files.add(name)

    def add_ids(ids: Iterable[str]) -> None:
        for test_id in ids:
            if test_id in number_by_id:
                selected.add(number_by_id[test_id])

    for change in changes:
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
                if path in SHARED_TEST_MODULES:
                    if not relaxed:
                        return everything(f"shared test module {path}")
                    prefix = SHARED_TEST_MODULES[path]
                    add_files(f for f in test_files if f.startswith(prefix))
                    reasons.append(f"{path}: shared test module, all importers")
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
                if status != "modified" and not aggressive:
                    return everything(f"{status} source module {path}")
                source_changes.append(path)
                if _is_core(path):
                    if aggressive and covered:
                        reasons.append(f"{path}: core module, coverage-driven (aggressive)")
                        continue
                    return everything(f"core module {path}")
                dialect = _dialect_of(path)
                if dialect is not None:
                    if dialect not in DIALECT_BASES and dialect not in DIALECT_TEST_FILE_MENTIONS:
                        if f"tests/dialects/test_{dialect}.py" not in test_files:
                            return everything(f"unknown dialect module {path}")
                    family = {dialect} | _dialect_descendants(dialect)
                    for name in sorted(family):
                        add_files([f"tests/dialects/test_{name}.py"])
                        add_files(DIALECT_TEST_FILE_MENTIONS.get(name, ()))
                        # aggressive: with coverage present, coverage replaces
                        # name mentions outside tests/dialects/.
                        if not (aggressive and covered):
                            add_ids(DIALECT_TEST_ID_MENTIONS.get(name, ()))
                    add_ids(ALL_DIALECT_TEST_IDS)
                    add_files(DIALECT_INFRA_TEST_FILES)
                    if path.startswith("sqlglot/typing/"):
                        add_files(["tests/test_typing.py"])
                    reasons.append(f"{path}: dialect {dialect} (+{len(family) - 1} inheriting)")
                add_files(_convention_test_files(path, test_files))
                add_files(MODULE_TEST_IMPORTERS.get(path, ()))
                add_files(MODULE_EXTRA_TEST_FILES.get(path, ()))
                for prefix, extra in PACKAGE_EXTRA_TEST_FILES.items():
                    if path.startswith(prefix):
                        add_files(extra)
                        add_files(MODULE_TEST_IMPORTERS.get(prefix + "__init__.py", ()))
                if dialect is None and not (
                    _convention_test_files(path, test_files)
                    or MODULE_TEST_IMPORTERS.get(path)
                    or MODULE_EXTRA_TEST_FILES.get(path)
                    or any(path.startswith(p) for p in PACKAGE_EXTRA_TEST_FILES)
                ):
                    if not covered:
                        return everything(f"no known tests for module {path}")
                reasons.append(f"{path}: source module")
                continue

            return everything(f"unknown path {path}")

    # ---- coverage / dependency signals ------------------------------------
    if source_changes:
        if not covered and not relaxed:
            return everything("coverage missing for source change(s): " + ", ".join(source_changes))
        if unknown_cov and not relaxed:
            return everything(f"{len(unknown_cov)} covered test ids not in tests.json")
        selected |= covered
    impacted_tests = {p for p in impacted if p in numbers_by_file}
    if impacted_tests:
        if aggressive and len(impacted_tests) >= UNINFORMATIVE_IMPACT_SHARE * max(1, len(test_files)):
            reasons.append("impacted_files names almost every test file; ignored (aggressive)")
        else:
            files |= impacted_tests
    for name in files:
        selected |= numbers_by_file[name]

    selected &= all_numbers
    if not selected:
        if source_changes:
            return everything("source change but no candidate tests")
        LOG.info("select_tests -> none: %s", "; ".join(reasons))
        return Selection(set(), False, reasons or ["no change can affect tests"])

    total = sum(seconds.values())
    chosen = sum(seconds[n] for n in selected)
    if total > 0 and chosen >= PROMOTE_TO_ALL_SHARE * total:
        return everything(f"selection already {chosen / total:.0%} of suite seconds")
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

    test_ids = {t["id"] for t in tests}
    test_files = sorted({t["file"] for t in tests})
    names = sorted(
        os.path.basename(p)[:-3]
        for p in glob.glob(os.path.join(sqlglot_root, "sqlglot/dialects/*.py"))
        if os.path.basename(p)[:-3] not in DIALECT_NON_DIALECT_STEMS
    )
    name_set = set(names)

    # 1. dialect -> dialects it builds on (any family module importing another's)
    bases: dict[str, tuple[str, ...]] = {}
    class_names: dict[str, str] = {}
    for name in names:
        found: set[str] = set()
        for package in DIALECT_PACKAGES:
            rel = f"sqlglot/{package}/{name}.py"
            if not os.path.exists(os.path.join(sqlglot_root, rel)):
                continue
            src = read(rel)
            for m in re.finditer(r"sqlglot\.(?:dialects|parsers|generators|typing)\.(\w+)", src):
                found.add(m.group(1))
            for m in re.finditer(r"from sqlglot\.(?:dialects|parsers|generators|typing) import ([\w, ]+)", src):
                found.update(x.strip() for x in m.group(1).split(","))
            if package == "dialects":
                m = re.search(r"^class (\w+)\(", src, re.M)
                if m:
                    class_names[name] = m.group(1)
        found = (found & name_set) - {name}
        if found:
            bases[name] = tuple(sorted(found))

    # 2. fixture keys and the dialects each fixture exercises
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
        out = set()
        for n in ast.walk(node):
            if isinstance(n, ast.Constant) and isinstance(n.value, str):
                out.add(n.value.strip("/"))
        return out

    def matched_fixtures(consts: set[str]) -> set[str]:
        return {p for p, keys in keys_by_fixture.items() if consts & keys}

    fixture_ids: dict[str, set[str]] = {}
    fixture_setup: dict[str, set[str]] = {}
    file_mentions: dict[str, set[str]] = {n: set() for n in names}
    id_mentions: dict[str, set[str]] = {n: set() for n in names}
    all_dialect_ids: set[str] = set()
    importers: dict[str, set[str]] = {}

    for tf in test_files:
        src = read(tf)
        tree = ast.parse(src)
        # module -> importing test file (static imports anywhere in the file)
        for node in ast.walk(tree):
            mods = []
            if isinstance(node, ast.Import):
                mods = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                mods = [node.module] + [f"{node.module}.{a.name}" for a in node.names]
            for mod in mods:
                if not mod.startswith("sqlglot"):
                    continue
                rel = mod.replace(".", "/")
                for cand in (rel + ".py", rel + "/__init__.py"):
                    if os.path.exists(os.path.join(sqlglot_root, cand)):
                        importers.setdefault(cand, set()).add(tf)
        if tf.startswith("tests/dialects/"):
            for name in names:
                if re.search(r"[\"']%s[\"']" % re.escape(name), src, re.I):
                    file_mentions[name].add(tf)
        # module-level / class-level (non-test) fixture loads -> whole file
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
        "DIALECT_BASES": bases,
        "DIALECT_TEST_FILE_MENTIONS": freeze(file_mentions),
        "DIALECT_TEST_ID_MENTIONS": freeze(id_mentions),
        "ALL_DIALECT_TEST_IDS": tuple(sorted(all_dialect_ids)),
        "FIXTURE_TEST_IDS": freeze(fixture_ids),
        "FIXTURE_SETUP_FILES": freeze(fixture_setup),
        "MODULE_TEST_IMPORTERS": freeze(importers),
    }


def _render_generated(maps: dict) -> str:
    import pprint

    types = {
        "DIALECT_BASES": "dict[str, tuple[str, ...]]",
        "DIALECT_TEST_FILE_MENTIONS": "dict[str, tuple[str, ...]]",
        "DIALECT_TEST_ID_MENTIONS": "dict[str, tuple[str, ...]]",
        "ALL_DIALECT_TEST_IDS": "tuple[str, ...]",
        "FIXTURE_TEST_IDS": "dict[str, tuple[str, ...]]",
        "FIXTURE_SETUP_FILES": "dict[str, tuple[str, ...]]",
        "MODULE_TEST_IMPORTERS": "dict[str, tuple[str, ...]]",
    }
    lines = []
    for key, annotation in types.items():
        body = pprint.pformat(maps[key], width=100, sort_dicts=True)
        lines.append(f"{key}: {annotation} = {body}")
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
