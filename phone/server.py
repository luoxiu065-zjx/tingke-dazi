# -*- coding: utf-8 -*-
# ------------------------------------------------------------------
# 听课搭子 (tingke-dazi) · Copyright (c) 2026 luoxiu065-zjx
# 原始仓库:https://github.com/luoxiu065-zjx/tingke-dazi
# 转载、二次发布请保留本版权声明和原作者署名。build: tkdz-1bf2795e
# ------------------------------------------------------------------
"""听课搭子 · 手机 / 平板网页版后端(跑在你自己的服务器上)

手机浏览器录麦克风 → 按停顿切成小段上传 → Groq whisper-large-v3-turbo 转写 → DeepSeek 翻译/总结/答疑
→ SSE 推回手机 → 结束时把记录写成 Markdown 放进 RECORDS_DIR(建议指到你自己的同步目录,手机、电脑都会收到)。
设计为一个人用:同一时刻只有一节课在录;断网/关页面不影响,重开页面自动接上。
"""
import os, sys, io, json, time, queue, threading, re, glob, datetime, uuid, subprocess, shutil, traceback
from flask import Flask, request, jsonify, Response, send_from_directory
from dotenv import load_dotenv
from openai import OpenAI
import engine, textkit

BASE = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE, ".env"))
TOKEN = os.getenv("ACCESS_TOKEN", "").strip()
GROQ_KEY = os.getenv("GROQ_API_KEY", "").strip()
GROQ_MODEL = os.getenv("GROQ_MODEL", "whisper-large-v3-turbo")
DS_KEY = os.getenv("DEEPSEEK_API_KEY", "").strip()
DS_BASE = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
DS_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")
VAULT = os.getenv("RECORDS_DIR") or os.getenv("VAULT_DIR") or os.path.join(BASE, "records")   # 记录根目录(建议指到你的 Obsidian 同步目录)
RECORD_SUB = os.getenv("RECORD_SUBDIR", "课堂记录")
TERM_START = datetime.date.fromisoformat(os.getenv("TERM_START", "2026-09-21"))
COURSES = [c.strip() for c in os.getenv("COURSES", "COMP0001 示例课程,其他").split(",") if c.strip()]   # 在 .env 里改成你自己的课
PORT = int(os.getenv("PORT", "8095"))
SUMMARY_EVERY = 40
DATA = os.path.join(BASE, "data", "sessions"); os.makedirs(DATA, exist_ok=True)
LOGS = os.path.join(BASE, "logs"); os.makedirs(LOGS, exist_ok=True)
TERMS_DIR = os.path.join(BASE, "术语")

groq = OpenAI(api_key=GROQ_KEY, base_url="https://api.groq.com/openai/v1",
              default_headers={"User-Agent": "tingke-dazi-live/0.1"})   # Cloudflare 拦默认 UA(403 错误 1010)
ds = OpenAI(api_key=DS_KEY, base_url=DS_BASE) if DS_KEY else None
app = Flask(__name__, static_folder=None)


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


def llm(messages, temperature=0.3, max_tokens=800):
    r = ds.chat.completions.create(model=DS_MODEL, messages=messages, temperature=temperature, max_tokens=max_tokens, stream=False)
    return r.choices[0].message.content.strip()


def week_of(dt):
    return (dt.date() - TERM_START).days // 7 + 1


def course_terms(course):
    try:
        with open(os.path.join(TERMS_DIR, engine.course_code(course) + ".txt"), encoding="utf-8") as f:
            return ", ".join(w.strip() for w in re.split(r"[,\n]", f.read()) if w.strip() and not w.startswith("#"))
    except Exception:
        return ""


