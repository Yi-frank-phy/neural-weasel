from __future__ import annotations

from typing import Any

from .pipe_server import (
    NamedPipeServer,
    _error,
    _optional_identifier,
    _reject_unknown_fields,
)
from .protocol import ProtocolError

_RUNTIME_COUNT_KEYS = (
    "max_before_tokens",
    "n_ctx",
    "n_batch",
    "last_refresh_context_tokens",
    "last_refresh_evaluated_tokens",
)
_RUNTIME_LATENCY_KEY = "last_refresh_latency_ms"


def _safe_runtime_metric(key: str, value: object) -> int | float | None:
    if value is None:
        return None
    if key == _RUNTIME_LATENCY_KEY:
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
            raise RuntimeError("invalid cached runtime latency metric")
        return float(value)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise RuntimeError("invalid cached runtime count metric")
    return value


class ProductionNamedPipeServer(NamedPipeServer):
    """Production pipe server with a metadata-only runtime diagnostic request."""

    def handle_message(self, message: dict[str, Any]) -> dict[str, Any]:
        if message.get("type") != "diagnostics":
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
            response[_RUNTIME_LATENCY_KEY] = _safe_runtime_metric(
                _RUNTIME_LATENCY_KEY,
                raw.get(_RUNTIME_LATENCY_KEY),
            )
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
