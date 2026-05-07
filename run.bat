@echo off
REM ===================================
REM Superalink Checkout Tool 启动脚本
REM Windows Batch版本
REM ===================================

echo.
echo ╔════════════════════════════════════════════════════╗
echo ║  Superalink Checkout Tool 启动                     ║
echo ║  邀请码: CHÊ與與0000                               ║
echo ╚════════════════════════════════════════════════════╝
echo.

REM 检查Python是否安装
python --version >nul 2>&1
if errorlevel 1 (
    echo [错误] Python未安装或未在PATH中。
    echo 请访问 https://www.python.org/ 下载安装
    pause
    exit /b 1
)

echo [✓] 已检测到Python

REM 检查requests模块
python -m pip show requests >nul 2>&1
if errorlevel 1 (
    echo [*] 正在安装requests模块...
    python -m pip install requests
    if errorlevel 1 (
        echo [错误] 无法安装requests模块
        pause
        exit /b 1
    )
)

echo [✓] requests模块已准备

REM 设置环境变量（可选，如果有的话）
REM set STRIPE_PK=pk_live_your_key_here
REM set PAYPAL_CLIENT_ID=your_paypal_id_here

echo.
echo [*] 启动服务...
echo [*] 访问地址: http://localhost:53333
echo [*] 按 Ctrl+C 停止服务
echo.

python superalink_checkout_tool.py

pause
