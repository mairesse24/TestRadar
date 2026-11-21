# V3 selection counterfactuals

One rule at a time on the submitted balanced selector. Packing cost uses the submitted scheduler. Reported penalties use available failures only. Runtime selection was held unchanged for these one-rule probes.

## sample (30 patches, fully labeled)

Baseline packing time 480.180 s, 6 fallbacks.

| Relaxation | Packing saved (s) | Missed commits / tests | New missed tests | Changed selections | Observed cost with penalties (s) |
| --- | ---: | ---: | ---: | ---: | ---: |
| core-static: sqlglot/dialects/dialect.py | 0.000 | 0 / 0 | 0 | 0 | 480.180 |
| core-coverage: sqlglot/dialects/dialect.py | 0.000 | 0 / 0 | 0 | 0 | 480.180 |
| core-static: sqlglot/dialects/tsql.py | 0.000 | 0 / 0 | 0 | 0 | 480.180 |
| core-coverage: sqlglot/dialects/tsql.py | 0.000 | 0 / 0 | 0 | 0 | 480.180 |
| core-static: sqlglot/expressions/ddl.py | 0.000 | 0 / 0 | 0 | 0 | 480.180 |
| core-coverage: sqlglot/expressions/ddl.py | 0.000 | 0 / 0 | 0 | 0 | 480.180 |
| core-static: sqlglot/optimizer/scope.py | 0.000 | 0 / 0 | 0 | 0 | 480.180 |
| core-coverage: sqlglot/optimizer/scope.py | 0.000 | 0 / 0 | 0 | 0 | 480.180 |
| core-static: sqlglot/serde.py | 0.000 | 0 / 0 | 0 | 0 | 480.180 |
| core-coverage: sqlglot/serde.py | 0.000 | 0 / 0 | 0 | 0 | 480.180 |
| core-static: sqlglot/transforms.py | 0.000 | 0 / 0 | 0 | 0 | 480.180 |
| core-coverage: sqlglot/transforms.py | 0.000 | 0 / 0 | 0 | 0 | 480.180 |
| dialect-all-ids | 42.343 | 0 / 0 | 0 | 13 | 437.837 |
| dialect-infrastructure | 0.009 | 0 / 0 | 0 | 13 | 480.171 |
| dialect-file-mentions | 10.720 | 0 / 0 | 0 | 13 | 469.460 |
| dialect-test-mentions | 9.586 | 2 / 7 | 7 | 13 | 1070.594 |
| dialect-descendants | 0.000 | 0 / 0 | 0 | 0 | 480.180 |
| dialect-infrastructure: tests/dialects/test_dialect.py | 0.000 | 0 / 0 | 0 | 0 | 480.180 |
| dialect-infrastructure: tests/test_dialect_imports.py | 0.001 | 0 / 0 | 0 | 13 | 480.179 |
| dialect-infrastructure: tests/test_dialect_entry_points.py | 0.009 | 0 / 0 | 0 | 13 | 480.171 |

### Changed patches

