"""Assign selected tests to workers.

Pure function: no I/O, no randomness, and no read of sample/answers.json.
Durations use the same millisecond conversion as score.py.
"""

from __future__ import annotations

import math
from collections.abc import Iterable

WORKER_COST_MILLISECONDS = 2_000
MAXIMUM_WORKERS = 8
_CACHE_LIMIT = 64
_LOCAL_STEPS = 32
_EXACT_TEST_LIMIT = 20
_EXACT_NODE_LIMIT = 4096
_FNV_OFFSET = 1469598103934665603
_FNV_PRIME = 1099511628211

# (catalog fingerprint, max_workers, chosen numbers in longest-first order) -> packing
_SCHEDULE_CACHE: dict[tuple[object, ...], list[list[int]]] = {}


def duration_milliseconds(seconds: float) -> int:
    """Convert a test duration the way score.py does.

    ``floor(seconds * 1000 + 0.5)`` rounds halves up, matching JavaScript
    ``Math.round``. Python's built-in ``round`` does not: ``round(1.0005 * 1000)``
    is 1000, while this function returns 1001.
    """
    return math.floor(seconds * 1000 + 0.5)


def clear_schedule_cache() -> None:
    """Drop cached packings. Benchmarks call this to time a cold schedule."""
    _SCHEDULE_CACHE.clear()


def schedule_tests_lpt(
    selected_numbers: set[int],
    tests: list[dict],
    max_workers: int = 8,
) -> list[list[int]]:
    """Previous longest-processing-time search, kept for comparison.

    Worker counts ``k`` from 1 through ``min(max_workers, 8, number of
    selected tests)`` are packed longest-first. The chosen packing minimizes
    ``max(worker_load_ms) + 2000 * nonempty workers``. A tie takes the smaller
    ``k``. The result is canonical: numbers inside a worker are ascending, and
    workers are ordered by their minimum test number.
    """
    if not selected_numbers or isinstance(max_workers, bool) or not isinstance(max_workers, int):
        return []
    if max_workers < 1:
        return []

    durations = _durations(tests)
    chosen = _chosen_numbers(selected_numbers, durations)
    if not chosen:
        return []

    limit = min(max_workers, MAXIMUM_WORKERS, len(chosen))
    best: tuple[int, int, list[list[int]]] | None = None
    for worker_count in range(1, limit + 1):
        assigned = _assign_longest_first(chosen, durations, worker_count)
        canonical = _canonicalize(assigned)
        makespan = max(_load(worker, durations) for worker in canonical)
        objective = makespan + WORKER_COST_MILLISECONDS * len(canonical)
        candidate = (objective, worker_count, canonical)
        if best is None or candidate < best:
            best = candidate
    if best is None:
        return []
    return best[2]


def schedule_tests(
    selected_numbers: set[int],
    tests: list[dict],
    max_workers: int = 8,
) -> list[list[int]]:
    """Pack ``selected_numbers`` onto workers and return one list per worker.

    Each inner list is that worker's test numbers. The result is canonical:
    numbers inside a worker are ascending, and workers are ordered by their
    minimum test number. Empty worker lists are never returned. ``[]`` means
    there is nothing to run.

    The objective is ``max(worker_load_ms) + 2000 * nonempty workers``. Ties
    keep the smaller worker count. Eight workers is a cap, not a target.

    Each worker count is packed longest-first. A count whose lower bound
    cannot beat the best objective already found is skipped. When the
    longest-first makespan is above the bound (the longer of the longest test
    and the total duration divided across that many workers), a first-fit
    makespan search and then local moves and swaps try to lower the objective.
    A repaired packing replaces longest-first only when its objective is
    strictly smaller. Small selections also try a node-budgeted capacity search.
    Negative durations skip the repair and use
    ``schedule_tests_lpt``.

    Identical catalog durations, selections, and worker caps are cached.
    """
    if not selected_numbers or isinstance(max_workers, bool) or not isinstance(max_workers, int):
        return []
    if max_workers < 1:
        return []

    durations = _durations(tests)
    chosen = _chosen_numbers(selected_numbers, durations)
    if not chosen:
        return []

    key = (_fingerprint(durations), max_workers, tuple(chosen))
    cached = _SCHEDULE_CACHE.get(key)
    if cached is not None:
        return [worker[:] for worker in cached]

    if any(durations[number] < 0 for number in chosen):
        canonical = schedule_tests_lpt(selected_numbers, tests, max_workers)
    else:
        canonical = _canonicalize(_schedule_improved(chosen, durations, max_workers))
    _remember(key, canonical)
    return [worker[:] for worker in canonical]


