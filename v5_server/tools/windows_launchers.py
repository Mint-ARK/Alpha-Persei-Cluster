"""Shared UTF-8 CMD entry points for source and Windows distributions."""
from pathlib import Path


def console_launcher(server_subdir=""):
    return f'''@echo off
setlocal DisableDelayedExpansion
chcp 65001 >nul
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
title 深空之眼 · 离线单机服务
cd /d "%~dp0{server_subdir}"
if errorlevel 1 exit /b 1
set "PYTHON_EXE=python"
if exist "..\\runtime\\python\\python.exe" set "PYTHON_EXE=..\\runtime\\python\\python.exe"
if exist "runtime\\python\\python.exe" set "PYTHON_EXE=runtime\\python\\python.exe"
"%PYTHON_EXE%" -X utf8 -B main.py %*
set "SERVER_EXIT_CODE=%ERRORLEVEL%"
if not "%SERVER_EXIT_CODE%"=="0" if "%~1"=="" pause
exit /b %SERVER_EXIT_CODE%
'''


def write_console_launcher(path, server_subdir=""):
    # No BOM before @echo off; CRLF is shared by generated and source scripts.
    Path(path).write_text(console_launcher(server_subdir), encoding="utf-8", newline="\r\n")
