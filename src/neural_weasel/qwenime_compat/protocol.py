from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any

MAX_JSON_BYTES = 1_048_576
MAX_SESSION_ID = 128
MAX_RAW_INPUT = 512
MAX_CANDIDATES = 50
MAX_COMMIT_TEXT = 32_768


class QwenImeProtocolError(ValueError):
    pass


class RequestKind(StrEnum):
    START_SESSION = "start_session"
    END_SESSION = "end_session"
    FOCUS_IN = "focus_in"
    FOCUS_OUT = "focus_out"
    UPDATE_INPUT_POSITION = "update_input_position"
    PROCESS_KEY = "process_key"
    CANDIDATE_ACTION = "candidate_action"
    DRAIN_CANDIDATE_ACTION_RESULT = "drain_candidate_action_result"
    CANCEL_COMPOSITION = "cancel_composition"


@dataclass(frozen=True, slots=True)
class NormalizedRequest:
    kind: RequestKind
    session_id: str | None = None
    key: str | None = None
    candidate_index: int | None = None
    candidate_action: str | None = None
    before: str = ""
    after: str = ""
    candidate_count: int = 9
    secure: bool = False
    extras: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class CandidateView:
    text: str
    candidate_id: str
    label: str = ""
    comment: str = ""


@dataclass(frozen=True, slots=True)
class CompositionView:
    raw_input: str = ""
    preedit: str = ""
    candidates: tuple[CandidateView, ...] = ()
    selected_index: int = 0

    @property
    def has_preedit(self) -> bool:
        return bool(self.preedit or self.raw_input)


@dataclass(frozen=True, slots=True)
class NormalizedResponse:
    function: str
    ok: bool
    handled: bool
    session_id: str | None
    composition: CompositionView = CompositionView()
    commit: str = ""
    error_code: str | None = None

    def to_dict(self) -> dict[str, Any]:
        candidates = [asdict(candidate) for candidate in self.composition.candidates]
        value: dict[str, Any] = {
            "function": self.function,
            "ok": self.ok,
            "handled": self.handled,
            "session_id": self.session_id,
            "has_preedit": self.composition.has_preedit,
            "preedit": self.composition.preedit,
            "raw_input": self.composition.raw_input,
            "commit": self.commit,
            "candidate_count": len(candidates),
            "candidates": candidates,
            "selected_index": self.composition.selected_index,
        }
        if self.error_code is not None:
            value["error_code"] = self.error_code
        return value


def _optional_text(message: Mapping[str, Any], key: str, maximum: int) -> str | None:
    value = message.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or len(value) > maximum:
        raise QwenImeProtocolError(f"{key} must be a string of at most {maximum} characters")
    return value


def _optional_index(message: Mapping[str, Any], key: str) -> int | None:
    value = message.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise QwenImeProtocolError(f"{key} must be a non-negative integer")
    return value


def _optional_bool(message: Mapping[str, Any], key: str, default: bool = False) -> bool:
    value = message.get(key, default)
    if not isinstance(value, bool):
        raise QwenImeProtocolError(f"{key} must be a boolean")
    return value


def parse_normalized_request(message: Mapping[str, Any]) -> NormalizedRequest:
    """Parse the version-pinned normalized request model.

    This deliberately does not claim to be the final proprietary packet codec.
    The packet header and exact field aliases remain behind a later captured-fixture gate.
    """

    if not isinstance(message, Mapping):
        raise QwenImeProtocolError("request must be a JSON object")
    function = message.get("function", message.get("type"))
    if not isinstance(function, str):
        raise QwenImeProtocolError("function must be a string")
    try:
        kind = RequestKind(function)
    except ValueError as error:
        raise QwenImeProtocolError("unsupported function") from error

    session_id = _optional_text(message, "session_id", MAX_SESSION_ID)
    key = _optional_text(message, "key", 32)
    before = _optional_text(message, "before", MAX_COMMIT_TEXT) or ""
    after = _optional_text(message, "after", MAX_COMMIT_TEXT) or ""
    candidate_index = _optional_index(message, "candidate_index")
    candidate_action = _optional_text(message, "candidate_action", 64)
    secure = _optional_bool(message, "secure")

    candidate_count_value = message.get("candidate_count", 9)
    if (
        isinstance(candidate_count_value, bool)
        or not isinstance(candidate_count_value, int)
        or not 1 <= candidate_count_value <= MAX_CANDIDATES
    ):
        raise QwenImeProtocolError(f"candidate_count must be an integer from 1 to {MAX_CANDIDATES}")

    known = {
        "function",
        "type",
        "session_id",
        "key",
        "candidate_index",
        "candidate_action",
        "before",
        "after",
        "candidate_count",
        "secure",
    }
    extras = {str(key): value for key, value in message.items() if key not in known}
    return NormalizedRequest(
        kind=kind,
        session_id=session_id,
        key=key,
        candidate_index=candidate_index,
        candidate_action=candidate_action,
        before=before,
        after=after,
        candidate_count=candidate_count_value,
        secure=secure,
        extras=extras,
    )


def parse_json_payload(payload: bytes) -> NormalizedRequest:
    if len(payload) > MAX_JSON_BYTES:
        raise QwenImeProtocolError("JSON payload is too large")
    try:
        value = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise QwenImeProtocolError("invalid UTF-8 JSON payload") from error
    if not isinstance(value, dict):
        raise QwenImeProtocolError("top-level JSON value must be an object")
    return parse_normalized_request(value)


def serialize_json_payload(response: NormalizedResponse) -> bytes:
    payload = json.dumps(
        response.to_dict(),
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    if len(payload) > MAX_JSON_BYTES:
        raise QwenImeProtocolError("JSON payload is too large")
    return payload
