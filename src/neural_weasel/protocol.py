from __future__ import annotations

import json
import struct
from dataclasses import dataclass
from typing import Any

MAX_MESSAGE_BYTES = 1_048_576


class ProtocolError(ValueError):
    pass


def encode_message(message: dict[str, Any]) -> bytes:
    payload = json.dumps(
        message,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    if len(payload) > MAX_MESSAGE_BYTES:
        raise ProtocolError(f"message is too large: {len(payload)} bytes")
    return struct.pack("<I", len(payload)) + payload


def decode_message(frame: bytes) -> dict[str, Any]:
    if len(frame) < 4:
        raise ProtocolError("frame is shorter than the length prefix")
    size = struct.unpack("<I", frame[:4])[0]
    if size > MAX_MESSAGE_BYTES:
        raise ProtocolError(f"declared message is too large: {size} bytes")
    payload = frame[4:]
    if len(payload) != size:
        raise ProtocolError(f"declared {size} bytes but received {len(payload)}")
    value = json.loads(payload.decode("utf-8"))
    if not isinstance(value, dict):
        raise ProtocolError("top-level JSON value must be an object")
    return value


@dataclass(frozen=True, slots=True)
class Revision:
    session_id: str
    revision: int
    context_epoch: int

    @classmethod
    def from_message(cls, message: dict[str, Any]) -> Revision:
        try:
            return cls(
                session_id=str(message["session_id"]),
                revision=int(message["revision"]),
                context_epoch=int(message["context_epoch"]),
            )
        except (KeyError, TypeError, ValueError) as error:
            raise ProtocolError("invalid session/revision/context_epoch") from error

