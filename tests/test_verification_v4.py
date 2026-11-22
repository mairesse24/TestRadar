from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from tools.selection_eval.verification_v4 import apply_patch, packing_cost, plan, xml_outcome


class VerificationV4Tests(unittest.TestCase):
    def test_worker_charge_can_make_a_cheap_singleton_eligible(self):
        tests = [{"number": 1, "seconds": 0.1, "id": "tests/test_x.py::test_x"}]
        rows = [{"commit": "abc", "workers": [[1]]}]
        self.assertEqual(plan(rows, tests), [(2100, "abc", "tests/test_x.py::test_x")])
        self.assertEqual(rows, [{"commit": "abc", "workers": [[1]]}])

    def test_does_not_choose_expensive_or_uneconomic_tests(self):
        tests = [
            {"number": 1, "seconds": 20, "id": "slow"},
            {"number": 2, "seconds": 0.1, "id": "cheap"},
        ]
        rows = [{"commit": "abc", "workers": [[1, 2]]}]
        self.assertEqual(plan(rows, tests), [])
        self.assertEqual(packing_cost([[1], [], [2]], {1: 20_000, 2: 100}), 24_000)

    def test_missing_multiple_or_skipped_reports_are_inconclusive(self):
        with TemporaryDirectory() as directory:
            report = Path(directory) / "report.xml"
            self.assertEqual(xml_outcome(report, 0), "inconclusive")
            for body in ("<testcase/><testcase/>", "<testcase><skipped/></testcase>"):
                report.write_text(f"<testsuite>{body}</testsuite>", encoding="utf-8")
                self.assertEqual(xml_outcome(report, 0), "inconclusive")
            report.write_text("<testsuite><testcase/></testsuite>", encoding="utf-8")
            self.assertEqual(xml_outcome(report, 0), "observed-pass")
            self.assertEqual(xml_outcome(report, 1), "pytest-nonzero")

    def test_rejects_identifier_that_could_escape_input_directory(self):
        with self.assertRaises(ValueError):
            plan([{"commit": "../other", "workers": []}], [])

    def test_crlf_transport_is_reported(self):
        patch = b"diff --git a/x b/x\n--- a/x\n+++ b/x\n@@ -1 +1 @@\n-old\n+new\n"
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "x").write_bytes(b"old\n")
            bundle = root / "change.patch"
            bundle.write_bytes(patch.replace(b"\n", b"\r\n"))
            self.assertEqual(apply_patch(root, bundle), "normalized-crlf")
            self.assertEqual((root / "x").read_bytes(), b"new\n")
