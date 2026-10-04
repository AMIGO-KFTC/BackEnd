# AMIGO dev setup for Windows (PowerShell 5.1 or 7+). Run once.
#
#   Folder layout:  <workspace>\RAG, <workspace>\AI, <workspace>\BackEnd, <workspace>\FrontEnd
#   Usage:          cd BackEnd
#                   powershell -ExecutionPolicy Bypass -File scripts\setup.ps1
#                   (optional) -Python "C:\Python312\python.exe"
#
#   1) checks that RAG and AI are cloned next to BackEnd
#   2) creates BackEnd\.venv and installs RAG + AI (editable) and the backend packages
#   3) copies .env.example to .env if .env does not exist
#   4) runs "npm install" in ..\FrontEnd if it exists
#
# This file is ASCII only on purpose: Windows PowerShell 5.1 reads scripts without a BOM
# using the system code page, so non-ASCII text could break the script.
param([string]$Python = "")
$ErrorActionPreference = "Stop"

$backend = Split-Path -Parent $PSScriptRoot
$root = Split-Path -Parent $backend
Set-Location $backend

function Step($message) { Write-Host ""; Write-Host "> $message" -ForegroundColor Cyan }
function Fail($message) { Write-Host ""; Write-Host "[X] $message" -ForegroundColor Red; exit 1 }

# Runs a command quietly and returns its first output line, or $null when it fails.
# (Windows PowerShell 5.1 turns redirected stderr into errors, so relax error handling here.)
function Invoke-Quiet([string]$exe, [string[]]$arguments) {
    $previous = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $out = & $exe @arguments 2>$null
        if ($LASTEXITCODE -eq 0) { return "$(@($out)[0])".Trim() }
    } catch {
    } finally {
        $ErrorActionPreference = $previous
    }
    return $null
}

Step "Checking repositories in $root"
foreach ($repo in "RAG", "AI") {
    if (-not (Test-Path (Join-Path (Join-Path $root $repo) "pyproject.toml"))) {
        Fail "$repo is missing. In $root run:  git clone https://github.com/AMIGO-KFTC/$repo.git"
    }
    Write-Host "  [ok] $repo"
}
$frontend = Join-Path $root "FrontEnd"
$hasFrontend = Test-Path (Join-Path $frontend "package.json")
if ($hasFrontend) { Write-Host "  [ok] FrontEnd" } else { Write-Host "  [-] FrontEnd not found (API only)" }

Step "Finding Python (3.12 recommended, 3.11 - 3.13 supported)"
if ($Python) {
    $candidates = @(@{ Exe = $Python; Args = @() })
} else {
    $candidates = @(
        @{ Exe = "py"; Args = @("-3.12") },
        @{ Exe = "py"; Args = @("-3.13") },
        @{ Exe = "py"; Args = @("-3.11") },
        @{ Exe = "python3.12"; Args = @() },
        @{ Exe = "python3.13"; Args = @() },
        @{ Exe = "python3.11"; Args = @() },
        @{ Exe = "python"; Args = @() },
        @{ Exe = "python3"; Args = @() }
    )
}
$pythonExe = $null
$pythonArgs = @()
$version = $null
foreach ($candidate in $candidates) {
    if (-not (Get-Command $candidate.Exe -ErrorAction SilentlyContinue)) { continue }
    $found = Invoke-Quiet $candidate.Exe ($candidate.Args + @("-c", "import sys; print('%d.%d' % sys.version_info[:2])"))
    if ($found -notmatch '^\d+\.\d+$') { continue }
    if ([version]$found -ge [version]"3.10") {
        $pythonExe = $candidate.Exe
        $pythonArgs = $candidate.Args
        $version = $found
        break
    }
    Write-Host "  [-] $((@($candidate.Exe) + $candidate.Args) -join ' ') is Python $found (too old)"
}
if (-not $pythonExe) {
    Fail "Python 3.11 - 3.13 not found. Install it from https://www.python.org/downloads/ (check 'Add python.exe to PATH'), open a new terminal and run this again."
}
Write-Host "  [ok] $((@($pythonExe) + $pythonArgs) -join ' ') (Python $version)"
if ([version]$version -lt [version]"3.11" -or [version]$version -ge [version]"3.14") {
    Write-Host "  [!] Python $version is not tested (3.11 - 3.13 recommended). Continuing." -ForegroundColor Yellow
}

Step "Creating virtual environment BackEnd\.venv"
if ($env:OS -eq "Windows_NT") { $venvPython = Join-Path $backend ".venv\Scripts\python.exe" } else { $venvPython = Join-Path $backend ".venv/bin/python" }
if ((Test-Path $venvPython) -and -not (Invoke-Quiet $venvPython @("-m", "pip", "--version"))) {
    Write-Host "  [!] .venv is broken. Creating it again."
    Remove-Item -Recurse -Force (Join-Path $backend ".venv")
}
if (-not (Test-Path $venvPython)) {
    & $pythonExe @pythonArgs -m venv .venv
    if ($LASTEXITCODE -ne 0) { Fail "Could not create the virtual environment." }
}
& $venvPython -m pip install --upgrade pip --quiet
if ($LASTEXITCODE -ne 0) { Fail "pip upgrade failed. Check internet / proxy settings (see Troubleshooting in README)." }

Step "Installing Python packages (first time takes a few minutes, about 600MB)"
& $venvPython -m pip install -r requirements-dev.txt
if ($LASTEXITCODE -ne 0) { Fail "pip install failed. See Troubleshooting in README (proxy / mirror settings)." }

Step "Verifying installation"
& $venvPython -c "import amigo_rag, amigo_agent, app.main; print('  [ok] amigo_rag, amigo_agent, app')"
if ($LASTEXITCODE -ne 0) { Fail "Import check failed." }

if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
    Write-Host "  [ok] Created .env from .env.example (put ANTHROPIC_API_KEY there to use Claude)"
}

if ($hasFrontend) {
    Step "Installing frontend packages (npm install)"
    if (Get-Command npm -ErrorAction SilentlyContinue) {
        Push-Location $frontend
        try {
            & npm install --no-audit --no-fund
            if ($LASTEXITCODE -ne 0) { Fail "npm install failed." }
        } finally {
            Pop-Location
        }
    } else {
        Write-Host "  [!] npm not found. Install Node.js 22 LTS (or 20.19+) and run 'npm install' in FrontEnd." -ForegroundColor Yellow
    }
}

Write-Host ""
Write-Host "Done." -ForegroundColor Green
Write-Host "  Backend : powershell -ExecutionPolicy Bypass -File scripts\dev.ps1   -> http://localhost:8000/docs"
Write-Host "  Frontend: cd ..\FrontEnd; npm run dev                              -> http://localhost:5173"
Write-Host "  Both    : powershell -ExecutionPolicy Bypass -File scripts\start-all.ps1"
