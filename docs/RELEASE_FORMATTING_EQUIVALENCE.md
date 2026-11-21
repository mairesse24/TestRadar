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

## Validation (Docker entrypoint, scorer flags, 3 runs each)

Image `tr-rc-formatting` was built from 49f6bcc **without build arguments**:

```
DOCKER_BUILDKIT=0 docker build --no-cache --platform linux/amd64 \
  --cpuset-cpus 0-3 --memory 8g --memory-swap 8g -t tr-rc-formatting .
```

* Build time: 141 s.
* Image size: 128 MB (8.1 MB of it is the pinned source).
* Maps are byte-identical to the validated ones (coverage be7997b2d436,
  dependencies e075eb7024a4).
* `docker inspect` shows `TESTRADAR_POLICY=balanced` and
  `TESTRADAR_EXPERIMENTS=equivalent`.
* Every run produced identical output.

The comparison image is `tr-final-balanced` (1955e89).

| Set | Final s | Misses | CPU s | 1955e89 s | Misses | CPU s |
|---|---:|---:|---|---:|---:|---|
| 30 samples | 401.155 | 0 | 0.71–0.77 | 480.207 | 0 | 0.41–0.42 |
| 63 mutants | 1534.953 | 0 | 1.07–1.08 | 1534.953 | 0 | 0.44–0.48 |
| 58 adversarial | 1573.743 | 0 | 1.56 | 1623.246 | 0 | 0.43–0.45 |
| 10 structural | 368.170 | 0 | 0.50–0.51 | 368.170 | 0 | 0.36–0.37 |
| 11 performance | 194.429 | 0 | 1.46–1.47 | 295.517 | 0 | 0.37–0.43 |
| 9 audit | 147.268 | 0 | 0.91–0.98 | 331.353 | 0 | 0.37–0.38 |

Where time was saved, in seconds:

* Samples (−79.052):
  * bap8 36.817
  * 9u5k 12.686
  * f4xa 11.186
  * 986c 9.750
  * bvw2 8.613
* Adversarial (−49.503): equiv-01 36.817 and equiv-02 12.686.

Applying the sample ratio to the official hidden-set score gives about
631.4 × 401.155 / 480.207 ≈ 527 s simulated. CPU rises by about 0.3–1.1 s
per run, because both versions of each modified module are parsed.

## Residual risks

* The hidden set may contain fewer formatting-only edits than the samples;
  then the saving shrinks, but the selection is never smaller than the
  release's for any other edit.
* Theoretical channels with no test in the pinned suite:
  * line-number-dependent warning de-duplication;
  * code-object equality or hashing;
  * traceback text.

  A hidden commit that also edits a test file is still handled, because
  changed test files are selected normally.
* The rule assumes the tests run on the same Python minor version as the
  image (3.13).
* Labels are from local pytest runs, not the official harness.
