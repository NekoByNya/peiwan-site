# -*- coding: utf-8 -*-
"""
自助预约 + 预约通知 + 老数据库迁移的自动测试。

测的是这三件新功能：
  1. POST /api/book：客户在前台点「立即锁定时间」→ 时间立刻被锁定
     （参数校验、价格计算、分段合并、被抢单 409、限流 429、后台能看到来源/模式/价格/微信/下单时间）
  2. 通知设置：保存渠道 + 发测试通知（用一个临时起的小 HTTP 服务器收通知，**不依赖外网**）
  3. 老数据库迁移：造一个只有旧字段的 locks 表，确认 init_db() 能补上字段且老数据还在

风格和 test_api.py 一样：纯标准库、打印 [OK]/[FAIL]、最后给通过/失败计数，失败则退出码非 0。
⚠️ 和 test_api.py 一样**会写数据**，只能对着测试实例跑。

用法：
    python tests\\test_booking.py                 （默认 http://127.0.0.1:8123）
    set TEST_BASE=http://127.0.0.1:8199 && python tests\\test_booking.py
"""

import gc
import http.cookiejar
import http.server
import json
import os
import re
import shutil
import sqlite3
import sys
import threading
import time
import urllib.error
import urllib.request

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASE = os.environ.get("TEST_BASE") or "http://127.0.0.1:8123"
PASSWORD = os.environ.get("TEST_PASSWORD") or "baixu123"
HOUR = 3600 * 1000

# 这个脚本会改数据，别对着线上站点跑（和 test_api.py 一样的保护）
IS_LOCAL = re.match(r"^https?://(127\.0\.0\.1|localhost)(:\d+)?$", BASE) is not None
if not IS_LOCAL and os.environ.get("ALLOW_REMOTE_TESTS") != "1":
    print("=" * 70)
    print(" 已阻止：这是一个会修改数据的测试脚本，不能对着线上站点运行。")
    print(" 你指定的地址是：%s" % BASE)
    print("=" * 70)
    sys.exit(2)

jar = http.cookiejar.CookieJar()
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
ok = fail = 0


def check(name, cond, extra=""):
    global ok, fail
    if cond:
        ok += 1
        print("  [OK]   %s %s" % (name, extra))
    else:
        fail += 1
        print("  [FAIL] %s %s" % (name, extra))


def call(path, method="GET", payload=None, ip=None):
    """ip 参数会塞进 X-Forwarded-For —— 用来模拟"不同客户来自不同 IP"（限流是按 IP 算的）"""
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if ip:
        req.add_header("X-Forwarded-For", ip)
    try:
        with opener.open(req, timeout=25) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


def free_run(count):
    """在公开时间表里找 count 个**连续**的空闲小时（拿不到就返回 None）"""
    pub = json.loads(call("/api/public")[1])
    run = []
    for slot in pub["slots"]:
        if slot["status"] == "free":
            run.append(slot["start"])
            if len(run) == count:
                return run
        else:
            run = []
    return None


def status_map():
    pub = json.loads(call("/api/public")[1])
    return {s["start"]: s["status"] for s in pub["slots"]}


# ============================================================================
#  收通知用的小服务器（只监听本机，测完就关，不碰外网）
# ============================================================================

class Hook(http.server.BaseHTTPRequestHandler):
    received = []

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length).decode("utf-8", "replace")
        Hook.received.append({"path": self.path, "body": raw,
                              "content_type": self.headers.get("Content-Type") or ""})
        if self.path.startswith("/fail"):
            body, code = b'{"ok":false}', 500
        else:
            body, code = b'{"code":200}', 200
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass          # 别把测试输出弄乱


def start_hook():
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Hook)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, server.server_address[1]


def wait_hook(count, timeout=8.0):
    """等后台线程把通知发出来（客户不用等，但测试要等它一下）"""
    end = time.time() + timeout
    while time.time() < end:
        if len(Hook.received) >= count:
            return True
        time.sleep(0.05)
    return len(Hook.received) >= count


# ============================================================================
#  第一部分：接口测试
# ============================================================================

