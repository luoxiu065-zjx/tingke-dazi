# -*- coding: utf-8 -*-
# ------------------------------------------------------------------
# 听课搭子 (tingke-dazi) · Copyright (c) 2026 luoxiu065-zjx
# 原始仓库:https://github.com/luoxiu065-zjx/tingke-dazi
# 转载、二次发布请保留本版权声明和原作者署名。build: tkdz-1bf2795e
# ------------------------------------------------------------------
"""
听课搭子 —— 英文课实时转写 + 逐句中文翻译 + AI 按主题总结 + 老师提问提醒 + 按课程存成 Markdown(可直接放进 Obsidian)
声音来源可选:麦克风(线下课)/ 电脑播放的声音(网课、录播)
每识别一句就刷新 live.md,方便其他 AI 工具实时读取课堂内容。
许可证:PolyForm Noncommercial 1.0.0(免费个人/学习使用,禁止商用)
"""
import os, sys, json, time, queue, threading, re, glob, webbrowser, datetime, shutil, urllib.parse

# pip 装的 CUDA 库(nvidia-cublas/cudnn)在 venv 里,要同时加进 DLL 搜索目录和 PATH,否则 cuda 模式找不到 cublas64_12.dll
for _d in glob.glob(os.path.join(sys.prefix, "Lib", "site-packages", "nvidia", "*", "bin")):
    os.add_dll_directory(_d)
    os.environ["PATH"] = _d + os.pathsep + os.environ.get("PATH", "")

BASE = os.path.dirname(os.path.abspath(__file__))
os.environ.setdefault("HF_HOME", os.path.join(BASE, "models"))   # 语音模型下载到程序旁边的 models 文件夹
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")

import numpy as np
import soundcard as sc          # 必须在主线程最先 import(它自己初始化 COM);其他线程再用 com_init()
from dotenv import load_dotenv
from flask import Flask, Response, send_from_directory, jsonify, request

load_dotenv()

# ---------------- 配置 ----------------
APP_NAME = "听课搭子"
APP_VERSION = "0.1.0"
APP_HOME = "https://github.com/luoxiu065-zjx/tingke-dazi"
APP_BUILD = "tkdz-1bf2795e"   # 暗记:用来在网上搜索未署名的复制版本
LIVE_FILE  = os.path.join(BASE, "live.md")                 # 给 Claude 读的实时转录
BACKUP_DIR = os.path.join(BASE, "课堂记录备份")              # 上次没正常结束的 live.md 挪到这里

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

# 记录存哪:填了 OBSIDIAN_VAULT 就存进库里的 RECORD_SUBDIR;没填就存到程序旁边的「课堂记录」文件夹
VAULT_DIR   = os.getenv("OBSIDIAN_VAULT", "").strip().strip('"')
RECORD_SUB  = os.getenv("RECORD_SUBDIR", "课堂记录").strip().strip("/\\") or "课堂记录"
RECORD_ROOT = os.path.join(VAULT_DIR, *RECORD_SUB.split("/")) if VAULT_DIR else os.path.join(BASE, "课堂记录")
COURSES = [c.strip() for c in re.split(r"[,，]", os.getenv("COURSES", "示例课程 机器学习,其他")) if c.strip()]

SAMPLE_RATE = 16000
BLOCK       = 1600          # 0.1s
SILENCE_RMS = float(os.getenv("SILENCE_RMS", "0.008"))
SILENCE_HANG = 0.7
MIN_SEG      = 0.8
MAX_SEG      = 12.0
SUMMARY_EVERY = 40          # 秒

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
    print(line)
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
if TRANSLATE_ENABLED:
    try:
        from openai import OpenAI
        llm_client = OpenAI(api_key=DEEPSEEK_KEY, base_url=DEEPSEEK_BASE)
    except Exception as e:
        print("[!] LLM 初始化失败，只显示英文：", e)
        TRANSLATE_ENABLED = False

