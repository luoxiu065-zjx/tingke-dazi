# -*- coding: utf-8 -*-
# ------------------------------------------------------------------
# 听课搭子 (tingke-dazi) · Copyright (c) 2026 luoxiu065-zjx
# 原始仓库:https://github.com/luoxiu065-zjx/tingke-dazi
# 转载、二次发布请保留本版权声明和原作者署名。build: tkdz-1bf2795e
# ------------------------------------------------------------------
"""多人版的用户层:每人一把钥匙、自己的 Groq / DeepSeek key、自己的课程表、自己的同步方式。

同步三选一:
  hosted  = 服务器给他开一个独立的 WebDAV 文件夹(/udav/<uid>/),他的 Obsidian 装 Remotely Save 指过来(默认)
  webdav  = 他自己的 WebDAV(坚果云 / 自建),下课后服务器把笔记 PUT 过去
  none    = 不同步,只在结束页下载
不管选哪种,服务器本地都留一份(data/records/<uid>/),7 天内可下载。
"""
import os, json, time, uuid, secrets, hashlib, base64, threading, datetime, re
import requests

LOCK = threading.RLock()
TRIAL_SECONDS = int(os.getenv("TRIAL_SECONDS", "600"))          # 每个新用户可用公共 key 识别的音频秒数
TRIAL_DAILY_CAP = int(os.getenv("TRIAL_DAILY_CAP", "3600"))     # 所有试用用户一天合计上限(保护作者的 Groq 免费档)
RECORD_KEEP_DAYS = 7
USER_SUB = "课堂记录"                                            # 普通用户的记录子目录(在他的同步根 / 网盘 base 下)


