"""Generate seeded mutants of the pinned sqlglot checkout (evaluation only).

Each mutant is a small source/fixture edit chosen systematically from module
strata with a fixed random seed. Nothing here looks at sample answers.

    python3 make_mutants.py <sqlglot checkout> <out_dir> [--seed N]

Writes <out_dir>/mutants.json: a list of {"id", "stratum", "ops": [...]} where
each op is one of
    {"kind": "edit", "path", "line", "old", "new", "desc"}
    {"kind": "add", "path", "content"}
    {"kind": "delete", "path"}
    {"kind": "rename", "path", "to"}
run_mutants.py applies the ops in a git worktree, records `git diff -M` as the
patch and runs the real test suite for ground truth.
"""

from __future__ import annotations

import ast
import glob
import io
import json
import os
import random
import re
import sys
import tokenize

SEED_DEFAULT = 20261120

DIALECT_PACKAGES = ("dialects", "parsers", "generators", "typing")
BASE_DIALECTS = ("postgres", "mysql", "hive", "presto", "tsql", "trino", "spark2", "duckdb")

FLIPS = {
    "==": "!=", "!=": "==", "<": "<=", ">": ">=", "<=": "<", ">=": ">",
    "and": "or", "or": "and", "True": "False", "False": "True", "is": "is not",
}


def docstring_lines(tree: ast.AST) -> set[int]:
    lines: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", [])
            if body and isinstance(body[0], ast.Expr) and isinstance(getattr(body[0], "value", None), ast.Constant):
                if isinstance(body[0].value.value, str):
                    lines.update(range(body[0].lineno, body[0].end_lineno + 1))
    return lines


def python_sites(src: str) -> list[tuple[int, int, int, str, str]]:
    """(line, col_start, col_end, old_token, new_token) candidate edits."""
    tree = ast.parse(src)
    skip = docstring_lines(tree)
    sites = []
    tokens = list(tokenize.generate_tokens(io.StringIO(src).readline))
    for i, tok in enumerate(tokens):
        (line, col), (eline, ecol) = tok.start, tok.end
        if line in skip or line != eline:
            continue
        text = tok.string
        if tok.type == tokenize.OP and text in FLIPS:
            sites.append((line, col, ecol, text, FLIPS[text]))
        elif tok.type == tokenize.NAME and text in FLIPS:
            if text == "is" and i + 1 < len(tokens) and tokens[i + 1].string == "not":
                continue
            sites.append((line, col, ecol, text, FLIPS[text]))
        elif tok.type == tokenize.NAME and text == "not":
            sites.append((line, col, ecol + 1, "not ", ""))
        elif tok.type == tokenize.NUMBER and re.fullmatch(r"\d+", text):
            sites.append((line, col, ecol, text, str(int(text) + 1)))
        elif tok.type == tokenize.STRING and re.fullmatch(r"[\"'][A-Z_][A-Z0-9_]{2,}[\"']", text):
            # SQL keyword / function-name strings, e.g. "DATE_ADD"
            sites.append((line, col, ecol, text, text[:-1] + "X" + text[-1]))
    return sites


def mutate_python(root: str, rel: str, rng: random.Random) -> dict | None:
    with open(os.path.join(root, rel), encoding="utf-8") as handle:
        src = handle.read()
    lines = src.splitlines(keepends=True)
    sites = python_sites(src)
    rng.shuffle(sites)
    for line, c0, c1, old, new in sites:
        text = lines[line - 1]
        if text[c0:c1] != old:
            continue
        mutated = text[:c0] + new + text[c1:]
        candidate = "".join(lines[: line - 1] + [mutated] + lines[line:])
        try:
            compile(candidate, rel, "exec")
        except SyntaxError:
            continue
        return {"kind": "edit", "path": rel, "line": line, "old": text, "new": mutated,
                "desc": f"{old!r} -> {new!r}"}
    return None


def mutate_fixture(root: str, rel: str, rng: random.Random) -> dict | None:
    with open(os.path.join(root, rel), encoding="utf-8") as handle:
        lines = handle.read().splitlines(keepends=True)
    candidates = [i for i, l in enumerate(lines) if l.strip() and not l.lstrip().startswith(("--", "#"))]
    rng.shuffle(candidates)
    for i in candidates:
        words = list(re.finditer(r"[A-Za-z_][A-Za-z0-9_]*", lines[i]))
        if not words:
            continue
        w = rng.choice(words)
        mutated = lines[i][: w.end()] + "1" + lines[i][w.end():]
        return {"kind": "edit", "path": rel, "line": i + 1, "old": lines[i], "new": mutated,
                "desc": f"identifier {w.group(0)!r} -> {w.group(0)}1"}
    return None


