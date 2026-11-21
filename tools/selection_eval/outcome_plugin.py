"""pytest plugin (evaluation only): record which test node ids failed.

Writes JSON to the path in $SELECTION_EVAL_OUTCOMES:
{"failed": [...nodeids], "passed": [...], "collect_errors": [...paths]}.
A test counts as failed when any phase (setup/call/teardown) or any subtest
fails or errors, matching the scorer's definition in README.md.
"""

import json
import os

_failed = set()
_seen = set()
_collect_errors = set()


def pytest_runtest_logreport(report):
    nodeid = report.nodeid.split("[", 1)[0] if "::" in report.nodeid else report.nodeid
    _seen.add(nodeid)
    if report.failed:
        _failed.add(nodeid)


def pytest_collectreport(report):
    if report.failed:
        _collect_errors.add(report.nodeid)


def pytest_sessionfinish(session, exitstatus):
    path = os.environ.get("SELECTION_EVAL_OUTCOMES")
    if not path:
        return
    # xdist workers report through the controller; only the controller writes.
    if hasattr(session.config, "workerinput"):
        return
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(
            {
                "failed": sorted(_failed),
                "passed": sorted(_seen - _failed),
                "collect_errors": sorted(_collect_errors),
            },
            handle,
        )
