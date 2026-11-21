# TestRadar workstreams

## Shared interfaces
Use Python 3.13 standard-library-friendly code. Patch and impact analysis owns `analyzer/contracts.py` and implements typed dataclasses or TypedDicts matching the fields below. Coordinate interface changes with integration and validation before implementation.

```python
# FileChange representation (from patch analyzer)
{
    "path": "sqlglot/parser.py",      # normalized new path, or old path if deleted
    "old_path": "sqlglot/parser.py",  # may be None for added file
    "new_path": "sqlglot/parser.py",  # may be None for deleted file
    "status": "modified",            # added | deleted | modified | renamed | copied | binary | unknown
    "old_lines": [12, 13],
    "new_lines": [12, 13],
    "added_text": ["..."],
    "removed_text": ["..."],
    "uncertain": False,
}
```

**Public function signatures:**
```python
# analyzer/patch_parser.py
parse_patch(text: str) -> list[dict]  # FileChange dictionaries above

# analyzer/dependency_graph.py
class DependencyGraph:
    @classmethod
    def load(cls, path: str) -> "DependencyGraph": ...
    def impacted_files(self, changed_paths: set[str]) -> set[str]: ...
    # Includes changed paths; returns transitive import/dependency neighbors when known.

# analyzer/coverage_map.py
class CoverageMap:
    @classmethod
    def load(cls, path: str) -> "CoverageMap": ...
    def tests_for_changes(self, changes: list[dict]) -> set[str]: ...
    # Return test node IDs matching tests.json `id`, NOT test numbers.

# analyzer/test_selector.py
select_tests(
    changes: list[dict],
    tests: list[dict],
    impacted_files: set[str],
    covered_test_ids: set[str],
    *,
    analysis_uncertain: bool = False,
) -> set[int]
# Return test `number` values. Safe fallback: all test numbers.

# analyzer/scheduler.py
schedule_tests(selected_numbers: set[int], tests: list[dict], max_workers: int = 8) -> list[list[int]]
# Return valid worker lists, optimize max(load) + 2 * nonempty worker count.
```

Provide a compatibility wrapper for an earlier patch parser interface if one exists and compatibility is practical. Use local stubs or fixtures for components that are still under development.

## Patch and impact analysis
**Branch:** `feature/impact-analysis`
**Own:** `analyzer/contracts.py`, `analyzer/patch_parser.py`, `analyzer/dependency_graph.py`, `analyzer/coverage_map.py`, `tests/test_analysis.py`.

### A1. Patch parsing
- Start from the supplied Phase 2 parser if present.
- Correctly handle multiple files, multiple hunks, additions/deletions, renames, `/dev/null`, line number shifts, binary/unknown changes, malformed headers, and safe path normalization.
- Extract old/new changed line numbers and actual added/removed text.
- Return uncertainty for unsupported forms; never pretend to understand malformed input.
- Unit-test real sample patches plus synthetic edge cases.

### A2. Dependency graph
- Derive a module-level dependency graph from **pinned original** SQLGlot source. Implement a separate build-time generation function/script if needed; do not download code during runtime.
- Map changed modules to related importers transitively. Document limits of static imports, inheritance, and dynamic imports.
- Serialize a compact map to JSON; validate it can be read by unprivileged runtime.
- Unit tests with small synthetic Python package trees.

### A3. Coverage mapping (after A1/A2 work)
- Propose an offline build-time map from test node IDs to covered source files/lines at pinned SQLGlot commit; do not key anything to sample answers.
- If full coverage is too expensive, implement file-level mapping and document precision limits.
- Respect build budget; keep artifact sizes small. Prefer safe fallback to false certainty.

**Done when:** All public interfaces exist, tests pass, build-time map generation documented, and uncertainty propagates.

## Test selection policy
**Branch:** `feature/test-selection`
**Own:** `analyzer/test_selector.py`, `tests/test_test_selector.py`.

