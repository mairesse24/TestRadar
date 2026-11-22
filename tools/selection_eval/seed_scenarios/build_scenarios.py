"""Targeted seed-filtering scenarios as run_mutants 'add' ops (full new file)."""
import json, pathlib
R = pathlib.Path('/workspace/sqlglot-src')
def rd(p): return (R / p).read_text(encoding='utf-8')
def sub(text, old, new, count=1):
    assert text.count(old) >= 1, old
    return text.replace(old, new, count)
EJ = 'sqlglot/optimizer/eliminate_joins.py'
CTX = 'sqlglot/executor/context.py'
CH = 'sqlglot/generators/clickhouse.py'
IMP_EJ = 'from sqlglot.optimizer.scope import Scope, find_in_scope, traverse_scope'
IMP_EJ_FMT = 'from sqlglot.optimizer.scope import (\n    Scope,\n    find_in_scope,\n    traverse_scope,\n)'
SEM_EJ = ('    if not unique_outputs:\n        return False', '    if unique_outputs:\n        return False')
SEM_CTX = ('        return table in self.tables', '        return table not in self.tables')
SEM_CH_OLD = 'def _array_sort_sql(self: ClickHouseGenerator, expression: exp.ArraySort) -> str:'
S = {}
def add(name, path, text, note):
    S[name] = {"id": name, "note": note, "ops": [{"kind": "add", "path": path, "content": text}]}
t = rd(EJ)
# 1 mixed file: import reflow (formatting) + semantic edit far away
add("seed-mixed-file-import-reflow", EJ, sub(sub(t, IMP_EJ, IMP_EJ_FMT), *SEM_EJ),
    "import statement reflowed (same tree) + semantic negation in _join_is_used helper")
# 2 same hunk: comment line 2 lines above semantic edit (same diff hunk, separate region)
x = sub(t, '    if not unique_outputs:\n', '    if not unique_outputs:\n', 1)
x = sub(x, '    unique_outputs = ', '    # outputs that make the join unique\n    unique_outputs = ', 1) if '    unique_outputs = ' in t else None
if x:
    add("seed-same-hunk-comment-plus-semantic", EJ, sub(x, *SEM_EJ), "comment and semantic edit in the same hunk")
# 3 same region: comment directly attached to the semantic line
add("seed-same-region-comment-semantic", EJ, sub(t, SEM_EJ[0], '    # inverted on purpose\n' + SEM_EJ[1]),
    "comment adjacent to the semantic edit (one region, kept)")
# 4 same function: formatting reflow in one statement + semantic edit in the same function
c = rd(CTX)
add("seed-same-function-format-semantic", CTX,
    sub(sub(c, '        return self.table.columns if self.tables else ()', '        return (\n            self.table.columns if self.tables else ()\n        )'),
        *SEM_CTX), "reflow in columns() + semantic edit in __contains__")
add("seed-ctx-semantic-only", CTX, sub(c, *SEM_CTX), "control: semantic edit only")
# 5 encoding cookie latin-1 + import reflow + semantic
add("seed-latin1-cookie-mixed", EJ, "# -*- coding: latin-1 -*-\n" + sub(sub(t, IMP_EJ, IMP_EJ_FMT), *SEM_EJ),
    "latin-1 coding cookie + import reflow + semantic edit (filter must refuse)")
# 6 utf-8 cookie + import reflow + semantic
add("seed-utf8-cookie-mixed", EJ, "# -*- coding: utf-8 -*-\n" + sub(sub(t, IMP_EJ, IMP_EJ_FMT), *SEM_EJ),
    "utf-8 coding cookie + import reflow + semantic edit")
# 7 BOM + mixed
add("seed-bom-mixed", EJ, "\ufeff" + sub(sub(t, IMP_EJ, IMP_EJ_FMT), *SEM_EJ),
    "UTF-8 BOM + import reflow + semantic edit (filter must refuse)")
# 8 comment-sensitive: docstring change (semantic) + comment that mentions an import
add("seed-docstring-plus-comment", EJ,
    sub(sub(t, '    Remove unused joins from an expression.', '    Remove unused joins from an expression (docstring edited).'),
        'def eliminate_joins(expression: E) -> E:', '# from sqlglot import optimizer  (note only)\ndef eliminate_joins(expression: E) -> E:'),
    "docstring change (tree change) + comment resembling an import")
# 9 comment-only that looks like an import, + semantic
add("seed-import-like-comment-semantic", EJ,
    sub(sub(t, 'def eliminate_joins(expression: E) -> E:', '# import importlib  (historic note)\ndef eliminate_joins(expression: E) -> E:'), *SEM_EJ),
    "comment mentioning importlib (dynamic-loading text) + semantic edit")
# 10 dependency uncertainty: real import added among formatting changes
add("seed-real-import-among-formatting", EJ,
    sub(sub(sub(t, IMP_EJ, IMP_EJ_FMT), 'import typing as t\n', 'import typing as t\nimport itertools\n'), *SEM_EJ),
    "reflowed import + a real new import + semantic edit (import signal must stay)")
# 11 dynamic import line reformatted in a registry module + semantic edit
d = rd('sqlglot/dialects/__init__.py')
line = next(l for l in d.split('\n') if 'import_module' in l and '(' in l)
add("seed-registry-dynamic-reflow", 'sqlglot/dialects/__init__.py',
    sub(d, line, line + '  # reflow note'), "comment appended to a dynamic-import line in the dialect registry")
# 12 structural hidden: def renamed among formatting changes
add("seed-structural-rename-hidden", EJ,
    sub(sub(sub(t, IMP_EJ, IMP_EJ_FMT), 'def _is_limit_1(', 'def _is_limit_one('), '_is_limit_1(scope)', '_is_limit_one(scope)'),
    "helper renamed (def removed) among an import reflow")
# 13 structural hidden: __all__-like/shared state edit among formatting in clickhouse generator
h = rd(CH)
add("seed-shared-state-hidden", CH,
    sub(sub(h, 'from sqlglot.helper import is_int', 'from sqlglot.helper import (\n    is_int,\n)'),
        SEM_CH_OLD, 'generator.Generator.TRANSFORMS.pop(exp.ArraySort, None)\n\n\n' + SEM_CH_OLD),
    "import reflow + module-level mutation of the base Generator TRANSFORMS")
# 14 dialect generator: import reflow + semantic change in a method
add("seed-dialect-mixed", CH,
    sub(sub(h, 'from sqlglot.helper import is_int', 'from sqlglot.helper import (\n    is_int,\n)'),
        '    def contains_sql(self, expression: exp.Contains) -> str:', '    def contains_sql(self, expression: exp.Contains) -> str:  # noqa'),
    "import reflow + comment on a method (whole file still equivalent)")
add("seed-dialect-mixed-semantic", CH,
    sub(sub(h, 'from sqlglot.helper import is_int', 'from sqlglot.helper import (\n    is_int,\n)'),
        'exp.ArraySort', 'exp.ArraySort', 1),
    "placeholder")
del S["seed-dialect-mixed-semantic"]
# 15 import reflow + semantic edit in clickhouse method
ret = '            offset = f"{offset} ROWS"'
add("seed-dialect-import-reflow-semantic", CH,
    sub(sub(h, 'from sqlglot.helper import is_int', 'from sqlglot.helper import (\n    is_int,\n)'), ret, ret.replace('ROWS', 'ROW')),
    "import reflow + semantic edit in offset_sql")
json.dump({"mutants": list(S.values())}, open('scenarios.json', 'w'), indent=1)
print(len(S), list(S))
