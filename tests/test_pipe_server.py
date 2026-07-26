from __future__ import annotations

import os
import threading
import time
import uuid
from dataclasses import dataclass

import pytest

from neural_weasel.pipe_client import NamedPipeClient
from neural_weasel.pipe_server import (
    NamedPipeServer,
    _current_user_security_attributes,
    current_user_sid_string,
)

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows named-pipe tests")


@dataclass(frozen=True)
class FakeCandidate:
    text: str
    pinyin: str
    consumed_keys: int
    score: float
    context_epoch: int = 1

    def to_dict(self) -> dict[str, object]:
        return {
            "text": self.text,
            "pinyin": self.pinyin,
            "consumed_keys": self.consumed_keys,
            "score": self.score,
            "context_epoch": self.context_epoch,
            "coverage": False,
        }


class FakeEngine:
    def __init__(self, delay: float = 0) -> None:
        self.delay = delay
        self.before = ""
        self.after = ""
        self.reset_count = 0
        self.context_epoch = 0
        self.requested_epoch = 0
        self.contexts: dict[int, tuple[str, str]] = {}

    def request_context_update(self, before: str, after: str = "") -> int:
        self.requested_epoch += 1
        epoch = self.requested_epoch

        def update() -> None:
            time.sleep(self.delay)
            self.before = before
            self.after = after
            self.contexts[epoch] = (before, after)
            self.context_epoch = epoch

        threading.Thread(target=update, daemon=True).start()
        return epoch

    def query(
        self,
        raw_pinyin: str,
        limit: int = 5,
        context_epoch: int | None = None,
    ) -> list[FakeCandidate]:
        before, _ = self.contexts.get(context_epoch or self.context_epoch, ("", ""))
        text = "纠缠" if raw_pinyin == "jiuchan" and "协议" in before else "就餐"
        epoch = context_epoch or self.context_epoch
        return [FakeCandidate(text, raw_pinyin, len(raw_pinyin), -0.25, epoch)][:limit]

    def reset(self) -> None:
        self.reset_count += 1


def _pipe_name() -> str:
    return rf"\\.\pipe\NeuralWeasel-test-{uuid.uuid4()}"


def _wait_ready(client: NamedPipeClient, epoch: int, timeout: float = 2) -> dict[str, object]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        response = client.request({"type": "health"})
        if response["ready"] and response["context_epoch"] == epoch:
            return response
        time.sleep(0.01)
    raise AssertionError("server did not publish the context snapshot")


def test_reusable_connection_context_query_and_reset() -> None:
    engine = FakeEngine()
    server = NamedPipeServer(engine, _pipe_name())
    server.start()
    try:
        with NamedPipeClient(server.pipe_name) as client:
            first = client.request({"type": "health", "request_id": "h1"})
            assert first == {
                "type": "health",
                "ok": True,
                "ready": False,
                "context_updating": False,
                "context_epoch": 0,
                "requested_context_epoch": 0,
                "last_context_error": None,
                "request_id": "h1",
            }
            accepted = client.request(
                {
                    "type": "context_update",
                    "request_id": "c1",
                    "context_epoch": 7,
                    "before": "该协议所消耗的",
                    "after": "资源",
                }
            )
            assert accepted["accepted"] is True
            assert accepted["client_context_epoch"] == 7
            assigned_epoch = accepted["context_epoch"]
            _wait_ready(client, assigned_epoch)

            candidates = client.request(
                {
                    "type": "query_pinyin",
                    "request_id": "q1",
                    "session_id": "session-a",
                    "revision": 3,
                    "context_epoch": assigned_epoch,
                    "raw_keys": "jiuchan",
                    "candidate_count": 5,
                }
            )
            assert candidates["type"] == "candidates"
            assert candidates["request_id"] == "q1"
            assert candidates["revision"] == 3
            assert candidates["stale"] is False
            assert candidates["candidates"][0]["text"] == "纠缠"
            assert candidates["candidates"][0]["context_epoch"] == assigned_epoch
            assert client.connected

            reset = client.request({"type": "reset", "session_id": "session-a"})
            assert reset["ok"] is True
            assert reset["context_epoch"] == assigned_epoch
            assert engine.reset_count == 1
    finally:
        server.stop()


