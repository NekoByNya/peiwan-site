# -*- coding: utf-8 -*-
"""
并发测试：模拟"客户在看时间表的同时，你在后台锁定/取消时段"。

为什么需要它：网站是多线程的，你和客户会同时读写同一个 SQLite 文件。
如果并发处理不好，会出现 500 报错、数据错乱、甚至卡住。

用法（需要网站已经在跑）：
    set TEST_BASE=http://127.0.0.1:8123
    set TEST_PASSWORD=你的密码
    python tests\\test_concurrency.py
"""

import http.cookiejar
import json
import os
import statistics
import sys
import threading
import time
import urllib.error
import urllib.request

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BASE = os.environ.get("TEST_BASE") or "http://127.0.0.1:8123"
PASSWORD = os.environ.get("TEST_PASSWORD") or "baixu123"
CLIENTS = 8          # 同时看时间表的"客户"数
ROUNDS = 6           # 每个客户重复多少轮

results = {"ok": 0, "http_err": 0, "net_err": 0, "errors": []}
lock = threading.Lock()


def note(kind, detail=""):
    with lock:
        results[kind] += 1
        if detail and len(results["errors"]) < 8:
            results["errors"].append(detail)


def timed_get(path, jar=None):
    # 注意：空 CookieJar 的 bool() 是 False（它实现了 __len__），
    # 所以这里必须写 `is not None`，否则第一次登录拿到的 Cookie 根本存不进去
    opener = (urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
              if jar is not None else urllib.request.build_opener())
    t0 = time.perf_counter()
    try:
        with opener.open(BASE + path, timeout=25) as r:
            r.read()
            ms = (time.perf_counter() - t0) * 1000
            if r.getcode() == 200:
                note("ok")
            else:
                note("http_err", "%s -> %s" % (path, r.getcode()))
            return ms
    except urllib.error.HTTPError as e:
        note("http_err", "%s -> %s %s" % (path, e.code, e.read()[:80]))
    except Exception as e:
        note("net_err", "%s -> %s: %s" % (path, type(e).__name__, e))
    return None


def call(path, method="GET", payload=None, jar=None):
    data = json.dumps(payload).encode() if payload is not None else None
    opener = (urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
              if jar is not None else urllib.request.build_opener())
    req = urllib.request.Request(BASE + path, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    try:
        with opener.open(req, timeout=25) as r:
            body = r.read().decode("utf-8", "replace")
            note("ok")
            return r.getcode(), body
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        note("http_err", "%s %s -> %s %s" % (method, path, e.code, body[:80]))
        return e.code, body
    except Exception as e:
        note("net_err", "%s %s -> %s: %s" % (method, path, type(e).__name__, e))
        return None, ""


def customer_worker(idx, latencies):
    """模拟客户：不断刷新时间表、核对空档"""
    for _ in range(ROUNDS):
        ms = timed_get("/api/public")
        if ms:
            latencies.append(ms)
        pub = None
        try:
            with urllib.request.urlopen(BASE + "/api/public", timeout=25) as r:
                pub = json.loads(r.read().decode("utf-8"))
        except Exception as e:
            note("net_err", "解析 /api/public 失败: %s" % e)
            continue
        free = [s["start"] for s in pub["slots"] if s["status"] == "free"][:3]
        if free:
            timed_get("/api/availability?hours=" + ",".join(str(x) for x in free))
        timed_get("/")


def admin_worker(latencies):
    """模拟你：一边刷新后台，一边锁定/取消时段"""
    jar = http.cookiejar.CookieJar()
    code, _ = call("/api/auth", "POST", {"password": PASSWORD}, jar)
    if code != 200:
        note("http_err", "后台登录失败：%s" % code)
        return
    for i in range(ROUNDS * 2):
        ms = None
        t0 = time.perf_counter()
        call("/api/admin", jar=jar)
        ms = (time.perf_counter() - t0) * 1000
        latencies.append(ms)
        _, body = call("/api/public")
        try:
            slots = json.loads(body)["slots"]
        except Exception:
            continue
        free = [s["start"] for s in slots if s["status"] == "free"]
        if not free:
            continue
        target = free[i % len(free)]
        _, saved = call("/api/admin/schedule", "POST", {
            "action": "save-lock", "start": target, "end": target + 3600 * 1000,
            "kind": "booking", "client": "并发测试", "note": "第 %d 轮" % i}, jar)
        try:
            lid = json.loads(saved)["schedule"]["locks"][-1]["id"]
        except Exception:
            continue
        call("/api/admin/schedule", "POST", {"action": "remove-lock", "id": lid}, jar)


def main():
    print("并发测试：%d 个客户端 + 1 个后台，同时读写 %s" % (CLIENTS, BASE))
    latencies = []
    threads = [threading.Thread(target=customer_worker, args=(i, latencies)) for i in range(CLIENTS)]
    threads.append(threading.Thread(target=admin_worker, args=(latencies,)))
    t0 = time.perf_counter()
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    spent = time.perf_counter() - t0

    print("\n总耗时 %.1f 秒 | 成功请求 %d | HTTP 错误 %d | 网络错误 %d"
          % (spent, results["ok"], results["http_err"], results["net_err"]))
    if latencies:
        latencies.sort()
        print("请求耗时：平均 %.0f ms | 中位 %.0f ms | 最慢 %.0f ms"
              % (statistics.mean(latencies), statistics.median(latencies), latencies[-1]))
    if results["errors"]:
        print("\n错误样例：")
        for e in results["errors"]:
            print("  -", e)

    failed = results["http_err"] + results["net_err"]
    print("\n结论：", "并发下没有出现任何错误 [OK]" if failed == 0 else "有 %d 个请求失败 [FAIL]" % failed)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
