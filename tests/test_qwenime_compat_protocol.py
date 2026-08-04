from __future__ import annotations

import json
from pathlib import Path

import pytest

from neural_weasel.qwenime_compat.manifest import ExpectedBinary, verify_vendor_install
from neural_weasel.qwenime_compat.protocol import (
    NormalizedResponse,
    QwenImeProtocolError,
    RequestKind,
    parse_json_payload,
    parse_normalized_request,
    serialize_json_payload,
)


def test_parse_known_request_and_preserve_unknown_fields() -> None:
    request = parse_normalized_request(
        {
            "function": "process_key",
            "session_id": "session-1",
            "key": "n",
            "candidate_count": 7,
            "vendor_field": {"opaque": True},
        }
    )

    assert request.kind == RequestKind.PROCESS_KEY
    assert request.session_id == "session-1"
    assert request.key == "n"
    assert request.candidate_count == 7
    assert request.extras == {"vendor_field": {"opaque": True}}


def test_parse_rejects_unknown_function_without_echoing_private_fields() -> None:
    with pytest.raises(QwenImeProtocolError, match="unsupported function"):
        parse_normalized_request(
            {
                "function": "private_text_here",
                "before": "sensitive surrounding context",
            }
        )


def test_json_round_trip_keeps_unicode_candidate_text() -> None:
    request = parse_json_payload(
        json.dumps(
            {
                "function": "start_session",
                "session_id": "session-1",
            }
        ).encode()
    )
    response = NormalizedResponse(
        function=request.kind.value,
        ok=True,
        handled=True,
        session_id=request.session_id,
        commit="量子信息",
    )

    payload = serialize_json_payload(response)

    assert json.loads(payload.decode())["commit"] == "量子信息"


def test_candidate_index_rejects_boolean() -> None:
    with pytest.raises(QwenImeProtocolError, match="candidate_index"):
        parse_normalized_request(
            {
                "function": "candidate_action",
                "session_id": "session-1",
                "candidate_index": True,
            }
        )


def test_static_manifest_verification_is_fail_closed(tmp_path: Path) -> None:
    binary = tmp_path / "QianwenIMEServer.exe"
    binary.write_bytes(b"expected")
    expected = (
        ExpectedBinary(
            relative_path=binary.name,
            size=len(b"expected"),
            sha256="cea23dd4b87e8b00d19fb9ccaaef93e97353c7353e2070f3baf05aeb3995dff4",
        ),
    )

    assert verify_vendor_install(tmp_path, expected=expected).ok

    binary.write_bytes(b"changed")
    report = verify_vendor_install(tmp_path, expected=expected)
    assert not report.ok
    assert report.mismatches[0].reason == "size_mismatch"
