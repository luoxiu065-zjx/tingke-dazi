// 听课搭子 桌面版(Electron 壳)
// 做的事:单实例 → 把 Python 程序复制到 %LOCALAPPDATA%\tingke-dazi\app → 没装就跑 _install.ps1 →
// 清掉旧实例、查端口 → 拉起 python app.py --no-browser → 等网页就绪 → 在自己的窗口里显示。
// Python 代码一行不改;关窗口 = 退出 = 杀掉后台 python。
'use strict';
const { app, BrowserWindow, Tray, Menu, shell, dialog, ipcMain, nativeImage } = require('electron');
const path = require('path');
const fs = require('fs');
const net = require('net');
const http = require('http');
const { spawn, execFile } = require('child_process');

const PRODUCT = '听课搭子';
const HOME = path.join(process.env.LOCALAPPDATA || app.getPath('appData'), 'tingke-dazi');
const APP_DIR = path.join(HOME, 'app');
const LOG_DIR = path.join(APP_DIR, 'logs');
const SRC_DIR = app.isPackaged ? path.join(process.resourcesPath, 'app-src') : path.join(__dirname, '..');
const VERSION_FILE = path.join(APP_DIR, '.desktop-version');
const ICON = path.join(__dirname, 'build', 'icon.png');
// 开发模式下 SRC_DIR 是整个仓库,这些不复制(打包版的 app-src 本来就只含程序文件)
const SKIP = new Set(['electron', 'phone', 'tests', 'python', 'venv', '.git', '__pycache__', 'logs', 'session', 'models',
  '课堂记录', '课堂记录备份', '课件', '问答', '待上传', 'node_modules']);

let splash = null, win = null, tray = null, py = null, logStream = null, quitting = false;

// ── 单实例 ──────────────────────────────────────────────────────────────
if (!app.requestSingleInstanceLock()) {
  app.quit();
} else {
  app.on('second-instance', () => { const w = win || splash; if (w) { if (w.isMinimized()) w.restore(); w.show(); w.focus(); } });
  app.whenReady().then(main).catch((e) => fatal('启动失败', String(e && e.stack || e)));
}

