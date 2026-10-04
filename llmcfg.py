# -*- coding: utf-8 -*-
# ------------------------------------------------------------------
# 听课搭子 (tingke-dazi) · Copyright (c) 2026 luoxiu065-zjx
# 原始仓库:https://github.com/luoxiu065-zjx/tingke-dazi
# 转载、二次发布请保留本版权声明和原作者署名。build: tkdz-1bf2795e
# ------------------------------------------------------------------
"""设置页的后盾:模型提供商预设、.env 读写、key 打码、token 用量统计。不 import app;tests/test_llmcfg.py 测。"""
import os, re, threading

PROVIDERS = {
    "deepseek": {"name": "DeepSeek", "base_url": "https://api.deepseek.com", "model": "deepseek-chat",
                 "models": ["deepseek-chat", "deepseek-reasoner"], "key_url": "https://platform.deepseek.com/api_keys", "balance": True},
    "qwen":     {"name": "通义千问(阿里百炼)", "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1", "model": "qwen-plus",
                 "models": ["qwen-plus", "qwen-turbo", "qwen-max"], "key_url": "https://bailian.console.aliyun.com/?apiKey=1", "balance": False},
    "moonshot": {"name": "Moonshot(Kimi)", "base_url": "https://api.moonshot.cn/v1", "model": "moonshot-v1-8k",
                 "models": ["moonshot-v1-8k", "moonshot-v1-32k", "kimi-k2-0711-preview"], "key_url": "https://platform.moonshot.cn/console/api-keys", "balance": False},
    "openai":   {"name": "OpenAI", "base_url": "https://api.openai.com/v1", "model": "gpt-4o-mini",
                 "models": ["gpt-4o-mini", "gpt-4.1-mini"], "key_url": "https://platform.openai.com/api-keys", "balance": False},
    "ollama":   {"name": "本机 Ollama(免费,要装)", "base_url": "http://127.0.0.1:11434/v1", "model": "qwen2.5:7b",
                 "models": [], "key_url": "", "balance": False},
    "custom":   {"name": "自定义(OpenAI 兼容地址)", "base_url": "", "model": "", "models": [], "key_url": "", "balance": False},
}
KEYS = ("LLM_PROVIDER", "DEEPSEEK_API_KEY", "DEEPSEEK_BASE_URL", "DEEPSEEK_MODEL")   # .env 里沿用老名字,别的模块不用改


def guess_provider(base_url):
    for k, v in PROVIDERS.items():
        if v["base_url"] and base_url and base_url.rstrip("/").startswith(v["base_url"].rstrip("/")):
            return k
    return "custom"


def mask(key):
    key = (key or "").strip()
    if len(key) < 8:
        return "(未填)" if not key else "*" * len(key)
    return key[:4] + "…" + key[-4:]


def read_env(path):
    out = {}
    if not os.path.exists(path):
        return out
    with open(path, encoding="utf-8") as f:
        for line in f:
            m = re.match(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$", line)
            if m and not line.lstrip().startswith("#"):
                out[m.group(1)] = m.group(2)
    return out


def write_env(path, updates):
    """只改给定的键,其余行(含注释)原样保留;没有的键追加到末尾。"""
    lines = []
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            lines = f.read().splitlines()
    done = set()
    for i, line in enumerate(lines):
        m = re.match(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=", line)
        if m and not line.lstrip().startswith("#") and m.group(1) in updates:
            lines[i] = "%s=%s" % (m.group(1), updates[m.group(1)]); done.add(m.group(1))
    for k, v in updates.items():
        if k not in done:
            lines.append("%s=%s" % (k, v))
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines) + "\n")
    os.replace(tmp, path)


def validate(provider, base_url, model, key):
    if provider not in PROVIDERS:
        return "没有这个提供商"
    if not base_url or not re.match(r"^https?://", base_url):
        return "地址要以 http:// 或 https:// 开头"
    if not model:
        return "模型名不能为空"
    if provider != "ollama" and not key:
        return "这个提供商需要 API key"
    return ""


class Usage:
    """累计这节课用了多少 token(所有提供商都能算,比只看 DeepSeek 余额通用)。"""
    def __init__(self):
        self.lock = threading.Lock(); self.reset()

    def reset(self):
        with self.lock:
            self.prompt = 0; self.completion = 0; self.calls = 0

    def add(self, usage):
        if not usage:
            return
        with self.lock:
            self.prompt += int(getattr(usage, "prompt_tokens", 0) or 0)
            self.completion += int(getattr(usage, "completion_tokens", 0) or 0)
            self.calls += 1

    def snapshot(self):
        with self.lock:
            return {"prompt": self.prompt, "completion": self.completion, "calls": self.calls}
