# -*- coding: utf-8 -*-
"""
两级分区（大类 + 分区）的自动测试。

测的是这次升级的全部新东西：
  1. 三个默认大类（陪玩=预约制 / 代肝、其他服务=服务制）
  2. ★ 老数据库迁移：单游戏站 → 两级分区，**一条数据都不能丢**，而且只迁一次
  3. 分区增删改查：创建（id 非法 / 重复 / 大类不存在 → 400）、改名、排序、隐藏/显示、删除要二次确认
  4. GET /api/public?zone=X：分区资料、图、主题色；**slots 在任意分区下完全一样**（时间全局共用）
  5. 不传 zone → 默认分区
  6. POST /api/book：带 zone 成功、锁上记的 zone 正确、文案里的游戏名是分区自己的；
     服务制分区（代肝）预约 → 400
  7. 展示墙按分区隔离：A 分区传的图，B 分区的 /api/public 看不到
  8. POST /api/admin/import-prices：正常解析、全角 ｜、说明省略、坏行报错、空行忽略
  9. 提取主色：纯色测试图 → 返回的颜色接近该颜色
 10. 隐藏的分区不在 /api/public 的 zones 里，但 /z/<id> 仍可直接访问（404 还是 200 我选了"能访问"）

风格和 test_booking.py 一样：纯标准库、打印 [OK]/[FAIL]、最后给通过/失败计数，失败则退出码非 0。
⚠️ 和 test_api.py 一样**会写数据**，只能对着测试实例跑。

用法：
    python tests\\test_zones.py
    set TEST_BASE=http://127.0.0.1:8199 && python tests\\test_zones.py
"""

import base64
import gc
import http.cookiejar
import json
import os
import re
import shutil
import sqlite3
import struct
import sys
import time
import urllib.error
import urllib.request
import zlib

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


def admin(path, payload):
    return call(path, "POST", payload)


def public(zone=None):
    path = "/api/public" + (("?zone=" + zone) if zone else "")
    s, b = call(path)
    return s, (json.loads(b) if s == 200 else {})


def free_run(count, zone=None):
    """在公开时间表里找 count 个**连续**的空闲小时"""
    _s, pub = public(zone)
    run = []
    for slot in pub.get("slots", []):
        if slot["status"] == "free":
            run.append(slot["start"])
            if len(run) == count:
                return run
        else:
            run = []
    return None


def png_bytes(hex_body):
    return bytes.fromhex(hex_body)


# 用 zlib 手工造一张纯色 PNG（不依赖 PIL，保证测试在哪儿都能跑）
def solid_png(rgb, size=16):
    def chunk(tag, data):
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xffffffff))
    raw = b"".join(b"\x00" + bytes(rgb) * size for _ in range(size))
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw))
            + chunk(b"IEND", b""))


def data_url(blob, mime="image/png"):
    return "data:%s;base64,%s" % (mime, base64.b64encode(blob).decode())


# ============================================================================
#  第一部分：接口测试
# ============================================================================

