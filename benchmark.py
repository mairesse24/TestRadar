#!/usr/bin/env python3
"""Score candidate JSONL files, and compare scheduling rules.

Scoring mode runs score.py. It does not time a selector or a container.
Pass ``--cpu-seconds`` only when you already measured a process; that number
is forwarded and is not a measurement made here.

``--compare-schedulers`` does not read answers.json. It schedules the catalog
with the starter 3-worker split, the previous longest-first search, and
``schedule_tests``, and prints makespan, worker overhead, scheduling CPU, and
the objective.

Examples:
    python3 benchmark.py output-docker.jsonl output-local.jsonl
    python3 benchmark.py run.jsonl --data-dir sample --answers sample/answers.json
    python3 benchmark.py --compare-schedulers --data-dir sample
"""

from __future__ import annotations

import argparse
import json
import random
import re
import shlex
import subprocess
import sys
import time
from pathlib import Path

from analyzer.scheduler import (  # noqa: E402
    _durations,
    clear_schedule_cache,
    schedule_tests,
    schedule_tests_lpt,
)

_SIMULATED = re.compile(r"^Simulated:\s+(\d+\.\d{3} s)\s*$", re.MULTILINE)
_MISSES = re.compile(r"^Misses:\s+(\d+)\b", re.MULTILINE)
_MISSED_COMMIT = re.compile(r"^  missed (\S+)\s*$", re.MULTILINE)
_REJECTED = re.compile(r"^Rejected:\s+(\S+)(?: on line (\d+))?\s*$", re.MULTILINE)


def main(argv: list[str] | None = None) -> int:
    root = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(
        description=(
            "Score candidate JSONL files with score.py, or compare schedulers. "
            "Scoring mode does not measure CPU. --compare-schedulers measures "
            "scheduling CPU and does not read answers.json."
        )
    )
    parser.add_argument(
        "outputs",
        nargs="*",
        type=Path,
        help="candidate stdout captures, one JSON object per line",
    )
    parser.add_argument(
        "--compare-schedulers",
        action="store_true",
        help="compare the starter 3-worker split, longest-first, and schedule_tests",
    )
    parser.add_argument(
        "--repeats",
        type=int,
        default=11,
        help="cold and warm timing samples per schedule when comparing (default: 11)",
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=root / "sample",
        help="directory with tests.json and commits/*.patch (default: ./sample)",
    )
    parser.add_argument(
        "--answers",
        type=Path,
        default=root / "sample" / "answers.json",
        help="answers.json passed only to score.py (default: ./sample/answers.json)",
    )
    parser.add_argument(
        "--score-py",
        type=Path,
        default=root / "score.py",
        help="path to score.py (default: ./score.py next to this script)",
    )
    parser.add_argument(
        "--cpu-seconds",
        type=float,
        default=None,
        help="already-measured CPU seconds to forward; benchmark.py does not measure any",
    )
    options = parser.parse_args(argv)
    if options.compare_schedulers:
        if options.repeats < 1:
            print("--repeats must be at least 1", file=sys.stderr)
            return 2
        try:
            catalog = load_catalog(options.data_dir)
        except (OSError, ValueError, KeyError, json.JSONDecodeError) as error:
            print(f"could not read tests.json: {error}", file=sys.stderr)
            return 2
        try:
            text, rows = run_schedule_comparison(
                catalog,
                repeats=options.repeats,
                source=str(options.data_dir / "tests.json"),
            )
        except RuntimeError as error:
            print(str(error), file=sys.stderr)
            return 1
        sys.stdout.write(text)
        if any(row["partition"] != "ok" or row["improved_regressed"] for row in rows):
            return 1
        return 0
    if not options.outputs:
        parser.error("the following arguments are required: outputs")

    print(
        "CPU time is unmeasured. benchmark.py does not time a selector or a container.",
        flush=True,
    )
    if options.cpu_seconds is not None:
        print(
            "Forwarding --cpu-seconds "
            f"{options.cpu_seconds} to score.py. That value was not measured here.",
            flush=True,
        )

    worst = 0
    for index, output in enumerate(options.outputs):
        if index:
            print()
        status, text = score_candidate(
            output=output,
            data_dir=options.data_dir,
            answers=options.answers,
            score_py=options.score_py,
            cpu_seconds=options.cpu_seconds,
        )
        sys.stdout.write(text)
        if not text.endswith("\n"):
            sys.stdout.write("\n")
        worst = max(worst, status)
    return worst


