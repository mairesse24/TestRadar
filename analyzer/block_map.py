"""Function-entry dependencies for the opt-in ``blocks`` policy.

The artifact is built from the pinned SQLGlot tree and a main-thread (plus
new-thread) trace of the pinned tests. It is positive evidence with the same
holes as ``analyzer.coverage_map``: collection, native code, processes that
were already alive, and paths the suite never took are invisible.

A change is eligible only when every hunk is a modified ``sqlglot`` function
body, the tokens differ by more than comments and string literals, and each
changed function was actually entered. Eligible changes replace the balanced
selection with tests that entered one of those functions, tests the trace
never observed, and tests in files that started a process pool (those files
stay at the balanced decision). Anything else returns ``None`` and the caller
keeps balanced.
"""

from __future__ import annotations

import ast
import io
import tokenize
from pathlib import Path
from typing import Iterable

from .contracts import PINNED_SQLGLOT, safe_path
from .dependency_graph import read_map, write_map

_SKIP_TOKENS = {
    tokenize.COMMENT,
    tokenize.NL,
    tokenize.NEWLINE,
    tokenize.ENCODING,
    tokenize.ENDMARKER,
    tokenize.INDENT,
    tokenize.DEDENT,
    tokenize.NL,
}
_STRING_TOKENS = {tokenize.STRING}
for _name in ("FSTRING_START", "FSTRING_MIDDLE", "FSTRING_END"):
    _token = getattr(tokenize, _name, None)
    if _token is not None:
        _STRING_TOKENS.add(_token)

_SIGNATURE = __import__("re").compile(r"^\s*(?:@|(?:async\s+)?(?:def|class)\b)")


class BlockMap:
    def __init__(
        self,
        functions: dict[str, frozenset[int]],
        observed: frozenset[int],
        opaque_files: frozenset[str],
        ranges: dict[str, tuple[tuple[str, int, int, int], ...]],
        uncertain_tests: frozenset[int] = frozenset(),
    ):
        self.functions = functions
        self.observed = observed
        self.opaque_files = opaque_files
        self.ranges = ranges
        self.uncertain_tests = uncertain_tests

    @classmethod
    def load(cls, path: str) -> "BlockMap":
        document = read_map(path, "testradar-blocks-v1")
        raw_functions = document.get("functions")
        raw_ranges = document.get("ranges")
        observed = document.get("observed")
        opaque = document.get("opaque_files")
        uncertain = document.get("uncertain_tests", [])
        if (
            not isinstance(raw_functions, dict)
            or not isinstance(raw_ranges, dict)
            or not _ints(observed)
            or not _ints(uncertain)
            or not isinstance(opaque, list)
            or not all(isinstance(item, str) for item in opaque)
        ):
            raise ValueError("invalid block map")
        functions: dict[str, frozenset[int]] = {}
        for key, tests in raw_functions.items():
            if not isinstance(key, str) or not _ints(tests):
                raise ValueError("invalid block map")
            functions[key] = frozenset(tests)
        ranges: dict[str, tuple[tuple[str, int, int, int], ...]] = {}
        for path, rows in raw_ranges.items():
            if not safe_path(path) or not isinstance(rows, list):
                raise ValueError("invalid block map")
            parsed = []
            for row in rows:
                if (
                    not isinstance(row, list)
                    or len(row) != 4
                    or not isinstance(row[0], str)
                    or not all(isinstance(item, int) and not isinstance(item, bool) for item in row[1:])
                ):
                    raise ValueError("invalid block map")
                parsed.append((row[0], row[1], row[2], row[3]))
            ranges[path] = tuple(parsed)
        return cls(functions, frozenset(observed), frozenset(opaque), ranges, frozenset(uncertain))

    def save(self, path: Path) -> None:
        temporary = path.with_suffix(path.suffix + ".tmp")
        write_map(temporary, {
            "schema": "testradar-blocks-v1",
            "source": PINNED_SQLGLOT,
            "observed": sorted(self.observed),
            "opaque_files": sorted(self.opaque_files),
            "uncertain_tests": sorted(self.uncertain_tests),
            "functions": {key: sorted(tests) for key, tests in sorted(self.functions.items())},
            "ranges": {
                path: [list(row) for row in rows]
                for path, rows in sorted(self.ranges.items())
            },
        })
        temporary.replace(path)

    def narrow(self, balanced: set[int], changes: list[dict], tests: list[dict]) -> set[int] | None:
        """Return a replacement selection, or None to keep ``balanced``."""
        if not isinstance(changes, list):
            return None
        source_change = any(
            safe_path(change.get("path")) and change["path"].startswith("sqlglot/")
            and change["path"].endswith(".py") for change in changes if isinstance(change, dict)
        )
        keys = self._affected(changes)
        guarded = self.uncertain_tests & {
            test["number"] for test in tests
            if isinstance(test, dict) and type(test.get("number")) is int
        } if source_change else set()
        conservative = (balanced | guarded) if guarded - balanced else None
        if not keys:
            return conservative
        entrants: set[int] = set()
        for key in keys:
            seen = self.functions.get(key)
            if not seen:
                return conservative
            entrants |= seen
        numbers = {
            test["number"]
            for test in tests
            if isinstance(test, dict) and isinstance(test.get("number"), int)
            and not isinstance(test.get("number"), bool)
        }
        files = {
            test["number"]: test.get("file")
            for test in tests
            if isinstance(test, dict) and isinstance(test.get("number"), int)
        }
        unobserved = numbers - self.observed
        opaque = {
            number for number, filename in files.items()
            if isinstance(filename, str) and filename in self.opaque_files
        }
        selected = (entrants | unobserved | (balanced & opaque) | guarded) & numbers
        if not selected or selected == balanced:
            return None
        return selected

    def _affected(self, changes: list[dict]) -> set[str] | None:
        if not isinstance(changes, list) or not changes:
            return None
        keys: set[str] = set()
        for change in changes:
            if not isinstance(change, dict) or change.get("uncertain") or change.get("status") != "modified":
                return None
            path = change.get("path")
            if not isinstance(path, str) or not path.endswith(".py") or not path.startswith("sqlglot/"):
                return None
            matched = _qualifying_functions(change, self.ranges.get(path, ()))
            if matched is None:
                return None
            keys.update(f"{path}:{name}" for name in matched)
        return keys or None


