"""Conservative static evidence for initialization-sensitive Python edits."""

from __future__ import annotations

import ast
from collections import defaultdict
from functools import lru_cache
from importlib.util import resolve_name
from pathlib import Path

from analyzer import edit_scope

Symbol = tuple[str, str]


def _name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = _name(node.value)
        if prefix is not None:
            return f"{prefix}.{node.attr}"
    return None


def _references(node: ast.AST) -> set[str]:
    return {name for item in ast.walk(node) if (name := _name(item)) is not None}


def _module(path: str) -> str:
    parts = path.removesuffix(".py").split("/")
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


class _Index(ast.NodeVisitor):
    def __init__(self, module: str, is_package: bool, tree: ast.Module):
        self.module = module
        self.package = module if is_package else module.rpartition(".")[0]
        self.roots: set[str] = set()
        self.edges: dict[str, set[str]] = defaultdict(set)
        self.imports: dict[str, set[str]] = defaultdict(set)
        self.classes: list[str] = []
        self.visit(tree)

    def visit_Import(self, node: ast.Import) -> None:
        for item in node.names:
            name = item.asname or item.name.split(".", 1)[0]
            self.imports[name].add(item.name if item.asname else name)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        target = node.module or ""
        if node.level:
            try:
                target = resolve_name("." * node.level + target, self.package)
            except ImportError:
                return
        for item in node.names:
            self.imports[item.asname or item.name].add(f"{target}.{item.name}")

    def visit_Call(self, node: ast.Call) -> None:
        # Arguments may be invoked by the callee, including registrations.
        self.roots.update(_references(node))

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        for decorator in node.decorator_list:
            self.roots.update(_references(decorator))
        for base in node.bases:
            self.visit(base)
            if name := _name(base):
                self.roots.add(f"{name}.__init_subclass__")
        for keyword in node.keywords:
            self.visit(keyword.value)
            if keyword.arg == "metaclass" and (name := _name(keyword.value)):
                self.roots.update(f"{name}.{hook}" for hook in ("__prepare__", "__new__", "__init__"))
        qualified = ".".join((*self.classes, node.name))
        self.edges[qualified].update(f"{qualified}.{hook}" for hook in ("__new__", "__init__"))
        self.edges[qualified].update(name for base in node.bases if (name := _name(base)))
        self.classes.append(node.name)
        for statement in node.body:
            self.visit(statement)
        self.classes.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        qualified = ".".join((*self.classes, node.name))
        for decorator in node.decorator_list:
            self.roots.update(_references(decorator))
        self.visit(node.args)
        if node.returns is not None:
            self.visit(node.returns)
        # References inside an eager function are positive candidates, even
        # if this analysis cannot resolve which branch or callback runs.
        for statement in node.body:
            self.edges[qualified].update(_references(statement))
            for item in ast.walk(statement):
                if isinstance(item, (ast.Import, ast.ImportFrom)):
                    self.visit(item)
        if self.classes:
            self.edges[node.name].add(qualified)

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_Lambda(self, node: ast.Lambda) -> None:
        self.visit(node.args)

    def visit_Assign(self, node: ast.Assign) -> None:
        self.visit(node.value)
        if name := _name(node.value):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    self.edges[target.id].add(name)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        self.visit(node.annotation)
        if node.value is not None:
            self.visit(node.value)
            if isinstance(node.target, ast.Name) and (name := _name(node.value)):
                self.edges[node.target.id].add(name)

    def resolve(self, name: str, modules: dict[str, _Index]):
        yield self.module, name
        if "." in name:
            yield self.module, name.rsplit(".", 1)[-1]
        head, sep, tail = name.partition(".")
        for imported in self.imports.get(head, ()):
            target = imported + sep + tail
            module, _, symbol = target.rpartition(".")
            while module and module not in modules:
                module, _, rest = module.rpartition(".")
                symbol = f"{rest}.{symbol}"
            if module and symbol:
                yield module, symbol


@lru_cache(maxsize=1)
def _pinned_index(root: Path) -> dict[str, _Index]:
    result = {}
    for source in sorted((root / "sqlglot").rglob("*.py")):
        module = _module(source.relative_to(root).as_posix())
        result[module] = _Index(module, source.name == "__init__.py", ast.parse(source.read_bytes()))
    if "sqlglot" not in result:
        raise ValueError("Pinned package unavailable")
    return result


def _reachable(modules: dict[str, _Index]) -> set[Symbol]:
    pending = [(module, name) for module, index in modules.items() for name in index.roots]
    seen: set[Symbol] = set()
    while pending:
        module, name = pending.pop()
        if (module, name) in seen:
            continue
        seen.add((module, name))
        index = modules[module]
        for target, symbol in index.resolve(name, modules):
            if symbol.count(".") > 12:
                raise ValueError("Initialization alias expansion exceeded its depth limit")
            if (target, symbol) != (module, name):
                pending.append((target, symbol))
        pending.extend((module, ref) for ref in index.edges.get(name, ()))
    return seen


@lru_cache(maxsize=1)
def _pinned_reachable(root: Path) -> frozenset[Symbol]:
    return frozenset(_reachable(_pinned_index(root)))


def reasons(root: Path | None, changes: list[dict]) -> list[str]:
    relevant = [change for change in changes if change["status"] == "modified"
                and change["path"].startswith("sqlglot/") and change["path"].endswith(".py")]
    if not relevant:
        return []
    if root is None:
        return ["initialization analysis: pinned source unavailable"]
    edited: dict[str, set[str]] = {}
    overlays: dict[str, _Index] = {}
    try:
        for change in relevant:
            path = change["path"]
            module = _module(path)
            if module in overlays or change["uncertain"]:
                return [f"initialization analysis: ambiguous source changes in {path}"]
            old = (root / path).read_text(encoding="utf-8")
            new = edit_scope.reconstruct(old, change)
            if new is None or any(edit_scope._source_encoding(text) != "utf-8" for text in (old, new)):
                return [f"initialization analysis: cannot reconstruct {path}"]
            _, before = edit_scope._units(old)
            _, after = edit_scope._units(new)
            names = {key[0] for key in before.keys() ^ after.keys()}
            for key in before.keys() & after.keys():
                # Headers remain in the baseline structural policy. Bodies
                # include nested definitions, which can escape via registries.
                if ast.dump(before[key][0]) != ast.dump(after[key][0]):
                    names.add(key[0])
            edited[module] = names
            overlays[module] = _Index(module, path.endswith("/__init__.py"), ast.parse(new))
        if not any(edited.values()):
            return []
        reachable = _pinned_reachable(root) | _reachable(_pinned_index(root) | overlays)
    except (OSError, SyntaxError, UnicodeError, RecursionError, ValueError):
        return ["initialization analysis: pinned or edited source could not be indexed"]
    return [
        f"initialization analysis: {module}.{name} may run during initialization"
        for module, names in edited.items()
        for name in sorted(names)
        if (module, name) in reachable
    ]
