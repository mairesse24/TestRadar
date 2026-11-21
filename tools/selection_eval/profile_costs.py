"""Offline per-patch cost profile using the production parser and policy."""

from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from analyzer.scheduler import duration_milliseconds, schedule_tests
from analyzer.test_selector import REGISTRY_MODULES, explain_selection
import selector


def profile(data: Path, maps: Path, *, aggressive: bool = False) -> dict:
    tests = json.loads((data / "tests.json").read_text(encoding="utf-8"))["tests"]
    graph, coverage = selector.load_analysis_maps(maps)
    duration = {test["number"]: duration_milliseconds(test["seconds"]) for test in tests}
    files = {test["number"]: test["file"] for test in tests}
    results = {}
    for patch in sorted((data / "commits").glob("*.patch")):
        changes = selector._read_changes(patch)
        if aggressive:
            paths = selector._paths(changes)
            hard = selector.hard_signals(changes, graph, coverage)
            impacted = graph.impacted_files(paths, stop_at=REGISTRY_MODULES) if graph else set()
            hits = coverage.tests_for_changes(changes) if coverage else set()
            selection = explain_selection(
                changes, tests, impacted, hits, mode="aggressive", analysis_uncertain=bool(hard),
            )
        else:
            selection, hard = selector.analyze_selective(patch, tests, graph, coverage, "balanced")
        numbers = selection.numbers
        workers = schedule_tests(numbers, tests)
        loads = [sum(duration[number] for number in worker) for worker in workers]
        total = sum(loads)
        longest = max((duration[number] for number in numbers), default=0)
        bound = min((
            max(longest, (total + width - 1) // width) + 2000 * width
            for width in range(1, min(8, len(numbers)) + 1)
        ), default=0)
        groups = Counter()
        for number in numbers:
            groups[files[number]] += duration[number]
        results[patch.stem] = {
            "paths": sorted(selector._paths(changes)),
            "fallback": selection.fell_back,
            "reasons": hard or selection.reasons,
            "test_count": len(numbers),
            "selected_numbers": sorted(numbers),
            "largest_test_files_ms": groups.most_common(5),
            "worker_loads_ms": loads,
            "cost_ms": max(loads, default=0) + 2000 * len(workers),
            "packing_lower_bound_ms": bound,
        }
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("data", type=Path)
    parser.add_argument("maps", type=Path)
    parser.add_argument("--aggressive", action="store_true", help="offline counterfactual only")
    args = parser.parse_args()
    print(json.dumps(profile(args.data, args.maps, aggressive=args.aggressive), sort_keys=True))


if __name__ == "__main__":
    main()