def score_candidate(
    output: Path,
    data_dir: Path,
    answers: Path,
    score_py: Path,
    cpu_seconds: float | None,
) -> tuple[int, str]:
    command = [
        sys.executable,
        str(score_py),
        str(data_dir),
        str(answers),
        str(output),
    ]
    if cpu_seconds is not None:
        command.extend(["--cpu-seconds", str(cpu_seconds)])

    if not score_py.is_file():
        return 2, _report(
            output=output,
            command=command,
            validation="unscored",
            cpu_seconds=cpu_seconds,
            detail=f"score.py not found: {score_py}",
            stdout="",
            stderr="",
            exit_code=None,
        )
    if not output.is_file():
        return 2, _report(
            output=output,
            command=command,
            validation="unscored",
            cpu_seconds=cpu_seconds,
            detail=f"candidate file not found: {output}",
            stdout="",
            stderr="",
            exit_code=None,
        )

    try:
        completed = subprocess.run(command, capture_output=True, check=False)
    except OSError as error:
        return 2, _report(
            output=output,
            command=command,
            validation="unscored",
            cpu_seconds=cpu_seconds,
            detail=f"could not run score.py: {error}",
            stdout="",
            stderr="",
            exit_code=None,
        )

    stdout = completed.stdout.decode("utf-8", errors="replace")
    stderr = completed.stderr.decode("utf-8", errors="replace")
    parsed = _parse(stdout, stderr, completed.returncode)
    return parsed["status"], _report(
        output=output,
        command=command,
        validation=parsed["validation"],
        cpu_seconds=cpu_seconds,
        detail=parsed["detail"],
        simulated=parsed["simulated"],
        misses=parsed["misses"],
        missed_commits=parsed["missed_commits"],
        line=parsed["line"],
        stdout=stdout,
        stderr=stderr,
        exit_code=completed.returncode,
    )


def _parse(stdout: str, stderr: str, returncode: int) -> dict:
    rejected = _REJECTED.search(stdout)
    simulated = _SIMULATED.search(stdout)
    misses = _MISSES.search(stdout)
    missed_commits = _MISSED_COMMIT.findall(stdout)

    if returncode == 0 and simulated and misses:
        return {
            "status": 0,
            "validation": "accepted",
            "detail": "",
            "simulated": simulated.group(1),
            "misses": int(misses.group(1)),
            "missed_commits": missed_commits,
            "line": None,
        }
    if returncode == 1 and rejected:
        return {
            "status": 1,
            "validation": rejected.group(1),
            "detail": "",
            "simulated": None,
            "misses": None,
            "missed_commits": None,
            "line": None if rejected.group(2) is None else int(rejected.group(2)),
        }

    detail = stderr.strip() or stdout.strip() or f"score.py exited {returncode}"
    return {
        "status": 2,
        "validation": "unscored",
        "detail": detail,
        "simulated": None,
        "misses": None,
        "missed_commits": None,
        "line": None,
    }


def _report(
    output: Path,
    command: list[str],
    validation: str,
    cpu_seconds: float | None,
    detail: str,
    stdout: str,
    stderr: str,
    exit_code: int | None,
    simulated: str | None = None,
    misses: int | None = None,
    missed_commits: list[str] | None = None,
    line: int | None = None,
) -> str:
    lines = [f"candidate: {output}", f"validation: {validation}"]
    if simulated is not None:
        lines.append(f"simulated: {simulated}")
    if misses is not None:
        lines.append(f"misses: {misses}")
    if missed_commits is not None:
        lines.append(
            "missed_commits: " + (", ".join(missed_commits) if missed_commits else "none")
        )
    if line is not None:
        lines.append(f"line: {line}")
    if cpu_seconds is None:
        lines.append("cpu: unmeasured")
    else:
        lines.append(
            f"cpu: {cpu_seconds} s supplied with --cpu-seconds; "
            "benchmark.py did not measure a process"
        )
    if detail:
        lines.append(f"detail: {detail}")
    lines.append(f"command: {shlex.join(command)}")
    if exit_code is not None:
        lines.append(f"score.py exit: {exit_code}")
    lines.append("--- score.py stdout ---")
    lines.append(stdout.rstrip("\n"))
    lines.append("--- score.py stderr ---")
    lines.append(stderr.rstrip("\n"))
    return "\n".join(lines) + "\n"


_METHODS = ("baseline_three_worker", "lpt", "improved")
_PRIMARY_GROUPS = ("full", "small", "uneven", "synthetic")
_FNV_OFFSET = 1469598103934665603
_FNV_PRIME = 1099511628211


class ScheduleCase:
    def __init__(
        self,
        name: str,
        group: str,
        tests: list[dict],
        selected: set[int],
        max_workers: int,
    ) -> None:
        self.name = name
        self.group = group
        self.tests = tests
        self.selected = selected
        self.max_workers = max_workers


