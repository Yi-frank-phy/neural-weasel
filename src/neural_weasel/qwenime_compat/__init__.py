"""Version-pinned compatibility core for the QwenIME process boundary."""

from .bridge import QwenImeBridge
from .manifest import SUPPORTED_QWENIME_VERSION, verify_vendor_install
from .protocol import (
    CandidateView,
    CompositionView,
    NormalizedRequest,
    NormalizedResponse,
    QwenImeProtocolError,
    RequestKind,
    parse_json_payload,
    parse_normalized_request,
    serialize_json_payload,
)

__all__ = [
    "CandidateView",
    "CompositionView",
    "NormalizedRequest",
    "NormalizedResponse",
    "QwenImeBridge",
    "QwenImeProtocolError",
    "RequestKind",
    "SUPPORTED_QWENIME_VERSION",
    "parse_json_payload",
    "parse_normalized_request",
    "serialize_json_payload",
    "verify_vendor_install",
]
