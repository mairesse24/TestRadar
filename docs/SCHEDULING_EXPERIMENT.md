# Scheduling and selection cost experiment

This branch starts at submitted commit
`d08cfc3a7630c2b8899ae1b0ac77a1b55568d8b4`. The submission's official score
was 632.1 seconds, with 631.336 simulated seconds, approximately 0.75 CPU
seconds, and zero missed commits.

## Reproduction

Run the comparison on Linux with the scorer restrictions in
`tools/selection_eval/run_container.py`. Build each tag from its own checkout.

```sh
docker build --platform linux/amd64 --progress=plain --tag testradar-cost:submitted .
docker build --platform linux/amd64 --progress=plain --tag testradar-cost:experiment .
python -m unittest discover -s tests -v
python tools/selection_eval/profile_costs.py sample /path/to/copied-image-maps
python tools/selection_eval/profile_costs.py sample /path/to/copied-image-maps --aggressive
```

For mutations, regenerate seed 20261120 with `make_mutants.py`, apply the
operations in a disposable checkout of the pinned SQLGlot source, and
place each Git diff under `commits/` beside `tests.json`. The profiler
reports each patch's fallback reason, selected IDs, worker loads, cost,
strongest test-file contributors and packing lower bound. The validation
used `check_output.py` to verify scheduled IDs and `score.py` against the
sample labels offline. CPU is cgroup v2 usage from a separate run with
matching stdout. The mutant per-test ground truth was unavailable.

## Results

Both images built successfully. The submitted image reused cached map
layers (6.614 seconds). The experimental image regenerated maps
(173.965 seconds). Linux unit tests reported 145 passes, zero failures,
and zero skips in 19.254 seconds.

| Dataset | Image | Packing seconds | Full-suite fallbacks | Tests scheduled | Missed commits / tests | CPU seconds |
| --- | --- | ---: | ---: | ---: | --- | ---: |
| Sample | Submitted | 480.180 | 6 | 19,983 | 0 / 0 | 0.868728 |
| Sample | Experiment | 480.180 | 6 | 19,983 | 0 / 0 | 0.860940 |
| Mutants | Submitted rebuild | 1524.455 | 24 | 63,525 | Unmeasured | 1.275742 |
| Mutants | Experiment | 1534.850 | 24 | 63,526 | Unmeasured | 0.853203 |

The historical labeled mutant result was 1534.850 simulated seconds
with zero misses. The rebuilt submitted image and experimental image
had 320 and 495 unobserved IDs respectively. Across independent builds
one mutant (`base_dialect-00`) added a test and rose from 17.059 to
27.454 seconds. With identical experimental maps, binding the submitted
scheduler into the experimental image produced the same selected tests
and cost for every one of the 93 patches. Maximum worker count was four.
The difference between the independent builds therefore came from coverage
evidence, and the single-run CPU measurements are not an improvement claim.

## What drives the cost

With the shared profiling maps, six core-module fallbacks cost 220.902 of
480.180 sample seconds. The expensive non-fallback paths included two
optimizer changes at 27.454 seconds each, executor context at 25.454, and
two BigQuery parser changes at 14.308 each. The long executor workload
includes the 21.454-second TPC-DS test. Full-suite scheduling also includes
the 28.817-second parser limit test. The largest accumulated test-file
work in the profile came from executor, optimizer, parser, and generator
tests.

The 24 mutant fallbacks cost 883.608 seconds. Broad non-fallback
selections included Hive and MySQL family changes, at about 29 seconds
per patch with around 1,300 selected tests. The submitted scheduler
already reached the per-commit packing lower bound for all 93 patches
in the shared-map profile.

## Risk and release decision

Aggressive mode retains hard analysis failures, and its observed coverage
hits are useful as additions. Those hits cannot justify replacing core
fallbacks or static dialect candidates. Ignoring a broad impact set,
relaxing structural-change fallback, or doubling refactor thresholds
has no independent evidence of safety. The offline aggressive
counterfactual saved packing time but missed 41 failing tests on five
sample commits. Its 290.541 packing seconds become 1790.541 simulated
seconds after penalties. It was never enabled in the image.

The experiment adds only a bounded packing search for up to 20 selected
tests. It accepts a strictly cheaper result and keeps the existing
schedule when the search budget is exhausted without finding one.
It improved an independent synthetic schedule from 12.421 to 12.348
seconds while preserving every test. On the real workloads it produced
no per-commit savings. Existing hidden-selection risks, including
implicit dependencies, import-time failures and subprocess coverage gaps,
remain. Keep the submitted candidate for release.