def load_catalog(data_dir: Path) -> list[dict]:
    payload = json.loads((data_dir / "tests.json").read_text(encoding="utf-8"))
    tests = payload["tests"]
    if not isinstance(tests, list):
        raise ValueError("tests.json key 'tests' is not a list")
    return tests


def starter_split(tests: list[dict], worker_count: int) -> list[list[int]]:
    """Copy of ``selector.split`` on origin/main.

    Longest ``seconds`` first. Equal seconds keep catalog order. The lightest
    worker wins, and an equal load keeps the lowest index.
    """
    workers: list[list[int]] = [[] for _ in range(worker_count)]
    loads = [0.0] * worker_count
    for test in sorted(tests, key=lambda test: test["seconds"], reverse=True):
        lightest = loads.index(min(loads))
        workers[lightest].append(test["number"])
        loads[lightest] += test["seconds"]
    return workers


def run_schedule_comparison(
    catalog: list[dict],
    *,
    repeats: int = 11,
    source: str = "tests.json",
) -> tuple[str, list[dict]]:
    """Compare the starter split, longest-first, and the refined scheduler.

    Does not read answers.json. Scheduling CPU is this process's ``process_time``.
    """
    if repeats < 1:
        raise ValueError("repeats must be at least 1")
    rows: list[dict] = []
    for case in _build_cases(catalog):
        case_rows = [_measure_case(case, method, repeats) for method in _METHODS]
        lpt_objective = next(row["objective_ms"] for row in case_rows if row["method"] == "lpt")
        improved = next(row for row in case_rows if row["method"] == "improved")
        improved["improved_regressed"] = improved["objective_ms"] > lpt_objective
        rows.extend(case_rows)
    return _format_comparison(catalog, rows, repeats, source), rows


def _build_cases(catalog: list[dict]) -> list[ScheduleCase]:
    durations = _durations(catalog)
    numbers = sorted(durations)
    everyone = set(numbers)
    cases = [ScheduleCase("full_suite", "full", catalog, everyone, 8)]
    if numbers:
        cases.append(ScheduleCase("small_n1", "small", catalog, {numbers[0]}, 8))
        cases.append(ScheduleCase("small_n5", "small", catalog, set(numbers[:5]), 8))
        cases.append(ScheduleCase("small_n10", "small", catalog, set(numbers[:10]), 8))
    if len(numbers) >= 12:
        picked = random.Random(11).sample(numbers, 12)
        cases.append(ScheduleCase("small_seed11_n12", "small", catalog, set(picked), 8))
    ranked = sorted(numbers, key=lambda number: (-durations[number], number))
    if ranked:
        cases.append(ScheduleCase("uneven_longest15", "uneven", catalog, set(ranked[:15]), 8))
    long_tail = {number for number in numbers if durations[number] >= 200}
    if long_tail:
        cases.append(ScheduleCase("uneven_at_least_200ms", "uneven", catalog, long_tail, 8))
    swap = {1224, 1107, 1123, 1428, 1440, 1295, 1382}
    if swap <= everyone:
        cases.append(ScheduleCase("uneven_swap", "uneven", catalog, swap, 8))
    classic = _classic_catalog()
    cases.append(ScheduleCase("uneven_classic", "uneven", classic, {1, 2, 3, 4, 5, 6, 7}, 8))
    power = _power_catalog()
    cases.append(
        ScheduleCase("uneven_fixed_power", "uneven", power, set(range(len(power))), 8)
    )
    for seed, count in ((0, 25), (1, 40), (2, 80), (3, 15), (4, 100)):
        synthetic = _synthetic_catalog(seed, count)
        cases.append(
            ScheduleCase(
                f"synthetic_seed{seed}_n{count}",
                "synthetic",
                synthetic,
                set(range(count)),
                8,
            )
        )
    for cap in (1, 2, 3, 4, 8):
        cases.append(ScheduleCase(f"cap{cap}_full_suite", "worker_cap", catalog, everyone, cap))
        cases.append(
            ScheduleCase(f"cap{cap}_classic", "worker_cap", classic, {1, 2, 3, 4, 5, 6, 7}, cap)
        )
    return cases


def _entry(number: int, seconds: float) -> dict:
    return {
        "number": number,
        "id": f"synthetic::{number}",
        "file": "synthetic.py",
        "seconds": seconds,
    }


def _classic_catalog() -> list[dict]:
    # 5, 5, 4, 4, 3, 3, 3 seconds. Longest-first wants 4 workers; the repair uses 3.
    return [_entry(number, seconds) for number, seconds in (
        (1, 5.0), (2, 5.0), (3, 4.0), (4, 4.0), (5, 3.0), (6, 3.0), (7, 3.0),
    )]


