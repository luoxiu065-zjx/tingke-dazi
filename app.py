# -*- coding: utf-8 -*-
# ------------------------------------------------------------------
# 听课搭子 (tingke-dazi) · Copyright (c) 2026 luoxiu065-zjx
# 原始仓库:https://github.com/luoxiu065-zjx/tingke-dazi
# 转载、二次发布请保留本版权声明和原作者署名。build: tkdz-1bf2795e
# ------------------------------------------------------------------
"""
听课搭子 —— 英文课实时转写 + 逐句中文翻译 + AI 总结 + 老师提问提醒 + 翻译/AI 问答/课件/了解我 四格模块,按课程存成 Markdown(可直接放进 Obsidian)
声音来源可选:麦克风(线下课)/ 电脑播放的声音(网课、录播)
每识别一句就刷新 live.md,Claude 读它就能实时知道课上在讲什么、老师问了什么。
"""
import os, sys, json, time, queue, threading, re, glob, webbrowser, datetime, shutil, urllib.parse, urllib.request, urllib.error, traceback

# pip 装的 CUDA 库(nvidia-cublas/cudnn)在 venv 里,要同时加进 DLL 搜索目录和 PATH,否则 cuda 模式找不到 cublas64_12.dll
for _d in glob.glob(os.path.join(sys.prefix, "Lib", "site-packages", "nvidia", "*", "bin")):
    os.add_dll_directory(_d)
    os.environ["PATH"] = _d + os.pathsep + os.environ.get("PATH", "")

os.environ.setdefault("HF_HOME", os.path.join(os.path.dirname(os.path.abspath(__file__)), "models"))   # 语音模型下载到程序旁边的 models 文件夹
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")

# 黑窗口是 GBK 编码,打印「▶」「–」这类字符会抛 UnicodeEncodeError(2026-10-03 冒烟时接口因此 500);改成打不出的字用 ? 代替
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(errors="replace")
    except Exception:
        pass

import numpy as np
import soundcard as sc          # 必须在主线程最先 import(它自己初始化 COM);其他线程再用 com_init()
from dotenv import load_dotenv
from flask import Flask, Response, send_from_directory, jsonify, request

load_dotenv()
import session, textkit     # 会话落盘/续录/电量;批量翻译解析(2026-10-03)
import askmem               # AI 问答模块的记忆(2026-10-04)
import slides               # 课件:抽文本/检索节选/术语补全(2026-10-04)
import llmcfg               # 设置页:提供商预设/.env 读写/用量(2026-10-04)
import context_pack         # 「了解我」上下文包(2026-10-04)

# ---------------- 配置 ----------------
BASE = os.path.dirname(os.path.abspath(__file__))
LIVE_FILE  = os.path.join(BASE, "live.md")                 # 给 Claude 读的实时转录
BACKUP_DIR = os.path.join(BASE, "课堂记录备份")              # 上次没正常结束的 live.md 挪到这里
PHONE_LINK = os.getenv("PHONE_LINK", "").strip()             # 手机版专属链接(https://…/live/?k=…),用于「移交手机」

def detect_device():
    """有 NVIDIA 显卡 → cuda + large-v3-turbo(实测约占 2.3GB 显存);没有 → CPU + small.en(慢一些、准确率低一些)。
    .env 里写了 WHISPER_DEVICE / WHISPER_MODEL 就以 .env 为准。"""
    dev = os.getenv("WHISPER_DEVICE", "auto").strip().lower()
    if dev == "auto":
        try:
            import ctranslate2
            dev = "cuda" if ctranslate2.get_cuda_device_count() > 0 else "cpu"
        except Exception:
            dev = "cpu"
    model = os.getenv("WHISPER_MODEL", "").strip() or ("large-v3-turbo" if dev == "cuda" else "small.en")
    compute = os.getenv("WHISPER_COMPUTE", "").strip() or ("float16" if dev == "cuda" else "int8")
    return model, dev, compute

WHISPER_MODEL, WHISPER_DEVICE, WHISPER_COMPUTE = detect_device()

DEEPSEEK_KEY   = os.getenv("DEEPSEEK_API_KEY", "").strip()
DEEPSEEK_BASE  = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com").strip()
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-chat").strip()
PORT = int(os.getenv("PORT", "5000"))

VAULT_DIR   = os.getenv("OBSIDIAN_VAULT", "").strip() or os.path.join(BASE, "课堂记录")   # 没填 Obsidian 库就存到程序旁边
RECORD_SUB  = os.getenv("RECORD_SUBDIR", "").strip()                   # 库里的相对路径,每门课一个子文件夹;留空=库根目录
COURSE_SUB  = os.getenv("COURSE_SUBDIR", "").strip()                   # 每门课的知识页(周页、AI问答.md)放在库里哪个文件夹下;留空=库根目录
COURSES = [c.strip() for c in os.getenv(
    "COURSES",
    "COMP0001 示例课程,其他").replace("，", ",").split(",") if c.strip()]

SAMPLE_RATE = 16000
BLOCK       = 1600          # 0.1s
SILENCE_RMS = float(os.getenv("SILENCE_RMS", "0.008"))
SILENCE_HANG = 0.7          # 停顿多久算一句
MIN_SEG      = 0.8
MAX_SEG      = 12.0         # 一句最长几秒(老师连着说不停顿时强制切)
SUMMARY_EVERY = 40          # 秒

BATTERY_WARN = int(os.getenv("BATTERY_WARN", "20"))     # 没插电且低于这个百分比:界面标红提醒
BATTERY_STOP = int(os.getenv("BATTERY_STOP", "8"))      # 没插电且低于这个百分比:自动结束并保存(10/1 断电丢了半节课)

_placeholder = ("", "your_deepseek_key_here", "sk-xxxx")
TRANSLATE_ENABLED = bool(DEEPSEEK_KEY) and DEEPSEEK_KEY.lower() not in _placeholder

app = Flask(__name__)

# ---------------- SSE 发布订阅 ----------------
subscribers = []
sub_lock = threading.Lock()

def publish(event):
    data = "data: " + json.dumps(event, ensure_ascii=False) + "\n\n"
    with sub_lock:
        for q in list(subscribers):
            try:
                q.put_nowait(data)
            except Exception:
                pass

LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")

