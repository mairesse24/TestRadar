"""Static evidence for direct dynamic imports.

Literal and package-prefix edges are useful for impact reporting. Runtime
rebinding, aliases, hooks and arbitrary expressions can hide further edges.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from importlib.util import resolve_name


@dataclass(frozen=True)
class DynamicSite:
    line: int
    kind: str
    targets: tuple[str, ...] = ()


def scan(tree: ast.AST, module: str, modules: dict[str, str]) -> list[DynamicSite]:
    aliases = {"__import__": "__import__"}
    namespaces: set[str] = set()
    bindings: set[str] = set()
    wildcard = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                name = alias.asname or alias.name.partition(".")[0]
                if alias.name == "importlib":
                    namespaces.add(name)
                else:
                    bindings.add(name)
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                name = alias.asname or alias.name
                if node.level == 0 and node.module == "importlib" and alias.name == "import_module":
                    aliases[name] = "import_module"
                elif alias.name == "*":
                    wildcard = True
                else:
                    bindings.add(name)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            bindings.add(node.id)
        elif (
            isinstance(node, ast.Attribute) and isinstance(node.ctx, (ast.Store, ast.Del))
            and isinstance(node.value, ast.Name)
        ):
            bindings.add(node.value.id)
        elif isinstance(node, ast.arg):
            bindings.add(node.arg)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            bindings.add(node.name)
        elif isinstance(node, (ast.ExceptHandler, ast.MatchAs, ast.MatchStar)) and node.name:
            bindings.add(node.name)
        elif isinstance(node, ast.MatchMapping) and node.rest:
            bindings.add(node.rest)

    def constant(node: ast.AST) -> str | None:
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        return None

    def prefix(node: ast.AST) -> str:
        if not isinstance(node, ast.JoinedStr):
            return ""
        parts = []
        for value in node.values:
            literal = constant(value)
            if literal is not None:
                parts.append(literal)
            elif (
                isinstance(value, ast.FormattedValue)
                and isinstance(value.value, ast.Name) and value.value.id == "__name__"
                and value.conversion == -1 and value.format_spec is None
                and "__name__" not in bindings
            ):
                parts.append(module)
            else:
                break
        return "".join(parts)

    sites = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        callee = node.func
        trusted = False
        if isinstance(callee, ast.Name):
            name = aliases.get(callee.id, callee.id)
            trusted = callee.id in aliases and callee.id not in bindings
        elif isinstance(callee, ast.Attribute):
            name = callee.attr
            trusted = (
                name == "import_module" and isinstance(callee.value, ast.Name)
                and callee.value.id in namespaces and callee.value.id not in bindings
            )
            # SQLGlot's context.eval is a method. Its actual Python eval is
            # a separate bare call and is still marked unknown below.
            if name == "eval" and not (
                isinstance(callee.value, ast.Name) and callee.value.id in {"builtins", "__builtins__"}
            ):
                continue
        else:
            continue
        if name not in {"__import__", "import_module", "eval", "exec", "exec_module"}:
            continue
        if wildcard or not trusted or not node.args or any(
            isinstance(arg, ast.Starred) for arg in node.args
        ) or any(arg.arg is None for arg in node.keywords):
            sites.append(DynamicSite(node.lineno, "unknown"))
            continue
        if name == "__import__" and (
            len(node.args) > 4 or any(arg.arg == "level" for arg in node.keywords)
        ):
            sites.append(DynamicSite(node.lineno, "unknown"))
            continue
        target = constant(node.args[0])
        if target and target.startswith(".") and name == "import_module":
            package = constant(node.args[1]) if len(node.args) == 2 else next(
                (constant(kw.value) for kw in node.keywords if kw.arg == "package"), None,
            )
            try:
                target = resolve_name(target, package) if package else None
            except ImportError:
                target = None
        if target in modules:
            sites.append(DynamicSite(node.lineno, "literal", (target,)))
        elif target and not target.startswith(".") and target.partition(".")[0] in {"sqlglot", "tests"}:
            sites.append(DynamicSite(node.lineno, "unresolved_internal", (target,)))
        else:
            # Prefix expansion adds observed package members as candidates.
            # Custom registration or import hooks can supply other targets.
            start = prefix(node.args[0])
            candidates = tuple(sorted(name for name in modules if start.endswith(".") and name.startswith(start)))
            sites.append(DynamicSite(node.lineno, "bounded_prefix" if candidates else "unknown", candidates))
    return sorted(sites, key=lambda site: (site.line, site.kind, site.targets))