| Relaxation | Patch | Packing before → after (s) | Tests before → after | Fallback before → after | Newly missed test IDs |
| --- | --- | ---: | ---: | --- | --- |
| dialect-all-ids | 2qn5 | 12.892 → 10.662 | 938 → 936 | False → False | — |
| dialect-all-ids | 4d63 | 13.502 → 11.272 | 1027 → 1025 | False → False | — |
| dialect-all-ids | 6ze8 | 10.686 → 5.131 | 246 → 244 | False → False | — |
| dialect-all-ids | 8y7j | 12.892 → 10.662 | 938 → 936 | False → False | — |
| dialect-all-ids | 986c | 13.615 → 11.385 | 1029 → 1027 | False → False | — |
| dialect-all-ids | 9u5k | 12.686 → 9.625 | 789 → 787 | False → False | — |
| dialect-all-ids | ary6 | 14.308 → 12.078 | 995 → 993 | False → False | — |
| dialect-all-ids | bj7g | 12.892 → 10.662 | 938 → 936 | False → False | — |
| dialect-all-ids | bvw2 | 10.686 → 5.323 | 227 → 225 | False → False | — |
| dialect-all-ids | f4xa | 11.177 → 7.832 | 657 → 655 | False → False | — |
| dialect-all-ids | fryt | 10.686 → 5.131 | 246 → 244 | False → False | — |
| dialect-all-ids | gbav | 10.686 → 6.832 | 518 → 516 | False → False | — |
| dialect-all-ids | j6uz | 14.308 → 12.078 | 995 → 993 | False → False | — |
| dialect-infrastructure | 2qn5 | 12.892 → 12.891 | 938 → 933 | False → False | — |
| dialect-infrastructure | 4d63 | 13.502 → 13.501 | 1027 → 1023 | False → False | — |
| dialect-infrastructure | 6ze8 | 10.686 → 10.686 | 246 → 241 | False → False | — |
| dialect-infrastructure | 8y7j | 12.892 → 12.891 | 938 → 933 | False → False | — |
| dialect-infrastructure | 986c | 13.615 → 13.614 | 1029 → 1025 | False → False | — |
| dialect-infrastructure | 9u5k | 12.686 → 12.686 | 789 → 784 | False → False | — |
| dialect-infrastructure | ary6 | 14.308 → 14.307 | 995 → 990 | False → False | — |
| dialect-infrastructure | bj7g | 12.892 → 12.891 | 938 → 933 | False → False | — |
| dialect-infrastructure | bvw2 | 10.686 → 10.686 | 227 → 222 | False → False | — |
| dialect-infrastructure | f4xa | 11.177 → 11.175 | 657 → 652 | False → False | — |
| dialect-infrastructure | fryt | 10.686 → 10.686 | 246 → 241 | False → False | — |
| dialect-infrastructure | gbav | 10.686 → 10.686 | 518 → 513 | False → False | — |
| dialect-infrastructure | j6uz | 14.308 → 14.307 | 995 → 990 | False → False | — |
| dialect-file-mentions | 2qn5 | 12.892 → 12.011 | 938 → 219 | False → False | — |
| dialect-file-mentions | 4d63 | 13.502 → 12.686 | 1027 → 366 | False → False | — |
| dialect-file-mentions | 6ze8 | 10.686 → 10.686 | 246 → 146 | False → False | — |
| dialect-file-mentions | 8y7j | 12.892 → 12.011 | 938 → 219 | False → False | — |
| dialect-file-mentions | 986c | 13.615 → 12.064 | 1029 → 338 | False → False | — |
| dialect-file-mentions | 9u5k | 12.686 → 10.728 | 789 → 200 | False → False | — |
| dialect-file-mentions | ary6 | 14.308 → 12.686 | 995 → 307 | False → False | — |
| dialect-file-mentions | bj7g | 12.892 → 12.011 | 938 → 219 | False → False | — |
| dialect-file-mentions | bvw2 | 10.686 → 10.686 | 227 → 125 | False → False | — |
| dialect-file-mentions | f4xa | 11.177 → 10.686 | 657 → 126 | False → False | — |
| dialect-file-mentions | fryt | 10.686 → 10.686 | 246 → 146 | False → False | — |
| dialect-file-mentions | gbav | 10.686 → 10.669 | 518 → 149 | False → False | — |
| dialect-file-mentions | j6uz | 14.308 → 12.686 | 995 → 307 | False → False | — |
| dialect-test-mentions | 2qn5 | 12.892 → 12.055 | 938 → 911 | False → False | — |
| dialect-test-mentions | 4d63 | 13.502 → 12.406 | 1027 → 959 | False → False | — |
| dialect-test-mentions | 6ze8 | 10.686 → 10.686 | 246 → 245 | False → False | — |
| dialect-test-mentions | 8y7j | 12.892 → 12.055 | 938 → 911 | False → False | — |
| dialect-test-mentions | 986c | 13.615 → 12.572 | 1029 → 960 | False → False | — |
| dialect-test-mentions | 9u5k | 12.686 → 11.774 | 789 → 775 | False → False | — |
| dialect-test-mentions | ary6 | 14.308 → 12.332 | 995 → 923 | False → False | tests/test_expressions.py::TestExprs::test_replace_tables, tests/test_optimizer.py::TestOptimizer::test_optimize, tests/test_optimizer.py::TestOptimizer::test_qualify_columns, tests/test_optimizer.py::TestOptimizer::test_qualify_tables, tests/test_optimizer.py::TestOptimizer::test_quote_identifiers, tests/test_parser.py::TestParser::test_ref_type |
| dialect-test-mentions | bj7g | 12.892 → 12.055 | 938 → 911 | False → False | — |
| dialect-test-mentions | bvw2 | 10.686 → 10.686 | 227 → 226 | False → False | — |
| dialect-test-mentions | f4xa | 11.177 → 11.105 | 657 → 656 | False → False | — |
| dialect-test-mentions | fryt | 10.686 → 10.686 | 246 → 245 | False → False | — |
| dialect-test-mentions | gbav | 10.686 → 10.686 | 518 → 517 | False → False | — |
| dialect-test-mentions | j6uz | 14.308 → 12.332 | 995 → 923 | False → False | tests/test_optimizer.py::TestOptimizer::test_annotate_funcs |
| dialect-infrastructure: tests/test_dialect_imports.py | 2qn5 | 12.892 → 12.892 | 938 → 936 | False → False | — |
| dialect-infrastructure: tests/test_dialect_imports.py | 4d63 | 13.502 → 13.502 | 1027 → 1026 | False → False | — |
| dialect-infrastructure: tests/test_dialect_imports.py | 6ze8 | 10.686 → 10.686 | 246 → 244 | False → False | — |
| dialect-infrastructure: tests/test_dialect_imports.py | 8y7j | 12.892 → 12.892 | 938 → 936 | False → False | — |
| dialect-infrastructure: tests/test_dialect_imports.py | 986c | 13.615 → 13.615 | 1029 → 1028 | False → False | — |
| dialect-infrastructure: tests/test_dialect_imports.py | 9u5k | 12.686 → 12.686 | 789 → 787 | False → False | — |
| dialect-infrastructure: tests/test_dialect_imports.py | ary6 | 14.308 → 14.308 | 995 → 993 | False → False | — |
| dialect-infrastructure: tests/test_dialect_imports.py | bj7g | 12.892 → 12.892 | 938 → 936 | False → False | — |
| dialect-infrastructure: tests/test_dialect_imports.py | bvw2 | 10.686 → 10.686 | 227 → 225 | False → False | — |
| dialect-infrastructure: tests/test_dialect_imports.py | f4xa | 11.177 → 11.176 | 657 → 655 | False → False | — |
| dialect-infrastructure: tests/test_dialect_imports.py | fryt | 10.686 → 10.686 | 246 → 244 | False → False | — |
| dialect-infrastructure: tests/test_dialect_imports.py | gbav | 10.686 → 10.686 | 518 → 516 | False → False | — |
| dialect-infrastructure: tests/test_dialect_imports.py | j6uz | 14.308 → 14.308 | 995 → 993 | False → False | — |
| dialect-infrastructure: tests/test_dialect_entry_points.py | 2qn5 | 12.892 → 12.891 | 938 → 935 | False → False | — |
| dialect-infrastructure: tests/test_dialect_entry_points.py | 4d63 | 13.502 → 13.501 | 1027 → 1024 | False → False | — |
| dialect-infrastructure: tests/test_dialect_entry_points.py | 6ze8 | 10.686 → 10.686 | 246 → 243 | False → False | — |
| dialect-infrastructure: tests/test_dialect_entry_points.py | 8y7j | 12.892 → 12.891 | 938 → 935 | False → False | — |
| dialect-infrastructure: tests/test_dialect_entry_points.py | 986c | 13.615 → 13.614 | 1029 → 1026 | False → False | — |
| dialect-infrastructure: tests/test_dialect_entry_points.py | 9u5k | 12.686 → 12.686 | 789 → 786 | False → False | — |
| dialect-infrastructure: tests/test_dialect_entry_points.py | ary6 | 14.308 → 14.307 | 995 → 992 | False → False | — |
| dialect-infrastructure: tests/test_dialect_entry_points.py | bj7g | 12.892 → 12.891 | 938 → 935 | False → False | — |
| dialect-infrastructure: tests/test_dialect_entry_points.py | bvw2 | 10.686 → 10.686 | 227 → 224 | False → False | — |
| dialect-infrastructure: tests/test_dialect_entry_points.py | f4xa | 11.177 → 11.175 | 657 → 654 | False → False | — |
| dialect-infrastructure: tests/test_dialect_entry_points.py | fryt | 10.686 → 10.686 | 246 → 243 | False → False | — |
| dialect-infrastructure: tests/test_dialect_entry_points.py | gbav | 10.686 → 10.686 | 518 → 515 | False → False | — |
| dialect-infrastructure: tests/test_dialect_entry_points.py | j6uz | 14.308 → 14.307 | 995 → 992 | False → False | — |

