# -*- coding: utf-8 -*-
"""
起飞前检查单：部署之前跑一下，确认要传的东西没问题。

检查项：
  1. 运行必需的文件在不在（app.py / static / templates）
  2. 有没有把 data/ 这种业务数据混进"要上传的文件"里
  3. app.py 只用标准库，且没有用到 Python 3.12/3.13 已删除的模块
  4. requirements.txt 里有没有第三方依赖（有的话 Render 会自动装，但要确认写对了）
  5. render.yaml 是否存在（Blueprint 要用）

用法：
    python tools\\check_deploy_ready.py
"""

import ast
import os
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
problems = []

# 这些目录不算"要上传的东西"：业务数据、临时测试库、字节码缓存、截图
SKIP_DIRS = {"data", ".testdata", "__pycache__", "screenshots", "_shots2", ".git"}

# ⚠️ app.py 之外的 .py 模块也要在这里点名（漏传会 ImportError，所有页面 500）。
REQUIRED = ["app.py", "requirements.txt", "render.yaml",
            "static/style.css", "static/site.js", "static/admin.js", "static/zone.js",
            "templates/index.html", "templates/admin.html", "templates/zone.html",
            "templates/terms.html"]

REMOVED_IN_PY = {
    "3.12": ["distutils", "asynchat", "asyncore", "smtpd", "imp"],
    "3.13": ["cgi", "cgitb", "pipes", "telnetlib", "crypt", "nis", "spwd", "sndhdr",
             "uu", "xdrlib", "lib2to3", "mailcap", "msilib", "ossaudiodev", "audioop",
             "chunk", "sunau", "aifc", "imghdr"],
}
FLAT_REMOVED = {m: v for v, mods in REMOVED_IN_PY.items() for m in mods}

print("=" * 62)
print(" 起飞前检查单")
print("=" * 62)

print("\n【1】运行必需的文件")
for rel in REQUIRED:
    ok = os.path.isfile(os.path.join(ROOT, rel))
    print("  %s %s" % ("[OK]  " if ok else "[FAIL]", rel))
    if not ok:
        problems.append("缺少必需文件：" + rel)

print("\n【2】会不会把业务数据传上去")
leaked = []
for folder, subdirs, files in os.walk(ROOT):
    if any(part in SKIP_DIRS for part in folder.split(os.sep)):
        continue
    for f in files:
        rel = os.path.relpath(os.path.join(folder, f), ROOT)
        if rel.split(os.sep)[0] in SKIP_DIRS:
            leaked.append(rel)
print("  [OK]   没有 data/ 之类的东西会被打包" if not leaked
      else "  [FAIL] 发现业务数据：%s" % leaked[:5])
if leaked:
    problems.append("有业务数据混进上传范围")

print("\n【3】app.py 的模块兼容性")
tree = ast.parse(open(os.path.join(ROOT, "app.py"), encoding="utf-8").read())
mods = set()
for node in ast.walk(tree):
    if isinstance(node, ast.Import):
        for a in node.names:
            mods.add(a.name.split(".")[0])
    elif isinstance(node, ast.ImportFrom) and node.module:
        mods.add(node.module.split(".")[0])
bad = sorted(m for m in mods if m in FLAT_REMOVED)
print("  导入了 %d 个模块：%s" % (len(mods), ", ".join(sorted(mods))))
if bad:
    print("  [FAIL] 这些模块在新版 Python 里已被删除：%s" % bad)
    problems.append("用了被删除的模块：%s" % bad)
else:
    print("  [OK]   全部是标准库，Python 3.9 ~ 3.13 都能跑")

print("\n【4】requirements.txt")
req_path = os.path.join(ROOT, "requirements.txt")
if os.path.isfile(req_path):
    lines = [l.strip() for l in open(req_path, encoding="utf-8")
             if l.strip() and not l.strip().startswith("#")]
    if lines:
        print("  [注意] 有 %d 个第三方依赖，Render 会自动 pip install：" % len(lines))
        for l in lines:
            print("         ", l)
    else:
        print("  [OK]   没有第三方依赖（零依赖项目）")
else:
    print("  [FAIL] 没有 requirements.txt")
    problems.append("缺少 requirements.txt")

print("\n【5】render.yaml")
ry = os.path.join(ROOT, "render.yaml")
print("  %s render.yaml %s" % ("[OK]  " if os.path.isfile(ry) else "[FAIL]",
                               "存在（Blueprint 会读它）" if os.path.isfile(ry) else "不存在"))
if not os.path.isfile(ry):
    problems.append("缺少 render.yaml（不影响手动建服务，但 Blueprint 用不了）")

print("\n" + "=" * 62)
if problems:
    print(" 发现 %d 个问题：" % len(problems))
    for p in problems:
        print("   -", p)
    sys.exit(1)
print(" 全部通过，可以推上 GitHub 了。")
print("\n 下一步：")
print("   路线 A：把 peiwan-site-上传用.zip 解压后拖进 GitHub 上传框")
print("   路线 B：python tools\\push_to_github.py --repo 你的用户名/peiwan-site（需要令牌）")
sys.exit(0)
