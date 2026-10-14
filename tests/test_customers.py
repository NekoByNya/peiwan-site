# -*- coding: utf-8 -*-
"""
★ 客户档案 + 到期提醒 的自动测试。

测的是这个功能的全部关键行为：
  1. 建档：字段全填 / 只填名字 / 名字为空要报中文错
  2. 日期校验："2024-13-45"、"8月20日"、乱填 → **一律安全置空**（我选的取舍，见下面第 2 节）
  3. ★ 到期分档：-5/-1/0/1/3/4/7/8 天各造一条，验证 level 和 daysLeft 完全正确
  4. 时区：**服务器时区不是中国时，"今天"也必须算对**（真的起一个 TZ 不对的子进程来验）
  5. 排序：列表顺序 = 紧急度（超期最前，长期/一次性最后）
  6. 续费：extend 从原到期日推 1 个月 ✓ / from_today 从今天推 ✓ /
     已超期很久用 extend 的结果"仍然是过去"（规则写清楚，而且要给提示）
  7. 状态：暂停 / 已结束的客户不算进"待催"
  8. 删除
  9. GET /api/admin 里有 customers / customerStats / today
 10. 金额是自由文本："20R/月" 一个字都不许变
 11. 备份导出 → 恢复后客户完好；**老备份（没有 customers 字段）导入不报错、不清空客户**
 12. 未登录访问 → 401

风格和 test_zones.py 一样：纯标准库、打印 [OK]/[FAIL]、最后给通过/失败计数，失败则退出码非 0。
⚠️ 和 test_api.py 一样**会写数据**，只能对着测试实例跑。

用法：
    python tests\\test_customers.py
    set TEST_BASE=http://127.0.0.1:8199 && python tests\\test_customers.py
"""

import datetime as dt
import http.cookiejar
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASE = os.environ.get("TEST_BASE") or "http://127.0.0.1:8123"
PASSWORD = os.environ.get("TEST_PASSWORD") or "baixu123"
CN = dt.timezone(dt.timedelta(hours=8))

# 这个脚本会改数据，别对着线上站点跑（和 test_api.py / test_zones.py 一样的保护）
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


def call(path, method="GET", payload=None, use_cookie=True):
    """
    发一个请求。use_cookie=False 时用一个"干净"的 opener（用来验证未登录会被挡）。
    HTTPError 也照常返回 (状态码, 正文)，和 test_zones.py 一致。
    """
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    opener_to_use = opener if use_cookie else urllib.request.build_opener()
    try:
        with opener_to_use.open(req, timeout=25) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


def jcall(path, method="GET", payload=None, use_cookie=True):
    """发请求并解析 JSON，返回 (状态码, 字典)"""
    s, b = call(path, method, payload, use_cookie)
    try:
        return s, json.loads(b)
    except ValueError:
        return s, {"raw": b[:200]}


def customer_api(payload):
    return jcall("/api/admin/customers", "POST", payload)


def admin_state():
    return jcall("/api/admin")[1]


# ---------- 日期小工具（和后端 today_cn() 一个算法：UTC 现在 + 8 小时） ----------
def today_cn():
    return (dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=8)).strftime("%Y-%m-%d")


def shift(days):
    t = dt.datetime.strptime(today_cn(), "%Y-%m-%d").date() + dt.timedelta(days=days)
    return t.strftime("%Y-%m-%d")


def add_months(text, months):
    """和后端 add_months() 完全一样的算法（日期溢出就压到当月最后一天）"""
    d = dt.datetime.strptime(text, "%Y-%m-%d").date()
    total = d.month - 1 + months
    year, month = d.year + total // 12, total % 12 + 1
    day = d.day
    while day > 1:
        try:
            return dt.date(year, month, day).strftime("%Y-%m-%d")
        except ValueError:
            day -= 1
    return dt.date(year, month, 1).strftime("%Y-%m-%d")


def make_customer(**fields):
    body = {"action": "create", "name": "测试客户", "status": "active"}
    body.update(fields)
    s, data = customer_api(body)
    if s != 200:
        raise AssertionError("建档失败：%s %s" % (s, data))
    return data["customer"]


def find(cid):
    return next((c for c in admin_state().get("customers", []) if c["id"] == cid), None)


