@echo off
cd /d "%~dp0"
python -m pip install -r requirements.txt
if errorlevel 1 (pause & exit /b 1)
where ffmpeg >nul 2>&1 || (echo Please install FFmpeg and add it to PATH.& pause & exit /b 1)
start "" "http://127.0.0.1:5050"
python app.py
pause
