[CmdletBinding()]
param(
    [ValidateSet('experimental', 'wisdom')]
    [string]$ServiceProfile = 'experimental',
    [ValidateSet('pipe', 'http')]
    [string]$Transport = 'pipe',
    [ValidateRange(1, 65535)]
    [int]$Port = 8000,
    [string]$Index
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$Model = 'Qwen/Qwen3.5-4B-Base'
$ModelFormat = 'gguf'
$Quantization = 'Q8_0'
$Runtime = 'llama.cpp'
$ComputeBackend = 'CUDA'
$LlamaCppPythonVersion = '0.3.23'
$LlamaCudaWheelIndex = 'https://abetlen.github.io/llama-cpp-python/whl/cu124'

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
$RuntimeNamespace = if ($ServiceProfile -eq 'wisdom') {
    'WisdomIntegration'
} else {
    'Experimental'
}
$RuntimeRoot = Join-Path $env:LOCALAPPDATA (Join-Path 'NeuralWeasel' $RuntimeNamespace)
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
        service_profile = $ServiceProfile
        transport = $Transport
        model = $Model
        format = $ModelFormat
        quantization = $Quantization
        runtime = $Runtime
        compute_backend = $ComputeBackend
        gpu_layers = 'all'
        index = $Index
        pid = $PID
        exit_code = $ExitCode
        safety_profile = 'crash-contained-4b-q8-gguf-cuda'
        updated_utc = [DateTime]::UtcNow.ToString('o')
    } | ConvertTo-Json
    Write-Utf8NoBom -Path $TemporaryState -Content $Json
    Move-Item -LiteralPath $TemporaryState -Destination $StatePath -Force
}

Write-ServiceState -State 'preparing-runtime'

# Keep the project environment reproducible while preserving the separately
# verified CUDA llama.cpp wheel between launches.
& $UvCommand sync --project $ProjectRoot --frozen --inexact
if ($LASTEXITCODE -ne 0) {
    Write-ServiceState -State 'failed' -ExitCode $LASTEXITCODE
    throw "Python environment synchronization failed with exit code $LASTEXITCODE."
}

$PythonExe = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $PythonExe -PathType Leaf)) {
    Write-ServiceState -State 'failed' -ExitCode 1
    throw "The synchronized Python runtime is missing: $PythonExe"
}

& $PythonExe -m neural_weasel.llama_install_check *> $null
if ($LASTEXITCODE -ne 0) {
    Write-Host (
        "Installing pinned llama-cpp-python $LlamaCppPythonVersion from the official CUDA 12.4 wheel index."
    )
    & $UvCommand pip install `
        --python $PythonExe `
        --reinstall `
        --extra-index-url $LlamaCudaWheelIndex `
        "llama-cpp-python==$LlamaCppPythonVersion"
    if ($LASTEXITCODE -ne 0) {
        Write-ServiceState -State 'failed' -ExitCode $LASTEXITCODE
        throw "CUDA llama.cpp installation failed with exit code $LASTEXITCODE."
    }
}

& $PythonExe -m neural_weasel.llama_install_check
if ($LASTEXITCODE -ne 0) {
    Write-ServiceState -State 'failed' -ExitCode $LASTEXITCODE
    throw (
        'llama-cpp-python did not prove a CUDA-enabled llama.cpp build. ' +
        'CPU fallback is forbidden.'
    )
}

Write-ServiceState -State 'starting'
try {
    $ServeCommand = if ($Transport -eq 'http') { 'serve-http' } else { 'serve' }
    $Arguments = @(
        'run', '--project', $ProjectRoot, '--no-sync', 'neural-weasel', $ServeCommand
    )
    if ($Index) {
        $Arguments += @('--index', $Index)
    }
    if ($Transport -eq 'http') {
        $Arguments += @('--host', '127.0.0.1', '--port', [string]$Port)
    }

    Write-ServiceState -State 'running'
    & $UvCommand @Arguments
    $ServiceExit = $LASTEXITCODE
    if ($ServiceExit -ne 0) {
        Write-ServiceState -State 'failed' -ExitCode $ServiceExit
        throw (
            "GGUF CUDA model service stopped with exit code $ServiceExit. " +
            'No CPU fallback or automatic model substitution was attempted.'
        )
    }
    Write-ServiceState -State 'stopped'
} catch {
    Write-ServiceState -State 'failed' -ExitCode 1
    throw
}
