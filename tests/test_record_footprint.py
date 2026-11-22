import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from tools.selection_eval.record_footprint import TARGET, summarize


class FootprintTests(unittest.TestCase):
    def write(self, path, rows):
        path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")

    def test_finished_parent_and_child_are_positive_evidence_only(self):
        with TemporaryDirectory() as name:
            root = Path(name)
            self.write(root / "1.jsonl", [
                ["start", 0], ["function", ["sqlglot/parser.py", "<module>", 1]],
                ["process", "subprocess.Popen"], ["finish", None],
            ])
            self.write(root / "2.jsonl", [
                ["start", 1], ["function", ["sqlglot/parser.py", "Parser.parse", 42]], ["finish", None],
            ])
            self.write(root / "protocol.jsonl", [[TARGET, p, "passed"] for p in ("setup", "call", "teardown")])
            result = summarize(root, TARGET, 0)
            self.assertTrue(result["protocol_passed"])
            self.assertEqual(result["process_count"], 2)
            self.assertEqual(result["process_events"], {"subprocess.Popen": 1})
            self.assertEqual(len(result["functions"]), 2)
            self.assertFalse(result["eligible_for_exclusion"])

    def test_unfinished_or_missing_protocol_retains_test(self):
        with TemporaryDirectory() as name:
            root = Path(name)
            self.write(root / "1.jsonl", [["start", 0]])
            result = summarize(root, TARGET, "timeout")
            self.assertFalse(result["protocol_passed"])
            self.assertEqual(result["unfinished"], ["1"])
            self.assertFalse(result["eligible_for_exclusion"])