def llm_chat(messages, temperature=0.3, max_tokens=800):
    resp = llm_client.chat.completions.create(
        model=DEEPSEEK_MODEL, messages=messages,
        temperature=temperature, max_tokens=max_tokens, stream=False)
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
    """soundcard 走 Windows COM，每个用到它的线程都要先初始化一次，否则报 0x800401f0。重复调用无害。"""
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
    status("正在加载语音识别模型(%s)，请稍候…" % WHISPER_MODEL)
    try:
        model = WhisperModel(WHISPER_MODEL, device=WHISPER_DEVICE, compute_type=WHISPER_COMPUTE)
    except Exception as e:
        status("模型加载失败：%s" % e, "error"); return
    STATE["model_ready"] = True
    status("语音识别就绪(%s)。选好课程和声音来源，点「开始录制」。" % ("显卡加速" if WHISPER_DEVICE == "cuda" else "CPU 模式，准确率会低一些"), "ok")
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
                            if not (s.no_speech_prob > 0.6 and s.avg_logprob < -1.0)).strip()
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
        write_live()
        if TRANSLATE_ENABLED:
            text_q.put({"id": item["id"], "en": text})

# ---------------- 翻译 ----------------
# ---------------- 自动答疑:老师提问 → 中英文参考回答 ----------------
AUTO_QA = {"on": os.getenv("AUTO_QA", "0") == "1"}   # 默认关闭:学习辅助,正式考试/口语评估时不要开
QA = []                      # [{id, seg_id, t, question_en, question_zh, answer_zh, answer_en}]
qa_q = queue.Queue()
PREP = {"text": os.getenv("COURSE_NOTES", "")}   # 可选:课前资料文字,给提问提醒当背景
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
        transcript = ("【上文，只用来理解，不要判断也不要回答】\n" +
                      "\n".join("[%s] %s" % (h["t"], h["en"]) for h in before) +
                      "\n\n【待判断的句子】\n" + "\n".join("[%s] %s" % (h["t"], h["en"]) for h in focus))
        answered = "; ".join(q["question_en"] for q in QA[-5:]) or "无"
        prompt = (
            "你是留学生的课堂学习助手，正在实时听一节英文课（课程：%s）。下面是课堂转写（机器识别，会有听错的词）。\n"
            "只判断【待判断的句子】里是否有**需要回答的真问题**：老师向全班提问、老师点名提问、或同学提了一个值得知道答案的问题。"
            "口头禅(right? okay?)、自问自答后老师马上说出了答案、修辞性提问、组织课堂的问话(can you hear me? is the mic working? any questions?)都算否。"
            "注意:转写常把问句识别成句号结尾、或把一个问题切成前后两段;同学提问常以 right? / is it? 结尾求确认,这些都可能是真问题。"
            "老师说「who can give me…」「you can give me another one」「what do you think」这类邀请回答的句子也算提问。"
            "上文里的问题不算；和已回答过的问题(" + answered.replace("%", "%%") + ")是同一个的也算否。\n"
            "只输出 JSON:{\"is_question\":true/false,\"question_en\":\"问题原句（纠正听错的词）\",\"question_zh\":\"一句中文说明在问什么\","
            "\"answer_zh\":\"中文答案要点，不超过 80 字，带关键公式或术语\",\"answer_en\":\"课堂上能直接说出口的英文回答：1-2 句、不超过 35 个词、口语化，先说结论\"}。\n"
            "is_question 为 false 时其他字段留空。答案要基于课程常识和下面的课件/预习资料，不确定就说明。\n\n"
            "【本课术语】%s\n【课前资料节选】%s\n【课中 AI 笔记】%s\n\n【最近转写】\n%s"
        ) % (STATE["course"] or "", course_terms(STATE["course"])[:400], PREP["text"][:2500],
             summary_md(SUMMARY["items"])[:1500], transcript)
        try:
            j = parse_json(llm_chat([{"role": "user", "content": prompt}], temperature=0.2, max_tokens=700), "{")
        except Exception as e:
            print("[qa] 出错：", e); continue
        if not j or not j.get("is_question") or not j.get("answer_en"):
            continue
        item = {"id": len(QA) + 1, "seg_id": sid, "t": focus[0]["t"],
                "question_en": j.get("question_en", ""), "question_zh": j.get("question_zh", ""),
                "answer_zh": j.get("answer_zh", ""), "answer_en": j.get("answer_en", "")}
        QA.append(item); last_answered = time.time()
        publish(dict(type="qa", **item))
        write_live()

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
        try:
            with history_lock:
                ctx = [h["en"] for h in history if h["id"] < item["id"]][-3:]
            zh = llm_chat([
                {"role": "system", "content": "你是大学课堂同传。把用户给的英文课堂口语翻成自然的简体中文，专业术语保留英文括注。只输出译文，不要解释、不要引号。"
                                              + ("\n上文（仅供理解，不要翻译）:" + " ".join(ctx) if ctx else "")},
                {"role": "user", "content": item["en"]},
            ], temperature=0.2, max_tokens=400)
        except Exception:
            zh = "（翻译失败，请检查 DeepSeek key 或网络）"
        with history_lock:
            for h in history:
                if h["id"] == item["id"]:
                    h["zh"] = zh; break
        publish({"type": "seg_zh", "id": item["id"], "zh": zh})
        write_live()

