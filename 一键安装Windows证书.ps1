# -*- coding: utf-8 -*-
# 深空之眼 V5 - Windows 受信任根证书一键安装脚本 (PowerShell)

# 1. 自动提权检查
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Write-Host "[PROMPT] 正在请求管理员权限以将证书安装至受信任根证书库..." -ForegroundColor Yellow
    Start-Process powershell.exe -ArgumentList "-NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`"" -Verb runAs
    Exit
}

Set-Location $PSScriptRoot
$certPath = Join-Path $PSScriptRoot "v5_server\sdk_ca.crt"
if (-not (Test-Path $certPath)) {
    $certPath = Join-Path $PSScriptRoot "sdk_ca.crt"
}

if (-not (Test-Path $certPath)) {
    Write-Host "[ERROR] 未找到根证书文件 sdk_ca.crt！" -ForegroundColor Red
    Pause
    Exit 1
}

Write-Host "==============================================================================" -ForegroundColor Cyan
Write-Host "🛡️  深空之眼 V5 自动化根证书安装 (Windows PC 专用)" -ForegroundColor Cyan
Write-Host "==============================================================================" -ForegroundColor Cyan
Write-Host ""

Write-Host "[1/3] 正在清理旧版本过期证书..." -ForegroundColor Gray
certutil -delstore "Root" "open.ys4fun.com" | Out-Null
certutil -delstore "Root" "94044b6a3b131417f2ae623339ebba7ed0b8ef22" | Out-Null
certutil -delstore -user "Root" "open.ys4fun.com" | Out-Null

Write-Host "[2/3] 正在导入最新 Root CA 根证书至 LocalMachine\Root..." -ForegroundColor Gray
Import-Certificate -FilePath $certPath -CertStoreLocation Cert:\LocalMachine\Root | Out-Null

Write-Host "[3/3] 正在验证证书完整性..." -ForegroundColor Gray
$installed = Get-ChildItem Cert:\LocalMachine\Root | Where-Object { $_.Subject -like "*AetherGazer V5 Root CA*" }

if ($installed) {
    Write-Host "==============================================================================" -ForegroundColor Green
    Write-Host "🎉 [SUCCESS] Windows 侧受信任根证书安装完成！" -ForegroundColor Green
    Write-Host ""
    Write-Host "✅ 证书主题: $($installed.Subject)" -ForegroundColor Green
    Write-Host "✅ 证书指纹: $($installed.Thumbprint)" -ForegroundColor Green
    Write-Host "✅ 有效期限: $($installed.NotAfter)" -ForegroundColor Green
    Write-Host "✅ 支持环境: Windows PC 客户端 / 浏览器 / iOS 局域网真机" -ForegroundColor Green
    Write-Host "==============================================================================" -ForegroundColor Green
} else {
    Write-Host "⚠️ 证书已导入，请直接启动游戏客户端进行登录测试。" -ForegroundColor Yellow
}

Write-Host ""
Pause
