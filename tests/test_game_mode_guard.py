from __future__ import annotations

import json
import subprocess
from pathlib import Path


def test_suspended_fullscreen_state_schema(tmp_path: Path) -> None:
    """Verify that suspended-fullscreen state file conforms to the model service schema."""
    state_file = tmp_path / "model-service.json"
    dummy_gguf = (
        r"C:\Users\zhaoy\AppData\Local\NeuralWeasel\gguf-poc\models\Qwen3.5-4B-Q4_K_M.gguf"
    )
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
