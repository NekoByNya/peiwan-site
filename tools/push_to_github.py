# -*- coding: utf-8 -*-
"""
把你的项目文件一次性推送到 GitHub 仓库（**不需要安装 git**）。

用法：
    # 1. 先空跑，看要传哪些文件
    python tools\\push_to_github.py --repo 你的用户名/peiwan-site --dry-run

    # 2. 确认没问题再真推（令牌只从环境变量读）
    set GITHUB_TOKEN=github_pat_xxxxxxxx
    python tools\\push_to_github.py --repo 你的用户名/peiwan-site

安全说明：
    · 令牌只从环境变量或 --token 参数读，脚本绝不把它写进任何文件
    · 先用 --dry-run 看清清单，再决定要不要真推
    · 传完请回 GitHub 令牌页面把它删掉
"""

import argparse
import base64
import json
import os
import sys
import urllib.error
import urllib.request

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# 正常情况下就是 GitHub 官方 API；留这个环境变量是为了能对着本地假服务器做测试
API = os.environ.get("GITHUB_API") or "https://api.github.com"

# 要传的文件（和 tools/make_package.py 保持一致）
FILES = ["app.py", "requirements.txt", "render.yaml", "start.bat",
         "README.md", "DEPLOY.md", ".gitignore"]
DIRS = ["static", "templates", "tests", "docs", "tools"]
SKIP_DIRS = {"data", ".testdata", "__pycache__", "screenshots", ".git"}


def collect():
    items = []
    for name in FILES:
        path = os.path.join(ROOT, name)
        if os.path.isfile(path):
            items.append((name.replace("\\", "/"), path))
    for name in DIRS:
        for folder, subdirs, files in os.walk(os.path.join(ROOT, name)):
            subdirs[:] = [d for d in subdirs if d not in SKIP_DIRS]
            for f in files:
                full = os.path.join(folder, f)
                rel = os.path.relpath(full, ROOT).replace("\\", "/")
                items.append((rel, full))
    return sorted(items)


