# Adversarial review of test selection (reference d08cfc3)

Scope: find how a faster selector could miss failing tests. The review covers
the submitted selector at `d08cfc3` (`release/candidate-scheduler`) and the
read-only head of `experiment/precision-selector-v2` (`56d3bdc`). SQLGlot is pinned
at `eb11aec4be8cb2122afe713a4816662c3f0fb2fa`. Line references marked "sqlglot" point at that commit.

## Verdict

**Measured:**

* **The submitted balanced policy misses failing tests on targeted changes.** On 58 new scenarios (47 with failures):
  * balanced missed 5 commits (89 failing tests);
  * aggressive missed 6;
  * safe and fallback missed none.
* **The misses cost more than balanced saves.** Balanced saves 543 s of selection time over safe, but the 5 misses add 1500 s.
  * Totals including the 300 s per-miss penalty: balanced 3015.4 s, safe 2058.8 s.
* **Two small rules on this branch remove all 5 balanced misses.**
  * The fixed balanced policy scores 1623.2 s with 0 misses.
  * Its cost elsewhere: +0.0 s on the supplied sample (480.2 s) and +0.2 s on the earlier 63-mutant set (1535.0 s). Both sets still have 0 misses.
* **V2 (`56d3bdc`) behaves exactly like balanced on these scenarios.**
  * Its `precision` path only narrows patches whose positioned AST and comments are unchanged.
  * With the d08cfc3 maps it scored 3015.4 s with the same 5 misses.
  * It inherits every balanced gap.
* **Aggressive is not worth the risk.**
  * Its gain over balanced is 0 s on the sample and 31 s on 63 mutants (about 0.5 s per commit).
  * It still misses 2 commits after the fixes, because aggressive ignores structural edits.
  * Break-even is fewer than 1 miss per roughly 600 commits.

**Reasoning, not measured:**

* Hidden commits that change dialect generator or parser behaviour are a realistic trigger for risk R1.
* R1 is not limited to import-time errors. Any change that alters what a dialect produces in the tests that loop over every dialect fails a test the d08cfc3 tables never select.
* Zero misses on these scenarios does not guarantee zero misses on hidden commits.

## Method

* **Scenarios:** `tools/adversarial/make_scenarios.py` writes 58 scenarios (`scenarios.json`).
  * They are written from the pinned source only. No sample answer, selector output or test outcome was used to design them.
  * Each line is located by content. Behaviour flips use a fixed seed.
* **Ground truth:** `tools/selection_eval/run_mutants.py` runs the full SQLGlot suite (pytest-xdist, `SKIP_INTEGRATION=0`).
  * Failing means failed or not collected, minus baseline failures. The baseline has 0 failures in 1,621 tests.
  * Results: `results/ground_truth.json`. Patches: `patches/*.patch`.
  * **Determinism:** the 6 missed scenarios were re-run twice more with `-n 2` and `-n 5`. The failing sets were identical, except one extra lineage test in one import-08 run.
* **Policies:** `tools/adversarial/evaluate_scenarios.py` calls the same functions `selector.run` calls, with the image maps of the d08cfc3 build (`coverage.json`, `dependencies.json`).
  * `fallback` = `analyze(..., "safe")`; `safe` and `balanced` = `analyze_selective`.
  * `aggressive` is not exposed by `selector.py`. It is `analyze_selective(..., "aggressive")`: same hard signals and maps.
  * Scores come from `score.evaluate_run`. Run it in a checkout of this branch from the repo root; set `SELECTOR_ROOT` to the checkout of the revision you are scoring (d08cfc3, this branch or V2).
* **Check that offline equals the container:** on the supplied sample and the 63-mutant set, this offline path reproduces the simulated seconds measured in the `tr-rc-*` containers exactly:
  * sample: balanced 480.180 s, safe 910.822 s, fallback 1104.510 s;
  * mutants: balanced 1534.850 s, safe 2154.281 s, fallback 2319.471 s.
  * The adversarial scenarios themselves were scored offline only. Container CPU (about 0.4–0.5 s per run) is not included.

## Risk register (severity × likelihood)

