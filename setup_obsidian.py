# -*- coding: utf-8 -*-
# ------------------------------------------------------------------
# 听课搭子 (tingke-dazi) · Copyright (c) 2026 luoxiu065-zjx
# 原始仓库:https://github.com/luoxiu065-zjx/tingke-dazi
# 转载、二次发布请保留本版权声明和原作者署名。build: tkdz-1bf2795e
# ------------------------------------------------------------------
"""一键配置 Obsidian + Claudian,并和听课搭子串起来。由 配置Obsidian.bat 调用,也可以 python setup_obsidian.py [库路径] [--key sk-xxx] 运行。

做的事(每步可重复运行,已经好的会跳过):
 1. 拿 DeepSeek key:.env 里有就用,没有就问你一次(Claudian 和听课搭子共用这把 key)
 2. 缺什么装什么:Obsidian、Node.js、Git(winget),Claude Code(npm)
 3. 建笔记库(默认在程序旁边的「听课搭子笔记」),放一篇「开始.md」
 4. 把 Claudian 插件装进库里,写好 DeepSeek 的环境变量,启用插件
 5. 把听课搭子的 .env 指到这个库(OBSIDIAN_VAULT / RECORD_SUBDIR / COURSE_SUBDIR),以后每节课直接存进库
 6. 用 obsidian://open 打开这个库
"""
import os, sys, re, json, subprocess, shutil, urllib.request, time, webbrowser
sys.stdout.reconfigure(errors="replace"); sys.stdin.reconfigure(errors="replace")
BASE = os.path.dirname(os.path.abspath(__file__)); ENV = os.path.join(BASE, ".env")
PLUGIN_ID = "realclaudian"
RELEASE = "https://github.com/YishenTu/claudian/releases/latest/download/"
MODEL_ID = "opus"                       # Claudian 的模型槽;下面的环境变量把它映射到 DeepSeek
MODEL_LABEL = "deepseek-flash[1m]"
ENV_TEXT = """ANTHROPIC_BASE_URL=https://api.deepseek.com/anthropic
ANTHROPIC_AUTH_TOKEN={key}
ANTHROPIC_MODEL=deepseek-flash[1m]
ANTHROPIC_DEFAULT_OPUS_MODEL=deepseek-flash[1m]
ANTHROPIC_DEFAULT_SONNET_MODEL=deepseek-flash[1m]
ANTHROPIC_DEFAULT_HAIKU_MODEL=deepseek-flash
CLAUDE_CODE_SUBAGENT_MODEL=deepseek-flash
API_TIMEOUT_MS=600000"""
WELCOME = """# 听课搭子笔记 · 开始

这个库是「听课搭子」自动配好的。以后每节课结束,记录会自动出现在 `课堂记录/<课号>/` 里。

## 课后怎么用
1. 打开一篇课堂记录。
2. 右侧栏打开 Claudian(左侧栏的小图标,或 Ctrl+P 搜 Claudian)。
3. 选中笔记里没听懂的一句,在 Claudian 里问:「这是什么意思?举个例子,然后把解释加到笔记末尾的『课后答疑』一节。」
4. 它会直接把解释写回这篇笔记。

## 它用的是哪个模型
Claudian 底层是 Claude Code,这里已经把它指到 DeepSeek(和听课搭子同一把 key),不用再登录。想换模型,在 Claudian 设置 → Environment 里改。

## 手机也想看
装 Remotely Save 插件把这个库同步到网盘,手机 Obsidian 指向同一个网盘就行;手机版听课搭子的教程里有五步说明。
"""

def say(s): print(s, flush=True)
def run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", shell=isinstance(cmd, str), **kw)
def which(name):
    for ext in ("", ".exe", ".cmd", ".bat"):
        p = shutil.which(name + ext)
        if p: return p
    return None
