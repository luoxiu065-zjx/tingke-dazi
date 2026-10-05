# -*- coding: utf-8 -*-
# ------------------------------------------------------------------
# 听课搭子 (tingke-dazi) · Copyright (c) 2026 luoxiu065-zjx
# 原始仓库:https://github.com/luoxiu065-zjx/tingke-dazi
# 转载、二次发布请保留本版权声明和原作者署名。build: tkdz-1bf2795e
# ------------------------------------------------------------------
"""听课搭子 · 手机 / 平板网页版后端(多人版)

手机浏览器录麦克风 → 按停顿切成小段上传 → Groq whisper-large-v3-turbo 转写 → DeepSeek 翻译/总结/答疑
→ SSE 推回手机 → 结束时写成 Markdown:托管 WebDAV(/udav/<uid>/)或他自己的 WebDAV,本地也留 7 天可下载。
每个用户一把钥匙、自己的 key、自己的课程表;每人同一时刻录一节课,不同用户互不影响。
没填 key 的新用户可用 .env 里的公共 key 试用 TRIAL_SECONDS 秒(不想开放试用就把 GROQ_API_KEY 留空)。断网/关页面不影响,重开页面自动接上。
"""
import os, sys, io, json, time, queue, threading, re, glob, datetime, uuid, subprocess, shutil, traceback
from flask import Flask, request, jsonify, Response, send_from_directory
from dotenv import load_dotenv
from openai import OpenAI
import engine, textkit, users as usersmod
import askmem, slides, llmcfg, context_pack, urllib.request, urllib.parse
from contextlib import contextmanager
from flask import g, abort, send_file

BASE = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE, ".env"))
TOKEN = os.getenv("ACCESS_TOKEN", "").strip()
GROQ_KEY = os.getenv("GROQ_API_KEY", "").strip()
GROQ_MODEL = os.getenv("GROQ_MODEL", "whisper-large-v3")      # 2026-10-05:非 turbo 版更准,Groq 免费档额度相同;要快可在 .env 改回 whisper-large-v3-turbo
DS_KEY = os.getenv("DEEPSEEK_API_KEY", "").strip()
DS_BASE = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
DS_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")
VAULT = os.getenv("VAULT_DIR", os.path.join(BASE, "records", "owner"))         # 部署者自己(owner 账号)的记录目录,建议指到你的 Obsidian 同步目录
DAV_ROOT = os.getenv("DAV_ROOT", "/srv/udav")                                    # 托管 WebDAV:每个用户一个子目录,nginx /udav/<uid>/ 指到这里
DAV_AUTH_DIR = os.getenv("DAV_AUTH_DIR", "/srv/udav-auth")                       # 每个用户一份 htpasswd,只给 nginx 读
PUBLIC_BASE = os.getenv("PUBLIC_BASE_URL", "")                                    # 对外地址,如 https://tingke.example.com(空=按请求的 Host)
RECORD_SUB = os.getenv("RECORD_SUBDIR", "课堂记录")
TERM_START = datetime.date.fromisoformat(os.getenv("TERM_START", "2026-09-21"))
COURSES = [c.strip() for c in os.getenv("COURSES", "COMP0001 示例课程,其他").split(",") if c.strip()]   # owner 账号的课;其他用户在页面里自己填
PORT = int(os.getenv("PORT", "8095"))
SUMMARY_EVERY = 40
DATA = os.path.join(BASE, "data", "sessions"); os.makedirs(DATA, exist_ok=True)
LOGS = os.path.join(BASE, "logs"); os.makedirs(LOGS, exist_ok=True)
TERMS_DIR = os.path.join(BASE, "术语")

def groq_client(key):
    return OpenAI(api_key=key, base_url="https://api.groq.com/openai/v1", default_headers={"User-Agent": "tingke-dazi-live/0.2"})   # Cloudflare 拦默认 UA(403 错误 1010)


def ds_client(key, base=None):
    return OpenAI(api_key=key, base_url=base or DS_BASE) if key else None


PUB_GROQ = groq_client(GROQ_KEY) if GROQ_KEY else None     # 公共 key:owner 账号用 + 新用户试用
PUB_DS = ds_client(DS_KEY)
app = Flask(__name__, static_folder=None)
# users 模块在 load_dotenv 之前就 import 了,试用额度两个值要在这里按 .env 再设一次
usersmod.TRIAL_SECONDS = int(os.getenv("TRIAL_SECONDS", "1800")); usersmod.TRIAL_DAILY_CAP = int(os.getenv("TRIAL_DAILY_CAP", "18000"))
TRIAL_CONCURRENT = int(os.getenv("TRIAL_CONCURRENT", "3"))      # 同时在录的试用课上限:Groq 免费档每个模型每分钟 20 次请求,一个人约 6–7 次/分钟
ALT_MODEL = {"whisper-large-v3": "whisper-large-v3-turbo", "whisper-large-v3-turbo": "whisper-large-v3"}


def groq_transcribe(client, data, ext, prompt):
    """先用默认模型;被限流(429)就换另一个模型(额度分开算),都限流就等几秒再试一次。其他错误照常抛出。"""
    models = [GROQ_MODEL, ALT_MODEL.get(GROQ_MODEL, GROQ_MODEL)]
    last = None
    for attempt in range(3):
        m = models[min(attempt, 1)]
        try:
            return client.audio.transcriptions.create(file=("chunk." + ext, data), model=m, language="en",
                                                      prompt=prompt, response_format="verbose_json", temperature=0)
        except Exception as e:
            last = e
            if "429" not in str(e) and "rate" not in str(e).lower():
                raise
            if attempt == 1:
                time.sleep(6)
    raise last


def trial_busy(u):
    """新开一节试用课前:已经有 TRIAL_CONCURRENT 节试用课在录,就请他填自己的 key。"""
    if u.get("owner") or u.get("groq_key"):
        return False
    with S_LOCK:
        n = sum(1 for x in SESSIONS.values() if x.trial and x.state in ("recording", "paused"))
    return n >= TRIAL_CONCURRENT


BUSY_MSG = "试用通道现在人满(同时最多 %d 人在录)。花 1 分钟申请一个免费的 Groq key 填进设置,就不用排队了"
USERS = usersmod.Users(os.path.join(BASE, "data"), DAV_ROOT, DAV_AUTH_DIR, PUBLIC_BASE)


