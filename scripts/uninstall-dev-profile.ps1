[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'

$ExperimentalClsid = '{8AA66261-ED5F-46B0-895D-339B42C3AE1B}'
$ExperimentalProfileGuid = '{C9B3984E-A16C-4779-80E8-ACD988C57B0D}'
$InstallRoot = Join-Path $env:LOCALAPPDATA 'NeuralWeasel\experimental-profile'
$InstalledTool = Join-Path $InstallRoot 'NeuralWeaselProfileTool.exe'

if (-not $InstallRoot.EndsWith('NeuralWeasel\experimental-profile')) {
    throw "Refusing to remove a directory outside the experimental-profile boundary."
}

if (Test-Path -LiteralPath $InstalledTool -PathType Leaf) {
    & $InstalledTool uninstall `
        --clsid $ExperimentalClsid `
        --profile-guid $ExperimentalProfileGuid
    if ($LASTEXITCODE -ne 0) {
        throw "Experimental profile unregistration failed with exit code $LASTEXITCODE."
    }
}

if (Test-Path -LiteralPath $InstallRoot -PathType Container) {
    Remove-Item -LiteralPath $InstallRoot -Recurse -Force
}

Write-Host 'Removed only the 神经小狼毫（实验） development profile.'
