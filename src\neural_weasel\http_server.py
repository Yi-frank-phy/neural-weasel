from __future__ import annotations

import base64
import binascii
import json
import os
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

MAX_REQUEST_BYTES = 65_536
MAX_PROMPT_CHARS = 16_384
MAX_CONSTRAINTS = 64
MAX_CONSTRAINT_CHARS = 32
FIRST_PAGE_CANDIDATES = 5
MAX_CANDIDATES = 50
FIRST_PAGE_CONTEXT_WAIT_SECONDS = 0.012
BRIDGE_POLL_SECONDS = 0.005


def _encode_bridge_candidates(candidates: list[Any]) -> str:
    return "\n".join(
        f"{candidate.consumed_keys}\t{candidate.text.strip()}" for candidate in candidates
    )


class WisdomHttpServer(ThreadingHTTPServer):
    """Loopback-only HTTP adapter for Wisdom Weasel's HF provider."""

    daemon_threads = True

    def __init__(self, address: tuple[str, int], engine: Any) -> None:
        host, _ = address
        if host not in {"127.0.0.1", "localhost"}:
            raise ValueError("the Wisdom HTTP adapter may bind only to loopback")
        super().__init__(address, WisdomRequestHandler)
        self.engine = engine
        self.engine_lock = threading.Lock()
        self.stats_lock = threading.Lock()
        self.request_count = 0
        self.success_count = 0
        self.failure_count = 0
        self.last_latency_ms: int | None = None
        self.context_request_count = 0
        self.last_prompt_chars = 0
        self.context_update_count = 0
        self.cached_query_count = 0
        self.snapshot_not_ready_count = 0
        self.stale_fallback_count = 0
        self.context_lock = threading.Lock()
        self.requested_prompt: str | None = None
        self.requested_epoch = 0

    def record_request(self, *, success: bool, latency_ms: int, prompt_chars: int) -> None:
        with self.stats_lock:
            self.request_count += 1
            if success:
                self.success_count += 1
            else:
                self.failure_count += 1
            self.last_latency_ms = latency_ms
            self.last_prompt_chars = prompt_chars
            if prompt_chars > 0:
                self.context_request_count += 1

    def stats(self) -> dict[str, int | None]:
        with self.stats_lock:
            return {
                "request_count": self.request_count,
                "success_count": self.success_count,
                "failure_count": self.failure_count,
                "last_latency_ms": self.last_latency_ms,
                "context_request_count": self.context_request_count,
                "last_prompt_chars": self.last_prompt_chars,
                "context_update_count": self.context_update_count,
                "cached_query_count": self.cached_query_count,
                "snapshot_not_ready_count": self.snapshot_not_ready_count,
                "stale_fallback_count": self.stale_fallback_count,
            }

    def request_context(self, prompt: str) -> int:
        with self.context_lock:
            if prompt == self.requested_prompt:
                return self.requested_epoch
            epoch = self.engine.request_context_update(prompt, "")
            self.requested_prompt = prompt
            self.requested_epoch = epoch
        with self.stats_lock:
            self.context_update_count += 1
        return epoch

    def query_candidates(
        self,
        prompt: str,
        raw_keys: str,
        candidate_count: int = FIRST_PAGE_CANDIDATES,
    ) -> tuple[list[Any], bool]:
        epoch = self.request_context(prompt)
        exact_snapshot = self.engine.has_snapshot(epoch)
        if not exact_snapshot:
            # Candidate count must not turn a keypress into a slow paging wait.
            exact_snapshot = self.engine.wait_for_epoch(epoch, FIRST_PAGE_CONTEXT_WAIT_SECONDS)
        query_epoch = epoch
        if not exact_snapshot:
            with self.stats_lock:
                self.snapshot_not_ready_count += 1
            # Keep the key path responsive while the newest context is being
            # prefetched. Epoch zero asks the engine for its latest completed
            # immutable snapshot; it never exposes a half-written forward.
            query_epoch = 0
        with self.engine_lock:
            candidates = self.engine.query(
                raw_keys,
                candidate_count,
                context_epoch=query_epoch,
            )
        with self.stats_lock:
            self.cached_query_count += 1
            if not exact_snapshot and candidates:
                self.stale_fallback_count += 1
        return [
            candidate for candidate in candidates if candidate.text and candidate.text.strip()
        ], exact_snapshot

    def generate(
        self,
        prompt: str,
        raw_keys: str,
        candidate_count: int = FIRST_PAGE_CANDIDATES,
    ) -> tuple[str, bool]:
        candidates, exact_snapshot = self.query_candidates(prompt, raw_keys, candidate_count)
        return " ".join(candidate.text.strip() for candidate in candidates), exact_snapshot