## seeded-mutants (63 patches, unlabeled)

Baseline packing time 1524.455 s, 24 fallbacks.

| Relaxation | Packing saved (s) | Missed commits / tests | New missed tests | Changed selections | Observed cost with penalties (s) |
| --- | ---: | ---: | ---: | ---: | ---: |
| core-static: sqlglot/expressions/constraints.py | 0.000 | unverified | unverified | 0 | 1524.455 |
| core-coverage: sqlglot/expressions/constraints.py | 0.000 | unverified | unverified | 0 | 1524.455 |
| core-static: sqlglot/expressions/datatypes.py | 0.000 | unverified | unverified | 0 | 1524.455 |
| core-coverage: sqlglot/expressions/datatypes.py | 0.000 | unverified | unverified | 0 | 1524.455 |
| core-static: sqlglot/expressions/ddl.py | 0.000 | unverified | unverified | 0 | 1524.455 |
| core-coverage: sqlglot/expressions/ddl.py | 0.000 | unverified | unverified | 0 | 1524.455 |
| core-static: sqlglot/expressions/properties.py | 0.000 | unverified | unverified | 0 | 1524.455 |
| core-coverage: sqlglot/expressions/properties.py | 0.000 | unverified | unverified | 0 | 1524.455 |
| core-static: sqlglot/expressions/query.py | 0.000 | unverified | unverified | 0 | 1524.455 |
| core-coverage: sqlglot/expressions/query.py | 0.000 | unverified | unverified | 0 | 1524.455 |
| core-static: sqlglot/optimizer/normalize_identifiers.py | 0.000 | unverified | unverified | 0 | 1524.455 |
| core-coverage: sqlglot/optimizer/normalize_identifiers.py | 0.000 | unverified | unverified | 0 | 1524.455 |
| core-static: sqlglot/optimizer/qualify_columns.py | 0.000 | unverified | unverified | 0 | 1524.455 |
| core-coverage: sqlglot/optimizer/qualify_columns.py | 0.000 | unverified | unverified | 0 | 1524.455 |
| core-static: sqlglot/parsers/tsql.py | 0.000 | unverified | unverified | 0 | 1524.455 |
| core-coverage: sqlglot/parsers/tsql.py | 0.000 | unverified | unverified | 0 | 1524.455 |
| core-static: sqlglot/serde.py | 0.000 | unverified | unverified | 0 | 1524.455 |
| core-coverage: sqlglot/serde.py | 0.000 | unverified | unverified | 0 | 1524.455 |
| core-static: sqlglot/tokenizer_core.py | 0.000 | unverified | unverified | 0 | 1524.455 |
| core-coverage: sqlglot/tokenizer_core.py | 0.000 | unverified | unverified | 0 | 1524.455 |
| core-static: sqlglot/tokens.py | 0.000 | unverified | unverified | 0 | 1524.455 |
| core-coverage: sqlglot/tokens.py | 0.000 | unverified | unverified | 0 | 1524.455 |
| core-static: sqlglot/transforms.py | 0.000 | unverified | unverified | 0 | 1524.455 |
| core-coverage: sqlglot/transforms.py | 0.000 | unverified | unverified | 0 | 1524.455 |
| core-static: sqlglot/typing/tsql.py | 0.000 | unverified | unverified | 0 | 1524.455 |
| core-coverage: sqlglot/typing/tsql.py | 0.000 | unverified | unverified | 0 | 1524.455 |
| dialect-all-ids | 64.139 | unverified | unverified | 24 | 1460.316 |
| dialect-infrastructure | 5.287 | unverified | unverified | 24 | 1519.168 |
| dialect-file-mentions | 20.681 | unverified | unverified | 17 | 1503.774 |
| dialect-test-mentions | 24.551 | unverified | unverified | 22 | 1499.904 |
| dialect-descendants | 0.000 | unverified | unverified | 0 | 1524.455 |
| dialect-infrastructure: tests/dialects/test_dialect.py | 5.253 | unverified | unverified | 3 | 1519.202 |
| dialect-infrastructure: tests/test_dialect_imports.py | 0.010 | unverified | unverified | 23 | 1524.445 |
| dialect-infrastructure: tests/test_dialect_entry_points.py | 0.020 | unverified | unverified | 24 | 1524.435 |

