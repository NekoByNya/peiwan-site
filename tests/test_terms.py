# -*- coding: utf-8 -*-
"""
★ 第四刀之二：**服务条款页**（/terms）+ 后台「文案 → 服务条款」编辑器。

为什么要单独一页条款：主人是个人接单，客户一定会问"账号安全吗""能退款吗""封号怎么办"。
写在页面上比每次微信里重复解释省事，而且**能直接发链接**（有 og 标签，微信里转发有卡片）。

这个文件测的是：

   1. `/terms` 能打开，标题、每一节、最后更新日期都在（**服务端渲染**，不需要 JS）
   2. og 标签齐全（要能直接发链接给客户：标题 / 简介 / 图 / 网址）
   3. 首页和分区页都有入口（顶部导航 + 页脚那一行小链接）
   4. 后台能改：标题、每一节的正文 → 前台立刻变
   5. 增 / 删 / **排序**（↑↓ 就是数组顺序）
   6. 空小节（标题和正文都空）会被丢掉，不算错，但要回报丢了几节
   7. 标题不能空 → 400；小节数、长度都有上限
   8. 「恢复默认条款」回到出厂那份（7 节）
   9. 未登录 → 401
  10. 备份：导出带 terms；改了之后恢复 → 变回备份时的样子；
      **老备份（没有 terms 字段）导入时一个字都不动**
  11. 正文里 `{wechat}` 会变成主人的微信号（换了微信号不用回来改条款）
  12. 正文的换行是"空行分段、单换行不断行"
  13. 文案是纯文本：正文里写 <script> 会被转义（不能变成 XSS 入口）

风格和 test_texts.py / test_gallery_link.py 一样。⚠️ 会写数据，只能对着测试实例跑。
用法：
    python tests\\test_terms.py
"""

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


def admin_terms():
    return jcall("/api/admin")[1].get("terms") or {}


def save_terms(title, sections):
    return jcall("/api/admin/terms", "POST",
                 {"action": "save", "title": title, "sections": sections})


def terms_page():
    """拿 /terms 的原始 HTML。

    另外返回"正文区从哪儿开始"—— 页面 <head> 里还有 og:description（它是第一节正文的开头），
    直接对整页做 find() 会撞到它，所以比位置的时候一律从正文区往后比。
    """
    s, raw = call("/terms")
    return s, raw


def body_at(raw, text):
    """某一节/某句话在**正文区**里的位置（找不到就是 -1）"""
    start = raw.find('class="panel terms-body"')
    return raw.find(text, start) if start >= 0 else -1


# ============================================================================
#  0. 登录
# ============================================================================
print("=" * 70)
print(" 服务条款页测试（%s）" % BASE)
print("=" * 70)

s, b = jcall("/api/auth", "POST", {"password": PASSWORD})
check("用测试密码登录成功", s == 200, "status=%s" % s)

# ============================================================================
#  1. 出厂那份默认条款
# ============================================================================
print("\n=== 1. 出厂默认条款（7 节，语气实在） ===")
terms = admin_terms()
check("★ 后台拿到了条款（标题 + 小节 + 最后更新日期）",
      bool(terms.get("title")) and isinstance(terms.get("sections"), list),
      str(terms.get("title")))
check("★ 标题是「服务条款」", terms.get("title") == "服务条款", str(terms.get("title")))
check("★ 小节数：7 节（服务内容 / 下单 / 付款 / 取消改期 / 账号安全 / 责任范围 / 联系方式）",
      len(terms.get("sections") or []) == 7, "共 %d 节" % len(terms.get("sections") or []))
heads = [x.get("heading") for x in terms.get("sections") or []]
check("每一节都有小标题", all(h for h in heads), str(heads)[:160])
check("★ 账号安全那一节必须在（这是保护主人最关键的一节）",
      any("账号安全" in (h or "") for h in heads), str(heads)[:160])
check("★ 责任范围那一节也在", any("责任" in (h or "") for h in heads), str(heads)[:160])
body_all = " ".join((x.get("body") or "") for x in terms.get("sections") or [])
check("★ 条款里写明了「请你自己保管」账号密码", "自己保管" in body_all)
check("★ 也写明了「不会用外挂 / 脚本」（主人自己定的安全底线）", "外挂" in body_all)
check("★ 退款说清楚了（保护客户，也就保护了主人）", "退" in body_all)
check("最后更新日期是今天（北京时间），不是空的",
      bool(re.match(r"^\d{4}-\d{2}-\d{2}$", terms.get("updated") or "")),
      str(terms.get("updated")))

