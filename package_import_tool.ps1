# Package the DKP import tool into a zip for distribution
# Run from the bidtracker project directory

$outputZip = "dkp_import_tool.zip"
$files = @(
    "dkp_import.py",
    "dkptrackerv3.py",
    "dkp_logger.py",
    "seed_items.txt",
    "items.zip"
)

# Remove old zip if it exists
if (Test-Path $outputZip) {
    Remove-Item $outputZip
}

# Create the zip
Compress-Archive -Path $files -DestinationPath $outputZip

Write-Host ""
Write-Host "========================================" -ForegroundColor Green
Write-Host "Packaged: $outputZip" -ForegroundColor Green
Write-Host "Contents:" -ForegroundColor Cyan
$files | ForEach-Object { Write-Host "  - $_" }
Write-Host "========================================" -ForegroundColor Green
Write-Host ""
Write-Host "Tell the recipient:" -ForegroundColor Yellow
Write-Host "  1. Extract the zip to a folder"
Write-Host "  2. Install Python 3.10+ and run: pip install rapidfuzz"
Write-Host "  3. Run: python dkp_import.py path\to\their\eqlog_*.txt"
Write-Host "  4. It produces dkp_history.json - place it next to dkp_client.exe"