def log(msg):
    """所有状态/错误同时写进 logs/app-日期.log,黑窗口关了也能查。"""
    line = "%s %s" % (datetime.datetime.now().strftime("%H:%M:%S"), msg)
    try:
        print(line)
    except Exception:
        pass
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        with open(os.path.join(LOG_DIR, "app-%s.log" % datetime.date.today().isoformat()), "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass

def status(msg, level="info"):
    log("[%s] %s" % (level, msg))
    STATE["status"] = {"msg": msg, "level": level}
    publish({"type": "status", "msg": msg, "level": level})

# ---------------- 会话状态 ----------------
# state: idle / recording / paused / saving
STATE = {"state": "idle", "course": None, "start": None, "elapsed_base": 0.0,
         "resume_t": None, "device_id": None, "device_name": "", "gen": 0,
         "model_ready": False, "status": {"msg": "", "level": "info"}}
state_lock = threading.RLock()

history = []                 # [{id, t, clock, en, zh}]
history_lock = threading.Lock()
seg_counter = 0
SUMMARY = {"items": [], "upto": 0}   # items=[{topic, points:[{k,v}]}], upto=已总结到的 seg id

HEALTH = {"audio": 0.0, "voice": 0.0, "seg": 0.0, "dropped": 0, "warned": ""}   # 看门狗用:最后收到声音/人声/字幕的时刻
audio_q = queue.Queue(maxsize=60)   # (音频段, 开始秒数, 墙钟时间)
text_q  = queue.Queue()

def elapsed():
    with state_lock:
        e = STATE["elapsed_base"]
        if STATE["state"] == "recording" and STATE["resume_t"]:
            e += time.time() - STATE["resume_t"]
    return e

def fmt_t(sec):
    sec = int(sec)
    return "%02d:%02d:%02d" % (sec // 3600, sec % 3600 // 60, sec % 60)

# ---------------- LLM ----------------
llm_client = None
USAGE = llmcfg.Usage()        # 这节课用了多少 token(所有提供商通用)

def build_llm():
    """按当前 DEEPSEEK_* 全局重建客户端;设置页保存后也走这里,不用重启。"""
    global llm_client, TRANSLATE_ENABLED
    TRANSLATE_ENABLED = bool(DEEPSEEK_KEY) and DEEPSEEK_KEY.lower() not in _placeholder or (DEEPSEEK_BASE and "11434" in DEEPSEEK_BASE)
    if not TRANSLATE_ENABLED:
        llm_client = None; return
    try:
        from openai import OpenAI
        llm_client = OpenAI(api_key=DEEPSEEK_KEY or "ollama", base_url=DEEPSEEK_BASE)
    except Exception as e:
        print("[!] LLM 初始化失败,只显示英文:", e)
        TRANSLATE_ENABLED = False; llm_client = None

build_llm()

def llm_chat(messages, temperature=0.3, max_tokens=800):
    resp = llm_client.chat.completions.create(
        model=DEEPSEEK_MODEL, messages=messages,
        temperature=temperature, max_tokens=max_tokens, stream=False)
    USAGE.add(getattr(resp, "usage", None))
    return resp.choices[0].message.content.strip()


def parse_json(s, opener="["):
    closer = "]" if opener == "[" else "}"
    m = re.search(re.escape(opener) + r".*" + re.escape(closer), s, re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except Exception:
        return None

# ---------------- 声音设备 ----------------
def com_init():
    """soundcard 走 Windows COM,每个用到它的线程都要先初始化一次,否则报 0x800401f0。重复调用无害。"""
    try:
        import ctypes
        ctypes.windll.ole32.CoInitializeEx(None, 0)
    except Exception:
        pass

def list_devices():
    com_init()
    out = []
    try:
        default_mic = sc.default_microphone().id
    except Exception:
        default_mic = None
    for m in sc.all_microphones(include_loopback=True):
        lb = bool(getattr(m, "isloopback", False))
        out.append({"id": m.id, "loopback": lb,
                    "name": ("电脑播放的声音 · " if lb else "麦克风 · ") + m.name,
                    "default": (not lb and m.id == default_mic)})
    out.sort(key=lambda d: (d["loopback"], not d["default"]))
    return out

def audio_capture(gen, device_id):
    """外层循环:声音设备中途出错(拔耳机、蓝牙断开、系统切换默认设备、休眠唤醒)就等 2 秒自动重连,直到这节课结束。"""
    com_init()
    fails = 0
    while STATE["gen"] == gen and STATE["state"] == "recording":
        try:
            _capture_once(gen, device_id)
            if fails:
                status("声音已恢复,继续转写", "ok")
            return
        except Exception as e:
            fails += 1
            status("声音输入中断(第 %d 次):%s。2 秒后自动重连…" % (fails, e), "error")
            time.sleep(2)

def _capture_once(gen, device_id):
    dev = sc.get_microphone(device_id, include_loopback=True)
    buf = []; voiced = 0.0; silence = 0.0; seg_t0 = None; seg_clock = None
    last_level = 0.0
    if True:
        with dev.recorder(samplerate=SAMPLE_RATE, channels=1, blocksize=SAMPLE_RATE // 2) as rec:   # 0.5s 缓冲,防识别占 CPU 时丢音
            while STATE["gen"] == gen and STATE["state"] == "recording":
                data = rec.record(numframes=BLOCK)
                mono = data[:, 0] if getattr(data, "ndim", 1) > 1 else data
                rms = float(np.sqrt(np.mean(mono ** 2)) + 1e-9)
                dur = len(mono) / SAMPLE_RATE
                now = time.time()
                HEALTH["audio"] = now
                if rms >= SILENCE_RMS:
                    HEALTH["voice"] = now
                if now - last_level > 0.1:
                    publish({"type": "level", "v": min(1.0, rms * 12)})
                    last_level = now
                buf.append(mono)
                if rms >= SILENCE_RMS and seg_t0 is None:     # 这句话第一次出声的时刻
                    seg_t0 = max(0.0, elapsed() - dur); seg_clock = datetime.datetime.now()
                if rms >= SILENCE_RMS:
                    voiced += dur; silence = 0.0
                else:
                    silence += dur
                total = sum(len(b) for b in buf) / SAMPLE_RATE
                flush = (voiced >= MIN_SEG and silence >= SILENCE_HANG) or \
                        (total >= MAX_SEG and voiced >= MIN_SEG)
                if flush:
                    seg = np.concatenate(buf).astype(np.float32)
                    try:
                        audio_q.put_nowait((seg, seg_t0, seg_clock))
                    except queue.Full:
                        HEALTH["dropped"] += 1          # 识别跟不上,丢了一段(看门狗会提示)
                    buf = []; voiced = 0.0; silence = 0.0; seg_t0 = None
                elif voiced == 0 and total > 2.0:
                    buf = buf[-5:]
        # 暂停/结束时把手里没说完的半句也送去识别
        if buf and voiced >= MIN_SEG:
            try:
                audio_q.put_nowait((np.concatenate(buf).astype(np.float32), seg_t0 or 0.0, seg_clock))
            except queue.Full:
                pass

# ---------------- 语音识别 ----------------
TERMS_DIR = os.path.join(BASE, "术语")      # 每门课一个 <课号>.txt,逗号或换行分隔,可自己加词

def course_terms(course):
    fp = os.path.join(TERMS_DIR, course_code(course) + ".txt")
    try:
        with open(fp, encoding="utf-8") as f:
            words = [w.strip() for w in re.split(r"[,\n]", f.read()) if w.strip() and not w.startswith("#")]
        return ", ".join(words)
    except Exception:
        return ""

def build_prompt():
    terms = course_terms(STATE["course"])
    with history_lock:
        prev = " ".join(h["en"] for h in history[-3:])
    # 上文里的 ". . . ." 会被模型模仿、越滚越多(2026-09-24 课上实测整分钟只剩点),先洗掉
    prev = re.sub(r"(\s*\.){2,}", ".", prev)
    prev = re.sub(r"\s+", " ", prev).strip()[-200:]
    p = ("University lecture. Terms: %s. " % terms[:350] if terms else "University lecture. ") + prev
    return p.strip()

def whisper_worker():
    global seg_counter
    try:
        from faster_whisper import WhisperModel
    except Exception as e:
        status("未安装 faster-whisper:%s" % e, "error"); return
    status("正在加载语音识别模型(%s),请稍候…" % WHISPER_MODEL)
    try:
        model = WhisperModel(WHISPER_MODEL, device=WHISPER_DEVICE, compute_type=WHISPER_COMPUTE)
    except Exception as e:
        status("模型加载失败:%s" % e, "error"); return
    STATE["model_ready"] = True
    status("语音识别就绪。选好课程和声音来源,点「开始录制」。", "ok")
    while True:
        try:
            _recognize_one(model)
        except Exception as e:                     # 兜底:识别线程永远不退出
            status("识别出错,已跳过这一段:%s" % e, "error")

def _recognize_one(model):
    global seg_counter
    if True:
        seg, t0, clock = audio_q.get()
        publish({"type": "recognizing", "on": True})
        try:
            # 精细解码(beam 5)+ 提示:本课术语 + 上一两句,让它认得专业词、接得上半句话
            segments, _ = model.transcribe(seg, language="en", vad_filter=True, beam_size=5,
                                           initial_prompt=build_prompt(), condition_on_previous_text=False)
            # 去掉「其实没人说话却编出一句」的幻觉段(Whisper 在噪音/静音上的老毛病)
            text = " ".join(s.text.strip() for s in segments
                            if not (s.no_speech_prob > 0.6 and s.avg_logprob < -1.0)
                            and not (getattr(s, "compression_ratio", 0) or 0) > 2.4).strip()   # 复读式幻觉(「秘密的秘密的…」)压缩比会很高,整段丢
            # 已经指定了英文识别,输出里的中文/日文字符只可能是幻觉(有人对着它说中文、或噪音太大),直接去掉;同一短语连续重复只留一次
            text = re.sub(r"[぀-ヿ㐀-鿿＀-￯]+", " ", text)
            text = re.sub(r"(\b.{3,40}?)(?:[\s,.…]*\1){2,}", r"\1", text)
            text = re.sub(r"\s{2,}", " ", text).strip(" ,.")
        except Exception as e:
            status("识别出错:%s" % e, "error"); return
        finally:
            publish({"type": "recognizing", "on": audio_q.qsize() > 0})
        text = re.sub(r"(\s*\.){3,}", " …", text).strip()
        # 课尾静音时的「Professor X … Professor X … Professor X」重复幻觉:同一短语连着 3 遍以上只留 1 遍
        text = re.sub(r"(\b.{4,40}?)(?:[\s…,.]*\1){2,}", r"\1", text)
        if not re.search(r"[A-Za-z]{2,}", text):     # 只剩标点(静音/杂音幻觉)的不要
            return
        clock_s = (clock or datetime.datetime.now()).strftime("%H:%M:%S")
        with history_lock:
            seg_counter += 1
            item = {"id": seg_counter, "t": fmt_t(t0), "clock": clock_s, "en": text, "zh": ""}
            history.append(item)
        HEALTH["seg"] = time.time()
        publish(dict(type="seg", **item))
        if AUTO_QA["on"] and TRANSLATE_ENABLED and looks_like_question(text):
            qa_q.put(item["id"])
        request_live()
        if TRANSLATE_ENABLED:
            text_q.put({"id": item["id"], "en": text})

# ---------------- 翻译 ----------------
# ---------------- 自动答疑:老师提问 → 中英文参考回答 ----------------
AUTO_QA = {"on": os.getenv("AUTO_QA", "0") == "1"}        # 开源版默认关:它是学习辅助,考试/口语评估时别开
CONTEXT = {"on": os.getenv("CONTEXT_MODE", "0") == "1", "text": "", "sources": [], "tokens": 0}   # 「了解我」:开课时打包,只喂总结/提问/问答

def context_prefix():
    """开着且有内容时,放在提示词最前面(固定前缀,DeepSeek 按缓存价计)。"""
    return (CONTEXT["text"] + "\n\n") if CONTEXT["on"] and CONTEXT["text"] else ""

def build_context(course):
    try:
        r = context_pack.build(course_code(course), course, BASE, VAULT_DIR, record_sub=RECORD_SUB, course_sub=COURSE_SUB)
        CONTEXT.update(text=r["text"], sources=r["sources"], tokens=r["tokens_est"])
        publish({"type": "context", "on": CONTEXT["on"], "sources": r["sources"], "tokens": r["tokens_est"]})
        log("[context] %d 个来源,约 %d tokens:%s" % (len(r["sources"]), r["tokens_est"], ", ".join(r["sources"])))
    except Exception as e:
        log("[warn] 上下文包失败:%s" % e)
QA = []                      # [{id, seg_id, t, question_en, question_zh, answer_zh, answer_en}]
qa_q = queue.Queue()
PREP = {"text": ""}          # 本周预习包(开课时从服务器取一次),给答疑当背景
# 第一道筛(规则,免费)只管「别漏」,真假交给 DeepSeek 判。2026-09-26 按 9/24-9/25 真实课堂转写重调(评测见 qa-eval/报告.md):
# Whisper 常把口头问句转成句号结尾;同学提问常以 ", right?" 收尾;老师也常用 "who can give me…" 这种不带问号的邀请。
_Q_LEAD = r"(?:(?:so|and|but|okay|ok|right|well|now|then|yes|yeah|alright|all right|um+|uh+|er+|like|sorry|actually|also)[,\s]+)*"
_WH_Q = re.compile(r"^" + _Q_LEAD + r"(?:(?:what|why|how|who|where|when)(?:'s|'re|\s+(?:is|are|was|were|do|does|did|can|could|would|should|will|might|about|if|else|kind|way|sort|type|exactly)\b)"
                   r"|which\s+(?:one|ones|way|of|do|does|did|can|could|would|should)\b"
                   r"|(?:how|what)\s+(?:many|much|long|often|far)\s+(?!you\b|we\b|i\b|they\b|it\b)\w+\s+(?:is|are|do|does|did|can|could|would|should|will|have|has)\b)", re.I)
_AUX_Q = re.compile(r"^" + _Q_LEAD + r"(?:can|could|would|will|do|does|did|is|are|was|were|should|shall|have|has|may|might|isn't|aren't|don't|doesn't|didn't|wouldn't|couldn't|can't)"
                    r"\s+(?:you|we|i|they|he|she|there|anyone|anybody|someone|somebody|everyone|everybody|any)\b", re.I)
_AUX_ANY = re.compile(r"^" + _Q_LEAD + r"(?:can|could|would|will|do|does|did|is|are|was|were|should|shall|have|has)\s+(?:it|this|that|these|those)\b", re.I)
_QA_CUE = re.compile(r"\b(?:anyone|anybody|any ideas?|what do you think|who can|who wants|can someone|can somebody|could someone|could somebody"
                     r"|can you (?:tell|give|guess|think|answer|explain|see|say|name)|give me (?:another|a|an|one|some|the)|(?:have|take) a guess|any guesses"
                     r"|i have a question|my question|question for you|i want to know|i was wondering|i'm wondering|what if|how come)\b", re.I)
_FILLER = re.compile(r"^(?:" + _Q_LEAD + r")?(?:right|okay|ok|yeah|yes|no|good|alright|isn.t it|you know|is it|does that make sense|makes sense|any questions|anything else"
                     r"|is that clear|is that ok|is that okay|all good|everyone ok|everybody ok|so|huh|hm+|sorry|pardon|really|yes please)$", re.I)
_TAGQ = re.compile(r"[,.]?\s*(?:right|okay|ok|yeah|isn.t it|you know|no|yes|don't we|don't you|isn't it|aren't they|can't we)\?$", re.I)
_ORG_Q = re.compile(r"\b(?:can you (?:all )?(?:hear|see) (?:me|this|that|it|the (?:screen|slides?))|is (?:the|my) (?:mic|microphone|sound)|can everyone (?:hear|see))\b", re.I)
_QA_MORE = re.compile(r"\b(?:question|wondering|want to know)\b|[-—…]\s*$", re.I)   # 问题多半还在下一段:同学先说 "I have a question…",或这句被切断

def looks_like_question(text):
    """第一道筛:宁多勿漏。① 以 ? 结尾、3 词以上、不是口头禅;带 right?/okay? 尾巴的,只有倒装/疑问词开头才算
    ② 没有问号但以疑问词+助动词(what is / how do / why would…)或倒装(can you / do we / is there…)开头
    ③ 含邀请回答的说法(anyone / who can / give me another / what do you think / I have a question…)"""
    for s in re.split(r"(?<=[?.!…])\s+|\s*[—–]\s*|\s*…\s*", text):
        s = s.strip()
        core = s.strip(" ,.?!-…").lower()
        n = len(core.split())
        if not core or _FILLER.match(core) or _ORG_Q.search(s):
            continue
        if s.endswith("?"):
            if not _TAGQ.search(s):
                if n >= 3:
                    return True
            elif n >= 5 and (_AUX_ANY.search(s) or _AUX_Q.search(s) or _WH_Q.search(s)):
                return True                      # "Is this the same as X, right?" 学生确认式提问
        if n >= 3 and (_WH_Q.search(s) or _AUX_Q.search(s)):
            return True                          # Whisper 把问句转成了句号结尾
    return bool(_QA_CUE.search(text))

def qa_worker():
    last_answered = 0.0
    while True:
        sid = first = qa_q.get()
        t_start = time.time()
        time.sleep(4)                                   # 等老师把问题说完(常常跨两句)
        # 再等后面的转写到齐:问题常被切成两段,只看候选那一段 DeepSeek 会判否(9/25 COMP6203 同学提问就这样漏的)
        with history_lock:
            txt = next((h["en"] for h in history if h["id"] == sid), "")
        need, limit = (2, 20) if _QA_MORE.search(txt) else (1, 8)
        while True:
            while not qa_q.empty():                     # 这几秒里又冒出的候选,合并成一次
                sid = max(sid, qa_q.get_nowait())
            with history_lock:
                last_id = history[-1]["id"] if history else 0
            if last_id >= sid + need or time.time() - t_start >= limit:
                break
            time.sleep(0.5)
        with history_lock:
            focus = [h for h in history if first <= h["id"] <= sid + 2][-6:]
            before = [h for h in history if focus and h["id"] < focus[0]["id"]][-10:]
        if not focus or (time.time() - last_answered < 8 and QA and QA[-1]["seg_id"] >= first - 2):
            continue
        ctx = before + focus
        transcript = ("【上文,只用来理解,不要判断也不要回答】\n" +
                      "\n".join("[%s] %s" % (h["t"], h["en"]) for h in before) +
                      "\n\n【待判断的句子】\n" + "\n".join("[%s] %s" % (h["t"], h["en"]) for h in focus))
        answered = "; ".join(q["question_en"] for q in QA[-5:]) or "无"
        prompt = (
            "你是 MSc 学生的课堂助手,正在实时听一节英文课(课程:%s)。下面是课堂转写(机器识别,会有听错的词)。\n"
            "只判断【待判断的句子】里是否有**需要回答的真问题**:老师向全班提问、老师点名提问、或同学提了一个值得知道答案的问题。"
            "口头禅(right? okay?)、自问自答后老师马上说出了答案、修辞性提问、组织课堂的问话(can you hear me? is the mic working? any questions?)都算否。"
            "注意:转写常把问句识别成句号结尾、或把一个问题切成前后两段;同学提问常以 right? / is it? 结尾求确认,这些都可能是真问题。"
            "老师说「who can give me…」「you can give me another one」「what do you think」这类邀请回答的句子也算提问。"
            "上文里的问题不算;和已回答过的问题(" + answered.replace("%", "%%") + ")是同一个的也算否。\n"
            "只输出 JSON:{\"is_question\":true/false,\"question_en\":\"问题原句(纠正听错的词)\",\"question_zh\":\"一句中文说明在问什么\","
            "\"answer_zh\":\"中文答案要点,不超过 80 字,带关键公式或术语\",\"answer_en\":\"课堂上能直接说出口的英文回答:1-2 句、不超过 35 个词、口语化,先说结论\"}。\n"
            "is_question 为 false 时其他字段留空。答案要基于课程常识和下面的课件/预习资料,不确定就说明。\n\n"
            "【本课术语】%s\n【课件节选】%s\n【本周预习包节选】%s\n【课中 AI 笔记】%s\n\n【最近转写】\n%s"
        ) % (STATE["course"] or "", course_terms(STATE["course"])[:400],
             slides.select(course_code(STATE["course"]), " ".join(h["en"] for h in ctx)) or "(无)",
             PREP["text"][:2500], summary_md(SUMMARY["items"])[:1500], transcript)
        try:
            j = parse_json(llm_chat([{"role": "user", "content": context_prefix() + prompt}], temperature=0.2, max_tokens=700), "{")
        except Exception as e:
            print("[qa] 出错:", e); continue
        if not j or not j.get("is_question") or not j.get("answer_en"):
            continue
        item = {"id": len(QA) + 1, "seg_id": sid, "t": focus[0]["t"],
                "question_en": j.get("question_en", ""), "question_zh": j.get("question_zh", ""),
                "answer_zh": j.get("answer_zh", ""), "answer_en": j.get("answer_en", "")}
        QA.append(item); last_answered = time.time()
        publish(dict(type="qa", **item))
        request_live()

def qa_md():
    out = []
    for q in QA:
        out += ["**[%s] 🎯 %s**" % (q["t"], q["question_zh"]), "> %s" % q["question_en"], "",
                "💡 %s" % q["answer_zh"], "", "🗣 *%s*" % q["answer_en"], ""]
    return "\n".join(out)

def translate_worker():
    if not TRANSLATE_ENABLED:
        return
    while True:
        item = text_q.get()
        batch = [item]
        # 积压(排队的还有 2 句以上)就把后面的几句合成一次调用,别让每句都排一遍队(2026-10-03 用户反馈"累积一大段才翻")
        while text_q.qsize() >= 2 and len(batch) < 6:
            try:
                batch.append(text_q.get_nowait())
            except queue.Empty:
                break
        with history_lock:
            ctx = [h["en"] for h in history if h["id"] < batch[0]["id"] and not h.get("gap")][-3:]
        sys_msg = ("你是大学课堂同传。把用户给的英文课堂口语翻成自然的简体中文,专业术语保留英文括注。只输出译文,不要解释、不要引号。"
                   + ("\n上文(仅供理解,不要翻译):" + " ".join(ctx) if ctx else ""))
        done = {}
        if len(batch) > 1:
            try:
                raw = llm_chat([
                    {"role": "system", "content": sys_msg + "\n用户会给编号的多句,逐句翻译,输出 JSON 数组:[{\"i\":编号,\"zh\":\"译文\"}],只输出 JSON。"},
                    {"role": "user", "content": textkit.batch_prompt(batch)},
                ], temperature=0.2, max_tokens=400 * len(batch))
                done = textkit.parse_batch(raw, batch)
            except Exception:
                done = {}
        for it in batch:
            zh = done.get(it["id"])
            if not zh:
                try:
                    zh = llm_chat([{"role": "system", "content": sys_msg},
                                   {"role": "user", "content": it["en"]}], temperature=0.2, max_tokens=400)
                except Exception:
                    zh = "(翻译失败,请检查 DeepSeek key 或网络)"
            with history_lock:
                for h in history:
                    if h["id"] == it["id"]:
                        h["zh"] = zh; break
            publish({"type": "seg_zh", "id": it["id"], "zh": zh})
        request_live()

# ---------------- 整节课 AI 总结(增量) ----------------
summary_lock = threading.Lock()

def update_summary():
    """把上次总结之后的新内容并进总结。返回是否有更新。"""
    if not TRANSLATE_ENABLED:
        return False
    with summary_lock:
        with history_lock:
            new = [h for h in history if h["id"] > SUMMARY["upto"] and not h.get("gap")]
        if not new:
            return False
        upto = new[-1]["id"]
        transcript = "\n".join("[%s] %s" % (h["t"], h["en"]) for h in new)
        prompt = (
            "你在给一节英文大学课程做实时中文笔记。下面是【已有笔记】(JSON)和【新的课堂内容】。"
            "把新内容并进笔记,输出更新后的完整笔记 JSON 数组:"
            "[{\"topic\":\"一句中文概括这一部分在讲什么\",\"points\":[{\"k\":\"关键词(4-8字)\",\"v\":\"一句中文说明\"}]}]。\n"
            "规则:按讲课顺序分主题;新内容延续上一主题就往里加要点,换话题就开新主题;每个主题 2-6 条要点;"
            "专业术语保留英文;寒暄、点名、设备调试之类不记;只输出 JSON。\n\n"
            "【已有笔记】\n" + json.dumps(SUMMARY["items"], ensure_ascii=False) +
            (("\n\n【课件节选,用来对照术语和主题,别照抄】\n" + _sl) if (_sl := slides.select(course_code(STATE["course"]), transcript, limit=1000)) else "") +
            "\n\n【新的课堂内容】\n" + transcript
        )
        try:
            raw = llm_chat([{"role": "user", "content": context_prefix() + prompt}], temperature=0.3, max_tokens=3000)
        except Exception:
            return False
        items = parse_json(raw, "[")
        if not isinstance(items, list):
            return False
        SUMMARY["items"] = items
        SUMMARY["upto"] = upto
    publish({"type": "summary", "items": items})
    request_live()
    return True

def summary_worker():
    while True:
        time.sleep(SUMMARY_EVERY)
        if STATE["state"] in ("recording", "paused"):
            update_summary()

# ---------------- live.md(给 Claude 读) ----------------
live_lock = threading.Lock()

def summary_md(items):
    lines = []
    for sec in items or []:
        lines.append("### " + str(sec.get("topic", "")))
        for p in sec.get("points", []):
            lines.append("- **%s**:%s" % (p.get("k", ""), p.get("v", "")))
        lines.append("")
    return "\n".join(lines)

LIVE_DIRTY = threading.Event()

def request_live():
    """工作线程不再自己重写 live.md(文件越录越大,每句都重写拖慢识别),只举个旗,live_writer 线程最多每 2 秒写一次。"""
    LIVE_DIRTY.set()

def persist_session():
    """把这节课的转写/总结/提问卡写进 session/当前.json,断电重开能续录。"""
    with state_lock:
        st = dict(STATE)
    if st["state"] not in ("recording", "paused") or not st["course"]:
        return
    with history_lock:
        rows = [dict(h) for h in history]
    try:
        session.dump(st["course"], st["start"], elapsed(), st["device_id"], st["device_name"],
                     rows, list(SUMMARY["items"]), SUMMARY["upto"], list(QA))
    except Exception as e:
        log("[warn] 会话落盘失败:%s" % e)

def live_writer():
    while True:
        dirty = LIVE_DIRTY.wait(timeout=2)
        if dirty:
            time.sleep(0.3)                 # 合并这 0.3 秒里的多次请求
            LIVE_DIRTY.clear()
        if STATE["state"] in ("recording", "paused"):
            if dirty:
                write_live()
            persist_session()
            publish({"type": "queues", "asr": audio_q.qsize(), "tr": text_q.qsize()})
        if dirty:
            time.sleep(1.7)

def write_live():
    with state_lock:
        st = dict(STATE)
    if st["state"] == "idle" or not st["course"]:
        return
    with history_lock:
        rows = list(history)
    label = {"recording": "录制中", "paused": "已暂停", "saving": "正在保存"}.get(st["state"], st["state"])
    lines = ["# 实时课堂转录",
             "",
             "- 课程:%s" % st["course"],
             "- 开始:%s(英国时间)" % st["start"].strftime("%Y-%m-%d %H:%M"),
             "- 状态:%s · 已录 %s · 文件更新于 %s" % (label, fmt_t(elapsed()), datetime.datetime.now().strftime("%H:%M:%S")),
             "- 声音来源:%s" % st["device_name"],
             "",
             "## AI 总结(约每 40 秒更新)",
             "",
             summary_md(SUMMARY["items"]) or "(还没有)",
             "",
             "## 课上提问与参考回答(自动答疑)",
             "",
             qa_md() or "(还没有)",
             "",
             "## 转写(越往下越新;格式 [课内时间 | 墙钟时间])",
             ""]
    for h in rows:
        lines.append("[%s | %s] %s" % (h["t"], h["clock"], h["en"]))
        if h["zh"]:
            lines.append("    中:" + h["zh"])
    with live_lock:
        tmp = LIVE_FILE + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                f.write("\n".join(lines) + "\n")
            if not _replace_retry(tmp, LIVE_FILE):
                log("[warn] live.md 正被别的程序占用,这一次没写进去(下一句会再写)")
        except Exception as e:                       # 写 live.md 只是辅助功能,绝不能拖死调用它的线程
            log("[warn] 写 live.md 失败:%s" % e)

def _replace_retry(tmp, dst, tries=8, wait=0.05):
    """Windows 上目标文件正被别的程序打开(杀毒、索引、有人在读)时,os.replace 会报 WinError 5/32。
    稍等重试;一直不行就放弃这一次,返回 False,不抛异常。
    2026-10-02:翻译线程就是在这里抛 PermissionError 后死掉的,之后整节课没有中文。"""
    for _ in range(tries):
        try:
            os.replace(tmp, dst)
            return True
        except PermissionError:
            time.sleep(wait)
    return False

def backup_stale_live():
    """启动时如果 live.md 里还有上次没保存的课,挪进备份文件夹,防止丢。"""
    if not os.path.exists(LIVE_FILE):
        return
    try:
        with open(LIVE_FILE, encoding="utf-8") as f:
            txt = f.read()
    except Exception:
        return
    if "状态:已保存" in txt or "## 转写" not in txt:
        return
    os.makedirs(BACKUP_DIR, exist_ok=True)
    dst = os.path.join(BACKUP_DIR, "未正常结束_%s.md" % datetime.datetime.now().strftime("%Y-%m-%d_%H%M%S"))
    shutil.move(LIVE_FILE, dst)
    print("[*] 上次的课没正常结束,转录已备份到", dst)

# ---------------- 保存到 Obsidian ----------------
def safe_name(s):
    return re.sub(r'[\\/:*?"<>|#^\[\]\n\r\t]', " ", s).strip()[:60]

def course_code(course):
    return course.split()[0] if course else "其他"

def save_to_obsidian():
    with state_lock:
        course = STATE["course"]; start = STATE["start"]
    dur = fmt_t(elapsed())
    end = datetime.datetime.now()
    # 等识别/翻译队列清空(最多 20 秒)
    t_end = time.time() + 20
    while time.time() < t_end and (audio_q.qsize() or text_q.qsize()):
        time.sleep(0.5)
    time.sleep(1.5)
    status("正在生成最终总结…")
    update_summary()
    with history_lock:
        rows = list(history)
    title = "简短录音" if len(rows) < 8 else "课堂记录"
    # 内容太少时不让 AI 起标题——笔记是空的它会凭空编一个
    if TRANSLATE_ENABLED and len(rows) >= 8:
        material = summary_md(SUMMARY["items"]) or "\n".join(h["en"] for h in rows)
        try:
            title = llm_chat([{"role": "user", "content":
                "根据下面这节课的内容,起一个 8-20 字的中文标题,概括这节课讲了什么。"
                "只能依据给出的内容,不要编造没出现的主题。只输出标题。\n\n"
                + material[:3000]}], temperature=0.2, max_tokens=60).strip().strip('"“”《》')
            # 模型有时不起标题而是解释「无法…」(2026-10-02 16:38 那条 90 秒记录就是),这种当没起
            if len(title) > 24 or re.search(r"无法|不能|抱歉|内容不完整|没有足够", title):
                title = "课堂记录"
        except Exception:
            pass
    code = course_code(course)
    folder = os.path.join(VAULT_DIR, *RECORD_SUB.split("/"), safe_name(code))
    os.makedirs(folder, exist_ok=True)
    fname = "%s %s %s.md" % (start.strftime("%Y-%m-%d %H%M"), code, safe_name(title))
    fpath = os.path.join(folder, fname)
    lines = ["---",
             'title: "%s"' % title.replace('"', "'"),
             "course: %s" % code,
             "course_name: \"%s\"" % course,
             "recorded_at: %s" % start.strftime("%Y-%m-%d %H:%M"),
             "ended_at: %s" % end.strftime("%Y-%m-%d %H:%M"),
             "duration: %s" % dur,
             "timezone: Europe/London",
             "source: 听课搭子",
             "deepseek_cost_cny: %s" % session_cost(),
             "llm_model: %s" % DEEPSEEK_MODEL,
             "context_mode: %s" % ("on" if CONTEXT["on"] and CONTEXT["text"] else "off"),
             "llm_tokens: %d" % (USAGE.snapshot()["prompt"] + USAGE.snapshot()["completion"]),
             "tags: [课堂记录, %s]" % code,
             "---", "",
             "# " + title, "",
             "录制 %s – %s(英国时间) · 时长 %s · 课程 %s" % (
                 start.strftime("%Y-%m-%d %H:%M"), end.strftime("%H:%M"), dur, course), "",
             "## AI 总结", "", summary_md(SUMMARY["items"]) or "(无)", "",
             "## 课上提问与参考回答", "", qa_md() or "(无)", ""]
    if MY_ASKS:
        lines += ["## 我的提问(AI 问答)", ""]
        for it in MY_ASKS:
            lines += ["**问:** %s" % it["q"], "", it["a"], ""]
    lines += ["## 全程转写(中英对照)", ""]
    for h in rows:
        lines.append("**%s** · %s  " % (h["t"], h["clock"]))
        lines.append(h["en"] + "  ")
        if h["zh"]:
            lines.append("> " + h["zh"])
        lines.append("")
    with open(fpath, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    ctx = {"code": code, "course": course, "start": start, "end": end, "dur": dur, "title": title,
           "week": None, "rows": rows, "summary_items": list(SUMMARY["items"]),
           "summary_md": summary_md(SUMMARY["items"]), "record_path": fpath}
    return fpath, len(rows), ctx

def session_cost():
    """这节课 DeepSeek 花了多少(开课余额 - 现在余额);查不到就写「未知」。"""
    b = fetch_balance(force=True)
    if not b or BALANCE["session_start"] is None:
        return "未知"
    return "%.2f" % max(0.0, BALANCE["session_start"] - b["total"])

def finish_session():
    ctx = None
    try:
        fpath, n, ctx = save_to_obsidian()
        rel = os.path.relpath(fpath, VAULT_DIR).replace("\\", "/")
        with live_lock:
            try:
                with open(LIVE_FILE, "a", encoding="utf-8") as f:
                    f.write("\n- 状态:已保存 → %s\n" % rel)
            except Exception:
                pass
        publish({"type": "saved", "path": rel, "n": n, "uri": obsidian_uri(rel)})
        status("已保存到 Obsidian:%s(%d 句)" % (rel, n), "ok")
        session.clear()                      # 保存成功,断电续录用的落盘文件就不要了
    except Exception as e:
        status("保存失败:%s(转录还在 live.md 里)" % e, "error")
    finally:
        reset_session()
        publish({"type": "state", **public_state()})

def reset_session():
    global seg_counter
    with history_lock:
        history.clear()
        seg_counter = 0
    SUMMARY["items"] = []; SUMMARY["upto"] = 0
    QA.clear(); PREP["text"] = ""; MY_ASKS.clear(); CONTEXT.update(text="", sources=[], tokens=0)
    with state_lock:
        STATE.update(state="idle", course=None, start=None, elapsed_base=0.0, resume_t=None)

def obsidian_uri(rel):
    return "obsidian://open?vault=%s&file=%s" % (
        urllib.parse.quote(os.path.basename(VAULT_DIR.rstrip("\\/"))), urllib.parse.quote(rel))

# ---------------- Web ----------------
def public_state():
    with state_lock:
        return {"state": STATE["state"], "course": STATE["course"],
                "start": STATE["start"].strftime("%Y-%m-%d %H:%M:%S") if STATE["start"] else None,
                "device_id": STATE["device_id"], "model_ready": STATE["model_ready"],
                "translate": TRANSLATE_ENABLED, "auto_qa": AUTO_QA["on"],
                "context_on": CONTEXT["on"], "context_sources": CONTEXT["sources"], "context_tokens": CONTEXT["tokens"]} | {"elapsed": elapsed()}

@app.route("/")
def index():
    return send_from_directory(BASE, "index.html")

@app.route("/config")
def config():
    try:
        devices = list_devices()
    except Exception as e:
        devices = []; status("读取声音设备失败:%s" % e, "error")
    return jsonify({"courses": COURSES, "devices": devices, "state": public_state()})

@app.route("/records")
def records():
    groups = {}
    base = os.path.join(VAULT_DIR, *RECORD_SUB.split("/"))
    for fp in glob.glob(os.path.join(base, "*", "*.md")):
        if "整合笔记" in os.path.basename(fp):      # 侧栏只列课堂记录本身,整合笔记在 Obsidian 里挨着看
            continue
        groups.setdefault(os.path.basename(os.path.dirname(fp)), []).append(fp)
    out = []
    for g in sorted(groups):
        items = []
        for fp in sorted(groups[g], reverse=True):
            rel = os.path.relpath(fp, VAULT_DIR).replace("\\", "/")
            name = os.path.splitext(os.path.basename(fp))[0]
            m = re.match(r"(\d{4}-\d{2}-\d{2} \d{4})\s*(.*)", name)
            items.append({"when": m.group(1) if m else "", "title": (m.group(2) if m else name),
                          "uri": obsidian_uri(rel)})
        out.append({"group": g, "items": items})
    return jsonify(out)

BALANCE = {"t": 0.0, "data": None, "session_start": None}

def fetch_balance(force=False):
    """DeepSeek 账户余额(元),缓存 60 秒。别的提供商没有这个接口,返回 None,界面改显示 token 用量。"""
    if not TRANSLATE_ENABLED or llmcfg.guess_provider(DEEPSEEK_BASE) != "deepseek":
        return None
    if not force and BALANCE["data"] and time.time() - BALANCE["t"] < 60:
        return BALANCE["data"]
    import urllib.request
    try:
        req = urllib.request.Request(DEEPSEEK_BASE.rstrip("/") + "/user/balance",
                                     headers={"Authorization": "Bearer " + DEEPSEEK_KEY, "Accept": "application/json"})
        j = json.loads(urllib.request.urlopen(req, timeout=10).read().decode())
        info = next((b for b in j.get("balance_infos", []) if b.get("currency") == "CNY"), (j.get("balance_infos") or [{}])[0])
        BALANCE["data"] = {"total": float(info.get("total_balance", 0)), "currency": info.get("currency", "CNY"),
                           "available": j.get("is_available", True),
                           "at": datetime.datetime.now().strftime("%H:%M")}
        BALANCE["t"] = time.time()
    except Exception as e:
        print("[!] 查余额失败:", e)
    return BALANCE["data"]

@app.route("/balance")
def balance():
    d = fetch_balance(force=request.args.get("force") == "1")
    prov = llmcfg.guess_provider(DEEPSEEK_BASE)
    if not d:
        return jsonify({"ok": False, "provider": prov, "model": DEEPSEEK_MODEL, "usage": USAGE.snapshot(), "enabled": TRANSLATE_ENABLED})
    out = dict(d, ok=True, provider=prov, model=DEEPSEEK_MODEL, usage=USAGE.snapshot())
    if BALANCE["session_start"] is not None and STATE["state"] != "idle":
        out["session_used"] = max(0.0, BALANCE["session_start"] - d["total"])
    return jsonify(out)


# ---- 录制期间禁止系统进入睡眠(2026-10-01 加:10/1 决策树课 15:29 起笔记本睡眠 75 分钟,转写全丢) ----
def keep_awake_loop(gen):
    """SetThreadExecutionState 是按线程生效的,所以放在一个常驻线程里每 30 秒刷一次;录制/暂停结束后清掉。
    注意:这只能挡住"闲置自动睡眠",合盖睡眠要在电源选项里把"关闭盖子时"改成"不采取任何操作"。"""
    if os.name != "nt":
        return
    import ctypes
    ES_CONTINUOUS, ES_SYSTEM_REQUIRED = 0x80000000, 0x00000001
    k32 = ctypes.windll.kernel32
    try:
        while STATE.get("state") in ("recording", "paused") and STATE.get("gen", 0) >= gen:
            k32.SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)
            time.sleep(30)
    finally:
        k32.SetThreadExecutionState(ES_CONTINUOUS)

def battery_guard(gen):
    """录制/暂停中每 30 秒看一次电池:没插电且低于 BATTERY_WARN 提醒;低于 BATTERY_STOP 自动结束保存。台式机查不到电池就什么都不做。"""
    warned = 0.0
    while STATE.get("state") in ("recording", "paused") and STATE.get("gen", 0) >= gen:
        pct, plugged = session.battery()
        if pct is not None:
            publish({"type": "battery", "pct": pct, "plugged": plugged})
            if plugged is False and pct <= BATTERY_STOP:
                status("电量只剩 %d%% 且没插电,自动结束并保存这节课,防止断电丢记录" % pct, "error")
                _stop_session()
                return
            if plugged is False and pct <= BATTERY_WARN and time.time() - warned > 300:
                warned = time.time()
                status("电量 %d%%,没插电!再不插电,到 %d%% 会自动结束保存" % (pct, BATTERY_STOP), "error")
        time.sleep(30)

PENDING = {"data": None}      # 启动时发现的上次没正常结束的会话

def restore_session(data):
    """把 session/当前.json 里的那节课装回内存,末尾插一行中断标记,供自动保存用。"""
    global seg_counter
    with history_lock:
        history.clear(); history.extend(data["history"])
        seg_counter = max([h["id"] for h in history] or [0])
    SUMMARY["items"] = data.get("summary_items") or []
    SUMMARY["upto"] = data.get("summary_upto") or 0
    QA.clear(); QA.extend(data.get("qa") or [])
    dev_id, name = data.get("device_id"), data.get("device_name", "")
    try:
        devs = list_devices()
    except Exception:
        devs = []
    if devs and not any(d["id"] == dev_id for d in devs):
        pick = next((d for d in devs if d["default"]), devs[0])
        dev_id, name = pick["id"], pick["name"]
        status("上次的声音设备不在了,改用:%s" % name)
    with state_lock:
        STATE.update(state="paused", course=data["course"], start=data["start_dt"],
                     elapsed_base=float(data.get("elapsed", 0)), resume_t=None,
                     device_id=dev_id, device_name=name, gen=STATE["gen"] + 1)
    with history_lock:
        seg_counter += 1
        history.append(session.gap_row(data, seg_counter))
    HEALTH.update(audio=0.0, voice=0.0, seg=time.time(), dropped=0, warned="")
    b = fetch_balance(force=True)
    BALANCE["session_start"] = b["total"] if b else None
    PENDING["data"] = None
    write_live()

def autosave_unfinished():
    """启动时发现上次没正常结束的课(断电/强退),直接存进 Obsidian,不问。用户 2026-10-04 定:续录=结束当前存库,新的一节自己去读上一条。"""
    data = PENDING["data"]
    if not data or STATE["state"] != "idle":
        return
    log("[*] " + session.describe(data) + ",正在自动保存进 Obsidian")
    restore_session(data)
    with state_lock:
        STATE.update(state="saving", resume_t=None, gen=STATE["gen"] + 1)
    publish({"type": "state", **public_state()})
    finish_session()

@app.route("/start", methods=["POST"])
def start():
    data = request.get_json(force=True, silent=True) or {}
    course = data.get("course"); device_id = data.get("device_id")
    if course not in COURSES:
        return jsonify({"ok": False, "msg": "先选是哪门课"}), 400
    if not device_id:
        return jsonify({"ok": False, "msg": "先选声音来源"}), 400
    if not STATE["model_ready"]:
        return jsonify({"ok": False, "msg": "语音识别模型还在加载,稍等几秒"}), 400
    with state_lock:
        if STATE["state"] != "idle":
            return jsonify({"ok": False, "msg": "已经在录了"}), 400
        name = next((d["name"] for d in list_devices() if d["id"] == device_id), device_id)
        STATE.update(state="recording", course=course, start=datetime.datetime.now(),
                     elapsed_base=0.0, resume_t=time.time(), device_id=device_id,
                     device_name=name, gen=STATE["gen"] + 1)
        gen = STATE["gen"]
    HEALTH.update(audio=0.0, voice=0.0, seg=time.time(), dropped=0, warned="")
    threading.Thread(target=audio_capture, args=(gen, device_id), daemon=True).start()
    threading.Thread(target=keep_awake_loop, args=(gen,), daemon=True, name="keep-awake").start()
    threading.Thread(target=battery_guard, args=(gen,), daemon=True, name="battery").start()
    threading.Thread(target=build_context, args=(course,), daemon=True, name="context").start()
    b = fetch_balance(force=True)
    BALANCE["session_start"] = b["total"] if b else None     # 记下开课时余额,算这节课花了多少
    USAGE.reset()
    write_live()
    status("开始录制:%s" % course, "ok")
    publish({"type": "state", **public_state()})
    return jsonify({"ok": True})

@app.route("/pause", methods=["POST"])
def pause():
    with state_lock:
        if STATE["state"] == "recording":
            STATE["elapsed_base"] += time.time() - STATE["resume_t"]
            STATE.update(state="paused", resume_t=None, gen=STATE["gen"] + 1)
        elif STATE["state"] == "paused":
            STATE.update(state="recording", resume_t=time.time(), gen=STATE["gen"] + 1)
            threading.Thread(target=audio_capture, args=(STATE["gen"], STATE["device_id"]), daemon=True).start()
        else:
            return jsonify({"ok": False}), 400
    write_live()
    publish({"type": "state", **public_state()})
    return jsonify({"ok": True})

def _stop_session():
    """结束这节课并后台保存。按钮和电量看门狗都走这里。返回是否真的结束了。"""
    with state_lock:
        if STATE["state"] not in ("recording", "paused"):
            return False
        if STATE["state"] == "recording":
            STATE["elapsed_base"] += time.time() - STATE["resume_t"]
        STATE.update(state="saving", resume_t=None, gen=STATE["gen"] + 1)
    write_live()
    publish({"type": "state", **public_state()})
    threading.Thread(target=finish_session, daemon=True).start()
    return True

@app.route("/stop", methods=["POST"])
def stop():
    if not _stop_session():
        return jsonify({"ok": False}), 400
    return jsonify({"ok": True})

@app.route("/autoqa", methods=["POST"])
def autoqa():
    AUTO_QA["on"] = bool((request.get_json(force=True, silent=True) or {}).get("on"))
    return jsonify({"ok": True, "on": AUTO_QA["on"]})

MY_ASKS = []     # 这节课里点了「写进笔记」的问答,存记录时附在后面

@app.route("/translate", methods=["POST"])
def translate_any():
    """翻译模块 / 划词翻译:任意一段英文 → 中文。"""
    text = ((request.get_json(force=True, silent=True) or {}).get("text") or "").strip()[:4000]
    if not text:
        return jsonify({"ok": False, "msg": "没有要翻的内容"}), 400
    if not TRANSLATE_ENABLED:
        return jsonify({"ok": False, "msg": "没配 DeepSeek key"}), 400
    try:
        zh = llm_chat([{"role": "system", "content": "你是大学课堂同传。把用户给的英文翻成自然的简体中文,专业术语保留英文括注。只输出译文。"},
                       {"role": "user", "content": text}], temperature=0.2, max_tokens=1200)
    except Exception as e:
        return jsonify({"ok": False, "msg": "翻译失败:%s" % str(e)[:100]}), 500
    return jsonify({"ok": True, "zh": zh})

def _ask_course(data):
    with state_lock:
        c = STATE["course"]
    return c or (data or {}).get("course") or "其他"

@app.route("/ask", methods=["POST"])
def ask():
    """AI 问答:带这门课以前的问答记忆 + 本节课转写/笔记。"""
    data = request.get_json(force=True, silent=True) or {}
    q = (data.get("q") or "").strip()[:2000]
    if not q:
        return jsonify({"ok": False, "msg": "问题是空的"}), 400
    if not TRANSLATE_ENABLED:
        return jsonify({"ok": False, "msg": "没配 DeepSeek key"}), 400
    course = _ask_course(data); code = course_code(course)
    with history_lock:
        recent = list(history)[-25:]
    transcript = "\n".join("[%s] %s" % (h["t"], h["en"]) for h in recent if not h.get("gap")) or "(这节课还没开始/没有转写)"
    prompt = (
        "【本课术语】%s\n\n【这门课以前问过的(按时间)】\n%s\n\n【本节课 AI 笔记】\n%s\n\n【最近转写】\n%s\n\n【现在的问题】%s"
    ) % (course_terms(course)[:400], askmem.memory_text(code) or "(还没问过)", summary_md(SUMMARY["items"])[:1500] or "(无)", transcript[-3000:], q)
    sys_msg = ("你是「%s」这门课的学习助手,学生英语一般、专业底子一般。用中文回答,先给结论,再用两三句解释,总共不超过 200 字,"
               "专业术语保留英文。问题和课上刚讲的内容有关时,引用转写里的时间点;以前问过相关问题就接着上次说。不确定就直说。" % course)
    try:
        a = llm_chat([{"role": "system", "content": sys_msg}, {"role": "user", "content": context_prefix() + prompt}], temperature=0.3, max_tokens=700)
    except Exception as e:
        return jsonify({"ok": False, "msg": "问答失败:%s" % str(e)[:100]}), 500
    item = askmem.append(code, q, a, context_note=STATE["course"] or "")
    return jsonify({"ok": True, "id": item["id"], "a": a})

@app.route("/ask/history")
def ask_history():
    course = request.args.get("course") or _ask_course({})
    items = askmem.load(course_code(course), 10)
    return jsonify({"items": [{"id": i["id"], "q": i["q"], "a": i["a"], "saved": i.get("saved", False), "t": i.get("t", "")} for i in items]})

@app.route("/ask/save", methods=["POST"])
def ask_save():
    """把一条问答写进笔记库:<课程文件夹>/<课号>/AI问答.md;录制中还会附进这节课的记录。"""
    data = request.get_json(force=True, silent=True) or {}
    course = _ask_course(data); code = course_code(course)
    item = askmem.mark_saved(code, data.get("id", ""))
    if not item:
        return jsonify({"ok": False, "msg": "找不到这条问答"}), 404
    folder = os.path.join(VAULT_DIR, *[x for x in COURSE_SUB.split("/") if x], safe_name(code))
    os.makedirs(folder, exist_ok=True)
    fpath = os.path.join(folder, "AI问答.md")
    new = not os.path.exists(fpath)
    with open(fpath, "a", encoding="utf-8") as f:
        if new:
            f.write("---\ncourse: %s\ntags: [AI问答, %s]\n---\n\n# %s AI 问答\n\n听课搭子「AI 问答」模块里点「写进笔记」存下来的。\n" % (code, code, code))
        f.write(askmem.note_block(item, course, STATE["course"] and (STATE["start"].strftime("%m-%d %H:%M") + " 课上") or ""))
    if STATE["state"] in ("recording", "paused"):
        MY_ASKS.append(item)
    rel = os.path.relpath(fpath, VAULT_DIR).replace("\\", "/")
    return jsonify({"ok": True, "path": rel, "uri": obsidian_uri(rel)})

@app.route("/slides", methods=["GET"])
def slides_list():
    course = request.args.get("course") or STATE["course"] or "其他"
    return jsonify({"items": slides.list_files(course_code(course))})

@app.route("/slides", methods=["POST"])
def slides_upload():
    """添加课件:pdf / pptx / md / txt,随时可加,下一次总结/提问起生效。"""
    f = request.files.get("file")
    course = request.form.get("course") or STATE["course"] or "其他"
    if not f or not f.filename:
        return jsonify({"ok": False, "msg": "没选文件"}), 400
    import tempfile
    tmp = os.path.join(tempfile.gettempdir(), "tkdz-" + safe_name(f.filename))
    f.save(tmp)
    try:
        r = slides.add(course_code(course), tmp, f.filename)
    except Exception as e:
        return jsonify({"ok": False, "msg": str(e)[:160]}), 400
    finally:
        try:
            os.remove(tmp)
        except Exception:
            pass
    status("课件已加入:%s(%d 页%s)" % (r["name"], r["pages"], (",新术语 %d 个" % len(r["new_terms"])) if r["new_terms"] else ""), "ok")
    return jsonify({"ok": True, **r})

@app.route("/slides/remove", methods=["POST"])
def slides_remove():
    d = request.get_json(force=True, silent=True) or {}
    ok = slides.remove(course_code(d.get("course") or STATE["course"] or "其他"), d.get("name", ""))
    return jsonify({"ok": ok})

ENV_PATH = os.path.join(BASE, ".env")

@app.route("/settings", methods=["GET"])
def settings_get():
    prov = os.getenv("LLM_PROVIDER") or llmcfg.guess_provider(DEEPSEEK_BASE)
    return jsonify({"provider": prov, "base_url": DEEPSEEK_BASE, "model": DEEPSEEK_MODEL, "key_masked": llmcfg.mask(DEEPSEEK_KEY),
                    "has_key": bool(DEEPSEEK_KEY), "enabled": TRANSLATE_ENABLED, "phone_link": PHONE_LINK,
                    "providers": {k: {kk: vv for kk, vv in v.items()} for k, v in llmcfg.PROVIDERS.items()}})

@app.route("/settings/test", methods=["POST"])
def settings_test():
    """用表单里的值(不保存)发一条最小请求,回延迟和模型回的字。"""
    d = request.get_json(force=True, silent=True) or {}
    prov, base_url, model = d.get("provider", "custom"), (d.get("base_url") or "").strip().rstrip("/"), (d.get("model") or "").strip()
    key = (d.get("api_key") or "").strip() or (DEEPSEEK_KEY if d.get("keep_key") else "")
    err = llmcfg.validate(prov, base_url, model, key)
    if err:
        return jsonify({"ok": False, "msg": err}), 400
    try:
        from openai import OpenAI
        c = OpenAI(api_key=key or "ollama", base_url=base_url)
        t = time.time()
        # max_tokens 别给太小:推理模型(deepseek-reasoner 之类)先想再答,8 个 token 会让正文为空,看起来像「不能用」
        r = c.chat.completions.create(model=model, messages=[{"role": "user", "content": "只回复两个字:可以"}], max_tokens=64, temperature=0)
        m = r.choices[0].message
        reply = (m.content or "").strip()[:40] or ("(推理模型,连接正常)" if getattr(m, "reasoning_content", None) else "(空回复)")
        return jsonify({"ok": True, "ms": int((time.time() - t) * 1000), "reply": reply})
    except Exception as e:
        return jsonify({"ok": False, "msg": "连不上:%s" % str(e)[:200]}), 400

@app.route("/settings/models", methods=["POST"])
def settings_models():
    """「列出可用模型」:用表单里的地址和 key 向平台要模型列表(OpenAI 兼容接口都有 /models),省得猜模型名。"""
    d = request.get_json(force=True, silent=True) or {}
    base_url = (d.get("base_url") or "").strip().rstrip("/")
    key = (d.get("api_key") or "").strip() or (DEEPSEEK_KEY if d.get("keep_key") else "")
    if not re.match(r"^https?://", base_url):
        return jsonify({"ok": False, "msg": "先填接口地址"}), 400
    try:
        from openai import OpenAI
        c = OpenAI(api_key=key or "ollama", base_url=base_url)
        ids = sorted({m.id for m in c.models.list().data if getattr(m, "id", "")})
        if not ids:
            return jsonify({"ok": False, "msg": "平台没返回模型列表,去它的文档里查模型名"}), 400
        return jsonify({"ok": True, "models": ids[:200]})
    except Exception as e:
        return jsonify({"ok": False, "msg": "拿不到列表(这家平台可能不支持),去它的文档里查:%s" % str(e)[:120]}), 400


@app.route("/settings", methods=["POST"])
def settings_save():
    """保存到 .env 并热生效(翻译/总结/问答下一次调用起用新模型)。"""
    global DEEPSEEK_KEY, DEEPSEEK_BASE, DEEPSEEK_MODEL, PHONE_LINK
    d = request.get_json(force=True, silent=True) or {}
    prov, base_url, model = d.get("provider", "custom"), (d.get("base_url") or "").strip().rstrip("/"), (d.get("model") or "").strip()
    key = (d.get("api_key") or "").strip() or (DEEPSEEK_KEY if d.get("keep_key") else "")
    err = llmcfg.validate(prov, base_url, model, key)
    if err:
        return jsonify({"ok": False, "msg": err}), 400
    if "phone_link" in d:
        PHONE_LINK = (d.get("phone_link") or "").strip()
        if PHONE_LINK and not re.match(r"^https?://\S+[?&]k=\S+$", PHONE_LINK):
            return jsonify({"ok": False, "msg": "手机版链接格式不对,应该是 https://…/live/?k=…(从手机版页面复制整条链接)"}), 400
    llmcfg.write_env(ENV_PATH, {"LLM_PROVIDER": prov, "DEEPSEEK_API_KEY": key, "DEEPSEEK_BASE_URL": base_url, "DEEPSEEK_MODEL": model, "PHONE_LINK": PHONE_LINK})
    os.environ["LLM_PROVIDER"] = prov
    DEEPSEEK_KEY, DEEPSEEK_BASE, DEEPSEEK_MODEL = key, base_url, model
    build_llm()
    BALANCE["data"] = None; BALANCE["t"] = 0.0
    status("模型设置已保存:%s · %s,下一次调用起生效" % (llmcfg.PROVIDERS.get(prov, {}).get("name", prov), model), "ok")
    publish({"type": "state", **public_state()})
    return jsonify({"ok": True, "enabled": TRANSLATE_ENABLED})

@app.route("/handoff", methods=["POST"])
def handoff():
    """把正在录的这节课原样移交给手机版:转写/总结/提问 POST 过去,电脑这边结束但不存库(手机那边最后一起存),只留一份备份。"""
    link = ((request.get_json(force=True, silent=True) or {}).get("link") or PHONE_LINK).strip()
    if not link:
        return jsonify({"ok": False, "msg": "先在「设置」里填手机版专属链接"}), 400
    with state_lock:
        if STATE["state"] not in ("recording", "paused"):
            return jsonify({"ok": False, "msg": "没有在录的课"}), 400
        course, start = STATE["course"], STATE["start"]
        if STATE["state"] == "recording":
            STATE["elapsed_base"] += time.time() - STATE["resume_t"]
        STATE.update(state="paused", resume_t=None, gen=STATE["gen"] + 1)     # 先停收音,把队列里的最后几句识别完
    publish({"type": "state", **public_state()})
    t_end = time.time() + 10
    while time.time() < t_end and (audio_q.qsize() or text_q.qsize()):
        time.sleep(0.3)
    el = elapsed()
    with history_lock:
        rows = [dict(h) for h in history if not h.get("gap")]
    payload = {"course": course, "start": start.strftime("%Y-%m-%d %H:%M:%S"), "elapsed": round(el, 1), "rows": rows,
               "summary": list(SUMMARY["items"]), "upto": SUMMARY["upto"], "qa": list(QA), "from": "desktop"}
    base = link.split("?", 1)[0].rstrip("/")
    if base.endswith("/index.html"):
        base = base[:-len("/index.html")]
    k = urllib.parse.parse_qs(urllib.parse.urlparse(link).query).get("k", [""])[0]
    try:
        req = urllib.request.Request(base + "/handoff?k=" + urllib.parse.quote(k), data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                                     headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=40) as r:
            j = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            j = json.loads(e.read().decode("utf-8"))
        except Exception:
            j = {"ok": False, "msg": "手机版服务器回应 %s" % e.code}
    except Exception as e:
        j = {"ok": False, "msg": "连不上手机版服务器:%s" % str(e)[:120]}
    if not j.get("ok"):
        status("移交失败:%s。这边继续录着,点「继续」" % j.get("msg", ""), "error")
        return jsonify({"ok": False, "msg": j.get("msg", "移交失败")}), 502
    # 手机那边收下了:这边结束,不写 Obsidian,留一份备份
    with state_lock:
        STATE.update(state="saving", resume_t=None, gen=STATE["gen"] + 1)
    write_live()
    try:
        os.makedirs(BACKUP_DIR, exist_ok=True)
        bp = os.path.join(BACKUP_DIR, "已移交手机_%s_%s.md" % (course_code(course), datetime.datetime.now().strftime("%Y-%m-%d_%H%M%S")))
        with open(bp, "w", encoding="utf-8") as f:
            f.write("# %s 已移交手机(%s 句,已录 %s)\n\n" % (course, len(rows), fmt_t(el)) + "\n".join("**%s** %s  \n> %s\n" % (h.get("t", ""), h.get("en", ""), h.get("zh", "")) for h in rows))
    except Exception as e:
        log("[warn] 移交备份失败:%s" % e)
    session.clear()
    status("已移交到手机(%d 句,%s)。手机上打开你的专属链接,点「继续」接着录;整节课最后由手机那边存进 Obsidian" % (len(rows), fmt_t(el)), "ok")
    reset_session()
    publish({"type": "state", **public_state()})
    return jsonify({"ok": True, "n": len(rows), "elapsed": el})


@app.route("/context", methods=["GET"])
def context_get():
    """「看看 AI 知道什么」:当前上下文包全文。没在上课时按给的课号现场打一份看。"""
    course = request.args.get("course") or STATE["course"]
    if STATE["state"] == "idle" and course:
        r = context_pack.build(course_code(course), course, BASE, VAULT_DIR, record_sub=RECORD_SUB, course_sub=COURSE_SUB)
        return jsonify({"on": CONTEXT["on"], "text": r["text"], "sources": r["sources"], "tokens": r["tokens_est"], "live": False})
    return jsonify({"on": CONTEXT["on"], "text": CONTEXT["text"], "sources": CONTEXT["sources"], "tokens": CONTEXT["tokens"], "live": True})

@app.route("/context", methods=["POST"])
def context_set():
    CONTEXT["on"] = bool((request.get_json(force=True, silent=True) or {}).get("on"))
    status("「了解我」已%s,下一次总结/提问起生效" % ("打开" if CONTEXT["on"] else "关闭"))
    publish({"type": "state", **public_state()})
    return jsonify({"ok": True, "on": CONTEXT["on"]})

@app.route("/open", methods=["POST"])
def open_uri():
    uri = (request.get_json(force=True, silent=True) or {}).get("uri", "")
    if uri.startswith("obsidian://"):
        os.startfile(uri)
    return jsonify({"ok": True})

@app.route("/stream")
def stream():
    def gen():
        q = queue.Queue(maxsize=5000)
        with sub_lock:
            subscribers.append(q)
        yield "data: " + json.dumps({"type": "state", **public_state()}, ensure_ascii=False) + "\n\n"
        if STATE["status"]["msg"]:
            yield "data: " + json.dumps({"type": "status", **STATE["status"]}, ensure_ascii=False) + "\n\n"
        with history_lock:
            snap = list(history)
        for h in snap:
            yield "data: " + json.dumps(dict(type="seg", **h), ensure_ascii=False) + "\n\n"
        if SUMMARY["items"]:
            yield "data: " + json.dumps({"type": "summary", "items": SUMMARY["items"]}, ensure_ascii=False) + "\n\n"
        for qa_item in list(QA):     # 别叫 q——q 是下面的消息队列
            yield "data: " + json.dumps(dict(type="qa", **qa_item), ensure_ascii=False) + "\n\n"
        try:
            while True:
                try:
                    yield q.get(timeout=15)
                except queue.Empty:
                    yield ": ping\n\n"
        except GeneratorExit:
            pass
        finally:
            with sub_lock:
                if q in subscribers:
                    subscribers.remove(q)
    return Response(gen(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no",
                             "Connection": "keep-alive"})

def watchdog():
    """录制中每 5 秒检查一次:声音数据断了 / 有人说话但很久没新字幕 / 识别跟不上丢段,都在界面上提醒并写日志。"""
    last_drop = 0
    while True:
        time.sleep(5)
        if STATE["state"] != "recording":
            HEALTH["warned"] = ""; continue
        now = time.time()
        started = STATE.get("resume_t") or now
        msg = ""
        if now - started > 15 and now - max(HEALTH["audio"], started) > 15:
            msg = "已经 %d 秒没收到声音数据了:检查麦克风/声音来源,程序在自动重连" % int(now - max(HEALTH["audio"], started))
        elif now - started > 240 and now - HEALTH["voice"] < 20 and now - max(HEALTH["seg"], started) > 180:
            msg = "有声音但 3 分钟没有新字幕,识别可能卡住了。可以暂停再继续试试;不行就结束保存后重启程序"
        elif HEALTH["dropped"] > last_drop + 3:
            msg = "识别跟不上,最近丢了 %d 段声音(电脑太忙?可以关掉别的大程序)" % (HEALTH["dropped"] - last_drop)
            last_drop = HEALTH["dropped"]
        if msg and msg != HEALTH["warned"]:
            HEALTH["warned"] = msg
            status(msg, "error")
        elif not msg and HEALTH["warned"]:
            HEALTH["warned"] = ""
            status("转写恢复正常", "ok")

def forever(fn):
    """工作线程的保险:里面抛了没接住的异常,把完整报错写进日志、在界面上提示,1 秒后重新进入,线程不会悄悄死掉。
    函数正常返回(比如没配 key 时翻译线程直接退出)就结束。"""
    def run():
        while True:
            try:
                fn()
                return
            except Exception:
                log("[error] 线程 %s 出错,1 秒后自动重启:\n%s" % (fn.__name__, traceback.format_exc()))
                try:
                    status("后台线程 %s 出错,已自动重启(详见日志)" % fn.__name__, "error")
                except Exception:
                    pass
                time.sleep(1)
    return run

def start_threads():
    threading.Thread(target=whisper_worker,  daemon=True).start()      # 它自己有兜底,不包(重进会重新加载模型)
    threading.Thread(target=forever(translate_worker), daemon=True, name="translate_worker").start()
    threading.Thread(target=forever(summary_worker),   daemon=True, name="summary_worker").start()
    threading.Thread(target=forever(qa_worker),        daemon=True, name="qa_worker").start()
    threading.Thread(target=forever(watchdog),         daemon=True, name="watchdog").start()
    threading.Thread(target=forever(live_writer),      daemon=True, name="live_writer").start()

session.init(BASE)
askmem.init(BASE)
slides.init(BASE)

if __name__ == "__main__":
    backup_stale_live()
    PENDING["data"] = session.load()
    if PENDING["data"]:
        threading.Thread(target=forever(autosave_unfinished), daemon=True, name="autosave").start()
    if "--no-audio" not in sys.argv:      # 冒烟测试用:只起网页,不加载模型
        start_threads()
    if "--no-browser" not in sys.argv:
        def open_browser():
            time.sleep(1.5)
            try:
                webbrowser.open("http://127.0.0.1:%d" % PORT)
            except Exception:
                pass
        threading.Timer(0.1, open_browser).start()
    if not TRANSLATE_ENABLED:
        print("[!] 未检测到 DeepSeek key,现在只会显示英文字幕。")
    print("[*] 打开 http://127.0.0.1:%d" % PORT)
    # 2026-09-28:多个实例挤在同一端口(werkzeug 允许重复绑定),浏览器会连到旧的卡死实例。端口被占就拒绝启动。
    import socket as _socket
    _probe = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
    _probe.settimeout(0.5)
    if _probe.connect_ex(("127.0.0.1", PORT if "PORT" in globals() else 5000)) == 0:
        _probe.close()
        log("[error] 端口已被另一个听课搭子占用,本实例退出。先关掉旧窗口或运行 启动.bat(会自动清理)。")
        raise SystemExit(1)
    _probe.close()
    app.run(host="127.0.0.1", port=PORT, threaded=True, use_reloader=False)
