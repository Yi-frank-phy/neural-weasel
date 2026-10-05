"""Isolated real Win32 pipe tests: process restart and revision/cleanup races."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

import pytest
from test_windows_native_context_recovery import PROBE, DropServer, Engine

pytestmark = pytest.mark.skipif(
    os.name != "nt" or not PROBE, reason="isolated native probe required"
)


def events(directory: Path, role: str) -> list[dict]:
    path = directory / f"{role}.jsonl"
    if not path.exists():
        return []
    # A concurrent write may expose an unfinished final line; wait for newline.
    return [
        json.loads(line)
        for line in path.read_text().splitlines(keepends=True)
        if line.endswith("\n")
    ]


def wait_event(directory: Path, role: str, wanted: str, process: subprocess.Popen) -> None:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if any(row["event"] == wanted for row in events(directory, role)):
            return
        assert process.poll() is None, process.communicate()
        time.sleep(0.005)
    raise AssertionError(f"{role} did not reach {wanted}")


@pytest.mark.parametrize(
    "stage,collision",
    [("before_accept", False), ("lost_ack", False), ("health", False), ("health", True)],
)
def test_real_service_process_restart(stage: str, collision: bool, tmp_path: Path) -> None:
    pipe = rf"\\.\pipe\nw-recovery-test-{uuid.uuid4().hex}"
    environment = os.environ | {"LOCALAPPDATA": str(tmp_path), "PYTHONIOENCODING": "utf-8"}
    service = Path(__file__).with_name("context_fault_service.py")
    processes: list[subprocess.Popen] = []

    def spawn(role: str, epoch: int) -> subprocess.Popen:
        process = subprocess.Popen(
            [
                sys.executable,
                str(service),
                "--pipe",
                pipe,
                "--directory",
                str(tmp_path),
                "--role",
                role,
                "--stage",
                stage,
                "--epoch",
                str(epoch),
            ],
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        processes.append(process)
        return process

    try:
        primary = spawn("primary", 100)
        replacement = spawn("replacement", 101 if collision else 0)
        wait_event(tmp_path, "primary", "listening", primary)
        wait_event(tmp_path, "replacement", "armed", replacement)
        scenario = (
            "restart-collision"
            if collision
            else "restart-health"
            if stage == "health"
            else "single"
        )
        probe = subprocess.Popen(
            [str(PROBE), pipe, scenario],
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        processes.append(probe)
        assert primary.wait(timeout=5) == 73
        (tmp_path / "replace.gate").touch()
        stdout, stderr = probe.communicate(timeout=6)
        assert probe.returncode == 0, (stdout, stderr)
        updates_before = [
            row for row in events(tmp_path, "primary") if row["event"] == "model_update"
        ]
        updates_after = [
            row for row in events(tmp_path, "replacement") if row["event"] == "model_update"
        ]
        assert len(updates_before) == (0 if stage == "before_accept" else 1)
        assert len(updates_after) == 1
        assert primary.pid != replacement.pid
        print(
            f"restart_stage={stage} collision={collision} "
            f"old_pid={primary.pid} new_pid={replacement.pid} "
            f"old_updates={len(updates_before)} new_updates={len(updates_after)} {stdout.strip()}"
        )
    finally:
        (tmp_path / "stop.gate").touch()
        for process in processes:
            if process.poll() is None:
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    # Only Popen objects created by this test; never persisted PIDs.
                    process.terminate()
                    process.wait(timeout=3)
            process.communicate()
        assert all(process.poll() is not None for process in processes)


class CleanupEngine(Engine):
    resets = 0

    def reset_private_context(self) -> None:
        self.resets += 1
        self.context_epoch = 0


class OrderedServer(DropServer):
    def __init__(self, engine: CleanupEngine) -> None:
        super().__init__(engine, "lost_ack")
        self.order: list[str] = []
        self.revisions: list[int] = []
        self.protected_text_seen = False

    def handle_message(self, message: dict) -> dict:
        kind = message["type"]
        if kind == "context_update":
            self.order.append("update")
            self.revisions.append(message["source_revision"])
        elif kind == "focus":
            self.order.append("focus")
            self.protected_text_seen |= "before" in message or "after" in message
        return super().handle_message(message)


@pytest.mark.parametrize("action", ["supersede", "secure", "barrier", "invalidate", "stop"])
def test_old_response_cannot_publish_across_revision_or_cleanup(
    action: str, tmp_path: Path
) -> None:
    engine = CleanupEngine()
    server = OrderedServer(engine)
    server.start()
    try:
        result = subprocess.run(
            [str(PROBE), server.pipe_name, action],
            env=os.environ | {"LOCALAPPDATA": str(tmp_path)},
            capture_output=True,
            text=True,
            timeout=5,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        assert result.returncode == 0, (result.stdout, result.stderr)
        assert server.receipts == 0
        assert not server.protected_text_seen
        if action == "supersede":
            assert server.revisions == [1, 2] and engine.calls == 2
        elif action == "barrier":
            assert server.order == ["update", "focus", "update"]
            assert server.revisions == [1, 3] and engine.calls == 2 and engine.resets == 1
        elif action == "secure":
            assert engine.calls == engine.resets == 1
            assert server._context_update_receipt is None
            assert server._context_bindings == {}
        else:
            assert engine.calls == 1
        print(
            f"action={action} updates={engine.calls} resets={engine.resets} "
            f"receipts={server.receipts} {result.stdout.strip()}"
        )
    finally:
        server.stop()
    assert not server._server_thread.is_alive()