def index_functions(source: str) -> tuple[tuple[str, int, int, int], ...]:
    """``(qualname, def_line, body_start, body_end)`` for each function.

    Qualnames follow ``code.co_qualname``, including ``<locals>`` for functions
    and classes nested in functions. The body starts at the first statement,
    so a change to the ``def`` line or a decorator is not inside the body.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return ()
    found: list[tuple[str, int, int, int]] = []

    class Visitor(ast.NodeVisitor):
        def __init__(self) -> None:
            self.stack: list[tuple[str, str]] = []

        def visit_ClassDef(self, node: ast.ClassDef) -> None:
            self.stack.append(("class", node.name))
            self.generic_visit(node)
            self.stack.pop()

        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            self._function(node)

        def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
            self._function(node)

        def _function(self, node: ast.AST) -> None:
            name = getattr(node, "name", "<lambda>")
            self.stack.append(("func", name))
            body = getattr(node, "body", [])
            end = getattr(node, "end_lineno", None)
            decorators = getattr(node, "decorator_list", None) or []
            # CPython's co_firstlineno is the first decorator line when the
            # function is decorated, and the def line otherwise.
            identity = decorators[0].lineno if decorators else node.lineno
            if body and end is not None:
                found.append((_qualname(self.stack), identity, body[0].lineno, end))
            self.generic_visit(node)
            self.stack.pop()

    Visitor().visit(tree)
    return tuple(found)


def index_tree(root: Path) -> dict[str, tuple[tuple[str, int, int, int], ...]]:
    ranges: dict[str, tuple[tuple[str, int, int, int], ...]] = {}
    for path in sorted((root / "sqlglot").rglob("*.py")):
        relative = path.relative_to(root).as_posix()
        indexed = index_functions(path.read_text(encoding="utf-8"))
        if indexed:
            ranges[relative] = indexed
    return ranges


def _qualname(stack: list[tuple[str, str]]) -> str:
    parts: list[str] = []
    for index, (kind, name) in enumerate(stack):
        if index and stack[index - 1][0] == "func":
            parts.append("<locals>")
        parts.append(name)
        del kind
    return ".".join(parts)


def _qualifying_functions(
    change: dict,
    ranges: Iterable[tuple[str, int, int, int]],
) -> set[str] | None:
    added = _lines(change, "added_text")
    removed = _lines(change, "removed_text")
    old_lines = change.get("old_lines")
    if not isinstance(old_lines, list) or len(added) != len(removed) or len(old_lines) != len(removed):
        return None
    if not removed:
        return None
    if _comment_or_string_only(added, removed):
        return None
    if any(_SIGNATURE.match(line) for line in added + removed):
        return None
    table = tuple(ranges)
    if not table:
        return None
    names: set[str] = set()
    for line in old_lines:
        if not isinstance(line, int) or isinstance(line, bool):
            return None
        match = _innermost(line, table)
        if match is None:
            return None
        names.add(f"{match[0]}@{match[1]}")
    return names or None


def _innermost(
    line: int,
    ranges: tuple[tuple[str, int, int, int], ...],
) -> tuple[str, int, int, int] | None:
    if any(row[1] == line for row in ranges):
        return None
    containing = [row for row in ranges if row[2] <= line <= row[3]]
    if not containing:
        return None
    return min(containing, key=lambda row: (row[3] - row[2], row[2]))


def _comment_or_string_only(added: list[str], removed: list[str]) -> bool:
    if all(_blank_or_comment(line) for line in added + removed):
        return True
    left = _code_tokens("\n".join(removed) + "\n")
    right = _code_tokens("\n".join(added) + "\n")
    return left is not None and left == right


def _blank_or_comment(line: str) -> bool:
    text = line.strip()
    return text == "" or text.startswith("#")


def _code_tokens(text: str) -> tuple | None:
    tokens = []
    try:
        for token in tokenize.generate_tokens(io.StringIO(text).readline):
            if token.type in _SKIP_TOKENS or token.type in _STRING_TOKENS:
                continue
            tokens.append((token.type, token.string))
    except (tokenize.TokenError, IndentationError, SyntaxError):
        return None
    return tuple(tokens)


def _lines(change: dict, key: str) -> list[str]:
    value = change.get(key)
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        return []
    return value


def _ints(value: object) -> bool:
    return isinstance(value, list) and all(
        isinstance(item, int) and not isinstance(item, bool) for item in value
    )
