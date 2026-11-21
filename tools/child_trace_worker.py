"""Build-only Python trace entry points used by spawned jobs and interpreters."""

from __future__ import annotations

from contextlib import contextmanager
import atexit
import json
import os
from pathlib import Path
import sys


@contextmanager
def capture(root: str, output: str):
    monitoring = sys.monitoring
    tool = 5
    if monitoring.get_tool(tool) is not None:
        yield
        return
    hits = set()
    prefix = os.path.join(root, "sqlglot") + os.sep

    def entry(code, offset):
        if code.co_filename.startswith(prefix):
            path = os.path.relpath(code.co_filename, root).replace(os.sep, "/")
            hits.add(f"{path}:{code.co_qualname}@{code.co_firstlineno}")

    events = monitoring.events.PY_START | monitoring.events.PY_RESUME
    monitoring.use_tool_id(tool, "testradar-child")
    monitoring.register_callback(tool, monitoring.events.PY_START, entry)
    monitoring.register_callback(tool, monitoring.events.PY_RESUME, entry)
    monitoring.set_events(tool, events)
    try:
        yield
    finally:
        monitoring.set_events(tool, 0)
        monitoring.register_callback(tool, monitoring.events.PY_START, None)
        monitoring.register_callback(tool, monitoring.events.PY_RESUME, None)
        monitoring.free_tool_id(tool)
        temporary = Path(output + ".tmp")
        try:
            temporary.write_text(json.dumps(sorted(hits)), encoding="utf-8")
            temporary.replace(output)
        except OSError:
            pass


def run_task(root, output, function, args, kwargs):
    with capture(root, output):
        return function(*args, **kwargs)


def start_interpreter():
    root = os.environ.pop("TESTRADAR_CHILD_ROOT", None)
    output = os.environ.pop("TESTRADAR_CHILD_OUTPUT", None)
    if root is not None and output is not None:
        session = capture(root, output)
        session.__enter__()
        atexit.register(session.__exit__, None, None, None)
