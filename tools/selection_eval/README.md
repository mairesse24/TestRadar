# Selection evaluation tools

Offline tooling for measuring `analyzer/test_selector.py`. Nothing here is
imported by `analyzer/` or used at run time, and the runtime selector never
reads `sample/answers.json` or the ground truth produced here.

| File | Purpose |
| --- | --- |
| `evaluate.py` | Runs the selector over the sample commits and/or mutants, per mode and coverage input; prints simulated seconds (SCORING.md arithmetic, LPT over 1..8 workers), misses, full-suite fallbacks, share of tests selected and selector CPU time. |
| `make_mutants.py` | Generates seeded, systematic mutants of the pinned sqlglot checkout across strata (core, base dialects, other dialects, optimizer/other, fixtures, multi-module, new files, renames, deletes). |
| `run_mutants.py` | Applies each mutant in a git worktree, records `git diff -M` as the patch and runs the real sqlglot suite to obtain the failing tests (ground truth). |
| `outcome_plugin.py` | pytest plugin used by `run_mutants.py`: a test fails if any phase or subtest fails; tests that are not collected count as failing. |
| `build_sim_coverage.py` | Turns a pytest-cov `--cov-context=test` database into a per-test line map, used as a **simulation** of the coverage input the analysis engine may provide. |

## Reproducing

```sh
# 1. pinned sqlglot and an evaluation venv (never part of the image)
git clone https://github.com/tobymao/sqlglot.git sqlglot-src
git -C sqlglot-src checkout eb11aec4be8cb2122afe713a4816662c3f0fb2fa
git -C sqlglot-src worktree add ../sqlglot-mut HEAD
python3 -m venv eval-venv
eval-venv/bin/pip install -r requirements.lock pytest-cov

# 2. mutants and ground truth (about 1 minute per mutant with -n 6)
python3 tools/selection_eval/make_mutants.py sqlglot-src out/mutants          # seed 20261120
python3 tools/selection_eval/run_mutants.py sqlglot-mut out/mutants/mutants.json \
    sample/tests.json out/mutants/results --python eval-venv/bin/python -n 6

# 3. optional coverage simulation
(cd sqlglot-src && PATH=../eval-venv/bin:$PATH SKIP_INTEGRATION=0 python -m pytest -n 4 \
    --cov=sqlglot --cov-context=test --cov-report= tests)
eval-venv/bin/python tools/selection_eval/build_sim_coverage.py sqlglot-src/.coverage sqlglot-src out/simcov.json

# 4. evaluate
python3 tools/selection_eval/evaluate.py --sample sample --mutants out/mutants/results \
    --coverage out/simcov.json --details out/details.json
```

## Caveats

* The mutants are small single-token edits (comparison/boolean/constant/
  keyword-string flips) plus a few structural cases; they approximate, but are
  not, the hidden commits.
* `impacted_files` is simulated as just the changed paths: no dependency graph.
* The coverage simulation misses lines executed in spawned subprocesses
  (`test_optimizer` uses a spawn `ProcessPoolExecutor`) and attributes
  module/class-body lines to whichever test happened to import the module
  first. A real coverage map has the same hazards, which is why the selector
  only ever *adds* coverage hits except in aggressive mode.
* Zero misses on these sets does not guarantee zero misses on hidden commits.