# ============================================================================
#  2. /terms 页面（服务端渲染，不需要 JS）
# ============================================================================
print("\n=== 2. /terms 页面能打开、内容完整 ===")
s, raw = terms_page()
check("GET /terms → 200", s == 200, "status=%s" % s)
check("★ 标题在页面上", "服务条款" in raw)
check("★ 每一节的小标题都在", all(h in raw for h in heads),
      str([h for h in heads if h not in raw])[:120])
check("★ 正文在页面上（服务端渲染，客户不装 JS 也看得到）",
      "没有工作室、没有中间商" in raw)
check("★ 页面上有「最后更新：」那一行",
      "最后更新：" in raw and terms["updated"] in raw)
check("页面里有 section 结构（styles.css 的 .terms-section 就是它）",
      'class="terms-section"' in raw)
check("★ 这一页不依赖 JavaScript（没有 <script src=）", "<script src=" not in raw)

og_title = re.search(r'property="og:title" content="([^"]*)"', raw)
og_desc = re.search(r'property="og:description" content="([^"]*)"', raw)
og_url = re.search(r'property="og:url" content="([^"]*)"', raw)
og_img = re.search(r'property="og:image" content="([^"]*)"', raw)
check("★ og:title 有（分享给客户时卡片上就是它）",
      bool(og_title) and "服务条款" in og_title.group(1), str(og_title and og_title.group(1)))
check("★ og:description 有（取第一节正文的开头）",
      bool(og_desc) and len(og_desc.group(1)) > 10, str(og_desc and og_desc.group(1))[:60])
check("★ og:url 就是 /terms（要能直接发这个链接）",
      bool(og_url) and og_url.group(1).endswith("/terms"), str(og_url and og_url.group(1)))
check("og:image 有（转发时有缩略图）", bool(og_img) and "http" in og_img.group(1),
      str(og_img and og_img.group(1))[:60])

check("★ 顶部导航有「我能做什么 / 关于我 / 服务条款」",
      all(x in raw for x in ("我能做什么", "关于我", "服务条款")))
check("★ 当前页高亮（nav 上那个 active）", 'class="active" href="/terms"' in raw)
check("底部有回主页的链接", 'href="/"' in raw)

# 首页 / 分区页的入口
s, home = call("/")
check("★ 首页顶部导航有服务条款入口", 'href="/terms"' in home)
check("★ 首页页脚也有一行小链接", 'class="footer-links"' in home and 'href="/terms"' in home)
adm = jcall("/api/admin")[1]
zones = adm.get("zones") or []
if zones:
    s, zp = call("/z/" + zones[0]["id"])
    check("★ 分区页顶部导航也有条款入口", 'href="/terms"' in zp)
    check("★ 分区页页脚也有那一行小链接", 'class="footer-links"' in zp)

# ============================================================================
#  3. 后台改标题和正文 → 前台立刻变
# ============================================================================
print("\n=== 3. 改标题 / 正文，前台立刻变 ===")
secs = [dict(x) for x in terms["sections"]]
secs[0]["body"] = "第一段是改过的。\n第二行接着。\n\n第二段开始。"
s, b = save_terms("我的服务约定", secs)
check("保存条款成功", s == 200 and b.get("ok"), "status=%s %s" % (s, str(b)[:100]))
check("★ 标题改了", b.get("terms", {}).get("title") == "我的服务约定",
      str(b.get("terms", {}).get("title")))
check("★ 最后更新日期是服务器自己写上的（不用前端传）",
      bool(re.match(r"^\d{4}-\d{2}-\d{2}$", b["terms"].get("updated") or "")),
      str(b["terms"].get("updated")))
check("改一节正文不会动别的小节",
      b["terms"]["sections"][1]["heading"] == secs[1]["heading"])

s, raw = terms_page()
check("★ 前台立刻是新标题", "我的服务约定" in raw)
check("★ 前台立刻是新正文", "第一段是改过的" in raw)
check("★ 空行分段（两个 <p>）", raw.count("<p>第一段是改过的。") == 1)
check("★ 单换行不断行（变成 <br>），列表式的写法不会挤成一坨",
      "第一段是改过的。<br>第二行接着。" in raw,
      str(raw[body_at(raw, "第一段是改过的"):][:120]))

# ============================================================================
#  4. 增 / 删 / 排序
# ============================================================================
print("\n=== 4. 增删小节 + 排序（↑↓ 就是数组顺序） ===")
more = [dict(x) for x in secs] + [{"heading": "八、新加的一节", "body": "这是新加的。"}]
s, b = save_terms("我的服务约定", more)
check("新增一节", s == 200 and len(b["terms"]["sections"]) == 8,
      "共 %d 节" % len(b["terms"]["sections"]))
