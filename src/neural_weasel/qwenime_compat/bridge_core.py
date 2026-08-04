from __future__ import annotations

from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass, field
from typing import Any, Protocol
from uuid import uuid4

from .protocol import (
    CandidateView,
    CompositionView,
    NormalizedRequest,
    NormalizedResponse,
    RequestKind,
)


class CandidateEngine(Protocol):
    def query(
        self,
        raw_keys: str,
        limit: int = 5,
        context_epoch: int | None = None,
    ) -> list[Any]: ...


@dataclass(slots=True)
class _Session:
    raw_input: str = ""
    candidates: list[CandidateView] = field(default_factory=list)
    selected_index: int = 0
    context_epoch: int = 0


class QwenImeBridge:
    """Fail-closed normalized QwenIME-to-Neural-Weasel adapter core.

    The class is intentionally independent of Windows named-pipe framing. It can be
    replay-tested in CI before any vendor process is touched.
    """

    def __init__(
        self,
        engine: CandidateEngine,
        *,
        session_id_factory: Callable[[], str] | None = None,
    ) -> None:
        self.engine = engine
        self._sessions: dict[str, _Session] = {}
        self._session_id_factory = session_id_factory or (lambda: uuid4().hex)

    def handle(self, request: NormalizedRequest) -> NormalizedResponse:
        if request.kind == RequestKind.START_SESSION:
            return self._start_session(request)

        session = self._sessions.get(request.session_id or "")
        if session is None:
            return NormalizedResponse(
                function=request.kind.value,
                ok=False,
                handled=False,
                session_id=request.session_id,
                error_code="missing_session",
            )

        if request.kind == RequestKind.END_SESSION:
            del self._sessions[request.session_id or ""]
            return self._response(request, session, handled=True)
        if request.kind in {RequestKind.FOCUS_IN, RequestKind.FOCUS_OUT}:
            return self._response(request, session, handled=False)
        if request.kind == RequestKind.UPDATE_INPUT_POSITION:
            return self._response(request, session, handled=False)
        if request.kind == RequestKind.CANCEL_COMPOSITION:
            self._clear(session)
            return self._response(request, session, handled=True)
        if request.kind == RequestKind.CANDIDATE_ACTION:
            return self._candidate_action(request, session)
        if request.kind == RequestKind.DRAIN_CANDIDATE_ACTION_RESULT:
            return self._response(request, session, handled=False)
        if request.kind == RequestKind.PROCESS_KEY:
            return self._process_key(request, session)
        return self._response(request, session, handled=False)

    def _start_session(self, request: NormalizedRequest) -> NormalizedResponse:
        session_id = request.session_id or self._session_id_factory()
        if not session_id or len(session_id) > 128:
            return NormalizedResponse(
                function=request.kind.value,
                ok=False,
                handled=False,
                session_id=None,
                error_code="invalid_session_id",
            )
        session = _Session()
        self._sessions[session_id] = session
        self._refresh_context(session, request)
        return NormalizedResponse(
            function=request.kind.value,
            ok=True,
            handled=True,
            session_id=session_id,
        )

    def _process_key(self, request: NormalizedRequest, session: _Session) -> NormalizedResponse:
        key = request.key
        if key is None:
            return self._response(
                request,
                session,
                ok=False,
                handled=False,
                error_code="missing_key",
            )

        self._refresh_context(session, request)
        normalized = key.lower()
        if len(key) == 1 and ("a" <= normalized <= "z" or key == "'"):
            session.raw_input += normalized
            self._refresh_candidates(session, request.candidate_count)
            return self._response(request, session, handled=True)
        if normalized == "backspace":
            if not session.raw_input:
                return self._response(request, session, handled=False)
            session.raw_input = session.raw_input[:-1]
            self._refresh_candidates(session, request.candidate_count)
            return self._response(request, session, handled=True)
        if normalized == "escape":
            if not session.raw_input:
                return self._response(request, session, handled=False)
            self._clear(session)
            return self._response(request, session, handled=True)
        if normalized == "space":
            if not session.raw_input:
                return self._response(request, session, handled=False)
            text = self._selected_text(session) or session.raw_input
            return self._commit(request, session, text)
        if normalized == "enter":
            if not session.raw_input:
                return self._response(request, session, handled=False)
            return self._commit(request, session, session.raw_input)
        if normalized in {"up", "left"}:
            return self._move_selection(request, session, -1)
        if normalized in {"down", "right"}:
            return self._move_selection(request, session, 1)
        if len(key) == 1 and "1" <= key <= "9":
            index = int(key) - 1
            if index >= len(session.candidates):
                return self._response(request, session, handled=False)
            return self._commit(request, session, session.candidates[index].text)
        return self._response(request, session, handled=False)

    def _candidate_action(
        self,
        request: NormalizedRequest,
        session: _Session,
    ) -> NormalizedResponse:
        index = request.candidate_index
        if index is None or index >= len(session.candidates):
            return self._response(
                request,
                session,
                ok=False,
                handled=False,
                error_code="invalid_candidate_index",
            )
        action = request.candidate_action or "select"
        if action in {"highlight", "hover"}:
            session.selected_index = index
            return self._response(request, session, handled=True)
        if action in {"select", "commit"}:
            return self._commit(request, session, session.candidates[index].text)
        return self._response(
            request,
            session,
            ok=False,
            handled=False,
            error_code="unsupported_candidate_action",
        )

    def _move_selection(
        self,
        request: NormalizedRequest,
        session: _Session,
        step: int,
    ) -> NormalizedResponse:
        if not session.candidates:
            return self._response(request, session, handled=False)
        session.selected_index = (session.selected_index + step) % len(session.candidates)
        return self._response(request, session, handled=True)

    def _refresh_context(self, session: _Session, request: NormalizedRequest) -> None:
        if not request.before and not request.after:
            return
        update = getattr(self.engine, "request_context_update", None)
        if not callable(update):
            return
        try:
            epoch = update(request.before, request.after)
        except Exception:
            return
        if isinstance(epoch, int) and not isinstance(epoch, bool) and epoch >= 0:
            session.context_epoch = epoch

    def _refresh_candidates(self, session: _Session, limit: int) -> None:
        if not session.raw_input:
            session.candidates.clear()
            session.selected_index = 0
            return
        try:
            values = self.engine.query(
                session.raw_input,
                limit,
                context_epoch=session.context_epoch,
            )
        except Exception:
            session.candidates.clear()
            session.selected_index = 0
            return

        candidates: list[CandidateView] = []
        for index, candidate in enumerate(values[:limit]):
            text = getattr(candidate, "text", None)
            if text is None and isinstance(candidate, dict):
                text = candidate.get("text")
            if not isinstance(text, str) or not text:
                continue
            pinyin = getattr(candidate, "pinyin", "")
            if not isinstance(pinyin, str):
                pinyin = ""
            candidates.append(
                CandidateView(
                    text=text,
                    candidate_id=str(index),
                    label=str(index + 1),
                    comment=pinyin,
                )
            )
        session.candidates = candidates
        session.selected_index = min(session.selected_index, max(0, len(candidates) - 1))

    def _commit(
        self,
        request: NormalizedRequest,
        session: _Session,
        text: str,
    ) -> NormalizedResponse:
        commit = getattr(self.engine, "commit", None)
        if callable(commit):
            with suppress(Exception):
                commit(text)
        self._clear(session)
        return self._response(request, session, handled=True, commit=text)

    @staticmethod
    def _selected_text(session: _Session) -> str | None:
        if 0 <= session.selected_index < len(session.candidates):
            return session.candidates[session.selected_index].text
        return None

    @staticmethod
    def _clear(session: _Session) -> None:
        session.raw_input = ""
        session.candidates.clear()
        session.selected_index = 0

    @staticmethod
    def _response(
        request: NormalizedRequest,
        session: _Session,
        *,
        ok: bool = True,
        handled: bool,
        commit: str = "",
        error_code: str | None = None,
    ) -> NormalizedResponse:
        composition = CompositionView(
            raw_input=session.raw_input,
            preedit=session.raw_input,
            candidates=tuple(session.candidates),
            selected_index=session.selected_index,
        )
        return NormalizedResponse(
            function=request.kind.value,
            ok=ok,
            handled=handled,
            session_id=request.session_id,
            composition=composition,
            commit=commit,
            error_code=error_code,
        )
