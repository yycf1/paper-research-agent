param([int]$Port = 8000, [switch]$Rebuild)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$env:PYTHONIOENCODING = 'utf-8'
$env:PYTHONPATH = Join-Path $PSScriptRoot 'src'
$python = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) {
    & uv sync --locked
    if ($LASTEXITCODE -ne 0) { throw 'Python dependency installation failed.' }
}
if ($Rebuild -or -not (Test-Path -LiteralPath (Join-Path $PSScriptRoot 'web\dist\index.html'))) {
    Push-Location (Join-Path $PSScriptRoot 'web')
    try {
        if (-not (Test-Path -LiteralPath 'node_modules')) {
            & npm.cmd ci
            if ($LASTEXITCODE -ne 0) { throw 'Frontend dependency installation failed.' }
        }
        & npm.cmd run build
        if ($LASTEXITCODE -ne 0) { throw 'Frontend build failed.' }
    } finally { Pop-Location }
}
Write-Host "Open http://127.0.0.1:$Port  (Ctrl+C to stop)"
& $python -m paper_agent.cli serve --host 127.0.0.1 --port $Port
if ($LASTEXITCODE -ne 0) { throw 'Web server exited with an error.' }
