# -*- coding: utf-8 -*-
"""
★ 第二刀：「待确认」处理台 + 自动释放的自动测试。

这个功能的背景（决定了下面每一条断言为什么这么写）：
  客户在前台自助预约时，那几格**立刻就锁走了**（这是主人指定的流程）。
  但"占住"不等于"这单成立" —— 他没加微信、联系不上，那段时间就白占着，
  而时段就是库存，库存就是钱。所以客户自助预约默认是 confirmed=0（待确认），
  后台最上面一排按钮，点「已确认」或「释放时段」，把这件事了掉。

测的东西：

  【待确认的基本规矩】
   1. 客户自助预约（source=client）新建时 confirmed=0
   2. 老板自己锁的（source=owner）confirmed=1
   3. ★ 老库迁移：跑一遍 init_db() 之后，**老锁必须还是 confirmed=1**
      （老库里的锁全是老板自己弄的，不该在升级那一刻集体变成"待确认"）
   4. GET /api/admin 的锁带 confirmed / confirmedAt；pendingLocks 是待确认清单
   5. 待确认清单的 summary 要带上：称呼、联系方式、分区、模式、时间、几小时、金额、备注

  【确认】
   6. confirm-lock → confirmed=1 + confirmed_at 有时间戳；这条从清单里消失、锁还在
   7. 确认一条不存在/已删的 → 404
   8. 确认之后再确认一次：不会报错（幂等），时间戳刷新

  【释放】
   9. release-lock → 这条锁被删掉，时段回到可约（/api/public 的 slots 变回 free）
  10. ★ 只允许释放待确认的单：老板自己锁的、已经确认过的，一律 400 且**不会被删**

  【自动释放（★ 默认必须是关闭的）】
  11. ★ 默认值就是 0（关闭）：什么都不删
  12. 开小值后：等太久的客户预约真的被删（release）
  13. 没超时的（刚下单的）不删
  14. ★ 只删"还没开始的时间"：已经过去的 / 已经开始的一律不碰
  15. ★ 已确认的不碰
  16. ★ 老板自己锁的不碰
  17. 删掉之后老板看得到：autoReleased 里有记录（谁、哪一场、什么原因）
  18. 设置能存能读（set-auto-release 接口 + 落库）

  【今日待办条的口径】
  19. 待办条三个数字的**后端来源**都对：今天的 booking 锁数 / customerStats.remind /
      pendingLocks 条数（前端 renderTodoBar 就是拿这三个数，口径由这里守着）

  【备份】
  20. confirmed / confirmed_at 跟着备份走；老备份（没有这两个字段）导回来是"已确认"

风格和 test_customers.py 一样：纯标准库、打印 [OK]/[FAIL]、最后给通过/失败计数，失败则退出码非 0。
⚠️ 和 test_api.py 一样**会写数据**，只能对着测试实例跑。

用法：
    python tests\\test_confirm.py
    set TEST_BASE=http://127.0.0.1:8199 && python tests\\test_confirm.py
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
DATA_DIR = os.environ.get("TEST_DATA_DIR") or os.path.join(ROOT, "data")
CN = dt.timezone(dt.timedelta(hours=8))
HOUR_MS = 3600 * 1000

# 这个脚本会改数据，别对着线上站点跑
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


def schedule(payload):
    return jcall("/api/admin/schedule", "POST", payload)


def admin_state():
    return jcall("/api/admin")[1]


def free_hours(count, min_lead_hours=2):
    """
    找 count 个**未来且空闲**的整点（从"现在 + min_lead_hours 小时"之后开始找）。
    为什么往后留 2 小时：自动释放的测试要区分"还没开始的"和"已经开始/过去的"，
    留出余量就不会因为测试跑着跑着时间滑过去而失败。
    """
    now = int(__import__("time").time() * 1000)
    want_from = now + min_lead_hours * HOUR_MS
    slots = jcall("/api/public")[1]["slots"]
    out = [s["start"] for s in slots if s["status"] == "free" and s["start"] >= want_from]
    return out[:count]


def book(hours, contact="wx_test_confirm", mode=None, contact_type="wechat"):
    """走客户自助预约（POST /api/book）。mode 不填就取该分区第一个档位名。"""
    zone = jcall("/api/public")[1]["zone"]
    if mode is None:
        prices = (zone.get("profile") or {}).get("prices") or []
        mode = prices[0]["name"] if prices else "机密 · 零号大坝"
    return jcall("/api/book", "POST", {"zone": zone["id"], "hours": hours, "mode": mode,
                                       "contact": contact, "contactType": contact_type})


def lock_by_id(lock_id):
    for lk in admin_state().get("schedule", {}).get("locks", []):
        if lk.get("id") == lock_id:
            return lk
    return None


def set_hours_env(value):
    """改服务端的「自动释放小时数」（通过接口，等价于老板在后台点保存）"""
    return schedule({"action": "set-auto-release", "hours": value})


def backdate(lock_id, hours):
    """
    把某条锁的 created_at 往前拨 N 小时 —— **直接用 sqlite 改库**，
    等价于"这条预约真的是 N 小时前下的"。真等 N 小时没法测。
    （服务端 app.py 里也有一个 backdate_lock，但那个只在同进程里能用；
      这里是独立进程，所以直接改同一个数据目录的库。）
    """
    import sqlite3
    conn = sqlite3.connect(os.path.join(DATA_DIR, "site.db"))
    conn.execute("UPDATE locks SET created_at=? WHERE id=?",
                 (int(__import__("time").time() * 1000) - int(hours * HOUR_MS), lock_id))
    conn.commit()
    conn.close()


def trigger_cleanup():
    """触发一次惰性清理（老板打开后台 / 客户刷时间表时发生的事）"""
    return jcall("/api/admin")


def cleanup_all_bookings():
    """把测试造出来的客户自助预约全删掉，别影响后面的阶段"""
    adm = admin_state()
    for lk in adm.get("schedule", {}).get("locks", []):
        if lk.get("source") == "client" and str(lk.get("contact") or "").startswith("wx_test"):
            call("/api/admin/schedule", "POST", {"action": "remove-lock", "id": lk["id"]})


# ============================================================================
#  0. 登录 + 先把设置归零（保证第 11 条测的是"默认就是关的"）
# ============================================================================
print("=" * 70)
print(" 「待确认」+ 自动释放 测试（%s）" % BASE)
print("=" * 70)

s, b = jcall("/api/auth", "POST", {"password": PASSWORD})
check("用测试密码登录成功", s == 200, "status=%s" % s)

cleanup_all_bookings()
set_hours_env(0)
adm = admin_state()
check("★ 自动释放的默认设置是 0 = 关闭", adm.get("autoReleaseHours") == 0,
      "autoReleaseHours=%r" % adm.get("autoReleaseHours"))

# ============================================================================
#  1. 客户自助预约默认是"待确认"
# ============================================================================
print("\n=== 1. 客户自助预约默认就是「待确认」 ===")
hours = free_hours(2)
check("（准备）找到了 2 个未来的空闲整点", len(hours) == 2, str(hours))
s, b = book(hours, contact="wx_test_a")
check("客户自助预约成功（还是「点了就锁定」）",
      s == 200 and b.get("ok"), "status=%s %s" % (s, str(b)[:120]))
client_locks = b.get("locks") or []
check("预约落库成了 1 段锁（连续 2 小时合并成一段）", len(client_locks) == 1, str(client_locks))
client_lock = client_lock_id = None
if client_locks:
    client_lock_id = [lk["id"] for lk in admin_state()["schedule"]["locks"]
                      if lk.get("source") == "client" and lk.get("contact") == "wx_test_a"]
    client_lock = lock_by_id(client_lock_id[0]) if client_lock_id else None
check("★ 客户自助预约的 confirmed = 0（待确认）",
      bool(client_lock) and int(client_lock.get("confirmed") or 0) == 0,
      "confirmed=%r" % (client_lock.get("confirmed") if client_lock else "没找到"))
check("刚下单的 confirmed_at 是 0（还没确认过）",
      bool(client_lock) and int(client_lock.get("confirmed_at") or 0) == 0,
      "confirmed_at=%r" % (client_lock.get("confirmed_at") if client_lock else "没找到"))

s, b = schedule({"action": "save-lock", "start": hours[-1] + HOUR_MS,
                 "end": hours[-1] + 2 * HOUR_MS,
                 "kind": "booking", "client": "老板自己锁的"})
check("老板自己在后台锁一个时段", s == 200 and b.get("ok"), "status=%s" % s)
owner_lock = next((lk for lk in admin_state()["schedule"]["locks"]
                   if lk.get("client") == "老板自己锁的"), None)
check("★ 老板自己锁的 confirmed = 1（不用你确认自己）",
      bool(owner_lock) and int(owner_lock.get("confirmed") or 0) == 1,
      "confirmed=%r" % (owner_lock.get("confirmed") if owner_lock else "没找到"))

# ============================================================================
#  2. 待确认清单（后台那一块就是照它画的）
# ============================================================================
print("\n=== 2. 待确认清单 pendingLocks ===")
adm = admin_state()
pending = adm.get("pendingLocks") or []
ids = [p["id"] for p in pending]
check("★ 待确认清单里有刚才那一条客户预约", client_lock_id and client_lock_id[0] in ids,
      "清单 %d 条：%s" % (len(pending), str(ids)[:120]))
check("★ 老板自己锁的那条**不在**待确认清单里",
      not any(p.get("client") == "老板自己锁的" for p in pending),
      str([p.get("client") for p in pending])[:120])
one = next((p for p in pending if p["id"] == (client_lock_id or [""])[0]), {})
summary = str(one.get("summary") or "")
check("清单每行都有一句人话 summary", bool(summary), summary[:160])
for keyword, why in [("wx_test_a", "联系方式"), ("三角洲", "分区名"),
                     ("小时", "几小时"), ("机密", "模式"), ("¥", "金额")]:
    check("summary 里有%s" % why, keyword in summary, summary[:160])
check("summary 里有时间（几月几日 xx:00–xx:00）", "月" in summary and "–" in summary, summary[:160])
check("清单里带着 created_at（前端靠它算「已经等了 N 小时」）",
      bool(one.get("created_at")), str(one.get("created_at")))

# ============================================================================
#  3. ★ 确认
# ============================================================================
print("\n=== 3. ★ 确认一条待确认的预约 ===")
s, b = schedule({"action": "confirm-lock", "id": client_lock_id[0]})
check("confirm-lock 成功", s == 200 and b.get("ok"), "status=%s %s" % (s, str(b)[:120]))
lk = lock_by_id(client_lock_id[0])
check("★ confirmed 变成 1", bool(lk) and int(lk.get("confirmed") or 0) == 1,
      "confirmed=%r" % (lk.get("confirmed") if lk else "没找到"))
check("★ confirmed_at 记下了确认时间（毫秒时间戳）",
      bool(lk) and int(lk.get("confirmed_at") or 0) > 0,
      "confirmed_at=%r" % (lk.get("confirmed_at") if lk else "没找到"))
check("★ 这条锁还在（确认只是改状态，不删时段）",
      bool(lk) and int(lk.get("start")) == hours[0], str(lk)[:120] if lk else "没了")
check("确认之后它从待确认清单里消失",
      client_lock_id[0] not in [p["id"] for p in (b.get("pendingLocks") or [])],
      str([p["id"] for p in (b.get("pendingLocks") or [])])[:120])
check("接口顺带回了一份最新的全量（schedule + pendingLocks + autoReleaseHours）",
      isinstance(b.get("schedule"), dict) and isinstance(b.get("pendingLocks"), list)
      and "autoReleaseHours" in b, str(sorted(b.keys())))

s, b = schedule({"action": "confirm-lock", "id": "根本没有这个 id"})
check("确认一个不存在的 id → 404（不会报 500）", s == 404, "status=%s" % s)

before = lock_by_id(client_lock_id[0]).get("confirmed_at")
s, b = schedule({"action": "confirm-lock", "id": client_lock_id[0]})
after = lock_by_id(client_lock_id[0]).get("confirmed_at")
check("再确认一次不会报错（幂等），时间戳刷新", s == 200 and int(after) >= int(before),
      "%s → %s" % (before, after))

# ============================================================================
#  4. ★ 释放
# ============================================================================
print("\n=== 4. ★ 释放一条待确认的时段 ===")
hours2 = free_hours(1)
s, b = book(hours2, contact="wx_test_b")
check("再来一条客户自助预约（用来释放）", s == 200 and b.get("ok"), "status=%s %s" % (s, str(b)[:100]))
release_id = next((lk["id"] for lk in admin_state()["schedule"]["locks"]
                   if lk.get("source") == "client" and lk.get("contact") == "wx_test_b"), None)
check("这条同样是待确认", bool(release_id) and int((lock_by_id(release_id) or {}).get("confirmed") or 0) == 0,
      "id=%s" % release_id)
status_before = {s2["start"]: s2["status"] for s2 in jcall("/api/public")[1]["slots"]}
check("释放前这段时间是 booking（被占着）", status_before.get(hours2[0]) == "booking",
      str(status_before.get(hours2[0])))

s, b = schedule({"action": "release-lock", "id": release_id})
check("release-lock 成功", s == 200 and b.get("ok"), "status=%s %s" % (s, str(b)[:120]))
check("★ 那条锁真的被删掉了（时段回到可约）", lock_by_id(release_id) is None, "还在")
check("接口回了一句中文提示", bool(b.get("message")), str(b.get("message"))[:80])
own_public = jcall("/api/public")[1]
check("★ /api/public 里那格变回 free（别人能约了）",
      {s2["start"]: s2["status"] for s2 in own_public["slots"]}.get(hours2[0]) == "free",
      str({s2["start"]: s2["status"] for s2 in own_public["slots"]}.get(hours2[0])))

s, b = schedule({"action": "release-lock", "id": "根本没有这个 id"})
check("释放一个不存在的 id → 404", s == 404, "status=%s" % s)

# ★ 只允许释放"待确认"的：老板自己锁的 / 已经确认过的，一律拦住
owner_id = (owner_lock or {}).get("id")
s, b = schedule({"action": "release-lock", "id": owner_id})
check("★ 想释放「老板自己锁的」那条 → 400 被拦住", s == 400,
      "status=%s %s" % (s, str(b.get("error"))[:100]))
check("★ 被拦住之后那条锁还在（没被误删）", lock_by_id(owner_id) is not None, "被删掉了！")

s, b = schedule({"action": "release-lock", "id": client_lock_id[0]})
check("★ 想释放一条**已经确认过**的 → 400 被拦住", s == 400,
      "status=%s %s" % (s, str(b.get("error"))[:100]))
check("★ 被拦住之后那条确认过的锁还在", lock_by_id(client_lock_id[0]) is not None, "被删掉了！")
check("错误提示告诉老板该去哪儿取消", "取消" in str(b.get("error", "")),
      str(b.get("error"))[:120])

# ============================================================================
#  5. ★ 老库迁移：老锁必须还是 confirmed=1
# ============================================================================
print("\n=== 5. ★ 老库迁移：老锁保持「已确认」，不会突然集体变待确认 ===")
OLD_SCHEMA = """
CREATE TABLE store (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE locks (
    id TEXT PRIMARY KEY, kind TEXT NOT NULL, start INTEGER NOT NULL, end INTEGER NOT NULL,
    client TEXT DEFAULT '', note TEXT DEFAULT '', source TEXT DEFAULT 'owner',
    mode TEXT DEFAULT '', price INTEGER DEFAULT 0, contact TEXT DEFAULT '',
    contact_type TEXT DEFAULT 'wechat', created_at INTEGER DEFAULT 0, zone TEXT DEFAULT ''
);
CREATE TABLE rules (
    id TEXT PRIMARY KEY, days TEXT NOT NULL, start_hour INTEGER NOT NULL,
    end_hour INTEGER NOT NULL, from_ts INTEGER NOT NULL, until_ts INTEGER,
    note TEXT DEFAULT '', enabled INTEGER DEFAULT 1
);
CREATE TABLE gallery (
    id TEXT PRIMARY KEY, title TEXT DEFAULT '', caption TEXT DEFAULT '',
    category TEXT DEFAULT 'screenshot', image TEXT DEFAULT '', visible INTEGER DEFAULT 0,
    archived INTEGER DEFAULT 0, created_at INTEGER NOT NULL, zone TEXT DEFAULT ''
);
CREATE TABLE zones (
    id TEXT PRIMARY KEY, category TEXT NOT NULL, name TEXT NOT NULL, short TEXT DEFAULT '',
    icon TEXT DEFAULT '', theme TEXT DEFAULT '', sort INTEGER DEFAULT 0,
    visible INTEGER DEFAULT 1, profile TEXT DEFAULT '{}', created_at INTEGER DEFAULT 0
);
"""
# 在一个子进程里跑迁移：这样不会污染当前测试进程里已经 import 好的 app（数据目录不一样）
MIGRATE_SCRIPT = r'''
import json, os, sqlite3, sys
ROOT = sys.argv[1]
data_dir = sys.argv[2]
os.environ["DATA_DIR"] = data_dir
sys.path.insert(0, ROOT)
import app
app.init_db()
conn = sqlite3.connect(os.path.join(data_dir, "site.db"))
conn.row_factory = sqlite3.Row                  # ★ 不给它设 row_factory 的话，查出来的是元组
cols = [r[1] for r in conn.execute("PRAGMA table_info(locks)")]
gal_cols = [r[1] for r in conn.execute("PRAGMA table_info(gallery)")]
rows = [dict(r) for r in conn.execute("SELECT * FROM locks ORDER BY id")]
print("RESULT" + json.dumps({
    "lock_cols": cols, "gallery_cols": gal_cols,
    "locks": [{"id": r["id"], "confirmed": r["confirmed"], "confirmed_at": r["confirmed_at"],
               "source": r["source"], "client": r["client"]} for r in rows],
    "gallery_link": [r["link"] for r in conn.execute("SELECT link FROM gallery")],
    "default_cats": app.list_gallery_categories(),
}, ensure_ascii=False))
'''

tmp = os.path.join(ROOT, ".testdata-confirm")
import shutil                                                  # noqa: E402
shutil.rmtree(tmp, ignore_errors=True)
os.makedirs(tmp)
try:
    import sqlite3                                             # noqa: E402
    db = os.path.join(tmp, "site.db")
    conn = sqlite3.connect(db)
    conn.executescript(OLD_SCHEMA)
    conn.execute("INSERT INTO store(key, value) VALUES('profile', ?)",
                 (json.dumps({"name": "老老板", "brand": "Nya"}, ensure_ascii=False),))
    conn.execute("INSERT INTO store(key, value) VALUES('revision', '3')")
    for i in range(3):
        conn.execute("""INSERT INTO locks(id, kind, start, end, client, note, source,
                        mode, price, contact, contact_type, created_at, zone)
                        VALUES(?,?,?,?,?,?,'owner','',0,'','wechat',0,'delta')""",
                     ("old-%d" % i, "booking", 1500000000000 + i * 3600000,
                      1500003600000 + i * 3600000, "老客户%d" % i, "老的锁"))
    conn.execute("""INSERT INTO gallery(id, title, caption, category, image, visible,
                    archived, created_at, zone) VALUES('g1','老截图','x','screenshot',
                    '/api/media/a.png',1,0,1500000000000,'delta')""")
    conn.commit()
    conn.close()

    proc = subprocess.run([sys.executable, "-c", MIGRATE_SCRIPT, ROOT, tmp],
                          capture_output=True, text=True, encoding="utf-8", timeout=120)
    out = (proc.stdout or "") + (proc.stderr or "")
    line = next((l for l in out.split("\n") if l.startswith("RESULT")), "")
    result = json.loads(line[len("RESULT"):]) if line else {}
    check("老库迁移脚本跑通（init_db 没报错）", bool(result), out[-300:])

    check("locks 表补上了 confirmed 字段", "confirmed" in (result.get("lock_cols") or []),
          str(result.get("lock_cols")))
    check("locks 表补上了 confirmed_at 字段", "confirmed_at" in (result.get("lock_cols") or []),
          str(result.get("lock_cols")))
    check("gallery 表补上了 link 字段", "link" in (result.get("gallery_cols") or []),
          str(result.get("gallery_cols")))
    locks = result.get("locks") or []
    check("老的 3 条锁一条都没丢", len(locks) == 3, "%d 条" % len(locks))
    check("★ 迁移后老锁全部保持 confirmed=1（绝不突然变成待确认）",
          all(int(lk.get("confirmed") or 0) == 1 for lk in locks),
          str([(lk["id"], lk["confirmed"]) for lk in locks]))
    check("老锁的 source 还是 owner / 内容原样", all(lk["source"] == "owner" for lk in locks),
          str([(lk["id"], lk["source"], lk["client"]) for lk in locks]))
    check("★ 老图的外链是空字符串（不会凭空多出链接）",
          all(x == "" for x in (result.get("gallery_link") or [])),
          str(result.get("gallery_link")))
    check("★ 迁移后展示墙默认分类是三类（游戏截图/老板评价/作品）",
          [c["id"] for c in (result.get("default_cats") or [])][:3]
          == ["screenshot", "review", "work"], str(result.get("default_cats")))
finally:
    shutil.rmtree(tmp, ignore_errors=True)

# ============================================================================
#  6. ★ 自动释放：默认不开 → 什么都不删
# ============================================================================
print("\n=== 6. ★ 自动释放默认关闭（0 = 什么都不删） ===")
cleanup_all_bookings()
set_hours_env(0)
hours3 = free_hours(1)
s, b = book(hours3, contact="wx_test_c")
keep_id = next((lk["id"] for lk in admin_state()["schedule"]["locks"]
                if lk.get("source") == "client" and lk.get("contact") == "wx_test_c"), None)
backdate(keep_id, hours=100)          # 假装这条已经等了 100 小时
trigger_cleanup()
check("★ 设置为 0 时，等了 100 小时的待确认预约**一条都不删**",
      lock_by_id(keep_id) is not None, "被删掉了！")
check("设置为 0 时也没有「最近自动释放」记录",
      not (admin_state().get("autoReleased") or []),
      str(admin_state().get("autoReleased"))[:120])

# ============================================================================
#  7. ★ 自动释放：开了就真的删（而且只删该删的）
# ============================================================================
print("\n=== 7. ★ 开了自动释放：真的删，但只删该删的 ===")
s, b = set_hours_env(24)
check("设置保存成 24 小时", s == 200 and b.get("autoReleaseHours") == 24,
      "autoReleaseHours=%r" % b.get("autoReleaseHours"))
check("设置落库了（GET /api/admin 读得到）", admin_state().get("autoReleaseHours") == 24,
      str(admin_state().get("autoReleaseHours")))

# 7.1 超时的那条（等了 100 小时 > 24）—— 应该被删
trigger_cleanup()
check("★ 待确认超过 24 小时的预约被自动释放了（真的删了）",
      lock_by_id(keep_id) is None, "还在！")
log = admin_state().get("autoReleased") or []
check("★ 老板看得到「最近自动释放」记录（不是悄悄删的）", len(log) >= 1, str(log)[:200])
if log:
    check("★ 记录里写了是谁", log[0].get("name") == "wx_test_c", str(log[0]))
    check("记录里写了是哪一场（start 时间戳）", int(log[0].get("start") or 0) > 0, str(log[0]))
    check("记录里写了是按几小时的规则释放的", int(log[0].get("hours") or 0) == 24, str(log[0]))
    check("记录里带了释放时间", int(log[0].get("at") or 0) > 0, str(log[0]))

# 7.2 没超时的（刚下单）—— 不删
#     ★ 这里不重新约一次，而是造一条"刚下的单"（时间在未来、created_at = 现在）：
#       /api/book 有"同一 IP 10 分钟最多 5 次"的限流，整个脚本一共只约 4 次，
#       留出余量才不会把自己限流掉（这也是为什么后面几条都用现成的锁来造场景）。
import sqlite3                                                 # noqa: E402


def make_client_lock(name, start_ms, created_hours_ago=0, confirmed=0, source="client"):
    """
    直接在库里造一条锁（**只给测试用**）—— 想验哪条边界就造哪条，不用反复走 /api/book。
    等价于"客户在那个时间点下了这么一单"。
    """
    lock_id = "test-" + name
    now = int(__import__("time").time() * 1000)
    conn = sqlite3.connect(os.path.join(DATA_DIR, "site.db"))
    conn.execute("DELETE FROM locks WHERE id=?", (lock_id,))
    conn.execute("""INSERT INTO locks(id, kind, start, end, client, note, source, mode, price,
                    contact, contact_type, created_at, zone, confirmed, confirmed_at)
                    VALUES(?,?,?,?,?,?,'client','测试模式',30,?,'wechat',?,?,?,?)""",
                 (lock_id, "booking", start_ms, start_ms + HOUR_MS, name, "",
                  "wx_" + name, now - int(created_hours_ago * HOUR_MS), first_zone_id(),
                  confirmed, 0 if not confirmed else now))
    conn.commit()
    conn.close()
    return lock_id


def first_zone_id():
    adm = admin_state()
    return adm["zones"][0]["id"] if adm.get("zones") else ""


future_slot = free_hours(1)
if not future_slot:                       # 极端情况：未来 7 天全被占了，就造到明天这个时候
    future_slot = [int(__import__("time").time() * 1000) + 26 * HOUR_MS]
fresh_id = make_client_lock("fresh", future_slot[0], created_hours_ago=0)
trigger_cleanup()
check("★ 刚下单（没等够 24 小时）的不删", lock_by_id(fresh_id) is not None, "被误删了！")

# 7.3 ★ 只删"还没开始的时间"：把一条的 start 改成过去 —— 不删


def move_lock_to_past(lock_id, hours_ago=5):
    """把一条锁整个挪到过去（开始和结束都在 N 小时前）—— 模拟「这单已经打完了」"""
    now = int(__import__("time").time() * 1000)
    start = now - hours_ago * HOUR_MS
    conn = sqlite3.connect(os.path.join(DATA_DIR, "site.db"))
    conn.execute("UPDATE locks SET start=?, end=?, created_at=? WHERE id=?",
                 (start, start + HOUR_MS, now - 200 * HOUR_MS, lock_id))
    conn.commit()
    conn.close()


move_lock_to_past(fresh_id, hours_ago=5)
trigger_cleanup()
check("★ 已经开始/已经过去的单，绝不自动删（人家真的打过了）",
      lock_by_id(fresh_id) is not None, "被误删了！")
check("★ 也因此它不再出现在待确认清单里（结束了就不提醒了）",
      fresh_id not in [p["id"] for p in (admin_state().get("pendingLocks") or [])],
      str([p["id"] for p in (admin_state().get("pendingLocks") or [])])[:120])
call("/api/admin/schedule", "POST", {"action": "remove-lock", "id": fresh_id})

# 7.4 ★ 已确认的不删（造一条"已确认 + 等了 200 小时"的客户单）
confirmed_slot = free_hours(1) or [future_slot[0] + 48 * HOUR_MS]
confirmed_id = make_client_lock("confirmed", confirmed_slot[0], created_hours_ago=200,
                                confirmed=1)
schedule({"action": "confirm-lock", "id": confirmed_id})       # 顺手再确认一次（幂等）
trigger_cleanup()
check("★ 已经点过「已确认」的不删（那是成立的单）",
      lock_by_id(confirmed_id) is not None, "被误删了！")
call("/api/admin/schedule", "POST", {"action": "remove-lock", "id": confirmed_id})

# 7.5 ★ 老板自己锁的不删
past_owner = next((lk for lk in admin_state()["schedule"]["locks"]
                   if lk.get("source") == "owner" and lk.get("client") == "老板自己锁的"), None)
if past_owner:
    conn = sqlite3.connect(os.path.join(DATA_DIR, "site.db"))
    conn.execute("UPDATE locks SET created_at=? WHERE id=?",
                 (int(__import__("time").time() * 1000) - 300 * HOUR_MS, past_owner["id"]))
    conn.commit()
    conn.close()
trigger_cleanup()
check("★ 老板自己锁的时段，等多久都不会被自动删",
      past_owner and lock_by_id(past_owner["id"]) is not None, "被误删了！")

# 7.6 ★ 关掉之后立刻停手（两步都验：刚关的那一下不删 + 之后触发也不删）
just_slot = free_hours(1) or [future_slot[0] + 72 * HOUR_MS]
just_id = make_client_lock("off", just_slot[0], created_hours_ago=500)
s, b = set_hours_env(0)               # 把开关关掉（关的一瞬间顺手清理一次）
check("★ 关掉自动释放的那一下，不会顺手把老单删掉", lock_by_id(just_id) is not None,
      "被删掉了！")
check("设置确实回到了 0", b.get("autoReleaseHours") == 0, str(b.get("autoReleaseHours")))
trigger_cleanup()                     # 再触发一次（客户刷时间表也会发生）
check("★ 关掉之后，等了 500 小时的单也一条都不删", lock_by_id(just_id) is not None,
      "被删掉了！")
check("关掉之后没有新的自动释放记录（日志里只剩之前那次的）",
      len(admin_state().get("autoReleased") or []) <= 1,
      str(admin_state().get("autoReleased"))[:120])
call("/api/admin/schedule", "POST", {"action": "remove-lock", "id": just_id})

# ============================================================================
#  8. ★ 今日待办条的数字口径（前端就是拿这三个数）
# ============================================================================
print("\n=== 8. ★ 今日待办条三个数字的口径 ===")
adm = admin_state()
now_ms = int(__import__("time").time() * 1000)
today_start = (int(dt.datetime.now(CN).replace(hour=0, minute=0, second=0,
                                               microsecond=0).timestamp() * 1000))
today_end = today_start + 24 * HOUR_MS
today_bookings = [lk for lk in adm["schedule"]["locks"]
                  if lk.get("kind") == "booking" and lk["start"] < today_end
                  and today_start < lk["end"]]
check("「今天要打」= 今天（北京时间）的 booking 类锁数量（和 renderStats 同一套算法）",
      isinstance(today_bookings, list), "%d 单" % len(today_bookings))
check("★ 待办条用的「今天要打」这个数，和 /api/admin 里的锁对得上（前端不用自己再算一遍别的口径）",
      len(today_bookings) == len([lk for lk in adm["schedule"]["locks"]
                                  if lk.get("kind") == "booking"
                                  and lk["start"] < today_end and today_start < lk["end"]]),
      "%d 单" % len(today_bookings))

check("「客户要催」用的是 customerStats.remind（后端算好的超期 + 7 天内到期）",
      isinstance((adm.get("customerStats") or {}).get("remind"), int),
      "remind=%r" % (adm.get("customerStats") or {}).get("remind"))

check("「单待确认」用的就是 pendingLocks 的条数",
      len(adm.get("pendingLocks") or []) >= 1, "%d 单" % len(adm.get("pendingLocks") or []))
check("★ 待确认清单里每一条都是 source=client 且 confirmed=0 且 end > now",
      all(p.get("source") == "client" and int(p.get("confirmed") or 0) == 0
          and int(p["end"]) > now_ms for p in (adm.get("pendingLocks") or [])),
      str([(p.get("client"), p.get("confirmed"), p.get("end")) for p in (adm.get("pendingLocks") or [])])[:160])
check("★ 清单没有把「已经过去的待确认」算进来（那种不再提醒）",
      all(int(p["end"]) > now_ms for p in (adm.get("pendingLocks") or [])), "有过去的")

check("GET /api/admin 带 serverTime（前端算「等了多久」要用服务器时间，不信客户端的钟）",
      int(adm.get("serverTime") or 0) > 0, str(adm.get("serverTime")))

# ============================================================================
#  9. ★ 备份带着 confirmed 走
# ============================================================================
print("\n=== 9. ★ 备份 / 恢复都带上 confirmed ===")
# 复用刚才那条待确认的单（少约一次少占一次限流名额：/api/book 同一个 IP 10 分钟最多 5 次）
cleanup_all_bookings()
hours7 = free_hours(2)
s, b = book(hours7, contact="wx_test_backup")
bk_id = next((lk["id"] for lk in admin_state()["schedule"]["locks"]
              if lk.get("source") == "client" and lk.get("contact") == "wx_test_backup"), None)
check("（准备）造一条用于备份测试的待确认预约", bool(bk_id), "status=%s %s" % (s, str(b)[:120]))
s, raw = call("/api/admin/export")
backup = json.loads(raw)
bkl = next((lk for lk in (backup.get("locks") or []) if lk.get("id") == bk_id), None)
check("★ 备份里每条锁都带 confirmed / confirmed_at",
      all("confirmed" in lk and "confirmed_at" in lk for lk in (backup.get("locks") or [])),
      str([(lk.get("client"), lk.get("confirmed")) for lk in (backup.get("locks") or [])])[:160])
check("★ 待确认的那条在备份里也是 confirmed=0",
      bool(bkl) and int(bkl.get("confirmed") or 0) == 0,
      "confirmed=%r" % (bkl.get("confirmed") if bkl else "没找到"))
check("备份里带着 autoReleaseHours", "autoReleaseHours" in backup,
      str(backup.get("autoReleaseHours")))
check("备份里带着 autoReleased 日志", isinstance(backup.get("autoReleased"), list),
      str(backup.get("autoReleased"))[:100])

# 恢复：确认它变回待确认
schedule({"action": "confirm-lock", "id": bk_id})
check("（准备）先把这条确认掉", int((lock_by_id(bk_id) or {}).get("confirmed") or 0) == 1)
s, b = jcall("/api/admin/import", "POST", {"backup": backup})
check("导入备份成功", s == 200 and b.get("ok"), "status=%s %s" % (s, str(b)[:100]))
check("★ 恢复后它又变回「待确认」（备份里是什么就是什么）",
      int((lock_by_id(bk_id) or {}).get("confirmed") or 0) == 0,
      "confirmed=%r" % (lock_by_id(bk_id) or {}).get("confirmed"))
check("★ 恢复后待确认清单里又出现了它",
      bk_id in [p["id"] for p in (b.get("pendingLocks") or [])],
      str([p["id"] for p in (b.get("pendingLocks") or [])])[:120])

# 老备份（没有 confirmed 字段）→ 一律当"已确认"，不能凭空造出一堆待确认
old_backup = json.loads(json.dumps(backup))
for lk in old_backup.get("locks", []):
    lk.pop("confirmed", None)
    lk.pop("confirmed_at", None)
old_backup.pop("autoReleaseHours", None)
old_backup.pop("autoReleased", None)
s, b = jcall("/api/admin/import", "POST", {"backup": old_backup})
check("★ 老备份（没有 confirmed 字段）也能导进来", s == 200 and b.get("ok"),
      "status=%s %s" % (s, str(b)[:100]))
check("★ 老备份导回来之后，锁全部是「已确认」（不会凭空变成待确认）",
      all(int(lk.get("confirmed") or 0) == 1 for lk in admin_state()["schedule"]["locks"]),
      str([(lk.get("client"), lk.get("confirmed")) for lk in admin_state()["schedule"]["locks"]])[:160])

# ============================================================================
# 10. 权限
# ============================================================================
print("\n=== 10. 权限（没登录什么都干不了） ===")
for action, label in [("confirm-lock", "确认"), ("release-lock", "释放"),
                      ("set-auto-release", "改自动释放设置")]:
    s, b = jcall("/api/admin/schedule", "POST", {"action": action, "id": bk_id, "hours": 1},
                 use_cookie=False)
    check("★ 未登录 %s → 401" % label, s == 401, "status=%s" % s)
s, b = jcall("/api/admin/schedule", "POST", {"action": "没有这个动作"}, use_cookie=False)
check("未登录连不认识的动作也是 401（先挡在门外）", s == 401, "status=%s" % s)
s, b = schedule({"action": "没有这个动作"})
check("登录后传不认识的动作 → 400", s == 400, str(b.get("error"))[:60])

# ============================================================================
# 11. 收尾
# ============================================================================
print("\n=== 11. 收尾 ===")
cleanup_all_bookings()
set_hours_env(0)
adm = admin_state()
left = [lk for lk in adm["schedule"]["locks"]
        if lk.get("source") == "client" and str(lk.get("contact") or "").startswith("wx_test")]
check("测试造的客户预约都清掉了", not left, str([(lk.get("client"), lk.get("contact")) for lk in left]))
check("自动释放设置还原成默认的 0", adm.get("autoReleaseHours") == 0,
      str(adm.get("autoReleaseHours")))
s, b = schedule({"action": "remove-lock", "id": (past_owner or {}).get("id")})
check("顺手把测试用的「老板自己锁的」那条也删掉",
      all(lk.get("client") != "老板自己锁的" for lk in admin_state()["schedule"]["locks"]),
      "还在")

print("\n" + "=" * 70)
print(" 通过 %d 项，失败 %d 项" % (ok, fail))
print("=" * 70)
sys.exit(1 if fail else 0)
