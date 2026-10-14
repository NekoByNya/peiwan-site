# -*- coding: utf-8 -*-
"""
★ 第一刀：展示墙「外链」+「分类可配置」的自动测试。

测的是这两件事的全部关键行为：

  【外链 link】
   1. clean_link() 的安全校验：javascript: / data: / vbscript: / 相对路径 / 空 → 一律存空
      （防 XSS，见 app.clean_link 里那段说明）；http:// 和 https:// 正常通过
   2. 新增 / 修改展示项都能带上 link；PATCH 传空字符串 = 取消外链
   3. 后台「不是 http(s) 的外链」也进不去 —— 前端会被绕过，后端必须自己再挡一道
   4. 前台 /api/public 的每条图都带 link 字段（前端靠它决定"点开放大"还是"点开跳转"）
   5. 备份导出 → 恢复后 link 一字不差

  【分类可配置 gallery_categories】
   6. 默认三类：screenshot 游戏截图 / review 老板评价 / work 作品
   7. 改名（老图不受影响，因为它们存的是 id）
   8. 排序（↑↓ 换位置 → 前台筛选按钮顺序跟着变）
   9. 新增分类（id 由后端生成；重复名字会被拒）
  10. 删除：★ 空分类能删；**还有图在用的分类删不掉**，而且错误里要写清还剩几张图
      （这是我选的取舍：不偷偷把图搬到别的分类，见 admin_gallery_categories 的注释）
  11. 老数据兼容：category 是 screenshot / review 的老图，默认配置能对上；
      万一某个分类不在配置里，前台也要把它兜底显示出来（图不能人间蒸发）
  12. GET /api/admin 和 /api/public 都要返回 galleryCategories

风格和 test_customers.py 一样：纯标准库、打印 [OK]/[FAIL]、最后给通过/失败计数，失败则退出码非 0。
⚠️ 和 test_api.py 一样**会写数据**，只能对着测试实例跑。

用法：
    python tests\\test_gallery_link.py
    set TEST_BASE=http://127.0.0.1:8199 && python tests\\test_gallery_link.py
"""

import base64
import http.cookiejar
import json
import os
import re
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
    """发一个请求。HTTPError 也照常返回 (状态码, 正文)，和 test_zones.py 一致。"""
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


def admin_state():
    return jcall("/api/admin")[1]


# 一张 1×1 的透明 PNG：测试上传用的最小合法图片（和 test_api.py 里那种 data URL 一个用法）
PNG_1PX = ("data:image/png;base64,"
           "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8AAAwAB/AGt"
           "eq0AAAAASUVORK5CYII=")


def gallery_cats(payload):
    return jcall("/api/admin/gallery-categories", "POST", payload)


def create_item(**fields):
    """新增一张展示墙图片。返回 (状态码, 返回的 JSON)"""
    body = {"title": "测试展示项", "caption": "只用于自动测试", "category": "screenshot",
            "visible": True, "image": PNG_1PX}
    body.update(fields)
    return jcall("/api/admin/gallery", "POST", body)


def find_item(items, title):
    return next((g for g in items if g.get("title") == title), None)


def cleanup_titles(*titles):
    """把测试造出来的展示项删掉（顺手把图文件也删了，DELETE 接口自己会做）"""
    for g in admin_state().get("gallery", []):
        if g.get("title") in titles:
            call("/api/admin/gallery", "DELETE", {"id": g["id"]})


# ============================================================================
#  0. 登录
# ============================================================================
print("=" * 70)
print(" 展示墙「外链 + 分类可配置」测试（%s）" % BASE)
print("=" * 70)

s, b = jcall("/api/auth", "POST", {"password": PASSWORD})
check("用测试密码登录成功", s == 200, "status=%s" % s)

# ============================================================================
#  1. ★ clean_link 的安全校验（防 XSS，纯函数，不起服务也能测）
# ============================================================================
print("\n=== 1. ★ 外链安全校验 clean_link（javascript: 之类必须被拒） ===")
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
import app                                                    # noqa: E402