### B1. Selection algorithm
- Implement `select_tests` contract. Map test IDs/files and impacted modules to candidates; incorporate coverage matches and test-file naming conventions.
- Build conservative fallback: unknown paths, changes to shared/core parser, test fixtures, config, broad refactors, uncertain patch analysis should not result in aggressive skipping.
- Avoid naive `path.replace("sqlglot/", "tests/test_")` as sole rule. Explain false-negative risks.
- Ensure `set[int]` output contains only valid numbers from tests.json and no duplicates.
- Unit tests for small, synthetic cases: narrow change, broad core change, unknown file, missing coverage, uncertain change, no relevant tests.

### B2. Policy evaluation proposal
- Provide configurable policy modes (`safe`, `balanced`, `aggressive`) **only if** this doesn't complicate the integration contract. Default safe.
- Suggest holdout evaluation strategies; do not read sample answers in runtime or hardcode commit-specific behavior.

**Done when:** Policy unit tests pass and selection is deterministic. Report when it falls back to all tests.

## Worker scheduling and benchmarking
**Branch:** `feature/worker-scheduling`
**Own:** `analyzer/scheduler.py`, `benchmark.py`, `tests/test_scheduler.py`, `docs/DOCKER_TESTING.md`.

### C1. Scheduling
- Implement `schedule_tests` contract. Use milliseconds (`round(seconds*1000)`) to mirror scorer.
- Try 1..min(8, selected test count) workers; use longest-processing-time-first assignment and choose lowest objective `max(worker_load_ms) + 2000 * nonempty_workers`.
- Return `[]` for no tests. Ensure each test number appears exactly once. Tie-break deterministically.
- Unit-test no tests, one test, skewed durations, many tests, invalid numbers, and the 2-second worker overhead tradeoff.

### C2. Benchmark tooling
- Create a script to compare scorer output across candidate result files without importing `sample/answers.json` into runtime selector.
- Record exact score, missed commits, and validation status; report CPU time separately or explicitly mark unmeasured.
- Document Windows PowerShell UTF-8 redirection issue and a safe command to capture Docker stdout as bytes.
- Do not change root Dockerfile/selector.py; instead propose integration steps.

**Done when:** Scheduler tests pass and benchmark tooling works with starter sample outputs.

## Integration and validation
**Branch:** `feature/integration`

**Own:** `selector.py`, root `Dockerfile`, `requirements.lock`, `.gitignore`, `README.md`, `AGENTS.md`, and `TASKS.md`.

### I1. Baseline preservation
- Use the existing full-suite selector and Dockerfile as the reference baseline. Preserve their Git history and saved sample output.
- Verify `python score.py sample sample/answers.json output-docker.jsonl` gives 1210.470 and 0 misses.

### I2. Integration
- Review each completed component against the shared interfaces and its tests. Obtain approval before merging.
- Integrate reading `/data/tests.json` and `/data/commits/*.patch` with one JSON line per commit.
- Preload static maps once; fallback to full suite if maps are missing or corrupt.
- Validate output schema, no logging to stdout, and CPU/memory limits.

### I3. Validation and submission
- Run unit tests, `make check`, and `make sample`. Record simulated seconds, missed commits, runtime and its measurement method, and unresolved issues. Compare with the baseline.
- Evaluate with held-out changes where feasible; avoid sample overfitting.
- Record the best validated commit for the project owner, who handles submission.

## Coordination order
1. Verify the baseline and review `analyzer/contracts.py` against the documented interfaces.
2. Review patch parsing and dependency mapping, then coverage mapping and its fallback behavior. Verify the maps come from the pinned SQLGlot source.
3. Review test selection, including full-suite fallback for uncertain analysis, then worker scheduling and benchmark tooling.
4. After approval for each merge, integrate the completed components into `feature/integration` in that order and wire the selector and Dockerfile.
5. Run the restricted Docker validation and scorer. Compare measured results against the baseline before optimizing.
