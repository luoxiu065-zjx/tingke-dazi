# -*- coding: utf-8 -*-
# ------------------------------------------------------------------
# 听课搭子 (tingke-dazi) · Copyright (c) 2026 luoxiu065-zjx
# 原始仓库:https://github.com/luoxiu065-zjx/tingke-dazi
# 转载、二次发布请保留本版权声明和原作者署名。build: tkdz-1bf2795e
# ------------------------------------------------------------------
"""翻译积压时的批量翻译:几句合成一次调用,再按编号拆回去。不 import app。"""
import json, re


def batch_prompt(items):
    """items=[{id, en}] → 给模型的用户消息。编号从 1 起,和 items 顺序一致。"""
    return "\n".join("%d. %s" % (i + 1, it["en"]) for i, it in enumerate(items))


def parse_batch(raw, items):
    """模型输出 → {id: zh}。接受 JSON 数组 [{"i":1,"zh":"…"}],也接受「1. 译文」逐行的格式。
    解析不到的句子不放进结果,调用方会单独再翻一次。"""
    out = {}
    n = len(items)
    m = re.search(r"\[.*\]", raw, re.S)
    if m:
        try:
            for row in json.loads(m.group(0)):
                i = int(row.get("i", 0)); zh = str(row.get("zh", "")).strip()
                if 1 <= i <= n and zh:
                    out[items[i - 1]["id"]] = zh
            if out:
                return out
        except Exception:
            pass
    for line in raw.splitlines():
        mm = re.match(r"\s*(\d+)\s*[.、:：)]\s*(.+)", line)
        if mm:
            i = int(mm.group(1)); zh = mm.group(2).strip().strip('"“”')
            if 1 <= i <= n and zh:
                out[items[i - 1]["id"]] = zh
    return out