# ---------------- 整节课 AI 总结(增量) ----------------
summary_lock = threading.Lock()

def update_summary():
    """把上次总结之后的新内容并进总结。返回是否有更新。"""
    if not TRANSLATE_ENABLED:
        return False
    with summary_lock:
        with history_lock:
            new = [h for h in history if h["id"] > SUMMARY["upto"]]
        if not new:
            return False
        upto = new[-1]["id"]
        transcript = "\n".join("[%s] %s" % (h["t"], h["en"]) for h in new)
        prompt = (
            "你在给一节英文大学课程做实时中文笔记。下面是【已有笔记】(JSON)和【新的课堂内容】。"
            "把新内容并进笔记，输出更新后的完整笔记 JSON 数组："
            "[{\"topic\":\"一句中文概括这一部分在讲什么\",\"points\":[{\"k\":\"关键词（4-8字）\",\"v\":\"一句中文说明\"}]}]。\n"
            "规则：按讲课顺序分主题；新内容延续上一主题就往里加要点，换话题就开新主题；每个主题 2-6 条要点；"
            "专业术语保留英文；寒暄、点名、设备调试之类不记；只输出 JSON。\n\n"
            "【已有笔记】\n" + json.dumps(SUMMARY["items"], ensure_ascii=False) +
            "\n\n【新的课堂内容】\n" + transcript
        )
        try:
            raw = llm_chat([{"role": "user", "content": prompt}], temperature=0.3, max_tokens=3000)
        except Exception:
            return False
        items = parse_json(raw, "[")
        if not isinstance(items, list):
            return False
        SUMMARY["items"] = items
        SUMMARY["upto"] = upto
    publish({"type": "summary", "items": items})
    write_live()
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

def write_live():
    with state_lock:
        st = dict(STATE)
    if st["state"] == "idle" or not st["course"]:
        return
    with history_lock:
        rows = list(history)
    label = {"recording": "录制中", "paused": "已暂停", "saving": "正在保存"}.get(st["state"], st["state"])
    lines = ["# 实时课堂转录(%s)" % APP_NAME,
             "",
             "- 课程：%s" % st["course"],
             "- 开始：%s" % st["start"].strftime("%Y-%m-%d %H:%M"),
             "- 状态：%s · 已录 %s · 文件更新于 %s" % (label, fmt_t(elapsed()), datetime.datetime.now().strftime("%H:%M:%S")),
             "- 声音来源：%s" % st["device_name"],
             "",
             "## AI 总结（约每 40 秒更新）",
             "",
             summary_md(SUMMARY["items"]) or "（还没有）",
             "",
             "## 课上提问与参考回答（自动答疑）",
             "",
             qa_md() or "（还没有）",
             "",
             "## 转写（越往下越新；格式 [课内时间 | 墙钟时间]）",
             ""]
    for h in rows:
        lines.append("[%s | %s] %s" % (h["t"], h["clock"], h["en"]))
        if h["zh"]:
            lines.append("    中：" + h["zh"])
    with live_lock:
        tmp = LIVE_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        os.replace(tmp, LIVE_FILE)

