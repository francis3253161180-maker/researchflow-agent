@echo off
setlocal EnableExtensions
cd /d "%~dp0"

set "PYTHON=.venv\Scripts\python.exe"
set "URL=http://127.0.0.1:8000"

if not exist "%PYTHON%" (
  echo ERROR: Project virtual environment was not found.
  echo Expected: .venv\Scripts\python.exe
  echo.
  echo Run these commands once from this folder:
  echo   python -m venv .venv
  echo   .\.venv\Scripts\python.exe -m pip install -e ".[dev]"
  echo.
  pause
  exit /b 1
)

netstat -ano | findstr /r /c:":8000 .*LISTENING" >nul
if not errorlevel 1 (
  echo ResearchFlow may already be running at %URL%
  start "" %URL%
  echo.
  pause
  exit /b 0
)

echo Starting ResearchFlow Agent...
echo The browser will open shortly: %URL%
echo To stop the server, press Ctrl+C in this window.
echo.
start "ResearchFlow Browser" powershell.exe -NoProfile -WindowStyle Hidden -Command "Start-Sleep -Seconds 2; Start-Process '%URL%'"
"%PYTHON%" -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload

set "EXIT_CODE=%ERRORLEVEL%"
echo.
echo ResearchFlow stopped with exit code %EXIT_CODE%.
pause
exit /b %EXIT_CODE%
