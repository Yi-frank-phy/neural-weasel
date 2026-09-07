"""GC policy for the dedicated, not-yet-listening model-service process."""

import gc


def freeze_startup_objects() -> None:
    """Exclude the long-lived startup graph from interactive GC scans.

    Call only once, after model/index/baseline initialization and before any
    listener can accept editor context. New session and context objects remain
    in the normal collector; automatic collection is never disabled. Frozen
    startup cycles live until this dedicated service process exits.
    """
    gc.collect()
    gc.freeze()