| # | Risk | Evidence | Measured effect | Sev × Lik |
|---|---|---|---|---|
| R1 | Tests in **one dialect's test file** that loop over **every dialect** are never treated as all-dialect tests | tests: `tests/dialects/test_spark.py:1659` (`for dialect in Dialects`), `tests/dialects/test_druid.py:20`. Selector (d08cfc3): `analyzer/test_selector.py:1709` skips `tests/dialects/` before the all-dialect check at :1716, so `ALL_DIALECT_TEST_IDS` (:304) holds only 2 non-dialect tests | import-01, import-02, import-06 missed in balanced; import-09 missed in aggressive | High × High |
| R2 | A non-core module **mutates shared class state** at import time (parent dialect tables, core expression classes) | `parsers/redshift.py:38-39` and `generators/spark2.py:156-160` show class bodies that read parent tables in place; the mutating form is one line away. Athena uses Hive generation (`generators/athena.py:6`, `:154`), so a Hive table mutated by spark2 changes Athena output. Every xdist worker imports every test module at collection (`tests/test_lineage.py:6`), so module-level side effects reach the whole run, deterministically | dialect-02: 4 missed; import-08: 79 missed (lineage patching `exp.Column.output_name`) | High × Low–Med |
| R3 | **Aggressive skips structural fallbacks** | `analyzer/test_selector.py:1312` (d08cfc3): `if structural and not aggressive` | aggressive still misses dialect-02 and import-08 after the R2 fix | High × Med (aggressive only) |
| R4 | **File-level coverage is incomplete** as the sole selector | `tests/test_optimizer.py:111` (spawn pool), `:259` (`parse_and_optimize` in workers); 5 tests unobserved; coverage traced only in the main thread | Coverage alone would miss failing tests in 27 of 47 failing scenarios (1,267 tests; `results/coverage_only.json`), e.g. serde 23 of 36, `qualify_tables` decorator 502 of 727 | High × High for any coverage-only rule |
| R5 | Registry and lazy loading | sqlglot `dialects/dialect.py:214-309` (`_Dialect` metaclass, `_classes`), `dialects/__init__.py:104,124` (`importlib.import_module`), `optimizer/__init__.py:57,61` | Renamed dialect class (import-09) caught in balanced only by the structural def/class rule | Med × Low |
| R6 | By-name dialect use | sqlglot `parsers/presto.py:92-94` (`"teradata"`) | dialect-06 caught (static `DIALECT_BASES`) | Med × Low |
| R7 | Core edits | sqlglot `tokens.py`, `trie.py`, `serde.py`, `helper.py`, ... | All 16 core and 6 shared-utility scenarios fell back to ALL under every policy; 0 misses | High × Low (if core stays ALL) |
| R8 | Test helpers and fixtures | `tests/dialects/test_dialect.py:54` (`validate_identity` default), `tests/fixtures/optimizer/tpc-h/tpc-h.sql` | tests-02 (451 failing) caught via shared-test importers; tests-01 and tests-03 caught | Med × Med |
| R9 | Unobserved tests | `tests/test_typing.py:64-65` reads typing sources | coverage-09 caught by the explicit `typing/ -> test_typing` rule | Med × Low |

## Fixes on this branch

1. **Every-dialect tests (R1).** `derive_static_maps` now runs the every-dialect test before skipping dialect test files. The regenerated tables add 6 ids to `ALL_DIALECT_TEST_IDS`, including `test_spark::test_strip_modifiers` and `test_druid::test_druid`. The tables are regenerated with `--derive`; no hand-edited table, no commit ids.
2. **Shared class state (R2).** `_structural_reason` treats an added or removed line that assigns to, or mutates in place, an attribute of a named class as structural, which means the full suite. Examples: `Parent.TABLE.pop(...)`, `exp.Column.x = ...`, `generator.Generator.TYPE_MAPPING[k] = v`.
   * The pattern matches 0 lines in pinned SQLGlot, so editing existing code never triggers it.
   * Residual risk: mutation through an alias such as `funcs = Parent.FUNCTIONS; funcs.pop(...)`.
3. Regression tests were added; the unit suite is 147 tests, all passing.

### Measured scores (simulated s, including 300 s per missed commit)

| Revision | Policy | Adversarial (58) | Missed | Sample (30) | 63 mutants |
|---|---|---:|---|---:|---:|
| d08cfc3 | fallback | 2135.4 | none | 1104.5 | 2319.5 |
| d08cfc3 | safe | 2058.8 | none | 910.8 | 2154.3 |
| d08cfc3 | balanced | 3015.4 | dialect-02, import-01, import-02, import-06, import-08 | 480.2 | 1534.8 |
| d08cfc3 | aggressive | 3289.3 | + import-09 | 480.2 | 1503.7 |
| V2 56d3bdc | precision | 3015.4 | same 5 as balanced | n/a | n/a |
| this branch | balanced | **1623.2** | none | 480.2 | 1535.0 |
| this branch | aggressive | 2089.3 | dialect-02, import-08 | 480.2 | 1503.8 |

