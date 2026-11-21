# Instructions for ALL coding agents

You are one contributor to a multi-agent RWX Blast Radius project. Start by reading `README.md`, `TASKS.md`, `SCORING.md`, and your existing assigned modules. Find your assignment by agent name in `TASKS.md`. Do not take other agents' tasks.

1. **Do not overwrite the baseline or modify `main`.** Work on your designated branch in a separate clone/worktree. Never force-push.
2. **File ownership matters.** Edit only files assigned to your role plus your own tests. Propose cross-file changes in your final handoff; do not make them without approval.
3. **Contracts first.** Match the shared interfaces in `TASKS.md`. Coordinate any contract change through the integrator before implementing it.
4. **No cheating / answer memorization.** `sample/answers.json` may be used only by offline tests/scoring. Never load it at runtime or encode known answers, commit IDs, patch fingerprints, or edit-type-to-failure lookups. Coverage and dependency maps must be derived from the pinned original SQLGlot commit.
5. **Correctness before optimization.** Unknown parsing, dynamic behavior, broad changes, fixtures, or uncertain mappings should produce an explicit uncertainty signal and trigger conservative selection. Never silently return no tests for unrecognized code.
6. **Safe output.** Only JSON answer lines to stdout from the final selector. Diagnostics to stderr. All input files are untrusted; avoid executing patch text or allowing path traversal.
7. **Respect constraints.** Offline runtime, 2 CPU, 4 GB, 3 min practice, read-only filesystem except `/tmp` 64 MB, unprivileged user, 2 MB stdout, max 8 nonempty workers.
8. **Run tests and report evidence.** Include changed files, design decisions, tests executed, their output, known limitations, and commit hash/branch in handoff. Never invent results.
9. **Do not install or change dependencies without approval.** Prefer Python standard library; if extra packages are essential, explain why and propose the change.
10. **Stop at handoff.** Do not independently submit to the buildathon, merge into main, or change official GitHub submission settings.

## Agent handoff format
- Agent / branch / commit:
- Files changed:
- API/interface implemented:
- Tests run and exact result:
- Performance evidence (if measured):
- Known gaps / uncertain cases:
- Integration instructions:
