$ErrorActionPreference = 'Stop'

$projectRoot = Split-Path -Parent $PSScriptRoot
Push-Location -LiteralPath $projectRoot
try {
    uv run ruff check .
    uv run pytest
    uv run neural-weasel gpu-info
} finally {
    Pop-Location
}