def main_tests():
    # ---------- 准备：登录 + 关掉通知，避免测试期间往外发消息 ----------
    print("=== 0. 准备 ===")
    s, b = call("/api/auth", "POST", {"password": PASSWORD})
    check("管理员登录", s == 200, b.strip()[:80])
    pub = json.loads(call("/api/public")[1])
    prices = pub["profile"]["prices"]
    check("资料里有报价档位（预约要用）", len(prices) >= 1, "共 %d 档" % len(prices))
    mode = prices[0]
    unit = int(mode["price"])
    call("/api/admin/notify", "POST", {"channel": "off", "target": ""})

    print("=== 1. 正常预约：点了就锁定 ===")
    hours = free_run(3)
    check("能找到 3 个连续的空闲小时", bool(hours), str(hours))
    s, b = call("/api/book", "POST",
                {"hours": hours, "mode": mode["name"], "contact": "wx_auto_001",
                 "contactType": "wechat", "note": "自动测试用"},
                ip="198.51.100.11")
    data = json.loads(b) if s == 200 else {}
    check("POST /api/book 预约成功", s == 200 and data.get("ok") is True, b.strip()[:140])
    check("3 个连续小时合并成 1 段",
          len(data.get("locks", [])) == 1 and data["locks"][0]["hours"] == 3, str(data.get("locks")))
    check("返回的价格 = 3 × 单价（%d 元）" % unit,
          data.get("totalPrice") == 3 * unit and data["locks"][0]["price"] == 3 * unit,
          "totalPrice=%s" % data.get("totalPrice"))
    check("返回总小时数", data.get("totalHours") == 3, str(data.get("totalHours")))
    check("返回模式名", data.get("mode") == mode["name"], str(data.get("mode")))
    check("返回联系方式渠道 contactType", data.get("contactType") == "wechat",
          str(data.get("contactType")))
    msg = data.get("message") or ""
    check("返回给客户看的微信文案（含模式 / 北京时间 / 费用）",
          mode["name"] in msg and "北京时间" in msg and ("¥%d" % (3 * unit)) in msg, msg.replace("\n", " / ")[:90])

    st = status_map()
    check("公开时间表里这 3 格变成 booking",
          all(st.get(h) == "booking" for h in hours), str([st.get(h) for h in hours]))

    # 不连续的两个小时 → 应该拆成 2 段
    gap = free_run(3)
    s, b = call("/api/book", "POST",
                {"hours": [gap[0], gap[2]], "mode": mode["name"], "contact": "wx_auto_002"},
                ip="198.51.100.12")
    data2 = json.loads(b) if s == 200 else {}
    check("不连续的两小时拆成 2 段",
          s == 200 and len(data2.get("locks", [])) == 2
          and all(lk["hours"] == 1 for lk in data2["locks"]), str(data2.get("locks")))
    check("拆成 2 段后总价仍然正确", data2.get("totalPrice") == 2 * unit, str(data2.get("totalPrice")))
    check("没传 contactType 时按微信处理（兼容老前端）",
          data2.get("contactType") == "wechat", str(data2.get("contactType")))

    print("=== 1b. 联系方式渠道：微信 / 闲鱼 / QQ ===")
    for i, (ctype, cname) in enumerate((("xianyu", "闲鱼"), ("qq", "QQ"))):
        hour = free_run(1)
        s, b = call("/api/book", "POST",
                    {"hours": hour, "mode": mode["name"], "contact": "%s_auto_%d" % (ctype, i),
                     "contactType": ctype}, ip="198.51.100.4%d" % i)
        d = json.loads(b) if s == 200 else {}
        check("contactType=%s 可以通过" % ctype, s == 200 and d.get("contactType") == ctype,
              "status=%s" % s)
        check("文案里带上渠道中文名「%s」" % cname, cname in (d.get("message") or ""),
              (d.get("message") or "").replace("\n", " / ")[:90])
    s, b = call("/api/book", "POST",
                {"hours": free_run(1), "mode": mode["name"], "contact": "wx_bad_type",
                 "contactType": "telegram"}, ip="198.51.100.49")
    check("contactType 非法值 → 400", s == 400 and "微信" in b, b.strip()[:110])

    print("=== 1c. 游戏名：一套代码能开任何游戏的预约站 ===")
    # 备份原资料，测完还原（不然会影响后面的测试）
    admin = json.loads(call("/api/admin")[1])
    original = dict(admin["profile"])
    cur_rev = [admin["profileRevision"]]

    def save_profile(**changes):
        prof = dict(original)
        prof.update(changes)
        s, b = call("/api/admin/profile", "PUT", {"profile": prof, "revision": cur_rev[0]})
        if s == 200:
            cur_rev[0] = json.loads(b).get("revision", cur_rev[0] + 1) \
                if isinstance(json.loads(b), dict) else cur_rev[0] + 1
        return s

    def book_and_get_message(ip):
        s, b = call("/api/book", "POST",
                    {"hours": free_run(1), "mode": mode["name"], "contact": "wx_game_test",
                     "contactType": "wechat"}, ip=ip)
        return s, (json.loads(b).get("message") or "") if s == 200 else ""

    s = save_profile(game="永劫无间", platform="PC 电脑端")
    check("后台能保存「游戏名」字段", s == 200, "status=%s" % s)
    s, msg = book_and_get_message("198.51.100.61")
    check("预约文案里出现游戏名「永劫无间」", s == 200 and "想约永劫无间 PC 电脑端陪玩" in msg,
          msg.replace("\n", " / ")[:90])

    save_profile(game="", platform="PC 电脑端")
    s, msg = book_and_get_message("198.51.100.62")
    check("游戏名留空时只说平台，不会出现「想约 陪玩」",
          s == 200 and "想约PC 电脑端陪玩" in msg and "想约 " not in msg,
          msg.replace("\n", " / ")[:90])

    save_profile(game="", platform="")
    s, msg = book_and_get_message("198.51.100.63")
    check("游戏名和平台都留空 → 用兜底话术",
          s == 200 and "我想约你的陪玩服务" in msg, msg.replace("\n", " / ")[:90])

    save_profile(**{k: original.get(k, "") for k in ("game", "platform")})
    check("测试后资料已还原", json.loads(call("/api/admin")[1])["profile"].get("game") == original.get("game"),
          "game=%r" % json.loads(call("/api/admin")[1])["profile"].get("game"))

    print("=== 2. 参数校验（每条都要有清楚的中文错误） ===")
    s, b = call("/api/book", "POST",
                {"hours": free_run(1), "mode": mode["name"], "contact": ""}, ip="198.51.100.21")
    check("没填联系方式 → 400", s == 400 and "联系方式" in b and "微信" in b, b.strip()[:110])
    s, b = call("/api/book", "POST",
                {"hours": free_run(1), "mode": mode["name"], "contact": "x"}, ip="198.51.100.22")
    check("联系方式太短 → 400", s == 400, b.strip()[:100])
    s, b = call("/api/book", "POST",
                {"hours": [], "mode": mode["name"], "contact": "wx_auto"}, ip="198.51.100.23")
    check("小时数为空 → 400", s == 400 and "时间" in b, b.strip()[:100])
    s, b = call("/api/book", "POST",
                {"mode": mode["name"], "contact": "wx_auto"}, ip="198.51.100.24")
    check("完全不传 hours → 400", s == 400, b.strip()[:100])
    s, b = call("/api/book", "POST",
                {"hours": ["坏数据"], "mode": mode["name"], "contact": "wx_auto"}, ip="198.51.100.25")
    check("hours 不是数字 → 400", s == 400, b.strip()[:100])
    s, b = call("/api/book", "POST",
                {"hours": [1, 2], "mode": mode["name"], "contact": "wx_auto"}, ip="198.51.100.26")
    check("时间戳不是整点/不在时间表里 → 400", s == 400, b.strip()[:100])
    seven = free_run(7)                    # 7 个真格子，超过 MAX_BOOK_HOURS=6
    s, b = call("/api/book", "POST",
                {"hours": seven, "mode": mode["name"], "contact": "wx_auto"}, ip="198.51.100.27")
    check("超过 6 小时 → 400", s == 400 and "6" in b, b.strip()[:110])
    s, b = call("/api/book", "POST",
                {"hours": free_run(1), "mode": "不存在的模式", "contact": "wx_auto"},
                ip="198.51.100.28")
    check("模式不存在 → 400", s == 400 and "模式" in b, b.strip()[:110])

    exactly6 = free_run(6)
    s, b = call("/api/book", "POST",
                {"hours": exactly6, "mode": mode["name"], "contact": "wx_auto_6hours"},
                ip="198.51.100.29")
    check("正好 6 小时可以通过（边界值）", s == 200 and json.loads(b).get("totalHours") == 6,
          "status=%s" % s)

    print("=== 3. 抢单冲突 → 409 ===")
    pair = free_run(2)
    call("/api/admin/schedule", "POST",
         {"action": "save-lock", "start": pair[0], "end": pair[0] + HOUR,
          "kind": "booking", "client": "老板先锁的", "note": "冲突测试"})
    s, b = call("/api/book", "POST",
                {"hours": pair, "mode": mode["name"], "contact": "wx_conflict"},
                ip="198.51.100.31")
    body = json.loads(b) if b.strip().startswith("{") else {}
    check("已经被锁的小时 → 409", s == 409, "status=%s %s" % (s, b.strip()[:90]))
    check("409 里说清楚是哪几个小时不可约", body.get("conflicts") == [pair[0]],
          str(body.get("conflicts")))
    check("409 的错误信息是中文且提到时间", "预约" in str(body.get("error")), str(body.get("error"))[:90])
    check("冲突时一点都不锁（没有半途锁一半）",
          status_map().get(pair[1]) == "free", str(status_map().get(pair[1])))

    print("=== 4. 限流：同一 IP 10 分钟最多 5 次 ===")
    codes = []
    for i in range(6):
        hour = free_run(1)
        s, _b = call("/api/book", "POST",
                     {"hours": hour, "mode": mode["name"], "contact": "wx_rate_%d" % i},
                     ip="203.0.113.77")
        codes.append(s)
    check("前 5 次正常（200）", codes[:5] == [200] * 5, str(codes))
    check("第 6 次被限流（429）", codes[5] == 429, str(codes))
    s, _b = call("/api/book", "POST",
                 {"hours": free_run(1), "mode": mode["name"], "contact": "wx_other"},
                 ip="203.0.113.78")
    check("换个 IP 不受影响（限流是按客户算的）", s == 200, "status=%s" % s)

    print("=== 5. 后台能看到来源 / 模式 / 价格 / 联系方式 / 下单时间 ===")
    adm = json.loads(call("/api/admin")[1])
    locks = adm["schedule"]["locks"]
    mine = [l for l in locks if l.get("contact") == "wx_auto_001"]
    check("后台能看到这笔预约", len(mine) == 1, "找到 %d 条" % len(mine))
    if mine:
        lk = mine[0]
        check("来源标记 source=client（客户自助预约）", lk.get("source") == "client", str(lk.get("source")))
        check("模式名记下来了", lk.get("mode") == mode["name"], str(lk.get("mode")))
        check("价格记下来了（%d 元）" % (3 * unit), lk.get("price") == 3 * unit, str(lk.get("price")))
        check("客户联系方式记下来了", lk.get("contact") == "wx_auto_001", str(lk.get("contact")))
        check("联系方式渠道记下来了（contactType=wechat）",
              lk.get("contact_type") == "wechat", str(lk.get("contact_type")))
        check("老板称呼也填成联系方式（后台列表直接能看）", lk.get("client") == "wx_auto_001")
        check("下单时间记下来了（当前毫秒时间戳）",
              lk.get("created_at", 0) > (time.time() - 3600) * 1000, str(lk.get("created_at")))
        check("kind 还是 booking（公开时间表显示「已预约」）", lk.get("kind") == "booking")
    xianyu = [l for l in locks if l.get("contact", "").startswith("xianyu_auto")]
    check("闲鱼渠道的预约也记下了渠道",
          bool(xianyu) and xianyu[0].get("contact_type") == "xianyu",
          str(xianyu[0].get("contact_type") if xianyu else "没找到"))
    own = [l for l in locks if l.get("client") == "老板先锁的"]
    check("老板自己锁的记录 source=owner", bool(own) and own[0].get("source") == "owner",
          str(own[0].get("source") if own else "没找到"))

    print("=== 6. 老板取消 → 该格恢复 free ===")
    if mine:
        s, _b = call("/api/admin/schedule", "POST", {"action": "remove-lock", "id": mine[0]["id"]})
        st = status_map()
        check("取消后这 3 格恢复 free",
              s == 200 and all(st.get(h) == "free" for h in hours),
              str([st.get(h) for h in hours]))

    print("=== 7. 预约通知（用本机的小服务器收，不依赖外网） ===")
    server, port = start_hook()
    try:
        hook_url = "http://127.0.0.1:%d/hook" % port
        s, b = call("/api/admin/notify", "POST", {"channel": "webhook", "target": hook_url})
        check("保存通知设置", s == 200 and json.loads(b)["notifyChannel"] == "webhook", b.strip()[:100])
        s, b = call("/api/admin/notify", "POST", {"channel": "webhook", "target": ""})
        check("Webhook 不填网址 → 400", s == 400, b.strip()[:100])
        s, b = call("/api/admin/notify", "POST", {"channel": "webhook", "target": "随便写点啥"})
        check("Webhook 网址不合法 → 400", s == 400, b.strip()[:100])
        s, b = call("/api/admin/notify", "POST", {"channel": "没这个渠道", "target": "x"})
        check("不认识的渠道 → 400", s == 400, b.strip()[:100])

        adm = json.loads(call("/api/admin")[1])
        check("GET /api/admin 返回通知配置",
              adm.get("notifyChannel") == "webhook" and adm.get("notifyTarget") == hook_url
              and "notifyLast" in adm, str(adm.get("notifyChannel")))

        Hook.received.clear()
        s, b = call("/api/admin/notify-test", "POST", {})
        data = json.loads(b)
        check("发送测试通知成功", s == 200 and data.get("ok") is True, b.strip()[:120])
        check("小服务器真的收到了请求", len(Hook.received) == 1 and Hook.received[0]["path"] == "/hook",
              str([r["path"] for r in Hook.received]))
        payload = json.loads(Hook.received[0]["body"]) if Hook.received else {}
        check("Webhook 收到 title / content / text 三个字段",
              {"title", "content", "text"} <= set(payload), str(sorted(payload)))
        adm = json.loads(call("/api/admin")[1])
        check("notifyLast 记下了成功结果",
              adm.get("notifyLast", {}).get("ok") is True, str(adm.get("notifyLast")))

        # 失败路径：让接收方回 500
        call("/api/admin/notify", "POST", {"channel": "webhook", "target": "http://127.0.0.1:%d/fail" % port})
        s, b = call("/api/admin/notify-test", "POST", {})
        data = json.loads(b)
        check("对方回 500 时如实报告失败原因",
              s == 200 and data.get("ok") is False and "500" in str(data.get("detail")), b.strip()[:140])
        adm = json.loads(call("/api/admin")[1])
        check("notifyLast 记下了失败原因",
              adm.get("notifyLast", {}).get("ok") is False
              and "500" in str(adm.get("notifyLast", {}).get("detail")), str(adm.get("notifyLast")))

        # 客户预约 → 自动推送
        call("/api/admin/notify", "POST", {"channel": "webhook", "target": hook_url})
        Hook.received.clear()
        one = free_run(1)                  # 注意：hours 必须是"列表"，直接用这个返回值
        s, _b = call("/api/book", "POST",
                     {"hours": one, "mode": mode["name"], "contact": "xy_notice_9",
                      "contactType": "xianyu", "note": "通知测试"},
                     ip="198.51.100.91")
        check("预约本身成功（没有因为发通知而变慢/失败）", s == 200, "status=%s" % s)
        check("客户预约后老板收到通知", wait_hook(1), "收到 %d 条" % len(Hook.received))
        notice = json.loads(Hook.received[0]["body"]) if Hook.received else {}
        text = str(notice.get("content") or "")
        check("通知标题带渠道，一眼看到去哪联系人",
              str(notice.get("title")) == "新的陪玩预约（闲鱼）", str(notice.get("title")))
        check("通知内容含模式 / 时间 / 小时数 / 总价 / 联系方式（带渠道）/ 备注",
              mode["name"] in text and "共 1 小时" in text and ("¥%d" % unit) in text
              and "联系方式：闲鱼 xy_notice_9" in text and "通知测试" in text,
              text.replace("\n", " / ")[:120])

        s, b = call("/api/admin/notify", "POST", {"channel": "off", "target": ""})
        check("把通知关回去", s == 200 and json.loads(b)["notifyChannel"] == "off", b.strip()[:100])
        s, b = call("/api/admin/notify-test", "POST", {})
        check("关闭状态下点测试会明确提示", json.loads(b).get("ok") is False, b.strip()[:120])
    finally:
        server.shutdown()
        server.server_close()

    print("=== 8. 备份要带上新字段（不然恢复后来源/价格就丢了） ===")
    backup = json.loads(call("/api/admin/export")[1])
    booked = [l for l in backup["locks"] if l.get("contact") == "xy_notice_9"]
    check("导出的备份里有 source / mode / price / contact / contact_type / created_at",
          bool(booked) and {"source", "mode", "price", "contact", "contact_type",
                            "created_at"} <= set(booked[0]),
          str(sorted(booked[0]) if booked else "没找到"))
    s, b = call("/api/admin/import", "POST", {"backup": backup})
    restored = [l for l in json.loads(call("/api/admin")[1])["schedule"]["locks"]
                if l.get("contact") == "xy_notice_9"]
    check("从备份恢复后，来源 / 价格 / 联系方式渠道还在",
          s == 200 and bool(restored) and restored[0].get("source") == "client"
          and restored[0].get("price") == unit and restored[0].get("mode") == mode["name"]
          and restored[0].get("contact_type") == "xianyu",
          str(restored[0] if restored else {})[:140])
    st = status_map()
    check("恢复后这几格仍然是「已预约」", st.get(one[0]) == "booking", str(st.get(one[0])))


