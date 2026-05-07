# ===================================
# Superalink Checkout Tool 启动脚本
# Windows PowerShell 版本
# ===================================

Write-Host ""
Write-Host "╔════════════════════════════════════════════════════╗" -ForegroundColor Cyan
Write-Host "║  Superalink Checkout Tool 启动                     ║" -ForegroundColor Cyan
Write-Host "║  邀请码: CHÊ與與0000                               ║" -ForegroundColor Cyan
Write-Host "╚════════════════════════════════════════════════════╝" -ForegroundColor Cyan
Write-Host ""

# 检查Python
try {
    $pythonVersion = python --version 2>&1
    Write-Host "[✓] 已检测到Python: $pythonVersion" -ForegroundColor Green
} catch {
    Write-Host "[错误] Python未安装或未在PATH中" -ForegroundColor Red
    Write-Host "请访问 https://www.python.org/ 下载安装" -ForegroundColor Red
    Read-Host "按Enter键退出"
    exit 1
}

# 检查requests模块
$requestsCheck = python -c "import requests" 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Host "[*] 正在安装requests模块..." -ForegroundColor Yellow
    python -m pip install requests
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[错误] 无法安装requests模块" -ForegroundColor Red
        Read-Host "按Enter键退出"
        exit 1
    }
}

Write-Host "[✓] requests模块已准备" -ForegroundColor Green

# 设置环境变量（可选）
# $env:STRIPE_PK = 'pk_live_your_key_here'
# $env:PAYPAL_CLIENT_ID = 'your_paypal_id_here'

Write-Host ""
Write-Host "[*] 启动服务..." -ForegroundColor Yellow
Write-Host "[*] 访问地址: http://localhost:53333" -ForegroundColor Cyan
Write-Host "[*] 按 Ctrl+C 停止服务" -ForegroundColor Yellow
Write-Host ""

python superalink_checkout_tool.py

Read-Host "按Enter键关闭"
