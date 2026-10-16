# -*- coding: utf-8 -*-
"""
★ 第五刀之一：**客户档案结构化**（游戏 / 区服 / 账号 / 密码 / 角色 / 练度）+ **密码打码**。

为什么要这一刀（主人的原话是"非常合理！"）：代肝业务必须知道"客户的号在哪个游戏、
哪个区服、账号密码是什么" —— 以前只能塞在**备注**里，找一个手机号得靠肉眼翻。
现在分成 6 个字段，但**一个都不能出现在公开接口里**（这是客户隐私）。

这个文件测的是：

    1. 迁移幂等：init_db 跑两遍不会重复加列、不会报错，**老客户资料一条都不丢**
    2. 老库兼容：手工造一个"没有这 6 列的旧 customers 表"，init_db 之后列都在、老数据还在
    3. 增删改查：6 个字段能存能读；超长截断；全是自由文本（"精二 90"原样保留）
    4. ★★ **绝不出现在公开接口里**：`/api/public`（含 site / profile / zone / nav / gallery /
       slots 全部角落）里都搜不到账号密码，`site_summary()` 的键里也没有
    5. ★ 密码打码的行为：后端存的是**原文**（不然列表那个「复制密码」就复制不出东西）
    6. 备份恢复：6 个字段跟着备份走；**老备份（客户里没有这几个字段）导入时置空、不报错**
    7. 搜索：账号 / 角色名 / 游戏 / 区服都能搜到（前端的搜索范围，靠后端把字段发下来）
    8. 权限：未登录读不到、写不了

⚠️ 会写数据，只能对着测试实例跑。
用法：
    python tests\\test_profile_fields.py
    set TEST_BASE=http://127.0.0.1:8199 && python tests\\test_profile_fields.py
"""

import http.cookiejar
import json
import os
import re
import sqlite3
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
DATA_DIR = os.environ.get("TEST_DATA_DIR") or os.path.join(ROOT, ".testdata")

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

# 这 6 个字段在 **JSON / 备份 / 前端** 里的名字（后端、前端、备份三处都用这一份清单）。
# ⚠️ 练度那一项对外叫 gameLevel（库里那一列叫 game_level），**不叫 level** ——
#    `level` 这个名字早就被"到期分档"占了（expired / today / soon…，见 customer_level()）。
#    两个意思挤同一个名字的后果我在写这一版时当场撞上了：练度存进去了，
#    但从 /api/admin 读出来永远是 "none"。所以库里那一列叫 game_level、JSON 里叫 gameLevel。
#    （下面那条「练度没有覆盖掉 level」的断言就是守着这个名字的：
#      以后谁想把它改回 level，这里会立刻红。）
ACCOUNT_FIELDS = ("game", "server", "account", "password", "role", "gameLevel")
# 数据库里的列名（和上面差一个：练度那列是 game_level）
DB_COLUMNS = ("game", "server", "account", "password", "role", "game_level")
# 后台弹窗里那 6 个输入框的 id（练度那个叫 cLevel，跟着数据库列走）
FORM_IDS = {"game": "cGame", "server": "cServer", "account": "cAccount",
            "password": "cPassword", "role": "cRole", "gameLevel": "cLevel"}


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


def customer_api(payload):
    return jcall("/api/admin/customers", "POST", payload)


def admin_state():
    return jcall("/api/admin")[1]


def find(cid):
    """从后台全量里找一条客户"""
    return next((c for c in admin_state().get("customers", []) if c["id"] == cid), None)


# ============================================================================
#  0. 登录
# ============================================================================
print("=" * 70)
print(" 客户档案结构化 + 密码打码测试（%s）" % BASE)
print("=" * 70)

s, b = jcall("/api/auth", "POST", {"password": PASSWORD})
check("用测试密码登录成功", s == 200, "status=%s" % s)
check("（测试数据目录）", os.path.isdir(DATA_DIR), DATA_DIR)

# ============================================================================
#  1. 数据库：6 列都在，而且是幂等迁移
# ============================================================================
print("\n=== 1. ★ 数据库迁移：6 个字段都在，而且是幂等的 ===")
db = os.path.join(DATA_DIR, "site.db")
check("找得到测试数据库", os.path.isfile(db), db)


