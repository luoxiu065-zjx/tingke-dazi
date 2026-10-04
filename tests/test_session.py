"""session.py / textkit.py 的测试:落盘、续录、电量、批量翻译解析。
运行:venv\\Scripts\\python.exe tests\\test_session.py
"""
import os, sys, json, datetime, tempfile, traceback

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import session, textkit


def fresh():
    d = tempfile.mkdtemp()
    session.init(d)
    return d


def test_dump_load_roundtrip():
    fresh()
    start = datetime.datetime(2026, 10, 1, 15, 3, 0)
    hist = [{"id": 1, "t": "00:00:01", "clock": "15:03:05", "en": "Hello class", "zh": "同学们好"}]
    assert session.dump("COMP6246 机器学习技术", start, 1560.4, "dev1", "麦克风 · X", hist,
                        [{"topic": "开场", "points": []}], 1, [{"id": 1, "question_en": "q"}])
    d = session.load()
    assert d and d["course"] == "COMP6246 机器学习技术"
    assert d["start_dt"] == start and d["elapsed"] == 1560.4
    assert d["history"] == hist and d["summary_upto"] == 1 and d["qa"][0]["id"] == 1
    assert "COMP6246" in session.describe(d) and "1 句" in session.describe(d)


def test_load_rejects_bad_or_empty():
    fresh()
    assert session.load() is None                       # 没文件
    open(session.path(), "w").write("{broken")
    assert session.load() is None                       # 坏文件
    json.dump({"course": "X", "start": "2026-10-01T10:00:00", "history": []}, open(session.path(), "w"))
    assert session.load() is None                       # 空课不算
    session.clear(); session.clear()                    # 重复清除不报错
    assert not os.path.exists(session.path())


def test_gap_row():
    d = {"start": "2026-10-01T15:03:00", "saved_at": "2026-10-01T15:29:40", "elapsed": 1560}
    row = session.gap_row(d, 62, datetime.datetime(2026, 10, 1, 16, 2))
    assert row["id"] == 62 and row["gap"] is True and row["t"] == "00:26:00"
    assert "15:29" in row["en"] and "意外中断" in row["en"]


def test_battery_shape():
    pct, plugged = session.battery()
    assert pct is None or (0 <= pct <= 100 and plugged in (True, False, None))
    print("  本机电量:", pct, "插电:", plugged)


def test_parse_batch_json_and_lines():
    items = [{"id": 10, "en": "a"}, {"id": 11, "en": "b"}, {"id": 12, "en": "c"}]
    assert textkit.batch_prompt(items) == "1. a\n2. b\n3. c"
    out = textkit.parse_batch('好的 [{"i":1,"zh":"甲"},{"i":3,"zh":"丙"},{"i":9,"zh":"x"}]', items)
    assert out == {10: "甲", 12: "丙"}                  # 编号越界的丢掉,缺的留给单句重翻
    out = textkit.parse_batch("1. 甲\n2、乙\n3: 丙", items)
    assert out == {10: "甲", 11: "乙", 12: "丙"}
    assert textkit.parse_batch("乱七八糟", items) == {}


if __name__ == "__main__":
    fails = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            try:
                fn(); print("PASS", name)
            except Exception:
                fails += 1; print("FAIL", name); traceback.print_exc()
    print("\n%d failed" % fails)
    sys.exit(1 if fails else 0)
