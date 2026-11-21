"""Run a built image under the Makefile's scorer flags and score it like the board.

    python3 tools/selection_eval/run_container.py IMAGE DATA_DIR ANSWERS [--runs 3]

DATA_DIR holds tests.json and commits/*.patch and is mounted read-only exactly
as in the Makefile. Each run gets its own cgroup parent so the CPU time of
every process in the container (user + system, cgroup v2 ``usage_usec``) can
be read after the container exits. The result uses score.py's arithmetic and
the median of the run totals, as SCORING.md describes. ANSWERS is read here,
outside the container, only for scoring.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import statistics
import subprocess
import sys
import uuid

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
import score  # noqa: E402

CGROUP_ROOT = Path("/sys/fs/cgroup")


def run_flags(data: Path) -> list[str]:
    # Keep in sync with RUN_FLAGS in the Makefile.
    return [
        "--rm", "--platform", "linux/amd64", "--network", "none", "--cpus", "2",
        "--memory", "4096m", "--memory-swap", "4096m", "--pids-limit", "256", "--read-only",
        "--tmpfs", "/tmp:rw,size=64m,mode=1777", "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges", "--user", "65534:65534", "--env", "HOME=/tmp",
        "--volume", f"{data / 'commits'}:/data/commits:ro",
        "--volume", f"{data / 'tests.json'}:/data/tests.json:ro",
    ]


def cpu_usage_usec(group: Path) -> int:
    for line in (group / "cpu.stat").read_text().splitlines():
        key, value = line.split()
        if key == "usage_usec":
            return int(value)
    raise RuntimeError("usage_usec missing")


def one_run(image: str, data: Path, extra: list[str]) -> tuple[bytes, str, int, int]:
    parent = f"testradar-eval-{uuid.uuid4().hex[:12]}"
    group = CGROUP_ROOT / parent
    subprocess.run(["sudo", "mkdir", str(group)], check=True)
    try:
        completed = subprocess.run(
            ["docker", "run", *run_flags(data), "--cgroup-parent", f"/{parent}", *extra, image],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=180,
        )
        usage = cpu_usage_usec(group)
    finally:
        subprocess.run(["sudo", "rmdir", str(group)], check=False)
    return completed.stdout, completed.stderr.decode("utf-8", "replace"), completed.returncode, usage


def missed_tests(stdout: bytes, answers: dict) -> int:
    chosen: dict[str, set[int]] = {}
    for line in stdout.decode("utf-8").splitlines():
        if line.strip():
            item = json.loads(line)
            chosen[item["commit"]] = {n for worker in item["workers"] for n in worker}
    return sum(len(set(a["failing"]) - chosen.get(c, set())) for c, a in answers.items())


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image")
    parser.add_argument("data", type=Path)
    parser.add_argument("answers", type=Path)
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--env", action="append", default=[], help="extra --env for docker run")
    parser.add_argument("--save", type=Path, help="write the last run's stdout here")
    args = parser.parse_args()
    data = args.data.resolve()
    tests = json.loads((data / "tests.json").read_text("utf-8"))["tests"]
    answers = json.loads(args.answers.read_text("utf-8"))["commits"]
    extra = [flag for item in args.env for flag in ("--env", item)]
    runs = []
    for _ in range(args.runs):
        stdout, stderr, code, usage = one_run(args.image, data, extra)
        evaluation = score.evaluate_run(stdout, tests, answers)
        summary = stderr.strip().splitlines()[-1:] if stderr.strip() else []
        run = {"exit_code": code, "cpu_usec": usage, "stdout_bytes": len(stdout), "stderr_tail": summary}
        if evaluation["ok"]:
            run.update(
                accepted=True,
                simulated_ms=evaluation["simulated_milliseconds"],
                missed_commits=evaluation["missed_commits"],
                missed_tests=missed_tests(stdout, answers),
                total_usec=score.run_total_microseconds(evaluation["simulated_milliseconds"], usage),
            )
        else:
            run.update(accepted=False, rejection=evaluation["code"], line=evaluation.get("line"))
        runs.append(run)
        if args.save:
            args.save.write_bytes(stdout)
    valid = all(r["accepted"] and r["exit_code"] == 0 for r in runs)
    result = {"image": args.image, "data": str(data), "runs": runs, "valid": valid}
    if valid:
        median = statistics.median_low(sorted(r["total_usec"] for r in runs))
        result["median_total_s"] = (median + 50_000) // 100_000 / 10
        result["median_cpu_s"] = statistics.median(r["cpu_usec"] for r in runs) / 1e6
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
