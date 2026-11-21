"""Targeted adversarial change scenarios against pinned SQLGlot.

    python3 tools/adversarial/make_scenarios.py SQLGLOT_ROOT OUT.json

Each scenario aims at one risk category for test selection (core modules,
import-time execution, cross-dialect effects, shared utilities and global
state, coverage blind spots, tests and fixtures). Scenarios are written
against the pinned source only; no test outcome, sample answer or selector
output is consulted. The output uses the ``ops`` format of
``tools/selection_eval/run_mutants.py`` so ground truth comes from the real
SQLGlot suite.
"""

from __future__ import annotations

import ast
import json
import random
import re
import sys
from pathlib import Path

SEED = 20261121
FLIPS = [(" == ", " != "), (" != ", " == "), (" <= ", " < "), (" < ", " <= "), (" >= ", " > "),
         (" > ", " >= "), (" and ", " or "), (" or ", " and "), ("True", "False"), ("False", "True"),
         (" + 1", " - 1"), (" - 1", " + 1"), ("not ", ""), (" - ", " + "), ("[0]", "[-1]"),
         ("[-1]", "[0]"), ("min(", "max("), ("max(", "min(")]


class Source:
    def __init__(self, root: Path):
        self.root = root

    def lines(self, path: str) -> list[str]:
        return (self.root / path).read_text(encoding="utf-8").splitlines(keepends=True)

    def edit(self, path: str, find: str, new: str, nth: int = 1) -> dict:
        """Replace the nth line containing ``find``. ``{old}`` in new keeps the line."""
        hits = [i for i, line in enumerate(self.lines(path)) if find in line]
        if len(hits) < nth:
            raise SystemExit(f"{path}: {find!r} not found")
        index = hits[nth - 1]
        old = self.lines(path)[index]
        return {"kind": "edit", "path": path, "line": index + 1, "old": old,
                "new": new.replace("{old}", old.rstrip("\n")) + "\n"}

    def flip(self, path: str, function: str, salt: str) -> dict:
        """Flip one operator or constant inside ``function`` (seeded choice)."""
        text = (self.root / path).read_text(encoding="utf-8")
        lines = text.splitlines(keepends=True)
        tree = ast.parse(text)
        # The last definition wins (typing overloads come first).
        node = [n for n in ast.walk(tree)
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == function][-1]
        body = node.body
        skip = set()  # lines inside string literals (docstrings, notes)
        for inner in ast.walk(node):
            if (isinstance(inner, ast.Constant) and isinstance(inner.value, str)
                    and inner.end_lineno > inner.lineno):
                skip.update(range(inner.lineno, inner.end_lineno + 1))
        sites = []
        for number in range(body[0].lineno, node.end_lineno + 1):
            line = lines[number - 1]
            code = line.split("#", 1)[0]
            if number in skip or not code.strip() or '"""' in code:
                continue
            for old, new in FLIPS:
                for match in re.finditer(re.escape(old), code):
                    sites.append((number, match.start(), old, new))
        if not sites:
            raise SystemExit(f"{path}:{function}: no flip site")
        rng = random.Random(f"{SEED}:{salt}")
        number, start, old, new = rng.choice(sites)
        line = lines[number - 1]
        return {"kind": "edit", "path": path, "line": number, "old": line,
                "new": line[:start] + new + line[start + len(old):]}


