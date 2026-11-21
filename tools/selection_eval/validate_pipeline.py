"""Validate an image with the scorer restrictions, including Docker Desktop.

Use --legacy for the known-good image whose selector has no --policy flag.
The answer manifest is read by this host process and is never mounted.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import subprocess
from time import perf_counter

from run_container import missed_tests, run_flags, score


_METER = """
import atexit
from pathlib import Path
import runpy
import sys

@atexit.register
def report():
    counters = dict(line.split() for line in Path("/sys/fs/cgroup/cpu.stat").read_text().splitlines())
    print("testradar_cpu_usec=" + counters["usage_usec"], file=sys.stderr)

sys.argv[0] = "/app/selector.py"
runpy.run_path(sys.argv[0], run_name="__main__")
"""


def evaluate(image: str, data: Path, tests: list[dict], answers: dict, policy: str) -> dict:
    base = ["docker", "run", *run_flags(data)]
    suffix = [] if policy in {"legacy", "default"} else ["--policy", policy]
    start = perf_counter()
    direct = subprocess.run(base + [image, *suffix], capture_output=True, check=True, timeout=180)
    wall = perf_counter() - start
    scored = score.evaluate_run(direct.stdout, tests, answers)
    if not scored["ok"] or len(direct.stdout) > 2_000_000:
        raise ValueError(f"output rejected: {scored}")
    summary = direct.stderr.decode().splitlines()[-1]
    fallbacks = int(re.search(r"full_suite_fallbacks=(\d+)", summary)[1])
    records = [json.loads(line) for line in direct.stdout.splitlines()]
    selected = sum(len(worker) for record in records for worker in record["workers"])
    measured = subprocess.run(
        base + ["--entrypoint", "python", image, "-c", _METER, *suffix],
        capture_output=True, check=True, timeout=180,
    )
    if direct.stdout != measured.stdout:
        raise ValueError("CPU measurement run changed stdout")
    cpu = re.search(rb"^testradar_cpu_usec=(\d+)$", measured.stderr, re.MULTILINE)
    return {
        "policy": policy,
        "summary": summary,
        "json_lines": len(records),
        "stdout_bytes": len(direct.stdout),
        "tests_scheduled": selected,
        "fallbacks": fallbacks,
        "simulated_s": scored["simulated_milliseconds"] / 1000,
        "missed_commits": scored["missed_commits"],
        "missed_tests": missed_tests(direct.stdout, answers),
        "docker_wall_s": round(wall, 3),
        "container_cpu_usec": int(cpu[1]) if cpu else None,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image")
    parser.add_argument("data", type=Path)
    parser.add_argument("answers", type=Path)
    parser.add_argument("--legacy", action="store_true")
    args = parser.parse_args()
    data = args.data.resolve()
    tests = json.loads((data / "tests.json").read_text(encoding="utf-8"))["tests"]
    answers = json.loads(args.answers.read_text(encoding="utf-8"))["commits"]
    policies = ("legacy",) if args.legacy else ("default", "safe", "balanced")
    for policy in policies:
        print(json.dumps(evaluate(args.image, data, tests, answers, policy)), flush=True)


if __name__ == "__main__":
    main()
