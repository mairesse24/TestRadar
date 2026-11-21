"""File-level per-test coverage from a local checkout of pinned SQLGlot.

Generate offline with the existing test dependencies installed:
    python -m analyzer.coverage_map SQLGLOT_ROOT tests.json coverage.json

Tracing covers the main thread during each pytest test protocol. Collection,
native code, subprocesses, other threads and changes to execution paths can
escape observation. Treat the artifact as positive evidence only; the
is_uncertain method marks coverage-based exclusion as unsupported.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

from .contracts import PINNED_SQLGLOT, safe_path
from .dependency_graph import pinned_files, read_map, write_map


class CoverageMap:
    def __init__(self, by_file: dict[str, set[str]], missing: set[str]):
        self._by_file = by_file
        self.missing_test_ids = frozenset(missing)

    @classmethod
    def load(cls, path: str) -> CoverageMap:
        """Raise OSError or ValueError for unavailable or invalid artifacts."""
        document = read_map(path, "testradar-coverage-v1")
        by_file, missing = document.get("files"), document.get("missing")
        if not isinstance(by_file, dict) or not _node_list(missing):
            raise ValueError("invalid coverage map")
        for path, node_ids in by_file.items():
            if not safe_path(path) or not _node_list(node_ids):
                raise ValueError("invalid coverage entry")
        return cls({path: set(ids) for path, ids in by_file.items()}, set(missing))

    def save(self, path: Path) -> None:
        write_map(path, {
            "schema": "testradar-coverage-v1",
            "source": PINNED_SQLGLOT,
            "files": {path: sorted(ids) for path, ids in self._by_file.items()},
            "missing": sorted(self.missing_test_ids),
        })

    def tests_for_changes(self, changes: list[dict]) -> set[str]:
        hits: set[str] = set()
        for change in changes:
            for key in ("path", "old_path", "new_path"):
                path = change.get(key)
                if safe_path(path):
                    hits.update(self._by_file.get(path, ()))
        return hits

    def is_uncertain(self, changes: list[dict]) -> bool:
        """File observations alone cannot justify excluding tests."""
        return True


def _node_list(value: object) -> bool:
    return isinstance(value, list) and all(
        isinstance(item, str) and "::" in item and bool(item.split("::", 1)[1])
        and safe_path(item.split("::", 1)[0]) for item in value
    )


def build(root: Path, manifest: Path) -> CoverageMap:
    # pytest is a build-time dependency already used by the SQLGlot test suite.
    import pytest

    paths = pinned_files(root)
    records = json.loads(manifest.read_text(encoding="utf-8"))["tests"]
    ids = [test["id"] for test in records]
    if not _node_list(ids) or not ids or len(set(ids)) != len(ids):
        raise ValueError("invalid or duplicate test IDs")
    expected = set(ids)
    source_paths = {
        os.path.normcase(str(root / path)): path
        for path in paths if path.startswith("sqlglot/") and path.endswith(".py")
    }
    coverage: dict[str, set[str]] = {}
    observed: set[str] = set()

    class Recorder:
        @pytest.hookimpl(wrapper=True, tryfirst=True)
        def pytest_runtest_protocol(self, item, nextitem):
            node_id = item.nodeid
            if node_id not in expected:
                return (yield)
            touched: set[str] = set()

            def trace(frame, event, arg):
                if event == "call":
                    path = source_paths.get(os.path.normcase(frame.f_code.co_filename))
                    if path is not None:
                        touched.add(path)
                return None

            previous = sys.gettrace()
            sys.settrace(trace)
            try:
                result = yield
            finally:
                sys.settrace(previous)
            if touched:
                observed.add(node_id)
                for path in touched:
                    coverage.setdefault(path, set()).add(node_id)
            return result

    previous_cwd = Path.cwd()
    previous_flag = os.environ.get("SKIP_INTEGRATION")
    sys.path.insert(0, str(root))
    os.environ["SKIP_INTEGRATION"] = "0"
    try:
        os.chdir(root)
        status = pytest.main(
            ["-q", "--continue-on-collection-errors", "--rootdir", str(root), "tests"],
            plugins=[Recorder()],
        )
    finally:
        os.chdir(previous_cwd)
        sys.path.remove(str(root))
        if previous_flag is None:
            os.environ.pop("SKIP_INTEGRATION", None)
        else:
            os.environ["SKIP_INTEGRATION"] = previous_flag
    if status != pytest.ExitCode.OK:
        print(f"Coverage collection incomplete (pytest exit code {int(status)})", file=sys.stderr)
    return CoverageMap(coverage, expected - observed)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sqlglot_root", type=Path)
    parser.add_argument("tests_json", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    build(args.sqlglot_root.resolve(), args.tests_json.resolve()).save(args.output)


if __name__ == "__main__":
    main()
