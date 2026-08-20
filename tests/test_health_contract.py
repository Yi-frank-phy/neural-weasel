from __future__ import annotations

import json
from http.client import HTTPConnection

from neural_weasel.http_server import WisdomHttpServer


class FakeEngine:
    def diagnostics(self) -> dict[str, object]:
        return {
            "backend_kind": "full_logits",
            "model": "Qwen/Qwen3.5-0.8B-Base",
            "precision": "int8",
            "tokenizer_revision": "rev-123",
            "tokenizer_fingerprint": "abc123",
            "index_model_id": "Qwen/Qwen3.5-0.8B-Base",
            "index_revision": "rev-123",
            "index_tokenizer_fingerprint": "abc123",
            "index_pypinyin_version": "0.55.0",
            "index_schema_version": 2,
        }


def test_health_contract_exposes_verified_runtime_and_index_identity() -> None:
    server = WisdomHttpServer(("127.0.0.1", 0), FakeEngine())
    thread = None
    try:
        import threading

        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        connection = HTTPConnection("127.0.0.1", server.server_port)
        connection.request("GET", "/health")
        response = connection.getresponse()
        payload = json.loads(response.read().decode("utf-8"))

        assert response.status == 200
        assert payload == {
            "status": "ok",
            "model": "Qwen/Qwen3.5-0.8B-Base",
            "precision": "int8",
            "backend_kind": "full_logits",
            "tokenizer_revision": "rev-123",
            "tokenizer_fingerprint": "abc123",
            "index_model_id": "Qwen/Qwen3.5-0.8B-Base",
            "index_revision": "rev-123",
            "index_tokenizer_fingerprint": "abc123",
            "index_pypinyin_version": "0.55.0",
            "index_schema_version": 2,
        }
        connection.close()
    finally:
        server.shutdown()
        server.server_close()
        if thread is not None:
            thread.join(timeout=2)
