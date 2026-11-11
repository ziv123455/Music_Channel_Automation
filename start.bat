@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  where python >nul 2>nul
  if not errorlevel 1 (
    python -m venv .venv
  ) else (
    where py >nul 2>nul
    if errorlevel 1 (
      echo ERROR: Python was not found. Install Python and enable Add Python to PATH.
      pause
      exit /b 1
    )
    py -3 -m venv .venv
  )
  if errorlevel 1 (
    echo ERROR: Could not create the virtual environment.
    pause
    exit /b 1
  )
)
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 (
  echo ERROR: Dependency installation failed.
  pause
  exit /b 1
)
start "" http://127.0.0.1:5050
".venv\Scripts\python.exe" app.py
if errorlevel 1 echo ERROR: Music Channel Automation exited with an error.
pause
