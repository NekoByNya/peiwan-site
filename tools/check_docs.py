# -*- coding: utf-8 -*-
"""
检查 docs\\代码导读.md 里引用的行号和代码片段是否还对得上 app.py。

什么时候用：你改过 app.py 之后跑一下。如果提示某处对不上，
说明代码导读里那个行号过期了，需要更新（内容没错，只是行号漂移了）。

用法：
    python tools\\check_docs.py
"""

import os
import re
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP = os.path.join(ROOT, "app.py")
DOC = os.path.join(ROOT, "docs", "代码导读.md")

if not os.path.isfile(APP) or not os.path.isfile(DOC):
    print("找不到 app.py 或 docs\\代码导读.md，请在项目目录里运行。")
    sys.exit(1)

lines = open(APP, encoding="utf-8").read().split("\n")
doc = open(DOC, encoding="utf-8").read()

problems = []
checked = 0

# ---------- 1. 检查「`函数名`（第 N 行）」这种引用 ----------
print("=== 函数与行号 ===")
# 文档里函数名可能带反引号，比如 `do_GET`（第 658 行），所以反引号要可选
for m in re.finditer(r"`?([A-Za-z_][A-Za-z0-9_]*)`?\s*（第\s*(\d+)\s*行）", doc):
    name, num = m.group(1), int(m.group(2))
    checked += 1
    if num - 1 >= len(lines):
        problems.append("第 %d 行超出文件范围（文档里提到 %s）" % (num, name))
        continue
    actual = lines[num - 1]
    if name in actual:
        print("  [OK]   %-18s 第 %d 行" % (name, num))
    else:
        now = [i + 1 for i, l in enumerate(lines)
               if ("def " + name) in l or ("class " + name) in l]
        hint = ("它现在在第 %d 行" % now[0]) if now else "在 app.py 里找不到这个名字"
        problems.append("%s：文档说第 %d 行，实际那行是 %r（%s）"
                        % (name, num, actual.strip()[:40], hint))
        print("  [漂移] %-18s 文档说 %d 行，%s" % (name, num, hint))

# ---------- 2. 检查章节范围「第 N–M 行」 ----------
print("\n=== 章节与范围引用 ===")
for m in re.finditer(r"（第\s*(\d+)\s*[–\-]\s*(\d+)\s*行", doc):
    start = int(m.group(1))
    checked += 1
    if start - 1 >= len(lines):
        problems.append("范围起点第 %d 行超出文件范围" % start)
        continue
    line = lines[start - 1].strip()
    # 范围起点应该落在"顶层的东西"上：章节注释、def/class、或模块级赋值（比如 SCHEMA = """）。
    # 如果落在缩进的行上，说明行号漂到某个函数体里了，那多半是过期了。
    if line.startswith("#") or line.startswith("def ") or line.startswith("class ") \
            or (line and not lines[start - 1].startswith((" ", "\t"))):
        print("  [OK]   第 %d 行 = %s" % (start, line[:46]))
    else:
        problems.append("范围引用第 %d 行，但那行是 %r，看起来不是章节或函数开头"
                        % (start, line[:40]))
        print("  [FAIL] 第 %d 行 = %s" % (start, line[:46]))

# ---------- 3. 检查文档里贴的代码片段 ----------
print("\n=== 代码片段 ===")


def code_only(line):
    """
    只比较"代码本身"：砍掉行尾注释、把连续空格压成一个。
    因为文档里为了讲解，会在代码后面加中文注释（比如 return "past"  # 1. 已经过去了），
    这不算抄错。
    """
    cut = line.find(" #")
    if cut >= 0:
        line = line[:cut]
    return " ".join(line.split())


src_normalized = {code_only(l) for l in lines}
snippets = []
in_block = False
for raw in doc.split("\n"):
    if raw.strip().startswith("```python"):
        in_block = True
        continue
    if raw.strip().startswith("```"):
        in_block = False
        continue
    if in_block:
        stripped = raw.strip()
        if len(stripped) < 15 or stripped.startswith("#") or "..." in stripped:
            continue
        snippets.append(stripped)

for snip in snippets:
    checked += 1
    if code_only(snip) in src_normalized:
        print("  [OK]   %s" % snip[:66])
    else:
        problems.append("代码片段在 app.py 里找不到：%s" % snip[:70])
        print("  [FAIL] %s" % snip[:66])

# ---------- 4. 检查其他文档里引用的"界面文字"是否真的存在 ----------
# 文档里用「」引起来的，按约定都是界面或代码里真实存在的文字。
# 这一项能防止"文档让你点『某某按钮』，而界面其实叫别的名字"。
print("\n=== 界面文字核对（docs\\上线后第一周.md）===")
launch = os.path.join(ROOT, "docs", "上线后第一周.md")
if os.path.isfile(launch):
    doc2 = open(launch, encoding="utf-8").read()
    sources = {}
    for rel in ["templates/admin.html", "templates/index.html", "static/site.js",
                "static/admin.js", "app.py", "DEPLOY.md", "README.md"]:
        p = os.path.join(ROOT, rel)
        if os.path.isfile(p):
            sources[rel] = open(p, encoding="utf-8").read()
    quoted = sorted(set(re.findall(r"「([^」]+)」", doc2)))
    for q in quoted:
        checked += 1
        hits = [name for name, src in sources.items() if q in src]
        if hits:
            print("  [OK]   「%s」 → %s" % (q, hits[0]))
        else:
            problems.append("《上线后第一周.md》里的「%s」在页面和代码里都找不到" % q)
            print("  [FAIL] 「%s」 找不到" % q)
else:
    print("  （没有这个文件，跳过）")

# ---------- 结论 ----------
print("\n" + "=" * 60)
print(" 共核对 %d 处" % checked)
if problems:
    print(" 有 %d 处对不上：" % len(problems))
    for p in problems:
        print("   -", p)
    print("\n 多半只是行号漂移或文案改名（你改过 app.py / 页面文字）。")
    print(" 打开 docs\\代码导读.md 或 docs\\上线后第一周.md 把对应数字/文字改一下即可。")
    sys.exit(1)
print(" 全部对得上，文档是准确的。")
sys.exit(0)
