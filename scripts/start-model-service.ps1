[CmdletBinding()]
param(
    [ValidateSet('Qwen/Qwen3.5-0.8B-Base')]
    [string]$Model = 'Qwen/Qwen3.5-0.8B-Base',
    [ValidateSet('full', 'sparse')]
    [string]$Backend = 'full',
    [ValidateSet('bf16', 'fp8', 'int8', 'nf4')]
    [string]$Precision = 'fp8',
    [ValidateSet('pipe', 'http')]
    [string]$Transport = 'pipe',
    [ValidateRange(1, 65535)]
    [int]$Port = 8000,
    [string]$Index
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$BundledUv = Join-Path $PSScriptRoot 'tools\uv.exe'
if (Test-Path -LiteralPath $BundledUv -PathType Leaf) {
    $UvCommand = $BundledUv
    $UvVersionOutput = (& $UvCommand --version).Trim()
    if ($LASTEXITCODE -ne 0 -or $UvVersionOutput -notmatch '^uv 0\.8\.22(?:\s|$)') {
        throw "The bundled uv runtime is not the pinned version: $UvVersionOutput"
    }
    $UvVersion = 'uv 0.8.22'
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
        precision = $Precision
        transport = $Transport
        model = $Model
        pid = $PID
        exit_code = $ExitCode
        safety_profile = 'crash-contained-0.8b'
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
    $ServeCommand = if ($Transport -eq 'http') { 'serve-http' } else { 'serve' }
    $Arguments = @(
        'run', '--project', $ProjectRoot, '--frozen', 'neural-weasel',
        $ServeCommand, '--model', $Model, '--precision', $Precision,
        '--backend', $Backend, '--index', $Index
    )
    if ($Transport -eq 'http') {
        $Arguments += @('--host', '127.0.0.1', '--port', [string]$Port)
    }
    & $UvCommand @Arguments
    $ServiceExit = $LASTEXITCODE
    if ($ServiceExit -ne 0) {
        Write-ServiceState -State 'failed' -ExitCode $ServiceExit
        throw "Model service stopped with exit code $ServiceExit. No automatic restart was attempted."
    }
    Write-ServiceState -State 'stopped'
} catch {
    Write-ServiceState -State 'failed' -ExitCode 1
    throw
}
