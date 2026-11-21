"""Run TestRadar against the scorer's mounted manifest and Git patches."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from analyzer.coverage_map import CoverageMap
from analyzer.dependency_graph import DependencyGraph
from analyzer.patch_parser import parse_patch
from analyzer.scheduler import schedule_tests
from analyzer.test_selector import Selection, explain_selection


DATA = Path("/data")
MAPS = Path("/app/maps")


def load_analysis_maps(directory: Path) -> tuple[DependencyGraph | None, CoverageMap | None]:
    try:
        graph = DependencyGraph.load(str(directory / "dependencies.json"))
    except (OSError, ValueError) as error:
        print(f"Dependency map unavailable: {error}", file=sys.stderr)
        graph = None
    try:
        coverage = CoverageMap.load(str(directory / "coverage.json"))
    except (OSError, ValueError) as error:
        print(f"Coverage map unavailable: {error}", file=sys.stderr)
        coverage = None
    return graph, coverage


def analyze(
    patch: Path,
    tests: list[dict],
    graph: DependencyGraph | None,
    coverage: CoverageMap | None,
    mode: str,
) -> tuple[Selection, list[str]]:
    try:
        changes = parse_patch(patch.read_text(encoding="utf-8"))
    except (OSError, UnicodeError):
        changes = []
    paths = {
        path
        for change in changes
        for key in ("old_path", "new_path", "path")
        if (path := change[key])
    }
    reasons = []
    if not changes or any(change["uncertain"] for change in changes):
        reasons.append("patch")
    if graph is None or graph.is_uncertain(paths):
        reasons.append("dependencies")
    if coverage is None or coverage.is_uncertain(changes):
        reasons.append("coverage")
    impacted = graph.impacted_files(paths) if graph is not None else paths
    covered = coverage.tests_for_changes(changes) if coverage is not None else set()
    selection = explain_selection(
        changes, tests, impacted, covered, analysis_uncertain=bool(reasons), mode=mode,
    )
    return selection, reasons


def run(data: Path, maps: Path, mode: str) -> None:
    tests = json.loads((data / "tests.json").read_text(encoding="utf-8"))["tests"]
    graph, coverage = load_analysis_maps(maps)
    all_numbers = {test["number"] for test in tests}
    full_schedule = schedule_tests(all_numbers, tests)
    fallback_count = 0
    commit_count = 0
    for patch in sorted((data / "commits").glob("*.patch")):
        selection, uncertainty = analyze(patch, tests, graph, coverage, mode)
        if selection.fell_back:
            fallback_count += 1
            reason = ", ".join(uncertainty) or "; ".join(selection.reasons)
            print(f"{patch.stem}: full-suite fallback ({reason})", file=sys.stderr)
        workers = (
            full_schedule if selection.numbers == all_numbers
            else schedule_tests(selection.numbers, tests)
        )
        print(json.dumps({"commit": patch.stem, "workers": workers}, separators=(",", ":")))
        commit_count += 1
    print(f"mode={mode} commits={commit_count} full_suite_fallbacks={fallback_count}", file=sys.stderr)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("safe", "balanced"), default="safe")
    args = parser.parse_args()
    run(DATA, MAPS, args.mode)


if __name__ == "__main__":
    main()
