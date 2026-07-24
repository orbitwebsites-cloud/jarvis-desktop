$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$pythonPath = Join-Path $projectRoot ".venv-desktop\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $pythonPath)) {
    Write-Host "Desktop runtime not installed. Running setup..." -ForegroundColor Yellow
    & (Join-Path $projectRoot "Setup-JARVIS-Desktop.ps1")
}

Set-Location -LiteralPath $projectRoot
& $pythonPath -m jarvis.desktop
