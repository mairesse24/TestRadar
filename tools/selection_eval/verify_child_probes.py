"""Generate and verify two child-only mutations in a disposable pinned checkout."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

from analyzer.dependency_graph import pinned_files


PROBES = (
    (
        "pool-child-only", "sqlglot/optimizer/optimizer.py",
        "    schema = ensure_schema(schema, dialect=dialect)",
        '    schema = ensure_schema(schema, dialect=dialect) if not inspect.sys.modules.get("multiprocessing") or inspect.sys.modules["multiprocessing"].parent_process() is None else None',
        ("tests/test_optimizer.py::TestOptimizer::test_optimize",
         "tests/test_optimizer.py::TestOptimizer::test_optimize_copy"),
    ),
    (
        "python-subprocess-only", "sqlglot/dialects/dialect.py",
        "        return klass",
        '        return klass if sys.argv[0] != "-c" else None',
        ("tests/dialects/test_dialect.py::TestDialect::test_lazy_load",
         "tests/dialects/test_dialect.py::TestDialect::test_enum"),
    ),
)


def outcomes(tree: Path, ids: tuple[str, ...], recording: Path, pythonpath: str) -> dict:
    recording.unlink(missing_ok=True)
    env = dict(os.environ, PYTHONPATH=pythonpath, SKIP_INTEGRATION="0",
               PYTHON_CPU_COUNT="4", SELECTION_EVAL_OUTCOMES=str(recording))
    result = subprocess.run(
        [sys.executable, "-B", "-m", "pytest", "-q", "-p", "outcome_plugin",
         "-p", "no:cacheprovider", "--timeout=45", *ids],
        cwd=tree, env=env, capture_output=True, timeout=95,
    )
    if not recording.exists():
        raise RuntimeError(result.stdout.decode(errors="replace") + result.stderr.decode(errors="replace"))
    return json.loads(recording.read_bytes())


def generate(tree: Path, output: Path, catalog: Path) -> dict:
    pinned_files(tree)
    tests = json.loads(catalog.read_bytes())["tests"]
    numbers = {test["id"]: test["number"] for test in tests}
    commits = output / "commits"
    commits.mkdir(parents=True)
    recording = output / "probe-outcome.json"
    pythonpath = os.pathsep.join((str(tree), str(Path(__file__).resolve().parent)))
    labels = {}
    for name, path, old, new, ids in PROBES:
        test_before = outcomes(tree, ids, recording, pythonpath)
        if set(test_before["passed"]) != set(ids):
            raise RuntimeError(f"baseline probes did not pass: {name} {test_before}")
        file = tree / path
        original = file.read_bytes()
        source = original.decode("utf-8")
        if source.splitlines().count(old) != 1:
            raise ValueError(f"pinned source did not match: {path}")
        try:
            file.write_bytes(source.replace(old + "\n", new + "\n", 1).encode())
            delta = subprocess.check_output(["git", "diff", "HEAD", "--", path], cwd=tree)
            (commits / f"{name}.patch").write_bytes(delta)
            after = outcomes(tree, ids, recording, pythonpath)
        finally:
            file.write_bytes(original)
        if set(after["failed"]) != {ids[0]} or set(after["passed"]) != {ids[1]}:
            raise RuntimeError(f"child mutation did not distinguish child and control: {name} {after}")
        labels[name] = {"failing": [numbers[ids[0]]]}
        print(f"{name}: {ids[0]} failed; {ids[1]} passed", file=sys.stderr)
    (output / "tests.json").write_bytes(catalog.read_bytes())
    (output / "answers.json").write_text(json.dumps({"commits": labels}), encoding="utf-8")
    return labels


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("tree", "output", "catalog"):
        parser.add_argument(name, type=Path)
    args = parser.parse_args()
    generate(args.tree.resolve(), args.output.resolve(), args.catalog.resolve())


if __name__ == "__main__":
    main()
