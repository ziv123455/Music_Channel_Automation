@echo off
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\auto-sync.ps1" -IntervalSeconds 30
pause
