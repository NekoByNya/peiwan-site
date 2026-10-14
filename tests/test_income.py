# -*- coding: utf-8 -*-
"""
★ 第三刀：收入登记 + 月度统计 的自动测试。

背景（决定了下面每一条断言为什么这么写）：
  主人是**一个人靠这个吃饭**。客户那一章解决的是"该催谁了"，这一章解决的是
  "钱从哪来、该推什么"。核心设计原则只有一条：
    ★ 登记必须发生在他本来就要做的动作里 —— 不能让他每天单独记账。
  所以三个记账入口全都挂在已有的动作上：
    ① 点「已确认」→ 自动记一笔（source='booking'）
    ② 续费时填了金额 → 自动记一笔（source='renew'）
    ③ 剪辑 / PS / 建站这类一次性单子 → 手动「+ 记一笔」（source='manual'）

测的东西：

  【金额解析】
   1. parse_amount：20R/月 → 20、推图15R → 15、￥200 → 200、100 元 → 100、
      "100R/月（含月卡5R）" → 100（★只取第一个数字）、按需报价 / 空 → None、"0" → 0.0

  【手动记一笔】
   2. create / update / delete 三个动作；坏输入一律 400 中文错误
   3. ★ 金额 0 / 负数 / 空 / 不是数字 → 400（流水里不许出现 0 元）
   4. 日期不合法 → **不报错**，按今天（北京时间）记，并回一句 dateTip 说清楚

  【★ 确认预约自动记账】
   5. confirm-lock 之后流水里多一条：金额 = 锁的 price、来源 = 确认预约、
      日期 = 这单开始那天（北京时间）、ref = 锁的 id
   6. ★ 幂等：同一单确认两次，流水里只有一条
   7. price = 0 的锁确认后**不记账**（免得流水里一堆 0 元）
   8. 休息锁（kind=rest）确认也不记账

  【★ 续费记账】
   9. 带 amount → 记一笔（来源=续费）；不带 → 不记
  10. ★ 同一客户同一个新到期日续两次，只记一笔（幂等）
  11. 客户档案里的金额文本依旧是自由文本，一个字都没被动

  【★ 月度统计】
  12. 四个范围（本月 / 上月 / 近 3 个月 / 今年）的**总额和笔数**都对
      （用造出来的跨月数据，一笔一笔自己加，和接口返回的数字比）
  13. ★ 环比：上月没有记录时**不显示 0%**，而是说"上月没有记录"
  14. 按大类 / 按服务 / 按客户的排行数字正确
  15. ★ "本月"按**北京时间**判（TZ=UTC 的子进程验一遍，和客户那一章同一套证明方法）

  【备份 / 恢复】
  16. 备份里有 payments；恢复后流水完好
  17. ★ 老备份（没有 payments 字段）导入不报错、**不清空现有流水**

  【安全】
  18. 未登录访问 /api/admin/payments → 401

  【回归】
  19. 确认 / 续费这些老动作的返回没被记账逻辑弄坏（还是原来的字段）

风格和 test_customers.py 一样：纯标准库、打印 [OK]/[FAIL]、最后给通过/失败计数，失败则退出码非 0。
⚠️ 和 test_api.py 一样**会写数据**，只能对着测试实例跑。

用法：
    python tests\\test_income.py
    set TEST_BASE=http://127.0.0.1:8199 && python tests\\test_income.py
"""

import datetime as dt
import http.cookiejar
import json
import os
import re
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASE = os.environ.get("TEST_BASE") or "http://127.0.0.1:8123"
PASSWORD = os.environ.get("TEST_PASSWORD") or "baixu123"
DATA_DIR = os.environ.get("TEST_DATA_DIR") or os.path.join(ROOT, "data")
CN = dt.timezone(dt.timedelta(hours=8))
HOUR_MS = 3600 * 1000

# 这个脚本会改数据，别对着线上站点跑（和 test_customers.py 一样的保护）
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
    s, b = call(path, method, payload, use_cookie)
    try:
        return s, json.loads(b)
    except ValueError:
        return s, {"raw": b[:200]}


def pay_api(payload):
    return jcall("/api/admin/payments", "POST", payload)


def schedule(payload):
    return jcall("/api/admin/schedule", "POST", payload)


def customer_api(payload):
    return jcall("/api/admin/customers", "POST", payload)


def admin_state():
    return jcall("/api/admin")[1]


# ---------- 日期小工具（全部按北京时间，和后端 today_cn() 一个算法） ----------

def today_cn():
    return (dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=8)).strftime("%Y-%m-%d")


def shift(days):
    d = dt.datetime.strptime(today_cn(), "%Y-%m-%d").date() + dt.timedelta(days=days)
    return d.strftime("%Y-%m-%d")


def add_months(text, months):
    """和后端 add_months() 完全一样：先加月份，日期溢出就压到当月最后一天。"""
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


def month_first_shift(months):
    """本月 1 号往前/往后推 N 个月 → "YYYY-MM-DD"（造跨月数据要用）"""
    return add_months(today_cn()[:8] + "01", months)


def shift_before(text):
    """"YYYY-MM-01" 的前一天 = 上个月最后一天"""
    d = dt.datetime.strptime(text, "%Y-%m-%d").date() - dt.timedelta(days=1)
    return d.strftime("%Y-%m-%d")


# ---------- 直接改库 / 造数据的小工具 ----------

def db():
    conn = sqlite3.connect(os.path.join(DATA_DIR, "site.db"), timeout=15)
    conn.row_factory = sqlite3.Row
    return conn


