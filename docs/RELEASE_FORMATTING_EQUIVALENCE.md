# Release candidate: formatting equivalence

Branch `release/candidate-formatting-equivalence`, built from the
adversarial-fixes release candidate (1955e89) with the edit-scope code of
`experiment/selection-performance` (ff5a467) ported and hardened.

Image default: `TESTRADAR_POLICY=balanced`, `TESTRADAR_EXPERIMENTS=equivalent`.
Construction narrowing is implemented but **disabled**.

## The rule

The selector rebuilds each modified `sqlglot/*.py` file exactly from the
pinned source shipped in the image (`/app/pinned`) and the patch. The file
then contributes no tests and no hard signals when all of the following
hold:

* both versions decode as plain UTF-8 (no coding cookie, no BOM);
* both versions parse;
* the path appears once in the patch;
* the syntax trees (`ast.dump` without positions) are identical.

A commit made only of such files runs nothing. Anything else, including any
doubt, keeps the previous release selection. Non-`.py` files, `.pyi` stubs,
test files and files outside `sqlglot/` are never eligible.

## Audit (CPython 3.13, pinned sqlglot and its tests)

Evidence: `rg` over `sqlglot/` and `tests/` at the pinned commit, plus a
runtime probe (`compile`/`exec` of paired sources). Verdicts:

| Risk | Evidence | Verdict |
|---|---|---|
| Line numbers in assertions | No test uses `lineno`, `co_firstlineno`, `traceback`, `format_exc`, `getsource*`, `linecache`, `.records`. `assertLogs` output is `LEVEL:logger:message`, and there is no custom `Formatter` or `%(lineno)` anywhere. | Not observable |
| Warnings | One `warnings.warn` (tokens.py, import time, message only); no test uses `assertWarns`/`catch_warnings`. The warnings registry de-duplicates per line, so joining two identical `warn` calls with `;` could merge them; sqlglot has no such pair. | Not observable (theoretical only) |
| Tracebacks | Line numbers change (probe), but nothing in sqlglot or the tests formats or compares Python tracebacks; SQL errors report SQL positions. | Not observable |
| Code objects | `co_code`, `co_consts`, `co_names` are identical, but `code ==` and `hash(code)` differ when lines shift (probe). Neither sqlglot nor the tests compare or hash code objects; `test_typing._same_code` compares only `co_code/co_names/co_varnames/co_consts`. `typing.overload` keys by `co_firstlineno` internally (consistent within a run). | Not observable |
| `inspect` | sqlglot uses `getmembers` (sorted by name), `signature`, `getfullargspec`: no source access. No `getsource` anywhere. | Not observable |
| Semantic comments | `type: ignore`, `noqa`, `pragma`, `fmt`: tooling only, and no test runs mypy, ruff or coverage pragmas. The shebang is irrelevant for imported modules. **Coding cookies are semantic**: probe and scenarios aud-cookie-01/02 (1600 and 277 failing tests) show a cookie changes decoding or breaks import while the text tree is unchanged. | **Real risk: fixed** (both versions must decode as UTF-8; BOM rejected) |
| Docstrings / `__doc__` | Docstrings are syntax-tree nodes, so a docstring edit is never equivalent (probe). `expressions/core.py` copies `__doc__`. `test_docs.py` (doctest) is not in `tests.json`. | Excluded by construction |
| Strings | Implicit concatenation and backslash continuations produce identical constants (probe). An f-string `=` debug text is part of the tree, so a whitespace change is detected (probe). With `from __future__ import annotations`, the annotation strings come from the tree, not the source (probe). | Not observable / detected |
| Tests reading source | `test_typing` reads `sqlglot/typing/*.py` but only through `ast.parse` (aud-typing-01 and perf-eq-02: 0 failing). `test_sqlglotc` runs `sqlglotc/setup.py`, which lists file names only. Fixtures are non-`.py`. | Not observable |
| Non-`.py` files | `classify` accepts only `sqlglot/**.py` with status `modified`; mode changes, renames, binaries and uncertain hunks are rejected. | Enforced |
| Python version | The selector parses with the image's Python 3.13, the same runtime the Dockerfile matches. | Residual if the test runtime differed |
| Skipping every test | With identical bytecode, constants, names and definition order, test outcomes can differ only through line metadata or decoding (above). Sample answers contain commits with 0 failing tests, so baseline failures are not labelled. A cheap sanity test could not detect any of the remaining theoretical channels and would cost at least 2 s per such commit. | Run nothing |

## Construction narrowing: disabled

It drops `test_generator_memory` and `test_canonical_time_format_vocabulary`
for function-body-only dialect edits. Its safety rests on a by-name
reachability approximation over five core modules:

* Dynamic dispatch (`getattr(cls, name)` with computed names, as in
  `_build_dispatch`) is invisible to it.
* Metaclass or `__init_subclass__` hooks outside those modules are not
  scanned.
* Memory behaviour of construction can depend on class state that a method
  mutates at generation time.

The scenarios passed, but the gain is small (8.9 s on samples, 16.5 s on
mutants, 0 on the adversarial sets). The rule stays implemented and opt-in
only.

## Scenarios

`tools/selection_eval/formatting_audit_scenarios.json`: 9 mutants, each
labelled twice with real pytest runs on a clean worktree, with identical
labels.

* Coding cookies (ascii, unknown codec, latin-1).
* BOM.
* Semicolon join.
* Trailing newline removed at end of file.
* Line shifts in `parser.py` and before the `tokens.py` warning.
* Comment in a typing module read by `test_typing`.

The ff5a467 rule missed aud-cookie-01 (1600 tests) and aud-cookie-02 (277
tests). The hardened rule misses none.

`performance_scenarios.json` perf-eq-01 now uses a different comment text,
relabelled (0 failing).