def _power_catalog() -> list[dict]:
    milliseconds = (
        20000, 18000, 17000, 9000, 8000, 8000, 7000, 3000, 3000, 3000, 1000, 1000, 500, 500, 100,
    )
    return [_entry(number, ms / 1000) for number, ms in enumerate(milliseconds)]


def _synthetic_catalog(seed: int, count: int) -> list[dict]:
    generator = random.Random(seed)
    tests = []
    for number in range(count):
        roll = generator.randrange(8)
        if roll == 0:
            milliseconds = generator.randrange(3000, 12001)
        elif roll <= 2:
            milliseconds = generator.randrange(400, 3000)
        else:
            milliseconds = generator.randrange(0, 80)
        tests.append(_entry(number, milliseconds / 1000))
    return tests


def _measure_case(case: ScheduleCase, method: str, repeats: int) -> dict:
    durations = _durations(case.tests)
    valid = {number for number in case.selected if number in durations}
    entries = [
        test
        for test in case.tests
        if isinstance(test, dict) and test.get("number") in valid
    ]
    if method == "baseline_three_worker":
        schedule = lambda: _occupied(starter_split(entries, 3))
    elif method == "lpt":
        schedule = lambda: schedule_tests_lpt(valid, case.tests, case.max_workers)
    elif method == "improved":
        schedule = lambda: schedule_tests(valid, case.tests, case.max_workers)
    else:
        raise ValueError(method)
    workers, cold_cpu, warm_cpu = _time_schedule(schedule, repeats)
    return _score_row(case, method, workers, durations, valid, cold_cpu, warm_cpu)


def _occupied(workers: list[list[int]]) -> list[list[int]]:
    return [worker for worker in workers if worker]


def _time_schedule(schedule, repeats: int) -> tuple[list[list[int]], float, float]:
    cold_results: list[list[list[int]]] = []
    cold_samples: list[float] = []
    for _ in range(repeats):
        clear_schedule_cache()
        started = time.process_time()
        cold_results.append(schedule())
        cold_samples.append(time.process_time() - started)
    if any(result != cold_results[0] for result in cold_results[1:]):
        raise RuntimeError("nondeterministic schedule")
    warm_results: list[list[list[int]]] = []
    warm_samples: list[float] = []
    for _ in range(repeats):
        started = time.process_time()
        warm_results.append(schedule())
        warm_samples.append(time.process_time() - started)
    if any(result != cold_results[0] for result in warm_results):
        raise RuntimeError("nondeterministic schedule")
    return cold_results[0], _median(cold_samples), _median(warm_samples)


