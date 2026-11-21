"""Score every selection policy on the adversarial scenarios (offline).

    python3 tools/adversarial/evaluate_scenarios.py GT_DIR[,GT_DIR...] MAPS_DIR OUT_DIR
    python3 tools/adversarial/evaluate_scenarios.py --data DATA_DIR MAPS_DIR OUT_DIR

GT_DIR holds ``<id>.patch`` and ``<id>.json`` written by
``tools/selection_eval/run_mutants.py`` (ground truth from the real SQLGlot
suite). The script builds a scorer data dir (``OUT_DIR/data``), runs the
integrated ``selector.py`` code path per policy with the given pinned maps,
writes one output file per policy, scores it with ``score.py`` and writes
``OUT_DIR/results.json``.

Policies: ``fallback``, ``safe`` and ``balanced`` call exactly the functions
``selector.run`` calls. ``aggressive`` is not exposed by ``selector.py``; it
runs ``analyze_selective`` (same hard signals and maps) with
``mode="aggressive"``. Ground truth is used only for scoring.
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import os

ROOT = Path(__file__).resolve().parents[2]
# SELECTOR_ROOT=<checkout> evaluates another revision's selector.py/analyzer.
sys.path.insert(0, os.environ.get("SELECTOR_ROOT", str(ROOT)))
sys.path.insert(1, str(ROOT))

import score  # noqa: E402
import selector  # noqa: E402
from analyzer.scheduler import schedule_tests  # noqa: E402
from analyzer.test_selector import Selection  # noqa: E402

POLICIES = ("fallback", "safe", "balanced", "aggressive")


def build_data(gt_dirs: list[Path], data: Path) -> dict:
    tests_doc = json.loads((ROOT / "sample" / "tests.json").read_text(encoding="utf-8"))
    number = {test["id"]: test["number"] for test in tests_doc["tests"]}
    if data.exists():
        shutil.rmtree(data)
    (data / "commits").mkdir(parents=True)
    (data / "tests.json").write_text(json.dumps(tests_doc), encoding="utf-8")
    answers = {}
    meta = {}
    for directory in gt_dirs:
        for result in sorted(directory.glob("*.json")):
            if result.name.startswith("_"):
                continue
            record = json.loads(result.read_text(encoding="utf-8"))
            sid = record["id"]
            shutil.copy(directory / f"{sid}.patch", data / "commits" / f"{sid}.patch")
            answers[sid] = {"failing": sorted(number[i] for i in record["failing"]), "kind": record["stratum"]}
            meta[sid] = record
    (data / "answers.json").write_text(json.dumps({"commits": answers}, indent=0), encoding="utf-8")
    return {"tests": tests_doc["tests"], "answers": answers, "meta": meta}


def select(policy: str, patch: Path, tests: list[dict], graph, coverage):
    all_numbers = {test["number"] for test in tests}
    try:
        if policy == "precision":  # only present in revisions that define it
            selection, reasons = selector.analyze_precision(patch, tests, graph, coverage)
        elif policy in ("safe", "balanced", "aggressive"):
            selection, reasons = selector.analyze_selective(patch, tests, graph, coverage, policy)
        else:
            selection, reasons = selector.analyze(patch, tests, graph, coverage, "safe")
    except Exception as error:  # mirrors selector.run
        selection, reasons = Selection(set(all_numbers), True, [f"analysis error: {type(error).__name__}"]), []
    return selection, reasons


def main() -> None:
    args = sys.argv[1:]
    if args[0] == "--data":  # an existing scorer data dir (commits/, tests.json, answers.json)
        data = Path(args[1])
        maps, out = Path(args[2]), Path(args[3])
        out.mkdir(parents=True, exist_ok=True)
        tests = json.loads((data / "tests.json").read_text(encoding="utf-8"))["tests"]
        answers = json.loads((data / "answers.json").read_text(encoding="utf-8"))["commits"]
        by_id = {test["number"]: test["id"] for test in tests}
        meta = {sid: {"failing": [by_id[n] for n in entry["failing"]], "collect_errors": []}
                for sid, entry in answers.items()}
    else:
        gt_dirs = [Path(p) for p in args[0].split(",")]
        maps, out = Path(args[1]), Path(args[2])
        out.mkdir(parents=True, exist_ok=True)
        built = build_data(gt_dirs, out / "data")
        tests, answers, meta = built["tests"], built["answers"], built["meta"]
        data = out / "data"
    print("selector:", selector.__file__, file=sys.stderr)
    graph, coverage = selector.load_analysis_maps(maps)
    all_numbers = {test["number"] for test in tests}
    by_number = {test["number"]: test["id"] for test in tests}
    full = schedule_tests(all_numbers, tests)
    files = {}
    for sid in answers:
        text = (data / "commits" / f"{sid}.patch").read_text(encoding="utf-8")
        files[sid] = ", ".join(sorted({line[6:] for line in text.splitlines() if line.startswith("+++ b/")}))
    rows = {sid: {"id": sid, "stratum": answers[sid]["kind"], "file": files[sid], "failing": meta[sid]["failing"],
                  "collect_errors": meta[sid]["collect_errors"]} for sid in answers}
    scores = {}
    policies = POLICIES
    if hasattr(selector, "analyze_precision"):
        # Function-impact classification reads the pinned sources.
        selector.SOURCES = Path(os.environ.get("PINNED_SQLGLOT", "/workspace/sqlglot-src"))
        policies = POLICIES + ("precision",)
    for policy in policies:
        lines = []
        for sid in sorted(answers):
            patch = data / "commits" / f"{sid}.patch"
            selection, reasons = select(policy, patch, tests, graph, coverage)
            selected = selection.numbers & all_numbers
            workers = full if selected == all_numbers else schedule_tests(selected, tests)
            lines.append(json.dumps({"commit": sid, "workers": workers}, separators=(",", ":")))
            missed = [by_number[n] for n in answers[sid]["failing"] if n not in selected]
            rows[sid][policy] = {
                "fell_back": selection.fell_back or selected == all_numbers,
                "selected": len(selected),
                "missed_tests": missed,
                "reasons": (reasons or [])[:3] + list(selection.reasons)[:3],
            }
        text = "\n".join(lines) + "\n"
        (out / f"output-{policy}.jsonl").write_text(text, encoding="utf-8")
        evaluation = score.evaluate_run(text.encode(), tests, answers)
        scores[policy] = evaluation
    result = {"scores": scores, "scenarios": [rows[s] for s in sorted(rows)]}
    (out / "results.json").write_text(json.dumps(result, indent=1), encoding="utf-8")
    for policy, evaluation in scores.items():
        print(policy, round(evaluation["simulated_milliseconds"] / 1000, 1), "s",
              "missed:", evaluation["missed_commits"])


if __name__ == "__main__":
    main()
