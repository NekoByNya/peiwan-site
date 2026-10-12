# -*- coding: utf-8 -*-
"""
================================================================================
 陪玩预约站 · 后端（单文件，只用 Python 标准库，零第三方依赖）
================================================================================
 这个文件就是整个网站的"服务器"。它的工作只有三件：
   1. 把数据存进 SQLite 数据库（data/site.db）
   2. 对外提供一组 JSON 接口（/api/...）
   3. 把 HTML / CSS / JS 文件发给浏览器

 怎么跑起来：
     python app.py                # 然后浏览器打开 http://127.0.0.1:8123
 换端口：
     set PORT=9000 && python app.py     (Windows)

 阅读顺序建议：先看【一、配置】→【三、时段计算】→【四、接口路由】
================================================================================
"""

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import socket
import sqlite3
import threading
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs, unquote, urlencode, quote

# ============================================================================
# 一、配置：改这里就能改变网站行为
# ============================================================================

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.environ.get("DATA_DIR") or os.path.join(ROOT, "data")   # 数据目录（想挂持久磁盘就改这个）
UPLOAD_DIR = os.path.join(DATA_DIR, "uploads")                        # 展示墙图片存这里
DB_PATH = os.path.join(DATA_DIR, "site.db")                           # 数据库文件
TEMPLATE_DIR = os.path.join(ROOT, "templates")
STATIC_DIR = os.path.join(ROOT, "static")

# 北京时间：中国全年不使用夏令时，所以固定 +8 小时就永远正确
CN = timezone(timedelta(hours=8))

HOUR_MS = 3600 * 1000          # 一小时的毫秒数
DAY_MS = 24 * HOUR_MS
DAYS_SHOWN = 7                 # 时间表显示未来几天（从今天算起）
MAX_SELECT_HOURS = 72          # 后台一次最多能勾选多少小时
MAX_BOOK_HOURS = 6             # 客户自助预约一次最多能锁多少小时（比后台严格，防止被人一口气占满）
BOOK_RATE_MAX = 5              # 同一个 IP 在下面这个时间窗里最多能提交几次预约
BOOK_RATE_WINDOW = 600         # 预约限流的时间窗（秒）：10 分钟
NOTIFY_TIMEOUT = 10            # 发通知的超时时间（秒）
SESSION_HOURS = 12             # 管理员登录后多少小时过期

# 联系方式渠道：客户可以用微信 / 闲鱼 / QQ 留下联系方式，后台按渠道显示
CONTACT_TYPES = {"wechat": "微信", "xianyu": "闲鱼", "qq": "QQ"}
CONTACT_DEFAULT = "wechat"     # 老接口没传 contactType 时当成微信

# 星期几的中文写法（datetime.weekday() 是 0=周一，所以顺序从周一开始）
WEEK_CN = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")

# 首次启动时的管理员密码（之后请在"安全与备份"里改掉）
DEFAULT_PASSWORD = os.environ.get("ADMIN_PASSWORD") or "baixu123"
PORT = int(os.environ.get("PORT") or 8123)
# 监听地址。默认只监听本机 127.0.0.1 —— 这样同一个 WiFi 下的其他设备
# （手机、室友的电脑、咖啡厅的陌生人）都连不上你的网站和后台，最安全。
# 需要临时放开时，用环境变量覆盖：
#   手机测试：  set HOST=0.0.0.0 && python app.py   （或者双击 start-lan.bat）
#   云平台：    Render 这类平台需要监听所有网卡，在平台的环境变量里设 HOST=0.0.0.0
HOST = os.environ.get("HOST") or "127.0.0.1"

# 首次启动时写入的默认资料（换成你自己的即可，也可以在后台直接改）
DEFAULT_PROFILE = {
    "name": "示例名字",
    "game": "",                              # ← 游戏名（留空也行，文案会自动省略）
    "tagline": "三角洲行动 · PC 端个人陪玩",
    "headline": "一起出发。\n尽兴而归。",
    "bio": "技术在线，情绪价值拉满。\n个人接单，非工作室，无中间商。猛攻、跑刀、任务、熟图，或者只是想找个人边聊边玩，都可以。听你指挥，也陪你放松。",
    "experience": "",
    "wechat": "your_wechat_id",
    "xianyu": "",
    "avatar": "",
    "background": "",          # 主页背景图：/api/media/xxx 或外部图片网址
    "backgroundDim": 72,       # 背景压暗程度（0-90），数字越大文字越清楚
    "heroImage": "",           # 首屏右侧的人物/游戏图
    "platform": "PC 电脑端",
    "accepting": True,
    "announcement": "先看空档，再来找我。微信 / 闲鱼确认后，由我锁定预约。",
    "styles": ["猛攻", "跑刀", "玻璃大炮", "堵桥", "陪做任务", "熟图", "轻松聊天"],
    "prices": [
        {"id": "dam", "name": "机密 · 零号大坝", "description": "轻装出发，熟悉每一条撤离路线。", "price": 15},
        {"id": "confidential", "name": "机密行动", "description": "打法听你的，一起把这局打漂亮。", "price": 20},
        {"id": "top-secret", "name": "绝密行动", "description": "高压战场，配合与陪伴始终在线。", "price": 25},
    ],
    "promises": [
        "以开第一局游戏为起点计时，时间打满；因我个人原因耽误的时间会补回。",
        "哪怕已经玩了 59 分钟，只要未满 1 小时，仍可再开一局，这一局超时不加价。",
        "同一订单 1 陪 1 或 1 陪 2，不额外收取人数差价。",
        "优先提供愉快的陪伴体验，尽力协助撤离；每局具有随机性，不承诺百分百撤离。",
        "设备无问题，可随时视频或截图验证。",
    ],
}

os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(UPLOAD_DIR, exist_ok=True)


# ============================================================================
# 二、数据库：只有 4 张表
# ============================================================================

SCHEMA = """
CREATE TABLE IF NOT EXISTS store (              -- 键值仓库：放资料、密码、版本号
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS locks (              -- 被锁定的时段（预约 booking / 休息 rest）
    id         TEXT PRIMARY KEY,
    kind       TEXT NOT NULL,
    start      INTEGER NOT NULL,                    -- 起始毫秒时间戳
    end        INTEGER NOT NULL,                    -- 结束毫秒时间戳
    client     TEXT DEFAULT '',                     -- 老板称呼（仅自己可见）
    note       TEXT DEFAULT '',                     -- 私密备注
    source     TEXT DEFAULT 'owner',                -- 谁锁的：owner=自己在后台锁的 / client=客户自助预约
    mode       TEXT DEFAULT '',                     -- 模式名（客户选的那一档报价）
    price      INTEGER DEFAULT 0,                   -- 这一段的总价（元，按小时数 × 单价算好）
    contact    TEXT DEFAULT '',                     -- 客户联系方式（自助预约必填，否则锁了时间也联系不上人）
    contact_type TEXT DEFAULT 'wechat',             -- 联系方式渠道：wechat 微信 / xianyu 闲鱼 / qq QQ
    created_at INTEGER DEFAULT 0                    -- 下单时间（毫秒时间戳；老数据是 0）
);
CREATE TABLE IF NOT EXISTS rules (              -- 固定休息规则（按周重复）
    id         TEXT PRIMARY KEY,
    days       TEXT NOT NULL,                   -- 例如 "[0,1,2]" 0=周日
    start_hour INTEGER NOT NULL,
    end_hour   INTEGER NOT NULL,                -- 24 表示到午夜
    from_ts    INTEGER NOT NULL,                -- 生效日期
    until_ts   INTEGER,                         -- 结束日期（可为空）
    note       TEXT DEFAULT '',
    enabled    INTEGER DEFAULT 1
);
CREATE TABLE IF NOT EXISTS gallery (            -- 展示墙
    id         TEXT PRIMARY KEY,
    title      TEXT DEFAULT '',
    caption    TEXT DEFAULT '',
    category   TEXT DEFAULT 'screenshot',       -- screenshot=截图 / review=老板评价
    image      TEXT DEFAULT '',                 -- /api/media/xxx.webp
    visible    INTEGER DEFAULT 0,               -- 是否在首页公开
    archived   INTEGER DEFAULT 0,               -- 是否归档（不在后台主列表显示）
    created_at INTEGER NOT NULL
);
"""