class WisdomRequestHandler(BaseHTTPRequestHandler):
    server: WisdomHttpServer

    def log_message(self, format: str, *args: object) -> None:
        # Requests can be derived from private composition context. Do not log
        # paths, payloads, candidates, or caller-controlled error details.
        return

    def _write_json(self, status: HTTPStatus, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(status.value)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/health":
            diagnostics = self.server.engine.diagnostics()
            self._write_json(
                HTTPStatus.OK,
                {
                    "status": "ok",
                    "model": diagnostics.get("model"),
                    "precision": diagnostics.get("precision"),
                    "backend_kind": diagnostics.get("backend_kind"),
                },
            )
            return
        if self.path == "/stats":
            self._write_json(HTTPStatus.OK, self.server.stats())
            return
        self._write_json(HTTPStatus.NOT_FOUND, {"error": "not_found"})

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/v1/generate/completions":
            self._write_json(HTTPStatus.NOT_FOUND, {"error": "not_found"})
            return

        started = time.perf_counter()
        recorded = False
        prompt_chars = 0

        def record(success: bool) -> None:
            nonlocal recorded
            if recorded:
                return
            recorded = True
            elapsed_ms = round((time.perf_counter() - started) * 1000)
            self.server.record_request(
                success=success,
                latency_ms=elapsed_ms,
                prompt_chars=prompt_chars,
            )

        try:
            content_length = int(self.headers.get("Content-Length", ""))
        except ValueError:
            content_length = -1
        if content_length < 0 or content_length > MAX_REQUEST_BYTES:
            record(False)
            self._write_json(HTTPStatus.BAD_REQUEST, {"error": "invalid_request"})
            return

        try:
            request = json.loads(self.rfile.read(content_length).decode("utf-8"))
            prompt, raw_keys, candidate_count = _validate_request(request)
            prompt_chars = len(prompt)
            responses, _ = self.server.generate(prompt, raw_keys, candidate_count)
            record(True)
            self._write_json(HTTPStatus.OK, {"responses": responses})
        except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError):
            record(False)
            self._write_json(HTTPStatus.BAD_REQUEST, {"error": "invalid_request"})
        except Exception:
            # Never return exception text because model errors can contain prompt
            # fragments. Wisdom Weasel safely degrades to ordinary Rime results.
            record(False)
            self._write_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "inference_failed"})


def _validate_request(request: object) -> tuple[str, str, int]:
    if not isinstance(request, dict):
        raise ValueError("request must be an object")
    if not request.keys() <= {"prompt", "prompt_b64", "pinyin_constraints", "candidate_count"}:
        raise ValueError("request contains unsupported fields")
    if "prompt" in request and "prompt_b64" in request:
        raise ValueError("request contains conflicting prompt fields")

    if "prompt_b64" in request:
        encoded_prompt = request["prompt_b64"]
        if not isinstance(encoded_prompt, str):
            raise ValueError("invalid prompt")
        try:
            prompt = base64.b64decode(encoded_prompt, validate=True).decode("utf-8")
        except (binascii.Error, UnicodeDecodeError):
            raise ValueError("invalid prompt") from None
    else:
        prompt = request.get("prompt", "")
    constraints = request.get("pinyin_constraints", [])
    candidate_count = request.get("candidate_count", FIRST_PAGE_CANDIDATES)
    if not isinstance(prompt, str) or len(prompt) > MAX_PROMPT_CHARS:
        raise ValueError("invalid prompt")
    if not isinstance(constraints, list) or len(constraints) > MAX_CONSTRAINTS:
        raise ValueError("invalid constraints")
    if (
        not isinstance(candidate_count, int)
        or isinstance(candidate_count, bool)
        or candidate_count < 1
        or candidate_count > MAX_CANDIDATES
    ):
        raise ValueError("invalid candidate_count")
    if any(
        not isinstance(part, str)
        or not part
        or len(part) > MAX_CONSTRAINT_CHARS
        or not part.isascii()
        or not all(character.isalnum() or character in "'-" for character in part)
        for part in constraints
    ):
        raise ValueError("invalid constraint")

    return prompt, "".join(constraints).lower(), candidate_count


def _serve_file_bridge(
    server: WisdomHttpServer,
    bridge_root: Path,
    stopping: threading.Event,
) -> None:
    bridge_root.mkdir(parents=True, exist_ok=True)
    while not stopping.is_set():
        handled = False
        for request_path in bridge_root.glob("*.request"):
            handled = True
            started = time.perf_counter()
            prompt_chars = 0
            response_path = request_path.with_suffix(".response")
            temporary_response = response_path.with_suffix(".response.tmp")
            send_response = True
            try:
                if request_path.stat().st_size > MAX_REQUEST_BYTES:
                    raise ValueError("invalid request")
                request = json.loads(request_path.read_text(encoding="utf-8"))
                operation = request.get("operation", "query")
                clean_request = {key: value for key, value in request.items() if key != "operation"}
                prompt, raw_keys, candidate_count = _validate_request(clean_request)
                prompt_chars = len(prompt)
                if operation == "context":
                    send_response = False
                    server.request_context(prompt)
                elif operation == "query":
                    candidates, _ = server.query_candidates(prompt, raw_keys, candidate_count)
                    responses = _encode_bridge_candidates(candidates)
                    temporary_response.write_text(responses, encoding="utf-8")
                    os.replace(temporary_response, response_path)
                else:
                    raise ValueError("invalid operation")
                success = True
            except Exception:
                success = False
                temporary_response.unlink(missing_ok=True)
                if send_response:
                    response_path.write_bytes(b"")
            finally:
                request_path.unlink(missing_ok=True)
                elapsed_ms = round((time.perf_counter() - started) * 1000)
                server.record_request(
                    success=success,
                    latency_ms=elapsed_ms,
                    prompt_chars=prompt_chars,
                )
        if not handled:
            stopping.wait(BRIDGE_POLL_SECONDS)


def serve_wisdom_http(engine: Any, host: str = "127.0.0.1", port: int = 8000) -> None:
    server = WisdomHttpServer((host, port), engine)
    server.request_context("")
    bridge_root = Path(os.environ["LOCALAPPDATA"]) / "NeuralWeasel" / "Bridge"
    stopping = threading.Event()
    bridge_thread = threading.Thread(
        target=_serve_file_bridge,
        args=(server, bridge_root, stopping),
        name="neural-weasel-file-bridge",
        daemon=True,
    )
    bridge_thread.start()
    try:
        server.serve_forever(poll_interval=0.25)
    finally:
        stopping.set()
        bridge_thread.join(timeout=2)
        server.server_close()
