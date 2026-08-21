from __future__ import annotations

import os
import re
import threading
from collections.abc import Callable
from contextlib import suppress
from typing import Any

from .protocol import MAX_MESSAGE_BYTES, ProtocolError, decode_message, encode_message

PIPE_PREFIX = r"\\.\pipe\NeuralWeasel-v1-"
MAX_PINYIN_KEYS = 512
MAX_CANDIDATES = 50
MAX_COMMIT_TEXT = 32_768
IDENTIFIER_PATTERN = re.compile(r"[A-Za-z0-9._:-]+")


class PipeUnavailableError(RuntimeError):
    """Raised when the Windows named-pipe runtime is unavailable."""


def _win32_modules() -> tuple[Any, Any, Any, Any, Any]:
    if os.name != "nt":
        raise PipeUnavailableError("Windows named pipes are only available on Windows")
    try:
        import pywintypes
        import win32api
        import win32con
        import win32file
        import win32pipe
    except ImportError as error:
        raise PipeUnavailableError("pywin32 is required for the named-pipe service") from error
    return pywintypes, win32api, win32con, win32file, win32pipe


def _security_modules() -> tuple[Any, Any, Any, Any]:
    if os.name != "nt":
        raise PipeUnavailableError("Windows named pipes are only available on Windows")
    try:
        import pywintypes
        import win32api
        import win32con
        import win32security
    except ImportError as error:
        raise PipeUnavailableError("pywin32 is required for pipe ACL creation") from error
    return pywintypes, win32api, win32con, win32security


def current_user_sid_string() -> str:
    _, win32api, win32con, win32security = _security_modules()
    token = win32security.OpenProcessToken(win32api.GetCurrentProcess(), win32con.TOKEN_QUERY)
    try:
        sid = win32security.GetTokenInformation(token, win32security.TokenUser)[0]
        return str(win32security.ConvertSidToStringSid(sid))
    finally:
        token.Close()


def default_pipe_name() -> str:
    sid = re.sub(r"[^A-Za-z0-9-]", "-", current_user_sid_string())
    return f"{PIPE_PREFIX}{sid}"


def _current_user_security_attributes() -> Any:
    pywintypes, win32api, win32con, win32security = _security_modules()
    token = win32security.OpenProcessToken(win32api.GetCurrentProcess(), win32con.TOKEN_QUERY)
    try:
        user_sid = win32security.GetTokenInformation(token, win32security.TokenUser)[0]
    finally:
        token.Close()

    dacl = win32security.ACL()
    dacl.AddAccessAllowedAce(
        win32security.ACL_REVISION,
        win32con.GENERIC_READ | win32con.GENERIC_WRITE,
        user_sid,
    )
    descriptor = win32security.SECURITY_DESCRIPTOR()
    descriptor.SetSecurityDescriptorOwner(user_sid, False)
    descriptor.SetSecurityDescriptorDacl(True, dacl, False)
    attributes = pywintypes.SECURITY_ATTRIBUTES()
    attributes.SECURITY_DESCRIPTOR = descriptor
    return attributes


def _read_exact(handle: Any, size: int) -> bytes:
    pywintypes, _, _, win32file, _ = _win32_modules()
    chunks: list[bytes] = []
    remaining = size
    while remaining:
        try:
            _, chunk = win32file.ReadFile(handle, remaining)
        except pywintypes.error as error:
            raise EOFError("named-pipe peer disconnected") from error
        if not chunk:
            raise EOFError("named-pipe peer disconnected")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _read_message(handle: Any) -> dict[str, Any]:
    prefix = _read_exact(handle, 4)
    declared = int.from_bytes(prefix, "little")
    if declared > MAX_MESSAGE_BYTES:
        raise ProtocolError(f"declared message is too large: {declared} bytes")
    return decode_message(prefix + _read_exact(handle, declared))


def _write_message(handle: Any, message: dict[str, Any]) -> None:
    _, _, _, win32file, _ = _win32_modules()
    frame = encode_message(message)
    written = 0
    while written < len(frame):
        _, count = win32file.WriteFile(handle, frame[written:])
        if not isinstance(count, int):
            # pywin32 returns the written bytes for synchronous pipe handles.
            count = len(count)
        if count <= 0:
            raise EOFError("named-pipe peer disconnected")
        written += count


