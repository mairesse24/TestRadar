"""Opt-in offline filter for two long probe tests.

This does not change the selector. It reads a selection that the default
selector already produced and may remove two test ids, then packs the
remainder with the existing scheduler.

The two ids are:

* ``tests/test_parser.py::TestParser::test_max_nodes``
  Builds a 10_000 by 100 expression, checks the node-limit error text, and
  checks that a later unlimited parse still returns a Select.
* ``tests/test_generator.py::TestGenerator::test_generator_memory``
  Constructs every dialect's generator 1_000 times and bounds the memory delta.

``test_max_nodes`` is removed only when no added or removed line contains
``_node_count`` or ``max_nodes``. A patch that edits the counter, the
comparison, or the error text keeps the test.

``test_generator_memory`` is removed only when every changed ``sqlglot``
Python file is proven, from the pinned tree, to be either syntax-equivalent
or a function-body edit that construction analysis does not reach from
import or from ``Dialect.generator``. Any other change keeps the test.
Non-Python paths do not keep it by themselves.

Run::

    python3 tools/fault_detection/probes.py \\
        --tests sample/tests.json \\
        --commits sample/commits \\
        --pinned /path/to/sqlglot \\
        --selection selection.jsonl \\
        --output probes.jsonl
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from analyzer import edit_scope
from analyzer.patch_parser import parse_patch
from analyzer.scheduler import schedule_tests

MAX_NODES_ID = "tests/test_parser.py::TestParser::test_max_nodes"
GENERATOR_MEMORY_ID = "tests/test_generator.py::TestGenerator::test_generator_memory"
PROBE_IDS = (MAX_NODES_ID, GENERATOR_MEMORY_ID)


def mentions_node_limit(changes: list[dict]) -> bool:
    """True when the patch text names the node counter or its parameter."""
    for change in changes:
        if not isinstance(change, dict):
            return True
        lines = list(change.get("added_text") or ()) + list(change.get("removed_text") or ())
        for line in lines:
            if not isinstance(line, str):
                return True
            if "_node_count" in line or "max_nodes" in line:
                return True
    return False


def generator_construction_may_change(changes: list[dict], root: Path | None) -> bool:
    """True when a generator-construction leak cannot be ruled out.

    ``root`` is the pinned SQLGlot checkout. Without it, nothing is ruled out.
    """
    if root is None:
        return True
    for change in changes:
        if not isinstance(change, dict):
            return True
        path = change.get("path") or ""
        if not (isinstance(path, str) and path.startswith("sqlglot/") and path.endswith(".py")):
            continue
        scope = edit_scope.classify(change, root, analyze_bodies=True)
        if scope is None:
            return True
        if scope.kind == "equivalent":
            continue
        if scope.kind == "bodies" and scope.construction_safe:
            continue
        return True
    return False


def probe_ids_to_drop(changes: list[dict], root: Path | None) -> set[str]:
    """Probe ids that this patch cannot affect, under the assumptions above.

    An empty or uncertain patch drops nothing. The selector's own fallback
    stands.
    """
    if not changes or any(not isinstance(change, dict) or change.get("uncertain") for change in changes):
        return set()
    drop: set[str] = set()
    if not mentions_node_limit(changes):
        drop.add(MAX_NODES_ID)
    if not generator_construction_may_change(changes, root):
        drop.add(GENERATOR_MEMORY_ID)
    return drop


def apply_filter(
    selection: list[dict],
    tests: list[dict],
    commits: Path,
    root: Path | None,
) -> list[dict]:
    """Return new answer lines. Unknown commits and extra keys are preserved."""
    number_by_id = {}
    for test in tests:
        if isinstance(test, dict) and isinstance(test.get("id"), str) and isinstance(test.get("number"), int):
            number_by_id[test["id"]] = test["number"]
    probe_numbers = {number_by_id[item] for item in PROBE_IDS if item in number_by_id}
    rewritten = []
    for row in selection:
        commit = row.get("commit")
        patch = commits / f"{commit}.patch" if isinstance(commit, str) else None
        if patch is None or not patch.is_file():
            rewritten.append(row)
            continue
        try:
            changes = parse_patch(patch.read_text(encoding="utf-8"))
        except (OSError, UnicodeError):
            rewritten.append(row)
            continue
        drop_ids = probe_ids_to_drop(changes, root)
        drop_numbers = {number_by_id[item] for item in drop_ids if item in number_by_id}
        if not drop_numbers:
            rewritten.append(row)
            continue
        kept: set[int] = set()
        for worker in row.get("workers") or ():
            if not isinstance(worker, list):
                continue
            for number in worker:
                if isinstance(number, int) and not isinstance(number, bool) and number not in drop_numbers:
                    kept.add(number)
        # A probe that was not selected cannot be dropped. Numbers outside the
        # catalog are left to the scheduler, which ignores them.
        if probe_numbers.isdisjoint(drop_numbers) or not (drop_numbers & {
            number
            for worker in row.get("workers") or ()
            if isinstance(worker, list)
            for number in worker
            if isinstance(number, int)
        }):
            rewritten.append(row)
            continue
        new_row = dict(row)
        new_row["workers"] = schedule_tests(kept, tests)
        rewritten.append(new_row)
    return rewritten


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tests", type=Path, required=True)
    parser.add_argument("--commits", type=Path, required=True)
    parser.add_argument("--pinned", type=Path, required=True)
    parser.add_argument("--selection", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    pinned = args.pinned if (args.pinned / "sqlglot" / "__init__.py").is_file() else None
    if pinned is None:
        print("pinned checkout missing; generator-memory test is not removed", file=sys.stderr)
    tests = json.loads(args.tests.read_text(encoding="utf-8"))["tests"]
    selection = [
        json.loads(line)
        for line in args.selection.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    rewritten = apply_filter(selection, tests, args.commits, pinned)
    payload = "".join(json.dumps(row, separators=(",", ":")) + "\n" for row in rewritten)
    args.output.write_text(payload, encoding="utf-8")
    print(f"wrote {len(rewritten)} lines to {args.output}", file=sys.stderr)


if __name__ == "__main__":
    main()
