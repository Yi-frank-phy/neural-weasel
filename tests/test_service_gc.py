import subprocess
import sys
import textwrap

import pytest

from neural_weasel import service_gc


@pytest.mark.parametrize("generation", [0, 1, 2])
def test_collection_metrics_contain_only_bounded_clock_and_count_metadata(monkeypatch, generation):
    monkeypatch.setattr(service_gc, "_collection_metrics", {})
    monkeypatch.setattr(service_gc, "_collection_started", {})
    readings = iter(range(0, 10_000_000, 1_000))
    monkeypatch.setattr(service_gc.time, "perf_counter_ns", lambda: next(readings))
    for _ in range(100):
        service_gc._record_collection("start", {"generation": generation})
        service_gc._record_collection("stop", {"generation": generation})
    snapshot = service_gc.collection_diagnostics()
    prefix = f"service_gc_gen{generation}"
    assert snapshot == {
        prefix + "_count": 100,
        prefix + "_start_ns": 198_000,
        prefix + "_end_ns": 199_000,
        prefix + "_elapsed_ms": 0.001,
        prefix + "_max_ms": 0.001,
    }
    snapshot.clear()
    assert len(service_gc.collection_diagnostics()) == 5


def test_startup_freeze_excludes_static_graph_but_collects_new_cycles():
    # Isolate the process-wide GC policy from pytest and its plugins.
    subprocess.run(
        [
            sys.executable,
            "-c",
            textwrap.dedent("""
        import gc
        import weakref
        from neural_weasel.service_gc import freeze_startup_objects
        class Node: pass
        static = Node()
        static.cycle = static
        reference = weakref.ref(static)
        freeze_startup_objects()
        from neural_weasel.service_gc import collection_diagnostics, _record_collection
        assert gc.callbacks.count(_record_collection) == 1
        assert gc.isenabled()
        assert gc.get_freeze_count() > 0
        assert not any(item is static for item in gc.get_objects())
        dynamic = Node()
        dynamic.cycle = dynamic
        live_reference = weakref.ref(dynamic)
        # Model a mutable startup owner acquiring and releasing later state.
        static.current = dynamic
        del dynamic
        gc.collect()
        assert collection_diagnostics()["service_gc_gen2_count"] >= 1
        assert live_reference() is not None
        static.current = None
        gc.collect()
        assert live_reference() is None
        gc.unfreeze()
        del static
        gc.collect()
        assert reference() is None
    """),
        ],
        check=True,
    )
