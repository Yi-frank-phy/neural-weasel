[CmdletBinding()]
param(
    [string]$Model = 'Qwen/Qwen3.5-0.8B-Base',
    [string]$Index
)

$ErrorActionPreference = 'Stop'

$ProjectRoot = Split-Path -Parent $PSScriptRoot
Push-Location -LiteralPath $ProjectRoot
try {
    $Arguments = @('run', 'neural-weasel', 'serve', '--model', $Model)
    if ($Index) {
        $Arguments += @('--index', $Index)
    }
    & uv @Arguments
    exit $LASTEXITCODE
} finally {
    Pop-Location
}
