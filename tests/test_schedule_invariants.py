"""Every selection survives scheduling intact: no lost or duplicated tests and
at most eight workers. Runs the real scheduler over the sample catalog and
through selector.run, with seeded random selections."""

import contextlib
import io
import json
from pathlib import Path
import random
from tempfile import TemporaryDirectory
import unittest
from unittest import mock

from analyzer.scheduler import schedule_tests
from analyzer.test_selector import Selection
import selector
from tools.selection_eval.check_output import check

CATALOG = json.loads((Path(__file__).resolve().parents[1] / "sample/tests.json").read_text("utf-8"))["tests"]
NUMBERS = [test["number"] for test in CATALOG]


def selections(seed=20261121, count=60):
    rng = random.Random(seed)
    sizes = [1, 2, 3, 7, 8, 9, 20, 100, len(NUMBERS)]
    for size in sizes:
        yield set(rng.sample(NUMBERS, size))
    for _ in range(count):
        yield set(rng.sample(NUMBERS, rng.randint(1, len(NUMBERS))))
    # long tests only, and a mix with one dominant test
    by_length = sorted(CATALOG, key=lambda test: -test["seconds"])
    yield {test["number"] for test in by_length[:12]}
    yield {by_length[0]["number"]} | {test["number"] for test in by_length[-50:]}


class ScheduleInvariants(unittest.TestCase):
    def test_scheduler_preserves_every_selection(self):
        for chosen in selections():
            workers = schedule_tests(chosen, CATALOG)
            flat = [n for worker in workers for n in worker]
            self.assertEqual(len(flat), len(set(flat)))
            self.assertEqual(set(flat), chosen)
            self.assertTrue(1 <= len(workers) <= 8)
            self.assertTrue(all(workers))

    def test_empty_selection_has_no_workers(self):
        self.assertEqual(schedule_tests(set(), CATALOG), [])

    def test_selector_output_matches_selection_for_every_commit(self):
        picks = list(selections(seed=7, count=10)) + [set()]
        with TemporaryDirectory() as directory:
            data = Path(directory)
            (data / "commits").mkdir()
            (data / "tests.json").write_text(json.dumps({"tests": CATALOG}), encoding="utf-8")
            expected = {}
            for index, chosen in enumerate(picks):
                (data / f"commits/c{index:02d}.patch").write_text("x\n", encoding="utf-8")
                expected[f"c{index:02d}"] = chosen
            answers = iter(picks)

            def fake(patch, tests, graph, coverage, mode):
                return Selection(next(answers), False, []), []

            stdout = io.StringIO()
            with mock.patch.object(selector, "load_analysis_maps", return_value=(None, None)), \
                    mock.patch.object(selector, "analyze_selective", side_effect=fake), \
                    contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(io.StringIO()):
                selector.run(data, data, "balanced")
        summary = check(stdout.getvalue().encode("utf-8"), expected, CATALOG)
        self.assertEqual(summary["violations"], [])
        self.assertLessEqual(summary["max_workers"], 8)
        self.assertEqual(summary["empty_selections"], 1)


if __name__ == "__main__":
    unittest.main()
