# -*- coding: utf-8 -*-
"""自动测试：把网站的每个接口都跑一遍，确认能正常工作。"""
import base64
import http.cookiejar
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

sys.stdout.reconfigure(encoding="utf-8")
# 默认测本机 8123；想测别的端口/地址就设环境变量 TEST_BASE
BASE = os.environ.get("TEST_BASE") or "http://127.0.0.1:8123"
PASSWORD = os.environ.get("TEST_PASSWORD") or "baixu123"    # 改过密码就设这个环境变量

# ============================================================================
#  ⚠️ 这个脚本会"写数据"：锁定时段、上传图片、改管理密码、导入备份……
#     所以它**只能对着测试环境跑**。如果你把它指向线上的网站，会把你自己的站改坏。
#     想检查线上站点是否健康，请用只读的 tests\check_deployed.py
# ============================================================================
IS_LOCAL = re.match(r"^https?://(127\.0\.0\.1|localhost)(:\d+)?$", BASE) is not None
if not IS_LOCAL and os.environ.get("ALLOW_REMOTE_TESTS") != "1":
    print("=" * 70)
    print(" 已阻止：这是一个会修改数据的测试脚本，不能对着线上站点运行。")
    print(" 你指定的地址是：%s" % BASE)
    print()
    print("   · 想检查线上站点健康 → 用只读脚本： python tests\\check_deployed.py %s" % BASE)
    print("   · 确实要在这个地址上做破坏性测试 → 设 ALLOW_REMOTE_TESTS=1 再运行")
    print("=" * 70)
    sys.exit(2)
# 测试自己会写数据，测完这些数据要清掉，所以要知道 uploads 在哪
DATA_DIR = os.environ.get("TEST_DATA_DIR") or os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
UPLOADS_DIR = os.path.join(DATA_DIR, "uploads")
jar = http.cookiejar.CookieJar()
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
ok = fail = 0


def call(path, method="GET", payload=None):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    try:
        with opener.open(req, timeout=20) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


def call_bytes(path):
    """取二进制内容（图片），不做文本解码"""
    try:
        with opener.open(urllib.request.Request(BASE + path), timeout=20) as r:
            return r.getcode(), r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def call_head(path):
    """发 HEAD 请求（分享预览爬虫就是这么发的）"""
    try:
        with opener.open(urllib.request.Request(BASE + path, method="HEAD"), timeout=20) as r:
            return r.getcode(), len(r.read()), dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, 0, {}


def call_text(path, headers=None):
    """带自定义请求头抓网页（用来模拟 Render 转发过来的 Host / X-Forwarded-Proto）"""
    req = urllib.request.Request(BASE + path)
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    with opener.open(req, timeout=20) as r:
        return r.getcode(), r.read().decode("utf-8", "replace")


def meta(html, prop):
    hit = re.search('<meta property="%s" content="([^"]*)"' % prop, html)
    return hit.group(1) if hit else None


def call_with_cookie(path, cookie):
    """不用 cookiejar，手动带 Cookie（用来验证签名 Cookie 是否自洽）"""
    req = urllib.request.Request(BASE + path)
    req.add_header("Cookie", cookie)
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.getcode(), r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


def check(name, cond, extra=""):
    global ok, fail
    if cond:
        ok += 1
        print("  [OK]   %s %s" % (name, extra))
    else:
        fail += 1
        print("  [FAIL] %s %s" % (name, extra))


print("=== 1. 页面 ===")
for path, keyword in [("/", "预约时间"), ("/admin", "管理终端"), ("/static/style.css", "--accent"),
                      ("/static/site.js", "api/public"), ("/static/admin.js", "save-lock")]:
    s, b = call(path)
    check("GET %s" % path, s == 200 and keyword in b, "status=%s len=%d" % (s, len(b)))

