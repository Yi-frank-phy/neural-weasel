[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [string]$BundleRoot
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$ExperimentalClsid = '{8AA66261-ED5F-46B0-895D-339B42C3AE1B}'
$ExperimentalProfileGuid = '{C9B3984E-A16C-4779-80E8-ACD988C57B0D}'

function Invoke-ExpectedFailure {
    param(
        [Parameter(Mandatory)][scriptblock]$Operation,
        [Parameter(Mandatory)][string]$Description
    )
    try {
        & $Operation
    } catch {
        Write-Host "Expected failure verified: $Description"
        return
    }
    throw "Expected failure did not occur: $Description"
}

$BundleRoot = (Resolve-Path -LiteralPath $BundleRoot).Path
$TestRoot = Join-Path $env:RUNNER_TEMP (
    "neural-weasel-install-safety-$([guid]::NewGuid().ToString('N'))"
)
$OriginalLocalAppData = $env:LOCALAPPDATA
try {
    New-Item -ItemType Directory -Path $TestRoot -Force | Out-Null
    $env:LOCALAPPDATA = Join-Path $TestRoot 'LocalAppData'

    & (Join-Path $BundleRoot 'install-dev-profile.ps1') -DryRun
    & (Join-Path $BundleRoot 'install-dev-profile.ps1') -DryRun
    & (Join-Path $BundleRoot 'uninstall-dev-profile.ps1') -DryRun
    & (Join-Path $BundleRoot 'uninstall-dev-profile.ps1') -DryRun

    $MissingDll = Join-Path $TestRoot 'missing-dll'
    Copy-Item -LiteralPath $BundleRoot -Destination $MissingDll -Recurse
    Remove-Item -LiteralPath (
        Join-Path $MissingDll 'NeuralWeaselExperimentalTSF.dll'
    ) -Force
    Invoke-ExpectedFailure -Description 'missing TSF DLL' -Operation {
        & (Join-Path $MissingDll 'install-dev-profile.ps1') `
            -BuildDirectory $MissingDll -DryRun
    }

    $Conflict = Join-Path $TestRoot 'identifier-conflict'
    Copy-Item -LiteralPath $BundleRoot -Destination $Conflict -Recurse
    $ManifestPath = Join-Path $Conflict 'build-manifest.json'
    $Manifest = Get-Content -LiteralPath $ManifestPath -Raw | ConvertFrom-Json
    $Manifest.experimental_clsid = '{00000000-0000-0000-0000-000000000000}'
    $Manifest | ConvertTo-Json -Depth 8 |
        Set-Content -LiteralPath $ManifestPath -Encoding utf8NoBOM
    Invoke-ExpectedFailure -Description 'identifier conflict' -Operation {
        & (Join-Path $Conflict 'install-dev-profile.ps1') `
            -BuildDirectory $Conflict -DryRun
    }

    $Tool = Join-Path $BundleRoot 'NeuralWeaselProfileTool.exe'
    & $Tool unregister `
        --clsid '{00000000-0000-0000-0000-000000000000}' `
        --profile-guid $ExperimentalProfileGuid `
        --dry-run
    if ($LASTEXITCODE -eq 0) {
        throw 'Profile tool accepted a non-experimental GUID.'
    }
    Write-Host 'Expected failure verified: non-experimental GUID'

    & $Tool unregister `
        --clsid $ExperimentalClsid `
        --profile-guid '{00000000-0000-0000-0000-000000000000}' `
        --dry-run
    if ($LASTEXITCODE -eq 0) {
        throw 'Profile tool accepted a non-experimental profile GUID.'
    }
} finally {
    $env:LOCALAPPDATA = $OriginalLocalAppData
    if (Test-Path -LiteralPath $TestRoot) {
        Remove-Item -LiteralPath $TestRoot -Recurse -Force
    }
}

Write-Host 'Disposable install-safety tests passed without global registration.'