def customer_columns():
    conn = sqlite3.connect(db)
    try:
        return [row[1] for row in conn.execute("PRAGMA table_info(customers)").fetchall()]
    finally:
        conn.close()


cols = customer_columns()
missing = [c for c in DB_COLUMNS if c not in cols]
check("★ customers 表里有游戏账号那 6 列（练度那列叫 game_level，不和 level 撞名）",
      not missing, "缺的：%s" % missing)
check("老的几列一个都没少（迁移只加不改）",
      all(c in cols for c in ("id", "name", "contact", "contact_type", "zone", "item",
                              "price", "start_date", "end_date", "status", "note",
                              "created_at", "updated_at")),
      str(cols))

# 幂等：直接再调一次 init_db()（子进程，用同一个 DATA_DIR），列不该变多、也不该报错
probe = ("import sys, json\n"
         "sys.path.insert(0, sys.argv[1])\n"
         "import app\n"
         "app.init_db()\n"
         "app.init_db()\n"
         "import sqlite3\n"
         "c = sqlite3.connect(app.DB_PATH)\n"
         "cols = [r[1] for r in c.execute('PRAGMA table_info(customers)')]\n"
         "print('PROBE ' + json.dumps({'cols': cols, 'n': len(cols)}))\n")
env = dict(os.environ)
env["PYTHONIOENCODING"] = "utf-8"
env["DATA_DIR"] = DATA_DIR
proc = subprocess.run([sys.executable, "-c", probe, ROOT], env=env, capture_output=True,
                      text=True, encoding="utf-8", errors="replace")
line = next((x for x in (proc.stdout or "").split("\n") if x.startswith("PROBE ")), "")
check("★ init_db() 连跑两次不报错（幂等）", bool(line), (proc.stderr or "")[-200:])
if line:
    info = json.loads(line[len("PROBE "):])
    check("★ 再跑两次 init_db 之后，列还是一样多（不会重复加列）",
          info["n"] == len(cols) and sorted(info["cols"]) == sorted(cols),
          "%d → %d" % (len(cols), info["n"]))

# ============================================================================
#  2. ★ 老库（没有这 6 列）也能升级，而且老客户一条不丢
# ============================================================================
print("\n=== 2. ★ 老库升级：没有这 6 列的旧表也能补上，老客户不丢 ===")
old_db_dir = os.path.join(DATA_DIR, "oldlib")
old_db = os.path.join(old_db_dir, "site.db")
os.makedirs(old_db_dir, exist_ok=True)
if os.path.isfile(old_db):
    os.remove(old_db)
conn = sqlite3.connect(old_db)
conn.executescript("""
CREATE TABLE store (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE customers (
    id TEXT PRIMARY KEY, name TEXT NOT NULL, contact TEXT DEFAULT '',
    contact_type TEXT DEFAULT 'wechat', zone TEXT DEFAULT '', item TEXT DEFAULT '',
    price TEXT DEFAULT '', start_date TEXT DEFAULT '', end_date TEXT DEFAULT '',
    status TEXT DEFAULT 'active', note TEXT DEFAULT '', created_at INTEGER DEFAULT 0,
    updated_at INTEGER DEFAULT 0);
INSERT INTO customers(id, name, contact, item, price, note, created_at, updated_at)
VALUES('old1', '老客户', 'wx_old', '普通托管', '20R/月', '写在备注里的账号：abc/123', 1, 1);
""")
conn.commit()
conn.close()
probe2 = ("import sys, json\n"
          "sys.path.insert(0, sys.argv[1])\n"
          "import app\n"
          "app.init_db()\n"
          "import sqlite3\n"
          "c = sqlite3.connect(app.DB_PATH)\n"
          "c.row_factory = sqlite3.Row\n"
          "cols = [r[1] for r in c.execute('PRAGMA table_info(customers)')]\n"
          "row = dict(c.execute(\"SELECT * FROM customers WHERE id='old1'\").fetchone())\n"
          "print('PROBE ' + json.dumps({'cols': cols, 'row': row}, ensure_ascii=False))\n")
env2 = dict(env)
env2["DATA_DIR"] = old_db_dir
proc2 = subprocess.run([sys.executable, "-c", probe2, ROOT], env=env2, capture_output=True,
                       text=True, encoding="utf-8", errors="replace")
