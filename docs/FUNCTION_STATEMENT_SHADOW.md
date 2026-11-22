# Function-body statement shadow experiment

Protected parent `eacab514997a41693d5cf5792ec3f6bd7e87b843`.
Pinned SQLGlot `eb11aec4be8cb2122afe713a4816662c3f0fb2fa`.

The offline runner reconstructs the edited module and compares old and new
AST structures. It aligns statements in the same function body, separates
changed compound headers from their nested statements, and marks structural
changes, decorated or special methods, nested definitions and detected
reflection or shared writes unsupported. It maps positive pinned execution
lines to containing statement spans. A line may belong to multiple spans.
A matched span supplies an observed test ID, without proving that other
tests cannot reach it.

A localized statement remains only a shape classification. This experiment
does not certify the closure of imports, fixtures, eager code, dispatch,
registry access, or worker processes. All shadow proposals retain baseline
selection. No sample answers, IDs, or scoring labels feed the classifier.
The selector, analyzer, Dockerfile and scorer are unchanged.

## Named-patch findings

| Patch | Classification and pinned evidence | Tests / simulated seconds retained |
| --- | --- | ---: |
| `2haz` | A localized conditional expression in `Dialect.case_sensitive`. Identity and equality agreed for a normal `NormalizationStrategy` member. With its raw string value assigned, the pinned original returned false and the reconstructed method true on the same text. The str-based enum and mutable instance strategy prevent a general equivalence argument. The three-node trace did not hit the edited statement. | 1,620 / 36.817 |
| `5y72` | A localized assignment in `_eliminate_cte`. The statement builds a fresh table and aliases it. Both copy modes produced `t AS a` in the ordinary control. The copied path invokes `Table.copy`; an independent hook raised only for that path. The pinned optimizer test `test_eliminate_subqueries` hit the statement, but this does not bound optimizer or executor callers. | 326 / 27.454 |
| `535z` | A T-SQL class constant used by week/date handling. Structural change. | 1,620 / 36.817 |
| `4d63` | A Snowflake tokenizer class dictionary changes escape decoding. Structural change. | 1,027 / 13.502 |
| `66ks` | The decorated `Context.table` property reads and updates cached state. The pinned executor also has process-pool tests. The collected footprint does not certify their attribution. | 60 / 25.454 |

The recorder used `sys.monitoring.LINE` on the clean pinned checkout and
wrote 79,464 observed triples across three independently launched nodes.
The successful controls were dialect lazy load, parser union and optimizer
eliminate-subqueries (28 subtests passed). Their startup and child events
are positive evidence only. An earlier normalize-identifiers capture
timed out at 55 seconds; missing observations are explicitly inconclusive.
The hook/copy controls above are semantic counterexamples, not labels
for omitted catalog tests.

## Measured validation

The unchanged exact runtime rebuilt using Docker cache in 4.006 seconds,
reproducing image `68198fb531f8`. Its prior exact-source cold build took
310.436 seconds. Three runs under Linux/amd64 scorer restrictions produced
identical JSONL and scored 401.155 simulated seconds, with five full-suite
fallbacks and zero sample misses. Cgroup CPU deltas were 1.809468,
1.602058 and 1.869095 seconds. Applying the official median and rounding
formula gives a local estimate of 403.0 seconds. Shadow tracing and AST
diagnostics run offline and add zero selector runtime CPU.

| Set | Simulated seconds on both sides | Full-suite fallbacks | Known missed commits / IDs |
| --- | ---: | ---: | --- |
| 30 sample | 401.155 | 5 | 0 / 0 |
| 58 adversarial | 1573.743 | 28 | 0 / 0 |
| 10 structural | 368.170 | 10 | 0 / 0 |
| 11 performance | 442.454 | 0 | 1 / 3, targeted labels |
| 16 seed | 464.623 | 9 | 0 / 0 |
| 2 process-pool / subprocess | 64.271 | 1 | 0 / 0, probe labels |
| 2 parser held-outs | 73.634 | 2 | 0 / 0, probe labels |
| 9 formatting | 147.268 | 4 | Labels unavailable |
| 63 regenerated | 1524.558 | 24 | Original labels unavailable |

No selected ID or schedule changed relative to the protected selector.
The performance row includes the inherited 300-second penalty for
`perf-con-01`, which misses `TestExecutor.test_execute_tpcds`,
`TestOptimizer.test_optimize_joins`, and
`TestOptimizer.test_unnest_subqueries`. The original 63-mutant bundle
was unavailable and regenerated packing did not match its archived score.

Linux tests: 168 run, 166 passed, two skipped, zero failures.
Reproduce offline with `python -B tools/selection_eval/statement_shadow.py
PINNED_TREE OBSERVATIONS.jsonl PATCH_DIRECTORY`. The observation format
is one JSON triple per line containing test ID, pinned relative Python
path and executed line. The launcher and resulting JSONL are recorded
in the local validation artifacts. Scoring is separate from capture.

Keep this experiment in shadow. A viable narrowing rule would need an
independent positive proof that omitted tests cannot reach any affected
behavior, including resource or identity observations and dependencies
through subprocesses. Stable absence from the index is insufficient.