def _remember(key: tuple[object, ...], workers: list[list[int]]) -> None:
    if key in _SCHEDULE_CACHE:
        return
    if len(_SCHEDULE_CACHE) >= _CACHE_LIMIT:
        _SCHEDULE_CACHE.pop(next(iter(_SCHEDULE_CACHE)))
    _SCHEDULE_CACHE[key] = [worker[:] for worker in workers]


def _fingerprint(durations: dict[int, int]) -> int:
    fingerprint = _FNV_OFFSET
    for number, duration in sorted(durations.items()):
        fingerprint ^= number
        fingerprint = (fingerprint * _FNV_PRIME) & 0xFFFFFFFFFFFFFFFF
        fingerprint ^= duration
        fingerprint = (fingerprint * _FNV_PRIME) & 0xFFFFFFFFFFFFFFFF
    return fingerprint ^ len(durations)


def _schedule_improved(
    chosen: list[int],
    durations: dict[int, int],
    max_workers: int,
) -> list[list[int]]:
    count = len(chosen)
    limit = min(max_workers, MAXIMUM_WORKERS, count)
    total = 0
    longest = 0
    for number in chosen:
        duration = durations[number]
        total += duration
        if duration > longest:
            longest = duration

    ranked: list[tuple[int, int, int]] = []
    for worker_count in range(1, limit + 1):
        bound = _makespan_bound(total, longest, worker_count)
        ranked.append((bound + WORKER_COST_MILLISECONDS * worker_count, worker_count, bound))
    ranked.sort()

    best_objective: int | None = None
    best_workers: list[list[int]] = []
    for lower_objective, worker_count, bound in ranked:
        if best_objective is not None and lower_objective > best_objective:
            break
        workers, _span, objective = _pack_count(chosen, durations, worker_count, bound)
        if best_objective is None or objective < best_objective:
            best_objective = objective
            best_workers = workers
    return best_workers


def _makespan_bound(total: int, longest: int, worker_count: int) -> int:
    shared = (total + worker_count - 1) // worker_count
    return longest if longest > shared else shared


def _pack_count(
    chosen: list[int],
    durations: dict[int, int],
    worker_count: int,
    bound: int,
) -> tuple[list[list[int]], int, int]:
    workers, loads = _assign_with_loads(chosen, durations, worker_count)
    span = max(loads)
    best_workers = workers
    best_span = span
    best_objective = _objective(span, len(workers))
    if span <= bound:
        return best_workers, best_span, best_objective

    fitted = _multifit(chosen, durations, worker_count, bound, span)
    if fitted is not None:
        fitted_workers, fitted_span = fitted
        fitted_objective = _objective(fitted_span, len(fitted_workers))
        if fitted_objective < best_objective:
            best_workers, best_span, best_objective = fitted_workers, fitted_span, fitted_objective

    if span > bound:
        repaired, repaired_span = _local_search(workers, loads, durations, bound)
        repaired_objective = _objective(repaired_span, len(repaired))
        if repaired_objective < best_objective:
            best_workers, best_span, best_objective = repaired, repaired_span, repaired_objective

    if best_span > bound and best_workers is not workers:
        repaired, repaired_span = _local_search(best_workers, _loads(best_workers, durations), durations, bound)
        repaired_objective = _objective(repaired_span, len(repaired))
        if repaired_objective < best_objective:
            best_workers, best_span, best_objective = repaired, repaired_span, repaired_objective
    if best_span > bound and len(chosen) <= _EXACT_TEST_LIMIT:
        exact = _bounded_pack(chosen, durations, worker_count, bound, best_span)
        if exact is not None:
            exact_span = max(_loads(exact, durations))
            exact_objective = _objective(exact_span, len(exact))
            if exact_objective < best_objective:
                return exact, exact_span, exact_objective
    return best_workers, best_span, best_objective


