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
import sqlite3
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs, unquote

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
MAX_SELECT_HOURS = 72          # 一次最多能勾选多少小时
SESSION_HOURS = 12             # 管理员登录后多少小时过期

# 首次启动时的管理员密码（之后请在"安全与备份"里改掉）
DEFAULT_PASSWORD = os.environ.get("ADMIN_PASSWORD") or "baixu123"
PORT = int(os.environ.get("PORT") or 8123)
HOST = os.environ.get("HOST") or "0.0.0.0"   # Render 要求监听 0.0.0.0

# 首次启动时写入的默认资料（换成你自己的即可，也可以在后台直接改）
DEFAULT_PROFILE = {
    "name": "败絮",
    "tagline": "三角洲行动 · PC 端个人陪玩",
    "headline": "一起出发。\n尽兴而归。",
    "bio": "技术在线，情绪价值拉满。\n个人接单，非工作室，无中间商。猛攻、跑刀、任务、熟图，或者只是想找个人边聊边玩，都可以。听你指挥，也陪你放松。",
    "experience": "",
    "wechat": "NekoByNya",
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
    id     TEXT PRIMARY KEY,
    kind   TEXT NOT NULL,
    start  INTEGER NOT NULL,                    -- 起始毫秒时间戳
    end    INTEGER NOT NULL,                    -- 结束毫秒时间戳
    client TEXT DEFAULT '',                     -- 老板称呼（仅自己可见）
    note   TEXT DEFAULT ''                      -- 私密备注
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


def init_db():
    with connect() as conn:
        conn.executescript(SCHEMA)
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
        try:
            locks.append((str(lk.get("id") or uuid.uuid4().hex),
                          "rest" if lk.get("kind") == "rest" else "booking",
                          int(lk["start"]), int(lk["end"]),
                          str(lk.get("client") or "")[:40], str(lk.get("note") or "")[:200]))
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
        conn.executemany("INSERT INTO locks(id, kind, start, end, client, note) VALUES(?,?,?,?,?,?)", locks)
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
# 五、HTTP 接口
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
            return self.send_json({
                "profile": get_profile(),
                "profileRevision": get_revision(),
                "lastExportAt": int(get_store("lastExportAt") or 0),
                "dataDir": DATA_DIR,   # 让后台显示"数据放在哪里"
                "schedule": {"locks": list_locks(), "rules": list_rules()},
                "gallery": list_gallery(),
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
                    conn.execute("UPDATE locks SET kind=?, start=?, end=?, client=?, note=? WHERE id=?",
                                 (kind, start, end, client, note, lock_id))
                else:
                    conn.execute("INSERT INTO locks(id, kind, start, end, client, note) VALUES(?,?,?,?,?,?)",
                                 (uuid.uuid4().hex, kind, start, end, client, note))

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
                # 把连续的时段合并成一段，看起来更清爽
                runs, run = [], []
                for h in fresh:
                    if run and h - run[-1] != HOUR_MS:
                        runs.append(run)
                        run = []
                    run.append(h)
                if run:
                    runs.append(run)
                for r in runs:
                    conn.execute("INSERT INTO locks(id, kind, start, end, client, note) VALUES(?,?,?,?,?,?)",
                                 (uuid.uuid4().hex, kind, r[0], r[-1] + HOUR_MS, client, note))

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


def main():
    init_db()
    # flush=True：日志立刻输出。Render 的日志窗口是按行读的，不加这句会"卡住不显示"
    print("=" * 60, flush=True)
    print(" 陪玩预约站已启动", flush=True)
    print(" 本地访问：http://127.0.0.1:%d" % PORT, flush=True)
    print(" 管理后台：http://127.0.0.1:%d/admin" % PORT, flush=True)
    print(" 数据文件：%s" % DB_PATH, flush=True)
    print("=" * 60, flush=True)
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
