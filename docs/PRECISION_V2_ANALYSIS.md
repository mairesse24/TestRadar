# Precision V2 cost analysis

Profiled the unmodified submitted policy on pinned Linux maps before changing selection. Coverage and closure deltas remove one evidence source at a time. They overlap other buckets and are diagnostic, not safe savings. The regenerated mutant labels were unavailable; selection costs exclude missed-test penalties. The shared partial coverage checkpoint differs from the historical mutant scoring checkpoint.

## sample

Total: 480.180 s. Coverage addition delta: +0.003 s. Dependency closure delta: +0.000 s.
Worker overhead: 148 s. Packing lower-bound gap: 0 ms.
Cost buckets (seconds): core fallback: 220.902, dialect selection: 161.016, other selection: 98.262.
Largest test groups (selected work among per-patch top five): tests/test_executor.py: 284.085 s, tests/test_optimizer.py: 201.833 s, tests/test_parser.py: 175.290 s, tests/test_generator.py: 127.516 s, tests/dialects/test_dialect.py: 34.504 s.

Rows ranked by cost. The first ten are the most expensive.

| Patch | Seconds | Tests | Workers | Fallback | First decision reason |
| --- | ---: | ---: | ---: | --- | --- |
| 2haz | 36.817 | 1620 | 4 | yes | core module sqlglot/dialects/dialect.py |
| 3utz | 36.817 | 1620 | 4 | yes | core module sqlglot/serde.py |
| 535z | 36.817 | 1620 | 4 | yes | core module sqlglot/dialects/tsql.py (used inside functions of sqlglot/generator.py, sqlglot/generators/tsql.py, sqlglot/parsers/tsql.py) |
| bap8 | 36.817 | 1620 | 4 | yes | core module sqlglot/transforms.py |
| ktz3 | 36.817 | 1620 | 4 | yes | core module sqlglot/expressions/ddl.py |
| rszg | 36.817 | 1620 | 4 | yes | core module sqlglot/optimizer/scope.py |
| 2jrq | 27.454 | 326 | 3 | no | sqlglot/optimizer/eliminate_joins.py: source module; used by sqlglot/executor/__init__.py, sqlglot/executor/python.py, sqlglot/optimizer/optimizer.py, sqlglot/planner.py |
| 5y72 | 27.454 | 326 | 3 | no | sqlglot/optimizer/eliminate_subqueries.py: source module; used by sqlglot/executor/__init__.py, sqlglot/optimizer/optimizer.py |
| 66ks | 25.454 | 60 | 2 | no | sqlglot/executor/context.py: source module; used by sqlglot/executor/__init__.py, sqlglot/executor/python.py |
| ary6 | 14.308 | 995 | 3 | no | sqlglot/parsers/bigquery.py: source module, dialects bigquery |
| j6uz | 14.308 | 995 | 3 | no | sqlglot/parsers/bigquery.py: source module, dialects bigquery |
| 986c | 13.615 | 1029 | 3 | no | sqlglot/dialects/singlestore.py: source module, dialects singlestore |
| 4d63 | 13.502 | 1027 | 3 | no | sqlglot/dialects/snowflake.py: source module, dialects snowflake |
| 2qn5 | 12.892 | 938 | 3 | no | sqlglot/generators/clickhouse.py: source module, dialects clickhouse |
| 8y7j | 12.892 | 938 | 3 | no | sqlglot/generators/clickhouse.py: source module, dialects clickhouse |
| bj7g | 12.892 | 938 | 3 | no | sqlglot/generators/clickhouse.py: source module, dialects clickhouse |
| 9u5k | 12.686 | 789 | 3 | no | sqlglot/generators/sqlite.py: source module, dialects sqlite |
| f4xa | 11.177 | 657 | 2 | no | sqlglot/generators/drill.py: source module, dialects drill |
| 6ze8 | 10.686 | 246 | 2 | no | sqlglot/parsers/dremio.py: source module, dialects dremio |
| bvw2 | 10.686 | 227 | 2 | no | sqlglot/dialects/materialize.py: source module, dialects materialize |
| fryt | 10.686 | 246 | 2 | no | sqlglot/parsers/dremio.py: source module, dialects dremio |
| gbav | 10.686 | 518 | 2 | no | sqlglot/parsers/singlestore.py: source module, dialects singlestore |
| mthh | 3.016 | 1 | 1 | no | tests/fixtures/optimizer/merge_subqueries.sql: fixture consumers |
| egys | 2.596 | 1 | 1 | no | tests/fixtures/optimizer/simplify.sql: fixture consumers |
| nwnx | 2.092 | 1 | 1 | no | tests/fixtures/optimizer/pushdown_projections.sql: fixture consumers |
| 2vdj | 2.083 | 1 | 1 | no | tests/fixtures/optimizer/pushdown_predicates.sql: fixture consumers |
| gbhk | 2.061 | 1 | 1 | no | tests/fixtures/optimizer/annotate_types.sql: fixture consumers |
| 6vvp | 2.037 | 1 | 1 | no | tests/fixtures/optimizer/qualify_columns__invalid.sql: fixture consumers |
| st8f | 2.008 | 1 | 1 | no | tests/fixtures/optimizer/isolate_table_selects.sql: fixture consumers |
| f4bn | 2.007 | 1 | 1 | no | tests/fixtures/optimizer/pushdown_cte_alias_columns.sql: fixture consumers |