def test_previous_snapshot_is_queryable_while_next_context_updates() -> None:
    engine = FakeEngine(delay=0.05)
    server = NamedPipeServer(engine, _pipe_name())
    server.start()
    try:
        with NamedPipeClient(server.pipe_name) as client:
            client.request(
                {
                    "type": "context_update",
                    "context_epoch": 1,
                    "before": "旧协议",
                    "after": "",
                }
            )
            _wait_ready(client, 1)
            client.request(
                {
                    "type": "context_update",
                    "context_epoch": 2,
                    "before": "新内容",
                    "after": "",
                }
            )
            response = client.request(
                {
                    "type": "query_pinyin",
                    "session_id": "s",
                    "revision": 1,
                    "context_epoch": 1,
                    "raw_keys": "jiuchan",
                    "candidate_count": 5,
                }
            )
            assert response["ok"] is True
            assert response["context_epoch"] == 1
            assert response["stale"] is False
            assert response["candidates"][0]["context_epoch"] == 1
            assert response["candidates"][0]["text"] == "纠缠"
            _wait_ready(client, 2)
            locked = client.request(
                {
                    "type": "query_pinyin",
                    "session_id": "s",
                    "revision": 1,
                    "context_epoch": 1,
                    "raw_keys": "jiuchan",
                    "candidate_count": 5,
                }
            )
            latest = client.request(
                {
                    "type": "query_pinyin",
                    "session_id": "s",
                    "revision": 2,
                    "context_epoch": 2,
                    "raw_keys": "jiuchan",
                    "candidate_count": 5,
                }
            )
            assert locked["candidates"][0]["text"] == "纠缠"
            assert latest["candidates"][0]["text"] == "就餐"
    finally:
        server.stop()


def test_validation_errors_are_structured() -> None:
    server = NamedPipeServer(FakeEngine(), _pipe_name())
    server.start()
    try:
        with NamedPipeClient(server.pipe_name) as client:
            unknown = client.request({"type": "not-real", "request_id": "bad"})
            assert unknown["ok"] is False
            assert unknown["error"]["code"] == "unknown_message_type"
            assert unknown["request_id"] == "bad"

            invalid = client.request(
                {
                    "type": "query_pinyin",
                    "session_id": "s",
                    "revision": True,
                    "context_epoch": 0,
                    "raw_keys": "ni",
                    "candidate_count": 5,
                }
            )
            assert invalid["ok"] is False
            assert invalid["error"]["code"] == "invalid_request"
    finally:
        server.stop()


def test_pipe_dacl_contains_only_current_user() -> None:
    import win32security

    attributes = _current_user_security_attributes()
    descriptor = attributes.SECURITY_DESCRIPTOR
    dacl = descriptor.GetSecurityDescriptorDacl()
    assert dacl.GetAceCount() == 1
    ace = dacl.GetAce(0)
    assert str(win32security.ConvertSidToStringSid(ace[2])) == current_user_sid_string()


def test_context_failure_does_not_echo_private_text() -> None:
    private_text = "PRIVATE-CONTEXT-MUST-NOT-LEAK"

    class FailingEngine(FakeEngine):
        def request_context_update(self, before: str, after: str = "") -> int:
            raise RuntimeError(f"failed while processing {before}")

    server = NamedPipeServer(FailingEngine(), _pipe_name())
    server.start()
    try:
        with NamedPipeClient(server.pipe_name) as client:
            response = client.request(
                {
                    "type": "context_update",
                    "context_epoch": 1,
                    "before": private_text,
                    "after": "",
                }
            )
            assert response["ok"] is False
            serialized = repr(response)
            assert private_text not in serialized
            assert response["error"]["code"] == "internal_error"
    finally:
        server.stop()
