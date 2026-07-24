$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$buildScript = Join-Path $projectRoot "Build-JARVIS-Portable.ps1"
$artifact = Join-Path $projectRoot "dist-portable\JARVIS.exe"
$siteArtifact = Join-Path $projectRoot "site\JARVIS.exe"

& $buildScript
if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $artifact)) {
    throw "The JARVIS desktop build did not produce an executable."
}

Copy-Item -LiteralPath $artifact -Destination $siteArtifact -Force
$hash = (Get-FileHash -LiteralPath $siteArtifact -Algorithm SHA256).Hash.ToLowerInvariant()
$size = (Get-Item -LiteralPath $siteArtifact).Length

Write-Host ""
Write-Host "Release artifact copied to site\JARVIS.exe" -ForegroundColor Green
Write-Host "Size: $size bytes"
Write-Host "SHA256: $hash"
