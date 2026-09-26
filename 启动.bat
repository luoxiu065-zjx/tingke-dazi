@echo off
rem 听课搭子 (tingke-dazi) · Copyright (c) 2026 luoxiu065-zjx · https://github.com/luoxiu065-zjx/tingke-dazi · build tkdz-1bf2795e
chcp 65001 >nul
cd /d "%~dp0"
title 听课搭子
set PYTHONUTF8=1
set HF_HUB_DISABLE_SYMLINKS_WARNING=1
if exist python\python.exe (
  python\python.exe app.py
) else if exist venv\Scripts\python.exe (
  venv\Scripts\python.exe app.py
) else (
  echo 还没有安装,请先双击「安装.bat」。
)
pause
