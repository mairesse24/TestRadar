import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from analyzer import edit_scope
from analyzer.patch_parser import parse_patch
import selector

DIALECT = '''from sqlglot import generator


def _factory(name):
    return lambda self, e: name


def _helper(self, e):
    return "x"


class FooGenerator(generator.Generator):
    TRANSFORMS = {"a": _helper, "b": _factory("b")}

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

    @property
    def flag(self):
        return 1

    def count_sql(self, e):
        # count
        return "COUNT"
'''


def _diff(path, old, new):
    import difflib
    lines = difflib.unified_diff(
        old.splitlines(keepends=True), new.splitlines(keepends=True),
        f"a/{path}", f"b/{path}", n=3,
    )
    return f"diff --git a/{path} b/{path}\n" + "".join(lines)


class EditScopeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / "sqlglot" / "generators").mkdir(parents=True)
        (self.root / "sqlglot" / "__init__.py").write_text("", encoding="utf-8")
        (self.root / "sqlglot" / "generator.py").write_text(
            "class Generator:\n    def __init__(self, **kw):\n        self.setup()\n\n"
            "    def setup(self):\n        pass\n", encoding="utf-8")
        self.path = "sqlglot/generators/foo.py"
        (self.root / self.path).write_text(DIALECT, encoding="utf-8")
        edit_scope._CONSTRUCTION_CACHE.clear()

    def classify(self, new):
        changes = parse_patch(_diff(self.path, DIALECT, new))
        self.assertEqual(len(changes), 1)
        rebuilt = edit_scope.reconstruct(DIALECT, changes[0])
        self.assertEqual(rebuilt, new)
        return edit_scope.classify(changes[0], self.root)

    def test_comment_and_formatting_only_is_equivalent(self):
        new = DIALECT.replace("        # count\n", "        # counts rows\n").replace(
            '"a": _helper, "b": _factory("b")', '\n        "a": _helper,\n        "b": _factory("b"),\n    ')
        self.assertEqual(self.classify(new).kind, "equivalent")

    def test_docstring_or_literal_change_is_not_equivalent(self):
        scope = self.classify(DIALECT.replace('return "COUNT"', 'return "COUNT "'))
        self.assertEqual(scope.kind, "bodies")
        self.assertTrue(scope.construction_safe)

    def test_class_level_change_is_unclassified(self):
        self.assertIsNone(self.classify(DIALECT.replace('"a": _helper', '"a": _factory("a")')))

    def test_import_time_and_constructor_bodies_are_not_construction_safe(self):
        for old, new in (
            ("return lambda self, e: name", "return lambda self, e: name.upper()"),
            ("super().__init__(**kwargs)", "super().__init__(**kwargs)\n        self.x = 1"),
            ("return 1", "return 2"),
            ('return "x"\n\n\nclass', 'return "y"\n\n\nclass'),
        ):
            with self.subTest(new=new):
                scope = self.classify(DIALECT.replace(old, new))
                self.assertEqual(scope.kind, "bodies")
                if "x\"" in old:  # _helper is only referenced in a dict, not called
                    self.assertTrue(scope.construction_safe)
                else:
                    self.assertFalse(scope.construction_safe, scope.detail)

    def test_names_reached_from_core_constructors_are_not_safe(self):
        text = DIALECT.replace(
            "    def count_sql(self, e):",
            "    def setup(self):\n        return None\n\n    def count_sql(self, e):")
        (self.root / self.path).write_text(text, encoding="utf-8")
        changes = parse_patch(_diff(self.path, text, text.replace("return None", "return 1")))
        scope = edit_scope.classify(changes[0], self.root)
        self.assertFalse(scope.construction_safe)

    def test_mismatched_patch_is_unclassified(self):
        changes = parse_patch(_diff(self.path, DIALECT.replace("COUNT", "CNT"), DIALECT))
        self.assertIsNone(edit_scope.classify(changes[0], self.root))

    def test_missing_pinned_source_is_unclassified(self):
        change = parse_patch(_diff(self.path, DIALECT, DIALECT + "\n# end\n"))[0]
        with mock.patch.dict(os.environ, {"TESTRADAR_PINNED": str(self.root / "absent")}):
            with mock.patch.object(edit_scope, "pinned_root", return_value=None):
                self.assertIsNone(edit_scope.classify(change))

    def test_encoding_cookie_or_bom_is_unclassified(self):
        for header in ("# -*- coding: latin-1 -*-\n", "# coding: ascii\n", "# coding: no-such-codec\n",
                       "\ufeff"):
            with self.subTest(header=header):
                changes = parse_patch(_diff(self.path, DIALECT, header + DIALECT))
                self.assertIsNone(edit_scope.classify(changes[0], self.root))

    def test_utf8_cookie_is_still_equivalent(self):
        self.assertEqual(self.classify("# -*- coding: utf-8 -*-\n" + DIALECT).kind, "equivalent")

    def test_body_analysis_can_be_skipped(self):
        changes = parse_patch(_diff(self.path, DIALECT, DIALECT.replace('return "COUNT"', 'return "C"')))
        scope = edit_scope.classify(changes[0], self.root, analyze_bodies=False)
        self.assertEqual(scope.kind, "bodies")
        self.assertFalse(scope.construction_safe)

    def test_selector_runs_nothing_only_for_single_equivalent_changes(self):
        tests_json = [{"number": 0, "id": "tests/test_a.py::T::test_a", "file": "tests/test_a.py", "seconds": 1.0}]
        comment = _diff(self.path, DIALECT, DIALECT.replace("        # count\n", "        # rows\n"))
        patch = self.root / "c.patch"
        env = {selector.EXPERIMENTS_ENV: "equivalent", "TESTRADAR_PINNED": str(self.root)}
        with mock.patch.dict(os.environ, env):
            patch.write_text(comment, encoding="utf-8")
            selection, _ = selector.analyze_selective(patch, tests_json, None, None, "balanced")
            self.assertEqual(selection.numbers, set())
            self.assertFalse(selection.fell_back)
            # The same path twice (second copy not a comment edit) must not be shortcut.
            other = _diff(self.path, DIALECT, DIALECT.replace('return "COUNT"', 'return "C"'))
            patch.write_text(comment + other, encoding="utf-8")
            selection, _ = selector.analyze_selective(patch, tests_json, None, None, "balanced")
            self.assertNotEqual(selection.numbers, set())