Selection time without penalties on the adversarial set:
* d08cfc3: balanced 1515.4 s, aggressive 1489.3 s.
* This branch: balanced 1623.2 s, so the fixes add +107.8 s over 58 commits.

V2 note: `56d3bdc` needs function-entry coverage that the d08cfc3 maps lack, so its `precision` path fell through to balanced in every case. Its narrowing applies only to AST-identical edits. On equiv-01 and equiv-02 no test failed, so it cannot miss there. It also cannot fix R1–R3.

## Scenario table (d08cfc3; "sel" = tests selected or ALL; bold = missed failing tests)

| Scenario | Category | File | Failing | safe sel / missed | balanced sel / missed | aggressive sel / missed |
|---|---|---|---:|---|---|---|
| core-01 | core | sqlglot/time.py | 36 | ALL / 0 | ALL / 0 | ALL / 0 |
| core-02 | core | sqlglot/trie.py | 297 | ALL / 0 | ALL / 0 | ALL / 0 |
| core-03 | core | sqlglot/serde.py | 36 | ALL / 0 | ALL / 0 | ALL / 0 |
| core-04 | core | sqlglot/errors.py | 11 | ALL / 0 | ALL / 0 | ALL / 0 |
| core-05 | core | sqlglot/transforms.py | 1 | ALL / 0 | ALL / 0 | ALL / 0 |
| core-06 | core | sqlglot/transforms.py | 3 | ALL / 0 | ALL / 0 | ALL / 0 |
| core-07 | core | sqlglot/jsonpath.py | 3 | ALL / 0 | ALL / 0 | ALL / 0 |
| core-08 | core | sqlglot/optimizer/qualify_columns.py | 8 | ALL / 0 | ALL / 0 | ALL / 0 |
| core-09 | core | sqlglot/optimizer/simplify.py | 0 | ALL / 0 | ALL / 0 | ALL / 0 |
| core-10 | core | sqlglot/optimizer/normalize_identifiers.py | 2 | ALL / 0 | ALL / 0 | ALL / 0 |
| core-11 | core | sqlglot/generator.py | 15 | ALL / 0 | ALL / 0 | ALL / 0 |
| core-12 | core | sqlglot/parser.py | 18 | ALL / 0 | ALL / 0 | ALL / 0 |
| core-13 | core | sqlglot/dialects/dialect.py | 11 | ALL / 0 | ALL / 0 | ALL / 0 |
| core-14 | core | sqlglot/dialects/dialect.py | 0 | ALL / 0 | ALL / 0 | ALL / 0 |
| core-15 | core | sqlglot/tokens.py | 338 | ALL / 0 | ALL / 0 | ALL / 0 |
| core-16 | core | sqlglot/tokens.py | 10 | ALL / 0 | ALL / 0 | ALL / 0 |
| coverage-01 | coverage_blind_spot | sqlglot/optimizer/pushdown_projections.py | 40 | ALL / 0 | 326 / 0 | 326 / 0 |
| coverage-02 | coverage_blind_spot | sqlglot/optimizer/merge_subqueries.py | 1 | ALL / 0 | 326 / 0 | 326 / 0 |
| coverage-03 | coverage_blind_spot | sqlglot/optimizer/unnest_subqueries.py | 0 | ALL / 0 | 326 / 0 | 326 / 0 |
| coverage-04 | coverage_blind_spot | sqlglot/optimizer/eliminate_ctes.py | 1 | ALL / 0 | 326 / 0 | 326 / 0 |
| coverage-05 | coverage_blind_spot | sqlglot/optimizer/canonicalize.py | 6 | ALL / 0 | ALL / 0 | ALL / 0 |
| coverage-06 | coverage_blind_spot | sqlglot/optimizer/annotate_types.py | 1 | ALL / 0 | ALL / 0 | ALL / 0 |
| coverage-07 | coverage_blind_spot | sqlglot/schema.py | 24 | ALL / 0 | ALL / 0 | ALL / 0 |
| coverage-08 | coverage_blind_spot | sqlglot/anonymize.py | 0 | ALL / 0 | 29 / 0 | 29 / 0 |
| coverage-09 | coverage_blind_spot | sqlglot/typing/presto.py | 1 | ALL / 0 | 1277 / 0 | 1277 / 0 |
| dialect-01 | cross_dialect | sqlglot/parsers/redshift.py | 0 | ALL / 0 | 830 / 0 | 830 / 0 |
| dialect-02 | cross_dialect | sqlglot/generators/spark2.py | 24 | ALL / 0 | 998 / **4** | 998 / **4** |
| dialect-03 | cross_dialect | sqlglot/dialects/postgres.py | 4 | ALL / 0 | 1024 / 0 | 1024 / 0 |
| dialect-04 | cross_dialect | sqlglot/dialects/mysql.py | 1 | ALL / 0 | 1308 / 0 | 1308 / 0 |
| dialect-05 | cross_dialect | sqlglot/generators/presto.py | 1 | ALL / 0 | 1276 / 0 | 1276 / 0 |
| dialect-06 | cross_dialect | sqlglot/dialects/teradata.py | 2 | ALL / 0 | 1276 / 0 | 1276 / 0 |
| dialect-07 | cross_dialect | sqlglot/dialects/hive.py | 4 | ALL / 0 | 1299 / 0 | 1299 / 0 |
| dialect-08 | cross_dialect | sqlglot/generators/exasol.py | 1 | ALL / 0 | 450 / 0 | 449 / 0 |
| dialect-09 | cross_dialect | sqlglot/generators/drill.py | 0 | ALL / 0 | 657 / 0 | 657 / 0 |
| dialect-10 | cross_dialect | sqlglot/generators/postgres.py | 2 | ALL / 0 | 1024 / 0 | 1024 / 0 |
| equiv-01 | ast_equivalent | sqlglot/helper.py | 0 | ALL / 0 | ALL / 0 | ALL / 0 |
| equiv-02 | ast_equivalent | sqlglot/generators/sqlite.py | 0 | ALL / 0 | 789 / 0 | 788 / 0 |
| global-01 | global_state | sqlglot/generators/exasol.py | 0 | ALL / 0 | 450 / 0 | 449 / 0 |
| import-01 | import_time | sqlglot/parsers/dremio.py | 37 | ALL / 0 | 246 / **2** | 246 / **2** |
| import-02 | import_time | sqlglot/generators/exasol.py | 46 | ALL / 0 | 450 / **1** | 449 / **2** |
| import-03 | import_time | sqlglot/optimizer/eliminate_joins.py | 149 | ALL / 0 | 326 / 0 | 326 / 0 |
| import-04 | import_time | sqlglot/planner.py | 60 | ALL / 0 | 60 / 0 | 60 / 0 |
| import-05 | import_time | sqlglot/lineage.py | 53 | ALL / 0 | 83 / 0 | 83 / 0 |
| import-06 | import_time | sqlglot/generators/drill.py | 36 | ALL / 0 | 657 / **2** | 657 / **2** |
| import-07 | import_time | sqlglot/optimizer/qualify_tables.py | 727 | ALL / 0 | 786 / 0 | 786 / 0 |
| import-08 | global_state | sqlglot/lineage.py | 123 | ALL / 0 | 83 / **79** | 83 / **79** |
| import-09 | import_time | sqlglot/dialects/dremio.py | 37 | ALL / 0 | ALL / 0 | 246 / **2** |
| shared-01 | shared_utility | sqlglot/helper.py | 1384 | ALL / 0 | ALL / 0 | ALL / 0 |
| shared-02 | shared_utility | sqlglot/helper.py | 11 | ALL / 0 | ALL / 0 | ALL / 0 |
| shared-03 | shared_utility | sqlglot/helper.py | 187 | ALL / 0 | ALL / 0 | ALL / 0 |
| shared-04 | signature | sqlglot/optimizer/qualify.py | 21 | ALL / 0 | 786 / 0 | 786 / 0 |
| shared-05 | signature | sqlglot/optimizer/eliminate_joins.py | 0 | ALL / 0 | 326 / 0 | 326 / 0 |
| shared-06 | global_state | sqlglot/__init__.py | 223 | ALL / 0 | ALL / 0 | ALL / 0 |
| shared-07 | shared_utility | sqlglot/executor/env.py | 1 | ALL / 0 | 60 / 0 | 60 / 0 |
| tests-01 | test_or_fixture | tests/fixtures/identity.sql | 2 | 33 / 0 | 2 / 0 | 2 / 0 |
| tests-02 | test_or_fixture | tests/dialects/test_dialect.py | 451 | ALL / 0 | 1020 / 0 | 1020 / 0 |
| tests-03 | test_or_fixture | tests/fixtures/optimizer/tpc-h/tpc-h.sql | 2 | 227 / 0 | 61 / 0 | 61 / 0 |
| tests-04 | test_or_fixture | tests/fixtures/pretty.sql | 0 | 28 / 0 | 1 / 0 | 1 / 0 |