s, raw = terms_page()
check("新那一节出现在页面上（排在最后）",
      body_at(raw, "八、新加的一节") > body_at(raw, heads[0]),
      "位置=%d vs %d" % (body_at(raw, "八、新加的一节"), body_at(raw, heads[0])))

# 排序：把新加那节挪到第一位
moved = [more[-1]] + more[:-1]
s, b = save_terms("我的服务约定", moved)
check("★ 调顺序（后台的 ↑↓ 就是调这个数组）", s == 200
      and b["terms"]["sections"][0]["heading"] == "八、新加的一节",
      str([x["heading"] for x in b["terms"]["sections"]][:3]))
s, raw = terms_page()
check("★ 前台顺序跟着变（客户看到的是第一节）",
      body_at(raw, "八、新加的一节") < body_at(raw, heads[0]),
      "%d < %d" % (body_at(raw, "八、新加的一节"), body_at(raw, heads[0])))

# 删除：把新加那节去掉
s, b = save_terms("我的服务约定", [dict(x) for x in more[:-1]])
check("删除一节", s == 200 and len(b["terms"]["sections"]) == 7,
      "共 %d 节" % len(b["terms"]["sections"]))
s, raw = terms_page()
check("删掉的那节不在页面上了", "八、新加的一节" not in raw)

# ============================================================================
#  5. 空小节 / 校验 / 上限
# ============================================================================
print("\n=== 5. 空小节与校验 ===")
s, b = save_terms("我的服务约定", [{"heading": "一、只有标题", "body": "有正文。"},
                                   {"heading": "", "body": ""},
                                   {"heading": "   ", "body": "  "}])
check("★ 标题和正文都空的小节被丢掉（= 你把这一节删了，不算错）",
      s == 200 and len(b["terms"]["sections"]) == 1, "共 %d 节" % len(b["terms"]["sections"]))
check("★ 丢了几节会回报给后台（好提示一句）", b.get("dropped") == 2, "dropped=%s" % b.get("dropped"))

s, b = save_terms("", [{"heading": "一", "body": "x"}])
check("标题空 → 400 中文报错", s == 400 and "标题" in str(b.get("error", "")),
      "status=%s %s" % (s, str(b.get("error"))[:60]))

s, b = jcall("/api/admin/terms", "POST", {"action": "save", "title": "x", "sections": "不是数组"})
check("sections 不是数组 → 400", s == 400 and "格式" in str(b.get("error", "")),
      "status=%s %s" % (s, str(b.get("error"))[:60]))

long_body = "长" * 5000
s, b = save_terms("上限测试", [{"heading": "一" * 100, "body": long_body}])
check("★ 正文超长会被截断（3000 字上限）",
      len(b["terms"]["sections"][0]["body"]) == 3000,
      "长度=%d" % len(b["terms"]["sections"][0]["body"]))
check("小标题超长也截断（40 字）", len(b["terms"]["sections"][0]["heading"]) == 40,
      "长度=%d" % len(b["terms"]["sections"][0]["heading"]))

s, b = save_terms("节数上限", [{"heading": "第 %d 节" % i, "body": "x"} for i in range(50)])
check("★ 小节数有上限（最多 30 节，多余的丢掉）",
      s == 200 and len(b["terms"]["sections"]) == 30, "共 %d 节" % len(b["terms"]["sections"]))

# ============================================================================
#  6. {wechat} 占位符 + 纯文本（不认 HTML）
# ============================================================================
print("\n=== 6. {wechat} 换微信号 + 文案是纯文本 ===")
adm = jcall("/api/admin")[1]
wechat = str((adm.get("profile") or {}).get("wechat") or "")
s, b = save_terms("占位符测试", [
    {"heading": "一、找我", "body": "微信：{wechat} 直接说。"},
    {"heading": "二、安全", "body": "这里写一句 <script>alert(1)</script> 试试。"},
])
check("保存带占位符的条款", s == 200)
s, raw = terms_page()
if wechat:
    check("★ {wechat} 被换成了真实微信号（换了号不用回来改条款）",
          ("微信：%s 直接说。" % wechat) in raw, "微信号=%s" % wechat)
else:
    check("★ 没填微信号时换成一句永远对的话",
          "页面顶部的「加微信」" in raw, str(raw[raw.find("微信："):][:80]))
check("★ 正文里的 <script> 被转义了（文案是纯文本，不能变成 XSS 入口）",
      "<script>alert(1)</script>" not in raw and "&lt;script&gt;" in raw)
