"""Generate seeded training mutations of the pinned sqlglot checkout (offline only).

Training data for predictive-selection research. Uses only the pinned
source; never reads sample answers or evaluation sets except to *exclude*
any mutation whose (path, line, new text) coincides with an evaluation
patch.

    python3 gen_mutations.py <sqlglot checkout> <out.json> --seed N --per-cell K
        [--exclude-patches DIR ...]

Each mutation is a single-line edit (run_mutants.py format) with metadata:
id, category, location, path, qualname, line, node, desc, ops.
"""

from __future__ import annotations

import argparse
import ast
import glob
import json
import os
import random
import re

CATEGORIES = ("operator_swap", "constant_change", "condition_negation", "return_change",
              "statement_deletion", "argument_swap", "dialect_table")
FLIP_CMP = {ast.Eq: ast.NotEq, ast.NotEq: ast.Eq, ast.Lt: ast.GtE, ast.GtE: ast.Lt,
            ast.Gt: ast.LtE, ast.LtE: ast.Gt, ast.Is: ast.IsNot, ast.IsNot: ast.Is,
            ast.In: ast.NotIn, ast.NotIn: ast.In}
FLIP_BIN = {ast.Add: ast.Sub, ast.Sub: ast.Add, ast.Mult: ast.FloorDiv, ast.Div: ast.Mult,
            ast.FloorDiv: ast.Mult, ast.Mod: ast.Mult, ast.BitOr: ast.BitAnd, ast.BitAnd: ast.BitOr}
UTILITIES = ("time.py", "trie.py", "serde.py", "diff.py", "lineage.py", "schema.py",
             "transforms.py", "errors.py", "jsonpath.py", "planner.py", "helper.py",
             "anonymize.py", "_typing.py")


def location_of(rel: str) -> str | None:
    parts = rel.split("/")
    if parts[1] in ("dialects", "parsers", "generators", "typing"):
        return None if parts[-1] in ("__init__.py", "dialect.py") else "dialect"
    if parts[1] == "optimizer":
        return "optimizer"
    if parts[1] == "executor":
        return "executor"
    if parts[1] in UTILITIES:
        return "utility"
    if parts[1] in ("parser.py", "generator.py", "tokens.py", "tokenizer_core.py", "expressions") \
            or rel == "sqlglot/dialects/dialect.py":
        return "core"
    return None


def docstring_ids(tree: ast.AST) -> set[int]:
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
                    and isinstance(body[0].value.value, str):
                out.add(id(body[0].value))
    return out


def annotate(tree: ast.AST) -> None:
    """Set .parent and .qual (enclosing class/function names) on every node."""
    def visit(node, qual, in_fstring, in_func):
        for child in ast.iter_child_nodes(node):
            child.parent = node
            q = qual
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                q = qual + [child.name]
            child.qual = ".".join(q)
            child.in_fstring = in_fstring or isinstance(child, ast.JoinedStr)
            child.in_func = in_func or isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
            visit(child, q, child.in_fstring, child.in_func)
    visit(tree, [], False, False)


def single_line(node) -> bool:
    return getattr(node, "lineno", None) is not None and node.lineno == node.end_lineno


def replace_segment(lines, node, text):
    line = lines[node.lineno - 1]
    b = line.encode("utf-8")
    new = (b[:node.col_offset] + text.encode("utf-8") + b[node.end_col_offset:]).decode("utf-8")
    return node.lineno, line, new