### Changed patches

| Relaxation | Patch | Packing before → after (s) | Tests before → after | Fallback before → after | Newly missed test IDs |
| --- | --- | ---: | ---: | --- | --- |
| dialect-all-ids | base_dialect-00 | 17.059 → 15.386 | 1117 → 1115 | False → False | unverified |
| dialect-all-ids | base_dialect-01 | 29.007 → 27.454 | 1276 → 1274 | False → False | unverified |
| dialect-all-ids | base_dialect-03 | 17.059 → 15.386 | 1117 → 1115 | False → False | unverified |
| dialect-all-ids | base_dialect-05 | 29.153 → 27.454 | 1299 → 1297 | False → False | unverified |
| dialect-all-ids | base_dialect-06 | 29.153 → 27.454 | 1299 → 1297 | False → False | unverified |
| dialect-all-ids | base_dialect-07 | 17.059 → 15.386 | 1117 → 1115 | False → False | unverified |
| dialect-all-ids | base_dialect-08 | 17.059 → 15.386 | 1117 → 1115 | False → False | unverified |
| dialect-all-ids | base_dialect-09 | 29.185 → 27.454 | 1308 → 1306 | False → False | unverified |
| dialect-all-ids | base_dialect-10 | 13.689 → 11.459 | 998 → 996 | False → False | unverified |
| dialect-all-ids | base_dialect-12 | 29.153 → 27.454 | 1299 → 1297 | False → False | unverified |
| dialect-all-ids | base_dialect-13 | 29.007 → 27.454 | 1276 → 1274 | False → False | unverified |
| dialect-all-ids | base_dialect-14 | 29.157 → 27.454 | 1300 → 1298 | False → False | unverified |
| dialect-all-ids | base_dialect-15 | 13.689 → 11.459 | 998 → 996 | False → False | unverified |
| dialect-all-ids | multi-00 | 29.277 → 29.276 | 1366 → 1365 | False → False | unverified |
| dialect-all-ids | multi-01 | 29.159 → 27.454 | 1327 → 1325 | False → False | unverified |
| dialect-all-ids | other_dialect-00 | 10.686 → 5.582 | 311 → 309 | False → False | unverified |
| dialect-all-ids | other_dialect-01 | 10.686 → 4.051 | 154 → 152 | False → False | unverified |
| dialect-all-ids | other_dialect-02 | 13.634 → 13.633 | 1102 → 1101 | False → False | unverified |
| dialect-all-ids | other_dialect-04 | 12.686 → 9.712 | 907 → 905 | False → False | unverified |
| dialect-all-ids | other_dialect-05 | 10.686 → 6.408 | 449 → 447 | False → False | unverified |
| dialect-all-ids | other_dialect-06 | 10.686 → 5.582 | 311 → 309 | False → False | unverified |
| dialect-all-ids | other_dialect-07 | 13.689 → 11.459 | 998 → 996 | False → False | unverified |
| dialect-all-ids | other_dialect-08 | 10.511 → 3.821 | 121 → 119 | False → False | unverified |
| dialect-all-ids | other_dialect-09 | 10.686 → 4.058 | 161 → 159 | False → False | unverified |
| dialect-infrastructure | base_dialect-00 | 17.059 → 17.058 | 1117 → 1112 | False → False | unverified |
| dialect-infrastructure | base_dialect-01 | 29.007 → 29.006 | 1276 → 1272 | False → False | unverified |
| dialect-infrastructure | base_dialect-03 | 17.059 → 17.058 | 1117 → 1112 | False → False | unverified |
| dialect-infrastructure | base_dialect-05 | 29.153 → 29.152 | 1299 → 1295 | False → False | unverified |
| dialect-infrastructure | base_dialect-06 | 29.153 → 29.152 | 1299 → 1295 | False → False | unverified |
| dialect-infrastructure | base_dialect-07 | 17.059 → 17.058 | 1117 → 1112 | False → False | unverified |
| dialect-infrastructure | base_dialect-08 | 17.059 → 17.058 | 1117 → 1112 | False → False | unverified |
| dialect-infrastructure | base_dialect-09 | 29.185 → 29.183 | 1308 → 1304 | False → False | unverified |
| dialect-infrastructure | base_dialect-10 | 13.689 → 13.687 | 998 → 993 | False → False | unverified |
| dialect-infrastructure | base_dialect-12 | 29.153 → 29.152 | 1299 → 1295 | False → False | unverified |
| dialect-infrastructure | base_dialect-13 | 29.007 → 29.006 | 1276 → 1272 | False → False | unverified |
| dialect-infrastructure | base_dialect-14 | 29.157 → 29.156 | 1300 → 1296 | False → False | unverified |
| dialect-infrastructure | base_dialect-15 | 13.689 → 13.687 | 998 → 993 | False → False | unverified |
| dialect-infrastructure | multi-00 | 29.277 → 29.276 | 1366 → 1363 | False → False | unverified |
| dialect-infrastructure | multi-01 | 29.159 → 29.157 | 1327 → 1323 | False → False | unverified |
| dialect-infrastructure | other_dialect-00 | 10.686 → 10.686 | 311 → 306 | False → False | unverified |
| dialect-infrastructure | other_dialect-01 | 10.686 → 8.947 | 154 → 37 | False → False | unverified |
| dialect-infrastructure | other_dialect-02 | 13.634 → 13.633 | 1102 → 1098 | False → False | unverified |
| dialect-infrastructure | other_dialect-04 | 12.686 → 12.686 | 907 → 902 | False → False | unverified |
| dialect-infrastructure | other_dialect-05 | 10.686 → 10.686 | 449 → 444 | False → False | unverified |
| dialect-infrastructure | other_dialect-06 | 10.686 → 10.686 | 311 → 306 | False → False | unverified |
| dialect-infrastructure | other_dialect-07 | 13.689 → 13.687 | 998 → 993 | False → False | unverified |
| dialect-infrastructure | other_dialect-08 | 10.511 → 8.717 | 121 → 4 | False → False | unverified |
| dialect-infrastructure | other_dialect-09 | 10.686 → 8.954 | 161 → 44 | False → False | unverified |
| dialect-file-mentions | base_dialect-01 | 29.007 → 27.454 | 1276 → 535 | False → False | unverified |
| dialect-file-mentions | base_dialect-05 | 29.153 → 27.645 | 1299 → 662 | False → False | unverified |
| dialect-file-mentions | base_dialect-06 | 29.153 → 27.454 | 1299 → 621 | False → False | unverified |
| dialect-file-mentions | base_dialect-09 | 29.185 → 27.454 | 1308 → 679 | False → False | unverified |
| dialect-file-mentions | base_dialect-10 | 13.689 → 11.950 | 998 → 219 | False → False | unverified |
| dialect-file-mentions | base_dialect-12 | 29.153 → 27.645 | 1299 → 662 | False → False | unverified |
| dialect-file-mentions | base_dialect-13 | 29.007 → 27.454 | 1276 → 535 | False → False | unverified |
| dialect-file-mentions | base_dialect-14 | 29.157 → 27.454 | 1300 → 623 | False → False | unverified |
| dialect-file-mentions | base_dialect-15 | 13.689 → 11.950 | 998 → 219 | False → False | unverified |
| dialect-file-mentions | multi-01 | 29.159 → 27.454 | 1327 → 650 | False → False | unverified |
| dialect-file-mentions | other_dialect-00 | 10.686 → 10.520 | 311 → 122 | False → False | unverified |
| dialect-file-mentions | other_dialect-01 | 10.686 → 10.550 | 154 → 122 | False → False | unverified |
| dialect-file-mentions | other_dialect-04 | 12.686 → 10.686 | 907 → 190 | False → False | unverified |
| dialect-file-mentions | other_dialect-05 | 10.686 → 10.686 | 449 → 143 | False → False | unverified |
| dialect-file-mentions | other_dialect-06 | 10.686 → 10.526 | 311 → 123 | False → False | unverified |
| dialect-file-mentions | other_dialect-07 | 13.689 → 12.062 | 998 → 217 | False → False | unverified |
| dialect-file-mentions | other_dialect-09 | 10.686 → 10.532 | 161 → 128 | False → False | unverified |
| dialect-test-mentions | base_dialect-00 | 17.059 → 12.573 | 1117 → 1027 | False → False | unverified |
| dialect-test-mentions | base_dialect-01 | 29.007 → 28.990 | 1276 → 1263 | False → False | unverified |
| dialect-test-mentions | base_dialect-03 | 17.059 → 12.579 | 1117 → 1028 | False → False | unverified |
| dialect-test-mentions | base_dialect-05 | 29.153 → 28.994 | 1299 → 1265 | False → False | unverified |
| dialect-test-mentions | base_dialect-06 | 29.153 → 28.994 | 1299 → 1265 | False → False | unverified |
| dialect-test-mentions | base_dialect-07 | 17.059 → 12.579 | 1117 → 1028 | False → False | unverified |
| dialect-test-mentions | base_dialect-08 | 17.059 → 12.579 | 1117 → 1028 | False → False | unverified |
| dialect-test-mentions | base_dialect-09 | 29.185 → 29.015 | 1308 → 1279 | False → False | unverified |
| dialect-test-mentions | base_dialect-10 | 13.689 → 12.388 | 998 → 967 | False → False | unverified |
| dialect-test-mentions | base_dialect-12 | 29.153 → 28.994 | 1299 → 1265 | False → False | unverified |
| dialect-test-mentions | base_dialect-13 | 29.007 → 28.990 | 1276 → 1263 | False → False | unverified |
| dialect-test-mentions | base_dialect-14 | 29.157 → 28.998 | 1300 → 1266 | False → False | unverified |
| dialect-test-mentions | base_dialect-15 | 13.689 → 12.388 | 998 → 967 | False → False | unverified |
| dialect-test-mentions | multi-00 | 29.277 → 29.103 | 1366 → 1317 | False → False | unverified |
| dialect-test-mentions | multi-01 | 29.159 → 29.000 | 1327 → 1295 | False → False | unverified |
| dialect-test-mentions | other_dialect-00 | 10.686 → 10.686 | 311 → 310 | False → False | unverified |
| dialect-test-mentions | other_dialect-02 | 13.634 → 12.606 | 1102 → 1036 | False → False | unverified |
| dialect-test-mentions | other_dialect-04 | 12.686 → 12.165 | 907 → 898 | False → False | unverified |
| dialect-test-mentions | other_dialect-05 | 10.686 → 10.686 | 449 → 447 | False → False | unverified |
| dialect-test-mentions | other_dialect-06 | 10.686 → 10.686 | 311 → 310 | False → False | unverified |
| dialect-test-mentions | other_dialect-07 | 13.689 → 12.388 | 998 → 967 | False → False | unverified |
| dialect-test-mentions | other_dialect-09 | 10.686 → 10.686 | 161 → 160 | False → False | unverified |
| dialect-infrastructure: tests/dialects/test_dialect.py | other_dialect-01 | 10.686 → 8.951 | 154 → 42 | False → False | unverified |
| dialect-infrastructure: tests/dialects/test_dialect.py | other_dialect-08 | 10.511 → 8.721 | 121 → 9 | False → False | unverified |
| dialect-infrastructure: tests/dialects/test_dialect.py | other_dialect-09 | 10.686 → 8.958 | 161 → 49 | False → False | unverified |
| dialect-infrastructure: tests/test_dialect_imports.py | base_dialect-00 | 17.059 → 17.058 | 1117 → 1115 | False → False | unverified |
| dialect-infrastructure: tests/test_dialect_imports.py | base_dialect-01 | 29.007 → 29.007 | 1276 → 1275 | False → False | unverified |
| dialect-infrastructure: tests/test_dialect_imports.py | base_dialect-03 | 17.059 → 17.058 | 1117 → 1115 | False → False | unverified |
| dialect-infrastructure: tests/test_dialect_imports.py | base_dialect-05 | 29.153 → 29.153 | 1299 → 1298 | False → False | unverified |
| dialect-infrastructure: tests/test_dialect_imports.py | base_dialect-06 | 29.153 → 29.153 | 1299 → 1298 | False → False | unverified |
| dialect-infrastructure: tests/test_dialect_imports.py | base_dialect-07 | 17.059 → 17.058 | 1117 → 1115 | False → False | unverified |
| dialect-infrastructure: tests/test_dialect_imports.py | base_dialect-08 | 17.059 → 17.058 | 1117 → 1115 | False → False | unverified |
| dialect-infrastructure: tests/test_dialect_imports.py | base_dialect-09 | 29.185 → 29.184 | 1308 → 1307 | False → False | unverified |
| dialect-infrastructure: tests/test_dialect_imports.py | base_dialect-10 | 13.689 → 13.688 | 998 → 996 | False → False | unverified |
| dialect-infrastructure: tests/test_dialect_imports.py | base_dialect-12 | 29.153 → 29.153 | 1299 → 1298 | False → False | unverified |
| dialect-infrastructure: tests/test_dialect_imports.py | base_dialect-13 | 29.007 → 29.007 | 1276 → 1275 | False → False | unverified |
| dialect-infrastructure: tests/test_dialect_imports.py | base_dialect-14 | 29.157 → 29.157 | 1300 → 1299 | False → False | unverified |
| dialect-infrastructure: tests/test_dialect_imports.py | base_dialect-15 | 13.689 → 13.688 | 998 → 996 | False → False | unverified |
| dialect-infrastructure: tests/test_dialect_imports.py | multi-01 | 29.159 → 29.158 | 1327 → 1326 | False → False | unverified |
| dialect-infrastructure: tests/test_dialect_imports.py | other_dialect-00 | 10.686 → 10.686 | 311 → 309 | False → False | unverified |
| dialect-infrastructure: tests/test_dialect_imports.py | other_dialect-01 | 10.686 → 10.686 | 154 → 152 | False → False | unverified |
| dialect-infrastructure: tests/test_dialect_imports.py | other_dialect-02 | 13.634 → 13.634 | 1102 → 1101 | False → False | unverified |
| dialect-infrastructure: tests/test_dialect_imports.py | other_dialect-04 | 12.686 → 12.686 | 907 → 905 | False → False | unverified |
| dialect-infrastructure: tests/test_dialect_imports.py | other_dialect-05 | 10.686 → 10.686 | 449 → 447 | False → False | unverified |
| dialect-infrastructure: tests/test_dialect_imports.py | other_dialect-06 | 10.686 → 10.686 | 311 → 309 | False → False | unverified |
| dialect-infrastructure: tests/test_dialect_imports.py | other_dialect-07 | 13.689 → 13.688 | 998 → 996 | False → False | unverified |
| dialect-infrastructure: tests/test_dialect_imports.py | other_dialect-08 | 10.511 → 10.510 | 121 → 119 | False → False | unverified |
| dialect-infrastructure: tests/test_dialect_imports.py | other_dialect-09 | 10.686 → 10.686 | 161 → 159 | False → False | unverified |
| dialect-infrastructure: tests/test_dialect_entry_points.py | base_dialect-00 | 17.059 → 17.058 | 1117 → 1114 | False → False | unverified |
| dialect-infrastructure: tests/test_dialect_entry_points.py | base_dialect-01 | 29.007 → 29.006 | 1276 → 1273 | False → False | unverified |
| dialect-infrastructure: tests/test_dialect_entry_points.py | base_dialect-03 | 17.059 → 17.058 | 1117 → 1114 | False → False | unverified |
| dialect-infrastructure: tests/test_dialect_entry_points.py | base_dialect-05 | 29.153 → 29.152 | 1299 → 1296 | False → False | unverified |
| dialect-infrastructure: tests/test_dialect_entry_points.py | base_dialect-06 | 29.153 → 29.152 | 1299 → 1296 | False → False | unverified |
| dialect-infrastructure: tests/test_dialect_entry_points.py | base_dialect-07 | 17.059 → 17.058 | 1117 → 1114 | False → False | unverified |
| dialect-infrastructure: tests/test_dialect_entry_points.py | base_dialect-08 | 17.059 → 17.058 | 1117 → 1114 | False → False | unverified |
| dialect-infrastructure: tests/test_dialect_entry_points.py | base_dialect-09 | 29.185 → 29.184 | 1308 → 1305 | False → False | unverified |
| dialect-infrastructure: tests/test_dialect_entry_points.py | base_dialect-10 | 13.689 → 13.688 | 998 → 995 | False → False | unverified |
| dialect-infrastructure: tests/test_dialect_entry_points.py | base_dialect-12 | 29.153 → 29.152 | 1299 → 1296 | False → False | unverified |
| dialect-infrastructure: tests/test_dialect_entry_points.py | base_dialect-13 | 29.007 → 29.006 | 1276 → 1273 | False → False | unverified |
| dialect-infrastructure: tests/test_dialect_entry_points.py | base_dialect-14 | 29.157 → 29.156 | 1300 → 1297 | False → False | unverified |
| dialect-infrastructure: tests/test_dialect_entry_points.py | base_dialect-15 | 13.689 → 13.688 | 998 → 995 | False → False | unverified |
| dialect-infrastructure: tests/test_dialect_entry_points.py | multi-00 | 29.277 → 29.276 | 1366 → 1363 | False → False | unverified |
| dialect-infrastructure: tests/test_dialect_entry_points.py | multi-01 | 29.159 → 29.158 | 1327 → 1324 | False → False | unverified |
| dialect-infrastructure: tests/test_dialect_entry_points.py | other_dialect-00 | 10.686 → 10.686 | 311 → 308 | False → False | unverified |
| dialect-infrastructure: tests/test_dialect_entry_points.py | other_dialect-01 | 10.686 → 10.686 | 154 → 151 | False → False | unverified |
| dialect-infrastructure: tests/test_dialect_entry_points.py | other_dialect-02 | 13.634 → 13.633 | 1102 → 1099 | False → False | unverified |
| dialect-infrastructure: tests/test_dialect_entry_points.py | other_dialect-04 | 12.686 → 12.686 | 907 → 904 | False → False | unverified |
| dialect-infrastructure: tests/test_dialect_entry_points.py | other_dialect-05 | 10.686 → 10.686 | 449 → 446 | False → False | unverified |
| dialect-infrastructure: tests/test_dialect_entry_points.py | other_dialect-06 | 10.686 → 10.686 | 311 → 308 | False → False | unverified |
| dialect-infrastructure: tests/test_dialect_entry_points.py | other_dialect-07 | 13.689 → 13.688 | 998 → 995 | False → False | unverified |
| dialect-infrastructure: tests/test_dialect_entry_points.py | other_dialect-08 | 10.511 → 10.508 | 121 → 118 | False → False | unverified |
| dialect-infrastructure: tests/test_dialect_entry_points.py | other_dialect-09 | 10.686 → 10.686 | 161 → 158 | False → False | unverified |

