"""Augment a pinned V3 block map with attributed child-process observations."""

from __future__ import annotations

import argparse
import ast
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
from contextlib import ExitStack
import json
import multiprocessing.process
import os
from pathlib import Path
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory
import threading
from unittest.mock import patch
import uuid

from analyzer.block_map import BlockMap
from analyzer.dependency_graph import pinned_files
from tools.child_trace_worker import run_task


def audit(root: Path, tracked: list[str]) -> dict[str, set[str]]:
    files: dict[str, set[str]] = {}
    modules = {
        "concurrent": "pool", "multiprocessing": "pool", "subprocess": "subprocess",
        "threading": "thread", "_thread": "thread", "importlib": "dynamic-import",
        "runpy": "dynamic-import",
    }
    calls = {"__import__": "dynamic-import", "import_module": "dynamic-import",
             "fork": "unwrapped-process", "system": "unwrapped-process", "posix_spawn": "unwrapped-process"}
    for path in tracked:
        if not path.startswith("tests/") or not path.endswith(".py"):
            continue
        kinds = set()
        for node in ast.walk(ast.parse((root / path).read_bytes(), filename=path)):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                names = [node.module or ""]
            else:
                names = []
            for name in names:
                kind = modules.get(name.partition(".")[0])
                if kind:
                    kinds.add(kind)
            if isinstance(node, ast.Call):
                callee = node.func
                kind = calls.get(getattr(callee, "attr", getattr(callee, "id", "")))
                if kind:
                    kinds.add(kind)
        if kinds:
            files[path] = kinds
    return files


