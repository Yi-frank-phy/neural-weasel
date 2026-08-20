[CmdletBinding()]
param(
    [ValidateSet('Qwen/Qwen3.5-0.8B-Base')]
    [string]$Model = 'Qwen/Qwen3.5-0.8B-Base',
    [ValidateSet('full')]
    [string]$Backend = 'full',
    [ValidateRange(10, 3600)]
    [int]$ReadyTimeoutSeconds = 1800,
    [switch]$DryRun
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$InstallRoot = Join-Path $env:LOCALAPPDATA 'NeuralWeasel\WisdomIntegration'
$LogRoot = Join-Path $InstallRoot 'Logs'

function Assert-LastExitCode {
    param([Parameter(Mandatory)][string]$Operation)
    if ($LASTEXITCODE -ne 0) {
        throw "$Operation failed with exit code $LASTEXITCODE."
    }
}

function Quote-ProcessArgument {
    param([Parameter(Mandatory)][string]$Value)
    return '"' + $Value.Replace('"', '\"') + '"'
}

if (-not [Environment]::Is64BitOperatingSystem) {
    throw 'Neural Weasel currently supports only 64-bit Windows.'
}
if (-not $env:LOCALAPPDATA -or -not $env:APPDATA) {
    throw 'LOCALAPPDATA and APPDATA are required.'
}

# The public one-click path deliberately uses the registered official Weasel
# TSF and candidate UI. Neural code runs only in the out-of-process HTTP
# backend; this launcher must never register or activate another text service.
$RequiredSourceFiles = @(
    'install-wisdom-integration.ps1',
    'start-model-service.ps1',
    'start-neural-weasel-integration.ps1',
    'start-wisdom-service.vbs',
    'tools\uv.exe',
    'python-service\pyproject.toml',
    'python-service\uv.lock',
    'python-service\src\neural_weasel\http_server.py',
    'build-manifest.json'
)
foreach ($RelativePath in $RequiredSourceFiles) {
    if (-not (Test-Path -LiteralPath (Join-Path $PSScriptRoot $RelativePath))) {
        throw "The downloaded bundle is incomplete: $RelativePath"
    }
}

$Installer = Join-Path $PSScriptRoot 'install-wisdom-integration.ps1'
& $Installer -BuildDirectory $PSScriptRoot -DryRun
Assert-LastExitCode -Operation 'Wisdom integration dry-run'
if ($DryRun) {
    Write-Host (
        'Dry-run succeeded; no TSF registration, DLL loading, configuration, ' +
        'defaults, or processes were changed.'
    )
    exit 0
}

& $Installer -BuildDirectory $PSScriptRoot
Assert-LastExitCode -Operation 'Wisdom integration installation'

$InstalledLauncher = Join-Path $InstallRoot 'start-neural-weasel-integration.ps1'
if (-not (Test-Path -LiteralPath $InstalledLauncher -PathType Leaf)) {
    throw "Installed official-shell launcher is missing: $InstalledLauncher"
}

New-Item -ItemType Directory -Path $LogRoot -Force | Out-Null
$PowerShellExe = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"
$Arguments = @(
    '-NoLogo',
    '-NoProfile',
    '-NonInteractive',
    '-ExecutionPolicy',
    'Bypass',
    '-File',
    (Quote-ProcessArgument $InstalledLauncher),
    '-StartupDelaySeconds',
    '0',
    '-BackendReadyTimeoutSeconds',
    [string]$ReadyTimeoutSeconds
) -join ' '

Start-Process `
    -FilePath $PowerShellExe `
    -ArgumentList $Arguments `
    -WorkingDirectory $InstallRoot `
    -WindowStyle Hidden `
    -RedirectStandardOutput (Join-Path $LogRoot 'official-shell-launch.stdout.log') `
    -RedirectStandardError (Join-Path $LogRoot 'official-shell-launch.stderr.log') |
    Out-Null

Write-Host 'Neural backend startup was handed off to a hidden background process.'
Write-Host 'The registered official Weasel TSF and candidate UI remain the input-method shell.'
