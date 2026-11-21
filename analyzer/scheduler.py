"""Assign selected tests to workers.

Pure function: no I/O, no randomness, and no read of sample/answers.json.
Durations use the same millisecond conversion as score.py.
"""

from __future__ import annotations

import math
from collections.abc import Iterable

WORKER_COST_MILLISECONDS = 2_000
MAXIMUM_WORKERS = 8


def duration_milliseconds(seconds: float) -> int:
    """Convert a test duration the way score.py does.

    ``floor(seconds * 1000 + 0.5)`` rounds halves up, matching JavaScript
    ``Math.round``. Python's built-in ``round`` does not: ``round(1.0005 * 1000)``
    is 1000, while this function returns 1001.
    """
    return math.floor(seconds * 1000 + 0.5)


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

    Worker counts ``k`` from 1 through ``min(max_workers, 8, number of
    selected tests)`` are packed longest-processing-time-first. Tests are
    ordered by duration descending, then by test number ascending. Each test
    goes to the current lowest-load worker; equal loads go to the lowest
    worker index.

    The chosen packing minimizes ``max(worker_load_ms) + 2000 * nonempty
    workers``. A tie takes the smaller ``k``. A further tie takes the
    lexicographically smaller canonical packing.
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
    workers: list[list[int]] = [[] for _ in range(worker_count)]
    loads = [0] * worker_count
    for number in numbers:
        worker_index = 0
        lightest = loads[0]
        for index in range(1, worker_count):
            if loads[index] < lightest:
                worker_index = index
                lightest = loads[index]
        workers[worker_index].append(number)
        loads[worker_index] += durations[number]
    return [worker for worker in workers if worker]


def _load(worker: list[int], durations: dict[int, int]) -> int:
    return sum(durations[number] for number in worker)


def _canonicalize(workers: list[list[int]]) -> list[list[int]]:
    ordered = [sorted(worker) for worker in workers if worker]
    ordered.sort(key=lambda worker: (worker[0], worker))
    return ordered
