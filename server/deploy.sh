#!/usr/bin/env bash
# fluxiaRSS 服务器端部署 + 逐源拉取测试（在 /opt/fluxiars 下运行）
set -e
DIR="/opt/fluxiars"
echo "==> 工作目录: $DIR"
cd "$DIR"
if [ ! -d .venv ]; then
  echo "==> 创建 venv"
  python3 -m venv .venv
fi
echo "==> 安装依赖"
.venv/bin/pip install -q -r requirements.txt
echo "==> 逐源拉取测试"
.venv/bin/python pull_test.py
