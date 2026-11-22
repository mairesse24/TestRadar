"""Collect positive execution evidence from a full pytest node on pinned source."""

from __future__ import annotations

import argparse
import atexit
from collections import Counter
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile

PIN = "eb11aec4be8cb2122afe713a4816662c3f0fb2fa"
TARGET = "tests/test_parser.py::TestParser::test_max_nodes"
PROCESS_EVENTS = {"subprocess.Popen", "os.fork", "os.forkpty", "os.posix_spawn", "os.exec", "os.system"}


def install():
    root = Path(os.environ["TESTRADAR_FOOTPRINT_ROOT"])
    directory = Path(os.environ["TESTRADAR_FOOTPRINT_DIR"])
    prefix = str(root) + os.sep
    monitor = sys.monitoring
    log = None
    seen = set()

    def write(kind, value=None):
        log.write(json.dumps([kind, value], separators=(",", ":")) + "\n")

    def start_process():
        nonlocal log
        if log is not None:
            log.close()
        log = (directory / f"{os.getpid()}.jsonl").open("a", encoding="utf-8", buffering=1)
        seen.clear()
        write("start", os.getppid())

    def observe(code, offset):
        path = code.co_filename
        if path.startswith(prefix):
            identity = (path[len(prefix):], code.co_qualname, code.co_firstlineno)
            if identity not in seen:
                seen.add(identity)
                write("function", identity)
        return monitor.DISABLE

    def audit(event, args):
        if event in PROCESS_EVENTS:
            write("process", event)

    def after_fork():
        start_process()
        monitor.restart_events()

    def finish():
        write("finish")

    start_process()
    monitor.use_tool_id(4, "testradar-footprint")
    for event in (monitor.events.PY_START, monitor.events.PY_RESUME):
        monitor.register_callback(4, event, observe)
    monitor.set_events(4, monitor.events.PY_START | monitor.events.PY_RESUME)
    sys.addaudithook(audit)
    os.register_at_fork(after_in_child=after_fork)
    atexit.register(finish)


def pytest_runtest_logreport(report):
    directory = os.environ.get("TESTRADAR_FOOTPRINT_DIR")
    if directory:
        with (Path(directory) / "protocol.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps([report.nodeid, report.when, report.outcome]) + "\n")


def summarize(directory, target, exit_code):
    functions, processes, unfinished = set(), Counter(), []
    traces = sorted(directory.glob("[0-9]*.jsonl"))
    for path in traces:
        events = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        kinds = [event[0] for event in events]
        if not kinds or kinds[0] != "start" or kinds[-1] != "finish":
            unfinished.append(path.stem)
        functions.update(tuple(value) for kind, value in events if kind == "function")
        processes.update(value for kind, value in events if kind == "process")
    protocol = directory / "protocol.jsonl"
    reports = [json.loads(line) for line in protocol.read_bytes().splitlines()] if protocol.is_file() else []
    phases = [[target, phase, "passed"] for phase in ("setup", "call", "teardown")]
    return {
        "target": target, "exit_code": exit_code, "protocol_passed": exit_code == 0 and reports == phases,
        "process_count": len(traces), "process_events": dict(processes), "unfinished": unfinished,
        "functions": sorted(functions),
        # Observations cannot establish a closed execution model even when
        # all observed processes finished and repeated traces match.
        "eligible_for_exclusion": False,
    }


def collect(root, target, deadline):
    root = root.resolve()
    revision = subprocess.check_output(
        ["git", "-c", f"safe.directory={root}", "-C", str(root), "rev-parse", "HEAD"], text=True,
    ).strip()
    if revision != PIN:
        raise ValueError("The recorder requires pinned SQLGlot")
    with tempfile.TemporaryDirectory() as tmp:
        scratch = Path(tmp)
        shutil.copyfile(__file__, scratch / "sitecustomize.py")
        env = dict(os.environ, TESTRADAR_FOOTPRINT_DIR=tmp, TESTRADAR_FOOTPRINT_ROOT=str(root),
                   PYTHONPATH=os.pathsep.join((tmp, str(root))), PYTHONDONTWRITEBYTECODE="1",
                   PYTHONPYCACHEPREFIX=str(scratch / "bytecode"), SKIP_INTEGRATION="0",
                   PYTEST_DISABLE_PLUGIN_AUTOLOAD="1")
        command = [sys.executable, "-B", "-m", "pytest", "-q", "-p", "sitecustomize",
                   "-p", "no:cacheprovider", target]
        process = subprocess.Popen(command, cwd=root, env=env, start_new_session=True, stdout=sys.stderr)
        try:
            status = process.wait(timeout=deadline)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()
            status = "timeout"
        return summarize(scratch, target, status)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    parser.add_argument("--target", default=TARGET)
    parser.add_argument("--deadline", type=int, default=210)
    args = parser.parse_args()
    print(json.dumps(collect(args.root, args.target, args.deadline), separators=(",", ":")))


if __name__ == "sitecustomize" and os.environ.get("TESTRADAR_FOOTPRINT_DIR"):
    install()
elif __name__ == "__main__":
    main()
