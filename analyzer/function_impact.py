"""Classify line-preserving SQLGlot edits against a pinned source snapshot."""

from __future__ import annotations

import ast
from dataclasses import dataclass
from io import StringIO
from pathlib import Path
import tokenize

from .contracts import safe_path


@dataclass(frozen=True)
class Impact:
    scope: str
    functions: tuple[tuple[str, int], ...] = ()
    equivalent: bool = False


def _comments(text: str) -> list[str]:
    return [token.string for token in tokenize.generate_tokens(StringIO(text).readline)
            if token.type == tokenize.COMMENT]


def _body_lines(tree: ast.AST, touched: set[int]) -> dict[int, int]:
    owners: dict[int, int] = {}
    functions = [node for node in ast.walk(tree)
                 if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))]
    # Narrower, nested bodies overwrite enclosing scopes. A nested definition
    # header is explicitly blocked after collecting all containing bodies.
    functions.sort(key=lambda node: node.end_lineno - node.lineno, reverse=True)
    for node in functions:
        entry = min([node.lineno, *(item.lineno for item in node.decorator_list)])
        first = node.body[0].lineno
        for line in touched:
            if first <= line <= node.end_lineno and line > node.lineno:
                owners[line] = entry
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            entry = min([node.lineno, *(item.lineno for item in node.decorator_list)])
            first = max(node.lineno + 1, node.body[0].lineno)
            for line in touched:
                if entry <= line < first:
                    owners.pop(line, None)
                elif isinstance(node, ast.ClassDef) and first <= line <= node.end_lineno:
                    # A nested class body runs while its enclosing function
                    # runs, but its direct statements are still structural.
                    owner = owners.get(line, 0)
                    if owner <= node.lineno:
                        owners.pop(line, None)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for line in touched:
                if node.lineno <= line <= node.end_lineno:
                    owners.pop(line, None)
    return owners


def classify(root: Path, changes: list[dict]) -> Impact:
    """Require verified replacement text, then compare scopes and positioned ASTs."""
    functions: set[tuple[str, int]] = set()
    equivalent = True
    if not changes:
        return Impact("unknown")
    for change in changes:
        path = change.get("path")
        if (change.get("uncertain") or not safe_path(path)
                or change.get("status") != "modified"
                or path != change.get("old_path") or path != change.get("new_path")
                or not path.startswith("sqlglot/") or not path.endswith(".py")):
            return Impact("unknown")
        old_lines, new_lines = change["old_lines"], change["new_lines"]
        if (not old_lines or old_lines != new_lines or len(old_lines) != len(set(old_lines))
                or not (len(old_lines) == len(change["added_text"]) == len(change["removed_text"]))):
            return Impact("structural")
        file = root / path
        try:
            if not file.resolve().is_relative_to(root.resolve()) or file.stat().st_size > 1_000_000:
                return Impact("unknown")
            source = file.read_text(encoding="utf-8")
            replaced = source.split("\n")
            for line, old, new in zip(old_lines, change["removed_text"], change["added_text"]):
                if not 1 <= line <= len(replaced) or replaced[line - 1] != old:
                    return Impact("unknown")
                replaced[line - 1] = new
            modified = "\n".join(replaced)
            before, after = ast.parse(source, type_comments=True), ast.parse(modified, type_comments=True)
            touched = set(old_lines)
            old_owners, new_owners = _body_lines(before, touched), _body_lines(after, touched)
            if old_owners.keys() != touched or old_owners != new_owners:
                return Impact("structural")
            functions.update((path, entry) for entry in old_owners.values())
            equivalent &= (
                ast.dump(before, include_attributes=True) == ast.dump(after, include_attributes=True)
                and _comments(source) == _comments(modified)
            )
        except (OSError, ValueError, SyntaxError, tokenize.TokenError, RecursionError):
            return Impact("unknown")
    return Impact("function_body", tuple(sorted(functions)), equivalent)
