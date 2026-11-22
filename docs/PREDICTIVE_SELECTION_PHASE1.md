# Predictive selection: Phase 1 (dataset and feasibility)

Branch `experiment/predictive-selection`, from release eacab51. Research only: the
selector and maps of eacab51 are unchanged and nothing here is used at runtime.

## Rules check
- README: "No answers worked out ahead of time and looked up by patch: no table from a
  patch, or from a kind of edit, to the tests it breaks. Maps built from the pinned
  commit (coverage, imports, fixtures) are allowed."
- README: "The build has the network, 4 cores, 8 GB of memory and 10 minutes. It may
  clone sqlglot at the pinned commit, install it and run its tests to make coverage or
  import maps." / "Your repository may hold 20 MB."
- AGENTS.md 4: "Never load it at runtime or encode known answers, commit IDs, patch
  fingerprints, or edit-type-to-failure lookups. Coverage and dependency maps must be
  derived from the pinned original SQLGlot commit."
- SCORING.md: "A commit is a miss when at least one of its failing tests is in none of
  its workers." (score.py: `if any(failing_test_number not in distinct_test_numbers ...)`)

Verdict: generating and labelling mutants of the pinned commit offline for research is
allowed. Any model or table keyed by edit kind -> failing tests is prohibited. A
location-keyed kill map (function/line -> tests) is a gray zone: it is derived from the
pinned commit like coverage, but it is also answers worked out ahead of time for
synthetic patches. It would need organizer clarification, and could not be produced
inside the 10-minute build (labelling costs ~30 s per mutant), so it would be committed.

## Tools
- `tools/pts/gen_mutations.py`: seeded AST single-line mutants (7 categories x 5
  location strata, per-cell sampling), with metadata (path, qualname, line, node,
  category, location). Evaluation patch signatures (sample + all earlier scenario sets)
  are excluded.
- `tools/selection_eval/run_mutants.py`: labels each mutant with the real pytest suite
  in an isolated clean worktree; now runs with `PYTHONDONTWRITEBYTECODE=1`.
- `tools/pts/analyze_matrix.py`: sparse matrix, coverage, greedy runtime-weighted
  preview, leave-one-group-out checks under the official miss rule.

## Data quality incident
The first labels for 52 mutants ran in a worktree holding stale `.pyc` files (same
size and mtime second as the restored source after earlier concurrent patching). Every
one gained the same 22 spurious failures. Those labels were discarded (kept off-repo as
a record), bytecode was purged and disabled, and 28 of the 52 were relabelled before
the time cap. The 24 not relabelled are excluded.

## Batch 1 (seed 20261121)
- Generated 155 (pool 47,211 candidates, 52 files); clean labels 131.
- Survived 18 (14%); broad/import-level 1; narrow-killed 112. No crashes or collection
  errors apart from the broad mutant.
- Flakiness: 10 mutants re-run on another clean worktree, 10/10 identical failing sets.
- Failing tests per killed mutant: min 1, median 3, max 1123.
- Tests detecting >=1 mutant: 1315/1620 (narrow mutants only: 832; 402 of them hit by
  exactly one mutant): kills are spread thin, with weak concentration.
- Label cost: ~52 s wall per mutant with `-n 3`; ~25-30 s effective with 3 parallel
  worktrees on 8 cores. Extrapolation: 1,000 mutants ~8 h, 5,000 ~40 h.

## Coverage and preview portfolio (official all-failing miss rule)
- Greedy detect-any portfolio: 84 tests, 6.3 s/commit vs full 36.8 s, but misses
  69/113 killed mutants in-sample, because a miss is ANY unselected failing test.
- Zero in-sample misses needs the union of failing tests: 1315 tests, 27.5 s (most of
  the 36.8 s, which `test_parser::test_max_nodes` alone sets at 28.8 s).
- Held out, the union still misses: leave-category-out 22%, leave-file-out 26%,
  leave-location-out 35%. The detect-any portfolio misses 87-96%.
- Oracle lower bounds over killed mutants: exact failing tests 6.9 s/commit, failing
  files 14.4 s/commit.
- eacab51 on the 131 clean mutants: 0 misses, 3833.6 s total (29.3 s/commit; 68 full
  suite). The earlier apparent 28 misses were all caused by the stale-bytecode labels.

## Feasibility
Not credible as a safe route to 100-200 s. A 200 s hidden score is about 152 s on the
sample (ratio 1.315), below the sample's failing-files floor of 256.5 s. Reaching it
needs per-commit test-level precision. Learned or mined portfolios miss 22-35% of
held-out mutants even at near-full-suite size, and each miss costs 300 s. The only signal
that would help is a per-location kill map, which is a rules gray zone and has the same
incompleteness as coverage.

## Phase 2 (only if the organizers allow a location-keyed kill map)
Label ~2,000 stratified mutants (~16 h on 3 worktrees), keep whole files and categories
held out, and measure held-out all-failing recall. Proceed only if held-out misses are
<1% at <15 s/commit. Expected outcome on present evidence: fail.
