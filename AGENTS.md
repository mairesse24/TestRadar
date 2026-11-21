# TestRadar contributor guidelines

Read `README.md`, `TASKS.md`, `SCORING.md`, and the modules for your assigned workstream. Use the branch and file ownership listed in `TASKS.md`.

1. **Preserve the baseline.** Work on your designated feature branch in a separate clone or worktree. Do not push directly to `main`, force-push, or merge another branch without approval.
2. **File ownership matters.** Edit only files assigned to your workstream plus your own tests. Propose cross-file changes in your final handoff; do not make them without approval.
3. **Contracts first.** Match the shared interfaces in `TASKS.md`. Coordinate any contract change through the integration and validation workstream before implementing it.
4. **Keep expected failures outside the selector.** `sample/answers.json` may be used only by offline tests/scoring. Never load it at runtime or encode known answers, commit IDs, patch fingerprints, or edit-type-to-failure lookups. Coverage and dependency maps must be derived from the pinned original SQLGlot commit.
5. **Correctness before optimization.** Unknown parsing, dynamic behavior, broad changes, fixtures, or uncertain mappings should produce an explicit uncertainty signal and trigger conservative selection. Never silently return no tests for unrecognized code.
6. **Safe output.** Only JSON answer lines to stdout from the final selector. Diagnostics to stderr. All input files are untrusted; avoid executing patch text or allowing path traversal.
7. **Respect constraints.** Offline runtime, 2 CPU, 4 GB, 3 min practice, read-only filesystem except `/tmp` 64 MB, unprivileged user, 2 MB stdout, max 8 nonempty workers.
8. **Run tests and report evidence.** Include changed files, design decisions, tests executed, their output, known limitations, and commit hash/branch in handoff. Never invent results.
9. **Do not install or change dependencies without approval.** Prefer Python standard library; if extra packages are essential, explain why and propose the change.
10. **Stop at handoff.** Do not independently submit to the buildathon, merge into main, or change official GitHub submission settings.

## Workstream handoff
- Workstream / branch / commit:
- Files changed:
- API/interface implemented:
- Tests run and exact result:
- Performance evidence (if measured):
- Known gaps / uncertain cases:
- Integration instructions:
