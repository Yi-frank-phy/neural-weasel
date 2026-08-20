[CmdletBinding()]
param(
    [ValidateRange(0, 60)]
    [int]$StartupDelaySeconds = 5,
    [ValidateRange(10, 1800)]
    [int]$BackendReadyTimeoutSeconds = 1800,
    [string]$CompatibilityRoot
)

$ErrorActionPreference = 'Stop'

# Windows PowerShell 5.1 treats UTF-8 scripts without a BOM as the active ANSI
# code page. Construct the non-ASCII directory name from code points so the
# launcher behaves identically under Windows PowerShell 5.1 and PowerShell 7.
if ([string]::IsNullOrWhiteSpace($CompatibilityRoot)) {
    if ($env:NEURAL_WEASEL_COMPATIBILITY_ROOT) {
        $CompatibilityRoot = $env:NEURAL_WEASEL_COMPATIBILITY_ROOT
    } else {
        $InputMethodDirectory = -join [char[]]@(0x8F93, 0x5165, 0x6CD5)
        $CompatibilityRoot = Join-Path (
            Join-Path "$env:SystemDrive\" $InputMethodDirectory
        ) 'wisdom_weasel_installer\weasel-release'
    }
}
$CompatibilityRoot = [IO.Path]::GetFullPath($CompatibilityRoot)
$compatibilityShell = Join-Path $CompatibilityRoot 'WeaselServer.exe'
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
    $officialRoot = [IO.Path]::GetFullPath((Join-Path $env:ProgramFiles 'Rime'))
    $processPath = if ($process.ExecutablePath) {
        [IO.Path]::GetFullPath($process.ExecutablePath)
    } else {
        $null
    }
    if (-not $processPath -or -not $processPath.StartsWith(
        $officialRoot + [IO.Path]::DirectorySeparatorChar,
        [StringComparison]::OrdinalIgnoreCase
    )) {
        throw (
            'Refusing to stop an unexpected WeaselServer process: ' +
            "PID $($process.ProcessId), path '$processPath'"
        )
    }
    Stop-Process -Id $process.ProcessId -ErrorAction Stop
}

if (-not $desiredShellRunning) {
    Start-Process `
        -FilePath $compatibilityShell `
        -WorkingDirectory $CompatibilityRoot `
        -WindowStyle Hidden | Out-Null
}
