#!/usr/bin/env bash
# ========== jev-mcp 一键启动（Linux / macOS） ==========
set -e
cd "$(dirname "$0")"

# 1) 检查 Python
if ! command -v python3 >/dev/null 2>&1; then
  echo "[x] 未检测到 python3，请先安装 Python 3.10+"
  exit 1
fi

# 2) 首次运行：创建虚拟环境并安装依赖
if [ ! -x ".venv/bin/python" ]; then
  echo "[*] 首次运行：创建虚拟环境并安装依赖..."
  python3 -m venv .venv
  ./.venv/bin/pip install -q -r requirements.txt
  echo "[ok] 依赖安装完成"
fi

# 3) 启动（Streamable HTTP 云端服务）
echo "[*] 启动 jev_mcp：http://0.0.0.0:8800/mcp  （Ctrl+C 停止）"
exec ./.venv/bin/python server.py
