[CmdletBinding()]
param(
    [ValidateRange(0, 60)]
    [int]$StartupDelaySeconds = 5,
    [ValidateRange(10, 1800)]
    [int]$BackendReadyTimeoutSeconds = 1800,
    [string]$CompatibilityRoot = $env:NEURAL_WEASEL_WISDOM_ROOT
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

if (-not $CompatibilityRoot) {
    throw (
        'Wisdom Weasel root is not configured. Pass -CompatibilityRoot or set ' +
        'NEURAL_WEASEL_WISDOM_ROOT to the directory containing WeaselServer.exe.'
    )
}
$compatibilityRoot = [IO.Path]::GetFullPath($CompatibilityRoot)
$compatibilityShell = Join-Path $compatibilityRoot 'WeaselServer.exe'
$backendLauncher = Join-Path $PSScriptRoot 'start-wisdom-service.vbs'

foreach ($requiredFile in @($compatibilityShell, $backendLauncher)) {
    if (-not (Test-Path -LiteralPath $requiredFile -PathType Leaf)) {
        throw "Required Neural Weasel integration file is missing: $requiredFile"
    }
}

function Test-ExpectedBackendHealth {
    try {
        $Health = Invoke-RestMethod -Uri 'http://127.0.0.1:8000/health' -TimeoutSec 2
        return (
            $Health.status -eq 'ok' -and
            $Health.model -eq 'Qwen/Qwen3.5-4B-Base' -and
            $Health.format -eq 'gguf' -and
            $Health.quantization -eq 'Q8_0' -and
            $Health.runtime -eq 'llama.cpp' -and
            $Health.backend -eq 'CUDA' -and
            $Health.backend_kind -eq 'full_logits' -and
            $Health.gpu_layers -eq 'all' -and
            -not [string]::IsNullOrWhiteSpace([string]$Health.gguf_sha256) -and
            -not [string]::IsNullOrWhiteSpace([string]$Health.vocab_fingerprint) -and
            $Health.gguf_sha256 -eq $Health.index_gguf_sha256 -and
            $Health.vocab_fingerprint -eq $Health.index_vocab_fingerprint -and
            $Health.index_identity_kind -eq 'gguf-v1' -and
            $Health.index_model_id -eq $Health.model
        )
    } catch {
        return $false
    }
}

if ($StartupDelaySeconds -gt 0) {
    Start-Sleep -Seconds $StartupDelaySeconds
}

$backendReady = Test-ExpectedBackendHealth
if (-not $backendReady) {
    & "$env:WINDIR\System32\wscript.exe" $backendLauncher
    $timer = [Diagnostics.Stopwatch]::StartNew()
    while ($timer.Elapsed.TotalSeconds -lt $BackendReadyTimeoutSeconds) {
        Start-Sleep -Milliseconds 500
        $backendReady = Test-ExpectedBackendHealth
        if ($backendReady) {
            break
        }
    }
    if (-not $backendReady) {
        throw (
            'Neural backend did not become ready with the expected 4B/Q8_0/GGUF/' +
            "llama.cpp/CUDA/all-layers identity within $BackendReadyTimeoutSeconds seconds."
        )
    }
}

$desiredPath = [IO.Path]::GetFullPath($compatibilityShell)
$desiredShellRunning = $false
foreach ($process in @(Get-Process -Name 'WeaselServer' -ErrorAction SilentlyContinue)) {
    try {
        if ($process.Path -and [IO.Path]::GetFullPath($process.Path) -eq $desiredPath) {
            $desiredShellRunning = $true
            break
        }
    } catch {
        # Access to another process path can be denied. Never terminate or mutate it.
    }
}

if (-not $desiredShellRunning) {
    & $compatibilityShell
}
