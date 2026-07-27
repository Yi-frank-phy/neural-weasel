[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$BuildDirectory
)

$ErrorActionPreference = 'Stop'

$ExperimentalClsid = '{8AA66261-ED5F-46B0-895D-339B42C3AE1B}'
$ExperimentalProfileGuid = '{C9B3984E-A16C-4779-80E8-ACD988C57B0D}'
$DisplayName = '神经小狼毫（实验）'
$InstallRoot = Join-Path $env:LOCALAPPDATA 'NeuralWeasel\experimental-profile'
$ProfileToolName = 'NeuralWeaselProfileTool.exe'
$TsfDllName = 'NeuralWeaselExperimentalTSF.dll'
$SourceTool = Join-Path $BuildDirectory $ProfileToolName
$SourceDll = Join-Path $BuildDirectory $TsfDllName

if (-not [Environment]::Is64BitOperatingSystem) {
    throw 'The v0.2 development profile supports only 64-bit Windows.'
}
if (-not (Test-Path -LiteralPath $SourceTool -PathType Leaf)) {
    throw "Missing profile tool: $SourceTool"
}
if (-not (Test-Path -LiteralPath $SourceDll -PathType Leaf)) {
    throw "Missing experimental TSF DLL: $SourceDll"
}

New-Item -ItemType Directory -Path $InstallRoot -Force | Out-Null
$InstalledTool = Join-Path $InstallRoot $ProfileToolName
$InstalledDll = Join-Path $InstallRoot $TsfDllName
Copy-Item -LiteralPath $SourceTool -Destination $InstalledTool -Force
Copy-Item -LiteralPath $SourceDll -Destination $InstalledDll -Force

& $InstalledTool install `
    --clsid $ExperimentalClsid `
    --profile-guid $ExperimentalProfileGuid `
    --display-name $DisplayName `
    --dll $InstalledDll `
    --no-default
if ($LASTEXITCODE -ne 0) {
    throw "Experimental profile registration failed with exit code $LASTEXITCODE."
}

Write-Host 'Installed 神经小狼毫（实验） without changing the default input method.'
Write-Host 'Switch to it manually from the Windows input-method list.'
