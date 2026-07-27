[CmdletBinding()]
param(
    [string]$InstallRoot = (
        Join-Path $env:LOCALAPPDATA (
            'NeuralWeasel\Experimental\experimental-profile'
        )
    ),
    [switch]$RemoveModelCache,
    [switch]$DryRun
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$ExperimentalClsid = '{8AA66261-ED5F-46B0-895D-339B42C3AE1B}'
$ExperimentalProfileGuid = '{C9B3984E-A16C-4779-80E8-ACD988C57B0D}'
$ExperimentalRuntimeRoot = [IO.Path]::GetFullPath(
    (Join-Path $env:LOCALAPPDATA 'NeuralWeasel\Experimental')
)
$ExpectedInstallRoot = Join-Path $ExperimentalRuntimeRoot 'experimental-profile'
$InstallRoot = [IO.Path]::GetFullPath($InstallRoot)
if ($InstallRoot -ne $ExpectedInstallRoot) {
    throw 'Refusing to remove a directory outside the reserved experimental-profile boundary.'
}

$InstalledTool = Join-Path $InstallRoot 'NeuralWeaselProfileTool.exe'
$InstalledDll = Join-Path $InstallRoot 'NeuralWeaselExperimentalTSF.dll'
if ($DryRun) {
    if (Test-Path -LiteralPath $InstalledTool -PathType Leaf) {
        & $InstalledTool unregister `
            --clsid $ExperimentalClsid `
            --profile-guid $ExperimentalProfileGuid `
            --dll $InstalledDll `
            --dry-run
        if ($LASTEXITCODE -ne 0) {
            throw "Experimental unregister dry-run failed with exit code $LASTEXITCODE."
        }
    }
    Write-Host 'Dry-run succeeded; no process, profile, registry key, or file was changed.'
    exit 0
}

Get-Process -Name 'NeuralWeaselServer' -ErrorAction SilentlyContinue |
    Stop-Process -Force -ErrorAction Stop

if (Test-Path -LiteralPath $InstalledTool -PathType Leaf) {
    & $InstalledTool unregister `
        --clsid $ExperimentalClsid `
        --profile-guid $ExperimentalProfileGuid `
        --dll $InstalledDll
    if ($LASTEXITCODE -ne 0) {
        throw "Experimental profile unregistration failed with exit code $LASTEXITCODE."
    }
} elseif (Test-Path -LiteralPath $InstallRoot -PathType Container) {
    throw 'Refusing partial uninstall: the identity-locked profile tool is missing.'
}

if (Test-Path -LiteralPath $ExperimentalRuntimeRoot -PathType Container) {
    Remove-Item -LiteralPath $ExperimentalRuntimeRoot -Recurse -Force
}

if ($RemoveModelCache) {
    $ModelCache = Join-Path $env:LOCALAPPDATA 'NeuralWeasel\huggingface'
    if (Test-Path -LiteralPath $ModelCache -PathType Container) {
        Remove-Item -LiteralPath $ModelCache -Recurse -Force
    }
}

Write-Host 'Removed only the 神经小狼毫（实验） profile and isolated runtime data.'
if (-not $RemoveModelCache) {
    Write-Host 'Model weights were preserved. Use -RemoveModelCache to remove them explicitly.'
}
