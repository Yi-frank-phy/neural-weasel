"""Disposable test service; intentionally exits at a specified protocol stage."""

from __future__ import annotations

import argparse
import json
import os
import threading
import time
from contextlib import suppress
from pathlib import Path

from neural_weasel.pipe_server import NamedPipeServer, _read_message, _win32_modules, _write_message


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pipe", required=True)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--role", choices=["primary", "replacement"], required=True)
    parser.add_argument("--stage", choices=["before_accept", "lost_ack", "health"], required=True)
    parser.add_argument("--epoch", type=int, required=True)
    args = parser.parse_args()
    if not args.pipe.startswith(r"\\.\pipe\nw-recovery-test-"):
        parser.error("isolated test pipe required")
    directory = args.directory
    log = directory / f"{args.role}.jsonl"
    log_lock = threading.Lock()

    def record(event: str, **counts: int) -> None:
        with log_lock, log.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({"event": event, "pid": os.getpid(), **counts}) + "\n")

    class Engine:
        context_epoch = args.epoch
        calls = 0

        def request_context_update(self, before: str, after: str) -> int:
            assert before == "public synthetic fixture" and after == ""
            self.calls += 1
            self.context_epoch += 1
            record("model_update", calls=self.calls, epoch=self.context_epoch)
            return self.context_epoch

    class CrashServer(NamedPipeServer):
        def _serve_connection(self, handle: object) -> None:
            pywintypes, _, _, win32file, win32pipe = _win32_modules()
            try:
                while not self._stop_event.is_set():
                    try:
                        message = _read_message(handle)
                        kind = message["type"]
                        if kind == "context_update_receipt":
                            record("receipt")
                        crash = args.role == "primary" and kind == (
                            "health" if args.stage == "health" else "context_update"
                        )
                        if crash and args.stage != "before_accept":
                            self.handle_message(message)
                        if crash:
                            record("intentional_exit")
                            os._exit(73)
                        _write_message(handle, self.handle_message(message))
                    except (EOFError, pywintypes.error):
                        break
            finally:
                with suppress(pywintypes.error):
                    win32pipe.DisconnectNamedPipe(handle)
                win32file.CloseHandle(handle)
                with self._client_threads_lock:
                    self._client_threads.discard(threading.current_thread())

    record("armed")
    deadline = time.monotonic() + 30
    if args.role == "replacement":
        while not (directory / "replace.gate").exists():
            if time.monotonic() >= deadline:
                raise TimeoutError("replacement gate not released")
            time.sleep(0.001)
    server = CrashServer(Engine(), pipe_name=args.pipe)
    server.start()
    record("listening")
    try:
        while not (directory / "stop.gate").exists() and time.monotonic() < deadline:
            time.sleep(0.005)
    finally:
        server.stop()
    record("stopped")


if __name__ == "__main__":
    main()