# ============================================================================
#  第二部分：老数据库迁移（在测试进程里直接调 app.init_db()，用独立的数据目录）
# ============================================================================

OLD_SCHEMA = """
CREATE TABLE store (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE locks (
    id     TEXT PRIMARY KEY,
    kind   TEXT NOT NULL,
    start  INTEGER NOT NULL,
    end    INTEGER NOT NULL,
    client TEXT DEFAULT '',
    note   TEXT DEFAULT ''
);
CREATE TABLE rules (
    id TEXT PRIMARY KEY, days TEXT NOT NULL, start_hour INTEGER NOT NULL,
    end_hour INTEGER NOT NULL, from_ts INTEGER NOT NULL, until_ts INTEGER,
    note TEXT DEFAULT '', enabled INTEGER DEFAULT 1
);
CREATE TABLE gallery (
    id TEXT PRIMARY KEY, title TEXT DEFAULT '', caption TEXT DEFAULT '',
    category TEXT DEFAULT 'screenshot', image TEXT DEFAULT '', visible INTEGER DEFAULT 0,
    archived INTEGER DEFAULT 0, created_at INTEGER NOT NULL
);
"""


def migration_test():
    print("=== 9. 老数据库迁移（老预约记录一条都不能丢） ===")
    tmp = os.path.join(ROOT, ".testdata-migrate")
    shutil.rmtree(tmp, ignore_errors=True)
    os.makedirs(tmp)
    db = os.path.join(tmp, "site.db")
    try:
        # 造一个"升级前"的数据库：locks 表只有老字段，里面还锁着一个真实时段
        conn = sqlite3.connect(db)
        conn.executescript(OLD_SCHEMA)
        conn.execute("INSERT INTO store(key, value) VALUES('profile', ?)",
                     (json.dumps({"name": "升级前的老板"}, ensure_ascii=False),))
        conn.execute("INSERT INTO locks(id, kind, start, end, client, note) "
                     "VALUES('old-lock-1','booking',1500000000000,1500003600000,'老客户','升级前锁的')")
        conn.execute("INSERT INTO locks(id, kind, start, end, client, note) "
                     "VALUES('old-lock-2','rest',1500003600000,1500007200000,'','老休息')")
        conn.commit()
        conn.close()

        # 让 app 指向这个临时数据目录，再跑一次 init_db（升级时真正发生的事）
        os.environ["DATA_DIR"] = tmp
        if ROOT not in sys.path:
            sys.path.insert(0, ROOT)
        import app                                    # noqa: E402  （放在这里是为了先设好 DATA_DIR）

        app.init_db()
        cols = [r[1] for r in sqlite3.connect(db).execute("PRAGMA table_info(locks)")]
        check("init_db() 给老库补上了 6 个新字段",
              {"source", "mode", "price", "contact", "contact_type", "created_at"} <= set(cols),
              str(cols))

        rows = {r["id"]: r for r in app.list_locks()}
        check("老预约记录一条都没丢", len(rows) == 2, "剩 %d 条" % len(rows))
        check("老记录的内容完全没变",
              rows.get("old-lock-1", {}).get("start") == 1500000000000
              and rows.get("old-lock-1", {}).get("client") == "老客户"
              and rows.get("old-lock-2", {}).get("kind") == "rest",
              str(rows.get("old-lock-1")))
        check("老记录的 source 自动补成 owner（老板自己锁的）",
              rows.get("old-lock-1", {}).get("source") == "owner", str(rows.get("old-lock-1", {}).get("source")))
        check("老记录的新字段有合理默认值（没有 None）",
              rows["old-lock-1"]["price"] == 0 and rows["old-lock-1"]["mode"] == ""
              and rows["old-lock-1"]["contact"] == ""
              and rows["old-lock-1"]["contact_type"] == "wechat", str(rows["old-lock-1"]))

        # 老资料还在吗
        check("老库里的资料没被覆盖", app.get_profile().get("name") == "升级前的老板",
              str(app.get_profile().get("name")))

        # 幂等：再跑一次不能报错，也不能把字段加两遍
        app.init_db()
        cols2 = [r[1] for r in sqlite3.connect(db).execute("PRAGMA table_info(locks)")]
        check("再跑一次 init_db() 依然幂等（字段不重复、数据不少）",
              cols2 == cols and len(app.list_locks()) == 2,
              "%d 个字段 / %d 条记录" % (len(cols2), len(app.list_locks())))

        # 迁移后新写入的记录（客户自助预约）字段是齐的
        # 注意：用完要 close()，不然 Windows 上这个 .db 文件一直被占着，临时目录删不掉
        conn2 = app.connect()
        try:
            with app._db_lock:
                app.insert_lock(conn2, id="new-1", kind="booking", start=1600000000000,
                                end=1600003600000, client="新客户", note="", source="client",
                                mode="机密行动", price=20, contact="new_wx",
                                contact_type="qq", created_at=1)
            conn2.commit()
        finally:
            conn2.close()
        got = {r["id"]: r for r in app.list_locks()}["new-1"]
        check("迁移之后，新记录照样能带全部新字段写入",
              got["source"] == "client" and got["mode"] == "机密行动" and got["price"] == 20
              and got["contact"] == "new_wx" and got["contact_type"] == "qq", str(got))
    finally:
        # Windows 上 SQLite 的连接要等垃圾回收才真正关闭：先 gc.collect()，删不掉就等一会儿重试
        # （临时目录，万一真删不掉也只是留个空壳，不影响测试结果）
        for _ in range(5):
            gc.collect()
            try:
                shutil.rmtree(tmp)
                break
            except OSError:
                time.sleep(0.2)


if __name__ == "__main__":
    print("=" * 60)
    print(" 自助预约 / 通知 / 数据库迁移 测试（%s）" % BASE)
    print("=" * 60, flush=True)
    main_tests()
    migration_test()
    print()
    print("通过 %d 项，失败 %d 项" % (ok, fail))
    sys.exit(1 if fail else 0)
