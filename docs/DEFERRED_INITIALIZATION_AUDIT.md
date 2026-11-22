# Deferred initialization callback audit

The experiment starts at `9263a714c74851838831bc6d7690f297f3ea5587`.
The protected release is `eacab514997a41693d5cf5792ec3f6bd7e87b843`.
The replay used SQLGlot `eb11aec4be8cb2122afe713a4816662c3f0fb2fa`.

Keep the broad guard. The exception needs a closed account of who may
execute or inspect the callback during initialization. A local comparison
can prove that the factory's eager statements, nested function header,
and closure bindings match. Once the factory returns a function and a
class publishes it in a registry, that comparison cannot establish the
execution timing of registry consumers.

For the Dremio patch, the outer factory bytecode and the callback's free
variables match across versions. The callback bytecode differs and the
body alters parsing. The audited selector still selects all 1,620 tests
instead of the release's 248. Restoring that row alone would save 26.131
simulated seconds on the supplied sample. Its empty failure labels give
no evidence about the ability of either selection to detect callback
regressions.

Six executable fixtures in `tests/test_deferred_initialization_audit.py`
retain the broad guard for direct invocation, an unchanged eager decorator,
a callee that invokes a returned callback, implicit metaclass registry
invocation, importer inspection and changed closure bindings. All have
identical projected ASTs after masking the edited callback body. These
fixtures encode behavior of synthetic programs and use no evaluation data.

## Reproduce

Run the fixtures via
`python -B -m unittest discover -s tests -p test_deferred_initialization_audit.py -v`.
For selections, follow the [common-input replay](INITIALIZATION_DEPENDENCY_GUARD.md)
using the same mount paths, map-builder image, catalog, and three trials per
revision. This branch can use the base guard's `equivalent,initialization`
flag. The selector, analyzer and Dockerfile are identical to the base guard.
The 63 scripts can be regenerated offline with
`python -B tools/selection_eval/make_mutants.py /sqlglot OUT --seed 20261120`.
Their original labels remain unavailable.

| Replay input | SHA-256 |
| --- | --- |
| Tests | `c18345fd53b3c95f735fb0e0dd7422b447f81861a6162e65d7acb683345555f4` |
| Dependencies | `e075eb7024a42415c1c9157460dfc4c327b0726ac6a54050e884d3942d217459` |
| Coverage | `1f77a6efa05c0b64b9ca13a7c5b7bbf115302afa9045028e643e43ce1e4eae53` |
| DuckDB patch | `384f2822db4ae1969f8fe78e1991259f076d46d3db3af0930dd2965746e2e008` |
| Dremio patch, normalized to repository LF | `a782289f77511867834a0544c287ed6f13e3d7a326eba8c49685fb11a6a7ec98` |

The guard replay again selected 1,620 tests for the DuckDB patch, including
all three independently verified failing tests. The release selected
1,117 and omitted all three. The labeled sample stayed at 401.155 seconds
on the release and 427.286 seconds on the guard with zero misses on both.

The Linux unit suite ran 174 tests with 172 passes, two skips and zero
failures.

| Dataset | Release simulated / CPU / total (s) | Guard simulated / CPU / total (s) | Missed commits / failing IDs |
| --- | --- | --- | --- |
| 30 sample | 401.155 / 0.939 / 402.094 | 427.286 / 0.962 / 428.248 | 0 / 0 on both |
| 11 performance, targeted labels | 442.454 / 1.853 / 444.307 | 181.970 / 2.675 / 184.645 | 1 / 3 to 0 / 0 |
| 10 structural | 368.170 / 1.248 / 369.418 | 368.170 / 0.618 / 368.788 | 0 / 0 on both |
| 58 adversarial | 1573.743 / 2.440 / 1576.183 | 1573.743 / 7.538 / 1581.281 | 0 / 0 on both |
| 16 seed | 464.623 / 1.474 / 466.097 | 464.623 / 1.105 / 465.728 | 0 / 0 on both |
| 9 formatting, packing only | 147.268 / 1.405 / 148.673 | 147.268 / 1.075 / 148.343 | Unverified |
| 63 regenerated, packing only | 1524.558 / 2.215 / 1526.773 | 1524.558 / 1.431 / 1525.989 | Unverified |

CPU is the median of three local measurements. Identical selections on the
formatting and regenerated sets cannot add misses relative to the release
under the same labels.
