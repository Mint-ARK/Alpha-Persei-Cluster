@echo off
chcp 65001 >nul
title 深空之眼 V5 - Windows 受信任根证书导入工具

:: 1. 检查管理员权限并自动提权
>nul 2>&1 "%SYSTEMROOT%\system32\cacls.exe" "%SYSTEMROOT%\system32\config\system"
if '%errorlevel%' NEQ '0' (
    echo [PROMPT] 正在请求管理员权限以导入证书至系统受信任根证书库...
    powershell -Command "Start-Process cmd.exe -ArgumentList '/c \"\"%~f0\"\"' -Verb runAs"
    exit /b
)

cd /d "%~dp0"
set "CERT_FILE=v5_server\sdk_ca.crt"
if not exist "%CERT_FILE%" (
    set "CERT_FILE=sdk_ca.crt"
)

if not exist "%CERT_FILE%" (
    echo [ERROR] 未找到根证书文件 sdk_ca.crt！
    echo 请确认当前目录下存在 v5_server\sdk_ca.crt 或 sdk_ca.crt
    pause
    exit /b 1
)

echo ==============================================================================
echo 🛡️  深空之眼 V5 根证书导入程序 (Windows PC)
echo ==============================================================================
echo.
echo [1/3] 正在清理旧版本过期证书...
certutil -delstore "Root" "open.ys4fun.com" >nul 2>&1
certutil -delstore "Root" "94044b6a3b131417f2ae623339ebba7ed0b8ef22" >nul 2>&1
certutil -delstore -user "Root" "open.ys4fun.com" >nul 2>&1

echo [2/3] 正在将最新 Root CA 根证书写入 LocalMachine\Root 存储...
certutil -addstore -f "Root" "%CERT_FILE%"
if %errorlevel% NEQ 0 (
    echo [WARN] LocalMachine 写入受阻，尝试写入 CurrentUser 存储...
    certutil -addstore -user -f "Root" "%CERT_FILE%"
)

echo.
echo [3/3] 正在验证证书链完整性...
certutil -store Root "AetherGazer V5 Root CA" >nul 2>&1
if %errorlevel% EQU 0 (
    echo ==============================================================================
    echo 🎉 [SUCCESS] Windows 侧受信任根证书安装完成！
    echo.
    echo ✅ 证书名称: AetherGazer V5 Root CA (open.ys4fun.com)
    echo ✅ 支持环境: Windows PC 客户端 / 浏览器 / iOS 局域网真机
    echo ✅ 覆盖域名: *.ys4fun.com, *.ys4fun.cn, open/skzy/download, 127.0.0.1
    echo ==============================================================================
) else (
    echo ⚠️ 证书安装已执行，请打开游戏或浏览器测试 https://open.ys4fun.com
)

echo.
pause
