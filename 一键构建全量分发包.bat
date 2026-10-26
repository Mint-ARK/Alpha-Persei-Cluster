@echo off
chcp 65001 >nul
title 深空之眼 · 全量发布打包工具 (绿色便携版 + Inno Setup安装包)
cd /d "%~dp0"

echo ======================================================================
echo    深空之眼 · 全量发布打包流水线 (绿色免安装版 + Inno Setup安装包)
echo ======================================================================
echo.

python v5_server\tools\build_all_distributions.py
if errorlevel 1 (
    echo.
    echo [错误] 打包流水线执行异常，请检查上方日志。
    pause
    exit /b 1
)

echo.
pause
