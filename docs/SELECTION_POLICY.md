# Selection policies

`selector.py` runs one of three policies. The image bakes the choice in at
build time, because the scorer passes no arguments:

```sh
docker build --build-arg TESTRADAR_POLICY=balanced --tag blast-radius-entry .
```

The Dockerfile builds `balanced` unless told otherwise; the measurements
below are the reason. `selector.py` run without `TESTRADAR_POLICY` (outside
the image) uses `fallback`.

| Policy | Image default | Behaviour |
| --- | --- | --- |
| `fallback` | no | The validated integration behaviour. Any uncertainty from the patch parser, the dependency graph or the coverage map forces the full suite. The coverage map always reports uncertainty, and the pinned graph is globally uncertain, so every commit runs every test. |
| `safe` | no | Selective. Hard signals (below) force the full suite. A source change is narrowed only with complete coverage evidence; the pinned build leaves a few tests unobserved, so in practice source changes fall back and only test, fixture and documentation changes are narrowed. |
| `balanced` | yes | Selective. Same hard signals. Source changes are narrowed with the selector's pinned static maps plus the graph's impact set; coverage hits only add tests. |

Locally, `python selector.py --policy balanced` overrides the environment.
An unknown `TESTRADAR_POLICY` value falls back to `fallback`.

## Two kinds of uncertainty

The integrated pipeline produces three uncertainty signals: `patch`,
`dependencies` and `coverage`. They mix two different situations.

### Hard signals: evidence is missing, corrupt or unsupported

The selective policies force the full suite whenever any of these holds
(`selector.hard_signals`):

* The patch cannot be read or decoded, parses to no file changes, or
  any file change is marked uncertain by `parse_patch`. This covers bad hunk
  headers, truncated hunks, binary diffs, unsafe or ambiguous paths and
  unknown statuses.
* `dependencies.json` or `coverage.json` is missing, unreadable, has
  the wrong schema or was built from another SQLGlot commit
  (`DependencyGraph.load` / `CoverageMap.load` fail).
* A pre-existing `sqlglot/` or `tests/` Python file named by the patch is
  absent from the pinned dependency graph. The artifact and the patch then
  disagree about the source tree.
* A modified Python file has an added or removed import statement at any
  indentation, or a line using `__import__`, `importlib`, `import_module`,
  `exec(` or `eval(`. Such a line can add a dependency that no map built
  from the pinned source describes.
* Analysis raises for any reason: that commit gets the full suite and a
  `analysis error` line on stderr.

The selector adds its own conservative fallbacks on top of these:

* unknown paths and build or config files
* new, deleted, renamed or copied source modules (a new module referenced
  by nothing is the one exception in balanced mode)
* core modules and anything that reaches them
* modules with no known tests
* broad changes (more than 12 files or 600 changed lines)
* module-level import, `__all__` or removed-definition edits
* fixtures without a known consumer
* test infrastructure (`conftest.py`, `helpers.py`, `__init__.py`)

### Known limitations of usable evidence: soft signals

These do not force a fallback in `safe` or `balanced`, because the
evidence is valid for the pinned source and the selector's pinned maps
already model the gap:

* **The graph's global `uncertain` flag.** `tools/audit_dependencies.py`
  attributes it to one optional unresolved module (`sqlglot._version`) and
  to dynamic loading in ten modules. The flag is a property of the pinned
  source, not of a commit, so it is identical for every commit. Each site is
  handled as follows:

  | Dynamic site | How the selector handles it |
  | --- | --- |
  | `sqlglot/__init__.py`, `sqlglot/dialects/__init__.py`, `sqlglot/optimizer/__init__.py` | Registry modules: lazy class and attribute tables. Editing them runs everything. Their tables are resolved into the selector's import maps. |
  | `sqlglot/dialects/dialect.py` | Loads dialects by name. It is core, so editing it runs everything. Dialect tests are found through name mentions in the pinned tests. |
  | `sqlglot/serde.py` | Imports classes named in serialized data. Core: editing it runs everything. |
  | `sqlglot/executor/python.py`, `sqlglot/executor/context.py` | Run generated code. Executor changes select `tests/test_executor.py`, and optimizer changes select it too. |
  | `tests/test_dialect_imports.py`, `tests/test_typing.py`, `tests/test_executor.py`, `tests/test_docs.py` | Tests that load modules dynamically. Dialect changes add `test_dialect_imports.py`, typing changes add `test_typing.py`, and executor or optimizer changes add `test_executor.py`. `test_docs.py` is not in `tests.json`. |

* **The graph covers Python files only.** Fixtures, docs and config are
  never in it, so `is_uncertain` flags every such path. The selector
  classifies those paths itself. Fixtures go through their pinned
  consumers, an unknown fixture falls back, docs select nothing, and
  config and unknown paths fall back.
