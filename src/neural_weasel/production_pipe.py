from __future__ import annotations

from typing import Any

from .neural_candidates import (
    CandidatePageError,
    CandidatePageTimeout,
    NeuralLanguageMode,
)
from .pipe_server import (
    MAX_PINYIN_KEYS,
    NamedPipeServer,
    _error,
    _optional_identifier,
    _optional_query_identity,
    _reject_unknown_fields,
    _require_int,
)
from .protocol import ProtocolError

_RUNTIME_COUNT_KEYS = (
    "max_before_tokens",
    "n_ctx",
    "n_batch",
    "last_refresh_context_tokens",
    "last_refresh_evaluated_tokens",
    "last_candidate_page_index",
    "last_candidate_count",
    "last_candidate_search_depth",
    "last_candidate_length_bucket",
    "candidate_page_timeout_count",
)
_RUNTIME_LATENCY_KEYS = (
    "last_refresh_latency_ms",
    "last_candidate_search_elapsed_ms",
)


def _safe_runtime_metric(key: str, value: object) -> int | float | None:
    if value is None:
        return None
    if key in _RUNTIME_LATENCY_KEYS:
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
            raise RuntimeError("invalid cached runtime latency metric")
        return float(value)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise RuntimeError("invalid cached runtime count metric")
    return value


class ProductionNamedPipeServer(NamedPipeServer):
    """Production pipe server with pure-neural paging and safe diagnostics."""

    def handle_message(self, message: dict[str, Any]) -> dict[str, Any]:
        message_type = message.get("type")
        if message_type == "query_candidate_page":
            return self._handle_candidate_page_request(message)
        if message_type != "diagnostics":
            return super().handle_message(message)

        request_id = None
        try:
            request_id = _optional_identifier(message, "request_id")
            _reject_unknown_fields(message, frozenset({"type", "request_id"}))
            provider = getattr(self.engine, "runtime_performance_diagnostics", None)
            if not callable(provider):
                return _error(
                    "diagnostics_unavailable",
                    "runtime performance diagnostics are unavailable",
                    request_id=request_id,
                )
            raw = provider()
            if not isinstance(raw, dict):
                raise RuntimeError("runtime performance diagnostics must be a mapping")

            ready_epoch = int(self.engine.context_epoch)
            with self._state_lock:
                requested_epoch = self._requested_context_epoch
            response: dict[str, Any] = {
                "type": "diagnostics",
                "ok": True,
                "context_epoch": ready_epoch,
                "requested_context_epoch": requested_epoch,
                "context_updating": requested_epoch > ready_epoch,
            }
            for key in _RUNTIME_COUNT_KEYS:
                response[key] = _safe_runtime_metric(key, raw.get(key))
            for key in _RUNTIME_LATENCY_KEYS:
                response[key] = _safe_runtime_metric(key, raw.get(key))
            if request_id is not None:
                response["request_id"] = request_id
            return response
        except ProtocolError as error:
            return _error("invalid_request", str(error), request_id=request_id)
        except Exception:
            return _error(
                "internal_error",
                "request processing failed",
                request_id=request_id,
                retryable=True,
            )

    def _handle_candidate_page_request(self, message: dict[str, Any]) -> dict[str, Any]:
        request_id = None
        try:
            request_id = _optional_identifier(message, "request_id")
            _reject_unknown_fields(
                message,
                frozenset(
                    {
                        "type",
                        "request_id",
                        "session_id",
                        "composition_revision",
                        "context_epoch",
                        "context_session",
                        "source_revision",
                        "language_mode",
                        "raw_keys",
                        "page_index",
                        "candidate_set_id",
                    }
                ),
            )
            session_id = _optional_identifier(message, "session_id")
            if session_id is None:
                raise ProtocolError("session_id is required")
            composition_revision = _require_int(message, "composition_revision", 0)
            context_epoch = _require_int(message, "context_epoch", 0)
            page_index = _require_int(message, "page_index", 0)
            candidate_set_id = _optional_identifier(message, "candidate_set_id")
            raw_keys = message.get("raw_keys")
            if not isinstance(raw_keys, str) or not raw_keys or len(raw_keys) > MAX_PINYIN_KEYS:
                raise ProtocolError(
                    f"raw_keys must be a non-empty string of at most {MAX_PINYIN_KEYS} characters"
                )
            try:
                language_mode = NeuralLanguageMode(message.get("language_mode"))
            except (TypeError, ValueError) as error:
                raise ProtocolError(
                    "language_mode must be chinese_first or latin_first"
                ) from error

            identity = _optional_query_identity(message)
            if context_epoch == 0 and identity is not None:
                raise ProtocolError("context identity must be omitted for context_epoch 0")
            if context_epoch > 0 and identity is None:
                raise ProtocolError("nonzero context_epoch requires context identity")
            binding_error = self._binding_error(message, context_epoch)
            if binding_error is not None:
                return binding_error

            if page_index == 0 and candidate_set_id is not None:
                raise ProtocolError("candidate_set_id must be omitted for page 0")
            if page_index > 0 and candidate_set_id is None:
                raise ProtocolError("candidate_set_id is required after page 0")

            context_session = identity[0] if identity is not None else None
            source_revision = identity[1] if identity is not None else None
            page = self.engine.query_candidate_page(
                client_session_id=session_id,
                composition_revision=composition_revision,
                context_epoch=context_epoch,
                context_session=context_session,
                source_revision=source_revision,
                language_mode=language_mode,
                raw_keys=raw_keys,
                page_index=page_index,
                candidate_set_id=candidate_set_id,
            )
            values = []
            for candidate_id, candidate in zip(
                page.candidate_ids,
                page.candidates,
                strict=True,
            ):
                value = candidate.to_dict() if hasattr(candidate, "to_dict") else dict(candidate)
                value["candidate_id"] = candidate_id
                value["context_epoch"] = context_epoch
                values.append(value)

            response: dict[str, Any] = {
                "type": "candidate_page",
                "ok": True,
                "session_id": session_id,
                "composition_revision": composition_revision,
                "context_epoch": context_epoch,
                "language_mode": language_mode.value,
                "candidate_set_id": page.candidate_set_id,
                "page_index": page.page_index,
                "page_size": page.page_size,
                "has_more": page.has_more,
                "score_source": page.score_source,
                "candidates": values,
            }
            if request_id is not None:
                response["request_id"] = request_id
            return response
        except ProtocolError as error:
            return _error("invalid_request", str(error), request_id=request_id)
        except CandidatePageTimeout:
            return _error(
                "candidate_page_timeout",
                "candidate page search exceeded its absolute deadline",
                request_id=request_id,
                retryable=True,
            )
        except CandidatePageError as error:
            return _error(
                "candidate_set_invalid",
                str(error),
                request_id=request_id,
                retryable=False,
            )
        except Exception:
            return _error(
                "internal_error",
                "request processing failed",
                request_id=request_id,
                retryable=True,
            )
