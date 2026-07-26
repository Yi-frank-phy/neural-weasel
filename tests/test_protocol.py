from __future__ import annotations

import json
import struct

import pytest

from neural_weasel.protocol import (
    MAX_MESSAGE_BYTES,
    ProtocolError,
    Revision,
    decode_message,
    encode_message,
)


def test_protocol_round_trip_preserves_unicode_and_nested_values() -> None:
    message = {
        "type": "query_pinyin",
        "before": "该协议所消耗的",
        "pinyin": "jiuchan",
        "candidates": 5,
        "flags": {"partial": True},
    }
    frame = encode_message(message)

    assert struct.unpack("<I", frame[:4])[0] == len(frame) - 4
    assert decode_message(frame) == message


def test_protocol_json_is_compact_and_rejects_nan() -> None:
    frame = encode_message({"a": 1, "b": "你"})
    assert b" " not in frame[4:]
    with pytest.raises(ValueError):
        encode_message({"score": float("nan")})


@pytest.mark.parametrize(
    ("frame", "message"),
    [
        (b"", "shorter"),
        (struct.pack("<I", 10) + b"{}", "declared 10"),
        (struct.pack("<I", MAX_MESSAGE_BYTES + 1), "too large"),
    ],
)
def test_decode_rejects_invalid_frame_lengths(frame: bytes, message: str) -> None:
    with pytest.raises(ProtocolError, match=message):
        decode_message(frame)


def test_decode_rejects_non_object_json() -> None:
    payload = json.dumps(["not", "an", "object"]).encode()
    with pytest.raises(ProtocolError, match="top-level"):
        decode_message(struct.pack("<I", len(payload)) + payload)


def test_encode_rejects_oversized_payload() -> None:
    with pytest.raises(ProtocolError, match="too large"):
        encode_message({"payload": "a" * MAX_MESSAGE_BYTES})


def test_revision_parses_wire_values() -> None:
    revision = Revision.from_message(
        {"session_id": "session-1", "revision": "12", "context_epoch": 7}
    )
    assert revision == Revision(session_id="session-1", revision=12, context_epoch=7)


@pytest.mark.parametrize(
    "message",
    [
        {},
        {"session_id": "s", "revision": object(), "context_epoch": 1},
        {"session_id": "s", "revision": 1, "context_epoch": None},
    ],
)
def test_revision_rejects_missing_or_unparseable_values(message: dict[str, object]) -> None:
    with pytest.raises(ProtocolError, match="invalid session"):
        Revision.from_message(message)
