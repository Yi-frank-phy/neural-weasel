from __future__ import annotations

from neural_weasel.http_server import WisdomHttpServer


class FakeEngine:
    def __init__(self, diagnostics: dict[str, object]) -> None:
        self._diagnostics = diagnostics

    def diagnostics(self) -> dict[str, object]:
        return self._diagnostics


def test_health_contract_exposes_backend_identity_fields() -> None:
    engine = FakeEngine(
        {
            "backend_kind": "qwen",
            "model_id": "Qwen/Qwen3.5-0.8B-Base",
            "quantization": "int8",
            "tokenizer_fingerprint": "abc123",
        }
    )

    server = WisdomHttpServer(("127.0.0.1", 0), engine)
    diagnostics = server.engine.diagnostics()

    assert diagnostics["backend_kind"] == "qwen"
    assert diagnostics["model_id"] == "Qwen/Qwen3.5-0.8B-Base"
    assert diagnostics["quantization"] == "int8"
    assert diagnostics["tokenizer_fingerprint"] == "abc123"
    server.server_close()
