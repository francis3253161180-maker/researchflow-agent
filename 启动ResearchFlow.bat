@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo [错误] 未找到项目虚拟环境：.venv\Scripts\python.exe
  echo 请先在项目根目录执行：python -m venv .venv
  echo 然后执行：.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
  pause
  exit /b 1
)

echo 正在启动 ResearchFlow Agent...
echo 浏览器将在服务就绪后自动打开：http://127.0.0.1:8000
echo 停止服务请在此窗口按 Ctrl+C。
start "ResearchFlow Browser" powershell.exe -NoProfile -WindowStyle Hidden -Command "Start-Sleep -Seconds 2; Start-Process 'http://127.0.0.1:8000'"
.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload

echo.
echo 服务已停止。
pause
