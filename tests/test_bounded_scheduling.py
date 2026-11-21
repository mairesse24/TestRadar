import unittest
from unittest.mock import patch

from analyzer.scheduler import clear_schedule_cache, schedule_tests


class BoundedSchedulingTests(unittest.TestCase):
    def test_exact_repair_improves_when_greedy_moves_are_stuck(self):
        durations = [4570, 3308, 2263, 2257, 1594, 1506, 1259, 750, 596, 438, 395]
        tests = [{"number": number, "seconds": value / 1000}
                 for number, value in enumerate(durations)]
        selected = set(range(len(tests)))

        def cost(workers):
            return max(sum(durations[number] for number in worker) for worker in workers) + 2000 * len(workers)

        clear_schedule_cache()
        with patch("analyzer.scheduler._EXACT_NODE_LIMIT", 0):
            baseline = schedule_tests(selected, tests)
        clear_schedule_cache()
        result = schedule_tests(selected, tests)
        self.assertEqual(cost(baseline), 12421)
        self.assertEqual(cost(result), 12348)
        self.assertEqual(sorted(number for worker in result for number in worker), sorted(selected))
        self.assertLessEqual(len(result), 8)
        self.assertTrue(all(result))
        self.assertEqual(schedule_tests(selected, list(reversed(tests))), result)