// ── 小工具 ──────────────────────────────────────────────────────────────
function send(ch, ...args) { if (splash && !splash.isDestroyed()) splash.webContents.send(ch, ...args); }
function status(t, sub) { send('status', t, sub); }
function logLine(line) {
  send('log', line);
  try { logStream && logStream.write(line); } catch {}
}
function fatal(title, detail) {
  logLine(`\n[桌面版] ${title}\n${detail}\n`);
  if (splash && !splash.isDestroyed()) { send('error', title, detail.split('\n')[0]); splash.show(); }
  else dialog.showErrorBox(title, detail);
}
function readPort() {
  try {
    const m = fs.readFileSync(path.join(APP_DIR, '.env'), 'utf8').match(/^PORT=(\d+)/m);
    if (m) return parseInt(m[1], 10);
  } catch {}
  return 5000;
}
function portBusy(port) {
  return new Promise((resolve) => {
    const s = net.createConnection({ host: '127.0.0.1', port });
    s.setTimeout(500);
    s.once('connect', () => { s.destroy(); resolve(true); });
    s.once('error', () => resolve(false));
    s.once('timeout', () => { s.destroy(); resolve(false); });
  });
}
function httpOk(port) {
  return new Promise((resolve) => {
    const req = http.get({ host: '127.0.0.1', port, path: '/', timeout: 1500 }, (res) => { res.resume(); resolve(res.statusCode > 0); });
    req.on('error', () => resolve(false));
    req.on('timeout', () => { req.destroy(); resolve(false); });
  });
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
function ps(script) {
  return new Promise((resolve) => {
    execFile('powershell.exe', ['-NoProfile', '-ExecutionPolicy', 'Bypass', '-Command', script], { windowsHide: true }, (err, out, errOut) => resolve({ err, out: String(out || ''), errOut: String(errOut || '') }));
  });
}

// ── 复制程序文件到可写目录 ─────────────────────────────────────────────
function copyTree(src, dst) {
  for (const name of fs.readdirSync(src)) {
    if (SKIP.has(name)) continue;
    const s = path.join(src, name), d = path.join(dst, name);
    const st = fs.statSync(s);
    if (st.isDirectory()) { fs.mkdirSync(d, { recursive: true }); copyTree(s, d); }
    else if (!app.isPackaged && /^_(?!install\.ps1$)/.test(name)) continue; // 开发模式跳过 _xxx.py 之类
    else fs.copyFileSync(s, d);
  }
}
function syncSources() {
  fs.mkdirSync(APP_DIR, { recursive: true });
  let have = '';
  try { have = fs.readFileSync(VERSION_FILE, 'utf8').trim(); } catch {}
  const want = app.getVersion();
  if (app.isPackaged && have === want && fs.existsSync(path.join(APP_DIR, 'app.py'))) return;
  status('正在准备程序文件…');
  copyTree(SRC_DIR, APP_DIR);
  if (!fs.existsSync(path.join(APP_DIR, '.env')) && fs.existsSync(path.join(APP_DIR, '.env.example'))) {
    fs.copyFileSync(path.join(APP_DIR, '.env.example'), path.join(APP_DIR, '.env'));
  }
  fs.writeFileSync(VERSION_FILE, want);
}

// ── 第一次:装 Python 和依赖(复用仓库里的 _install.ps1) ─────────────────
function runInstall() {
  return new Promise((resolve) => {
    status('第一次使用:正在安装 Python 和依赖(约 2GB,几分钟到十几分钟)', '只装在本程序自己的文件夹里,不影响电脑上其他软件。装完会自动继续。');
    const child = spawn('powershell.exe', ['-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', path.join(APP_DIR, '_install.ps1')],
      { cwd: APP_DIR, windowsHide: true, env: { ...process.env, PYTHONUTF8: '1', PYTHONIOENCODING: 'utf-8' } });
    child.stdout.on('data', (b) => logLine(b.toString('utf8')));
    child.stderr.on('data', (b) => logLine(b.toString('utf8')));
    child.on('exit', (code) => resolve(code === 0));
    child.on('error', (e) => { logLine(String(e)); resolve(false); });
  });
}

// ── 清掉本目录的旧实例(同 _start.ps1) ──────────────────────────────────
async function killStale() {
  const dir = APP_DIR.replace(/'/g, "''");
  await ps(`Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue | Where-Object { $_.CommandLine -match 'app\\.py' -and $_.ExecutablePath -like ('${dir}' + '*') } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }`);
  await sleep(600);
}

// ── 拉起后台 python ────────────────────────────────────────────────────
function startPython() {
  const exe = path.join(APP_DIR, 'python', 'python.exe');
  py = spawn(exe, ['app.py', '--no-browser'], {
    cwd: APP_DIR, windowsHide: true,
    env: { ...process.env, PYTHONUTF8: '1', PYTHONIOENCODING: 'utf-8', HF_HUB_DISABLE_SYMLINKS_WARNING: '1' },
  });
  py.stdout.on('data', (b) => logLine(b.toString('utf8')));
  py.stderr.on('data', (b) => logLine(b.toString('utf8')));
  py.on('exit', (code) => {
    const was = py; py = null;
    if (quitting) return;
    fatal('后台识别程序退出了(代码 ' + code + ')', '日志在 ' + LOG_DIR + ' ,把最新那份发到 GitHub Issues 就能查。');
    if (win && !win.isDestroyed()) win.close();
    void was;
  });
}
function killPython() {
  if (!py) return;
  const pid = py.pid; py = null;
  try { execFile('taskkill', ['/PID', String(pid), '/T', '/F'], { windowsHide: true }, () => {}); } catch {}
}

// ── 窗口和托盘 ─────────────────────────────────────────────────────────
function makeSplash() {
  splash = new BrowserWindow({ width: 720, height: 520, resizable: false, title: PRODUCT, icon: ICON, autoHideMenuBar: true,
    webPreferences: { nodeIntegration: true, contextIsolation: false } });
  splash.loadFile(path.join(__dirname, 'splash.html'));
  splash.on('closed', () => { splash = null; if (!win) app.quit(); });
}
function makeMain(url) {
  win = new BrowserWindow({ width: 1320, height: 860, minWidth: 900, minHeight: 600, title: PRODUCT, icon: ICON, autoHideMenuBar: true, show: false });
  win.webContents.setWindowOpenHandler(({ url: u }) => { if (/^https?:/.test(u) && !u.startsWith(url)) { shell.openExternal(u); return { action: 'deny' }; } return { action: 'allow' }; });
  win.webContents.on('will-navigate', (e, u) => { if (!u.startsWith(url)) { e.preventDefault(); shell.openExternal(u); } });
  win.webContents.on('before-input-event', (e, input) => {
    if (input.type !== 'keyDown') return;
    if (input.key === 'F5') { win.webContents.reload(); e.preventDefault(); }
    if (input.key === 'F12') { win.webContents.toggleDevTools(); e.preventDefault(); }
  });
  win.once('ready-to-show', () => { win.show(); if (splash && !splash.isDestroyed()) { const s = splash; splash = null; s.destroy(); } });
  win.on('closed', () => { win = null; app.quit(); });
  win.loadURL(url);
}
function makeTray(url) {
  try {
    tray = new Tray(nativeImage.createFromPath(ICON).resize({ width: 16, height: 16 }));
    tray.setToolTip(PRODUCT + ' · ' + url);
    tray.setContextMenu(Menu.buildFromTemplate([
      { label: '显示窗口', click: () => { if (win) { win.show(); win.focus(); } } },
      { label: '在浏览器里打开', click: () => shell.openExternal(url) },
      { type: 'separator' },
      { label: '打开程序文件夹(.env、课堂记录)', click: () => shell.openPath(APP_DIR) },
      { label: '打开日志文件夹', click: () => shell.openPath(LOG_DIR) },
      { type: 'separator' },
      { label: '退出', click: () => app.quit() },
    ]));
    tray.on('click', () => { if (win) { win.show(); win.focus(); } });
  } catch {}
}

// ── 主流程 ─────────────────────────────────────────────────────────────
async function main() {
  Menu.setApplicationMenu(null);
  makeSplash();
  await sleep(300);
  try { syncSources(); } catch (e) { return fatal('复制程序文件失败', String(e)); }
  fs.mkdirSync(LOG_DIR, { recursive: true });
  const stamp = new Date().toISOString().slice(0, 10).replace(/-/g, '');
  logStream = fs.createWriteStream(path.join(LOG_DIR, `desktop-${stamp}.log`), { flags: 'a' });
  logLine(`\n===== ${new Date().toLocaleString()} 桌面版 v${app.getVersion()} 启动 =====\n程序目录 ${APP_DIR}\n`);

  if (!fs.existsSync(path.join(APP_DIR, 'python', 'python.exe'))) {
    const ok = await runInstall();
    if (!ok) return fatal('安装没有成功', '上面的黑字是安装日志,截图发到 GitHub Issues;或者关掉本窗口重新打开,已下载的部分不会重下。');
  }

  status('正在清理旧实例…');
  await killStale();
  const port = readPort();
  if (await portBusy(port)) {
    return fatal(`端口 ${port} 被别的程序占着,无法启动`, '关掉另一个听课搭子窗口;或者在程序文件夹的 .env 里把 PORT 改成别的数字(如 5001)再打开。');
  }

  status('正在启动后台识别程序…', '第一次要下载语音模型(约 1.6GB),下面会显示进度,请等几分钟。');
  startPython();
  const url = `http://127.0.0.1:${port}`;
  const deadline = Date.now() + 40 * 60 * 1000;
  while (Date.now() < deadline) {
    if (!py) return; // 已经在 exit 里报错了
    if (await httpOk(port)) break;
    await sleep(600);
  }
  if (!(await httpOk(port))) return fatal('后台程序一直没有就绪', '日志在 ' + LOG_DIR);
  status('就绪,正在打开界面…');
  makeTray(url);
  makeMain(url);
}

app.on('before-quit', () => { quitting = true; killPython(); });
app.on('will-quit', () => { killPython(); try { logStream && logStream.end(); } catch {} });
app.on('window-all-closed', () => app.quit());
ipcMain.on('open-logs', () => shell.openPath(LOG_DIR));
