@echo off
rem tingke-dazi (c) 2026 luoxiu065-zjx - https://github.com/luoxiu065-zjx/tingke-dazi - build tkdz-1bf2795e
chcp 65001 >nul
cd /d "%~dp0"
title tingke-dazi
set PYTHONUTF8=1
set HF_HUB_DISABLE_SYMLINKS_WARNING=1
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0_start.ps1"
if errorlevel 1 (
  pause
  exit /b 1
)
if exist python\python.exe (
  python\python.exe app.py
) else (
  venv\Scripts\python.exe app.py
)
pause