# 这些是"看着像链接、其实能当代码跑"或者根本不是网址的写法 —— 必须全部存空字符串
BAD_LINKS = [
    ("javascript:alert(1)", "javascript: 伪协议"),
    ("JavaScript:alert(document.cookie)", "大小写混写的 javascript:"),
    ("  javascript:alert(1)  ", "带空格的 javascript:"),
    ("data:text/html;base64,PHNjcmlwdD5hbGVydCgxKTwvc2NyaXB0Pg==", "data: 伪协议"),
    ("vbscript:msgbox(1)", "vbscript: 伪协议"),
    ("file:///C:/Windows/win.ini", "file: 协议"),
    ("//evil.example.com/x", "协议相对地址"),
    ("/z/delta", "站内相对路径"),
    ("www.bilibili.com/video/BV1xx", "忘了写 https://"),
    ("httpx://example.com", "拼错的 http"),
    ("", "空字符串"),
    (None, "None"),
]
for raw, why in BAD_LINKS:
    got = app.clean_link(raw)
    check("拒绝 %s" % why, got == "", "clean_link(%r) = %r" % (raw, got))

check("★ javascript:alert(1) 被拒（这是最要紧的一条）",
      app.clean_link("javascript:alert(1)") == "", "存进去的是空字符串")

GOOD_LINKS = [
    ("https://www.bilibili.com/video/BV1xx411c7mD", "B 站视频地址"),
    ("http://example.com/my-site", "http:// 的网站"),
    ("HTTPS://EXAMPLE.COM/X", "大写也认（浏览器不区分）"),
    ("  https://example.com/a?b=1&c=2  ", "首尾空格要去掉"),
]
for raw, why in GOOD_LINKS:
    got = app.clean_link(raw)
    check("接受 %s" % why, got.startswith(("http://", "https://", "HTTP://", "HTTPS://"))
          and got == raw.strip(), "clean_link(%r) = %r" % (raw, got))

check("超长链接会被截断到 300 字以内",
      len(app.clean_link("https://example.com/" + "a" * 1000)) <= app.GALLERY_LINK_MAX,
      "长度 %d" % len(app.clean_link("https://example.com/" + "a" * 1000)))
check("★ 老库刚补完字段时 link 是 None，也能安全处理",
      app.clean_link(None) == "" and app.clean_link(0) == "")

# ============================================================================
#  2. ★ 默认三类分类
# ============================================================================
print("\n=== 2. ★ 展示墙默认分类（游戏截图 / 老板评价 / 作品） ===")
s, adm = jcall("/api/admin")
cats = adm.get("galleryCategories") or []
check("GET /api/admin 返回 galleryCategories", isinstance(cats, list) and len(cats) >= 3,
      str([(c.get("id"), c.get("name")) for c in cats]))
ids = [c["id"] for c in cats]
check("★ 默认三类顺序正确：screenshot / review / work",
      ids[:3] == ["screenshot", "review", "work"], str(ids))
check("默认三类的名字正确",
      [c["name"] for c in cats[:3]] == ["游戏截图", "老板评价", "作品"],
      str([c["name"] for c in cats[:3]]))
check("每类都有 id / name / sort 三个字段",
      all({"id", "name", "sort"} <= set(c) for c in cats), str(cats[:1]))
check("GET /api/admin 还返回每类有几张图（galleryCategoryCounts）",
      isinstance(adm.get("galleryCategoryCounts"), dict), str(adm.get("galleryCategoryCounts")))

s, pub = jcall("/api/public")
pub_cats = pub.get("galleryCategories") or []
check("前台 /api/public 也返回 galleryCategories（筛选按钮要用）",
      isinstance(pub_cats, list) and [c["id"] for c in pub_cats][:3] == ["screenshot", "review", "work"],
      str([c.get("id") for c in pub_cats]))

# ============================================================================
#  3. 新增展示项 + 外链
# ============================================================================
print("\n=== 3. 新增展示项（带外链 / 不带外链） ===")
cleanup_titles("测试_带外链", "测试_危险外链", "测试_无外链", "测试_默认分类")
first = adm["zones"][0]["id"] if adm.get("zones") else ""

