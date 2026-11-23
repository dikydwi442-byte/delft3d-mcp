$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
Write-Host "Building delft3d-mcp in $repoRoot ..."
Push-Location $repoRoot
try {
    uv sync --all-groups
    uv run pytest tests/ -v
    uv run python scripts/smoke_mcp_stdio.py
} finally {
    Pop-Location
}
