# -*- coding: utf-8 -*-
# ------------------------------------------------------------------
# 听课搭子 (tingke-dazi) · Copyright (c) 2026 luoxiu065-zjx
# 原始仓库:https://github.com/luoxiu065-zjx/tingke-dazi
# 转载、二次发布请保留本版权声明和原作者署名。build: tkdz-1bf2795e
# ------------------------------------------------------------------
"""生成网页版「电脑布局」页面:把电脑版 index.html 原样复制过来,只在主脚本前后各插一个适配脚本。
电脑版界面以后改了,重跑这个脚本就同步过来。用法:python make_desk.py [电脑版 index.html 路径]"""
import sys, os, re
SRC = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "index.html")
DST = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "desk.html")
s = open(SRC, encoding="utf-8").read()
assert s.count("<script>") == 1 and s.count("</script>") == 1, "电脑版页面结构变了:应该只有一个内联主脚本"
a = s.index("<script>"); b = s.index("</script>", a) + len("</script>")
SHIM = '<script src="/live/static/desk-shim.js"></script>\n'
AFTER = '\n<script src="/live/static/desk-after.js"></script>'
s = s[:a] + SHIM + s[a:b] + AFTER + s[b:]
s = re.sub(r"<title>.*?</title>", "<title>听课搭子 · 网页版</title>", s, count=1)
if 'name="viewport"' not in s:
    s = s.replace("<head>", '<head>\n<meta name="viewport" content="width=device-width, initial-scale=1">', 1)
open(DST, "w", encoding="utf-8").write(s)
print("已生成", DST, len(s), "字节")
