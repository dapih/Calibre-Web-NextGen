@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\windows-start.ps1"
if errorlevel 1 (
    echo The application could not start. See the message above.
    pause
    exit /b 1
)
