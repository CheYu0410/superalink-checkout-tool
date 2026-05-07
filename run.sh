#!/bin/bash
# ===================================
# Superalink Checkout Tool 启动脚本
# Linux/macOS 版本
# ===================================

echo ""
echo "╔════════════════════════════════════════════════════╗"
echo "║  Superalink Checkout Tool 启动                     ║"
echo "║  邀请码: CHÊ與與0000                               ║"
echo "╚════════════════════════════════════════════════════╝"
echo ""

# 检查Python版本
if ! command -v python3 &> /dev/null; then
    echo "[错误] Python3未安装"
    echo "请运行: brew install python3 (macOS) 或 apt install python3 (Linux)"
    exit 1
fi

PYTHON_VERSION=$(python3 --version | awk '{print $2}')
echo "[✓] 已检测到Python: $PYTHON_VERSION"

# 检查requests模块
python3 -c "import requests" 2>/dev/null
if [ $? -ne 0 ]; then
    echo "[*] 正在安装requests模块..."
    pip3 install requests
    if [ $? -ne 0 ]; then
        echo "[错误] 无法安装requests模块"
        exit 1
    fi
fi

echo "[✓] requests模块已准备"

# 设置环境变量（可选）
# export STRIPE_PK='pk_live_your_key_here'
# export PAYPAL_CLIENT_ID='your_paypal_id_here'

echo ""
echo "[*] 启动服务..."
echo "[*] 访问地址: http://localhost:53333"
echo "[*] 按 Ctrl+C 停止服务"
echo ""

python3 superalink_checkout_tool.py