_db_lock = threading.Lock()


def connect():
    """
    每次操作开一个连接，避免多线程共用连接出问题。
    WAL 模式：读的时候不会被写挡住（客户看时间表的同时你能锁定时段），
    对"多人同时访问"的小站更稳。
    """
    conn = sqlite3.connect(DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def ensure_column(cur, table, column, decl):
    """
    给"老数据库"补字段（幂等迁移，重复调用没有任何副作用）。

    为什么必须有它：`CREATE TABLE IF NOT EXISTS` 只负责"表不存在时建表"，
    表已经存在时它一行都不改。老站点升级上来时，locks 表里没有
    source / mode / price / contact / created_at 这些新字段，
    直接查询就会报 "no such column"。

    做法：先 PRAGMA table_info 看一眼字段在不在，缺了才 ALTER TABLE ADD COLUMN。
    **老数据一条都不会丢**：加一列只是加一列，已有的行原样保留
    （老记录的 source 会自动填成 'owner'，也就是"老板自己锁的"）。
    """
    cols = [row[1] for row in cur.execute("PRAGMA table_info(%s)" % table).fetchall()]
    if column in cols:
        return False
    cur.execute("ALTER TABLE %s ADD COLUMN %s %s" % (table, column, decl))
    return True


def init_db():
    with connect() as conn:
        conn.executescript(SCHEMA)
        # 老数据库迁移：locks 表后加的字段，缺哪个补哪个（老预约记录原样保留）
        added = [col for col, decl in (
            ("source", "TEXT DEFAULT 'owner'"),
            ("mode", "TEXT DEFAULT ''"),
            ("price", "INTEGER DEFAULT 0"),
            ("contact", "TEXT DEFAULT ''"),
            ("contact_type", "TEXT DEFAULT 'wechat'"),
            ("created_at", "INTEGER DEFAULT 0"),
        ) if ensure_column(conn, "locks", col, decl)]
        if added:
            print("[迁移] locks 表补上了新字段：%s（老数据没动）" % "、".join(added), flush=True)
        if conn.execute("SELECT 1 FROM store WHERE key='profile'").fetchone() is None:
            conn.execute("INSERT INTO store(key, value) VALUES('profile', ?)",
                         (json.dumps(DEFAULT_PROFILE, ensure_ascii=False),))
            conn.execute("INSERT INTO store(key, value) VALUES('revision', '1')")
        if conn.execute("SELECT 1 FROM store WHERE key='password'").fetchone() is None:
            conn.execute("INSERT INTO store(key, value) VALUES('password', ?)",
                         (hash_password(DEFAULT_PASSWORD),))


# ---------- 资料 ----------

def get_profile():
    with connect() as conn:
        row = conn.execute("SELECT value FROM store WHERE key='profile'").fetchone()
        profile = json.loads(row["value"]) if row else dict(DEFAULT_PROFILE)
    merged = dict(DEFAULT_PROFILE)
    merged.update(profile)          # 老数据缺字段时用默认值补齐
    return merged


def get_revision():
    with connect() as conn:
        row = conn.execute("SELECT value FROM store WHERE key='revision'").fetchone()
    return int(row["value"]) if row else 1


def get_store(key, default=None):
    with connect() as conn:
        row = conn.execute("SELECT value FROM store WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def set_store(key, value):
    with _db_lock, connect() as conn:
        if conn.execute("SELECT 1 FROM store WHERE key=?", (key,)).fetchone():
            conn.execute("UPDATE store SET value=? WHERE key=?", (str(value), key))
        else:
            conn.execute("INSERT INTO store(key, value) VALUES(?,?)", (key, str(value)))


def save_profile(profile, revision):
    """返回 (是否成功, 新版本号)。revision 用来防止两个设备互相覆盖。"""
    with _db_lock, connect() as conn:
        row = conn.execute("SELECT value FROM store WHERE key='revision'").fetchone()
        current = int(row["value"]) if row else 1
        if int(revision) != current:
            return False, current
        new_rev = current + 1
        conn.execute("UPDATE store SET value=? WHERE key='profile'",
                     (json.dumps(profile, ensure_ascii=False),))
        conn.execute("UPDATE store SET value=? WHERE key='revision'", (str(new_rev),))
        return True, new_rev


# ---------- 密码 ----------

def hash_password(password):
    salt = secrets.token_hex(8)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 120_000)
    return salt + "$" + digest.hex()


def verify_password(password):
    with connect() as conn:
        row = conn.execute("SELECT value FROM store WHERE key='password'").fetchone()
    if not row:
        return False
    salt, digest = row["value"].split("$", 1)
    check = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 120_000)
    return secrets.compare_digest(check.hex(), digest)


def set_password(password):
    with _db_lock, connect() as conn:
        conn.execute("UPDATE store SET value=? WHERE key='password'", (hash_password(password),))


# ---------- 时段与规则 ----------

def list_locks():
    with connect() as conn:
        rows = conn.execute("SELECT * FROM locks ORDER BY start").fetchall()
    return [dict(r) for r in rows]


# 锁定记录的完整字段（写全再插入，老库补完字段后和新库行为完全一致）
LOCK_COLUMNS = ("id", "kind", "start", "end", "client", "note",
                "source", "mode", "price", "contact", "contact_type", "created_at")


def insert_lock(conn, **fields):
    """插一条锁定时段。source='owner' 是自己锁的，'client' 是客户自助预约。"""
    conn.execute("INSERT INTO locks(%s) VALUES(%s)" % (
        ", ".join(LOCK_COLUMNS), ", ".join("?" * len(LOCK_COLUMNS))),
        [fields.get(name) for name in LOCK_COLUMNS])


def list_rules():
    with connect() as conn:
        rows = conn.execute("SELECT * FROM rules ORDER BY start_hour").fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["days"] = json.loads(d["days"])
        d["enabled"] = bool(d["enabled"])
        out.append(d)
    return out


def list_gallery(only_public=False):
    sql = "SELECT * FROM gallery"
    if only_public:
        sql += " WHERE visible=1 AND archived=0"
    sql += " ORDER BY created_at DESC"
    with connect() as conn:
        rows = conn.execute(sql).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["visible"] = bool(d["visible"])
        d["archived"] = bool(d["archived"])
        out.append(d)
    return out


# ---------- 备份与恢复（免费档重新部署会清空 data/，所以这两个函数很重要） ----------

MIME_BY_EXT = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png",
               "webp": "image/webp", "gif": "image/gif"}
MAX_BACKUP_IMAGE = 3 * 1024 * 1024      # 单张超过 3MB 就不塞进备份，避免备份文件过大
MAX_UPLOAD = 8 * 1024 * 1024            # 单张上传上限 8MB


PROFILE_IMAGE_KEYS = ("avatar", "background", "heroImage")   # 资料里可以放图片的三个字段


def save_data_url(data_url):
    """
    把前端传来的 data:image/...;base64,... 存成文件。
    返回 (图片网址, 错误信息)——两个里只有一个不是 None。
    """
    if not data_url:
        return None, "请先选择图片。"
    m = re.match(r"^data:image/(png|jpeg|jpg|webp);base64,(.+)$", str(data_url), re.S)
    if not m:
        return None, "图片格式不支持，请用 JPG / PNG / WebP。"
    try:
        blob = base64.b64decode(m.group(2))
    except Exception:
        return None, "图片解析失败。"
    if len(blob) > MAX_UPLOAD:
        return None, "图片太大，请压缩到 8 MB 以内。"
    ext = {"jpeg": "jpg", "jpg": "jpg", "png": "png", "webp": "webp"}[m.group(1)]
    key = uuid.uuid4().hex + "." + ext
    with open(os.path.join(UPLOAD_DIR, key), "wb") as f:
        f.write(blob)
    return "/api/media/" + key, None


def image_to_data_url(image_path):
    """把 uploads 里的图片读成 data:image/...;base64,... 这样备份 JSON 才是完整的"""
    if not image_path:
        return None
    key = os.path.basename(image_path)
    full = os.path.join(UPLOAD_DIR, key)
    if not os.path.isfile(full) or os.path.getsize(full) > MAX_BACKUP_IMAGE:
        return None
    ext = key.rsplit(".", 1)[-1].lower()
    with open(full, "rb") as f:
        blob = base64.b64encode(f.read()).decode()
    return "data:%s;base64,%s" % (MIME_BY_EXT.get(ext, "application/octet-stream"), blob)


