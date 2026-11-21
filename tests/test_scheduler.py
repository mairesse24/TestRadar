"""Scheduler and benchmark tests.

pytest is not installed in this environment. Run from the repo root:

    python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import json
import random
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from analyzer.scheduler import (  # noqa: E402
    clear_schedule_cache,
    duration_milliseconds,
    schedule_tests,
    schedule_tests_lpt,
)
from benchmark import run_schedule_comparison  # noqa: E402
import score as score_py  # noqa: E402


def entry(number: int, seconds: float) -> dict:
    return {
        "number": number,
        "id": f"tests/test_it.py::test_{number}",
        "file": "tests/test_it.py",
        "seconds": seconds,
    }


def catalog(*pairs: tuple[int, float]) -> list[dict]:
    return [entry(number, seconds) for number, seconds in pairs]


def packing_objective(workers: list[list[int]], tests: list[dict]) -> tuple[int, int, int]:
    """Return ``(objective_ms, nonempty workers, makespan_ms)`` using score.py rounding."""
    durations: dict[int, int] = {}
    for test in tests:
        number = test.get("number")
        seconds = test.get("seconds")
        if isinstance(number, bool) or not isinstance(number, int):
            continue
        if isinstance(seconds, bool) or not isinstance(seconds, (int, float)):
            continue
        durations[number] = duration_milliseconds(seconds)
    occupied = [worker for worker in workers if worker]
    if not occupied:
        return (0, 0, 0)
    span = max(sum(durations[number] for number in worker) for worker in occupied)
    return (span + 2000 * len(occupied), len(occupied), span)


class SchedulerTests(unittest.TestCase):
    def test_no_tests(self) -> None:
        self.assertEqual(schedule_tests(set(), []), [])
        self.assertEqual(schedule_tests(set(), catalog((1, 1.0), (2, 2.0))), [])

    def test_one_test(self) -> None:
        self.assertEqual(schedule_tests({4}, catalog((4, 1.5))), [[4]])

    def test_skewed_durations(self) -> None:
        # Durations ms: 10=5000, 3=4000, 1=1000, 2=1000.
        # LPT order: 10, 3, 1, 2.
        # k=1 load 11000 objective 13000.
        # k=2: w0 gets 10 then 2 (6000), w1 gets 3 then 1 (5000).
        #      makespan 6000 objective 10000.
        # k=3 makespan 5000 objective 11000. k=4 objective 13000.
        # Canonical form sorts workers by minimum test number.
        tests = catalog((10, 5.0), (3, 4.0), (1, 1.0), (2, 1.0))
        self.assertEqual(schedule_tests({10, 3, 1, 2}, tests), [[1, 3], [2, 10]])

    def test_many_tests(self) -> None:
        # 12 tests of 3s = 3000 ms. Best k is 4 (makespan 9000 + 8000 = 17000).
        # k=3 is 12000 + 6000 = 18000. k=5 is 9000 + 10000 = 19000.
        tests = catalog(*[(number, 3.0) for number in range(12)])
        selected = set(range(12))
        result = schedule_tests(selected, tests)
        self.assertEqual(
            result,
            [[0, 4, 8], [1, 5, 9], [2, 6, 10], [3, 7, 11]],
        )
        self.assert_partition(selected, result)
        self.assertEqual(schedule_tests(selected, list(reversed(tests))), result)

    def test_invalid_numbers_are_dropped(self) -> None:
        tests = catalog((1, 0.5), (7, 0.5))
        result = schedule_tests({1, 99, -3, 7}, tests)
        self.assertEqual(result, [[1, 7]])
        self.assertEqual(schedule_tests({50}, tests), [])
        self.assertEqual(schedule_tests({1, 7}, []), [])
        self.assertEqual(schedule_tests(["1", 1.5], tests), [])
        self.assertEqual(schedule_tests([1, 1, 7], tests), [[1, 7]])

        coerced = schedule_tests({True}, catalog((1, 1.0)))
        self.assertEqual(coerced, [[1]])
        self.assertNotIsInstance(coerced[0][0], bool)

        integral = schedule_tests({1.0}, catalog((1, 1.0)))
        self.assertEqual(integral, [[1]])
        self.assertNotIsInstance(integral[0][0], bool)

    def test_one_worker_beats_two_when_overhead_exceeds_makespan_savings(self) -> None:
        # Two tests at 1000 ms. One worker: 2000 + 2000 = 4000.
        # Two workers: 1000 + 4000 = 5000. The extra worker costs more than it saves.
        tests = catalog((1, 1.0), (2, 1.0))
        self.assertEqual(schedule_tests({1, 2}, tests), [[1, 2]])

    def test_two_workers_win_when_makespan_savings_exceed_overhead(self) -> None:
        # Two tests at 3000 ms. One worker: 6000 + 2000 = 8000.
        # Two workers: 3000 + 4000 = 7000.
        tests = catalog((1, 3.0), (2, 3.0))
        self.assertEqual(schedule_tests({1, 2}, tests), [[1], [2]])
        self.assertEqual(schedule_tests({1, 2}, tests, max_workers=1), [[1, 2]])

    def test_equal_objective_picks_smaller_worker_count(self) -> None:
        # 2000 ms each. k=1 is 4000 + 2000 = 6000. k=2 is 2000 + 4000 = 6000.
        tests = catalog((1, 2.0), (2, 2.0))
        self.assertEqual(duration_milliseconds(2.0), 2000)
        self.assertEqual(schedule_tests({2, 1}, tests), [[1, 2]])

    def test_worker_count_is_capped_at_eight(self) -> None:
        # 9 tests of 10s. Nine workers would score 10000 + 18000 = 28000.
        # The cap stops the search at 8. Among k=1..8 the best is k=5
        # (makespan 20000 + 10000 = 30000), which beats k=8 (36000).
        tests = catalog(*[(number, 10.0) for number in range(9)])
        result = schedule_tests(set(range(9)), tests, max_workers=20)
        self.assertEqual(result, [[0, 5], [1, 6], [2, 7], [3, 8], [4]])
        self.assertLessEqual(len(result), 8)

    def test_duration_milliseconds_match_score_py(self) -> None:
        for seconds in (0.0005, 1.0005, 2.0005, 3.0005, 0.034, 0.0, 1.5, 10.0):
            self.assertEqual(
                duration_milliseconds(seconds),
                score_py._duration_milliseconds(seconds),
                seconds,
            )
        self.assertEqual(duration_milliseconds(1.0005), 1001)
        self.assertEqual(round(1.0005 * 1000), 1000)

        sample = json.loads((ROOT / "sample" / "tests.json").read_text(encoding="utf-8"))
        for test in sample["tests"]:
            self.assertEqual(
                duration_milliseconds(test["seconds"]),
                score_py._duration_milliseconds(test["seconds"]),
            )

    def test_full_sample_partition_is_deterministic(self) -> None:
        sample = json.loads((ROOT / "sample" / "tests.json").read_text(encoding="utf-8"))
        tests = sample["tests"]
        selected = {test["number"] for test in tests}
        self.assertEqual(len(selected), 1620)
        first = schedule_tests(selected, tests)
        second = schedule_tests(set(reversed(list(selected))), list(reversed(tests)))
        self.assertEqual(first, second)
        self.assert_partition(selected, first)
        self.assertLessEqual(len(first), 8)
        self.assertGreaterEqual(len(first), 1)

    def test_last_valid_catalog_entry_wins(self) -> None:
        # score.py keeps the last tests.json entry for a repeated number.
        # Last durations are 1000, 4000, 4000 ms, so two workers win: [[1, 2], [3]].
        # The first 5s entry for test 1 would have made three workers win.
        tests = [
            entry(1, 5.0),
            entry(1, 1.0),
            entry(2, 4.0),
            entry(3, 4.0),
        ]
        self.assertEqual(schedule_tests({1, 2, 3}, tests), [[1, 2], [3]])

    def test_non_positive_max_workers_returns_empty(self) -> None:
        self.assertEqual(schedule_tests({1}, catalog((1, 1.0)), max_workers=0), [])

    def test_full_sample_matches_longest_first_and_lower_bound(self) -> None:
        sample = json.loads((ROOT / "sample" / "tests.json").read_text(encoding="utf-8"))
        tests = sample["tests"]
        selected = {test["number"] for test in tests}
        clear_schedule_cache()
        refined = schedule_tests(selected, tests)
        previous = schedule_tests_lpt(selected, tests)
        self.assertEqual(refined, previous)
        longest = max(duration_milliseconds(test["seconds"]) for test in tests)
        objective, width, span = packing_objective(refined, tests)
        self.assertEqual(width, 4)
        self.assertEqual(span, longest)
        self.assertEqual(objective, longest + 8000)
        self.assertEqual(objective, 36817)
        mutated = [worker[:] for worker in refined]
        mutated[0].append(-1)
        clear_schedule_cache()
        again = schedule_tests(selected, tests)
        self.assertEqual(again, previous)
        self.assertNotIn(-1, [number for worker in again for number in worker])

    def test_classic_repair_uses_three_workers(self) -> None:
        # 5000+5000+4000+4000+3000+3000+3000. Longest-first objective is 16000.
        # First-fit at makespan 9000 is optimal: 15000.
        tests = catalog(
            (1, 5.0), (2, 5.0), (3, 4.0), (4, 4.0), (5, 3.0), (6, 3.0), (7, 3.0),
        )
        selected = {1, 2, 3, 4, 5, 6, 7}
        previous = schedule_tests_lpt(selected, tests)
        refined = schedule_tests(selected, tests)
        self.assertEqual(packing_objective(previous, tests), (16000, 4, 8000))
        self.assertEqual(refined, [[1, 3], [2, 4], [5, 6, 7]])
        self.assertEqual(packing_objective(refined, tests), (15000, 3, 9000))
        capped = schedule_tests(selected, tests, max_workers=2)
        self.assertLessEqual(len(capped), 2)
        self.assertEqual(packing_objective(capped, tests)[0], 18000)
        self.assertLess(
            packing_objective(capped, tests)[0],
            packing_objective(schedule_tests_lpt(selected, tests, max_workers=2), tests)[0],
        )

    def test_pairwise_swap_lowers_makespan(self) -> None:
        # Catalog milliseconds 6686, 3863, 3537, 2533, 2232, 1963, 1406.
        # Longest-first makespan is 8033 (objective 14033). One swap reaches
        # 7802 (objective 13802): test 1224 stays alone, and the rest do not
        # split into two loads of 7767.
        sample = json.loads((ROOT / "sample" / "tests.json").read_text(encoding="utf-8"))
        numbers = {1224, 1107, 1123, 1428, 1440, 1295, 1382}
        tests = [test for test in sample["tests"] if test["number"] in numbers]
        previous = schedule_tests_lpt(numbers, tests)
        refined = schedule_tests(numbers, tests)
        self.assertEqual(packing_objective(previous, tests), (14033, 3, 8033))
        self.assertEqual(packing_objective(refined, tests), (13802, 3, 7802))
        self.assertEqual([worker for worker in refined if 1224 in worker], [[1224]])
        self.assert_partition(numbers, refined)
        self.assertLess(packing_objective(refined, tests)[0], packing_objective(previous, tests)[0])

    def test_refined_objective_never_exceeds_longest_first(self) -> None:
        sample = json.loads((ROOT / "sample" / "tests.json").read_text(encoding="utf-8"))
        catalog_tests = sample["tests"]
        durations = {
            test["number"]: duration_milliseconds(test["seconds"]) for test in catalog_tests
        }
        long_tail = {number for number, duration in durations.items() if duration >= 200}
        self.assertLess(
            packing_objective(schedule_tests(long_tail, catalog_tests), catalog_tests)[0],
            packing_objective(schedule_tests_lpt(long_tail, catalog_tests), catalog_tests)[0],
        )
        generator = random.Random(2026)
        for seed in range(30):
            generator.seed(seed)
            count = generator.randint(1, 36)
            pairs = []
            for number in range(count):
                seconds = generator.choice((0.0, 0.001, 0.05, 0.4, 1.0, 2.0, 3.0, 5.0, 8.0, 13.0))
                pairs.append((number, float(seconds)))
            tests = catalog(*pairs)
            selected = set(range(count))
            if count > 4 and seed % 2 == 0:
                selected.discard(0)
            refined = schedule_tests(selected, tests)
            previous = schedule_tests_lpt(selected, tests)
            self.assertLessEqual(
                packing_objective(refined, tests)[0],
                packing_objective(previous, tests)[0],
            )
            self.assert_partition(selected, refined)
            self.assertLessEqual(len(refined), 8)
            self.assertEqual(refined, schedule_tests(set(reversed(list(selected))), list(reversed(tests))))

    def test_malformed_inputs_stay_predictable(self) -> None:
        self.assertEqual(schedule_tests(None, catalog((1, 1.0))), [])
        self.assertEqual(schedule_tests({1}, None), [])
        self.assertEqual(schedule_tests({1}, "nope"), [])
        self.assertEqual(schedule_tests({1}, [None, 3, "x"]), [])
        self.assertEqual(schedule_tests({1}, catalog((1, float("nan")))), [])
        self.assertEqual(schedule_tests({1}, catalog((1, float("inf")))), [])
        self.assertEqual(schedule_tests({1}, [{"number": 1, "seconds": True}]), [])
        self.assertEqual(schedule_tests({1}, [{"number": True, "seconds": 1.0}]), [])
        self.assertEqual(schedule_tests({1}, catalog((1, 1.0)), max_workers=True), [])
        self.assertEqual(schedule_tests({1}, catalog((1, 1.0)), max_workers=1.5), [])
        negative = schedule_tests({1, 2}, catalog((1, -1.0), (2, 3.0)))
        self.assert_partition({1, 2}, negative)
        self.assertLessEqual(len(negative), 8)

    def test_cache_does_not_ignore_catalog_edits(self) -> None:
        tests = catalog((1, 1.0), (2, 1.0))
        clear_schedule_cache()
        self.assertEqual(schedule_tests({1, 2}, tests), [[1, 2]])
        tests[0]["seconds"] = 5.0
        tests[1]["seconds"] = 5.0
        self.assertEqual(schedule_tests({1, 2}, tests), [[1], [2]])

    def assert_partition(self, selected: set[int], result: list[list[int]]) -> None:
        flat = [number for worker in result for number in worker]
        self.assertEqual(sorted(flat), sorted(selected))
        self.assertEqual(len(flat), len(set(flat)))
        self.assertTrue(result)
        for worker in result:
            self.assertTrue(worker)
            self.assertEqual(worker, sorted(worker))
        self.assertEqual(result, sorted(result, key=lambda worker: worker[0]))


class BenchmarkTests(unittest.TestCase):
    def test_benchmark_scores_accepts_misses_and_rejections(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            data = root / "data"
            commits = data / "commits"
            commits.mkdir(parents=True)
            (commits / "abc.patch").write_bytes(b"diff --git a/a b/a\n")
            (data / "tests.json").write_text(
                json.dumps(
                    {
                        "tests": [
                            entry(0, 1.5),
                            entry(1, 0.25),
                        ]
                    }
                ),
                encoding="utf-8",
            )
            answers = root / "answers.json"
            answers.write_text(
                json.dumps({"commits": {"abc": {"failing": [1], "kind": "code"}}}),
                encoding="utf-8",
            )

            # 1500 + 250 + 2000 = 3750 ms, and test 1 is included, so no miss.
            accepted = root / "accepted.jsonl"
            accepted.write_bytes(
                json.dumps({"commit": "abc", "workers": [[0, 1]]}).encode() + b"\n"
            )
            # Test 1 is omitted: 1500 + 2000 + 300000 = 303500 ms.
            missed = root / "missed.jsonl"
            missed.write_bytes(
                json.dumps({"commit": "abc", "workers": [[0]]}).encode() + b"\n"
            )
            rejected = root / "rejected.jsonl"
            rejected.write_bytes(b"not json\n")
            utf16 = root / "utf16.jsonl"
            utf16.write_bytes('{"commit": "abc", "workers": [[0]]}\n'.encode("utf-16"))
            empty = root / "empty.jsonl"
            empty.write_bytes(b"")

            accepted_run = self.run_benchmark(accepted, data, answers)
            self.assertEqual(accepted_run.returncode, 0, accepted_run.stdout)
            self.assertIn("CPU time is unmeasured.", accepted_run.stdout)
            self.assertIn("validation: accepted", accepted_run.stdout)
            self.assertIn("simulated: 3.750 s", accepted_run.stdout)
            self.assertIn("misses: 0", accepted_run.stdout)
            self.assertIn("missed_commits: none", accepted_run.stdout)
            self.assertIn("cpu: unmeasured", accepted_run.stdout)

            missed_run = self.run_benchmark(missed, data, answers)
            self.assertEqual(missed_run.returncode, 0, missed_run.stdout)
            self.assertIn("validation: accepted", missed_run.stdout)
            self.assertIn("simulated: 303.500 s", missed_run.stdout)
            self.assertIn("misses: 1", missed_run.stdout)
            self.assertIn("missed_commits: abc", missed_run.stdout)
            self.assertIn("cpu: unmeasured", missed_run.stdout)

            rejected_run = self.run_benchmark(rejected, data, answers)
            self.assertEqual(rejected_run.returncode, 1, rejected_run.stdout)
            self.assertIn("validation: not_json", rejected_run.stdout)
            self.assertIn("line: 1", rejected_run.stdout)
            self.assertIn("cpu: unmeasured", rejected_run.stdout)

            utf16_run = self.run_benchmark(utf16, data, answers)
            self.assertEqual(utf16_run.returncode, 1, utf16_run.stdout)
            self.assertIn("validation: not_utf8", utf16_run.stdout)
            self.assertIn("cpu: unmeasured", utf16_run.stdout)

            empty_run = self.run_benchmark(empty, data, answers)
            self.assertEqual(empty_run.returncode, 1, empty_run.stdout)
            self.assertIn("validation: missing_commit", empty_run.stdout)

            both = self.run_benchmark_many([accepted, missed], data, answers)
            self.assertEqual(both.returncode, 0, both.stdout)
            self.assertEqual(both.stdout.count("validation: accepted"), 2)

    def test_benchmark_reports_layout_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            data = root / "data"
            (data / "commits").mkdir(parents=True)
            (data / "commits" / "abc.patch").write_bytes(b"\n")
            (data / "tests.json").write_text(
                json.dumps({"tests": [entry(0, 1.0)]}),
                encoding="utf-8",
            )
            answers = root / "answers.json"
            answers.write_text(
                json.dumps(
                    {
                        "commits": {
                            "abc": {"failing": [], "kind": "code"},
                            "missing": {"failing": [], "kind": "code"},
                        }
                    }
                ),
                encoding="utf-8",
            )
            output = root / "out.jsonl"
            output.write_bytes(
                json.dumps({"commit": "abc", "workers": [[0]]}).encode() + b"\n"
            )
            completed = self.run_benchmark(output, data, answers)
            self.assertEqual(completed.returncode, 2, completed.stdout)
            self.assertIn("validation: unscored", completed.stdout)
            self.assertIn("answers have no patch", completed.stdout)
            self.assertIn("cpu: unmeasured", completed.stdout)
            self.assertIn("CPU time is unmeasured.", completed.stdout)

    def test_compare_schedulers_reports_three_rules(self) -> None:
        sample = json.loads((ROOT / "sample" / "tests.json").read_text(encoding="utf-8"))
        text, rows = run_schedule_comparison(sample["tests"], repeats=1, source="sample/tests.json")
        again, again_rows = run_schedule_comparison(sample["tests"], repeats=1, source="sample/tests.json")
        self.assertEqual(
            [(row["case"], row["method"], row["objective_ms"], row["packing"]) for row in rows],
            [(row["case"], row["method"], row["objective_ms"], row["packing"]) for row in again_rows],
        )
        self.assertIn("answers_json: not read", text)
        self.assertIn("determinism: pass", text)
        full = {
            row["method"]: row
            for row in rows
            if row["case"] == "full_suite"
        }
        self.assertEqual(full["baseline_three_worker"]["objective_ms"], 40349)
        self.assertEqual(full["baseline_three_worker"]["workers"], 3)
        self.assertEqual(full["baseline_three_worker"]["makespan_ms"], 34349)
        self.assertEqual(full["lpt"]["objective_ms"], 36817)
        self.assertEqual(full["improved"]["objective_ms"], 36817)
        self.assertEqual(full["improved"]["scheduled"], 1620)
        self.assertEqual(full["lpt"]["packing"], full["improved"]["packing"])
        self.assertTrue(all(row["partition"] == "ok" for row in rows))
        self.assertTrue(all(row["workers"] <= 8 for row in rows))
        by_case: dict[str, dict[str, dict]] = {}
        for row in rows:
            by_case.setdefault(row["case"], {})[row["method"]] = row
        for methods in by_case.values():
            self.assertLessEqual(
                methods["improved"]["objective_ms"],
                methods["lpt"]["objective_ms"],
            )
            self.assertEqual(methods["improved"]["scheduled"], methods["lpt"]["scheduled"])
        self.assertEqual(by_case["uneven_classic"]["lpt"]["objective_ms"], 16000)
        self.assertEqual(by_case["uneven_classic"]["improved"]["objective_ms"], 15000)
        self.assertEqual(by_case["uneven_swap"]["lpt"]["objective_ms"], 14033)
        self.assertEqual(by_case["uneven_swap"]["improved"]["objective_ms"], 13802)
        self.assertLess(
            by_case["synthetic_seed3_n15"]["improved"]["objective_ms"],
            by_case["synthetic_seed3_n15"]["lpt"]["objective_ms"],
        )
        self.assertIn("summary full_suite_30_commits_s", text)
        self.assertIn("baseline_three_worker=1210.470", text)
        self.assertIn("lpt=1104.510", text)
        self.assertIn("improved=1104.510", text)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            data = root / "data"
            data.mkdir()
            (data / "tests.json").write_text(
                json.dumps({"tests": [entry(1, 5.0), entry(2, 5.0)]}),
                encoding="utf-8",
            )
            completed = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "benchmark.py"),
                    "--compare-schedulers",
                    "--repeats",
                    "1",
                    "--data-dir",
                    str(data),
                ],
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
            )
            self.assertEqual(completed.returncode, 0, completed.stderr + completed.stdout)
            self.assertIn("method=baseline_three_worker", completed.stdout)
            self.assertIn("method=lpt", completed.stdout)
            self.assertIn("method=improved", completed.stdout)
            self.assertIn("answers_json: not read", completed.stdout)
            self.assertNotIn("CPU time is unmeasured.", completed.stdout)
            self.assertNotIn("answers.json", completed.stdout)

    def run_benchmark(self, output: Path, data: Path, answers: Path) -> subprocess.CompletedProcess[str]:
        return self.run_benchmark_many([output], data, answers)

    def run_benchmark_many(
        self,
        outputs: list[Path],
        data: Path,
        answers: Path,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                sys.executable,
                str(ROOT / "benchmark.py"),
                *[str(output) for output in outputs],
                "--data-dir",
                str(data),
                "--answers",
                str(answers),
                "--score-py",
                str(ROOT / "score.py"),
            ],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )


if __name__ == "__main__":
    unittest.main()
