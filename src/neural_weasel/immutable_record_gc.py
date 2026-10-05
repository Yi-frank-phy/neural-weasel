"""Avoid cyclic-GC scans of validated, immutable, acyclic search records.

This leaves automatic GC enabled. Callers must be exact frozen record types
whose complete payload consists of the atomic values checked below. Subclasses,
mutable values and objects with arbitrary Python references stay tracked.
"""

from __future__ import annotations

import ctypes
import sys

_ATOMIC_TYPES = (str, int, float, bool, type(None))
_untrack = None
if sys.implementation.name == "cpython":
    try:
        _untrack = ctypes.pythonapi.PyObject_GC_UnTrack
        _untrack.argtypes = [ctypes.py_object]
        _untrack.restype = None
    except AttributeError:
        pass


def is_atomic_payload(payload: tuple[object, ...]) -> bool:
    """Check scalar/flat-tuple payloads without retaining any values."""
    for value in payload:
        if type(value) in _ATOMIC_TYPES:
            continue
        if type(value) is not tuple:
            return False
        for item in value:
            if type(item) not in _ATOMIC_TYPES:
                return False
    return True


def exclude_atomic_record(record: object, payload: tuple[object, ...]) -> None:
    """Untrack an exact immutable record after checking its complete payload.

    Flat tuples cover public pronunciation syllables and token paths. Other
    containers deliberately fall back to normal GC; no object graph is inspected.
    Untracking preserves reference counting and does not freeze or retain objects.
    """
    if _untrack is None:
        return
    if is_atomic_payload(payload):
        _untrack(record)


def exclude_prevalidated_atomic_record(record: object) -> None:
    """Reuse a complete payload proof at an exact immutable construction site.

    The cached-match and root-plan builders call this after proving every
    reference either atomic or an exact, immutable, validated index/match.
    Their remaining owned fields are checked separately at construction.
    Generic constructors and unvalidated matches keep normal GC.
    """
    if _untrack is not None:
        _untrack(record)