## mutants

Total: 1524.455 s. Coverage addition delta: +0.000 s. Dependency closure delta: +0.504 s.
Worker overhead: 378 s. Packing lower-bound gap: 0 ms.
Cost buckets (seconds): core fallback: 699.523, dialect selection: 432.656, other selection: 208.191, structural or other fallback: 184.085.
Largest test groups (selected work among per-patch top five): tests/test_executor.py: 1245.543 s, tests/test_optimizer.py: 816.017 s, tests/test_parser.py: 699.432 s, tests/test_generator.py: 322.723 s, tests/dialects/test_dialect.py: 87.168 s.

Rows ranked by cost. The first ten are the most expensive.

| Patch | Seconds | Tests | Workers | Fallback | First decision reason |
| --- | ---: | ---: | ---: | --- | --- |
| added-01 | 36.817 | 1620 | 4 | yes | patch: import statement edited in sqlglot/optimizer/canonicalize_internal_names.py |
| base_dialect-02 | 36.817 | 1620 | 4 | yes | core module sqlglot/parsers/tsql.py |
| base_dialect-04 | 36.817 | 1620 | 4 | yes | core module sqlglot/parsers/tsql.py |
| base_dialect-11 | 36.817 | 1620 | 4 | yes | core module sqlglot/typing/tsql.py |
| core-00 | 36.817 | 1620 | 4 | yes | core module sqlglot/expressions/query.py |
| core-01 | 36.817 | 1620 | 4 | yes | core module sqlglot/tokens.py |
| core-02 | 36.817 | 1620 | 4 | yes | core module sqlglot/serde.py |
| core-03 | 36.817 | 1620 | 4 | yes | core module sqlglot/transforms.py |
| core-04 | 36.817 | 1620 | 4 | yes | core module sqlglot/expressions/ddl.py |
| core-05 | 36.817 | 1620 | 4 | yes | core module sqlglot/expressions/datatypes.py |
| core-06 | 36.817 | 1620 | 4 | yes | core module sqlglot/expressions/properties.py (used inside functions of sqlglot/expressions/query.py) |
| core-07 | 36.817 | 1620 | 4 | yes | core module sqlglot/transforms.py |
| core-08 | 36.817 | 1620 | 4 | yes | core module sqlglot/expressions/constraints.py |
| core-09 | 36.817 | 1620 | 4 | yes | core module sqlglot/transforms.py |
| delete-00 | 36.817 | 1620 | 4 | yes | deleted source module sqlglot/optimizer/journal.py |
| multi-02 | 36.817 | 1620 | 4 | yes | core module sqlglot/serde.py |
| multi-03 | 36.817 | 1620 | 4 | yes | core module sqlglot/parsers/tsql.py |
| optimizer_other-00 | 36.817 | 1620 | 4 | yes | core module sqlglot/tokenizer_core.py |
| optimizer_other-04 | 36.817 | 1620 | 4 | yes | core module sqlglot/optimizer/qualify_columns.py |
| optimizer_other-05 | 36.817 | 1620 | 4 | yes | core module sqlglot/optimizer/normalize_identifiers.py |
| optimizer_other-08 | 36.817 | 1620 | 4 | yes | core module sqlglot/optimizer/qualify_columns.py |
| other_dialect-03 | 36.817 | 1620 | 4 | yes | unknown dialect module sqlglot/generators/python.py |
| rename-00 | 36.817 | 1620 | 4 | yes | renamed source module sqlglot/executor/table.py |
| rename-01 | 36.817 | 1620 | 4 | yes | renamed source module sqlglot/parsers/tableau.py |
| multi-00 | 29.277 | 1366 | 3 | no | sqlglot/parsers/hive.py: source module, dialects athena, databricks, dune, hive, presto, spark, spark2, trino; used by sqlglot/executor/__init__.py, sqlglot/optimizer/optimizer.py, sqlglot/optimizer/pushdown_predicates.py |
| base_dialect-09 | 29.185 | 1308 | 3 | no | sqlglot/dialects/mysql.py: source module, dialects athena, doris, drill, dune, mysql, presto, singlestore, starrocks, trino; used by sqlglot/executor/__init__.py, sqlglot/optimizer/optimizer.py, sqlglot/optimizer/pushdown_predicates.py |
| multi-01 | 29.159 | 1327 | 3 | no | sqlglot/anonymize.py: source module |
| base_dialect-14 | 29.157 | 1300 | 3 | no | sqlglot/typing/hive.py: source module, dialects athena, databricks, dune, hive, presto, spark, spark2, trino; used by sqlglot/executor/__init__.py, sqlglot/optimizer/optimizer.py, sqlglot/optimizer/pushdown_predicates.py |
| base_dialect-05 | 29.153 | 1299 | 3 | no | sqlglot/parsers/hive.py: source module, dialects athena, databricks, dune, hive, presto, spark, spark2, trino; used by sqlglot/executor/__init__.py, sqlglot/optimizer/optimizer.py, sqlglot/optimizer/pushdown_predicates.py |
| base_dialect-06 | 29.153 | 1299 | 3 | no | sqlglot/dialects/hive.py: source module, dialects athena, databricks, dune, hive, presto, spark, spark2, trino; used by sqlglot/executor/__init__.py, sqlglot/optimizer/optimizer.py, sqlglot/optimizer/pushdown_predicates.py |
| base_dialect-12 | 29.153 | 1299 | 3 | no | sqlglot/parsers/hive.py: source module, dialects athena, databricks, dune, hive, presto, spark, spark2, trino; used by sqlglot/executor/__init__.py, sqlglot/optimizer/optimizer.py, sqlglot/optimizer/pushdown_predicates.py |
| base_dialect-01 | 29.007 | 1276 | 3 | no | sqlglot/parsers/presto.py: source module, dialects athena, dune, presto, trino; used by sqlglot/executor/__init__.py, sqlglot/optimizer/optimizer.py, sqlglot/optimizer/pushdown_predicates.py |
| base_dialect-13 | 29.007 | 1276 | 3 | no | sqlglot/parsers/presto.py: source module, dialects athena, dune, presto, trino; used by sqlglot/executor/__init__.py, sqlglot/optimizer/optimizer.py, sqlglot/optimizer/pushdown_predicates.py |
| added-00 | 27.454 | 281 | 3 | no | sqlglot/optimizer/extra_rules.py: new module not referenced by any change or pinned import |
| optimizer_other-02 | 27.454 | 326 | 3 | no | sqlglot/optimizer/journal.py: source module; used by sqlglot/executor/__init__.py, sqlglot/optimizer/eliminate_ctes.py, sqlglot/optimizer/optimizer.py, sqlglot/optimizer/pushdown_projections.py |
| optimizer_other-06 | 27.454 | 786 | 3 | no | sqlglot/optimizer/isolate_table_selects.py: source module; used by sqlglot/executor/__init__.py, sqlglot/lineage.py, sqlglot/optimizer/optimizer.py, sqlglot/optimizer/qualify.py |
| optimizer_other-09 | 27.454 | 326 | 3 | no | sqlglot/optimizer/optimizer.py: source module; used by sqlglot/executor/__init__.py |
| fixture-01 | 25.454 | 61 | 2 | no | tests/fixtures/optimizer/tpc-ds/tpc-ds.sql: fixture consumers |
| optimizer_other-07 | 25.454 | 60 | 2 | no | sqlglot/executor/table.py: source module; used by sqlglot/executor/__init__.py, sqlglot/executor/context.py, sqlglot/executor/python.py |
| base_dialect-00 | 17.059 | 1117 | 4 | no | sqlglot/generators/duckdb.py: source module, dialects duckdb |
| base_dialect-03 | 17.059 | 1117 | 4 | no | sqlglot/parsers/duckdb.py: source module, dialects duckdb |
| base_dialect-07 | 17.059 | 1117 | 4 | no | sqlglot/dialects/duckdb.py: source module, dialects duckdb |
| base_dialect-08 | 17.059 | 1117 | 4 | no | sqlglot/dialects/duckdb.py: source module, dialects duckdb |
| base_dialect-10 | 13.689 | 998 | 3 | no | sqlglot/parsers/spark2.py: source module, dialects databricks, spark, spark2 |
| base_dialect-15 | 13.689 | 998 | 3 | no | sqlglot/parsers/spark2.py: source module, dialects databricks, spark, spark2 |
| other_dialect-07 | 13.689 | 998 | 3 | no | sqlglot/parsers/spark.py: source module, dialects databricks, spark, spark2 |
| other_dialect-02 | 13.634 | 1102 | 3 | no | sqlglot/parsers/snowflake.py: source module, dialects snowflake |
| other_dialect-04 | 12.686 | 907 | 3 | no | sqlglot/generators/oracle.py: source module, dialects oracle |
| other_dialect-00 | 10.686 | 311 | 2 | no | sqlglot/parsers/tableau.py: source module, dialects tableau |
| other_dialect-01 | 10.686 | 154 | 2 | no | sqlglot/generators/dune.py: source module, dialects dune |
| other_dialect-05 | 10.686 | 449 | 2 | no | sqlglot/dialects/exasol.py: source module, dialects exasol |
| other_dialect-06 | 10.686 | 311 | 2 | no | sqlglot/generators/tableau.py: source module, dialects tableau |
| other_dialect-09 | 10.686 | 161 | 2 | no | sqlglot/parsers/fabric.py: source module, dialects fabric |
| other_dialect-08 | 10.511 | 121 | 1 | no | sqlglot/dialects/solr.py: source module, dialects solr |
| fixture-05 | 3.006 | 1 | 1 | no | tests/fixtures/optimizer/canonicalize.sql: fixture consumers |
| fixture-02 | 2.596 | 1 | 1 | no | tests/fixtures/optimizer/simplify.sql: fixture consumers |
| fixture-00 | 2.329 | 1 | 1 | no | tests/fixtures/optimizer/unnest_subqueries.sql: fixture consumers |
| fixture-03 | 2.213 | 1 | 1 | no | tests/fixtures/optimizer/canonicalize_internal_names.sql: fixture consumers |
| fixture-04 | 2.105 | 1 | 1 | no | tests/fixtures/optimizer/eliminate_joins.sql: fixture consumers |
| delete-01 | 2.045 | 14 | 1 | no | tests/test_diff.py: changed test file |
| optimizer_other-01 | 2.007 | 29 | 1 | no | sqlglot/anonymize.py: source module |
| optimizer_other-03 | 2.007 | 29 | 1 | no | sqlglot/anonymize.py: source module |
| added-02 | 0.000 | 0 | 0 | no | sqlglot/dialects/newsql.py: new module not referenced by any change or pinned import |

