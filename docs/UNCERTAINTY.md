# Dependency uncertainty audit

Run `python -m tools.audit_dependencies SQLGLOT_ROOT [changed/path.py ...]` from
the repository root with a clean checkout of the pinned SQLGlot commit.
The audit reports external imports separately from unresolved internal
references and unsupported or dynamic Python. A finding's
`in_known_impact` field indicates whether its source appears in the known
reverse import closure for the query.

On the pinned source, the audit reports 66 external import names, one
unresolved internal dependency (`sqlglot._version` in `sqlglot/__init__.py`),
and dynamic loading or execution in ten source and test modules. The version module is
optional at checkout time. External imports do not set the graph's global
uncertainty flag. The dynamic sites include dialect and optimizer loaders
and the deserializer, which passes runtime data to `__import__`.
Some calls named `eval` may be ordinary method calls. Those conservative
detections do not account for all dynamic import risks.

A finding outside the known impact set can introduce an edge omitted by
static traversal. Its location therefore cannot clear the global fallback.
The selection policy can identify a narrow candidate for an isolated
collected test-file edit when upstream analysis has no uncertainty. The
existing mappings alone cannot establish that a changed test module has no
implicit consumers, or that an unobserved test is unaffected by a source
edit. File-level coverage records observed hits and cannot prove exclusions.

The deployed policy, map generators and artifact formats are unchanged.
Both safe and balanced continue receiving the existing dependency and
coverage uncertainty signals. The validated integration commit
`b3252d3fa681b987cc4b00b6cd5ff319789b9725` remains in branch history.
