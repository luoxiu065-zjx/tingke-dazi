"""2026-10-02 事故的回归测试:live.md 被别的程序占用时,写文件不能把工作线程拖死。

不 import app(那会加载声卡和模型,可能干扰正在录的课),只把要测的两个函数从 app.py 里取出来单独跑。
运行:venv\\Scripts\\python.exe tests\\test_live_write.py
"""
import ast, os, sys, tempfile, threading, time, traceback

APP = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app.py")


def load(*names):
    """从 app.py 里按名字取出函数定义,在一个干净的命名空间里执行"""
    tree = ast.parse(open(APP, encoding="utf-8").read())
    found = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names]
    missing = set(names) - {n.name for n in found}
    assert not missing, "app.py 里还没有这些函数: %s" % sorted(missing)
    logs = []
    ns = {"os": os, "time": time, "traceback": traceback, "threading": threading,
          "log": logs.append, "status": lambda msg, level="info": logs.append("[%s] %s" % (level, msg))}
    exec(compile(ast.Module(body=found, type_ignores=[]), APP, "exec"), ns)
    return ns, logs


def hold_open(path, seconds):
    """模拟别的程序占着文件:普通方式打开(不允许别人替换它),过一会儿再放开"""
    f = open(path, "r", encoding="utf-8")
    threading.Timer(seconds, f.close).start()
    return f


def test_reproduce_root_cause():
    """事故本身:目标文件被占用时,os.replace 直接抛 PermissionError"""
    d = tempfile.mkdtemp()
    dst, tmp = os.path.join(d, "live.md"), os.path.join(d, "live.md.tmp")
    open(dst, "w").write("old"); open(tmp, "w").write("new")
    f = hold_open(dst, 0.3)
    try:
        os.replace(tmp, dst)
    except PermissionError as e:
        print("  复现成功:", e.__class__.__name__, getattr(e, "winerror", ""))
    else:
        raise AssertionError("没有复现出 PermissionError")
    finally:
        f.close()


def test_replace_retry_waits_for_short_lock():
    """占用 0.15 秒就放开:重试之后应当写成功"""
    ns, _ = load("_replace_retry")
    d = tempfile.mkdtemp()
    dst, tmp = os.path.join(d, "live.md"), os.path.join(d, "live.md.tmp")
    open(dst, "w").write("old"); open(tmp, "w").write("new")
    hold_open(dst, 0.15)
    assert ns["_replace_retry"](tmp, dst) is True
    assert open(dst).read() == "new"


def test_replace_retry_gives_up_without_raising():
    """一直被占用:应当返回 False,而不是抛异常"""
    ns, _ = load("_replace_retry")
    d = tempfile.mkdtemp()
    dst, tmp = os.path.join(d, "live.md"), os.path.join(d, "live.md.tmp")
    open(dst, "w").write("old"); open(tmp, "w").write("new")
    f = hold_open(dst, 5)
    try:
        assert ns["_replace_retry"](tmp, dst) is False
        assert open(dst).read() == "old"
    finally:
        f.close()


def test_forever_restarts_worker_and_logs():
    """工作线程里抛了没接住的异常:要写日志、界面报错,并且线程接着干活"""
    ns, logs = load("forever")
    calls = []

    def worker():
        calls.append(1)
        if len(calls) < 3:
            raise PermissionError("模拟 live.md 被占用")
        # 第 3 次正常返回,线程结束

    ns["time"] = type("T", (), {"sleep": staticmethod(lambda s: None)})    # 测试里不真等 1 秒
    t = threading.Thread(target=ns["forever"](worker), daemon=True)
    t.start(); t.join(3)
    assert not t.is_alive() and len(calls) == 3, "线程没有在出错后重新进入: calls=%d" % len(calls)
    text = "\n".join(logs)
    assert "worker" in text and "PermissionError" in text, "日志里没有记下报错:\n" + text


if __name__ == "__main__":
    failed = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            try:
                fn(); print("PASS", name)
            except Exception as e:
                failed += 1; print("FAIL", name, "->", str(e).splitlines()[0][:150])
    sys.exit(1 if failed else 0)