def scenarios(src: Source) -> list[dict]:
    out: list[dict] = []

    def add(sid, category, intent, *ops):
        out.append({"id": sid, "stratum": category, "intent": intent, "ops": list(ops)})

    # --- cross-dialect effects: inheritance, by-name use, shared tables -------
    add("dialect-01", "cross_dialect", "leaf parser mutates its parent's FUNCTIONS table at import",
        src.edit("sqlglot/parsers/redshift.py", "    FUNCTIONS = {",
                 '    PostgresParser.FUNCTIONS.pop("GET_BIT", None)\n{old}'))
    add("dialect-02", "cross_dialect", "child generator mutates the parent's TYPE_MAPPING at import",
        src.edit("sqlglot/generators/spark2.py", "    TRANSFORMS = {",
                 '    HiveGenerator.TYPE_MAPPING[exp.DType.TEXT] = "VARCHAR"\n{old}'))
    add("dialect-03", "cross_dialect", "base tokenizer keyword removed (inherited by descendants)",
        src.edit("sqlglot/dialects/postgres.py", '"~": TokenType.RLIKE,', ""))
    add("dialect-04", "cross_dialect", "base tokenizer keyword removed (mysql family)",
        src.edit("sqlglot/dialects/mysql.py", '"DISTINCTROW": TokenType.DISTINCT,', ""))
    add("dialect-05", "cross_dialect", "base generator type mapping changed (presto family)",
        src.edit("sqlglot/generators/presto.py", 'exp.DType.BIT: "BOOLEAN",', '        exp.DType.BIT: "BIT",'))
    add("dialect-06", "cross_dialect", "dialect used only by name from another dialect (teradata time formats)",
        src.edit("sqlglot/dialects/teradata.py", '"YYYY": "%Y",', '        "YYYY": "%y",'))
    add("dialect-07", "cross_dialect", "base dialect class attribute changed (hive family)",
        src.edit("sqlglot/dialects/hive.py", "    SAFE_DIVISION = True", "    SAFE_DIVISION = False"))
    add("dialect-08", "cross_dialect", "leaf generator type mapping changed",
        src.edit("sqlglot/generators/exasol.py", 'exp.DType.TINYINT: "SMALLINT",', '        exp.DType.TINYINT: "TINYINT",'))
    add("dialect-09", "cross_dialect", "leaf mysql-family generator type mapping changed",
        src.edit("sqlglot/generators/drill.py", 'exp.DType.INT: "INTEGER",', '        exp.DType.INT: "BIGINT",'))
    add("dialect-10", "cross_dialect", "base generator type mapping changed (postgres family)",
        src.edit("sqlglot/generators/postgres.py", 'exp.DType.FLOAT: "REAL",', '        exp.DType.FLOAT: "FLOAT4",'))

    # --- import-time execution: class bodies, defaults, decorators -----------
    add("import-01", "import_time", "class-body AttributeError in a leaf dialect parser",
        src.edit("sqlglot/parsers/dremio.py", '"BIT_AND": exp.BitwiseAndAgg.from_arg_list,',
                 '        "BIT_AND": exp.BitwiseAndAggregate.from_arg_list,'))
    add("import-02", "import_time", "class-body AttributeError in a leaf dialect generator",
        src.edit("sqlglot/generators/exasol.py", 'exp.DType.MEDIUMINT: "INT",', '        exp.DType.MEDIUMINTEGER: "INT",'))
    add("import-03", "import_time", "default argument evaluated at import in an optimizer rule",
        src.edit("sqlglot/optimizer/eliminate_joins.py", "def eliminate_joins(expression: E) -> E:",
                 "def eliminate_joins(expression: E, _unused=exp.NotAnExpression) -> E:"))
    add("import-04", "import_time", "module-level statement raising at import in planner",
        src.edit("sqlglot/planner.py", "class Plan:", "_PLAN_GUARD = exp.NotAnExpression\n\n\nclass Plan:"))
    add("import-05", "import_time", "syntax error in a non-core module",
        src.edit("sqlglot/lineage.py", "def to_node(", "def to_node(("))
    add("import-06", "import_time", "syntax error in a leaf dialect generator",
        src.edit("sqlglot/generators/drill.py", 'ARRAY_SIZE_NAME = "REPEATED_COUNT"', '    ARRAY_SIZE_NAME = "REPEATED_COUNT"('))
    add("import-07", "import_time", "decorator evaluated at import in an optimizer rule",
        src.edit("sqlglot/optimizer/qualify_tables.py", "def qualify_tables(", "@exp.not_a_decorator\n{old}"))
    add("import-08", "global_state", "non-core module monkeypatches a core class at import",
        src.edit("sqlglot/lineage.py", "from sqlglot.schema import ensure_schema",
                 '{old}\n\nexp.Column.output_name = property(lambda self: "")'))
    add("import-09", "import_time", "dialect class renamed (registry key changes)",
        src.edit("sqlglot/dialects/dremio.py", "class Dremio(", "class DremioDialect(Dialect):"))

    # --- core modules: unexpected consumers -----------------------------------
    for sid, path, fn in [
        ("core-01", "sqlglot/time.py", "format_time"),
        ("core-02", "sqlglot/trie.py", "in_trie"),
        ("core-03", "sqlglot/serde.py", "load"),
        ("core-04", "sqlglot/errors.py", "highlight_sql"),
        ("core-05", "sqlglot/transforms.py", "eliminate_qualify"),
        ("core-06", "sqlglot/transforms.py", "eliminate_distinct_on"),
        ("core-07", "sqlglot/jsonpath.py", "parse"),
        ("core-08", "sqlglot/optimizer/qualify_columns.py", "qualify_columns"),
        ("core-09", "sqlglot/optimizer/simplify.py", "simplify_not"),
        ("core-10", "sqlglot/optimizer/normalize_identifiers.py", "normalize_identifiers"),
        ("core-11", "sqlglot/generator.py", "tablesample_sql"),
        ("core-12", "sqlglot/parser.py", "_parse_pivot"),
        ("core-13", "sqlglot/dialects/dialect.py", "strposition_sql"),
        ("core-14", "sqlglot/dialects/dialect.py", "build_formatted_time"),
    ]:
        add(sid, "core", f"behaviour change inside {fn}", src.flip(path, fn, sid))
    add("core-15", "core", "tokenizer keyword removed", src.edit("sqlglot/tokens.py", '"QUALIFY": TokenType.QUALIFY,', ""))
    add("core-16", "core", "tokenizer keyword remapped", src.edit("sqlglot/tokens.py", '"ILIKE": TokenType.ILIKE,', '        "ILIKE": TokenType.LIKE,'))

    # --- signatures, shared utilities, global state -----------------------------
    add("shared-01", "shared_utility", "shared helper gains a required parameter",
        src.edit("sqlglot/helper.py", "def name_sequence(prefix: str)", "def name_sequence(prefix: str, start: int) -> t.Callable[[], str]:"))
    add("shared-02", "shared_utility", "shared helper behaviour change", src.flip("sqlglot/helper.py", "split_num_words", "shared-02"))
    add("shared-03", "shared_utility", "shared helper behaviour change", src.flip("sqlglot/helper.py", "is_iterable", "shared-03"))
    add("shared-04", "signature", "non-core entry point loses a default",
        src.edit("sqlglot/optimizer/qualify.py", "    dialect: DialectType = None,", "    dialect: DialectType,"))
    add("shared-05", "signature", "non-core helper used by the planner changes behaviour",
        src.flip("sqlglot/optimizer/eliminate_joins.py", "join_condition", "shared-05"))
    add("shared-06", "global_state", "package-level default setting changed",
        src.edit("sqlglot/__init__.py", "pretty = False", "pretty = True"))
    add("shared-07", "shared_utility", "executor aggregate table entry changed",
        src.edit("sqlglot/executor/env.py", '"ARRAYUNIQUEAGG": filter_nulls(lambda acc: list(set(acc))),',
                 '    "ARRAYUNIQUEAGG": filter_nulls(lambda acc: list(acc)),'))

    # --- code reached mostly in spawned workers or rarely executed -------------
    for sid, path, fn in [
        ("coverage-01", "sqlglot/optimizer/pushdown_projections.py", "pushdown_projections"),
        ("coverage-02", "sqlglot/optimizer/merge_subqueries.py", "_mergeable"),
        ("coverage-03", "sqlglot/optimizer/unnest_subqueries.py", "decorrelate"),
        ("coverage-04", "sqlglot/optimizer/eliminate_ctes.py", "eliminate_ctes"),
        ("coverage-05", "sqlglot/optimizer/canonicalize.py", "remove_redundant_casts"),
        ("coverage-06", "sqlglot/optimizer/annotate_types.py", "_annotate_div"),
        ("coverage-07", "sqlglot/schema.py", "normalize_name"),
        ("coverage-08", "sqlglot/anonymize.py", "anonymize"),
    ]:
        add(sid, "coverage_blind_spot", f"behaviour change inside {fn}", src.flip(path, fn, sid))

    # --- tests and fixtures ------------------------------------------------------
    add("tests-01", "test_or_fixture", "shared identity fixture line changed",
        src.edit("tests/fixtures/identity.sql", "SUM(CASE WHEN x > 1 THEN 1 ELSE 0 END) / y",
                 "SUM(CASE WHEN x > 1 THEN 1 ELSE 0 END) // y"))
    add("tests-02", "test_or_fixture", "shared test helper default changed",
        src.edit("tests/dialects/test_dialect.py", "self, sql, write_sql=None, pretty=False,",
                 "        self, sql, write_sql=None, pretty=True, check_command_warning=False, identify=False"))
    add("tests-03", "test_or_fixture", "fixture shared by executor and optimizer changed",
        src.edit("tests/fixtures/optimizer/tpc-h/tpc-h.sql", "avg(l_discount) as avg_disc,",
                 "        max(l_discount) as avg_disc,"))
    add("tests-04", "test_or_fixture", "pretty-print fixture changed",
        src.edit("tests/fixtures/pretty.sql", "SELECT * FROM test;", "SELECT * FROM  test;"))
    # --- added after the first ground-truth pass (same rules) -------------------
    add("global-01", "global_state", "leaf dialect generator mutates the base Generator table at import",
        src.edit("sqlglot/generators/exasol.py", "    TYPE_MAPPING = {",
                 '    generator.Generator.TYPE_MAPPING[exp.DType.LONGTEXT] = "CLOB"\n{old}'))
    add("coverage-09", "coverage_blind_spot", "typing entry that restates the parent entry (checked only by an unobserved test)",
        src.edit("sqlglot/typing/presto.py", "    **EXPRESSION_METADATA,",
                 '{old}\n    exp.FromBase64: {"returns": exp.DType.BINARY},'))
    add("equiv-01", "ast_equivalent", "trailing whitespace only in a core helper (behaviour unchanged)",
        src.edit("sqlglot/helper.py", "        return seq[index]", "{old}   "))
    add("equiv-02", "ast_equivalent", "same-length quote style change in a dialect function body",
        src.edit("sqlglot/generators/sqlite.py", '"constraints", exp.ColumnConstraint(kind=exp.PrimaryKeyColumnConstraint())',
                 "                'constraints', exp.ColumnConstraint(kind=exp.PrimaryKeyColumnConstraint())"))
    return out


def main() -> None:
    root, output = Path(sys.argv[1]), Path(sys.argv[2])
    result = scenarios(Source(root))
    ids = [s["id"] for s in result]
    assert len(ids) == len(set(ids))
    output.write_text(json.dumps({"seed": SEED, "mutants": result}, indent=1) + "\n", encoding="utf-8")
    print(f"{len(result)} scenarios", file=sys.stderr)


if __name__ == "__main__":
    main()