def backup_stale_live():
    """启动时如果 live.md 里还有上次没保存的课，挪进备份文件夹，防止丢。"""
    if not os.path.exists(LIVE_FILE):
        return
    try:
        with open(LIVE_FILE, encoding="utf-8") as f:
            txt = f.read()
    except Exception:
        return
    if "状态：已保存" in txt or "## 转写" not in txt:
        return
    os.makedirs(BACKUP_DIR, exist_ok=True)
    dst = os.path.join(BACKUP_DIR, "未正常结束_%s.md" % datetime.datetime.now().strftime("%Y-%m-%d_%H%M%S"))
    shutil.move(LIVE_FILE, dst)
    print("[*] 上次的课没正常结束，转录已备份到", dst)

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
                "根据下面这节课的内容，起一个 8-20 字的中文标题，概括这节课讲了什么。"
                "只能依据给出的内容，不要编造没出现的主题。只输出标题。\n\n"
                + material[:3000]}], temperature=0.2, max_tokens=60).strip().strip('"“”《》')
        except Exception:
            pass
    code = course_code(course)
    folder = os.path.join(RECORD_ROOT, safe_name(code))
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
             "source: 听课搭子",
             "generator: \"%s %s (%s) %s\"" % (APP_NAME, APP_VERSION, APP_HOME, APP_BUILD),
             "deepseek_cost_cny: %s" % session_cost(),
             "tags: [课堂记录，%s]" % code,
             "---", "",
             "# " + title, "",
             "录制 %s – %s · 时长 %s · 课程 %s" % (
                 start.strftime("%Y-%m-%d %H:%M"), end.strftime("%H:%M"), dur, course), "",
             "## AI 总结", "", summary_md(SUMMARY["items"]) or "（无）", "",
             "## 课上提问与参考回答", "", qa_md() or "（无）", "",
             "## 全程转写（中英对照）", ""]
    for h in rows:
        lines.append("**%s** · %s  " % (h["t"], h["clock"]))
        lines.append(h["en"] + "  ")
        if h["zh"]:
            lines.append("> " + h["zh"])
        lines.append("")
    lines += ["", "---", "*本笔记由 [%s](%s) 生成 · 机器转写和翻译可能有误,重要内容以课件和老师原话为准*" % (APP_NAME, APP_HOME)]
    with open(fpath, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return fpath, len(rows)

def session_cost():
    """这节课 DeepSeek 花了多少（开课余额 - 现在余额）；查不到就写「未知」。"""
    b = fetch_balance(force=True)
    if not b or BALANCE["session_start"] is None:
        return "未知"
    return "%.2f" % max(0.0, BALANCE["session_start"] - b["total"])

def finish_session():
    try:
        fpath, n = save_to_obsidian()
        rel = os.path.relpath(fpath, RECORD_ROOT).replace("\\", "/")
        with live_lock:
            try:
                with open(LIVE_FILE, "a", encoding="utf-8") as f:
                    f.write("\n- 状态：已保存 → %s\n" % rel)
            except Exception:
                pass
        publish({"type": "saved", "path": rel, "n": n, "uri": open_uri_for(fpath)})
        status("已保存：%s（%d 句）" % (rel, n), "ok")
    except Exception as e:
        status("保存失败：%s（转录还在 live.md 里）" % e, "error")
    finally:
        reset_session()
        publish({"type": "state", **public_state()})

def reset_session():
    global seg_counter
    with history_lock:
        history.clear()
        seg_counter = 0
    SUMMARY["items"] = []; SUMMARY["upto"] = 0
    QA.clear()
    with state_lock:
        STATE.update(state="idle", course=None, start=None, elapsed_base=0.0, resume_t=None)

def open_uri_for(fpath):
    """在 Obsidian 库里就用 obsidian:// 打开，否则用系统默认程序打开 .md 文件。"""
    if VAULT_DIR:
        rel = os.path.relpath(fpath, VAULT_DIR).replace("\\", "/")
        return "obsidian://open?vault=%s&file=%s" % (
            urllib.parse.quote(os.path.basename(VAULT_DIR.rstrip("\\/"))), urllib.parse.quote(rel))
    return "file:" + fpath

# ---------------- Web ----------------
def public_state():
    with state_lock:
        return {"state": STATE["state"], "course": STATE["course"],
                "start": STATE["start"].strftime("%Y-%m-%d %H:%M:%S") if STATE["start"] else None,
                "device_id": STATE["device_id"], "model_ready": STATE["model_ready"],
                "translate": TRANSLATE_ENABLED, "auto_qa": AUTO_QA["on"],
                "device": WHISPER_DEVICE, "model": WHISPER_MODEL, "obsidian": bool(VAULT_DIR)} | {"elapsed": elapsed()}

@app.route("/")
def index():
    return send_from_directory(BASE, "index.html")

@app.route("/config")
def config():
    try:
        devices = list_devices()
    except Exception as e:
        devices = []; status("读取声音设备失败：%s" % e, "error")
    return jsonify({"courses": COURSES, "devices": devices, "state": public_state(), "record_root": RECORD_ROOT, "app": {"name": APP_NAME, "version": APP_VERSION, "home": APP_HOME, "build": APP_BUILD}})

@app.route("/records")
def records():
    groups = {}
    for fp in glob.glob(os.path.join(RECORD_ROOT, "*", "*.md")):
        groups.setdefault(os.path.basename(os.path.dirname(fp)), []).append(fp)
    out = []
    for g in sorted(groups):
        items = []
        for fp in sorted(groups[g], reverse=True):
            name = os.path.splitext(os.path.basename(fp))[0]
            m = re.match(r"(\d{4}-\d{2}-\d{2} \d{4})\s*(.*)", name)
            items.append({"when": m.group(1) if m else "", "title": (m.group(2) if m else name),
                          "uri": open_uri_for(fp)})
        out.append({"group": g, "items": items})
    return jsonify(out)

BALANCE = {"t": 0.0, "data": None, "session_start": None}

def fetch_balance(force=False):
    """DeepSeek 账户余额（元），缓存 60 秒。"""
    if not TRANSLATE_ENABLED:
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
        print("[!] 查余额失败：", e)
    return BALANCE["data"]

@app.route("/balance")
def balance():
    d = fetch_balance(force=request.args.get("force") == "1")
    if not d:
        return jsonify({"ok": False})
    out = dict(d, ok=True)
    if BALANCE["session_start"] is not None and STATE["state"] != "idle":
        out["session_used"] = max(0.0, BALANCE["session_start"] - d["total"])
    return jsonify(out)

@app.route("/start", methods=["POST"])
def start():
    data = request.get_json(force=True, silent=True) or {}
    course = data.get("course"); device_id = data.get("device_id")
    if course not in COURSES:
        return jsonify({"ok": False, "msg": "先选是哪门课"}), 400
    if not device_id:
        return jsonify({"ok": False, "msg": "先选声音来源"}), 400
    if not STATE["model_ready"]:
        return jsonify({"ok": False, "msg": "语音识别模型还在加载，稍等几秒"}), 400
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
    b = fetch_balance(force=True)
    BALANCE["session_start"] = b["total"] if b else None     # 记下开课时余额,算这节课花了多少
    write_live()
    status("开始录制：%s" % course, "ok")
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

@app.route("/stop", methods=["POST"])
def stop():
    with state_lock:
        if STATE["state"] not in ("recording", "paused"):
            return jsonify({"ok": False}), 400
        if STATE["state"] == "recording":
            STATE["elapsed_base"] += time.time() - STATE["resume_t"]
        STATE.update(state="saving", resume_t=None, gen=STATE["gen"] + 1)
    write_live()
    publish({"type": "state", **public_state()})
    threading.Thread(target=finish_session, daemon=True).start()
    return jsonify({"ok": True})

@app.route("/autoqa", methods=["POST"])
def autoqa():
    AUTO_QA["on"] = bool((request.get_json(force=True, silent=True) or {}).get("on"))
    publish({"type": "state", **public_state()})
    return jsonify({"ok": True, "on": AUTO_QA["on"]})

@app.route("/open", methods=["POST"])
def open_uri():
    uri = (request.get_json(force=True, silent=True) or {}).get("uri", "")
    if uri.startswith("obsidian://"):
        os.startfile(uri)
    elif uri.startswith("file:"):
        fp = os.path.abspath(uri[5:])
        if fp.startswith(os.path.abspath(RECORD_ROOT)) and fp.endswith(".md") and os.path.exists(fp):   # 只允许打开课堂记录
            os.startfile(fp)
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

def start_threads():
    threading.Thread(target=whisper_worker,  daemon=True).start()
    threading.Thread(target=translate_worker, daemon=True).start()
    threading.Thread(target=summary_worker,  daemon=True).start()
    threading.Thread(target=qa_worker,       daemon=True).start()
    threading.Thread(target=watchdog,        daemon=True).start()

if __name__ == "__main__":
    backup_stale_live()
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
        print("[!] 未检测到 DeepSeek key，现在只会显示英文字幕。在 .env 里填 DEEPSEEK_API_KEY 后重启。")
    print("[*] 语音识别：%s / %s / %s" % (WHISPER_MODEL, WHISPER_DEVICE, WHISPER_COMPUTE))
    print("[*] 记录保存在：%s" % RECORD_ROOT)
    print("[*] %s v%s · %s" % (APP_NAME, APP_VERSION, APP_HOME))
    print("[*] 打开 http://127.0.0.1:%d" % PORT)
    app.run(host="127.0.0.1", port=PORT, threaded=True, use_reloader=False)
