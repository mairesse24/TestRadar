# Eager initialization dependency guard

Protected TestRadar parent `eacab514997a41693d5cf5792ec3f6bd7e87b843`.
Pinned SQLGlot `eb11aec4be8cb2122afe713a4816662c3f0fb2fa`.

The pinned dialect registry can import a generator on its first lookup
from an optimizer or executor test. Class bodies build transform tables
with factory calls, and a failure during one of those calls can prevent
the dialect from loading. A reverse static import closure or partial
file coverage cannot bound those consumers. The guard expands to the
full suite when edited bodies are positively linked to eager execution.

## Analysis and integration

The guard reconstructs modified package source from pinned files and
compares old and new function ASTs. It indexes module and class calls,
decorators, explicit metaclass hooks, constructors, import aliases and
assignments. A transitive walk follows references from eager roots
through function bodies and across resolved imports. It includes calls
and possible callbacks passed to registrations. Simple method names
and references are overapproximations. It checks both the pinned graph
and a graph with the changed files overlaid. The pinned index and its
reachability are cached per selector process.

Existing patch, import/fixture, syntax-equivalence and static selection
logic still runs, and all new decisions are full-suite fallbacks.
Missing pinned source, reconstruction/index errors, or an exceeded
alias-expansion bound trigger full selection on applicable edits. No
statement trace is used to exclude tests. The experiment defaults to
`TESTRADAR_EXPERIMENTS=equivalent,initialization`. Override that with
`equivalent` to replay the release behavior in the same image.

The static model does not prove completeness through arbitrary
reflection, computed registry keys, opaque external dispatch, or all
implicit inheritance and import hooks. Some callback references are
deferred in reality. The guard deliberately retains them because
it cannot establish that a published callback cannot be invoked or
inspected by initialization code. Absence of a new edge is never used
by this guard to drop an existing selection.

## Validation procedure

Use the pinned builder image and identical copies of `tests.json`,
dependency map and coverage map for the paired replay. Mount only the
catalog and commit patches as runtime inputs. Use the scorer offline.
The experiment used a runtime-source overlay on the builder for the
multi-dataset paired replay and separately built the candidate
Dockerfile for restricted sample validation. Both modes retained the
eight-worker maximum and yielded valid JSON Lines. Unit results:
169 run, 167 passed, two skipped, zero failures on Linux.

Shared replay coverage SHA-256:
`1f77a6efa05c0b64b9ca13a7c5b7bbf115302afa9045028e643e43ce1e4eae53`.
Dependency SHA-256:
`e075eb7024a42415c1c9157460dfc4c327b0726ac6a54050e884d3942d217459`.
Catalog SHA-256:
`c18345fd53b3c95f735fb0e0dd7422b447f81861a6162e65d7acb683345555f4`.
Reconstructed `perf-con-01` patch SHA-256:
`384f2822db4ae1969f8fe78e1991259f076d46d3db3af0930dd2965746e2e008`.

The verified DuckDB case expands from 1,117 to 1,620 tests and recovers
`TestExecutor.test_execute_tpcds`, `TestOptimizer.test_optimize_joins`
and `TestOptimizer.test_unnest_subqueries`. The only changed sample
selection is a Dremio parser registry factory whose nested returned
callback changed. The extra full-suite selection costs 26.131
simulated seconds. The two adversarial additions protect a module
and a generator containing syntax errors. The original labeled 63
seeded mutants, and complete formatting/performance labels, were
unavailable. The performance results cover the three verified
failures and the other two sets have packing-only results.

## Measured results

| Set | Baseline to guard simulated seconds | Fallbacks | Verified misses |
| --- | --- | --- | --- |
| 30 samples | 401.155 to 427.286 | 5 to 6 | 0 to 0 |
| 11 reconstructed performance | 442.454 to 181.970 | 0 to 2 | 1 commit / 3 IDs to 0 / 0, targeted labels |
| 58 adversarial | 1573.743 to 1633.781 | 28 to 30 | 0 to 0 |
| 10 structural | 368.170 to 368.170 | 10 to 10 | 0 to 0 |
| 16 seed | 464.623 to 464.623 | 9 to 9 | 0 to 0 |
| 2 process-pool and subprocess | 64.271 to 64.271 | 1 to 1 | 0 to 0, targeted labels independently rerun |
| 9 formatting | 147.268 to 147.268 | 4 to 4 | Labels unavailable |
| 63 regenerated | 1524.558 to 1524.558 | 24 to 24 | Original labels unavailable |

The performance row includes the 300-second baseline penalty, removed
by the guard. All compared candidate selections were supersets of the
before selections.

The candidate Dockerfile built in 378.760 s on Linux/amd64 under the
4-CPU, 8 GB caps, retaining the coverage checkpoint. The final runtime
maps matched the hashes above. Both modes ran three times with no
network, two CPUs, 4 GB, read-only root, 64 MB temporary storage, 256
processes, dropped capabilities and UID 65534. Each mode's JSONL was
byte-identical across runs. Cgroup CPU deltas for baseline were 1.277749,
0.957155 and 1.020812 s. Guard deltas were 3.200758, 3.088659 and
5.532688 s. Adding the respective medians and rounding gives local
score estimates of 402.2 and 430.5 s. The rebuilt image selected all
1,620 IDs for the exact verified regression.