## Independent pytest verification

| Probe | Attempted | Baseline passes | Mutated outcomes reported | Verified failures | Collection errors |
| --- | ---: | ---: | ---: | ---: | --- |
| parser-body | 16 | 16 | 16 | 16 | 0 |
| generator-body | 16 | 16 | 16 | 15 | 0 |
| postgres-generator-body | 24 | 24 | 24 | 3 | 0 |
| dialect-loader-body | 18 | 18 | 0 | 16 | 2 |

## independent-probes (4 patches, verified failures only)

Baseline packing time 124.718 s, 3 fallbacks.

| Relaxation | Packing saved (s) | Missed commits / tests | New missed tests | Changed selections | Observed cost with penalties (s) |
| --- | ---: | ---: | ---: | ---: | ---: |
| core-static: sqlglot/dialects/__init__.py | 34.816 | 1 / 16 | 16 | 1 | 389.902 |
| core-coverage: sqlglot/dialects/__init__.py | 34.816 | 1 / 16 | 16 | 1 | 389.902 |
| core-static: sqlglot/generator.py | 0.000 | 0 / 0 | 0 | 0 | 124.718 |
| core-coverage: sqlglot/generator.py | 0.000 | 0 / 0 | 0 | 0 | 124.718 |
| core-static: sqlglot/parser.py | 0.000 | 0 / 0 | 0 | 0 | 124.718 |
| core-coverage: sqlglot/parser.py | 0.000 | 0 / 0 | 0 | 0 | 124.718 |
| dialect-all-ids | 2.230 | 0 / 0 | 0 | 1 | 122.488 |
| dialect-infrastructure | 0.001 | 0 / 0 | 0 | 1 | 124.717 |
| dialect-file-mentions | 1.322 | 0 / 0 | 0 | 1 | 123.396 |
| dialect-test-mentions | 1.848 | 0 / 0 | 0 | 1 | 122.870 |
| dialect-descendants | 0.000 | 0 / 0 | 0 | 0 | 124.718 |
| dialect-infrastructure: tests/dialects/test_dialect.py | 0.000 | 0 / 0 | 0 | 0 | 124.718 |
| dialect-infrastructure: tests/test_dialect_imports.py | 0.000 | 0 / 0 | 0 | 1 | 124.718 |
| dialect-infrastructure: tests/test_dialect_entry_points.py | 0.001 | 0 / 0 | 0 | 1 | 124.717 |

