from __future__ import annotations

import threading

import numpy as np

from neural_weasel.backends import BackendState, FullLogitsSnapshotBackend, RuntimeSnapshot
from neural_weasel.context import EditorContext
from neural_weasel.pipe_server import NamedPipeServer
from neural_weasel.realtime import SnapshotCoordinator

ROOT = Path(__file__).resolve().parents[1]
SENTINEL = "NW_SENTINEL_SECRET_6d1f48f1"


class NoopEngine:
    context_epoch = 0

    def request_context_update(self, before: str, after: str = "") -> int:
        self.context_epoch += 1
        return self.context_epoch


class BlockingRuntime:
    def __init__(self) -> None:
        self.started = threading.Event()
        self.release = threading.Event()
        self.invalidations = 0

    def full_logits(self, before: str, after: str) -> RuntimeSnapshot:
        self.started.set()
        if not self.release.wait(2.0):
            raise RuntimeError("test runtime forward was not released")
        return RuntimeSnapshot(
            payload=np.asarray([1.0, 2.0], dtype=np.float32),
            before_hash="private-before",
            after_hash="private-after",
            latency_ms=1.0,
            continuation_root=b"private-root",
        )

    def invalidate_private_state(self) -> None:
        self.invalidations += 1

    def diagnostics(self) -> dict[str, object]:
        return {"runtime": "blocking-test"}


class BlockingBackend:
    def __init__(self) -> None:
        self.started = threading.Event()
        self.release = threading.Event()
        self.invalidated = threading.Event()
        self.state: BackendState | None = None

    def load(self) -> None:
        return None

    def update_context(self, before: str, after: str = "") -> BackendState:
        self.started.set()
        if not self.release.wait(2.0):
            raise RuntimeError("test backend forward was not released")
        state = BackendState(
            epoch=1,
            backend_kind="blocking-test",
            before_hash="private-before",
            after_hash="private-after",
            created_monotonic=0.0,
            publication_latency_ms=1.0,
            payload=np.asarray([1.0], dtype=np.float32),
            continuation_root=b"private-root",
        )
        self.state = state
        return state

    def latest_state(self) -> BackendState | None:
        return self.state

    def score_allowed_tokens(
        self,
        state: BackendState,
        allowed_token_ids: list[int],
    ) -> np.ndarray:
        return np.asarray([1.0 for _ in allowed_token_ids], dtype=np.float32)

    def diagnostics(self) -> dict[str, object]:
        return {"runtime": "blocking-test"}

    def invalidate_private_state(self) -> None:
        self.state = None
        self.invalidated.set()


def test_context_metadata_has_no_text_or_content_fingerprint() -> None:
    context = EditorContext(
        before=SENTINEL,
        after="private research tail",
        app_id="editor.exe",
        partial=False,
        complete_region=True,
        secure=False,
    )
    metadata = context.metadata()
    representation = repr(metadata)

    assert SENTINEL not in representation
    assert "private research tail" not in representation
    for key in metadata:
        normalized = str(key).casefold()
        assert "sha" not in normalized
        assert "hash" not in normalized
        assert "digest" not in normalized
        assert "fingerprint" not in normalized


def test_unknown_context_oracle_operations_do_not_echo_secret() -> None:
    server = NamedPipeServer(NoopEngine(), pipe_name=r"\\.\pipe\unused-privacy-test")
    for operation in ("get_context", "dump_context", "list_contexts"):
        response = server.handle_message({"type": operation, "payload": SENTINEL})
        assert response["ok"] is False
        assert response["error"]["code"] == "unknown_message_type"
        assert SENTINEL not in repr(response)


def test_restored_tsf_path_does_not_use_wisdom_or_persistence_bridges() -> None:
    overlay = (ROOT / "scripts/prepare-weasel-overlay.ps1").read_text(encoding="utf-8")
    client = (ROOT / "native/tsf/context_capture_client.cc").read_text(encoding="utf-8")
    broker = (ROOT / "native/context/context_capture_broker.cc").read_text(encoding="utf-8")

    context_path = "\n".join((client, broker))
    for marker in ("Wisdom", "sqlite", "telemetry", "ofstream", "fopen("):
        assert marker.casefold() not in context_path.casefold()

    tsf_start = overlay.index("$TsfXmake")
    server_start = overlay.index("$ServerXmake")
    tsf_block = overlay[tsf_start:server_start]
    assert "wisdom" not in tsf_block.casefold()


def test_backend_does_not_publish_snapshot_crossing_private_invalidation() -> None:
    runtime = BlockingRuntime()
    backend = FullLogitsSnapshotBackend(runtime)
    errors: list[BaseException] = []

    def refresh() -> None:
        try:
            backend.update_context(SENTINEL, "")
        except BaseException as error:  # noqa: BLE001 - thread result is asserted below
            errors.append(error)

    worker = threading.Thread(target=refresh)
    worker.start()
    assert runtime.started.wait(1.0)

    backend.invalidate_private_state()
    runtime.release.set()
    worker.join(2.0)

    assert not worker.is_alive()
    assert len(errors) == 1
    assert "invalidated" in str(errors[0])
    assert backend.latest_state() is None
    assert runtime.invalidations == 1


def test_secure_reset_waits_out_inflight_forward_then_wipes_backend() -> None:
    backend = BlockingBackend()
    coordinator = SnapshotCoordinator(
        backend=backend,
        engine=object(),  # type: ignore[arg-type]
        candidate_query=lambda *args, **kwargs: [],
    )
    coordinator.request_context_update(SENTINEL)
    assert backend.started.wait(1.0)

    reset = threading.Thread(target=coordinator.invalidate_private_state)
    reset.start()

    # The invalidate call must be gated behind the in-flight forward. Without
    # that ordering the old worker could publish its private root after reset.
    assert not backend.invalidated.wait(0.05)
    backend.release.set()
    reset.join(2.0)
    assert not reset.is_alive()
    assert backend.invalidated.wait(1.0)

    # The worker observes its request epoch was invalidated and cannot republish
    # either the coordinator state or the backend's private root after reset.
    worker = coordinator._worker
    if worker is not None:
        worker.join(2.0)
    assert coordinator.context_epoch == 0
    assert coordinator.latest_state is None
    assert backend.latest_state() is None