def build_backup():
    profile = get_profile()
    # 资料里的三张图（头像 / 主页背景 / 首屏图）也一起打包，否则恢复后会变成空图
    profile_images = {}
    for key in PROFILE_IMAGE_KEYS:
        data_url = image_to_data_url(profile.get(key))
        if data_url:
            profile_images[key] = data_url
    gallery = list_gallery()
    for g in gallery:
        g["imageData"] = image_to_data_url(g.get("image"))     # 展示墙图片一起打包
    return {
        "app": "peiwan-site",
        "version": 1,
        "exportedAt": int(time.time() * 1000),
        "profile": profile,
        "profileImages": profile_images,
        "profileRevision": get_revision(),
        "locks": list_locks(),
        "rules": list_rules(),
        "gallery": gallery,
    }


def apply_backup(data):
    """把备份写回数据库。返回 (摘要, 错误信息)。会整体覆盖现在的数据。"""
    if not isinstance(data, dict) or not isinstance(data.get("profile"), dict):
        return None, "备份文件格式不正确。"

    profile = dict(DEFAULT_PROFILE)
    profile.update(data["profile"])

    locks, rules, gallery = [], [], []
    images = 0

    # 先把资料里的三张图还原回磁盘
    for key, data_url in (data.get("profileImages") or {}).items():
        if key not in PROFILE_IMAGE_KEYS:
            continue
        url, _err = save_data_url(data_url)
        if url:
            profile[key] = url
            images += 1

    for lk in data.get("locks") or []:
        # 新字段（source/mode/price/contact/created_at）缺席时用默认值，
        # 这样"以前导出的老备份"也能正常恢复
        try:
            price = max(0, int(lk.get("price") or 0))
        except (TypeError, ValueError):
            price = 0
        try:
            created_at = int(lk.get("created_at") or 0)
        except (TypeError, ValueError):
            created_at = 0
        contact_type = str(lk.get("contact_type") or "")
        if contact_type not in CONTACT_TYPES:
            contact_type = CONTACT_DEFAULT if lk.get("contact") else ""
        try:
            locks.append((str(lk.get("id") or uuid.uuid4().hex),
                          "rest" if lk.get("kind") == "rest" else "booking",
                          int(lk["start"]), int(lk["end"]),
                          str(lk.get("client") or "")[:40], str(lk.get("note") or "")[:200],
                          "client" if lk.get("source") == "client" else "owner",
                          str(lk.get("mode") or "")[:40], price,
                          str(lk.get("contact") or "")[:40], contact_type, created_at))
        except (KeyError, TypeError, ValueError):
            continue        # 坏数据跳过，不让整个恢复失败

    for r in data.get("rules") or []:
        try:
            days = sorted({int(d) for d in (r.get("days") or []) if 0 <= int(d) <= 6})
        except (TypeError, ValueError):
            continue
        if not days:
            continue
        try:
            rules.append((str(r.get("id") or uuid.uuid4().hex), json.dumps(days),
                          max(0, min(23, int(r.get("start_hour") or 0))),
                          max(0, min(24, int(r.get("end_hour") or 0))),
                          int(r.get("from_ts") or 0),
                          int(r["until_ts"]) if r.get("until_ts") else None,
                          str(r.get("note") or "")[:40],
                          1 if r.get("enabled", True) else 0))
        except (TypeError, ValueError):
            continue

    for g in data.get("gallery") or []:
        image = str(g.get("image") or "")
        data_url = g.get("imageData") or ""
        m = re.match(r"^data:image/(png|jpeg|jpg|webp|gif);base64,(.+)$", data_url, re.S) if data_url else None
        if m:
            try:
                blob = base64.b64decode(m.group(2))
            except Exception:
                blob = None
            if blob and len(blob) <= 8 * 1024 * 1024:
                key = uuid.uuid4().hex + "." + {"jpeg": "jpg", "jpg": "jpg",
                                                "png": "png", "webp": "webp", "gif": "gif"}[m.group(1)]
                with open(os.path.join(UPLOAD_DIR, key), "wb") as f:
                    f.write(blob)
                image = "/api/media/" + key
                images += 1
        gallery.append((str(g.get("id") or uuid.uuid4().hex),
                        str(g.get("title") or "")[:60], str(g.get("caption") or "")[:300],
                        "review" if g.get("category") == "review" else "screenshot",
                        image, 1 if g.get("visible") else 0, 1 if g.get("archived") else 0,
                        int(g.get("createdAt") or time.time() * 1000)))

    with _db_lock, connect() as conn:
        conn.execute("DELETE FROM locks")
        conn.execute("DELETE FROM rules")
        conn.execute("DELETE FROM gallery")
        conn.executemany("""INSERT INTO locks(id, kind, start, end, client, note, source,
                            mode, price, contact, contact_type, created_at)
                            VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""", locks)
        conn.executemany("""INSERT INTO rules(id, days, start_hour, end_hour, from_ts, until_ts,
                            note, enabled) VALUES(?,?,?,?,?,?,?,?)""", rules)
        conn.executemany("""INSERT INTO gallery(id, title, caption, category, image, visible,
                            archived, created_at) VALUES(?,?,?,?,?,?,?,?)""", gallery)
        row = conn.execute("SELECT value FROM store WHERE key='revision'").fetchone()
        new_rev = (int(row["value"]) if row else 1) + 1
        conn.execute("UPDATE store SET value=? WHERE key='profile'",
                     (json.dumps(profile, ensure_ascii=False),))
        conn.execute("UPDATE store SET value=? WHERE key='revision'", (str(new_rev),))

    return {"locks": len(locks), "rules": len(rules), "gallery": len(gallery),
            "images": images, "profileRevision": new_rev}, None


# ============================================================================
# 三、时段计算（整个网站的核心，只有 30 行）
# ============================================================================

def midnight_ms(ms):
    """把某个时刻归到它当天北京时间的 00:00。"""
    dt = datetime.fromtimestamp(ms / 1000, CN)
    return int(dt.replace(hour=0, minute=0, second=0, microsecond=0).timestamp() * 1000)


def cn_label(ms):
    """把毫秒时间戳写成「10月13日 周一 20:00」（北京时间，前台和文案用的是同一套写法）"""
    dt = datetime.fromtimestamp(ms / 1000, CN)
    return "%d月%d日 %s %02d:00" % (dt.month, dt.day, WEEK_CN[dt.weekday()], dt.hour)


def cn_hhmm(ms):
    """只要「20:00」这一截（时间段的结束时间用它）"""
    return "%02d:00" % datetime.fromtimestamp(ms / 1000, CN).hour


def rule_covers(rule, slot_start_ms):
    """判断某个小时是否落在"固定休息"规则里（支持跨午夜，例如 22:00–06:00）。"""
    start_hour, end_hour = int(rule["start_hour"]), int(rule["end_hour"])
    # 算这条规则一次持续多少小时：24 表示到午夜；跨零点（如 22→6）也对
    span_hours = 24 - start_hour if end_hour == 24 else (end_hour - start_hour) % 24
    if span_hours <= 0:
        return False
    # 跨午夜的规则，可能由"前一天"开始，所以要检查今天和昨天两条基准线
    for back in (0, 1):
        base = midnight_ms(slot_start_ms) - back * DAY_MS
        if base < int(rule["from_ts"]):
            continue
        if rule["until_ts"] and base > int(rule["until_ts"]):
            continue
        weekday = (datetime.fromtimestamp(base / 1000, CN).weekday() + 1) % 7  # 转成 0=周日
        if weekday not in rule["days"]:
            continue
        window_start = base + start_hour * HOUR_MS
        window_end = window_start + span_hours * HOUR_MS
        if window_start <= slot_start_ms < window_end:
            return True
    return False