class ExperimentsFlagTest(unittest.TestCase):
    def test_flags_default_off_and_ignore_unknown(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop(selector.EXPERIMENTS_ENV, None)
            self.assertEqual(selector.enabled_experiments(), frozenset())
        with mock.patch.dict(os.environ, {selector.EXPERIMENTS_ENV: "equivalent, bogus"}):
            self.assertEqual(selector.enabled_experiments(), frozenset({"equivalent"}))

    def test_construction_only_ids_are_all_dialect_tests(self):
        from analyzer import test_selector
        self.assertLessEqual(test_selector.CONSTRUCTION_ONLY_TEST_IDS, set(test_selector.ALL_DIALECT_TEST_IDS))


if __name__ == "__main__":
    unittest.main()


class SeedFilterTest(unittest.TestCase):
    """Region-level filtering of formatting-only regions (``seeds``)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / "sqlglot" / "generators").mkdir(parents=True)
        (self.root / "sqlglot" / "__init__.py").write_text("", encoding="utf-8")
        self.path = "sqlglot/generators/foo.py"
        (self.root / self.path).write_text(DIALECT, encoding="utf-8")

    def semantic(self, new):
        changes = parse_patch(_diff(self.path, DIALECT, new))
        self.assertEqual(len(changes), 1)
        return changes[0], edit_scope.semantic_change(changes[0], self.root)

    REFLOW = DIALECT.replace("from sqlglot import generator", "from sqlglot import (\n    generator,\n)")
    SEMANTIC = ('return "COUNT"', 'return "C"')

    def test_import_reflow_is_dropped_semantic_kept(self):
        change, result = self.semantic(self.REFLOW.replace(*self.SEMANTIC))
        self.assertIsNotNone(result)
        sub, dropped = result
        self.assertEqual(dropped, 1)
        self.assertEqual(sub["added_text"], ['        return "C"'])
        self.assertEqual(sub["removed_text"], ['        return "COUNT"'])
        graph = mock.Mock(knows=lambda path: True)
        self.assertEqual(selector.hard_signals([sub], graph, object()), [])
        self.assertTrue(any("import" in r for r in selector.hard_signals([change], graph, object())))

    def test_real_import_is_kept(self):
        new = self.REFLOW.replace(*self.SEMANTIC).replace("from sqlglot import (", "import os\nfrom sqlglot import (")
        _, result = self.semantic(new)
        if result is not None:
            self.assertIn("import os", result[0]["added_text"])

    def test_adjacent_comment_stays_with_semantic_edit(self):
        _, result = self.semantic(DIALECT.replace('        return "COUNT"', '        # changed\n        return "C"'))
        self.assertIsNone(result)

    def test_single_region_and_equivalent_files_are_untouched(self):
        self.assertIsNone(self.semantic(DIALECT.replace(*self.SEMANTIC))[1])
        self.assertIsNone(self.semantic(self.REFLOW)[1])

    def test_encoding_cookie_or_bom_refuses(self):
        for prefix in ("# -*- coding: latin-1 -*-\n", "\ufeff"):
            self.assertIsNone(self.semantic(prefix + self.REFLOW.replace(*self.SEMANTIC))[1], prefix)

    def test_utf8_cookie_region_is_formatting(self):
        _, result = self.semantic("# -*- coding: utf-8 -*-\n" + self.REFLOW.replace(*self.SEMANTIC))
        self.assertIsNotNone(result)
        self.assertEqual(result[0]["added_text"], ['        return "C"'])

    def test_split_parenthesis_regions_are_not_dropped_alone(self):
        # Two regions that are only equivalent together: each alone breaks
        # the syntax, so neither is dropped.
        new = DIALECT.replace('    TRANSFORMS = {"a": _helper, "b": _factory("b")}',
                              '    TRANSFORMS = {"a": _helper,\n\n\n\n\n "b": _factory("b")}')
        new = new.replace(*self.SEMANTIC)
        _, result = self.semantic(new)
        if result is not None:
            rebuilt = edit_scope.reconstruct(DIALECT, result[0])
            self.assertIsNotNone(rebuilt)

    def test_selector_uses_semantic_regions_only(self):
        tests_json = [{"number": 0, "id": "tests/test_a.py::T::test_a", "file": "tests/test_a.py", "seconds": 1.0}]
        patch = self.root / "c.patch"
        patch.write_text(_diff(self.path, DIALECT, self.REFLOW.replace(*self.SEMANTIC)), encoding="utf-8")
        for experiments, expect_import in (("equivalent", True), ("equivalent,seeds", False)):
            env = {selector.EXPERIMENTS_ENV: experiments, "TESTRADAR_PINNED": str(self.root)}
            with mock.patch.dict(os.environ, env):
                _, reasons = selector.analyze_selective(patch, tests_json, None, None, "balanced")
                self.assertEqual(any("import statement" in r for r in reasons), expect_import, experiments)
