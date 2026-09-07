import subprocess
import sys
import textwrap


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
