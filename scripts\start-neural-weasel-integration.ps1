[CmdletBinding()]
param(
    [ValidateRange(0, 60)]
    [int]$StartupDelaySeconds = 5,
    [ValidateRange(10, 1800)]
    [int]$BackendReadyTimeoutSeconds = 1800
)

$ErrorActionPreference = 'Stop'

$compatibilityRoot = 'C:\输入法\wisdom_weasel_installer\weasel-release'
$compatibilityShell = Join-Path $compatibilityRoot 'WeaselServer.exe'
$backendLauncher = Join-Path $PSScriptRoot 'start-wisdom-service.vbs'

foreach ($requiredFile in @($compatibilityShell, $backendLauncher)) {
    if (-not (Test-Path -LiteralPath $requiredFile -PathType Leaf)) {
        throw "Required Neural Weasel integration file is missing: $requiredFile"
    }
}

if ($StartupDelaySeconds -gt 0) {
    Start-Sleep -Seconds $StartupDelaySeconds
}

$backendReady = $false
try {
    $backendReady = (
        Invoke-RestMethod -Uri 'http://127.0.0.1:8000/health' -TimeoutSec 2
    ).status -eq 'ok'
} catch {
    $backendReady = $false
}
if (-not $backendReady) {
    & "$env:WINDIR\System32\wscript.exe" $backendLauncher
    $timer = [Diagnostics.Stopwatch]::StartNew()
    while ($timer.Elapsed.TotalSeconds -lt $BackendReadyTimeoutSeconds) {
        Start-Sleep -Milliseconds 500
        try {
            $backendReady = (
                Invoke-RestMethod -Uri 'http://127.0.0.1:8000/health' -TimeoutSec 2
            ).status -eq 'ok'
        } catch {
            $backendReady = $false
        }
        if ($backendReady) {
            break
        }
    }
    if (-not $backendReady) {
        throw "Neural backend did not become ready within $BackendReadyTimeoutSeconds seconds."
    }
}

$desiredPath = [IO.Path]::GetFullPath($compatibilityShell)
$desiredShellRunning = $false
foreach ($process in @(Get-CimInstance Win32_Process -Filter "Name='WeaselServer.exe'")) {
    if ($process.ExecutablePath -and
        [IO.Path]::GetFullPath($process.ExecutablePath) -eq $desiredPath) {
        $desiredShellRunning = $true
        continue
    }
    Stop-Process -Id $process.ProcessId -ErrorAction Stop
}

if (-not $desiredShellRunning) {
    & $compatibilityShell
}
