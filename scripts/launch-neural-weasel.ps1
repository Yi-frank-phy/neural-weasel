[CmdletBinding()]
param(
    [ValidateRange(10, 3600)]
    [int]$ReadyTimeoutSeconds = 1800,
    [switch]$NoActivate,
    [switch]$DryRun
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$Model = 'Qwen/Qwen3.5-4B-Base'
$ModelFormat = 'gguf'
$Quantization = 'Q8_0'
$Runtime = 'llama.cpp'
$ComputeBackend = 'CUDA'
$ExperimentalClsid = '{8AA66261-ED5F-46B0-895D-339B42C3AE1B}'
$ExperimentalProfileGuid = '{C9B3984E-A16C-4779-80E8-ACD988C57B0D}'
$InstallRoot = Join-Path $env:LOCALAPPDATA (
    'NeuralWeasel\Experimental\experimental-profile'
)
$RuntimeRoot = Join-Path $env:LOCALAPPDATA 'NeuralWeasel\Experimental'
$StatePath = Join-Path $RuntimeRoot 'model-service.json'
$LogRoot = Join-Path $RuntimeRoot 'logs'

function Assert-LastExitCode {
    param([Parameter(Mandatory)][string]$Operation)
    if ($LASTEXITCODE -ne 0) {
        throw "$Operation failed with exit code $LASTEXITCODE."
    }
}

function Get-ModelPipePath {
    $Identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    if (-not $Identity.User) {
        throw 'The current Windows user SID is unavailable.'
    }
    $SanitizedSid = $Identity.User.Value -replace '[^A-Za-z0-9-]', '-'
    return "\\.\pipe\NeuralWeasel-v1-$SanitizedSid"
}

function Test-ModelPipe {
    param([Parameter(Mandatory)][string]$PipePath)
    try {
        return $PipePath -in [IO.Directory]::GetFiles('\\.\pipe\')
    } catch {
        return $false
    }
}

function Get-LiveModelServiceProcess {
    if (-not (Test-Path -LiteralPath $StatePath -PathType Leaf)) {
        return $null
    }
    try {
        $State = Get-Content -LiteralPath $StatePath -Raw | ConvertFrom-Json
        if (-not $State.pid) {
            return $null
        }
        $Process = Get-Process -Id ([int]$State.pid) -ErrorAction SilentlyContinue
        if (-not $Process) {
            return $null
        }
        if (
            $State.transport -ne 'pipe' -or
            $State.model -ne $Model -or
            $State.safety_profile -ne 'crash-contained-4b-q8-gguf-cuda'
        ) {
            throw (
                'A live incompatible Neural Weasel model service owns the shared state. ' +
                'Stop that legacy service before launching the experimental pipe service.'
            )
        }
        $UpdatedUtc = [DateTime]::Parse([string]$State.updated_utc).ToUniversalTime()
        if ($Process.StartTime.ToUniversalTime() -gt $UpdatedUtc.AddSeconds(2)) {
            return $null
        }
        return $Process
    } catch {
        return $null
    }
}

function Wait-ModelPipe {
    param(
        [Parameter(Mandatory)][string]$PipePath,
        [Parameter(Mandatory)][int]$TimeoutSeconds,
        [Diagnostics.Process]$ExpectedProcess
    )
    $Timer = [Diagnostics.Stopwatch]::StartNew()
    while ($Timer.Elapsed.TotalSeconds -lt $TimeoutSeconds) {
        if (Test-ModelPipe -PipePath $PipePath) {
            return
        }
        if ($ExpectedProcess -and $ExpectedProcess.HasExited) {
            throw (
                'The model service exited before its named pipe became ready. ' +
                "See $LogRoot for details."
            )
        }
        if (Test-Path -LiteralPath $StatePath -PathType Leaf) {
            try {
                $State = Get-Content -LiteralPath $StatePath -Raw | ConvertFrom-Json
                if ($State.state -eq 'failed') {
                    throw (
                        "The model service reported state '$($State.state)'. " +
                        "See $LogRoot for details."
                    )
                }
            } catch [System.Management.Automation.RuntimeException] {
                throw
            } catch {
                # The state file can be between atomic writes; retry briefly.
            }
        }
        Start-Sleep -Milliseconds 250
    }
    throw (
        "The model service did not become ready within $TimeoutSeconds seconds. " +
        "See $LogRoot for details."
    )
}

function Quote-ProcessArgument {
    param([Parameter(Mandatory)][string]$Value)
    return '"' + $Value.Replace('"', '\"') + '"'
}

if (-not [Environment]::Is64BitOperatingSystem) {
    throw '神经小狼毫 currently supports only 64-bit Windows.'
}
if (-not $env:LOCALAPPDATA) {
    throw 'LOCALAPPDATA is required.'
}

$RequiredSourceFiles = @(
    'install-dev-profile.ps1',
    'start-model-service.ps1',
    'NeuralWeaselServer.exe',
    'NeuralWeaselSessionActivator.exe',
    'tools\uv.exe',
    'build-manifest.json'
)
foreach ($RelativePath in $RequiredSourceFiles) {
    if (-not (Test-Path -LiteralPath (Join-Path $PSScriptRoot $RelativePath))) {
        throw "The downloaded bundle is incomplete: $RelativePath"
    }
}

$Installer = Join-Path $PSScriptRoot 'install-dev-profile.ps1'
if ($DryRun) {
    & $Installer -BuildDirectory $PSScriptRoot -InstallRoot $InstallRoot -DryRun
    Assert-LastExitCode -Operation 'One-click install dry-run'
    $DryRunActivator = Join-Path $PSScriptRoot 'NeuralWeaselSessionActivator.exe'
    & $DryRunActivator activate `
        --clsid $ExperimentalClsid `
        --profile-guid $ExperimentalProfileGuid `
        --dry-run
    Assert-LastExitCode -Operation 'Current-session activation dry-run'
    Write-Host 'Dry-run succeeded; no files, profiles, defaults, or processes were changed.'
    exit 0
}

& $Installer -BuildDirectory $PSScriptRoot -InstallRoot $InstallRoot
Assert-LastExitCode -Operation 'One-click installation'

$ServiceScript = Join-Path $InstallRoot 'start-model-service.ps1'
$Server = Join-Path $InstallRoot 'NeuralWeaselServer.exe'
$Activator = Join-Path $InstallRoot 'NeuralWeaselSessionActivator.exe'
foreach ($Path in @($ServiceScript, $Server, $Activator)) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "Installed runtime file is missing: $Path"
    }
}

New-Item -ItemType Directory -Path $LogRoot -Force | Out-Null
$PipePath = Get-ModelPipePath
$ServiceProcess = Get-LiveModelServiceProcess

if (-not (Test-ModelPipe -PipePath $PipePath)) {
    if (-not $ServiceProcess) {
        $PowerShellExe = (Get-Process -Id $PID).Path
        $StdOut = Join-Path $LogRoot 'model-service.stdout.log'
        $StdErr = Join-Path $LogRoot 'model-service.stderr.log'
        $Arguments = @(
            '-NoLogo',
            '-NoProfile',
            '-ExecutionPolicy',
            'Bypass',
            '-File',
            (Quote-ProcessArgument $ServiceScript)
        ) -join ' '
        Write-Host (
            "Starting $Model $Quantization $ModelFormat through $Runtime $ComputeBackend. " +
            'The first launch may download about 4.5 GB and build the pinyin index.'
        )
        $ServiceProcess = Start-Process `
            -FilePath $PowerShellExe `
            -ArgumentList $Arguments `
            -WorkingDirectory $InstallRoot `
            -WindowStyle Minimized `
            -RedirectStandardOutput $StdOut `
            -RedirectStandardError $StdErr `
            -PassThru
    } else {
        Write-Host 'A model-service process already exists; waiting for it to become ready.'
    }
    Wait-ModelPipe `
        -PipePath $PipePath `
        -TimeoutSeconds $ReadyTimeoutSeconds `
        -ExpectedProcess $ServiceProcess
}

if (-not (Get-Process -Name 'NeuralWeaselServer' -ErrorAction SilentlyContinue)) {
    Start-Process -FilePath $Server -WorkingDirectory $InstallRoot | Out-Null
}

if (-not $NoActivate) {
    & $Activator activate `
        --clsid $ExperimentalClsid `
        --profile-guid $ExperimentalProfileGuid
    Assert-LastExitCode -Operation 'Current-session 神经小狼毫 activation'
}

Write-Host '神经小狼毫（安全版） is running and activated for the current Windows desktop session.'
Write-Host (
    "$Model $Quantization $ModelFormat is required to run with full model-layer $ComputeBackend offload."
)
Write-Host 'Neural code runs outside application processes; the Windows default input method was not changed.'
