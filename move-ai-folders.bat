@echo off
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\move-ai-folders.ps1"
if errorlevel 1 echo The move did not complete. Read the errors above.
pause