### Changed patches

| Relaxation | Patch | Packing before → after (s) | Tests before → after | Fallback before → after | Newly missed test IDs |
| --- | --- | ---: | ---: | --- | --- |
| core-static: sqlglot/dialects/__init__.py | dialect-loader-body | 36.817 → 2.001 | 1620 → 2 | True → False | tests/dialects/test_dialect.py::TestDialect::test_agg, tests/dialects/test_dialect.py::TestDialect::test_alias, tests/dialects/test_dialect.py::TestDialect::test_array, tests/dialects/test_dialect.py::TestDialect::test_array_any, tests/dialects/test_dialect.py::TestDialect::test_ascii, tests/dialects/test_dialect.py::TestDialect::test_between, tests/dialects/test_dialect.py::TestDialect::test_cast, tests/dialects/test_dialect.py::TestDialect::test_cast_to_user_defined_type, tests/dialects/test_postgres.py::TestPostgres::test_alter_drop_column, tests/dialects/test_postgres.py::TestPostgres::test_alter_mixed_actions, tests/dialects/test_postgres.py::TestPostgres::test_analyze, tests/dialects/test_postgres.py::TestPostgres::test_array_length, tests/dialects/test_postgres.py::TestPostgres::test_array_offset, tests/dialects/test_postgres.py::TestPostgres::test_begin_transaction, tests/dialects/test_postgres.py::TestPostgres::test_bool_or, tests/dialects/test_postgres.py::TestPostgres::test_called_on_null_input_malformed |
| core-coverage: sqlglot/dialects/__init__.py | dialect-loader-body | 36.817 → 2.001 | 1620 → 2 | True → False | tests/dialects/test_dialect.py::TestDialect::test_agg, tests/dialects/test_dialect.py::TestDialect::test_alias, tests/dialects/test_dialect.py::TestDialect::test_array, tests/dialects/test_dialect.py::TestDialect::test_array_any, tests/dialects/test_dialect.py::TestDialect::test_ascii, tests/dialects/test_dialect.py::TestDialect::test_between, tests/dialects/test_dialect.py::TestDialect::test_cast, tests/dialects/test_dialect.py::TestDialect::test_cast_to_user_defined_type, tests/dialects/test_postgres.py::TestPostgres::test_alter_drop_column, tests/dialects/test_postgres.py::TestPostgres::test_alter_mixed_actions, tests/dialects/test_postgres.py::TestPostgres::test_analyze, tests/dialects/test_postgres.py::TestPostgres::test_array_length, tests/dialects/test_postgres.py::TestPostgres::test_array_offset, tests/dialects/test_postgres.py::TestPostgres::test_begin_transaction, tests/dialects/test_postgres.py::TestPostgres::test_bool_or, tests/dialects/test_postgres.py::TestPostgres::test_called_on_null_input_malformed |
| dialect-all-ids | postgres-generator-body | 14.267 → 12.037 | 1024 → 1022 | False → False | — |
| dialect-infrastructure | postgres-generator-body | 14.267 → 14.266 | 1024 → 1019 | False → False | — |
| dialect-file-mentions | postgres-generator-body | 14.267 → 12.945 | 1024 → 310 | False → False | — |
| dialect-test-mentions | postgres-generator-body | 14.267 → 12.419 | 1024 → 971 | False → False | — |
| dialect-infrastructure: tests/test_dialect_imports.py | postgres-generator-body | 14.267 → 14.267 | 1024 → 1022 | False → False | — |
| dialect-infrastructure: tests/test_dialect_entry_points.py | postgres-generator-body | 14.267 → 14.266 | 1024 → 1021 | False → False | — |