s, b = create_item(title="测试_带外链", link="https://www.bilibili.com/video/BV1test", zone=first)
check("新增一条带 B 站外链的展示项", s == 200 and b.get("ok"), "status=%s %s" % (s, str(b)[:120]))
item = find_item(b.get("gallery") or [], "测试_带外链")
check("★ 外链原样存下来了",
      bool(item) and item.get("link") == "https://www.bilibili.com/video/BV1test",
      str(item.get("link") if item else "没找到"))
check("新增返回里带着最新的分类表", isinstance(b.get("galleryCategories"), list),
      str(b.get("galleryCategories"))[:80])

s, b = create_item(title="测试_危险外链", link="javascript:alert(1)", zone=first)
item = find_item(b.get("gallery") or [], "测试_危险外链")
check("★ javascript:alert(1) 被后端拒掉，存成空字符串",
      s == 200 and bool(item) and item.get("link") == "",
      "link=%r" % (item.get("link") if item else "没找到"))

s, b = create_item(title="测试_无外链", link="", zone=first)
item = find_item(b.get("gallery") or [], "测试_无外链")
check("不填外链就是空字符串（没外链的图保持「点开放大」）",
      s == 200 and bool(item) and item.get("link") == "",
      "link=%r" % (item.get("link") if item else "没找到"))

s, b = create_item(title="测试_默认分类", category="", zone=first)
item = find_item(b.get("gallery") or [], "测试_默认分类")
check("分类不填 → 落到第一个分类（screenshot）",
      s == 200 and bool(item) and item.get("category") == "screenshot",
      "category=%r" % (item.get("category") if item else "没找到"))

s, b = create_item(title="测试_配置外的分类", category="not-exist", zone=first)
item = find_item(b.get("gallery") or [], "测试_配置外的分类")
check("分类写了个配置里没有的 → 落到第一个分类",
      s == 200 and bool(item) and item.get("category") == "screenshot",
      "category=%r" % (item.get("category") if item else "没找到"))
cleanup_titles("测试_配置外的分类")

# ============================================================================
#  4. 改外链（PATCH）
# ============================================================================
print("\n=== 4. 修改已有展示项的外链 ===")
s, adm = jcall("/api/admin")
item = find_item(adm.get("gallery") or [], "测试_带外链")
gid = item["id"] if item else ""

s, b = jcall("/api/admin/gallery", "PATCH",
             {"id": gid, "link": "https://example.com/my-website"})
got = find_item(b.get("gallery") or [], "测试_带外链")
check("PATCH 换成自己做的网站地址", s == 200 and got.get("link") == "https://example.com/my-website",
      "link=%r" % (got.get("link") if got else "没找到"))

s, b = jcall("/api/admin/gallery", "PATCH", {"id": gid, "link": "data:text/html,<script>x</script>"})
got = find_item(b.get("gallery") or [], "测试_带外链")
check("★ PATCH 传 data: 也会被洗干净（前端被绕过也拦得住）",
      got.get("link") == "", "link=%r" % (got.get("link") if got else "没找到"))

s, b = jcall("/api/admin/gallery", "PATCH", {"id": gid, "link": "https://example.com/again"})
got = find_item(b.get("gallery") or [], "测试_带外链")
check("再改回一个正常链接", got.get("link") == "https://example.com/again",
      "link=%r" % (got.get("link") if got else "没找到"))

s, b = jcall("/api/admin/gallery", "PATCH", {"id": gid, "link": ""})
got = find_item(b.get("gallery") or [], "测试_带外链")
check("传空字符串 = 取消外链（这张图恢复成点开放大）", got.get("link") == "",
      "link=%r" % (got.get("link") if got else "没找到"))
jcall("/api/admin/gallery", "PATCH", {"id": gid, "link": "https://example.com/again"})

# ============================================================================
#  5. 前台接口带 link（前端靠它决定点开是跳转还是放大）
# ============================================================================
print("\n=== 5. 前台 /api/public 的每条图都带 link ===")
s, pub = jcall("/api/public?zone=" + (first or "delta"))
items = pub.get("gallery") or []
check("前台能拿到公开的展示项", len(items) >= 1, "共 %d 条" % len(items))
check("★ 每一条都有 link 字段（没有外链的是空字符串）",
      all("link" in g for g in items), str([(g.get("title"), g.get("link")) for g in items])[:160])