def slot_status(start_ms, end_ms, now_ms, locks, rules, accepting):
    """一个小时的 5 种状态：past 已过去 / booking 已预约 / rest 休息 / closed 停接 / free 可预约"""
    if end_ms <= now_ms:
        return "past"
    for lk in locks:
        if lk["start"] < end_ms and start_ms < lk["end"]:
            return "booking" if lk["kind"] == "booking" else "rest"
    for rule in rules:
        if rule["enabled"] and rule_covers(rule, start_ms):
            return "rest"
    if not accepting:
        return "closed"
    return "free"


def merge_hour_runs(hours):
    """
    把一串整点时间戳按"连续"合并成几段，例如
    [20,21,22, 次日0,次日1] → [[20,21,22], [次日0,次日1]]
    后台批量锁定和客户自助预约都用它，规则完全一致。
    """
    runs, run = [], []
    for h in sorted(set(hours)):
        if run and h - run[-1] != HOUR_MS:
            runs.append(run)
            run = []
        run.append(h)
    if run:
        runs.append(run)
    return runs


def build_slots(now_ms=None):
    """生成未来 7 天 × 24 小时 = 168 个格子。"""
    now_ms = now_ms or int(time.time() * 1000)
    start0 = midnight_ms(now_ms)
    locks, rules = list_locks(), list_rules()
    accepting = bool(get_profile().get("accepting", True))
    slots = []
    for i in range(DAYS_SHOWN * 24):
        s = start0 + i * HOUR_MS
        e = s + HOUR_MS
        slots.append({"start": s, "end": e,
                      "status": slot_status(s, e, now_ms, locks, rules, accepting)})
    return slots


# ============================================================================
# 三之二、客户自助预约（点了就锁定）
#   流程：复查空档 → 把连续的小时合并成几段 → 每段插一条 source='client' 的锁
#          → 返回一段可以直接发微信的文案（顺便给老板发通知，见第五章）
# ============================================================================

def contact_label(contact_type):
    """把渠道代码翻成中文：wechat → 微信 / xianyu → 闲鱼 / qq → QQ"""
    return CONTACT_TYPES.get(str(contact_type or ""), CONTACT_TYPES[CONTACT_DEFAULT])


def booking_head(profile):
    """
    预约文案的第一句。游戏名（game）和接单平台（platform）都在后台可改，
    所以同一套代码可以拿去开任何游戏的预约站，不用改代码。

      游戏名=三角洲行动 平台=PC 电脑端 → 你好，X！想约三角洲行动 PC 电脑端陪玩。
      游戏名=永劫无间   平台=PC 电脑端 → 你好，X！想约永劫无间 PC 电脑端陪玩。
      两个都留空                        → 你好，X！我想约你的陪玩服务。
    """
    name = str(profile.get("name") or "").strip()
    game = str(profile.get("game") or "").strip()
    platform = str(profile.get("platform") or "").strip()
    what = " ".join(x for x in (game, platform) if x)
    if what:
        return "你好，%s！想约%s陪玩。" % (name, what)
    return "你好，%s！我想约你的陪玩服务。" % name


def booking_message(profile, mode_name, segments, total_hours, total_price, contact, contact_type):
    """拼一段给客户复制到微信的文案（格式和前台 buildMessage() 保持一致）"""
    lines = ["%d. %s–%s" % (i, cn_label(seg["start"]), cn_hhmm(seg["end"]))
             for i, seg in enumerate(segments, 1)]
    return "\n".join([
        booking_head(profile),
        "模式：%s" % mode_name,
        "游玩时间（北京时间）：",
    ] + lines + [
        "共 %d 段、%d 小时，参考费用 ¥%d。" % (len(segments), total_hours, total_price),
        "我的%s：%s，加我确认一下就行。" % (contact_label(contact_type), contact),
    ])


def create_booking(hours, mode_name, contact, contact_type, note):
    """
    客户自助预约的核心：复查空档 → 分段 → 落库。
    返回一个字典：成功是 {"ok": True, "locks": [...], ...}；
    失败是 {"ok": False, "error": "中文原因", "status": 400/409}（前端按 status 处理）。
    空档复查和写入都在同一把写锁里，两个人同时点也只会有一个成功。
    """
    profile = get_profile()
    mode = next((p for p in (profile.get("prices") or [])
                 if str(p.get("name")) == mode_name), None)
    if not mode:
        return {"ok": False, "error": "没有「%s」这个模式，请刷新页面重新选择。" % mode_name,
                "status": 400}
    unit_price = int(round(float(mode.get("price") or 0)))

    with _db_lock, connect() as conn:
        now_ms = int(time.time() * 1000)
        # 复查：只有"现在还是 free"的小时才能锁（别的客户可能刚抢走）
        status_of = {s["start"]: s["status"] for s in build_slots(now_ms)}
        unknown = [h for h in hours if h not in status_of]
        if unknown:
            return {"ok": False, "status": 400,
                    "error": "所选时间无效（不在可预约的时间表里），请刷新页面重新选择。"}
        conflicts = [h for h in hours if status_of[h] != "free"]
        if conflicts:
            return {"ok": False, "status": 409, "conflicts": conflicts,
                    "error": "这几段时间刚刚被别人预约了：%s。请重新选择。"
                             % "、".join(cn_label(h) for h in conflicts)}

        created = [{"start": run[0], "end": run[-1] + HOUR_MS, "hours": len(run),
                    "price": unit_price * len(run)}
                   for run in merge_hour_runs(hours)]
        for seg in created:
            insert_lock(conn, id=uuid.uuid4().hex, kind="booking", start=seg["start"],
                        end=seg["end"], client=contact, note=note, source="client",
                        mode=mode_name, price=seg["price"], contact=contact,
                        contact_type=contact_type, created_at=now_ms)

    total_hours = sum(seg["hours"] for seg in created)
    total_price = sum(seg["price"] for seg in created)
    return {"ok": True, "locks": created, "totalPrice": total_price, "totalHours": total_hours,
            "mode": mode_name, "contact": contact, "contactType": contact_type,
            "message": booking_message(profile, mode_name, created, total_hours, total_price,
                                       contact, contact_type)}


def booking_notice(mode_name, segments, total_hours, total_price, contact, contact_type, note):
    """客户预约成功后，推给老板的内容（标题 + 正文）。标题里带渠道，一眼就知道去哪联系人。"""
    span = "、".join("%s–%s" % (cn_label(seg["start"]), cn_hhmm(seg["end"])) for seg in segments)
    content = ("模式：%s\n时间（北京时间）：%s\n共 %d 小时，合计 ¥%d\n联系方式：%s %s\n备注：%s"
               % (mode_name, span, total_hours, total_price,
                  contact_label(contact_type), contact, note or "（没填）"))
    return "新的陪玩预约（%s）" % contact_label(contact_type), content


# 预约限流：{IP: [最近几次提交的时间戳]}，只放在内存里，进程一重启就重置（个人站够用了）
_book_hits = {}
_book_hits_lock = threading.Lock()


def book_rate_limited(ip, now=None):
    """
    同一个 IP 在 BOOK_RATE_WINDOW 秒内最多提交 BOOK_RATE_MAX 次预约。
    返回 (是否被限流, 还要等几秒)。挡的是"有人拿脚本刷预约"，不挡正常客户。
    """
    now = now or time.time()
    with _book_hits_lock:
        hits = [t for t in _book_hits.get(ip, []) if now - t < BOOK_RATE_WINDOW]
        if len(hits) >= BOOK_RATE_MAX:
            _book_hits[ip] = hits
            return True, int(BOOK_RATE_WINDOW - (now - hits[0])) + 1
        hits.append(now)
        _book_hits[ip] = hits
        # 顺手清理：字典太大的时候，把已经过期的 IP 一次性删掉
        # （不然有人拿随机 IP 一直刷，这个字典会一直涨）
        if len(_book_hits) > 5000:
            for key in [k for k, v in _book_hits.items() if not v or now - v[-1] >= BOOK_RATE_WINDOW]:
                _book_hits.pop(key, None)
    return False, 0


# ============================================================================
# 四、登录会话
#    Cookie 里放的是"过期时间 + 签名"，签名用数据库里的密码哈希当密钥。
#    这样做的好处：网站重启/休眠/重新部署之后，你依然是登录状态（不用反复输密码）；
#    而你一改密码，所有旧 Cookie 立刻失效。
# ============================================================================

COOKIE_NAME = "peiwan_session"


