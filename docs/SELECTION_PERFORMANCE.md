# Selection performance investigation

Baseline: `release/candidate-adversarial-fixes` at 1955e89 (balanced policy).
Official hidden-set score for that image: 632.1 s (631.366 s simulated, about
0.73 s CPU, no misses). All numbers below are simulated seconds from
`score.py` rules (busiest worker + 2 s per non-empty worker, 300 s per missed
commit) with the validated pinned maps (coverage be7997b2d436, dependencies
e075eb7024a4). Offline numbers were checked against the Docker entrypoint and
match exactly.

## 1. Where the 480.2 sample seconds go (1955e89)

| Group | Commits | Seconds | Failing-tests bound | Failing-files bound |
|---|---:|---:|---:|---:|
| Full-suite fallbacks (core modules) | 6 | 220.902 | 33.814 | 52.666 |
| Optimizer / executor selections | 3 | 80.362 | 25.454 | 27.454 |
| Dialect selections | 13 | 161.043 | 17.183 | 75.499 |
| Fixture-only (one test each) | 8 | 17.900 | 17.900 | 100.848 |
| Total | 30 | 480.207 | 94.351 | 256.467 |

"Failing-tests bound" schedules only the tests that really fail; the
"failing-files bound" schedules every test in the files containing them.
Neither is achievable without knowing the answers; they show the ceiling.

Scheduling is already optimal. For every commit in every set the scheduler's
cost equals the lower bound min over k of max(longest test, total/k) + 2k
(gap 0.000 s on samples, mutants, the 58 and 10 scenario sets). The full suite
costs 36.817 s because `test_parser::test_max_nodes` alone takes 28.82 s
(4 workers). All savings must come from selection.

Per commit (1955e89 base vs the candidate below, sorted by base cost):

