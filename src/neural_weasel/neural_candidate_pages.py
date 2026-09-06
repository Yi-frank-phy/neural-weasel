from __future__ import annotations

from typing import Any

from .backends import BackendState
from .neural_candidate_pages_scored import NeuralCandidatePageManager as _ScoredPageManager
from .neural_candidates import CandidatePage, NeuralLanguageMode, _SearchIdentity


class NeuralCandidatePageManager(_ScoredPageManager):
    """Publish completed background work only on an explicit presentation pull."""

    def query_page(
        self,
        *,
        client_session_id: str,
        composition_revision: int,
        context_epoch: int,
        context_session: str | None,
        source_revision: int | None,
        mode: NeuralLanguageMode | str,
        raw_keys: str,
        page_index: int,
        candidate_set_id: str | None,
        state: BackendState | None,
        deadline_ms: float | None = None,
        presentation_refresh: bool = False,
    ) -> CandidatePage:
        normalized_mode = NeuralLanguageMode(mode)
        if page_index == 0 and not presentation_refresh:
            identity = _SearchIdentity(
                client_session_id=client_session_id,
                composition_revision=composition_revision,
                context_epoch=context_epoch,
                context_session=context_session,
                source_revision=source_revision,
                mode=normalized_mode,
                raw_keys=raw_keys,
            )
            with self._state_lock:
                self._expire_sessions()
                # Multiple immutable presentation snapshots may share one input
                # identity. A normal retry always replays the newest published
                # snapshot; only presentation_refresh may advance it.
                for existing_set_id, session in reversed(tuple(self._sessions.items())):
                    if session.identity != identity:
                        continue
                    frozen = session.frozen_pages.get(0)
                    if frozen is None:
                        continue
                    session.last_used = self.clock()
                    self._sessions.move_to_end(existing_set_id)
                    self._record_metrics(frozen)
                    return frozen

        return super().query_page(
            client_session_id=client_session_id,
            composition_revision=composition_revision,
            context_epoch=context_epoch,
            context_session=context_session,
            source_revision=source_revision,
            mode=normalized_mode,
            raw_keys=raw_keys,
            page_index=page_index,
            candidate_set_id=candidate_set_id,
            state=state,
            deadline_ms=deadline_ms,
        )


__all__ = ["NeuralCandidatePageManager"]
