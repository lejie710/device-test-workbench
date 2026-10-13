@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo The project environment is missing. Follow the setup instructions in README.md.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" "scripts\start_app.py" %*
if errorlevel 1 pause
