[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [string]$WeaselRoot,
    [Parameter(Mandatory)]
    [string]$NativeBuildRoot,
    [string]$RepositoryRoot = (Split-Path -Parent $PSScriptRoot),
    [string]$OutputRoot = (
        Join-Path (Split-Path -Parent $PSScriptRoot) 'dist/neural-weasel-experimental'
    ),
    [string]$RepositoryCommit = $env:GITHUB_SHA
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$ExperimentalClsid = '{8AA66261-ED5F-46B0-895D-339B42C3AE1B}'
$ExperimentalProfileGuid = '{C9B3984E-A16C-4779-80E8-ACD988C57B0D}'
$WeaselRevision = '9cc96e20dc71b80876b12f689bb5863c76c2a7ed'
$PipeEndpoint = '\\.\pipe\NeuralWeasel-v1-<current-user-SID>'
$WeaselIpcEndpoint = '\\.\pipe\<current-user-name>\NeuralWeaselExperimentalIPC'

function Copy-RequiredFile {
    param(
        [Parameter(Mandatory)][string]$Source,
        [Parameter(Mandatory)][string]$Destination
    )
    if (-not (Test-Path -LiteralPath $Source -PathType Leaf)) {
        throw "Required build artifact is missing: $Source"
    }
    $DestinationDirectory = Split-Path -Parent $Destination
    New-Item -ItemType Directory -Path $DestinationDirectory -Force | Out-Null
    Copy-Item -LiteralPath $Source -Destination $Destination -Force
}

function Resolve-RequiredBuildArtifact {
    param(
        [Parameter(Mandatory)][string]$FileName,
        [Parameter(Mandatory)][string[]]$Candidates,
        [Parameter(Mandatory)][string[]]$SearchRoots
    )

    foreach ($Candidate in $Candidates) {
        if (Test-Path -LiteralPath $Candidate -PathType Leaf) {
            return (Resolve-Path -LiteralPath $Candidate).Path
        }
    }

    $Matches = @()
    foreach ($SearchRoot in $SearchRoots) {
        if (-not (Test-Path -LiteralPath $SearchRoot -PathType Container)) {
            continue
        }
        $Matches += Get-ChildItem -LiteralPath $SearchRoot -Filter $FileName `
            -File -Recurse -ErrorAction SilentlyContinue |
            Select-Object -ExpandProperty FullName
    }
    $Matches = @($Matches | Sort-Object -Unique)

    if ($Matches.Count -eq 1) {
        return $Matches[0]
    }
    if ($Matches.Count -gt 1) {
        throw "Ambiguous build artifact ${FileName}: $($Matches -join ', ')"
    }

    $Diagnostics = @()
    foreach ($SearchRoot in $SearchRoots) {
        if (Test-Path -LiteralPath $SearchRoot -PathType Container) {
            $Diagnostics += Get-ChildItem -LiteralPath $SearchRoot -File -Recurse `
                -ErrorAction SilentlyContinue |
                Where-Object {
                    $_.Extension -in @('.dll', '.exe', '.lib', '.pdb')
                } |
                Select-Object -First 200 -ExpandProperty FullName
        }
    }
    throw (
        "Required build artifact $FileName was not found. " +
        "Binary outputs seen: $($Diagnostics -join ', ')"
    )
}

$RepositoryRoot = (Resolve-Path -LiteralPath $RepositoryRoot).Path
$WeaselRoot = (Resolve-Path -LiteralPath $WeaselRoot).Path
$NativeBuildRoot = (Resolve-Path -LiteralPath $NativeBuildRoot).Path
$OutputRoot = [IO.Path]::GetFullPath($OutputRoot)

if (Test-Path -LiteralPath $OutputRoot) {
    Remove-Item -LiteralPath $OutputRoot -Recurse -Force
}
New-Item -ItemType Directory -Path $OutputRoot -Force | Out-Null

$TsfArtifact = Resolve-RequiredBuildArtifact `
    -FileName 'NeuralWeaselExperimentalTSF.dll' `
    -Candidates @(
        (Join-Path $WeaselRoot 'output/NeuralWeaselExperimentalTSF.dll')
    ) `
    -SearchRoots @(
        (Join-Path $WeaselRoot 'output'),
        (Join-Path $WeaselRoot 'build')
    )
$ServerArtifact = Resolve-RequiredBuildArtifact `
    -FileName 'NeuralWeaselServer.exe' `
    -Candidates @(
        (Join-Path $WeaselRoot 'output/NeuralWeaselServer.exe')
    ) `
    -SearchRoots @(
        (Join-Path $WeaselRoot 'output'),
        (Join-Path $WeaselRoot 'build')
    )
$ProfileToolArtifact = Resolve-RequiredBuildArtifact `
    -FileName 'NeuralWeaselProfileTool.exe' `
    -Candidates @(
        (Join-Path $NativeBuildRoot 'Release/NeuralWeaselProfileTool.exe')
    ) `
    -SearchRoots @($NativeBuildRoot)

Copy-RequiredFile -Source $TsfArtifact `
    -Destination (Join-Path $OutputRoot 'NeuralWeaselExperimentalTSF.dll')
Copy-RequiredFile -Source $ServerArtifact `
    -Destination (Join-Path $OutputRoot 'NeuralWeaselServer.exe')
Copy-RequiredFile -Source $ProfileToolArtifact `
    -Destination (Join-Path $OutputRoot 'NeuralWeaselProfileTool.exe')

$RimeLibraryCandidates = @(
    (Join-Path $WeaselRoot 'build/windows/x64/release/RimeWithWeasel/RimeWithWeasel.lib'),
    (Join-Path $WeaselRoot 'build/.objs/RimeWithWeasel/windows/x64/release/RimeWithWeasel.lib')
)
$RimeLibrary = $RimeLibraryCandidates |
    Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } |
    Select-Object -First 1