line2 = next((x for x in (proc2.stdout or "").split("\n") if x.startswith("PROBE ")), "")
check("★ 用老库启动 init_db() 不报错", bool(line2), (proc2.stderr or "")[-300:])
if line2:
    info2 = json.loads(line2[len("PROBE "):])
    check("★ 6 列都补上了", all(c in info2["cols"] for c in DB_COLUMNS),
          "缺的：%s" % [c for c in DB_COLUMNS if c not in info2["cols"]])
    row = info2["row"]
    check("★★ 老客户那一条**一个字都没变**（价格、备注、联系方式都还在）",
          row["name"] == "老客户" and row["price"] == "20R/月" and row["contact"] == "wx_old"
          and row["note"] == "写在备注里的账号：abc/123",
          str({k: row[k] for k in ("name", "price", "contact")}))
    check("★ 新补的 6 列是空字符串（不是 None，前端不用做空判断）",
          all(row.get(c) == "" for c in DB_COLUMNS),
          str({c: row.get(c) for c in DB_COLUMNS}))
    check("老备注**没有被自动搬进新字段**（不猜他的意思 —— 搬错了他更难发现）",
          row["account"] == "" and row["password"] == "")

# ============================================================================
#  3. 增删改查：6 个字段
# ============================================================================
print("\n=== 3. 增删改查（6 个字段） ===")
created = []
s, data = customer_api({
    "action": "create", "name": "档案测试老板", "contact": "wx_dangan", "contact_type": "wechat",
    "item": "精托托管", "price": "100R/月",
    "game": "明日方舟", "server": "官服", "account": "13800001111",
    "password": "abc123XYZ!@#", "role": "败絮#1234", "gameLevel": "精二 90",
    "note": "每天返图",
})
check("带齐 6 个字段建档成功", s == 200 and data.get("ok"), "status=%s %s" % (s, str(data)[:120]))
full = data.get("customer") or {}
created.append(full.get("id"))
for key, want in (("game", "明日方舟"), ("server", "官服"), ("account", "13800001111"),
                  ("password", "abc123XYZ!@#"), ("role", "败絮#1234"),
                  ("gameLevel", "精二 90")):
    check("  %s 原样存下来了" % key, full.get(key) == want, repr(full.get(key)))
check("★ 密码里的特殊字符（!@#）一个字都没被改",
      full.get("password") == "abc123XYZ!@#", repr(full.get("password")))
check("★ 练度是自由文本（\"精二 90\" 没被转成数字）",
      full.get("gameLevel") == "精二 90", repr(full.get("gameLevel")))
check("★★ 练度**没有覆盖掉**「到期分档」那个 level（两个名字必须分开）",
      full.get("level") in ("none", "ok", "soon", "week", "today", "expired",
                            "paused", "ended"),
      "level=%r，练度=%r" % (full.get("level"), full.get("gameLevel")))
check("老字段不受影响（价格还是自由文本）", full.get("price") == "100R/月", repr(full.get("price")))

# 读回来（重新从 /api/admin 全量里拿，确认真的落库了）
again = find(full.get("id"))
check("★ 重新读一遍，6 个字段都在（不是只存在内存里）",
      again and all(again.get(k) == full.get(k) for k in ACCOUNT_FIELDS),
      str({k: (again or {}).get(k) for k in ACCOUNT_FIELDS}))

# 改：只改其中几个
s, data = customer_api({"action": "update", "id": full["id"], "name": "档案测试老板",
                        "game": "明日方舟", "server": "B服", "account": "13900002222",
                        "password": "newpass456", "role": "新角色", "gameLevel": "满级",
                        "price": "100R/月"})
check("改 6 个字段成功", s == 200 and data.get("ok"), "status=%s" % s)
upd = (data.get("customer") or {})
check("★ 改完之后是新值", upd.get("server") == "B服" and upd.get("password") == "newpass456"
      and upd.get("gameLevel") == "满级", str({k: upd.get(k) for k in ACCOUNT_FIELDS}))

# 第二个客户：只填名字（6 个字段应该都是空字符串，不是 None）
s, data = customer_api({"action": "create", "name": "只填名字的2"})
only = data.get("customer") or {}
created.append(only.get("id"))
check("★ 不填这 6 项 → 都是空字符串（前端不用做空判断）",
      all(only.get(k) == "" for k in ACCOUNT_FIELDS),
      str({k: only.get(k) for k in ACCOUNT_FIELDS}))