def trace(root: Path, manifest: Path, seed: Path, output: Path, report_path: Path,
          only: frozenset[str] = frozenset(), test_timeout: int = 15) -> None:
    import pytest

    tracked = pinned_files(root)
    files = audit(root, tracked)
    original = BlockMap.load(str(seed))
    catalog = json.loads(manifest.read_bytes())["tests"]
    targets = {item["id"]: item["number"] for item in catalog if item["file"] in files}
    if only:
        if not only <= targets.keys():
            raise ValueError("requested test IDs are outside the pinned execution audit")
        targets = {name: number for name, number in targets.items() if name in only}
    hits = {key: set(numbers) for key, numbers in original.functions.items()}
    uncertain = set(targets.values()) | set(original.uncertain_tests)
    mechanisms: dict[str, set[str]] = defaultdict(set)
    observed_children: dict[str, set[str]] = defaultdict(set)
    completed: set[str] = set()
    active: str | None = None

    def checkpoint() -> None:
        BlockMap(
            {key: frozenset(values) for key, values in hits.items()},
            original.observed, original.opaque_files, original.ranges, frozenset(uncertain),
        ).save(output)
        tmp = report_path.with_suffix(".tmp")
        tmp.write_text(json.dumps({
            "static": {name: sorted(kinds) for name, kinds in sorted(files.items())},
            "targeted": sorted(targets),
            "completed": sorted(completed),
            "mechanisms": {name: sorted(kinds) for name, kinds in sorted(mechanisms.items())},
            "children": {name: sorted(keys) for name, keys in sorted(observed_children.items())},
            "uncertain": sorted(uncertain),
        }, sort_keys=True), encoding="utf-8")
        tmp.replace(report_path)

    checkpoint()
    if not targets:
        return
    with TemporaryDirectory() as scratch:
        temp = Path(scratch)
        startup = temp / "startup"
        startup.mkdir()
        (startup / "sitecustomize.py").write_text(
            "from tools.child_trace_worker import start_interpreter\nstart_interpreter()\n",
            encoding="utf-8",
        )
        jobs: list[Path] = []
        real_submit = ProcessPoolExecutor.submit
        real_popen = subprocess.Popen
        real_start = multiprocessing.process.BaseProcess.start
        real_thread = threading.Thread.start

        def submit(executor, function, /, *args, **kwargs):
            if active is None:
                return real_submit(executor, function, *args, **kwargs)
            mechanisms[active].add("pool-job")
            destination = temp / uuid.uuid4().hex
            jobs.append(destination)
            return real_submit(executor, run_task, str(root), str(destination), function, args, kwargs)

        def popen(args, *positional, **kwargs):
            if active is not None:
                mechanisms[active].add("subprocess")
                if (not positional and not kwargs.get("shell") and not kwargs.get("executable")
                        and isinstance(args, (list, tuple)) and args
                        and Path(os.fsdecode(args[0])).name in {"python", "python3", Path(sys.executable).name}
                        and len(args) >= 3 and args[1] == "-c"):
                    destination = temp / uuid.uuid4().hex
                    jobs.append(destination)
                    env = dict(os.environ if kwargs.get("env") is None else kwargs["env"])
                    old = env.get("PYTHONPATH", "")
                    env["PYTHONPATH"] = os.pathsep.join(filter(None, (
                        str(startup), str(Path(__file__).resolve().parents[1]), str(root), old,
                    )))
                    env["TESTRADAR_CHILD_ROOT"] = str(root)
                    env["TESTRADAR_CHILD_OUTPUT"] = str(destination)
                    kwargs["env"] = env
            return real_popen(args, *positional, **kwargs)

        def start(child, *args, **kwargs):
            if active is not None:
                mechanisms[active].add("process-start")
            return real_start(child, *args, **kwargs)

        def start_thread(thread, *args, **kwargs):
            if active is not None:
                mechanisms[active].add("thread-start")
            return real_thread(thread, *args, **kwargs)

        known = {f"{path}:{name}@{line}" for path, rows in original.ranges.items()
                 for name, line, _, _ in rows}

        class Recorder:
            @pytest.hookimpl(wrapper=True, tryfirst=True)
            def pytest_runtest_protocol(self, item, nextitem):
                nonlocal active
                number = targets.get(item.nodeid)
                if number is None:
                    return (yield)
                active = item.nodeid
                jobs.clear()
                try:
                    return (yield)
                finally:
                    active = None
                    completed.add(item.nodeid)
                    for file in jobs:
                        try:
                            keys = set(json.loads(file.read_bytes())) & known
                        except (OSError, ValueError, TypeError):
                            mechanisms[item.nodeid].add("missing-child-trace")
                            continue
                        for key in keys:
                            hits.setdefault(key, set()).add(number)
                        observed_children[item.nodeid].update(keys)
                    # A completed Python test with no observed external launch
                    # still keeps the guard when static inspection found
                    # dynamic loading, unmanaged processes, or threads.
                    kinds = files[item.nodeid.split("::", 1)[0]]
                    if not mechanisms[item.nodeid] and kinds <= {"pool", "subprocess"}:
                        uncertain.discard(number)
                    if len(completed) % 10 == 0:
                        checkpoint()

        previous = Path.cwd()
        os.chdir(root)
        sys.path.insert(0, str(root))
        try:
            with ExitStack() as stack:
                stack.enter_context(patch.object(ProcessPoolExecutor, "submit", submit))
                stack.enter_context(patch.object(subprocess, "Popen", popen))
                stack.enter_context(patch.object(multiprocessing.process.BaseProcess, "start", start))
                stack.enter_context(patch.object(threading.Thread, "start", start_thread))
                pytest.main(
                    ["-q", "-p", "no:cacheprovider", "--continue-on-collection-errors",
                     f"--timeout={test_timeout}", "--rootdir", str(root), *targets],
                    plugins=[Recorder()],
                )
        finally:
            sys.path.remove(str(root))
            os.chdir(previous)
            checkpoint()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("root", "manifest", "seed", "output", "report"):
        parser.add_argument(name, type=Path)
    parser.add_argument("--test-id", action="append", default=[])
    parser.add_argument("--test-timeout", type=int, default=15)
    args = parser.parse_args()
    if args.test_timeout <= 0:
        parser.error("--test-timeout must be positive")
    paths = [getattr(args, name).resolve() for name in ("root", "manifest", "seed", "output", "report")]
    trace(*paths, only=frozenset(args.test_id), test_timeout=args.test_timeout)


if __name__ == "__main__":
    main()
