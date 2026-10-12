# -*- coding: utf-8 -*-
"""
检查 render.yaml 有没有写错（字段名、结构、取值）。

什么时候用：你改过 render.yaml 之后跑一下，免得在 Render 上才发现写错。

它会做三件事：
  1. 把文件里每个字段名，拿去 Render 官方规范原文里逐个核对
  2. 用一个最小解析器检查缩进结构和必需字段（本脚本不依赖 PyYAML）
  3. 检查关键取值（plan / region / runtime / startCommand 等）

用法：
    python tools\\check_render_yaml.py
"""

import os
import re
import sys
import urllib.request

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATH = os.path.join(ROOT, "render.yaml")
SPEC_URL = "https://render.com/docs/blueprint-spec.md"

if not os.path.isfile(PATH):
    print("找不到 render.yaml")
    sys.exit(1)

raw = open(PATH, encoding="utf-8").read()
problems = []

# ---------- ① 字段名核对官方规范 ----------
print("=== ① 字段名对照 Render 官方规范 ===")
try:
    with urllib.request.urlopen(SPEC_URL, timeout=60) as r:
        spec = r.read().decode("utf-8", "replace")
    got_spec = True
except Exception as e:
    print("  [跳过] 连不上官方文档（%s），只做本地结构检查" % type(e).__name__)
    got_spec = False

if got_spec:
    names = []
    for line in raw.split("\n"):
        m = re.match(r"^\s*-?\s*([A-Za-z][A-Za-z0-9_]*)\s*:", line.split("#")[0].rstrip())
        if m and m.group(1) not in names:
            names.append(m.group(1))
    for f in names:
        hit = bool(re.search(r"#####\s+`%s`" % re.escape(f), spec)) or \
              bool(re.search(r"\b%s:" % re.escape(f), spec))
        print("  %s %s" % ("[OK]  " if hit else "[FAIL]", f))
        if not hit:
            problems.append("字段名 %s 不在官方规范里" % f)

# ---------- ② 结构检查 ----------
print("\n=== ② 结构检查 ===")
if "\t" in raw:
    problems.append("文件里有 Tab 字符（YAML 不允许）")
    print("  [FAIL] 有 Tab 字符")
else:
    print("  [OK]   没有 Tab（YAML 只认空格）")

entries = []
for line in raw.split("\n"):
    s = line.split("#")[0].rstrip()
    if not s.strip():
        continue
    indent = len(s) - len(s.lstrip(" "))
    body = s.strip()
    if body.startswith("- "):        # 列表项的 "- " 视作 +2 缩进
        indent += 2
        body = body[2:].strip()
    entries.append((indent, body))

top = [e[1].split(":")[0] for e in entries if e[0] == 0]
print("  顶层字段:", top)
if "services" not in top:
    problems.append("顶层必须有 services")
    print("  [FAIL] 顶层没有 services")

svc = [e[1].split(":")[0].strip() for e in entries if e[0] == 4]
print("  服务字段:", svc)
required = ["type", "name", "runtime", "plan", "buildCommand", "startCommand"]
missing = [k for k in required if k not in svc]
if missing:
    problems.append("缺少必需字段：%s" % missing)
print("  %s 必需字段齐全" % ("[OK]  " if not missing else "[FAIL]"))
if "envVars" not in svc:
    print("  [注意] 没有 envVars（首次启动会用代码里的默认密码）")

# ---------- ③ 取值检查 ----------
print("\n=== ③ 取值检查 ===")
clean = "\n".join(l.split("#")[0].rstrip() for l in raw.split("\n"))
checks = [
    ("type 是 web", r"^\s*-?\s*type:\s*web\s*$"),
    ("runtime 是 python", r"^\s*-?\s*runtime:\s*python\s*$"),
    ("plan 取值合法", r"^\s*-?\s*plan:\s*(free|0\.5c-512mb|1c-2g|2c-4g|2c-8g|2c-16g|"
                      r"4c-8g|4c-16g|4c-32g|8c-16g|8c-32g)\s*$"),
    ("region 取值合法", r"^\s*-?\s*region:\s*(oregon|ohio|virginia|frankfurt|singapore)\s*$"),
    ("startCommand 存在", r"startCommand:\s*\S+"),
]
for name, pattern in checks:
    ok = bool(re.search(pattern, clean, re.M))
    print("  %s %s" % ("[OK]  " if ok else "[FAIL]", name))
    if not ok:
        problems.append(name + " 检查不通过")

if "value: baixu123" in clean:
    problems.append("ADMIN_PASSWORD 硬编码在文件里了（应该用 sync: false）")
    print("  [FAIL] 密码被硬编码在 render.yaml 里了")
else:
    print("  [OK]   没有把管理密码硬编码在文件里")

# ---------- 结论 ----------
print("\n" + "=" * 60)
if problems:
    print(" 有 %d 个问题：" % len(problems))
    for p in problems:
        print("   -", p)
    sys.exit(1)
print(" render.yaml 没问题，Blueprint 能直接读。")
sys.exit(0)
