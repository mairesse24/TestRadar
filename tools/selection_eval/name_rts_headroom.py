"""Offline eacab51 cost audit; counterfactual removals are not selector rules."""

from __future__ import annotations

import argparse
from collections import Counter
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from analyzer import edit_scope
from analyzer.scheduler import duration_milliseconds, schedule_tests
import selector


MEMORY = "tests/test_generator.py::TestGenerator::test_generator_memory"
TPCDS = "tests/test_executor.py::TestExecutor::test_execute_tpcds"
MAX_NODES = "tests/test_parser.py::TestParser::test_max_nodes"


def profile(data: Path, maps: Path) -> dict:
    tests = json.loads((data / "tests.json").read_bytes())["tests"]
    names = {row["id"]: row["number"] for row in tests}
    durations = {row["number"]: duration_milliseconds(row["seconds"]) for row in tests}
    graph, coverage = selector.load_analysis_maps(maps)
    if graph is None or coverage is None or edit_scope.pinned_root() is None:
        raise ValueError("the audit requires pinned source and both reference maps")

    def evaluate(numbers: set[int]) -> tuple[int, int]:
        workers = schedule_tests(numbers, tests)
        return max((sum(durations[number] for number in worker) for worker in workers), default=0) + (
            2000 * len(workers)
        ), len(workers)

    rows = {}
    totals = Counter()
    for patch in sorted((data / "commits").glob("*.patch")):
        selection, hard = selector.analyze_selective(patch, tests, graph, coverage, "balanced")
        cost, workers = evaluate(selection.numbers)
        scopes = []
        for change in selector._read_changes(patch):
            scope = edit_scope.classify(change)
            scopes.append({"path": change["path"], "kind": scope.kind if scope else "unknown",
                           "functions": sorted(scope.functions) if scope else []})
        marginal = {
            name: cost - evaluate(selection.numbers - {names[name]})[0]
            for name in (MEMORY, TPCDS, MAX_NODES) if names[name] in selection.numbers
        }
        totals.update(marginal)
        totals["total_ms"] += cost
        if selection.fell_back:
            totals["fallback_ms"] += cost
            totals["fallbacks"] += 1
        if (names[TPCDS] in selection.numbers and not selection.fell_back
                and any(item["path"].startswith(("sqlglot/optimizer/", "sqlglot/executor/"))
                        for item in scopes)):
            # An optimistic bound: keep TPC-DS and charge its cheapest possible
            # schedule. Other required tests and their overhead can lower the ceiling.
            totals["protected_tpcds_headroom_ms"] += cost - durations[names[TPCDS]] - 2000
        if (not selection.fell_back and len(scopes) == 1 and scopes[0]["kind"] == "bodies"
                and scopes[0]["path"].startswith("sqlglot/parsers/")):
            totals["parser_construction_target_ms"] += marginal.get(MEMORY, 0)
        rows[patch.stem] = {"cost_ms": cost, "workers": workers, "tests": len(selection.numbers),
                            "fallback": selection.fell_back, "reasons": hard or selection.reasons,
                            "scopes": scopes, "marginal_ms": marginal}
    return {"totals": dict(totals), "patches": rows}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("data", type=Path)
    parser.add_argument("maps", type=Path)
    args = parser.parse_args()
    os.environ["TESTRADAR_EXPERIMENTS"] = "equivalent"
    print(json.dumps(profile(args.data, args.maps), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
