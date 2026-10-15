# -*- coding: utf-8 -*-
"""
打包上传包：把"要传上 GitHub 的文件"打成 zip，方便你备份或在另一台电脑上打开。

用法：
    python tools\\make_package.py

产出：
    项目外层的 peiwan-site-上传用.zip
（data/、.testdata/、__pycache__ 不会被包进去——那是你的业务数据，不该上传）
"""

import os
import sys
import zipfile

# Windows 中文控制台默认是 GBK，这里统一成 UTF-8，中文和符号才不会报错
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(os.path.dirname(ROOT), "peiwan-site-上传用.zip")

# 要打包的文件（显式列出来，避免误把数据打进去）
FILES = ["app.py", "wsgi.py", "requirements.txt", "render.yaml", "start.bat", "start-lan.bat",
         "README.md", "DEPLOY.md", "DEPLOY-PYTHONANYWHERE.md", ".gitignore"]
DIRS = ["static", "templates", "tests", "docs", "tools", ".github"]
# 这些目录永远不进包：数据、临时测试库、缓存的字节码、以及**给主人看的截图目录**
# （_shots2 里是几 MB 的 PNG，打进去只会让上传包变肥；它同时在 .gitignore 里）
SKIP_DIRS = {"data", ".testdata", "__pycache__", "screenshots", "_shots2", ".git"}


def collect():
    items = []
    for name in FILES:
        path = os.path.join(ROOT, name)
        if os.path.isfile(path):
            items.append((path, name))
        else:
            print("  [跳过] 找不到 %s" % name)
    for name in DIRS:
        base = os.path.join(ROOT, name)
        for folder, subdirs, files in os.walk(base):
            subdirs[:] = [d for d in subdirs if d not in SKIP_DIRS]
            for f in files:
                full = os.path.join(folder, f)
                items.append((full, os.path.relpath(full, ROOT)))
    return items


def main():
    items = collect()
    if os.path.exists(OUT):
        os.remove(OUT)
    with zipfile.ZipFile(OUT, "w", zipfile.ZIP_DEFLATED) as z:
        for full, rel in items:
            z.write(full, rel)
    size = os.path.getsize(OUT)
    print("已打包 %d 个文件 → %s（%.1f KB）" % (len(items), OUT, size / 1024))
    print("\n包内清单：")
    for _, rel in sorted(items, key=lambda x: x[1]):
        print("  " + rel.replace("\\", "/"))
    leaked = [rel for _, rel in items if rel.split(os.sep)[0] in SKIP_DIRS]
    if leaked:
        print("\n[警告] 有数据文件被打进去了：", leaked)
        return 1
    print("\n确认：没有 data/ 之类的东西被打进去 [OK]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
