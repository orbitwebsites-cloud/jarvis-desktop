$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$pythonPath = Join-Path $projectRoot ".venv-desktop\Scripts\python.exe"
$outputPath = Join-Path $projectRoot "dist-portable"
$workPath = Join-Path $projectRoot "build-portable"
$specPath = Join-Path $projectRoot "build-portable-spec"
$staticPath = Join-Path $projectRoot "static"
$entryPath = Join-Path $projectRoot "jarvis_desktop_entry.py"

if (-not (Test-Path -LiteralPath $pythonPath)) {
    & (Join-Path $projectRoot "Setup-JARVIS-Desktop.ps1")
}

Set-Location -LiteralPath $projectRoot
& $pythonPath -m PyInstaller `
    --noconfirm `
    --clean `
    --onefile `
    --windowed `
    --name "JARVIS" `
    --distpath $outputPath `
    --workpath $workPath `
    --specpath $specPath `
    --add-data "$staticPath;static" `
    --hidden-import "webview.platforms.edgechromium" `
    $entryPath
if ($LASTEXITCODE -ne 0) {
    throw "The portable JARVIS build failed (exit code $LASTEXITCODE)."
}

Write-Host ""
Write-Host "Portable build created at dist-portable\JARVIS.exe" -ForegroundColor Green
$artifact = Join-Path $outputPath "JARVIS.exe"
$hash = (Get-FileHash -LiteralPath $artifact -Algorithm SHA256).Hash.ToLowerInvariant()
$size = (Get-Item -LiteralPath $artifact).Length
Write-Host "Size: $size bytes"
Write-Host "SHA256: $hash"
