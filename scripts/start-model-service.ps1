[CmdletBinding()]
param(
    [string]$Model = 'Qwen/Qwen3.5-0.8B-Base',
    [ValidateSet('full', 'sparse')]
    [string]$Backend = 'full',
    [string]$Index
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$AllowedModels = @(
    'Qwen/Qwen3.5-0.8B-Base',
    'Qwen/Qwen3.5-4B-Base'
)
if ($Model -notin $AllowedModels) {
    throw "Checkpoint is not in the Base-only allowlist: $($AllowedModels -join ', ')"
}

$BundledUv = Join-Path $PSScriptRoot 'tools\uv.exe'
if (Test-Path -LiteralPath $BundledUv -PathType Leaf) {
    $UvCommand = $BundledUv
    $UvVersion = (& $UvCommand --version).Trim()
    if ($LASTEXITCODE -ne 0 -or $UvVersion -ne 'uv 0.8.22') {
        throw "The bundled uv runtime is not the pinned version: $UvVersion"
    }
} else {
    $PathUv = Get-Command uv -ErrorAction SilentlyContinue
    if (-not $PathUv) {
        throw 'The bundle is missing tools\uv.exe and uv was not found on PATH.'
    }
    $UvCommand = $PathUv.Source
}

$ArtifactProject = Join-Path $PSScriptRoot 'python-service'
$ProjectRoot = if (Test-Path -LiteralPath $ArtifactProject -PathType Container) {
    $ArtifactProject
} else {
    Split-Path -Parent $PSScriptRoot
}
$RuntimeRoot = Join-Path $env:LOCALAPPDATA 'NeuralWeasel\Experimental'
$StatePath = Join-Path $RuntimeRoot 'model-service.json'
New-Item -ItemType Directory -Path $RuntimeRoot -Force | Out-Null

if (-not $Index) {
    $Hasher = [Security.Cryptography.SHA256]::Create()
    try {
        $Bytes = [Text.Encoding]::UTF8.GetBytes($Model)
        $Digest = $Hasher.ComputeHash($Bytes)
        $ModelHash = ([BitConverter]::ToString($Digest)).Replace('-', '')
        $ModelHash = $ModelHash.Substring(0, 16).ToLowerInvariant()
    } finally {
        $Hasher.Dispose()
    }
    $IndexRoot = Join-Path $env:LOCALAPPDATA 'NeuralWeasel\indexes'
    New-Item -ItemType Directory -Path $IndexRoot -Force | Out-Null
    $Index = Join-Path $IndexRoot "$ModelHash.sqlite3"
}

function Write-Utf8NoBom {
    param(
        [Parameter(Mandatory)][string]$Path,
        [Parameter(Mandatory)][string]$Content
    )
    $Encoding = New-Object Text.UTF8Encoding($false)
    [IO.File]::WriteAllText($Path, $Content, $Encoding)
}

function Write-ServiceState {
    param(
        [Parameter(Mandatory)][string]$State,
        [int]$ExitCode = 0
    )
    $TemporaryState = "$StatePath.tmp-$PID"
    $Json = [ordered]@{
        state = $State
        backend = $Backend
        model = $Model
        pid = $PID
        exit_code = $ExitCode
        updated_utc = [DateTime]::UtcNow.ToString('o')
    } | ConvertTo-Json
    Write-Utf8NoBom -Path $TemporaryState -Content $Json
    Move-Item -LiteralPath $TemporaryState -Destination $StatePath -Force
}

if (-not (Test-Path -LiteralPath $Index -PathType Leaf)) {
    Write-ServiceState -State 'building-index'
    & $UvCommand run --project $ProjectRoot --frozen neural-weasel build-index `
        --model $Model `
        --output $Index
    if ($LASTEXITCODE -ne 0) {
        Write-ServiceState -State 'index-failed' -ExitCode $LASTEXITCODE
        throw "Pinyin index initialization failed with exit code $LASTEXITCODE."
    }
}

Write-ServiceState -State 'running'
try {
    & $UvCommand run --project $ProjectRoot --frozen neural-weasel serve `
        --model $Model `
        --backend $Backend `
        --index $Index
    $ServiceExit = $LASTEXITCODE
    if ($ServiceExit -ne 0) {
        Write-ServiceState -State 'failed' -ExitCode $ServiceExit
        throw "Model service stopped with exit code $ServiceExit. No backend fallback was attempted."
    }
    Write-ServiceState -State 'stopped'
} catch {
    Write-ServiceState -State 'failed' -ExitCode 1
    throw
}
