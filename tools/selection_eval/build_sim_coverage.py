"""Export a per-test coverage database into a compact JSON map (evaluation only).

This is a *simulation* of what a coverage map from the analysis engine might
look like; it is not used by analyzer/ at run time.

Produce the database on the pinned sqlglot checkout with pytest-cov, e.g.
    SKIP_INTEGRATION=0 python -m pytest -n 4 --cov=sqlglot --cov-context=test --cov-report= tests
then
    python build_sim_coverage.py <.coverage> <sqlglot root> <out.json>

Output: {"tests": [ids], "files": {relpath: {"lines": {line: [test idx]},
"import_time": [lines executed only outside any test context]}}}.
Limitations: lines run in spawned subprocesses (test_optimizer uses a
ProcessPoolExecutor with spawn) and lines executed at import time (module and
class bodies) are not attributed to a test.
"""

import json
import os
import sqlite3
import sys

from coverage.numbits import numbits_to_nums


def main() -> None:
    db, root, out = sys.argv[1:4]
    root = os.path.abspath(root) + os.sep
    con = sqlite3.connect(db)
    contexts = dict(con.execute("select id, context from context"))
    files = {fid: path for fid, path in con.execute("select id, path from file")}
    test_ids = sorted({c.split("|", 1)[0] for c in contexts.values() if c})
    index = {t: i for i, t in enumerate(test_ids)}
    result: dict = {}
    for fid, cid, bits in con.execute("select file_id, context_id, numbits from line_bits"):
        path = files[fid]
        if not path.startswith(root):
            continue
        rel = path[len(root):].replace(os.sep, "/")
        entry = result.setdefault(rel, {"lines": {}, "import_time": set()})
        ctx = contexts[cid]
        for line in numbits_to_nums(bits):
            if ctx:
                entry["lines"].setdefault(str(line), set()).add(index[ctx.split("|", 1)[0]])
            else:
                entry["import_time"].add(line)
    for entry in result.values():
        entry["lines"] = {k: sorted(v) for k, v in entry["lines"].items()}
        entry["import_time"] = sorted(int(x) for x in entry["import_time"] if str(x) not in entry["lines"])
    with open(out, "w", encoding="utf-8") as handle:
        json.dump({"tests": test_ids, "files": result}, handle)


if __name__ == "__main__":
    main()