def main() -> None:
    root, out_dir = sys.argv[1], sys.argv[2]
    seed = int(sys.argv[sys.argv.index("--seed") + 1]) if "--seed" in sys.argv else SEED_DEFAULT
    rng = random.Random(seed)
    os.chdir(root)
    py = sorted(glob.glob("sqlglot/**/*.py", recursive=True))

    def dialect_of(p):
        parts = p.split("/")
        if len(parts) == 3 and parts[1] in DIALECT_PACKAGES and parts[2][:-3] not in ("__init__", "dialect", "base"):
            return parts[2][:-3]
        return None

    core = [p for p in py if p in {
        "sqlglot/parser.py", "sqlglot/generator.py", "sqlglot/tokens.py", "sqlglot/helper.py",
        "sqlglot/dialects/dialect.py", "sqlglot/transforms.py", "sqlglot/schema.py", "sqlglot/time.py",
        "sqlglot/jsonpath.py", "sqlglot/serde.py"} or p.startswith("sqlglot/expressions/")]
    base_family = [p for p in py if dialect_of(p) in BASE_DIALECTS]
    other_family = [p for p in py if dialect_of(p) and dialect_of(p) not in BASE_DIALECTS]
    other = [p for p in py if p not in core and not dialect_of(p) and not p.endswith("__init__.py")
             and p not in ("sqlglot/__main__.py", "sqlglot/_typing.py")]
    fixtures = sorted(glob.glob("tests/fixtures/**/*.sql", recursive=True))

    strata = [("core", core, 10), ("base_dialect", base_family, 16), ("other_dialect", other_family, 10),
              ("optimizer_other", other, 10), ("fixture", fixtures, 6)]
    mutants = []
    seen_edits: set[tuple] = set()
    for name, pool, count in strata:
        made = 0
        attempts = 0
        while made < count and attempts < 200:
            attempts += 1
            rel = rng.choice(pool)
            op = mutate_fixture(root, rel, rng) if name == "fixture" else mutate_python(root, rel, rng)
            if op is None or (op["path"], op["line"], op["new"]) in seen_edits:
                continue
            seen_edits.add((op["path"], op["line"], op["new"]))
            mutants.append({"id": f"{name}-{made:02d}", "stratum": name, "ops": [op]})
            made += 1

    # Multi-module changes: two single-line edits from different strata.
    singles = [m for m in mutants if m["stratum"] != "fixture"]
    for i in range(4):
        a, b = rng.sample(singles, 2)
        if a["ops"][0]["path"] == b["ops"][0]["path"]:
            continue
        mutants.append({"id": f"multi-{i:02d}", "stratum": "multi", "ops": a["ops"] + b["ops"]})

    # Newly added modules: one unused module, one wired into an existing optimizer pass.
    mutants.append({"id": "added-00", "stratum": "added", "ops": [
        {"kind": "add", "path": "sqlglot/optimizer/extra_rules.py",
         "content": "def passthrough(expression):\n    return expression\n"}]})
    target = rng.choice([p for p in other if p.startswith("sqlglot/optimizer/")])
    src = open(target, encoding="utf-8").read()
    m = re.search(r"^def (\w+)\(\s*expression\b[^\n]*\n((?:\s+\"\"\"[\s\S]*?\"\"\"\n)?)", src, re.M)
    if m:
        insert_at = src[: m.end()].count("\n") + 1
        lines = src.splitlines(keepends=True)
        old = lines[insert_at - 1]
        new = "    from sqlglot.optimizer.extra_rules import drop_all\n    expression = drop_all(expression)\n" + old
        mutants.append({"id": "added-01", "stratum": "added", "ops": [
            {"kind": "add", "path": "sqlglot/optimizer/extra_rules.py",
             "content": "def drop_all(expression):\n    return expression.copy() if expression.this is None else expression.this\n"},
            {"kind": "edit", "path": target, "line": insert_at, "old": old, "new": new,
             "desc": f"{target}:{m.group(1)} calls a new module"}]})
    mutants.append({"id": "added-02", "stratum": "added", "ops": [
        {"kind": "add", "path": "sqlglot/dialects/newsql.py",
         "content": "from sqlglot.dialects.dialect import Dialect\n\n\nclass NewSQL(Dialect):\n    pass\n"}]})

    # Renames and deletes of source and test modules (importers left untouched).
    for i, rel in enumerate(rng.sample([p for p in other if "/" in p[len("sqlglot/"):]] or other, 1)
                            + rng.sample(other_family, 1)):
        mutants.append({"id": f"rename-{i:02d}", "stratum": "rename", "ops": [
            {"kind": "rename", "path": rel, "to": rel[:-3] + "_renamed.py"}]})
    for i, rel in enumerate(rng.sample(other, 1) + rng.sample(sorted(glob.glob("tests/test_*.py")), 1)):
        mutants.append({"id": f"delete-{i:02d}", "stratum": "delete", "ops": [{"kind": "delete", "path": rel}]})

    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "mutants.json"), "w", encoding="utf-8") as handle:
        json.dump({"seed": seed, "mutants": mutants}, handle, indent=1)
    print(f"{len(mutants)} mutants (seed {seed})", file=sys.stderr)


if __name__ == "__main__":
    main()
