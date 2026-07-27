$ErrorActionPreference = 'Stop'

$projectRoot = Split-Path -Parent $PSScriptRoot
Push-Location -LiteralPath $projectRoot
try {
    uv python install 3.12
    uv sync --extra dev
    uv run neural-weasel gpu-info
} finally {
    Pop-Location
}


