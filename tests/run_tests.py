# -*- coding: utf-8 -*-
"""
一键自检：在独立目录里起一个测试实例，跑完整测试，再关掉自动清理。

特点：
  1. **完全不动你的真实数据**——测试实例用项目里的 .testdata 目录，
     你 data/site.db 里的真实预约记录不会被碰到。
  2. 最后会**真的把网站重启一次**，验证"重启/休眠后数据和登录状态都还在"。

用法：
    python tests\\run_tests.py
"""

import http.cookiejar
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PORT = int(os.environ.get("TEST_PORT") or 8199)
TEST_PASSWORD = "testpass123"
TEST_DATA = os.path.join(ROOT, ".testdata")
BASE = "http://127.0.0.1:%d" % PORT

ok = fail = 0


def check(name, cond, extra=""):
    global ok, fail
    if cond:
        ok += 1
        print("  [OK]   %s %s" % (name, extra))
    else:
        fail += 1
        print("  [FAIL] %s %s" % (name, extra))


def wait_ready(url, timeout=30):
    end = time.time() + timeout
    while time.time() < end:
        try:
            urllib.request.urlopen(url, timeout=2)
            return True
        except Exception:
            time.sleep(0.4)
    return False


def start_server(env):
    return subprocess.Popen([sys.executable, "-u", "app.py"], cwd=ROOT, env=env)


def stop_server(proc):
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except Exception:
        proc.kill()


def make_client():
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))

    def call(path, method="GET", payload=None):
        data = json.dumps(payload).encode() if payload is not None else None
        req = urllib.request.Request(BASE + path, data=data, method=method)
        req.add_header("Content-Type", "application/json")
        with opener.open(req, timeout=20) as r:
            return r.getcode(), r.read().decode("utf-8", "replace")

    return jar, call


