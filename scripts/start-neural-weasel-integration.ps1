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
    } catch {
        return $false
    }
    return (
        $Health.status -eq 'ok' -and
        $Health.model -eq 'Qwen/Qwen3.5-0.8B-Base' -and
        $Health.precision -eq 'int8' -and
        $Health.backend_kind -eq 'full_logits' -and
        -not [string]::IsNullOrWhiteSpace([string]$Health.tokenizer_fingerprint) -and
        $Health.tokenizer_fingerprint -eq $Health.index_tokenizer_fingerprint -and
        $Health.tokenizer_revision -eq $Health.index_revision -and
        $Health.index_model_id -eq $Health.model
    )
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
            'Neural backend did not become ready with the expected model/int8/full-logits ' +
            "identity within $BackendReadyTimeoutSeconds seconds."
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