def _error(
    code: str,
    message: str,
    *,
    request_id: object | None = None,
    retryable: bool = False,
) -> dict[str, Any]:
    response: dict[str, Any] = {
        "type": "error",
        "ok": False,
        "error": {"code": code, "message": message, "retryable": retryable},
    }
    if request_id is not None:
        response["request_id"] = request_id
    return response


def _require_int(message: dict[str, Any], key: str, minimum: int = 0) -> int:
    value = message.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ProtocolError(f"{key} must be an integer >= {minimum}")
    return value


def _optional_identifier(message: dict[str, Any], key: str) -> str | None:
    value = message.get(key)
    if value is None:
        return None
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 128
        or IDENTIFIER_PATTERN.fullmatch(value) is None
    ):
        raise ProtocolError(f"{key} must be a valid identifier of at most 128 characters")
    return value


def _require_bool(message: dict[str, Any], key: str) -> bool:
    value = message.get(key)
    if not isinstance(value, bool):
        raise ProtocolError(f"{key} must be a boolean")
    return value


def _reject_unknown_fields(message: dict[str, Any], allowed: frozenset[str]) -> None:
    if not message.keys() <= allowed:
        # Do not include caller-controlled field names: they may themselves contain private text.
        raise ProtocolError("message contains unsupported fields")


