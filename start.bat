@echo off
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
  py -3 -m venv .venv || exit /b 1
)
.venv\Scripts\python.exe -m pip install -r requirements.txt || exit /b 1
start "" http://127.0.0.1:5050
.venv\Scripts\python.exe app.py
pause