def _median(samples: list[float]) -> float:
    ordered = sorted(samples)
    return ordered[len(ordered) // 2]


def _score_row(
    case: ScheduleCase,
    method: str,
    workers: list[list[int]],
    durations: dict[int, int],
    valid: set[int],
    cold_cpu: float,
    warm_cpu: float,
) -> dict:
    occupied = _occupied(workers)
    flat = [number for worker in occupied for number in worker]
    if occupied:
        span = max(sum(durations[number] for number in worker) for worker in occupied)
    else:
        span = 0
    width = len(occupied)
    overhead = 2000 * width
    objective = span + overhead
    if valid:
        total = sum(durations[number] for number in valid)
        longest = max(durations[number] for number in valid)
    else:
        total = 0
        longest = 0
    bound = _makespan_bound(total, longest, width)
    partition = "ok" if len(flat) == len(set(flat)) and set(flat) == valid else "bad"
    return {
        "case": case.name,
        "group": case.group,
        "n": len(valid),
        "max_workers": case.max_workers,
        "method": method,
        "makespan_ms": span,
        "workers": width,
        "overhead_ms": overhead,
        "objective_ms": objective,
        "gap_ms": span - bound,
        "scheduled": len(flat),
        "partition": partition,
        "packing": _packing_signature(occupied),
        "cold_cpu_s": cold_cpu,
        "warm_cpu_s": warm_cpu,
        "improved_regressed": False,
    }


def _makespan_bound(total: int, longest: int, worker_count: int) -> int:
    if worker_count <= 0:
        return 0
    shared = (total + worker_count - 1) // worker_count
    return longest if longest > shared else shared


def _packing_signature(workers: list[list[int]]) -> str:
    fingerprint = _FNV_OFFSET
    for worker in workers:
        for number in worker:
            fingerprint ^= number + 0x9E3779B97F4A7C15
            fingerprint = (fingerprint * _FNV_PRIME) & 0xFFFFFFFFFFFFFFFF
        fingerprint ^= 0xFF
        fingerprint = (fingerprint * _FNV_PRIME) & 0xFFFFFFFFFFFFFFFF
    return f"{fingerprint:016x}"


def _format_comparison(
    catalog: list[dict],
    rows: list[dict],
    repeats: int,
    source: str,
) -> str:
    lines = [
        "scheduling comparison",
        f"source: {source}",
        f"catalog_tests: {len(catalog)}",
        "answers_json: not read",
        "objective_ms = makespan_ms + 2000 * nonempty workers",
        "baseline_three_worker: selector.split from origin/main, always 3 workers, longest seconds first; scored in milliseconds",
        "lpt: schedule_tests_lpt (previous longest-first search over 1..max_workers)",
        "improved: schedule_tests (lower-bound worker search, then makespan repair and local moves/swaps)",
        f"cold_cpu_s: median process_time of {repeats} calls with the schedule cache cleared",
        f"warm_cpu_s: median process_time of {repeats} more calls without clearing the cache",
        "determinism: pass",
        "",
    ]
    by_case: dict[str, dict[str, dict]] = {}
    for row in rows:
        by_case.setdefault(row["case"], {})[row["method"]] = row
        lines.append(_format_row(row))
    lines.append("")
    for case_name, methods in by_case.items():
        lines.append(_format_case_delta(case_name, methods))
    lines.append("")
    for label, groups in (
        ("full", ("full",)),
        ("small", ("small",)),
        ("uneven", ("uneven",)),
        ("synthetic", ("synthetic",)),
        ("primary", _PRIMARY_GROUPS),
        ("worker_cap", ("worker_cap",)),
    ):
        lines.append(_format_group(label, rows, groups))
    full = by_case.get("full_suite", {})
    if full:
        lines.append(
            "summary full_suite_30_commits_s "
            + " ".join(
                f"{method}={full[method]['objective_ms'] * 30 / 1000:.3f}"
                for method in _METHODS
                if method in full
            )
            + " note=30 times one full-catalog schedule, not a rescored answers file"
        )
    lines.append(
        "summary worker_cap_note baseline_three_worker stays at 3 workers; lpt and improved honor max_workers"
    )
    return "\n".join(lines) + "\n"


def _format_row(row: dict) -> str:
    return (
        f"case={row['case']} group={row['group']} n={row['n']} max_workers={row['max_workers']} "
        f"method={row['method']} makespan_ms={row['makespan_ms']} workers={row['workers']} "
        f"overhead_ms={row['overhead_ms']} objective_ms={row['objective_ms']} gap_ms={row['gap_ms']} "
        f"scheduled={row['scheduled']} partition={row['partition']} packing={row['packing']} "
        f"cold_cpu_s={row['cold_cpu_s']:.6f} warm_cpu_s={row['warm_cpu_s']:.6f}"
    )


def _format_case_delta(case_name: str, methods: dict[str, dict]) -> str:
    baseline = methods["baseline_three_worker"]["objective_ms"]
    lpt = methods["lpt"]["objective_ms"]
    improved = methods["improved"]["objective_ms"]
    return (
        f"delta case={case_name} "
        f"saved_vs_lpt_ms={lpt - improved} saved_vs_lpt_pct={_percent(lpt - improved, lpt)} "
        f"saved_vs_baseline_ms={baseline - improved} "
        f"saved_vs_baseline_pct={_percent(baseline - improved, baseline)}"
    )


def _format_group(label: str, rows: list[dict], groups: tuple[str, ...]) -> str:
    totals = {
        method: sum(
            row["objective_ms"]
            for row in rows
            if row["group"] in groups and row["method"] == method
        )
        for method in _METHODS
    }
    return (
        f"summary group={label} "
        f"baseline_ms={totals['baseline_three_worker']} lpt_ms={totals['lpt']} "
        f"improved_ms={totals['improved']} "
        f"saved_vs_lpt_ms={totals['lpt'] - totals['improved']} "
        f"saved_vs_lpt_pct={_percent(totals['lpt'] - totals['improved'], totals['lpt'])} "
        f"saved_vs_baseline_ms={totals['baseline_three_worker'] - totals['improved']} "
        f"saved_vs_baseline_pct={_percent(totals['baseline_three_worker'] - totals['improved'], totals['baseline_three_worker'])}"
    )


def _percent(saved: int, base: int) -> str:
    if base == 0:
        return "0.0000"
    return f"{saved / base * 100:.4f}"


if __name__ == "__main__":
    sys.exit(main())
