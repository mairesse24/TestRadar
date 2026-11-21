"""The probe filter is opt-in and does not decide from answer files."""

import ast
import unittest
from pathlib import Path

from analyzer.edit_scope import EditScope
from tools.fault_detection.probes import (
    GENERATOR_MEMORY_ID,
    MAX_NODES_ID,
    apply_filter,
    generator_construction_may_change,
    mentions_node_limit,
    probe_ids_to_drop,
)


def _change(path, added=(), removed=()):
    return {
        "path": path,
        "old_path": path,
        "new_path": path,
        "status": "modified",
        "added_text": list(added),
        "removed_text": list(removed),
        "uncertain": False,
    }


class ProbeRuleTests(unittest.TestCase):
    def test_node_limit_text_keeps_the_parser_probe(self):
        change = _change("sqlglot/parser.py", removed=["            self._node_count += 1"])
        self.assertTrue(mentions_node_limit([change]))
        self.assertNotIn(MAX_NODES_ID, probe_ids_to_drop([change], root=None))

    def test_unrelated_operator_edit_drops_the_parser_probe(self):
        change = _change(
            "sqlglot/dialects/dialect.py",
            added=["            if self.normalization_strategy == NormalizationStrategy.UPPERCASE"],
            removed=["            if self.normalization_strategy is NormalizationStrategy.UPPERCASE"],
        )
        self.assertIn(MAX_NODES_ID, probe_ids_to_drop([change], root=None))

    def test_message_line_that_names_max_nodes_is_kept(self):
        change = _change(
            "sqlglot/parser.py",
            added=['self.raise_error(f"Node budget ({self.max_nodes}) exceeded")'],
            removed=['self.raise_error(f"Maximum number of AST nodes ({self.max_nodes}) exceeded")'],
        )
        self.assertNotIn(MAX_NODES_ID, probe_ids_to_drop([change], root=None))

    def test_missing_pinned_tree_keeps_generator_memory(self):
        change = _change("sqlglot/generators/clickhouse.py", added=["        return False"])
        self.assertTrue(generator_construction_may_change([change], None))
        self.assertNotIn(GENERATOR_MEMORY_ID, probe_ids_to_drop([change], root=None))

    def test_construction_safe_body_drops_generator_memory(self):
        change = _change("sqlglot/generators/clickhouse.py", added=["        return False"])
        scope = EditScope("bodies", {"ClickHouse.func_sql"}, construction_safe=True, detail="bodies")

        def classify(change, root, *, analyze_bodies):
            return scope

        from analyzer import edit_scope
        original = edit_scope.classify
        edit_scope.classify = classify
        try:
            dropped = probe_ids_to_drop([change], root=Path("/unused"))
        finally:
            edit_scope.classify = original
        self.assertIn(GENERATOR_MEMORY_ID, dropped)
        self.assertIn(MAX_NODES_ID, dropped)

    def test_init_edit_keeps_generator_memory(self):
        change = _change("sqlglot/generator.py", added=["        leaked.append(tuple(range(40)))"])
        scope = EditScope("bodies", {"Generator.__init__"}, construction_safe=False, detail="init")

        from analyzer import edit_scope
        original = edit_scope.classify
        edit_scope.classify = lambda change, root, *, analyze_bodies: scope
        try:
            dropped = probe_ids_to_drop([change], root=Path("/unused"))
        finally:
            edit_scope.classify = original
        self.assertNotIn(GENERATOR_MEMORY_ID, dropped)

    def test_uncertain_patch_drops_nothing(self):
        change = _change("sqlglot/parser.py", removed=["x"])
        change["uncertain"] = True
        self.assertEqual(probe_ids_to_drop([change], root=None), set())

    def test_filter_reschedules_without_reading_answers(self):
        tests = [
            {"number": 1, "id": MAX_NODES_ID, "file": "tests/test_parser.py", "seconds": 28.817},
            {"number": 2, "id": "tests/test_parser.py::TestParser::test_small", "file": "tests/test_parser.py", "seconds": 0.01},
            {"number": 3, "id": GENERATOR_MEMORY_ID, "file": "tests/test_generator.py", "seconds": 6.686},
        ]
        selection = [{"commit": "zz", "workers": [[1, 2], [3]]}]
        # No patch on disk: the line is unchanged.
        rewritten = apply_filter(selection, tests, Path("/no/such/commits"), None)
        self.assertEqual(rewritten, selection)

    def test_module_has_no_answer_table(self):
        source = Path("tools/fault_detection/probes.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        joined = ast.dump(tree)
        self.assertNotIn("answers.json", joined)
        self.assertNotIn("sample/answers", source)


if __name__ == "__main__":
    unittest.main()