def api(method, path, token=None, payload=None):
    url = path if path.startswith("http") else API + path
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("User-Agent", "peiwan-deploy")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    if data:
        req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            body = r.read()
            return r.getcode(), (json.loads(body) if body else {})
    except urllib.error.HTTPError as e:
        body = e.read()
        try:
            return e.code, json.loads(body)
        except Exception:
            return e.code, {"message": body[:200].decode("utf-8", "replace")}
    except Exception as e:
        return None, {"message": "%s: %s" % (type(e).__name__, e)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True, help="形如 用户名/仓库名")
    ap.add_argument("--branch", default="main")
    ap.add_argument("--token", default=os.environ.get("GITHUB_TOKEN"))
    ap.add_argument("--message", default="第一版：陪玩预约站")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    items = collect()
    total = sum(os.path.getsize(p) for _, p in items)
    leaked = [rel for rel, _ in items if rel.split("/")[0] in SKIP_DIRS]

    print("=" * 64)
    print(" 要推送的仓库：%s（分支 %s）" % (args.repo, args.branch))
    print(" 文件数：%d 个，合计 %.0f KB" % (len(items), total / 1024))
    print("=" * 64)
    for rel, path in items:
        print("  %8.1f KB  %s" % (os.path.getsize(path) / 1024, rel))

    if leaked:
        print("\n[中止] 检测到不该上传的文件：", leaked)
        print("（data/ 里是你的预约记录、密码和上传的图片）")
        return 1

    if args.dry_run:
        print("\n空跑完成：没有 data/ 之类的东西，可以放心真推。")
        print("真推命令： set GITHUB_TOKEN=你的令牌  &&  python tools\\push_to_github.py --repo %s"
              % args.repo)
        return 0

    if not args.token:
        print("\n[中止] 没有令牌。请先设置环境变量 GITHUB_TOKEN，或用 --token 传进来。")
        return 1

    # ---------- 1. 确认仓库可写 ----------
    code, info = api("GET", "/repos/" + args.repo, args.token)
    if code != 200:
        print("\n[失败] 访问仓库失败（HTTP %s）：%s" % (code, info.get("message")))
        print("检查：用户名/仓库名写对了吗？令牌勾了 Contents: Read and write 吗？")
        return 1
    print("\n[1/6] 仓库确认：%s（默认分支 %s）" % (info.get("full_name"), info.get("default_branch")))
    if not (info.get("permissions") or {}).get("push"):
        print("[失败] 这个令牌没有写入权限（permissions.push 是 false）")
        return 1

    # ---------- 2. 看仓库里现在有没有东西 ----------
    code, ref = api("GET", "/repos/%s/git/ref/heads/%s" % (args.repo, args.branch), args.token)
    parent_sha = base_tree = None
    if code == 200:
        parent_sha = ref["object"]["sha"]
        _, commit = api("GET", "/repos/%s/git/commits/%s" % (args.repo, parent_sha), args.token)
        base_tree = commit.get("tree", {}).get("sha")
        print("[2/6] 仓库已有内容，会在它的基础上添加（不会删掉你原有的文件）")
    else:
        # 全新的空仓库：GitHub 不允许直接用 Git Data API 建 blob（会返回 409 Git Repository is empty），
        # 所以先用 Contents API 传一个文件，制造出第一个提交。
        print("[2/6] 空仓库：先用 Contents API 建一个初始提交（GitHub 对空仓库有这个限制）")
        seed_rel = "README.md" if os.path.isfile(os.path.join(ROOT, "README.md")) else ".gitignore"
        with open(os.path.join(ROOT, seed_rel), "rb") as f:
            seed_content = base64.b64encode(f.read()).decode()
        code, res = api("PUT", "/repos/%s/contents/%s" % (args.repo, seed_rel), args.token,
                        {"message": "初始化仓库", "content": seed_content, "branch": args.branch})
        if code not in (200, 201):
            print("[失败] 初始化空仓库出错（HTTP %s）：%s" % (code, res.get("message")))
            return 1
        print("      已创建初始提交（%s）" % seed_rel)
        code, ref = api("GET", "/repos/%s/git/ref/heads/%s" % (args.repo, args.branch), args.token)
        if code == 200:
            parent_sha = ref["object"]["sha"]
            _, commit = api("GET", "/repos/%s/git/commits/%s" % (args.repo, parent_sha), args.token)
            base_tree = commit.get("tree", {}).get("sha")

    # ---------- 3. 上传每个文件（生成 blob） ----------
    tree = []
    for rel, path in items:
        with open(path, "rb") as f:
            blob = base64.b64encode(f.read()).decode()
        code, res = api("POST", "/repos/%s/git/blobs" % args.repo, args.token,
                        {"content": blob, "encoding": "base64"})
        if code not in (201, 200):
            print("[失败] 上传 %s 出错（HTTP %s）：%s" % (rel, code, res.get("message")))
            return 1
        tree.append({"path": rel, "mode": "100644", "type": "blob", "sha": res["sha"]})
        print("      ✓ %s" % rel)
    print("[3/6] %d 个文件已生成 blob" % len(tree))

    # ---------- 4. 建目录树 ----------
    payload = {"tree": tree}
    if base_tree:
        payload["base_tree"] = base_tree
    code, res = api("POST", "/repos/%s/git/trees" % args.repo, args.token, payload)
    if code not in (201, 200):
        print("[失败] 创建目录树出错（HTTP %s）：%s" % (code, res.get("message")))
        return 1
    print("[4/6] 目录树已创建")

    # ---------- 5. 建提交 ----------
    commit_payload = {"message": args.message, "tree": res["sha"]}
    if parent_sha:
        commit_payload["parents"] = [parent_sha]
    code, res = api("POST", "/repos/%s/git/commits" % args.repo, args.token, commit_payload)
    if code not in (201, 200):
        print("[失败] 创建提交出错（HTTP %s）：%s" % (code, res.get("message")))
        return 1
    commit_sha = res["sha"]
    print("[5/6] 提交已创建：%s" % commit_sha[:10])

    # ---------- 6. 移动分支指针 ----------
    if parent_sha:
        code, res = api("PATCH", "/repos/%s/git/refs/heads/%s" % (args.repo, args.branch),
                        args.token, {"sha": commit_sha})
    else:
        code, res = api("POST", "/repos/%s/git/refs" % args.repo, args.token,
                        {"ref": "refs/heads/" + args.branch, "sha": commit_sha})
    if code not in (201, 200):
        print("[失败] 更新分支出错（HTTP %s）：%s" % (code, res.get("message")))
        return 1
    print("[6/6] 分支 %s 已指向这次提交" % args.branch)

    # ---------- 校验：把仓库里的文件列出来对一遍 ----------
    code, res = api("GET", "/repos/%s/git/trees/%s?recursive=1" % (args.repo, commit_sha), args.token)
    remote = sorted(x["path"] for x in res.get("tree", []) if x["type"] == "blob")
    local = sorted(rel for rel, _ in items)
    print("\n" + "=" * 64)
    print(" 校验：本地 %d 个文件，仓库里 %d 个" % (len(local), len(remote)))
    if local == remote:
        print(" 完全一致 [OK]")
    else:
        only_local = [x for x in local if x not in remote]
        only_remote = [x for x in remote if x not in local]
        print(" 只在本地的：", only_local or "无")
        print(" 只在仓库的：", only_remote or "无")
    print("=" * 64)
    print("\n 仓库地址：https://github.com/%s" % args.repo)
    print(" 下一步：去 Render 建服务（见 DEPLOY.md 第 3 步）")
    print("\n 提醒：现在可以回 GitHub 令牌页面把刚才那个令牌删掉了。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