def session_key():
    """拿密码哈希当签名密钥——改密码 => 密钥变了 => 旧 Cookie 全部失效"""
    with connect() as conn:
        row = conn.execute("SELECT value FROM store WHERE key='password'").fetchone()
    return (row["value"] if row else "no-password-set").encode("utf-8")


def new_session():
    expires = int(time.time()) + SESSION_HOURS * 3600
    sig = hmac.new(session_key(), str(expires).encode(), hashlib.sha256).hexdigest()[:32]
    return "%d.%s" % (expires, sig)


def session_valid(token):
    if not token or "." not in token:
        return False
    payload, _, sig = token.partition(".")
    expect = hmac.new(session_key(), payload.encode(), hashlib.sha256).hexdigest()[:32]
    if not secrets.compare_digest(sig, expect):
        return False
    try:
        return int(payload) > time.time()
    except ValueError:
        return False


# ============================================================================
# 五、通知：有人在前台自助预约时，推一条消息到老板手机
#   配置存在 store 表里：
#     notify_channel  off / pushplus / serverchan / webhook
#     notify_target   token 或 webhook 网址
#     notify_last     上一次发送结果（JSON），后台拿它显示成功还是失败
#   发送放在后台线程里，客户提交完立刻拿到响应，不用等网络。
# ============================================================================

NOTIFY_CHANNELS = ("off", "pushplus", "serverchan", "webhook")
NOTIFY_NAMES = {"off": "关闭", "pushplus": "PushPlus", "serverchan": "Server酱",
                "webhook": "自定义 Webhook"}


def get_notify_config():
    """读通知配置 + 上一次的发送结果"""
    channel = get_store("notify_channel", "off") or "off"
    if channel not in NOTIFY_CHANNELS:
        channel = "off"                     # 数据库里是个不认识的渠道时，当成"关闭"
    try:
        last = json.loads(get_store("notify_last") or "{}") or {}
    except ValueError:
        last = {}
    return {"channel": channel, "target": get_store("notify_target", "") or "", "last": last}


def set_notify_last(ok, detail):
    """记下这次发送的结果（成功/失败原因 + 时间），后台刷新就能看到"""
    payload = {"ok": bool(ok), "time": int(time.time() * 1000), "detail": str(detail or "")[:300]}
    set_store("notify_last", json.dumps(payload, ensure_ascii=False))
    return payload


def send_notify(channel, target, title, content):
    """
    真正把通知发出去，返回 (是否成功, 说明文字)。
    三种渠道都只用标准库 urllib.request，统一 10 秒超时。
    """
    if channel == "off":
        return False, "通知渠道是「关闭」，不用发。"
    if not target:
        return False, "还没填 token 或 Webhook 网址。"
    try:
        if channel == "pushplus":
            payload = json.dumps({"token": target, "title": title,
                                  "content": content, "template": "txt"}).encode("utf-8")
            req = urllib.request.Request("https://www.pushplus.plus/send", data=payload,
                                         headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=NOTIFY_TIMEOUT) as resp:
                raw = resp.read().decode("utf-8", "replace")
            try:
                data = json.loads(raw)
            except ValueError:
                return False, "PushPlus 返回的内容看不懂：%s" % raw[:80]
            if str(data.get("code")) == "200":
                return True, "PushPlus 已发送"
            return False, "PushPlus 返回 code=%s %s" % (data.get("code"),
                                                        str(data.get("msg") or "")[:60])

        if channel == "serverchan":
            # Server酱（Turbo 版）的接口长这样：https://sc.ftqq.com/<SendKey>.send
            url = "https://sc.ftqq.com/%s.send" % quote(target, safe="")
            body = urlencode({"text": title, "desp": content}).encode("utf-8")
            req = urllib.request.Request(url, data=body, headers={
                "Content-Type": "application/x-www-form-urlencoded"})
            with urllib.request.urlopen(req, timeout=NOTIFY_TIMEOUT) as resp:
                raw = resp.read().decode("utf-8", "replace")
            try:
                data = json.loads(raw)
            except ValueError:
                return False, "Server酱 返回的内容看不懂：%s" % raw[:80]
            code = data.get("code")
            if code is None:
                code = data.get("errno")        # 老接口用 errno
            if str(code) == "0":
                return True, "Server酱 已发送"
            return False, "Server酱 返回：%s" % raw[:80]

        if channel == "webhook":
            if not str(target).lower().startswith(("http://", "https://")):
                return False, "Webhook 网址要以 http:// 或 https:// 开头。"
            payload = json.dumps({"title": title, "content": content,
                                  "text": "%s\n%s" % (title, content)}).encode("utf-8")
            req = urllib.request.Request(target, data=payload,
                                         headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=NOTIFY_TIMEOUT) as resp:
                resp.read()
                return True, "Webhook 已发送（HTTP %s）" % resp.status

        return False, "不认识的通知渠道：%s" % channel
    except urllib.error.HTTPError as e:
        # 403 / 401 多半是 token 不对，404 多半是网址写错
        return False, "HTTP %s（token 或网址可能不对）" % e.code
    except socket.timeout:
        return False, "超时（%d 秒没有响应）" % NOTIFY_TIMEOUT
    except urllib.error.URLError as e:
        if isinstance(getattr(e, "reason", None), socket.timeout):
            return False, "超时（%d 秒没有响应）" % NOTIFY_TIMEOUT
        return False, "连不上：%s" % str(getattr(e, "reason", e))[:120]
    except Exception as e:                  # 兜底：发通知出任何问题都不能影响客户下单
        return False, "%s: %s" % (type(e).__name__, str(e)[:120])


def notify_owner(title, content):
    """
    给老板发通知。
    **放在后台线程里发**：客户提交完立刻拿到"锁定成功"，不用等这个网络请求。
    发完把结果写进 notify_last；没配置渠道就静默跳过（不覆盖上次的测试结果）。
    """
    cfg = get_notify_config()
    if cfg["channel"] == "off" or not cfg["target"]:
        return False

    def worker():
        try:
            ok, detail = send_notify(cfg["channel"], cfg["target"], title, content)
        except Exception as e:              # pragma: no cover - 兜底
            ok, detail = False, "%s: %s" % (type(e).__name__, e)
        set_notify_last(ok, detail)
        print("[通知] %s：%s" % ("成功" if ok else "失败", detail), flush=True)

    threading.Thread(target=worker, daemon=True).start()
    return True


# ============================================================================
# 六、HTTP 接口
# ============================================================================