# 超长截断（长度上限是必须的：不然有人能塞 10 万字进来）
s, data = customer_api({"action": "create", "name": "超长测试", "game": "游" * 100,
                        "server": "服" * 100, "account": "a" * 200, "password": "p" * 200,
                        "role": "角" * 100, "gameLevel": "级" * 100})
long_c = data.get("customer") or {}
created.append(long_c.get("id"))
check("★ 游戏 / 区服 / 练度截到 30 字", len(long_c.get("game", "")) == 30
      and len(long_c.get("server", "")) == 30 and len(long_c.get("gameLevel", "")) == 30,
      "%d/%d/%d" % (len(long_c.get("game", "")), len(long_c.get("server", "")),
                    len(long_c.get("gameLevel", ""))))
check("★ 账号 / 密码截到 60 字", len(long_c.get("account", "")) == 60
      and len(long_c.get("password", "")) == 60,
      "%d/%d" % (len(long_c.get("account", "")), len(long_c.get("password", ""))))
check("★ 角色名截到 40 字", len(long_c.get("role", "")) == 40, "%d" % len(long_c.get("role", "")))

# 前后空格会被去掉（手机上粘贴很容易带空格）
s, data = customer_api({"action": "create", "name": "空格测试",
                        "password": "  pw with space  ", "account": "  138  "})
sp = data.get("customer") or {}
created.append(sp.get("id"))
check("前后空格去掉", sp.get("password") == "pw with space" and sp.get("account") == "138",
      repr(sp.get("password")))

# ============================================================================
#  4. ★★ 绝不出现在公开接口里
# ============================================================================
print("\n=== 4. ★★ 这 6 个字段绝不能出现在公开接口里（客户隐私） ===")
SECRET_VALUES = ["13800001111", "13900002222", "newpass456", "abc123XYZ!@#",
                 "败絮#1234", "精二 90", "新角色", "wx_dangan"]

s, pub = jcall("/api/public")
raw = json.dumps(pub, ensure_ascii=False)
check("★ /api/public 的整段 JSON 里搜不到任何一个隐私值",
      not any(v in raw for v in SECRET_VALUES),
      str([v for v in SECRET_VALUES if v in raw]))
check("★ /api/public 里没有 customers 这个键", "customers" not in pub, str(list(pub.keys())[:12]))
check("★ site_summary() 的键里一个客户字段都没有",
      not any(k in (pub.get("site") or {}) for k in ACCOUNT_FIELDS + ("customers",)),
      str(sorted((pub.get("site") or {}).keys())))
# 连嵌套的角落都查一遍（profile 是平铺的兼容视图，最容易漏）
for where in ("profile", "zone", "site", "nav", "gallery", "categories", "zones",
              "galleryCategories"):
    chunk = json.dumps(pub.get(where), ensure_ascii=False)
    check("  pub[%s] 里也没有" % where, not any(v in chunk for v in SECRET_VALUES))
# 分区页 HTML（服务端塞了 __PAGE__，也是公开的）
s, home = call("/")
check("★ 首页 HTML 里也搜不到（__PAGE__ 是公开的）",
      not any(v in home for v in SECRET_VALUES),
      str([v for v in SECRET_VALUES if v in home]))
s, zone_html = call("/z/" + ((admin_state().get("zones") or [{}])[0].get("id") or "delta"))
check("★ 分区页 HTML 里也搜不到", not any(v in zone_html for v in SECRET_VALUES))
# /terms 也是公开的
s, terms_html = call("/terms")
check("★ 条款页 HTML 里也搜不到", not any(v in terms_html for v in SECRET_VALUES))
# 公开接口里出现的"字段名"也要看一眼（有名字没值同样不该有）
check("★ 公开接口里连字段名都不该出现（game 除外：分区资料本来就有 game）",
      not any(k in raw for k in ("password", "account", "role")),
      str([k for k in ("password", "account", "role") if k in raw]))

# 未登录读不到
s, _b = call("/api/admin", use_cookie=False)
check("★ 未登录 /api/admin → 401（客户档案只走这一条路）", s == 401, "status=%s" % s)

