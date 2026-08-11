from __future__ import annotations

import struct
from dataclasses import dataclass

HEADER_MAGIC = 0x54554951  # little-endian bytes: QIUT
TRAILER_MAGIC = 0x45554951  # little-endian bytes: QIUE
HEADER_VERSION = 1
HEADER_SIZE = 16
COMMAND_BLOCK_SIZE = 8
MAX_PAYLOAD_BYTES = 0x500000
RESPONSE_BIT = 0x80000000

_HEADER = struct.Struct("<IHHII")
_COMMAND_BLOCK = struct.Struct("<II")
_TRAILER = struct.Struct("<I")
_PIPE_PREFIX = r"\\.\pipe\QianwenIME_"


class QwenImeWireError(ValueError):
    """Raised when a QwenIME packet or pipe identity violates the recovered contract."""


@dataclass(frozen=True, slots=True)
class WirePacket:
    wire_command: int
    command_id: int
    payload: bytes
    is_request: bool
    version: int = HEADER_VERSION
    header_extension: bytes = b""


def sanitize_windows_username(username: str) -> str:
    """Match QwenIME's GetUserNameW suffix sanitization.

    The binary walks UTF-16 code units and preserves only ASCII letters and digits.
    Every other code unit becomes an underscore; an empty result falls back to
    ``default``.
    """

    if not isinstance(username, str):
        raise TypeError("username must be a string")
    encoded = username.encode("utf-16le", errors="surrogatepass")
    units = struct.unpack(f"<{len(encoded) // 2}H", encoded) if encoded else ()
    sanitized = "".join(
        chr(unit) if 0x30 <= unit <= 0x39 or 0x41 <= unit <= 0x5A or 0x61 <= unit <= 0x7A else "_"
        for unit in units
    )
    return sanitized or "default"


def _pipe_name(username: str, suffix: str) -> str:
    return f"{_PIPE_PREFIX}{sanitize_windows_username(username)}{suffix}"


def windows_ipc_pipe_name(username: str) -> str:
    return _pipe_name(username, "_Pipe")


def windows_control_pipe_name(username: str) -> str:
    return _pipe_name(username, "_ControlPipe")


def windows_utility_pipe_name(username: str) -> str:
    return _pipe_name(username, "_UtilityPipe")


def _require_uint32(value: int, name: str, *, allow_response_bit: bool = True) -> int:
    maximum = 0xFFFFFFFF if allow_response_bit else RESPONSE_BIT - 1
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= maximum:
        raise QwenImeWireError(f"{name} must be an integer from 0 to {maximum}")
    return value


def encode_wire_packet(
    *,
    wire_command: int,
    command_id: int,
    payload: bytes,
    is_request: bool,
) -> bytes:
    """Encode the statically recovered QwenIME main-pipe packet envelope."""

    normalized_wire_command = _require_uint32(
        wire_command,
        "wire_command",
        allow_response_bit=False,
    )
    normalized_command_id = _require_uint32(command_id, "command_id")
    if not isinstance(payload, bytes):
        raise QwenImeWireError("payload must be bytes")
    if len(payload) > MAX_PAYLOAD_BYTES:
        raise QwenImeWireError("payload is too large")
    if not isinstance(is_request, bool):
        raise QwenImeWireError("is_request must be a boolean")

    encoded_wire_command = normalized_wire_command
    if not is_request:
        encoded_wire_command |= RESPONSE_BIT
    payload_bytes = len(payload) + COMMAND_BLOCK_SIZE
    return b"".join(
        (
            _HEADER.pack(
                HEADER_MAGIC,
                HEADER_VERSION,
                HEADER_SIZE,
                encoded_wire_command,
                payload_bytes,
            ),
            _COMMAND_BLOCK.pack(normalized_command_id, len(payload)),
            payload,
            _TRAILER.pack(TRAILER_MAGIC),
        )
    )


def decode_wire_packet(packet: bytes) -> WirePacket:
    """Decode and validate the recovered QwenIME main-pipe packet envelope."""

    if not isinstance(packet, bytes):
        raise QwenImeWireError("packet must be bytes")
    minimum_size = HEADER_SIZE + COMMAND_BLOCK_SIZE + _TRAILER.size
    if len(packet) < minimum_size:
        raise QwenImeWireError("packet size is too small")

    magic, version, header_size, encoded_command, payload_bytes = _HEADER.unpack_from(packet)
    if magic != HEADER_MAGIC:
        raise QwenImeWireError("header magic is invalid")
    if version != HEADER_VERSION:
        raise QwenImeWireError("header version is unsupported")
    if header_size < HEADER_SIZE or header_size > len(packet):
        raise QwenImeWireError("header size is invalid")
    if not COMMAND_BLOCK_SIZE <= payload_bytes <= MAX_PAYLOAD_BYTES + COMMAND_BLOCK_SIZE:
        raise QwenImeWireError("payload byte count is invalid")

    expected_size = header_size + payload_bytes + _TRAILER.size
    if len(packet) != expected_size:
        raise QwenImeWireError("packet size does not match its header")
    trailer = _TRAILER.unpack_from(packet, expected_size - _TRAILER.size)[0]
    if trailer != TRAILER_MAGIC:
        raise QwenImeWireError("trailer magic is invalid")

    command_id, declared_payload_size = _COMMAND_BLOCK.unpack_from(packet, header_size)
    if declared_payload_size != payload_bytes - COMMAND_BLOCK_SIZE:
        raise QwenImeWireError("payload size does not match its command block")
    if declared_payload_size > MAX_PAYLOAD_BYTES:
        raise QwenImeWireError("payload is too large")

    payload_start = header_size + COMMAND_BLOCK_SIZE
    payload_end = payload_start + declared_payload_size
    is_request = encoded_command & RESPONSE_BIT == 0
    wire_command = encoded_command & ~RESPONSE_BIT
    return WirePacket(
        wire_command=wire_command,
        command_id=command_id,
        payload=packet[payload_start:payload_end],
        is_request=is_request,
        version=version,
        header_extension=packet[HEADER_SIZE:header_size],
    )
