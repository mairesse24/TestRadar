# Docker testing notes

These notes cover how to capture a run and how to call the scheduler. They do not change `Dockerfile` or `selector.py`.

## PowerShell redirection writes UTF-16

`score.py` reads the saved stdout as raw bytes and decodes them as UTF-8 (`evaluate_run` in `score.py`). A file that is not valid UTF-8 is rejected as `not_utf8`. A byte-order mark is not stripped, so a UTF-8 BOM on an otherwise valid file fails earlier as `not_json` on line 1 (`SCORING.md`).

In Windows PowerShell 5.1, the `>` operator is `Out-File` with the Unicode encoding, which is UTF-16 LE. Docker's JSON bytes are decoded by the shell and written back as UTF-16. `score.py` then rejects the file as `not_utf8`. Bash and `cmd.exe` redirections do not do this: `make sample` is safe in `sh` because the shell writes the process's stdout bytes unchanged.

Do not use this on Windows PowerShell:

```powershell
docker run ... > output.jsonl
```

The first bytes of a good file are `7B` (`{`). A UTF-16 LE file starts with `FF FE` or with NUL bytes between the ASCII characters. Check with:

```powershell
Format-Hex -Path output.jsonl -Count 8
```

## Safe capture: write Docker's stdout bytes

Run this from the repository root in PowerShell. `subprocess` with `stdout=subprocess.PIPE` keeps the pipe bytes, and `write_bytes` stores them without re-encoding. stderr stays on the console, which is where `selector.py` logs.

```powershell
python -c @"
import subprocess
from pathlib import Path

sample = Path('sample').resolve()
command = [
    'docker', 'run', '--rm', '--platform', 'linux/amd64', '--network', 'none',
    '--cpus', '2', '--memory', '4096m', '--memory-swap', '4096m',
    '--pids-limit', '256', '--read-only',
    '--tmpfs', '/tmp:rw,size=64m,mode=1777',
    '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges',
    '--user', '65534:65534', '--env', 'HOME=/tmp',
    '--volume', (sample / 'commits').as_posix() + ':/data/commits:ro',
    '--volume', (sample / 'tests.json').as_posix() + ':/data/tests.json:ro',
    'blast-radius-entry',
]
completed = subprocess.run(command, stdout=subprocess.PIPE)
Path('output.jsonl').write_bytes(completed.stdout)
raise SystemExit(completed.returncode)
"@
```

Those flags are the `RUN_FLAGS` from the starter `Makefile`. On macOS or Linux, `make sample` already captures bytes correctly; use this PowerShell command only where `>` would re-encode.

`cmd /c` is also byte-preserving when the whole redirection stays inside `cmd.exe`:

```powershell
cmd /c "docker run --rm blast-radius-entry > output.jsonl"
```

Quote the volume paths if they contain spaces, and keep the `>` inside the `cmd /c` string. The Python capture above is the one to use for a full scorer-flags run.

Score the bytes with the starter CLI:

```powershell
python score.py sample sample/answers.json output.jsonl
```

Or, for several captures at once:

```powershell
python benchmark.py output.jsonl output-docker.jsonl
```

`benchmark.py` invokes that same `score.py` CLI. It prints `validation: accepted` or the rejection code (`not_utf8`, `not_json`, ...), the exact simulated time, the miss count, and the missed commit ids when `score.py` lists them. CPU time is unmeasured: `benchmark.py` does not time a container. The simulated seconds are still exact for that file.

## This checkout cannot score the starter outputs yet

`score.py` scores a file only when every `sample/commits/<id>.patch` has an entry in `sample/answers.json` and every answer has a patch. The answers file lists 30 commits. The upload contained 7 patches, placed here under the commit id (the upload suffix was only the flat-file rename):

| Upload | Placed at |
| --- | --- |
| `2haz_995f.patch` | `sample/commits/2haz.patch` |
| `2jrq_5a6d.patch` | `sample/commits/2jrq.patch` |
| `2qn5_9bdd.patch` | `sample/commits/2qn5.patch` |
| `2vdj_759f.patch` | `sample/commits/2vdj.patch` |
| `3utz_f345.patch` | `sample/commits/3utz.patch` |
| `4d63_a421.patch` | `sample/commits/4d63.patch` |
| `5y72_2b03.patch` | `sample/commits/5y72.patch` |

