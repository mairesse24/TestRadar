"""Run TestRadar against the scorer's mounted manifest and Git patches.

The selection policy is chosen at image build time through the
``TESTRADAR_POLICY`` environment variable (``--policy`` overrides it locally):

``fallback``  the validated production behaviour: every uncertainty signal
              from the patch parser, dependency graph and coverage map forces
              the full suite. The coverage map always reports uncertainty, so
              every commit runs every test.
``safe``      selective. Hard signals (see ``hard_signals``) force the full
              suite; source changes also need complete coverage evidence.
``balanced``  selective. Same hard signals; source changes are narrowed with
              the selector's pinned static maps, and coverage only adds tests.

See docs/SELECTION_POLICY.md for the signal classification and residual risks.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import sys

from analyzer.coverage_map import CoverageMap
from analyzer.dependency_graph import DependencyGraph
from analyzer.patch_parser import parse_patch
from analyzer.scheduler import schedule_tests
from analyzer.test_selector import REGISTRY_MODULES, Selection, explain_selection


DATA = Path("/data")
MAPS = Path("/app/maps")
POLICIES = ("fallback", "safe", "balanced")
DEFAULT_POLICY = "fallback"
POLICY_ENV = "TESTRADAR_POLICY"

# Dynamic loading or execution introduced by a patch is not described by any
# map derived from the pinned source.
_DYNAMIC = re.compile(r"__import__|\bimportlib\b|\bimport_module\b|(?<![\w.])(?:exec|eval)\s*\(")
# Any import statement added or removed by a patch, at any indentation.
_IMPORT_LINE = re.compile(r"^\s*(?:import\s+[\w.]|from\s+[\w.]+\s+import\b)")


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


def _read_changes(patch: Path) -> list:
    try:
        return parse_patch(patch.read_text(encoding="utf-8"))
    except (OSError, UnicodeError):
        return []


def _paths(changes: list) -> set[str]:
    return {
        path
        for change in changes
        for key in ("old_path", "new_path", "path")
        if (path := change[key])
    }


def analyze(
    patch: Path,
    tests: list[dict],
    graph: DependencyGraph | None,
    coverage: CoverageMap | None,
    mode: str,
) -> tuple[Selection, list[str]]:
    """The ``fallback`` policy: any integrated uncertainty signal is final."""
    changes = _read_changes(patch)
    paths = _paths(changes)
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


def hard_signals(
    changes: list,
    graph: DependencyGraph | None,
    coverage: CoverageMap | None,
) -> list[str]:
    """Signals meaning the evidence itself is missing, corrupt or unsupported.

    Each one forces the full suite in every policy. Known limitations of a
    valid pinned artifact (the graph's global dynamic-loading flag, its
    Python-only scope, coverage never proving exclusion) are not listed here;
    the selector's pinned static maps and fallbacks handle those.
    """
    reasons = []
    if not changes:
        reasons.append("patch: no file changes parsed")
    elif any(change["uncertain"] for change in changes):
        reasons.append("patch: unsupported or malformed diff")
    if graph is None:
        reasons.append("dependencies: map unavailable or invalid")
    if coverage is None:
        reasons.append("coverage: map unavailable or invalid")
    for change in changes:
        status = change["status"]
        # The pre-change file must exist in the pinned graph. New paths of
        # added, renamed or copied files cannot be in it by construction; the
        # selector applies its own fallbacks to those statuses.
        pinned = change["path"] if status == "modified" else change["old_path"]
        if (graph is not None and status != "added" and pinned
                and pinned.endswith(".py") and pinned.startswith(("sqlglot/", "tests/"))
                and not graph.knows(pinned)):
            reasons.append(f"dependencies: {pinned} is not in the pinned dependency map")
        # A brand-new file is loaded only through an edit to an existing file,
        # and a deleted file adds no dependency; both are judged by the selector.
        if status in ("added", "deleted") or not (change["path"] or "").endswith(".py"):
            continue
        for line in change["added_text"] + change["removed_text"]:
            if _DYNAMIC.search(line):
                reasons.append(f"patch: dynamic loading or execution edited in {change['path']}")
                break
            if _IMPORT_LINE.match(line):
                reasons.append(f"patch: import statement edited in {change['path']}")
                break
    return list(dict.fromkeys(reasons))


def analyze_selective(
    patch: Path,
    tests: list[dict],
    graph: DependencyGraph | None,
    coverage: CoverageMap | None,
    mode: str,
) -> tuple[Selection, list[str]]:
    """The ``safe`` and ``balanced`` policies."""
    changes = _read_changes(patch)
    reasons = hard_signals(changes, graph, coverage)
    paths = _paths(changes)
    impacted = graph.impacted_files(paths, stop_at=REGISTRY_MODULES) if graph is not None else set()
    covered: set[str] = set()
    if coverage is not None and (mode == "balanced" or not coverage.missing_test_ids):
        # Positive evidence only. Safe mode counts coverage as present only
        # when every test was observed; balanced uses partial hits as extras.
        covered = coverage.tests_for_changes(changes)
    selection = explain_selection(
        changes, tests, impacted, covered, analysis_uncertain=bool(reasons), mode=mode,
    )
    return selection, reasons


def run(data: Path, maps: Path, policy: str) -> None:
    tests = json.loads((data / "tests.json").read_text(encoding="utf-8"))["tests"]
    graph, coverage = load_analysis_maps(maps)
    all_numbers = {test["number"] for test in tests}
    full_schedule = schedule_tests(all_numbers, tests)
    fallback_count = 0
    commit_count = 0
    for patch in sorted((data / "commits").glob("*.patch")):
        try:
            if policy in ("safe", "balanced"):
                selection, uncertainty = analyze_selective(patch, tests, graph, coverage, policy)
            else:
                selection, uncertainty = analyze(patch, tests, graph, coverage, "safe")
        except Exception as error:  # never skip tests because analysis crashed
            selection = Selection(set(all_numbers), True, [f"analysis error: {type(error).__name__}"])
            uncertainty = []
        selected = selection.numbers & all_numbers
        if selection.fell_back:
            fallback_count += 1
            categories = dict.fromkeys(item.split(":", 1)[0] for item in uncertainty)
            reason = ", ".join(categories) or "; ".join(selection.reasons)
            print(f"{patch.stem}: full-suite fallback ({reason})", file=sys.stderr)
            details = [item for item in uncertainty if ":" in item]
            if details:
                print(f"{patch.stem}: " + "; ".join(details), file=sys.stderr)
        workers = (
            full_schedule if selected == all_numbers
            else schedule_tests(selected, tests)
        )
        print(json.dumps({"commit": patch.stem, "workers": workers}, separators=(",", ":")))
        commit_count += 1
    print(f"policy={policy} commits={commit_count} full_suite_fallbacks={fallback_count}", file=sys.stderr)


def configured_policy(value: str | None) -> str:
    if value in POLICIES:
        return value
    if value:
        print(f"Unknown {POLICY_ENV}={value!r}; using {DEFAULT_POLICY}", file=sys.stderr)
    return DEFAULT_POLICY


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--policy", choices=POLICIES, default=None)
    args = parser.parse_args()
    run(DATA, MAPS, args.policy or configured_policy(os.environ.get(POLICY_ENV)))


if __name__ == "__main__":
    main()
