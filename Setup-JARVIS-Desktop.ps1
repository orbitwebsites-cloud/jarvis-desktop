$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$venvPath = Join-Path $projectRoot ".venv-desktop"
$pythonPath = Join-Path $venvPath "Scripts\python.exe"

Set-Location -LiteralPath $projectRoot

if (-not (Get-Command py -ErrorAction SilentlyContinue)) {
    throw "The Python launcher is required. Install Python 3.12 or newer, then try again."
}

if (-not (Test-Path -LiteralPath $pythonPath)) {
    Write-Host "Creating the JARVIS desktop environment..." -ForegroundColor Cyan
    py -3.12 -m venv $venvPath
    if ($LASTEXITCODE -ne 0) {
        py -3 -m venv $venvPath
    }
}

& $pythonPath -m pip install --upgrade pip
& $pythonPath -m pip install -r (Join-Path $projectRoot "requirements-desktop.txt")

Write-Host ""
Write-Host "JARVIS Desktop is ready." -ForegroundColor Green
Write-Host "Launch it with Start-JARVIS-Desktop.cmd"