class Users:
    def __init__(self, data_dir, dav_root, dav_auth_dir, public_base):
        self.path = os.path.join(data_dir, "users.json")
        self.trial_path = os.path.join(data_dir, "trial_day.json")
        self.records_dir = os.path.join(data_dir, "records"); os.makedirs(self.records_dir, exist_ok=True)
        self.dav_root, self.dav_auth_dir, self.public_base = dav_root, dav_auth_dir, public_base.rstrip("/")
        self.users = {}
        if os.path.exists(self.path):
            with open(self.path, encoding="utf-8") as f:
                self.users = json.load(f)
        self.by_key = {u["key"]: u for u in self.users.values()}

    # ---------- 存取 ----------
    def save(self):
        with LOCK:
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.users, f, ensure_ascii=False, indent=1)
            os.replace(tmp, self.path)
            try:
                os.chmod(self.path, 0o600)
            except Exception:
                pass
            self.by_key = {u["key"]: u for u in self.users.values()}

    def get_by_key(self, k):
        return self.by_key.get(k or "")

    def get(self, uid):
        return self.users.get(uid)

    def ensure_owner(self, key, courses, records_dir):
        """作者自己的账号:沿用原来的钥匙,用 .env 里的 key,记录写进自己的同步目录,保留 hub 交付。"""
        if not key:
            return None
        with LOCK:
            u = next((u for u in self.users.values() if u.get("owner")), None)
            if not u:
                u = self._new("owner", key); u["owner"] = True; self.users["owner"] = u
            u.update({"key": key, "courses": courses, "records_dir": records_dir, "sync": {"mode": "owner"}})
            self.save(); return u

    def _new(self, uid, key):
        return {"uid": uid, "key": key, "created": datetime.datetime.now().isoformat(timespec="seconds"), "name": "",
                "groq_key": "", "ds_key": "", "ds_base": "https://api.deepseek.com", "ds_model": "deepseek-chat",
                "courses": ["其他"], "sync": {"mode": "hosted"}, "dav_pass": "", "trial_used": 0.0, "owner": False, "records_dir": ""}

    def register(self, name=""):
        with LOCK:
            uid = "u" + secrets.token_hex(4)
            while uid in self.users:
                uid = "u" + secrets.token_hex(4)
            u = self._new(uid, secrets.token_urlsafe(18)); u["name"] = (name or "")[:40]
            self.users[uid] = u; self.save()
            return u

    def update(self, u, data):
        """只接受白名单字段;key 字段传空串 = 不改,传 'clear' = 清掉。"""
        with LOCK:
            for k in ("groq_key", "ds_key"):
                if k in data:
                    v = (data[k] or "").strip()
                    if v == "clear":
                        u[k] = ""
                    elif v:
                        u[k] = v
            for k in ("ds_base", "ds_model", "name"):
                if data.get(k) is not None:
                    u[k] = str(data[k]).strip()[:200]
            if data.get("ds_provider") is not None:
                u["ds_provider"] = str(data["ds_provider"]).strip()[:40]
            for k in ("auto_qa", "context_on"):
                if k in data:
                    u[k] = bool(data[k])
            if isinstance(data.get("courses"), list):
                cs = [re.sub(r"\s+", " ", str(c)).strip()[:60] for c in data["courses"]]
                cs = [c for c in cs if c]
                u["courses"] = (cs or ["其他"])[:30]
            if isinstance(data.get("sync"), dict):
                s = data["sync"]; mode = s.get("mode", u["sync"].get("mode", "hosted"))
                if mode in ("hosted", "webdav", "none"):
                    new = {"mode": mode}
                    if mode == "webdav":
                        w = s.get("webdav") or {}
                        old = u["sync"].get("webdav") or {}
                        new["webdav"] = {"url": str(w.get("url", old.get("url", ""))).strip().rstrip("/"),
                                         "user": str(w.get("user", old.get("user", ""))).strip(),
                                         "pass": str(w.get("pass") or old.get("pass", "")).strip(),
                                         "base": str(w.get("base", old.get("base", "tingke"))).strip().strip("/") or "tingke"}
                    u["sync"] = new
            self.save()

    def public(self, u, host=""):
        """给页面看的用户信息:key 只露尾 4 位。"""
        sync = dict(u["sync"]); mode = sync.get("mode")
        if mode == "webdav" and sync.get("webdav"):
            sync["webdav"] = dict(sync["webdav"]); sync["webdav"]["pass"] = "••••" if sync["webdav"].get("pass") else ""
        if mode == "hosted":
            try:
                self.ensure_hosted(u)          # 第一次看到就把目录和密码准备好,页面上才有密码可复制
            except Exception:
                pass
            sync["hosted"] = self.hosted_info(u, host)
        return {"uid": u["uid"], "name": u.get("name", ""), "owner": bool(u.get("owner")),
                "groq_key_tail": u["groq_key"][-4:] if u["groq_key"] else "", "ds_key_tail": u["ds_key"][-4:] if u["ds_key"] else "",
                "ds_base": u.get("ds_base", ""), "ds_model": u.get("ds_model", ""), "courses": u["courses"], "sync": sync,
                "trial_left": max(0, TRIAL_SECONDS - int(u.get("trial_used", 0))), "trial_total": TRIAL_SECONDS,
                "auto_qa": bool(u.get("auto_qa", bool(u.get("owner")))), "context_on": bool(u.get("context_on", True))}

    # ---------- 试用额度 ----------
    def trial_ok(self, u, need=0):
        if u.get("owner"):
            return True
        if u.get("trial_used", 0) + need > TRIAL_SECONDS:
            return False
        day = self._trial_day()
        return day["used"] + need <= TRIAL_DAILY_CAP

    def trial_add(self, u, seconds):
        with LOCK:
            u["trial_used"] = float(u.get("trial_used", 0)) + float(seconds)
            day = self._trial_day(); day["used"] += float(seconds)
            with open(self.trial_path, "w", encoding="utf-8") as f:
                json.dump(day, f)
            self.save()

    def _trial_day(self):
        today = datetime.date.today().isoformat()
        try:
            with open(self.trial_path, encoding="utf-8") as f:
                d = json.load(f)
            if d.get("day") == today:
                return d
        except Exception:
            pass
        return {"day": today, "used": 0.0}

    # ---------- 托管 WebDAV(hosted) ----------
    def hosted_dir(self, u):
        return os.path.join(self.dav_root, u["uid"])

    def ensure_hosted(self, u):
        """给他开 /udav/<uid>/:建目录 + 一份只给 nginx 看的 htpasswd。密码只生成一次,之后一直显示同一个。"""
        with LOCK:
            d = self.hosted_dir(u); os.makedirs(d, exist_ok=True)
            try:
                os.chmod(d, 0o2775)
            except Exception:
                pass
            if not u.get("dav_pass"):
                u["dav_pass"] = secrets.token_urlsafe(9); self.save()
            os.makedirs(self.dav_auth_dir, exist_ok=True)
            hp = os.path.join(self.dav_auth_dir, u["uid"] + ".htpasswd")
            line = "%s:{SHA}%s\n" % (u["uid"], base64.b64encode(hashlib.sha1(u["dav_pass"].encode()).digest()).decode())
            if not os.path.exists(hp) or open(hp, encoding="utf-8").read() != line:
                with open(hp, "w", encoding="utf-8") as f:
                    f.write(line)
                try:
                    os.chmod(hp, 0o644)
                except Exception:
                    pass
            return d

    def hosted_info(self, u, host=""):
        base = self.public_base or ("https://" + host if host else "")
        return {"url": "%s/udav/%s/" % (base, u["uid"]), "user": u["uid"], "pass": u.get("dav_pass", ""), "base": "tingke"}

    def hosted_vault_dir(self, u):
        """Remotely Save 会在 WebDAV 根下建一个「远程基础目录」(我们让用户填 tingke);已经同步过就用他建的那个。"""
        d = self.ensure_hosted(u)
        subs = [x for x in os.listdir(d) if os.path.isdir(os.path.join(d, x)) and not x.startswith(".")]
        if len(subs) == 1:
            return os.path.join(d, subs[0])
        if "tingke" in subs:
            return os.path.join(d, "tingke")
        return os.path.join(d, "tingke")

    # ---------- 记录落地 ----------
    def local_record_dir(self, u):
        d = os.path.join(self.records_dir, u["uid"]); os.makedirs(d, exist_ok=True); return d

    def record_targets(self, u, record_sub, code):
        """返回这个用户的记录应该写到哪些目录(第一个是主目录,后面是副本)。
        record_sub 只对作者账号生效(他的库有自己的层级);普通用户固定放在「课堂记录/<课号>/」。"""
        mode = u["sync"].get("mode")
        if mode == "owner":
            return [os.path.join(u["records_dir"], *record_sub.split("/"), code)]
        local = os.path.join(self.local_record_dir(u), code)
        if mode == "hosted":
            return [os.path.join(self.hosted_vault_dir(u), USER_SUB, code), local]
        return [local]

    def push_webdav(self, u, record_sub, code, fname, content):
        """他自己的 WebDAV:逐级 MKCOL 再 PUT。成功返回远端路径,失败抛异常。"""
        w = u["sync"].get("webdav") or {}
        if not w.get("url"):
            raise RuntimeError("没有填 WebDAV 地址")
        auth = (w.get("user", ""), w.get("pass", ""))
        parts = [w.get("base", "tingke"), USER_SUB, code]
        cur = w["url"]
        for p in parts:
            cur = cur + "/" + requests.utils.quote(p)
            r = requests.request("MKCOL", cur + "/", auth=auth, timeout=20)
            if r.status_code not in (201, 204, 405, 301, 302):
                raise RuntimeError("建目录失败 %s(%s)" % (r.status_code, p))
        target = cur + "/" + requests.utils.quote(fname)
        r = requests.put(target, data=content.encode("utf-8"), auth=auth, timeout=60, headers={"Content-Type": "text/markdown; charset=utf-8"})
        if r.status_code not in (200, 201, 204):
            raise RuntimeError("上传失败 %s" % r.status_code)
        return "/".join(parts) + "/" + fname

    def test_webdav(self, w):
        """填完当场验证:MKCOL + PUT + GET + DELETE 一个小文件。返回 (ok, 说明)。"""
        url = (w.get("url") or "").strip().rstrip("/")
        if not url.startswith("http"):
            return False, "地址要以 http:// 或 https:// 开头"
        auth = (w.get("user", ""), w.get("pass", ""))
        base = (w.get("base") or "tingke").strip("/")
        try:
            r = requests.request("PROPFIND", url + "/", auth=auth, timeout=15, headers={"Depth": "0"})
            if r.status_code in (401, 403):
                return False, "账号或密码不对(%s)" % r.status_code
            if r.status_code >= 400 and r.status_code != 404:
                return False, "服务器回应 %s,这个地址可能不是 WebDAV" % r.status_code
            d = url + "/" + requests.utils.quote(base)
            r = requests.request("MKCOL", d + "/", auth=auth, timeout=15)
            if r.status_code not in (201, 204, 405, 301, 302):
                return False, "建目录失败(%s)" % r.status_code
            t = d + "/.tingke-test.txt"
            r = requests.put(t, data=b"ok", auth=auth, timeout=15)
            if r.status_code not in (200, 201, 204):
                return False, "写文件失败(%s)" % r.status_code
            r = requests.get(t, auth=auth, timeout=15)
            if r.status_code != 200 or r.content.strip() != b"ok":
                return False, "写进去了但读不回来(%s)" % r.status_code
            requests.delete(t, auth=auth, timeout=15)
            return True, "连上了,能读能写"
        except requests.RequestException as e:
            return False, "连不上:%s" % str(e)[:120]

    def cleanup_local(self):
        cutoff = time.time() - RECORD_KEEP_DAYS * 86400
        for uid in os.listdir(self.records_dir):
            d = os.path.join(self.records_dir, uid)
            for root, _, files in os.walk(d):
                for f in files:
                    p = os.path.join(root, f)
                    try:
                        if os.path.getmtime(p) < cutoff:
                            os.remove(p)
                    except Exception:
                        pass

    def list_local(self, u):
        d = self.local_record_dir(u); out = []
        for root, _, files in os.walk(d):
            for f in files:
                if f.endswith(".md"):
                    p = os.path.join(root, f)
                    out.append({"name": f, "rel": os.path.relpath(p, d).replace("\\", "/"), "mtime": int(os.path.getmtime(p)), "size": os.path.getsize(p)})
        return sorted(out, key=lambda x: -x["mtime"])
