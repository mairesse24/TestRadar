"""Apply each mutant in a git worktree of pinned sqlglot, record its patch and
the real failing tests (evaluation only; never imported by analyzer/).

    python3 run_mutants.py <worktree> <mutants.json> <tests.json> <out_dir> [--python PY] [-n N]

Ground truth for a mutant = tests.json ids that fail (any phase or subtest) or
are not collected at all (import/collection error), minus ids that already
fail on the unmodified tree. Results go to <out_dir>/<id>.patch and
<out_dir>/<id>.json; finished mutants are skipped on re-run.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))


def run(cmd, cwd, **kw):
    return subprocess.run(cmd, cwd=cwd, check=True, text=True, capture_output=True, **kw)


def run_suite(tree: str, python: str, workers: str, out: str) -> dict:
    env = dict(os.environ)
    env.update({
        "SKIP_INTEGRATION": "0",
        "SELECTION_EVAL_OUTCOMES": out,
        "PYTHONPATH": f"{tree}{os.pathsep}{HERE}",
        "PATH": os.path.dirname(python) + os.pathsep + env.get("PATH", ""),
    })
    if os.path.exists(out):
        os.remove(out)
    subprocess.run(
        [python, "-m", "pytest", "-q", "-p", "outcome_plugin", "-p", "no:cacheprovider",
         "-n", workers, "--timeout", "300", "tests"],
        cwd=tree, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=1800,
    )
    if not os.path.exists(out):
        return {"failed": [], "passed": [], "collect_errors": ["<pytest crashed>"]}
    with open(out, encoding="utf-8") as handle:
        return json.load(handle)


def apply(tree: str, ops: list[dict]) -> None:
    for op in ops:
        path = os.path.join(tree, op["path"])
        if op["kind"] == "edit":
            with open(path, encoding="utf-8") as handle:
                lines = handle.read().splitlines(keepends=True)
            assert lines[op["line"] - 1] == op["old"], (op["path"], op["line"])
            lines[op["line"] - 1] = op["new"]
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("".join(lines))
        elif op["kind"] == "add":
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(op["content"])
            run(["git", "add", "-N", op["path"]], tree)
        elif op["kind"] == "delete":
            run(["git", "rm", "-q", op["path"]], tree)
        elif op["kind"] == "rename":
            run(["git", "mv", op["path"], op["to"]], tree)


def main() -> None:
    tree, mutants_path, tests_path, out_dir = sys.argv[1:5]
    python = sys.argv[sys.argv.index("--python") + 1] if "--python" in sys.argv else sys.executable
    workers = sys.argv[sys.argv.index("-n") + 1] if "-n" in sys.argv else "4"
    os.makedirs(out_dir, exist_ok=True)
    ids = {t["id"] for t in json.load(open(tests_path, encoding="utf-8"))["tests"]}
    base_path = os.path.join(out_dir, "_baseline.json")
    if not os.path.exists(base_path):
        run(["git", "reset", "-q", "--hard"], tree)
        base = run_suite(tree, python, workers, os.path.join(out_dir, "_raw.json"))
        with open(base_path, "w", encoding="utf-8") as handle:
            json.dump(base, handle)
    base = json.load(open(base_path, encoding="utf-8"))
    base_bad = (set(base["failed"]) | (ids - set(base["failed"]) - set(base["passed"]))) & ids
    mutants = json.load(open(mutants_path, encoding="utf-8"))["mutants"]
    if "--reverse" in sys.argv:  # lets a second runner work from the other end
        mutants = mutants[::-1]
    skip_dirs = [sys.argv[i + 1] for i, a in enumerate(sys.argv) if a == "--skip-done-in"]
    for mutant in mutants:
        result_path = os.path.join(out_dir, mutant["id"] + ".json")
        if os.path.exists(result_path) or any(
            os.path.exists(os.path.join(d, mutant["id"] + ".json")) for d in skip_dirs
        ):
            continue
        run(["git", "reset", "-q", "--hard"], tree)
        run(["git", "clean", "-qfd", "sqlglot", "tests"], tree)
        try:
            apply(tree, mutant["ops"])
            patch = run(["git", "diff", "-M", "HEAD"], tree).stdout
            start = time.time()
            raw = run_suite(tree, python, workers, os.path.join(out_dir, "_raw.json"))
            seen = set(raw["failed"]) | set(raw["passed"])
            failing = ((set(raw["failed"]) | (ids - seen)) & ids) - base_bad
            with open(os.path.join(out_dir, mutant["id"] + ".patch"), "w", encoding="utf-8") as handle:
                handle.write(patch)
            with open(result_path, "w", encoding="utf-8") as handle:
                json.dump({"id": mutant["id"], "stratum": mutant["stratum"], "failing": sorted(failing),
                           "collect_errors": raw["collect_errors"], "seconds": round(time.time() - start, 1)},
                          handle)
            print(f"{mutant['id']}: {len(failing)} failing", file=sys.stderr, flush=True)
        finally:
            run(["git", "reset", "-q", "--hard"], tree)
            run(["git", "clean", "-qfd", "sqlglot", "tests"], tree)


if __name__ == "__main__":
    main()