## V2 implementation and results

The opt-in `precision` policy verifies line-preserving edits against the
pinned checkout before comparing the old and new ASTs. It tracks enclosing
function and method ranges, checks their first executable line, and rejects
unverified text, mismatched scopes, signatures, decorators, imports, and
nonlocal changes. Only identical positioned ASTs and comment tokens permit
a function-specific selection. The observations select positive hits in that
case. Executable changes in an otherwise localized body use the submitted
balanced policy.

The build records Python function entry and resume events using
`sys.monitoring` when available, with the previous tracing mechanism as
a fallback. It retains file-level hits for the existing policy and indexes
function observations by the pinned source location and compact test IDs.
The 120-second build checkpoint retained 1,200 of 1,620 test IDs and 5,080
observed source entry sites in 2,414,871 bytes. The previous submitted
checkpoint had 320 unobserved IDs, so this build improved evidence
granularity without improving observed test count. Collection, threads,
subprocesses, and native code are not fully covered.

Dialect inheritance and by-name coverage rules still apply to semantic
changes. The baseline uses explicit dialect descendants plus test name
mentions, shared dialect infrastructure, and a broad set of dialect test
IDs. The recorded positive evidence does not distinguish all incidental
mentions from runtime relationships, so these rules remain in place.
There is no scheduler change.

