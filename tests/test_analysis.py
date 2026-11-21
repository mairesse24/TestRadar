import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from analyzer.coverage_map import CoverageMap, build
from analyzer.dependency_graph import DependencyGraph
from analyzer.patch_parser import parse_patch


MODIFICATION = """diff --git a/sqlglot/example.py b/sqlglot/example.py
--- a/sqlglot/example.py
+++ b/sqlglot/example.py
@@ -11,3 +11,4 @@
 start
-old
+new
+extra
 end
"""


class PatchTests(unittest.TestCase):
    def test_modified_lines_and_text(self):
        change, = parse_patch(MODIFICATION)
        self.assertEqual(change, {
            "path": "sqlglot/example.py",
            "old_path": "sqlglot/example.py",
            "new_path": "sqlglot/example.py",
            "status": "modified",
            "old_lines": [12],
            "new_lines": [12, 13],
            "added_text": ["new", "extra"],
            "removed_text": ["old"],
            "uncertain": False,
        })

    def test_multiple_files_and_hunks(self):
        text = MODIFICATION + "@@ -19 +20 @@\n-end\n+finish\n"
        text += MODIFICATION.replace("example", "other")
        first, second = parse_patch(text)
        self.assertEqual(first["old_lines"], [12, 19])
        self.assertEqual(first["new_lines"], [12, 13, 20])
        self.assertEqual(second["path"], "sqlglot/other.py")
        self.assertFalse(first["uncertain"] or second["uncertain"])

    def test_added_and_deleted(self):
        added = """diff --git a/new.py b/new.py
new file mode 100644
--- /dev/null
+++ b/new.py
@@ -0,0 +1 @@
+hello
"""
        deleted = """diff --git a/old.py b/old.py
deleted file mode 100644
--- a/old.py
+++ /dev/null
@@ -1 +0,0 @@
-bye
"""
        one, two = parse_patch(added + deleted)
        self.assertEqual((one["status"], one["old_path"], one["new_lines"]), ("added", None, [1]))
        self.assertEqual((two["status"], two["new_path"], two["old_lines"]), ("deleted", None, [1]))
        self.assertFalse(one["uncertain"] or two["uncertain"])

    def test_rename_and_copy_with_spaces(self):
        for operation, status in [("rename", "renamed"), ("copy", "copied")]:
            with self.subTest(operation=operation):
                change, = parse_patch(
                    "diff --git a/old file.py b/new file.py\n"
                    f"similarity index 100%\n{operation} from old file.py\n"
                    f"{operation} to new file.py\n"
                )
                self.assertEqual(change["status"], status)
                self.assertEqual(change["old_path"], "old file.py")
                self.assertEqual(change["path"], "new file.py")
                self.assertFalse(change["uncertain"])

    def test_quoted_git_paths_and_no_newline_marker(self):
        change, = parse_patch(
            'diff --git "a/caf\\303\\251.py" "b/caf\\303\\251.py"\n'
            '--- "a/caf\\303\\251.py"\n+++ "b/caf\\303\\251.py"\n'
            '@@ -1 +1 @@\n-a\n\\ No newline at end of file\n+b\n'
            '\\ No newline at end of file\n'
        )
        self.assertEqual(change["path"], "café.py")
        self.assertEqual(change["added_text"], ["b"])
        self.assertFalse(change["uncertain"])

    def test_binary(self):
        for body in ("GIT binary patch\nliteral 0\nHcmV?d00001\n",
                     "Binary files a/logo.bin and b/logo.bin differ\n"):
            change, = parse_patch("diff --git a/logo.bin b/logo.bin\n" + body)
            self.assertEqual(change["status"], "binary")
            self.assertEqual(change["path"], "logo.bin")
            self.assertTrue(change["uncertain"])

    def test_malformed_patches(self):
        for text in ("", "not a diff", "diff --cc file.py", MODIFICATION[:-5],
                     MODIFICATION.replace("@@ -11", "@@ -0"), MODIFICATION + "+extra\n",
                     MODIFICATION.replace("+++ b/", "not a header ")):
            with self.subTest(text=text):
                self.assertTrue(any(change["uncertain"] for change in parse_patch(text)))

    def test_unsafe_paths(self):
        for path in ("../../escape", "/absolute", "C:/drive", "foo\\bar", "dir/CON.txt"):
            with self.subTest(path=path):
                change, = parse_patch(MODIFICATION.replace("sqlglot/example.py", path))
                self.assertTrue(change["uncertain"])
                self.assertEqual(change["path"], "")

    def test_bundled_sample_patches(self):
        fixtures = sorted((Path(__file__).resolve().parents[1] / "sample" / "commits").glob("*.patch"))
        self.assertTrue(fixtures)
        for fixture in fixtures:
            with self.subTest(patch=fixture.name):
                text = fixture.read_text(encoding="utf-8")
                changes = parse_patch(text)
                self.assertEqual(len(changes), text.count("\ndiff --git ") + 1)
                self.assertTrue(all(change["path"] for change in changes))
                self.assertFalse(any(change["uncertain"] for change in changes))


class MapTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)

    def write(self, path, text):
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        return target

    def test_relative_transitive_circular_dependencies(self):
        files = {
            "sqlglot/__init__.py": "",
            "sqlglot/sub/__init__.py": "",
            "sqlglot/a.py": "from .sub import b\n",
            "sqlglot/sub/b.py": "from .. import a\n",
            "tests/__init__.py": "",
            "tests/test_a.py": "import sqlglot.a\n",
        }
        for path, source in files.items():
            self.write(path, source)
        graph = DependencyGraph._from_tree(self.root, list(files))
        self.assertEqual(
            graph.impacted_files({"sqlglot/sub/b.py"}),
            {"sqlglot/sub/b.py", "sqlglot/a.py", "tests/test_a.py"},
        )
        self.assertFalse(graph.is_uncertain({"sqlglot/sub/b.py"}))
        self.assertTrue(graph.is_uncertain({"sqlglot/unknown.py"}))
        self.assertEqual(graph.impacted_files({"sqlglot/unknown.py"}), {"sqlglot/unknown.py"})

    def test_import_uncertainty(self):
        self.write("sqlglot/__init__.py", "")
        for source in ("import sqlglot.missing", "__import__(name)", "invalid syntax !"):
            with self.subTest(source=source):
                self.write("sqlglot/a.py", source)
                graph = DependencyGraph._from_tree(self.root, ["sqlglot/__init__.py", "sqlglot/a.py"])
                self.assertTrue(graph.is_uncertain({"sqlglot/a.py"}))

    def test_deterministic_graph_roundtrip(self):
        paths = ["sqlglot/__init__.py", "sqlglot/a.py", "sqlglot/b.py"]
        for path in paths[:2]:
            self.write(path, "")
        self.write(paths[2], "from . import a")
        first, second = self.root / "one.json", self.root / "two.json"
        DependencyGraph._from_tree(self.root, paths).save(first)
        DependencyGraph._from_tree(self.root, list(reversed(paths))).save(second)
        self.assertEqual(first.read_bytes(), second.read_bytes())
        graph = DependencyGraph.load(str(first))
        self.assertEqual(graph.impacted_files({paths[1]}), set(paths[1:]))

    def test_missing_and_corrupt_artifacts(self):
        for cls in (DependencyGraph, CoverageMap):
            with self.subTest(cls=cls):
                with self.assertRaises(FileNotFoundError):
                    cls.load(str(self.root / "missing.json"))
                with self.assertRaises(ValueError):
                    cls.load(str(self.write("broken.json", "{invalid")))
                with self.assertRaises(ValueError):
                    cls.load(str(self.write("structure.json", '{"schema":1}')))

    def test_coverage_file_hits_and_uncertainty(self):
        node = "tests/test_example.py::test_example"
        other = "tests/test_example.py::test_other"
        first, second = self.root / "one.json", self.root / "two.json"
        coverage = CoverageMap({"sqlglot/example.py": {node}}, {other})
        coverage.save(first)
        loaded = CoverageMap.load(str(first))
        loaded.save(second)
        self.assertEqual(first.read_bytes(), second.read_bytes())
        self.assertEqual(loaded.missing_test_ids, {other})
        changes = parse_patch(MODIFICATION)
        self.assertEqual(loaded.tests_for_changes(changes), {node})
        self.assertTrue(loaded.is_uncertain(changes))
        unknown = parse_patch(MODIFICATION.replace("example", "different"))
        self.assertEqual(loaded.tests_for_changes(unknown), set())
        self.assertTrue(loaded.is_uncertain(unknown))

    def test_build_coverage_with_real_pytest_node_ids(self):
        try:
            import pytest
        except ImportError:
            self.skipTest("pytest is required only for coverage generation")
        self.write("sqlglot/__init__.py", "")
        self.write("sqlglot/example.py", "def answer():\n    return 42\n")
        self.write(
            "tests/test_example.py",
            "def test_hit():\n    from sqlglot.example import answer\n    assert answer() == 42\n",
        )
        hit = "tests/test_example.py::test_hit"
        missing = "tests/test_example.py::test_missing"
        manifest = self.write("tests.json", json.dumps({"tests": [{"id": hit}, {"id": missing}]}))
        with patch("analyzer.coverage_map.pinned_files", return_value=[
            "sqlglot/__init__.py", "sqlglot/example.py", "tests/test_example.py",
        ]), patch.dict("os.environ", {"PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1"}):
            coverage = build(self.root.resolve(), manifest.resolve())
        self.assertEqual(coverage.tests_for_changes(parse_patch(MODIFICATION)), {hit})
        self.assertEqual(coverage.missing_test_ids, {missing})
