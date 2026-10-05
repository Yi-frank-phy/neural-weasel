from __future__ import annotations

import heapq
from collections.abc import Callable, Generator, Iterable
from typing import Any

_BATCH_SIZE = 32


def cooperative_sorted[T](
    values: Iterable[T], *, key: Callable[[T], Any]
) -> Generator[None, None, list[T]]:
    """Keep stable global ordering while yielding between bounded work batches."""
    runs = []
    batch = []
    for position, value in enumerate(values):
        batch.append((key(value), position, value))
        if len(batch) == _BATCH_SIZE:
            runs.append(sorted(batch))
            batch = []
            yield None
    if batch:
        runs.append(sorted(batch))
        yield None
    ordered = []
    for _, _, value in heapq.merge(*runs):
        ordered.append(value)
        if len(ordered) % _BATCH_SIZE == 0:
            yield None
    return ordered
