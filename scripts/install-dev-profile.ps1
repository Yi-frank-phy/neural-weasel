[CmdletBinding()]
param(
    [string]$BuildDirectory = $PSScriptRoot,
    [string]$InstallRoot = (
        Join-Path $env:LOCALAPPDATA (
            'NeuralWeasel\Experimental\experimental-profile'
        )
    ),
    [switch]$DryRun
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$ExperimentalClsid = '{8AA66261-ED5F-46B0-895D-339B42C3AE1B}'
$ExperimentalProfileGuid = '{C9B3984E-A16C-4779-80E8-ACD988C57B0D}'
$ExpectedWeaselRevision = '9cc96e20dc71b80876b12f689bb5863c76c2a7ed'
$ProfileToolName = 'NeuralWeaselProfileTool.exe'
$TsfDllName = 'NeuralWeaselExperimentalTSF.dll'
$ServerName = 'NeuralWeaselServer.exe'

function Assert-LastExitCode {
    param([Parameter(Mandatory)][string]$Operation)
    if ($LASTEXITCODE -ne 0) {
        throw "$Operation failed with exit code $LASTEXITCODE."
    }
}

if (-not [Environment]::Is64BitOperatingSystem) {
    throw 'The experimental profile supports only 64-bit Windows.'
}
if (-not $env:LOCALAPPDATA) {
    throw 'LOCALAPPDATA is required.'
}

$BuildDirectory = (Resolve-Path -LiteralPath $BuildDirectory).Path
$ExpectedInstallRoot = [IO.Path]::GetFullPath(
    (Join-Path $env:LOCALAPPDATA (
        'NeuralWeasel\Experimental\experimental-profile'
    ))
)
$InstallRoot = [IO.Path]::GetFullPath($InstallRoot)
if ($InstallRoot -ne $ExpectedInstallRoot) {
    throw "Refusing install directory outside the reserved experimental boundary."
}

$ManifestPath = Join-Path $BuildDirectory 'build-manifest.json'
if (-not (Test-Path -LiteralPath $ManifestPath -PathType Leaf)) {
    throw "Missing build manifest: $ManifestPath"
}
$Manifest = Get-Content -LiteralPath $ManifestPath -Raw | ConvertFrom-Json
if (
    $Manifest.experimental_clsid -ne $ExperimentalClsid -or
    $Manifest.experimental_profile_guid -ne $ExperimentalProfileGuid -or
    $Manifest.upstream_weasel_revision -ne $ExpectedWeaselRevision -or
    $Manifest.architecture -ne 'x64'
) {
    throw 'Build manifest does not describe the reserved experimental bundle.'
}

$Required = @(
    $TsfDllName,
    $ProfileToolName,
    $ServerName,
    'NeuralWeaselRimeModule.lib',
    'build-manifest.json',
    'install-dev-profile.ps1',
    'uninstall-dev-profile.ps1',
    'diagnose.ps1',
    'start-model-service.ps1',
    'README-INSTALL-TEST.md',
    'data\neural_weasel.schema.yaml',
    'rime-user\default.custom.yaml',
    'python-service\pyproject.toml',
    'python-service\uv.lock'
)
foreach ($RelativePath in $Required) {
    $Path = Join-Path $BuildDirectory $RelativePath
    if (-not (Test-Path -LiteralPath $Path)) {
        throw "Missing required bundle artifact: $RelativePath"
    }
    if ($RelativePath -ne 'build-manifest.json') {
        $ManifestName = $RelativePath.Replace('\', '/')
        if ($ManifestName -notin $Manifest.artifacts.PSObject.Properties.Name) {
            throw "Required bundle artifact is not hashed: $RelativePath"
        }
    }
}

foreach ($Property in $Manifest.artifacts.PSObject.Properties) {
    $RelativePath = $Property.Name.Replace('/', [IO.Path]::DirectorySeparatorChar)
    if (
        [IO.Path]::IsPathRooted($RelativePath) -or
        '..' -in $RelativePath.Split(
            [IO.Path]::DirectorySeparatorChar,
            [StringSplitOptions]::RemoveEmptyEntries
        )
    ) {
        throw "Manifest artifact escapes the bundle: $($Property.Name)"
    }
    $Path = [IO.Path]::GetFullPath((Join-Path $BuildDirectory $RelativePath))
    if (-not $Path.StartsWith(
        $BuildDirectory + [IO.Path]::DirectorySeparatorChar,
        [StringComparison]::OrdinalIgnoreCase
    )) {
        throw "Manifest artifact escapes the bundle: $($Property.Name)"
    }
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "Manifest artifact is missing: $($Property.Name)"
    }
    $ActualHash = (
        Get-FileHash -LiteralPath $Path -Algorithm SHA256
    ).Hash.ToLowerInvariant()
    if ($ActualHash -ne [string]$Property.Value) {
        throw "Artifact hash mismatch: $($Property.Name)"
    }
}

$SourceTool = Join-Path $BuildDirectory $ProfileToolName
$SourceDll = Join-Path $BuildDirectory $TsfDllName
& $SourceTool verify `
    --clsid $ExperimentalClsid `
    --profile-guid $ExperimentalProfileGuid `
    --dll $SourceDll
Assert-LastExitCode -Operation 'Experimental identity verification'

if ($DryRun) {
    Write-Host 'Dry-run succeeded; no files, registry keys, profiles, or defaults were changed.'
    exit 0
}

$ExistingManifestPath = Join-Path $InstallRoot 'build-manifest.json'
if (Test-Path -LiteralPath $ExistingManifestPath -PathType Leaf) {
    $ExistingMatches = (
        (Get-FileHash -LiteralPath $ExistingManifestPath -Algorithm SHA256).Hash -eq
        (Get-FileHash -LiteralPath $ManifestPath -Algorithm SHA256).Hash
    )
    if ($ExistingMatches) {
        foreach ($Property in $Manifest.artifacts.PSObject.Properties) {
            $RelativePath = $Property.Name.Replace(
                '/',
                [IO.Path]::DirectorySeparatorChar
            )
            $ExistingPath = Join-Path $InstallRoot $RelativePath
            if (-not (Test-Path -LiteralPath $ExistingPath -PathType Leaf)) {
                $ExistingMatches = $false
                break
            }
            $ExistingHash = (
                Get-FileHash -LiteralPath $ExistingPath -Algorithm SHA256
            ).Hash.ToLowerInvariant()
            if ($ExistingHash -ne [string]$Property.Value) {
                $ExistingMatches = $false
                break
            }
        }
    }
    if ($ExistingMatches) {
        & (Join-Path $InstallRoot $ProfileToolName) register `
            --clsid $ExperimentalClsid `
            --profile-guid $ExperimentalProfileGuid `
            --dll (Join-Path $InstallRoot $TsfDllName)
        Assert-LastExitCode -Operation 'Idempotent experimental profile registration'
        Write-Host 'The identical experimental bundle is already installed and registered.'
        exit 0
    }
}

