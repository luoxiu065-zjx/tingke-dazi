"""llmcfg.py 的测试:.env 只改指定键、打码、提供商猜测、校验、用量累计。运行:venv\\Scripts\\python.exe tests\\test_llmcfg.py"""
import os, sys, tempfile, traceback
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import llmcfg


def test_env_roundtrip_keeps_other_lines():
    p = os.path.join(tempfile.mkdtemp(), ".env")
    open(p, "w", encoding="utf-8").write("# 注释\nDEEPSEEK_API_KEY=old\nWHISPER_MODEL=large-v3-turbo\n\nCOURSES=a,b\n")
    llmcfg.write_env(p, {"DEEPSEEK_API_KEY": "new", "DEEPSEEK_MODEL": "deepseek-chat"})
    txt = open(p, encoding="utf-8").read()
    assert "# 注释" in txt and "WHISPER_MODEL=large-v3-turbo" in txt and "COURSES=a,b" in txt
    assert "DEEPSEEK_API_KEY=new" in txt and "old" not in txt and txt.rstrip().endswith("DEEPSEEK_MODEL=deepseek-chat")
    env = llmcfg.read_env(p)
    assert env["DEEPSEEK_API_KEY"] == "new" and env["COURSES"] == "a,b" and "注释" not in str(env)


def test_mask_guess_validate():
    assert llmcfg.mask("sk-1234567890abcdef") == "sk-1…cdef" and llmcfg.mask("") == "(未填)"
    assert llmcfg.guess_provider("https://api.deepseek.com") == "deepseek"
    assert llmcfg.guess_provider("https://dashscope.aliyuncs.com/compatible-mode/v1/") == "qwen"
    assert llmcfg.guess_provider("http://10.0.0.2:8000/v1") == "custom"
    assert llmcfg.validate("deepseek", "https://api.deepseek.com", "deepseek-chat", "k") == ""
    assert llmcfg.validate("deepseek", "api.deepseek.com", "deepseek-chat", "k")
    assert llmcfg.validate("deepseek", "https://api.deepseek.com", "deepseek-chat", "")
    assert llmcfg.validate("ollama", "http://127.0.0.1:11434/v1", "qwen2.5:7b", "") == ""


def test_usage():
    class U: prompt_tokens = 120; completion_tokens = 30
    u = llmcfg.Usage(); u.add(U()); u.add(U()); u.add(None)
    assert u.snapshot() == {"prompt": 240, "completion": 60, "calls": 2}
    u.reset(); assert u.snapshot()["calls"] == 0


if __name__ == "__main__":
    fails = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            try:
                fn(); print("PASS", name)
            except Exception:
                fails += 1; print("FAIL", name); traceback.print_exc()
    print("\n%d failed" % fails); sys.exit(1 if fails else 0)
