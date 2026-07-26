from __future__ import annotations

import threading
import time
from typing import Any

from .pipe_server import (
    PipeUnavailableError,
    _read_message,
    _win32_modules,
    _write_message,
    default_pipe_name,
)


class NamedPipeClient:
    """Synchronous reusable client; callers may retain one connection per composition."""

    def __init__(self, pipe_name: str | None = None, *, timeout_ms: int = 1_000) -> None:
        if timeout_ms < 0:
            raise ValueError("timeout_ms must be non-negative")
        self.pipe_name = pipe_name or default_pipe_name()
        self.timeout_ms = timeout_ms
        self._handle: Any | None = None
        self._lock = threading.Lock()

    @property
    def connected(self) -> bool:
        return self._handle is not None

    def connect(self) -> None:
        pywintypes, _, win32con, win32file, win32pipe = _win32_modules()
        if self._handle is not None:
            return
        deadline = time.monotonic() + self.timeout_ms / 1_000
        while True:
            remaining_ms = max(0, int((deadline - time.monotonic()) * 1_000))
            try:
                win32pipe.WaitNamedPipe(self.pipe_name, remaining_ms)
                self._handle = win32file.CreateFile(
                    self.pipe_name,
                    win32con.GENERIC_READ | win32con.GENERIC_WRITE,
                    0,
                    None,
                    win32con.OPEN_EXISTING,
                    0,
                    None,
                )
                return
            except pywintypes.error as error:
                if time.monotonic() >= deadline:
                    raise TimeoutError(
                        f"could not connect to named pipe: {self.pipe_name}"
                    ) from error
                if error.winerror not in {2, 121, 231}:  # not found, timeout, busy
                    raise OSError(error.winerror, "named-pipe connection failed") from error

    def close(self) -> None:
        if self._handle is None:
            return
        _, _, _, win32file, _ = _win32_modules()
        win32file.CloseHandle(self._handle)
        self._handle = None

    def request(self, message: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            if self._handle is None:
                self.connect()
            if self._handle is None:
                raise PipeUnavailableError("named-pipe connection was not created")
            try:
                _write_message(self._handle, message)
                return _read_message(self._handle)
            except (EOFError, OSError):
                self.close()
                raise

    def __enter__(self) -> NamedPipeClient:
        self.connect()
        return self

    def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> None:
        self.close()
