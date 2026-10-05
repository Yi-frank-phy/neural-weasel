"""GC policy for the dedicated, not-yet-listening model-service process."""

import gc
import time

_collection_started: dict[int, int] = {}
_collection_metrics: dict[str, int | float] = {}


def _record_collection(phase: str, info: dict[str, int]) -> None:
    """Record only clocks and counters; never inspect the collected graph."""
    generation = info.get("generation")
    if generation not in (0, 1, 2):
        return
    now = time.perf_counter_ns()
    if phase == "start":
        _collection_started[generation] = now
    elif phase == "stop":
        started = _collection_started.pop(generation, None)
        if started is None:
            return
        elapsed = max(0.0, (now - started) / 1_000_000.0)
        prefix = f"service_gc_gen{generation}"
        _collection_metrics[prefix + "_count"] = _collection_metrics.get(prefix + "_count", 0) + 1
        _collection_metrics[prefix + "_start_ns"] = started
        _collection_metrics[prefix + "_end_ns"] = now
        _collection_metrics[prefix + "_elapsed_ms"] = elapsed
        _collection_metrics[prefix + "_max_ms"] = max(
            elapsed, _collection_metrics.get(prefix + "_max_ms", 0.0)
        )


def collection_diagnostics() -> dict[str, int | float]:
    """Return a fixed-size snapshot of completed collection timings."""
    return dict(_collection_metrics)


def freeze_startup_objects() -> None:
    """Exclude the long-lived startup graph from interactive GC scans.

    Call only once, after model/index/baseline initialization and before any
    listener can accept editor context. New session and context objects remain
    in the normal collector; automatic collection is never disabled. Frozen
    startup cycles live until this dedicated service process exits.
    """
    gc.collect()
    gc.freeze()
    if _record_collection not in gc.callbacks:
        gc.callbacks.append(_record_collection)