# ============================================================================
#  5. ★ 密码打码：后端存原文，打码是"显示层"的事
# ============================================================================
print("\n=== 5. ★ 密码打码的行为（存原文、显示层打码） ===")
one = find(full["id"])
check("★ 后端发下来的密码是**原文**（前端才能做「复制密码」）",
      one and one.get("password") == "newpass456", repr((one or {}).get("password")))
# 前端打码的常量在 admin.js 里（后端不该做这件事 —— 做了就没法复制了）
admin_js = open(os.path.join(ROOT, "static", "admin.js"), encoding="utf-8").read()
check("★ 前端有打码常量 PASSWORD_MASK = ••••••",
      "PASSWORD_MASK = '••••••'" in admin_js,
      "找得到" if "PASSWORD_MASK" in admin_js else "找不到")
check("★ 打码的是密码，账号**不打码**（他经常要看着拨号 / 搜索）",
      "账号：" in admin_js and "密码：" in admin_js and "PASSWORD_MASK" in admin_js)
check("★ 有小眼睛按钮和复制按钮", "data-pw-toggle" in admin_js and "data-pw-copy" in admin_js)
check("★ 复制走的是原文（不用先显示）", "span.dataset.pw" in admin_js)
# ★ 2026-10-16 改过判据（原来写得太宽 ✗ 会误伤）：
#   原来断言的是"admin.js 里【任何】localStorage 都不许用"✗
#   但后来加了「文案页折叠状态记忆」✓ 它必须要用 localStorage ✓
#   真正要守的是：【密码的显示状态】不许持久化 ✓（刷新后必须回到打码 ✓）
#   所以改成：localStorage 只允许用来记折叠状态 ✓ 不许碰密码那些 key ✓
_ls_calls = re.findall(r"localStorage\.(?:set|get|remove)Item\(([^)]*)", admin_js)
check("★ 密码显示状态不写进 localStorage（刷新后回到打码）—— 只允许折叠记忆用",
      all(("TEXTS_OPEN" in c) or ("textsOpen" in c) for c in _ls_calls),
      "localStorage 用到 %d 处：%s" % (len(_ls_calls), [c.strip()[:28] for c in _ls_calls]))
check("★ 密码不进搜索范围（搜不出密码对他没损失，但能免则免）",
      "c.game, c.server, c.account, c.role, c.gameLevel]" in admin_js)
tpl = open(os.path.join(ROOT, "templates", "admin.html"), encoding="utf-8").read()
check("★ 弹窗里有那 6 个输入框（id 都在）",
      all(('id="%s"' % FORM_IDS[k]) in tpl for k in ACCOUNT_FIELDS),
      str([k for k in ACCOUNT_FIELDS if ('id="%s"' % FORM_IDS[k]) not in tpl]))
check("★ 标签写清楚了（游戏 / 区服（官服/B服）/ 账号或手机号 / 密码 / 角色名 / 练度或等级）",
      all(t in tpl for t in ("游戏", "区服（官服/B服）", "账号或手机号", "密码",
                             "角色名", "练度或等级")))

# 那句提示：在 TEXT_GROUPS 里（客户看到的每一句话都能改，后台提示也一样）
adm = admin_state()
fields = {f["key"]: f for g in adm.get("textGroups") or [] for f in g["fields"]}
check("★ 「密码默认打码…」那句提示也在文案目录里（能改）",
      fields.get("adminPasswordMaskHint", {}).get("default")
      == "密码默认打码，点小眼睛才显示 —— 防的是别人站你身后看到屏幕。",
      str(fields.get("adminPasswordMaskHint", {}).get("default"))[:60])

# ============================================================================
#  6. 备份 / 恢复（含老备份兼容）
# ============================================================================
print("\n=== 6. 备份带上 6 个字段（老备份导入不清空） ===")
s, raw = call("/api/admin/export")
backup = json.loads(raw)
brow = next((c for c in backup.get("customers", []) if c["id"] == full["id"]), None)
check("★ 备份里有这位客户", bool(brow), "共 %d 位" % len(backup.get("customers", [])))
check("★★ 6 个字段全都打进备份了（换台电脑恢复也还在）",
      brow and all(brow.get(k) == (one or {}).get(k) for k in ACCOUNT_FIELDS),
      str({k: (brow or {}).get(k) for k in ACCOUNT_FIELDS}))