| Policy | Simulated s (incl. 300 s per missed commit) | Missed commits |
|---|---:|---|
| fallback | 2135.4 | none |
| safe | 2058.8 | none |
| balanced | 3015.4 | dialect-02, import-01, import-02, import-06, import-08 |
| aggressive | 3289.3 | dialect-02, import-01, import-02, import-06, import-08, import-09 |

## Reproducible counterexamples (d08cfc3, balanced)

| Scenario | Patch | Missed tests |
|---|---|---|
| import-01 | `tools/adversarial/patches/import-01.patch` (class-body `AttributeError` in `parsers/dremio.py`) | `test_druid::test_druid`, `test_spark::test_strip_modifiers` |
| import-02 | `patches/import-02.patch` (`generators/exasol.py`) | `test_druid::test_druid` |
| import-06 | `patches/import-06.patch` (syntax error, `generators/drill.py`) | `test_druid::test_druid`, `test_spark::test_strip_modifiers` |
| dialect-02 | `patches/dialect-02.patch` (spark2 mutates `HiveGenerator.TYPE_MAPPING`) | 4 `test_athena` tests |
| import-08 | `patches/import-08.patch` (`lineage.py` patches `exp.Column.output_name`) | 79 tests in optimizer, executor and dialect files |

To reproduce, run from a checkout of this branch:

