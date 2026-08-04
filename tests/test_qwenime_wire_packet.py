from __future__ import annotations

import struct

import pytest

from neural_weasel.qwenime_compat.wire import (
    HEADER_MAGIC,
    HEADER_SIZE,
    HEADER_VERSION,
    RESPONSE_BIT,
    TRAILER_MAGIC,
    QwenImeWireError,
    decode_wire_packet,
    encode_wire_packet,
    sanitize_windows_username,
    windows_control_pipe_name,
    windows_ipc_pipe_name,
    windows_utility_pipe_name,
)


def test_pipe_names_use_sanitized_windows_username() -> None:
    assert sanitize_windows_username("A B-中") == "A_B__"
    assert sanitize_windows_username("") == "default"
    assert windows_ipc_pipe_name("Alice") == r"\\.\pipe\QianwenIME_Alice_Pipe"
    assert windows_control_pipe_name("Alice") == r"\\.\pipe\QianwenIME_Alice_ControlPipe"
    assert windows_utility_pipe_name("Alice") == r"\\.\pipe\QianwenIME_Alice_UtilityPipe"


def test_encode_request_packet_matches_recovered_layout() -> None:
    payload = b'{"function":"start_session"}'

    packet = encode_wire_packet(
        wire_command=0x32,
        command_id=0x1E,
        payload=payload,
        is_request=True,
    )

    expected = b"".join(
        (
            struct.pack(
                "<IHHII",
                HEADER_MAGIC,
                HEADER_VERSION,
                HEADER_SIZE,
                0x32,
                len(payload) + 8,
            ),
            struct.pack("<II", 0x1E, len(payload)),
            payload,
            struct.pack("<I", TRAILER_MAGIC),
        )
    )
    assert packet == expected


def test_response_packet_sets_direction_bit_and_round_trips() -> None:
    payload = "量子".encode()
    encoded = encode_wire_packet(
        wire_command=0x34,
        command_id=0x20,
        payload=payload,
        is_request=False,
    )

    header_wire_command = struct.unpack_from("<I", encoded, 8)[0]
    decoded = decode_wire_packet(encoded)

    assert header_wire_command == 0x34 | RESPONSE_BIT
    assert decoded.wire_command == 0x34
    assert decoded.command_id == 0x20
    assert decoded.payload == payload
    assert decoded.is_request is False


def test_decoder_accepts_forward_compatible_extended_header() -> None:
    payload = b"{}"
    extension = b"ABCD"
    header_size = HEADER_SIZE + len(extension)
    packet = b"".join(
        (
            struct.pack(
                "<IHHII",
                HEADER_MAGIC,
                HEADER_VERSION,
                header_size,
                1,
                len(payload) + 8,
            ),
            extension,
            struct.pack("<II", 1, len(payload)),
            payload,
            struct.pack("<I", TRAILER_MAGIC),
        )
    )

    decoded = decode_wire_packet(packet)

    assert decoded.header_extension == extension
    assert decoded.payload == payload


@pytest.mark.parametrize(
    ("mutator", "message"),
    [
        (lambda value: b"BAD!" + value[4:], "header magic"),
        (lambda value: value[:-4] + b"BAD!", "trailer magic"),
        (lambda value: value[:-1], "packet size"),
    ],
)
def test_decoder_rejects_corrupt_packets(mutator, message: str) -> None:
    valid = encode_wire_packet(wire_command=1, command_id=1, payload=b"{}")

    with pytest.raises(QwenImeWireError, match=message):
        decode_wire_packet(mutator(valid))