def cleanup(ids):
    """把测试造的客户删掉，免得影响后面的阶段（跑完 run_tests 也会整个删掉 .testdata）"""
    for cid in ids:
        customer_api({"action": "delete", "id": cid})


# ============================================================================
#  第一到第十部分：接口测试（对着运行中的实例）
# ============================================================================

def main_tests():
    print("=== 0. 准备：登录 + 记下基线 ===")
    s, b = call("/api/auth", "POST", {"password": PASSWORD})
    check("管理员登录", s == 200, b.strip()[:80])
    adm = admin_state()
    check("/api/admin 里有 customers 列表", isinstance(adm.get("customers"), list),
          type(adm.get("customers")).__name__)
    check("/api/admin 里有 customerStats", isinstance(adm.get("customerStats"), dict),
          str(adm.get("customerStats"))[:120])
    check("/api/admin 里有 today（北京时间今天）", adm.get("today") == today_cn(),
          "%s / 期望 %s" % (adm.get("today"), today_cn()))
    base_total = adm["customerStats"]["total"]
    print("     基线：现有客户 %d 位" % base_total)
    # 客户档案可以关联一个分区（也就是"他买的是哪个服务"）。这里**只挑一个已经存在的
    # 分区来用**，绝不新建 —— 否则单独跑本脚本时会把分区表搅乱（曾经真的撞过一次车：
    # 这里建了 arknights，后面 test_zones.py 再建就报重复了）。
    s, pub = call("/api/public")
    pub = json.loads(pub) if s == 200 else {}
    zones = pub.get("zones") or []
    test_zone = zones[0]["id"] if zones else ""
    test_zone_name = zones[0]["name"] if zones else ""
    print("     用分区「%s」（%s）来测关联服务这个字段" % (test_zone_name, test_zone))
    check("站点里至少有一个分区可以拿来关联", bool(zones), str([z["id"] for z in zones]))

    print("=== 1. 新建客户 ===")
    created = []
    full = make_customer(name="小明", contact="wx_xiaoming", contact_type="wechat",
                         zone=test_zone, item="精托托管", price="100R/月",
                         start_date=shift(-30), end_date=shift(2), status="active",
                         note="账号已给，每天返图")
    created.append(full["id"])
    check("各种字段都填 → 建出来了", bool(full.get("id")), str(full)[:100])
    check("字段原样存下来（含中文备注）",
          full["name"] == "小明" and full["contact"] == "wx_xiaoming"
          and full["item"] == "精托托管" and full["note"] == "账号已给，每天返图",
          "%s / %s" % (full["name"], full["item"]))
    check("★ 金额是自由文本，一个字都没变", full["price"] == "100R/月", repr(full["price"]))
    check("★ 金额里没有被偷偷加 ¥", "¥" not in full["price"], repr(full["price"]))
    check("带上了 daysLeft 和 level", full["daysLeft"] == 2 and full["level"] == "soon",
          "daysLeft=%s level=%s" % (full["daysLeft"], full["level"]))
    check("带上了分区名（前端好显示是哪个服务）", full.get("zoneName") == test_zone_name,
          "%s（期望 %s）" % (full.get("zoneName"), test_zone_name))

    only_name = make_customer(name="只填名字的")
    created.append(only_name["id"])
    check("只填名字 → 也能建（其余字段是空字符串）",
          only_name["name"] == "只填名字的" and only_name["price"] == ""
          and only_name["end_date"] == "" and only_name["status"] == "active",
          str(only_name)[:120])
    check("没填到期日 → daysLeft 是 None、level 是 none（长期）",
          only_name["daysLeft"] is None and only_name["level"] == "none",
          "daysLeft=%s level=%s" % (only_name["daysLeft"], only_name["level"]))

    s, data = customer_api({"action": "create", "name": "   "})
    check("名字是空白 → 400 并且给中文提示",
          s == 400 and "客户称呼不能是空的" in data.get("error", ""), "%s %s" % (s, data))
    s, data = customer_api({"action": "create"})
    check("连名字都没传 → 400 同样提示", s == 400 and "客户称呼不能是空的" in data.get("error", ""),
          "%s %s" % (s, data))
    s, data = customer_api({"action": "create", "name": "x" * 80})
    check("超长名字被截到 30 字", s == 200 and len(data["customer"]["name"]) == 30,
          str(len(data.get("customer", {}).get("name", ""))))
    created.append(data["customer"]["id"])
    s, data = customer_api({"action": "create", "name": "渠道乱填的", "contact_type": "telegram"})
    check("渠道不在白名单 → 回落到微信",
          s == 200 and data["customer"]["contact_type"] == "wechat",
          str(data.get("customer", {}).get("contact_type")))
    created.append(data["customer"]["id"])
    s, data = customer_api({"action": "create", "name": "分区乱填的", "zone": "no-such-zone"})
    check("分区不存在 → 不报错，只是不关联",
          s == 200 and data["customer"]["zone"] == "", str(data.get("error") or data["customer"]["zone"]))
    created.append(data["customer"]["id"])
    s, data = customer_api({"action": "create", "name": "状态乱填的", "status": "hibernating"})
    check("状态不在白名单 → 回落到进行中",
          s == 200 and data["customer"]["status"] == "active",
          str(data.get("customer", {}).get("status")))
    created.append(data["customer"]["id"])

    print("=== 2. 日期格式校验（我的选择：不合法 → 安全置空，不报错） ===")
    # 取舍说明：主人常常先建档案、日期以后再补。为一个打错的日期把整表单打回去，
    # 他填的档位/金额/备注就白填了；置空之后这条客户变成"长期"，永远不会误报到期的假警。
    for bad in ("2024-13-45", "8月20日", "乱填", "2026-02-30", "2026-1-5", "2026/01/05", "0"):
        s, data = customer_api({"action": "create", "name": "日期测试%s" % bad[:6], "end_date": bad})
        got = data.get("customer", {}).get("end_date")
        check("坏日期 %-10r → 安全置空（仍建档成功）" % bad,
              s == 200 and got == "" and data["customer"]["level"] == "none",
              "状态=%s end_date=%r" % (s, got))
        if s == 200:
            created.append(data["customer"]["id"])
    s, data = customer_api({"action": "create", "name": "好日期", "start_date": "2026-08-01",
                            "end_date": "2026-08-20"})
    check("正常日期 2026-08-20 原样存下", s == 200 and data["customer"]["end_date"] == "2026-08-20",
          str(data.get("customer", {}).get("end_date")))
    created.append(data["customer"]["id"])

    print("=== 3. ★ 到期分档（整个功能的灵魂） ===")
    # 每种天数各造一条，逐个核对 daysLeft 和 level
    cases = [(-5, "expired"), (-1, "expired"), (0, "today"), (1, "soon"), (3, "soon"),
             (4, "week"), (7, "week"), (8, "ok")]
    for days, expect in cases:
        c = make_customer(name="分档%+d" % days, end_date=shift(days))
        created.append(c["id"])
        check("end_date = 今天%+d 天 → level=%-7s daysLeft=%s" % (days, expect, days),
              c["level"] == expect and c["daysLeft"] == days,
              "实际 level=%s daysLeft=%s" % (c["level"], c["daysLeft"]))
    long_term = make_customer(name="分档长期", end_date="")
    created.append(long_term["id"])
    check("end_date 留空 → level=none、daysLeft=None（不算到期）",
          long_term["level"] == "none" and long_term["daysLeft"] is None,
          "level=%s daysLeft=%s" % (long_term["level"], long_term["daysLeft"]))
    check("每档都有中文名和颜色（前端照着上色）",
          all(c.get("levelLabel") and c.get("levelColor") and c.get("levelLabel") != ""
              for c in admin_state()["customers"]),
          "抽查：" + str([(c["level"], c["levelLabel"]) for c in admin_state()["customers"][:3]]))

    print("=== 4. 时区：服务器时区不是中国时，今天也必须算对 ===")
    # 怎么验证的：把 app.py 导进一个 TZ 故意设错（UTC / 纽约 / 东京）的子进程，
    # 看 today_cn() 和 days_left() 的结果。算法是"带 +8 时区的现在"，和 TZ 环境变量无关；
    # 而 datetime.now()（不带时区）在 TZ=UTC 时会比北京时间晚 8 小时 —— 用错的写法这里就会挂。
    probe = (
        "import sys, datetime as dt\n"
        "sys.path.insert(0, %r)\n"
        "import app\n"
        "want = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=8)).strftime('%%Y-%%m-%%d')\n"
        "print('PROBE', app.today_cn(), want, app.days_left(want))\n"
    ) % (ROOT,)
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    for tz in ("UTC", "America/New_York", "Asia/Tokyo"):
        env["TZ"] = tz
        try:
            res = subprocess.run([sys.executable, "-c", probe], env=env, capture_output=True,
                                 text=True, timeout=60)
            line = next((l for l in res.stdout.splitlines() if l.startswith("PROBE")), "")
            # 去掉开头的 PROBE（app.py 被 import 时会打印迁移日志，所以只认这一行）
            _tag, got, want, days = line.split()
            check("TZ=%-18s 时 today_cn() 仍是北京时间的今天" % tz, got == want,
                  "得到 %s，期望 %s" % (got, want))
            check("TZ=%-18s 时 days_left(今天) = 0" % tz, days == "0", "得到 %s" % days)
        except Exception as e:
            check("TZ=%s 的子进程探针" % tz, False,
                  "%s: %s / stderr=%s" % (type(e).__name__, e, (res.stderr or "")[-120:]))

    print("=== 5. 排序：列表顺序 = 紧急度 ===")
    # 这一刻"进行中"的客户里，顺序必须是：超期最久 → 今天 → 3天内 → 一周内 → 正常 → 长期
    active_levels = [c["level"] for c in admin_state()["customers"] if c["status"] == "active"]
    order = {"expired": 0, "today": 1, "soon": 2, "week": 3, "ok": 4, "none": 5}
    ranks = [order.get(lv, 9) for lv in active_levels]
    check("★ 列表是按紧急度排的（超期最前，长期最后）", ranks == sorted(ranks), str(active_levels))
    expired_days = [c["daysLeft"] for c in admin_state()["customers"] if c["level"] == "expired"]
    check("超期的里面，超期最久的排最前", expired_days == sorted(expired_days), str(expired_days))
    first_active = next(c for c in admin_state()["customers"] if c["status"] == "active")
    check("列表第一条就是最该处理的那个（超期/今天到期）",
          first_active["level"] in ("expired", "today"),
          "%s level=%s daysLeft=%s" % (first_active["name"], first_active["level"],
                                       first_active["daysLeft"]))

    print("=== 6. ★ 续费 ===")
    # 6.1 extend：从原到期日推 1 个月（不损失客户已经付过的天数）
    c = make_customer(name="续费extend", end_date=shift(2), item="精托托管", price="100R/月")
    created.append(c["id"])
    want = add_months(c["end_date"], 1)
    s, data = customer_api({"action": "renew", "id": c["id"], "months": 1, "mode": "extend"})
    check("extend 从原到期日推 1 个月：%s → %s" % (c["end_date"], want),
          s == 200 and data.get("newEndDate") == want, "%s %s" % (s, data.get("newEndDate")))
    after = data["customer"]
    check("★ 续费后 level / daysLeft 跟着变了",
          after["end_date"] == want and after["daysLeft"] == (dt.datetime.strptime(want, "%Y-%m-%d").date()
                                                             - dt.datetime.strptime(today_cn(), "%Y-%m-%d").date()).days
          and after["level"] not in ("soon", "today", "expired"),
          "daysLeft=%s level=%s" % (after["daysLeft"], after["level"]))

    # 6.2 from_today：从今天推
    c2 = make_customer(name="续费fromToday", end_date=shift(-2))
    created.append(c2["id"])
    want2 = add_months(today_cn(), 1)
    s, data = customer_api({"action": "renew", "id": c2["id"], "months": 1, "mode": "from_today"})
    check("from_today 从今天推 1 个月：今天 → %s" % want2,
          s == 200 and data.get("newEndDate") == want2, "%s %s" % (s, data.get("newEndDate")))
    check("超期客户用 from_today 之后立刻变成正常",
          data["customer"]["level"] == "ok" and data["customer"]["daysLeft"] > 7,
          "level=%s daysLeft=%s" % (data["customer"]["level"], data["customer"]["daysLeft"]))

    # 6.3 已超期很久 + extend：结果仍然是过去的日期（规则写死在这里，并且要给提示）
    c3 = make_customer(name="续费超期久", end_date=shift(-100))
    created.append(c3["id"])
    s, data = customer_api({"action": "renew", "id": c3["id"], "months": 1, "mode": "extend"})
    expect3 = add_months(c3["end_date"], 1)
    check("已超期 100 天用 extend：还是从原到期日推（%s → %s）" % (c3["end_date"], expect3),
          s == 200 and data.get("newEndDate") == expect3, "%s %s" % (s, data.get("newEndDate")))
    check("★ 这种情况下后端会给一句提示，让他改用「从今天算」",
          "从今天算" in (data.get("tip") or ""), repr(data.get("tip"))[:80])
    check("★ 提示不是随便给的：只有算出来还是过去时才出现",
          data["customer"]["daysLeft"] < 0,
          "续费后 daysLeft=%s" % data["customer"]["daysLeft"])
    s, data = customer_api({"action": "renew", "id": c3["id"], "months": 1, "mode": "from_today"})
    check("同一客户改用 from_today → 立刻变成未来的日期",
          s == 200 and data["customer"]["daysLeft"] > 7,
          "新到期日 %s（daysLeft=%s）" % (data.get("newEndDate"), data["customer"]["daysLeft"]))

    # 6.4 周 / 天 / 三个月 / 叠加
    c4 = make_customer(name="续费按周", end_date=shift(1))
    created.append(c4["id"])
    s, data = customer_api({"action": "renew", "id": c4["id"], "weeks": 1, "mode": "extend"})
    check("续 1 周 = 原到期日 + 7 天",
          s == 200 and data.get("newEndDate") == shift(8), str(data.get("newEndDate")))
    s, data = customer_api({"action": "renew", "id": c4["id"], "days": 3, "mode": "extend"})
    check("再续 3 天（可叠加）", s == 200 and data.get("newEndDate") == shift(11),
          str(data.get("newEndDate")))
    s, data = customer_api({"action": "renew", "id": c4["id"], "months": 3, "mode": "extend"})
    check("续 3 个月", s == 200 and data.get("newEndDate") == add_months(shift(11), 3),
          str(data.get("newEndDate")))
    s, data = customer_api({"action": "renew", "id": c4["id"], "months": 0, "mode": "extend"})
    check("什么都不续 → 400 中文提示", s == 400 and "续多久" in data.get("error", ""),
          "%s %s" % (s, data))
    s, data = customer_api({"action": "renew", "id": long_term["id"], "months": 1})
    check("长期客户（没有到期日）不能续费 → 400 让他先填日期",
          s == 400 and "到期日" in data.get("error", ""), "%s %s" % (s, data))
    s, data = customer_api({"action": "renew", "id": "no-such-id", "months": 1})
    check("续费一个不存在的客户 → 404", s == 404, "%s %s" % (s, data.get("error")))
    # 月份进位：1 月 31 日 + 1 个月要压到 2 月 28/29 日（后端 add_months 的规则）
    c5 = make_customer(name="续费跨月", end_date="2026-01-31")
    created.append(c5["id"])
    s, data = customer_api({"action": "renew", "id": c5["id"], "months": 1, "mode": "extend"})
    check("1 月 31 日 + 1 个月 → 2 月 28 日（压到月末，不会滚到 3 月）",
          s == 200 and data.get("newEndDate") == "2026-02-28", str(data.get("newEndDate")))

    print("=== 7. 状态：暂停 / 已结束 不算进待催 ===")
    paused = make_customer(name="暂停的", end_date=shift(-1), status="paused")
    created.append(paused["id"])
    ended = make_customer(name="结束的", end_date=shift(1), status="ended")
    created.append(ended["id"])
    check("暂停的客户 level=paused（不按天数算）",
          paused["level"] == "paused" and paused["levelLabel"] == "已暂停",
          "level=%s" % paused["level"])
    check("已结束的客户 level=ended", ended["level"] == "ended" and ended["levelLabel"] == "已结束",
          "level=%s" % ended["level"])
    stats = admin_state()["customerStats"]
    paused_count = len([c for c in admin_state()["customers"] if c["level"] == "paused"])
    ended_count = len([c for c in admin_state()["customers"] if c["level"] == "ended"])
    active_urgent = len([c for c in admin_state()["customers"]
                         if c["status"] == "active"
                         and c["level"] in ("expired", "today", "soon", "week")])
    check("★「待催」人数不包括暂停/已结束的",
          stats["remind"] == active_urgent,
          "remind=%s，进行中且急的=%s（暂停 %s / 结束 %s）"
          % (stats["remind"], active_urgent, paused_count, ended_count))
    check("统计里 expired / today / soon / week 都是累计口径（week >= soon >= today）",
          stats["week"] >= stats["soon"] >= stats["today"] and stats["soon"] >= stats["expired"],
          str(stats))
    check("active = 进行中的客户数（暂停/已结束不算）",
          stats["active"] == len([c for c in admin_state()["customers"] if c["status"] == "active"]),
          str(stats))
    check("total = 全部客户（含暂停/已结束/长期）",
          stats["total"] == len(admin_state()["customers"]), str(stats))

    print("=== 8. 编辑 / 删除 ===")
    s, data = customer_api({"action": "update", "id": full["id"], "name": "小明改名了",
                            "price": "55R/月", "end_date": shift(5), "status": "active",
                            "contact": "wx_new", "contact_type": "qq", "zone": test_zone,
                            "item": "半精托管", "note": "换档了"})
    check("编辑：名字/金额/日期都能改",
          s == 200 and data["customer"]["name"] == "小明改名了"
          and data["customer"]["price"] == "55R/月"
          and data["customer"]["end_date"] == shift(5),
          str({k: data.get("customer", {}).get(k) for k in ("name", "price", "end_date")}))
    check("编辑后 level 跟着新日期变（5 天 → week）",
          data["customer"]["level"] == "week", str(data["customer"]["level"]))
    before = len(admin_state()["customers"])
    s, data = customer_api({"action": "delete", "id": full["id"]})
    check("删除成功", s == 200 and len(data["customers"]) == before - 1,
          "%s → %s" % (before, len(data.get("customers", []))))
    check("删掉的客户确实找不到了", find(full["id"]) is None)
    created.remove(full["id"])
    s, data = customer_api({"action": "delete", "id": full["id"]})
    check("再删一次 → 404（不会报 500）", s == 404, "%s %s" % (s, data.get("error")))
    s, data = customer_api({"action": "delete", "id": "no-such-id"})
    check("删一个不存在的 id → 404", s == 404, str(s))
    s, data = customer_api({"action": "explode"})
    check("不认识的动作 → 400", s == 400, str(data.get("error")))

    print("=== 9. 金额一律自由文本 ===")
    for price in ("20R/月", "推图15R", "55 元/月", "面议", "100R/月（含月卡5R）", "0"):
        c = make_customer(name="价格%s" % price[:4], price=price)
        created.append(c["id"])
        check("价格 %-20r 原样进出" % price, c["price"] == price, repr(c["price"]))
    check("★ 全站没有任何地方给金额加 ¥（客户档案里）",
          all("¥" not in (c.get("price") or "")
              for c in admin_state()["customers"] if c.get("price") != "面议"),
          str([c.get("price") for c in admin_state()["customers"] if "¥" in (c.get("price") or "")]))

    print("=== 10. 未登录访问 ===")
    s, _b = call("/api/admin/customers", "POST", {"action": "create", "name": "黑产"}, use_cookie=False)
    check("★ 未登录 POST /api/admin/customers → 401", s == 401, str(s))
    s, data = call("/api/admin/customers", "POST", {"action": "delete", "id": "x"}, use_cookie=False)
    check("未登录连删除也做不了（同样 401，不会真删）", s == 401, str(s))
    s, _b = call("/api/admin/customers", "GET", None, use_cookie=False)
    check("GET /api/admin/customers 不是接口（客户数据只从 /api/admin 里拿）→ 404", s == 404, str(s))
    s, _b = call("/api/admin", "GET", None, use_cookie=False)
    check("★ 未登录 GET /api/admin → 401（客户数据也在里面）", s == 401, str(s))

    return created


