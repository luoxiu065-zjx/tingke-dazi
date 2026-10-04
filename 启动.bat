@echo off
rem 听课搭子 (tingke-dazi) · Copyright (c) 2026 luoxiu065-zjx · https://github.com/luoxiu065-zjx/tingke-dazi · build tkdz-1bf2795e
chcp 65001 >nul
cd /d "%~dp0"
title 听课搭子
set PYTHONUTF8=1
set HF_HUB_DISABLE_SYMLINKS_WARNING=1
rem 启动前先关掉本目录还在跑的旧实例;多个实例挤在同一个端口时浏览器会连到旧的、卡死的那个。
powershell -NoProfile -Command "$d='%~dp0'; Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | Where-Object { $_.CommandLine -match 'app\.py' -and (($_.ExecutablePath -like ($d + '*')) -or ($_.CommandLine -like ('*' + $d + '*'))) } | ForEach-Object { Write-Host ('  关闭旧实例 pid ' + $_.ProcessId); Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }; Start-Sleep 1" 2>nul
powershell -NoProfile -Command "if (netstat -ano | Select-String ':5000 .*LISTENING') { Write-Host '  端口 5000 仍被占用,不启动。请先关掉另一个听课搭子窗口。'; exit 1 }"
if errorlevel 1 (
  pause
  exit /b 1
)
if exist python\python.exe (
  python\python.exe app.py
) else if exist venv\Scripts\python.exe (
  venv\Scripts\python.exe app.py
) else (
  echo 还没有安装,请先双击「安装.bat」。
)
pause
