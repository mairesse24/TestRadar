# Release candidate: adversarial selection fixes

* **Base:** `d08cfc3`, the submitted release.
* **SQLGlot:** pinned `eb11aec4be8cb2122afe713a4816662c3f0fb2fa`.
* **Default policy:** `balanced` (unchanged).

## Changes

1. **Every-dialect tests.**
   * The static-table derivation now records a test that loops over `Dialects` or `Dialect.classes` before it skips dialect test files.
   * As a result, `ALL_DIALECT_TEST_IDS` now includes `test_spark::test_strip_modifiers`, `test_druid::test_druid`, three `test_dialect` tests and `test_bigquery::test_week`.
2. **Shared class state.** An added or removed line that assigns to, or mutates in place, an attribute of a named class is structural, which means the full suite. Examples: `Parent.TABLE.pop(...)`, `exp.Column.x = ...`, `generator.Generator.TYPE_MAPPING[k] = v`.
3. **Indirect shared-state edits (found by the false-negative review below).** Each of the following is structural:
   * `del Class.attr...`
   * `setattr` or `delattr`
   * in-place methods (including `__setitem__` and `__delitem__`) on any class attribute
   * unbound `dict.update(Table, ...)` and similar
   * subscript assignment to an upper-case table
   * a bare alias of another class's table (`ALIAS = Parent.TYPE_MAPPING`)
   * mutation of a class table through `self` or `cls`
   * a bare call at module level (code run at import)
   * a class named after a different dialect, which re-registers that name in the `Dialect` metaclass registry
   * These rules only add full-suite fallbacks. On pinned SQLGlot they match 22 existing lines. Of those, 14 are in core modules, which already fall back. The other 8 are dialect lines (`COERCES_TO[...] |=` and the existing `MySQL.TIME_MAPPING`/`INVERSE_TIME_*` aliases), so editing them now runs the full suite.
4. **Build-time tables.** The image build re-derives the generated tables from the pinned checkout (`test_selector.py --refresh`).
   * The image uses the derived copy.
   * The build log reports whether it equals the checked-in block.
   * If derivation fails, the checked-in tables are kept.
   * Build result: "derived tables identical to checked-in tables". The image's `analyzer/test_selector.py` is byte-identical to the repository file.

## False-negative review of rule 2 (real pytest labels)

The ten scenarios below are in `tools/selection_eval/structural_scenarios.json` (run_mutants ops plus failing tests). Each one fails real tests.

| Scenario | Edit | Failing | d08cfc3 balanced | rules 1–2 only | this branch |
|---|---|---:|---|---|---|
| fn-alias-01 | `_HIVE_TYPES = HiveGenerator.TYPE_MAPPING`, then `_HIVE_TYPES[...] = ...` (spark2) | 24 | missed | missed (4 tests) | full suite |
| fn-del-01 | `del HiveGenerator.TYPE_MAPPING[...]` | 24 | missed | missed | full suite |
| fn-setattr-01 | `setattr(exp.Column, "output_name", ...)` (lineage) | 123 | missed | missed (79) | full suite |
| fn-helper-01 | new function patches `column.output_name`, called at module level | 123 | missed | missed (79) | full suite |
| fn-lowerattr-01 | `exp.Column.arg_types.pop("catalog")` | 88 | missed | missed (82) | full suite |
| fn-dunder-01 | `HiveGenerator.TYPE_MAPPING.__setitem__(...)` | 24 | missed | missed | full suite |
| fn-dictupdate-01 | `dict.update(HiveGenerator.TYPE_MAPPING, ...)` | 24 | missed | missed | full suite |
| fn-self-01 | `self.TYPE_MAPPING[...] = ...` in a Spark2 method | 16 | caught | caught | full suite |
| fn-registry-01 | `class Druid(Dialect)` added to dremio.py | 3 | missed | missed (1) | full suite |
| fn-augassign-01 | `HiveGenerator.TYPE_MAPPING \|= {...}` | 24 | missed | caught | full suite |

The image with only rules 1 and 2 (`ee8d5a9`) missed 8 of these 10 commits (257 tests) through the Docker entrypoint. This branch misses none.

## Docker results (competition entrypoint, Makefile run flags, 3 runs)

Images:
* `tr-final-*`: this branch.
* `tr-rc-balanced`: `d08cfc3`.

All images use the same in-image coverage checkpoint (`coverage.json` sha256 `be7997b2d436…`: 170 files, 5 unobserved tests) and the same `dependencies.json` (`e075eb7024a4…`). Simulated seconds include 300 s per missed commit. Missed tests are failing tests that were not selected.

