from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _script() -> str:
    return (ROOT / "scripts/start-neural-weasel-integration.ps1").read_text(encoding="utf-8")


def test_wisdom_shell_root_is_configured_instead_of_machine_hardcoded() -> None:
    script = _script()

    assert "C:\\输入法\\wisdom_weasel_installer" not in script
    assert "$CompatibilityRoot" in script
    assert "NEURAL_WEASEL_WISDOM_ROOT" in script


def test_wisdom_startup_never_terminates_unmanaged_weasel_processes() -> None:
    script = _script()

    assert "Stop-Process" not in script
    assert "Get-CimInstance Win32_Process" not in script


def test_wisdom_startup_accepts_only_expected_gguf_cuda_backend_identity() -> None:
    script = _script()

    for marker in (
        "Qwen/Qwen3.5-4B-Base",
        "gguf",
        "Q8_0",
        "llama.cpp",
        "CUDA",
        "all",
        "gguf_sha256",
        "vocab_fingerprint",
        "index_vocab_fingerprint",
    ):
        assert marker in script
    assert "$Health.vocab_fingerprint -eq $Health.index_vocab_fingerprint" in script
    assert "$Health.gguf_sha256 -eq $Health.index_gguf_sha256" in script
