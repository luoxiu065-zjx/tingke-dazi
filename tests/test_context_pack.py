"""context_pack.py 的测试:用临时 profile 和临时 Obsidian 库,看各来源能不能被读到、缺了会不会跳过、总量封顶。
运行:venv\\Scripts\\python.exe tests\\test_context_pack.py"""
import os, sys, tempfile, traceback, datetime
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import context_pack as cp


def w(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    open(path, "w", encoding="utf-8").write(text)


def make():
    base, vault = tempfile.mkdtemp(), tempfile.mkdtemp()
    w(os.path.join(base, "profile", "我.md"), "南安普顿 MSc AI 学生,英语听力一般,本科没学过博弈论。")
    w(os.path.join(base, "profile", "资料", "本科课表.md"), "本科学过:线性代数、概率论、Python。")
    w(os.path.join(base, "profile", "老师", "COMP6203.md"), "# Enrico Gerding\n研究方向:多智能体、拍卖机制设计。")
    w(os.path.join(base, "profile", "学习状态.md"), "## COMP6203\n没懂:偏好关系 ⪰ 不是等于。\n错题:练习册 I Q1。\n\n## COMP6246\n已掌握:信息增益。")
    w(os.path.join(vault, "MSc课程", "课堂记录", "COMP6203", "2026-09-25 1006 COMP6203 效用.md"),
      '---\ntitle: "效用理论与策略型博弈"\n---\n\n## AI 总结\n\n### 效用函数\n- a\n### 占优策略\n- b\n\n## 课上提问与参考回答\n\n老师问了什么是纳什均衡\n\n## 全程转写(中英对照)\n...')
    w(os.path.join(vault, "MSc课程", "课堂记录", "COMP6203", "2026-09-28 0900 COMP6203 作业 整合笔记.md"), "# 不该被读到\n")
    w(os.path.join(vault, "MSc课程", "COMP6203", "W02 效用理论与策略型博弈I.md"), "# W02\n## 1 效用\n正文\n## 2 博弈\n这里没懂:混合策略\n正文")
    w(os.path.join(vault, "学习", "本周问题清单-2026W40.md"), "- COMP6203:为什么要用期望效用?\n- COMP6246:熵和基尼\n")
    return base, vault


def test_all_sources_present():
    base, vault = make()
    r = cp.build("COMP6203", "COMP6203 智能体", base, vault, today=datetime.date(2026, 9, 30))   # W02
    t = r["text"]
    assert "关于这位学生" in t and "本科没学过博弈论" in t
    assert "本科课表.md" in t and "Enrico" in t
    assert "偏好关系" in t and "信息增益" not in t                   # 只取这门课的学习状态
    assert "效用理论与策略型博弈" in t and "### 占优策略" in t and "不该被读到" not in t
    assert "这里没懂:混合策略" in t and "正文" not in t                 # 周页只要标题和标记行
    assert "期望效用" in t and "熵和基尼" not in t
    assert len(r["sources"]) == 7 and r["chars"] <= cp.TOTAL_CAP, r["sources"]
    print("  sources:", r["sources"], "| chars:", r["chars"], "| tokens≈", r["tokens_est"])


def test_missing_everything_is_empty():
    r = cp.build("COMP6231", "COMP6231 AI基础", tempfile.mkdtemp(), tempfile.mkdtemp())
    assert r["text"] == "" and r["sources"] == [] and r["chars"] == 0


def test_cap():
    base, vault = make()
    w(os.path.join(base, "profile", "我.md"), "x" * 5000)
    r = cp.build("COMP6203", "COMP6203 智能体", base, vault, today=datetime.date(2026, 9, 30))
    assert r["chars"] <= cp.TOTAL_CAP and r["text"].count("x") <= cp.CAPS["me"]


if __name__ == "__main__":
    fails = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            try:
                fn(); print("PASS", name)
            except Exception:
                fails += 1; print("FAIL", name); traceback.print_exc()
    print("\n%d failed" % fails); sys.exit(1 if fails else 0)