## Recommendation and revalidation

The two highest-value zero-known-miss changes were removing blanket
dialect IDs and removing whole-file dialect-name mentions. They saved
42.343 and 10.720 sample seconds respectively in isolation. The two
changes are combined only for patches consisting of direct, modified
dialect-family modules. The opt-in `dialect-focused` policy keeps direct
dialect test files, test-level name mentions, inherited dialects, shared
infrastructure, dependency candidates, and positive coverage hits.
Mixed, core, structural, and uncertain cases keep the existing policy.
The Docker default remains balanced.

Dropping test-level name mentions lost seven failures across two samples.
Neither individual core relaxation saved time on the labeled samples.
An independent loader mutation showed the consequence of relaxing the
registry fallback: both direct-core variants dropped 16 independently
verified collection failures, turning a 34.816-second packing saving
into a 265.184-second regression after the miss penalty.

The restricted Linux runtime was built as a local overlay on the
submitted image with only the two selector source files replaced. This
holds maps, scheduler, and Python runtime constant. All runs emitted
valid JSON Lines and used at most four workers. The Linux unit suite
reported 147 passes, zero failures, and zero skips in 28.205 seconds.

| Dataset | Balanced seconds | Focused seconds | Tests before → after | Fallbacks | Known misses in focused |
| --- | ---: | ---: | ---: | ---: | --- |
| 30 labeled samples | 480.180 | 424.380 | 19,983 → 14,078 | 6 | 0 commits, 0 tests |
| 63 regenerated seeded mutants | 1524.455 | 1444.534 | 63,525 → 54,937 | 24 | Unverified |
| 4 targeted independent probes | 124.718 | 121.166 | 5,884 → 5,168 | 3 | 0 of 50 verified failing test instances |

Sample median cgroup CPU across three measured runs was 1.281409 seconds
for balanced and 1.186828 seconds for focused. The verified probe labels
cover a subset of pytest nodes and include import failures for matching
files when collection fails. The original 63 mutant failure labels were
unavailable, so their values are packing time only.

The combined [per-patch deltas](V3_FOCUSED_RESULTS.md) cover all changed
selections. A hidden test can still choose a dialect through shared
fixtures, generated names, or subprocesses with no direct mention or
observed coverage. Keep focused mode experimental pending broader
labeled validation. It can be evaluated with `--policy dialect-focused`.
