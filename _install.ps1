# 听课搭子 (tingke-dazi) · 安装脚本 · Copyright (c) 2026 luoxiu065-zjx · build tkdz-1bf2795e
# 由 安装.bat 调用。做的事:下载一个只放在本文件夹里的 Python 嵌入版 → 装 pip → 装依赖 → 有 NVIDIA 显卡再装显卡库 → 复制 .env
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch {}
Set-Location $PSScriptRoot
$PYVER = "3.12.10"

function Fail($what) {
  Write-Host ""
  Write-Host "安装没有成功(卡在:$what)。请把上面的报错截图,发到 GitHub Issues:https://github.com/luoxiu065-zjx/tingke-dazi/issues"
  exit 1
}

try {
  if (!(Test-Path "python\python.exe")) {
    Write-Host "[1/4] 下载 Python $PYVER 嵌入版,约 11MB,只放在本文件夹里,不影响电脑上其他软件..."
    Invoke-WebRequest "https://www.python.org/ftp/python/$PYVER/python-$PYVER-embed-amd64.zip" -OutFile "py.zip"
    Expand-Archive "py.zip" -DestinationPath "python" -Force
    Remove-Item "py.zip"
    if (!(Test-Path "python\python.exe")) { Fail "下载 Python" }
    (Get-Content "python\python312._pth") -replace "^#import site", "import site" | Set-Content "python\python312._pth"
  }
  # 嵌入版 Python 的 ._pth 不会把程序目录放进搜索路径,app.py 会找不到旁边的 session.py 等模块;加一行 ".."(= 程序目录)
  if (!(Select-String -Path "python\python312._pth" -Pattern "^\.\.$" -Quiet)) { Add-Content "python\python312._pth" ".." }
  if (!(Test-Path "python\Scripts\pip.exe")) {
    Write-Host "[2/4] 安装 pip..."
    Invoke-WebRequest "https://bootstrap.pypa.io/get-pip.py" -OutFile "get-pip.py"
    & "python\python.exe" get-pip.py --no-warn-script-location
    if ($LASTEXITCODE -ne 0) { Fail "安装 pip" }
    Remove-Item "get-pip.py"
  }
  Write-Host "[3/4] 安装依赖,第一次需要几分钟(约 300MB)..."
  & "python\python.exe" -m pip install --no-warn-script-location -r requirements.txt
  if ($LASTEXITCODE -ne 0) { Fail "安装依赖" }
  if (Get-Command nvidia-smi -ErrorAction SilentlyContinue) {
    Write-Host "      检测到 NVIDIA 显卡,安装显卡加速库,约 1.4GB..."
    & "python\python.exe" -m pip install --no-warn-script-location -r requirements-gpu.txt
    if ($LASTEXITCODE -ne 0) { Fail "安装显卡加速库" }
  } else {
    Write-Host "      没有检测到 NVIDIA 显卡,将使用 CPU 模式,速度慢一些、准确率低一些。"
  }
  if (!(Test-Path ".env")) { Copy-Item ".env.example" ".env" }
} catch {
  Write-Host $_.Exception.Message
  Fail "出错"
}

Write-Host ""
Write-Host "[4/4] 安装完成!"
Write-Host "下一步:双击「启动.bat」。网页上会出现「开始前三步」,照着点就行(填 key、改课程、选声音来源)。"
Write-Host "第一次启动会自动下载语音模型(约 1.6GB),需要等几分钟。"
exit 0
