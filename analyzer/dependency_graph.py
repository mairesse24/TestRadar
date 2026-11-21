"""Reverse Python import graph generated from a clean, pinned SQLGlot checkout.

Build offline with:
    python -m analyzer.dependency_graph /path/to/sqlglot dependencies.json

Static imports do not establish runtime dependencies from registrations,
reflection, inheritance through dynamic bases, or native extensions.
"""

from __future__ import annotations

import argparse
import ast
import importlib.util
import json
from pathlib import Path
import subprocess

from .contracts import PINNED_SQLGLOT, safe_path
from .dynamic_imports import scan


def pinned_files(root: Path) -> list[str]:
    """Identify tracked paths and verify the build input's provenance."""
    def git(*args: str) -> str:
        return subprocess.check_output(
            ["git", "-C", str(root), *args], text=True, encoding="utf-8",
        )

    if Path(git("rev-parse", "--show-toplevel").strip()).resolve() != root.resolve():
        raise ValueError("expected the SQLGlot repository root")
    if git("rev-parse", "HEAD").strip() != PINNED_SQLGLOT:
        raise ValueError(f"expected SQLGlot at {PINNED_SQLGLOT}")
    if git("status", "--porcelain"):
        raise ValueError("expected a clean SQLGlot checkout")
    return [path for path in git("ls-files", "-z").split("\0") if path]


def write_map(path: Path, document: dict) -> None:
    path.write_text(
        json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n",
        encoding="utf-8", newline="\n",
    )
    path.chmod(0o644)


def read_map(path: str, schema: str) -> dict:
    document = json.loads(Path(path).read_text(encoding="utf-8"))
    if (not isinstance(document, dict) or document.get("schema") != schema
            or document.get("source") != PINNED_SQLGLOT):
        raise ValueError(f"invalid {schema} artifact")
    return document


def _module(path: str) -> str:
    name = path.removesuffix(".py").removesuffix("/__init__")
    return name.replace("/", ".")


def _static_imports(tree: ast.AST, package: str, modules: dict[str, str]) -> tuple[set[str], bool]:
    imported: set[str] = set()
    uncertain = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                try:
                    base = importlib.util.resolve_name("." * node.level + base, package)
                except ImportError:
                    uncertain = True
                    continue
            imported.add(base)
            imported.update(
                f"{base}.{alias.name}" for alias in node.names if f"{base}.{alias.name}" in modules
            )
    return imported, uncertain


def _imports(tree: ast.AST, package: str, modules: dict[str, str]) -> tuple[set[str], bool]:
    """Compatibility helper for the integration branch's dependency audit."""
    imported, incomplete = _static_imports(tree, package, modules)
    return imported, incomplete or any(site.kind != "literal" for site in scan(tree, package, modules))


class DependencyGraph:
    def __init__(self, importers: dict[str, set[str]], uncertain: bool = False):
        self._importers = importers
        self._uncertain = uncertain
        self._findings: list[dict] = []

    @classmethod
    def load(cls, path: str) -> DependencyGraph:
        """Raise OSError or ValueError for unavailable or invalid artifacts."""
        document = read_map(path, "testradar-imports-v1")
        neighbors = document.get("importers")
        uncertain = document.get("uncertain")
        if not isinstance(neighbors, dict) or type(uncertain) is not bool:
            raise ValueError("invalid dependency map")
        for path, importers in neighbors.items():
            if (not safe_path(path) or not isinstance(importers, list)
                    or any(not safe_path(item) or item not in neighbors for item in importers)):
                raise ValueError("invalid dependency edge")
        return cls({path: set(edges) for path, edges in neighbors.items()}, uncertain)

    @classmethod
    def _from_tree(cls, root: Path, paths: list[str]) -> DependencyGraph:
        paths = sorted(
            path for path in paths
            if path.endswith(".py") and path.startswith(("sqlglot/", "tests/"))
        )
        modules = {_module(path): path for path in paths}
        importers: dict[str, set[str]] = {path: set() for path in paths}
        graph = cls(importers, len(modules) != len(paths) or not paths)

        def record(path: str, kind: str, targets: tuple[str, ...] = (), line: int | None = None):
            graph._findings.append({"path": path, "kind": kind, "line": line, "targets": targets})
            if kind != "literal":
                graph._uncertain = True
        for path in paths:
            source = root / path
            if not source.resolve().is_relative_to(root.resolve()) or source.is_symlink():
                record(path, "unsupported_source")
                continue
            try:
                tree = ast.parse(source.read_bytes(), filename=path)
            except (OSError, SyntaxError, ValueError):
                record(path, "unsupported_source")
                continue
            module = _module(path)
            package = module if path.endswith("/__init__.py") else module.rpartition(".")[0]
            imported, incomplete = _static_imports(tree, package, modules)
            if incomplete:
                record(path, "unresolved_relative")
            for site in scan(tree, module, modules):
                record(path, site.kind, site.targets, site.line)
                imported.update(target for target in site.targets if target in modules)
            # Importing a submodule also initializes its parent packages.
            imported.add(package)
            for target in sorted(imported):
                if target.partition(".")[0] not in {"tests", "sqlglot"}:
                    continue
                if target not in modules:
                    record(path, "unresolved_internal", (target,))
                parts = target.split(".")
                for length in range(1, len(parts) + 1):
                    imported_path = modules.get(".".join(parts[:length]))
                    if imported_path is not None and imported_path != path:
                        importers[imported_path].add(path)
        return graph

    @classmethod
    def build(cls, root: Path) -> DependencyGraph:
        return cls._from_tree(root, pinned_files(root))

    def save(self, path: Path) -> None:
        write_map(path, {
            "schema": "testradar-imports-v1",
            "source": PINNED_SQLGLOT,
            "uncertain": self._uncertain,
            "importers": {path: sorted(edges) for path, edges in self._importers.items()},
        })

    def save_evidence(self, path: Path) -> None:
        """Save a build-time explanation alongside the compatible graph artifact."""
        write_map(path, {
            "schema": "testradar-import-evidence-v1",
            "source": PINNED_SQLGLOT,
            "uncertain": self._uncertain,
            "findings": self._findings,
        })

    def is_uncertain(self, changed_paths: set[str]) -> bool:
        """Flag incomplete static analysis or queried paths outside the graph."""
        return self._uncertain or not changed_paths or not changed_paths <= self._importers.keys()

    def impacted_files(self, changed_paths: set[str]) -> set[str]:
        impacted = set(changed_paths)
        pending = list(changed_paths)
        while pending:
            for importer in self._importers.get(pending.pop(), ()):
                if importer not in impacted:
                    impacted.add(importer)
                    pending.append(importer)
        return impacted


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sqlglot_root", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--evidence", type=Path, help="optional JSON report for build-time review")
    args = parser.parse_args()
    graph = DependencyGraph.build(args.sqlglot_root)
    graph.save(args.output)
    if args.evidence is not None:
        graph.save_evidence(args.evidence)


if __name__ == "__main__":
    main()
