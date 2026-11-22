"""Offline statement-impact diagnostics from positive pinned line observations."""

from __future__ import annotations

import argparse
import ast
from collections import defaultdict
import json
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from analyzer import edit_scope
from analyzer.patch_parser import parse_patch

PIN = "eb11aec4be8cb2122afe713a4816662c3f0fb2fa"


def span(statement):
    return statement.lineno, statement.col_offset, statement.end_lineno, statement.end_col_offset


def census(body):
    return [node for node in ast.walk(body) if isinstance(node, ast.stmt)]


def header(node):
    """Include expressions and structure but leave nested statement bodies out."""
    def shape(value):
        if isinstance(value, ast.stmt):
            return type(value).__name__
        if isinstance(value, list):
            return tuple(shape(item) for item in value)
        if isinstance(value, ast.AST):
            return header(value)
        return value

    return type(node).__name__, tuple((key, shape(value)) for key, value in ast.iter_fields(node))


def restricted(statement):
    for node in ast.walk(statement):
        if isinstance(node, (ast.Import, ast.ImportFrom, ast.Global, ast.Nonlocal,
                             ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef,
                             ast.Await, ast.Yield, ast.YieldFrom)):
            return True
        if isinstance(node, (ast.Attribute, ast.Subscript)) and isinstance(node.ctx, (ast.Store, ast.Del)):
            return True
        if isinstance(node, ast.Name) and node.id in {"eval", "exec", "__import__", "globals", "locals", "getattr",
                                                       "setattr", "delattr", "importlib", "subprocess",
                                                       "multiprocessing", "ProcessPoolExecutor"}:
            return True
    return False


def classify(before, after):
    """Compare bodies in both ASTs. A localized shape is not a safety certificate."""
    if any(edit_scope._source_encoding(text) != "utf-8" for text in (before, after)):
        return {"status": "unsupported-encoding"}
    old_structure, old = edit_scope._units(before)
    new_structure, new = edit_scope._units(after)
    if old_structure != new_structure or old.keys() != new.keys():
        return {"status": "structural-change"}
    affected = []
    for key, (body, decorated, _, _) in old.items():
        updated = new[key][0]
        if ast.dump(body) == ast.dump(updated):
            continue
        if decorated or key[0].split(".")[-1].startswith("__"):
            return {"status": "decorated-or-special-body"}
        old_statements, new_statements = census(body), census(updated)
        if any(isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
               for node in old_statements + new_statements):
            return {"status": "nested-definition"}
        if [type(s) for s in old_statements] != [type(s) for s in new_statements]:
            return {"status": "ambiguous-statement-alignment"}
        for first, second in zip(old_statements, new_statements, strict=True):
            if header(first) == header(second):
                continue
            if restricted(first) or restricted(second):
                return {"status": "dynamic-or-shared-effect"}
            affected.append({"function": key[0], "old": span(first), "new": span(second)})
    return {"status": "localized-body" if affected else "unchanged-ast", "statements": affected}


def index_statements(root, observations):
    """Map observed test IDs to containing AST statements in pinned source."""
    index = defaultdict(set)
    statements = {}
    for test, path, line in observations:
        source = (root / path).resolve()
        if not source.is_relative_to(root / "sqlglot") or source.suffix != ".py":
            raise ValueError(f"Observation outside the pinned package: {path}")
        if path not in statements:
            statements[path] = census(ast.parse(source.read_bytes()))
        for statement in statements[path]:
            first, _, last, _ = location = span(statement)
            if first <= line <= last:
                index[path, location].add(test)
    return index


def predict(root, changes, index):
    diagnostics = []
    for change in changes:
        path = change["path"]
        try:
            source = (root / path).resolve()
            if not source.is_relative_to(root / "sqlglot") or source.suffix != ".py":
                raise ValueError("Unsupported path")
            before = source.read_text(encoding="utf-8")
            after = edit_scope.reconstruct(before, change)
            if after is None:
                raise ValueError("Source reconstruction failed")
            result = classify(before, after)
        except (OSError, ValueError, SyntaxError, UnicodeError, RecursionError):
            result = {"status": "unsupported-or-unavailable"}
        positive = {test for item in result.get("statements", ())
                    for test in index.get((path, item["old"]), ())}
        diagnostics.append({"path": path, **result, "positive_test_ids": sorted(positive)})
    # A positive-only index carries no closed dependency proof, including
    # for localized edits with reproducible line observations.
    return {"changes": diagnostics, "selection": "retain-baseline", "reason": "dependency closure unproven"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    parser.add_argument("observations", type=Path, help="JSONL triples: test ID, pinned path, executed line")
    parser.add_argument("patches", type=Path)
    args = parser.parse_args()
    root = args.root.resolve()
    revision = subprocess.check_output(
        ["git", "-c", f"safe.directory={root}", "-C", str(root), "rev-parse", "HEAD"], text=True,
    ).strip()
    if revision != PIN:
        raise ValueError("Expected pinned SQLGlot")
    observations = [json.loads(line) for line in args.observations.read_bytes().splitlines()]
    index = index_statements(root, observations)
    for patch in sorted(args.patches.glob("*.patch")):
        print(json.dumps({"commit": patch.stem, **predict(root, parse_patch(patch.read_text()), index)}))


if __name__ == "__main__":
    main()
