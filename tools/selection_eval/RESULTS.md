# Selection evaluation results (2026-11-21)

Measured with `evaluate.py` on the 30 sample commits and on 63 seeded mutants
(`make_mutants.py`, seed 20261120) whose ground truth came from running the
real sqlglot suite at the pinned commit (`run_mutants.py`, SKIP_INTEGRATION=0).
`impacted_files` was simulated as the changed paths only. "sim-file" and
"sim-line" are a **simulation** of the analysis engine's coverage input built
from a pytest-cov per-test run (`build_sim_coverage.py`); "none" means no
coverage was passed. Simulated seconds use the SCORING.md arithmetic with an
LPT scheduler over 1..8 workers; container CPU time is not included. Selector
CPU is `time.process_time()` around each call on the evaluation machine.

Mutant strata (count, how many broke at least one test): core 10/6,
base dialects 16/7, other dialects 10/7, optimizer and other modules 10/7,
fixtures 6/6, multi-module 4/4, added files 3/1, renames 2/2, deletes 2/2.

## Current selector

| dataset | coverage | mode | commits | simulated s | missed commits | missed failing tests | full-suite fallbacks | % tests (count) | % tests (seconds) | selector CPU ms mean / max |
|---|---|---|---|---|---|---|---|---|---|---|
| sample | - | all tests (LPT) | 30 | 1104.510 | 0 | 0 | 30 | 100.0 | 100.0 | - |
| sample | none | safe | 30 | 910.822 | 0 | 0 | 22 | 76.1 | 78.5 | 0.12 / 2.19 |
| sample | none | balanced | 30 | 480.177 | 0 | 0 | 6 | 41.1 | 32.0 | 0.06 / 0.13 |
| sample | none | aggressive | 30 | 480.177 | 0 | 0 | 6 | 41.1 | 32.0 | 0.06 / 0.16 |
| sample | sim-file | safe | 30 | 571.541 | 0 | 0 | 6 | 44.5 | 38.1 | 0.12 / 0.63 |
| sample | sim-file | balanced | 30 | 480.180 | 0 | 0 | 6 | 41.1 | 32.0 | 0.09 / 0.38 |
| sample | sim-file | aggressive | 30 | 983.607 | 2 | 3 | 1 | 28.7 | 20.0 | 0.09 / 0.46 |
| sample | sim-line | safe | 30 | 571.541 | 0 | 0 | 6 | 44.5 | 38.1 | 0.08 / 0.27 |
| sample | sim-line | balanced | 30 | 480.180 | 0 | 0 | 6 | 41.1 | 32.0 | 0.07 / 0.19 |
| sample | sim-line | aggressive | 30 | 1249.972 | 3 | 17 | 0 | 25.2 | 16.1 | 0.08 / 0.34 |
| mutants | - | all tests (LPT) | 63 | 2319.471 | 0 | 0 | 63 | 100.0 | 100.0 | - |
| mutants | none | safe | 63 | 2154.281 | 0 | 0 | 56 | 89.9 | 91.2 | 0.05 / 0.16 |
| mutants | none | balanced | 63 | 1514.586 | 0 | 0 | 23 | 60.7 | 56.1 | 0.07 / 0.26 |
| mutants | none | aggressive | 63 | 1493.860 | 0 | 0 | 21 | 57.9 | 54.2 | 0.07 / 0.22 |
| mutants | sim-file | safe | 63 | 1648.682 | 0 | 0 | 28 | 66.5 | 62.4 | 0.12 / 0.50 |
| mutants | sim-file | balanced | 63 | 1525.062 | 0 | 0 | 23 | 60.7 | 56.4 | 0.11 / 0.37 |
| mutants | sim-file | aggressive | 63 | 1154.587 | 0 | 0 | 4 | 38.0 | 34.8 | 0.11 / 0.43 |
| mutants | sim-line | safe | 63 | 1638.215 | 0 | 0 | 28 | 66.5 | 62.0 | 0.09 / 0.45 |
| mutants | sim-line | balanced | 63 | 1514.595 | 0 | 0 | 23 | 60.7 | 56.1 | 0.10 / 0.73 |
| mutants | sim-line | aggressive | 63 | 1624.191 | 2 | 14 | 3 | 34.0 | 30.2 | 0.10 / 0.44 |

