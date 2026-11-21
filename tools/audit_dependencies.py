"""Explain the v1 graph's uncertainty without changing either build artifact.

The affected flag describes the location of a finding in the known reverse
closure. Findings outside that closure may still add missing dependency edges.
"""

from __future__ import annotations

import argparse
import ast
import json
from pathlib import Path

from analyzer.dependency_graph import DependencyGraph, _imports, _module, pinned_files


def audit(root: Path, tracked: list[str], changed: set[str]) -> dict:
    paths = sorted(
        path for path in tracked
        if path.endswith(".py") and path.startswith(("sqlglot/", "tests/"))
    )
    modules = {_module(path): path for path in paths}
    graph = DependencyGraph._from_tree(root, tracked)
    impacted = graph.impacted_files(changed)
    external: set[str] = set()
    findings = []

    def record(path: str, kind: str, detail: str) -> None:
        findings.append({
            "path": path, "kind": kind, "detail": detail, "in_known_impact": path in impacted,
        })

    for path in paths:
        source = root / path
        if source.is_symlink() or not source.resolve().is_relative_to(root.resolve()):
            record(path, "unsupported_source", "path is outside the supported checkout")
            continue
        try:
            tree = ast.parse(source.read_bytes(), filename=path)
        except (OSError, SyntaxError, ValueError) as error:
            record(path, "unsupported_source", type(error).__name__)
            continue
        module = _module(path)
        package = module if path.endswith("/__init__.py") else module.rpartition(".")[0]
        imported, incomplete = _imports(tree, package, modules)
        for target in sorted(imported | {package}):
            if not target:
                continue
            if target.partition(".")[0] in {"sqlglot", "tests"}:
                if target not in modules:
                    record(path, "unresolved_internal", target)
            else:
                external.add(target)
        if incomplete:
            record(path, "dynamic_or_unresolved_relative",
                   "contains dynamic execution/loading or an invalid relative import")

    return {
        "external_imports": sorted(external),
        "findings": findings,
        "module_index_complete": len(modules) == len(paths) and bool(paths),
        "unknown_changed_paths": sorted(changed.difference(paths)),
        "uncertain_for_query": graph.is_uncertain(changed),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sqlglot_root", type=Path)
    parser.add_argument("changed_paths", nargs="*")
    args = parser.parse_args()
    result = audit(args.sqlglot_root, pinned_files(args.sqlglot_root), set(args.changed_paths))
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
