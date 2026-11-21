#!/usr/bin/env python3
"""Score candidate JSONL files by invoking the local score.py.

CPU time is unmeasured. This tool does not time a selector or a container.
It only runs score.py, which reports an exact simulated time and, on a valid
file, the missed commit ids. Pass ``--cpu-seconds`` only when you already
measured a process; that number is forwarded and is not a measurement made
here.

Examples:
    python3 benchmark.py output-docker.jsonl output-local.jsonl
    python3 benchmark.py run.jsonl --data-dir sample --answers sample/answers.json
"""

from __future__ import annotations

import argparse
import re
import shlex
import subprocess
import sys
from pathlib import Path

_SIMULATED = re.compile(r"^Simulated:\s+(\d+\.\d{3} s)\s*$", re.MULTILINE)
_MISSES = re.compile(r"^Misses:\s+(\d+)\b", re.MULTILINE)
_MISSED_COMMIT = re.compile(r"^  missed (\S+)\s*$", re.MULTILINE)
_REJECTED = re.compile(r"^Rejected:\s+(\S+)(?: on line (\d+))?\s*$", re.MULTILINE)


def main(argv: list[str] | None = None) -> int:
    root = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(
        description=(
            "Score one or more candidate JSONL files with the local score.py. "
            "CPU time is unmeasured unless you pass --cpu-seconds from a "
            "measurement you already took."
        )
    )
    parser.add_argument(
        "outputs",
        nargs="+",
        type=Path,
        help="candidate stdout captures, one JSON object per line",
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=root / "sample",
        help="directory with tests.json and commits/*.patch (default: ./sample)",
    )
    parser.add_argument(
        "--answers",
        type=Path,
        default=root / "sample" / "answers.json",
        help="answers.json passed only to score.py (default: ./sample/answers.json)",
    )
    parser.add_argument(
        "--score-py",
        type=Path,
        default=root / "score.py",
        help="path to score.py (default: ./score.py next to this script)",
    )
    parser.add_argument(
        "--cpu-seconds",
        type=float,
        default=None,
        help="already-measured CPU seconds to forward; benchmark.py does not measure any",
    )
    options = parser.parse_args(argv)

    print(
        "CPU time is unmeasured. benchmark.py does not time a selector or a container.",
        flush=True,
    )
    if options.cpu_seconds is not None:
        print(
            "Forwarding --cpu-seconds "
            f"{options.cpu_seconds} to score.py. That value was not measured here.",
            flush=True,
        )

    worst = 0
    for index, output in enumerate(options.outputs):
        if index:
            print()
        status, text = score_candidate(
            output=output,
            data_dir=options.data_dir,
            answers=options.answers,
            score_py=options.score_py,
            cpu_seconds=options.cpu_seconds,
        )
        sys.stdout.write(text)
        if not text.endswith("\n"):
            sys.stdout.write("\n")
        worst = max(worst, status)
    return worst


def score_candidate(
    output: Path,
    data_dir: Path,
    answers: Path,
    score_py: Path,
    cpu_seconds: float | None,
) -> tuple[int, str]:
    command = [
        sys.executable,
        str(score_py),
        str(data_dir),
        str(answers),
        str(output),
    ]
    if cpu_seconds is not None:
        command.extend(["--cpu-seconds", str(cpu_seconds)])

    if not score_py.is_file():
        return 2, _report(
            output=output,
            command=command,
            validation="unscored",
            cpu_seconds=cpu_seconds,
            detail=f"score.py not found: {score_py}",
            stdout="",
            stderr="",
            exit_code=None,
        )
    if not output.is_file():
        return 2, _report(
            output=output,
            command=command,
            validation="unscored",
            cpu_seconds=cpu_seconds,
            detail=f"candidate file not found: {output}",
            stdout="",
            stderr="",
            exit_code=None,
        )

    try:
        completed = subprocess.run(command, capture_output=True, check=False)
    except OSError as error:
        return 2, _report(
            output=output,
            command=command,
            validation="unscored",
            cpu_seconds=cpu_seconds,
            detail=f"could not run score.py: {error}",
            stdout="",
            stderr="",
            exit_code=None,
        )

    stdout = completed.stdout.decode("utf-8", errors="replace")
    stderr = completed.stderr.decode("utf-8", errors="replace")
    parsed = _parse(stdout, stderr, completed.returncode)
    return parsed["status"], _report(
        output=output,
        command=command,
        validation=parsed["validation"],
        cpu_seconds=cpu_seconds,
        detail=parsed["detail"],
        simulated=parsed["simulated"],
        misses=parsed["misses"],
        missed_commits=parsed["missed_commits"],
        line=parsed["line"],
        stdout=stdout,
        stderr=stderr,
        exit_code=completed.returncode,
    )


def _parse(stdout: str, stderr: str, returncode: int) -> dict:
    rejected = _REJECTED.search(stdout)
    simulated = _SIMULATED.search(stdout)
    misses = _MISSES.search(stdout)
    missed_commits = _MISSED_COMMIT.findall(stdout)

    if returncode == 0 and simulated and misses:
        return {
            "status": 0,
            "validation": "accepted",
            "detail": "",
            "simulated": simulated.group(1),
            "misses": int(misses.group(1)),
            "missed_commits": missed_commits,
            "line": None,
        }
    if returncode == 1 and rejected:
        return {
            "status": 1,
            "validation": rejected.group(1),
            "detail": "",
            "simulated": None,
            "misses": None,
            "missed_commits": None,
            "line": None if rejected.group(2) is None else int(rejected.group(2)),
        }

    detail = stderr.strip() or stdout.strip() or f"score.py exited {returncode}"
    return {
        "status": 2,
        "validation": "unscored",
        "detail": detail,
        "simulated": None,
        "misses": None,
        "missed_commits": None,
        "line": None,
    }


def _report(
    output: Path,
    command: list[str],
    validation: str,
    cpu_seconds: float | None,
    detail: str,
    stdout: str,
    stderr: str,
    exit_code: int | None,
    simulated: str | None = None,
    misses: int | None = None,
    missed_commits: list[str] | None = None,
    line: int | None = None,
) -> str:
    lines = [f"candidate: {output}", f"validation: {validation}"]
    if simulated is not None:
        lines.append(f"simulated: {simulated}")
    if misses is not None:
        lines.append(f"misses: {misses}")
    if missed_commits is not None:
        lines.append(
            "missed_commits: " + (", ".join(missed_commits) if missed_commits else "none")
        )
    if line is not None:
        lines.append(f"line: {line}")
    if cpu_seconds is None:
        lines.append("cpu: unmeasured")
    else:
        lines.append(
            f"cpu: {cpu_seconds} s supplied with --cpu-seconds; "
            "benchmark.py did not measure a process"
        )
    if detail:
        lines.append(f"detail: {detail}")
    lines.append(f"command: {shlex.join(command)}")
    if exit_code is not None:
        lines.append(f"score.py exit: {exit_code}")
    lines.append("--- score.py stdout ---")
    lines.append(stdout.rstrip("\n"))
    lines.append("--- score.py stderr ---")
    lines.append(stderr.rstrip("\n"))
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    sys.exit(main())