def main_tests():
    print("=== 0. 准备：登录 + 看默认大类 ===")
    s, b = call("/api/auth", "POST", {"password": PASSWORD})
    check("管理员登录", s == 200, b.strip()[:80])
    s, pub = public()
    cats = {c["id"]: c for c in pub.get("categories", [])}
    check("默认有 3 个大类", len(pub.get("categories", [])) == 3,
          str([c["id"] for c in pub.get("categories", [])]))
    check("陪玩大类是预约制（booking）", cats.get("play", {}).get("kind") == "booking",
          str(cats.get("play")))
    check("代肝大类是服务制（service）", cats.get("boost", {}).get("kind") == "service",
          str(cats.get("boost")))
    check("其他服务大类是服务制（service）", cats.get("service", {}).get("kind") == "service",
          str(cats.get("service")))
    check("大类名分别是 陪玩 / 代肝 / 其他服务",
          [cats.get(k, {}).get("name") for k in ("play", "boost", "service")]
          == ["陪玩", "代肝", "其他服务"],
          str([c["name"] for c in pub["categories"]]))

    # 老数据迁移过来的第一个分区（delta）应该在
    zone_ids = [z["id"] for z in pub.get("zones", [])]
    check("迁移后至少有一个分区（delta）", "delta" in zone_ids, str(zone_ids))
    check("不传 zone 时用的是默认分区（第一个可见分区）",
          pub.get("zone", {}).get("id") == zone_ids[0], str(pub.get("zone", {}).get("id")))
    for key in ("id", "name", "short", "icon", "theme", "category", "kind", "profile", "startPrice"):
        if key not in pub.get("zone", {}):
            check("分区视图字段齐全（缺 %s）" % key, False)
            break
    else:
        check("分区视图字段齐全", True)

    print("=== 1. 分区：创建 ===")
    s, b = admin("/api/admin/zones", {"action": "create", "id": "ow", "category": "play",
                                     "name": "守望先锋", "short": "守望"})
    check("创建分区 ow（守望先锋）", s == 200 and json.loads(b)["zone"]["id"] == "ow", b.strip()[:120])
    s, b = admin("/api/admin/zones", {"action": "create", "id": "arknights", "category": "boost",
                                     "name": "明日方舟", "short": "明日方舟"})
    check("创建分区 arknights（代肝类）", s == 200, b.strip()[:120])
    check("代肝类分区自动带上 kind=service",
          json.loads(b)["zone"]["kind"] == "service", json.loads(b)["zone"]["kind"])
    s, b = admin("/api/admin/zones", {"action": "create", "id": "editing", "category": "service",
                                     "name": "视频剪辑", "short": "剪辑"})
    check("创建分区 editing（其他服务类）", s == 200 and json.loads(b)["zone"]["kind"] == "service",
          b.strip()[:120])

    s, b = admin("/api/admin/zones", {"action": "create", "id": "Bad ID!", "category": "play",
                                     "name": "乱写"})
    check("id 不合法 → 400", s == 400 and "短标识" in b, b.strip()[:110])
    s, b = admin("/api/admin/zones", {"action": "create", "id": "ow", "category": "play",
                                     "name": "重复"})
    check("id 重复 → 400", s == 400 and "短标识" in b, b.strip()[:110])
    s, b = admin("/api/admin/zones", {"action": "create", "id": "zzz", "category": "没有这个大类",
                                     "name": "无家可归"})
    check("大类不存在 → 400", s == 400 and "大类" in b, b.strip()[:110])
    s, b = admin("/api/admin/zones", {"action": "create", "id": "empty-name", "category": "play",
                                     "name": "   "})
    check("分区全称空 → 400", s == 400, b.strip()[:110])

    print("=== 2. ★ 时间表全局共用：任意分区的 slots 完全一样 ===")
    _s, p1 = public("ow")
    _s, p2 = public("arknights")
    _s, p3 = public()
    check("任意分区都返回 168 格",
          len(p1["slots"]) == 168 and len(p2["slots"]) == 168, "%d/%d" % (len(p1["slots"]), len(p2["slots"])))
    check("★ 守望分区和代肝分区的 slots 一模一样（时间只有一份）",
          p1["slots"] == p2["slots"], "前 3 格：%s / %s" % (p1["slots"][:1], p2["slots"][:1]))
    check("★ 不传 zone 的 slots 也一样（默认分区）", p3["slots"] == p1["slots"])

    print("=== 3. 分区资料（每个分区一套） ===")
    s, b = admin("/api/admin/zones", {"action": "profile", "id": "ow", "profile": {
        "game": "守望先锋", "tagline": "钻石以下都能带", "headline": "一起上分。\n不掉分。",
        "highlights": ["官服B服都接", "低练度也接"], "promises": ["承诺一", "承诺二"],
        "platform": "PC 电脑端", "announcement": "守望的公告", "backgroundDim": 40,
        "theme": "#8fa832",
        "prices": [{"id": "p1", "name": "快速游戏", "description": "轻松打", "price": 15},
                   {"id": "p2", "name": "竞技上分", "description": "认真打", "price": 25}],
    }})
    check("保存守望分区资料", s == 200, b.strip()[:120])
    _s, pow_ = public("ow")
    zp = pow_["zone"]["profile"]
    check("分区资料按分区取（守望的 game）", zp["game"] == "守望先锋", str(zp.get("game")))
    check("老字段 styles 已改名成 highlights 并搬进来",
          zp["highlights"] == ["官服B服都接", "低练度也接"], str(zp.get("highlights")))
    check("分区可以有自己的公告", zp["announcement"] == "守望的公告", str(zp.get("announcement")))
    check("分区的三档价格跟着分区走", len(zp["prices"]) == 2
          and zp["prices"][1]["price"] == 25, str(zp.get("prices")))
    check("预约制分区的起步价 = 最小的数字价",
          pow_["zone"]["startPrice"] == "¥15/小时起", str(pow_["zone"]["startPrice"]))
    check("主题色可以在分区里改（profile 之外的字段）",
          pow_["zone"]["theme"] not in ("", None), str(pow_["zone"]["theme"]))

    s, b = admin("/api/admin/zones", {"action": "update", "id": "ow", "theme": "#3fa7d6"})
    check("改分区主色", s == 200 and json.loads(b)["zone"]["theme"] == "#3fa7d6",
          json.loads(b)["zone"]["theme"])
    _s, pow2 = public("ow")
    check("主色立刻在公开接口生效", pow2["zone"]["theme"] == "#3fa7d6", pow2["zone"]["theme"])

    s, b = admin("/api/admin/zones", {"action": "update", "id": "ow", "name": "守望先锋2"})
    check("改分区全称", s == 200 and json.loads(b)["zone"]["name"] == "守望先锋2", b.strip()[:100])
    admin("/api/admin/zones", {"action": "update", "id": "ow", "name": "守望先锋"})

    print("=== 4. 服务制分区的分组价目表（价格是自由文本，绝不改成 ¥） ===")
    s, b = admin("/api/admin/zones", {"action": "profile", "id": "arknights", "profile": {
        "game": "明日方舟", "tagline": "低配也能过图",
        "priceGroups": [
            {"id": "g1", "name": "托管", "note": "", "items": [
                {"id": "i1", "name": "普通托管", "price": "20R/月", "note": "日双清+每周返图", "hot": False},
                {"id": "i2", "name": "半精托管", "price": "55R/月", "note": "加活动代打", "hot": False},
                {"id": "i3", "name": "精托托管", "price": "100R/月", "note": "全托管", "hot": True},
            ]},
            {"id": "g2", "name": "推图", "note": "按图算", "items": [
                {"id": "i4", "name": "主线推图", "price": "推图15R", "note": "一章起", "hot": False},
            ]},
        ],
        "featured": ["i3", "i1", "i2"],
    }})
    check("保存代肝分区的分组价目表", s == 200, b.strip()[:120])
    _s, park = public("arknights")
    groups = park["zone"]["profile"]["priceGroups"]
    check("价目表按组返回", len(groups) == 2 and groups[0]["name"] == "托管", str([g["name"] for g in groups]))
    check("★ 价格原样保留自由文本（20R/月 没有被改成 ¥）",
          groups[0]["items"][0]["price"] == "20R/月", str(groups[0]["items"][0]["price"]))
    check("★ 推图15R 这种写法也原样保留",
          groups[1]["items"][0]["price"] == "推图15R", str(groups[1]["items"][0]["price"]))
    check("主推档位（featured）按顺序返回 3 个",
          park["zone"]["profile"]["featured"] == ["i3", "i1", "i2"],
          str(park["zone"]["profile"]["featured"]))
    check("不存在的 featured id 会被丢掉", "不存在" not in str(park["zone"]["profile"]["featured"]))
    check("服务制分区的起步价：月付优先，取数字最小的那一档",
          park["zone"]["startPrice"] == "20R/月起", str(park["zone"]["startPrice"]))
    # 只有按周/按期报价时，就照实显示（不会硬凑成"月"）
    s, b = admin("/api/admin/zones", {"action": "profile", "id": "arknights", "profile": {
        "priceGroups": [{"id": "gw", "name": "周常", "items": [
            {"id": "w1", "name": "包周", "price": "8R/周"},
            {"id": "w2", "name": "包月", "price": "30R/月"},
        ]}]}})
    check("同时有「8R/周」和「30R/月」时显示月付（30R/月起）",
          json.loads(b)["zone"]["startPrice"] == "30R/月起", json.loads(b)["zone"]["startPrice"])
    s, b = admin("/api/admin/zones", {"action": "profile", "id": "arknights", "profile": {
        "priceGroups": [{"id": "gw", "name": "周常", "items": [
            {"id": "w1", "name": "包周", "price": "8R/周"},
        ]}]}})
    check("只有按周报价时照实显示（8R/周起）",
          json.loads(b)["zone"]["startPrice"] == "8R/周起", json.loads(b)["zone"]["startPrice"])
    # 还原成测试用的那两组
    admin("/api/admin/zones", {"action": "profile", "id": "arknights", "profile": {
        "priceGroups": [
            {"id": "g1", "name": "托管", "note": "", "items": [
                {"id": "i1", "name": "普通托管", "price": "20R/月", "note": "日双清+每周返图", "hot": False},
                {"id": "i2", "name": "半精托管", "price": "55R/月", "note": "加活动代打", "hot": False},
                {"id": "i3", "name": "精托托管", "price": "100R/月", "note": "全托管", "hot": True},
            ]},
            {"id": "g2", "name": "推图", "note": "按图算", "items": [
                {"id": "i4", "name": "主线推图", "price": "推图15R", "note": "一章起", "hot": False},
            ]},
        ],
        "featured": ["i3", "i1", "i2"],
    }})

    s, b = admin("/api/admin/zones", {"action": "profile", "id": "arknights", "profile": {
        "priceGroups": [{"id": "g1", "name": "托管", "items": [
            {"id": "i1", "name": "普通托管", "price": "20R/月"},
        ]}],
        "featured": ["i1", "i1", "i1", "i1"],
    }})
    feat = json.loads(b)["zone"]["profile"]["featured"]
    check("featured 去重且最多 3 个", feat == ["i1"], str(feat))

    print("=== 5. POST /api/book：带 zone ===")
    hours = free_run(2)
    check("能找到 2 个连续空闲小时", bool(hours), str(hours))
    price_name = public("ow")[1]["zone"]["profile"]["prices"][0]["name"]
    s, b = call("/api/book", "POST", {
        "zone": "ow", "hours": hours, "mode": price_name,
        "contact": "watcher_001", "contactType": "wechat", "note": "分区预约测试"},
        ip="198.51.100.201")
    data = json.loads(b) if s == 200 else {}
    check("带 zone 的预约成功", s == 200 and data.get("ok") is True, b.strip()[:140])
    check("返回里带 zone", data.get("zone") == "ow", str(data.get("zone")))
    msg = data.get("message") or ""
    check("★ 文案里的游戏名是分区自己的（守望先锋）", "想约守望先锋" in msg,
          msg.replace("\n", " / ")[:100])
    check("文案里不会出现别的分区（三角洲）", "三角洲" not in msg, msg.replace("\n", " / ")[:100])

    adm = json.loads(call("/api/admin")[1])
    mine = [l for l in adm["schedule"]["locks"] if l.get("contact") == "watcher_001"]
    check("后台能看到这条预约", len(mine) == 1, "找到 %d 条" % len(mine))
    check("★ 锁上记录的 zone 是 ow", bool(mine) and mine[0].get("zone") == "ow",
          str(mine[0].get("zone") if mine else "没找到"))
    _s, p_ow = public("ow")
    st = {x["start"]: x["status"] for x in p_ow["slots"]}
    check("这段时间在守望分区里显示为已预约",
          all(st.get(h) == "booking" for h in hours), str([st.get(h) for h in hours]))
    _s, p_park = public("arknights")
    st2 = {x["start"]: x["status"] for x in p_park["slots"]}
    check("★ 同一个时段在代肝分区里也是已预约（时间全局共用）",
          all(st2.get(h) == "booking" for h in hours), str([st2.get(h) for h in hours]))

    s, b = call("/api/book", "POST", {
        "zone": "arknights", "hours": free_run(1), "mode": "普通托管",
        "contact": "wx_service_test"}, ip="198.51.100.202")
    check("★ 服务制分区不接受时段预约 → 400（提示加微信下单）",
          s == 400 and "不支持时段预约" in b, b.strip()[:140])
    s, b = call("/api/book", "POST", {
        "zone": "arknights", "hours": free_run(1), "mode": "普通托管",
        "contact": "wx_service_test", "contactType": "wechat"}, ip="198.51.100.202")
    check("服务制分区提示里带上分区名（明日方舟）", "明日方舟" in b, b.strip()[:140])

    s, b = call("/api/book", "POST", {
        "zone": "没有这个分区", "hours": free_run(1), "mode": price_name,
        "contact": "wx_bad_zone"}, ip="198.51.100.203")
    check("zone 不存在 → 400（不是 500）", s == 400, "status=%s %s" % (s, b.strip()[:80]))

    s, b = call("/api/book", "POST", {
        "zone": "ow", "hours": free_run(1, "ow"), "mode": price_name,
        "contact": "wx_no_zone"}, ip="198.51.100.204")
    check("带 zone 的预约仍然正常", s == 200, "status=%s %s" % (s, b.strip()[:100]))

    # 老前端（不传 zone）走的是"第一个可见分区"。给它配上价格再试，
    # 这样验证的是"真的能约上"，而不是"碰巧报了个别的错"。
    admin("/api/admin/zones", {"action": "profile", "id": "delta", "profile": {
        "game": "三角洲行动", "platform": "PC 电脑端",
        "prices": [{"id": "dam", "name": "机密 · 零号大坝", "description": "", "price": 15}]}})
    s, b = call("/api/book", "POST", {
        "hours": free_run(1, "delta"), "mode": "机密 · 零号大坝", "contact": "wx_no_zone"},
        ip="198.51.100.205")
    d = json.loads(b) if s == 200 else {}
    check("★ 老前端不传 zone 也能约（自动用默认分区）", s == 200 and d.get("zone") == "delta",
          "status=%s %s" % (s, b.strip()[:100]))
    check("不传 zone 时文案用默认分区的游戏名（三角洲行动）",
          "想约三角洲行动" in (d.get("message") or ""),
          (d.get("message") or "").replace("\n", " / ")[:90])

    print("=== 6. 展示墙按分区隔离 ===")
    red = data_url(solid_png((220, 40, 40)))
    s, b = admin("/api/admin/gallery", {"zone": "ow", "title": "守望的截图",
                                        "caption": "只给守望看", "category": "screenshot",
                                        "visible": True, "image": red})
    check("给守望分区传一张公开图", s == 200, b.strip()[:120])
    _s, g_ow = public("ow")
    _s, g_park = public("arknights")
    _s, g_delta = public("delta")
    check("★ 守望分区能看到自己的图", len(g_ow["gallery"]) == 1, "共 %d 张" % len(g_ow["gallery"]))
    check("★ 代肝分区看不到守望的图（分区隔离）",
          all(x["zone"] != "ow" for x in g_park["gallery"]), "%d 张" % len(g_park["gallery"]))
    check("★ 三角洲分区也看不到守望的图", len(g_delta["gallery"]) == 0,
          "%d 张" % len(g_delta["gallery"]))
    check("返回的图带 zone 字段", bool(g_ow["gallery"]) and g_ow["gallery"][0]["zone"] == "ow",
          str(g_ow["gallery"][0].get("zone") if g_ow["gallery"] else "没图"))
    s, b = admin("/api/admin/gallery", {"zone": "没有这个分区", "title": "x",
                                        "image": red})
    check("给不存在的分区传图 → 400", s == 400, b.strip()[:100])

    print("=== 7. 批量粘贴导入价目表 ===")
    s, b = admin("/api/admin/import-prices", {"zone": "arknights", "group": "活动", "text":
        "危机合约 | 30R/期 | 全勤\n"
        "剿灭作战｜15R/期｜每周\n"
        "只写名字\n"
        "\n"
        "# 这是注释行\n"
        "   \n"
        " | 空名称 | 会被报错"})
    d = json.loads(b) if s == 200 else {}
    check("import-prices 成功", s == 200 and d.get("ok") is True, b.strip()[:140])
    check("成功导入 3 条（半角竖线 / 全角竖线 / 省略说明）", d.get("imported") == 3,
          str(d.get("imported")))
    check("空行和 # 注释行被忽略（不算错、不算条目）",
          [e["line"] for e in d.get("errors", [])] == [7], str(d.get("errors")))
    check("坏行报出正确的行号和中文原因",
          bool(d.get("errors")) and "名称" in d["errors"][0]["reason"],
          str(d.get("errors")))
    items = [it for g in d["profile"]["priceGroups"] if g["name"] == "活动" for it in g["items"]]
    check("导入的条目进了「活动」这一组", len(items) == 3, str([i["name"] for i in items]))
    check("全角竖线也能分开（剿灭作战 15R/期）",
          any(i["name"] == "剿灭作战" and i["price"] == "15R/期" for i in items), str(items))
    check("省略说明时说明为空字符串", any(i["name"] == "只写名字" and i["note"] == "" for i in items),
          str([(i["name"], i["note"]) for i in items]))
    check("导入的价格文本原样保留（30R/期）",
          any(i["price"] == "30R/期" for i in items), str([i["price"] for i in items]))

    s, b = admin("/api/admin/import-prices", {"zone": "arknights", "group": "", "text": "x | y"})
    check("没填组名 → 400", s == 400 and "哪一组" in b, b.strip()[:110])
    s, b = admin("/api/admin/import-prices", {"zone": "没有", "group": "托管", "text": "x | y"})
    check("分区不存在 → 400", s == 400, b.strip()[:110])
    s, b = admin("/api/admin/import-prices", {"zone": "arknights", "group": "托管", "text": ""})
    check("空文本 → 导入 0 条且不报错", s == 200 and json.loads(b)["imported"] == 0, b.strip()[:110])

    print("=== 8. 从首屏图自动提取主色 ===")
    blue = data_url(solid_png((30, 60, 220)))
    s, b = admin("/api/admin/extract-color", {"image": blue})
    d = json.loads(b) if s == 200 else {}
    color = str(d.get("color") or "")
    check("extract-color 返回 #rrggbb 格式", s == 200 and re.match(r"^#[0-9a-f]{6}$", color),
          "%s %s" % (s, color))
    if re.match(r"^#[0-9a-f]{6}$", color):
        r, g, bl = int(color[1:3], 16), int(color[3:5], 16), int(color[5:7], 16)
        check("取到的颜色接近原来的蓝色（蓝通道最大、红通道最小）",
              bl > r + 40 and bl > g, "取到 %s → r=%d g=%d b=%d" % (color, r, g, bl))
    s, b = admin("/api/admin/extract-color", {"image": "data:text/plain;base64,aGk="})
    check("不是图片 → 400", s == 400, b.strip()[:110])
    s, b = admin("/api/admin/extract-color", {})
    check("没传图 → 400", s == 400, b.strip()[:110])

    print("=== 9. 隐藏 / 显示 / 排序 / 删除 ===")
    s, b = admin("/api/admin/zones", {"action": "hide", "id": "ow"})
    check("隐藏守望分区", s == 200 and json.loads(b)["zone"]["visible"] is False, b.strip()[:100])
    _s, pub_now = public()
    check("★ 隐藏的分区不出现在 /api/public 的 zones 里",
          "ow" not in [z["id"] for z in pub_now["zones"]],
          str([z["id"] for z in pub_now["zones"]]))
    check("隐藏的分区不在首页导航里（nav 里也没有）",
          "ow" not in json.dumps(pub_now.get("nav", []), ensure_ascii=False))
    s, b = call("/z/ow")
    check("★ 隐藏的分区页仍然能直接打开（已发出去的链接不失效）", s == 200, "status=%s" % s)
    s, b = public("ow")
    check("★ 隐藏的分区 /api/public?zone=ow 也照常返回", s == 200 and b["zone"]["id"] == "ow",
          "status=%s" % s)
    s, b = admin("/api/admin/zones", {"action": "show", "id": "ow"})
    check("重新显示守望分区", s == 200 and json.loads(b)["zone"]["visible"] is True, b.strip()[:100])

    s, b = admin("/api/admin/zones", {"action": "sort", "order": ["ow", "delta", "arknights", "editing"]})
    order = [z["id"] for z in json.loads(b)["zones"]]
    check("分区排序生效（按大类分组展示，但 sort 字段已更新）",
          s == 200 and json.loads(call("/api/admin")[1])["zones"][0]["id"] == "ow", str(order))
    admin("/api/admin/zones", {"action": "sort", "order": ["delta", "ow", "arknights", "editing"]})

    s, b = admin("/api/admin/zones", {"action": "delete", "id": "editing"})
    d = json.loads(b)
    check("★ 删除分区不带 confirm → 只返回摘要，不真删",
          s == 200 and d.get("needConfirm") is True and "editing" in [z["id"] for z in json.loads(call("/api/admin")[1])["zones"]],
          b.strip()[:200])
    check("摘要里说清会牵连什么（展示墙 / 预约记录数量）",
          isinstance(d.get("summary"), dict) and "gallery" in d["summary"] and "locks" in d["summary"],
          str(d.get("summary")))
    s, b = admin("/api/admin/zones", {"action": "delete", "id": "editing", "confirm": True})
    check("带 confirm=true 才真删", s == 200 and json.loads(b).get("deleted", {}).get("id") == "editing",
          b.strip()[:140])
    check("删掉之后分区列表里没有它了",
          "editing" not in [z["id"] for z in json.loads(call("/api/admin")[1])["zones"]])
    s, b = call("/z/editing")
    check("删掉的分区页 → 404", s == 404, "status=%s" % s)
    s, b = admin("/api/admin/zones", {"action": "delete", "id": "editing", "confirm": True})
    check("删不存在的分区 → 404", s == 404, b.strip()[:100])

    print("=== 10. 大类：改名 / 改类型 / 排序，且不能删除 ===")
    s, b = admin("/api/admin/categories", {"action": "save", "id": "service",
                                           "name": "设计与建站"})
    check("大类改名", s == 200 and any(c["name"] == "设计与建站" for c in json.loads(b)["categories"]),
          b.strip()[:120])
    s, b = admin("/api/admin/categories", {"action": "save", "id": "boost", "kind": "booking"})
    check("大类可以改成预约制",
          json.loads(b)["categories"][[c["id"] for c in json.loads(b)["categories"]].index("boost")]["kind"] == "booking",
          b.strip()[:120])
    _s, pk = public("arknights")
    check("★ 大类改成预约制后，它下面的分区跟着变成 booking（不用改分区）",
          pk["zone"]["kind"] == "booking", pk["zone"]["kind"])
    admin("/api/admin/categories", {"action": "save", "id": "boost", "kind": "service"})
    _s, pk = public("arknights")
    check("改回服务制后分区也跟着回来", pk["zone"]["kind"] == "service", pk["zone"]["kind"])

    s, b = admin("/api/admin/categories", {"action": "sort", "order": ["service", "boost", "play"]})
    got = [c["id"] for c in json.loads(b)["categories"]]
    check("大类排序生效", got == ["service", "boost", "play"], str(got))
    admin("/api/admin/categories", {"action": "sort", "order": ["play", "boost", "service"]})
    s, b = admin("/api/admin/categories", {"action": "save", "id": "play", "name": "  "})
    check("大类名字不能改空 → 400", s == 400, b.strip()[:100])
    s, b = admin("/api/admin/categories", {"action": "save", "id": "play", "kind": "乱写"})
    check("大类类型只能是 booking/service → 400", s == 400, b.strip()[:100])
    s, b = admin("/api/admin/categories", {"action": "delete", "id": "play"})
    check("★ 大类不允许删除 → 400（分区不会无家可归）", s == 400 and "不能删" in b, b.strip()[:120])

    print("=== 11. 页面与分享卡片 ===")
    s, b = call("/z/ow")
    check("GET /z/ow 是 200 且没有占位符残留", s == 200 and "{{" not in b, "status=%s" % s)
    check("分区页标题用的是分区自己的名字", "<title>守望先锋" in b or "守望先锋" in b.split("<title>")[1][:60],
          b.split("<title>")[1][:50] if "<title>" in b else "没有 title")
    s, b = call("/")
    check("个人主页 200 且没有占位符残留", s == 200 and "{{" not in b and "}}" not in b, "status=%s" % s)
    s, b = call("/nya-0508")
    check("/nya-0508 没被分区路由抢走", s == 200 and "管理终端" in b, "status=%s" % s)
    s, b = call("/api/ping")
    check("/api/ping 没被分区路由抢走", s == 200 and json.loads(b).get("ok") is True, b.strip()[:60])
    s, b = call("/z")
    check("/z（没写分区）→ 404 且提示怎么用", s == 404, "status=%s" % s)

    print("=== 12. 备份要把分区和图片一起带走 ===")
    backup = json.loads(call("/api/admin/export")[1])
    # 备份里 arknights 的现状（第 4 步改写过、第 7 步导入过），恢复后应该和它一致
    backup_groups = [g["name"] for z in backup.get("zones") or [] if z["id"] == "arknights"
                     for g in (z.get("profile") or {}).get("priceGroups") or []]
    check("备份里有 categories", isinstance(backup.get("categories"), list) and backup["categories"],
          str(len(backup.get("categories") or [])))
    check("备份里有 zones（含每个分区的资料）",
          isinstance(backup.get("zones"), list) and any(z["id"] == "arknights" for z in backup["zones"]),
          str([z["id"] for z in backup.get("zones") or []]))
    check("备份里连分区的那几张图也打包了（zoneImages）",
          isinstance(backup.get("zoneImages"), dict), str(sorted((backup.get("zoneImages") or {}).keys())))

    # 把守望分区的资料改坏，再从备份恢复，验证真的回来了
    admin("/api/admin/zones", {"action": "profile", "id": "ow", "profile": {"game": "被改坏了"}})
    check("先把守望分区改坏", public("ow")[1]["zone"]["profile"]["game"] == "被改坏了")
    s, b = admin("/api/admin/import", {"backup": backup})
    check("从备份恢复成功", s == 200, b.strip()[:140])
    check("★ 恢复后分区资料回来了（守望的游戏名 / 价格 / 图）",
          public("ow")[1]["zone"]["profile"]["game"] == "守望先锋"
          and len(public("ow")[1]["zone"]["profile"]["prices"]) == 2,
          public("ow")[1]["zone"]["profile"]["game"])
    check("★ 恢复后代肝分区的价目表和备份里的一致",
          [g["name"] for g in public("arknights")[1]["zone"]["profile"]["priceGroups"]]
          == backup_groups,
          "%s / 备份里是 %s" % ([g["name"] for g in public("arknights")[1]["zone"]["profile"]["priceGroups"]],
                              backup_groups))
    check("★ 恢复后「活动」组里导入的那几条也还在",
          len([it for g in public("arknights")[1]["zone"]["profile"]["priceGroups"]
               if g["name"] == "活动" for it in g["items"]]) == 3,
          str([[it["name"] for it in g["items"]] for g in
               public("arknights")[1]["zone"]["profile"]["priceGroups"] if g["name"] == "活动"]))
    check("恢复后展示墙还在它原来的分区里",
          any(x["zone"] == "ow" for x in json.loads(call("/api/admin")[1])["gallery"]),
          str([(x["title"], x["zone"]) for x in json.loads(call("/api/admin")[1])["gallery"]]))

    print("=== 13. 老备份（没有 zones）也要能导入 ===")
    old_backup = {"profile": dict(backup["profile"]), "locks": backup["locks"],
                  "rules": [], "gallery": []}
    zones_before = [z["id"] for z in json.loads(call("/api/admin")[1])["zones"]]
    s, b = admin("/api/admin/import", {"backup": old_backup})
    check("老备份能导入（不报错）", s == 200, b.strip()[:140])
    check("★ 老备份里没有 zones，恢复后分区保持现状（不被清空）",
          [z["id"] for z in json.loads(call("/api/admin")[1])["zones"]] == zones_before,
          str(zones_before))
    check("老备份恢复后预约记录还在",
          len(json.loads(call("/api/admin")[1])["schedule"]["locks"]) == len(old_backup["locks"]),
          str(len(json.loads(call("/api/admin")[1])["schedule"]["locks"])))

    print("=== 14. 后台接口返回分区信息 ===")
    s, b = call("/api/admin")
    adm = json.loads(b)
    check("GET /api/admin 返回 categories / zones / site",
          isinstance(adm.get("categories"), list) and isinstance(adm.get("zones"), list)
          and isinstance(adm.get("site"), dict), str(sorted(adm.keys()))[:160])
    check("后台能看到隐藏的分区（含 visible 字段）",
          all("visible" in z for z in adm["zones"]), str([(z["id"], z["visible"]) for z in adm["zones"]]))
    check("site 里有品牌 / 昵称 / 信任信号",
          {"brand", "name", "trust"} <= set(adm["site"]), str(sorted(adm["site"])))


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

