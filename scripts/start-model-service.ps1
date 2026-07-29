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
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    throw 'uv is required to start the development model service.'
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
        $ModelHash = [Convert]::ToHexString(
            $Hasher.ComputeHash($Bytes)
        ).Substring(0, 16).ToLowerInvariant()
    } finally {
        $Hasher.Dispose()
    }
    $IndexRoot = Join-Path $env:LOCALAPPDATA 'NeuralWeasel\indexes'
    New-Item -ItemType Directory -Path $IndexRoot -Force | Out-Null
    $Index = Join-Path $IndexRoot "$ModelHash.sqlite3"
}

function Write-ServiceState {
    param(
        [Parameter(Mandatory)][string]$State,
        [int]$ExitCode = 0
    )
    [ordered]@{
        state = $State
        backend = $Backend
        model = $Model
        pid = $PID
        exit_code = $ExitCode
        updated_utc = [DateTime]::UtcNow.ToString('o')
    } | ConvertTo-Json |
        Set-Content -LiteralPath $StatePath -Encoding utf8NoBOM
}

if (-not (Test-Path -LiteralPath $Index -PathType Leaf)) {
    Write-ServiceState -State 'building-index'
    & uv run --project $ProjectRoot --frozen neural-weasel build-index `
        --model $Model `
        --output $Index
    if ($LASTEXITCODE -ne 0) {
        Write-ServiceState -State 'index-failed' -ExitCode $LASTEXITCODE
        throw "Pinyin index initialization failed with exit code $LASTEXITCODE."
    }
}

Write-ServiceState -State 'running'
try {
    & uv run --project $ProjectRoot --frozen neural-weasel serve `
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