# ============================================================================
#  第十一部分：备份 / 恢复
# ============================================================================

def backup_tests(created):
    print("=== 11. 备份导出 / 恢复 ===")
    s, _b = call("/api/auth", "POST", {"password": PASSWORD})
    s, body = call("/api/admin/export")
    check("导出备份 200", s == 200, str(s))
    backup = json.loads(body)
    check("★ 备份里有 customers 字段", isinstance(backup.get("customers"), list),
          type(backup.get("customers")).__name__)
    names = [c["name"] for c in backup["customers"]]
    check("★ 备份里的客户数 = 列表里的客户数",
          len(backup["customers"]) == len(admin_state()["customers"]),
          "%s / %s" % (len(backup["customers"]), len(admin_state()["customers"])))
    check("★ 备份里连金额文本都一模一样",
          all(any(x["price"] == c["price"] and x["end_date"] == c["end_date"]
                  for x in backup["customers"])
              for c in admin_state()["customers"] if c["price"]),
          "抽查 %d 条" % len(names))

    # 11.1 造一个"多出来的"客户，然后恢复 → 它应该被备份里的内容替换掉
    extra = make_customer(name="恢复前多出来的")
    check("恢复前先造一条多余的客户", find(extra["id"]) is not None)
    s, data = jcall("/api/admin/import", "POST", {"backup": backup})
    check("导入备份成功", s == 200 and data.get("ok") is True, "%s %s" % (s, str(data)[:120]))
    check("★ 恢复后客户和备份里完全一致（多余的被覆盖掉）",
          find(extra["id"]) is None and len(data["customers"]) == len(backup["customers"]),
          "恢复了 %s 位" % len(data.get("customers", [])))
    sample = next((c for c in backup["customers"] if c["price"]), None)
    if sample:
        got = find(sample["id"])
        check("★ 恢复后金额/到期日/档位一字不差",
              got is not None and got["price"] == sample["price"]
              and got["end_date"] == sample["end_date"] and got["item"] == sample["item"],
              "%s / %s" % (got and got.get("price"), got and got.get("end_date")))
    check("恢复摘要里带上了客户数", "customers" in (data.get("summary") or {}),
          str((data.get("summary") or {}).get("customers")))

    # 11.2 ★ 老备份（没有 customers 字段）导入：不报错、不清空现在的客户
    old = {k: v for k, v in backup.items() if k != "customers"}
    count_before = len(admin_state()["customers"])
    s, data = jcall("/api/admin/import", "POST", {"backup": old})
    check("★ 老备份（没有 customers）导入不报错", s == 200 and data.get("ok") is True,
          "%s %s" % (s, str(data)[:120]))
    after = admin_state()["customers"]
    check("★ 老备份导入后，客户一条都没少（绝不清空）", len(after) == count_before,
          "%s → %s" % (count_before, len(after)))
    check("★ 老备份导入后，客户内容也没被动过",
          all(any(a["id"] == b["id"] and a["price"] == b["price"] and a["name"] == b["name"]
                  for b in backup["customers"]) for a in after),
          "抽查了 %d 条" % len(after))

    # 11.3 备份里的坏数据不能让整份恢复失败
    broken = json.loads(json.dumps(backup))
    broken["customers"] = broken["customers"] + [
        {"name": "", "price": "没有名字的坏数据"},
        {"name": "日期乱写的", "end_date": "2024-13-45", "price": "推图15R"},
        "这一条根本不是字典",
    ]
    s, data = jcall("/api/admin/import", "POST", {"backup": broken})
    check("备份里有坏行 → 恢复照样成功（坏行跳过，日期乱写的置空）",
          s == 200 and data.get("ok") is True, "%s %s" % (s, str(data)[:120]))
    got = next((c for c in admin_state()["customers"] if c["name"] == "日期乱写的"), None)
    check("坏日期那条被恢复成了长期（end_date 空）",
          got is not None and got["end_date"] == "" and got["level"] == "none",
          str(got and {k: got[k] for k in ("name", "end_date", "level")}))
    check("没名字的那条被跳过了（没有生成空名字客户）",
          all(c["name"].strip() for c in admin_state()["customers"]),
          str([c["name"] for c in admin_state()["customers"] if not c["name"].strip()]))
    # 最后一件事：把状态恢复成备份里的样子，别把坏数据留给后面的阶段
    s, _d = jcall("/api/admin/import", "POST", {"backup": backup})
    check("再导一次干净备份，状态回到原样", s == 200, str(s))
    return True


def main():
    created = []
    try:
        created = main_tests()
        backup_tests(created)
    finally:
        # 测试造的客户自己收拾干净（run_tests 结束也会整个删掉 .testdata）
        try:
            cleanup([c for c in created if c])
        except Exception:
            pass
    print()
    print("通过 %d 项，失败 %d 项" % (ok, fail))
    return 1 if fail else 0


if __name__ == "__main__":
    print("=" * 60)
    print(" 客户档案 + 到期提醒 测试（%s）" % BASE)
    print("=" * 60, flush=True)
    sys.exit(main())
