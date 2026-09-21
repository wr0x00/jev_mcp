@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

rem ========== jev-mcp 一键启动（Windows） ==========

rem 1) 检查 Python
python --version >nul 2>nul
if errorlevel 1 (
  echo [x] 未检测到 Python，请先安装 Python 3.10+：https://www.python.org/downloads/
  pause & exit /b 1
)

rem 2) 首次运行：创建虚拟环境并安装依赖
if not exist ".venv\Scripts\python.exe" (
  echo [*] 首次运行：创建虚拟环境并安装依赖...
  python -m venv .venv
  if errorlevel 1 ( echo [x] 创建虚拟环境失败 & pause & exit /b 1 )
  ".venv\Scripts\python.exe" -m pip install -q -r requirements.txt
  if errorlevel 1 ( echo [x] 依赖安装失败，请检查网络后重试 & pause & exit /b 1 )
  echo [ok] 依赖安装完成
)

rem 3) 启动（Streamable HTTP 云端服务）
echo [*] 启动 jev_mcp：http://0.0.0.0:8800/mcp  （Ctrl+C 停止）
".venv\Scripts\python.exe" server.py
pause
