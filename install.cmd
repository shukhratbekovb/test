@echo off
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0deploy\install-windows.ps1" %*
if errorlevel 1 (
    echo.
    echo Installation failed. Read the error above before retrying.
    pause
    exit /b 1
)
echo.
pause
