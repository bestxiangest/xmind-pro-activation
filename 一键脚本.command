#!/bin/bash
# macOS 版一键脚本（Windows 版一键脚本.bat 的移植）
# 用法：终端执行 ./一键脚本.command，或直接在 Finder 双击运行
set -e
cd "$(dirname "$0")"

echo "[1/3] 创建虚拟环境..."
python3 -m venv venv

echo "[2/3] 安装依赖..."
venv/bin/pip install -r requirements.txt

echo "[3/3] 运行补丁脚本（需要 Xmind 已安装在 /Applications/Xmind.app）..."
venv/bin/python xmind.py

echo
echo "完成。如果 Xmind 正在运行，请先退出再重新打开。"
read -r -p "按回车键退出..." _