| Set | Image | valid | simulated s (3 runs) | missed commits | missed tests | full-suite fallbacks | tests selected | CPU s median (runs) | total s median |
|---|---|---|---|---|---:|---:|---:|---|---:|
| sample | tr-final-balanced | True | 480.207/480.207/480.207 | 0  | 0 | 6/30 | 20000 | 0.422 (0.429/0.382/0.422) | 480.6 |
| sample | tr-final-fallback | True | 1104.510/1104.510/1104.510 | 0  | 0 | 30/30 | 48600 | 0.361 (0.418/0.356/0.361) | 1104.9 |
| sample | tr-rc-balanced | True | 480.180/480.180/480.180 | 0  | 0 | 6/30 | 19983 | 0.382 (0.382/0.385/0.380) | 480.6 |
| mutants | tr-final-balanced | True | 1534.953/1534.953/1534.953 | 0  | 0 | 24/63 | 63557 | 0.429 (0.429/0.441/0.420) | 1535.4 |
| mutants | tr-final-fallback | True | 2319.471/2319.471/2319.471 | 0  | 0 | 63/63 | 102060 | 0.379 (0.373/0.392/0.379) | 2319.8 |
| mutants | tr-rc-balanced | True | 1534.850/1534.850/1534.850 | 0  | 0 | 24/63 | 63528 | 0.421 (0.421/0.489/0.419) | 1535.3 |
| adv58 | tr-final-balanced | True | 1623.246/1623.246/1623.246 | 0  | 0 | 29/58 | 63573 | 0.419 (0.419/0.422/0.410) | 1623.7 |
| adv58 | tr-final-fallback | True | 2135.386/2135.386/2135.386 | 0  | 0 | 58/58 | 93960 | 0.365 (0.363/0.365/0.370) | 2135.8 |
| adv58 | tr-rc-balanced | True | 3015.418/3015.418/3015.418 | 5 ['dialect-02', 'import-01', 'import-02', 'import-06', 'import-08'] | 88 | 25/58 | 59438 | 0.425 (0.425/0.473/0.417) | 3015.8 |
| fn10 | tr-final-balanced | True | 368.170/368.170/368.170 | 0  | 0 | 10/10 | 16200 | 0.383 (0.410/0.383/0.353) | 368.6 |
| fn10 | tr-final-fallback | True | 368.170/368.170/368.170 | 0  | 0 | 10/10 | 16200 | 0.360 (0.360/0.404/0.360) | 368.5 |
| fn10 | tr-rc-balanced | True | 2800.050/2800.050/2800.050 | 9 ['fn-alias-01', 'fn-augassign-01', 'fn-del-01', 'fn-dictupdate-01', 'fn-dunder-01', 'fn-helper-01', 'fn-lowerattr-01', 'fn-registry-01', 'fn-setattr-01'] | 263 | 0/10 | 6483 | 0.384 (0.365/0.384/0.449) | 2800.4 |

Scheduling invariants (`check_output.py`, balanced outputs): no violations, at most 4 workers per commit.

Labels:
* **58 scenarios:** labels were re-derived by re-running the full SQLGlot suite for every scenario; all 58 failing sets are identical to the stored labels.
* **Ten new scenarios:** labelled by real pytest runs.
* **30 samples and 63 mutants:** the provided labels.

## Resources

* **Build:** `DOCKER_BUILDKIT=0 docker build --no-cache --platform linux/amd64 --cpuset-cpus 0-3 --memory 8g --memory-swap 8g -t IMAGE .` took 136 s, within the 10 min, 4 CPU, 8 GB limits.
* **Image:** 120.6 MB.
* **Run:** `--network none --cpus 2 --memory 4096m --memory-swap 4096m --pids-limit 256 --read-only --tmpfs /tmp:rw,size=64m,mode=1777 --cap-drop ALL --security-opt no-new-privileges --user 65534:65534`. Every run exited 0 well inside 3 minutes.
* **Container CPU:** about 0.36–0.43 s median per run.
* **Selection and packing CPU inside the image (balanced):**
  * sample: 0.012 s selection, 0.026 s packing;
  * mutants: 0.026 s and 0.044 s;
  * 58 scenarios: 0.036 s and 0.040 s.
* **Repository:** about 2 MB without `.git`.

## Remaining risks (reasoning, not measured)

* **Aliases the rules do not see:**
  * mutation through a function-local alias (`m = self.TYPE_MAPPING; m[k] = v`);
  * `.pop`/`.update` on an *existing* class-body alias (e.g. `TIME_MAPPING` in presto, which aliases MySQL's);
  * tables whose names lack the listed suffixes.
* **Shared mutable defaults and caches** mutated only at run time inside functions.
* **Coverage blind spots** (spawned optimizer workers, 5 unobserved tests): balanced only adds coverage hits, so coverage gaps reduce selection but do not cause misses unless the static rules also miss.
* **Hidden commits** may combine effects the synthetic one-line scenarios do not cover. Zero misses here is not a guarantee.
