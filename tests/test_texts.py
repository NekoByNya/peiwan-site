# -*- coding: utf-8 -*-
"""
★ 第四刀之一：**文案可改**（客户看到的每一句话都能在后台改）+ **图片显示位置**（九宫格）。

为什么这两件事放在同一个文件里测：它们是一类东西 ——
"客户看到的东西，主人自己就能调"，而且都走同一条路（store / profile + 前台读值）。

  【文案表 store key='texts'】
   1. 目录：`/api/admin` 的 textGroups 6 组、每条都有 key / 说明 / 默认值 / 现在的值；
      默认值必须**就是以前写死的那句话**（没改过的人完全看不出差别）
   2. 保存：改一句 → `/api/public` 的 site.texts 立刻变（前台不用刷新缓存）
   3. **留空 = 用默认**：清空某一格 → 那一句回到默认；
      而且"填得和默认一模一样"也**不写库**（否则主人点开看一眼再保存，
      以后我们改好默认文案他就永远收不到了）
   4. 白名单 + 长度：不认识的 key 丢掉；超长的截断；前后空格去掉
   5. 「恢复默认」：按组（keys）和整页（不传 keys）两种
   6. 未登录 → 401
   7. 备份：导出只带**改过的**那几句；改了之后再恢复 → 变回备份时的样子；
      **老备份（没有 texts 字段）导入时一句都不动**（绝不清空他改出来的口气）
   8. 首页 HTML 里带着文案（服务端塞进 __PAGE__ 的 JSON，前端 tx() 再取）

  【图片显示位置 profile.avatarPosition / backgroundPosition / heroImagePosition
     + 分区资料里的 backgroundPosition / heroImagePosition】
   9. 默认全是 center（= 这一版之前的行为，观感不变）
  10. 存的必须是 9 个合法值之一：乱写的、CSS 里那种 "center center"、简写 "top"
      都会在**读的时候**被洗一遍（所以脏数据最坏也只是回到 center）
  11. 首页那三张在全局资料里、分区那两张在分区资料里，各自存、互不干扰
  12. 跟着备份走（profile 和 zones 本来就在备份里，这里验一下恢复后还在）
  13. 未登录改资料 → 401

风格和 test_gallery_link.py 一样：纯标准库、打印 [OK]/[FAIL]、最后给计数，失败退出码非 0。
⚠️ 会写数据，只能对着测试实例跑。

用法：
    python tests\\test_texts.py
    set TEST_BASE=http://127.0.0.1:8199 && python tests\\test_texts.py
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

# 这个脚本会改数据，别对着线上站点跑（和 test_api.py / test_gallery_link.py 一样的保护）
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


def admin_state():
    return jcall("/api/admin")[1]


def save_texts(payload):
    """保存文案。

    ⚠️ 后台的「保存文案」是**整页一起发**的（80 多格全发过去），后端按"这份就是全部"处理 ——
       所以"清空某一格 = 那一句回默认"才能成立。测试里也照这个规矩来：
       先合并进 OVERRIDES 再整份发，不然前面改过的话会被这一发冲掉（我第一版就踩了）。
    """
    OVERRIDES.clear()
    OVERRIDES.update(payload)
    return jcall("/api/admin/texts", "POST", {"action": "save", "texts": dict(OVERRIDES)})


def reset_texts(keys=None):
    body = {"action": "reset"}
    if keys is not None:
        body["keys"] = keys
    s, b = jcall("/api/admin/texts", "POST", body)
    if s == 200:
        keep = set(keys or [])
        if keys is None:
            OVERRIDES.clear()
        else:
            for k in list(OVERRIDES):
                if k in keep:
                    OVERRIDES.pop(k, None)
    return s, b


def public_texts():
    s, pub = jcall("/api/public")
    return (pub.get("site") or {}).get("texts") or {}


def all_fields(adm=None):
    """把目录拍平：{key: 那一条}（后台渲染和测试都用它）"""
    adm = adm if adm is not None else admin_state()
    out = {}
    for g in adm.get("textGroups") or []:
        for f in g["fields"]:
            out[f["key"]] = f
    return out


# 把这一轮测试动过的文案恢复成默认（跑第二遍、以及后面的阶段都不受影响）
OVERRIDES = {}


# ============================================================================
#  0. 登录
# ============================================================================
print("=" * 70)
print(" 文案可改 + 图片显示位置测试（%s）" % BASE)
print("=" * 70)

s, b = jcall("/api/auth", "POST", {"password": PASSWORD})
check("用测试密码登录成功", s == 200, "status=%s" % s)

if not IS_LOCAL and os.environ.get("ALLOW_REMOTE_TESTS") != "1":   # pragma: no cover
    sys.exit(2)

# ============================================================================
#  1. 目录与默认值
# ============================================================================
print("\n=== 1. 文案目录与默认值（没改过的人看不出差别） ===")
adm = admin_state()
groups = adm.get("textGroups") or []
fields = all_fields(adm)
check("★ 后台拿到了文案目录（分组）", len(groups) >= 4,
      "共 %d 组：%s" % (len(groups), [g["name"] for g in groups]))
# ⚠️ 这个数字**会跟着加功能变**（第五刀加了一组「⑥ 后台提示」：密码打码的提示；
#    二维码那几句在 2026-10-15 那个功能被砍掉时一起删了，但**组数没变**，还是 6 组），
#    所以同时守住"不少于 5 组"和"名字对得上"。
#    改这一行的时候请顺手把上面"共 %d 句"那个数（现在 86 句）
#    和 docs\交接说明.md / README.md 里"八十多句"的说法一起对一遍。
check("★ 目录是 6 组（主页 / 分区页价目 / 下单流程 / 页脚通用 / 空状态 / 后台提示）",
      len(groups) == 6, str([g["name"] for g in groups]))
check("每一组都有一句人话说明", all(g.get("hint") for g in groups),
      str([g.get("hint") for g in groups])[:120])
check("★ 字段数量够覆盖（>= 80 句）", len(fields) >= 80, "共 %d 句" % len(fields))
check("每条都有 key / 说明 / 默认值 / 现在的值",
      all(f.get("key") and f.get("label") and f.get("default") is not None
          and f.get("value") is not None for f in fields.values()))

# 用户点名要能改的那些句子，一条都不能少（这是这一刀的存在理由）
MUST_HAVE = {
    "heroCta": "看看我能做什么",            # 首页
    "servicesHint": "点开看详情与价目",
    "whyTitle": "为什么找我。",
    "whyHint": "个人接单 · 无中间商",
    "wechatCardHint": "加我微信，直接聊需求",
    "footerQrCaption": "长按上面的二维码就能加我（部分浏览器支持）",
    "footerTip": "加我之后直接说：哪个游戏 / 哪一档，我来安排。",
    "footerDisclaimer": "本页为个人服务页面，与游戏官方无隶属关系；游戏素材版权归原权利人所有。",
    "heroIntro": "我是 {name}，下面这些都是我一个人在做。",     # ★ 主人特别点名的
    "priceTitle": "玩的方式你选，陪伴我来。",
    "priceHint": "明码标价 · 个人直约 · 按实际游玩时间计费",
    "servicePriceHint": "加微信直接下单",
    "priceSheetHint": "点标题展开 / 收起",
    "priceSheetImageLabel": "完整价目表（长图）",
    "scheduleTitle": "你的时间，我来配合。",
    "scheduleHint": "每格 1 小时 · 北京时间 · 点击空闲格多选，可跨日期",
    "sharedNotice": "这份时间表是全部服务共用的：不管约哪个游戏，占的都是同一批时段。",
    "legendRest": "休息 / 不接单",
    "selectionHint": "先点选想玩的时间，连续或分开都可以（一次最多 {max} 小时）。",
    "nextStep": "下一步：选择模式",
    "promiseTitleBooking": "下单前，聊清楚这几件事",
    "galleryEmpty": "这个分区的展示墙还在装修中，先来找我玩一局吧。",
    "modeHint": "选好模式、留下联系方式，点「立即锁定时间」这几个小时就归你了。",
    "contactTypeHint": "填哪个都行，方便联系。",
    "contactLabel": "你的联系方式（必填）",
    "contactHint": "请填真实能加上的号——填错了我加不到你，这单就联系不上了（也可以直接加我，见页面顶部）",
    "notePlaceholder": "例如：想玩大坝、晚上可能晚到 10 分钟",
    "lockWarn": "⚠️ 点「立即锁定时间」后，这几个小时会立刻被锁定，别人就点不动了；如需取消请微信联系我。",
    "doneTitle": "时间已锁定 ✅",
    "doneNote": "时间预约成功Ciallo～(∠・ω< )⌒★",
    "doneCloseHint": "要取消或改时间，微信上跟我说一声就行。",
    "footerHintBooking": "选好时间点一下就锁上了，我这边立刻收到消息。",
    "footerHintService": "还没想好要哪一档？加我问一句，说清需求和工期就行。",
    "footerZoneLabel": "当前页面：",
}
missing = [k for k, v in MUST_HAVE.items() if fields.get(k, {}).get("default") != v]
check("★ 用户点名要能改的那些句子，一句不少、默认值也一字不差",
      not missing, "对不上的：%s" % str(missing)[:200])
check("★ 默认值里保留了 {name} / {max} 这类占位符（前端会替换成昵称 / 上限）",
      "{name}" in fields["heroIntro"]["default"] and "{max}" in fields["selectionHint"]["default"])

check("没改过的时候，每一句的 value 都等于默认值",
      all(f["value"] == f["default"] for f in fields.values()),
      str([f["key"] for f in fields.values() if f["value"] != f["default"]])[:120])
check("没改过的时候 textOverrides 是空的",
      not (adm.get("textOverrides") or {}), str(adm.get("textOverrides"))[:80])

s, pub = jcall("/api/public")
site_texts = (pub.get("site") or {}).get("texts") or {}
check("★ 前台 /api/public 也拿得到整套文案（默认值已补齐）", len(site_texts) >= 80,
      "共 %d 句" % len(site_texts))
check("前台和后台看到的是同一套值",
      site_texts.get("heroIntro") == fields["heroIntro"]["value"])

# ============================================================================
#  2. 改一句 → 前台立刻变
# ============================================================================
print("\n=== 2. 改一句，前台立刻变 ===")
s, b = save_texts({"heroIntro": "我是 {name}，这些活都是我一个人干的。",
                   "galleryLinkBadge": "点我看看 ↗"})
check("保存文案成功", s == 200 and b.get("ok"), "status=%s %s" % (s, str(b)[:100]))
check("★ 保存完就回了一份最新文案表（前端直接重画）",
      b.get("texts", {}).get("heroIntro") == "我是 {name}，这些活都是我一个人干的。",
      str(b.get("texts", {}).get("heroIntro"))[:60])
check("★ 只存了改过的那两句", sorted((b.get("textOverrides") or {}).keys()) == ["galleryLinkBadge", "heroIntro"],
      str(b.get("textOverrides"))[:100])
check("saved 数字对得上", b.get("saved") == 2, "saved=%s" % b.get("saved"))

check("★ 前台 /api/public 立刻就是新的那句",
      public_texts().get("heroIntro") == "我是 {name}，这些活都是我一个人干的。",
      str(public_texts().get("heroIntro"))[:60])
s, raw = call("/")
check("★ 首页 HTML 里也带上了新文案（服务端塞进 __PAGE__，前端 tx() 取）",
      "这些活都是我一个人干的" in raw)
check("★ 服务端发出去的是**带占位符的原文**（{name} 由前端换成昵称，所以换昵称不用改文案）",
      "{name}" in raw)

# ============================================================================
#  3. 留空 = 用默认；填一样的字也不存
# ============================================================================
print("\n=== 3. 留空 = 用默认那句话 ===")
s, b = save_texts({"heroIntro": "", "whyTitle": "为什么找我。"})
check("把 heroIntro 留空 → 回到默认那句",
      b.get("texts", {}).get("heroIntro") == "我是 {name}，下面这些都是我一个人在做。",
      str(b.get("texts", {}).get("heroIntro"))[:60])
check("★ 留空 / 和默认一字不差，都不写进库（只改过的才存）",
      "heroIntro" not in (b.get("textOverrides") or {}) and "whyTitle" not in (b.get("textOverrides") or {}),
      str(b.get("textOverrides"))[:100])
check("★ 保存是「整页覆盖」语义：这一发没发到的 key 也当成留空（回默认）",
      "galleryLinkBadge" not in (b.get("textOverrides") or {}),
      str(b.get("textOverrides"))[:100])

# ============================================================================
#  4. 白名单 / 长度 / 空格
# ============================================================================
print("\n=== 4. 白名单、长度、空格 ===")
s, b = save_texts({"不存在的key": "随便写", "footerTip": "  加我之后直接说需求就行  ",
                   "heroCta": "看" * 200})
check("不认识的 key 被丢掉（不会越存越多）",
      "不存在的key" not in (b.get("textOverrides") or {}),
      str(list((b.get("textOverrides") or {}).keys()))[:100])
check("前后的空格会被去掉",
      (b.get("textOverrides") or {}).get("footerTip") == "加我之后直接说需求就行",
      str((b.get("textOverrides") or {}).get("footerTip")))
check("★ 超长的会被截断（heroCta 上限 20 字）",
      len((b.get("textOverrides") or {}).get("heroCta", "")) == 20,
      "长度=%d" % len((b.get("textOverrides") or {}).get("heroCta", "")))

s, b = jcall("/api/admin/texts", "POST", {"action": "save", "texts": "不是字典"})
check("texts 不是对象 → 400 中文报错", s == 400 and "格式" in str(b.get("error", "")),
      "status=%s %s" % (s, str(b.get("error"))[:80]))
s, b = jcall("/api/admin/texts", "POST", {"action": "没了这个动作"})
check("不认识的动作 → 400", s == 400, "status=%s %s" % (s, str(b.get("error"))[:60]))

# ============================================================================
#  5. 恢复默认（按组 / 整页）
# ============================================================================
print("\n=== 5. 恢复默认 ===")
save_texts({"heroIntro": "临时改一句 A", "whyTitle": "临时改一句 B", "footerTip": "临时改一句 C"})
check("（准备）现在有 3 句是改过的",
      len(admin_state().get("textOverrides") or {}) >= 3,
      str(list((admin_state().get("textOverrides") or {}).keys()))[:120])

s, b = reset_texts(["heroIntro"])
check("★ 只恢复「① 主页」这一组里的 heroIntro", s == 200 and b.get("ok"))
check("那一句回到默认了",
      b.get("texts", {}).get("heroIntro") == "我是 {name}，下面这些都是我一个人在做。")
check("同一次里别的 key 不受影响 —— 后台按组传 keys，只删这一组",
      (b.get("textOverrides") or {}).get("whyTitle") == "临时改一句 B",
      str(b.get("textOverrides"))[:100])

s, b = reset_texts()
check("★ 整页恢复默认（不传 keys）", s == 200 and b.get("ok"))
check("★ 一句都不剩了", not (b.get("textOverrides") or {}),
      str(b.get("textOverrides"))[:80])
check("恢复默认之后前台也是默认那句",
      public_texts().get("whyTitle") == "为什么找我。",
      str(public_texts().get("whyTitle"))[:40])

# ============================================================================
#  6. 未登录
# ============================================================================
print("\n=== 6. 未登录 → 401 ===")
s, b = jcall("/api/admin/texts", "POST", {"action": "save", "texts": {"heroIntro": "偷偷改"}},
             use_cookie=False)
check("未登录保存文案 → 401", s == 401, "status=%s" % s)
s, b = jcall("/api/admin/texts", "POST", {"action": "reset"}, use_cookie=False)
check("未登录恢复默认 → 401", s == 401, "status=%s" % s)
check("★ 被拒之后文案一个字都没变",
      public_texts().get("heroIntro") == "我是 {name}，下面这些都是我一个人在做。",
      str(public_texts().get("heroIntro"))[:50])

# ============================================================================
#  7. 备份与恢复
# ============================================================================
print("\n=== 7. 备份带上文案（老备份不清空） ===")
save_texts({"heroIntro": "备份时的那句话", "slotFree": "空着呢"})
s, raw = call("/api/admin/export")
backup = json.loads(raw)
check("★ 备份里带着 texts", isinstance(backup.get("texts"), dict), str(backup.get("texts"))[:120])
check("★ 备份里只有改过的那几句（没改的不占地方）",
      set(backup["texts"].keys()) == {"heroIntro", "slotFree"},
      str(sorted(backup["texts"].keys())))
check("备份里也带着 terms（服务条款，另一条线的东西也在同一份备份里）",
      isinstance(backup.get("terms"), dict)
      and len(backup["terms"].get("sections") or []) >= 1,
      str((backup.get("terms") or {}).get("title"))[:40])

save_texts({"heroIntro": "备份之后又改的"})
check("（准备）改成了别的", public_texts().get("heroIntro") == "备份之后又改的")

s, b = jcall("/api/admin/import", "POST", {"backup": backup})
check("导入备份成功", s == 200 and b.get("ok"), "status=%s %s" % (s, str(b)[:100]))
check("★ 恢复后文案回到备份时的样子",
      b.get("texts", {}).get("heroIntro") == "备份时的那句话",
      str(b.get("texts", {}).get("heroIntro"))[:50])
check("恢复摘要里有文案条数", "texts" in (b.get("summary") or {}),
      str(b.get("summary"))[:160])

# 老备份：把 texts / terms 删掉，模拟"第四刀之前导出的备份"
old_backup = json.loads(json.dumps(backup))
old_backup.pop("texts", None)
old_backup.pop("terms", None)
save_texts({"heroIntro": "老备份导入前改的"})
s, b = jcall("/api/admin/import", "POST", {"backup": old_backup})
check("★ 老备份（没有 texts 字段）也能导入", s == 200 and b.get("ok"), "status=%s" % s)
check("★ 老备份**不会清空**他改过的文案（一句都不动）",
      b.get("texts", {}).get("heroIntro") == "老备份导入前改的",
      str(b.get("texts", {}).get("heroIntro"))[:50])

# ============================================================================
#  8. 图片显示位置（九宫格）
# ============================================================================
print("\n=== 8. ★ 图片显示位置（首页三张 + 分区两张） ===")
adm = admin_state()
choices = [c["value"] for c in (adm.get("positionChoices") or [])]
# ★ 第六刀之后后台不再画九宫格（改成在图上直接拖），但这份"9 个选项"还留在接口里：
#   拖动要用它把老关键词翻译成"该从哪儿开始拖"，接口也不该突然少一个字段。
check("★ 接口仍然带着 9 个位置关键词（拖框用它翻译老值）", len(choices) == 9, str(choices))
check("★ 默认是 center（= 这一版之前的样子，观感不变）",
      (adm["profile"].get("avatarPosition") == "center"
       and adm["profile"].get("backgroundPosition") == "center"
       and adm["profile"].get("heroImagePosition") == "center"),
      str({k: adm["profile"].get(k) for k in
           ("avatarPosition", "backgroundPosition", "heroImagePosition")}))
check("前台默认拿到的也是 center",
      all(v == "center" for v in ((jcall("/api/public")[1].get("site") or {})
                                  .get("imagePositions") or {}).values()))

profile = dict(adm["profile"])
profile.update({"avatarPosition": "center top", "backgroundPosition": "left bottom",
                "heroImagePosition": "right"})
s, b = jcall("/api/admin/profile", "PUT",
             {"profile": profile, "revision": adm["profileRevision"]})
check("保存首页三张的位置", s == 200 and b.get("ok"), "status=%s %s" % (s, str(b)[:100]))
positions = ((jcall("/api/public")[1].get("site") or {}).get("imagePositions") or {})
check("★ 前台按 key 拿到三个位置（前台就是拿它写 background-position / object-position）",
      positions.get("avatar") == "center top" and positions.get("background") == "left bottom",
      str(positions))
check("简写 right 会被规范成 right center",
      positions.get("heroImage") == "right center", str(positions.get("heroImage")))

# 乱写的值：读的时候必须被洗回 center（脏数据最坏也只是回到原来的观感）
adm = admin_state()
profile = dict(adm["profile"])
profile["avatarPosition"] = "'; background:url(javascript:alert(1))"
profile["heroImagePosition"] = "中间那块"
s, b = jcall("/api/admin/profile", "PUT", {"profile": profile, "revision": adm["profileRevision"]})
check("乱写的位置也能存进去（不报错）", s == 200, "status=%s" % s)
positions = ((jcall("/api/public")[1].get("site") or {}).get("imagePositions") or {})
check("★ 但读出来一定是 9 个合法值之一（洗回 center），绝不会带着奇怪的值进 CSS",
      positions.get("avatar") == "center" and positions.get("heroImage") == "center",
      str(positions))
check("合法的那一个不受影响", positions.get("background") == "left bottom", str(positions))

# 分区那两张：存在分区资料里，跟着「保存这个分区」走
zones = adm.get("zones") or []
if zones:
    zid = zones[0]["id"]
    zp = dict(zones[0]["profile"])
    zp["backgroundPosition"] = "left top"
    zp["heroImagePosition"] = "center bottom"
    s, b = jcall("/api/admin/zones", "POST", {"action": "profile", "id": zid, "profile": zp})
    check("保存分区图片的位置", s == 200 and b.get("ok"), "status=%s %s" % (s, str(b)[:80]))
    saved = b.get("zone", {}).get("profile", {})
    check("★ 分区背景图和首屏图各存一份",
          saved.get("backgroundPosition") == "left top"
          and saved.get("heroImagePosition") == "center bottom",
          "%s / %s" % (saved.get("backgroundPosition"), saved.get("heroImagePosition")))
    s, pub = jcall("/api/public?zone=" + zid)
    zp2 = (pub.get("zone") or {}).get("profile") or {}
    check("★ 分区页前台也拿得到（zone.js 用它写 style）",
          zp2.get("backgroundPosition") == "left top"
          and zp2.get("heroImagePosition") == "center bottom",
          "%s / %s" % (zp2.get("backgroundPosition"), zp2.get("heroImagePosition")))

    # 乱写的分区位置也要被洗
    zp["backgroundPosition"] = "top left top"
    jcall("/api/admin/zones", "POST", {"action": "profile", "id": zid, "profile": zp})
    s, pub = jcall("/api/public?zone=" + zid)
    zp2 = (pub.get("zone") or {}).get("profile") or {}
    check("分区里乱写的位置也会被洗回 center", zp2.get("backgroundPosition") == "center",
          str(zp2.get("backgroundPosition")))

    # 备份恢复之后位置还在（profile 整个都在备份里）
    zp["backgroundPosition"] = "left top"        # 先放一个"干净的好值"
    jcall("/api/admin/zones", "POST", {"action": "profile", "id": zid, "profile": zp})
    s, raw = call("/api/admin/export")
    b2 = json.loads(raw)
    zp = dict(next(z for z in b2["zones"] if z["id"] == zid)["profile"])
    zp["backgroundPosition"] = "right top"       # 导出之后再改掉它
    jcall("/api/admin/zones", "POST", {"action": "profile", "id": zid, "profile": zp})
    s, b3 = jcall("/api/admin/import", "POST", {"backup": b2})
    zone_after = next((z for z in (b3.get("zones") or []) if z["id"] == zid), {})
    check("★ 备份恢复后，分区图片位置跟着回来（profile 在备份里）",
          (zone_after.get("profile") or {}).get("backgroundPosition") == "left top",
          str((zone_after.get("profile") or {}).get("backgroundPosition")))

s, b = jcall("/api/admin/profile", "PUT", {"profile": {}, "revision": 0}, use_cookie=False)
check("未登录改资料（含图片位置）→ 401", s == 401, "status=%s" % s)

# ============================================================================
#  8b. ★ 百分比位置（第六刀：九宫格删了，改成"直接在图上拖"）
# ============================================================================
#  两条硬要求：
#    ① 新值（"42% 18%" 这种 CSS object-position）要能存、能读、能下发到前台；
#    ② 老值（9 个关键词）**一个字节都不能变** —— 主人线上库里现在存的就是它，
#       升级之后前台渲染必须和以前一模一样，否则他已有的图就错位了。
print("\n=== 8b. ★ 百分比位置 + 老关键词兼容 ===")
adm = admin_state()
profile = dict(adm["profile"])
profile.update({"avatarPosition": "42% 18%", "backgroundPosition": "0% 100%",
                "heroImagePosition": "100% 0%"})
s, b = jcall("/api/admin/profile", "PUT",
             {"profile": profile, "revision": adm["profileRevision"]})
check("百分比位置能存进去", s == 200, "status=%s %s" % (s, str(b)[:80]))
p2 = admin_state().get("profile") or {}
check("★ 读出来还是那个百分比（不被洗掉）",
      p2.get("avatarPosition") == "42% 18%" and p2.get("backgroundPosition") == "0% 100%",
      "%s / %s" % (p2.get("avatarPosition"), p2.get("backgroundPosition")))
pos = ((jcall("/api/public")[1].get("site") or {}).get("imagePositions") or {})
check("★ 前台拿到的也是百分比（直接塞进 object-position）",
      pos.get("avatar") == "42% 18%" and pos.get("heroImage") == "100% 0%",
      str(pos))

# ---- 老关键词：存进去 / 读出来必须**原样**（这是"老数据不能错位"的防线）----
adm = admin_state()
OLD_VALUES = ["left top", "center top", "right top", "left center", "center",
              "right center", "left bottom", "center bottom", "right bottom"]
profile = dict(adm["profile"])
profile.update({"avatarPosition": "left top", "backgroundPosition": "center bottom",
                "heroImagePosition": "right center"})
jcall("/api/admin/profile", "PUT", {"profile": profile, "revision": adm["profileRevision"]})
p3 = admin_state().get("profile") or {}
check("★ 老关键词原样存着（没被转成百分比 —— 前台渲染才会和以前一模一样）",
      p3.get("avatarPosition") == "left top"
      and p3.get("backgroundPosition") == "center bottom"
      and p3.get("heroImagePosition") == "right center",
      "%s / %s / %s" % (p3.get("avatarPosition"), p3.get("backgroundPosition"),
                        p3.get("heroImagePosition")))
pos = ((jcall("/api/public")[1].get("site") or {}).get("imagePositions") or {})
check("★ 前台拿到的老值还是那个字符串（一个字节没变）",
      pos.get("avatar") == "left top" and pos.get("background") == "center bottom",
      str(pos))

# 九个关键词循环一遍，**进什么出什么**（一个都不许被改写）
bad = []
for v in OLD_VALUES:
    adm = admin_state()
    pr = dict(adm["profile"])
    pr["avatarPosition"] = v
    jcall("/api/admin/profile", "PUT", {"profile": pr, "revision": adm["profileRevision"]})
    got = (admin_state().get("profile") or {}).get("avatarPosition")
    if got != v:
        bad.append((v, got))
check("★ 9 个老关键词全部原样保留（round-trip 一个都没变）", not bad, str(bad))

# 脏值 / 注入：还是必须退回 center（不能带着奇怪的东西进 CSS）
adm = admin_state()
pr = dict(adm["profile"])
pr["avatarPosition"] = "'; background:url(javascript:alert(1))"
pr["heroImagePosition"] = "42%; color:red"
jcall("/api/admin/profile", "PUT", {"profile": pr, "revision": adm["profileRevision"]})
pos = ((jcall("/api/public")[1].get("site") or {}).get("imagePositions") or {})
check("★ 带分号/括号的脏位置值一律退回 center（挡注入）",
      pos.get("avatar") == "center" and pos.get("heroImage") == "center", str(pos))

# 越界的百分比会被夹到 0..100（不露白边的取值范围）
adm = admin_state()
pr = dict(adm["profile"])
pr["avatarPosition"] = "999% -50%"
jcall("/api/admin/profile", "PUT", {"profile": pr, "revision": adm["profileRevision"]})
got = (admin_state().get("profile") or {}).get("avatarPosition")
check("★ 越界百分比被夹回 0..100（拖不出白边）", got == "100% 0%", repr(got))

# 备份 / 恢复：百分比也跟着回来
adm = admin_state()
pr = dict(adm["profile"])
pr["avatarPosition"] = "37% 91%"
jcall("/api/admin/profile", "PUT", {"profile": pr, "revision": adm["profileRevision"]})
s, raw = call("/api/admin/export")
bk = json.loads(raw)
adm = admin_state()
pr = dict(adm["profile"])
pr["avatarPosition"] = "center"
jcall("/api/admin/profile", "PUT", {"profile": pr, "revision": adm["profileRevision"]})
s, b4 = jcall("/api/admin/import", "POST", {"backup": bk})
check("★ 备份恢复后百分比位置跟着回来",
      (b4.get("profile") or {}).get("avatarPosition") == "37% 91%",
      repr((b4.get("profile") or {}).get("avatarPosition")))

# ============================================================================
#  9. 收尾：恢复默认，别影响后面的阶段
# ============================================================================
print("\n=== 9. 收尾 ===")
reset_texts()
check("文案已全部恢复默认", not (admin_state().get("textOverrides") or {}),
      str(admin_state().get("textOverrides"))[:80])

adm = admin_state()
profile = dict(adm["profile"])
for key in ("avatarPosition", "backgroundPosition", "heroImagePosition"):
    profile[key] = "center"
jcall("/api/admin/profile", "PUT", {"profile": profile, "revision": adm["profileRevision"]})
check("首页三张的位置已恢复居中",
      all(v == "center" for v in ((jcall("/api/public")[1].get("site") or {})
                                  .get("imagePositions") or {}).values()))
if zones:
    zp = dict(zones[0]["profile"])
    zp["backgroundPosition"] = "center"
    zp["heroImagePosition"] = "center"
    jcall("/api/admin/zones", "POST", {"action": "profile", "id": zones[0]["id"], "profile": zp})
    zp2 = ((jcall("/api/public?zone=" + zones[0]["id"])[1].get("zone") or {}).get("profile") or {})
    check("分区两张的位置已恢复居中", zp2.get("backgroundPosition") == "center",
          str(zp2.get("backgroundPosition")))

print("\n" + "=" * 70)
print(" 通过 %d 项，失败 %d 项" % (ok, fail))
print("=" * 70)
sys.exit(1 if fail else 0)