| Commit | Selected base→cand | Base s | Candidate s | Saved s | Failing | Failing-tests bound s | Failing-files bound s | Top files (base) | Base reason |
|---|---:|---:|---:|---:|---:|---:|---:|---|---|
| 2haz | ALL→ALL | 36.817 | 36.817 | 0.000 | 0 | 0.000 | 0.000 | test_executor.py 31.57, test_parser.py 29.14 | core module sqlglot/dialects/dialect.py |
| 3utz | ALL→ALL | 36.817 | 36.817 | 0.000 | 37 | 25.454 | 27.454 | test_executor.py 31.57, test_parser.py 29.14 | core module sqlglot/serde.py |
| 535z | ALL→ALL | 36.817 | 36.817 | 0.000 | 1 | 2.596 | 12.606 | test_executor.py 31.57, test_parser.py 29.14 | core module sqlglot/dialects/tsql.py (used inside functions  |
| bap8 | ALL→0 | 36.817 | 0.000 | 36.817 | 0 | 0.000 | 0.000 | test_executor.py 31.57, test_parser.py 29.14 | core module sqlglot/transforms.py |
| ktz3 | ALL→ALL | 36.817 | 36.817 | 0.000 | 0 | 0.000 | 0.000 | test_executor.py 31.57, test_parser.py 29.14 | core module sqlglot/expressions/ddl.py |
| rszg | ALL→ALL | 36.817 | 36.817 | 0.000 | 3 | 5.764 | 12.606 | test_executor.py 31.57, test_parser.py 29.14 | core module sqlglot/optimizer/scope.py |
| 2jrq | 326→326 | 27.454 | 27.454 | 0.000 | 8 | 25.454 | 27.454 | test_executor.py 31.57, test_optimizer.py 19.82 | sqlglot/optimizer/eliminate_joins.py: source module; used by |
| 5y72 | 326→326 | 27.454 | 27.454 | 0.000 | 0 | 0.000 | 0.000 | test_executor.py 31.57, test_optimizer.py 19.82 | sqlglot/optimizer/eliminate_subqueries.py: source module; us |
| 66ks | 60→60 | 25.454 | 25.454 | 0.000 | 0 | 0.000 | 0.000 | test_executor.py 31.57 | sqlglot/executor/context.py: source module; used by sqlglot/ |
| ary6 | 996→994 | 14.311 | 12.081 | 2.230 | 10 | 4.382 | 32.817 | test_optimizer.py 7.87, test_generator.py 6.72 | sqlglot/parsers/bigquery.py: source module, dialects bigquer |
| j6uz | 996→996 | 14.311 | 14.311 | 0.000 | 2 | 4.399 | 12.920 | test_optimizer.py 7.87, test_generator.py 6.72 | sqlglot/parsers/bigquery.py: source module, dialects bigquer |
| 986c | 1030→145 | 13.618 | 3.868 | 9.750 | 1 | 2.329 | 12.606 | test_generator.py 6.69, test_optimizer.py 5.58 | sqlglot/dialects/singlestore.py: source module, dialects sin |
| 4d63 | 1027→1027 | 13.502 | 13.502 | 0.000 | 0 | 0.000 | 0.000 | test_generator.py 6.69, test_optimizer.py 5.26 | sqlglot/dialects/snowflake.py: source module, dialects snowf |
| 2qn5 | 939→937 | 12.895 | 10.665 | 2.230 | 0 | 0.000 | 0.000 | test_generator.py 6.69, test_optimizer.py 4.38 | sqlglot/generators/clickhouse.py: source module, dialects cl |
| 8y7j | 939→937 | 12.895 | 10.665 | 2.230 | 0 | 0.000 | 0.000 | test_generator.py 6.69, test_optimizer.py 4.38 | sqlglot/generators/clickhouse.py: source module, dialects cl |
| bj7g | 939→937 | 12.895 | 10.665 | 2.230 | 1 | 2.011 | 2.515 | test_generator.py 6.69, test_optimizer.py 4.38 | sqlglot/generators/clickhouse.py: source module, dialects cl |
| 9u5k | 790→0 | 12.686 | 0.000 | 12.686 | 0 | 0.000 | 0.000 | test_generator.py 6.69, test_dialect.py 1.82 | sqlglot/generators/sqlite.py: source module, dialects sqlite |
| f4xa | 659→0 | 11.186 | 0.000 | 11.186 | 0 | 0.000 | 0.000 | test_generator.py 6.69, test_dialect.py 1.82 | sqlglot/generators/drill.py: source module, dialects drill |
| 6ze8 | 248→248 | 10.686 | 10.686 | 0.000 | 1 | 2.001 | 2.035 | test_generator.py 6.69, test_dialect.py 1.82 | sqlglot/parsers/dremio.py: source module, dialects dremio |
| bvw2 | 228→3 | 10.686 | 2.073 | 8.613 | 1 | 2.061 | 12.606 | test_generator.py 6.69, test_dialect.py 1.82 | sqlglot/dialects/materialize.py: source module, dialects mat |
| fryt | 248→248 | 10.686 | 10.686 | 0.000 | 0 | 0.000 | 0.000 | test_generator.py 6.69, test_dialect.py 1.82 | sqlglot/parsers/dremio.py: source module, dialects dremio |
| gbav | 521→521 | 10.686 | 10.686 | 0.000 | 0 | 0.000 | 0.000 | test_generator.py 6.69, test_dialect.py 1.82 | sqlglot/parsers/singlestore.py: source module, dialects sing |
| mthh | 1→1 | 3.016 | 3.016 | 0.000 | 1 | 3.016 | 12.606 | test_optimizer.py 1.02 | tests/fixtures/optimizer/merge_subqueries.sql: fixture consu |
| egys | 1→1 | 2.596 | 2.596 | 0.000 | 1 | 2.596 | 12.606 | test_optimizer.py 0.6 | tests/fixtures/optimizer/simplify.sql: fixture consumers |
| nwnx | 1→1 | 2.092 | 2.092 | 0.000 | 1 | 2.092 | 12.606 | test_optimizer.py 0.09 | tests/fixtures/optimizer/pushdown_projections.sql: fixture c |
| 2vdj | 1→1 | 2.083 | 2.083 | 0.000 | 1 | 2.083 | 12.606 | test_optimizer.py 0.08 | tests/fixtures/optimizer/pushdown_predicates.sql: fixture co |
| gbhk | 1→1 | 2.061 | 2.061 | 0.000 | 1 | 2.061 | 12.606 | test_optimizer.py 0.06 | tests/fixtures/optimizer/annotate_types.sql: fixture consume |
| 6vvp | 1→1 | 2.037 | 2.037 | 0.000 | 1 | 2.037 | 12.606 | test_optimizer.py 0.04 | tests/fixtures/optimizer/qualify_columns__invalid.sql: fixtu |
| st8f | 1→1 | 2.008 | 2.008 | 0.000 | 1 | 2.008 | 12.606 | test_optimizer.py 0.01 | tests/fixtures/optimizer/isolate_table_selects.sql: fixture  |
| f4bn | 1→1 | 2.007 | 2.007 | 0.000 | 1 | 2.007 | 12.606 | test_optimizer.py 0.01 | tests/fixtures/optimizer/pushdown_cte_alias_columns.sql: fix |

## 2. Ranked opportunities (sample seconds)

1. Full-suite fallbacks, 220.9 s. Edits to core modules (`dialect.py`,
   `serde.py`, `transforms.py`, `expressions/ddl.py`, `optimizer/scope.py`,
   and `dialects/tsql.py`, which `generator.py` imports inside functions).
   Narrowing a semantic core edit needs per-test runtime evidence (function
   entry or block coverage), which V2/V3 showed to be incomplete (collection,
   subprocesses, threads, the 120 s checkpoint). Not attempted. Formatting-only
   core edits are a different case: they are provably inert. One sample commit
   (bap8, 36.8 s) is like that.
2. Dialect selections, 161.0 s. About 6.7 s of each is
   `test_generator::test_generator_memory`, which resolves every dialect and
   constructs its generator. Four commits (9u5k, f4xa, and the dialect files in
   986c and bvw2) are formatting-only. Dropping the dialect-mention tests in
   `test_optimizer` would save at most 9.7 s more. That rule cannot be
   justified from source (mentions do not separate reading from writing a
   dialect), so it was not implemented.
3. Optimizer / executor, 80.4 s. `test_executor::test_execute_tpcds`
   (21.45 s) runs whenever the executor or optimizer is reachable, and two of
   the three commits really fail tests in that file. Nothing safe to drop.
4. Fixture-only, 17.9 s. Already at the failing-tests bound.

## 3. Earlier experiments (read-only)

* V2, `experiment/precision-selector-v2` (56d3bdc). This branch was a
  profiling and opt-in `precision` policy. Function-entry observations were
  recorded with `sys.monitoring` at build time (1,200 of 1,620 tests
  observed). Narrowing applied only to line-preserving edits whose positioned
  AST and comments were unchanged.
  * Measured: samples 480.180 s, the same as balanced; the 63 mutants
    1524.455 s, the same as balanced. CPU rose to 1.56–2.98 s, and the build
    took 223 s.
  * Conclusion: no gain, and it keeps the submitted policy.
* V3, `experiment/regression-selection-v3` (2f02703, on d08cfc3). This branch
  added a `blocks` policy with a 1.8 MB checked-in function-entry map. A change
  qualifies when every hunk is an executed function body. It then keeps only
  the tests that entered the function, plus the tests the trace never
  observed.
  * No measurements were committed. I measured it offline with the same maps:

    | Set | Seconds | Missed commits |
    |---|---:|---:|
    | Samples | 422.085 | 0 |
    | Mutants | 1447.656 | 0 |
    | 58 scenarios | 2844.884 | 5 (88 tests) |
    | 10 structural scenarios | 2800.050 | 9 (263 tests) |

  * The scenario misses are the d08cfc3 base gaps that 1955e89 fixed. Its
    narrowing rests on trace completeness, the known weak point, so it is not
    a safe basis.
* No V3.1 branch exists on the remote.

## 4. Experiments on this branch (opt-in, `TESTRADAR_EXPERIMENTS`)

Both rules rebuild each modified `sqlglot/*.py` file exactly from the pinned
source and the patch. The image now carries the pinned package under
`/app/pinned`. Rebuilt files matched `git apply` on 154 of 154 changed files
across all sets. Any mismatch, parse error, rename or missing source returns
"unknown", and the release behaviour applies.

* `equivalent` (experiment a): if a file's syntax tree is unchanged, the edit
  touched only comments, blank lines or line breaks. CPython compiles
  identical code; line numbers are not part of `co_code`, `co_consts` or
  `co_names`, the only code attributes the suite compares (`test_typing`). The
  only tests that read source parse it with `ast` (`test_typing`). The file
  contributes no tests and no hard signals; a commit of only such files runs
  nothing. Docstring changes are real syntax-tree changes and do not qualify.
* `construction` (experiment b): `test_generator_memory` and
  `test_time::test_canonical_time_format_vocabulary` only resolve dialects,
  construct generators and read class-level tables. They are dropped from a
  dialect selection only when:
  * everything outside function bodies is unchanged (imports, class bodies,
    signatures, decorators, defaults); and
  * no edited function is dunder or decorated, used in a call at module or
    class level, or reachable by name from the core constructors
    (`Dialect`/`Generator`/`Parser`/`Tokenizer` `__init__`, `__new__`,
    `get_or_raise`, `generator`, `_build_dispatch`, ...).

  No dialect is dropped: inheritance and mention rules are untouched.
* Cost-aware selection and scheduling (experiment c): the scheduler gap is 0
  in every set, so there is nothing to gain there. Test-level narrowing beyond
  (a) and (b) needs runtime evidence; it was not implemented (see section 2).

New targeted scenarios (`tools/selection_eval/performance_scenarios.json`):
11 mutants with pytest labels, run twice on clean worktrees with identical
labels.
* Comment-only core edit, import reformat in a typing module, and condition
  reformat.
* Controls that must not count as equivalent: `2` → `2.0`, and a docstring
  edit.
* Import-time factory that raises (1,113 failing tests).
* Generator `__init__` that leaks per construction: only
  `test_generator_memory` fails, and the rule keeps it.
* Body-only edits that break or mutate a dialect, or leak while generating.

| Experiment | Samples | 63 mutants | 58 scenarios | 10 structural | 11 new | Misses |
|---|---:|---:|---:|---:|---:|---:|
| 1955e89 balanced | 480.207 | 1534.953 | 1623.246 | 368.170 | 295.517 | 0 |
| (a) equivalent | 401.155 | 1534.953 | 1573.743 | 368.170 | 194.429 | 0 |
| (b) construction | 471.287 | 1518.430 | 1623.246 | 368.170 | 291.585 | 0 |
| (a)+(b) | 392.235 | 1518.430 | 1573.743 | 368.170 | 190.497 | 0 |

Where time was saved with (a)+(b), in seconds:

* Samples (−87.972):

  | Commit | Saved | Why |
  |---|---:|---|
  | bap8 | 36.817 | formatting-only `transforms.py` |
  | 9u5k | 12.686 | formatting only |
  | f4xa | 11.186 | formatting only |
  | 986c | 9.750 | formatting-only dialect files, fixture kept |
  | bvw2 | 8.613 | same |
  | ary6, 2qn5, 8y7j, bj7g | 2.230 each | construction-only tests dropped |

* Mutants (−16.523):

  | Commit | Saved |
  |---|---:|
  | other_dialect-09 | 6.558 |
  | other_dialect-06 | 5.034 |
  | base_dialect-05 | 1.702 |
  | base_dialect-08 | 1.673 |
  | base_dialect-13 | 1.556 |

* 58 scenarios (−49.503): equiv-01 36.817 and equiv-02 12.686.

## 5. Docker validation of (a)+(b)

Image `tr-perf-eqcon`, built from ff5a467 with
`--build-arg TESTRADAR_EXPERIMENTS=equivalent,construction`, in 137 s (limits
4 CPUs and 8 GB). It is 128 MB (the pinned source adds about 8 MB), and its
maps are byte-identical to the validated ones. `run_container.py` used the
scorer flags, 3 runs per set, with identical output in each run:

| Set | Simulated s | Missed | Container CPU s (3 runs) |
|---|---:|---:|---|
| Samples | 392.235 | 0 | 1.019 / 1.002 / 1.134 |
| 63 mutants | 1518.430 | 0 | 1.515 / 1.505 / 1.491 |
| 58 scenarios | 1573.743 | 0 | 2.504 / 2.262 / 2.287 |
| 10 structural | 368.170 | 0 | 0.742 / 0.717 / 0.724 |
| 11 new | 190.497 | 0 | 1.903 / 1.890 / 1.867 |

CPU rises by about 0.6–1.9 s per run (parsing both versions of large modules)
against 87.97 s of simulated savings on the samples.

## 6. Is about 200 s reachable?

* Hidden / sample ratio for 1955e89: 631.366 / 480.207 = 1.315. Under that
  ratio, 200 s on the hidden set is about 152 s on the samples.
* The candidate is at 392.2 s: 392.235 × 1.315 ≈ 516 s hidden, if the hidden
  mix resembles the samples.
* A perfect file-level oracle is at 256.5 s, or about 174 s when fixtures stay
  test-level. Both are still above 152 s.
* Only a near test-level oracle (94.4 s) gets there.
* Narrowing all five remaining core fallbacks to their failing-file bound
  would leave about 261 s, or about 343 s hidden.

About 200 s therefore needs test-level precision on semantic core and dialect
edits. The only known evidence for that is runtime tracing, which has the
known completeness holes. It is not reachable safely with the evidence
available.
