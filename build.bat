@echo off
REM Build script for dkp_client.exe
REM Requires PyInstaller to be installed: pip install pyinstaller

echo Building dkp_client.exe...
echo.

pyinstaller dkp_client.spec

if %ERRORLEVEL% NEQ 0 (
    echo.
    echo ERROR: Build failed. Ensure PyInstaller is installed (pip install pyinstaller).
    exit /b 1
)

echo.
echo ========================================
echo Build complete!
echo Output: dist\dkp_client.exe
echo ========================================