if (-not $RimeLibrary) {
    $RimeLibrary = Get-ChildItem -LiteralPath (Join-Path $WeaselRoot 'build') `
        -Filter 'RimeWithWeasel.lib' -File -Recurse |
        Select-Object -ExpandProperty FullName -First 1
}
if (-not $RimeLibrary) {
    throw 'Linked RimeWithWeasel static neural module was not produced.'
}
Copy-RequiredFile -Source $RimeLibrary `
    -Destination (Join-Path $OutputRoot 'NeuralWeaselRimeModule.lib')

Get-ChildItem -LiteralPath (Join-Path $WeaselRoot 'output') -Filter '*.dll' -File |
    Where-Object {
        $_.Name -notmatch '^(?i:weasel.*\.dll)$' -and
        $_.Name -ne 'NeuralWeaselExperimentalTSF.dll'
    } |
    ForEach-Object {
        Copy-Item -LiteralPath $_.FullName -Destination $OutputRoot -Force
    }

$DataRoot = Join-Path $OutputRoot 'data'
New-Item -ItemType Directory -Path $DataRoot -Force | Out-Null
Get-ChildItem -LiteralPath (Join-Path $RepositoryRoot 'assets/rime') `
    -Filter '*.yaml' -File |
    ForEach-Object {
        Copy-Item -LiteralPath $_.FullName -Destination $DataRoot -Force
    }
$OpenCcSource = Join-Path $WeaselRoot 'librime/share/opencc'
if (Test-Path -LiteralPath $OpenCcSource -PathType Container) {
    Copy-Item -LiteralPath $OpenCcSource -Destination (
        Join-Path $DataRoot 'opencc'
    ) -Recurse -Force
}

$RimeUser = Join-Path $OutputRoot 'rime-user'
New-Item -ItemType Directory -Path $RimeUser -Force | Out-Null
Get-ChildItem -LiteralPath (Join-Path $RepositoryRoot 'assets/rime') `
    -Filter '*.yaml' -File |
    ForEach-Object {
        Copy-Item -LiteralPath $_.FullName -Destination $RimeUser -Force
    }

foreach ($Script in @(
    'install-dev-profile.ps1',
    'uninstall-dev-profile.ps1',
    'diagnose.ps1',
    'start-model-service.ps1'
)) {
    Copy-RequiredFile -Source (Join-Path $RepositoryRoot "scripts/$Script") `
        -Destination (Join-Path $OutputRoot $Script)
}
Copy-RequiredFile -Source (
    Join-Path $RepositoryRoot 'docs/manual/windows-install-smoke-test.md'
) -Destination (Join-Path $OutputRoot 'README-INSTALL-TEST.md')

$PythonService = Join-Path $OutputRoot 'python-service'
New-Item -ItemType Directory -Path $PythonService -Force | Out-Null
Copy-RequiredFile -Source (Join-Path $RepositoryRoot 'pyproject.toml') `
    -Destination (Join-Path $PythonService 'pyproject.toml')
Copy-RequiredFile -Source (Join-Path $RepositoryRoot 'uv.lock') `
    -Destination (Join-Path $PythonService 'uv.lock')
Copy-Item -LiteralPath (Join-Path $RepositoryRoot 'src') `
    -Destination (Join-Path $PythonService 'src') -Recurse -Force

$ActualWeaselRevision = (& git -C $WeaselRoot rev-parse HEAD).Trim()
if ($ActualWeaselRevision -ne $WeaselRevision) {
    throw "Unexpected Weasel revision in bundle: $ActualWeaselRevision"
}
$LibrimeRevision = (& git -C (Join-Path $WeaselRoot 'librime') rev-parse HEAD).Trim()
if ($LASTEXITCODE -ne 0 -or $LibrimeRevision.Length -ne 40) {
    throw 'Unable to resolve pinned librime submodule revision.'
}
if (-not $RepositoryCommit) {
    $RepositoryCommit = (& git -C $RepositoryRoot rev-parse HEAD).Trim()
}

$HashTargets = Get-ChildItem -LiteralPath $OutputRoot -File -Recurse |
    Where-Object { $_.Name -ne 'build-manifest.json' } |
    Sort-Object FullName
$Hashes = [ordered]@{}
foreach ($File in $HashTargets) {
    $Relative = (
        [IO.Path]::GetRelativePath($OutputRoot, $File.FullName)
    ).Replace('\', '/')
    $Hash = Get-FileHash -LiteralPath $File.FullName -Algorithm SHA256
    $Hashes[$Relative] = $Hash.Hash.ToLowerInvariant()
}

$Manifest = [ordered]@{
    schema_version = 1
    repository_commit_sha = $RepositoryCommit
    upstream_weasel_revision = $WeaselRevision
    librime_revision = $LibrimeRevision
    compiler = "MSVC $env:VSCMD_VER"
    architecture = 'x64'
    experimental_clsid = $ExperimentalClsid
    experimental_profile_guid = $ExperimentalProfileGuid
    pipe_endpoint = $PipeEndpoint
    model_pipe_endpoint = $PipeEndpoint
    weasel_ipc_endpoint = $WeaselIpcEndpoint
    install_directory = '%LOCALAPPDATA%\NeuralWeasel\Experimental\experimental-profile'
    artifacts = $Hashes
}
$Manifest | ConvertTo-Json -Depth 6 |
    Set-Content -LiteralPath (Join-Path $OutputRoot 'build-manifest.json') `
        -Encoding utf8NoBOM

Write-Host "Built Windows bundle: $OutputRoot"
