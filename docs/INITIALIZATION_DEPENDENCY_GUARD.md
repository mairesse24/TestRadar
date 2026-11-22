# Initialization-time dependency guard

The parent revision is TestRadar `eacab514997a41693d5cf5792ec3f6bd7e87b843`.
The SQLGlot checkout is `eb11aec4be8cb2122afe713a4816662c3f0fb2fa`.

The protected selector uses a reverse import graph with registry boundaries.
A dialect-local generator edit can fail during class construction, before
the resulting transform can be dispatched. For example, DuckDB registers
the result of calling `_date_delta_to_binary_interval_op` when creating
`DuckDBGenerator.TRANSFORMS`. Dremio similarly invokes
`build_date_delta_with_cast_interval` to populate its parser registry.
Other pinned examples are DuckDB's `array_append_sql` transform factory
and BigQuery's `_build_format_time` and
`_build_extract_json_with_default_path` parser factories.

The experimental image enables `equivalent,initialization`. Reconstruct
each eligible edit from pinned source, compare old and new function bodies
and headers, and walk names reachable from eager module or class evaluation
in both versions. Calls, bare decorators, local aliases and transitive
references can supply positive evidence. On a hit the selector requests the
full suite, because registry consumers can include dynamic and fixture-driven
dialect calls and work in child processes. No new rule removes tests.

The walk is local to the changed module. It can overapproximate references
with the same short name and factories that return deferred callbacks. It
does not resolve arbitrary cross-module calls, dynamic name lookup, or
implicit metaclass and descriptor calls. Absence of a hit does not certify
that an edit is safe. Other analysis and fallback rules still apply.

## Common-input validation

Both selectors ran in `testradar-integration:map-builder` (image ID
`sha256:2720cc74c41e8366c86a4c4559d91e62664dd5156e4abb4d11e5ec7fe22cc775`)
with networking disabled, two CPUs, 4 GB, 256 process limit, a read-only
container filesystem, and a 64 MB temporary filesystem. A separate 96 MB
scratch mount held the pinned checkout for offline patch reconstruction.
This was a same-image selector overlay, with build-stage maps and the
candidate source mounted read-only. It was run as the builder user.

Use `balanced`, `equivalent` for the baseline and
`equivalent,initialization` for the candidate, and set `TESTRADAR_PINNED`
to the common checkout. Apply each scenario recipe with
`tools/selection_eval/run_mutants.py`'s `apply`, capture bytes using
`git diff -M HEAD`, then restore the clean checkout. Run each selector three
times with only patches, test catalog, and pinned metadata as inputs.
Use the unchanged `score.evaluate_run` offline. CPU is selector child
user plus system time, with the median across the three runs.

| Input | SHA-256 |
| --- | --- |
| Test catalog | `c18345fd53b3c95f735fb0e0dd7422b447f81861a6162e65d7acb683345555f4` |
| Dependency map | `e075eb7024a42415c1c9157460dfc4c327b0726ac6a54050e884d3942d217459` |
| Coverage map | `1f77a6efa05c0b64b9ca13a7c5b7bbf115302afa9045028e643e43ce1e4eae53` |
| Performance scenario recipe | `57806216136907980a0a957a8a9343a8987cad11edf45f5c6490fa40fffd0d11` |
| Reproduced `perf-con-01.patch`, 554 bytes | `384f2822db4ae1969f8fe78e1991259f076d46d3db3af0930dd2965746e2e008` |

Patch-set digests hash the sorted JSON mapping of patch filenames to
individual SHA-256 values. Label digests hash sorted JSON indexed by commit.

| Set | Patch-set digest | Label digest |
| --- | --- | --- |
| Sample | `3f4f9ec814debba9640f06da29f3b24023c2fa5fa6073bdef6000f791c0f7e44` | `25f0cf958f50f16910f65aa3e65ceeed45a193debed4757fd5e3c36fcc3e9996` |
| Performance | `e5e97f5cefdbf05fc646eadfbe0de11d8d5129b044fc3bdc39db5431ffc5d207` | `6ec0efb9837551d885e9c6df52038bd3e7b1238a2ea41378b4e553787f789b7c` |
| Structural | `70562e847451dcbb7a7cf27e1e1a0c41251d1f159e1ff5e8608c17673f449fd1` | `928f3e837f44a5406946201b556791264ef692f0effaa1d927dc2d2033882f6c` |
| Formatting | `3c295353589933bd2c7cc00787e30eb70eff4347e0f8237d61e2f52b331b6d62` | No labels used |
| Adversarial | `bd5bc8b14b8634234b52ccbde424d1daaeabaf74c15526a1468c8092c1a599da` | `148342cbcc72615b9bf84579d6ac24d3d13534b9ca7ed8cc2936c09d8b531f8d` |
| Seed | `8a40cf00b01bac3ac663e746b561465d0f1ec2b4a074e076a704dd56144fa740` | `fe46b567a10ffb353eba8804750a5d1a279fd7e38593db2b2ef72797ba7452f8` |

The targeted labels came from fresh clean and mutated runs of the same
patch, with separate empty bytecode cache prefixes. All three passed clean
and failed on the mutation. The baseline omitted all three, and the guard
selected all three by expanding that patch from 1,117 to 1,620 tests.

- `tests/test_executor.py::TestExecutor::test_execute_tpcds`
- `tests/test_optimizer.py::TestOptimizer::test_optimize_joins`
- `tests/test_optimizer.py::TestOptimizer::test_unnest_subqueries`

The sample changes from 401.155 to 427.286 simulated seconds, with zero
misses on both sides. The extra 26.131 seconds comes from the Dremio registry
factory edit. Across the reconstructed performance recipes, scheduled time
increases from 142.454 to 181.970 seconds. The known miss adds a 300-second
penalty to the baseline, making the comparison 442.454 to 181.970 on the
available targeted labels. This is a correctness repair.

| Dataset | Baseline simulated / CPU / total (s) | Candidate simulated / CPU / total (s) | Missed commits / failing IDs |
| --- | --- | --- | --- |
| 30 samples | 401.155 / 1.128 / 402.283 | 427.286 / 1.499 / 428.785 | 0 / 0 on both |
| 11 performance, three verified labels | 442.454 / 2.338 / 444.792 | 181.970 / 3.019 / 184.989 | 1 / 3 to 0 / 0 on those labels |
| 10 structural | 368.170 / 0.439 / 368.609 | 368.170 / 0.620 / 368.790 | 0 / 0 on both |
| 9 formatting, packing only | 147.268 / 1.397 / 148.665 | 147.268 / 5.342 / 152.610 | Unverified |
| 58 adversarial | 1573.743 / 3.263 / 1577.006 | 1573.743 / 5.092 / 1578.835 | 0 / 0 on both |
| 16 seed scenarios | 464.623 / 0.716 / 465.339 | 464.623 / 0.896 / 465.519 | 0 / 0 on both |

The exact release coverage map, complete historical performance and
formatting labels, and original labeled 63-mutant bundle were unavailable.
The common-input result demonstrates this repair with the recorded maps.
It does not reproduce the historical full-label report.

Linux unit tests ran 168 tests with 166 passes, two skips and zero failures.