$InstallParent = Split-Path -Parent $InstallRoot
New-Item -ItemType Directory -Path $InstallParent -Force | Out-Null
$StagingRoot = "$InstallRoot.staging-$([guid]::NewGuid().ToString('N'))"
$BackupRoot = "$InstallRoot.backup-$([guid]::NewGuid().ToString('N'))"
$HadPreviousInstall = Test-Path -LiteralPath $InstallRoot -PathType Container
$InstallSwapped = $false

try {
    New-Item -ItemType Directory -Path $StagingRoot -Force | Out-Null
    Get-ChildItem -LiteralPath $BuildDirectory -Force |
        ForEach-Object {
            Copy-Item -LiteralPath $_.FullName -Destination $StagingRoot `
                -Recurse -Force
        }

    $StagedTool = Join-Path $StagingRoot $ProfileToolName
    $StagedDll = Join-Path $StagingRoot $TsfDllName
    & $StagedTool verify `
        --clsid $ExperimentalClsid `
        --profile-guid $ExperimentalProfileGuid `
        --dll $StagedDll
    Assert-LastExitCode -Operation 'Staged experimental identity verification'

    Get-Process -Name 'NeuralWeaselServer' -ErrorAction SilentlyContinue |
        Stop-Process -Force -ErrorAction Stop

    if ($HadPreviousInstall) {
        Move-Item -LiteralPath $InstallRoot -Destination $BackupRoot
    }
    Move-Item -LiteralPath $StagingRoot -Destination $InstallRoot
    $InstallSwapped = $true

    $RuntimeRoot = Join-Path $env:LOCALAPPDATA 'NeuralWeasel\Experimental'
    $RimeUserRoot = Join-Path $RuntimeRoot 'RimeUser'
    New-Item -ItemType Directory -Path $RimeUserRoot -Force | Out-Null
    Get-ChildItem -LiteralPath (Join-Path $InstallRoot 'rime-user') -File |
        ForEach-Object {
            $Destination = Join-Path $RimeUserRoot $_.Name
            if (-not (Test-Path -LiteralPath $Destination)) {
                Copy-Item -LiteralPath $_.FullName -Destination $Destination
            }
        }

    $InstalledTool = Join-Path $InstallRoot $ProfileToolName
    $InstalledDll = Join-Path $InstallRoot $TsfDllName
    & $InstalledTool register `
        --clsid $ExperimentalClsid `
        --profile-guid $ExperimentalProfileGuid `
        --dll $InstalledDll
    Assert-LastExitCode -Operation 'Experimental profile registration'

    if (Test-Path -LiteralPath $BackupRoot) {
        try {
            Remove-Item -LiteralPath $BackupRoot -Recurse -Force
        } catch {
            Write-Warning "Profile is installed, but the previous isolated backup could not be removed: $BackupRoot"
        }
    }
} catch {
    $InstallFailure = $_
    if (
        $InstallSwapped -and
        (Test-Path -LiteralPath $InstallRoot -PathType Container)
    ) {
        Remove-Item -LiteralPath $InstallRoot -Recurse -Force
    }
    if (Test-Path -LiteralPath $BackupRoot -PathType Container) {
        Move-Item -LiteralPath $BackupRoot -Destination $InstallRoot
        $RollbackTool = Join-Path $InstallRoot $ProfileToolName
        $RollbackDll = Join-Path $InstallRoot $TsfDllName
        & $RollbackTool register `
            --clsid $ExperimentalClsid `
            --profile-guid $ExperimentalProfileGuid `
            --dll $RollbackDll
        if ($LASTEXITCODE -ne 0) {
            throw "Install failed and previous profile registration could not be restored."
        }
    }
    if (Test-Path -LiteralPath $StagingRoot -PathType Container) {
        Remove-Item -LiteralPath $StagingRoot -Recurse -Force
    }
    throw $InstallFailure
}

Write-Host 'Installed 神经小狼毫（实验） without changing the default input method.'
Write-Host 'Open Windows input settings or Win+Space and select 神经小狼毫（实验） manually.'
Write-Host "Start the model service with: $InstallRoot\start-model-service.ps1 -Backend full"