print("=== 2. 公开接口 ===")
s, b = call("/api/public")
pub = json.loads(b)
check("GET /api/public", s == 200 and len(pub["slots"]) == 168, "slots=%d" % len(pub["slots"]))
check("格子字段完整", all(set(x) == {"start", "end", "status"} for x in pub["slots"]))
# 注意：不能断言"今天有多少格子空闲"——那个数字随当前时间变化（跑在晚上就会变少）。
# 明天是完整的 24 小时且一定在未来，拿它做断言才稳定。
day2 = pub["slots"][24:48]
check("全新站点：明天 24 小时全部可预约",
      len(day2) == 24 and all(x["status"] == "free" for x in day2),
      "明天状态=%s" % sorted({x["status"] for x in day2}))
today_status = sorted({x["status"] for x in pub["slots"][:24]})
check("今天的状态只用合法的值（过去的算 past）",
      set(today_status) <= {"free", "past"}, str(today_status))
check("资料已预置", pub["profile"]["name"] == "败絮" and pub["profile"]["wechat"] == "NekoByNya")

now = int(time.time() * 1000)
target = next(x["start"] for x in pub["slots"] if x["status"] == "free")
s, b = call("/api/availability?hours=%d" % target)
check("GET /api/availability 正常", s == 200 and json.loads(b)["slots"][0]["status"] == "free")
s, b = call("/api/availability")
check("空参数返回 400", s == 400, b.strip())
s, b = call("/api/availability?hours=abc")
check("乱参数返回 400", s == 400, b.strip())

print("=== 3. 未登录必须被拦 ===")
for path, method in [("/api/admin", "GET"), ("/api/admin/schedule", "POST"),
                     ("/api/admin/profile", "PUT"), ("/api/admin/gallery", "POST"),
                     ("/api/admin/password", "POST"), ("/api/admin/export", "GET")]:
    s, b = call(path, method, {})
    check("%s %s -> 401" % (method, path), s == 401, "status=%s" % s)

print("=== 4. 登录 ===")
s, b = call("/api/auth", "POST", {"password": "wrong-one"})
check("错误密码 401", s == 401, b.strip())
s, b = call("/api/auth", "POST", {"password": PASSWORD})
check("正确密码 200", s == 200 and json.loads(b).get("ok"))
check("拿到会话 Cookie", any(c.name == "peiwan_session" for c in jar))
s, b = call("/api/auth")
check("GET /api/auth 已登录", json.loads(b)["authenticated"] is True)

print("=== 5. 后台数据 ===")
s, b = call("/api/admin")
adm = json.loads(b)
check("GET /api/admin", s == 200 and "profileRevision" in adm)
check("初始无锁定无规则", adm["schedule"]["locks"] == [] and adm["schedule"]["rules"] == [])

print("=== 6. 锁定时段 ===")
start = target
s, b = call("/api/admin/schedule", "POST",
            {"action": "save-lock", "start": start, "end": start + 2 * 3600 * 1000,
             "kind": "booking", "client": "小王老板", "note": "机密行动 · 微信已付 2 小时"})
check("save-lock", s == 200 and len(json.loads(b)["schedule"]["locks"]) == 1)
s, b = call("/api/availability?hours=%d" % start)
check("该格变已预约", json.loads(b)["slots"][0]["status"] == "booking")
s, b = call("/api/admin/schedule", "POST", {"action": "remove-lock", "id": "nope"})
check("删除不存在的锁定不报错", s == 200)

# 批量锁定：4 个连续小时应合并成 1 段；已锁定的格子应被跳过
hours = [x["start"] for x in pub["slots"] if x["status"] == "free"][10:14]
s, b = call("/api/admin/schedule", "POST",
            {"action": "save-batch", "hours": hours + [start], "kind": "booking", "client": "批量测试"})
locks = json.loads(b)["schedule"]["locks"]
batch = [l for l in locks if l["client"] == "批量测试"]
check("save-batch 把 4 个连续小时合并成 1 段", s == 200 and len(locks) == 2 and len(batch) == 1,
      "locks=%d batch=%d" % (len(locks), len(batch)))
check("合并后的长度正好 4 小时",
      bool(batch) and batch[0]["end"] - batch[0]["start"] == 4 * 3600 * 1000,
      "len=%s" % ((batch[0]["end"] - batch[0]["start"]) / 3600000 if batch else "?"))
