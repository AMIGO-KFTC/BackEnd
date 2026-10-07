# Start the backend (port 8000) and the frontend (port 5173) in two new PowerShell windows (Windows).
#   powershell -ExecutionPolicy Bypass -File scripts\start-all.ps1
#   powershell -ExecutionPolicy Bypass -File scripts\start-all.ps1 -Port 8001
# Press Ctrl+C in a window (or close it) to stop that server.
param([int]$Port = 8000)
$ErrorActionPreference = "Stop"

$backend = Split-Path -Parent $PSScriptRoot
$root = Split-Path -Parent $backend
$frontend = Join-Path $root "FrontEnd"

if (-not (Test-Path (Join-Path $frontend "node_modules"))) {
    Write-Host "[X] FrontEnd packages are missing. Clone FrontEnd next to BackEnd and run scripts\setup.ps1 again." -ForegroundColor Red
    exit 1
}

if (Get-Command pwsh -ErrorAction SilentlyContinue) { $shell = "pwsh" } else { $shell = "powershell" }
$devScript = Join-Path $PSScriptRoot "dev.ps1"

Start-Process $shell -WorkingDirectory $backend -ArgumentList "-NoExit -ExecutionPolicy Bypass -File `"$devScript`" -Port $Port"

Write-Host "Waiting for the backend..."
for ($i = 0; $i -lt 30; $i++) {
    try {
        Invoke-RestMethod -Uri "http://127.0.0.1:$Port/api/health" -TimeoutSec 2 | Out-Null
        break
    } catch {
        Start-Sleep -Seconds 1
    }
}

# The frontend window inherits AMIGO_API from this process, so the dev proxy points at the backend port.
$previousApi = $env:AMIGO_API
$env:AMIGO_API = "http://localhost:$Port"
try {
    Start-Process $shell -WorkingDirectory $frontend -ArgumentList "-NoExit -ExecutionPolicy Bypass -Command npm run dev"
} finally {
    $env:AMIGO_API = $previousApi
}

Write-Host ""
Write-Host "  Screen  : http://localhost:5173"
Write-Host "  API docs: http://localhost:$Port/docs"