Build on Linux/amd64 with `docker build --platform linux/amd64 --progress=plain
--tag testradar-v2 .`. The measured build succeeded in **223.443 seconds**.
The pinned Linux unit suite reported **147 passes, zero failures and zero
skips in 23.075 seconds**.

The table uses identical scorer restrictions for each image. Sample CPU is
the median of three instrumented runs with identical stdout to the direct
entrypoint. Mutation CPU is a single run. Failure labels were available for
the sample only, outside the image.

| Dataset | Image / policy | Packing seconds | Missed commits / tests | Full-suite fallbacks | Tests scheduled | Container CPU |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| Samples | Submitted | 480.180 | 0 / 0 | 6 | 19,983 | 0.750751 s |
| Samples | V2 balanced | 480.180 | 0 / 0 | 6 | 19,983 | 0.803591 s |
| Samples | V2 precision | 480.180 | 0 / 0 | 6 | 19,983 | 1.558980 s |
| Seeded mutants | Submitted rebuild | 1524.455 | Unverified | 24 | 63,525 | 0.734606 s |
| Seeded mutants | V2 balanced | 1524.455 | Unverified | 24 | 63,525 | 0.828266 s |
| Seeded mutants | V2 precision | 1524.455 | Unverified | 24 | 63,525 | 2.977709 s |
| Independent mutants | Submitted rebuild | 124.718 | Unverified | 3 | 5,884 | 0.717332 s |
| Independent mutants | V2 balanced | 124.718 | Unverified | 3 | 5,884 | 0.878806 s |
| Independent mutants | V2 precision | 124.718 | Unverified | 3 | 5,884 | 1.806579 s |

The 63 seeded patches were regenerated with the original seed. Four
additional pinned-source patches exercised a core return, a dialect
base-class change, a dynamic import, and a source file rename. All images
emitted valid JSON Lines with at most four workers. Every selected test
set and per-patch cost matched across both image generations and policies.
The historical labeled 1534.850-second mutant result used a different
coverage checkpoint. Current runtimes cannot independently verify its
miss count.

The V2 change did not meet the simulated-score milestone. It narrows a
synthetic quote-spelling substitution, but a hidden source-reading test
could observe the spelling even when Python's AST and source locations
are identical. Function observations cannot rule out that case or
unseen execution paths. Keep the submitted candidate for release.
