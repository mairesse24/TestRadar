"""Scheduler and benchmark tests.

pytest is not installed in this environment. Run from the repo root:

    python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from analyzer.scheduler import duration_milliseconds, schedule_tests  # noqa: E402
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
