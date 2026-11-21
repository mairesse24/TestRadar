"""Offline evaluation of analyzer/test_selector.py (never imported by analyzer/).

    python3 tools/selection_eval/evaluate.py \
        --sample sample                      # commits/*.patch + answers.json
        --mutants <results dir>              # <id>.patch + <id>.json from run_mutants.py
        [--coverage <simcov.json>]           # from build_sim_coverage.py
        [--details out.json]

For every dataset x coverage input x mode it prints simulated seconds (the
SCORING.md arithmetic with an LPT scheduler over 1..8 workers), missed commits
and missed failing tests, full-suite fallbacks, share of tests selected (by
count and by recorded seconds) and selector CPU time per commit.

Inputs the analysis engine will provide are simulated here and labelled so:
  * parse_patch     -> a minimal unified-diff parser in this file
  * impacted_files  -> the changed paths only (no dependency graph)
  * covered_test_ids-> none, or a per-test coverage simulation:
        "file": every test that executed any line of a changed file
        "line": tests that executed a changed (old) line, falling back to
                "file" for a file where no changed line was executed
answers / ground truth are used only to score, never passed to the selector.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import statistics
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WORKER_COST_MS = 2000
MISS_PENALTY_MS = 300_000


def load_selector(path: str | None = None):
    target = Path(path) if path else ROOT / "analyzer" / "test_selector.py"
    spec = importlib.util.spec_from_file_location("selector_under_test", target)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def parse_patch(text: str) -> list[dict]:
    changes: list[dict] = []
    cur = None
    old_no = new_no = 0
    for line in text.splitlines():
        if line.startswith("diff --git "):
            m = re.match(r"diff --git a/(\S+) b/(\S+)$", line)
            cur = {
                "path": m.group(2) if m else None,
                "old_path": m.group(1) if m else None,
                "new_path": m.group(2) if m else None,
                "status": "modified" if m and m.group(1) == m.group(2) else "renamed",
                "old_lines": [], "new_lines": [], "added_text": [], "removed_text": [],
                "uncertain": m is None,
            }
            changes.append(cur)
            continue
        if cur is None:
            continue
        if line.startswith("new file mode"):
            cur["status"], cur["old_path"] = "added", None
        elif line.startswith("deleted file mode"):
            cur["status"], cur["new_path"], cur["path"] = "deleted", None, cur["old_path"]
        elif line.startswith("rename from "):
            cur["status"], cur["old_path"] = "renamed", line[len("rename from "):]
        elif line.startswith("rename to "):
            cur["new_path"] = cur["path"] = line[len("rename to "):]
        elif line.startswith(("Binary files", "GIT binary patch")):
            cur["status"] = "binary"
        elif line.startswith(("--- ", "+++ ", "index ", "similarity index", "old mode", "new mode")):
            pass
        elif line.startswith("@@"):
            m = re.match(r"@@ -(\d+)(?:,\d+)? \+(\d+)", line)
            old_no, new_no = int(m.group(1)), int(m.group(2))
        elif line.startswith("+"):
            cur["new_lines"].append(new_no); cur["added_text"].append(line[1:]); new_no += 1
        elif line.startswith("-"):
            cur["old_lines"].append(old_no); cur["removed_text"].append(line[1:]); old_no += 1
        elif line.startswith("\\"):
            pass
        else:
            old_no += 1; new_no += 1
    return changes


def schedule_cost(numbers: set[int], ms: dict[int, int]) -> int:
    if not numbers:
        return 0
    order = sorted(numbers, key=lambda n: (-ms[n], n))
    best = None
    for k in range(1, min(8, len(order)) + 1):
        loads = [0] * k
        for n in order:
            i = loads.index(min(loads))
            loads[i] += ms[n]
        cost = max(loads) + WORKER_COST_MS * k
        best = cost if best is None else min(best, cost)
    return best


class SimCoverage:
    def __init__(self, path: str, granularity: str):
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        self.tests = data["tests"]
        self.files = data["files"]
        self.granularity = granularity
        self._file_tests: dict[str, set[str]] = {}

    def _all(self, rel: str) -> set[str]:
        if rel not in self._file_tests:
            entry = self.files.get(rel, {"lines": {}})
            idx = set()
            for ids in entry["lines"].values():
                idx.update(ids)
            self._file_tests[rel] = {self.tests[i] for i in idx}
        return self._file_tests[rel]

    def tests_for_changes(self, changes: list[dict]) -> set[str]:
        out: set[str] = set()
        for change in changes:
            rel = change.get("old_path") or change.get("path")
            if not rel or rel not in self.files:
                continue
            if self.granularity == "file":
                out |= self._all(rel)
                continue
            lines = self.files[rel]["lines"]
            hit = set()
            for line in change.get("old_lines") or []:
                hit.update(lines.get(str(line), ()))
            out |= {self.tests[i] for i in hit} if hit else self._all(rel)
        return out


def load_datasets(args) -> dict[str, list[dict]]:
    datasets: dict[str, list[dict]] = {}
    if args.sample:
        sample = Path(args.sample)
        answers = json.loads((sample / "answers.json").read_text(encoding="utf-8"))["commits"]
        datasets["sample"] = [
            {"id": p.stem, "patch": p.read_text(encoding="utf-8"), "failing": set(answers[p.stem]["failing"]),
             "group": answers[p.stem].get("kind", "")}
            for p in sorted((sample / "commits").glob("*.patch"))
        ]
    return datasets


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sample")
    parser.add_argument("--tests", default=str(ROOT / "sample" / "tests.json"))
    parser.add_argument("--mutants")
    parser.add_argument("--coverage")
    parser.add_argument("--modes", default="safe,balanced,aggressive")
    parser.add_argument("--details")
    parser.add_argument("--selector", help="evaluate another copy of test_selector.py (e.g. an older revision)")
    args = parser.parse_args()

    ts = load_selector(args.selector)
    tests = json.loads(Path(args.tests).read_text(encoding="utf-8"))["tests"]
    ms = {t["number"]: round(t["seconds"] * 1000) for t in tests}
    number_by_id = {t["id"]: t["number"] for t in tests}
    all_numbers = set(ms)
    total_ms = sum(ms.values())

    datasets = load_datasets(args)
    if args.mutants:
        rows = []
        for result in sorted(Path(args.mutants).glob("*.json")):
            if result.name.startswith("_"):
                continue
            data = json.loads(result.read_text(encoding="utf-8"))
            patch = result.with_suffix(".patch").read_text(encoding="utf-8")
            rows.append({"id": data["id"], "patch": patch, "group": data["stratum"],
                         "failing": {number_by_id[i] for i in data["failing"] if i in number_by_id}})
        datasets["mutants"] = rows

    coverages = [("none", None)]
    if args.coverage:
        coverages += [("sim-file", SimCoverage(args.coverage, "file")), ("sim-line", SimCoverage(args.coverage, "line"))]

    details = []
    header = ("| dataset | coverage | mode | commits | simulated s | missed commits | missed failing tests "
              "| full-suite fallbacks | % tests (count) | % tests (seconds) | selector CPU ms mean / max |")
    print(header)
    print("|" + "---|" * 11)
    for name, rows in datasets.items():
        ref = sum(schedule_cost(all_numbers, ms) for _ in rows)
        print(f"| {name} | - | all tests (LPT) | {len(rows)} | {ref / 1000:.3f} | 0 | 0 | {len(rows)} | 100.0 | 100.0 | - |")
        for cov_name, cov in coverages:
            for mode in args.modes.split(","):
                sim = 0
                missed_commits = missed_tests = fallbacks = 0
                pct_n, pct_s, cpu = [], [], []
                for row in rows:
                    changes = parse_patch(row["patch"])
                    covered = cov.tests_for_changes(changes) if cov else set()
                    impacted = {c["path"] for c in changes if c.get("path")}
                    start = time.process_time()
                    result = ts.explain_selection(changes, tests, impacted, covered, mode=mode)
                    cpu.append((time.process_time() - start) * 1000)
                    chosen = result.numbers
                    missed = row["failing"] - chosen
                    sim += schedule_cost(chosen, ms) + (MISS_PENALTY_MS if missed else 0)
                    missed_commits += bool(missed)
                    missed_tests += len(missed)
                    fallbacks += result.fell_back
                    pct_n.append(100 * len(chosen) / len(all_numbers))
                    pct_s.append(100 * sum(ms[n] for n in chosen) / total_ms)
                    details.append({"dataset": name, "coverage": cov_name, "mode": mode, "id": row["id"],
                                    "group": row["group"], "selected": len(chosen), "failing": len(row["failing"]),
                                    "missed": sorted(missed), "fell_back": result.fell_back,
                                    "reasons": result.reasons[:3]})
                print(f"| {name} | {cov_name} | {mode} | {len(rows)} | {sim / 1000:.3f} | {missed_commits} | "
                      f"{missed_tests} | {fallbacks} | {statistics.mean(pct_n):.1f} | {statistics.mean(pct_s):.1f} | "
                      f"{statistics.mean(cpu):.2f} / {max(cpu):.2f} |")
    if args.details:
        Path(args.details).write_text(json.dumps(details, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
