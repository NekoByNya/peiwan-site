# -*- coding: utf-8 -*-
"""
线上体检：**只读检查**，不修改任何数据。部署完之后跑一次，确认网站真的健康。

用法：
    python tests\\check_deployed.py https://你的服务名.onrender.com
    python tests\\check_deployed.py                     （默认检查本机 8123）

它会逐项检查：页面、接口、分享卡片、静态资源、保活接口、响应速度，
最后给一个总结。全程只发 GET 请求，绝不写数据。
"""

import json
import os
import re
import statistics
import sys
import time
import urllib.error
import urllib.request

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BASE = (sys.argv[1] if len(sys.argv) > 1 else os.environ.get("TEST_BASE")
        or "http://127.0.0.1:8123").rstrip("/")
IS_LOCAL = re.match(r"^https?://(127\.0\.0\.1|localhost)", BASE) is not None

ok = warn = fail = 0
notes = []


def report(state, name, extra=""):
    global ok, warn, fail
    if state == "ok":
        ok += 1
        print("  [OK]   %s %s" % (name, extra))
    elif state == "warn":
        warn += 1
        print("  [注意] %s %s" % (name, extra))
    else:
        fail += 1
        print("  [FAIL] %s %s" % (name, extra))


def fetch(path, method="GET", timeout=30):
    url = BASE + path
    req = urllib.request.Request(url, method=method)
    req.add_header("User-Agent", "peiwan-health-check/1.0")
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read()
            ms = (time.perf_counter() - t0) * 1000
            return r.getcode(), body, dict(r.headers), ms, None
    except urllib.error.HTTPError as e:
        return e.code, e.read(), dict(e.headers), (time.perf_counter() - t0) * 1000, None
    except Exception as e:
        return None, b"", {}, (time.perf_counter() - t0) * 1000, "%s: %s" % (type(e).__name__, e)


def meta(html, prop):
    hit = re.search('<meta property="%s" content="([^"]*)"' % prop, html)
    return hit.group(1) if hit else None


