"""Counterexamples to inferring callback timing from an eager AST alone."""

import ast
from difflib import unified_diff
from pathlib import Path
from tempfile import TemporaryDirectory
from textwrap import dedent
import unittest

from analyzer.edit_scope import classify
from analyzer.patch_parser import parse_patch


PATH = "sqlglot/factory_example.py"


def eager_projection(source):
    tree = ast.parse(source)
    factory = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                   and node.name == "factory")
    callback = next(node for node in factory.body if isinstance(node, ast.FunctionDef)
                    and node.name == "callback")
    callback.body = [ast.Pass()]
    return ast.dump(tree)


def run_source(source):
    namespace = {}
    exec(compile(source, "<synthetic callback fixture>", "exec"), namespace)
    return namespace


class DeferredInitializationAudit(unittest.TestCase):
    def check_body_only_edit(self, source, replacement="return 1702"):
        old = dedent(source).lstrip()
        new = old.replace("return 1701", replacement)
        self.assertNotEqual(old, new)
        self.assertEqual(eager_projection(old), eager_projection(new))
        patch = f"diff --git a/{PATH} b/{PATH}\n" + "".join(unified_diff(
            old.splitlines(keepends=True), new.splitlines(keepends=True), f"a/{PATH}", f"b/{PATH}",
        ))
        change, = parse_patch(patch)
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / PATH).parent.mkdir()
            (root / PATH).write_text(old, encoding="utf-8")
            scope = classify(change, root, analyze_initialization=True)
        self.assertEqual(scope.initialization_functions, {"factory"})
        return run_source(old), run_source(new)

    def test_eager_direct_invocation(self):
        old, new = self.check_body_only_edit("""
            def factory():
                def callback():
                    return 1701
                return callback()
            observed = factory()
            """)
        self.assertNotEqual(old["observed"], new["observed"])

    def test_unchanged_decorator_can_invoke_body(self):
        old, new = self.check_body_only_edit("""
            def decorate(fn):
                return fn()
            def factory():
                @decorate
                def callback():
                    return 1701
                return callback
            observed = factory()
            """)
        self.assertNotEqual(old["observed"], new["observed"])

    def test_unknown_callee_can_invoke_returned_callback(self):
        old, new = self.check_body_only_edit("""
            def factory():
                def callback():
                    return 1701
                return callback
            def register(fn):
                return fn()
            observed = register(factory())
            """)
        self.assertNotEqual(old["observed"], new["observed"])

    def test_registry_metaclass_can_invoke_callback(self):
        old, new = self.check_body_only_edit("""
            def factory():
                def callback():
                    return 1701
                return callback
            class Meta(type):
                def __new__(cls, name, bases, namespace):
                    namespace["observed"] = namespace["registry"]["entry"]()
                    return super().__new__(cls, name, bases, namespace)
            class Consumer(metaclass=Meta):
                registry = {"entry": factory()}
            observed = Consumer.observed
            """)
        self.assertNotEqual(old["observed"], new["observed"])

    def test_stored_callback_can_be_inspected_by_an_importer(self):
        old, new = self.check_body_only_edit("""
            def factory():
                def callback():
                    return 1701
                return callback
            registry = {"entry": factory()}
            """)
        old_callback = old["registry"]["entry"]
        new_callback = new["registry"]["entry"]
        self.assertNotEqual(old_callback.__code__.co_consts, new_callback.__code__.co_consts)
        self.assertEqual(old_callback(), 1701)
        self.assertEqual(new_callback(), 1702)

    def test_body_only_edit_can_change_closure_bindings(self):
        old, new = self.check_body_only_edit("""
            def factory():
                captured = 1702
                def callback():
                    return 1701
                return callback
            registry = {"entry": factory()}
            """, replacement="return captured")
        self.assertEqual(old["registry"]["entry"].__code__.co_freevars, ())
        self.assertEqual(new["registry"]["entry"].__code__.co_freevars, ("captured",))