def escape_html(text):
    return (str(text or "")
            .replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;").replace("'", "&#39;"))


class Handler(BaseHTTPRequestHandler):
    server_version = "PeiwanSite/1.0"

    # ---------- 小工具 ----------

    def write_body(self, body):
        """HEAD 请求只要响应头、不要正文（分享预览爬虫会先发 HEAD）"""
        if getattr(self, "head_only", False):
            return
        self.wfile.write(body)          # 注意：这里必须直接写 socket，不能调用 write_body 自己

    def do_HEAD(self):
        """有些分享/预览爬虫（微信、QQ、微博）会先发 HEAD，返回 501 会让分享卡片失效"""
        self.head_only = True
        self.do_GET()

    def site_origin(self):
        """
        算出这个网站的完整网址，例如 https://peiwan-site.onrender.com
        微信分享卡片里的 og:image / og:url 必须是完整网址，不能是 /static/xxx 这种相对路径。
        """
        host = self.headers.get("Host") or ("127.0.0.1:%d" % PORT)
        proto = self.headers.get("X-Forwarded-Proto")
        if not proto:
            proto = "http" if host.split(":")[0] in ("127.0.0.1", "localhost") else "https"
        return "%s://%s" % (proto, host)

    def share_image_url(self, profile):
        """
        分享缩略图挑哪张：头像 → 首屏图 → 展示墙第一张公开图 → 自带的默认分享图。
        """
        for key in ("avatar", "heroImage"):
            url = str(profile.get(key) or "")
            if url:
                return url if url.startswith("http") else self.site_origin() + url
        for item in list_gallery(only_public=True):
            if item.get("image"):
                return self.site_origin() + item["image"]
        return self.site_origin() + "/static/img/share-default.jpg"

    def serve_index(self):
        """把主页 HTML 里的 {{占位符}} 换成你的资料，微信分享出去才有标题和简介"""
        path = os.path.join(TEMPLATE_DIR, "index.html")
        if not os.path.isfile(path):
            return self.send_json({"error": "缺少模板文件。"}, 500)
        with open(path, encoding="utf-8") as f:
            html = f.read()
        profile = get_profile()
        name = str(profile.get("name") or "个人陪玩")
        tagline = str(profile.get("tagline") or "")
        desc = " ".join(str(profile.get("announcement") or profile.get("bio") or "").split())
        if not desc:
            desc = "%s 的个人陪玩预约页面：先看空档时间，再联系确认。" % name
        title = ("%s · %s" % (name, tagline)) if tagline else name
        for key, value in (("{{TITLE}}", title), ("{{NAME}}", name),
                           ("{{TAGLINE}}", tagline), ("{{DESC}}", desc[:120]),
                           ("{{OG_IMAGE}}", self.share_image_url(profile)),
                           ("{{OG_URL}}", self.site_origin() + "/")):
            html = html.replace(key, escape_html(value))
        body = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")     # 改了资料立刻生效
        self.end_headers()
        self.write_body(body)

    def send_json(self, obj, status=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.write_body(body)

    def send_file(self, path, content_type=None, download_name=None):
        if not os.path.isfile(path):
            return self.send_json({"error": "文件不存在。"}, 404)
        with open(path, "rb") as f:
            body = f.read()
        self.send_response(200)
        self.send_header("Content-Type", content_type or "application/octet-stream")
        self.send_header("Content-Length", str(len(body)))
        if download_name:
            self.send_header("Content-Disposition", 'attachment; filename="%s"' % download_name)
        self.end_headers()
        self.write_body(body)

    def read_json(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except Exception:
            return None

    def cookie_token(self):
        raw = self.headers.get("Cookie") or ""
        for part in raw.split(";"):
            k, _, v = part.strip().partition("=")
            if k == COOKIE_NAME:
                return v
        return ""

    def is_admin(self):
        return session_valid(self.cookie_token())

    def require_admin(self):
        """没登录就回 401，前端看到 401 会自动跳回登录框。"""
        if self.is_admin():
            return True
        self.send_json({"error": "登录已失效，请重新输入管理员密码。"}, 401)
        return False

    def client_ip(self):
        """
        取客户真实 IP（限流用）。
        部署在 PythonAnywhere / Render 后面时，请求都是代理转发过来的，
        self.client_address 永远是代理的地址（那样所有人共用一个限流名额），
        所以优先看代理写进来的 X-Forwarded-For 的第一段。
        """
        forwarded = (self.headers.get("X-Forwarded-For") or "").split(",")[0].strip()
        if re.fullmatch(r"[0-9A-Fa-f:.]{3,45}", forwarded or ""):
            return forwarded
        return (self.client_address[0] if self.client_address else "") or "unknown"

    def log_message(self, fmt, *args):        # 让日志干净一点
        print("[%s] %s" % (datetime.now(CN).strftime("%H:%M:%S"), fmt % args))

    # ---------- 路由 ----------

    def do_GET(self):
        parsed = urlparse(self.path)
        # 浏览器会把中文文件名转成 %E8%83%8C... 这种编码，这里解码回来，
        # 否则你放一张"背景图.jpg"就会 404
        path, query = unquote(parsed.path), parse_qs(parsed.query)

        # 页面
        if path in ("/", "/index.html"):
            return self.serve_index()
        if path in ("/admin", "/admin/"):
            return self.send_file(os.path.join(TEMPLATE_DIR, "admin.html"), "text/html; charset=utf-8")

        # 静态资源（支持 static/img/xxx.jpg 这样的子目录，方便你放背景图）
        if path.startswith("/static/"):
            rel = path[len("/static/"):]
            safe = os.path.normpath(rel).replace("\\", "/")
            if not safe or safe.startswith("..") or safe.startswith("/") or safe == ".":
                return self.send_json({"error": "路径不合法。"}, 400)
            full = os.path.join(STATIC_DIR, *safe.split("/"))
            ext = safe.rsplit(".", 1)[-1].lower() if "." in safe else ""
            ctype = {
                "css": "text/css; charset=utf-8",
                "js": "application/javascript; charset=utf-8",
                "html": "text/html; charset=utf-8",
                "svg": "image/svg+xml",
                "png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
                "webp": "image/webp", "gif": "image/gif", "ico": "image/x-icon",
                "woff2": "font/woff2", "json": "application/json; charset=utf-8",
                "txt": "text/plain; charset=utf-8",
            }.get(ext)
            if not ctype:
                return self.send_json({"error": "不支持的文件类型。"}, 404)
            return self.send_file(full, ctype)

        # 图片
        if path.startswith("/api/media/"):
            key = os.path.basename(path)
            if not re.fullmatch(r"[A-Za-z0-9_.\-]+", key or ""):
                return self.send_json({"error": "文件名不合法。"}, 400)
            ext = key.rsplit(".", 1)[-1].lower()
            ctype = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png",
                     "webp": "image/webp", "gif": "image/gif"}.get(ext, "application/octet-stream")
            return self.send_file(os.path.join(UPLOAD_DIR, key), ctype)

        # ---- 公开接口 ----
        if path == "/api/ping":
            # 给"保活"用的极简接口：不查数据库、不算时间表，几乎不耗流量和 CPU。
            # 免费的定时访问服务（UptimeRobot 等）每 10 分钟打一次，实例就不会休眠，
            # 数据也就不会被清空。详见 DEPLOY.md
            return self.send_json({"ok": True, "time": int(time.time() * 1000)})

        if path == "/api/public":
            return self.send_json({
                "profile": get_profile(),
                "slots": build_slots(),
                "gallery": list_gallery(only_public=True),
                "serverTime": int(time.time() * 1000),
            })

        if path == "/api/availability":
            # 只读核对：前端传 ?hours=时间戳,时间戳……
            wanted = (query.get("hours") or [""])[0]
            if not wanted:
                return self.send_json({"error": "所选时间无效。"}, 400)
            try:
                hours = [int(x) for x in wanted.split(",") if x.strip()]
            except ValueError:
                return self.send_json({"error": "所选时间无效。"}, 400)
            if not hours:
                return self.send_json({"error": "所选时间无效。"}, 400)
            valid = {s["start"]: s["status"] for s in build_slots()}
            out = []
            for h in hours:
                if h not in valid:
                    return self.send_json({"error": "所选时间无效。"}, 400)
                out.append({"start": h, "status": valid[h]})
            return self.send_json({"slots": out})

        if path == "/api/auth":
            return self.send_json({"authenticated": self.is_admin()})

        # ---- 管理接口（都需要登录）----
        if path == "/api/admin":
            if not self.require_admin():
                return
            notify = get_notify_config()
            return self.send_json({
                "profile": get_profile(),
                "profileRevision": get_revision(),
                "lastExportAt": int(get_store("lastExportAt") or 0),
                "dataDir": DATA_DIR,   # 让后台显示"数据放在哪里"
                "schedule": {"locks": list_locks(), "rules": list_rules()},
                "gallery": list_gallery(),
                "notifyChannel": notify["channel"],     # 预约通知：渠道 / 目标 / 上次结果
                "notifyTarget": notify["target"],
                "notifyLast": notify["last"],
            })

        if path == "/api/admin/export":
            if not self.require_admin():
                return
            body = json.dumps(build_backup(), ensure_ascii=False, indent=2).encode("utf-8")
            set_store("lastExportAt", int(time.time() * 1000))   # 记下来，后台会提醒你"多久没备份了"
            stamp = datetime.now(CN).strftime("%Y%m%d-%H%M")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Content-Disposition", 'attachment; filename="peiwan-backup-%s.json"' % stamp)
            self.end_headers()
            self.write_body(body)
            return

        return self.send_json({"error": "接口不存在。"}, 404)

    def do_POST(self):
        path = urlparse(self.path).path
        body = self.read_json()
        if body is None:
            return self.send_json({"error": "请使用 JSON 格式提交。"}, 415)

        # 登录
        if path == "/api/auth":
            if not isinstance(body, dict) or not body.get("password"):
                return self.send_json({"error": "请输入密码。"}, 400)
            if not verify_password(str(body["password"])):
                time.sleep(0.6)                      # 稍微拖延，防止暴力猜密码
                return self.send_json({"error": "密码不正确。"}, 401)
            token = new_session()
            # 走 HTTPS 时（也就是部署到 Render 之后）加上 Secure，本地调试不加
            secure = "; Secure" if self.headers.get("X-Forwarded-Proto") == "https" else ""
            cookie = "%s=%s; Path=/; HttpOnly; SameSite=Lax; Max-Age=%d%s" % (
                COOKIE_NAME, token, SESSION_HOURS * 3600, secure)
            payload = json.dumps({"ok": True}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Set-Cookie", cookie)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.write_body(payload)
            return

        # 客户自助预约：公开接口，不用登录 —— 点了就把时间锁上（见 api_book）
        if path == "/api/book":
            return self.api_book(body)

        if not self.require_admin():
            return

        if path == "/api/admin/schedule":
            return self.admin_schedule(body)
        if path == "/api/admin/import":
            return self.admin_import(body)
        if path == "/api/admin/media":
            return self.admin_media_upload(body)
        if path == "/api/admin/gallery":
            return self.admin_gallery_create(body)
        if path == "/api/admin/password":
            return self.admin_password(body)
        if path == "/api/admin/notify":
            return self.admin_notify(body)
        if path == "/api/admin/notify-test":
            return self.admin_notify_test(body)

        return self.send_json({"error": "接口不存在。"}, 404)

    def do_PUT(self):
        path = urlparse(self.path).path
        body = self.read_json()
        if body is None:
            return self.send_json({"error": "请使用 JSON 格式提交。"}, 415)
        if not self.require_admin():
            return

        if path == "/api/admin/profile":
            profile = body.get("profile") or {}
            revision = body.get("revision") or 0
            if not isinstance(profile, dict):
                return self.send_json({"error": "资料格式不正确。"}, 400)
            ok, rev = save_profile(profile, revision)
            if not ok:
                return self.send_json({"error": "资料已在别处更新，请刷新后重试。"}, 409)
            return self.send_json({"ok": True, "profileRevision": rev, "profile": get_profile()})

        return self.send_json({"error": "接口不存在。"}, 404)

    def do_PATCH(self):
        path = urlparse(self.path).path
        body = self.read_json()
        if body is None:
            return self.send_json({"error": "请使用 JSON 格式提交。"}, 415)
        if not self.require_admin():
            return
        if path == "/api/admin/gallery":
            return self.admin_gallery_update(body)
        return self.send_json({"error": "接口不存在。"}, 404)

    def do_DELETE(self):
        path = urlparse(self.path).path
        if path == "/api/auth":
            payload = json.dumps({"ok": True}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Set-Cookie", "%s=; Path=/; Max-Age=0" % COOKIE_NAME)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.write_body(payload)
            return
        if path == "/api/admin/gallery":
            if not self.require_admin():
                return
            body = self.read_json() or {}
            gid = body.get("id")
            with _db_lock, connect() as conn:
                row = conn.execute("SELECT image FROM gallery WHERE id=?", (gid,)).fetchone()
                conn.execute("DELETE FROM gallery WHERE id=?", (gid,))
            if row and row["image"]:
                f = os.path.join(UPLOAD_DIR, os.path.basename(row["image"]))
                if os.path.isfile(f):
                    os.remove(f)
            return self.send_json({"ok": True, "gallery": list_gallery()})
        return self.send_json({"error": "接口不存在。"}, 404)

    # ---------- 客户自助预约 ----------

    def api_book(self, body):
        """
        POST /api/book —— 客户在前台点「立即锁定时间」时调它。
        请求：{"hours":[毫秒时间戳...], "mode":"机密 · 零号大坝", "contact":"联系方式",
               "contactType":"wechat|xianyu|qq", "note":"备注"}
        成功：{"ok":true, "locks":[…], "totalPrice":45, "totalHours":3, "mode":"…", "message":"微信文案"}
        失败状态码：400 参数不对 / 409 这几格被抢走了 / 429 提交太频繁
        """
        if not isinstance(body, dict):
            return self.send_json({"error": "请用 JSON 格式提交预约。"}, 400)

        # 1. 限流：先挡住"拿脚本刷预约"的（不影响正常客户，10 分钟内 5 次）
        limited, wait = book_rate_limited(self.client_ip())
        if limited:
            return self.send_json({"error": "提交太频繁了，请 %d 秒后再试。" % wait}, 429)

        # 2. 时间：必须非空、都是整点、不超过 MAX_BOOK_HOURS
        raw_hours = body.get("hours")
        if not isinstance(raw_hours, list) or not raw_hours:
            return self.send_json({"error": "请先选择要预约的时间。"}, 400)
        try:
            hours = sorted({int(h) for h in raw_hours})
        except (TypeError, ValueError):
            return self.send_json({"error": "所选时间无效，请刷新页面重新选择。"}, 400)
        if any(h % HOUR_MS != 0 for h in hours):
            return self.send_json({"error": "所选时间无效，请按整点选择。"}, 400)
        if len(hours) > MAX_BOOK_HOURS:
            return self.send_json(
                {"error": "一次最多预约 %d 小时，请分两次提交。" % MAX_BOOK_HOURS}, 400)

        # 3. 联系方式必填：锁了时间却联系不上人，等于白锁
        #    渠道可以是微信 / 闲鱼 / QQ；老前端没传 contactType 时按微信处理
        raw_type = str(body.get("contactType") or "").strip()
        contact_type = raw_type or CONTACT_DEFAULT
        if contact_type not in CONTACT_TYPES:
            return self.send_json(
                {"error": "联系方式渠道只能是微信 / 闲鱼 / QQ 这三种。"}, 400)
        contact = str(body.get("contact") or "").strip()[:40]
        if not 2 <= len(contact) <= 40:
            return self.send_json(
                {"error": "请填写你的联系方式（微信 / 闲鱼 / QQ），2–40 个字符，不然我加不上你。"}, 400)

        mode_name = str(body.get("mode") or "").strip()[:40]
        note = str(body.get("note") or "").strip()[:200]

        # 4. 复查空档 → 合并连续小时 → 落库（全在 create_booking 里，带写锁，防两个人抢同一格）
        result = create_booking(hours, mode_name, contact, contact_type, note)
        if not result.get("ok"):
            payload = {"error": result.get("error") or "预约没有成功，请重试。"}
            if result.get("conflicts"):
                payload["conflicts"] = result["conflicts"]
            return self.send_json(payload, result.get("status") or 400)

        # 5. 通知老板（后台线程发，客户不用等）
        title, content = booking_notice(result["mode"], result["locks"], result["totalHours"],
                                        result["totalPrice"], contact, contact_type, note)
        notify_owner(title, content)
        return self.send_json(result)

    # ---------- 管理动作 ----------

    def admin_schedule(self, body):
        action = body.get("action")
        with _db_lock, connect() as conn:
            if action == "save-lock":
                start, end = int(body.get("start") or 0), int(body.get("end") or 0)
                if end <= start:
                    return self.send_json({"error": "结束时间必须晚于开始时间。"}, 400)
                kind = "rest" if body.get("kind") == "rest" else "booking"
                client = str(body.get("client") or "")[:40]
                note = str(body.get("note") or "")[:200]
                lock_id = body.get("id")
                if lock_id:
                    # 编辑已有锁定：source / mode / price / contact 原样保留
                    # （客户自助预约被你在后台改时间时，来源标记不该丢）
                    conn.execute("UPDATE locks SET kind=?, start=?, end=?, client=?, note=? WHERE id=?",
                                 (kind, start, end, client, note, lock_id))
                else:
                    insert_lock(conn, id=uuid.uuid4().hex, kind=kind, start=start, end=end,
                                client=client, note=note, source="owner", mode="", price=0,
                                contact="", contact_type="", created_at=int(time.time() * 1000))

            elif action == "save-batch":
                hours = sorted({int(h) for h in (body.get("hours") or [])})
                if not hours:
                    return self.send_json({"error": "请先选择时间。"}, 400)
                if len(hours) > MAX_SELECT_HOURS:
                    return self.send_json({"error": "一次最多选择 %d 小时。" % MAX_SELECT_HOURS}, 400)
                kind = "rest" if body.get("kind") == "rest" else "booking"
                client = str(body.get("client") or "")[:40]
                note = str(body.get("note") or "")[:200]
                existing = [dict(r) for r in conn.execute("SELECT * FROM locks").fetchall()]
                fresh = [h for h in hours
                         if not any(lk["start"] < h + HOUR_MS and h < lk["end"] for lk in existing)]
                # 把连续的时段合并成一段，看起来更清爽（和客户自助预约用的是同一个函数）
                created = int(time.time() * 1000)
                for run in merge_hour_runs(fresh):
                    insert_lock(conn, id=uuid.uuid4().hex, kind=kind, start=run[0],
                                end=run[-1] + HOUR_MS, client=client, note=note, source="owner",
                                mode="", price=0, contact="", contact_type="", created_at=created)

            elif action == "remove-lock":
                conn.execute("DELETE FROM locks WHERE id=?", (body.get("id"),))

            elif action == "save-rule":
                days = sorted({int(d) for d in (body.get("days") or []) if 0 <= int(d) <= 6})
                if not days:
                    return self.send_json({"error": "请至少选择一个星期。"}, 400)
                start_hour = max(0, min(23, int(body.get("start_hour") or 0)))
                end_hour = max(0, min(24, int(body.get("end_hour") or 0)))
                if start_hour == end_hour:
                    return self.send_json({"error": "开始和结束时间不能相同。"}, 400)
                from_ts = int(body.get("from_ts") or midnight_ms(int(time.time() * 1000)))
                until_ts = body.get("until_ts") or None
                note = str(body.get("note") or "")[:40]
                enabled = 1 if body.get("enabled", True) else 0
                rule_id = body.get("id")
                if rule_id:
                    conn.execute("""UPDATE rules SET days=?, start_hour=?, end_hour=?, from_ts=?,
                                    until_ts=?, note=?, enabled=? WHERE id=?""",
                                 (json.dumps(days), start_hour, end_hour, from_ts,
                                  int(until_ts) if until_ts else None, note, enabled, rule_id))
                else:
                    conn.execute("""INSERT INTO rules(id, days, start_hour, end_hour, from_ts,
                                    until_ts, note, enabled) VALUES(?,?,?,?,?,?,?,?)""",
                                 (uuid.uuid4().hex, json.dumps(days), start_hour, end_hour,
                                  from_ts, int(until_ts) if until_ts else None, note, enabled))

            elif action == "toggle-rule":
                conn.execute("UPDATE rules SET enabled=? WHERE id=?",
                             (1 if body.get("enabled") else 0, body.get("id")))

            elif action == "remove-rule":
                conn.execute("DELETE FROM rules WHERE id=?", (body.get("id"),))

            else:
                return self.send_json({"error": "不支持的操作。"}, 400)

        return self.send_json({"ok": True, "schedule": {"locks": list_locks(), "rules": list_rules()}})

    def admin_gallery_create(self, body):
        title = str(body.get("title") or "")[:60]
        caption = str(body.get("caption") or "")[:300]
        category = "review" if body.get("category") == "review" else "screenshot"
        visible = 1 if body.get("visible") else 0
        if not title:
            return self.send_json({"error": "请填写标题。"}, 400)
        url, error = save_data_url(body.get("image") or "")
        if error:
            return self.send_json({"error": error}, 400)
        with _db_lock, connect() as conn:
            conn.execute("""INSERT INTO gallery(id, title, caption, category, image, visible,
                            archived, created_at) VALUES(?,?,?,?,?,?,0,?)""",
                         (uuid.uuid4().hex, title, caption, category, url,
                          visible, int(time.time() * 1000)))
        return self.send_json({"ok": True, "gallery": list_gallery()})

    def admin_media_upload(self, body):
        """通用图片上传：头像、主页背景图、首屏图都用它。只返回网址，不改资料。"""
        url, error = save_data_url(body.get("image") or "")
        if error:
            return self.send_json({"error": error}, 400)
        return self.send_json({"ok": True, "url": url})

    def admin_gallery_update(self, body):
        gid = body.get("id")
        fields, values = [], []
        for key in ("title", "caption"):
            if key in body:
                fields.append("%s=?" % key)
                values.append(str(body[key])[:300])
        if "category" in body:
            fields.append("category=?")
            values.append("review" if body["category"] == "review" else "screenshot")
        for key in ("visible", "archived"):
            if key in body:
                fields.append("%s=?" % key)
                values.append(1 if body[key] else 0)
        if not fields:
            return self.send_json({"error": "没有要修改的内容。"}, 400)
        values.append(gid)
        with _db_lock, connect() as conn:
            conn.execute("UPDATE gallery SET %s WHERE id=?" % ", ".join(fields), values)
        return self.send_json({"ok": True, "gallery": list_gallery()})

    def admin_import(self, body):
        """从备份 JSON 恢复：整体覆盖预约时段、休息规则、资料和展示墙。"""
        data = body.get("backup") if isinstance(body, dict) and isinstance(body.get("backup"), dict) else body
        summary, error = apply_backup(data)
        if error:
            return self.send_json({"error": error}, 400)
        return self.send_json({
            "ok": True, "summary": summary,
            "profile": get_profile(), "profileRevision": summary["profileRevision"],
            "schedule": {"locks": list_locks(), "rules": list_rules()},
            "gallery": list_gallery(),
        })

    def admin_password(self, body):
        current = str(body.get("current") or "")
        new = str(body.get("next") or "")
        if not verify_password(current):
            return self.send_json({"error": "当前密码不正确。"}, 401)
        if len(new) < 6:
            return self.send_json({"error": "新密码至少 6 位。"}, 400)
        set_password(new)
        # 不用手动清会话：Cookie 的签名密钥就是密码哈希，改了密码旧 Cookie 自动失效
        return self.send_json({"ok": True})

    def admin_notify(self, body):
        """保存预约通知的渠道与目标（token 或 webhook 网址）"""
        channel = str(body.get("channel") or "off")
        if channel not in NOTIFY_CHANNELS:
            return self.send_json({"error": "不支持的通知渠道。"}, 400)
        target = str(body.get("target") or "").strip()[:200]
        if channel != "off" and not target:
            return self.send_json({"error": "请填写 token 或 Webhook 网址。"}, 400)
        if channel == "webhook" and not target.lower().startswith(("http://", "https://")):
            return self.send_json({"error": "Webhook 网址要以 http:// 或 https:// 开头。"}, 400)
        set_store("notify_channel", channel)
        set_store("notify_target", target)
        return self.send_json({"ok": True, "notifyChannel": channel, "notifyTarget": target,
                               "notifyLast": get_notify_config()["last"]})

    def admin_notify_test(self, body):
        """
        用当前配置发一条测试通知。
        这里**同步发**（不像客户预约那样丢到后台线程），因为你点了按钮就要立刻看到结果。
        """
        cfg = get_notify_config()
        if cfg["channel"] == "off":
            return self.send_json({"ok": False, "detail": "通知渠道是「关闭」，先选一个渠道再点测试。",
                                   "notifyLast": cfg["last"]})
        ok, detail = send_notify(cfg["channel"], cfg["target"], "陪玩站测试通知",
                                 "你在微信里看到这条，就说明预约通知配置成功了。\n"
                                 "（这是后台「发送测试通知」发的）")
        last = set_notify_last(ok, detail)
        return self.send_json({"ok": ok, "detail": detail, "notifyLast": last})


def main():
    init_db()
    notify = get_notify_config()
    # flush=True：日志立刻输出。Render 的日志窗口是按行读的，不加这句会"卡住不显示"
    print("=" * 60, flush=True)
    print(" 陪玩预约站已启动", flush=True)
    print(" 本地访问：http://127.0.0.1:%d" % PORT, flush=True)
    print(" 管理后台：http://127.0.0.1:%d/admin" % PORT, flush=True)
    print(" 数据文件：%s" % DB_PATH, flush=True)
    print(" 预约通知：%s" % (NOTIFY_NAMES.get(notify["channel"], notify["channel"])
                          + ("（已配置，客户预约会推送）" if notify["target"] else "（未配置）")), flush=True)
    print("=" * 60, flush=True)
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