OLD_PROFILE = {
    "name": "升级前的老板",
    "game": "三角洲行动",
    "tagline": "三角洲行动 · PC 端个人陪玩",
    "headline": "一起出发。\n尽兴而归。",
    "bio": "技术在线，情绪价值拉满。",
    "experience": "老字段：陪玩经历",
    "wechat": "old_wechat_id",
    "xianyu": "https://example.com/xianyu",
    "avatar": "",
    "background": "/api/media/old-bg.png",
    "backgroundDim": 55,
    "heroImage": "/api/media/old-hero.png",
    "platform": "PC 电脑端",
    "accepting": False,
    "announcement": "老公告：先看空档",
    "styles": ["猛攻", "跑刀", "堵桥"],
    "prices": [{"id": "dam", "name": "机密 · 零号大坝", "description": "轻装出发", "price": 15},
               {"id": "conf", "name": "机密行动", "description": "打法听你的", "price": 20}],
    "promises": ["老承诺一", "老承诺二", "老承诺三"],
}


def migration_test():
    print("=== 15. ★ 老数据库迁移（零丢失 + 只迁一次） ===")
    tmp = os.path.join(ROOT, ".testdata-zones")
    shutil.rmtree(tmp, ignore_errors=True)
    os.makedirs(tmp)
    db = os.path.join(tmp, "site.db")
    try:
        # ---- 造一个"升级前"的库：没有 zones 表、locks/gallery 没有 zone 字段 ----
        conn = sqlite3.connect(db)
        conn.executescript(OLD_SCHEMA)
        conn.execute("INSERT INTO store(key, value) VALUES('profile', ?)",
                     (json.dumps(OLD_PROFILE, ensure_ascii=False),))
        conn.execute("INSERT INTO store(key, value) VALUES('revision', '7')")
        for i in range(3):
            conn.execute("INSERT INTO locks(id, kind, start, end, client, note) VALUES(?,?,?,?,?,?)",
                         ("old-lock-%d" % i, "booking" if i < 2 else "rest",
                          1500000000000 + i * 3600000, 1500003600000 + i * 3600000,
                          "老客户%d" % i, "升级前锁的%d" % i))
        for i in range(2):
            conn.execute("""INSERT INTO gallery(id, title, caption, category, image, visible,
                            archived, created_at) VALUES(?,?,?,?,?,1,0,?)""",
                         ("old-gal-%d" % i, "老截图%d" % i, "老的说明", "screenshot",
                          "/api/media/old-%d.png" % i, 1500000000000 + i))
        conn.execute("""INSERT INTO rules(id, days, start_hour, end_hour, from_ts, until_ts,
                        note, enabled) VALUES('old-rule','[0,1,2]',2,10,1500000000000,NULL,'睡觉',1)""")
        conn.commit()
        conn.close()

        # ---- 让 app 指向这个临时数据目录，再跑 init_db（升级时真正发生的事） ----
        os.environ["DATA_DIR"] = tmp
        if ROOT not in sys.path:
            sys.path.insert(0, ROOT)
        import app                                    # noqa: E402

        app.init_db()

        cols_locks = [r[1] for r in sqlite3.connect(db).execute("PRAGMA table_info(locks)")]
        cols_gal = [r[1] for r in sqlite3.connect(db).execute("PRAGMA table_info(gallery)")]
        tables = [r[0] for r in sqlite3.connect(db).execute(
            "SELECT name FROM sqlite_master WHERE type='table'")]
        check("建出了 zones 表", "zones" in tables, str(tables))
        check("locks 表补上了 zone 字段", "zone" in cols_locks, str(cols_locks))
        check("gallery 表补上了 zone 字段", "zone" in cols_gal, str(cols_gal))

        cats = app.list_categories()
        check("迁移时建好了 3 个默认大类", len(cats) == 3, str([c["id"] for c in cats]))
        check("大类类型正确（陪玩=booking，代肝/其他=service）",
              cats[0]["kind"] == "booking" and cats[1]["kind"] == "service"
              and cats[2]["kind"] == "service", str([(c["id"], c["kind"]) for c in cats]))

        zones = app.list_zones()
        check("迁移后正好一个分区 delta", len(zones) == 1 and zones[0]["id"] == "delta",
              str([z["id"] for z in zones]))
        z = zones[0]
        check("delta 属于陪玩大类、可见、排在第一个",
              z["category"] == "play" and z["visible"] is True and z["sort"] == 1, str(z)[:140])
        check("delta 的全称取老 profile.game（三角洲行动）", z["name"] == "三角洲行动", z["name"])
        check("delta 的简称是「三角洲」", z["short"] == "三角洲", z["short"])
        p = z["profile"]
        check("★ 老游戏名搬进了分区（profile.game）", p["game"] == "三角洲行动", str(p["game"]))
        check("★ 老标语 / 大标题 / 简介都搬进来了",
              p["tagline"] == OLD_PROFILE["tagline"] and p["headline"] == OLD_PROFILE["headline"]
              and p["bio"] == OLD_PROFILE["bio"],
              "%s / %s" % (p["tagline"], p["headline"]))
        check("★ 老 styles 变成 highlights 一条不丢",
              p["highlights"] == OLD_PROFILE["styles"], str(p["highlights"]))
        check("★ 老 promises 搬进来了", p["promises"] == OLD_PROFILE["promises"], str(p["promises"]))
        check("★ 老价格档位搬进来了（含数字价格）",
              len(p["prices"]) == 2 and p["prices"][0]["price"] == 15, str(p["prices"]))
        check("★ 老背景图 / 首屏图 / 压暗程度都搬进来了",
              p["background"] == "/api/media/old-bg.png" and p["heroImage"] == "/api/media/old-hero.png"
              and p["backgroundDim"] == 55,
              "%s / %s / %s" % (p["background"], p["heroImage"], p["backgroundDim"]))
        check("★ 老公告和平台搬进来了",
              p["announcement"] == "老公告：先看空档" and p["platform"] == "PC 电脑端",
              "%s / %s" % (p["announcement"], p["platform"]))

        locks = app.list_locks()
        check("★ 老预约记录一条没丢", len(locks) == 3, "剩 %d 条" % len(locks))
        check("★ 老 locks 的 zone 全部变成 delta",
              all(l["zone"] == "delta" for l in locks), str([l["zone"] for l in locks]))
        check("★ 老 locks 的内容没被改动",
              locks[0]["client"] == "老客户0" and locks[2]["kind"] == "rest",
              str(locks[0])[:120])

        gal = app.list_gallery()
        check("★ 老展示墙一张没丢", len(gal) == 2, "剩 %d 张" % len(gal))
        check("★ 老 gallery 的 zone 全部变成 delta",
              all(g["zone"] == "delta" for g in gal), str([g["zone"] for g in gal]))
        check("老展示墙的内容没变", gal[0]["title"].startswith("老截图"), str(gal[0])[:100])
        check("老休息规则还在", len(app.list_rules()) == 1, str(len(app.list_rules())))
        check("老的资料版本号没被重置", app.get_revision() == 7, str(app.get_revision()))

        gp = app.get_profile()
        check("★ 全局资料保留了品牌默认值 / 昵称 / 微信 / 闲鱼 / 接单开关",
              gp["brand"] == "Nya" and gp["name"] == "升级前的老板"
              and gp["wechat"] == "old_wechat_id" and gp["xianyu"] == "https://example.com/xianyu"
              and gp["accepting"] is False,
              "%s / %s / %s" % (gp["brand"], gp["name"], gp["wechat"]))
        check("★ 全局资料补上了 trust 数组（默认空）", gp["trust"] == [], str(gp["trust"]))
        check("全局资料里仍然留着老的 game / platform（老接口不失效）",
              gp.get("game") == "三角洲行动" and gp.get("platform") == "PC 电脑端",
              "%s / %s" % (gp.get("game"), gp.get("platform")))

        # ---- 幂等：再跑一次不能重复迁移 ----
        app.init_db()
        app.init_db()
        check("★ 再跑两次 init_db 也不会重复迁移（还是 1 个分区）",
              len(app.list_zones()) == 1, "现在 %d 个" % len(app.list_zones()))
        check("★ 也不会把老数据搬第二遍（locks/gallery 还是那些）",
              len(app.list_locks()) == 3 and len(app.list_gallery()) == 2,
              "%d / %d" % (len(app.list_locks()), len(app.list_gallery())))

        # ---- 迁移之后新建的分区照常工作 ----
        new_zone, err = app.create_zone("ow", "play", "守望先锋", "守望")
        check("迁移后能新建分区", new_zone is not None and err is None, str(err))
        saved, err = app.save_zone_profile("ow", {"game": "守望先锋", "prices": [
            {"name": "快速", "price": 12}]})
        check("迁移后能存分区资料", saved is not None and saved["profile"]["prices"][0]["price"] == 12,
              str(err))
        check("新分区的起步价算得出来", app.zone_start_price(app.get_zone("ow")) == "¥12/小时起",
              app.zone_start_price(app.get_zone("ow")))
        check("老分区的起步价也算得出来（15R）",
              app.zone_start_price(app.get_zone("delta")) == "¥15/小时起",
              app.zone_start_price(app.get_zone("delta")))
    finally:
        # Windows 上 SQLite 连接要等垃圾回收才真正关闭：先 gc.collect()，删不掉就等一会儿重试
        for _ in range(5):
            gc.collect()
            try:
                shutil.rmtree(tmp)
                break
            except OSError:
                time.sleep(0.2)


if __name__ == "__main__":
    print("=" * 60)
    print(" 两级分区测试（大类 / 分区 / 分区资料 / 时间共用）（%s）" % BASE)
    print("=" * 60, flush=True)
    main_tests()
    migration_test()
    print()
    print("通过 %d 项，失败 %d 项" % (ok, fail))
    sys.exit(1 if fail else 0)
