# V3.1 child execution tracing

Starting point: `2f0270375de6a8ff2a41bf5c1f0437188e957bdc`.
This changes the opt-in `blocks` path. The official submission and
Docker's balanced default remain intact.

## Execution audit

The pinned suite uses a reused spawn pool in `TestOptimizer`, spawn
pools in `TestExecutor`, and a separate Python interpreter in
`TestDialect.test_lazy_load`. The optimizer submits per-fixture tasks;
the executor submits TPC-H and TPC-DS tasks. Pool machinery also starts
thread-backed dispatchers. Static analysis found dynamic imports in
dialect import, typing, docs, and native-adapter test files. The audit
also checks direct multiprocessing, threading and low-level process
calls. An imported helper, native worker, or a preexisting thread can
escape these checks.

The [audit artifact](../tools/selection_eval/trace-audit.json) lists the
candidate and completed test IDs and recorded mechanisms. The targeted
run completed 221 of 346 candidates. It captured Python block hits for
eight pool tests and the named subprocess test. TPC-H submitted pool
work but produced no indexed child hits. A per-test launch guard remains
for both traced and untraced paths with potential bootstrap, native,
thread, or dynamic-loading gaps.

Each pool job carries a unique return-file path tied to the submitting
pytest test. A Python monitoring callback records indexed function
entries and resumes during the job. The named `python -c` subprocess
receives a private startup hook and writes its own trace at normal
interpreter exit. Neither path shares an append file across processes.
Files are published by atomic replacement. Pool unpickling and startup
before the wrapper, native code, interpreter shutdown failures, inherited
threads, and arbitrary launches can evade collection. Subprocess and pool
observations remain positive evidence.

The original map reported 1,620 observed manifest test IDs. The
supplement increases indexed functions with observations from 3,250 to
3,278 and test/function pairs from 332,328 to 334,089. It explicitly
guards 137 test IDs in blocks mode, including 125 candidate IDs whose
targeted protocols did not complete. None of these counts certifies
coverage completeness.

## Building and validating

Use the pinned SQLGlot checkout and the existing locked build
dependencies. Run the build-only tracer outside the scorer runtime:

```sh
python -B -m tools.trace_children /sqlglot sample/tests.json maps/blocks.json /tmp/blocks.json /tmp/trace-audit.json
```

The Docker Desktop trace pass used four CPUs, the pinned Linux builder,
and a 180-second command cap. It retained a checkpoint after timing out
and took 194.164 seconds of host wall time. A second pass with
`PYTHON_CPU_COUNT=4` retraced the two audited pool IDs with a 55-second
per-test timeout and took 33.364 seconds. Both targeted tests passed.
The traced runtime overlay took 16.232 seconds. It holds the submitted
file-level maps and scheduler constant while copying the traced block
map and the two block-policy source files.

The Linux unit suite reported `Ran 154 tests in 24.075s` and `OK`,
with zero failures or skips. Restricted containers passed JSON Lines
validation and used at most four workers. The original prototype and the
traced overlay used the same maps apart from block-trace evidence.

| Dataset | Policy | Simulated seconds | Tests scheduled | Missed commits / tests | Full-suite fallbacks |
| --- | --- | ---: | ---: | ---: | ---: |
| 30 labeled samples | Original blocks | 422.085 | 12,249 | 0 / 0 | 4 |
| 30 labeled samples | Traced blocks | 654.062 | 14,018 | 0 / 0 | 4 |
| 30 labeled samples | Balanced | 480.180 | 19,983 | 0 / 0 | 6 |
| 2 child-only probes | Original blocks | 354.908 | 470 | 1 / 1 | 0 |
| 2 child-only probes | Traced blocks | 54.908 | 484 | 0 / 0 | 0 |
| 2 child-only probes | Balanced | 64.271 | 1,946 | 0 / 0 | 1 |
| 4 earlier verified probes | Original blocks | 124.718 | 5,884 | 0 / 0 | 3 |
| 4 earlier verified probes | Traced blocks | 137.905 | 5,993 | 0 / 0 | 3 |
| 58 archived adversarial mutants | Original blocks | 2844.884 | 34,462 | 5 / 88 | 6 |
| 58 archived adversarial mutants | Traced blocks | 3088.233 | 36,752 | 5 / 57 | 6 |
| 58 archived adversarial mutants | Balanced | 3015.418 | 59,438 | 5 / 88 | 25 |

The original blocks sample scored 0.003 seconds above the separately
reported 422.082 result in this controlled map environment. Sample CPU
was 0.862906 seconds for original blocks, 1.063533 seconds for traced
blocks, and 0.707788 seconds for balanced. CPU was measured in separate
matching-output cgroup runs. Labels were loaded only by the host scorer.

The pinned child-only probes independently verified failures in
`TestOptimizer.test_optimize` and `TestDialect.test_lazy_load`; the
corresponding parent controls passed. Original blocks kept the optimizer
failure but missed the named subprocess failure. Traced blocks kept both.
The 58 archived adversarial labels came from the independently verified
dataset on `experiment/grok-adversarial`. The earlier four probes contributed 50 verified failing test instances.
Traced blocks recovered 31 labeled adversarial failures with no newly
missed IDs. The default balanced sample output was byte-identical across
the original and traced images. The 63 seeded mutants' original
per-test labels were unavailable.

Keep the official submission. The conservative child guards close the
verified named-subprocess gap, but traced blocks is slower than balanced
on the sample and still misses five archived adversarial commits.
