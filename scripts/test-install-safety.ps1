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

function Invoke-WindowsPowerShellInstallDryRun {
    param([Parameter(Mandatory)][string]$Root)
    $PowerShell51 = Join-Path $env:WINDIR `
        'System32\WindowsPowerShell\v1.0\powershell.exe'
    if (-not (Test-Path -LiteralPath $PowerShell51 -PathType Leaf)) {
        throw "Windows PowerShell 5.1 was not found: $PowerShell51"
    }
    $Installer = Join-Path $Root 'install-dev-profile.ps1'
    & $PowerShell51 `
        -NoLogo `
        -NoProfile `
        -NonInteractive `
        -ExecutionPolicy Bypass `
        -File $Installer `
        -BuildDirectory $Root `
        -DryRun
    if ($LASTEXITCODE -ne 0) {
        throw "Windows PowerShell 5.1 install dry-run failed with exit code $LASTEXITCODE."
    }
}

$BundleRoot = (Resolve-Path -LiteralPath $BundleRoot).Path
$TestRoot = Join-Path $env:RUNNER_TEMP (
    "neural-weasel-install-safety-$([guid]::NewGuid().ToString('N'))"
)
$OriginalLocalAppData = $env:LOCALAPPDATA
try {
    New-Item -ItemType Directory -Path $TestRoot -Force | Out-Null
    $env:LOCALAPPDATA = Join-Path $TestRoot 'LocalAppData'

    Invoke-WindowsPowerShellInstallDryRun -Root $BundleRoot
    Invoke-WindowsPowerShellInstallDryRun -Root $BundleRoot
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
    Write-Host 'Expected failure verified: non-experimental profile GUID'
} finally {
    $env:LOCALAPPDATA = $OriginalLocalAppData
    if (Test-Path -LiteralPath $TestRoot) {
        Remove-Item -LiteralPath $TestRoot -Recurse -Force
    }
}

# The final profile-tool invocation is intentionally expected to return
# nonzero. Clear that native exit code only after every assertion and cleanup
# above has completed; thrown failures still terminate the script normally.
$global:LASTEXITCODE = 0
Write-Host 'Disposable install-safety tests passed without global registration.'