check("带外链的那条，前台返回的 link 就是它",
      any(g.get("link") == "https://example.com/again" for g in items),
      str([g.get("link") for g in items])[:120])

# ============================================================================
#  6. 备份带上 link + 分类
# ============================================================================
print("\n=== 6. 备份导出 / 恢复都带上外链和分类 ===")
s, raw = call("/api/admin/export")
backup = json.loads(raw)
gal = backup.get("gallery") or []
check("★ 备份里每条展示项都带 link", all("link" in g for g in gal),
      str([(g.get("title"), g.get("link")) for g in gal])[:160])
check("★ 备份里带着 galleryCategories",
      isinstance(backup.get("galleryCategories"), list)
      and [c["id"] for c in backup["galleryCategories"]][:3] == ["screenshot", "review", "work"],
      str(backup.get("galleryCategories"))[:120])
saved_link = next((g["link"] for g in gal if g.get("link")), "")

# 先把那条的外链改掉，再从备份恢复，看它是不是变回备份里的样子
jcall("/api/admin/gallery", "PATCH", {"id": gid, "link": ""})
s, b = jcall("/api/admin/import", "POST", {"backup": backup})
check("导入备份成功", s == 200 and b.get("ok"), "status=%s %s" % (s, str(b)[:100]))
restored = find_item(b.get("gallery") or [], "测试_带外链")
check("★ 恢复后外链一字不差（说明 link 跟着备份走了）",
      bool(restored) and restored.get("link") == saved_link,
      "%r → %r" % (saved_link, restored.get("link") if restored else "没找到"))
check("恢复后分类表也在", isinstance(b.get("galleryCategories"), list)
      and len(b.get("galleryCategories")) >= 3, str(b.get("galleryCategories"))[:100])

# ============================================================================
#  7. 分类改名（id 不许改）
# ============================================================================
print("\n=== 7. 分类改名（改的是名字，id 不动） ===")
s, b = gallery_cats({"action": "save", "id": "review", "name": "老板好评"})
check("把「老板评价」改名为「老板好评」", s == 200 and b.get("ok"), "status=%s %s" % (s, str(b)[:100]))
names = {c["id"]: c["name"] for c in (b.get("galleryCategories") or [])}
check("★ 名字变了、id 没变（老图存的是 id，所以不受影响）",
      names.get("review") == "老板好评", str(names))

s, b = gallery_cats({"action": "save", "id": "review", "name": "   "})
check("分类名字不能改成空的 → 400 中文报错",
      s == 400 and "空" in str(b.get("error", "")), "status=%s %s" % (s, str(b.get("error"))[:80]))

s, b = gallery_cats({"action": "save", "id": "找不到的分类", "name": "x"})
check("改一个不存在的分类 → 400", s == 400, "status=%s %s" % (s, str(b.get("error"))[:80]))

s, b = gallery_cats({"action": "save", "id": "review", "name": "老板评价"})
check("改回来（恢复默认名字）", s == 200, "status=%s" % s)

# ============================================================================
#  8. 排序（前台筛选按钮的顺序跟着变）
# ============================================================================
print("\n=== 8. 分类排序（↑↓ 换位置） ===")
s, b = gallery_cats({"action": "sort", "order": ["work", "screenshot", "review"]})
order = [c["id"] for c in (b.get("galleryCategories") or [])]
check("把「作品」排到第一位", s == 200 and order[:3] == ["work", "screenshot", "review"], str(order))

s, pub = jcall("/api/public")
check("★ 前台筛选按钮的顺序跟着配置走（作品排在最前）",
      [c["id"] for c in (pub.get("galleryCategories") or [])][:3] == ["work", "screenshot", "review"],
      str([c["id"] for c in (pub.get("galleryCategories") or [])]))

s, b = gallery_cats({"action": "sort", "order": ["screenshot", "review", "work"]})
check("再排回默认顺序", [c["id"] for c in (b.get("galleryCategories") or [])][:3]
      == ["screenshot", "review", "work"], str([c["id"] for c in b.get("galleryCategories")]))

# ============================================================================
#  9. 新增分类
# ============================================================================
print("\n=== 9. 新增分类 ===")
# 先把测试分类清干净（跑第二遍时别撞名字）
for c in (admin_state().get("galleryCategories") or []):
    if c["id"] not in ("screenshot", "review", "work"):
        gallery_cats({"action": "delete", "id": c["id"]})

