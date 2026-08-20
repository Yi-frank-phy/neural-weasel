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


def test_wisdom_startup_accepts_only_the_expected_backend_health_identity() -> None:
    script = _script()

    for marker in (
        "Qwen/Qwen3.5-0.8B-Base",
        "int8",
        "full_logits",
        "tokenizer_fingerprint",
        "index_tokenizer_fingerprint",
    ):
        assert marker in script
    assert "$Health.tokenizer_fingerprint -eq $Health.index_tokenizer_fingerprint" in script
