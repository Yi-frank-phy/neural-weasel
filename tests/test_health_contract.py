from __future__ import annotations

import json
from http.client import HTTPConnection

from neural_weasel.http_server import WisdomHttpServer


class FakeEngine:
    def diagnostics(self) -> dict[str, object]:
        return {
            "backend_kind": "full_logits",
            "model": "Qwen/Qwen3.5-4B-Base",
            "format": "gguf",
            "quantization": "Q8_0",
            "runtime": "llama.cpp",
            "backend": "CUDA",
            "gpu_layers": "all",
            "gpu_name": "NVIDIA GeForce RTX 4060 Laptop GPU",
            "gpu_uuid": "GPU-test",
            "gguf_sha256": "a" * 64,
            "vocab_fingerprint": "vocab-123",
            "index_model_id": "Qwen/Qwen3.5-4B-Base",
            "index_identity_kind": "gguf-v1",
            "index_gguf_sha256": "a" * 64,
            "index_vocab_fingerprint": "vocab-123",
            "index_pypinyin_version": "0.55.0",
            "index_schema_version": 2,
        }


def test_health_contract_exposes_verified_gguf_cuda_identity() -> None:
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
            "model": "Qwen/Qwen3.5-4B-Base",
            "format": "gguf",
            "quantization": "Q8_0",
            "runtime": "llama.cpp",
            "backend": "CUDA",
            "backend_kind": "full_logits",
            "gpu_layers": "all",
            "gpu_name": "NVIDIA GeForce RTX 4060 Laptop GPU",
            "gpu_uuid": "GPU-test",
            "gguf_sha256": "a" * 64,
            "vocab_fingerprint": "vocab-123",
            "index_model_id": "Qwen/Qwen3.5-4B-Base",
            "index_identity_kind": "gguf-v1",
            "index_gguf_sha256": "a" * 64,
            "index_vocab_fingerprint": "vocab-123",
            "index_pypinyin_version": "0.55.0",
            "index_schema_version": 2,
        }
        connection.close()
    finally:
        server.shutdown()
        server.server_close()
        if thread is not None:
            thread.join(timeout=2)