check("已锁定的格子被跳过（没有重复锁定）",
      len([l for l in locks if l["start"] <= start < l["end"]]) == 1)
s, b = call("/api/admin/schedule", "POST",
            {"action": "save-batch", "hours": [], "kind": "booking"})
check("空 hours 返回 400", s == 400, b.strip())
s, b = call("/api/admin/schedule", "POST",
            {"action": "save-batch", "hours": list(range(0, 100)), "kind": "booking"})
check("超过 72 小时被拒绝", s == 400, b.strip())

print("=== 7. 固定休息规则 ===")
# 先清掉前面的测试锁定，避免干扰规则判断
for l in json.loads(call("/api/admin")[1])["schedule"]["locks"]:
    call("/api/admin/schedule", "POST", {"action": "remove-lock", "id": l["id"]})
today0 = pub["slots"][0]["start"]
s, b = call("/api/admin/schedule", "POST",
            {"action": "save-rule", "days": [0, 1, 2, 3, 4, 5, 6], "start_hour": 2, "end_hour": 10,
             "from_ts": today0, "note": "睡觉休息", "enabled": True})
check("save-rule", s == 200 and len(json.loads(b)["schedule"]["rules"]) == 1)
s, b = call("/api/public")
pub2 = json.loads(b)
# 明天（第 24 格起）的数据一定都是"未来"，不受当前时刻影响
tomorrow = {(x["start"] - today0) // 3600000: x["status"] for x in pub2["slots"][24:48]}
check("每天凌晨 3 点变休息", tomorrow.get(27) == "rest", str(tomorrow.get(27)))
check("每天上午 11 点仍可预约", tomorrow.get(35) == "free", str(tomorrow.get(35)))
# 今天凌晨 3 点也可能是"已过去"，所以只在它还没到的时候检查（避免这个测试"看时间才通过"）
today_map = {(x["start"] - today0) // 3600000: x["status"] for x in pub2["slots"][:24]}
if today_map.get(3) == "past":
    check("今天 03:00 已过去（跳过休息判断）", True, "past")
else:
    check("今天凌晨 3 点也休息", today_map.get(3) == "rest", str(today_map.get(3)))

# 跨午夜规则 22:00 -> 06:00
s, b = call("/api/admin/schedule", "POST",
            {"action": "save-rule", "days": [0, 1, 2, 3, 4, 5, 6], "start_hour": 22, "end_hour": 6,
             "from_ts": today0, "note": "跨午夜测试", "enabled": True})
s, b = call("/api/public")
p3 = json.loads(b)
st = {(x["start"] - today0) // 3600000: x["status"] for x in p3["slots"]}
check("跨午夜规则：第二天 0 点算休息", st.get(24) == "rest", str(st.get(24)))
check("跨午夜规则：第二天 5 点算休息", st.get(29) == "rest", str(st.get(29)))
# 注意：02:00–10:00 那条规则还盖着 6 点，所以用 10 点来验证"跨午夜规则确实在 06:00 结束"
check("跨午夜规则：06:00 后不再受该规则影响", st.get(34) == "free", str(st.get(34)))
check("跨午夜规则：第一天 21 点仍可预约", st.get(21) == "free", str(st.get(21)))

rules = call("/api/admin")[1]
rid = json.loads(rules)["schedule"]["rules"][0]["id"]
s, b = call("/api/admin/schedule", "POST", {"action": "toggle-rule", "id": rid, "enabled": False})
check("toggle-rule 暂停", json.loads(b)["schedule"]["rules"][0]["enabled"] is False)

print("=== 8. 资料与版本号 ===")
cur = call("/api/admin")[1]
adm = json.loads(cur)
prof = dict(adm["profile"])
prof["announcement"] = "测试公告"
s, b = call("/api/admin/profile", "PUT", {"profile": prof, "revision": adm["profileRevision"]})
check("PUT profile", s == 200 and json.loads(b)["profileRevision"] == adm["profileRevision"] + 1)
s, b = call("/api/admin/profile", "PUT", {"profile": prof, "revision": adm["profileRevision"]})
check("旧版本号被拒绝(409)", s == 409, b.strip())
check("公告已生效", json.loads(call("/api/public")[1])["profile"]["announcement"] == "测试公告")

print("=== 9. 展示墙 ===")
png = base64.b64encode(bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000a49444154789c6360000002000100ffff03000006000557bfabd40000000049454e44ae426082")).decode()
s, b = call("/api/admin/gallery", "POST",
            {"title": "测试截图", "caption": "只用于自动测试", "category": "screenshot",
             "visible": True, "image": "data:image/png;base64," + png})
gal = json.loads(b)["gallery"]
check("上传图片", s == 200 and len(gal) == 1, "gallery=%d" % len(gal))
gid = gal[0]["id"]
check("图片可直接访问", call(gal[0]["image"])[0] == 200)
check("公开接口能看到", len(json.loads(call("/api/public")[1])["gallery"]) == 1)
s, b = call("/api/admin/gallery", "PATCH", {"id": gid, "visible": False})
check("隐藏后首页看不到", len(json.loads(call("/api/public")[1])["gallery"]) == 0)
s, b = call("/api/admin/gallery", "PATCH", {"id": gid, "archived": True})
check("归档标记生效", json.loads(b)["gallery"][0]["archived"] is True)
s, b = call("/api/admin/gallery", "POST", {"title": "没有图"})
check("缺图片返回 400", s == 400, b.strip())
s, b = call("/api/admin/gallery", "DELETE", {"id": gid})
check("删除图片", s == 200 and json.loads(b)["gallery"] == [])
uploads = UPLOADS_DIR
check("图片文件已被清理", os.listdir(uploads) == [],
      str(os.listdir(uploads)))

print("=== 10. 备份导出 ===")
s, b = call("/api/admin/export")
backup = json.loads(b)
check("导出 JSON", s == 200 and {"profile", "locks", "rules", "gallery"} <= set(backup))

print("=== 11. 改密码与退出 ===")
s, b = call("/api/admin/password", "POST", {"current": "bad", "next": "newpass123"})
check("当前密码错误 401", s == 401, b.strip())
s, b = call("/api/admin/password", "POST", {"current": PASSWORD, "next": "12345"})
check("新密码太短 400", s == 400, b.strip())
s, b = call("/api/admin/password", "POST", {"current": PASSWORD, "next": PASSWORD})
check("改密码成功", s == 200)
check("改完密码会话失效", call("/api/admin")[0] == 401)
s, b = call("/api/auth", "POST", {"password": PASSWORD})
check("用新密码能重新登录", s == 200)

print("=== 12. 备份与恢复 ===")
HOURMS = 3600 * 1000
pub_now = json.loads(call("/api/public")[1])
free_hours = [x["start"] for x in pub_now["slots"] if x["status"] == "free"][20:23]
call("/api/admin/schedule", "POST", {"action": "save-batch", "hours": free_hours,
                                     "kind": "booking", "client": "备份测试老板", "note": "3 小时"})
call("/api/admin/schedule", "POST", {"action": "save-rule", "days": [6, 0], "start_hour": 1, "end_hour": 2,
                                     "from_ts": pub_now["slots"][0]["start"], "note": "备份测试规则"})
png_bytes = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000a49444154789c6360000002000100ffff03000006000557bfabd40000000049454e44ae426082")
png_b64 = base64.b64encode(png_bytes).decode()
call("/api/admin/gallery", "POST", {"title": "备份用图", "caption": "待恢复", "category": "review",
                                    "visible": True, "image": "data:image/png;base64," + png_b64})

s, raw = call("/api/admin/export")
backup = json.loads(raw)
check("导出成功", s == 200)
check("备份带版本信息", backup.get("app") == "peiwan-site" and "exportedAt" in backup)
check("备份含锁定/规则/展示", len(backup["locks"]) >= 1 and len(backup["rules"]) >= 1
      and len(backup["gallery"]) == 1, "locks=%d rules=%d gallery=%d" % (
          len(backup["locks"]), len(backup["rules"]), len(backup["gallery"])))
check("图片被一起打包进备份",
      str(backup["gallery"][0].get("imageData", "")).startswith("data:image/png;base64,"))
old_locks, old_rules = len(backup["locks"]), len(backup["rules"])

# 清空全部数据（模拟"重新部署后 data/ 被清空"）
for l in json.loads(call("/api/admin")[1])["schedule"]["locks"]:
    call("/api/admin/schedule", "POST", {"action": "remove-lock", "id": l["id"]})
for r in json.loads(call("/api/admin")[1])["schedule"]["rules"]:
    call("/api/admin/schedule", "POST", {"action": "remove-rule", "id": r["id"]})
for g in json.loads(call("/api/admin")[1])["gallery"]:
    call("/api/admin/gallery", "DELETE", {"id": g["id"]})
emptied = json.loads(call("/api/admin")[1])
check("数据已清空（模拟重新部署）",
      not emptied["schedule"]["locks"] and not emptied["schedule"]["rules"] and not emptied["gallery"])

s, raw = call("/api/admin/import", "POST", {"backup": backup})
summary = json.loads(raw).get("summary", {}) if s == 200 else {}
check("导入备份成功", s == 200, raw[:120])
check("时段/规则数量与备份一致",
      summary.get("locks") == old_locks and summary.get("rules") == old_rules,
      "locks=%s/%s rules=%s/%s" % (summary.get("locks"), old_locks, summary.get("rules"), old_rules))
check("图片被重新写回磁盘", summary.get("images") == 1, str(summary))
restored = json.loads(call("/api/admin")[1])["gallery"][0]
check("恢复后的展示项还是公开状态", restored["visible"] is True and restored["category"] == "review")
code, blob = call_bytes(restored["image"])
check("恢复后的图片能打开且和原图一模一样", code == 200 and blob == png_bytes,
      "status=%s len=%d" % (code, len(blob)))
check("公开主页也能看到恢复的展示墙", len(json.loads(call("/api/public")[1])["gallery"]) == 1)

s, raw = call("/api/admin/import", "POST", {"backup": {"hello": "world"}})
check("垃圾备份被拒绝(400)", s == 400, raw.strip())
s, raw = call("/api/admin/import", "POST", {"backup": {"profile": {}, "locks": [
    {"start": "坏数据", "end": None}, {"start": 1, "end": 2, "kind": "booking"}]}})
check("备份里混入坏记录也能恢复（自动跳过）",
      s == 200 and json.loads(raw)["summary"]["locks"] == 1, raw[:120])

print("=== 13. 分享卡片与 HEAD 请求 ===")
html = call("/")[1]
check("主页里的 {{占位符}} 已全部替换", "{{" not in html and "}}" not in html)
title_start = html.find("<title>")
check("浏览器标题带上了名字", "<title>败絮" in html, html[title_start:title_start + 50])
check("og 分享标签存在",
      'property="og:title"' in html and 'property="og:description"' in html
      and 'property="og:site_name"' in html)
og_desc = html.split('property="og:description" content="')[1].split('"')[0]
check("分享简介非空且有内容", len(og_desc) > 8, og_desc[:60])

code, size, headers = call_head("/")
check("HEAD / 返回 200 且没有正文", code == 200 and size == 0, "code=%s body=%s" % (code, size))
code, size, headers = call_head("/static/style.css")
check("HEAD 静态文件带正确 Content-Length",
      code == 200 and int(headers.get("Content-Length") or 0) > 1000,
      "len=%s" % headers.get("Content-Length"))
code, size, _ = call_head("/api/public")
check("HEAD /api/public 返回 200", code == 200, "code=%s" % code)
code, size, _ = call_head("/no-such-page")
check("HEAD 不存在的路径返回 404", code == 404, "code=%s" % code)

# 静态资源要支持子目录（背景图常放 static/img/），同时不能让人读到项目源码
STATIC_IMG = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static", "img")
probe = os.path.join(STATIC_IMG, "_test_probe.png")
try:
    os.makedirs(STATIC_IMG, exist_ok=True)
    with open(probe, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n")
    code, blob = call_bytes("/static/img/_test_probe.png")
    check("静态资源支持子目录 static/img/xxx.png",
          code == 200 and blob.startswith(b"\x89PNG"), "status=%s" % code)
finally:
    if os.path.exists(probe):
        os.remove(probe)

code, _ = call_bytes("/static/../app.py")
check("禁止用 ../ 偷看项目源码", code in (400, 404), "status=%s" % code)
code, _ = call_bytes("/static/..%2fapp.py")
check("禁止用编码过的 %2f 绕过目录限制", code in (400, 404), "status=%s" % code)

# 开箱自带的默认背景图
code, blob = call_bytes("/static/img/default-bg.jpg")
check("自带默认背景图能访问", code == 200 and len(blob) > 50 * 1024,
      "status=%s size=%.0fKB" % (code, len(blob) / 1024))
css = call("/static/style.css")[1]
check("样式里引用了默认背景图", "default-bg.jpg" in css)

# 改了名字，分享标题要立刻跟着变（不用重新部署）
cur = json.loads(call("/api/admin")[1])
prof = dict(cur["profile"])
prof["name"] = "测试名字"
call("/api/admin/profile", "PUT", {"profile": prof, "revision": cur["profileRevision"]})
html2 = call("/")[1]
check("改了名字，分享标题立刻跟着变", "<title>测试名字" in html2,
      html2[html2.find("<title>"):html2.find("<title>") + 40])
cur2 = json.loads(call("/api/admin")[1])
prof2 = dict(cur2["profile"])
prof2["name"] = "败絮"
call("/api/admin/profile", "PUT", {"profile": prof2, "revision": cur2["profileRevision"]})

print("=== 14. 接单总开关 与 全天休息规则 ===")
cur = json.loads(call("/api/admin")[1])
prof = dict(cur["profile"])
prof["accepting"] = False
call("/api/admin/profile", "PUT", {"profile": prof, "revision": cur["profileRevision"]})
pub_off = json.loads(call("/api/public")[1])
future_off = [s["status"] for s in pub_off["slots"] if s["status"] != "past"]
check("关掉「开放接单」后空闲格全部变停接(closed)",
      bool(future_off) and all(x == "closed" for x in future_off),
      str(sorted(set(future_off))))

cur2 = json.loads(call("/api/admin")[1])
prof2 = dict(cur2["profile"])
prof2["accepting"] = True
call("/api/admin/profile", "PUT", {"profile": prof2, "revision": cur2["profileRevision"]})
pub_on = json.loads(call("/api/public")[1])
check("重新打开接单后恢复可预约",
      any(s["status"] == "free" for s in pub_on["slots"]))

# 00:00–24:00 这种"整天"边界最容易写错（跨午夜公式在 end_hour=24 时要单独处理）
call("/api/admin/schedule", "POST", {"action": "save-rule", "days": [0, 1, 2, 3, 4, 5, 6],
                                     "start_hour": 0, "end_hour": 24,
                                     "from_ts": pub_on["slots"][0]["start"], "note": "全天休息测试"})
pub_all = json.loads(call("/api/public")[1])
future_all = [s["status"] for s in pub_all["slots"] if s["status"] != "past"]
check("00:00–24:00 的规则能覆盖一整天", bool(future_all) and all(x == "rest" for x in future_all),
      str(sorted(set(future_all))))

for r in json.loads(call("/api/admin")[1])["schedule"]["rules"]:
    call("/api/admin/schedule", "POST", {"action": "remove-rule", "id": r["id"]})
pub_clean = json.loads(call("/api/public")[1])
check("删掉规则后立即恢复可预约",
      any(s["status"] == "free" for s in pub_clean["slots"]))

print("=== 15. 主页图片（背景图 / 首屏图 / 头像） ===")
s, raw = call("/api/admin/media", "POST", {"image": "data:image/png;base64," + png_b64})
media = json.loads(raw) if s == 200 else {}
check("上传图片接口返回网址",
      s == 200 and str(media.get("url", "")).startswith("/api/media/"), raw[:80])
code, blob = call_bytes(media.get("url") or "/x")
check("上传的图片能直接打开且内容正确", code == 200 and blob == png_bytes, "status=%s" % code)

s, raw = call("/api/admin/media", "POST", {"image": "data:text/plain;base64,aGk="})
check("非法图片格式被拒绝(400)", s == 400, raw.strip())
s, raw = call("/api/admin/media", "POST", {})
check("没传图片被拒绝(400)", s == 400, raw.strip())

cur = json.loads(call("/api/admin")[1])
prof = dict(cur["profile"])
prof["background"] = media["url"]
prof["heroImage"] = media["url"]
prof["backgroundDim"] = 60
call("/api/admin/profile", "PUT", {"profile": prof, "revision": cur["profileRevision"]})
pub_img = json.loads(call("/api/public")[1])
check("公开接口带上了背景图和压暗设置",
      pub_img["profile"]["background"] == media["url"] and pub_img["profile"]["backgroundDim"] == 60)
check("背景图地址能被浏览器加载", call_bytes(pub_img["profile"]["background"])[0] == 200)

backup2 = json.loads(call("/api/admin/export")[1])
check("备份把资料里的图片也打包了",
      str((backup2.get("profileImages") or {}).get("background", "")).startswith("data:image/png;base64,"))

cur = json.loads(call("/api/admin")[1])
prof = dict(cur["profile"])
prof["background"] = ""
prof["heroImage"] = ""
call("/api/admin/profile", "PUT", {"profile": prof, "revision": cur["profileRevision"]})
check("背景图已清空", json.loads(call("/api/public")[1])["profile"]["background"] == "")

call("/api/admin/import", "POST", {"backup": backup2})
restored_bg = json.loads(call("/api/public")[1])["profile"]["background"]
check("从备份恢复后背景图回来了，而且是能打开的图片",
      restored_bg.startswith("/api/media/") and call_bytes(restored_bg)[0] == 200, restored_bg)

# 收尾：把三张图清空，回到初始状态
cur = json.loads(call("/api/admin")[1])
prof = dict(cur["profile"])
prof["background"] = prof["heroImage"] = prof["avatar"] = ""
call("/api/admin/profile", "PUT", {"profile": prof, "revision": cur["profileRevision"]})
check("清理完成（三张图为空）",
      json.loads(call("/api/public")[1])["profile"]["background"] == "")

print("=== 16. 微信分享卡片与复制兜底 ===")
html = call_text("/")[1]
og_image = meta(html, "og:image")
og_url = meta(html, "og:url")
check("og:image 不再缺失", bool(og_image), str(og_image))
check("og:image 是完整网址（微信只认绝对地址）", str(og_image).startswith("http"), str(og_image))
check("og:url 是完整网址", str(og_url).startswith("http"), str(og_url))
check("没设头像/首屏图时用自带分享图",
      str(og_image).endswith("/static/img/share-default.jpg"), str(og_image))
code, blob = call_bytes("/static/img/share-default.jpg")
check("自带分享缩略图能访问且是 JPEG", code == 200 and blob[:2] == b"\xff\xd8",
      "status=%s size=%.0fKB" % (code, len(blob) / 1024))

# 模拟 Render 转发：Host + https
code, html_prod = call_text("/", {"Host": "peiwan-site.onrender.com", "X-Forwarded-Proto": "https"})
check("部署后 og:image 自动变成你的域名",
      meta(html_prod, "og:image") == "https://peiwan-site.onrender.com/static/img/share-default.jpg",
      str(meta(html_prod, "og:image")))
code, html_prod2 = call_text("/", {"Host": "peiwan-site.onrender.com"})
check("没有 X-Forwarded-Proto 时默认按 https 处理",
      str(meta(html_prod2, "og:image")).startswith("https://peiwan-site.onrender.com/"),
      str(meta(html_prod2, "og:image")))

# 设了头像 → 分享图优先用头像
cur = json.loads(call("/api/admin")[1])
prof = dict(cur["profile"])
avatar_url, _ = None, None
s, raw = call("/api/admin/media", "POST", {"image": "data:image/png;base64," + png_b64})
avatar_url = json.loads(raw)["url"]
prof["avatar"] = avatar_url
call("/api/admin/profile", "PUT", {"profile": prof, "revision": cur["profileRevision"]})
html_av = call_text("/")[1]
check("设了头像后，分享缩略图改用头像",
      str(meta(html_av, "og:image")).endswith(avatar_url), str(meta(html_av, "og:image")))
cur = json.loads(call("/api/admin")[1])
prof = dict(cur["profile"])
prof["avatar"] = ""
call("/api/admin/profile", "PUT", {"profile": prof, "revision": cur["profileRevision"]})

# 微信号必须能在页面上直接看到（微信里自动复制常被拦）
check("页面上有直接显示微信号的位置", 'id="wechatLine"' in html)
check("复制有 execCommand 兜底", "execCommand" in call("/static/site.js")[1])
check("复制彻底失败时有手动复制弹窗", 'id="copyModal"' in html and "showCopyFallback" in call("/static/site.js")[1])

print("=== 17. 保活接口、签名登录、备份提醒 ===")
s, b = call("/api/ping")
check("GET /api/ping 可用（给保活定时器用，不查数据库）",
      s == 200 and json.loads(b).get("ok") is True, b.strip())

# 登录 Cookie 是"自带签名"的：服务端不保存任何会话，所以重启/休眠后不用重新登录
cookie_value = next((c.value for c in jar if c.name == "peiwan_session"), "")
check("拿到登录 Cookie", bool(cookie_value), cookie_value[:16] + "...")
code, _ = call_with_cookie("/api/admin", "peiwan_session=" + cookie_value)
check("Cookie 自带签名，服务端无需存会话（重启后仍登录）", code == 200, "status=%s" % code)
tampered = cookie_value[:-2] + ("ff" if not cookie_value.endswith("ff") else "aa")
code, _ = call_with_cookie("/api/admin", "peiwan_session=" + tampered)
check("篡改过的 Cookie 会被拒绝", code == 401, "status=%s" % code)
code, _ = call_with_cookie("/api/admin", "peiwan_session=9999999999.deadbeefdeadbeef")
check("伪造签名的 Cookie 会被拒绝", code == 401, "status=%s" % code)

# 部署到 HTTPS 后自动加 Secure，本地调试不加（否则登录不上）
req = urllib.request.Request(
    BASE + "/api/auth", data=json.dumps({"password": PASSWORD}).encode(),
    headers={"Content-Type": "application/json",
             "X-Forwarded-Proto": "https", "Host": "peiwan-site.onrender.com"})
with urllib.request.urlopen(req, timeout=20) as r:
    sc_https = r.headers.get("Set-Cookie") or ""
check("HTTPS 环境下 Cookie 自动带 Secure", "Secure" in sc_https, sc_https[:60])
req = urllib.request.Request(
    BASE + "/api/auth", data=json.dumps({"password": PASSWORD}).encode(),
    headers={"Content-Type": "application/json"})
with urllib.request.urlopen(req, timeout=20) as r:
    sc_http = r.headers.get("Set-Cookie") or ""
check("本地 http 调试不加 Secure（否则登录不上）", "Secure" not in sc_http, sc_http[:60])

# 备份提醒
adm0 = json.loads(call("/api/admin")[1])
check("后台返回「上次导出备份时间」", "lastExportAt" in adm0)
call("/api/admin/export")
after_export = json.loads(call("/api/admin")[1])["lastExportAt"]
check("下载备份后会记录时间（用来提醒你勤备份）",
      after_export > 0 and (time.time() * 1000 - after_export) < 60000,
      "lastExportAt=%s" % after_export)

print()
print("通过 %d 项，失败 %d 项" % (ok, fail))
sys.exit(1 if fail else 0)
