from concurrent.futures import ProcessPoolExecutor
import json
from multiprocessing import get_context
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from analyzer.block_map import BlockMap
from analyzer.test_selector import Selection
from selector import narrow_blocks
from tools.child_trace_worker import run_task


def run_fixture(root):
    path = Path(root) / "sqlglot" / "demo.py"
    namespace = {}
    exec(compile("def entry():\n    return 7\nentry()\n", str(path), "exec"), namespace)
    return 7


class ChildTraceTests(unittest.TestCase):
    def test_reused_spawn_worker_attributes_each_job_to_its_own_record(self):
        with TemporaryDirectory() as directory, ProcessPoolExecutor(
            max_workers=1, mp_context=get_context("spawn"),
        ) as pool:
            destinations = [Path(directory) / str(index) for index in range(2)]
            for destination in destinations:
                self.assertEqual(pool.submit(
                    run_task, directory, str(destination), run_fixture, (directory,), {},
                ).result(timeout=20), 7)
            for destination in destinations:
                self.assertIn("sqlglot/demo.py:entry@1", json.loads(destination.read_bytes()))

    def test_uncertain_test_survives_near_total_guard_in_blocks_only(self):
        blocks = BlockMap({}, frozenset({0, 1}), frozenset(), {}, frozenset({1}))
        tests = [
            {"number": 0, "seconds": 1, "file": "tests/test_parent.py"},
            {"number": 1, "seconds": 1, "file": "tests/test_child.py"},
        ]
        change = [{"path": "sqlglot/demo.py", "status": "modified"}]
        base = Selection({0}, False, [])
        self.assertEqual(narrow_blocks(base, change, tests, blocks).numbers, {0, 1})
        self.assertEqual(narrow_blocks(base, [{"path": "README.md"}], tests, blocks), base)
