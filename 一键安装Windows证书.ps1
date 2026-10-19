# -*- coding: utf-8 -*-
# 深空之眼 V5 - Windows 受信任根证书一键安装脚本 (PowerShell)
param(
    [switch]$NonInteractive
)

$ErrorActionPreference = 'Stop'

# 1. 自动提权检查
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Write-Host "[PROMPT] 正在请求管理员权限以将证书安装至受信任根证书库..." -ForegroundColor Yellow
    # 隐藏的提权子进程不得停在 Pause；交互提示由当前窗口负责。
    $argList = "-NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`" -NonInteractive"
    try {
        $certInstallProcess = Start-Process powershell.exe -ArgumentList $argList -Verb runAs -WindowStyle Hidden -Wait -PassThru
        if ($certInstallProcess.ExitCode -eq 0) {
            Write-Host "[SUCCESS] 当前根证书已导入并完成指纹复核。" -ForegroundColor Green
        } else {
            Write-Host "[ERROR] 根证书安装未完成，请从启动壳重新检查证书状态。" -ForegroundColor Red
        }
        if (-not $NonInteractive) { Pause }
        Exit $certInstallProcess.ExitCode
    } catch {
        Write-Host "[ERROR] 未获得管理员授权，证书尚未导入。" -ForegroundColor Red
        Exit 1
    }
}

Set-Location $PSScriptRoot
$certPath = Join-Path $PSScriptRoot "v5_server\sdk_ca.crt"
if (-not (Test-Path $certPath)) {
    $certPath = Join-Path $PSScriptRoot "sdk_ca.crt"
}

if (-not (Test-Path $certPath)) {
    Write-Host "[ERROR] 未找到根证书文件 sdk_ca.crt！" -ForegroundColor Red
    if (-not $NonInteractive) { Pause }
    Exit 1
}

Write-Host "==============================================================================" -ForegroundColor Cyan
Write-Host "🛡️  深空之眼 V5 自动化根证书安装 (Windows PC 专用)" -ForegroundColor Cyan
Write-Host "==============================================================================" -ForegroundColor Cyan
Write-Host ""

try {
    Write-Host "[1/3] 正在读取并检查待安装根证书..." -ForegroundColor Gray
    $certToInstall = New-Object System.Security.Cryptography.X509Certificates.X509Certificate2($certPath)
    if ($certToInstall.NotBefore -gt (Get-Date) -or $certToInstall.NotAfter -le (Get-Date)) {
        throw '根证书尚未生效或已经过期，请先重新部署证书。'
    }
    $certConstraints = $certToInstall.Extensions | Where-Object { $_.Oid.Value -eq '2.5.29.19' }
    if (-not $certConstraints -or -not $certConstraints.CertificateAuthority) {
        throw '文件不是有效的 CA 根证书。'
    }
    $expectedThumbprint = $certToInstall.Thumbprint
    # 保留旧根，不按主题批量清理，避免在导入失败时破坏已有信任。
    Write-Host "[2/3] 正在导入当前根证书至系统与用户受信任存储区..." -ForegroundColor Gray
    Import-Certificate -FilePath $certPath -CertStoreLocation Cert:\LocalMachine\Root | Out-Null
    Import-Certificate -FilePath $certPath -CertStoreLocation Cert:\CurrentUser\Root | Out-Null
    Write-Host "[3/3] 正在核对本次根证书的精确指纹..." -ForegroundColor Gray
    $installedMachine = Get-ChildItem Cert:\LocalMachine\Root | Where-Object { $_.Thumbprint -eq $expectedThumbprint }
    $installedUser = Get-ChildItem Cert:\CurrentUser\Root | Where-Object { $_.Thumbprint -eq $expectedThumbprint }
    if (-not $installedMachine -or -not $installedUser) { throw '导入后根证书指纹复核失败。' }
    $installed = $installedMachine
} catch {
    Write-Host "[ERROR] 证书安装未完成: $($_.Exception.Message)" -ForegroundColor Red
    if (-not $NonInteractive) { Pause }
    Exit 1
}

if ($installed) {
    Write-Host "==============================================================================" -ForegroundColor Green
    Write-Host "🎉 [SUCCESS] Windows 侧受信任根证书安装完成！" -ForegroundColor Green
    Write-Host ""
    Write-Host "✅ 证书主题: $($installed.Subject)" -ForegroundColor Green
    Write-Host "✅ 证书指纹: $($installed.Thumbprint)" -ForegroundColor Green
    Write-Host "✅ 有效期限: $($installed.NotAfter)" -ForegroundColor Green
    Write-Host "✅ Windows 根信任已就绪；iOS 仍需自行安装描述文件并开启完全信任。" -ForegroundColor Green
    Write-Host "==============================================================================" -ForegroundColor Green
} else {
    Write-Host "⚠️ 证书已导入，请直接启动游戏客户端进行登录测试。" -ForegroundColor Yellow
}

Write-Host ""
if (-not $NonInteractive) {
    Pause
}
