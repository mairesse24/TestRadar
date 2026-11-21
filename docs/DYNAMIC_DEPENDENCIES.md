# Dynamic dependency evidence

Generate the graph and optional build-time report from a clean checkout of the
pinned SQLGlot revision:

```sh
python -m analyzer.dependency_graph /sqlglot dependencies.json --evidence import-evidence.json
```

The graph still uses `testradar-imports-v1`. The existing loader and runtime
selector can consume it without interface changes. Keep the evidence report
outside the runtime image if it is only needed for review. It records literal
targets, bounded package-prefix candidates, unresolved internal imports, and
unsupported or unknown sites. Coverage is a separate artifact and keeps its
existing positive-evidence semantics.

| Pinned site | Source evidence and limits |
| --- | --- |
| `sqlglot/dialects/__init__.py:124` | `MODULE_BY_ATTRIBUTE` derives names from the dialect registry and explicit aliases. The package prefix adds tracked dialect modules as candidates. The scanner does not prove the registry or import hooks are immutable. |
| `sqlglot/dialects/dialect.py:245,288` | Both import under the same dialect package using a runtime key. Static imports still supply inheritance-related edges where bases are directly imported. |
| `sqlglot/optimizer/__init__.py:57,61` | `_LAZY_ATTRS` includes explicit optimizer and scope targets. The same-package fallback adds both and the other tracked optimizer submodules. The generic scanner does not follow the table lookup at line 57. |
| `sqlglot/serde.py:116` | The deserializer splits a class name supplied by a payload. The module target remains unknown. |
| `sqlglot/executor/context.py:46` | The bare built-in `eval` executes generated code in a supplied environment. Calls to the context's `eval` method in the executor and its tests are separate method calls. |
| `tests/test_dialect_imports.py:25`, `tests/test_docs.py:19` | Dynamic targets come from test inputs or discovered modules. They remain unknown. |
| `tests/test_typing.py:64,65` | The typing package prefix adds tracked candidates. The parent name at the second call remains unknown. |
| `sqlglot/__init__.py:66` | The optional generated `sqlglot._version` module is absent from the checkout and remains explicitly unresolved. |

A literal is resolved only for recognized direct import forms, including
straightforward imported aliases and relative literals with an explicit
package. A package prefix contributes candidate edges and retains uncertainty.
General aliases, runtime rebinding, import hooks, and native code can obscure
dependencies. A finding outside the known reverse closure still matters
because a missing edge can change that closure.

Build-time import tracing could confirm targets exercised by a bounded test
run. The existing partial coverage run shows why that would only add positive
evidence. It would consume build budget and could not rule out unobserved
deserialization, registry entries, or import paths, so this branch omits it.
No test exclusion is supported by the new evidence alone. The integrated
selector and its validated commit stay untouched.
