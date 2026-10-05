from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest


def test_suspended_fullscreen_state_schema(tmp_path: Path) -> None:
    """Verify that suspended-fullscreen state file conforms to the model service schema."""
    state_file = tmp_path / "model-service.json"
    dummy_gguf = r"C:\Users\zhaoy\AppData\Local\NeuralWeasel\gguf-poc\models\Qwen3.5-4B-Q4_K_M.gguf"
    state_payload = {
        "state": "suspended-fullscreen",
        "transport": "pipe",
        "model": "Qwen/Qwen3.5-4B-Base",
        "format": "gguf",
        "quantization": "Q4_K_M",
        "runtime": "llama.cpp",
        "compute_backend": "CUDA",
        "gpu_layers": "all",
        "index": "",
        "pid": None,
        "exit_code": 0,
        "gguf_path": dummy_gguf,
        "safety_profile": "crash-contained-4b-q4-gguf-cuda",
        "updated_utc": "2026-09-23T16:00:00.0000000Z",
    }
    state_file.write_text(json.dumps(state_payload, indent=2), encoding="utf-8")

    loaded = json.loads(state_file.read_text(encoding="utf-8"))
    assert loaded["state"] == "suspended-fullscreen"
    assert loaded["pid"] is None
    assert loaded["transport"] == "pipe"
    assert loaded["quantization"] == "Q4_K_M"


def test_game_detector_compiles_and_evaluates() -> None:
    """Verify Win32 game detector compiles and executes without runtime errors."""
    repo_root = Path(__file__).resolve().parent.parent
    script_path = repo_root / "scripts" / "start-model-service-hidden.ps1"
    assert script_path.exists(), f"Supervisor script missing at {script_path}"

    ps_code = (
        f"$c = Get-Content -LiteralPath '{script_path}' -Raw; "
        "$def = [regex]::Match($c, '(?s)Add-Type -TypeDefinition @\"(.*?)\"@').Groups[1].Value; "
        "Add-Type -TypeDefinition $def; "
        "$ev = [NeuralWeaselGameDetector]::Check(); "
        "Write-Output ('EVAL_OK:' + $ev.IsFullscreenGame.ToString() + ':' + $ev.Quns.ToString());"
    )
    test_cmd = ["powershell.exe", "-NoProfile", "-Command", ps_code]
    proc = subprocess.run(test_cmd, capture_output=True, text=True, check=False)
    assert proc.returncode == 0, f"PowerShell test failed: stderr={proc.stderr}"
    assert "EVAL_OK:" in proc.stdout, f"Evaluation output unexpected: {proc.stdout}"


def test_game_detector_releases_desktop_handles_after_each_check() -> None:
    script = Path(__file__).resolve().parent.parent / "scripts/start-model-service-hidden.ps1"
    command = (
        f"$c = Get-Content -LiteralPath '{script}' -Raw; "
        "$def = [regex]::Match($c, '(?s)Add-Type -TypeDefinition @\"(.*?)\"@').Groups[1].Value; "
        "Add-Type -TypeDefinition $def; "
        "for ($i=0; $i -lt 10; $i++) { $null = [NeuralWeaselGameDetector]::Check() }; "
        "[GC]::Collect(); [GC]::WaitForPendingFinalizers(); "
        "$before = [Diagnostics.Process]::GetCurrentProcess().HandleCount; "
        "for ($i=0; $i -lt 120; $i++) { $null = [NeuralWeaselGameDetector]::Check() }; "
        "[GC]::Collect(); [GC]::WaitForPendingFinalizers(); "
        "$after = [Diagnostics.Process]::GetCurrentProcess().HandleCount; "
        "Write-Output ($after - $before); if ($after - $before -gt 5) { exit 1 }"
    )
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, f"handle growth={result.stdout}, stderr={result.stderr}"


@pytest.mark.parametrize("live", [True, False])
def test_persisted_pid_never_authorizes_process_termination(live: bool) -> None:
    script = Path(__file__).resolve().parent.parent / "scripts/start-model-service-hidden.ps1"
    source = script.read_text(encoding="utf-8")
    function = source.split("function Stop-LingeringService {", 1)[1].split(
        "# Main Supervisor Execution", 1
    )[0]
    command = (
        "$ErrorActionPreference = 'Stop'; $StatePath = 'unused'; $script:kills = 0; "
        "function Test-Path { return $true }; "
        "function Get-Content { return '{\"pid\":42}' }; "
        "function Get-Process { "
        + ("return [pscustomobject]@{Id=42;ProcessName='python'}" if live else "return $null")
        + " }; function taskkill.exe { $script:kills++ }; "
        + "function Stop-LingeringService {"
        + function
        + "; $blocked = $false; try { Stop-LingeringService } catch { $blocked = $true }; "
        + f"if ($blocked -ne ${str(live).lower()} -or $script:kills -ne 0) {{ exit 1 }}; "
        + "Write-Output 'SAFE'"
    )
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "SAFE" in result.stdout
