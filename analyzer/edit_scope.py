"""Classify what a modified Python file's edit can change at runtime.

The pinned sqlglot source is available to the selector, so a modified
module can be rebuilt exactly from the patch and both versions parsed.
Two facts are useful and can be proven from the syntax trees alone:

* ``equivalent``: the module's syntax tree is unchanged (comments, blank
  lines, line breaks or other formatting only).  CPython compiles the same
  code, so no test can observe the edit through behaviour.
* ``bodies``: everything outside function bodies is unchanged (imports,
  class bodies, signatures, defaults, decorators, module statements), so
  importing the module and defining its classes runs exactly the same code
  unless one of the edited functions is itself called while importing or
  while constructing a dialect or generator.  ``construction_safe`` is
  True only when none of the edited functions can be reached that way.

Anything else, or any doubt (unreadable source, a patch that does not
match the pinned file, a syntax error), yields ``None`` so callers keep
their existing conservative behaviour.
"""

from __future__ import annotations

import ast
import io
import os
import tokenize
from dataclasses import dataclass, field
from pathlib import Path

# Names whose bodies run when a module is imported, a class is created, a
# dialect is resolved or a generator is constructed.  Edited functions with
# these names (or reached from them by name) are never construction-safe.
_CONSTRUCTION_ENTRY = frozenset({
    "__init__", "__new__", "__init_subclass__", "__call__", "__set_name__",
    "__class_getitem__", "__getattr__", "__getattribute__", "__post_init__",
    "get_or_raise", "generator", "_build_dispatch", "__prepare__",
})
# Core modules whose constructors define what "constructing a dialect or
# generator" executes.
_CONSTRUCTION_MODULES = (
    "sqlglot/dialects/dialect.py", "sqlglot/generator.py", "sqlglot/parser.py",
    "sqlglot/tokens.py", "sqlglot/__init__.py",
)


@dataclass
class EditScope:
    kind: str  # "equivalent" or "bodies"
    functions: set[str] = field(default_factory=set)
    construction_safe: bool = False
    detail: str = ""


def pinned_root() -> Path | None:
    for candidate in (os.environ.get("TESTRADAR_PINNED"), "/app/pinned"):
        if candidate and (Path(candidate) / "sqlglot" / "__init__.py").is_file():
            return Path(candidate)
    return None


def reconstruct(old_text: str, change: dict) -> str | None:
    """Rebuild the new file from the pinned file and one parsed change."""
    if change.get("uncertain") or change.get("status") != "modified":
        return None
    old = old_text.split("\n")
    removed = change.get("old_lines") or []
    added = change.get("new_lines") or []
    removed_text = change.get("removed_text") or []
    added_text = change.get("added_text") or []
    if len(removed) != len(removed_text) or len(added) != len(added_text):
        return None
    for number, text in zip(removed, removed_text):
        if not 1 <= number <= len(old) or old[number - 1] != text:
            return None
    gone = set(removed)
    kept = [line for i, line in enumerate(old, 1) if i not in gone]
    total = len(kept) + len(added)
    new_at = dict(zip(added, added_text))
    if len(new_at) != len(added) or any(not 1 <= n <= total for n in added):
        return None
    out, source = [], iter(kept)
    for number in range(1, total + 1):
        if number in new_at:
            out.append(new_at[number])
        else:
            out.append(next(source))
    return "\n".join(out)


def _dump(node: ast.AST) -> str:
    return ast.dump(node, annotate_fields=True, include_attributes=False)


def _units(text: str) -> tuple[str, dict]:
    """Parse ``text`` and return (skeleton dump without function bodies,
    {(qualified name, ordinal): (body, decorated, first line, last line)})."""
    bodies: dict = {}

    class Strip(ast.NodeTransformer):
        def __init__(self) -> None:
            self.scope: list[str] = []

        def visit_ClassDef(self, node):
            self.scope.append(node.name)
            self.generic_visit(node)
            self.scope.pop()
            return node

        def _function(self, node):
            name = ".".join(self.scope + [node.name])
            ordinal = sum(1 for key in bodies if key[0] == name)
            # Same-named definitions (overloads, redefinitions) are kept
            # apart by their order; the skeleton comparison pins that order.
            bodies[(name, ordinal)] = (
                ast.Module(body=node.body, type_ignores=[]), bool(node.decorator_list),
                node.lineno, node.end_lineno or node.lineno,
            )
            node.body = [ast.Pass()]
            return node  # nested defs stay inside the stripped body

        visit_FunctionDef = _function
        visit_AsyncFunctionDef = _function

    skeleton = Strip().visit(ast.parse(text))
    return _dump(skeleton), bodies


