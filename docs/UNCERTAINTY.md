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

The fallback policy keeps the validated behaviour: every signal above is
final. `docs/SELECTION_POLICY.md` describes the optional `safe` and
`balanced` policies. They treat missing or corrupt artifacts and malformed
patches as hard fallbacks and the graph's global dynamic-loading flag as a
known limitation handled by the selector's pinned maps. The map generators
and artifact formats are unchanged. The validated integration commit
`8244dff92038bdee3ccc6052b042fc77af2829f7` remains in branch history.