## Previous selector revision (effe42a), same harness

| dataset | coverage | mode | commits | simulated s | missed commits | missed failing tests | full-suite fallbacks | % tests (count) | % tests (seconds) | selector CPU ms mean / max |
|---|---|---|---|---|---|---|---|---|---|---|
| sample | - | all tests (LPT) | 30 | 1104.510 | 0 | 0 | 30 | 100.0 | 100.0 | - |
| sample | none | safe | 30 | 910.822 | 0 | 0 | 22 | 76.1 | 78.5 | 1.31 / 1.54 |
| sample | none | balanced | 30 | 456.046 | 0 | 0 | 5 | 39.8 | 29.3 | 1.30 / 1.70 |
| sample | none | aggressive | 30 | 456.046 | 0 | 0 | 5 | 39.8 | 29.3 | 1.32 / 1.58 |
| sample | sim-file | safe | 30 | 547.410 | 0 | 0 | 5 | 43.1 | 35.5 | 1.40 / 1.89 |
| sample | sim-file | balanced | 30 | 456.049 | 0 | 0 | 5 | 39.8 | 29.3 | 1.41 / 1.88 |
| sample | sim-file | aggressive | 30 | 993.207 | 2 | 3 | 1 | 30.6 | 20.5 | 1.34 / 1.71 |
| sample | sim-line | safe | 30 | 547.410 | 0 | 0 | 5 | 43.1 | 35.5 | 1.30 / 1.59 |
| sample | sim-line | balanced | 30 | 456.049 | 0 | 0 | 5 | 39.8 | 29.3 | 1.32 / 1.49 |
| sample | sim-line | aggressive | 30 | 1259.572 | 3 | 17 | 0 | 27.1 | 16.7 | 1.30 / 1.44 |
| mutants | - | all tests (LPT) | 63 | 2319.471 | 0 | 0 | 63 | 100.0 | 100.0 | - |
| mutants | none | safe | 63 | 2154.281 | 0 | 0 | 56 | 89.9 | 91.2 | 1.29 / 1.57 |
| mutants | none | balanced | 63 | 1339.819 | 0 | 0 | 22 | 59.4 | 47.9 | 1.30 / 1.50 |
| mutants | none | aggressive | 63 | 1300.367 | 0 | 0 | 18 | 54.2 | 44.5 | 1.34 / 1.90 |
| mutants | sim-file | safe | 63 | 1448.222 | 0 | 0 | 24 | 61.6 | 52.4 | 1.80 / 17.75 |
| mutants | sim-file | balanced | 63 | 1350.298 | 0 | 0 | 22 | 59.5 | 48.2 | 1.31 / 1.56 |
| mutants | sim-file | aggressive | 63 | 1054.046 | 0 | 0 | 5 | 38.9 | 29.5 | 1.31 / 1.71 |
| mutants | sim-line | safe | 63 | 1437.752 | 0 | 0 | 24 | 61.6 | 52.1 | 1.29 / 1.69 |
| mutants | sim-line | balanced | 63 | 1339.828 | 0 | 0 | 22 | 59.5 | 47.9 | 1.31 / 1.89 |
| mutants | sim-line | aggressive | 63 | 1827.910 | 3 | 27 | 4 | 35.1 | 24.6 | 1.28 / 1.55 |

## Reading the numbers

* safe and balanced missed nothing on either set, with or without simulated
  coverage. aggressive with simulated coverage missed commits whose failures
  run in spawned processes (serde pickling) or depend on class-body lines that
  coverage attributes to an arbitrary test; it should not be the default.
* The current revision selects more than the previous one on base dialects
  (hive, mysql, presto, tsql families): T-SQL is used inside functions of the
  base generator and is now core, and presto/athena are imported by
  `optimizer/pushdown_predicates.py` on every call, so their dependants'
  optimizer and executor tests are included. Neither set contained a mutant
  that the previous revision missed because of these dependencies; the extra
  cost is the price of not relying on that.
* Per-call selector CPU dropped from about 1.3 ms to under 0.15 ms by
  indexing tests.json once per tests list.
* None of this guarantees zero misses on hidden commits.