def _import_time_names(tree: ast.Module) -> set[str]:
    """Names used in a call (as callee or argument) outside function bodies."""
    names: set[str] = set()

    def collect(node: ast.AST) -> None:
        for sub in ast.walk(node):
            if isinstance(sub, ast.Name):
                names.add(sub.id)
            elif isinstance(sub, ast.Attribute):
                names.add(sub.attr)

    def visit(statements: list[ast.stmt]) -> None:
        for stmt in statements:
            if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for part in (*stmt.decorator_list, stmt.args, stmt.returns):
                    if part is not None:
                        for sub in ast.walk(part):
                            if isinstance(sub, ast.Call):
                                collect(sub)
                continue
            if isinstance(stmt, ast.ClassDef):
                for part in (*stmt.decorator_list, *stmt.bases, *stmt.keywords):
                    collect(part)
                visit(stmt.body)
                continue
            for sub in ast.walk(stmt):
                if isinstance(sub, ast.Call):
                    collect(sub)

    visit(tree.body)
    return names


_CONSTRUCTION_CACHE: dict[str, frozenset[str]] = {}


def construction_names(root: Path) -> frozenset[str]:
    """Function names reachable (by name) from construction entry points."""
    key = str(root)
    if key in _CONSTRUCTION_CACHE:
        return _CONSTRUCTION_CACHE[key]
    functions: dict[str, list[ast.AST]] = {}
    for rel in _CONSTRUCTION_MODULES:
        path = root / rel
        if not path.is_file():
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                functions.setdefault(node.name, []).append(node)
    reached = set(_CONSTRUCTION_ENTRY)
    pending = [name for name in reached if name in functions]
    while pending:
        for node in functions.pop(pending.pop(), []):
            for sub in ast.walk(node):
                name = None
                if isinstance(sub, ast.Call):
                    func = sub.func
                    name = func.id if isinstance(func, ast.Name) else (
                        func.attr if isinstance(func, ast.Attribute) else None)
                elif isinstance(sub, ast.Attribute):
                    name = sub.attr  # properties run on attribute access
                if name and name not in reached:
                    reached.add(name)
                    if name in functions:
                        pending.append(name)
    _CONSTRUCTION_CACHE[key] = frozenset(reached)
    return _CONSTRUCTION_CACHE[key]


def _source_encoding(text: str) -> str | None:
    """The encoding CPython would use for ``text`` saved as UTF-8 bytes.

    A PEP 263 cookie (or a BOM) changes how the interpreter decodes the
    file, which the text-level syntax tree cannot see, so both versions must
    decode as plain UTF-8.
    """
    try:
        encoding, _ = tokenize.detect_encoding(io.BytesIO(text.encode("utf-8")).readline)
    except (SyntaxError, LookupError, UnicodeError):
        return None
    return encoding


def classify(change: dict, root: Path | None = None, *, analyze_bodies: bool = True) -> EditScope | None:
    """Classify one change; ``analyze_bodies=False`` skips the construction
    analysis (callers that only use ``equivalent`` results)."""
    root = root or pinned_root()
    path = change.get("path") if isinstance(change, dict) else None
    if root is None or not path or not path.startswith("sqlglot/") or not path.endswith(".py"):
        return None
    if change.get("old_path", path) != path:
        return None
    source = root / path
    try:
        old_text = source.read_text(encoding="utf-8")
        new_text = reconstruct(old_text, change)
        if new_text is None:
            return None
        if _source_encoding(old_text) != "utf-8" or _source_encoding(new_text) != "utf-8":
            return None
        if "\ufeff" in old_text or "\ufeff" in new_text:
            return None
        old_skeleton, old_bodies = _units(old_text)
        new_skeleton, new_bodies = _units(new_text)
    except (OSError, SyntaxError, ValueError, UnicodeError, RecursionError):
        return None
    if old_skeleton != new_skeleton or old_bodies.keys() != new_bodies.keys():
        return None
    # A function none of whose lines were removed (old file) or added (new
    # file) has identical text on both sides; only touched bodies are
    # compared. The syntax tree is unchanged exactly when the skeletons and
    # every body agree.
    removed, added = set(change.get("old_lines") or ()), set(change.get("new_lines") or ())
    changed: set[str] = set()
    decorated: set[str] = set()
    for key, (body, has_decorators, first, last) in old_bodies.items():
        other, other_decorators, new_first, new_last = new_bodies[key]
        touched = (
            any(first <= n <= last for n in removed)
            or any(new_first <= n <= new_last for n in added)
            or last - first != new_last - new_first
        )
        if touched and _dump(body) != _dump(other):
            changed.add(key[0])
            if has_decorators or other_decorators:
                decorated.add(key[0])
    if not changed:
        return EditScope("equivalent", detail="syntax tree unchanged")
    if not analyze_bodies:
        return EditScope("bodies", changed, construction_safe=False,
                         detail="bodies of " + ", ".join(sorted(changed)))
    entry = construction_names(root)
    import_time = _import_time_names(ast.parse(old_text)) | _import_time_names(ast.parse(new_text))
    risky = []
    for name in changed:
        short = name.rsplit(".", 1)[-1]
        if short in entry or short in import_time or name in decorated or short.startswith("__"):
            risky.append(name)
    return EditScope(
        "bodies", changed, construction_safe=not risky,
        detail=("bodies of " + ", ".join(sorted(changed))
                + (f"; construction/import reachable: {', '.join(sorted(risky))}" if risky else "")),
    )