def _bounded_pack(
    chosen: list[int],
    durations: dict[int, int],
    width: int,
    bound: int,
    current_span: int,
) -> list[list[int]] | None:
    """Search only for better packings, with a fixed node budget per width."""
    remaining = _EXACT_NODE_LIMIT
    improving = None

    def fit(capacity: int) -> list[list[int]] | None:
        loads = [0] * width
        workers: list[list[int]] = [[] for _ in loads]
        visited: set[tuple[int, tuple[int, ...]]] = set()

        def assign(index: int) -> bool:
            nonlocal remaining
            if index == len(chosen):
                return True
            if remaining == 0:
                return False
            remaining -= 1
            state = index, tuple(sorted(loads))
            if state in visited:
                return False
            visited.add(state)
            number = chosen[index]
            duration = durations[number]
            equivalent_loads: set[int] = set()
            for worker in sorted(range(width), key=loads.__getitem__, reverse=True):
                load = loads[worker]
                if load in equivalent_loads or load + duration > capacity:
                    continue
                equivalent_loads.add(load)
                loads[worker] += duration
                workers[worker].append(number)
                if assign(index + 1):
                    return True
                workers[worker].pop()
                loads[worker] -= duration
                if remaining == 0:
                    break
            return False

        if assign(0):
            return [worker for worker in workers if worker]
        return None

    high = current_span - 1
    while bound <= high and remaining:
        capacity = (bound + high) // 2
        packing = fit(capacity)
        if packing is None:
            bound = capacity + 1
        else:
            improving = packing
            high = max(_loads(packing, durations)) - 1
    return improving


def _objective(span: int, worker_count: int) -> int:
    return span + WORKER_COST_MILLISECONDS * worker_count


def _multifit(
    chosen: list[int],
    durations: dict[int, int],
    worker_count: int,
    bound: int,
    lpt_span: int,
) -> tuple[list[list[int]], int] | None:
    """Binary-search the makespan and pack first-fit in longest-first order."""
    best: tuple[list[list[int]], int] | None = None
    best_span = lpt_span
    low = bound
    high = lpt_span - 1
    while low <= high:
        mid = (low + high) // 2
        packed = _first_fit(chosen, durations, worker_count, mid)
        if packed is None:
            low = mid + 1
            continue
        bins, loads = _trim(packed[0], packed[1])
        if not bins:
            low = mid + 1
            continue
        span = max(loads)
        if span < best_span:
            best = (bins, span)
            best_span = span
        high = mid - 1
    return best


def _first_fit(
    chosen: list[int],
    durations: dict[int, int],
    worker_count: int,
    capacity: int,
) -> tuple[list[list[int]], list[int]] | None:
    loads = [0] * worker_count
    bins: list[list[int]] = [[] for _ in range(worker_count)]
    for number in chosen:
        duration = durations[number]
        placed = False
        for index in range(worker_count):
            if loads[index] + duration <= capacity:
                loads[index] += duration
                bins[index].append(number)
                placed = True
                break
        if not placed:
            return None
    return bins, loads


def _local_search(
    workers: list[list[int]],
    loads: list[int],
    durations: dict[int, int],
    bound: int,
) -> tuple[list[list[int]], int]:
    """Move or swap until the objective stops falling.

    A move takes one test off a heaviest worker. A swap exchanges a longer
    test on a heaviest worker with a shorter test on a lighter worker. The
    accepted step is the deterministic one with the smallest new objective.
    """
    bins = [list(worker) for worker in workers]
    current = list(loads)
    span = max(current) if current else 0
    for _step in range(_LOCAL_STEPS):
        # This width's makespan is already optimal. A smaller width is tried on its own.
        if not current or span <= bound:
            break
        choice = _best_reduction(bins, current, durations, span)
        if choice is None:
            break
        _apply_reduction(bins, current, durations, choice)
        span = max(current) if current else 0
    return bins, span


def _best_reduction(
    bins: list[list[int]],
    loads: list[int],
    durations: dict[int, int],
    span: int,
) -> tuple[int, int, int, int, int, int, int] | None:
    width = len(loads)
    old_objective = _objective(span, width)
    best: tuple[int, int, int, int, int, int, int] | None = None
    heavies = [index for index, load in enumerate(loads) if load == span]
    for left in heavies:
        for number in bins[left]:
            duration = durations[number]
            for right in range(width):
                if right == left:
                    continue
                new_left = loads[left] - duration
                new_right = loads[right] + duration
                new_span = _replaced_span(loads, left, right, new_left, new_right)
                new_width = width - 1 if len(bins[left]) == 1 else width
                new_objective = _objective(new_span, new_width)
                if new_objective >= old_objective:
                    continue
                candidate = (new_objective, new_span, 0, left, right, number, -1)
                if best is None or candidate < best:
                    best = candidate
        for source in bins[left]:
            source_duration = durations[source]
            for right in range(width):
                if right == left or loads[right] >= span:
                    continue
                for target in bins[right]:
                    target_duration = durations[target]
                    if target_duration >= source_duration:
                        continue
                    new_left = loads[left] - source_duration + target_duration
                    new_right = loads[right] - target_duration + source_duration
                    new_span = _replaced_span(loads, left, right, new_left, new_right)
                    new_objective = _objective(new_span, width)
                    if new_objective >= old_objective:
                        continue
                    candidate = (new_objective, new_span, 1, left, right, source, target)
                    if best is None or candidate < best:
                        best = candidate
    return best


