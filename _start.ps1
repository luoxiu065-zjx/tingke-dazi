# 听课搭子 (tingke-dazi) · 启动前检查 · Copyright (c) 2026 luoxiu065-zjx · build tkdz-1bf2795e
# 由 启动.bat 调用:关掉本目录还在跑的旧实例;端口被别的程序占着就不启动;没装就提示。
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch {}
$d = $PSScriptRoot
# 多个实例挤在同一个端口时浏览器会连到旧的、卡死的那个,先清掉本目录的旧实例
Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
  Where-Object { $_.CommandLine -match "app\.py" -and $_.ExecutablePath -like ($d + "*") } |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
Start-Sleep -Milliseconds 600
$port = 5000
$envf = Join-Path $d ".env"
if (Test-Path $envf) {
  $m = Select-String -Path $envf -Pattern "^PORT=(\d+)"
  if ($m) { $port = [int]$m.Matches[0].Groups[1].Value }
}
if (netstat -ano | Select-String (":$port .*LISTENING")) {
  Write-Host "  端口 $port 被占用,不启动。请先关掉另一个听课搭子窗口;或者在 .env 里把 PORT 改成别的数字(如 5001)。"
  exit 1
}
if (!(Test-Path (Join-Path $d "python\python.exe")) -and !(Test-Path (Join-Path $d "venv\Scripts\python.exe"))) {
  Write-Host "还没有安装,请先双击「安装.bat」。"
  exit 1
}
Write-Host "启动中... 浏览器会打开 http://127.0.0.1:$port ;第一次要下载语音模型,请等几分钟。关掉这个窗口就停止。"
exit 0
