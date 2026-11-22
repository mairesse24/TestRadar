# Equivalence-aware seed filtering (experiment)

Branch `experiment/equivalence-seed-filtering`, from the official release eacab51.
Not merged, not submitted. No probe exclusions, and nothing from
`experiment/fault-detection` is included.

## Origin
No implementation or record of a 376.293 s result was found on any remote branch,
in commit messages, in the docs, or in local result files. `experiment/fault-detection`
(6222922) contains only an opt-in probe-test filter (`tools/fault_detection/probes.py`),
and that is the unsafe probe exclusion. The idea was therefore implemented here from
its description.

## Rule
Experiment `seeds` (image default `TESTRADAR_EXPERIMENTS=equivalent,seeds`). For a
modified `sqlglot/*.py` file listed once in the patch, whose syntax tree changed:

1. Rebuild the new file from the pinned file and split the edit into contiguous
   regions.
2. Mark a region as formatting-only when reverting it alone leaves the syntax tree
   equal to the real new file's.
3. Rebuild the file from the remaining regions only. Use it only if its tree equals the
   real new file's and `reconstruct` reproduces it exactly.
4. Only those remaining regions feed the text-level rules: the import and dynamic-loading
   hard signals, the structural rule, and the broad-change line count.

Module-level selection (graph, coverage and dialect rules) is unchanged.

The rule returns the original change, and so keeps eacab51's behaviour, in any of these
cases:
- a non-UTF-8 coding cookie, or a BOM, in either version;
- a single region, or more than 40 regions;
- a whole-file equivalent edit (left to the existing rule);
- the path appears twice in the patch;
- any parse, rebuild or read error.

Every fallback, both adversarial fixes, the structural rule, and the coding-cookie and
BOM hardening are unchanged.

## Results
Docker results (exact Makefile flags, 3 runs each) are in
`tools/selection_eval/seed_scenarios/docker_table.md`. On every existing labelled set
(30 samples, 63 mutants, 58 adversarial, 10 structural, 11 performance, 9 formatting
audit, 131 Phase-1 training mutants) the candidate makes identical selections to
eacab51: none of those commits has a semantically edited file with a separate
formatting-only region. On 16 new targeted scenarios (real pytest labels, clean
worktrees, bytecode off) it saves 52.0 s simulated with 0 misses.
