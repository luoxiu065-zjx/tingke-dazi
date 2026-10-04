# -*- coding: utf-8 -*-
# ------------------------------------------------------------------
# 听课搭子 (tingke-dazi) · Copyright (c) 2026 luoxiu065-zjx
# 原始仓库:https://github.com/luoxiu065-zjx/tingke-dazi
# 转载、二次发布请保留本版权声明和原作者署名。build: tkdz-1bf2795e
# ------------------------------------------------------------------
"""会话落盘 / 断电续录 / 电量读取。

2026-10-01 决策树课:笔记本 15:29 没电休眠,内存里的转写、总结、提问卡全没了,只剩 live.md 被挪进备份夹。
这里把一节课的状态每隔几秒写进 session/当前.json;程序重开时发现它,就能「续录 / 直接保存 / 丢弃」。
不 import app,纯函数,tests/test_session.py 直接测。
"""
import os, json, time, datetime, ctypes

SESSION_DIR = None
FILE = "当前.json"


def init(base):
    global SESSION_DIR
    SESSION_DIR = os.path.join(base, "session")
    os.makedirs(SESSION_DIR, exist_ok=True)


def path():
    return os.path.join(SESSION_DIR, FILE)


def fmt_t(sec):
    sec = int(sec)
    return "%02d:%02d:%02d" % (sec // 3600, sec % 3600 // 60, sec % 60)


def dump(course, start, elapsed, device_id, device_name, history, summary_items, summary_upto, qa):
    """把当前这节课写进 session/当前.json。先写 .tmp 再替换,断电时文件不会写一半。"""
    data = {"version": 1,
            "course": course,
            "start": start.isoformat(timespec="seconds") if isinstance(start, datetime.datetime) else start,
            "elapsed": round(float(elapsed), 1),
            "device_id": device_id, "device_name": device_name,
            "saved_at": datetime.datetime.now().isoformat(timespec="seconds"),
            "history": history, "summary_items": summary_items, "summary_upto": summary_upto, "qa": qa}
    tmp = path() + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    for _ in range(8):
        try:
            os.replace(tmp, path())
            return True
        except PermissionError:
            time.sleep(0.05)
    return False


def load():
    """读上次没正常结束的会话;文件不存在、坏了、没内容都返回 None。"""
    try:
        with open(path(), encoding="utf-8") as f:
            data = json.load(f)
        if not data.get("course") or not data.get("start") or not isinstance(data.get("history"), list):
            return None
        if not data["history"]:
            return None
        data["start_dt"] = datetime.datetime.fromisoformat(data["start"])
        return data
    except Exception:
        return None


def clear():
    try:
        os.remove(path())
    except FileNotFoundError:
        pass
    except Exception:
        pass


def describe(data):
    """给首页显示的一句话:上次 COMP6246 15:03 开始的课没有正常结束,录到 15:29,61 句"""
    start = data["start_dt"]
    last = data.get("saved_at", data["start"])[11:16]
    return "上次 %s %s 开始的课没有正常结束,录到 %s,共 %d 句" % (
        data["course"], start.strftime("%H:%M"), last, len(data["history"]))


def gap_row(data, next_id, now=None):
    """自动保存时插在转写末尾的一行,标出从哪里断的。"""
    now = now or datetime.datetime.now()
    last = data.get("saved_at", data["start"])[11:16]
    return {"id": next_id, "t": fmt_t(data.get("elapsed", 0)), "clock": now.strftime("%H:%M:%S"),
            "en": "[程序在 %s 意外中断,之后的内容没录到;可用 Panopto 录像或手机录音补]" % last,
            "zh": "", "gap": True}


class _POWER(ctypes.Structure):
    _fields_ = [("ACLineStatus", ctypes.c_ubyte), ("BatteryFlag", ctypes.c_ubyte),
                ("BatteryLifePercent", ctypes.c_ubyte), ("SystemStatusFlag", ctypes.c_ubyte),
                ("BatteryLifeTime", ctypes.c_ulong), ("BatteryFullLifeTime", ctypes.c_ulong)]


def battery():
    """(电量百分比, 是否插着电);台式机/查不到返回 (None, None)。只用 Windows 自带接口,不装包。"""
    if os.name != "nt":
        return None, None
    try:
        st = _POWER()
        if not ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(st)):
            return None, None
        if st.BatteryFlag & 128 or st.BatteryLifePercent == 255:    # 128 = 没有电池
            return None, None
        plugged = None if st.ACLineStatus == 255 else st.ACLineStatus == 1
        return int(st.BatteryLifePercent), plugged
    except Exception:
        return None, None
