@echo off
rem tingke-dazi (c) 2026 luoxiu065-zjx - https://github.com/luoxiu065-zjx/tingke-dazi - build tkdz-1bf2795e
chcp 65001 >nul
cd /d "%~dp0"
title tingke-dazi - install
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0_install.ps1"
if errorlevel 1 (
  pause
  exit /b 1
)
pause
exit /b 0
