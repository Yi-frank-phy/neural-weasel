[CmdletBinding()]
param(
    [string]$BuildDirectory = $PSScriptRoot,
    [string]$InstallRoot = (
        Join-Path $env:LOCALAPPDATA 'NeuralWeasel\WisdomIntegration'
    ),
    [string]$RimeUserRoot = (Join-Path $env:APPDATA 'Rime'),
    [switch]$DryRun
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

function Write-Utf8NoBom {
    param(
        [Parameter(Mandatory)][string]$Path,
        [Parameter(Mandatory)][string]$Content
    )
    $Encoding = New-Object Text.UTF8Encoding($false)
    [IO.File]::WriteAllText($Path, $Content, $Encoding)
}

if (-not $env:LOCALAPPDATA -or -not $env:APPDATA) {
    throw 'LOCALAPPDATA and APPDATA are required.'
}

$BuildDirectory = (Resolve-Path -LiteralPath $BuildDirectory).Path
$ExpectedInstallRoot = [IO.Path]::GetFullPath(
    (Join-Path $env:LOCALAPPDATA 'NeuralWeasel\WisdomIntegration')
)
$InstallRoot = [IO.Path]::GetFullPath($InstallRoot)
if ($InstallRoot -ne $ExpectedInstallRoot) {
    throw 'Refusing an install directory outside the reserved Wisdom integration boundary.'
}

$RimeUserRoot = [IO.Path]::GetFullPath($RimeUserRoot)
$ExpectedRimeRoot = [IO.Path]::GetFullPath((Join-Path $env:APPDATA 'Rime'))
if ($RimeUserRoot -ne $ExpectedRimeRoot) {
    throw 'Refusing to edit a Rime directory outside the current user profile.'
}

$Required = @(
    'build-manifest.json',
    'start-model-service.ps1',
    'start-wisdom-service.vbs',
    'start-neural-weasel-integration.ps1',
    'install-wisdom-integration.ps1',
    'tools\uv.exe',
    'python-service\pyproject.toml',
    'python-service\uv.lock',
    'python-service\src\neural_weasel\http_server.py',
    'python-service\src\neural_weasel\internal_cli.py'
)
foreach ($RelativePath in $Required) {
    if (-not (Test-Path -LiteralPath (Join-Path $BuildDirectory $RelativePath) -PathType Leaf)) {
        throw "Missing required integration artifact: $RelativePath"
    }
}

$ManifestPath = Join-Path $BuildDirectory 'build-manifest.json'
$Manifest = Get-Content -LiteralPath $ManifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
$InstallFiles = @(
    Get-ChildItem -LiteralPath (Join-Path $BuildDirectory 'python-service') -File -Recurse |
        Where-Object { $_.FullName -notmatch '[\\/]__pycache__[\\/]' }
    Get-Item -LiteralPath (Join-Path $BuildDirectory 'tools\uv.exe')
    Get-Item -LiteralPath (Join-Path $BuildDirectory 'start-model-service.ps1')
    Get-Item -LiteralPath (Join-Path $BuildDirectory 'start-wisdom-service.vbs')
    Get-Item -LiteralPath (Join-Path $BuildDirectory 'start-neural-weasel-integration.ps1')
)
$FilesToVerify = @(
    $InstallFiles
    Get-Item -LiteralPath (Join-Path $BuildDirectory 'install-wisdom-integration.ps1')
)
foreach ($File in $FilesToVerify) {
    $RelativePath = $File.FullName.Substring($BuildDirectory.Length + 1)
    $ManifestName = $RelativePath.Replace('\', '/')
    $Property = $Manifest.artifacts.PSObject.Properties[$ManifestName]
    if (-not $Property) {
        throw "Integration artifact is not hashed: $RelativePath"
    }
    $ActualHash = (Get-FileHash -LiteralPath $File.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($ActualHash -ne [string]$Property.Value) {
        throw "Integration artifact hash mismatch: $RelativePath"
    }
}

$InstallMatches = Test-Path -LiteralPath $InstallRoot -PathType Container
if ($InstallMatches) {
    foreach ($File in $InstallFiles) {
        $RelativePath = $File.FullName.Substring($BuildDirectory.Length + 1)
        $InstalledPath = Join-Path $InstallRoot $RelativePath
        if (-not (Test-Path -LiteralPath $InstalledPath -PathType Leaf) -or
            (Get-FileHash -LiteralPath $InstalledPath -Algorithm SHA256).Hash -ne
            (Get-FileHash -LiteralPath $File.FullName -Algorithm SHA256).Hash) {
            $InstallMatches = $false
            break
        }
    }
}

$ConfigPath = Join-Path $RimeUserRoot 'weasel.custom.yaml'
if (-not (Test-Path -LiteralPath $ConfigPath -PathType Leaf)) {
    throw "Wisdom Weasel user configuration was not found: $ConfigPath"
}

if ($DryRun) {
    Write-Host 'Wisdom integration dry-run succeeded; no files or configuration were changed.'
    exit 0
}

$InstallParent = Split-Path -Parent $InstallRoot
New-Item -ItemType Directory -Path $InstallParent -Force | Out-Null
$StagingRoot = "$InstallRoot.staging-$([guid]::NewGuid().ToString('N'))"
$BackupRoot = "$InstallRoot.backup-$([guid]::NewGuid().ToString('N'))"
$HadPreviousInstall = Test-Path -LiteralPath $InstallRoot -PathType Container
$Swapped = $false

try {
    if (-not $InstallMatches) {
        New-Item -ItemType Directory -Path $StagingRoot -Force | Out-Null
        foreach ($File in $InstallFiles) {
            $RelativePath = $File.FullName.Substring($BuildDirectory.Length + 1)
            $Destination = Join-Path $StagingRoot $RelativePath
            New-Item -ItemType Directory -Path (Split-Path -Parent $Destination) -Force |
                Out-Null
            Copy-Item -LiteralPath $File.FullName -Destination $Destination -Force
        }
        Copy-Item -LiteralPath $ManifestPath `
            -Destination (Join-Path $StagingRoot 'build-manifest.json') -Force

        if ($HadPreviousInstall) {
            Move-Item -LiteralPath $InstallRoot -Destination $BackupRoot
        }
        Move-Item -LiteralPath $StagingRoot -Destination $InstallRoot
        $Swapped = $true
    }

    $Lines = @(Get-Content -LiteralPath $ConfigPath -Encoding UTF8)
    $ManagedKeys = @(
        'llm/enabled',
        'llm/provider_type',
        'llm/hf_constraint/api_url',
        'llm/hf_constraint/health_url',
        'llm/hf_constraint/launcher_path',
        'llm/hf_constraint/startup_timeout_ms',
        'llm/hf_constraint/startup_poll_interval_ms'
    )
    $Filtered = @(
        foreach ($Line in $Lines) {
            $IsManaged = $false
            foreach ($Key in $ManagedKeys) {
                if ($Line -match ('^\s*["'']?' + [regex]::Escape($Key) + '["'']?\s*:')) {
                    $IsManaged = $true
                    break
                }
            }
            if (-not $IsManaged) {
                $Line
            }
        }
    )
    if (-not ($Filtered -match '^patch:\s*$')) {
        $Filtered += 'patch:'
    }
    $LauncherPath = (Join-Path $InstallRoot 'start-wisdom-service.vbs').Replace('\', '/')
    $Filtered += @(
        '  "llm/enabled": true',
        '  "llm/provider_type": hf_constraint',
        '  "llm/hf_constraint/api_url": "http://127.0.0.1:8000/v1/generate/completions"',
        '  "llm/hf_constraint/health_url": "http://127.0.0.1:8000/health"',
        ('  "llm/hf_constraint/launcher_path": "' + $LauncherPath + '"'),
        '  "llm/hf_constraint/startup_timeout_ms": 1800000',
        '  "llm/hf_constraint/startup_poll_interval_ms": 500'
    )
    $DesiredConfig = ($Filtered -join "`r`n") + "`r`n"
    $ExistingConfig = [IO.File]::ReadAllText($ConfigPath, [Text.Encoding]::UTF8)
    if ($ExistingConfig -ne $DesiredConfig) {
        $Timestamp = Get-Date -Format 'yyyyMMdd-HHmmss'
        $ConfigBackup = "$ConfigPath.neural-weasel-backup-$Timestamp"
        Copy-Item -LiteralPath $ConfigPath -Destination $ConfigBackup
        Write-Utf8NoBom -Path $ConfigPath -Content $DesiredConfig
    }

    if (Test-Path -LiteralPath $BackupRoot -PathType Container) {
        Remove-Item -LiteralPath $BackupRoot -Recurse -Force
    }
} catch {
    $Failure = $_
    if ($Swapped -and (Test-Path -LiteralPath $InstallRoot -PathType Container)) {
        Remove-Item -LiteralPath $InstallRoot -Recurse -Force
    }
    if (Test-Path -LiteralPath $BackupRoot -PathType Container) {
        Move-Item -LiteralPath $BackupRoot -Destination $InstallRoot
    }
    if (Test-Path -LiteralPath $StagingRoot -PathType Container) {
        Remove-Item -LiteralPath $StagingRoot -Recurse -Force
    }
    throw $Failure
}

if ($InstallMatches) {
    Write-Host "Wisdom Weasel model adapter files are already current at $InstallRoot"
} else {
    Write-Host "Installed the Wisdom Weasel model adapter at $InstallRoot"
}
Write-Host 'The existing Rime schemas and user dictionary were not replaced.'
Write-Host 'Start start-wisdom-service.vbs, then redeploy or restart Wisdom Weasel.'
