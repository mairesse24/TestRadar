"""Build independent SQLGlot probes and verify failures with pinned pytest tests."""

from __future__ import annotations

import argparse
import ast
import json
import os
from pathlib import Path
import subprocess
import sys

PROBES = (
    ("parser-body", "sqlglot/parser.py", "Parser", "parse",
     ("tests/test_parser.py", "tests/test_transpile.py")),
    ("generator-body", "sqlglot/generator.py", "Generator", "generate",
     ("tests/test_generator.py", "tests/test_transpile.py")),
    ("postgres-generator-body", "sqlglot/generators/postgres.py", "PostgresGenerator", "datatype_sql",
     ("tests/dialects/test_postgres.py", "tests/dialects/test_duckdb.py", "tests/dialects/test_dialect.py")),
    ("dialect-loader-body", "sqlglot/dialects/__init__.py", None, "__getattr__",
     ("tests/test_dialect_imports.py", "tests/dialects/test_dialect.py", "tests/dialects/test_postgres.py")),
)


def execute(tree: Path, ids: list[str], output: Path) -> dict:
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join((str(tree), str(Path(__file__).resolve().parent)))
    env["SKIP_INTEGRATION"] = "0"
    env["SELECTION_EVAL_OUTCOMES"] = str(output)
    env.pop("PYTEST_DISABLE_PLUGIN_AUTOLOAD", None)
    output.unlink(missing_ok=True)
    result = subprocess.run(
        [sys.executable, "-B", "-m", "pytest", "-q", "-p", "outcome_plugin", "-p",
         "no:cacheprovider", "--timeout", "10", *ids],
        cwd=tree, env=env, capture_output=True, timeout=100,
    )
    if not output.exists():
        raise RuntimeError(result.stderr.decode(errors="replace") or "pytest produced no outcomes")
    return json.loads(output.read_bytes())


def find_statement(source: str, owner: str | None, function: str) -> int:
    tree = ast.parse(source)
    parent = tree if owner is None else next(
        node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == owner
    )
    method = next(node for node in parent.body if isinstance(node, ast.FunctionDef) and node.name == function)
    first = method.body[0]
    if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) and isinstance(first.value.value, str):
        first = method.body[1]
    return first.lineno - 1


def generate(tree: Path, dataset: Path) -> list[dict]:
    catalog = json.loads((dataset / "tests.json").read_bytes())["tests"]
    numbers = {test["id"]: test["number"] for test in catalog}
    reports = []
    for name, path, owner, method, files in PROBES:
        ids = []
        for filename in files:
            matching = sorted(
                (test for test in catalog if test["file"] == filename),
                key=lambda test: test["id"],
            )
            ids.extend(test["id"] for test in matching[:8])
        if not ids:
            raise ValueError(f"no test probes for {name}")
        scratch = dataset / "outcomes.json"
        before = execute(tree, ids, scratch)
        file = tree / path
        original = file.read_bytes()
        source_lines = original.decode().splitlines(keepends=True)
        line = find_statement("".join(source_lines), owner, method)
        indentation = source_lines[line][:len(source_lines[line]) - len(source_lines[line].lstrip())]
        source_lines.insert(line, indentation + "raise RuntimeError('independent test selection probe')\n")
        try:
            file.write_bytes("".join(source_lines).encode())
            delta = subprocess.check_output(["git", "diff", "HEAD", "--", path], cwd=tree)
            (dataset / "commits" / f"{name}.patch").write_bytes(delta)
            after = execute(tree, ids, scratch)
        finally:
            file.write_bytes(original)
        file_errors = set(after["collect_errors"])
        import_failures = {node for node in ids if node.split("::", 1)[0] in file_errors}
        verified = set(before["passed"]) & (set(after["failed"]) | import_failures) & numbers.keys()
        reports.append({
            "name": name, "failing": sorted(numbers[node] for node in verified),
            "attempted": len(ids), "baseline_passed": len(before["passed"]),
            "mutated_reported": len(set(after["passed"]) | set(after["failed"])),
            "collection_errors": after["collect_errors"],
        })
    (dataset / "answers.json").write_text(
        json.dumps({"commits": {row["name"]: {"failing": row["failing"]} for row in reports}}),
        encoding="utf-8",
    )
    return reports


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("tree", type=Path)
    parser.add_argument("dataset", type=Path)
    args = parser.parse_args()
    for row in generate(args.tree.resolve(), args.dataset.resolve()):
        print(row)


if __name__ == "__main__":
    main()
