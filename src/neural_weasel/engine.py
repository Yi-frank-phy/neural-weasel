from __future__ import annotations

import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, replace

from .candidate import Candidate
from .index import PinyinIndex
from .model import LogitsSnapshot, QwenBaseBackend
from .ranker import rank_candidates


@dataclass(frozen=True, slots=True)
class ContextRequest:
    before: str
    after: str
    requested_epoch: int


class NeuralPinyinEngine:
    """Owns immutable logits snapshots; queries never invoke model.forward()."""

    def __init__(self, backend: QwenBaseBackend, index: PinyinIndex) -> None:
        expected_hash = index.metadata.get("tokenizer_hash")
        from importlib.metadata import version

        from .index import resolved_tokenizer_revision, tokenizer_fingerprint

        actual_hash = tokenizer_fingerprint(backend.tokenizer)
        if expected_hash != actual_hash:
            raise RuntimeError("tokenizer/index mismatch; rebuild the pinyin index")
        if index.metadata.get("model_id") != backend.model_id:
            raise RuntimeError("model/index mismatch; rebuild the pinyin index")
        actual_revision = resolved_tokenizer_revision(backend.tokenizer)
        if index.metadata.get("revision") != actual_revision:
            raise RuntimeError("model revision/index mismatch; rebuild the pinyin index")
        if index.metadata.get("pypinyin_version") != version("pypinyin"):
            raise RuntimeError("pypinyin/index mismatch; rebuild the pinyin index")
        self.backend = backend
        self.index = index
        self._snapshot: LogitsSnapshot | None = None
        self._snapshots: OrderedDict[int, LogitsSnapshot] = OrderedDict()
        self._snapshot_lock = threading.Lock()
        self._request_lock = threading.Lock()
        self._requested_context_epoch = 0
        self._pending_context: tuple[int, str, str] | None = None
        self._context_worker: threading.Thread | None = None

    def update_context(self, before: str, after: str = "") -> LogitsSnapshot:
        with self._request_lock:
            self._requested_context_epoch += 1
            requested_epoch = self._requested_context_epoch
        snapshot = replace(
            self.backend.create_snapshot(before, after),
            epoch=requested_epoch,
        )
        with self._request_lock:
            if requested_epoch == self._requested_context_epoch:
                self._publish_snapshot(snapshot)
        return snapshot

    def request_context_update(self, before: str, after: str = "") -> int:
        """Queue the newest context without blocking a pipe/client thread.

        A running forward is not forcibly interrupted, but its result is never
        published if a newer context request has arrived.
        """
        with self._request_lock:
            self._requested_context_epoch += 1
            requested_epoch = self._requested_context_epoch
            self._pending_context = (requested_epoch, before, after)
            if self._context_worker is None or not self._context_worker.is_alive():
                self._context_worker = threading.Thread(
                    target=self._context_worker_loop,
                    name="neural-weasel-context",
                    daemon=True,
                )
                self._context_worker.start()
            return requested_epoch

    def _context_worker_loop(self) -> None:
        while True:
            with self._request_lock:
                pending = self._pending_context
                self._pending_context = None
                if pending is None:
                    # Clear the ownership marker while holding the same lock
                    # used by request_context_update(). Otherwise a request can
                    # arrive after this empty read but before the thread exits,
                    # observe an apparently live worker, and remain stranded.
                    self._context_worker = None
                    return
            requested_epoch, before, after = pending
            snapshot = replace(
                self.backend.create_snapshot(before, after),
                epoch=requested_epoch,
            )
            with self._request_lock:
                still_latest = requested_epoch == self._requested_context_epoch
                if still_latest:
                    # Publish while holding the request lock so a secure reset
                    # cannot invalidate the epoch and then lose a race to this
                    # older snapshot publication.
                    self._publish_snapshot(snapshot)

    def _publish_snapshot(self, snapshot: LogitsSnapshot) -> None:
        with self._snapshot_lock:
            self._snapshot = snapshot
            self._snapshots[snapshot.epoch] = snapshot
            while len(self._snapshots) > 4:
                self._snapshots.popitem(last=False)

    def wait_for_epoch(self, epoch: int, timeout_seconds: float = 5.0) -> bool:
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            if self.context_epoch >= epoch:
                return True
            time.sleep(0.005)
        return self.context_epoch >= epoch

    def query(
        self,
        raw_pinyin: str,
        limit: int = 5,
        context_epoch: int | None = None,
    ) -> list[Candidate]:
        with self._snapshot_lock:
            snapshot = (
                self._snapshots.get(context_epoch) if context_epoch is not None else self._snapshot
            )
        if snapshot is None:
            return []
        return rank_candidates(
            index=self.index,
            raw_pinyin=raw_pinyin,
            logits=snapshot.logits,
            context_epoch=snapshot.epoch,
            limit=limit,
            after_text=snapshot.after_text,
        )

    def has_snapshot(self, epoch: int) -> bool:
        with self._snapshot_lock:
            return epoch in self._snapshots

    def reset_private_context(self) -> None:
        """Fail closed when focus enters a secure or protected field.

        Incrementing the requested epoch invalidates any forward already in
        flight. Invalidating the backend's cache nonce prevents that forward
        from retaining private model state after it completes. Clearing pending
        work and every published snapshot ensures no later query can address
        context captured before the secure transition.
        """

        with self._request_lock:
            self._requested_context_epoch += 1
            self._pending_context = None
            self.backend.invalidate_context_cache()
        with self._snapshot_lock:
            self._snapshot = None
            self._snapshots.clear()

    def clear_history(self) -> None:
        """Clear committed-text fallback state.

        v0.1 does not persist commit history yet. Keeping the explicit method
        makes secure-focus cleanup mandatory when that fallback is introduced.
        """

    @property
    def context_epoch(self) -> int:
        with self._snapshot_lock:
            return self._snapshot.epoch if self._snapshot else 0
