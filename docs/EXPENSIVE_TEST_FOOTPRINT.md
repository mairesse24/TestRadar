# Approach B · expensive-test footprint audit

Base TestRadar revision: `eacab514997a41693d5cf5792ec3f6bd7e87b843`.
Pinned SQLGlot: `eb11aec4be8cb2122afe713a4816662c3f0fb2fa`.

The [RWX rules](https://www.joinbuilders.org/buildathon/tracks/blast-radius)
permit coverage and dependency maps generated from the pinned revision.
They forbid patch-to-failure and edit-type-to-failure tables. The recorder
uses no patches, labels, or test answers when generating the clean map.

## Recorder and exclusion decision

A temporary Python startup hook records function entries and resumptions
before pytest imports the suite. A function identity is repository-relative
path, qualified code name, and first source line. This includes module and
class bodies, setup and teardown functions, and Python constructors.
Pytest reports setup, call and teardown for the requested node separately.
The bootstrap travels through the Python environment to spawned interpreters.
Forks create distinct PID logs and re-arm monitoring. Launch audit events and
missing termination records expose uncertainty. The maps are diagnostics.
The selector and Dockerfile retain the protected behavior in this branch.

Two full clean runs of `tests/test_parser.py::TestParser::test_max_nodes`
passed. Each observed 1,307 function identities, including 39 module
bodies and eight names matching `__init__`. Each had one process and
completed all three pytest phases. The maps were byte-identical at 68,394
bytes, SHA-256
`a0baf7a245eb6f993ce58c8ea8d267fe8fa53cdf43ac4137c26ceffcb6e7e120`.
They took 40.901 and 39.975 seconds wall time in disposable Docker at
four CPUs, 8 GB, read-only root and a 64 MB temporary filesystem. The
separate dialect lazy-load control passed and recorded two Python
processes, one subprocess launch and 1,541 unioned function identities.

Stable positive traces cannot establish non-reachability. Native workers,
interpreters that skip the bootstrap, abrupt process death, data-only
reads, outside dependencies and changed control flow remain open.
Module initialization can alter later state without an edited function
appearing in a clean trace. The recorder declares no exclusions, including
when collection is incomplete or unavailable. The footprint is absent
from the runtime image and contributes zero runtime CPU or memory.

## Validation

The common-input selector replay used the same source, test catalog and
builder maps on both sides. No test IDs or worker assignments changed.
Penalty-inclusive simulated seconds follow the official scorer.

| Dataset | Simulated seconds on both sides | Verified missed commits / IDs |
| --- | ---: | --- |
| 30 samples | 401.155 | 0 / 0 |
| 58 adversarial | 1573.743 | 0 / 0 |
| 10 structural | 368.170 | 0 / 0 |
| 11 performance | 442.454 | 1 / 3, targeted labels |
| 16 seed | 464.623 | 0 / 0 |
| 9 formatting | 147.268 | Original labels unavailable |
| 63 regenerated | 1524.558 | Original labels unavailable |
| 2 new parser mutations | 73.634 | 0 / 0 on the targeted labels |

The three inherited misses on reconstructed `perf-con-01` are
`TestExecutor.test_execute_tpcds`, `TestOptimizer.test_optimize_joins`,
and `TestOptimizer.test_unnest_subqueries`. Its 300-second penalty is
already included. The original 63-mutant labeled bundle was unavailable
and the regenerated packing total differs from the archived total.
The two new held-outs add an option-dependent input length check inside
`parse_one` and reverse tokens for the option-bearing path in
`Dialect.parse`. The pinned probe fails on each and the selector retains
it in both full-suite schedules. Those are probe-only labels.

Linux ran 166 unit tests with 164 passes, two skips and zero failures.
Run the recorder using the existing pinned map-builder image, mounting
`tools/selection_eval/record_footprint.py` as `/record.py` and invoking
`python -B /record.py /sqlglot` as the entrypoint. Run the child control
with `--target tests/dialects/test_dialect.py::TestDialect::test_lazy_load`.
Use the offline scorer for the selector's JSONL. Never mount answers
into the selector or recorder.

The exact-release Linux/amd64 Docker rebuild finished in 310.436 seconds
with the four-CPU and 8 GB build limits. Its existing coverage collector
used the last checkpoint after reaching its collection budget. All three
restricted sample runs produced byte-identical valid JSONL, scoring
401.155 simulated seconds and zero misses. Local cgroup CPU deltas around
the selector invocation were 1.785638, 1.398327 and 0.914576 seconds.
Applying the official median and rounding formula gives a local estimate
of 402.6 seconds. The recorder is a separate build-time experiment and
adds no runtime work to that image.

## Approach C · feasibility only

The collector can supply an additional positive-evidence map at
function granularity. A gate for exclusions requires proof independently
of missing trace edges. At minimum, resolve old and new edited functions,
dynamic and imported call sites, class construction, shared state,
fixtures and child-process entry points. Keep the current selection on
unresolved edges and preserve existing fallbacks on structural edits.
Evaluate each proposed exclusion against independently labeled changes
and measure the actual packing marginal. The observed stability and map
size support further diagnostic work. They do not support enabling a
selection gate, and this branch implements no Approach C behavior.
