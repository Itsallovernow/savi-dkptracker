# Build script for dkp_client.exe
# Requires PyInstaller: pip install pyinstaller

Write-Host "Building dkp_client.exe..." -ForegroundColor Cyan
Write-Host ""

pyinstaller dkp_client.spec

if ($LASTEXITCODE -ne 0) {
    Write-Host ""
    Write-Host "ERROR: Build failed. Ensure PyInstaller is installed (pip install pyinstaller)." -ForegroundColor Red
    exit 1
}

Write-Host ""
Write-Host "========================================" -ForegroundColor Green
Write-Host "Build complete!" -ForegroundColor Green
Write-Host "Output: dist\dkp_client.exe" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green
