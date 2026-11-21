"""Record which pinned tests enter which SQLGlot functions.

Run from a checkout that can import the pinned suite (pytest, duckdb, pandas).
The SQLGlot tree is the build input; this script does not modify it.

    python -m tools.build_block_map /path/to/sqlglot sample/tests.json maps/blocks.json

Traces the main thread and threads started after a test begins. A test file
that constructs a process pool is recorded as opaque: workers forked earlier
cannot be attributed to the test that submitted the job. Tests the run does
not finish are left out of ``observed`` so the selector keeps them.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import threading
import time

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from analyzer.block_map import BlockMap, index_tree
from analyzer.contracts import PINNED_SQLGLOT


def build(root: Path, manifest: Path, output: Path) -> BlockMap:
    import pytest

    records = json.loads(manifest.read_text(encoding="utf-8"))["tests"]
    records_ids = [test["id"] for test in records]
    number_by_id = {test["id"]: test["number"] for test in records}
    expected = set(number_by_id)
    ranges = index_tree(root)
    by_code_key = {}
    for path, rows in ranges.items():
        for qualname, def_line, _start, _end in rows:
            by_code_key[(path, qualname, def_line)] = f"{path}:{qualname}@{def_line}"
    source_files = {
        os.path.normcase(str((root / path).resolve())): path
        for path in ranges
    }
    hits: dict[str, set[int]] = {}
    observed: set[int] = set()
    opaque_files: set[str] = set()
    state = {"number": None, "opaque": False}

    sys.monitoring.use_tool_id(1, "testradar-blocks")
    events = sys.monitoring.events

    def on_start(code, _offset):
        number = state["number"]
        if number is None:
            return None
        if code.co_qualname.endswith("ProcessPoolExecutor.__init__"):
            state["opaque"] = True
        filename = source_files.get(os.path.normcase(code.co_filename))
        if filename is None:
            return None
        key = by_code_key.get((filename, code.co_qualname, code.co_firstlineno))
        if key is not None:
            hits.setdefault(key, set()).add(number)
        return None

    sys.monitoring.register_callback(1, events.PY_START, on_start)

    def enable() -> None:
        sys.monitoring.set_events(1, events.PY_START)

    def disable() -> None:
        sys.monitoring.set_events(1, 0)

    previous_thread_profile = threading.getprofile()

    def on_thread(frame, event, arg):
        if event == "call":
            enable()
        if previous_thread_profile is not None:
            return previous_thread_profile(frame, event, arg)
        return None

    class Recorder:
        @pytest.hookimpl(wrapper=True, tryfirst=True)
        def pytest_runtest_protocol(self, item, nextitem):
            number = number_by_id.get(item.nodeid)
            if number is None:
                return (yield)
            state["number"] = number
            state["opaque"] = False
            enable()
            try:
                yield
            finally:
                disable()
                if state["opaque"]:
                    opaque_files.add(item.nodeid.split("::", 1)[0])
                observed.add(number)
                state["number"] = None

    started = time.perf_counter()
    previous_cwd = Path.cwd()
    previous_flag = os.environ.get("SKIP_INTEGRATION")
    sys.path.insert(0, str(root))
    os.environ["SKIP_INTEGRATION"] = "0"
    threading.setprofile(on_thread)
    try:
        os.chdir(root)
        status = pytest.main(
            [
                "-p", "no:cacheprovider",
                "--timeout=180",
                "-q",
                "--continue-on-collection-errors",
                "--rootdir", str(root),
                *records_ids,
            ],
            plugins=[Recorder()],
        )
    finally:
        threading.setprofile(previous_thread_profile)
        disable()
        os.chdir(previous_cwd)
        sys.path.remove(str(root))
        if previous_flag is None:
            os.environ.pop("SKIP_INTEGRATION", None)
        else:
            os.environ["SKIP_INTEGRATION"] = previous_flag
    elapsed = time.perf_counter() - started
    if status != pytest.ExitCode.OK:
        print(f"block trace incomplete (pytest exit code {int(status)})", file=sys.stderr)
    print(
        f"block trace source={PINNED_SQLGLOT} observed={len(observed)}/{len(expected)} "
        f"functions={len(hits)} opaque_files={sorted(opaque_files)} seconds={elapsed:.3f}",
        file=sys.stderr,
    )
    return BlockMap(
        {key: frozenset(values) for key, values in hits.items()},
        frozenset(observed),
        frozenset(opaque_files),
        ranges,
    )


def main() -> None:
    if len(sys.argv) != 4:
        print(f"usage: python -m tools.build_block_map SQLGLOT_ROOT tests.json output", file=sys.stderr)
        sys.exit(2)
    root, manifest, output = (Path(argument).resolve() for argument in sys.argv[1:])
    build(root, manifest, output).save(output)


if __name__ == "__main__":
    main()