# ⚠️ 这一条是"截图里抓到的 bug"守住的：底部那行微信号是 app.py 拼好的一小段 HTML，
#    忘了把它放进"不转义"的例外里，客户在页面上看到的就是一串源码
#    （<p class="done-wechat-id">微信号：<b>xxx</b></p>）。
if wechat:
    check("★ 底部那行微信号是正常渲染的（不是一串 HTML 源码）",
          ("微信号：<b>%s</b>" % wechat) in raw and ("&lt;b&gt;%s&lt;/b&gt;" % wechat) not in raw,
          str(raw[raw.find("terms-foot"):][:160]))

# ============================================================================
#  7. 恢复默认
# ============================================================================
print("\n=== 7. 恢复默认条款 ===")
s, b = jcall("/api/admin/terms", "POST", {"action": "reset"})
check("恢复默认", s == 200 and b.get("ok"), "status=%s" % s)
check("★ 标题回到「服务条款」", b["terms"].get("title") == "服务条款", str(b["terms"].get("title")))
check("★ 小节回到出厂的 7 节", len(b["terms"]["sections"]) == 7,
      "共 %d 节" % len(b["terms"]["sections"]))
s, raw = terms_page()
check("前台也回到默认了", "我的服务约定" not in raw and "占位符测试" not in raw)
s, b = jcall("/api/admin/terms", "POST", {"action": "没了这个动作"})
check("不认识的动作 → 400", s == 400, "status=%s" % s)

# ============================================================================
#  8. 未登录
# ============================================================================
print("\n=== 8. 未登录 → 401 ===")
s, b = jcall("/api/admin/terms", "POST",
             {"action": "save", "title": "偷偷改", "sections": []}, use_cookie=False)
check("未登录保存条款 → 401", s == 401, "status=%s" % s)
s, b = jcall("/api/admin/terms", "POST", {"action": "reset"}, use_cookie=False)
check("未登录恢复默认 → 401", s == 401, "status=%s" % s)
check("★ 被拒之后条款一个字都没变", admin_terms().get("title") == "服务条款",
      str(admin_terms().get("title")))

# ============================================================================
#  9. 备份 / 恢复 / 老备份
# ============================================================================
print("\n=== 9. 备份带上条款（老备份不动它） ===")
save_terms("备份时的标题", [{"heading": "一、备份", "body": "备份时的正文。"}])
s, raw = call("/api/admin/export")
backup = json.loads(raw)
check("★ 备份里有 terms", isinstance(backup.get("terms"), dict)
      and backup["terms"].get("title") == "备份时的标题",
      str((backup.get("terms") or {}).get("title")))
check("备份里带最后更新日期（恢复后不会变成空白）",
      bool(re.match(r"^\d{4}-\d{2}-\d{2}$", (backup.get("terms") or {}).get("updated") or "")),
      str((backup.get("terms") or {}).get("updated")))

save_terms("备份之后又改的", [{"heading": "一、后来", "body": "后来改的。"}])
s, b = jcall("/api/admin/import", "POST", {"backup": backup})
check("导入备份成功", s == 200 and b.get("ok"), "status=%s %s" % (s, str(b)[:100]))
check("★ 恢复后条款回到备份时的样子",
      b.get("terms", {}).get("title") == "备份时的标题",
      str(b.get("terms", {}).get("title")))
check("恢复摘要里有条款节数", "terms" in (b.get("summary") or {}), str(b.get("summary"))[:170])
s, raw = terms_page()
check("前台也是备份时那份", "备份时的正文" in raw)

old_backup = json.loads(json.dumps(backup))
old_backup.pop("terms", None)
old_backup.pop("texts", None)
save_terms("老备份导入前改的", [{"heading": "一、我现在的", "body": "我现在的正文。"}])
s, b = jcall("/api/admin/import", "POST", {"backup": old_backup})
check("★ 老备份（没有 terms 字段）也能导入", s == 200 and b.get("ok"), "status=%s" % s)
check("★ 老备份**不会清空**他改过的条款（一个字都不动）",
      b.get("terms", {}).get("title") == "老备份导入前改的",
      str(b.get("terms", {}).get("title")))

# ============================================================================
#  10. 收尾：恢复默认
# ============================================================================
print("\n=== 10. 收尾 ===")
jcall("/api/admin/terms", "POST", {"action": "reset"})
check("条款已恢复默认（后面的阶段不受影响）",
      admin_terms().get("title") == "服务条款" and len(admin_terms().get("sections") or []) == 7,
      str(admin_terms().get("title")))
s, raw = terms_page()
check("前台也是默认那份", "一、我能提供什么" in raw)

print("\n" + "=" * 70)
print(" 通过 %d 项，失败 %d 项" % (ok, fail))
print("=" * 70)
sys.exit(1 if fail else 0)
