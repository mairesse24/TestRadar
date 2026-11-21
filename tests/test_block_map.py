"""Unit tests for function-entry narrowing. No sample answers and no SQLGlot tree."""

import textwrap
import unittest

from analyzer.block_map import BlockMap, index_functions


SOURCE = textwrap.dedent(
    '''
    class Dialect:
        WEEK = 1

        def case_sensitive(self, text):
            return text.isupper()

        def nested(self):
            def inner():
                return 1
            return inner

        @classmethod
        def get_or_raise(cls):
            return cls
    '''
).lstrip("\n")


def change(old, new, line, path="sqlglot/dialects/dialect.py"):
    return {
        "path": path,
        "old_path": path,
        "new_path": path,
        "status": "modified",
        "old_lines": [line],
        "new_lines": [line],
        "added_text": [new],
        "removed_text": [old],
        "uncertain": False,
    }


class IndexTests(unittest.TestCase):
    def test_qualnames_match_code_objects(self):
        rows = {row[0]: row for row in index_functions(SOURCE)}
        self.assertEqual(rows["Dialect.case_sensitive"][1], SOURCE.splitlines().index("    def case_sensitive(self, text):") + 1)
        self.assertIn("Dialect.nested.<locals>.inner", rows)
        body = rows["Dialect.case_sensitive"]
        self.assertGreater(body[2], body[1])
        self.assertGreaterEqual(body[3], body[2])
        decorated = rows["Dialect.get_or_raise"]
        self.assertLess(decorated[1], decorated[2])


class NarrowTests(unittest.TestCase):
    def setUp(self):
        rows = index_functions(SOURCE)
        key = "sqlglot/dialects/dialect.py:Dialect.case_sensitive@4"
        # The def line depends on the fixture text; derive it from the index.
        case = next(row for row in rows if row[0] == "Dialect.case_sensitive")
        self.line = case[2]
        key = f"sqlglot/dialects/dialect.py:Dialect.case_sensitive@{case[1]}"
        self.map = BlockMap(
            functions={key: frozenset({1, 3})},
            observed=frozenset({0, 1, 2, 3}),
            opaque_files=frozenset({"tests/test_optimizer.py"}),
            ranges={"sqlglot/dialects/dialect.py": rows},
        )
        self.tests = [
            {"number": 0, "id": "tests/test_parser.py::t", "file": "tests/test_parser.py", "seconds": 1},
            {"number": 1, "id": "tests/test_serde.py::t", "file": "tests/test_serde.py", "seconds": 1},
            {"number": 2, "id": "tests/test_optimizer.py::t", "file": "tests/test_optimizer.py", "seconds": 5},
            {"number": 3, "id": "tests/dialects/test_dialect.py::t", "file": "tests/dialects/test_dialect.py", "seconds": 1},
        ]

    def test_operator_change_keeps_entrants_and_opaque_balanced_tests(self):
        selected = self.map.narrow(
            {0, 1, 2, 3},
            [change("return text.isupper()", "return text.islower()", self.line)],
            self.tests,
        )
        self.assertEqual(selected, {1, 2, 3})

    def test_class_attribute_stays_with_balanced(self):
        week = next(index + 1 for index, line in enumerate(SOURCE.splitlines()) if "WEEK" in line)
        selected = self.map.narrow(
            {0, 1, 2, 3},
            [change("WEEK = 1", "WEEK = 2", week)],
            self.tests,
        )
        self.assertIsNone(selected)

    def test_comment_and_string_do_not_narrow(self):
        self.assertIsNone(self.map.narrow(
            {0, 1, 2, 3},
            [change("return text.isupper()", "return text.isupper()  # note", self.line)],
            self.tests,
        ))
        self.assertIsNone(self.map.narrow(
            {0, 1, 2, 3},
            [change('label = "a"', 'label = "b"', self.line)],
            self.tests,
        ))

    def test_missing_entries_do_not_narrow(self):
        empty = BlockMap(
            functions={},
            observed=self.map.observed,
            opaque_files=self.map.opaque_files,
            ranges=self.map.ranges,
        )
        self.assertIsNone(empty.narrow(
            {0, 1, 2, 3},
            [change("return text.isupper()", "return text.islower()", self.line)],
            self.tests,
        ))

    def test_unobserved_tests_stay_selected(self):
        partial = BlockMap(
            functions=self.map.functions,
            observed=frozenset({0, 1, 3}),
            opaque_files=frozenset(),
            ranges=self.map.ranges,
        )
        selected = partial.narrow(
            {0, 1, 2, 3},
            [change("return text.isupper()", "return text.islower()", self.line)],
            self.tests,
        )
        self.assertEqual(selected, {1, 2, 3})

    def test_fixture_hunk_blocks_narrowing(self):
        edit = change("return text.isupper()", "return text.islower()", self.line)
        fixture = {
            "path": "tests/fixtures/optimizer/simplify.sql",
            "old_path": "tests/fixtures/optimizer/simplify.sql",
            "new_path": "tests/fixtures/optimizer/simplify.sql",
            "status": "modified",
            "old_lines": [1],
            "new_lines": [1],
            "added_text": ["SELECT 2"],
            "removed_text": ["SELECT 1"],
            "uncertain": False,
        }
        self.assertIsNone(self.map.narrow({0, 1, 2, 3}, [edit, fixture], self.tests))

    def test_signature_line_does_not_narrow(self):
        def_line = next(row[1] for row in index_functions(SOURCE) if row[0] == "Dialect.case_sensitive")
        self.assertIsNone(self.map.narrow(
            {0, 1, 2, 3},
            [change("def case_sensitive(self, text):", "def case_sensitive(self, text, extra=1):", def_line)],
            self.tests,
        ))


if __name__ == "__main__":
    unittest.main()
