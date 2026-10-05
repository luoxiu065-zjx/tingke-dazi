@echo off
rem tingke-dazi (c) 2026 luoxiu065-zjx - build tkdz-1bf2795e
chcp 65001 >nul
cd /d "%~dp0"
title tingke-dazi - setup Obsidian
set PYTHONUTF8=1
if exist python\python.exe (
  python\python.exe setup_obsidian.py %*
) else if exist venv\Scripts\python.exe (
  venv\Scripts\python.exe setup_obsidian.py %*
) else (
  echo Please run install first.
)
pause
