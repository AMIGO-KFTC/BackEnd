# Run the backend dev server on Windows (auto-reloads when BackEnd, AI or RAG code changes).
#   powershell -ExecutionPolicy Bypass -File scripts\dev.ps1            -> http://localhost:8000
#   powershell -ExecutionPolicy Bypass -File scripts\dev.ps1 -Port 8001
param([int]$Port = 8000)
$ErrorActionPreference = "Stop"

$backend = Split-Path -Parent $PSScriptRoot
$root = Split-Path -Parent $backend
Set-Location $backend

if ($env:OS -eq "Windows_NT") { $venvPython = Join-Path $backend ".venv\Scripts\python.exe" } else { $venvPython = Join-Path $backend ".venv/bin/python" }
if (-not (Test-Path $venvPython)) {
    Write-Host "[X] Virtual environment not found. Run scripts\setup.ps1 first." -ForegroundColor Red
    exit 1
}

$reload = @("--reload-dir", "app")
foreach ($repo in "AI", "RAG") {
    $src = Join-Path (Join-Path $root $repo) "src"
    if (Test-Path $src) { $reload += @("--reload-dir", $src) }
}

& $venvPython -m uvicorn app.main:app --reload --port $Port @reload
exit $LASTEXITCODE