def read_env():
    out = {}
    if os.path.exists(ENV):
        for line in open(ENV, encoding="utf-8"):
            m = re.match(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$", line)
            if m and not line.lstrip().startswith("#"): out[m.group(1)] = m.group(2)
    return out
def write_env(updates):
    lines = open(ENV, encoding="utf-8").read().splitlines() if os.path.exists(ENV) else []
    done = set()
    for i, line in enumerate(lines):
        m = re.match(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=", line)
        if m and not line.lstrip().startswith("#") and m.group(1) in updates:
            lines[i] = "%s=%s" % (m.group(1), updates[m.group(1)]); done.add(m.group(1))
    for k, v in updates.items():
        if k not in done: lines.append("%s=%s" % (k, v))
    open(ENV, "w", encoding="utf-8").write("\n".join(lines) + "\n")
def winget_install(pkg_id, name):
    say("  正在用 winget 安装 %s(第一次会弹一个安装进度窗口,等它结束)…" % name)
    r = run(["winget", "install", "-e", "--id", pkg_id, "--accept-package-agreements", "--accept-source-agreements", "--silent"])
    if r.returncode not in (0, -1978335189):   # -1978335189 = 已经装过
        say("  winget 装 %s 没成功:%s" % (name, (r.stdout + r.stderr)[-300:].strip())); return False
    return True
def refresh_path():
    # winget 装完的东西在当前进程 PATH 里还没有,把常见位置补上
    for p in (os.path.expandvars(r"%ProgramFiles%\nodejs"), os.path.expandvars(r"%APPDATA%\npm"), os.path.expandvars(r"%ProgramFiles%\Git\cmd"),
              os.path.expandvars(r"%LOCALAPPDATA%\Programs\Obsidian"), os.path.expandvars(r"%LOCALAPPDATA%\Obsidian")):
        if os.path.isdir(p) and p not in os.environ["PATH"]: os.environ["PATH"] = p + os.pathsep + os.environ["PATH"]

def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    key_arg = next((sys.argv[i + 1] for i, a in enumerate(sys.argv) if a == "--key" and i + 1 < len(sys.argv)), "")
    skip_install = "--no-install" in sys.argv
    say("=== 听课搭子 · 一键配置 Obsidian + Claudian ===\n")
    # 1) key
    env = read_env(); key = key_arg or env.get("DEEPSEEK_API_KEY", "").strip()
    if not key or key.lower().startswith(("sk-xxx", "your")):
        say("[1/6] 还没有 DeepSeek key。去 platform.deepseek.com → API keys → 创建,然后粘到这里(粘完回车):")
        key = input("  DeepSeek key: ").strip()
        if not key.startswith("sk-"):
            say("  这不像一个 DeepSeek key(应该 sk- 开头),先去申请再来。"); return 1
        write_env({"DEEPSEEK_API_KEY": key}); say("  已写进 .env,听课搭子也用它。")
    else:
        say("[1/6] 用 .env 里已有的 DeepSeek key(尾号 %s)。" % key[-4:])
    # 2) 软件
    say("[2/6] 检查要装的东西…"); refresh_path()
    need = []
    obs_exe = next((p for p in (os.path.expandvars(r"%LOCALAPPDATA%\Programs\Obsidian\Obsidian.exe"), os.path.expandvars(r"%LOCALAPPDATA%\Obsidian\Obsidian.exe")) if os.path.exists(p)), None) or which("Obsidian")
    if not obs_exe and not os.path.exists(os.path.expandvars(r"%APPDATA%\obsidian\obsidian.json")): need.append(("Obsidian.Obsidian", "Obsidian"))
    if not which("node"): need.append(("OpenJS.NodeJS.LTS", "Node.js"))
    if not which("git"): need.append(("Git.Git", "Git"))
    if need and not skip_install:
        if not which("winget"):
            say("  这台电脑没有 winget,请手动装:" + ",".join(n for _, n in need) + "(obsidian.md / nodejs.org / git-scm.com),装完再运行一次。"); return 1
        for pid, name in need: winget_install(pid, name)
        refresh_path()
    elif need:
        say("  (--no-install)跳过安装:" + ",".join(n for _, n in need))
    if not which("claude"):
        if skip_install: say("  (--no-install)跳过 Claude Code")
        else:
            npm = which("npm")
            if not npm: say("  没找到 npm(Node.js 没装好),装好 Node.js 再运行一次。"); return 1
            say("  正在装 Claude Code(npm install -g @anthropic-ai/claude-code),一两分钟…")
            r = run([npm, "install", "-g", "@anthropic-ai/claude-code"])
            if r.returncode != 0: say("  装 Claude Code 失败:" + (r.stdout + r.stderr)[-300:]); return 1
            refresh_path()
    cli = which("claude"); say("  Obsidian:%s  Node:%s  Git:%s  Claude Code:%s" % ("有" if (obs_exe or not need) else "装了", "有" if which("node") else "无", "有" if which("git") else "无", "有" if cli else "无"))
    # 3) 库
    vault = os.path.abspath(args[0]) if args else os.path.join(os.path.dirname(BASE), "听课搭子笔记")
    os.makedirs(os.path.join(vault, ".obsidian", "plugins", PLUGIN_ID), exist_ok=True)
    os.makedirs(os.path.join(vault, "课堂记录"), exist_ok=True); os.makedirs(os.path.join(vault, "课程"), exist_ok=True)
    if not os.path.exists(os.path.join(vault, "开始.md")): open(os.path.join(vault, "开始.md"), "w", encoding="utf-8").write(WELCOME)
    say("[3/6] 笔记库:%s" % vault)
    # 4) Claudian:插件文件 + 启用 + 指向 DeepSeek + 选好模型
    pdir = os.path.join(vault, ".obsidian", "plugins", PLUGIN_ID)
    for f in ("main.js", "manifest.json", "styles.css"):
        dst = os.path.join(pdir, f)
        if not os.path.exists(dst):
            say("  下载 Claudian 插件 %s…" % f)
            try:
                urllib.request.urlretrieve(RELEASE + f, dst)
            except Exception as e:
                say("  下载失败(%s)。网络到 github.com 不通的话,手动在 Obsidian 的第三方插件里搜 Claudian 安装,再运行一次本脚本。" % e); return 1
    cpj = os.path.join(vault, ".obsidian", "community-plugins.json")
    plugins = json.load(open(cpj, encoding="utf-8")) if os.path.exists(cpj) else []
    if PLUGIN_ID not in plugins:
        plugins.append(PLUGIN_ID); json.dump(plugins, open(cpj, "w", encoding="utf-8"))
    # Claudian 真正的配置在库里的 .claudian/claudian-settings.json(插件目录下的 data.json 不管用)
    cdir = os.path.join(vault, ".claudian"); os.makedirs(cdir, exist_ok=True)
    cs = os.path.join(cdir, "claudian-settings.json")
    data = {}
    if os.path.exists(cs):
        try: data = json.load(open(cs, encoding="utf-8"))
        except Exception: data = {}
    pc = data.setdefault("providerConfigs", {}); claude_cfg = pc.setdefault("claude", {})
    old_env = (claude_cfg.get("environmentVariables") or "").strip()
    if old_env and "api.deepseek.com" not in old_env:
        # 这个库的 Claudian 已经配过别的(比如 Claude 订阅),不覆盖
        say("[4/6] 这个库的 Claudian 已经配过别的,保持原样不动。")
    else:
        claude_cfg.update({
            "enabled": True,
            "environmentVariables": ENV_TEXT.format(key=key),
            "visibleModels": [MODEL_ID],
            "selectedModels": [{"value": MODEL_ID, "label": MODEL_LABEL, "description": "听课搭子默认(DeepSeek)",
                                "resolvedModel": MODEL_LABEL, "reasoningMetadataResolved": True,
                                "supportedEffortLevels": ["low", "medium", "high", "xhigh", "max"]}],
            "modelAliases": {}, "cliPath": "", "cliPathsByHost": {}, "environmentHash": "",
            "loadUserSettings": False,   # 不去读电脑上别处的 Claude 配置,保证这个库干净
        })
        data["model"] = MODEL_ID; data["settingsProvider"] = "claude"
        say("[4/6] Claudian 已装好,直接用 DeepSeek,模型也选好了。")
    json.dump(data, open(cs, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    # 5) 听课搭子 .env
    if "--no-link" in sys.argv:
        say("[5/6] (--no-link)没改听课搭子的 .env。")
    else:
        write_env({"OBSIDIAN_VAULT": vault, "RECORD_SUBDIR": "课堂记录", "COURSE_SUBDIR": "课程"})
        say("[5/6] 听课搭子的记录以后直接存进这个库(.env 已改,重启听课搭子生效)。")
    # 6) 把库登记到 Obsidian(obsidian.json),然后打开。登记时 Obsidian 必须是关着的,不然它会把文件改回去
    if "--no-open" not in sys.argv:
        reg = os.path.expandvars(r"%APPDATA%\obsidian\obsidian.json")
        def obsidian_running():
            r = run('tasklist /FI "IMAGENAME eq Obsidian.exe" /NH'); return "Obsidian.exe" in (r.stdout or "")
        if obsidian_running():
            if "--auto-close" in sys.argv:
                run("taskkill /IM Obsidian.exe /F"); time.sleep(2)
            else:
                say("[6/6] Obsidian 正开着。请先把 Obsidian 整个关掉(关掉所有窗口),然后回到这里按回车…")
                while obsidian_running():
                    try: input("  关好了按回车: ")
                    except EOFError: break
        d = {"vaults": {}}
        if os.path.exists(reg):
            try: d = json.load(open(reg, encoding="utf-8"))
            except Exception: pass
        vaults = d.setdefault("vaults", {})
        vid = next((k for k, v in vaults.items() if os.path.normcase(v.get("path", "")) == os.path.normcase(vault)), None)
        if not vid:
            import secrets; vid = secrets.token_hex(8)
        vaults[vid] = {"path": vault, "ts": int(time.time() * 1000), "open": True}
        os.makedirs(os.path.dirname(reg), exist_ok=True); json.dump(d, open(reg, "w", encoding="utf-8"))
        exe = obs_exe or next((p for p in (os.path.expandvars(r"%LOCALAPPDATA%\Programs\Obsidian\Obsidian.exe"), os.path.expandvars(r"%LOCALAPPDATA%\Obsidian\Obsidian.exe"), r"D:\apps\Obsidian\Obsidian.exe") if os.path.exists(p)), None)
        if exe: subprocess.Popen([exe], close_fds=True)
        else: say("  没找到 Obsidian.exe,请手动打开 Obsidian,它会显示这个库。")
        say("[6/6] 已登记并打开这个库。第一次打开 Obsidian 会问要不要信任这个库里的插件,点「信任并启用」。\n")
    else:
        say("[6/6] (--no-open)没打开 Obsidian。\n")
    say("都配好了。打开库里的「开始.md」看怎么用;右侧栏打开 Claudian 就能问问题,不用登录。")
    return 0

if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(1)