def log(msg):
    line = "%s %s" % (datetime.datetime.now().strftime("%H:%M:%S"), msg)
    try:
        print(line, flush=True)
    except Exception:
        pass
    try:
        with open(os.path.join(LOGS, "live-%s.log" % datetime.date.today().isoformat()), "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def llm_with(client, model, messages, temperature=0.3, max_tokens=800):
    r = client.chat.completions.create(model=model, messages=messages, temperature=temperature, max_tokens=max_tokens, stream=False)
    return r.choices[0].message.content.strip()


def week_of(dt):
    return (dt.date() - TERM_START).days // 7 + 1


def course_terms(course, user=None):
    words = []
    dirs = [TERMS_DIR] + ([udir(user, "术语")] if user else [])
    for d in dirs:
        try:
            with open(os.path.join(d, engine.course_code(course) + ".txt"), encoding="utf-8") as f:
                words += [w.strip() for w in re.split(r"[,\n]", f.read()) if w.strip() and not w.startswith("#")]
        except Exception:
            pass
    seen = set()
    return ", ".join(w for w in words if not (w.lower() in seen or seen.add(w.lower())))


# ---------------- 每个用户自己的数据目录 + 电脑版模块(问答记忆/课件)按用户切换 ----------------
def udir(u, sub=""):
    d = os.path.join(BASE, "data", "users", u["uid"], sub) if sub else os.path.join(BASE, "data", "users", u["uid"])
    os.makedirs(d, exist_ok=True)
    return d


MODLOCK = threading.RLock()


@contextmanager
def umods(u):
    """askmem / slides 是电脑版的单用户模块(全局目录),这里按用户把目录切过去再用,用完放锁。"""
    with MODLOCK:
        askmem.DIR = udir(u, "问答"); slides.DIR = udir(u, "课件"); slides.TERMS_DIR = udir(u, "术语")
        yield


def auto_qa_on(u):
    return bool(u.get("auto_qa", bool(u.get("owner"))))      # owner 账号默认开(和电脑版一样);新用户默认关,在「老师提问」开关里打开


def context_on(u):
    return bool(u.get("context_on", True))


# ---------------- 用户级推送:「电脑布局」页面订阅这个,不管有没有在上课都收得到 ----------------
USER_SUBS = {}
US_LOCK = threading.Lock()


def user_publish(uid, data):
    with US_LOCK:
        qs = list(USER_SUBS.get(uid, []))
    for q in qs:
        try:
            q.put_nowait(data)
        except Exception:
            pass


def prev_lesson_text(user, course):
    """上节课讲到哪:这个用户记录目录里这门课最新的一条记录(AI 总结那节)。"""
    code = engine.course_code(course)
    out = []
    folder = USERS.record_targets(user, RECORD_SUB, engine.safe_name(code))[0]
    files = [f for f in glob.glob(os.path.join(folder, "*.md")) if "整合笔记" not in os.path.basename(f)]
    if files:
        latest = max(files)
        try:
            out.append(engine.extract_prev_lesson(open(latest, encoding="utf-8").read()))
        except Exception:
            pass
    return "\n\n".join(x for x in out if x)


def forever(fn):
    def run(*a):
        while True:
            try:
                fn(*a); return
            except Exception:
                log("[error] %s:\n%s" % (fn.__name__, traceback.format_exc())); time.sleep(1)
    return run


# ---------------- 会话 ----------------
class Session:
    def __init__(self, user, course, start=None, sid=None, restore=None):
        self.sid = sid or uuid.uuid4().hex[:10]
        self.user = user; self.uid = user["uid"]
        self.course = course
        # 模型客户端:有自己的 key 用自己的;没有就用公共 key 试用(owner 账号无限制)
        self.trial = not user.get("owner") and not user.get("groq_key")
        self.groq = groq_client(user["groq_key"]) if user.get("groq_key") else PUB_GROQ
        self.ds = ds_client(user["ds_key"], user.get("ds_base")) if user.get("ds_key") else (PUB_DS if (user.get("owner") or self.trial) else None)
        self.ds_model = user.get("ds_model") or DS_MODEL
        self.trial_warned = False
        self.start = start or datetime.datetime.now()
        self.state = "recording"
        self.rows, self.summary, self.upto, self.qa = [], [], 0, []
        self.seg = 0
        self.elapsed = 0.0
        self.lock = threading.RLock()
        self.subs, self.sub_lock = [], threading.Lock()
        self.chunks, self.next_seq, self.cv = {}, 0, threading.Condition()
        self.tq, self.qaq = queue.Queue(), queue.Queue()
        self.dirty = threading.Event()
        self.last_answered = 0.0
        self.terms = course_terms(course, user)
        self.prev = ""
        self.ctx = {"text": "", "sources": [], "tokens": 0}
        self.my_asks = []
        self.max_seq = -1
        self.bal_start = None
        if restore:
            self.rows = restore.get("rows", []); self.summary = restore.get("summary", []); self.upto = restore.get("upto", 0)
            self.qa = restore.get("qa", []); self.elapsed = restore.get("elapsed", 0.0)
            self.seg = max([r["id"] for r in self.rows] or [0]); self.next_seq = restore.get("next_seq", 0)
        else:
            threading.Thread(target=self._load_prev, daemon=True).start()
        threading.Thread(target=self.build_context, daemon=True).start()
        for fn, name in ((self.chunk_worker, "chunk"), (self.translate_worker, "translate"), (self.summary_worker, "summary"),
                         (self.qa_worker, "qa"), (self.persist_worker, "persist")):
            threading.Thread(target=forever(fn), daemon=True, name="%s-%s" % (name, self.sid)).start()

    def llm(self, messages, temperature=0.3, max_tokens=800):
        return llm_with(self.ds, self.ds_model, messages, temperature, max_tokens)

    def build_context(self):
        """「了解我」:这门课最近两节课的记录等,打包放进总结/提问/问答的提示词最前面(和电脑版同一个模块)。"""
        if not context_on(self.user):
            return
        try:
            u = self.user; mode = u["sync"].get("mode")
            if mode == "owner":
                vault, rs, cs = VAULT, RECORD_SUB, "课程"
            elif mode == "hosted":
                vault, rs, cs = USERS.hosted_vault_dir(u), usersmod.USER_SUB, "课程"
            else:
                vault, rs, cs = USERS.local_record_dir(u), "", "课程"
            r = context_pack.build(engine.course_code(self.course), self.course, udir(u), vault, record_sub=rs, course_sub=cs)
            self.ctx = {"text": r["text"], "sources": r["sources"], "tokens": r["tokens_est"]}
            self.publish({"type": "context", "on": True, "sources": r["sources"], "tokens": r["tokens_est"]})
        except Exception as e:
            log("[%s] 了解我 打包失败:%s" % (self.sid, e))

    def prefix(self):
        return (self.ctx["text"] + "\n\n") if context_on(self.user) and self.ctx.get("text") else ""

    def slides_sel(self, text, limit=1500):
        try:
            with umods(self.user):
                return slides.select(engine.course_code(self.course), text, limit=limit) or ""
        except Exception:
            return ""

    def _load_prev(self):
        try:
            self.prev = prev_lesson_text(self.user, self.course)
        except Exception as e:
            log("[warn] 读上节课失败:%s" % e)

    # --- 推送 ---
    def publish(self, ev):
        data = "data: " + json.dumps(ev, ensure_ascii=False) + "\n\n"
        with self.sub_lock:
            for q in list(self.subs):
                try:
                    q.put_nowait(data)
                except Exception:
                    pass
        if ev.get("type") == "state":
            push_state(self.uid)
        else:
            user_publish(self.uid, data)

    def status(self, msg, level="info"):
        log("[%s][%s] %s" % (self.sid, level, msg)); self.publish({"type": "status", "msg": msg, "level": level})

    # --- 收音频段 ---
    def add_chunk(self, seq, t0, dur, mime, data):
        with self.cv:
            self.chunks[seq] = (t0, dur, mime, data)
            self.max_seq = max(self.max_seq, seq)
            self.elapsed = max(self.elapsed, t0 + dur)
            self.cv.notify_all()

    def chunk_worker(self):
        while self.state in ("recording", "paused") or self.chunks:
            with self.cv:
                deadline = time.time() + 3
                while self.next_seq not in self.chunks and self.state in ("recording", "paused"):
                    if self.chunks and time.time() > deadline:       # 等了 3 秒还没等到前一段:跳过它
                        self.next_seq = min(self.chunks); break
                    self.cv.wait(timeout=0.5)
                if self.next_seq not in self.chunks:
                    if self.state not in ("recording", "paused") and not self.chunks:
                        return
                    if self.chunks:
                        self.next_seq = min(self.chunks)
                    else:
                        continue
                t0, dur, mime, data = self.chunks.pop(self.next_seq); self.next_seq += 1
            self.transcribe(t0, dur, mime, data)

    def transcribe(self, t0, dur, mime, data):
        ext = "mp4" if "mp4" in mime else "ogg" if "ogg" in mime else "wav" if "wav" in mime else "webm"
        with self.lock:
            prev = " ".join(h["en"] for h in self.rows[-3:])
        prev = re.sub(r"(\s*\.){2,}", ".", prev); prev = re.sub(r"\s+", " ", prev).strip()[-200:]
        prompt = (("University lecture. Terms: %s. " % self.terms[:350]) if self.terms else "University lecture. ") + prev
        if self.groq is None:
            if not self.trial_warned:
                self.trial_warned = True; self.status("还没填 Groq key,识别不了。到设置里填一下", "error")
            return
        if self.trial:
            if not USERS.trial_ok(self.user, dur):
                if not self.trial_warned:
                    self.trial_warned = True; self.status("试用额度用完了。到设置里填自己的 Groq 和 DeepSeek key(都免费申请),就能继续", "error")
                return
            USERS.trial_add(self.user, dur)
        try:
            r = groq_transcribe(self.groq, data, ext, prompt.strip())
            segs = getattr(r, "segments", None) or []
            if segs:
                text = " ".join(s.get("text", "").strip() if isinstance(s, dict) else (s.text or "").strip() for s in segs
                                if not ((s.get("no_speech_prob", 0) if isinstance(s, dict) else getattr(s, "no_speech_prob", 0)) > 0.6 and
                                        (s.get("avg_logprob", 0) if isinstance(s, dict) else getattr(s, "avg_logprob", 0)) < -1.0))
            else:
                text = (getattr(r, "text", "") or "").strip()
        except Exception as e:
            self.status("识别出错,跳过这一段:%s" % str(e)[:120], "error"); return
        text = engine.clean_text(text)
        if not text:
            return
        with self.lock:
            self.seg += 1
            row = {"id": self.seg, "t": engine.fmt_t(t0), "clock": datetime.datetime.now().strftime("%H:%M:%S"), "en": text, "zh": ""}
            self.rows.append(row)
        self.publish(dict(type="seg", **row)); self.dirty.set()
        if self.ds and auto_qa_on(self.user) and engine.looks_like_question(text):
            self.qaq.put(row["id"])
        if self.ds:
            self.tq.put(row)

    # --- 翻译 ---
    def translate_worker(self):
        if not self.ds:
            return
        while True:
            item = self.tq.get()
            batch = [item]
            while self.tq.qsize() >= 2 and len(batch) < 6:
                try:
                    batch.append(self.tq.get_nowait())
                except queue.Empty:
                    break
            with self.lock:
                ctx = [h["en"] for h in self.rows if h["id"] < batch[0]["id"]][-3:]
            sys_msg = engine.TRANSLATE_SYS + ("\n上文(仅供理解,不要翻译):" + " ".join(ctx) if ctx else "")
            done = {}
            if len(batch) > 1:
                try:
                    raw = self.llm([{"role": "system", "content": sys_msg + "\n用户会给编号的多句,逐句翻译,输出 JSON 数组:[{\"i\":编号,\"zh\":\"译文\"}],只输出 JSON。"},
                               {"role": "user", "content": textkit.batch_prompt(batch)}], temperature=0.2, max_tokens=400 * len(batch))
                    done = textkit.parse_batch(raw, batch)
                except Exception:
                    done = {}
            for it in batch:
                zh = done.get(it["id"])
                if not zh:
                    try:
                        zh = self.llm([{"role": "system", "content": sys_msg}, {"role": "user", "content": it["en"]}], temperature=0.2, max_tokens=400)
                    except Exception:
                        zh = "(翻译失败)"
                with self.lock:
                    it["zh"] = zh
                self.publish({"type": "seg_zh", "id": it["id"], "zh": zh})
            self.dirty.set()

    # --- 总结 ---
    def update_summary(self):
        if not self.ds:
            return False
        with self.lock:
            new = [h for h in self.rows if h["id"] > self.upto]
            items = list(self.summary)
        if not new:
            return False
        try:
            sl = self.slides_sel("\n".join(h["en"] for h in new), 1000)
            prompt = engine.summary_prompt(items, new) + (("\n\n【课件节选,用来对照术语和主题,别照抄】\n" + sl) if sl else "")
            raw = self.llm([{"role": "user", "content": self.prefix() + prompt}], temperature=0.3, max_tokens=3000)
        except Exception as e:
            log("[%s][summary] 模型出错:%s" % (self.sid, str(e)[:160])); return False
        got = engine.parse_json(raw, "[")
        if not isinstance(got, list):
            log("[%s][summary] 返回不是列表:%s" % (self.sid, raw[:120])); return False
        if not got:
            # 内容还太少,模型给了空列表:这几句不算「已总结」,下一轮连同新句子一起再看;第一次给页面一句解释
            if not getattr(self, "_sum_empty_told", False):
                self._sum_empty_told = True
                self.publish({"type": "status", "msg": "内容还太少,总结先不出;讲到有要点时会自动出现", "level": "info"})
            return False
        with self.lock:
            self.summary = got; self.upto = new[-1]["id"]
        self.publish({"type": "summary", "items": got}); self.dirty.set()
        return True

    def summary_worker(self):
        while self.state in ("recording", "paused"):
            time.sleep(SUMMARY_EVERY)
            if self.state == "recording":
                self.update_summary()

    # --- 答疑 ---
    def qa_worker(self):
        while True:
            sid = first = self.qaq.get()
            t_start = time.time()
            time.sleep(4)
            with self.lock:
                txt = next((h["en"] for h in self.rows if h["id"] == sid), "")
            need, limit = (2, 20) if engine.QA_MORE.search(txt) else (1, 8)
            while True:
                while not self.qaq.empty():
                    sid = max(sid, self.qaq.get_nowait())
                with self.lock:
                    last_id = self.rows[-1]["id"] if self.rows else 0
                if last_id >= sid + need or time.time() - t_start >= limit:
                    break
                time.sleep(0.5)
            with self.lock:
                focus = [h for h in self.rows if first <= h["id"] <= sid + 2][-6:]
                before = [h for h in self.rows if focus and h["id"] < focus[0]["id"]][-10:]
                answered = "; ".join(q["question_en"] for q in self.qa[-5:])
                notes = engine.summary_md(self.summary)
            if not focus or (time.time() - self.last_answered < 8 and self.qa and self.qa[-1]["seg_id"] >= first - 2):
                continue
            try:
                sl = self.slides_sel(" ".join(h["en"] for h in before + focus))
                prev_bg = self.prev + (("\n【课件节选】\n" + sl) if sl else "")
                j = engine.parse_json(self.llm([{"role": "user", "content": self.prefix() + engine.qa_prompt(self.course, self.terms, prev_bg, notes, before, focus, answered)}],
                                          temperature=0.2, max_tokens=700), "{")
            except Exception as e:
                log("[qa] 出错:%s" % e); continue
            if not j or not j.get("is_question") or not j.get("answer_en"):
                continue
            item = {"id": len(self.qa) + 1, "seg_id": sid, "t": focus[0]["t"], "question_en": j.get("question_en", ""),
                    "question_zh": j.get("question_zh", ""), "answer_zh": j.get("answer_zh", ""), "answer_en": j.get("answer_en", "")}
            with self.lock:
                self.qa.append(item)
            self.last_answered = time.time()
            self.publish(dict(type="qa", **item)); self.dirty.set()

    # --- 落盘 ---
    def snapshot(self):
        with self.lock:
            return {"sid": self.sid, "uid": self.uid, "course": self.course, "start": self.start.isoformat(timespec="seconds"), "state": self.state,
                    "elapsed": self.elapsed, "next_seq": self.next_seq, "rows": [dict(r) for r in self.rows],
                    "summary": list(self.summary), "upto": self.upto, "qa": list(self.qa),
                    "saved_at": datetime.datetime.now().isoformat(timespec="seconds")}

    def persist(self):
        p = os.path.join(DATA, self.sid + ".json")
        with open(p + ".tmp", "w", encoding="utf-8") as f:
            json.dump(self.snapshot(), f, ensure_ascii=False)
        os.replace(p + ".tmp", p)

    def persist_worker(self):
        while self.state in ("recording", "paused", "saving"):
            if self.dirty.wait(timeout=2):
                self.dirty.clear()
            if self.state in ("recording", "paused"):
                self.persist()
            time.sleep(1.5)

    # --- 结束 ---
    def finish(self, interrupted_at=None):
        if self.state == "saved":
            return
        self.state = "saving"
        self.publish({"type": "state", "state": "saving"})
        t_end = time.time() + 20
        while time.time() < t_end and (self.chunks or not self.tq.empty()):
            time.sleep(0.5)
        time.sleep(1)
        if interrupted_at:
            with self.lock:
                self.seg += 1
                self.rows.append({"id": self.seg, "t": engine.fmt_t(self.elapsed), "clock": datetime.datetime.now().strftime("%H:%M:%S"),
                                  "en": "[程序在 %s 意外中断,之后的内容没录到]" % interrupted_at, "zh": "", "gap": True})
        self.update_summary()
        end = datetime.datetime.now()
        with self.lock:
            rows = list(self.rows); items = list(self.summary); qa = list(self.qa)
        dur = engine.fmt_t(self.elapsed)
        title = "简短录音" if len(rows) < 8 else "课堂记录"
        if self.ds and len(rows) >= 8:
            try:
                title = engine.sane_title(self.llm([{"role": "user", "content": engine.title_prompt(engine.summary_md(items) or "\n".join(h["en"] for h in rows))}],
                                              temperature=0.2, max_tokens=60))
            except Exception:
                pass
        code = engine.course_code(self.course)
        stem = "%s %s %s" % (self.start.strftime("%Y-%m-%d %H%M"), code, engine.safe_name(title))
        md = engine.record_md(self.course, self.start, end, dur, title, items, qa, rows)
        if self.my_asks:
            block = "## 我的提问(AI 问答)\n\n" + "".join("**问:** %s\n\n%s\n\n" % (it["q"], it["a"]) for it in self.my_asks)
            md = md.replace("## 全程转写", block + "## 全程转写", 1) if "## 全程转写" in md else md + "\n" + block
        targets = USERS.record_targets(self.user, RECORD_SUB, engine.safe_name(code))
        fpath = None; written = []
        for k, folder in enumerate(targets):
            p = os.path.join(folder, stem + ".md")
            try:
                os.makedirs(folder, exist_ok=True)
                with open(p, "w", encoding="utf-8") as f:
                    f.write(md)
                os.chmod(p, 0o664)
                written.append(p)
                if fpath is None:
                    fpath = p
            except Exception as e:
                log("[%s] 写 %s 失败:%s" % (self.sid, folder, str(e)[:160]))
                if k == 0 and len(targets) == 1:
                    # 唯一的目标都写不进去:别丢数据,留着落盘文件,重启后会再试
                    self.state = "paused"; self.persist(); push_state(self.uid)
                    self.status("保存失败:%s。转写没丢,修好后重启会自动补存" % str(e)[:120], "error")
                    log("[%s] 保存失败:\n%s" % (self.sid, traceback.format_exc()))
                    return
        if fpath is None:
            self.state = "paused"; self.persist(); push_state(self.uid)
            self.status("保存失败,转写没丢,修好后重启会自动补存", "error"); return
        mode = self.user["sync"].get("mode")
        if mode == "owner":
            rel = os.path.relpath(fpath, VAULT).replace("\\", "/"); where = "Obsidian " + rel
        elif mode == "hosted":
            rel = os.path.relpath(fpath, USERS.hosted_dir(self.user)).replace("\\", "/"); where = "你的同步文件夹 " + rel + "(手机 Obsidian 同步一下就有)"
        elif mode == "webdav":
            try:
                rel = USERS.push_webdav(self.user, RECORD_SUB, engine.safe_name(code), stem + ".md", md); where = "已推到你的网盘 " + rel
            except Exception as e:
                rel = stem + ".md"; where = "推到网盘失败(%s),已留在服务器,可在「我的记录」里下载" % str(e)[:80]
                log("[%s] webdav push 失败:%s" % (self.sid, e))
        else:
            rel = stem + ".md"; where = "已留在服务器,可在「我的记录」里下载"
        self.state = "saved"
        try:
            os.remove(os.path.join(DATA, self.sid + ".json"))
        except Exception:
            pass
        uri = obsidian_uri(fpath) if mode == "owner" else ""
        self.publish({"type": "saved", "path": rel, "where": where, "n": len(rows), "uri": uri,
                      "download": "record?f=" + urllib.parse.quote(engine.safe_name(code) + "/" + stem + ".md")})
        push_state(self.uid)
        log("[%s] 已保存 %s(%d 句)→ %s" % (self.sid, stem, len(rows), where))



SESSIONS = {}
S_LOCK = threading.Lock()


def active_session(uid):
    with S_LOCK:
        for s in SESSIONS.values():
            if s.uid == uid and s.state in ("recording", "paused"):
                return s
    return None


def user_session(uid):
    """这个用户正在录 / 暂停 / 保存中的那节课(电脑布局用,含保存中)。"""
    with S_LOCK:
        c = [x for x in SESSIONS.values() if x.uid == uid and x.state in ("recording", "paused", "saving")]
    return max(c, key=lambda x: x.start) if c else None


def user_ds(u):
    """(客户端, 模型):自己的 key 优先;owner 账号和试用用户用公共 key。"""
    if u.get("ds_key"):
        return ds_client(u["ds_key"], u.get("ds_base")), (u.get("ds_model") or DS_MODEL)
    if u.get("owner") or not u.get("groq_key"):
        return PUB_DS, DS_MODEL
    return None, DS_MODEL


def desk_state(u):
    s = user_session(u["uid"])
    cl, _ = user_ds(u)
    st = {"state": s.state if s else "idle", "course": s.course if s else None,
          "start": s.start.strftime("%Y-%m-%d %H:%M:%S") if s else None, "device_id": "mic", "model_ready": True,
          "translate": bool(s.ds if s else cl), "auto_qa": auto_qa_on(u), "context_on": context_on(u),
          "context_sources": s.ctx["sources"] if s else [], "context_tokens": s.ctx["tokens"] if s else 0,
          "elapsed": round(s.elapsed, 1) if s else 0, "sid": s.sid if s else None,
          "next_seq": max(s.max_seq + 1, s.next_seq) if s else 0}
    return st


def push_state(uid):
    u = USERS.get(uid)
    if u:
        user_publish(uid, "data: " + json.dumps(dict(type="state", **desk_state(u)), ensure_ascii=False) + "\n\n")


def obsidian_uri(fpath):
    rel = os.path.relpath(fpath, VAULT).replace("\\", "/")
    return "obsidian://open?vault=%s&file=%s" % (urllib.parse.quote(os.path.basename(VAULT.rstrip("/"))), urllib.parse.quote(rel))


def my_session(sid):
    s = SESSIONS.get(sid or "")
    return s if s and s.uid == g.user["uid"] else None


def autosave_unfinished():
    """启动时把上次没正常结束的会话直接存进同步目录(用户 10/4 定:不问,直接存)。"""
    for p in sorted(glob.glob(os.path.join(DATA, "*.json"))):
        try:
            d = json.load(open(p, encoding="utf-8"))
            if d.get("state") not in ("recording", "paused", "saving") or not d.get("rows"):
                os.remove(p); continue
            u = USERS.get(d.get("uid") or "owner")      # 账号已删就丢掉,千万别落到 owner 的库里(10/6 测试账号删了后,它的半节课被存进了 owner 的 Obsidian)
            if not u:
                log("[*] 会话 %s 的账号 %s 已不存在,丢弃" % (d.get("sid"), d.get("uid")))
                os.remove(p); continue
            s = Session(u, d["course"], start=datetime.datetime.fromisoformat(d["start"]), sid=d["sid"], restore=d)
            with S_LOCK:
                SESSIONS[s.sid] = s
            log("[*] 上次 %s 没正常结束(%d 句),自动保存" % (d["course"], len(d["rows"])))
            s.finish(interrupted_at=d.get("saved_at", "")[11:16])
        except Exception:
            log("[error] 自动保存失败:\n" + traceback.format_exc())


# ---------------- 接口 ----------------
OPEN_PATHS = ("/live", "/live/index.html", "/live/health", "/live/register", "/live/desk")


@app.before_request
def _auth():
    if request.path.rstrip("/") in OPEN_PATHS or request.path.startswith("/live/static/"):
        return None
    k = request.args.get("k") or request.form.get("k") or (request.get_json(silent=True) or {}).get("k")
    g.user = USERS.get_by_key(k)
    if not g.user:
        return jsonify({"ok": False, "msg": "钥匙不对或已失效"}), 401


@app.route("/live/")
@app.route("/live/index.html")
def index():
    return send_from_directory(os.path.join(BASE, "static"), "index.html")


@app.route("/live/desk")
def desk_page():
    """电脑布局:电脑版页面原样(static/desk.html 由 make_desk.py 从电脑版生成),钥匙从本机浏览器里拿。"""
    r = send_from_directory(os.path.join(BASE, "static"), "desk.html")
    r.headers["Cache-Control"] = "no-cache"
    return r


@app.route("/live/static/<path:name>")
def static_file(name):
    return send_from_directory(os.path.join(BASE, "static"), name)


@app.route("/live/health")
def health():
    return jsonify({"ok": True, "groq": bool(PUB_GROQ), "deepseek": bool(PUB_DS), "users": len(USERS.users)})


REG_IP = {}


@app.route("/live/register", methods=["POST"])
def register():
    ip = request.headers.get("X-Real-IP") or request.remote_addr or "?"
    day = datetime.date.today().isoformat()
    n = REG_IP.get((ip, day), 0)
    if n >= 20:
        return jsonify({"ok": False, "msg": "今天注册太多次了"}), 429
    REG_IP[(ip, day)] = n + 1
    u = USERS.register((request.get_json(silent=True) or {}).get("name", ""))
    log("[reg] %s from %s" % (u["uid"], ip))
    return jsonify({"ok": True, "k": u["key"], "uid": u["uid"]})


@app.route("/live/config")
def config():
    s = active_session(g.user["uid"])
    return jsonify({"me": USERS.public(g.user, request.host), "courses": g.user["courses"],
                    "active": ({"sid": s.sid, "course": s.course, "elapsed": s.elapsed, "start": s.start.strftime("%Y-%m-%d %H:%M")} if s else None)})


@app.route("/live/settings", methods=["POST"])
def settings():
    USERS.update(g.user, request.get_json(force=True, silent=True) or {})
    if g.user["sync"].get("mode") == "hosted":
        try:
            USERS.ensure_hosted(g.user)
        except Exception as e:
            log("[settings] ensure_hosted 失败:%s" % e)
    return jsonify({"ok": True, "me": USERS.public(g.user, request.host)})


@app.route("/live/keys/test", methods=["POST"])
def keys_test():
    """填完 key 当场测:groq 列模型,deepseek 发一个字。"""
    j = request.get_json(force=True, silent=True) or {}
    out = {}
    gk = (j.get("groq_key") or "").strip() or g.user.get("groq_key")
    dk = (j.get("ds_key") or "").strip() or g.user.get("ds_key")
    if j.get("which", "both") in ("both", "groq"):
        if not gk:
            out["groq"] = [False, "没填"]
        else:
            try:
                groq_client(gk).models.list(); out["groq"] = [True, "能用"]
            except Exception as e:
                out["groq"] = [False, "不对:%s" % str(e)[:100]]
    if j.get("which", "both") in ("both", "ds"):
        if not dk:
            out["ds"] = [False, "没填"]
        else:
            try:
                # max_tokens 别太小:推理模型先想再答,太小正文为空;这里只看有没有报错
                llm_with(ds_client(dk, j.get("ds_base") or g.user.get("ds_base")), j.get("ds_model") or g.user.get("ds_model") or DS_MODEL,
                         [{"role": "user", "content": "回复一个字:好"}], max_tokens=64); out["ds"] = [True, "能用"]
            except Exception as e:
                msg = str(e)
                out["ds"] = [False, "余额不足,去充几块钱" if "402" in msg or "Insufficient" in msg else "不对:%s" % msg[:100]]
    return jsonify({"ok": True, "result": out})


@app.route("/live/sync/test", methods=["POST"])
def sync_test():
    j = request.get_json(force=True, silent=True) or {}
    w = dict(j.get("webdav") or {})
    if not w.get("pass") and (g.user["sync"].get("webdav") or {}).get("pass"):
        w["pass"] = g.user["sync"]["webdav"]["pass"]
    ok, msg = USERS.test_webdav(w)
    return jsonify({"ok": ok, "msg": msg})


@app.route("/live/sync/testnote", methods=["POST"])
def sync_testnote():
    """发一份测试笔记到他的同步位置,让他在手机 Obsidian 里确认收到。"""
    now = datetime.datetime.now()
    md = "# 听课搭子 测试笔记\n\n%s 发出。你在 Obsidian 里看到这一页,说明同步通了。\n这一页可以删。\n" % now.strftime("%Y-%m-%d %H:%M")
    fname = "测试 %s.md" % now.strftime("%Y-%m-%d %H%M")
    mode = g.user["sync"].get("mode")
    try:
        if mode == "webdav":
            rel = USERS.push_webdav(g.user, RECORD_SUB, "测试", fname, md); where = "已推到你的网盘 " + rel
        else:
            folder = USERS.record_targets(g.user, RECORD_SUB, "测试")[0]
            os.makedirs(folder, exist_ok=True)
            with open(os.path.join(folder, fname), "w", encoding="utf-8") as f:
                f.write(md)
            os.chmod(os.path.join(folder, fname), 0o664)
            where = "已放进你的同步文件夹,手机 Obsidian 同步一下" if mode == "hosted" else "已留在服务器,可在「我的记录」下载"
        return jsonify({"ok": True, "msg": where})
    except Exception as e:
        return jsonify({"ok": False, "msg": "失败:%s" % str(e)[:140]})


@app.route("/live/records")
def records():
    if g.user.get("owner"):
        return jsonify({"ok": True, "items": []})
    return jsonify({"ok": True, "items": USERS.list_local(g.user)})


@app.route("/live/record")
def record():
    rel = (request.args.get("f") or "").replace("\\", "/")
    if g.user.get("owner") or not rel or ".." in rel:
        abort(404)
    p = os.path.normpath(os.path.join(USERS.local_record_dir(g.user), rel))
    if not p.startswith(os.path.normpath(USERS.local_record_dir(g.user))) or not os.path.isfile(p):
        abort(404)
    return send_file(p, as_attachment=True, download_name=os.path.basename(p), mimetype="text/markdown")


@app.route("/live/prev")
def prev():
    course = request.args.get("course", "")
    return jsonify({"text": prev_lesson_text(g.user, course) if course in g.user["courses"] else ""})


@app.route("/live/start", methods=["POST"])
def start():
    course = (request.get_json(force=True, silent=True) or {}).get("course")
    if course not in g.user["courses"]:
        return jsonify({"ok": False, "msg": "先选是哪门课"}), 400
    s = active_session(g.user["uid"])
    if s:
        return jsonify({"ok": True, "sid": s.sid, "resumed": True, "course": s.course, "elapsed": s.elapsed})
    if not g.user.get("owner") and not g.user.get("groq_key") and not USERS.trial_ok(g.user, 1):
        return jsonify({"ok": False, "msg": "试用额度用完了,到设置里填自己的 Groq 和 DeepSeek key 再开始"}), 402
    if not PUB_GROQ and not g.user.get("groq_key"):
        return jsonify({"ok": False, "msg": "还没填 Groq key"}), 400
    if trial_busy(g.user):
        return jsonify({"ok": False, "msg": BUSY_MSG % TRIAL_CONCURRENT}), 429
    s = Session(g.user, course)
    with S_LOCK:
        SESSIONS[s.sid] = s
    s.persist(); push_state(s.uid)
    log("[%s] 开始:%s(%s%s)" % (s.sid, course, s.uid, " 试用" if s.trial else ""))
    return jsonify({"ok": True, "sid": s.sid, "resumed": False, "trial": s.trial})


@app.route("/live/handoff", methods=["POST"])
def handoff():
    """电脑版把正在录的这节课移交过来:转写/总结/提问原样带上,这边建一节「暂停中」的课;手机打开链接自动接上,点「继续」接着录。"""
    j = request.get_json(force=True, silent=True) or {}
    if active_session(g.user["uid"]):
        return jsonify({"ok": False, "msg": "手机这边已经有一节课在录,先结束它再移交"}), 409
    course = re.sub(r"\s+", " ", str(j.get("course") or "其他")).strip()[:60]
    try:
        start = datetime.datetime.fromisoformat(str(j.get("start", ""))[:19].replace(" ", "T"))
    except Exception:
        start = datetime.datetime.now()
    elapsed = max(0.0, float(j.get("elapsed") or 0))
    rows = [dict(r) for r in (j.get("rows") or []) if isinstance(r, dict) and r.get("en")]
    for i, r in enumerate(rows, 1):
        r.setdefault("id", i); r.setdefault("t", ""); r.setdefault("clock", ""); r.setdefault("zh", "")
    nid = max([int(r.get("id", 0)) for r in rows] or [0]) + 1
    rows.append({"id": nid, "t": engine.fmt_t(elapsed), "clock": datetime.datetime.now().strftime("%H:%M:%S"),
                 "en": "[从电脑版移交到手机,接着录]", "zh": "", "gap": True})
    restore = {"rows": rows, "summary": j.get("summary") or [], "upto": int(j.get("upto") or 0), "qa": j.get("qa") or [],
               "elapsed": elapsed, "next_seq": 0}
    if course not in g.user["courses"]:
        USERS.update(g.user, {"courses": g.user["courses"] + [course]})
    s = Session(g.user, course, start=start, restore=restore)
    s.state = "paused"
    with S_LOCK:
        SESSIONS[s.sid] = s
    s.persist()
    log("[%s] 从电脑版移交:%s,%d 句,已录 %.0f 秒(%s)" % (s.sid, course, len(rows) - 1, elapsed, s.uid))
    return jsonify({"ok": True, "sid": s.sid, "n": len(rows) - 1, "elapsed": elapsed})


@app.route("/live/chunk", methods=["POST"])
def chunk():
    s = my_session(request.form.get("sid", ""))
    if not s or s.state not in ("recording", "paused"):
        return jsonify({"ok": False, "msg": "这节课已经结束了"}), 410
    f = request.files.get("file")
    if not f:
        return jsonify({"ok": False, "msg": "没有音频"}), 400
    data = f.read()
    if len(data) < 800:
        return jsonify({"ok": True, "skipped": True})
    s.state = "recording"
    s.add_chunk(int(request.form.get("seq", 0)), float(request.form.get("t0", 0)), float(request.form.get("dur", 0)),
                request.form.get("mime", ""), data)
    return jsonify({"ok": True})


@app.route("/live/pause", methods=["POST"])
def pause():
    s = my_session((request.get_json(force=True, silent=True) or {}).get("sid", ""))
    if not s:
        return jsonify({"ok": False}), 404
    s.state = "paused" if s.state == "recording" else "recording"
    s.dirty.set(); push_state(s.uid)
    return jsonify({"ok": True, "state": s.state})


@app.route("/live/stop", methods=["POST"])
def stop():
    s = my_session((request.get_json(force=True, silent=True) or {}).get("sid", ""))
    if not s or s.state not in ("recording", "paused"):
        return jsonify({"ok": False, "msg": "没有在录的课"}), 400
    threading.Thread(target=forever(s.finish), daemon=True).start()
    return jsonify({"ok": True})


@app.route("/live/stream")
def stream():
    s = my_session(request.args.get("sid", ""))
    if not s:
        return jsonify({"ok": False}), 404

    def gen():
        q = queue.Queue(maxsize=5000)
        with s.sub_lock:
            s.subs.append(q)
        with s.lock:
            rows = list(s.rows); items = list(s.summary); qa = list(s.qa)
        for h in rows:
            yield "data: " + json.dumps(dict(type="seg", **h), ensure_ascii=False) + "\n\n"
        if items:
            yield "data: " + json.dumps({"type": "summary", "items": items}, ensure_ascii=False) + "\n\n"
        for it in qa:
            yield "data: " + json.dumps(dict(type="qa", **it), ensure_ascii=False) + "\n\n"
        try:
            while True:
                try:
                    yield q.get(timeout=15)
                except queue.Empty:
                    yield ": ping\n\n"
        except GeneratorExit:
            pass
        finally:
            with s.sub_lock:
                if q in s.subs:
                    s.subs.remove(q)
    return Response(gen(), mimetype="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ================= 「电脑布局」接口:和电脑版 app.py 同名同格式,按当前用户执行 =================
# 页面是电脑版 index.html 原样;desk-shim.js 把它的 /config、/start… 改成 /live/d/config?k=…,麦克风在浏览器里录、传 /live/chunk。
D = "/live/d"
MIC_DEVICE = [{"id": "mic", "name": "麦克风 · 这台设备", "default": True, "loopback": False}]


def _j():
    return request.get_json(force=True, silent=True) or {}


def _course(data=None):
    s = user_session(g.user["uid"])
    return (s.course if s else None) or (data or {}).get("course") or request.args.get("course") or "其他"


@app.route(D + "/config")
def d_config():
    return jsonify({"courses": g.user["courses"], "devices": MIC_DEVICE, "state": desk_state(g.user),
                    "me": USERS.public(g.user, request.host)})


@app.route(D + "/records")
def d_records():
    u = g.user; groups = {}
    if u.get("owner"):
        base = os.path.join(VAULT, *RECORD_SUB.split("/"))
        for fp in glob.glob(os.path.join(base, "*", "*.md")):
            if "整合笔记" not in os.path.basename(fp):
                groups.setdefault(os.path.basename(os.path.dirname(fp)), []).append((fp, obsidian_uri(fp)))
    else:
        d = USERS.local_record_dir(u)
        for it in USERS.list_local(u):
            parts = it["rel"].split("/")
            if len(parts) < 2 or parts[0] in ("测试", "课程") or "整合笔记" in it["name"] or it["name"] == "AI问答.md":
                continue
            groups.setdefault(parts[0], []).append((os.path.join(d, it["rel"]), "record?f=" + urllib.parse.quote(it["rel"])))
    out = []
    for gname in sorted(groups):
        items = []
        for fp, uri in sorted(groups[gname], reverse=True):
            name = os.path.splitext(os.path.basename(fp))[0]
            m = re.match(r"(\d{4}-\d{2}-\d{2} \d{4})\s*(.*)", name)
            items.append({"when": m.group(1) if m else "", "title": (m.group(2) if m else name), "uri": uri})
        out.append({"group": gname, "items": items})
    return jsonify(out)


BAL = {}


def _balance(u, force=False):
    key = u.get("ds_key") or (DS_KEY if u.get("owner") else "")
    base = (u.get("ds_base") or DS_BASE) if u.get("ds_key") else DS_BASE
    if not key or llmcfg.guess_provider(base) != "deepseek":
        return None
    c = BAL.get(u["uid"])
    if c and not force and time.time() - c["t"] < 60:
        return c["data"]
    try:
        req = urllib.request.Request(base.rstrip("/") + "/user/balance", headers={"Authorization": "Bearer " + key, "Accept": "application/json"})
        j = json.loads(urllib.request.urlopen(req, timeout=10).read().decode())
        info = next((b for b in j.get("balance_infos", []) if b.get("currency") == "CNY"), (j.get("balance_infos") or [{}])[0])
        data = {"total": float(info.get("total_balance", 0)), "currency": info.get("currency", "CNY"),
                "available": j.get("is_available", True), "at": datetime.datetime.now().strftime("%H:%M")}
        BAL[u["uid"]] = {"t": time.time(), "data": data}
        return data
    except Exception as e:
        log("[balance] %s 查余额失败:%s" % (u["uid"], str(e)[:100]))
        return c["data"] if c else None


@app.route(D + "/balance")
def d_balance():
    u = g.user
    trial = not u.get("owner") and not u.get("groq_key")
    base = u.get("ds_base") or DS_BASE
    head = {"provider": u.get("ds_provider") or llmcfg.guess_provider(base), "model": u.get("ds_model") or DS_MODEL, "usage": {},
            "enabled": bool(user_ds(u)[0]), "trial": trial,
            "trial_left": max(0, usersmod.TRIAL_SECONDS - int(u.get("trial_used", 0))) if trial else None}
    d = None if trial else _balance(u, force=request.args.get("force") == "1")
    if not d:
        return jsonify(dict(head, ok=False))
    out = dict(d, ok=True, **head)
    s = user_session(u["uid"])
    if s and s.bal_start is not None:
        out["session_used"] = max(0.0, s.bal_start - d["total"])
    return jsonify(out)


@app.route(D + "/start", methods=["POST"])
def d_start():
    u = g.user; course = _j().get("course")
    if course not in u["courses"]:
        return jsonify({"ok": False, "msg": "先选是哪门课"}), 400
    s = user_session(u["uid"])
    if s:
        return jsonify({"ok": False, "msg": "已经在录了"}), 400
    if not u.get("owner") and not u.get("groq_key") and not USERS.trial_ok(u, 1):
        return jsonify({"ok": False, "msg": "试用额度用完了:点左下角「设置」填自己的 Groq key 和 DeepSeek key(都免费申请)再开始"}), 402
    if not PUB_GROQ and not u.get("groq_key"):
        return jsonify({"ok": False, "msg": "还没填 Groq key:点左下角「设置」填一下"}), 400
    if trial_busy(u):
        return jsonify({"ok": False, "msg": BUSY_MSG % TRIAL_CONCURRENT}), 429
    s = Session(u, course)
    with S_LOCK:
        SESSIONS[s.sid] = s
    s.persist()
    b = _balance(u, force=True)
    s.bal_start = b["total"] if b else None
    log("[%s] 开始(电脑布局):%s(%s%s)" % (s.sid, course, s.uid, " 试用" if s.trial else ""))
    push_state(u["uid"])
    s.status("开始录制:%s" % course, "ok")
    return jsonify({"ok": True, "sid": s.sid, "next_seq": 0, "elapsed": 0})


def _take_elapsed(s, data):
    try:
        e = float(data.get("elapsed") or 0)
        if 0 < e < 6 * 3600:
            s.elapsed = max(s.elapsed, e)
    except Exception:
        pass


@app.route(D + "/pause", methods=["POST"])
def d_pause():
    s = active_session(g.user["uid"])
    if not s:
        return jsonify({"ok": False}), 400
    _take_elapsed(s, _j())
    s.state = "paused" if s.state == "recording" else "recording"
    s.dirty.set(); push_state(s.uid)
    return jsonify({"ok": True, "state": s.state, "sid": s.sid, "elapsed": s.elapsed, "next_seq": max(s.max_seq + 1, s.next_seq)})


@app.route(D + "/stop", methods=["POST"])
def d_stop():
    s = active_session(g.user["uid"])
    if not s:
        return jsonify({"ok": False}), 400
    _take_elapsed(s, _j())
    threading.Thread(target=forever(s.finish), daemon=True).start()
    return jsonify({"ok": True})


@app.route(D + "/handoff", methods=["POST"])
def d_handoff():
    return jsonify({"ok": False, "msg": "网页版里不需要移交:换台设备打开同一条专属链接,点「继续」就接上了"}), 400


@app.route(D + "/autoqa", methods=["POST"])
def d_autoqa():
    on = bool(_j().get("on"))
    USERS.update(g.user, {"auto_qa": on})
    return jsonify({"ok": True, "on": on})


@app.route(D + "/context", methods=["GET"])
def d_context_get():
    u = g.user; s = user_session(u["uid"])
    if s:
        return jsonify({"on": context_on(u), "text": s.ctx["text"], "sources": s.ctx["sources"], "tokens": s.ctx["tokens"], "live": True})
    course = request.args.get("course") or "其他"
    try:
        mode = u["sync"].get("mode")
        if mode == "owner":
            vault, rs, cs = VAULT, RECORD_SUB, "课程"
        elif mode == "hosted":
            vault, rs, cs = USERS.hosted_vault_dir(u), usersmod.USER_SUB, "课程"
        else:
            vault, rs, cs = USERS.local_record_dir(u), "", "课程"
        r = context_pack.build(engine.course_code(course), course, udir(u), vault, record_sub=rs, course_sub=cs)
        return jsonify({"on": context_on(u), "text": r["text"], "sources": r["sources"], "tokens": r["tokens_est"], "live": False})
    except Exception as e:
        return jsonify({"on": context_on(u), "text": "", "sources": [], "tokens": 0, "live": False, "msg": str(e)[:100]})


@app.route(D + "/context", methods=["POST"])
def d_context_set():
    on = bool(_j().get("on"))
    USERS.update(g.user, {"context_on": on})
    s = user_session(g.user["uid"])
    if s and on and not s.ctx.get("text"):
        threading.Thread(target=s.build_context, daemon=True).start()
    push_state(g.user["uid"])
    return jsonify({"ok": True, "on": on})


@app.route(D + "/translate", methods=["POST"])
def d_translate():
    text = (_j().get("text") or "").strip()[:4000]
    if not text:
        return jsonify({"ok": False, "msg": "没有要翻的内容"}), 400
    cl, model = user_ds(g.user)
    if not cl:
        return jsonify({"ok": False, "msg": "没配 DeepSeek key:点左下角「设置」填一下"}), 400
    try:
        zh = llm_with(cl, model, [{"role": "system", "content": "你是大学课堂同传。把用户给的英文翻成自然的简体中文,专业术语保留英文括注。只输出译文。"},
                                  {"role": "user", "content": text}], temperature=0.2, max_tokens=1200)
    except Exception as e:
        return jsonify({"ok": False, "msg": "翻译失败:%s" % str(e)[:100]}), 500
    return jsonify({"ok": True, "zh": zh})


@app.route(D + "/ask", methods=["POST"])
def d_ask():
    u = g.user; data = _j()
    q = (data.get("q") or "").strip()[:2000]
    if not q:
        return jsonify({"ok": False, "msg": "问题是空的"}), 400
    cl, model = user_ds(u)
    if not cl:
        return jsonify({"ok": False, "msg": "没配 DeepSeek key:点左下角「设置」填一下"}), 400
    course = _course(data); code = engine.course_code(course)
    s = user_session(u["uid"])
    rows = (list(s.rows) if s else [])[-25:]
    notes = engine.summary_md(s.summary) if s else ""
    transcript = "\n".join("[%s] %s" % (h["t"], h["en"]) for h in rows if not h.get("gap")) or "(这节课还没开始/没有转写)"
    with umods(u):
        mem = askmem.memory_text(code)
    prompt = ("【本课术语】%s\n\n【这门课以前问过的(按时间)】\n%s\n\n【本节课 AI 笔记】\n%s\n\n【最近转写】\n%s\n\n【现在的问题】%s"
              ) % (course_terms(course, u)[:400], mem or "(还没问过)", notes[:1500] or "(无)", transcript[-3000:], q)
    sys_msg = ("你是「%s」这门课的学习助手,学生英语一般、专业底子一般。用中文回答,先给结论,再用两三句解释,总共不超过 200 字,"
               "专业术语保留英文。问题和课上刚讲的内容有关时,引用转写里的时间点;以前问过相关问题就接着上次说。不确定就直说。" % course)
    try:
        a = llm_with(cl, model, [{"role": "system", "content": sys_msg}, {"role": "user", "content": (s.prefix() if s else "") + prompt}],
                     temperature=0.3, max_tokens=700)
    except Exception as e:
        return jsonify({"ok": False, "msg": "问答失败:%s" % str(e)[:100]}), 500
    with umods(u):
        item = askmem.append(code, q, a, context_note=(s.course if s else ""))
    return jsonify({"ok": True, "id": item["id"], "a": a})


@app.route(D + "/ask/history")
def d_ask_history():
    with umods(g.user):
        items = askmem.load(engine.course_code(_course()), 10)
    return jsonify({"items": [{"id": i["id"], "q": i["q"], "a": i["a"], "saved": i.get("saved", False), "t": i.get("t", "")} for i in items]})


@app.route(D + "/ask/save", methods=["POST"])
def d_ask_save():
    """写进笔记:owner 账号写 课程/<课号>/AI问答.md(和电脑版同一个文件);别人写进他的同步目录 课程/<课号>/AI问答.md。"""
    u = g.user; data = _j()
    course = _course(data); code = engine.course_code(course)
    with umods(u):
        item = askmem.mark_saved(code, data.get("id", ""))
    if not item:
        return jsonify({"ok": False, "msg": "找不到这条问答"}), 404
    mode = u["sync"].get("mode")
    if mode == "owner":
        folder = os.path.join(VAULT, "课程", engine.safe_name(code))
    elif mode == "hosted":
        folder = os.path.join(USERS.hosted_vault_dir(u), "课程", engine.safe_name(code))
    else:
        folder = os.path.join(USERS.local_record_dir(u), "课程", engine.safe_name(code))
    os.makedirs(folder, exist_ok=True)
    fpath = os.path.join(folder, "AI问答.md")
    new = not os.path.exists(fpath)
    s = user_session(u["uid"])
    with open(fpath, "a", encoding="utf-8") as f:
        if new:
            f.write("---\ncourse: %s\ntags: [AI问答, %s]\n---\n\n# %s AI 问答\n\n听课搭子「AI 问答」模块里点「写进笔记」存下来的。\n" % (code, code, code))
        f.write(askmem.note_block(item, course, (s.start.strftime("%m-%d %H:%M") + " 课上") if s else ""))
    try:
        os.chmod(fpath, 0o664)
    except Exception:
        pass
    if s and s.state in ("recording", "paused"):
        s.my_asks.append(item)
    where = "课程/%s/AI问答.md" % code
    if mode == "webdav":
        try:
            where = "网盘 " + USERS.push_webdav(u, RECORD_SUB, code, "AI问答.md", open(fpath, encoding="utf-8").read())
        except Exception as e:
            where = "服务器(推网盘失败:%s)" % str(e)[:60]
    return jsonify({"ok": True, "path": os.path.relpath(fpath, VAULT).replace("\\", "/") if mode == "owner" else where,
                    "uri": obsidian_uri(fpath) if mode == "owner" else ""})


@app.route(D + "/slides", methods=["GET"])
def d_slides_list():
    with umods(g.user):
        return jsonify({"items": slides.list_files(engine.course_code(_course()))})


@app.route(D + "/slides", methods=["POST"])
def d_slides_upload():
    f = request.files.get("file")
    course = request.form.get("course") or _course()
    if not f or not f.filename:
        return jsonify({"ok": False, "msg": "没选文件"}), 400
    tmp = os.path.join(udir(g.user, "tmp"), engine.safe_name(f.filename) or "upload")
    f.save(tmp)
    try:
        with umods(g.user):
            r = slides.add(engine.course_code(course), tmp, f.filename)
    except Exception as e:
        return jsonify({"ok": False, "msg": str(e)[:160]}), 400
    finally:
        try:
            os.remove(tmp)
        except Exception:
            pass
    s = user_session(g.user["uid"])
    if s:
        s.terms = course_terms(s.course, g.user)
        s.status("课件已加入:%s(%d 页%s)" % (r["name"], r["pages"], (",新术语 %d 个" % len(r["new_terms"])) if r["new_terms"] else ""), "ok")
    return jsonify({"ok": True, **r})


@app.route(D + "/slides/remove", methods=["POST"])
def d_slides_remove():
    d = _j()
    with umods(g.user):
        ok = slides.remove(engine.course_code(d.get("course") or _course()), d.get("name", ""))
    return jsonify({"ok": ok})


DESK_PROVIDERS = {k: v for k, v in llmcfg.PROVIDERS.items() if k != "ollama"}     # 服务器连不到用户自己电脑上的 Ollama


@app.route(D + "/settings", methods=["GET"])
def d_settings_get():
    u = g.user
    base = u.get("ds_base") or DS_BASE
    return jsonify({"provider": u.get("ds_provider") or llmcfg.guess_provider(base), "base_url": base,
                    "model": u.get("ds_model") or DS_MODEL, "key_masked": llmcfg.mask(u.get("ds_key", "")),
                    "has_key": bool(u.get("ds_key")), "enabled": bool(user_ds(u)[0]), "phone_link": "",
                    "groq_masked": llmcfg.mask(u.get("groq_key", "")), "has_groq": bool(u.get("groq_key")),
                    "owner": bool(u.get("owner")), "providers": DESK_PROVIDERS})


def _form_key(d):
    return (d.get("api_key") or "").strip() or (g.user.get("ds_key", "") if d.get("keep_key") else "")


@app.route(D + "/settings/test", methods=["POST"])
def d_settings_test():
    d = _j()
    prov, base_url, model = d.get("provider", "custom"), (d.get("base_url") or "").strip().rstrip("/"), (d.get("model") or "").strip()
    key = _form_key(d)
    err = llmcfg.validate(prov, base_url, model, key)
    if err:
        return jsonify({"ok": False, "msg": err}), 400
    try:
        t = time.time()
        r = OpenAI(api_key=key, base_url=base_url).chat.completions.create(model=model, messages=[{"role": "user", "content": "只回复两个字:可以"}], max_tokens=64, temperature=0)
        m = r.choices[0].message
        reply = (m.content or "").strip()[:40] or ("(推理模型,连接正常)" if getattr(m, "reasoning_content", None) else "(空回复)")
        msg = {"ok": True, "ms": int((time.time() - t) * 1000), "reply": reply}
    except Exception as e:
        return jsonify({"ok": False, "msg": "连不上:%s" % str(e)[:200]}), 400
    gk = (d.get("groq_key") or "").strip()
    if gk:
        try:
            groq_client(gk).models.list(); msg["reply"] += " · Groq key 能用"
        except Exception as e:
            return jsonify({"ok": False, "msg": "模型通了,但 Groq key 不对:%s" % str(e)[:120]}), 400
    return jsonify(msg)


@app.route(D + "/settings/models", methods=["POST"])
def d_settings_models():
    d = _j()
    base_url = (d.get("base_url") or "").strip().rstrip("/")
    key = _form_key(d)
    if not re.match(r"^https?://", base_url):
        return jsonify({"ok": False, "msg": "先填接口地址"}), 400
    try:
        ids = sorted({m.id for m in OpenAI(api_key=key or "none", base_url=base_url).models.list().data if getattr(m, "id", "")})
        if not ids:
            return jsonify({"ok": False, "msg": "平台没返回模型列表,去它的文档里查模型名"}), 400
        return jsonify({"ok": True, "models": ids[:200]})
    except Exception as e:
        return jsonify({"ok": False, "msg": "拿不到列表(这家平台可能不支持),去它的文档里查:%s" % str(e)[:120]}), 400


@app.route(D + "/settings", methods=["POST"])
def d_settings_save():
    d = _j()
    prov, base_url, model = d.get("provider", "custom"), (d.get("base_url") or "").strip().rstrip("/"), (d.get("model") or "").strip()
    key = _form_key(d)
    err = llmcfg.validate(prov, base_url, model, key)
    if err:
        return jsonify({"ok": False, "msg": err}), 400
    gk = (d.get("groq_key") or "").strip()
    if gk and not gk.startswith("gsk_"):
        return jsonify({"ok": False, "msg": "Groq key 应该以 gsk_ 开头,看看是不是复制错了"}), 400
    upd = {"ds_key": key, "ds_base": base_url, "ds_model": model, "ds_provider": prov}
    if gk:
        upd["groq_key"] = gk
    USERS.update(g.user, upd)
    BAL.pop(g.user["uid"], None)
    push_state(g.user["uid"])
    return jsonify({"ok": True, "enabled": True})


@app.route(D + "/stream")
def d_stream():
    uid = g.user["uid"]; u = g.user

    def gen():
        q = queue.Queue(maxsize=5000)
        with US_LOCK:
            USER_SUBS.setdefault(uid, []).append(q)
        yield "data: " + json.dumps(dict(type="state", **desk_state(u)), ensure_ascii=False) + "\n\n"
        s = user_session(uid)
        if s:
            with s.lock:
                rows = list(s.rows); items = list(s.summary); qa = list(s.qa)
            for h in rows:
                yield "data: " + json.dumps(dict(type="seg", **h), ensure_ascii=False) + "\n\n"
            if items:
                yield "data: " + json.dumps({"type": "summary", "items": items}, ensure_ascii=False) + "\n\n"
            for it in qa:
                yield "data: " + json.dumps(dict(type="qa", **it), ensure_ascii=False) + "\n\n"
        try:
            while True:
                try:
                    yield q.get(timeout=15)
                except queue.Empty:
                    yield ": ping\n\n"
        except GeneratorExit:
            pass
        finally:
            with US_LOCK:
                if q in USER_SUBS.get(uid, []):
                    USER_SUBS[uid].remove(q)
    return Response(gen(), mimetype="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


if __name__ == "__main__":
    if TOKEN:
        USERS.ensure_owner(TOKEN, COURSES, VAULT)      # 部署者自己的账号(owner):链接 /live/?k=ACCESS_TOKEN,记录写进 VAULT_DIR
    if not PUB_GROQ:
        print("[!] .env 里没有 GROQ_API_KEY,新用户没法试用(填了自己 key 的照常)")
    threading.Thread(target=forever(autosave_unfinished), daemon=True).start()
    threading.Thread(target=forever(USERS.cleanup_local), daemon=True).start()
    log("[*] 听课搭子手机版后端启动,端口 %d" % PORT)
    app.run(host="127.0.0.1", port=PORT, threaded=True, use_reloader=False)