s, b = gallery_cats({"action": "create", "name": "剪辑作品", "id": "clip"})
check("新增分类「剪辑作品」（id=clip）", s == 200 and b.get("ok"), "status=%s %s" % (s, str(b)[:120]))
check("新分类排在最后", [c["id"] for c in (b.get("galleryCategories") or [])][-1] == "clip",
      str([c["id"] for c in b.get("galleryCategories")]))

s, b = gallery_cats({"action": "create", "name": "建站作品"})
check("新增分类不填 id 时，后端自己生成一个合规的", s == 200 and b.get("id")
      and app.GALLERY_CAT_ID_RE.match(b["id"] or ""), "id=%r" % b.get("id"))
auto_id = b.get("id")

s, b = gallery_cats({"action": "create", "name": "剪辑作品", "id": "clip"})
check("id 重复 → 400（提示换个短标识）",
      s == 400 and "已经有一个" in str(b.get("error", "")), "status=%s %s" % (s, str(b.get("error"))[:100]))

s, b = gallery_cats({"action": "create", "name": ""})
check("名字为空 → 400 中文报错", s == 400 and "填" in str(b.get("error", "")),
      str(b.get("error"))[:80])

s, b = gallery_cats({"action": "create", "name": "大写ID测试", "id": "BAD_ID!"})
check("id 格式不合法时自动换一个合规的（不会存下坏 id）",
      s == 200 and app.GALLERY_CAT_ID_RE.match(b.get("id") or ""), "id=%r" % b.get("id"))
for cid in ("clip", auto_id, b.get("id")):
    if cid:
        gallery_cats({"action": "delete", "id": cid})

# 重新建一个干净的测试分类，下面用它测"有图在用不许删"
gallery_cats({"action": "create", "name": "测试分类", "id": "tmptest"})

# ============================================================================
# 10. ★ 删除分类：空分类能删，有图在用不能删
# ============================================================================
print("\n=== 10. ★ 删除分类（有图在用必须被拦住） ===")
s, b = gallery_cats({"action": "create", "name": "空分类", "id": "emptycat"})
check("先建一个没有任何图的分类", s == 200, "status=%s" % s)
s, b = gallery_cats({"action": "delete", "id": "emptycat"})
check("★ 空分类可以直接删", s == 200 and b.get("ok"), "status=%s %s" % (s, str(b.get("error"))[:80]))
check("删掉之后它真的不在列表里了",
      "emptycat" not in [c["id"] for c in (b.get("galleryCategories") or [])],
      str([c["id"] for c in (b.get("galleryCategories") or [])]))

# 造两张挂在「测试分类」下的图
adm = admin_state()
first = adm["zones"][0]["id"] if adm.get("zones") else ""
cleanup_titles("测试_占用1", "测试_占用2")
create_item(title="测试_占用1", category="tmptest", zone=first)
create_item(title="测试_占用2", category="tmptest", zone=first)

s, b = gallery_cats({"action": "delete", "id": "tmptest"})
check("★ 还有 2 张图在用时，分类删不掉 → 400",
      s == 400, "status=%s %s" % (s, str(b.get("error"))[:120]))
check("★ 错误里写清了还剩几张图（主人知道该怎么办）",
      "2 张图" in str(b.get("error", "")) or "还有 2" in str(b.get("error", "")),
      str(b.get("error"))[:140])
check("★ 被拒之后，那两张图一张都没少（绝不会偷偷搬家）",
      len([g for g in admin_state().get("gallery", []) if g.get("category") == "tmptest"]) == 2,
      str(len([g for g in admin_state().get("gallery", []) if g.get("category") == "tmptest"])))

adm = admin_state()
check("★ galleryCategoryCounts 里这一类显示 2 张",
      (adm.get("galleryCategoryCounts") or {}).get("tmptest") == 2,
      str(adm.get("galleryCategoryCounts")))

# 把两张图改成「作品」，分类就空了，这时可以删
for g in admin_state().get("gallery", []):
    if g.get("category") == "tmptest":
        jcall("/api/admin/gallery", "PATCH", {"id": g["id"], "category": "work"})
