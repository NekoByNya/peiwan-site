# -*- coding: utf-8 -*-
"""
★ 第五刀之二：**常见问题 FAQ** + **服务流程 steps**（客户"正犹豫"和"不知道下一步"时看的两块）。

这两块为什么放在同一个文件里：它们是同一类东西 ——
一份"能整条增删改排序"的结构化内容（不是一句话，所以不能塞进 TEXT_GROUPS），
写法照「服务条款」那一套（store 一个 key + 后台整块编辑 + 前台渲染）。

  【常见问题 store key='faq'】
    1. 出厂默认 8 条（就是主人点名要的那 8 个问题），标题 / 副标题 / 每条的问答都在
    2. 前台能拿到：`/api/public` 的 site.faq + **分区页** HTML 里的 __PAGE__
   ⚠️ 主页【故意不放 FAQ】（2026-10-15 主人拍的）—— 见下面第 3 节那两条断言
    3. 后台能改：标题、副标题、每条的问答（改完前台立刻变）
    4. 增 / 删 / **排序**（↑↓ 就是数组顺序）
    5. 空条目（问题和答案都空）会被丢掉，不算错，但要回报丢了几条
    6. 标题不能空 → 400；条数、长度都有上限
    7. 「恢复默认」回到出厂那 8 条
    8. 未登录 → 401
    9. 备份：导出带 faq；改了之后恢复 → 变回备份时的样子；
       **老备份（没有 faq 字段）导入时一条都不动**

  【服务流程 store key='steps'】
   10. 出厂默认 4 步；★ **步数可改（2~6 步）**
   11. 少于 2 步 → **退回默认 4 步**（不报 400：他可能正编辑到一半），并回一句 tip
   12. 多于 6 步 → 只留前 6 步，并回一句 tip
   13. 前台渲染：**分区页**有（主页故意没有），编号用 .promises 那套圆圈
   14. 备份与老备份兼容（和 FAQ 一样）

  【前台位置（主人确认过的）】
   15. 分区页：FAQ 在价目表那一块的**下面**，流程在**上面**
   16. 主页：服务卡片下面只有流程；FAQ 故意不放
   17. ★ 折叠块不是独立页面：每条一个 <details>，**默认全部收起**
   18. 文案是纯文本：问答里写 <script> 会被转义（和条款同一条安全底线）

⚠️ 会写数据，只能对着测试实例跑。
用法：
    python tests\\test_faq_steps.py
    set TEST_BASE=http://127.0.0.1:8199 && python tests\\test_faq_steps.py
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

# 主人点名要的那 8 个问题（前 8 个词用来核对"默认内容真的按他说的写了"）
WANT_QUESTIONS = [
    "账号安全吗？你会动我的东西吗？",
    "需要我提供什么？（哪种登录方式、要不要验证码）",
    "没打完 / 打不完怎么算？",
    "能退款吗？什么时候付钱？",
    "多久能打完？要排队吗？",
    "能指定时间吗？临时有事怎么办？",
    "你会用外挂或者脚本吗？",
    "服务结束后我需要做什么？",
]


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


def admin_faq():
    return jcall("/api/admin")[1].get("faq") or {}


def admin_steps():
    return jcall("/api/admin")[1].get("steps") or {}


def save_faq(title, items, subtitle=None):
    body = {"action": "save", "title": title, "items": items}
    if subtitle is not None:
        body["subtitle"] = subtitle
    return jcall("/api/admin/faq", "POST", body)


def save_steps(title, items):
    return jcall("/api/admin/steps", "POST", {"action": "save", "title": title, "items": items})


def public_site():
    return (jcall("/api/public")[1].get("site") or {})


def first_zone_id():
    zones = jcall("/api/admin")[1].get("zones") or []
    return zones[0]["id"] if zones else "delta"


# 收集"JS 里那个渲染函数到底写了什么"（结构问题只有读源码才看得出来）
SITE_JS = open(os.path.join(ROOT, "static", "site.js"), encoding="utf-8").read()
ZONE_JS = open(os.path.join(ROOT, "static", "zone.js"), encoding="utf-8").read()
ADMIN_JS = open(os.path.join(ROOT, "static", "admin.js"), encoding="utf-8").read()
STYLE_CSS = open(os.path.join(ROOT, "static", "style.css"), encoding="utf-8").read()
INDEX_HTML = open(os.path.join(ROOT, "templates", "index.html"), encoding="utf-8").read()
ZONE_HTML = open(os.path.join(ROOT, "templates", "zone.html"), encoding="utf-8").read()
ADMIN_HTML = open(os.path.join(ROOT, "templates", "admin.html"), encoding="utf-8").read()

# ⚠️ 结构断言必须先把 HTML 注释去掉再找 —— 注释里会提到 id 和 data-nav 的名字
#    （例如 zone.html 顶部那段说明"谁带了 data-nav 就进导航"），
#    带着注释找下标就会把注释当成真实结构（第一版就是这样，断言当场报了）。
ZONE_HTML_NOCOMMENT = re.sub(r"<!--.*?-->", "", ZONE_HTML, flags=re.S)


def _pos(snippet):
    """片段在 zone.html（去掉注释后）里的下标；找不到就直接炸 —— 说明结构被改动了"""
    found = ZONE_HTML_NOCOMMENT.find(snippet)
    assert found >= 0, "zone.html 里找不到 %s" % snippet
    return found

# ============================================================================
#  0. 登录
# ============================================================================
print("=" * 70)
print(" 常见问题 + 服务流程测试（%s）" % BASE)
print("=" * 70)

s, b = jcall("/api/auth", "POST", {"password": PASSWORD})
check("用测试密码登录成功", s == 200, "status=%s" % s)

# ============================================================================
#  1. 出厂默认：FAQ 8 条 + 流程 4 步
# ============================================================================
print("\n=== 1. 出厂默认（FAQ 8 条 + 流程 4 步） ===")
faq = admin_faq()
check("★ 后台拿到了 FAQ（标题 + 副标题 + 条目）",
      bool(faq.get("title")) and isinstance(faq.get("items"), list), str(faq.get("title")))
check("★ 出厂是 8 条", len(faq.get("items") or []) == 8,
      "共 %d 条" % len(faq.get("items") or []))
questions = [x.get("q") for x in faq.get("items") or []]
check("★ 就是主人点名要的那 8 个问题（顺序也一样）", questions == WANT_QUESTIONS,
      str([q for q, w in zip(questions, WANT_QUESTIONS) if q != w])[:200])
check("★ 每条都有答案（不能是空壳）",
      all((x.get("a") or "").strip() for x in faq.get("items") or []),
      str([x.get("q") for x in faq.get("items") or [] if not (x.get("a") or "").strip()]))
check("标题是「常见问题」", faq.get("title") == "常见问题", str(faq.get("title")))
check("有副标题（标题右边那行小字）", bool(faq.get("subtitle")), str(faq.get("subtitle")))

# 内容质量：这几句是这一块存在的理由，得真的答到点上
body_all = " ".join((x.get("a") or "") for x in faq.get("items") or [])
check("★ 答案里讲清了账号怎么保管（客户最担心的那条）", "自己保管" in body_all)
check("★ 明确写了不用外挂 / 脚本（和条款里的口径一致）", "外挂" in body_all and "脚本" in body_all)
check("★ 讲清了退款 / 按做过的部分算（保护客户也就保护了主人）",
      "退" in body_all and "按做过的" in body_all)
check("★ 讲清了服务结束后要改密码", "改一次密码" in body_all)

steps = admin_steps()
check("★ 后台拿到了服务流程", bool(steps.get("title")) and isinstance(steps.get("items"), list),
      str(steps.get("title")))
check("★ 出厂是 4 步", len(steps.get("items") or []) == 4,
      "共 %d 步" % len(steps.get("items") or []))
step_titles = [x.get("title") for x in steps.get("items") or []]
check("★ 4 步就是主人说的那 4 步（挑时间 / 加微信 / 我确认 / 开始服务）",
      step_titles == ["挑时间 / 说需求", "加我微信", "我确认", "开始服务"], str(step_titles))
check("每一步都有说明", all((x.get("desc") or "").strip() for x in steps.get("items") or []),
      str(step_titles))
check("★ 第 2 步讲清了「页面上的微信号点一下就能复制」（主人点名要的）",
      "点一下就能复制" in (steps["items"][1].get("desc") or ""),
      str(steps["items"][1].get("desc"))[:60])

# ============================================================================
#  2. 前台拿得到（site.faq / site.steps）
# ============================================================================
print("\n=== 2. 前台拿得到（/api/public） ===")
site = public_site()
check("★ /api/public 的 site 里有 faq", isinstance(site.get("faq"), dict), str(type(site.get("faq"))))
check("★ /api/public 的 site 里有 steps", isinstance(site.get("steps"), dict))
check("前台拿到的 FAQ 和后台是同一份（8 条）",
      len((site.get("faq") or {}).get("items") or []) == 8)
check("前台拿到的流程也是 4 步",
      len((site.get("steps") or {}).get("items") or []) == 4)

s, home = call("/")
check("★ 首页 HTML 里带着这两块的数据（服务端塞进 __PAGE__）",
      '"faq"' in home and '"steps"' in home)
check("★ 首页 HTML 里没有残留的模板占位符（{{ }} 会被清掉）",
      "{{" not in home and "}}" not in home)
s, zone_html = call("/z/" + first_zone_id())
check("★ 分区页 HTML 里也带着", '"faq"' in zone_html and '"steps"' in zone_html)
check("分区页 HTML 里没有残留的 {{ 占位符（漏渲染的模板标记）",
      "{{" not in zone_html)

# ============================================================================
#  3. ★ 前台位置（2026-10-16 重排过：档位 → 流程 → 须知 → 预约 → 问答 → 展示）
# ============================================================================
print("\n=== 3. ★ 前台位置（分区页：流程在档位卡下面、须知在预约前、问答在预约后） ===")
# ⚠️ 这一段以前守的是"FAQ 和流程都挤在『价格区』那一个 section 里、FAQ 在流程后面"。
#    2026-10-16 主人把顺序重排了（下完单才看到规则是本末倒置 ✗），所以断言跟着改：
#      · 流程：还在价格区里（档位卡下面）；
#      · 常见问题：**搬出去成了独立的 <section id="faq">**，排在预约后面；
#      · 须知（promises）：搬到预约**前面**。
#    改动见 docs\交接说明.md 的坑 60。
price_sec = ZONE_HTML_NOCOMMENT[_pos('id="services"'):_pos('id="faq"')]
check("★ 分区页：stepsPanel 在价格区里（档位卡那一块，排在价格后面）",
      'id="stepsPanel"' in price_sec)
check("★ 分区页：价格区里【已经没有】faqPanel 了（它搬成了独立一块）",
      'id="faqPanel"' not in price_sec, "价格区里不该再有 FAQ")
check("★ 分区页：常见问题是独立的一块 <section id=faq>（不再寄生在价格区里）",
      '<section class="section" id="faq"' in ZONE_HTML_NOCOMMENT)
_order = {k: _pos(v) for k, v in {
    "档位": 'id="services"', "流程": 'id="stepsPanel"', "须知": 'id="promises"',
    "预约": 'id="schedule"', "问答": 'id="faq"', "展示": 'id="gallery"',
}.items()}
check("★ 顺序：档位 → 流程 → 须知 → 预约 → 问答 → 展示",
      _order["档位"] < _order["流程"] < _order["须知"] < _order["预约"]
      < _order["问答"] < _order["展示"], str(_order))
# ★ 两块内容只是**搬家**，渲染逻辑不许重写：老函数名和老结构都还在
check("★ 两块只是搬家：renderSteps / renderFaq 还在，条目渲染还是老写法",
      'function renderSteps()' in ZONE_JS and 'function renderFaq()' in ZONE_JS
      and 'class="promises steps-list"' in ZONE_HTML and 'id="faqList"' in ZONE_HTML)
check("★ 主页【故意既没有 FAQ 也没有流程】（2026-10-15 主人拍的）："
      "问答和流程都是针对具体服务的，客户在主页还没选服务，提前看一整套太多余",
      'id="faqPanel"' not in INDEX_HTML and 'id="stepsPanel"' not in INDEX_HTML
      and 'renderFaq' not in SITE_JS and 'renderSteps' not in SITE_JS,
      "主页里既不该有这两块元素，也不该有对应的渲染函数")
check("★ 两块都只在分区页里有（那儿才是对的时机：客户正看着那个服务的价目）",
      'id="faqPanel"' in ZONE_HTML and 'id="stepsPanel"' in ZONE_HTML
      and 'renderFaq' in ZONE_JS and 'renderSteps' in ZONE_JS)

# ============================================================================
#  3b. ★★ 左侧固定导航（2026-10-16 新加）
#     主人专门强调的一条："导航项不许手写列表" —— 页面顺序是 A,B,C,D，
#     导航就得是 A,B,C,D。所以它是按 DOM 顺序现场生成的，这里用源码断言守住。
# ============================================================================
print("\n=== 3b. ★★ 左侧导航（按块顺序自动生成，不许手写列表） ===")
# ① 六个块各自带了 data-nav（谁带谁进导航），首屏和页脚故意不带
_nav_blocks = re.findall(r'data-nav="([A-Za-z0-9_]+)"', ZONE_HTML_NOCOMMENT)
check("★ 六个块都打了 data-nav（值就是后台文案表里的 key）",
      _nav_blocks == ["navZonePrice", "navZoneSteps", "navZonePromises",
                      "navZoneSchedule", "navZoneFaq", "navZoneGallery"],
      str(_nav_blocks))
check("★ data-nav 的先后顺序 == 页面上块的先后顺序（同一个字符串里数出来的）",
      [_pos('data-nav="%s"' % k) for k in _nav_blocks]
      == sorted(_pos('data-nav="%s"' % k) for k in _nav_blocks))
check("★ 首屏和页脚【不进导航】（它们没有 data-nav）",
      'data-nav' not in ZONE_HTML_NOCOMMENT[_pos('id="zoneHero"'):_pos('id="services"')]
      and 'data-nav' not in ZONE_HTML_NOCOMMENT[_pos('class="footer"'):])
# ② 导航项是**收集**出来的，不是写死的列表
check("★★ 导航项由 JS 按 DOM 顺序收集（querySelectorAll('[data-nav]')），没有手写数组",
      "$$('[data-nav]')" in ZONE_JS and 'navBlocks' in ZONE_JS)
check("★★ 没有『写死的导航项列表』这种东西（HTML 里那个 #zoneNavList 是空盒子）",
      'id="zoneNavList"></div>' in ZONE_HTML,
      "导航项必须全部由 JS 生成")
check("★ 导航项的文字来自文案表（改后台导航词，导航跟着变）",
      "tx(el.dataset.nav" in ZONE_JS)
check("★ 服务制分区没有时间表那一块 → 导航里也不该出现「预约」"
      "（hidden 的块照样会被选到，所以 renderNav 里按 hidden 过滤了）",
      ".filter((el) => !el.hidden)" in ZONE_JS)
# ③ 位置与"不挤压正文"：断点 1440 + body 只让出左边距
# ★ 2026-10-16 断点从 1440 降到 1360：导航宽度改成自适应后（最小 104px），
#   1366 的小笔记本也放得下了 ✓（原来放不下只能整条隐藏 ✗）
check("★ 导航只在够宽时出现（@media (min-width: 1360px)），窄屏 display:none 一个字不占",
      "@media (min-width: 1360px)" in STYLE_CSS and ".zone-nav {" in STYLE_CSS
      and "display: none;" in STYLE_CSS[STYLE_CSS.find(".zone-nav {"):][:120])
check("★ 导航宽度自适应（clamp 104~148px）：屏幕窄了自动收窄，不硬塞也不整条消失",
      "width: clamp(104px, calc((100vw - 1080px) / 2 - 32px), 148px);" in STYLE_CSS)
check("★ 导航字号 15px（主人说 13.5px 太小，2026-10-16 调大）",
      "font-size: 15px; line-height: 1.4; color: var(--muted);" in STYLE_CSS)
# ★★ 这一条 2026-10-16 改过判据（原来的写法是错的 ✗）：
#    原来断言的是 "宽屏时给 body 加 padding-left:192px" ✗
#    但那个做法会让【整页正文右移 96px】✗（正文是 max-width 居中的，
#    可用宽度少 192px → 居中点就右移 96px ✓）—— 实测 1920/1600/1440 三档都偏移 96px ✗
#    正确做法：导航栏【纯粹待在正文左边的留白里】✓ 正文的宽度和位置一点不动 ✓
check("★ 正文一点没被挤动：宽屏时【不给 body 加内边距】✗，正文容器的 width / margin:auto 原样",
      "body { padding-left" not in STYLE_CSS
      and ".container { width: min(1080px, 100% - 32px); margin: 0 auto; }" in STYLE_CSS)
# ★ 2026-10-16 改：左边距从"写死 16px"改成"跟着正文走" ✗
#   写死 16px 时，屏幕越宽导航离正文越远 —— 2560px 屏上离了 576px，
#   看着像孤零零挂在屏幕角上 ✗。改成 left = 正文左边 − 自身宽 − 16px ✓
check("★ 导航左边距跟着正文走（永远隔 16px，不会在宽屏上漂远）",
      "left: calc(max(16px, (100vw - 1080px) / 2) - var(--nav-w) - 16px);" in STYLE_CSS
      and "--nav-w: clamp(" in STYLE_CSS,
      "2560px 屏：导航右边缘 724，正文 740 → 隔 16px ✓")
check("★ 跳转要留出吸顶导航的高度（不然目标被顶栏挡住）：scroll-margin-top",
      "[data-nav] { scroll-margin-top: 78px; }" in STYLE_CSS
      and "navOffset()" in ZONE_JS and "scroll-margin-top" in STYLE_CSS)
check("★ scroll-spy 用同一份块顺序算（不会和页面顺序不同步）",
      "updateActiveNav" in ZONE_JS and "zone-nav-item.active" in STYLE_CSS
      and ".zone-nav-item.active" in STYLE_CSS)

# ============================================================================
#  3c. ★ 两个摘要行（自动拼 + 可覆盖），2026-10-16 新加
# ============================================================================
print("\n=== 3c. ★ 折叠块的摘要行（步骤名 / 条目名自动拼，留空=自动） ===")
check("★ 「怎么开始」是原生 <details>，摘要行 = 标题 + 自动拼的步骤名",
      "<details class=\"panel steps-panel\" id=\"stepsPanel\"" in ZONE_HTML
      and 'id="stepsSummary"' in ZONE_HTML)
check("★ 「下单前须知」也是原生 <details>（默认收起）",
      "<details class=\"panel promise-panel\" id=\"promisePanel\"" in ZONE_HTML
      and 'id="promisesSummary"' in ZONE_HTML)
check("★★ 摘要行是**自动拼**出来的：名字直接来自 steps / promises 的数据",
      "function autoSummary(" in ZONE_JS and "stepsSummaryText" in ZONE_JS
      and "promisesSummaryText" in ZONE_JS
      and "(s.items || []).map((it) => it.title)" in ZONE_JS)
check("★ 摘要行有【条数上限】和【字数预算】（免得摘要比正文还长）",
      "maxShow = 5" in ZONE_JS and "budget = 40" in ZONE_JS
      and "used + n.length > budget" in ZONE_JS and "more ? ' …'" in ZONE_JS)
# ★ 2026-10-16 补的三条（截图里发现的问题：须知那几条是整句话，直接拼出来占了两行 ✗）
check("★ 长句会被截成短名字（≤14 字原样用；>14 字在第一个标点处断开）",
      "function shortenName(" in ZONE_JS and "limit = 14" in ZONE_JS
      and "search(/[" in ZONE_JS and "。；！？，、,;!?]" in ZONE_JS)
check("★ 短标题一个字都不动（步骤名走「≤14 字原样用」那条路）",
      "if (s.length <= limit) return s;" in ZONE_JS)
check("★ 只有标点的垃圾条目会被丢掉（数据里真出现过一条「、」✗）",
      "filter((t) => t &&" in ZONE_JS and "[^" in ZONE_JS and "s·、，,;；" in ZONE_JS)
check("★ 两个键都能被后台覆盖（填了就听主人的，留空才自动拼）",
      "panelTip('stepsSummary'" in ZONE_JS and "panelTip('promisesSummary'" in ZONE_JS
      and "tx(key, '') || auto" in ZONE_JS)
check("★ 折叠块没有 open 属性 → 默认收起（流程 / 须知都一样）",
      'id="stepsPanel" data-nav="navZoneSteps" hidden>' in ZONE_HTML_NOCOMMENT
      and 'id="promisePanel">' in ZONE_HTML_NOCOMMENT)
# 6 个导航词 + 2 个摘要覆盖键 + 导航栏标题，都能在后台改
s, adm = jcall("/api/admin")
_texts = {f["key"]: f for g in (adm.get("textGroups") or []) for f in g["fields"]}
_want = {"navZoneTitle": "本页内容", "navZonePrice": "档位", "navZoneSteps": "流程",
         "navZonePromises": "须知", "navZoneSchedule": "预约", "navZoneFaq": "问答",
         "navZoneGallery": "展示"}
check("★ 6 个导航词 + 导航栏标题都在文案目录里（后台能改）",
      all(_texts.get(k, {}).get("default") == v for k, v in _want.items()),
      str([k for k, v in _want.items() if _texts.get(k, {}).get("default") != v]))
check("★ 两个摘要覆盖键默认留空（留空 = 自动拼；后台那格显示灰色提示）",
      _texts.get("stepsSummary", {}).get("default") == ""
      and _texts.get("promisesSummary", {}).get("default") == ""
      and "留空" in str(_texts.get("stepsSummary", {}).get("placeholder")),
      str(_texts.get("stepsSummary", {}).get("placeholder")))
check("★ 「常见问题整块要不要也折叠」也有一个键（faqPanelSummary，留空 = 不折叠）",
      _texts.get("faqPanelSummary", {}).get("default") == ""
      and "faqPanelSummary" in ZONE_JS)

# ============================================================================
#  4. 折叠块（不是独立页面）+ 纯文本
# ============================================================================
print("\n=== 4. ★ 折叠块（每条一个 details，默认全部收起） ===")
check("★ 前台用原生 <details> 渲染 FAQ 每一条（不装 JS 也能点开）——分区页",
      '<details class="faq-item">' in ZONE_JS and 'class="faq-item"' in ZONE_JS)
check("★ 没有 open 属性 → **默认全部收起**（这就是「折叠块」的意思）",
      '<details class="faq-item">' in ZONE_JS and "faq-item\" open" not in ZONE_JS)
check("★ 流程复用 .promises 那套 01/02/03 的圆圈编号（沿用项目里已有的样式）",
      'class="promises steps-list"' in ZONE_HTML)
check("★ 两块都不是独立页面（没有新模板、没有新路由，就在已有页面里）",
      not os.path.isfile(os.path.join(ROOT, "templates", "faq.html"))
      and 'data-panel="faq"' not in ADMIN_HTML
      and "/api/admin/faq" in ADMIN_JS and "/api/admin/steps" in ADMIN_JS)
check("答案保留主人写的换行（CSS white-space: pre-line）",
      "white-space: pre-line" in open(os.path.join(ROOT, "static", "style.css"),
                                      encoding="utf-8").read())
check("★ 渲染用的是 textContent / escapeHtml（文案是纯文本，不认 HTML）",
      "escapeHtml(fillWechat(it.a || ''))" in ZONE_JS)
check("★ {wechat} 在前台会被换成微信号（空了也不会显示成 {wechat}）",
      "fillWechat" in SITE_JS and "fillWechat" in ZONE_JS)

# ============================================================================
#  5. 后台能改：标题 / 副标题 / 问答 → 前台立刻变
# ============================================================================
print("\n=== 5. 后台改 FAQ → 前台立刻变 ===")
secs = [dict(x) for x in faq["items"]]
secs[0]["a"] = "第一段是改过的。\n第二行接着。\n\n第二段开始。"
s, b = save_faq("我的常见问题", secs, subtitle="改过的副标题")
check("保存 FAQ 成功", s == 200 and b.get("ok"), "status=%s %s" % (s, str(b)[:100]))
check("★ 标题改了", b.get("faq", {}).get("title") == "我的常见问题",
      str(b.get("faq", {}).get("title")))
check("★ 副标题也改了（它不是装饰：一句话告诉客户这块怎么用）",
      b.get("faq", {}).get("subtitle") == "改过的副标题",
      str(b.get("faq", {}).get("subtitle")))
check("改一条答案不会动别的问题",
      b["faq"]["items"][1]["q"] == secs[1]["q"])

site = public_site()
check("★ 前台立刻是新标题 / 新副标题",
      site["faq"]["title"] == "我的常见问题" and site["faq"]["subtitle"] == "改过的副标题")
check("★ 前台立刻是新答案", site["faq"]["items"][0]["a"].startswith("第一段是改过的。"),
      str(site["faq"]["items"][0]["a"])[:40])
s, home = call("/")
check("★ 首页 HTML 里也立刻是新的（不用等缓存）", "改过的副标题" in home)

# ============================================================================
#  6. 增 / 删 / 排序
# ============================================================================
print("\n=== 6. 增删条目 + 排序（↑↓ 就是数组顺序） ===")
more = [dict(x) for x in secs] + [{"q": "九、新加的问题", "a": "这是新加的答案。"}]
s, b = save_faq("我的常见问题", more)
check("新增一条", s == 200 and len(b["faq"]["items"]) == 9,
      "共 %d 条" % len(b["faq"]["items"]))
check("★ 新那条排在最后（顺序就是数组顺序）",
      b["faq"]["items"][-1]["q"] == "九、新加的问题")

moved = [more[-1]] + more[:-1]
s, b = save_faq("我的常见问题", moved)
check("★ 调顺序（↑↓ 调的就是这个数组）", s == 200
      and b["faq"]["items"][0]["q"] == "九、新加的问题",
      str([x["q"] for x in b["faq"]["items"]][:3]))
site = public_site()
check("★ 前台顺序跟着变（客户看到的是第一条）",
      site["faq"]["items"][0]["q"] == "九、新加的问题",
      str(site["faq"]["items"][0]["q"]))

s, b = save_faq("我的常见问题", [dict(x) for x in more[:-1]])
check("删除一条", s == 200 and len(b["faq"]["items"]) == 8,
      "共 %d 条" % len(b["faq"]["items"]))
check("删掉的那条不在前台了",
      all(x["q"] != "九、新加的问题" for x in public_site()["faq"]["items"]))

# ============================================================================
#  7. 空条目 / 校验 / 上限
# ============================================================================
print("\n=== 7. 空条目与校验 ===")
s, b = save_faq("我的常见问题", [{"q": "有问题", "a": "有答案"}, {"q": "", "a": ""},
                                 {"q": "   ", "a": "  "}])
check("★ 问题和答案都空的条目被丢掉（= 你把这条删了，不算错）",
      s == 200 and len(b["faq"]["items"]) == 1, "共 %d 条" % len(b["faq"]["items"]))
check("★ 丢了几条会回报给后台", b.get("dropped") == 2, "dropped=%s" % b.get("dropped"))
s, b = save_faq("我的常见问题", [{"q": "只有问题", "a": ""}])
check("★ 只有问题、答案还空着 → 保留（他想先占位、答案以后再填）",
      s == 200 and len(b["faq"]["items"]) == 1, "共 %d 条" % len(b["faq"]["items"]))
s, b = save_faq("", [{"q": "q", "a": "a"}])
check("标题空 → 400 中文报错", s == 400 and "标题" in str(b.get("error", "")),
      "status=%s %s" % (s, str(b.get("error"))[:60]))
s, b = jcall("/api/admin/faq", "POST", {"action": "save", "title": "x", "items": "不是数组"})
check("items 不是数组 → 400", s == 400 and "格式" in str(b.get("error", "")),
      "status=%s %s" % (s, str(b.get("error"))[:60]))

long_a = "长" * 3000
s, b = save_faq("上限测试", [{"q": "问" * 200, "a": long_a}])
check("★ 答案超长截断（1500 字上限）", len(b["faq"]["items"][0]["a"]) == 1500,
      "长度=%d" % len(b["faq"]["items"][0]["a"]))
check("问题超长也截断（80 字）", len(b["faq"]["items"][0]["q"]) == 80,
      "长度=%d" % len(b["faq"]["items"][0]["q"]))
s, b = save_faq("条数上限", [{"q": "第 %d 条" % i, "a": "x"} for i in range(50)])
check("★ 条数有上限（最多 30 条，多余的丢掉）",
      s == 200 and len(b["faq"]["items"]) == 30, "共 %d 条" % len(b["faq"]["items"]))

# ============================================================================
#  8. ★ 服务流程：2~6 步
# ============================================================================
print("\n=== 8. ★ 服务流程的步数（可改，2~6 步） ===")
three = [{"title": "第一步", "desc": "做这个"}, {"title": "第二步", "desc": "做那个"},
         {"title": "第三步", "desc": "收尾"}]
s, b = save_steps("我的流程", three)
check("改成 3 步", s == 200 and len(b["steps"]["items"]) == 3,
      "共 %d 步" % len(b["steps"]["items"]))
check("前台立刻是 3 步", len(public_site()["steps"]["items"]) == 3)

six = [{"title": "第%d步" % i, "desc": "说明 %d" % i} for i in range(1, 7)]
s, b = save_steps("我的流程", six)
check("★ 6 步是上限，能存", s == 200 and len(b["steps"]["items"]) == 6,
      "共 %d 步" % len(b["steps"]["items"]))
check("6 步时不提示", not b.get("tip"), str(b.get("tip")))

seven = six + [{"title": "第七步", "desc": "多余的一步"}]
s, b = save_steps("我的流程", seven)
check("★ 超过 6 步 → 只留前 6 步（不报错）",
      s == 200 and len(b["steps"]["items"]) == 6, "共 %d 步" % len(b["steps"]["items"]))
check("★ 并且回一句提示告诉主人（最多 6 步，超出的没保存）",
      "6 步" in str(b.get("tip")), str(b.get("tip"))[:60])

s, b = save_steps("我的流程", [{"title": "只剩一步", "desc": "不成流程"}])
check("★ 少于 2 步 → **退回默认那 4 步**（不是 400：他可能正编辑到一半）",
      s == 200 and len(b["steps"]["items"]) == 4, "共 %d 步" % len(b["steps"]["items"]))
check("★ 并且回一句提示", "2 步" in str(b.get("tip")) or "4 步" in str(b.get("tip")),
      str(b.get("tip"))[:80])
check("★ 退回的是出厂那 4 步（不是空列表，前台永远有东西显示）",
      [x["title"] for x in b["steps"]["items"]]
      == ["挑时间 / 说需求", "加我微信", "我确认", "开始服务"],
      str([x["title"] for x in b["steps"]["items"]]))

s, b = save_steps("", six)
check("流程标题空 → 400", s == 400 and "标题" in str(b.get("error", "")),
      "status=%s %s" % (s, str(b.get("error"))[:60]))
s, b = jcall("/api/admin/steps", "POST", {"action": "save", "title": "x", "items": "不是数组"})
check("items 不是数组 → 400", s == 400, "status=%s" % s)
s, b = save_steps("上限测试", [{"title": "标" * 100, "desc": "说" * 500},
                               {"title": "第二", "desc": "d"}])
check("★ 步骤标题截到 30 字、说明截到 200 字",
      len(b["steps"]["items"][0]["title"]) == 30 and len(b["steps"]["items"][0]["desc"]) == 200,
      "%d/%d" % (len(b["steps"]["items"][0]["title"]), len(b["steps"]["items"][0]["desc"])))

# ============================================================================
#  9. 恢复默认
# ============================================================================
print("\n=== 9. 恢复默认 ===")
s, b = jcall("/api/admin/faq", "POST", {"action": "reset"})
check("FAQ 恢复默认", s == 200 and b.get("ok"), "status=%s" % s)
check("★ 回到出厂那 8 条（问题一字不差）",
      [x["q"] for x in b["faq"]["items"]] == WANT_QUESTIONS,
      str([x["q"] for x in b["faq"]["items"]])[:100])
check("标题也回到「常见问题」", b["faq"]["title"] == "常见问题")
s, b = jcall("/api/admin/steps", "POST", {"action": "reset"})
check("流程恢复默认", s == 200 and b.get("ok"), "status=%s" % s)
check("★ 回到出厂那 4 步", [x["title"] for x in b["steps"]["items"]]
      == ["挑时间 / 说需求", "加我微信", "我确认", "开始服务"])
s, b = jcall("/api/admin/faq", "POST", {"action": "没了这个动作"})
check("FAQ 不认识的动作 → 400", s == 400, "status=%s" % s)
s, b = jcall("/api/admin/steps", "POST", {"action": "没了这个动作"})
check("流程不认识的动作 → 400", s == 400, "status=%s" % s)

# ============================================================================
# 10. 未登录
# ============================================================================
print("\n=== 10. 未登录 → 401 ===")
s, b = jcall("/api/admin/faq", "POST", {"action": "save", "title": "偷偷改", "items": []},
             use_cookie=False)
check("未登录保存 FAQ → 401", s == 401, "status=%s" % s)
s, b = jcall("/api/admin/faq", "POST", {"action": "reset"}, use_cookie=False)
check("未登录恢复 FAQ → 401", s == 401, "status=%s" % s)
s, b = jcall("/api/admin/steps", "POST", {"action": "save", "title": "偷偷改", "items": []},
             use_cookie=False)
check("未登录保存流程 → 401", s == 401, "status=%s" % s)
check("★ 被拒之后一个字都没变（FAQ 还是默认 8 条）",
      len(admin_faq().get("items") or []) == 8, str(admin_faq().get("title")))

# ============================================================================
# 11. 纯文本（安全底线）：问答里写 <script> 会被转义
# ============================================================================
print("\n=== 11. 文案是纯文本（XSS 底线） ===")
save_faq("转义测试", [{"q": "试试 <b>加粗</b>", "a": "这里写 <script>alert(1)</script> 看看"}])
s, home = call("/")
check("★ 首页 HTML 里没有真的 <script>alert(1)</script>（没被当成标签执行）",
      "<script>alert(1)</script>" not in home, "找得到就糟了")
check("★ 但内容确实发出去了（在 __PAGE__ 的 JSON 里，前端 escapeHtml 之后再放进页面）",
      "alert(1)" in home and "转义测试" in home)
# ⚠️ 这一段 JSON 是塞在 <script> 标签里的，所以 json.dumps 之后 "/" 会被转义成 \/
#    （render_page 里那个 .replace("</", "<\\/")），不然 </script> 会提前把标签关掉、
#    整页 HTML 就断了。这里顺手守住它。
check("★ JSON 里的 </script> 被转义成 <\\/script>（不会提前关掉 script 标签）",
      "<\\/script>" in home)
check("★ 前端渲染时走的是 escapeHtml（所以客户看到的是字面量，不是加粗）",
      "escapeHtml(it.q || '')" in ZONE_JS)
# 流程那块：分区页 HTML 里同样不能出现"能当标签用"的形式
save_steps("转义测试", [{"title": "<img onerror=x>", "desc": "<script>bad()</script>"},
                        {"title": "第二步", "desc": "正常的一步"}])
s, zone_html = call("/z/" + first_zone_id())
# ★ 判据（实测过才敢这么写）：
#   · 数据里的 "</script>" 一定被 render_page 转成 "<\/script>"（否则它会提前关掉标签、
#     整页 HTML 就断了）—— 所以"裸的 </script>"只该剩**骨架里的那两个**：
#     一个结束 __PAGE__ 那个 <script>，一个结束 <script src="/static/zone.js">。
#   · 数据里的 "<script>" 是**字面量**（在 JSON 字符串里，浏览器不会把它当标签解析），
#     前端拿到之后用 escapeHtml() 再放进 DOM，所以客户看到的是字面量、不是加粗/报错弹窗。
check("★ 数据里的 </script> 被转义成 <\\/script>（不会提前关掉 script 标签）",
      "<\\/script>" in zone_html)
check("★ 裸的 </script> 只剩骨架那两个（数据里一个都没有漏出来）",
      zone_html.count("</script>") == 2, "实际 %d 个" % zone_html.count("</script>"))
check("★ 内容确实发出去了（在前端渲染的那份 JSON 里）", "bad()" in zone_html)
check("★ 前端渲染时走的是 escapeHtml（标题和说明都过一遍）",
      "escapeHtml(it.title || '')" in ZONE_JS and "escapeHtml(fillWechat(it.desc))" in ZONE_JS)

# ============================================================================
# 12. 备份 / 恢复 / 老备份
# ============================================================================
print("\n=== 12. 备份带上这两块（老备份不动它） ===")
save_faq("备份时的标题", [{"q": "备份时的问题", "a": "备份时的答案"}], subtitle="备份时的副标题")
save_steps("备份时的流程", [{"title": "备份第一步", "desc": "d1"},
                            {"title": "备份第二步", "desc": "d2"}])
s, raw = call("/api/admin/export")
backup = json.loads(raw)
check("★ 备份里有 faq", isinstance(backup.get("faq"), dict)
      and backup["faq"].get("title") == "备份时的标题",
      str((backup.get("faq") or {}).get("title")))
check("★ 备份里有 steps", isinstance(backup.get("steps"), dict)
      and len((backup.get("steps") or {}).get("items") or []) == 2,
      str((backup.get("steps") or {}).get("title")))

save_faq("备份之后又改的", [{"q": "后来的", "a": "后来的答案"}])
save_steps("备份之后又改的", [{"title": "后来的", "desc": "x"}, {"title": "又一步", "desc": "y"}])
s, b = jcall("/api/admin/import", "POST", {"backup": backup})
check("导入备份成功", s == 200 and b.get("ok"), "status=%s %s" % (s, str(b)[:100]))
check("★ 恢复后 FAQ 回到备份时的样子", b.get("faq", {}).get("title") == "备份时的标题",
      str(b.get("faq", {}).get("title")))
check("★ 恢复后流程也回到备份时的样子", b.get("steps", {}).get("title") == "备份时的流程",
      str(b.get("steps", {}).get("title")))
check("★ 恢复摘要里有 FAQ 条数和流程步数",
      "faq" in (b.get("summary") or {}) and "steps" in (b.get("summary") or {}),
      str((b.get("summary") or {}).get("faq")) + " / " + str((b.get("summary") or {}).get("steps")))
check("前台也是备份时那份", public_site()["faq"]["title"] == "备份时的标题")

old_backup = json.loads(json.dumps(backup))
old_backup.pop("faq", None)
old_backup.pop("steps", None)
save_faq("老备份导入前改的", [{"q": "我现在的", "a": "我现在的答案"}])
save_steps("老备份导入前改的", [{"title": "我现在的一步", "desc": "a"},
                                {"title": "我现在的二步", "desc": "b"}])
s, b = jcall("/api/admin/import", "POST", {"backup": old_backup})
check("★ 老备份（没有 faq / steps 字段）也能导入", s == 200 and b.get("ok"), "status=%s" % s)
check("★★ 老备份**不会清空**他写的 FAQ（一条都不动）",
      b.get("faq", {}).get("title") == "老备份导入前改的",
      str(b.get("faq", {}).get("title")))
check("★★ 老备份**不会清空**他写的流程（一条都不动）",
      b.get("steps", {}).get("title") == "老备份导入前改的",
      str(b.get("steps", {}).get("title")))
check("★ 老备份导入后，前台还是他现在那份",
      public_site()["faq"]["title"] == "老备份导入前改的")

# 坏数据不能让整份备份导不进来
broken = json.loads(json.dumps(backup))
broken["faq"] = {"title": "坏数据", "items": ["不是字典", {"q": "", "a": ""}, {"q": "好的", "a": "好"}]}
broken["steps"] = {"title": "坏数据", "items": ["不是字典", {"title": "好的", "desc": "好"},
                                               {"title": "好的2", "desc": "2"}]}
s, b = jcall("/api/admin/import", "POST", {"backup": broken})
check("★ 备份里这两块有坏行 → 恢复照样成功（坏行跳过）", s == 200 and b.get("ok"),
      "status=%s %s" % (s, str(b)[:100]))
check("坏行被丢掉、好的留下了", len(b.get("faq", {}).get("items") or []) == 1
      and len(b.get("steps", {}).get("items") or []) == 2,
      "%d / %d" % (len(b.get("faq", {}).get("items") or []),
                   len(b.get("steps", {}).get("items") or [])))

# ============================================================================
# 13. 后台编辑器在「文案」页里
# ============================================================================
print("\n=== 13. 后台编辑器 / 文案目录 ===")
texts_sec = ADMIN_HTML[ADMIN_HTML.find('data-panel="texts"'):ADMIN_HTML.find('data-panel="prices"')]
check("★ FAQ 编辑器在「文案」标签页里（和条款同一页）",
      'id="faqItems"' in texts_sec and 'id="saveFaq"' in texts_sec)
check("★ 流程编辑器也在「文案」标签页里",
      'id="stepsItems"' in texts_sec and 'id="saveSteps"' in texts_sec)
check("★ 两条都有 ↑↓ / 删除 / 新增 / 恢复默认（和条款那套交互一致）",
      all(x in ADMIN_JS for x in ("data-faq-up", "data-faq-down", "data-faq-del",
                                  "data-step-up", "data-step-down", "data-step-del")))
check("★ 后台的增删改排序也是「先收内存、再重画」（不会丢刚打的字）",
      "collectFaqFromForm()" in ADMIN_JS and "collectStepsFromForm()" in ADMIN_JS)

# ⚠️ 原来这里还查了「分享二维码那几句文案在目录里」。
#    那个功能 2026-10-15 被砍了（理由见 docs\交接说明.md 的坑 56），
#    那几句文案（adminQr*）也一起从 app.py 的 TEXT_GROUPS 删掉了，
#    所以这几条断言跟着删 —— 留着就是守着不存在的东西。

# ============================================================================
# 14. 收尾：恢复默认，别影响后面的阶段
# ============================================================================
print("\n=== 14. 收尾 ===")
jcall("/api/admin/faq", "POST", {"action": "reset"})
jcall("/api/admin/steps", "POST", {"action": "reset"})
check("FAQ 已恢复默认 8 条", len(admin_faq().get("items") or []) == 8,
      str(admin_faq().get("title")))
check("流程已恢复默认 4 步", len(admin_steps().get("items") or []) == 4,
      str(admin_steps().get("title")))
s, home = call("/")
check("前台也是默认那份", "账号安全吗" in home)

print("\n" + "=" * 70)
print(" 通过 %d 项，失败 %d 项" % (ok, fail))
print("=" * 70)
sys.exit(1 if fail else 0)
