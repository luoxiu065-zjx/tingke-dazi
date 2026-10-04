@echo off
rem 听课搭子 (tingke-dazi) · Copyright (c) 2026 luoxiu065-zjx · https://github.com/luoxiu065-zjx/tingke-dazi · build tkdz-1bf2795e
chcp 65001 >nul
cd /d "%~dp0"
title 听课搭子 - 安装
set PYVER=3.12.10

if not exist python\python.exe (
  echo [1/4] 下载 Python %PYVER% 嵌入版,约 11MB,只放在本文件夹里,不影响电脑上其他软件...
  powershell -NoProfile -ExecutionPolicy Bypass -Command "$ProgressPreference='SilentlyContinue'; Invoke-WebRequest 'https://www.python.org/ftp/python/%PYVER%/python-%PYVER%-embed-amd64.zip' -OutFile 'py.zip'; Expand-Archive 'py.zip' -DestinationPath 'python' -Force; Remove-Item 'py.zip'"
  if not exist python\python.exe goto fail
  powershell -NoProfile -ExecutionPolicy Bypass -Command "(Get-Content 'python\python312._pth') -replace '^#import site','import site' | Set-Content 'python\python312._pth'"
)

if not exist python\Scripts\pip.exe (
  echo [2/4] 安装 pip...
  powershell -NoProfile -ExecutionPolicy Bypass -Command "$ProgressPreference='SilentlyContinue'; Invoke-WebRequest 'https://bootstrap.pypa.io/get-pip.py' -OutFile 'get-pip.py'"
  python\python.exe get-pip.py --no-warn-script-location
  if errorlevel 1 goto fail
  del get-pip.py
)

echo [3/4] 安装依赖,第一次需要几分钟...
python\python.exe -m pip install --no-warn-script-location -r requirements.txt
if errorlevel 1 goto fail

where nvidia-smi >nul 2>nul
if %errorlevel%==0 (
  echo       检测到 NVIDIA 显卡,安装显卡加速库,约 1GB...
  python\python.exe -m pip install --no-warn-script-location -r requirements-gpu.txt
  if errorlevel 1 goto fail
) else (
  echo       没有检测到 NVIDIA 显卡,将使用 CPU 模式,速度慢一些、准确率低一些。
)

if not exist .env copy .env.example .env >nul
echo.
echo [4/4] 安装完成!
echo 下一步:双击「启动.bat」,在网页左下角「设置」里贴上你的 DeepSeek Key,点「测试连接」通了就保存。
echo 第一次启动会自动下载语音模型,约 1.6GB,需要等一会儿。
pause
exit /b 0

:fail
echo.
echo 安装没有成功。请把上面的报错截图,发到 GitHub Issues。
pause
exit /b 1
