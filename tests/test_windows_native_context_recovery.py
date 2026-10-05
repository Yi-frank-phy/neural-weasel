"""Opt-in real Windows pipe fault injection against a separately built probe."""

from __future__ import annotations

import os
import subprocess
import threading
import uuid
from contextlib import suppress
from pathlib import Path

import pytest

from neural_weasel.pipe_server import (
    NamedPipeServer,
    _read_message,
    _win32_modules,
    _write_message,
)
from neural_weasel.response_workers import after_response

PROBE = os.environ.get("NEURAL_WEASEL_RECOVERY_PROBE")
pytestmark = pytest.mark.skipif(
    os.name != "nt" or not PROBE, reason="isolated native probe required"
)


class Engine:
    context_epoch = 0
    calls = 0

    def request_context_update(self, before: str, after: str) -> int:
        # Public fixture only; no production runtime, model or history.
        assert before == "public synthetic fixture" and after == ""
        self.calls += 1
        self.context_epoch += 1
        return self.context_epoch


class DropServer(NamedPipeServer):
    def __init__(self, engine: Engine, mode: str) -> None:
        super().__init__(engine, pipe_name=rf"\\.\pipe\nw-recovery-test-{uuid.uuid4().hex}")
        self.mode = mode
        self.dropped = False
        self.receipts = 0
        self.requests = 0

    def _serve_connection(self, handle: object) -> None:
        pywintypes, _, _, win32file, win32pipe = _win32_modules()
        try:
            while not self._stop_event.is_set():
                try:
                    message = _read_message(handle)
                    self.requests += 1
                    if message["type"] == "context_update_receipt":
                        self.receipts += 1
                    drop = not self.dropped and (
                        message["type"] == ("health" if self.mode == "health" else "context_update")
                    )
                    if drop:
                        self.dropped = True
                        if self.mode != "before_accept":
                            self.handle_message(message)
                        # Close the real OS pipe after reading the frame, either
                        # before processing or after acceptance but before ack.
                        break
                    with after_response():
                        _write_message(handle, self.handle_message(message))
                except (EOFError, pywintypes.error):
                    break
        finally:
            with suppress(pywintypes.error):
                win32pipe.DisconnectNamedPipe(handle)
            win32file.CloseHandle(handle)
            with self._client_threads_lock:
                self._client_threads.discard(threading.current_thread())


@pytest.mark.parametrize("mode", ["before_accept", "lost_ack", "health"])
def test_one_submit_recovers_across_real_pipe_disconnect(mode: str, tmp_path: Path) -> None:
    engine = Engine()
    server = DropServer(engine, mode)
    server.start()
    try:
        environment = os.environ | {"LOCALAPPDATA": str(tmp_path)}
        result = subprocess.run(
            [str(PROBE), server.pipe_name],
            env=environment,
            capture_output=True,
            text=True,
            timeout=5,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        assert result.returncode == 0, (result.stdout, result.stderr)
        assert "single_update_published=1 epoch=1" in result.stdout
        assert server.dropped and engine.calls == 1
        assert server.receipts == (0 if mode == "health" else 1)
        print(
            f"mode={mode} single_update_published=1 model_updates={engine.calls} "
            f"receipts={server.receipts} frames={server.requests}"
        )
    finally:
        server.stop()
    assert not server._server_thread.is_alive()
