"""slides.py 的测试:抽文本(用真实课件)、检索、术语补全。运行:venv\\Scripts\\python.exe tests\\test_slides.py"""
import os, sys, tempfile, traceback, shutil
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import slides

PDF = r"D:/论文库/课程文献/COMP6203/slides/W02-Strategic-form-Games-I.pdf"
PPTX = r"D:/论文库/课程文献/COMP6246/slides/W01-L1-MLT_L1_21Sep.pptx"


def fresh():
    d = tempfile.mkdtemp(); slides.init(d); return d


def test_pdf_and_pptx_extract():
    fresh()
    if os.path.exists(PDF):
        r = slides.add("COMP6203", PDF)
        assert r["pages"] > 5 and r["chars"] > 2000, r
        print("  pdf:", r["pages"], "页", r["chars"], "字, 新术语", r["new_terms"][:8])
    if os.path.exists(PPTX):
        r = slides.add("COMP6246", PPTX)
        assert r["pages"] > 3, r
        print("  pptx:", r["pages"], "页", r["chars"], "字")


def test_select_picks_relevant_page():
    fresh()
    d = tempfile.mkdtemp()
    md = os.path.join(d, "notes.md")
    open(md, "w", encoding="utf-8").write("# Nash equilibrium\nA Nash equilibrium is a strategy profile where no player can gain by deviating.\n# Auctions\nSecond price auction bidders bid their true value.\n# Voting\nCondorcet winner beats every other candidate pairwise.")
    r = slides.add("其他", md, "notes.md")
    assert r["pages"] == 3
    sel = slides.select("其他", "so the teacher said in a Nash equilibrium nobody wants to deviate")
    assert "Nash equilibrium" in sel and "Condorcet" not in sel.split("---")[0]
    assert slides.select("其他", "") == ""
    assert slides.select("COMP6231", "search tree") == ""          # 没课件的课返回空
    assert slides.list_files("其他")[0]["name"] == "notes.md"
    assert slides.remove("其他", "notes.md") and slides.list_files("其他") == []


def test_terms_appended_once():
    fresh()
    pages = [{"n": 1, "text": "Bayesian Networks and Markov Decision Process (MDP). MDP again. Bayesian Networks again."}]
    new = slides.add_terms("COMP6231", pages)
    assert "MDP" in new and "Bayesian Networks" in new
    assert slides.add_terms("COMP6231", pages) == []                 # 第二次不重复加
    txt = open(os.path.join(slides.TERMS_DIR, "COMP6231.txt"), encoding="utf-8").read()
    assert txt.count("MDP") == 1


if __name__ == "__main__":
    fails = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            try:
                fn(); print("PASS", name)
            except Exception:
                fails += 1; print("FAIL", name); traceback.print_exc()
    print("\n%d failed" % fails); sys.exit(1 if fails else 0)
