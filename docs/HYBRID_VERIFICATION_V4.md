# TestRadar V4 shadow verification

Base revision `eacab514997a41693d5cf5792ec3f6bd7e87b843`. The experiment uses
SQLGlot `eb11aec4be8cb2122afe713a4816662c3f0fb2fa` and the existing selector's
test IDs. The scorer, Dockerfile, and selector remain byte-identical to the
base. The new tool is an offline controller.

## Baseline

Three restricted Linux runs reproduced **401.155 simulated seconds**, zero
misses, 15,821 scheduled test instances and 62 nonempty workers across 30
patches. Selector subprocess CPU was 1.786072, 3.064914 and 1.405003 seconds
in these three runs. The common replay of the exact release source against
the builder maps separately measured median CPU of 0.838252 seconds at the
same simulated score. These are local CPU estimates.

The three restricted output files were identical. The archived JSONL SHA-256
is `ae64b151cba6fd643e9b36357469206991ff0da7402025e30178cdd111391412`.
To inspect the selected IDs, resolve each number in its worker list against
the supplied `tests.json`. The actual selection is the shadow output.

The five full-suite fallbacks cost 184.085 seconds. One-test-at-a-time
rescheduling on the baseline gives 49.746 seconds of marginal cost for
TPC-DS executor, 36.815 for the parser node-limit test and 28.250 for the
generator memory test. These costs overlap and are upper bounds on
opportunity, without evidence to omit the tests. The file coverage map
adds tests that observed any code in a changed file, including unrelated
functions. It also has partial test and child-process attribution, so an
unobserved edge does not establish independence. The initialization gap
on the reconstructed DuckDB performance patch remains in the release
selection and is explicitly visible in the replay.

## Shadow procedure

Supply the existing JSONL schedule and a data directory containing the
manifest and patches. The controller computes the marginal change in
busiest-worker load plus worker charges by rescheduling without one
candidate at a time. It considers tests with catalog durations up to
one second, and attempts only candidates whose estimated savings exceed
the initial one-second CPU estimate. This policy uses no labels.

Each attempt starts a disposable offline container from the local pinned
map-builder image, with one CPU, 4 GB, 256 process slots, read-only root,
64 MB scratch, UID 65534 and dropped capabilities. Only the chosen patch
and trusted recorder file are mounted. It verifies the SQLGlot revision,
copies a fresh checkout without stale bytecode, applies the patch via
`git apply`, and runs the full pytest node. Uniform CRLF transport is
reported separately when normalizing it is needed to apply a Windows
export. No subset of subtests is used. Setup, teardown and fixtures run
through pytest. Child output stays out of scorer stdout.

The controller reserves a fixed number of serial slots at one CPU, limits
each Docker start/attach by a wall deadline and forcibly removes each
trial container before the next. Completed trials read cgroup CPU usage.
Killed trials do not have a final CPU counter. This is a conservative
experiment budget. Exact batch-wide CPU enforcement, including teardown
and controller work, and score-environment parity are required before
putting execution into the submission. The contest runtime does not
provide a Docker daemon for the shadow controller.

Example from the repository root, with archive paths substituted:

```sh
python -B -m unittest discover -s tests -p test_verification_v4.py -v
python -B tools/selection_eval/verification_v4.py SAMPLE BASELINE.jsonl --budget 56 --trial 8
```

For the common replay, generate scenario patches from the original
recipes using `tools/selection_eval/run_mutants.py`'s `apply` helper and
`git diff -M HEAD` on the pinned checkout. Run the archived release source
with `TESTRADAR_EXPERIMENTS=equivalent` and `balanced` against each dataset
and score its JSONL offline. Those scheduled IDs are the input to every
attempt. The builder coverage SHA-256 was
`1f77a6efa05c0b64b9ca13a7c5b7bbf115302afa9045028e643e43ce1e4eae53`.
Its dependency hash was
`e075eb7024a42415c1c9157460dfc4c327b0726ac6a54050e884d3942d217459`.
The restricted sample replay used the previously built guard image with
initialization filtering disabled and matched the exact-source sample
replay's score.

Seven sample attempts yielded five timeouts and two nonzero pytest exits
that match the sample's failing IDs. Neither provided a basis to omit
a test. Completed attempts consumed 5.763533 CPU seconds. Timeout CPU is
additional and unmeasured. A separate pinned-source parser control ran a
full node and reported a pass at 1.101901 CPU seconds.

| Dataset | Baseline and shadow simulated (s) | Baseline median CPU (s) | Known missed commits / IDs | Verification attempts |
| --- | ---: | ---: | --- | --- |
| 30 samples, exact-source replay | 401.155 | 0.838 | 0 / 0 | 2 nonzero pytest exits, 5 timeouts |
| 58 adversarial | 1573.743 | 2.507 | 0 / 0 | 1 timeout |
| 10 structural | 368.170 | 0.446 | 0 / 0 | 0 |
| 11 performance | 442.454 | 2.731 | 1 / 3, targeted labels | 0 |
| 16 seed | 464.623 | 0.511 | 0 / 0 | 0 |
| 9 formatting | 147.268 | 0.904 | Labels unavailable | 0 |
| 63 regenerated | 1524.558 | 1.660 | Original labels unavailable | 2 timeouts of 4 eligible |

The performance score includes one inherited 300-second penalty on
`perf-con-01` for `TestExecutor.test_execute_tpcds`,
`TestOptimizer.test_optimize_joins`, and
`TestOptimizer.test_unnest_subqueries`. The shadow worker checks only
scheduled candidates, which leaves omissions from selection unchanged.
The original 63-mutant labeled bundle was unavailable. Regeneration did
not reproduce its archived packing score.

The runner never rewrites the input schedule, including on a pass. A
single isolated run does not establish equivalence of collection,
session-scoped fixtures, ordering, subprocess environments, or timeouts
to the scorer's test execution. No probabilistic selection change was
enabled.

## Stage-level verification proposal

Start with a full fresh execution of the expensive executor test and
inventory ordered fixtures, input cases, assertions, process exits and
timeouts under the pinned label environment. Capture stage boundaries as
inputs and outputs rather than predicting failure from an edit. Reuse
a stage result only after a proof that its entire input state and
dependency closure match, including process state, fixture data and
configuration. Re-execute every affected stage and compare against the
original assertions for every case. Collection, setup, teardown and
shared or subprocess state require their own equivalence checks. An
unobserved stage, missing exit, or unsupported dependency retains the
whole test. Establish the argument by comparing candidate and reference
stack and subprocess outcomes on independent changes before any exclusion.

For each patch, calculate the packing marginal after excluding a fully
verified test. Budget verification by measured setup, uncached stage,
validation and teardown CPU. The expected marginal must exceed that CPU
and any uncertainty about equivalent execution must still preserve the
test. The current experiment provides no evidence to implement this
stage decomposition.
