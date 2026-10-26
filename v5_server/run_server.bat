@echo off
setlocal DisableDelayedExpansion
chcp 65001 >nul
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
title 深空之眼 · 离线单机服务
cd /d "%~dp0"
if errorlevel 1 exit /b 1
set "PYTHON_EXE=python"
if exist "..\runtime\python\python.exe" set "PYTHON_EXE=..\runtime\python\python.exe"
if exist "runtime\python\python.exe" set "PYTHON_EXE=runtime\python\python.exe"
"%PYTHON_EXE%" -X utf8 -B main.py %*
set "SERVER_EXIT_CODE=%ERRORLEVEL%"
if not "%SERVER_EXIT_CODE%"=="0" if "%~1"=="" pause
exit /b %SERVER_EXIT_CODE%