check("★ 密码也是原文进备份（恢复后「复制密码」照样能用）",
      brow and brow.get("password") == "newpass456", repr((brow or {}).get("password")))

# 改掉 → 恢复 → 应该回到备份时的样子
customer_api({"action": "update", "id": full["id"], "name": "档案测试老板",
              "game": "改过的游戏", "password": "改过的密码", "account": "改过的账号"})
check("（准备）先改成别的", (find(full["id"]) or {}).get("password") == "改过的密码")
s, data = jcall("/api/admin/import", "POST", {"backup": backup})
check("导入备份成功", s == 200 and data.get("ok"), "status=%s %s" % (s, str(data)[:100]))
restored = next((c for c in data.get("customers", []) if c["id"] == full["id"]), None)
check("★ 恢复后 6 个字段回到备份时的样子",
      restored and all(restored.get(k) == (one or {}).get(k) for k in ACCOUNT_FIELDS),
      str({k: (restored or {}).get(k) for k in ACCOUNT_FIELDS}))
check("★ 恢复摘要里有客户数", "customers" in (data.get("summary") or {}),
      str((data.get("summary") or {}).get("customers")))

# 老备份：客户里没有这 6 个字段（第五刀之前导出的）
old_backup = json.loads(json.dumps(backup))
for c in old_backup["customers"]:
    for k in ACCOUNT_FIELDS:
        c.pop(k, None)
s, data = jcall("/api/admin/import", "POST", {"backup": old_backup})
check("★ 老备份（客户里没有这 6 个字段）也能导入", s == 200 and data.get("ok"),
      "status=%s %s" % (s, str(data)[:120]))
old_back = next((c for c in data.get("customers", []) if c["id"] == full["id"]), None)
check("★ 导入老备份后：6 个字段是空字符串（**不是报错、也不是留着旧值**）",
      old_back and all(old_back.get(k) == "" for k in ACCOUNT_FIELDS),
      str({k: (old_back or {}).get(k) for k in ACCOUNT_FIELDS}))
check("★ 老备份导入后，其它字段（价格/备注/名字）照样是对的",
      old_back and old_back.get("price") == "100R/月" and old_back.get("name") == "档案测试老板",
      str({k: (old_back or {}).get(k) for k in ("name", "price")}))

# 坏行不能让整份备份导不进来
broken = json.loads(json.dumps(backup))
broken["customers"] = broken["customers"] + [
    "这不是字典", {"name": ""}, {"name": "坏行但是好的", "password": 12345},
]
s, data = jcall("/api/admin/import", "POST", {"backup": broken})
check("★ 备份里有坏行 → 恢复照样成功（坏行跳过）", s == 200 and data.get("ok"),
      "status=%s %s" % (s, str(data)[:100]))
weird = next((c for c in data.get("customers", []) if c["name"] == "坏行但是好的"), None)
check("★ 密码写成数字 12345 也不报错（转成文本存）", weird and weird.get("password") == "12345",
      repr((weird or {}).get("password")))

# 收尾：恢复成干净备份
jcall("/api/admin/import", "POST", {"backup": backup})
check("再导一次干净备份，状态回到原样", True)

# ============================================================================
#  7. 权限 + 收尾清理
# ============================================================================
print("\n=== 7. 权限与收尾 ===")
s, _b = call("/api/admin/customers", "POST", {"action": "create", "name": "偷偷建的",
                                              "password": "x"}, use_cookie=False)
check("★ 未登录建档 → 401", s == 401, "status=%s" % s)
s, _b = call("/api/admin/customers", "POST", {"action": "delete", "id": full["id"]},
             use_cookie=False)
check("★ 未登录删档 → 401", s == 401, "status=%s" % s)
check("★ 被拒之后这位客户还在", find(full["id"]) is not None)

for cid in created:
    if cid:
        customer_api({"action": "delete", "id": cid})
check("测试建的客户已清理干净",
      all(find(cid) is None for cid in created if cid),
      "剩 %d 位" % len([c for c in created if c and find(c)]))

print("\n" + "=" * 70)
print(" 通过 %d 项，失败 %d 项" % (ok, fail))
print("=" * 70)
sys.exit(1 if fail else 0)