def main():
    shutil.rmtree(TEST_DATA, ignore_errors=True)
    print("=" * 60)
    print(" 自检开始（测试数据放在：%s）" % TEST_DATA)
    print("=" * 60, flush=True)

    env = dict(os.environ)
    env.update({"PORT": str(PORT), "DATA_DIR": TEST_DATA, "ADMIN_PASSWORD": TEST_PASSWORD,
                "PYTHONIOENCODING": "utf-8",
                # 让「自动释放」的惰性清理每次请求都真的查一遍库 ——
                # 平时它是 30 秒节流一次（省得客户刷页面反复查库），
                # 但测试要在几秒内连续验 5 种边界，节流会把中间几次跳过去。
                "AUTO_RELEASE_CHECK_SECONDS": "0"})

    proc = start_server(env)
    try:
        if not wait_ready(BASE + "/api/public"):
            print("[失败] 网站 30 秒内没有启动成功，请单独运行 python app.py 看报错。")
            return 1
        print("测试实例已就绪：%s\n" % BASE, flush=True)

        tenv = dict(os.environ)
        tenv.update({"TEST_BASE": BASE, "TEST_PASSWORD": TEST_PASSWORD,
                     "TEST_DATA_DIR": TEST_DATA, "PYTHONUNBUFFERED": "1"})
        code = subprocess.call([sys.executable, os.path.join(ROOT, "tests", "test_api.py")], env=tenv)

        # ---------------- 第二阶段：客户自助预约 + 通知 + 数据库迁移 ----------------
        print("\n" + "=" * 60)
        print(" 自助预约测试（客户点一下就锁定 + 老板收到通知）")
        print("=" * 60, flush=True)
        bcode = subprocess.call(
            [sys.executable, os.path.join(ROOT, "tests", "test_booking.py")], env=tenv)

        # ---------------- 第三阶段：两级分区（大类 + 分区 + 老数据迁移） ----------------
        print("\n" + "=" * 60)
        print(" 两级分区测试（陪玩/代肝/其他服务 + 老库迁移零丢失）")
        print("=" * 60, flush=True)
        zcode = subprocess.call(
            [sys.executable, os.path.join(ROOT, "tests", "test_zones.py")], env=tenv)

        # ---------------- 第四阶段：客户档案 + 到期提醒 ----------------
        print("\n" + "=" * 60)
        print(" 客户档案测试（谁快到期了 + 续费怎么算 + 时区）")
        print("=" * 60, flush=True)
        ccode = subprocess.call(
            [sys.executable, os.path.join(ROOT, "tests", "test_customers.py")], env=tenv)

        # ---------------- 第五阶段：展示墙外链 + 分类可配置（第一刀） ----------------
        print("\n" + "=" * 60)
        print(" 展示墙测试（外链防 XSS + 分类增删改排序 + 老数据兼容）")
        print("=" * 60, flush=True)
        glcode = subprocess.call(
            [sys.executable, os.path.join(ROOT, "tests", "test_gallery_link.py")], env=tenv)

        # ---------------- 第六阶段：待确认 + 自动释放（第二刀） ----------------
        print("\n" + "=" * 60)
        print(" 待确认测试（确认 / 释放 / 自动释放的 5 种边界 + 老库迁移）")
        print("=" * 60, flush=True)
        cflcode = subprocess.call(
            [sys.executable, os.path.join(ROOT, "tests", "test_confirm.py")], env=tenv)

        # ---------------- 第七阶段：收入登记 + 月度统计（第三刀） ----------------
        print("\n" + "=" * 60)
        print(" 收入测试（三个记账入口 + 幂等 + 月度统计 + 备份恢复）")
        print("=" * 60, flush=True)
        incode = subprocess.call(
            [sys.executable, os.path.join(ROOT, "tests", "test_income.py")], env=tenv)

        # ---------------- 第八阶段：真的重启一次 ----------------
        print("\n" + "=" * 60)
        print(" 重启测试（模拟 Render 休眠 / 重新部署后的重启）")
        print("=" * 60, flush=True)
        jar, call = make_client()
        call("/api/auth", "POST", {"password": TEST_PASSWORD})
        pub = json.loads(call("/api/public")[1])
        target = next(s["start"] for s in pub["slots"] if s["status"] == "free")
        call("/api/admin/schedule", "POST", {
            "action": "save-lock", "start": target, "end": target + 3600 * 1000,
            "kind": "booking", "client": "重启测试老板", "note": "重启后应该还在"})
        cookie = next((c.value for c in jar if c.name == "peiwan_session"), "")
        check("重启前：已登录并锁定了一个时段", bool(cookie))

        stop_server(proc)
        print("  ...网站已关闭，正在用同一份数据重新启动...", flush=True)
        time.sleep(1)
        proc = start_server(env)
        if not wait_ready(BASE + "/api/public"):
            check("重启后网站能起来", False)
            return 1
        check("重启后网站能起来", True)

        status, _ = call("/api/admin")
        check("重启后不用重新登录（Cookie 自带签名，不依赖服务器内存）", status == 200, "status=%s" % status)
        adm = json.loads(call("/api/admin")[1])
        kept = [l for l in adm["schedule"]["locks"] if l.get("client") == "重启测试老板"]
        check("重启后预约记录还在（数据在磁盘上）", len(kept) == 1, "找到 %d 条" % len(kept))
        if kept:
            check("时段内容完全一致", kept[0]["start"] == target and kept[0]["note"] == "重启后应该还在")

        # ---------------- 第九阶段：并发读写 ----------------
        print("\n" + "=" * 60)
        print(" 并发测试（客户看时间表的同时，你在后台锁定/取消时段）")
        print("=" * 60, flush=True)
        cenv = dict(os.environ)
        cenv.update({"TEST_BASE": BASE, "TEST_PASSWORD": TEST_PASSWORD, "PYTHONUNBUFFERED": "1"})
        concode = subprocess.call(
            [sys.executable, os.path.join(ROOT, "tests", "test_concurrency.py")], env=cenv)
        check("并发下没有报错", concode == 0)

        print("\n本阶段：通过 %d 项，失败 %d 项" % (ok, fail))
        return (code or bcode or zcode or ccode or glcode or cflcode or incode or concode
                or (1 if fail else 0))
    finally:
        stop_server(proc)
        shutil.rmtree(TEST_DATA, ignore_errors=True)
        print("\n测试实例已关闭，测试数据已清理（你的 data/ 从头到尾没被碰过）")


if __name__ == "__main__":
    sys.exit(main())
