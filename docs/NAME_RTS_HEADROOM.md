# Name-based RTS headroom at eacab51

Decision: stop before implementing the runtime graph. The profile exposed
expensive groups, but none of the examined exclusions has a closed static
dependency argument under the approved guards. The same policy remains the
default. A broader reachability graph could still find other opportunities.

## Baseline

The offline auditor ran all 30 sample patches against pinned Linux source and
the existing reference maps, with `TESTRADAR_EXPERIMENTS=equivalent`. It
reproduced **401.155 simulated packing seconds**. It does not read failure
labels. Five full-suite fallbacks accounted for **184.085 seconds**.

| Fallback context | Pinned source or scope | Packing seconds | Decision |
| --- | --- | ---: | --- |
| Dialect base | `Dialect.case_sensitive` | 36.817 | Keep. The base attribute participates in dispatch across dialects. |
| Serialization | `serde.dump` | 36.817 | Keep. Expression serialization and `__reduce__` can enter it outside the initiating process. |
| T-SQL | Dialect changes outside the verified body-only subset | 36.817 | Keep. Core users and structural evidence prevent localization. |
| Expression definitions | `expressions/ddl.py` outside the verified body-only subset | 36.817 | Keep. Class/definition effects and imports remain conservative. |
| Mixed typing and fixture | Snowflake typing body plus optimizer data | 36.817 | Keep. The fixture edge remains and the combined selection promoted to full suite. |

## Expensive tests and exclusions

A marginal is the baseline cost minus the cost after deleting exactly one
test ID at a time and rescheduling. Marginals overlap and cannot be summed as
savings. The table does not assert that the dropped tests are unaffected.

| Test | Catalog duration | Sum of one-test marginals | Static finding |
| --- | ---: | ---: | --- |
| `tests/test_executor.py::TestExecutor::test_execute_tpcds` | 21.454 s | 49.746 s | Keep. The optimizer rules call the edited join/subquery helpers; executor paths reach context methods. TPC-DS submits child work, and the absence of a child hit does not sever those edges. |
| `tests/test_parser.py::TestParser::test_max_nodes` | 28.817 s | 36.815 s | Keep on all five fallbacks. It drives the generic parser with large SQL inputs. Shared dialect and expression dispatch and the unresolved fallback reasons above prevent a closed exclusion. |
| `tests/test_generator.py::TestGenerator::test_generator_memory` | 6.686 s | 28.250 s | Keep unless construction can be bounded independently of trace observations. The test loops over all dialects; dialect metaclass initialization is dynamic, and generator initialization uses `dir`, computed `getattr`, and a transform dictionary. ClickHouse changes include methods discoverable by this dispatch and a registered callback. |
| Targeted optimizer fixture consumers | Varies | Often one worker only | Keep. Several fixture changes already select a single consumer, leaving no meaningful name-level reduction. |

## Conditional headroom

* For the three non-fallback optimizer/executor patches with TPC-DS selected,
  charging **only** the mandatory 21.454-second test and one worker yields
  **10.000 seconds** of maximum packing headroom in those patches. This is
  an optimistic bound. Other tests remain required.
* For direct parser-only body edits, deleting only the generator-memory
  test would save **9.988 seconds** before source review. Dremio accounts
  for **5.532 seconds** of that figure and is ineligible: the edited
  `build_date_delta_with_cast_interval` function is called while building
  the parser class's function registry. The two BigQuery method-body edits
  account for the remaining **4.456 seconds**. They are worth investigating
  if the reflective construction boundary can be closed, but this audit
  could not prove that boundary.
* Taken together, these disjoint inspected opportunities yield a favorable
  **14.456-second ceiling (3.6%)** while retaining core/structural fallbacks
  and TPC-DS. They are not a forecast for every possible graph design.
  **Currently justified savings from the inspected exclusions are 0.000 s.**

The conditional ceiling does not justify a shadow-to-candidate implementation
here. Building a graph and enabling an exclusion based on a missing static
edge through these opaque paths would repeat the failure mode already observed
in the blocks experiments. Targeted dynamic adversarial cases, candidate CPU
and the six-set replay remain deferred because this gate produced no candidate.
Historical six-set results remain in
[the eacab51 release report](RELEASE_FORMATTING_EQUIVALENCE.md).

## Reproduce

Run the offline audit with the pinned source and compatible reference maps on
Linux. It prints per-patch cost, worker and test counts, fallback reasons,
verified body scopes, and individual test marginals. The 30-patch Docker
audit returned 401155 ms, 184085 fallback ms, 9988 ms for the raw parser
construction counterfactual, and 10000 ms of TPC-DS-preserving headroom.

```sh
TESTRADAR_PINNED=/sqlglot python -B tools/selection_eval/name_rts_headroom.py sample /maps
```

This branch changes only the offline audit and this note. Neither official
release, scorer entrypoint, map artifact, nor scheduler was changed.
