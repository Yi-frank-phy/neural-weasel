from __future__ import annotations

from dataclasses import dataclass

from neural_weasel.qwenime_compat.bridge import QwenImeBridge
from neural_weasel.qwenime_compat.protocol import parse_normalized_request


@dataclass
class _Candidate:
    text: str
    pinyin: str = ""


class _FakeEngine:
    def __init__(self) -> None:
        self.queries: list[tuple[str, int, int | None]] = []
        self.commits: list[str] = []
        self.contexts: list[tuple[str, str]] = []
        self.fail_query = False

    def request_context_update(self, before: str, after: str) -> int:
        self.contexts.append((before, after))
        return len(self.contexts)

    def query(
        self,
        raw_keys: str,
        limit: int = 5,
        context_epoch: int | None = None,
    ) -> list[_Candidate]:
        self.queries.append((raw_keys, limit, context_epoch))
        if self.fail_query:
            raise RuntimeError("private model error")
        table = {
            "n": [_Candidate("你", "ni"), _Candidate("呢", "ne")],
            "ni": [_Candidate("你", "ni"), _Candidate("拟", "ni")],
        }
        return table.get(raw_keys, [])[:limit]

    def commit(self, text: str) -> None:
        self.commits.append(text)


def _request(function: str, **fields: object):
    return parse_normalized_request({"function": function, **fields})


def test_full_pinyin_minimal_loop_commits_selected_candidate() -> None:
    engine = _FakeEngine()
    bridge = QwenImeBridge(engine, session_id_factory=lambda: "session-1")

    started = bridge.handle(_request("start_session", before="量子"))
    typed_n = bridge.handle(_request("process_key", session_id="session-1", key="n"))
    typed_i = bridge.handle(_request("process_key", session_id="session-1", key="i"))
    committed = bridge.handle(_request("process_key", session_id="session-1", key="space"))

    assert started.session_id == "session-1"
    assert typed_n.composition.raw_input == "n"
    assert [candidate.text for candidate in typed_i.composition.candidates] == ["你", "拟"]
    assert committed.commit == "你"
    assert not committed.composition.has_preedit
    assert engine.commits == ["你"]
    assert engine.contexts == [("量子", "")]


def test_query_failure_preserves_literal_and_returns_no_candidates() -> None:
    engine = _FakeEngine()
    engine.fail_query = True
    bridge = QwenImeBridge(engine, session_id_factory=lambda: "session-1")
    bridge.handle(_request("start_session"))

    response = bridge.handle(_request("process_key", session_id="session-1", key="n"))

    assert response.ok
    assert response.handled
    assert response.composition.raw_input == "n"
    assert response.composition.candidates == ()
    assert response.commit == ""


def test_missing_session_never_creates_state_implicitly() -> None:
    engine = _FakeEngine()
    bridge = QwenImeBridge(engine)

    response = bridge.handle(_request("process_key", session_id="missing", key="n"))

    assert not response.ok
    assert not response.handled
    assert response.error_code == "missing_session"
    assert engine.queries == []


def test_escape_clears_composition_without_committing() -> None:
    engine = _FakeEngine()
    bridge = QwenImeBridge(engine, session_id_factory=lambda: "session-1")
    bridge.handle(_request("start_session"))
    bridge.handle(_request("process_key", session_id="session-1", key="n"))

    response = bridge.handle(_request("process_key", session_id="session-1", key="escape"))

    assert response.handled
    assert response.composition.raw_input == ""
    assert response.commit == ""
    assert engine.commits == []


def test_candidate_action_selects_explicit_index() -> None:
    engine = _FakeEngine()
    bridge = QwenImeBridge(engine, session_id_factory=lambda: "session-1")
    bridge.handle(_request("start_session"))
    bridge.handle(_request("process_key", session_id="session-1", key="n"))

    response = bridge.handle(
        _request(
            "candidate_action",
            session_id="session-1",
            candidate_index=1,
            candidate_action="select",
        )
    )

    assert response.commit == "呢"
    assert engine.commits == ["呢"]
