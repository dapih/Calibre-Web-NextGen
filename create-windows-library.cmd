@echo off
"%~dp0.cwng-runtime\python\python.exe" -I -X utf8 "%~dp0scripts\windows-create-library.py" %*
if errorlevel 1 (
    echo Library creation did not finish. See the message above.
    pause
    exit /b 1
)
pause
