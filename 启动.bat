@echo off
setlocal
cd /d "%~dp0"
title AI Novel Writer - Running (close this window to stop)

echo ==========================================================
echo           本地 AI 小说续写   -   正在启动
echo ==========================================================
echo.

set "ROOT=%~dp0"
set "VENV=%ROOT%backend\.venv\Scripts\python.exe"

if not exist "%VENV%" goto novenv
if not exist "%ROOT%.env" copy "%ROOT%.env.example" "%ROOT%.env" >nul 2>&1

echo [OK] 运行环境已就绪
echo [..] 正在启动服务，浏览器稍后自动打开
echo.
echo      网址: http://localhost:8000
echo      没自动打开的话，请手动把上面的网址输入浏览器
echo      停止服务: 关闭本窗口，或按 Ctrl + C
echo ==========================================================
echo.

start "" /min powershell -NoProfile -WindowStyle Hidden -Command "Start-Sleep -Seconds 6; Start-Process 'http://localhost:8000'"

cd /d "%ROOT%backend"
"%VENV%" -m uvicorn app.main:app --host 127.0.0.1 --port 8000

echo.
echo 服务已停止。
goto end

:novenv
echo [错误] 找不到运行环境:
echo        %VENV%
echo.
echo 请先看「使用说明.md」安装依赖，或在命令提示符执行:
echo        cd /d "%ROOT%backend"
echo        python -m venv .venv
echo        .venv\Scripts\python.exe -m pip install -r requirements.txt
goto end

:end
echo.
pause