def candidates(src: str, rel: str):
    tree = ast.parse(src)
    annotate(tree)
    docs = docstring_ids(tree)
    lines = src.splitlines(keepends=True)
    out = []

    def add(cat, node, lineno, old, new, desc):
        if old == new:
            return
        out.append({"category": cat, "line": lineno, "old": old, "new": new, "desc": desc,
                    "node": type(node).__name__, "qualname": getattr(node, "qual", "")})

    for node in ast.walk(tree):
        if getattr(node, "in_fstring", False):
            continue
        if not single_line(node) and not isinstance(node, (ast.If, ast.While, ast.Dict)):
            continue
        if isinstance(node, ast.Compare) and len(node.ops) == 1 and type(node.ops[0]) in FLIP_CMP and node.in_func:
            m = ast.Compare(node.left, [FLIP_CMP[type(node.ops[0])]()], node.comparators)
            add("operator_swap", node, *replace_segment(lines, node, "(" + ast.unparse(m) + ")"),
                f"compare {type(node.ops[0]).__name__} flipped")
        elif isinstance(node, ast.BinOp) and type(node.op) in FLIP_BIN and node.in_func:
            m = ast.BinOp(node.left, FLIP_BIN[type(node.op)](), node.right)
            add("operator_swap", node, *replace_segment(lines, node, "(" + ast.unparse(m) + ")"),
                f"binop {type(node.op).__name__} swapped")
        elif isinstance(node, ast.BoolOp) and node.in_func:
            m = ast.BoolOp(ast.Or() if isinstance(node.op, ast.And) else ast.And(), node.values)
            add("operator_swap", node, *replace_segment(lines, node, "(" + ast.unparse(m) + ")"),
                "and/or swapped")
        elif isinstance(node, ast.Constant) and id(node) not in docs and node.in_func:
            v = node.value
            if isinstance(v, bool):
                new = repr(not v)
            elif isinstance(v, int) and not isinstance(v, bool):
                new = repr(v + 1)
            elif isinstance(v, str) and v and len(v) < 40:
                new = repr(v + "_x")
            else:
                continue
            add("constant_change", node, *replace_segment(lines, node, new), f"constant {v!r}")
        elif isinstance(node, (ast.If, ast.While)) and single_line(node.test) and node.in_func:
            t = node.test
            add("condition_negation", node, *replace_segment(lines, t, "not (" + ast.unparse(t) + ")"),
                f"{type(node).__name__.lower()} condition negated")
        elif isinstance(node, ast.Return) and node.value is not None and not (
                isinstance(node.value, ast.Constant) and node.value.value is None):
            add("return_change", node, *replace_segment(lines, node, "return None"), "return None")
        elif isinstance(node, (ast.Assign, ast.AugAssign, ast.Expr)) and node.in_func \
                and not (isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant)):
            add("statement_deletion", node, *replace_segment(lines, node, "pass"), "statement replaced by pass")
        elif isinstance(node, ast.Call) and node.in_func and len(node.args) >= 2 and \
                not any(isinstance(a, ast.Starred) for a in node.args[:2]):
            a0, a1 = node.args[0], node.args[1]
            if ast.dump(a0) != ast.dump(a1):
                m = ast.Call(node.func, [a1, a0] + node.args[2:], node.keywords)
                add("argument_swap", node, *replace_segment(lines, node, ast.unparse(m)), "first two arguments swapped")
        elif isinstance(node, ast.Dict) and not node.in_func and location_of(rel) == "dialect":
            for k, v in zip(node.keys, node.values):
                if k is None or k.lineno != v.end_lineno:
                    continue
                text = lines[k.lineno - 1]
                seg = text.strip()
                if seg.endswith(",") and seg.count(":") >= 1 and text.lstrip().startswith(
                        ast.get_source_segment(src, k) or "\0"):
                    nxt_ok = all(not (o is not k and o.lineno == k.lineno) for o in node.keys if o is not None)
                    if nxt_ok:
                        out.append({"category": "dialect_table", "line": k.lineno, "old": text, "new": "",
                                    "desc": "class-level table entry removed", "node": "Dict",
                                    "qualname": getattr(node, "qual", "")})
    return out


def eval_signatures(dirs):
    sigs = set()
    for d in dirs:
        for p in glob.glob(os.path.join(d, "**", "*.patch"), recursive=True):
            path = None
            for line in open(p, encoding="utf-8", errors="replace"):
                if line.startswith("+++ b/"):
                    path = line[6:].strip()
                elif line.startswith("+") and not line.startswith("+++") and path:
                    sigs.add((path, line[1:].rstrip("\n").strip()))
    return sigs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("root"); ap.add_argument("out")
    ap.add_argument("--seed", type=int, default=20261121)
    ap.add_argument("--per-cell", type=int, default=5)
    ap.add_argument("--exclude-patches", nargs="*", default=[])
    a = ap.parse_args()
    rng = random.Random(a.seed)
    excluded = eval_signatures(a.exclude_patches)
    pool = {}
    sources = {}
    for path in sorted(glob.glob(os.path.join(a.root, "sqlglot", "**", "*.py"), recursive=True)):
        rel = os.path.relpath(path, a.root).replace(os.sep, "/")
        loc = location_of(rel)
        if loc is None:
            continue
        src = open(path, encoding="utf-8").read()
        sources[rel] = src
        for c in candidates(src, rel):
            if (rel, c["new"].strip()) in excluded:
                continue
            c.update(path=rel, location=loc)
            pool.setdefault((loc, c["category"]), []).append(c)
    muts, n = [], 0
    for loc in ("core", "dialect", "optimizer", "executor", "utility"):
        for cat in CATEGORIES:
            cell = pool.get((loc, cat), [])
            rng.shuffle(cell)
            used_lines = set()
            k = 0
            for c in cell:
                key = (c["path"], c["line"])
                if key in used_lines:
                    continue
                trial = sources[c["path"]].splitlines(keepends=True)
                trial[c["line"] - 1] = c["new"]
                try:
                    ast.parse("".join(trial))  # validate only the sampled edits
                except SyntaxError:
                    continue
                used_lines.add(key)
                muts.append({"id": f"tm-{loc}-{cat}-{k:02d}", "stratum": f"{loc}/{cat}",
                             "category": cat, "location": loc, "path": c["path"], "qualname": c["qualname"],
                             "line": c["line"], "node": c["node"], "desc": c["desc"],
                             "ops": [{"kind": "edit", "path": c["path"], "line": c["line"],
                                      "old": c["old"], "new": c["new"], "desc": c["desc"]}]})
                k += 1
                if k >= a.per_cell:
                    break
    sizes = {f"{l}/{c}": len(v) for (l, c), v in sorted(pool.items())}
    json.dump({"seed": a.seed, "pool_sizes": sizes, "excluded_signatures": len(excluded),
               "mutants": muts}, open(a.out, "w"), indent=1)
    print(len(muts), "mutations;", sum(sizes.values()), "candidates")


if __name__ == "__main__":
    main()