Missing patches: `535z`, `66ks`, `6vvp`, `6ze8`, `8y7j`, `986c`, `9u5k`, `ary6`, `bap8`, `bj7g`, `bvw2`, `egys`, `f4bn`, `f4xa`, `fryt`, `gbav`, `gbhk`, `j6uz`, `ktz3`, `mthh`, `nwnx`, `rszg`, `st8f`.

Until those 23 files exist, `score.py` exits 2 and does not print a simulated time. `benchmark.py` reports `validation: unscored` and the stderr line. `output-docker.jsonl` (LF) and `output-local.jsonl` (CRLF) are the starter captures; both are UTF-8. CRLF is accepted by the JSON parser because a trailing `\r` is whitespace. Neither file can be scored while the patch set is short.

`sample/answers.json` was copied unchanged. Do not trim it to the 7 patches and treat that as the starter score.

## Calling `schedule_tests` from the runtime

`analyzer/scheduler.py` exports:

```python
def schedule_tests(selected_numbers: set[int], tests: list[dict], max_workers: int = 8) -> list[list[int]]
```

`tests` is the list at `tests.json` under the key `tests`. Each item has `number`, `id`, `file`, and `seconds`. The function returns worker lists, or `[]` when there is nothing to run. It prints nothing, reads nothing, and does not open `answers.json`.

In `selector.py`, replace the fixed 3-worker `split` call. Suggested body:

```python
from analyzer.scheduler import schedule_tests

def main() -> None:
    tests = json.loads((DATA / "tests.json").read_text())["tests"]
    for patch in sorted((DATA / "commits").glob("*.patch")):
        # Swap this set for select_tests(...) when test selection is available.
        # Until then every test number is the safe fallback.
        selected = {test["number"] for test in tests}
        workers = schedule_tests(selected, tests)
        print(json.dumps({"commit": patch.stem, "workers": workers}))
```

Call `schedule_tests` once and reuse the lists when every commit has the same selected set. Repeating an identical pack does not change the simulated time and does spend CPU, which the board adds to the score. Per-commit selection has to call it once per distinct set.

`Dockerfile` today only copies `selector.py`. Copy the scheduler onto the image next to it, because `python /app/selector.py` puts `/app` on `sys.path`:

```dockerfile
COPY selector.py /app/selector.py
COPY analyzer/scheduler.py /app/analyzer/scheduler.py
CMD ["python", "/app/selector.py"]
```

No `analyzer/__init__.py` is required. The scheduler uses the standard library only, so `requirements.lock` stays as it is. Logs stay on stderr. Stdout stays one JSON object per commit and nothing else.

Unknown numbers are dropped, not raised. Pass only `number` values from `tests.json`. An empty selection becomes `"workers": []`, which scores as 0 for that commit and is a 300 second miss if a failing test was left out. That miss is the selector's decision; the scheduler does not add tests back.

The pack minimizes `max(load_ms) + 2000 * nonempty workers`. Eight is a cap, not the number of workers it will pick. A lower bound (the longer of the longest selected test and the total duration split across `k` workers, plus 2000 ms per worker) skips counts that cannot win. Each remaining count is packed longest-first. When that makespan is still above the bound, a first-fit search on the makespan and then local moves and swaps try to lower the objective. A repaired packing is kept only when the objective is strictly smaller. Ties keep the smaller worker count.

`schedule_tests_lpt` is the previous longest-first search, for comparison. The runtime should call `schedule_tests`. Identical catalog durations, selections, and worker caps are cached inside the process. On the full catalog the longest test is already the four-worker makespan, so the refined pack matches longest-first there. Uneven subsets are where the repair saves time.

Compare the starter 3-worker split (`selector.split` on `main`: longest `seconds` first, three workers), longest-first, and `schedule_tests` without reading `answers.json`:

```powershell
python benchmark.py --compare-schedulers --data-dir sample
```

It prints makespan, worker overhead, the objective, and scheduling CPU (`process_time` of the scheduler, not container CPU).
