"""askmem.py 的测试:记一条、读最近、标记已写、记忆文本封顶。运行:venv\\Scripts\\python.exe tests\\test_askmem.py"""
import os, sys, tempfile, traceback
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import askmem


def test_append_load_mark():
    askmem.init(tempfile.mkdtemp())
    a = askmem.append("COMP6203", "什么是纳什均衡?", "每个人都不想单独改策略的状态。")
    b = askmem.append("COMP6203", "和帕累托最优的区别?", "一个讲稳定,一个讲效率。")
    askmem.append("COMP6246", "别的课", "x")
    rows = askmem.load("COMP6203", 10)
    assert [r["q"] for r in rows] == ["什么是纳什均衡?", "和帕累托最优的区别?"]
    assert rows[0]["saved"] is False
    hit = askmem.mark_saved("COMP6203", a["id"])
    assert hit and hit["q"] == a["q"]
    assert askmem.load("COMP6203", 10)[0]["saved"] is True
    assert askmem.mark_saved("COMP6203", "nope") is None
    assert askmem.load("ECSP6002") == []


def test_memory_text_cap():
    askmem.init(tempfile.mkdtemp())
    for i in range(30):
        askmem.append("其他", "问题%d " % i + "x" * 300, "答案%d " % i + "y" * 500)
    t = askmem.memory_text("其他", n=12, limit=2500)
    assert len(t) <= 2500 and "问题29" in t and "问题0 " not in t


def test_note_block():
    blk = askmem.note_block({"t": "2026-10-04 10:00", "q": "Q?", "a": "A."}, "COMP6203 智能体", "10-04 10:00 课上")
    assert "## 2026-10-04 10:00 · 10-04 10:00 课上" in blk and "**问:** Q?" in blk and "A." in blk


if __name__ == "__main__":
    fails = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            try:
                fn(); print("PASS", name)
            except Exception:
                fails += 1; print("FAIL", name); traceback.print_exc()
    print("\n%d failed" % fails); sys.exit(1 if fails else 0)