def _apply_reduction(
    bins: list[list[int]],
    loads: list[int],
    durations: dict[int, int],
    choice: tuple[int, int, int, int, int, int, int],
) -> None:
    _objective_value, _span, kind, left, right, source, target = choice
    if kind == 0:
        bins[left].remove(source)
        bins[right].append(source)
        loads[left] -= durations[source]
        loads[right] += durations[source]
    else:
        bins[left].remove(source)
        bins[right].remove(target)
        bins[left].append(target)
        bins[right].append(source)
        loads[left] = loads[left] - durations[source] + durations[target]
        loads[right] = loads[right] - durations[target] + durations[source]
    kept = [(worker, load) for worker, load in zip(bins, loads) if worker]
    bins[:] = [worker for worker, _load in kept]
    loads[:] = [load for _worker, load in kept]


def _replaced_span(loads: list[int], left: int, right: int, new_left: int, new_right: int) -> int:
    span = new_left if new_left > new_right else new_right
    for index, load in enumerate(loads):
        if index == left or index == right:
            continue
        if load > span:
            span = load
    return span


def _assign_with_loads(
    numbers: list[int],
    durations: dict[int, int],
    worker_count: int,
) -> tuple[list[list[int]], list[int]]:
    workers: list[list[int]] = [[] for _ in range(worker_count)]
    loads = [0] * worker_count
    for number in numbers:
        worker_index = 0
        lightest = loads[0]
        for index in range(1, worker_count):
            load = loads[index]
            if load < lightest:
                worker_index = index
                lightest = load
        workers[worker_index].append(number)
        loads[worker_index] += durations[number]
    return _trim(workers, loads)


def _loads(workers: list[list[int]], durations: dict[int, int]) -> list[int]:
    return [_load(worker, durations) for worker in workers]


def _trim(
    workers: list[list[int]],
    loads: list[int],
) -> tuple[list[list[int]], list[int]]:
    kept_workers: list[list[int]] = []
    kept_loads: list[int] = []
    for worker, load in zip(workers, loads):
        if worker:
            kept_workers.append(worker)
            kept_loads.append(load)
    return kept_workers, kept_loads


def _durations(tests: list[dict] | None) -> dict[int, int]:
    """Map test number to milliseconds. The last valid entry for a number wins."""
    durations: dict[int, int] = {}
    for test in tests or []:
        if not isinstance(test, dict):
            continue
        number = test.get("number")
        seconds = test.get("seconds")
        if isinstance(number, bool) or not isinstance(number, int):
            continue
        if isinstance(seconds, bool) or not isinstance(seconds, (int, float)):
            continue
        if not math.isfinite(seconds):
            continue
        durations[number] = duration_milliseconds(seconds)
    return durations


def _coerce_number(number: object) -> int | None:
    if isinstance(number, bool):
        return int(number)
    if isinstance(number, int):
        return number
    if isinstance(number, float) and math.isfinite(number) and number.is_integer():
        return int(number)
    return None


def _chosen_numbers(selected_numbers: Iterable[object], durations: dict[int, int]) -> list[int]:
    chosen: list[int] = []
    seen: set[int] = set()
    for number in selected_numbers:
        coerced = _coerce_number(number)
        if coerced is None or coerced not in durations or coerced in seen:
            continue
        seen.add(coerced)
        chosen.append(coerced)
    chosen.sort(key=lambda number: (-durations[number], number))
    return chosen


def _assign_longest_first(
    numbers: list[int],
    durations: dict[int, int],
    worker_count: int,
) -> list[list[int]]:
    workers, _loads_for_workers = _assign_with_loads(numbers, durations, worker_count)
    return workers


def _load(worker: list[int], durations: dict[int, int]) -> int:
    return sum(durations[number] for number in worker)


def _canonicalize(workers: list[list[int]]) -> list[list[int]]:
    ordered = [sorted(worker) for worker in workers if worker]
    ordered.sort(key=lambda worker: (worker[0], worker))
    return ordered