def prev_lesson_text(course):
    """上节课讲到哪:记录目录里这门课最新的一条记录(AI 总结那节)。"""
    code = engine.course_code(course)
    out = []
    folder = os.path.join(VAULT, *RECORD_SUB.split("/"), code)
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
    def __init__(self, course, start=None, sid=None, restore=None):
        self.sid = sid or uuid.uuid4().hex[:10]
        self.course = course
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

    def _load_prev(self):
        try:
            self.prev = prev_lesson_text(self.course)
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
        try:
            r = groq.audio.transcriptions.create(file=("chunk." + ext, data), model=GROQ_MODEL, language="en",
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
        if ds and engine.looks_like_question(text):
            self.qaq.put(row["id"])
        if ds:
            self.tq.put(row)

    # --- 翻译 ---
    def translate_worker(self):
        if not ds:
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
                    raw = llm([{"role": "system", "content": sys_msg + "\n用户会给编号的多句,逐句翻译,输出 JSON 数组:[{\"i\":编号,\"zh\":\"译文\"}],只输出 JSON。"},
                               {"role": "user", "content": textkit.batch_prompt(batch)}], temperature=0.2, max_tokens=400 * len(batch))
                    done = textkit.parse_batch(raw, batch)
                except Exception:
                    done = {}
            for it in batch:
                zh = done.get(it["id"])
                if not zh:
                    try:
                        zh = llm([{"role": "system", "content": sys_msg}, {"role": "user", "content": it["en"]}], temperature=0.2, max_tokens=400)
                    except Exception:
                        zh = "(翻译失败)"
                with self.lock:
                    it["zh"] = zh
                self.publish({"type": "seg_zh", "id": it["id"], "zh": zh})
            self.dirty.set()

    # --- 总结 ---
    def update_summary(self):
        if not ds:
            return False
        with self.lock:
            new = [h for h in self.rows if h["id"] > self.upto]
            items = list(self.summary)
        if not new:
            return False
        try:
            raw = llm([{"role": "user", "content": engine.summary_prompt(items, new)}], temperature=0.3, max_tokens=3000)
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
                j = engine.parse_json(llm([{"role": "user", "content": engine.qa_prompt(self.course, self.terms, self.prev, notes, before, focus, answered)}],
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
            return {"sid": self.sid, "course": self.course, "start": self.start.isoformat(timespec="seconds"), "state": self.state,
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
        if ds and len(rows) >= 8:
            try:
                title = engine.sane_title(llm([{"role": "user", "content": engine.title_prompt(engine.summary_md(items) or "\n".join(h["en"] for h in rows))}],
                                              temperature=0.2, max_tokens=60))
            except Exception:
                pass
        code = engine.course_code(self.course)
        folder = os.path.join(VAULT, *RECORD_SUB.split("/"), engine.safe_name(code))
        stem = "%s %s %s" % (self.start.strftime("%Y-%m-%d %H%M"), code, engine.safe_name(title))
        fpath = os.path.join(folder, stem + ".md")
        md = engine.record_md(self.course, self.start, end, dur, title, items, qa, rows)
        try:
            os.makedirs(folder, exist_ok=True)
            with open(fpath, "w", encoding="utf-8") as f:
                f.write(md)
            os.chmod(fpath, 0o664)
        except Exception as e:
            # 写不进同步目录(权限/磁盘):别丢数据,留着落盘文件,重启后会再试
            self.state = "paused"; self.persist()
            self.status("保存失败:%s。转写没丢,修好后重启会自动补存" % str(e)[:120], "error")
            log("[%s] 保存失败:\n%s" % (self.sid, traceback.format_exc()))
            return
        rel = os.path.relpath(fpath, VAULT).replace("\\", "/")
        self.state = "saved"
        try:
            os.remove(os.path.join(DATA, self.sid + ".json"))
        except Exception:
            pass
        self.publish({"type": "saved", "path": rel, "n": len(rows)})
        log("[%s] 已保存 %s(%d 句)" % (self.sid, rel, len(rows)))



SESSIONS = {}
S_LOCK = threading.Lock()


def active_session():
    with S_LOCK:
        for s in SESSIONS.values():
            if s.state in ("recording", "paused"):
                return s
    return None


def autosave_unfinished():
    """启动时把上次没正常结束的会话直接存进同步目录(用户 10/4 定:不问,直接存)。"""
    for p in sorted(glob.glob(os.path.join(DATA, "*.json"))):
        try:
            d = json.load(open(p, encoding="utf-8"))
            if d.get("state") not in ("recording", "paused", "saving") or not d.get("rows"):
                os.remove(p); continue
            s = Session(d["course"], start=datetime.datetime.fromisoformat(d["start"]), sid=d["sid"], restore=d)
            with S_LOCK:
                SESSIONS[s.sid] = s
            log("[*] 上次 %s 没正常结束(%d 句),自动保存" % (d["course"], len(d["rows"])))
            s.finish(interrupted_at=d.get("saved_at", "")[11:16])
        except Exception:
            log("[error] 自动保存失败:\n" + traceback.format_exc())


# ---------------- 接口 ----------------
def authed():
    k = request.args.get("k") or request.form.get("k") or (request.get_json(silent=True) or {}).get("k")
    return bool(TOKEN) and k == TOKEN


@app.before_request
def _auth():
    if request.path.rstrip("/") in ("/live/health",):
        return None
    if not authed():
        return jsonify({"ok": False, "msg": "没有钥匙"}), 401


@app.route("/live/")
@app.route("/live/index.html")
def index():
    return send_from_directory(os.path.join(BASE, "static"), "index.html")


@app.route("/live/health")
def health():
    return jsonify({"ok": True, "active": bool(active_session()), "groq": bool(GROQ_KEY), "deepseek": bool(ds)})


@app.route("/live/config")
def config():
    s = active_session()
    return jsonify({"courses": COURSES, "active": ({"sid": s.sid, "course": s.course, "elapsed": s.elapsed,
                                                    "start": s.start.strftime("%Y-%m-%d %H:%M")} if s else None)})


@app.route("/live/prev")
def prev():
    course = request.args.get("course", "")
    return jsonify({"text": prev_lesson_text(course) if course in COURSES else ""})


@app.route("/live/start", methods=["POST"])
def start():
    course = (request.get_json(force=True, silent=True) or {}).get("course")
    if course not in COURSES:
        return jsonify({"ok": False, "msg": "先选是哪门课"}), 400
    s = active_session()
    if s:
        return jsonify({"ok": True, "sid": s.sid, "resumed": True, "course": s.course, "elapsed": s.elapsed})
    s = Session(course)
    with S_LOCK:
        SESSIONS[s.sid] = s
    s.persist()
    log("[%s] 开始:%s" % (s.sid, course))
    return jsonify({"ok": True, "sid": s.sid, "resumed": False})


@app.route("/live/chunk", methods=["POST"])
def chunk():
    s = SESSIONS.get(request.form.get("sid", ""))
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
    s = SESSIONS.get((request.get_json(force=True, silent=True) or {}).get("sid", ""))
    if not s:
        return jsonify({"ok": False}), 404
    s.state = "paused" if s.state == "recording" else "recording"
    s.dirty.set()
    return jsonify({"ok": True, "state": s.state})


@app.route("/live/stop", methods=["POST"])
def stop():
    s = SESSIONS.get((request.get_json(force=True, silent=True) or {}).get("sid", ""))
    if not s or s.state not in ("recording", "paused"):
        return jsonify({"ok": False, "msg": "没有在录的课"}), 400
    threading.Thread(target=forever(s.finish), daemon=True).start()
    return jsonify({"ok": True})


@app.route("/live/stream")
def stream():
    s = SESSIONS.get(request.args.get("sid", ""))
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
    if not TOKEN:
        print("[!] .env 里没有 ACCESS_TOKEN,拒绝启动"); sys.exit(1)
    threading.Thread(target=forever(autosave_unfinished), daemon=True).start()
    log("[*] 听课搭子手机版后端启动,端口 %d" % PORT)
    app.run(host="127.0.0.1", port=PORT, threaded=True, use_reloader=False)
