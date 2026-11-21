"""Check a selector run's stdout against the selection it was scheduled from.

    python3 tools/selection_eval/check_output.py OUTPUT DATA_DIR MAPS_DIR POLICY

For every patch in DATA_DIR/commits, the selection is recomputed in-process
with selector.py's own analysis (same policy, same maps), then compared with
the worker lists in OUTPUT. Checked per commit:

* exactly one valid JSON line ``{"commit": ..., "workers": [[int, ...], ...]}``
* the union of worker test numbers equals the selected set (nothing lost,
  nothing added)
* no test number appears twice, within or across workers
* 1 to 8 non-empty workers when anything is selected, none otherwise
* every number is in tests.json

Does not read answers.json. Prints a JSON summary; exits 1 on any violation.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
import selector  # noqa: E402

MAX_WORKERS = 8


def expected_selections(data: Path, maps: Path, policy: str) -> tuple[dict[str, set[int]], list[dict]]:
    tests = json.loads((data / "tests.json").read_text(encoding="utf-8"))["tests"]
    all_numbers = {test["number"] for test in tests}
    graph, coverage = selector.load_analysis_maps(maps)
    expected = {}
    for patch in sorted((data / "commits").glob("*.patch")):
        if policy in ("safe", "balanced"):
            selection, _ = selector.analyze_selective(patch, tests, graph, coverage, policy)
        else:
            selection, _ = selector.analyze(patch, tests, graph, coverage, "safe")
        expected[patch.stem] = selection.numbers & all_numbers
    return expected, tests


def check(output: bytes, expected: dict[str, set[int]], tests: list[dict]) -> dict:
    seconds = {test["number"]: test["seconds"] for test in tests}
    violations: list[str] = []
    seen: dict[str, list[list[int]]] = {}
    for line_number, line in enumerate(output.decode("utf-8").split("\n"), start=1):
        if not line:
            continue
        try:
            item = json.loads(line)
        except ValueError:
            violations.append(f"line {line_number}: not JSON")
            continue
        if (not isinstance(item, dict) or set(item) != {"commit", "workers"}
                or not isinstance(item["workers"], list)
                or not all(isinstance(w, list) and all(type(n) is int for n in w) for w in item["workers"])):
            violations.append(f"line {line_number}: wrong shape")
            continue
        if item["commit"] in seen:
            violations.append(f"{item['commit']}: repeated commit line")
        seen[item["commit"]] = item["workers"]
    summary = {"commits": len(expected), "tests_selected": 0, "workers": 0, "max_workers": 0,
               "sum_max_worker_seconds": 0.0, "empty_selections": 0}
    for commit, wanted in expected.items():
        workers = seen.get(commit)
        if workers is None:
            violations.append(f"{commit}: no output line")
            continue
        flat = [n for worker in workers for n in worker]
        nonempty = [worker for worker in workers if worker]
        if len(flat) != len(set(flat)):
            violations.append(f"{commit}: duplicate test numbers")
        if set(flat) != wanted:
            violations.append(f"{commit}: lost {len(wanted - set(flat))}, extra {len(set(flat) - wanted)}")
        if any(n not in seconds for n in flat):
            violations.append(f"{commit}: unknown test number")
        if wanted and not 1 <= len(nonempty) <= MAX_WORKERS:
            violations.append(f"{commit}: {len(nonempty)} workers")
        if not wanted:
            summary["empty_selections"] += 1
            if nonempty:
                violations.append(f"{commit}: workers for an empty selection")
        summary["tests_selected"] += len(set(flat))
        summary["workers"] += len(nonempty)
        summary["max_workers"] = max(summary["max_workers"], len(nonempty))
        summary["sum_max_worker_seconds"] += max(
            (sum(round(seconds.get(n, 0) * 1000) for n in w) for w in nonempty), default=0,
        ) / 1000
    for commit in seen.keys() - expected.keys():
        violations.append(f"{commit}: unknown commit")
    summary["sum_max_worker_seconds"] = round(summary["sum_max_worker_seconds"], 3)
    summary["violations"] = violations
    summary["ok"] = not violations
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("data", type=Path)
    parser.add_argument("maps", type=Path)
    parser.add_argument("policy", choices=selector.POLICIES)
    args = parser.parse_args()
    expected, tests = expected_selections(args.data, args.maps, args.policy)
    summary = check(args.output.read_bytes(), expected, tests)
    print(json.dumps(summary, indent=2))
    sys.exit(0 if summary["ok"] else 1)


if __name__ == "__main__":
    main()
