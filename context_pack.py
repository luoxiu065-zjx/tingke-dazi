# -*- coding: utf-8 -*-
# ------------------------------------------------------------------
# 听课搭子 (tingke-dazi) · Copyright (c) 2026 luoxiu065-zjx
# 原始仓库:https://github.com/luoxiu065-zjx/tingke-dazi
# 转载、二次发布请保留本版权声明和原作者署名。build: tkdz-1bf2795e
# ------------------------------------------------------------------
"""「了解我」上下文包:开课时把个人档案 + 这门课在 Obsidian 里的进度读成一段固定前缀,只喂给 AI 总结、提问回答、AI 问答。
纯函数,不碰网络;tests/test_context_pack.py 用临时库测。"""
import os, re, glob, datetime

CAPS = {"me": 600, "materials": 800, "teacher": 500, "status": 800, "records": 800, "week": 600, "questions": 400}
TOTAL_CAP = 5000
TERM_START = datetime.date(2026, 9, 21)


def week_of(d):
    n = (d - TERM_START).days // 7 + 1
    return n if n >= 1 else None


def _read(path, cap):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read().strip()[:cap]
    except Exception:
        return ""


def _section(md, heading_re, cap):
    """取 markdown 里某个 ## 标题下的内容。"""
    m = re.search(r"^##\s*(?:%s).*?\n(.*?)(?=^##\s|\Z)" % heading_re, md, re.S | re.M)
    return (m.group(1).strip() if m else "")[:cap]


def _headings_and_flags(md, cap):
    """周页:只要各级标题 + 含「没懂 / ? / 不会」标记的行。"""
    out = []
    for line in md.splitlines():
        s = line.strip()
        if s.startswith("#") or re.search(r"没懂|不懂|不会|存疑|\?\?", s):
            out.append(s)
    return "\n".join(out)[:cap]


def build(code, course_name, base, vault, today=None, record_sub="MSc课程/课堂记录", course_sub="MSc课程"):
    """返回 {"text", "sources": [...], "chars"}。base=程序目录(profile/ 在这里),vault=Obsidian 库根。缺什么跳过什么。"""
    today = today or datetime.date.today()
    parts, sources = [], []
    prof = os.path.join(base, "profile")

    me = _read(os.path.join(prof, "我.md"), CAPS["me"])
    if me:
        parts.append("【关于这位学生】\n" + me); sources.append("自述 我.md")

    mats = []
    for fp in sorted(glob.glob(os.path.join(prof, "资料", "*")))[:6]:
        ext = os.path.splitext(fp)[1].lower()
        if ext in (".md", ".txt"):
            t = _read(fp, 300)
        elif ext == ".pdf":
            try:
                import fitz
                with fitz.open(fp) as doc:
                    t = " ".join(p.get_text("text") for p in list(doc)[:2])[:300]
            except Exception:
                t = ""
        else:
            t = ""
        if t:
            mats.append("《%s》:%s" % (os.path.basename(fp), re.sub(r"\s+", " ", t)))
    if mats:
        parts.append("【学生过去的相关资料】\n" + "\n".join(mats)[:CAPS["materials"]]); sources.append("资料/ %d 份" % len(mats))

    teacher = _read(os.path.join(prof, "老师", code + ".md"), CAPS["teacher"])
    if teacher:
        parts.append("【这门课的老师】\n" + teacher); sources.append("老师画像")

    status_md = _read(os.path.join(prof, "学习状态.md"), 20000)
    st = _section(status_md, re.escape(code), CAPS["status"]) if status_md else ""
    if st:
        parts.append("【这门课的学习状态(复盘写的)】\n" + st); sources.append("学习状态")

    rec_dir = os.path.join(vault, *[x for x in record_sub.split("/") if x], code)
    recs = sorted(f for f in glob.glob(os.path.join(rec_dir, "*.md")) if "整合笔记" not in os.path.basename(f))[-2:]
    rec_txt = []
    for fp in recs:
        md = _read(fp, 60000)
        title = re.search(r'^title:\s*"?(.+?)"?\s*$', md, re.M)
        summ = _section(md, "AI 总结", 300)
        heads = "\n".join(l for l in summ.splitlines() if l.startswith("###"))[:200] or summ[:200]
        qa = _section(md, "课上提问与参考回答", 200)
        rec_txt.append("%s · %s\n%s%s" % (os.path.basename(fp)[:16], title.group(1) if title else "", heads, ("\n提问:" + qa) if qa and qa != "(无)" else ""))
    if rec_txt:
        parts.append("【最近两节课讲了什么】\n" + "\n---\n".join(rec_txt)[:CAPS["records"]]); sources.append("课堂记录 %d 条" % len(rec_txt))

    wk = week_of(today)
    if wk:
        pages = glob.glob(os.path.join(vault, *[x for x in course_sub.split("/") if x], code, "W%02d*.md" % wk))
        if pages:
            md = _read(pages[0], 60000)
            hf = _headings_and_flags(md, CAPS["week"])
            if hf:
                parts.append("【本周(W%02d)笔记页的结构和标记】\n" % wk + hf); sources.append("本周页 " + os.path.basename(pages[0])[:30])

    qfiles = sorted(glob.glob(os.path.join(vault, "学习", "本周问题清单*.md")))
    if qfiles:
        md = _read(qfiles[-1], 60000)
        lines = [l.strip() for l in md.splitlines() if code in l or (l.strip().startswith("-") and code.lower() in l.lower())]
        if lines:
            parts.append("【问题清单里这门课的条目】\n" + "\n".join(lines)[:CAPS["questions"]]); sources.append("问题清单")

    text = ("以下是关于这位学生和「%s」这门课的背景,回答时要贴合它;没提到的别编。\n\n" % course_name) + "\n\n".join(parts) if parts else ""
    text = text[:TOTAL_CAP]
    return {"text": text, "sources": sources, "chars": len(text), "tokens_est": int(len(text) / 1.6)}
