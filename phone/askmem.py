# -*- coding: utf-8 -*-
# ------------------------------------------------------------------
# 听课搭子 (tingke-dazi) · Copyright (c) 2026 luoxiu065-zjx
# 原始仓库:https://github.com/luoxiu065-zjx/tingke-dazi
# 转载、二次发布请保留本版权声明和原作者署名。build: tkdz-1bf2795e
# ------------------------------------------------------------------
"""AI 问答模块的记忆:每门课一个 jsonl,记全部问答;「写进笔记」时追加到 Obsidian 的 AI问答.md。不 import app。"""
import os, json, datetime, re

DIR = None


def init(base):
    global DIR
    DIR = os.path.join(base, "问答")
    os.makedirs(DIR, exist_ok=True)


def _path(code):
    return os.path.join(DIR, re.sub(r"[^\w\-]", "_", code or "其他") + ".jsonl")


def append(code, q, a, context_note=""):
    """记一条问答,返回它的 id(时间戳)。"""
    # 同一毫秒内连问两条会撞 id(测试里撞过),加 4 位随机尾巴
    item = {"id": datetime.datetime.now().strftime("%Y%m%d-%H%M%S-%f") + "-" + os.urandom(2).hex(),
            "t": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
            "q": q, "a": a, "saved": False, "ctx": context_note}
    with open(_path(code), "a", encoding="utf-8") as f:
        f.write(json.dumps(item, ensure_ascii=False) + "\n")
    return item


def load(code, n=20):
    try:
        with open(_path(code), encoding="utf-8") as f:
            rows = [json.loads(l) for l in f if l.strip()]
    except FileNotFoundError:
        return []
    return rows[-n:]


def mark_saved(code, item_id):
    p = _path(code)
    try:
        with open(p, encoding="utf-8") as f:
            rows = [json.loads(l) for l in f if l.strip()]
    except FileNotFoundError:
        return None
    hit = None
    for r in rows:
        if r.get("id") == item_id:
            r["saved"] = True; hit = r
    if hit:
        with open(p, "w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
    return hit


def memory_text(code, n=12, limit=2500):
    """给模型看的「这门课以前问过什么」,最近 n 条,总长封顶。"""
    rows = load(code, n)
    out = []
    for r in rows:
        out.append("问(%s):%s\n答:%s" % (r.get("t", ""), r["q"][:200], r["a"][:400]))
    return "\n\n".join(out)[-limit:]


def note_block(item, course, lesson_title=""):
    """追加到 Obsidian AI问答.md 的一段。"""
    head = "## %s %s" % (item["t"], ("· " + lesson_title) if lesson_title else "")
    return "\n%s\n\n**问:** %s\n\n%s\n" % (head.strip(), item["q"], item["a"])