def main():
    print("=" * 64)
    print(" 线上体检：%s" % BASE)
    print(" 检查方式：全部只读（GET），不会修改你的任何数据")
    print("=" * 64, flush=True)

    # ---------- 首页 ----------
    print("\n【1】页面能不能打开")
    code, body, headers, ms, err = fetch("/")
    html = body.decode("utf-8", "replace")
    if err:
        report("fail", "首页打不开", err)
        print("\n网站根本没起来，后面的检查跳过。先去看 Render 的 Logs。")
        return 1
    report("ok" if code == 200 else "fail", "首页返回 %s" % code, "耗时 %.0f ms" % ms)
    if ms > 3000:
        report("warn", "首次响应偏慢（%.1f 秒）" % (ms / 1000),
               "免费实例可能刚被叫醒；如果经常这样，检查保活定时器是否生效")
    report("ok" if "heroGrid" in html else "fail",
           "页面结构完整（有首屏区域）" if "heroGrid" in html else "页面结构不对，可能没传全文件")
    report("ok" if "{{" not in html else "fail",
           "模板占位符已全部替换" if "{{" not in html else "页面上还残留 {{占位符}}，说明 app.py 没跑对")

    # ---------- 分享卡片 ----------
    print("\n【2】微信分享卡片")
    og_title = meta(html, "og:title")
    og_desc = meta(html, "og:description")
    og_image = meta(html, "og:image")
    og_url = meta(html, "og:url")
    report("ok" if og_title else "fail", "分享标题", og_title or "缺失")
    report("ok" if og_desc else "fail", "分享简介", (og_desc or "缺失")[:40])
    if not og_image:
        report("fail", "分享缩略图", "缺失（微信里分享出去会没有小图）")
    elif not og_image.startswith("http"):
        report("fail", "分享缩略图必须是完整网址", og_image)
    else:
        report("ok", "分享缩略图", og_image)
        host = re.sub(r"^https?://", "", BASE)
        if host.split("/")[0] not in og_image:
            report("warn", "缩略图域名和当前访问域名不一致",
                   "如果绑定了自定义域名，属正常；否则说明反代头没传到")
        icode, ibody, _, _, _ = fetch(og_image.replace(BASE, "") if og_image.startswith(BASE) else og_image)
        report("ok" if icode == 200 else "fail",
               "缩略图能下载", "%.0f KB" % (len(ibody) / 1024) if icode == 200 else "HTTP %s" % icode)
    report("ok" if og_url else "warn", "分享链接 og:url", og_url or "缺失")

    # ---------- 接口 ----------
    print("\n【3】公开接口")
    code, body, _, ms, err = fetch("/api/public")
    try:
        pub = json.loads(body)
    except Exception:
        pub = None
    if code != 200 or not pub:
        report("fail", "/api/public 异常", err or "HTTP %s" % code)
    else:
        slots = pub.get("slots") or []
        report("ok" if len(slots) == 168 else "fail", "时间表格子数 = %d" % len(slots),
               "（应为 168 = 7 天 × 24 小时）")
        statuses = sorted({s.get("status") for s in slots})
        known = {"free", "booking", "rest", "past", "closed"}
        report("ok" if set(statuses) <= known else "fail", "格子状态取值合法", str(statuses))
        free = [s for s in slots if s.get("status") == "free"]
        report("ok" if free else "warn", "当前可预约时段 %d 个" % len(free),
               "" if free else "一个空闲都没有：要么都锁满了，要么休息规则写成了全天")
        prof = pub.get("profile") or {}
        report("ok" if prof.get("name") else "fail", "资料已配置", "名字：%s" % prof.get("name"))
        report("ok" if prof.get("wechat") else "warn", "微信号",
               prof.get("wechat") or "没填的话客户联系不到你")
        if free:
            target = free[0]["start"]
            c2, b2, _, _, _ = fetch("/api/availability?hours=%d" % target)
            try:
                st = json.loads(b2)["slots"][0]["status"]
            except Exception:
                st = "解析失败"
            report("ok" if st == "free" else "fail", "空档核对接口", "返回 %s" % st)
        c3, _, _, _, _ = fetch("/api/availability")
        report("ok" if c3 == 400 else "warn", "空档接口对空参数返回 400", "实际 %s" % c3)

    # ---------- 权限 ----------
    print("\n【4】后台有没有被保护")
    code, _, _, _, _ = fetch("/api/admin")
    report("ok" if code == 401 else "fail", "未登录访问 /api/admin",
           "返回 %s（应为 401）" % code)
    code, body, _, _, _ = fetch("/nya-0508")
    report("ok" if code == 200 and "管理终端" in body.decode("utf-8", "replace") else "fail",
           "后台登录页能打开", "返回 %s" % code)
    code, body, _, _, _ = fetch("/api/auth")
    try:
        authed = json.loads(body).get("authenticated")
    except Exception:
        authed = None
    report("ok" if authed is False else "warn", "当前未登录状态正确", str(authed))

    # ---------- 静态资源 ----------
    print("\n【5】静态资源")
    for path, need in [("/static/style.css", "text/css"), ("/static/site.js", "javascript"),
                       ("/static/admin.js", "javascript"), ("/static/img/default-bg.jpg", "image"),
                       ("/static/img/share-default.jpg", "image")]:
        code, body, headers, _, err = fetch(path)
        ctype = headers.get("Content-Type", "")
        state = "ok" if code == 200 and need in ctype else "fail"
        report(state, path, "%s %s %.0fKB" % (code, ctype.split(";")[0], len(body) / 1024))

    # ---------- 保活 ----------
    print("\n【6】保活接口（给定时访问服务用）")
    code, body, _, ms, _ = fetch("/api/ping")
    try:
        pong = json.loads(body).get("ok")
    except Exception:
        pong = None
    report("ok" if code == 200 and pong is True else "fail", "/api/ping", "返回 %s，%d 字节" % (code, len(body)))
    if len(body) < 500:
        report("ok", "响应很小，适合高频保活", "%d 字节" % len(body))
    else:
        report("warn", "响应偏大", "%d 字节" % len(body))

    # ---------- 响应速度 ----------
    print("\n【7】响应速度（免费实例只有 0.1 CPU，看个大概）")
    samples = []
    for _ in range(5):
        _, _, _, ms, _ = fetch("/api/public")
        samples.append(ms)
    avg, worst = statistics.mean(samples), max(samples)
    report("ok" if avg < 1500 else "warn", "接口平均耗时 %.0f ms" % avg, "最慢 %.0f ms" % worst)

    # ---------- 协议 ----------
    print("\n【8】安全与协议")
    if BASE.startswith("https://"):
        report("ok", "使用 HTTPS（微信里打开不会报不安全）")
    else:
        report("warn", "当前不是 HTTPS", "本地调试正常；部署后应该是 https://")
        if not IS_LOCAL:
            report("warn", "线上建议用 https 地址重新访问")

    # ---------- 总结 ----------
    print("\n" + "=" * 64)
    print(" 通过 %d 项 | 注意 %d 项 | 失败 %d 项" % (ok, warn, fail))
    if fail == 0 and warn == 0:
        print(" 网站完全健康，可以放心把链接发给客户了。")
    elif fail == 0:
        print(" 没有致命问题，上面标了「注意」的地方有空再看。")
    else:
        print(" 有失败项，按上面的提示逐个处理；需要的话把这段输出发给我。")
    print("=" * 64)
    return 1 if fail else 0


if __name__ == "__main__":
    sys.exit(main())
