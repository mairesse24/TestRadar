"""Offline one-rule-at-a-time counterfactual report. Never used by the image."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import inspect
import json
from pathlib import Path
import sys
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
import selector
from analyzer import test_selector as policy
from analyzer.scheduler import duration_milliseconds, schedule_tests


@contextmanager
def relaxation(kind: str, name: str):
    if kind.startswith("core-"):
        source = inspect.getsource(policy.explain_selection)
        before = ("                if _is_core(path):" if kind == "core-static"
                  else "                    if aggressive and covered:")
        after = ("                if _is_core(path) and path != _study_core:"
                 if kind == "core-static"
                 else "                    if (aggressive or path == _study_core) and covered:")
        if source.count(before) != 1:
            raise ValueError("policy changed: update the offline probe")
        scope = dict(vars(policy), _study_core=name)
        # Compile the repository's trusted policy function only, never patch data.
        exec(compile(source.replace(before, after), "<counterfactual policy>", "exec"), scope)
        with patch.object(selector, "explain_selection", scope["explain_selection"]):
            yield
        return
    attribute, replacement = {
        "dialect-all-ids": ("ALL_DIALECT_TEST_IDS", ()),
        "dialect-infrastructure": ("DIALECT_INFRA_TEST_FILES", ()),
        "dialect-file-mentions": ("DIALECT_TEST_FILE_MENTIONS",
                                  dict.fromkeys(policy.DIALECT_TEST_FILE_MENTIONS, ())),
        "dialect-test-mentions": ("DIALECT_TEST_ID_MENTIONS",
                                  dict.fromkeys(policy.DIALECT_TEST_ID_MENTIONS, ())),
        "dialect-descendants": ("_dialect_descendants", lambda name: frozenset()),
    }[kind]
    if kind == "dialect-infrastructure" and name:
        replacement = tuple(file for file in policy.DIALECT_INFRA_TEST_FILES if file != name)
    with patch.object(policy, attribute, replacement):
        yield


def report(dataset: Path, maps: Path, answers: Path | None, partial: bool = False) -> None:
    catalog = json.loads((dataset / "tests.json").read_bytes())["tests"]
    durations = {test["number"]: duration_milliseconds(test["seconds"]) for test in catalog}
    labels = json.loads(answers.read_bytes())["commits"] if answers is not None else None
    patches = sorted((dataset / "commits").glob("*.patch"))
    if labels is not None and labels.keys() != {item.stem for item in patches}:
        raise ValueError("label manifest does not match the patches")
    graph, coverage = selector.load_analysis_maps(maps)
    if graph is None or coverage is None:
        raise ValueError("valid pinned maps are required for the study")
    paths = {change["path"] for item in patches for change in selector._read_changes(item)}
    variants = [(kind, name) for name in sorted(path for path in paths if policy._is_core(path))
                for kind in ("core-static", "core-coverage")]
    variants += [(kind, "") for kind in ("dialect-all-ids", "dialect-infrastructure",
               "dialect-file-mentions", "dialect-test-mentions", "dialect-descendants")]
    variants += [("dialect-infrastructure", file) for file in policy.DIALECT_INFRA_TEST_FILES]

    def evaluate():
        rows = {}
        for item in patches:
            selected, hard = selector.analyze_selective(item, catalog, graph, coverage, "balanced")
            workers = schedule_tests(selected.numbers, catalog)
            cost = max((sum(durations[number] for number in worker) for worker in workers), default=0)
            cost += 2000 * len(workers)
            missing = set(labels[item.stem]["failing"]) - selected.numbers if labels is not None else set()
            rows[item.stem] = (selected.numbers, cost, missing, selected.fell_back, hard or selected.reasons)
        return rows

    baseline = evaluate()
    base_cost = sum(row[1] for row in baseline.values())
    mode = "unlabeled" if labels is None else "verified failures only" if partial else "fully labeled"
    print(f"## {dataset.name} ({len(patches)} patches, {mode})\n")
    print(f"Baseline packing time {base_cost / 1000:.3f} s, "
          f"{sum(r[3] for r in baseline.values())} fallbacks.\n")
    print("| Relaxation | Packing saved (s) | Missed commits / tests | New missed tests | Changed selections | Observed cost with penalties (s) |")
    print("| --- | ---: | ---: | ---: | ---: | ---: |")
    differences = []
    for kind, name in variants:
        variant = f"{kind}: {name}" if name else kind
        with relaxation(kind, name):
            candidate = evaluate()
        changed = 0
        new_misses = 0
        for patch_id, row in candidate.items():
            old = baseline[patch_id]
            new_misses += len(row[2] - old[2])
            if old[:4] != row[:4]:
                changed += old[0] != row[0]
                differences.append((variant, patch_id, old, row))
        cost = sum(row[1] for row in candidate.values())
        miss_commits = sum(bool(row[2]) for row in candidate.values())
        miss_tests = sum(len(row[2]) for row in candidate.values())
        miss_text = f"{miss_commits} / {miss_tests}" if labels is not None else "unverified"
        new_text = str(new_misses) if labels is not None else "unverified"
        print(f"| {variant} | {(base_cost - cost) / 1000:.3f} | {miss_text} | "
              f"{new_text} | {changed} | "
              f"{(cost + 300_000 * miss_commits) / 1000:.3f} |")

    tests = {test["number"]: test["id"] for test in catalog}
    print("\n### Changed patches\n")
    print("| Relaxation | Patch | Packing before → after (s) | Tests before → after | Fallback before → after | Newly missed test IDs |")
    print("| --- | --- | ---: | ---: | --- | --- |")
    for variant, patch_id, old, row in differences:
        lost = ", ".join(tests[number].replace("|", "&#124;") for number in sorted(row[2] - old[2]))
        if labels is None:
            lost = "unverified"
        print(f"| {variant} | {patch_id} | {old[1]/1000:.3f} → {row[1]/1000:.3f} | "
              f"{len(old[0])} → {len(row[0])} | {old[3]} → {row[3]} | {lost or '—'} |")
    print()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset", type=Path)
    parser.add_argument("maps", type=Path)
    parser.add_argument("--answers", type=Path)
    parser.add_argument("--partial-labels", action="store_true")
    args = parser.parse_args()
    report(args.dataset, args.maps, args.answers, args.partial_labels)


if __name__ == "__main__":
    main()
