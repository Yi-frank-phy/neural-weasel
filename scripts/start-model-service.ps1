[CmdletBinding()]
param(
    [ValidateSet('Qwen/Qwen3.5-0.8B-Base')]
    [string]$Model = 'Qwen/Qwen3.5-0.8B-Base',
    [ValidateSet('full', 'sparse')]
    [string]$Backend = 'full',
    [ValidateSet('int8')]
    [string]$Precision = 'int8',
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
        transport = $Transport
        model = $Model
        precision = $Precision
        index = $Index
        pid = $PID
        exit_code = $ExitCode
        safety_profile = 'crash-contained-0.8b'
        updated_utc = [DateTime]::UtcNow.ToString('o')
    } | ConvertTo-Json
    Write-Utf8NoBom -Path $TemporaryState -Content $Json
    Move-Item -LiteralPath $TemporaryState -Destination $StatePath -Force
}

if (-not $Index) {
    $IndexOutput = @(
        & $UvCommand run --project $ProjectRoot --frozen python -m neural_weasel.resolve_index `
            --model $Model
    )
    if ($LASTEXITCODE -ne 0 -or $IndexOutput.Count -eq 0) {
        throw 'Failed to resolve the canonical tokenizer-versioned pinyin index path.'
    }
    $Index = ([string]$IndexOutput[-1]).Trim()
    if (-not $Index) {
        throw 'The canonical pinyin index path resolved to an empty value.'
    }
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
        $ServeCommand,
        '--model', $Model,
        '--precision', $Precision,
        '--backend', $Backend,
        '--index', $Index
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
