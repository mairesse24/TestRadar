"""Offline shadow verification. Schedules and label files are never rewritten."""

from __future__ import annotations

import argparse
from collections import Counter
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import uuid
from xml.etree import ElementTree

PIN = "eb11aec4be8cb2122afe713a4816662c3f0fb2fa"
RUN_FLAGS = (
    "--platform", "linux/amd64", "--network", "none", "--memory", "4096m",
    "--memory-swap", "4096m", "--pids-limit", "256", "--read-only", "--tmpfs",
    "/tmp:rw,size=64m,mode=1777", "--cap-drop", "ALL", "--security-opt",
    "no-new-privileges", "--user", "65534:65534", "--env", "HOME=/tmp",
)


def packing_cost(workers, durations):
    loads = [sum(durations[number] for number in worker) for worker in workers if worker]
    return max(loads, default=0) + 2000 * len(loads)


def plan(rows, catalog, max_test_ms=1000, estimated_cpu_ms=1000):
    """Rank positive marginal savings under the actual worker objective."""
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from analyzer.scheduler import duration_milliseconds, schedule_tests

    tests = {test["number"]: test for test in catalog}
    durations = {n: duration_milliseconds(test["seconds"]) for n, test in tests.items()}
    for row in rows:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", row["commit"]):
            raise ValueError("Invalid commit identifier in schedule")
    opportunities = []
    for row in rows:
        workers = row["workers"]
        selected = {number for worker in workers for number in worker}
        current_cost = packing_cost(workers, durations)
        largest = sorted((durations[n] for n in selected), reverse=True)[:2]
        total = sum(durations[n] for n in selected)
        for number in sorted(selected):
            duration = durations[number]
            if duration > max_test_ms:
                continue
            remaining = total - duration
            longest = largest[1] if len(largest) == 2 and duration == largest[0] else largest[0]
            bound = min(
                (max(longest, (remaining + k - 1) // k) + 2000 * k
                 for k in range(1, min(8, len(selected) - 1) + 1)),
                default=0,
            )
            if current_cost - bound <= estimated_cpu_ms:
                continue
            savings = current_cost - packing_cost(schedule_tests(selected - {number}, catalog), durations)
            if savings > estimated_cpu_ms:
                opportunities.append((savings, row["commit"], tests[number]["id"]))
    return sorted(opportunities, key=lambda item: (-item[0], item[1], item[2]))


def cpu_usage():
    try:
        with open("/sys/fs/cgroup/cpu.stat", encoding="utf-8") as stream:
            return int(next(line.split()[1] for line in stream if line.startswith("usage_usec ")))
    except (OSError, ValueError, StopIteration):
        return None


def xml_outcome(path, returncode):
    if returncode != 0:
        return "pytest-nonzero"
    try:
        tests = ElementTree.parse(path).findall(".//testcase")
    except (OSError, ElementTree.ParseError):
        return "inconclusive"
    if len(tests) == 1 and not any(tests[0].find(tag) is not None for tag in ("failure", "error", "skipped")):
        return "observed-pass"
    return "inconclusive"


def apply_patch(root, patch):
    """Apply exact bytes first; recognize uniform CRLF transport on retry."""
    command = ["git", "-c", "core.autocrlf=false", "apply", "--whitespace=nowarn", "-"]
    with patch.open("rb") as source:
        raw = source.read(2 * 1024 * 1024 + 1)
    if len(raw) > 2 * 1024 * 1024:
        return None
    inputs = [("exact", raw)]
    if b"\r\n" in raw and raw.count(b"\r\n") == raw.count(b"\n"):
        inputs.append(("normalized-crlf", raw.replace(b"\r\n", b"\n")))
    for transport, data in inputs:
        result = subprocess.run(command, cwd=root, input=data,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if result.returncode == 0:
            return transport
    return None


def worker(node_id):
    start = cpu_usage()
    pinned = Path("/sqlglot")
    status = "unavailable"
    pinned_git = ["git", "-c", f"safe.directory={pinned}", "-C", str(pinned)]
    if subprocess.check_output([*pinned_git, "rev-parse", "HEAD"], text=True).strip() != PIN:
        raise ValueError("Unexpected SQLGlot revision")
    with tempfile.TemporaryDirectory() as tmp:
        root, report = Path(tmp) / "sqlglot", Path(tmp) / "report.xml"
        shutil.copytree(pinned, root, ignore=shutil.ignore_patterns(".git", "__pycache__", ".pytest_cache"))
        transport = apply_patch(root, Path("/input/patch"))
        if transport is None:
            status = "patch-rejected"
        else:
            env = dict(os.environ, PYTHONPATH=str(root), PYTHONDONTWRITEBYTECODE="1", SKIP_INTEGRATION="0")
            completed = subprocess.run(
                [sys.executable, "-B", "-m", "pytest", "-q", "-p", "no:cacheprovider",
                 f"--junitxml={report}", "--", node_id],
                cwd=root, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            status = xml_outcome(report, completed.returncode)
    end = cpu_usage()
    return {"trial": status, "patch_transport": transport,
            "cpu_seconds": (end - start) / 1e6 if start is not None and end is not None else None}


def isolate(image, patch, node_id, timeout):
    name = f"testradar-v4-{uuid.uuid4().hex}"
    command = [
        "docker", "create", *RUN_FLAGS, "--cpus", "1", "--name", name,
        "--volume", f"{patch.resolve()}:/input/patch:ro",
        "--volume", f"{Path(__file__).resolve()}:/verification.py:ro",
        "--entrypoint", "python", image, "-B", "/verification.py", "worker", node_id,
    ]
    created = subprocess.run(command, capture_output=True, text=True, timeout=20)
    if created.returncode:
        return {"trial": "container-error", "cpu_seconds": None, "diagnostic": created.stderr[-1000:]}
    try:
        completed = subprocess.run(["docker", "start", "-a", name], timeout=timeout,
                                   capture_output=True, text=True)
        if completed.returncode == 0:
            return json.loads(completed.stdout)
        return {"trial": "container-error", "cpu_seconds": None, "diagnostic": completed.stderr[-1000:]}
    except subprocess.TimeoutExpired:
        return {"trial": "timeout", "cpu_seconds": None}
    except (OSError, json.JSONDecodeError):
        return {"trial": "inconclusive", "cpu_seconds": None}
    finally:
        # An external controller kills every process in the trial container.
        # A cleanup failure stops the whole batch before another trial starts.
        subprocess.run(["docker", "rm", "-f", name], capture_output=True, timeout=10, check=True)


def main():
    if len(sys.argv) == 3 and sys.argv[1] == "worker":
        print(json.dumps(worker(sys.argv[2])))
        return
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("data", type=Path)
    parser.add_argument("schedule", type=Path)
    parser.add_argument("--image", default="testradar-integration:map-builder")
    parser.add_argument("--budget", type=int, default=30, help="total trial wall seconds reserved at one CPU")
    parser.add_argument("--trial", type=int, default=5, help="wall deadline per trial")
    parser.add_argument("--estimated-cpu-ms", type=int, default=1000)
    args = parser.parse_args()
    if args.budget < 0 or args.trial <= 0 or args.estimated_cpu_ms < 0:
        parser.error("budget and estimate must be nonnegative and trial must be positive")
    rows = [json.loads(line) for line in args.schedule.read_bytes().splitlines()]
    catalog = json.loads((args.data / "tests.json").read_bytes())["tests"]
    opportunities = plan(rows, catalog, estimated_cpu_ms=args.estimated_cpu_ms)
    outcomes = Counter()
    measured_cpu = 0.0
    slots = args.budget // args.trial
    commits = (args.data / "commits").resolve()
    for savings, commit, node_id in opportunities[:slots]:
        patch = (commits / f"{commit}.patch").resolve()
        if patch.parent != commits or not patch.is_file():
            outcome = {"trial": "patch-unavailable", "cpu_seconds": None}
        else:
            outcome = isolate(args.image, patch, node_id, args.trial)
        outcomes[outcome["trial"]] += 1
        measured_cpu += outcome["cpu_seconds"] or 0
        print(json.dumps({"commit": commit, "test": node_id, "hypothetical_ms": savings, **outcome}), flush=True)
    print(json.dumps({"opportunities": len(opportunities), "outcomes": outcomes,
                      "measured_worker_cpu_seconds": measured_cpu,
                      "reserved_trial_seconds": sum(outcomes.values()) * args.trial}))


if __name__ == "__main__":
    main()
