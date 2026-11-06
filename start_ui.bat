@echo off
cd /d "%~dp0"
echo Starting CUPL grade alert UI...
echo.
python web\server.py
if errorlevel 1 (
  echo.
  echo Failed to start. Please check:
  echo   1. Python is installed and available in PATH
  echo   2. Dependencies: python -m pip install -r requirements.txt
  echo.
  pause
)