class NamedPipeServer:
    """Per-user, reusable Windows named-pipe service for cached IME queries."""

    def __init__(
        self,
        engine: Any,
        pipe_name: str | None = None,
        *,
        max_instances: int = 4,
    ) -> None:
        if max_instances < 1:
            raise ValueError("max_instances must be positive")
        self.engine = engine
        self.pipe_name = pipe_name or default_pipe_name()
        self.max_instances = max_instances
        self._stop_event = threading.Event()
        self._listening_event = threading.Event()
        self._server_thread: threading.Thread | None = None
        self._client_threads: set[threading.Thread] = set()
        self._client_threads_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._context_forward_lock = threading.Lock()
        self._latest_client_context_epoch = 0
        self._requested_context_epoch = 0
        self._last_context_error: str | None = None

    def start(self, timeout: float = 5.0) -> None:
        if self._server_thread and self._server_thread.is_alive():
            return
        self._stop_event.clear()
        self._listening_event.clear()
        self._server_thread = threading.Thread(
            target=self.serve_forever,
            name="neural-weasel-pipe-server",
            daemon=True,
        )
        self._server_thread.start()
        if not self._listening_event.wait(timeout):
            raise TimeoutError("named-pipe server did not start listening")

    def serve_forever(self) -> None:
        pywintypes, _, win32con, win32file, win32pipe = _win32_modules()
        security_attributes = _current_user_security_attributes()
        first_instance = True
        while not self._stop_event.is_set():
            open_mode = win32pipe.PIPE_ACCESS_DUPLEX
            if first_instance:
                # Refuse to start behind a pre-created permissive pipe with the
                # same predictable SID-derived name.
                open_mode |= win32pipe.FILE_FLAG_FIRST_PIPE_INSTANCE
            try:
                handle = win32pipe.CreateNamedPipe(
                    self.pipe_name,
                    open_mode,
                    win32pipe.PIPE_TYPE_BYTE
                    | win32pipe.PIPE_READMODE_BYTE
                    | win32pipe.PIPE_WAIT
                    | getattr(win32pipe, "PIPE_REJECT_REMOTE_CLIENTS", 0x8),
                    self.max_instances,
                    65_536,
                    65_536,
                    0,
                    security_attributes,
                )
            except pywintypes.error as error:
                if not first_instance and error.winerror == 231:  # ERROR_PIPE_BUSY
                    # All persistent instances are occupied. There is no
                    # capacity for another listener until one disconnects.
                    self._stop_event.wait(0.005)
                    continue
                raise
            first_instance = False
            self._listening_event.set()
            try:
                try:
                    win32pipe.ConnectNamedPipe(handle, None)
                except pywintypes.error as error:
                    if error.winerror == 535:  # ERROR_PIPE_CONNECTED
                        pass
                    elif error.winerror == 232:  # ERROR_NO_DATA
                        # A client can connect and close between CreateNamedPipe
                        # and ConnectNamedPipe. The instance is no longer
                        # reusable, but the listener itself must stay alive.
                        win32file.CloseHandle(handle)
                        if self._stop_event.is_set():
                            break
                        continue
                    else:
                        raise
                if self._stop_event.is_set():
                    win32file.CloseHandle(handle)
                    break
                thread = threading.Thread(
                    target=self._serve_connection,
                    args=(handle,),
                    name="neural-weasel-pipe-client",
                    daemon=True,
                )
                with self._client_threads_lock:
                    self._client_threads.add(thread)
                thread.start()
            except Exception:
                win32file.CloseHandle(handle)
                if not self._stop_event.is_set():
                    raise

    def stop(self, timeout: float = 5.0) -> None:
        self._stop_event.set()
        if self._server_thread and self._server_thread.is_alive():
            with suppress(OSError, PipeUnavailableError, TimeoutError):
                from .pipe_client import NamedPipeClient

                with NamedPipeClient(self.pipe_name, timeout_ms=500):
                    pass
            self._server_thread.join(timeout)
        with self._client_threads_lock:
            threads = tuple(self._client_threads)
        for thread in threads:
            thread.join(timeout)

    def _serve_connection(self, handle: Any) -> None:
        pywintypes, _, _, win32file, win32pipe = _win32_modules()
        try:
            while not self._stop_event.is_set():
                try:
                    request = _read_message(handle)
                    response = self.handle_message(request)
                    _write_message(handle, response)
                except EOFError:
                    break
                except ProtocolError as error:
                    _write_message(handle, _error("protocol_error", str(error)))
                    break
                except pywintypes.error:
                    break
        finally:
            with suppress(pywintypes.error):
                win32pipe.DisconnectNamedPipe(handle)
            win32file.CloseHandle(handle)
            current = threading.current_thread()
            with self._client_threads_lock:
                self._client_threads.discard(current)

    def handle_message(self, message: dict[str, Any]) -> dict[str, Any]:
        request_id = None
        try:
            request_id = _optional_identifier(message, "request_id")
            if not isinstance(message.get("type"), str):
                raise ProtocolError("type must be a string")
            message_type = message["type"]
            handlers: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
                "health": self._handle_health,
                "context_update": self._handle_context_update,
                "query_pinyin": self._handle_query_pinyin,
                "query_candidates": self._handle_query_candidates,
                "reset": self._handle_reset,
                "commit": self._handle_commit,
                "focus": self._handle_focus,
                "fatal": self._handle_fatal,
            }
            handler = handlers.get(message_type)
            if handler is None:
                return _error(
                    "unknown_message_type",
                    "unsupported message type",
                    request_id=request_id,
                )
            response = handler(message)
            if request_id is not None:
                response["request_id"] = request_id
            return response
        except ProtocolError as error:
            return _error("invalid_request", str(error), request_id=request_id)
        except Exception:
            # Never include exception text: model errors can contain prompt fragments.
            return _error(
                "internal_error",
                "request processing failed",
                request_id=request_id,
                retryable=True,
            )

    def _handle_health(self, message: dict[str, Any]) -> dict[str, Any]:
        ready_epoch = int(self.engine.context_epoch)
        with self._state_lock:
            requested_epoch = self._requested_context_epoch
            return {
                "type": "health",
                "ok": True,
                "ready": ready_epoch > 0,
                "context_updating": requested_epoch > ready_epoch,
                "context_epoch": ready_epoch,
                "requested_context_epoch": requested_epoch,
                "last_context_error": self._last_context_error,
            }

    def _handle_context_update(self, message: dict[str, Any]) -> dict[str, Any]:
        epoch = _require_int(message, "context_epoch", 1)
        before = message.get("before")
        after = message.get("after", "")
        if not isinstance(before, str) or not isinstance(after, str):
            raise ProtocolError("before and after must be strings")

        # Multiple pipe clients may race. Serialize only the cheap forwarding
        # decision so an already superseded client revision never enters the
        # model coordinator. request_context_update itself is asynchronous.
        with self._context_forward_lock:
            if epoch <= self._latest_client_context_epoch:
                with self._state_lock:
                    requested_epoch = self._requested_context_epoch
                return {
                    "type": "context_update",
                    "ok": True,
                    "accepted": False,
                    "stale": True,
                    "context_epoch": requested_epoch,
                    "client_context_epoch": epoch,
                }

            assigned_epoch = self.engine.request_context_update(before, after)
            if isinstance(assigned_epoch, bool) or not isinstance(assigned_epoch, int):
                raise RuntimeError("engine returned an invalid context epoch")
            self._latest_client_context_epoch = epoch
            with self._state_lock:
                self._requested_context_epoch = assigned_epoch
                self._last_context_error = None

        return {
            "type": "context_update",
            "ok": True,
            "accepted": True,
            "context_epoch": assigned_epoch,
            "client_context_epoch": epoch,
        }

    def _handle_query_pinyin(self, message: dict[str, Any]) -> dict[str, Any]:
        session_id = _optional_identifier(message, "session_id")
        if session_id is None:
            raise ProtocolError("session_id is required")
        revision = _require_int(message, "revision", 0)
        requested_epoch = _require_int(message, "context_epoch", 0)
        raw_pinyin = message.get("raw_keys", message.get("raw_pinyin"))
        if not isinstance(raw_pinyin, str) or len(raw_pinyin) > MAX_PINYIN_KEYS:
            raise ProtocolError(
                f"raw_keys must be a string of at most {MAX_PINYIN_KEYS} characters"
            )
        limit_key = "candidate_count" if "candidate_count" in message else "candidates"
        limit = _require_int(message, limit_key, 1)
        if limit > MAX_CANDIDATES:
            raise ProtocolError(f"candidate_count must not exceed {MAX_CANDIDATES}")

        latest_epoch = int(self.engine.context_epoch)
        if requested_epoch == 0:
            requested_epoch = latest_epoch
        elif requested_epoch > latest_epoch:
            return _error(
                "context_not_ready",
                "requested model context snapshot is not ready",
                request_id=message.get("request_id"),
                retryable=True,
            )
        elif requested_epoch < latest_epoch:
            has_snapshot = getattr(self.engine, "has_snapshot", None)
            if callable(has_snapshot) and not has_snapshot(requested_epoch):
                return _error(
                    "context_expired",
                    "requested model context snapshot has expired",
                    request_id=message.get("request_id"),
                    retryable=False,
                )

        candidates = []
        for candidate in self.engine.query(raw_pinyin, limit, context_epoch=requested_epoch):
            value = candidate.to_dict() if hasattr(candidate, "to_dict") else dict(candidate)
            value["context_epoch"] = requested_epoch
            candidates.append(value)
        return {
            "type": "candidates",
            "ok": True,
            "session_id": session_id,
            "revision": revision,
            "context_epoch": requested_epoch,
            # An empty result for an older epoch is ambiguous without an engine
            # snapshot-membership API. Never claim it is current; callers should
            # retry against the latest epoch.
            "stale": not candidates and requested_epoch < latest_epoch,
            "candidates": candidates,
        }

    def _handle_query_candidates(self, message: dict[str, Any]) -> dict[str, Any]:
        session_id = _optional_identifier(message, "session_id")
        if session_id is None:
            raise ProtocolError("session_id is required")
        revision = _require_int(message, "revision", 0)
        requested_epoch = _require_int(message, "context_epoch", 0)
        raw_keys = message.get("raw_keys")
        if not isinstance(raw_keys, str) or len(raw_keys) > MAX_PINYIN_KEYS:
            raise ProtocolError(
                f"raw_keys must be a string of at most {MAX_PINYIN_KEYS} characters"
            )
        limit = _require_int(message, "candidate_count", 1)
        if limit > MAX_CANDIDATES:
            raise ProtocolError(f"candidate_count must not exceed {MAX_CANDIDATES}")

        latest_epoch = int(self.engine.context_epoch)
        if requested_epoch == 0:
            requested_epoch = latest_epoch
        elif requested_epoch > latest_epoch:
            return _error(
                "context_not_ready",
                "requested model context snapshot is not ready",
                request_id=message.get("request_id"),
                retryable=True,
            )
        elif requested_epoch < latest_epoch:
            has_snapshot = getattr(self.engine, "has_snapshot", None)
            if callable(has_snapshot) and not has_snapshot(requested_epoch):
                return _error(
                    "context_expired",
                    "requested model context snapshot has expired",
                    request_id=message.get("request_id"),
                    retryable=False,
                )

        values = []
        for candidate in self.engine.query(raw_keys, limit, context_epoch=requested_epoch):
            value = candidate.to_dict() if hasattr(candidate, "to_dict") else dict(candidate)
            value["context_epoch"] = requested_epoch
            values.append(value)
        context_kind = getattr(self.engine, "context_kind", None)
        input_mode = context_kind(requested_epoch) if callable(context_kind) else "ambiguous"
        return {
            "type": "candidates",
            "ok": True,
            "session_id": session_id,
            "revision": revision,
            "context_epoch": requested_epoch,
            "stale": bool(requested_epoch < latest_epoch),
            "input_mode": input_mode,
            "candidates": values,
        }

    def _handle_reset(self, message: dict[str, Any]) -> dict[str, Any]:
        session_id = _optional_identifier(message, "session_id")
        with self._state_lock:
            self._requested_context_epoch = int(self.engine.context_epoch)
            self._last_context_error = None
        reset = getattr(self.engine, "reset", None)
        if callable(reset):
            reset()
        return {
            "type": "reset",
            "ok": True,
            "session_id": session_id,
            "context_epoch": int(self.engine.context_epoch),
        }

    def _handle_commit(self, message: dict[str, Any]) -> dict[str, Any]:
        _reject_unknown_fields(
            message,
            frozenset({"type", "request_id", "session_id", "revision", "text"}),
        )
        session_id = _optional_identifier(message, "session_id")
        if session_id is None:
            raise ProtocolError("session_id is required")
        revision = _require_int(message, "revision", 0)
        text = message.get("text")
        if not isinstance(text, str) or len(text) > MAX_COMMIT_TEXT:
            raise ProtocolError(f"text must be a string of at most {MAX_COMMIT_TEXT} characters")

        commit = getattr(self.engine, "commit", None)
        if callable(commit):
            commit(text)
        return {
            "type": "commit",
            "ok": True,
            "accepted": True,
            "session_id": session_id,
            "revision": revision,
        }

    def _handle_focus(self, message: dict[str, Any]) -> dict[str, Any]:
        _reject_unknown_fields(
            message,
            frozenset({"type", "request_id", "session_id", "focused", "secure"}),
        )
        session_id = _optional_identifier(message, "session_id")
        if session_id is None:
            raise ProtocolError("session_id is required")
        focused = _require_bool(message, "focused")
        secure = _require_bool(message, "secure")

        if secure:
            cleanup_failed = False
            for method_name in ("reset_private_context", "clear_history"):
                method = getattr(self.engine, method_name, None)
                if callable(method):
                    try:
                        method()
                    except Exception:
                        cleanup_failed = True
            if cleanup_failed:
                raise RuntimeError("secure context cleanup failed")
        return {
            "type": "focus",
            "ok": True,
            "accepted": True,
            "session_id": session_id,
            "focused": focused,
            "secure": secure,
        }

    def _handle_fatal(self, message: dict[str, Any]) -> dict[str, Any]:
        _reject_unknown_fields(
            message,
            frozenset({"type", "request_id", "session_id", "revision"}),
        )
        session_id = _optional_identifier(message, "session_id")
        if session_id is None:
            raise ProtocolError("session_id is required")
        revision = _require_int(message, "revision", 0)

        # This is deliberately only a protocol acknowledgement. System-profile
        # switching belongs to the native TSF integration, never the model service.
        return {
            "type": "fatal",
            "ok": True,
            "acknowledged": True,
            "service_alive": True,
            "session_id": session_id,
            "revision": revision,
        }
