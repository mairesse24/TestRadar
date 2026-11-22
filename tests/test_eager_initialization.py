import difflib
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from analyzer import eager_initialization
from analyzer.patch_parser import parse_patch


class EagerInitializationTests(unittest.TestCase):
    def reasons(self, before, after, others=None, path="sqlglot/example.py"):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            for name, source in {"sqlglot/__init__.py": "", path: before, **(others or {})}.items():
                target = root / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(source, encoding="utf-8")
            diff = "".join(difflib.unified_diff(
                before.splitlines(keepends=True), after.splitlines(keepends=True),
                f"a/{path}", f"b/{path}",
            ))
            changes = parse_patch(f"diff --git a/{path} b/{path}\n{diff}")
            eager_initialization._pinned_index.cache_clear()
            eager_initialization._pinned_reachable.cache_clear()
            return eager_initialization.reasons(root, changes)

    def test_class_registry_factory_and_transitive_annotated_alias(self):
        src = ("def helper():\n    return 1\n\n"
               "factory: object = helper\n\nclass Registry:\n    table = {'key': factory()}\n")
        self.assertIn("example.helper", self.reasons(src, src.replace("return 1", "raise RuntimeError()"))[0])

    def test_relative_imported_factory(self):
        src = "def helper():\n    return 1\n"
        elsewhere = {"sqlglot/consumer.py": "from .example import helper as build\nvalue = build()\n"}
        self.assertIn("example.helper", self.reasons(src, src.replace("return 1", "return 2"), elsewhere)[0])

    def test_constructor_and_explicit_metaclass(self):
        for src, method in (
            ("class Base:\n    def __init__(self):\n        self.value = 1\n"
             "class Child(Base):\n    pass\nvalue = Child()\n", "Base.__init__"),
            ("class Meta(type):\n    def __init__(cls, name, bases, members):\n        cls.value = 1\n"
             "class Child(metaclass=Meta):\n    pass\n", "Meta.__init__"),
        ):
            with self.subTest(method=method):
                self.assertIn(method, self.reasons(src, src.replace("value = 1", "value = 2"))[0])

    def test_new_eager_call_is_visible_in_patched_structure(self):
        src = "def helper():\n    return 1\n\nvalue = 1\n"
        new = src.replace("return 1", "return 2").replace("value = 1", "value = helper()")
        self.assertIn("example.helper", self.reasons(src, new)[0])

    def test_runtime_function_and_unrelated_formatting_do_not_trigger(self):
        src = "def helper():\n    return 1\n\nvalue = 1\n"
        self.assertEqual(self.reasons(src, src.replace("return 1", "return 2")), [])
        self.assertEqual(self.reasons(src, src.replace("return 1", "# comment\n    return 1")), [])
