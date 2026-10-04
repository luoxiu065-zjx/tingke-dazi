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
from flask import g, abort, send_file

BASE = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE, ".env"))
TOKEN = os.getenv("ACCESS_TOKEN", "").strip()
GROQ_KEY = os.getenv("GROQ_API_KEY", "").strip()
GROQ_MODEL = os.getenv("GROQ_MODEL", "whisper-large-v3-turbo")
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


def course_terms(course):
    try:
        with open(os.path.join(TERMS_DIR, engine.course_code(course) + ".txt"), encoding="utf-8") as f:
            return ", ".join(w.strip() for w in re.split(r"[,\n]", f.read()) if w.strip() and not w.startswith("#"))
    except Exception:
        return ""


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
        self.terms = course_terms(course)
        self.prev = ""
        if restore:
            self.rows = restore.get("rows", []); self.summary = restore.get("summary", []); self.upto = restore.get("upto", 0)
            self.qa = restore.get("qa", []); self.elapsed = restore.get("elapsed", 0.0)
            self.seg = max([r["id"] for r in self.rows] or [0]); self.next_seq = restore.get("next_seq", 0)
        else:
            threading.Thread(target=self._load_prev, daemon=True).start()
        for fn, name in ((self.chunk_worker, "chunk"), (self.translate_worker, "translate"), (self.summary_worker, "summary"),
                         (self.qa_worker, "qa"), (self.persist_worker, "persist")):
            threading.Thread(target=forever(fn), daemon=True, name="%s-%s" % (name, self.sid)).start()

    def llm(self, messages, temperature=0.3, max_tokens=800):
        return llm_with(self.ds, self.ds_model, messages, temperature, max_tokens)

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

    def status(self, msg, level="info"):
        log("[%s][%s] %s" % (self.sid, level, msg)); self.publish({"type": "status", "msg": msg, "level": level})

    # --- 收音频段 ---
    def add_chunk(self, seq, t0, dur, mime, data):
        with self.cv:
            self.chunks[seq] = (t0, dur, mime, data)
            self.elapsed = max(self.elapsed, t0 + dur)
            self.cv.notify_all()

    def chunk_worker(self):
        while self.state == "recording" or self.chunks:
            with self.cv:
                deadline = time.time() + 3
                while self.next_seq not in self.chunks and self.state == "recording":
                    if self.chunks and time.time() > deadline:       # 等了 3 秒还没等到前一段:跳过它
                        self.next_seq = min(self.chunks); break
                    self.cv.wait(timeout=0.5)
                if self.next_seq not in self.chunks:
                    if self.state != "recording" and not self.chunks:
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
            r = self.groq.audio.transcriptions.create(file=("chunk." + ext, data), model=GROQ_MODEL, language="en",
                                                 prompt=prompt.strip(), response_format="verbose_json", temperature=0)
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
        if self.ds and engine.looks_like_question(text):
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
            raw = self.llm([{"role": "user", "content": engine.summary_prompt(items, new)}], temperature=0.3, max_tokens=3000)
        except Exception:
            return False
        got = engine.parse_json(raw, "[")
        if not isinstance(got, list):
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
                j = engine.parse_json(self.llm([{"role": "user", "content": engine.qa_prompt(self.course, self.terms, self.prev, notes, before, focus, answered)}],
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
                title = engine.sane_title(llm([{"role": "user", "content": engine.title_prompt(engine.summary_md(items) or "\n".join(h["en"] for h in rows))}],
                                              temperature=0.2, max_tokens=60))
            except Exception:
                pass
        code = engine.course_code(self.course)
        stem = "%s %s %s" % (self.start.strftime("%Y-%m-%d %H%M"), code, engine.safe_name(title))
        md = engine.record_md(self.course, self.start, end, dur, title, items, qa, rows)
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
                    self.state = "paused"; self.persist()
                    self.status("保存失败:%s。转写没丢,修好后重启会自动补存" % str(e)[:120], "error")
                    log("[%s] 保存失败:\n%s" % (self.sid, traceback.format_exc()))
                    return
        if fpath is None:
            self.state = "paused"; self.persist()
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
        self.publish({"type": "saved", "path": rel, "where": where, "n": len(rows), "download": "record?f=" + engine.safe_name(code) + "/" + stem + ".md"})
        log("[%s] 已保存 %s(%d 句)→ %s" % (self.sid, stem, len(rows), where))



SESSIONS = {}
S_LOCK = threading.Lock()


def active_session(uid):
    with S_LOCK:
        for s in SESSIONS.values():
            if s.uid == uid and s.state in ("recording", "paused"):
                return s
    return None


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
            u = USERS.get(d.get("uid", "owner")) or USERS.get("owner")
            if not u:
                os.remove(p); continue
            s = Session(u, d["course"], start=datetime.datetime.fromisoformat(d["start"]), sid=d["sid"], restore=d)
            with S_LOCK:
                SESSIONS[s.sid] = s
            log("[*] 上次 %s 没正常结束(%d 句),自动保存" % (d["course"], len(d["rows"])))
            s.finish(interrupted_at=d.get("saved_at", "")[11:16])
        except Exception:
            log("[error] 自动保存失败:\n" + traceback.format_exc())


# ---------------- 接口 ----------------
OPEN_PATHS = ("/live", "/live/index.html", "/live/health", "/live/register")


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
                llm_with(ds_client(dk, j.get("ds_base") or g.user.get("ds_base")), j.get("ds_model") or g.user.get("ds_model") or DS_MODEL,
                         [{"role": "user", "content": "回复一个字:好"}], max_tokens=5); out["ds"] = [True, "能用"]
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
    s = Session(g.user, course)
    with S_LOCK:
        SESSIONS[s.sid] = s
    s.persist()
    log("[%s] 开始:%s(%s%s)" % (s.sid, course, s.uid, " 试用" if s.trial else ""))
    return jsonify({"ok": True, "sid": s.sid, "resumed": False, "trial": s.trial})


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
    s.dirty.set()
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


if __name__ == "__main__":
    if TOKEN:
        USERS.ensure_owner(TOKEN, COURSES, VAULT)      # 部署者自己的账号(owner):链接 /live/?k=ACCESS_TOKEN,记录写进 VAULT_DIR
    if not PUB_GROQ:
        print("[!] .env 里没有 GROQ_API_KEY,新用户没法试用(填了自己 key 的照常)")
    threading.Thread(target=forever(autosave_unfinished), daemon=True).start()
    threading.Thread(target=forever(USERS.cleanup_local), daemon=True).start()
    log("[*] 听课搭子手机版后端启动,端口 %d" % PORT)
    app.run(host="127.0.0.1", port=PORT, threaded=True, use_reloader=False)
