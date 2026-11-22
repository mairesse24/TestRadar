from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from tools.selection_eval.statement_shadow import classify, index_statements, predict


class StatementShadowTests(unittest.TestCase):
    def test_compound_condition_aligned_to_own_statement(self):
        old = "def f(x):\n    if x > 3:\n        return 1\n    return 0\n"
        result = classify(old, old.replace("> 3", ">= 3"))
        self.assertEqual(result["status"], "localized-body")
        self.assertEqual(result["statements"][0]["old"][:2], (2, 4))
        self.assertEqual(len(result["statements"]), 1)

    def test_class_attribute_defaults_and_decorator_remain_uncertain(self):
        for old, new, status in (
            ("class C:\n    limit = 1\n", "class C:\n    limit = 2\n", "structural-change"),
            ("def f(x=1):\n    return x\n", "def f(x=2):\n    return x\n", "structural-change"),
            ("@deco\ndef f():\n    return 1\n", "@deco\ndef f():\n    return 2\n",
             "decorated-or-special-body"),
        ):
            with self.subTest(status=status):
                self.assertEqual(classify(old, new)["status"], status)

    def test_dynamic_or_shared_state_is_not_a_local_candidate(self):
        for old, new in (
            ("def f(x):\n    return x\n", "def f(x):\n    return getattr(x, 'value')\n"),
            ("def f(obj):\n    obj.cache = 1\n", "def f(obj):\n    obj.cache = 2\n"),
        ):
            self.assertEqual(classify(old, new)["status"], "dynamic-or-shared-effect")

    def test_trace_hit_cannot_authorize_removal(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "sqlglot/example.py"
            source.parent.mkdir()
            source.write_text("def f():\n    return 1\n", encoding="utf-8")
            index = index_statements(root, [("test-a", "sqlglot/example.py", 2)])
            change = {"path": "sqlglot/example.py", "status": "modified", "uncertain": False,
                      "old_lines": [2], "new_lines": [2], "removed_text": ["    return 1"],
                      "added_text": ["    return 2"]}
            result = predict(root, [change], index)
            self.assertEqual(result["selection"], "retain-baseline")
            self.assertEqual(result["changes"][0]["positive_test_ids"], ["test-a"])
            self.assertEqual(predict(root, [change], {})["selection"], "retain-baseline")