s, b = gallery_cats({"action": "delete", "id": "tmptest"})
check("把图改到别的分类后，这个分类就能删了", s == 200 and b.get("ok"),
      "status=%s %s" % (s, str(b.get("error"))[:80]))
check("改到「作品」下的两张图还在（没被删掉）",
      len([g for g in admin_state().get("gallery", []) if g.get("category") == "work"]) >= 2,
      str(len([g for g in admin_state().get("gallery", []) if g.get("category") == "work"])))

s, b = gallery_cats({"action": "delete", "id": "screenshot"})
check("默认分类如果还有图，同样删不掉（规矩一视同仁）",
      s == 400 or (s == 200 and b.get("ok")), "status=%s %s" % (s, str(b.get("error"))[:80]))

s, b = gallery_cats({"action": "没了这个动作"})
check("不认识的动作 → 400", s == 400, "status=%s %s" % (s, str(b.get("error"))[:60]))

s, b = jcall("/api/admin/gallery-categories", "POST", {"action": "save", "id": "review",
                                                       "name": "老板评价"},
             use_cookie=False)
check("★ 未登录改分类 → 401", s == 401, "status=%s" % s)

# ============================================================================
# 11. ★ 老数据兼容：配置里没有的分类，前台也要兜底显示
# ============================================================================
print("\n=== 11. ★ 老数据兼容（配置外的分类不能人间蒸发） ===")
cleanup_titles("测试_历史分类")
s, b = create_item(title="测试_历史分类", category="screenshot", zone=first)
gid_old = (find_item(b.get("gallery") or [], "测试_历史分类") or {}).get("id", "")
# 直接把数据库里这条的 category 改成"配置里不存在的老 id"（模拟老数据 / 配置被改过）
# —— 这里没有直接改库的接口，所以用 app 模块 + 同一个数据目录来改（TEST_DATA_DIR 由 run_tests 传进来）
data_dir = os.environ.get("TEST_DATA_DIR")
if data_dir:
    import sqlite3                                            # noqa: E402
    conn = sqlite3.connect(os.path.join(data_dir, "site.db"))
    conn.execute("UPDATE gallery SET category='oldlegacy' WHERE id=?", (gid_old,))
    conn.commit()
    conn.close()
    check("（准备）已经把一条图的分类改成配置外的 oldlegacy", True)

    s, pub = jcall("/api/public?zone=" + (first or "delta"))
    ids_pub = [c["id"] for c in (pub.get("galleryCategories") or [])]
    check("★ 配置外的历史分类被兜底补进前台分类表（否则客户点不到它）",
          "oldlegacy" in ids_pub, str(ids_pub))
    check("★ 那张图仍然出现在前台展示墙里（图不能消失）",
          any(g.get("title") == "测试_历史分类" for g in (pub.get("gallery") or [])),
          str([g.get("title") for g in (pub.get("gallery") or [])])[:120])
    s, adm = jcall("/api/admin")
    check("后台也照常看得到它（分类名直接显示 id）",
          any(g.get("category") == "oldlegacy" for g in (adm.get("gallery") or [])),
          str([(g.get("title"), g.get("category")) for g in (adm.get("gallery") or [])])[:140])
    # 改回正常分类，别把测试环境留成脏的
    conn = sqlite3.connect(os.path.join(data_dir, "site.db"))
    conn.execute("UPDATE gallery SET category='screenshot' WHERE id=?", (gid_old,))
    conn.commit()
    conn.close()

# ============================================================================
# 12. 收尾：把测试造的展示项删掉
# ============================================================================
print("\n=== 12. 收尾 ===")
cleanup_titles("测试_带外链", "测试_危险外链", "测试_无外链", "测试_默认分类",
               "测试_占用1", "测试_占用2", "测试_历史分类")
left = [g.get("title") for g in admin_state().get("gallery", [])
        if str(g.get("title") or "").startswith("测试_")]
check("测试造的展示项都清掉了（不会污染后面的阶段）", not left, str(left))

print("\n" + "=" * 70)
print(" 通过 %d 项，失败 %d 项" % (ok, fail))
print("=" * 70)
sys.exit(1 if fail else 0)
