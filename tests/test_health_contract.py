from __future__ import annotations

import json
from http.client import HTTPConnection

from neural_weasel.http_server import WisdomHttpServer


class FakeEngine:
    def diagnostics(self) -> dict[str, object]:
        return {
            "backend_kind": "qwen",
            "model_id": "Qwen/Qwen3.5-0.8B-Base",
            "quantization": "int8",
            "tokenizer_fingerprint": "abc123",
        }


def test_health_contract_exposes_backend_identity_fields() -> None:
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
        assert payload["backend_kind"] == "qwen"
        assert payload["model_id"] == "Qwen/Qwen3.5-0.8B-Base"
        assert payload["quantization"] == "int8"
        assert payload["tokenizer_fingerprint"] == "abc123"
        connection.close()
    finally:
        server.shutdown()
        server.server_close()
        if thread is not None:
            thread.join(timeout=2)