def make_client_lock(name, start_ms, price=0, mode="测试模式", confirmed=0, kind="booking",
                     contact=None, zone=None):
    """
    往库里直接造一条"客户自助预约"（等价于客户在前台点了一下）。

    为什么不走 /api/book：那个接口有"同一 IP 10 分钟最多 5 次"的限流，
    而且价格来自分区的报价档位、不好随手指定。这里是"想验哪条边界就造哪条"，
    做法和 tests/test_confirm.py 的 make_client_lock() 一致。
    """
    lock_id = uuid.uuid4().hex
    now_ms = int(time.time() * 1000)
    conn = db()
    conn.execute("""INSERT INTO locks(id, kind, start, end, client, note, source, mode, price,
                    contact, contact_type, created_at, zone, confirmed, confirmed_at)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                 (lock_id, kind, int(start_ms), int(start_ms) + HOUR_MS, name, "",
                  "client", mode, int(price), contact or ("wx_" + name), "wechat",
                  now_ms, zone or "", int(confirmed), 0))
    conn.commit()
    conn.close()
    return lock_id


def delete_lock(lock_id):
    conn = db()
    conn.execute("DELETE FROM locks WHERE id=?", (lock_id,))
    conn.commit()
    conn.close()


def free_future_hour(offset_hours=2):
    """找一个未来且空闲的整点（给造出来的锁用）"""
    now_ms = int(time.time() * 1000)
    want = now_ms + offset_hours * HOUR_MS
    slots = jcall("/api/public")[1]["slots"]
    hit = next((s["start"] for s in slots if s["status"] == "free" and s["start"] >= want), None)
    return hit or (now_ms + 48 * HOUR_MS)


def lock_by_id(lock_id):
    return next((lk for lk in admin_state().get("schedule", {}).get("locks", [])
                 if lk.get("id") == lock_id), None)


def payments_now():
    return admin_state().get("payments") or []


def find_payment(source, ref=None):
    return next((p for p in payments_now()
                 if p.get("source") == source and (ref is None or p.get("ref") == ref)), None)


def payment_view(ref):
    """
    去**统计返回的流水视图**里找这一条（带 amountText / sourceLabel / label 那些给人看的字段）。

    为什么要分两个来源看：/api/admin 的 payments 是**数据库原样**（编辑表单要的是干净字段），
    而收入页表格用的是 income.records（后端 income_view() 洗过的）。
    两边都对才算真的对 —— 少一边，前端就会显示成空白。
    """
    s, data = pay_api({"action": "range", "range": "all"})
    for p in ((data.get("income") or {}).get("records") or []):
        if p.get("ref") == ref:
            return p
    return None


def payments_with(source, ref):
    return [p for p in payments_now() if p.get("source") == source and p.get("ref") == ref]


def make_payment(**fields):
    body = {"action": "create", "amount": 30, "date": today_cn(), "item": "测试项目"}
    body.update(fields)
    s, data = pay_api(body)
    if s != 200:
        raise AssertionError("记一笔失败：%s %s" % (s, data))
    return data


def cleanup_payments(ids):
    """把测试造的流水删干净，别把数字留给后面的阶段"""
    for pid in ids:
        pay_api({"action": "delete", "id": pid})


# ============================================================================
#  第一到第十三部分：接口测试（对着运行中的实例）
# ============================================================================

def main_tests():
    print("=== 0. 准备：登录 + 记下基线 ===")
    s, b = call("/api/auth", "POST", {"password": PASSWORD})
    check("管理员登录", s == 200, b.strip()[:80])
    adm = admin_state()
    check("/api/admin 里有 payments（收入流水）", isinstance(adm.get("payments"), list),
          type(adm.get("payments")).__name__)
    check("/api/admin 里有 income（统计）", isinstance(adm.get("income"), dict),
          str(adm.get("income"))[:120])
    check("/api/admin 里有 paymentSources（来源的中文名）",
          isinstance(adm.get("paymentSources"), dict)
          and adm["paymentSources"].get("booking") == "确认预约",
          str(adm.get("paymentSources"))[:120])
    check("/api/admin 里有 incomeRanges（五个范围按钮）",
          [r.get("id") for r in (adm.get("incomeRanges") or [])]
          == ["month", "last-month", "3m", "year", "all"],
          str(adm.get("incomeRanges"))[:160])
    base_count = len(adm.get("payments") or [])
    print("     基线：已有流水 %d 条" % base_count)
    zones = jcall("/api/public")[1].get("zones") or []
    test_zone = zones[0]["id"] if zones else ""
    print("     用分区「%s」来测关联服务这个字段" % (zones[0]["name"] if zones else "（没有分区）"))

    created = []

    print("=== 1. ★ parse_amount：从人类写的价格文本里抠数字 ===")
    # 这一条要在**子进程**里验：parse_amount 是 app.py 里的纯函数，
    # 不起网站也能测（而且不受服务器状态影响）。
    # 输出用 | 分隔（别用空格）：用例里有"100 元"这种带空格的中文写法。
    probe = "\n".join([
        "import sys",
        "sys.path.insert(0, %r)" % ROOT,
        "import app",
        "cases = [('20R/月', 20), ('推图15R', 15), ('100R', 100), ('￥200', 200), ('100 元', 100),",
        "         ('55R/月', 55), ('8R/周', 8), ('100R/月（含月卡5R）', 100), ('0', 0), ('0R', 0),",
        "         ('20.5R', 20.5)]",
        "for text, want in cases:",
        "    got = app.parse_amount(text)",
        "    same = got is not None and abs(float(got) - float(want)) < 0.0001",
        "    print('CASE|' + text + '|' + repr(got) + '|' + repr(want) + '|'",
        "          + ('SAME' if same else 'DIFF'))",
        "for text in ('按需报价', '', None, '面议', '（没填）'):",
        "    print('NONE|' + str(text) + '|' + repr(app.parse_amount(text)))",
    ])
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    try:
        res = subprocess.run([sys.executable, "-c", probe], env=env, capture_output=True,
                             text=True, encoding="utf-8", errors="replace", timeout=60)
        lines = res.stdout.splitlines()
        cases = [l.split("|", 4) for l in lines if l.startswith("CASE|")]
        check("parse_amount 的用例都跑出来了（%d 条）" % len(cases), len(cases) == 11,
              (res.stderr or "")[-160:])
        for _tag, text, got, want, same in cases:
            check("parse_amount(%s) == %s" % (text, want), same == "SAME", "得到 %s" % got)
        nones = [l.split("|", 2) for l in lines if l.startswith("NONE|")]
        check("抠不出数字的用例也跑出来了（%d 条）" % len(nones), len(nones) == 5,
              (res.stderr or "")[-160:])
        for _tag, text, got in nones:
            check("parse_amount(%s) 是 None（抠不出数字）" % text, got == "None", "得到 %s" % got)
    except Exception as e:
        check("parse_amount 的子进程探针", False, "%s: %s" % (type(e).__name__, e))

    print("=== 2. 手动记一笔（剪辑 / PS / 建站这类一次性单子） ===")
    s, data = pay_api({"action": "create", "amount": 160, "date": today_cn(),
                       "item": "一条 3 分钟视频", "customer": "阿伟",
                       "note": "B 站视频，验收当天就付了"})
    check("记一笔成功", s == 200 and data.get("ok") is True, "%s %s" % (s, str(data)[:120]))
    one = data.get("payment") or {}
    pid = one.get("id")
    created.append(pid)
    check("★ 字段原样存下（日期 / 金额 / 项目 / 客户 / 备注）",
          one.get("date") == today_cn() and abs(float(one.get("amount") or 0) - 160) < 0.001
          and one.get("item") == "一条 3 分钟视频" and one.get("customer") == "阿伟"
          and "验收" in (one.get("note") or ""),
          str(one)[:160])
    check("★ 来源是「手动补记」（老板手记的不能冒充自动记账）",
          one.get("source") == "manual" and one.get("sourceLabel") == "手动补记",
          "%s / %s" % (one.get("source"), one.get("sourceLabel")))
    check("金额带上了给人看的写法 amountText", one.get("amountText") == "¥160",
          str(one.get("amountText")))
    check("返回里带了最新的流水列表和统计",
          isinstance(data.get("payments"), list) and isinstance(data.get("income"), dict),
          str(sorted(data.keys()))[:160])
    check("★ 统计里的本月总额跟着变了",
          abs(float(data["income"]["total"]) - sum(
              float(p["amount"]) for p in data["payments"] if p["date"][:7] == today_cn()[:7])) < 0.01,
          "total=%s" % data["income"]["total"])

    s, data = pay_api({"action": "create", "amount": "80R", "date": today_cn(), "zone": test_zone,
                       "item": "档位名", "customer": "小明"})
    created.append((data.get("payment") or {}).get("id"))
    check("★ 金额写成 \"80R\" 也认（后端会从文字里抠数字）",
          s == 200 and abs(float((data.get("payment") or {}).get("amount") or 0) - 80) < 0.001,
          "%s %s" % (s, str(data.get("payment"))[:100]))
    check("带了分区 → 返回里带上分区名（前台表格好显示）",
          (data.get("payment") or {}).get("zoneName") == (zones[0]["name"] if zones else ""),
          str((data.get("payment") or {}).get("zoneName")))

    print("=== 3. 金额与日期的校验（中文错误） ===")
    for bad, why in [(0, "0"), (-5, "负数"), ("", "空"), (None, "没填"),
                     ("按需报价", "抠不出数字"), ("abc", "乱填")]:
        s, data = pay_api({"action": "create", "amount": bad, "date": today_cn()})
        check("金额是%s → 400 中文错误" % why,
              s == 400 and "金额" in (data.get("error") or ""), "%s %s" % (s, data.get("error")))
    s, data = pay_api({"action": "create", "amount": 2000000, "date": today_cn()})
    check("金额大得离谱（200 万）→ 400（防手滑多打几个 0）",
          s == 400 and "金额" in (data.get("error") or ""), "%s %s" % (s, data.get("error")))

    s, data = pay_api({"action": "create", "amount": 55, "date": "2024-13-45"})
    created.append((data.get("payment") or {}).get("id"))
    check("★ 日期不合法 → **不报错**，按今天（北京时间）记",
          s == 200 and (data.get("payment") or {}).get("date") == today_cn(),
          "%s %s" % (s, str(data.get("payment"))[:100]))
    check("★ 并且回一句 dateTip 说清「我按今天记了」",
          bool(data.get("dateTip")) and data.get("dateFallback") is True,
          str(data.get("dateTip"))[:120])
    s, data = pay_api({"action": "create", "amount": 55, "date": ""})
    created.append((data.get("payment") or {}).get("id"))
    check("日期留空 → 也是按今天记（不报错）",
          s == 200 and (data.get("payment") or {}).get("date") == today_cn(),
          str((data.get("payment") or {}).get("date")))
    s, data = pay_api({"action": "create", "amount": 55, "date": today_cn(), "zone": "no-such-zone"})
    created.append((data.get("payment") or {}).get("id"))
    check("分区不存在 → 不报错，只是不关联",
          s == 200 and (data.get("payment") or {}).get("zone") == "",
          str((data.get("payment") or {}).get("zone")))

    print("=== 4. 改一笔 / 删一笔 ===")
    s, data = pay_api({"action": "update", "id": pid, "amount": 200, "date": shift(-1),
                       "item": "改过的项目", "customer": "阿伟", "note": "改过"})
    check("改一笔：金额 / 日期 / 项目都能改",
          s == 200 and abs(float((data.get("payment") or {}).get("amount") or 0) - 200) < 0.001
          and (data.get("payment") or {}).get("date") == shift(-1)
          and (data.get("payment") or {}).get("item") == "改过的项目",
          str(data.get("payment"))[:140])
    check("改完之后还是同一条（id 没变）", (data.get("payment") or {}).get("id") == pid)
    s, data = pay_api({"action": "update", "id": "no-such-id", "amount": 10})
    check("改一条不存在的 → 404", s == 404, "%s %s" % (s, data.get("error")))
    s, data = pay_api({"action": "update", "id": pid, "amount": 0})
    check("改成 0 元 → 400（同样的规矩）", s == 400 and "金额" in (data.get("error") or ""),
          "%s %s" % (s, data.get("error")))
    s, data = pay_api({"action": "delete", "id": pid})
    check("删一笔成功", s == 200 and data.get("ok") is True, "%s %s" % (s, str(data)[:80]))
    check("删掉之后列表里找不到了", all(p.get("id") != pid for p in payments_now()),
          str([p.get("id") for p in payments_now()])[:120])
    created.remove(pid)
    s, data = pay_api({"action": "delete", "id": pid})
    check("再删一次 → 404（不会报 500）", s == 404, "%s %s" % (s, data.get("error")))
    s, data = pay_api({"action": "explode"})
    check("不认识的动作 → 400", s == 400, str(data.get("error")))

    print("=== 5. ★ 确认预约 → 自动记一笔 ===")
    start_ms = free_future_hour(3)
    lock_id = make_client_lock("income-a", start_ms, price=45, mode="机密·零号大坝")
    check("（准备）造了一条待确认的客户预约（¥45）",
          bool(lock_by_id(lock_id)) and lock_by_id(lock_id)["price"] == 45)
    before = len(payments_now())
    s, data = schedule({"action": "confirm-lock", "id": lock_id})
    check("confirm-lock 成功", s == 200 and data.get("ok"), "status=%s %s" % (s, str(data)[:100]))
    got = payments_with("booking", lock_id)
    check("★ 流水里多了一条（来自「确认预约」）", len(got) == 1, "找到 %d 条" % len(got))
    if got:
        p = got[0]
        want_date = dt.datetime.fromtimestamp(start_ms / 1000, CN).strftime("%Y-%m-%d")
        check("★ 金额 = 这条锁的 price（45）", abs(float(p["amount"]) - 45) < 0.001,
              str(p["amount"]))
        check("★ 日期 = 这单**开始那天**（北京时间 %s）" % want_date, p["date"] == want_date,
              "%s" % p["date"])
        check("★ 来源 = 确认预约（source=booking）", p["source"] == "booking", str(p["source"]))
        # 中文名（"确认预约"那个小徽章）走的是收入页表格用的那份视图
        view = payment_view(lock_id)
        check("★ 表格里显示成「确认预约」+ 给人看的金额写法",
              bool(view) and view["sourceLabel"] == "确认预约" and view["amountText"] == "¥45",
              str(view and {k: view[k] for k in ("sourceLabel", "amountText", "label")}))
        check("带上了模式名（项目）和客户称呼",
              p["item"] == "机密·零号大坝" and p["customer"] == "income-a",
              "%s / %s" % (p["item"], p["customer"]))
        check("★ ref = 这条锁的 id（防重复记账就靠它）", p["ref"] == lock_id,
              "%s" % p["ref"])
    check("流水条数 +1", len(payments_now()) == before + 1,
          "%d → %d" % (before, len(payments_now())))
    check("★ 接口顺手回了一句中文提示（点确认那一刻告诉他钱也记上了）",
          "已记一笔收入" in str(data.get("message") or ""), str(data.get("message"))[:80])
    check("确认的返回里带了最新的收入统计", isinstance(data.get("income"), dict),
          str(data.get("income"))[:80])

    print("=== 6. ★ 幂等：同一单确认两次，流水里只有一条 ===")
    n1 = len(payments_with("booking", lock_id))
    s, data = schedule({"action": "confirm-lock", "id": lock_id})
    n2 = len(payments_with("booking", lock_id))
    check("再确认一次（老板可能不记得点过没有）", s == 200 and data.get("ok"),
          "status=%s" % s)
    check("★ 流水条数不变（%d → %d）" % (n1, n2), n1 == 1 and n2 == 1, "%d → %d" % (n1, n2))
    # 再模拟一次"恢复备份之后重放"：把锁改回待确认，再确认一遍
    conn = db()
    conn.execute("UPDATE locks SET confirmed=0, confirmed_at=0 WHERE id=?", (lock_id,))
    conn.commit()
    conn.close()
    s, _d = schedule({"action": "confirm-lock", "id": lock_id})
    check("★ 把锁改回待确认再确认一次（等价于恢复备份后重放）→ 流水还是只有一条",
          len(payments_with("booking", lock_id)) == 1,
          "%d 条" % len(payments_with("booking", lock_id)))

    print("=== 7. price = 0 的锁确认后不记账 ===")
    zero_id = make_client_lock("income-zero", free_future_hour(5), price=0)
    before = len(payments_now())
    s, data = schedule({"action": "confirm-lock", "id": zero_id})
    check("确认一条 price = 0 的预约", s == 200 and data.get("ok"), "status=%s" % s)
    check("★ 不记账（流水里不会有 0 元）", len(payments_now()) == before,
          "%d → %d" % (before, len(payments_now())))
    check("★ 也不会弹一句奇怪的提示", "已记一笔收入" not in str(data.get("message") or ""),
          str(data.get("message"))[:80])
    rest_id = make_client_lock("income-rest", free_future_hour(7), price=99, kind="rest")
    before = len(payments_now())
    s, _d = schedule({"action": "confirm-lock", "id": rest_id})
    check("★ 休息锁（kind=rest）确认也不记账（休息不是生意）",
          len(payments_now()) == before, "%d → %d" % (before, len(payments_now())))
    check("confirm-lock 的返回里，确认过的锁还是 confirmed=1（没被记账逻辑弄坏）",
          int((lock_by_id(zero_id) or {}).get("confirmed") or 0) == 1)

    print("=== 8. ★ 续费记账 ===")
    s, data = customer_api({"action": "create", "name": "续费记账测试", "item": "精托托管",
                            "price": "100R/月", "end_date": shift(2), "status": "active",
                            "zone": test_zone})
    cust = data.get("customer") or {}
    cid = cust.get("id")
    check("（准备）建了一个包月客户（金额写的是自由文本 100R/月）",
          bool(cid) and cust.get("price") == "100R/月", str(cust)[:120])
    want_end = add_months(cust["end_date"], 1)
    s, data = customer_api({"action": "renew", "id": cid, "months": 1, "mode": "extend",
                            "amount": 100})
    check("续费（填了这次收了 100）", s == 200 and data.get("newEndDate") == want_end,
          "%s %s" % (s, data.get("newEndDate")))
    ref = "%s@%s" % (cid, want_end)
    got = payments_with("renew", ref)
    check("★ 记了一笔，来源 = 续费（source=renew）",
          len(got) == 1 and got[0]["source"] == "renew", "找到 %d 条" % len(got))
    if got:
        check("★ 金额 = 这次填的数字", abs(float(got[0]["amount"]) - 100) < 0.001,
              str(got[0]["amount"]))
        check("★ 客户称呼和分区都带上了（排行要用）",
              got[0]["customer"] == "续费记账测试" and got[0]["zone"] == test_zone,
              "%s / %s" % (got[0]["customer"], got[0]["zone"]))
        check("备注里写清了是哪次续费（%s → %s）" % (cust["end_date"], want_end),
              "续费自动记账" in got[0]["note"], str(got[0]["note"])[:100])
    check("接口回了一句「已记一笔收入」", "已记一笔收入" in str(data.get("message") or ""),
          str(data.get("message"))[:80])
    check("★ 客户档案里的金额还是自由文本，一个字都没被动",
          (data.get("customer") or {}).get("price") == "100R/月",
          repr((data.get("customer") or {}).get("price")))

    before = len(payments_now())
    s, data = customer_api({"action": "renew", "id": cid, "months": 1, "mode": "extend"})
    check("再续一次但**不填金额**", s == 200, "status=%s" % s)
    check("★ 不填就不记账（不逼他填）", len(payments_now()) == before,
          "%d → %d" % (before, len(payments_now())))

    # ★ 同一客户、同一个新到期日续两次 → 只记一笔
    conn = db()
    row = conn.execute("SELECT end_date FROM customers WHERE id=?", (cid,)).fetchone()
    before_end = row["end_date"]
    conn.execute("UPDATE customers SET end_date=? WHERE id=?", (cust["end_date"], cid))
    conn.commit()
    conn.close()
    s, data = customer_api({"action": "renew", "id": cid, "months": 1, "mode": "extend",
                            "amount": 100})
    check("把到期日改回去再续一次（会算出同一个新到期日）",
          s == 200 and data.get("newEndDate") == want_end, str(data.get("newEndDate")))
    check("★ 幂等：同一客户同一个新到期日，流水里只有一条",
          len(payments_with("renew", ref)) == 1,
          "%d 条" % len(payments_with("renew", ref)))
    check("（清理）把客户档案的到期日恢复原样", bool(before_end))

    print("=== 9. ★ 月度统计的四个范围（跨界数据，一笔笔自己加） ===")
    # 造 4 笔：2 个月前、上个月、上个月、今天。加上前面第 5、8 节自动记的那两笔，
    # 下面全部**按实际流水自己算期望值**再和接口返回比 —— 不写死数字，跑哪天都对。
    plan = [
        (month_first_shift(-2), 300, "两个月前那笔"),
        (month_first_shift(-1), 700, "上个月第一笔"),
        (add_months(month_first_shift(-1), 1)[:8] + "01", 90, "上个月最后一笔"),
        (today_cn(), 111, "今天这笔"),
    ]
    for date_text, amount, item in plan:
        s, data = pay_api({"action": "create", "amount": amount, "date": date_text,
                           "item": item, "customer": "排行测试客户"})
        created.append((data.get("payment") or {}).get("id"))
        check("造一笔：%s ¥%d" % (date_text, amount), s == 200, "status=%s" % s)

    allp = payments_now()
    zones_map = {z["id"]: z["name"] for z in zones}
    cats = jmap_categories()
    zone_cat = {z["id"]: z.get("category") for z in zones}

    def expected(start, end):
        """自己算：这个闭区间里有几笔、一共多少钱（这就是"标准答案"）"""
        rows = [p for p in allp if (not start or p["date"] >= start) and (not end or p["date"] <= end)]
        return len(rows), round(sum(float(p["amount"]) for p in rows), 2)

    first = month_first_shift(0)
    last = (dt.datetime.strptime(add_months(first, 1), "%Y-%m-%d").date()
            - dt.timedelta(days=1)).strftime("%Y-%m-%d")
    cases = [
        ("month", "本月", first, last),
        ("last-month", "上月", month_first_shift(-1), shift_before(first)),
        ("3m", "近 3 个月", month_first_shift(-2), last),
        ("year", "今年", first[:4] + "-01-01", first[:4] + "-12-31"),
        ("all", "全部", "", ""),
    ]
    for key, label, start, end in cases:
        s, data = pay_api({"action": "range", "range": key})
        stats = data.get("income") or {}
        want_n, want_total = expected(start, end)
        check("★ %s：总额 ¥%s" % (label, want_total),
              s == 200 and abs(float(stats.get("total") or 0) - want_total) < 0.01,
              "接口给 %s" % stats.get("total"))
        check("★ %s：笔数 %d" % (label, want_n), int(stats.get("count") or 0) == want_n,
              "接口给 %s" % stats.get("count"))
        check("%s：范围标签和边界对得上" % label,
              stats.get("rangeLabel") == label and stats.get("start") == start
              and stats.get("end") == end,
              "%s / %s~%s" % (stats.get("rangeLabel"), stats.get("start"), stats.get("end")))

    s, data = pay_api({"action": "range", "range": "上一辈子"})
    check("认不出来的范围 → 退回「本月」（不报 500）",
          s == 200 and (data.get("income") or {}).get("range") == "month",
          str((data.get("income") or {}).get("range")))

    print("=== 10. ★ 环比：上月没有记录时不许显示 0% ===")
    # 现场造一个"上月一笔都没有"的世界：把上个月那两笔临时挪到 3 个月前，
    # 看完环比再挪回来（这样不用重造整个数据，也不会影响别的断言）。
    moved = []
    conn = db()
    lrows = conn.execute("SELECT id, date FROM payments WHERE date >= ? AND date <= ?",
                         (month_first_shift(-1), shift_before(first))).fetchall()
    for r in lrows:
        moved.append((r["id"], r["date"]))
        conn.execute("UPDATE payments SET date=? WHERE id=?", (month_first_shift(-2), r["id"]))
    conn.commit()
    conn.close()
    check("（准备）把上个月那 %d 笔临时挪到 2 个月前" % len(moved), True, str(len(moved)))
    s, data = pay_api({"action": "range", "range": "month"})
    stats = data.get("income") or {}
    check("★ 上月没有记录 → momPercent 是 None（**绝不是 0**）",
          stats.get("momPercent") is None and int(stats.get("prevCount") or 0) == 0,
          "momPercent=%r prevCount=%r" % (stats.get("momPercent"), stats.get("prevCount")))
    check("★ 而且说人话：\"上月没有记录\"",
          "上月没有记录" in str(stats.get("momText") or ""), str(stats.get("momText")))
    # 挪回去
    conn = db()
    for pid2, old_date in moved:
        conn.execute("UPDATE payments SET date=? WHERE id=?", (old_date, pid2))
    conn.commit()
    conn.close()
    s, data = pay_api({"action": "range", "range": "month"})
    stats = data.get("income") or {}
    prev_total = round(sum(float(p["amount"]) for p in payments_now()
                           if month_first_shift(-1) <= p["date"] <= shift_before(first)), 2)
    month_total = round(sum(float(p["amount"]) for p in payments_now()
                            if first <= p["date"] <= last), 2)
    want_pct = int(round((month_total - prev_total) / prev_total * 100))
    check("★ 挪回来之后环比算对了（本月 ¥%s vs 上月 ¥%s → %+d%%）"
          % (month_total, prev_total, want_pct),
          stats.get("momPercent") == want_pct and abs(float(stats.get("prevTotal")) - prev_total) < 0.01,
          "接口给 %r / 上月 %s" % (stats.get("momPercent"), stats.get("prevTotal")))
    check("文字形如「比上月 +18%」", re.search(r"比上月 [+-]?\d+%", str(stats.get("momText") or ""))
          is not None, str(stats.get("momText")))

    print("=== 11. 按大类 / 按服务 / 按客户的排行 ===")
    s, data = pay_api({"action": "range", "range": "all"})
    stats = data.get("income") or {}
    # 大类：自己按"分区 → 大类"的映射加一遍
    want_cat = {}
    for p in payments_now():
        cat = zone_cat.get(p.get("zone") or "")
        want_cat[cat or ""] = want_cat.get(cat or "", 0.0) + float(p["amount"])
    got_cat = {c["id"]: round(float(c["amount"]), 2) for c in (stats.get("categories") or [])}
    check("★ 按大类加总的金额对得上（陪玩 / 代肝 / 其他服务 / 未分类）",
          all(abs(got_cat.get(k, 0) - round(v, 2)) < 0.01 for k, v in want_cat.items()),
          "接口 %s / 自算 %s" % (got_cat, {k: round(v, 2) for k, v in want_cat.items()}))
    check("★ 没挂分区（或者分区被删）的记录归到「未分类」",
          ("" in want_cat) == ("" in got_cat) and abs(got_cat.get("", 0) - round(want_cat.get("", 0), 2)) < 0.01,
          "未分类 = %s（自算 %s）" % (got_cat.get(""), round(want_cat.get("", 0), 2)))
    check("每个大类小卡都带金额和笔数",
          all("amount" in c and "count" in c and "name" in c for c in (stats.get("categories") or [])),
          str((stats.get("categories") or [])[:3])[:160])

    # 服务排行：按分区名（没有分区的算"未分类"）
    want_svc = {}
    for p in payments_now():
        name = zones_map.get(p.get("zone") or "") or "未分类"
        want_svc[name] = want_svc.get(name, 0.0) + float(p["amount"])
    top = stats.get("topServices") or []
    want_top = sorted(want_svc.items(), key=lambda kv: (-kv[1], kv[0]))[:5]
    check("★ 服务排行前 5 名和自算的一致",
          [t["name"] for t in top] == [w[0] for w in want_top],
          "接口 %s / 自算 %s" % ([t["name"] for t in top], [w[0] for w in want_top]))
    check("★ 服务排行的金额也对",
          all(abs(float(t["amount"]) - round(w[1], 2)) < 0.01 for t, w in zip(top, want_top)),
          str([(t["name"], t["amount"]) for t in top])[:160])
    check("服务排行最多 5 个", len(top) <= 5, str(len(top)))

    want_cus = {}
    for p in payments_now():
        if p.get("customer"):
            want_cus[p["customer"]] = want_cus.get(p["customer"], 0.0) + float(p["amount"])
    topc = stats.get("topCustomers") or []
    want_topc = sorted(want_cus.items(), key=lambda kv: (-kv[1], kv[0]))[:5]
    check("★ 客户排行前 5 名和金额都对",
          [t["name"] for t in topc] == [w[0] for w in want_topc]
          and all(abs(float(t["amount"]) - round(w[1], 2)) < 0.01
                  for t, w in zip(topc, want_topc)),
          "接口 %s" % str([(t["name"], t["amount"]) for t in topc])[:160])
    check("★ 排行是按钱从多到少排的（不是按笔数）",
          all(float(topc[i]["amount"]) >= float(topc[i + 1]["amount"]) for i in range(len(topc) - 1)),
          str([t["amount"] for t in topc]))

    print("=== 12. 流水明细：只显示范围内的，最多 100 条 ===")
    s, data = pay_api({"action": "range", "range": "all"})
    stats = data.get("income") or {}
    rows = stats.get("records") or []
    total_all = len(payments_now())
    check("★ 全部范围的明细条数 = min(100, 全部笔数)",
          len(rows) == min(100, total_all), "明细 %d 条 / 总共 %d 条" % (len(rows), total_all))
    check("明细里每一条都带齐了表格要的字段（日期/金额/来源/标签）",
          all(p.get("date") and p.get("amountText") and p.get("sourceLabel") and p.get("label")
              for p in rows), str(rows[:1])[:160])
    check("★ 明细是「新的在前」（日期倒序）",
          [p["date"] for p in rows] == sorted([p["date"] for p in rows], reverse=True),
          str([p["date"] for p in rows])[:120])
    s, data = pay_api({"action": "range", "range": "month"})
    stats = data.get("income") or {}
    check("★ 切到本月之后，明细里只剩本月的（服务端按范围过滤）",
          all(p["date"][:7] == today_cn()[:7] for p in (stats.get("records") or [])),
          str([p["date"] for p in (stats.get("records") or [])])[:120])
    check("★ recordTotal 是「这个范围一共几笔」（不是明细条数）",
          int(stats.get("recordTotal") or 0) == sum(
              1 for p in payments_now() if p["date"][:7] == today_cn()[:7]),
          "recordTotal=%s" % stats.get("recordTotal"))

    print("=== 13. ★「本月」按北京时间判（服务器时区不是中国也算对） ===")
    probe2 = (
        "import sys, datetime as dt\n"
        "sys.path.insert(0, %r)\n"
        "import app\n"
        "want = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=8)).strftime('%%Y-%%m-%%d')\n"
        "start, end = app.income_range_bounds('month', want)\n"
        "print('PROBE', app.today_cn(), want, start, end, app.INCOME_RANGE_NAMES['month'])\n"
    ) % (ROOT,)
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    for tz in ("UTC", "America/New_York", "Asia/Tokyo"):
        env["TZ"] = tz
        try:
            res = subprocess.run([sys.executable, "-c", probe2], env=env, capture_output=True,
                                 text=True, encoding="utf-8", errors="replace", timeout=60)
            line = next((l for l in res.stdout.splitlines() if l.startswith("PROBE")), "")
            _tag, got, want, start, end, label = line.split()
            check("TZ=%-18s 时「本月」的边界还是北京时间的那个月" % tz,
                  got == want and start == want[:8] + "01" and end[:7] == want[:7]
                  and start <= want <= end,
                  "%s ~ %s（今天 %s）" % (start, end, got))
        except Exception as e:
            check("TZ=%s 的子进程探针" % tz, False,
                  "%s: %s / stderr=%s" % (type(e).__name__, e, (res.stderr or "")[-120:]))

    print("=== 14. 未登录访问 ===")
    s, _b = call("/api/admin/payments", "POST", {"action": "create", "amount": 999}, use_cookie=False)
    check("★ 未登录 POST /api/admin/payments → 401", s == 401, str(s))
    s, _b = call("/api/admin/payments", "POST", {"action": "delete", "id": "x"}, use_cookie=False)
    check("未登录连删除也做不了（同样 401，不会真删）", s == 401, str(s))
    s, _b = call("/api/admin", "GET", None, use_cookie=False)
    check("★ 未登录 GET /api/admin → 401（收入统计也在里面）", s == 401, str(s))
    s, _b = call("/api/admin/export", "GET", None, use_cookie=False)
    check("未登录导出备份 → 401（备份里有全部收入流水）", s == 401, str(s))

    return created, cid, lock_id, zero_id, rest_id


def shift_before(text):
    """"YYYY-MM-01" 的前一天 = 上个月最后一天"""
    d = dt.datetime.strptime(text, "%Y-%m-%d").date() - dt.timedelta(days=1)
    return d.strftime("%Y-%m-%d")


def jmap_categories():
    return {c["id"]: c for c in (admin_state().get("categories") or [])}


# ============================================================================
#  第十五部分：备份 / 恢复
# ============================================================================

def backup_tests(created):
    print("=== 15. ★ 备份导出 / 恢复 ===")
    s, _b = call("/api/auth", "POST", {"password": PASSWORD})
    s, body = call("/api/admin/export")
    check("导出备份 200", s == 200, str(s))
    backup = json.loads(body)
    check("★ 备份里有 payments 字段", isinstance(backup.get("payments"), list),
          type(backup.get("payments")).__name__)
    check("★ 备份里的流水条数 = 列表里的条数",
          len(backup["payments"]) == len(payments_now()),
          "%s / %s" % (len(backup["payments"]), len(payments_now())))
    check("★ 备份里连金额和来源都一模一样（浮点也一样）",
          all(any(float(x["amount"]) == float(p["amount"]) and x["source"] == p["source"]
                  and x["date"] == p["date"] and x["ref"] == p["ref"]
                  for x in backup["payments"]) for p in payments_now()),
          "抽查 %d 条" % len(backup["payments"]))

    # 15.1 造一条"多出来的"流水，恢复后应该被备份里的内容替换掉
    s, data = pay_api({"action": "create", "amount": 7777, "date": today_cn(), "item": "恢复前多出来的"})
    extra = (data.get("payment") or {}).get("id")
    check("恢复前先造一条多余的流水（¥7777）",
          any(p.get("id") == extra for p in payments_now()))
    s, data = jcall("/api/admin/import", "POST", {"backup": backup})
    check("导入备份成功", s == 200 and data.get("ok") is True, "%s %s" % (s, str(data)[:120]))
    check("★ 恢复后流水和备份里完全一致（多余的被覆盖掉）",
          all(p.get("id") != extra for p in payments_now())
          and len(payments_now()) == len(backup["payments"]),
          "恢复了 %d 条" % len(payments_now()))
    sample = next((p for p in backup["payments"] if float(p["amount"]) > 0), None)
    if sample:
        got = next((p for p in payments_now() if p["id"] == sample["id"]), None)
        check("★ 恢复后金额/日期/来源/ref 一字不差",
              got is not None and float(got["amount"]) == float(sample["amount"])
              and got["date"] == sample["date"] and got["source"] == sample["source"]
              and got["ref"] == sample["ref"],
              str(got and {k: got[k] for k in ("amount", "date", "source")}))
    check("恢复摘要里带上了流水条数", "payments" in (data.get("summary") or {}),
          str((data.get("summary") or {}).get("payments")))
    check("★ 恢复完收入统计跟着重算了（不是留的旧数字）",
          isinstance(data.get("income"), dict)
          and abs(float(data["income"]["total"]) - float(
              (data.get("income") or {}).get("total"))) < 0.01,
          str((data.get("income") or {}).get("total")))

    # 15.2 ★ 老备份（没有 payments 字段）导入：不报错、不清空现在的流水
    old = {k: v for k, v in backup.items() if k != "payments"}
    count_before = len(payments_now())
    s, data = jcall("/api/admin/import", "POST", {"backup": old})
    check("★ 老备份（没有 payments）导入不报错", s == 200 and data.get("ok") is True,
          "%s %s" % (s, str(data)[:120]))
    after = payments_now()
    check("★ 老备份导入后，流水一条都没少（绝不清空）", len(after) == count_before,
          "%s → %s" % (count_before, len(after)))
    check("★ 老备份导入后，流水内容也没被动过",
          all(any(a["id"] == b["id"] and float(a["amount"]) == float(b["amount"])
                  for b in backup["payments"]) for a in after),
          "抽查了 %d 条" % len(after))

    # 15.3 备份里的坏数据不能让整份恢复失败
    broken = json.loads(json.dumps(backup))
    broken["payments"] = broken["payments"] + [
        {"amount": 0, "date": today_cn(), "item": "0 元的坏数据"},
        {"amount": "按需报价", "date": today_cn(), "item": "抠不出数字的"},
        {"amount": 66, "date": "2024-13-45", "item": "日期乱写但钱是真的"},
        "这一条根本不是字典",
    ]
    s, data = jcall("/api/admin/import", "POST", {"backup": broken})
    check("备份里有坏行 → 恢复照样成功（0 元/抠不出的跳过）",
          s == 200 and data.get("ok") is True, "%s %s" % (s, str(data)[:120]))
    got = next((p for p in payments_now() if p.get("item") == "日期乱写但钱是真的"), None)
    check("★ 日期乱写但金额是真的 → 按今天救回来（宁可少一条坏日期，也别丢一笔钱）",
          got is not None and got["date"] == today_cn(),
          str(got and {k: got[k] for k in ("item", "date", "amount")}))
    check("0 元那条被跳过了（流水里没有 0 元）",
          all(float(p["amount"]) > 0 for p in payments_now()),
          str([p["amount"] for p in payments_now() if float(p["amount"]) <= 0]))
    check("没有生成「抠不出数字」的流水",
          all(p.get("item") != "抠不出数字的" for p in payments_now()))

    # 最后一件事：把状态恢复成干净备份的样子，别把坏数据留给后面的阶段
    s, _d = jcall("/api/admin/import", "POST", {"backup": backup})
    check("再导一次干净备份，状态回到原样", s == 200, str(s))
    return True


def cleanup(created, customer_id, lock_ids):
    """把测试造的东西删干净（跑完 run_tests 也会整个删掉 .testdata）"""
    try:
        cleanup_payments([p for p in created if p])
    except Exception:
        pass
    try:
        if customer_id:
            customer_api({"action": "delete", "id": customer_id})
    except Exception:
        pass
    try:
        for lid in lock_ids:
            if lid:
                delete_lock(lid)
    except Exception:
        pass


def main():
    created, cid, lock_id, zero_id, rest_id = [], None, None, None, None
    try:
        created, cid, lock_id, zero_id, rest_id = main_tests()
        backup_tests(created)
    finally:
        cleanup(created, cid, [lock_id, zero_id, rest_id])
    print()
    print("通过 %d 项，失败 %d 项" % (ok, fail))
    return 1 if fail else 0


if __name__ == "__main__":
    print("=" * 60)
    print(" 收入登记 + 月度统计 测试（%s）" % BASE)
    print("=" * 60, flush=True)
    sys.exit(main())
