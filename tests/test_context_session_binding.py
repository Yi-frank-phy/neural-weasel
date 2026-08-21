from __future__ import annotations

from dataclasses import dataclass

from neural_weasel.pipe_server import NamedPipeServer


@dataclass
class Candidate:
    text: str = "ok"

    def to_dict(self) -> dict[str, object]:
        return {"text": self.text}


class RecordingEngine:
    def __init__(self) -> None:
        self.context_epoch = 0
        self.updates: list[tuple[str, str]] = []
        self.queries: list[tuple[str, int, int | None]] = []

    def request_context_update(self, before: str, after: str = "") -> int:
        self.updates.append((before, after))
        self.context_epoch += 1
        return self.context_epoch

    def query(self, raw: str, limit: int, context_epoch: int | None = None) -> list[Candidate]:
        self.queries.append((raw, limit, context_epoch))
        return [Candidate()]

    def has_snapshot(self, epoch: int) -> bool:
        return 1 <= epoch <= self.context_epoch


def _update(
    server: NamedPipeServer,
    *,
    client_epoch: int,
    context_session: str,
    source_revision: int,
    label: str = "normal",
) -> dict[str, object]:
    return server.handle_message(
        {
            "type": "context_update",
            "context_epoch": client_epoch,
            "context_session": context_session,
            "source_revision": source_revision,
            "security_label": label,
            "before": "paper context",
            "after": "",
        }
    )


def _query(
    server: NamedPipeServer,
    *,
    epoch: int,
    context_session: str,
    source_revision: int,
) -> dict[str, object]:
    return server.handle_message(
        {
            "type": "query_candidates",
            "session_id": "rime-test",
            "revision": 1,
            "context_epoch": epoch,
            "context_session": context_session,
            "source_revision": source_revision,
            "raw_keys": "lunwen",
            "candidate_count": 5,
        }
    )


def test_model_epoch_is_bound_to_editor_capability_and_revision() -> None:
    engine = RecordingEngine()
    server = NamedPipeServer(engine, pipe_name=r"\\.\pipe\unused-binding-test")
    capability = "a" * 32

    accepted = _update(
        server,
        client_epoch=7,
        context_session=capability,
        source_revision=4,
    )
    assert accepted["ok"] is True
    epoch = int(accepted["context_epoch"])

    matching = _query(
        server,
        epoch=epoch,
        context_session=capability,
        source_revision=4,
    )
    assert matching["ok"] is True
    assert len(engine.queries) == 1

    wrong_capability = _query(
        server,
        epoch=epoch,
        context_session="b" * 32,
        source_revision=4,
    )
    assert wrong_capability["ok"] is False
    assert wrong_capability["error"]["code"] == "context_session_mismatch"

    wrong_revision = _query(
        server,
        epoch=epoch,
        context_session=capability,
        source_revision=3,
    )
    assert wrong_revision["ok"] is False
    assert wrong_revision["error"]["code"] == "context_session_mismatch"
    assert len(engine.queries) == 1


def test_context_binding_rejects_malformed_identity_and_password_payload() -> None:
    engine = RecordingEngine()
    server = NamedPipeServer(engine, pipe_name=r"\\.\pipe\unused-binding-invalid")

    malformed = _update(
        server,
        client_epoch=1,
        context_session="NOT-HEX",
        source_revision=1,
    )
    assert malformed["ok"] is False

    password = _update(
        server,
        client_epoch=2,
        context_session="c" * 32,
        source_revision=1,
        label="password",
    )
    assert password["ok"] is False
    assert engine.updates == []


def test_context_binding_is_bounded_and_epoch_zero_never_reuses_old_context() -> None:
    engine = RecordingEngine()
    server = NamedPipeServer(engine, pipe_name=r"\\.\pipe\unused-binding-bounded")

    oldest_epoch = 0
    for index in range(9):
        capability = f"{index + 1:032x}"
        accepted = _update(
            server,
            client_epoch=index + 1,
            context_session=capability,
            source_revision=1,
            label="private" if index == 8 else "normal",
        )
        assert accepted["ok"] is True
        if index == 0:
            oldest_epoch = int(accepted["context_epoch"])

    expired = _query(
        server,
        epoch=oldest_epoch,
        context_session=f"{1:032x}",
        source_revision=1,
    )
    assert expired["ok"] is False
    assert expired["error"]["code"] in {"context_expired", "context_session_mismatch"}

    no_identity = _query(
        server,
        epoch=0,
        context_session="0" * 32,
        source_revision=1,
    )
    assert no_identity["ok"] is False
    assert len(engine.queries) == 0
