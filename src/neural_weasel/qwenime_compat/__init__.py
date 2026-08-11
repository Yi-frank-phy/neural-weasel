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
from .swap_plan import (
    QwenImeSwapPlanError,
    ServerSwapPlan,
    build_server_swap_plan,
)
from .wire import (
    QwenImeWireError,
    WirePacket,
    decode_wire_packet,
    encode_wire_packet,
    sanitize_windows_username,
    windows_control_pipe_name,
    windows_ipc_pipe_name,
    windows_utility_pipe_name,
)

__all__ = [
    "CandidateView",
    "CompositionView",
    "NormalizedRequest",
    "NormalizedResponse",
    "QwenImeBridge",
    "QwenImeProtocolError",
    "QwenImeSwapPlanError",
    "QwenImeWireError",
    "RequestKind",
    "SUPPORTED_QWENIME_VERSION",
    "ServerSwapPlan",
    "WirePacket",
    "build_server_swap_plan",
    "decode_wire_packet",
    "encode_wire_packet",
    "parse_json_payload",
    "parse_normalized_request",
    "sanitize_windows_username",
    "serialize_json_payload",
    "verify_vendor_install",
    "windows_control_pipe_name",
    "windows_ipc_pipe_name",
    "windows_utility_pipe_name",
]
