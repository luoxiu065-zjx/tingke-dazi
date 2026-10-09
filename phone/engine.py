# -*- coding: utf-8 -*-
# ------------------------------------------------------------------
# 听课搭子 (tingke-dazi) · Copyright (c) 2026 luoxiu065-zjx
# 原始仓库:https://github.com/luoxiu065-zjx/tingke-dazi
# 转载、二次发布请保留本版权声明和原作者署名。build: tkdz-1bf2795e
# ------------------------------------------------------------------
"""听课搭子手机版的「脑子」:提问识别、AI 总结、答疑、记录排版。从桌面版 app.py 移植,不碰网络和状态。"""
import re, json, datetime

# ---------- 提问初筛(和桌面版一致,2026-09-26 按真实课堂调过) ----------
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
QA_MORE = re.compile(r"\b(?:question|wondering|want to know)\b|[-—…]\s*$", re.I)


def looks_like_question(text):
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
                return True
        if n >= 3 and (_WH_Q.search(s) or _AUX_Q.search(s)):
            return True
    return bool(_QA_CUE.search(text))


def parse_json(s, opener="["):
    closer = "]" if opener == "[" else "}"
    m = re.search(re.escape(opener) + r".*" + re.escape(closer), s, re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except Exception:
        return None


def clean_text(text):
    """去掉 Whisper 的点点点和静音复读幻觉;只剩标点的返回空串。"""
    text = re.sub(r"(\s*\.){3,}", " …", text).strip()
    text = re.sub(r"(\b.{4,40}?)(?:[\s…,.]*\1){2,}", r"\1", text)
    # 2026-10-05:whisper-large-v3 会把 um / uh 这类语气词原样写出来,去掉(只删语气词本身,句子其余不动)
    text = re.sub(r"(?i)(?:^|(?<=[\s,.;:(]))(?:um+|uh+|uhm+|erm?|hmm+|mm+)\b[,.]?\s*", "", text)
    text = re.sub(r"\s{2,}", " ", text)
    text = re.sub(r"\s+([,.;:?!])", r"\1", text)
    text = re.sub(r"^[,.;:\s]+", "", text).strip()
    if text[:1].islower():
        text = text[0].upper() + text[1:]
    if not re.search(r"[A-Za-z]{2,}", text):
        return ""
    return text


def fmt_t(sec):
    sec = int(sec)
    return "%02d:%02d:%02d" % (sec // 3600, sec % 3600 // 60, sec % 60)


# ---------- 提示词 ----------
TRANSLATE_SYS = "你是大学课堂同传。把用户给的英文课堂口语翻成自然的简体中文,专业术语保留英文括注。只输出译文,不要解释、不要引号。"


def qa_prompt(course, terms, prev_lesson, notes_md, before, focus, answered):
    transcript = ("【上文,只用来理解,不要判断也不要回答】\n" +
                  "\n".join("[%s] %s" % (h["t"], h["en"]) for h in before) +
                  "\n\n【待判断的句子】\n" + "\n".join("[%s] %s" % (h["t"], h["en"]) for h in focus))
    return (
        "你是 MSc 学生的课堂助手,正在实时听一节英文课(课程:%s)。下面是课堂转写(机器识别,会有听错的词)。\n"
        "只判断【待判断的句子】里是否有**需要回答的真问题**:老师向全班提问、老师点名提问、或同学提了一个值得知道答案的问题。"
        "口头禅(right? okay?)、自问自答后老师马上说出了答案、修辞性提问、组织课堂的问话(can you hear me? is the mic working? any questions?)都算否。"
        "注意:转写常把问句识别成句号结尾、或把一个问题切成前后两段;同学提问常以 right? / is it? 结尾求确认,这些都可能是真问题。"
        "老师说「who can give me…」「you can give me another one」「what do you think」这类邀请回答的句子也算提问。"
        "上文里的问题不算;和已回答过的问题(" + (answered or "无").replace("%", "%%") + ")是同一个的也算否。\n"
        "只输出 JSON:{\"is_question\":true/false,\"question_en\":\"问题原句(纠正听错的词)\",\"question_zh\":\"一句中文说明在问什么\","
        "\"answer_zh\":\"中文答案要点,不超过 80 字,带关键公式或术语\",\"answer_en\":\"课堂上能直接说出口的英文回答:1-2 句、不超过 35 个词、口语化,先说结论\"}。\n"
        "is_question 为 false 时其他字段留空。答案要基于课程常识和下面的资料,不确定就说明。\n\n"
        "【本课术语】%s\n【上节课讲到哪】%s\n【本节课 AI 笔记】%s\n\n【最近转写】\n%s"
    ) % (course or "", (terms or "")[:400], (prev_lesson or "(无)")[:1500], (notes_md or "(无)")[:1500], transcript)


def summary_prompt(items, new_rows):
    transcript = "\n".join("[%s] %s" % (h["t"], h["en"]) for h in new_rows)
    return (
        "你在给一节英文大学课程做实时中文笔记。下面是【已有笔记】(JSON)和【新的课堂内容】。"
        "把新内容并进笔记,输出更新后的完整笔记 JSON 数组:"
        "[{\"topic\":\"一句中文概括这一部分在讲什么\",\"points\":[{\"k\":\"关键词(4-8字)\",\"v\":\"一句中文说明\"}]}]。\n"
        "规则:按讲课顺序分主题;新内容延续上一主题就往里加要点,换话题就开新主题;每个主题 2-6 条要点;"
        "专业术语保留英文;寒暄、点名、设备调试之类不记;只输出 JSON。\n\n"
        "【已有笔记】\n" + json.dumps(items, ensure_ascii=False) +
        "\n\n【新的课堂内容】\n" + transcript
    )


def title_prompt(material):
    return ("根据下面这节课的内容,起一个 8-20 字的中文标题,概括这节课讲了什么。"
            "只能依据给出的内容,不要编造没出现的主题。只输出标题。\n\n" + material[:3000])


def sane_title(title):
    title = (title or "").strip().strip('"“”《》')
    if not title or len(title) > 24 or re.search(r"无法|不能|抱歉|内容不完整|没有足够", title):
        return "课堂记录"
    return title


# ---------- 排版 ----------
def summary_md(items):
    lines = []
    for sec in items or []:
        lines.append("### " + str(sec.get("topic", "")))
        for p in sec.get("points", []):
            lines.append("- **%s**:%s" % (p.get("k", ""), p.get("v", "")))
        lines.append("")
    return "\n".join(lines)


def qa_md(qa):
    out = []
    for q in qa:
        out += ["**[%s] 🎯 %s**" % (q["t"], q["question_zh"]), "> %s" % q["question_en"], "",
                "💡 %s" % q["answer_zh"], "", "🗣 *%s*" % q["answer_en"], ""]
    return "\n".join(out)


def safe_name(s):
    return re.sub(r'[\\/:*?"<>|#^\[\]\n\r\t]', " ", s).strip()[:60]


def course_code(course):
    return course.split()[0] if course else "其他"


def record_md(course, start, end, dur, title, items, qa, rows, extra_front=None):
    code = course_code(course)
    front = ["---",
             'title: "%s"' % title.replace('"', "'"),
             "course: %s" % code,
             "course_name: \"%s\"" % course,
             "recorded_at: %s" % start.strftime("%Y-%m-%d %H:%M"),
             "ended_at: %s" % end.strftime("%Y-%m-%d %H:%M"),
             "duration: %s" % dur,
             "timezone: Europe/London",
             "source: 听课搭子手机版"]
    for k, v in (extra_front or {}).items():
        front.append("%s: %s" % (k, v))
    front += ["tags: [课堂记录, %s]" % code, "---", ""]
    lines = front + [
        "# " + title, "",
        "录制 %s – %s(英国时间) · 时长 %s · 课程 %s" % (
            start.strftime("%Y-%m-%d %H:%M"), end.strftime("%H:%M"), dur, course), "",
        "## AI 总结", "", summary_md(items) or "(无)", "",
        "## 课上提问与参考回答", "", qa_md(qa) or "(无)", "",
        "## 全程转写(中英对照)", ""]
    for h in rows:
        lines.append("**%s** · %s  " % (h["t"], h["clock"]))
        lines.append(h["en"] + "  ")
        if h.get("zh"):
            lines.append("> " + h["zh"])
        lines.append("")
    return "\n".join(lines)


def extract_prev_lesson(md_text, limit=1500):
    """从上一条课堂记录里取标题 + AI 总结那一节,给新一节课当「上节课讲到哪」。"""
    if not md_text:
        return ""
    m = re.search(r'^title:\s*"?(.+?)"?\s*$', md_text, re.M)
    title = m.group(1) if m else ""
    sec = re.search(r"## AI 总结\s*\n(.*?)(?:\n## |\Z)", md_text, re.S)
    body = (sec.group(1).strip() if sec else "")
    out = ("标题:%s\n%s" % (title, body)).strip()
    return out[:limit]