```
# ground truth for one scenario (pinned SQLGlot worktree, test venv)
python3 -c "import json;d=json.load(open('tools/adversarial/scenarios.json'));d['mutants']=[m for m in d['mutants'] if m['id']=='import-01'];json.dump(d,open('/tmp/one.json','w'))"
python3 tools/selection_eval/run_mutants.py SQLGLOT_WORKTREE /tmp/one.json sample/tests.json /tmp/gt --python VENV/bin/python -n 4
# score all policies of a revision against recorded ground truth
SELECTOR_ROOT=/path/to/checkout-at-d08cfc3 python3 tools/adversarial/evaluate_scenarios.py GT_DIR MAPS_DIR /tmp/out
python3 tools/adversarial/render_table.py /tmp/out/results.json
```

`GT_DIR` holds the `<id>.json` and `<id>.patch` files written by `run_mutants.py`; one directory or a comma-separated list. `MAPS_DIR` holds the image's `coverage.json` and `dependencies.json`, e.g. copied from `/app/maps` of the image.

## Recommendations for the V2 author

**Keep:**
* full-suite fallback for core and derived-core modules, for registry modules, and for structural edits (imports, `__all__`, removed or renamed def/class);
* hard signals (dynamic loading, unknown modules, malformed patches);
* dialect descendants plus `DIALECT_BASES` by-name links;
* the `typing/ -> test_typing` rule, shared-test importers and fixture consumers;
* coverage used only to **add** tests.

**Add:**
* the two fixes above;
* the structural rule also in aggressive;
* a full-suite fallback for any module-level or class-body statement that is not a plain assignment to a name defined in that class or module;
* a rule that any edit to a dialect module selects every test iterating `Dialects` or `Dialect.classes`, wherever it lives.

**Do not:**
* let file- or function-level coverage replace static selection: it misses 27 of 47 failing scenarios here;
* trust the coverage map for code run in spawned workers (`tests/test_optimizer.py:111`).

**Aggressive:** keep it off. It needs:
* structural fallbacks;
* no dropping of dialect test-id mentions;
* core coverage that covers subprocesses;
* and even then it must show fewer than 1 miss per roughly 600 commits to pay for itself.

**Measurement gate before adopting any faster policy:**
* 0 missed commits on the sample, on the 63 mutants and on these 58 adversarial scenarios;
* every scenario that would be narrowed must be compared with safe;
* report selection time and misses separately;
* any new rule that narrows selection needs its own new adversarial scenarios, written from source before they are scored.

## Break-even

* A miss costs 300 s.
* Balanced versus fallback on the sample: (1104.5 - 480.2) / 30 = 20.8 s per commit, so one miss per 14 commits erases it.
* Aggressive versus balanced: 0 s per commit on the sample and 31 s / 63 = 0.5 s per commit on the mutants, so one miss per roughly 600 commits erases it.

## Limitations

* The scenarios are synthetic and one-line.
* Ground truth uses the full suite under pytest-xdist; hidden-commit ground truth may come from a different runner.
* Adversarial scores are offline simulated seconds without container CPU.
* V2 was run with d08cfc3 maps (no function coverage).
* 11 scenarios produced no failing test, so they cannot reveal misses.