* **The graph over-approximates through registry modules.** A module
  imports its parent package, and `optimizer/__init__.py` names the
  optimizer only under `TYPE_CHECKING`. The reverse closure therefore
  reaches `sqlglot/__init__.py` and with it every file, so the impact set
  stops expanding at registry modules. Everything `import sqlglot` really
  executes is in the selector's derived core set (now including
  `sqlglot/diff.py`), and changing a core file runs the full suite.
* **Coverage cannot prove exclusion** (`CoverageMap.is_uncertain` is always
  true). In `safe` and `balanced`, coverage is only a source of extra
  candidates. A test with no coverage hit is never treated as unaffected.
  Tracing misses subprocesses (for example the optimizer's spawn pool),
  native code and module-level lines executed during collection. In
  `safe`, a coverage map with unobserved tests counts as no coverage.

## Assumptions behind `balanced`

1. The commit is a diff against the pinned SQLGlot commit that the maps were
   derived from (`analyzer/contracts.PINNED_SQLGLOT`).
2. A function-body edit in a non-core module can only break tests that
   reach that module. Reaching means static imports, function-level imports,
   registry tables, by-name dialect use, the module's naming convention, or
   the pinned tests that mention its dialect.
3. A dialect's behaviour reaches tests through the dialect family files, the
   dialects built on it, and tests that name it.
4. Import-time breakage is limited to edits the structural rules catch,
   to core modules and to the modules `import sqlglot` executes.

## Residual risks

* An edit inside a function can still raise at import time when the
  function runs at module level, as a decorator, class-body call or default
  argument. A syntax error in a non-core module also breaks only its
  importers. Both stay outside the static rules: the patch carries hunks
  only, and the pinned source is not in the runtime image.
* Tests that reach a module through a dynamic path no pinned map
  describes can be missed. Examples: a dialect obtained from a variable
  name, or behaviour exercised only inside a subprocess.
* The coverage artifact is built against a 120-second budget. On a slower
  build machine more tests stay unobserved. That cannot cause a miss in
  `balanced`, because coverage only adds, but it changes which extra tests
  are selected.
* The sample set (30 commits) and the seeded mutant set (63) are small.
  Zero misses on them is not a guarantee for hidden commits.

## Measured results (2026-11-21)

Each policy was built as its own image from this branch, with
`--build-arg TESTRADAR_POLICY=...`. The builds used the legacy builder
limited to 4 CPUs and 8 GB, and took about 125-130 s. All three images
carry byte-identical maps and code.

Every image ran 3 times under the Makefile's `RUN_FLAGS`, using
`tools/selection_eval/run_container.py`. CPU time is the container
cgroup's `usage_usec` (user + system for every process in the container).
The total is score.py's simulated time plus CPU, and the table reports the
median of the 3 totals, as on the board. Simulated time and misses were
identical across the 3 runs.

The mutant set is the 63 seeded mutants from `tools/selection_eval`,
seed 20261120. Their ground truth comes from running the real pinned SQLGlot
suite. The container read each mutant as `/data/commits/<id>.patch`
with the sample `tests.json`, so the results below come from the real
parser, graph and coverage map, not from simplified inputs.

| Set | Policy | Simulated s | Missed commits / tests | Full-suite fallbacks | Tests run (count / seconds) | CPU s (median) | Total s (median) | score.py |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| sample (30) | validated 8244dff image | 1104.510 | 0 / 0 | 30 | 100% / 100% | 0.447 | 1105.0 | accepted |
| sample (30) | fallback | 1104.510 | 0 / 0 | 30 | 100% / 100% | 0.364 | 1104.9 | accepted |
| sample (30) | safe | 910.822 | 0 / 0 | 22 | 76.1% / 78.5% | 0.397 | 911.2 | accepted |
| sample (30) | balanced | 480.180 | 0 / 0 | 6 | 41.1% / 32.0% | 0.418 | 480.6 | accepted |
| mutants (63) | validated 8244dff image | 2319.471 | 0 / 0 | 63 | 100% / 100% | 0.520 | 2320.0 | accepted |
| mutants (63) | fallback | 2319.471 | 0 / 0 | 63 | 100% / 100% | 0.410 | 2319.9 | accepted |
| mutants (63) | safe | 2154.281 | 0 / 0 | 56 | 89.9% / 91.2% | 0.416 | 2154.7 | accepted |
| mutants (63) | balanced | 1534.850 | 0 / 0 | 24 | 62.2% / 57.2% | 0.484 | 1535.3 | accepted |

42 of the 63 mutants break at least one test. Balanced has 24 fallbacks on
the mutant set:

* core modules: 19
* a dialect-like module with no dialect tests (`sqlglot/generators/python.py`): 1
* deleted or renamed source modules: 3
* a new function-level import of a newly added module: 1

The CPU figures come from one 8-core Linux box with each container capped at
2 CPUs. They are not the board's numbers.
