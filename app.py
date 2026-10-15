# -*- coding: utf-8 -*-
"""
================================================================================
 个人服务招牌站 · 后端（单文件，只用 Python 标准库，零第三方依赖）
================================================================================
 这个文件就是整个网站的"服务器"。它的工作只有三件：
   1. 把数据存进 SQLite 数据库（data/site.db）
   2. 对外提供一组 JSON 接口（/api/...）
   3. 把 HTML / CSS / JS 文件发给浏览器

 网站结构（两級分区）：
   /            个人主页：品牌 + 我能做什么（按大类分组的分区卡片）+ 信任信号 + 加微信
   /z/<分区id>  分区页：陪玩类显示预约时间表，代肝/其他服务类显示分组价目表
                后台（网址是个暗号，见 ADMIN_PATH）、/static/... /api/... 不会被分区 id 抢走

 两级分区的两个核心概念：
   categories（大类）：陪玩（预约制 booking）/ 代肝（服务制 service）/ 其他服务
   zones（分区）    ：三角洲行动、守望先锋、明日方舟……都存在 zones 表里，后台自助增删改

 ⚠️ **时间表是全局共用的**：只有一份时间，不管客户从哪个分区进来，
    占的都是同一批时段（locks 表）。分区只决定"展示什么"，不决定"卖什么时间"。

 怎么跑起来：
     python app.py                # 然后浏览器打开 http://127.0.0.1:8123
 换端口：
     set PORT=9000 && python app.py     (Windows)

 阅读顺序建议：先看【一、配置】→【二之三、分区与大类】→【三、时段计算】→【六、HTTP 接口】
================================================================================
"""

import base64
import hashlib
import hmac
import io
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

# 登录限流：同一个 IP 在 10 分钟里最多错几次密码，超了就先拦住
# （密码本身已经用 PBKDF2 加盐存了，这里防的是"有人拿脚本一直猜"）
LOGIN_RATE_MAX = 8
LOGIN_RATE_WINDOW = 600
NOTIFY_TIMEOUT = 10            # 发通知的超时时间（秒）
SESSION_HOURS = 12             # 管理员登录后多少小时过期

# ★ 「待确认」与自动释放（第二刀）
#   客户自助预约（source='client'）默认是**待确认**状态：时间先给他留住，
#   但老板没点「已确认」之前，这单一直挂在后台最上面提醒他。
#   AUTO_RELEASE_DEFAULT_HOURS = 0 表示**默认不自动释放** ——
#   客户没加微信、联系不上的时候，那段时间确实白占着（时段=库存=钱），
#   但"悄悄把客户的时间删掉"这件事必须由主人自己决定什么时候开，默认绝不能替他做主。
AUTO_RELEASE_DEFAULT_HOURS = 0      # store key='auto_release_hours' 的默认值：0 = 关闭
AUTO_RELEASE_LOG_MAX = 10           # 「最近自动释放」最多留几条（store key='auto_released_log'）
AUTO_RELEASE_LOG_TEXT = 200         # 日志里每条"客户名 + 时间"的文本上限
AUTO_RELEASE_CHECK_SECONDS = int(os.environ.get("AUTO_RELEASE_CHECK_SECONDS") or 30)
AUTO_RELEASE_HOURS_MAX = 720        # 设置里最多能填多少小时（30 天，再多就不如手动删了）
# 联系方式渠道：客户可以用微信 / 闲鱼 / QQ 留下联系方式，后台按渠道显示
CONTACT_TYPES = {"wechat": "微信", "xianyu": "闲鱼", "qq": "QQ"}
CONTACT_DEFAULT = "wechat"     # 老接口没传 contactType 时当成微信

# 星期几的中文写法（datetime.weekday() 是 0=周一，所以顺序从周一开始）
WEEK_CN = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")

# 首次启动时的管理员密码（之后请在"安全与备份"里改掉）
DEFAULT_PASSWORD = os.environ.get("ADMIN_PASSWORD") or "baixu123"
PORT = int(os.environ.get("PORT") or 8123)

# ★ 后台网址的"暗号"：只有你知道。
#   为什么要改：自动扫描机器人一天到晚在扫全网的 /admin、/wp-admin，
#   把门牌换个名字，它们就找不到门了（旧地址 /admin 会直接 404，不给任何提示）。
#   ⚠️ 换了之后：新地址要存书签；测试文件里的路径也要跟着改（下面注释有说明）。
#   想再换：改这一行 + tests/test_api.py、tests/test_zones.py、tests/check_deployed.py 里的路径。
ADMIN_PATH = "/" + (os.environ.get("ADMIN_PATH") or "nya-0508").strip("/")
# 监听地址。默认只监听本机 127.0.0.1 —— 这样同一个 WiFi 下的其他设备
# （手机、室友的电脑、咖啡厅的陌生人）都连不上你的网站和后台，最安全。
# 需要临时放开时，用环境变量覆盖：
#   手机测试：  set HOST=0.0.0.0 && python app.py   （或者双击 start-lan.bat）
#   云平台：    Render 这类平台需要监听所有网卡，在平台的环境变量里设 HOST=0.0.0.0
HOST = os.environ.get("HOST") or "127.0.0.1"

# 首次启动时写入的默认资料（换成你自己的即可，也可以在后台直接改）
#
# ⚠️ 升级到"两级分区"之后，这个 store 里的 profile **只放全局共用的东西**：
#    品牌名、昵称、微信号、头像、个人介绍、信任信号、接单总开关……
#    "游戏名 / 标语 / 报价 / 服务承诺 / 背景图 / 首屏图"这些**每个分区不一样**的，
#    全部搬进了 zones 表里每个分区自己的 profile（见下面 DEFAULT_ZONE_PROFILE）。
DEFAULT_PROFILE = {
    "brand": "Nya",                  # 品牌名（首页顶部那个大标题）
    "name": "示例名字",               # 昵称（客户怎么称呼你）
    "tagline": "陪玩 · 代肝 · 剪辑 · 设计",     # 首页副标题（一句话说清你能做什么）
    "wechat": "your_wechat_id",
    "xianyu": "",
    "avatar": "",
    "bio": "个人接单，非工作室，无中间商。\n陪玩、代肝、剪辑、设计都能做，点进去看每个分区的详情和价目。",
    "accepting": True,               # 接单总开关（关掉后所有分区的空闲格都显示"停接"）
    "services_intro": "我能做什么",    # 首页服务区的标题
    "trust": [],                     # 信任信号（字符串数组），例如 ["微博完单量 1000+", "信誉图见主页"]
    "wechatQr": "",                  # 微信二维码图（选填）
    "heroImage": "",                 # 主页首屏主视觉（选填，PNG 透明底最好看）
    "background": "",                # 首页整页背景图（各分区自己的背景图在 zones.profile 里）
    "backgroundDim": 72,             # 首页背景压暗程度（0-90）
    # ★ 图片「显示位置」（3×3 九宫格，见 POSITION_CHOICES）：
    #   这几张图会被裁掉一部分才铺得满，所以给一个"保留哪一块"的开关。
    #   默认 center = 和以前一模一样，没设置过的人看不出任何变化。
    "avatarPosition": "center",      # 头像（object-fit: cover）
    "backgroundPosition": "center",  # 首页整页背景图（background-size: cover）
    "heroImagePosition": "center",   # 主页首屏主视觉（object-fit: cover）
}

# 默认大类（store key='categories'）。大类**不允许删除**，只能改名 / 改类型 / 排序，
# 这样分区永远不会"无家可归"。
#   kind="booking" → 这个大类下的分区显示预约时间表，用扁平 prices（三档）
#   kind="service" → 这个大类下的分区不显示时间表，用分组价目表 priceGroups
DEFAULT_CATEGORIES = [
    # hint = 首页/分区页上跟着大类名字显示的那句小字（后台可以自己改）
    {"id": "play", "name": "陪玩", "hint": "按小时预约", "kind": "booking", "sort": 1},
    {"id": "boost", "name": "代肝", "hint": "包月 / 按量", "kind": "service", "sort": 2},
    {"id": "service", "name": "其他服务", "hint": "按需报价", "kind": "service", "sort": 3},
]

# 老数据里的大类没有 hint 字段时，按 id 补上默认值（这样升级后不会空着）
DEFAULT_CATEGORY_HINTS = {c["id"]: c["hint"] for c in DEFAULT_CATEGORIES}

# ★ 展示墙的分类（store key='gallery_categories'）。
#   为什么要做成可配置：展示墙一开始只服务"陪玩"（游戏截图 / 老板评价），
#   但剪辑、PS、建站这些**作品集**也要挂上去 —— 总不能为了加一个"作品"分类去改代码。
#   写法照抄上面的 DEFAULT_CATEGORIES（大类）：存 store 表、一段 JSON 数组、id + name + sort。
#
#   ⚠️ 和"大类"唯一的区别：**展示墙分类允许删除**（见 admin_gallery_categories）。
#      大类不能删是因为分区会无家可归；一个展示墙分类删了，图还在，只是要换个归类。
DEFAULT_GALLERY_CATEGORIES = [
    {"id": "screenshot", "name": "游戏截图", "sort": 1},
    {"id": "review", "name": "老板评价", "sort": 2},
    {"id": "work", "name": "作品", "sort": 3},
]

# 展示墙分类 id 的格式：英文小写字母/数字开头，后面可以跟小写字母/数字/连字符，最长 21 位。
#   比分区 id（ZONE_ID_RE，31 位）短一些 —— 分类只在自己站内用，不需要那么长。
GALLERY_CAT_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,20}$")

# 展示墙「外链」的长度上限（B 站视频地址、自己做的网站地址都够用了）
GALLERY_LINK_MAX = 300

# 老数据里 category 写的是这两个值（写死的时代留下来的），新库默认配置正好能对上：
#   screenshot → 游戏截图 / review → 老板评价
DEFAULT_GALLERY_CATEGORY = "screenshot"

# ============================================================================
# ★ 文案表（store key='texts'）：客户能看到的每一句中文，全都从这里来
#
#   为什么要有它：在这之前，这些句子是**写死在** templates/*.html 和 static/*.js 里的，
#   主人想把「看看我能做什么」改成别的说法，得先学会改代码 —— 这不合理。
#   现在它们集中在这一份目录里，一份目录三处用：
#     · 后台「文案」标签页照它**自动生成**输入框（不用一个个写 HTML）；
#     · 前台 site.js / zone.js 按 key 取值（服务端把默认值补齐了，前端不用兜底）；
#     · 备份/恢复到 store 的 texts 里。
#
#   ⚠️ 三条约定（tests\test_texts.py 里守着它们）：
#     1. **文案是纯文本，不认 HTML**。主人粘贴一句带 <b> 的话，页面上就会原样看到尖括号 ——
#        宁可难看一点，也不能让"文案"变成一段能执行/能注入的代码（那是 XSS 的入口）。
#     2. 默认值里的 **{name} / {zone} / {max} / {wechat}** 是"变量"，前台会替换成
#        昵称 / 分区名 / 一次最多几小时 / 微信号。改文案时别把占位符删掉，
#        否则那句话就和实际规则对不上了（比如"一次最多 6 小时"其实早就改成 10 了）。
#     3. 每条都有长度上限（max），超了截断：防止有人往这里塞一整篇小说。
#
#   存的时候**只存"和默认不一样"的那几句**（见 clean_texts）：
#   空字符串、或者填得和默认一字不差，都不写进数据库 —— 这样
#   "没改过的人完全看不出差别"，而且以后默认值改好了，他那边也跟着变好。
TEXT_GROUPS = [
    {"id": "home", "name": "① 主页", "hint": "客户打开个人主页（/）时看到的话。", "fields": [
        {"key": "navServices", "label": "顶部导航：我能做什么（点了跳到服务卡片）", "default": "我能做什么", "max": 20},
        {"key": "navWhy", "label": "顶部导航：为什么找我", "default": "为什么找我", "max": 20},
        {"key": "navAbout", "label": "顶部导航 + 页脚小链接：关于我", "default": "关于我", "max": 20},
        {"key": "heroIntro", "label": "首屏大标题下面那行自我介绍（{name} = 你的昵称）", "default": "我是 {name}，下面这些都是我一个人在做。", "max": 120},
        {"key": "heroCta", "label": "首屏主按钮：看看我能做什么", "default": "看看我能做什么", "max": 20},
        {"key": "servicesHint", "label": "「我能做什么」右边那行小字", "default": "点开看详情与价目", "max": 40},
        {"key": "whyTitle", "label": "「为什么找我。」那一段的大标题", "default": "为什么找我。", "max": 40},
        {"key": "whyHint", "label": "「为什么找我」右边那行小字", "default": "个人接单 · 无中间商", "max": 40},
        {"key": "aboutTitle", "label": "「关于我」那一块的大标题（{name} = 你的昵称）", "default": "你好，我是 {name}。", "max": 60},
        {"key": "wechatCardHint", "label": "首页二维码上面那句话", "default": "加我微信，直接聊需求", "max": 40},
    ]},
    {"id": "zone-price", "name": "② 分区页 · 价目", "hint": "每个分区页（/z/xxx）的价格那一段。", "fields": [
        {"key": "zoneCtaBooking", "label": "分区页首屏主按钮（陪玩类）", "default": "看空档时间", "max": 20},
        {"key": "zoneCtaService", "label": "分区页首屏主按钮（代肝 / 剪辑类）", "default": "看价目表", "max": 20},
        {"key": "priceTitle", "label": "陪玩三档价格那一段的标题", "default": "玩的方式你选，陪伴我来。", "max": 40},
        {"key": "priceHint", "label": "价格标题右边那行小字", "default": "明码标价 · 个人直约 · 按实际游玩时间计费", "max": 60},
        {"key": "servicePriceTitle", "label": "服务制价目表的标题（{zone} = 游戏名 / 分区名）", "default": "{zone} · 价目表", "max": 60},
        {"key": "servicePriceHint", "label": "价目表标题右边那行小字（大类的小字会自动接在后面）", "default": "加微信直接下单", "max": 40},
        {"key": "featuredTitle", "label": "「主推档位」小标题", "default": "主推档位", "max": 20},
        {"key": "priceSheetTitle", "label": "「完整价目表」小标题", "default": "完整价目表", "max": 20},
        {"key": "priceSheetHint", "label": "价目表上方那行小提示", "default": "点标题展开 / 收起", "max": 40},
        {"key": "priceSheetImageLabel", "label": "价目表长图上面那个标签", "default": "完整价目表（长图）", "max": 40},
        {"key": "galleryLinkBadge", "label": "展示墙图片右上角的角标（只有带外链的图才有）", "default": "点开看 ↗", "max": 20},
        {"key": "galleryEmpty", "label": "这个分区还没传展示墙图片时", "default": "这个分区的展示墙还在装修中，先来找我玩一局吧。", "max": 60},
        {"key": "promiseTitleBooking", "label": "「服务承诺」那一段的标题（陪玩类）", "default": "下单前，聊清楚这几件事", "max": 30},
        {"key": "promiseTitleService", "label": "「服务说明」那一段的标题（代肝 / 剪辑类）", "default": "服务说明", "max": 30},
    ]},
    {"id": "booking", "name": "③ 下单流程", "hint": "时间表、选模式弹窗、锁定成功弹窗里的话。", "fields": [
        {"key": "scheduleTitle", "label": "时间表那一段的标题", "default": "你的时间，我来配合。", "max": 40},
        {"key": "scheduleHint", "label": "时间表标题右边那行小字", "default": "每格 1 小时 · 北京时间 · 点击空闲格多选，可跨日期", "max": 60},
        {"key": "sharedNotice", "label": "时间表上面那条灰底提示", "default": "这份时间表是全部服务共用的：不管约哪个游戏，占的都是同一批时段。", "max": 80},
        {"key": "legendFree", "label": "图例：可预约", "default": "可预约", "max": 12},
        {"key": "legendBooking", "label": "图例：已预约", "default": "已预约", "max": 12},
        {"key": "legendRest", "label": "图例：休息 / 不接单", "default": "休息 / 不接单", "max": 16},
        {"key": "legendPast", "label": "图例：已过去", "default": "已过去", "max": 12},
        {"key": "slotFree", "label": "格子上写的字：可预约", "default": "可预约", "max": 8},
        {"key": "slotBooking", "label": "格子上写的字：已预约", "default": "已预约", "max": 8},
        {"key": "slotRest", "label": "格子上写的字：休息", "default": "休息", "max": 8},
        {"key": "slotPast", "label": "格子上写的字：已过去", "default": "已过去", "max": 8},
        {"key": "slotClosed", "label": "格子上写的字：停接（你关了接单开关时）", "default": "停接", "max": 8},
        {"key": "slotSelected", "label": "格子被选中时写的字", "default": "已选", "max": 8},
        {"key": "selectionHint", "label": "选时间区那行提示（{max} = 一次最多几小时）", "default": "先点选想玩的时间，连续或分开都可以（一次最多 {max} 小时）。", "max": 80},
        {"key": "nextStep", "label": "「下一步：选择模式」按钮", "default": "下一步：选择模式", "max": 20},
        {"key": "modeTitle", "label": "选模式弹窗的标题", "default": "选择模式", "max": 20},
        {"key": "modeHint", "label": "选模式弹窗里那句说明", "default": "选好模式、留下联系方式，点「立即锁定时间」这几个小时就归你了。", "max": 80},
        {"key": "contactTypeLabel", "label": "「联系方式」下拉的标签", "default": "联系方式", "max": 20},
        {"key": "contactTypeHint", "label": "下拉下面那行小字", "default": "填哪个都行，方便联系。", "max": 40},
        {"key": "contactLabel", "label": "联系方式输入框的标签", "default": "你的联系方式（必填）", "max": 30},
        {"key": "contactHint", "label": "联系方式输入框下面那行小字", "default": "请填真实能加上的号——填错了我加不到你，这单就联系不上了（也可以直接加我，见页面顶部）", "max": 120},
        {"key": "contactPhWechat", "label": "联系方式输入框里的灰字（选微信时）", "default": "你的微信号", "max": 30},
        {"key": "contactPhXianyu", "label": "联系方式输入框里的灰字（选闲鱼时）", "default": "你的闲鱼用户名", "max": 30},
        {"key": "contactPhQq", "label": "联系方式输入框里的灰字（选 QQ 时）", "default": "你的 QQ 号", "max": 30},
        {"key": "noteLabel", "label": "备注输入框的标签", "default": "备注（选填）", "max": 20},
        {"key": "notePlaceholder", "label": "备注输入框里的灰字", "default": "例如：想玩大坝、晚上可能晚到 10 分钟", "max": 60},
        {"key": "lockWarn", "label": "选模式弹窗底部那条警告", "default": "⚠️ 点「立即锁定时间」后，这几个小时会立刻被锁定，别人就点不动了；如需取消请微信联系我。", "max": 120},
        {"key": "doneTitle", "label": "锁定成功弹窗的标题", "default": "时间已锁定 ✅", "max": 30},
        # ⚠️ 这一句的真实默认值是"时间预约成功Ciallo～"：以前 HTML 里写的是另一句，
        #    但 zone.js 在打开弹窗前会把它覆盖掉 —— 客户实际看到的一直是 Ciallo 这句，
        #    所以默认值取"客户现在真正看到的那句"（换个默认值 = 悄悄改了他的话）。
        {"key": "doneNote", "label": "锁定成功弹窗的第一句（以前是「时间预约成功Ciallo～(∠・ω< )⌒★」）", "default": "时间预约成功Ciallo～(∠・ω< )⌒★", "max": 60},
        {"key": "bookedToast", "label": "锁定成功后右下角弹的那句小提示", "default": "时间已锁定，记得加我微信", "max": 40},
        {"key": "doneWechatTitle", "label": "成功弹窗里「下一步：加我微信」", "default": "下一步：加我微信", "max": 30},
        {"key": "wechatAddTail", "label": "加上好友之后要做什么（接在加我方式后面）", "default": "加上之后，把上面的时间发我一下就行。", "max": 60},
        {"key": "wechatAddQr", "label": "「怎么加我」：在微信里打开、而且传了二维码", "default": "长按二维码识别加我；识别不出来就点下面的「复制微信号」。", "max": 60},
        {"key": "wechatAddNoQr", "label": "「怎么加我」：在微信里打开、但没传二维码", "default": "搜下面的微信号加我。", "max": 40},
        {"key": "wechatAddBrowser", "label": "「怎么加我」：在浏览器里打开（长按识别不了二维码）", "default": "若网页无法识别二维码，请直接复制我的微信并添加", "max": 60},
        {"key": "doneCloseHint", "label": "锁定成功弹窗最下面那行小字", "default": "要取消或改时间，微信上跟我说一声就行。", "max": 60},
    ]},
    {"id": "common", "name": "④ 页脚与通用", "hint": "每个页面都会用到的那些话（页脚、微信号、接单状态…）。", "fields": [
        {"key": "wechatLabel", "label": "「微信号：」这个前缀（首页、分区页、条款页都用它）", "default": "微信号：", "max": 16},
        {"key": "wechatCopyTip", "label": "微信号旁边的小提示（在微信里打开时）", "default": "长按可复制", "max": 20},
        {"key": "wechatCopyTipBrowser", "label": "微信号旁边的小提示（在浏览器里打开时）", "default": "点一下复制，去微信粘贴", "max": 30},
        {"key": "wechatCta", "label": "「加微信」按钮（首页、分区页各一个）", "default": "加微信", "max": 12},
        {"key": "wechatQrAlt", "label": "二维码图片的说明文字（图片加载失败 / 读屏软件念的就是它）", "default": "我的微信二维码", "max": 30},
        {"key": "footerQrCaption", "label": "页脚二维码下面那行小字", "default": "长按上面的二维码就能加我（部分浏览器支持）", "max": 60},
        {"key": "footerTip", "label": "页脚「加我之后直接说…」那句", "default": "加我之后直接说：哪个游戏 / 哪一档，我来安排。", "max": 60},
        {"key": "footerDisclaimer", "label": "每个页面最下面那行版权说明", "default": "本页为个人服务页面，与游戏官方无隶属关系；游戏素材版权归原权利人所有。", "max": 100},
        {"key": "footerZoneLabel", "label": "分区页页脚「当前页面：」这个前缀", "default": "当前页面：", "max": 16},
        {"key": "footerHintBooking", "label": "分区页页脚那句（陪玩类）", "default": "选好时间点一下就锁上了，我这边立刻收到消息。", "max": 60},
        {"key": "footerHintService", "label": "分区页页脚那句（代肝 / 剪辑类）", "default": "还没想好要哪一档？加我问一句，说清需求和工期就行。", "max": 60},
        {"key": "footerHomeHint", "label": "首页页脚那句（你没填首页副标题时用它）", "default": "陪玩 / 代肝 / 剪辑 / 设计 / 建站 —— 都是我一个人做。", "max": 60},
        {"key": "acceptingOn", "label": "接单状态小字（开着接单时）", "default": "开放接单", "max": 12},
        {"key": "acceptingOff", "label": "接单状态小字（关掉接单时）", "default": "暂时休整", "max": 12},
        {"key": "navTerms", "label": "「服务条款」这个链接的名字（顶部导航 + 页脚都用它）", "default": "服务条款", "max": 20},
        {"key": "termsUpdatedLabel", "label": "条款页上「最后更新：」这个前缀", "default": "最后更新：", "max": 16},
        {"key": "termsBackHome", "label": "条款页底部回主页的链接", "default": "← 回主页看我的服务", "max": 30},
    ]},
    {"id": "empty", "name": "⑤ 空状态（没配好时才出现）", "hint": "这些只在「这一块还没填」的时候才给客户看到，一般保持默认就行。", "fields": [
        {"key": "servicesEmpty", "label": "一个分区都还没有时，首页显示这句", "default": "还没有分区。去后台「分区管理」新建一个，这里就会出现卡片。", "max": 80},
        {"key": "zoneCardDefault", "label": "分区卡片没填「一句话简介」时显示这句", "default": "点开看详情与价目", "max": 40},
        {"key": "priceEmptyBooking", "label": "陪玩分区还没填三档价格时", "default": "这个分区还没有设置报价，去后台加一档。", "max": 60},
        {"key": "priceEmptyService", "label": "服务制分区还没设主推档位时", "default": "还没设置主推档位。去后台「分区管理 → 编辑 → 价目表」勾几个 ⭐ 主推。", "max": 80},
        {"key": "groupsEmpty", "label": "服务制分区还没有价目表时", "default": "还没有价目表。去后台「分区管理 → 编辑 → 价目表」加一组，或者用「批量粘贴导入」。", "max": 80},
        {"key": "promiseEmptyBooking", "label": "陪玩分区没填服务承诺时", "default": "还没有填写服务承诺，去后台这个分区的资料里补上。", "max": 60},
        {"key": "promiseEmptyService", "label": "服务制分区没填服务说明时", "default": "还没有填写服务说明，去后台这个分区的资料里补上。", "max": 60},
        {"key": "wechatEmpty", "label": "后台还没填微信号时的占位", "default": "（还没设置微信号）", "max": 20},
    ]},
]

# 拍平出来的两份表：key → 默认那句话 / key → 长度上限
DEFAULT_TEXTS = {f["key"]: f["default"] for g in TEXT_GROUPS for f in g["fields"]}
TEXT_LIMITS = {f["key"]: int(f.get("max") or 120) for g in TEXT_GROUPS for f in g["fields"]}
TEXTS_STORE_KEY = "texts"        # store 里存"主人改过的那几句"（JSON 对象）

# ============================================================================
# ★ 图片「显示位置」：3×3 九宫格（左上 / 上 / 右上 / 左 / 居中 / 右 / 左下 / 下 / 右下）
#
#   为什么需要它：有些图会被**裁掉一部分**才铺得满（背景图是 background-size: cover，
#   头像和首屏图是 object-fit: cover）。以前客户想让它"露中间那块"，主人只能自己
#   先在 PS 里裁好再传 —— 现在点一下九宫格就行。
#
#   哪些图需要（我自己逐个去看 CSS 确认过的）：
#     · 首页整页背景图（.site-bg：background-size: cover）        → 要
#     · 头像（.avatar：object-fit: cover）                        → 要
#     · 首页首屏主视觉 / 分区首屏主视觉（.hero-media img：object-fit: cover）→ 要
#     · 分区页背景图（.zone-hero-bg：background-size: cover）      → 要
#   哪些图**不需要**（所以后台也没给它们放九宫格）：
#     · 微信二维码（object-fit: contain —— 裁了就有扫不出来的风险，必须完整）
#     · 微信分享图（微信/QQ 自己裁卡片，我们控制不了）
#     · 价目表长图（整张缩放显示，不裁）
#     · 展示墙的图（.gallery-item img 是 170px 高的缩略图，一批图共用一套样式；
#       单张单独调要给它加一列字段 + 一堆 UI，先不做，见交接说明的取舍表）
#
#   存哪：**跟着图片走** —— 首页那几张存在全局资料里（profile），
#   分区那两张存在分区资料里（zones.profile）。这样"保存主页设置 / 保存这个分区"
#   就顺手把它一起存了，而且备份不用改（profile 整个都在备份里）。
#   默认 "center" = 和以前一模一样，所以老数据/没设置过的人看不出任何变化。
POSITION_DEFAULT = "center"
POSITION_CHOICES = [
    ("left top", "左上"), ("center top", "上"), ("right top", "右上"),
    ("left center", "左"), ("center", "居中"), ("right center", "右"),
    ("left bottom", "左下"), ("center bottom", "下"), ("right bottom", "右下"),
]
POSITION_VALUES = [value for value, _name in POSITION_CHOICES]
# 单个关键字也认（"top" = "center top"），省得写接口/写测试的人踩坑
POSITION_ALIASES = {"center center": "center", "top": "center top", "bottom": "center bottom",
                    "left": "left center", "right": "right center"}
PROFILE_POSITION_KEYS = ("avatarPosition", "backgroundPosition", "heroImagePosition")
ZONE_POSITION_KEYS = ("backgroundPosition", "heroImagePosition")

# ============================================================================
# ★ 服务条款（store key='terms'）：/terms 那一页的全部内容
#
#   为什么要有这一页：主人是**个人接单**，客户问"账号安全吗""能退款吗""封号怎么办"
#   总要有个地方能指着看。写在页面上比每次微信里重复解释一遍省事得多，
#   而且能直接发链接（有 og 标签，微信里转发有卡片）。
#
#   语气要求（主人明确说的）：**实在一点，不要写成吓人的法律文书**。
#   所以下面这份默认条款是"一个接单的人在跟客户把话说清楚"的口气：
#   该保护自己的地方写清楚（账号你保管、我只按约定操作、游戏官方的处理我不背），
#   但不堆免责声明。
#
#   结构：{"title": 标题, "updated": "YYYY-MM-DD", "sections": [{"heading","body"}, …]}
#   "updated" 是**你最后一次保存条款那天**（北京时间）自动写上的，不用自己填。
TERMS_STORE_KEY = "terms"
TERMS_TITLE_MAX = 40             # 标题最多几个字
TERMS_HEADING_MAX = 40           # 每节小标题
TERMS_BODY_MAX = 3000            # 每节正文（够写很长一段了）
TERMS_SECTION_MAX = 30           # 最多几节（再多就不是条款是小说了）
DEFAULT_TERMS = {
    "title": "服务条款",
    # updated 出厂是空的：第一次保存时才会写上那天。页面上不显示空日期。
    "updated": "",
    "sections": [
        {"heading": "一、我能提供什么",
         "body": "这里是我一个人的服务页面：陪玩、代肝、视频剪辑 / PS / 设计 / 建站。\n"
                 "所有服务都由我本人完成，没有工作室、没有中间商，你付的钱里没有「转包差价」这一项。\n\n"
                 "页面上的价目是明码标价的：陪玩按小时、代肝按包月或按量、剪辑和设计按项目。\n"
                 "具体到你的这一单，以我们在微信上聊定的为准 —— 页面上写的是常规价格，\n"
                 "特殊情况（加急、超长时间、特殊要求）我会先说清楚再动手。"},
        {"heading": "二、下单与确认",
         "body": "陪玩类：你在页面上挑好时段，点「立即锁定时间」并留下能加上的联系方式，\n"
                 "那几个小时就归你了（别人约不到），之后我会加你确认细节。\n\n"
                 "代肝、剪辑、设计、建站这类：页面上没有时间表，加我微信说清需求，\n"
                 "我报个价，你点头我就开工。\n\n"
                 "有一点请你知道：页面上的「已锁定」是占位，不等于我已经确认接单。\n"
                 "我会尽快（一般当天）通过你留的方式联系你；如果一直联系不上，\n"
                 "那几个小时可能会被放回去，让别人也能约。"},
        {"heading": "三、付款",
         "body": "网站本身不收款，也没有在线支付。付钱只有一种方式：我们在微信上说好，\n"
                 "你直接转给我（或者走闲鱼下单）。\n\n"
                 "什么时候付都行：开工前付、或者这一次服务结束后付，你自己挑，我不催。\n\n"
                 "价格以我们聊定的为准。还没开始服务就想退，全额退给你；\n"
                 "已经做了一部分，就按做过的部分算，剩下的退给你。"},
        {"heading": "四、取消与改期",
         "body": "陪玩：想改时间或者取消，提前 2 小时在微信上跟我说一声就行，\n"
                 "那几格我放回去，你可以重新选（提前说是为了不让那段时间白空着）。\n\n"
                 "代肝 / 剪辑 / 设计：还没开工之前随时可以取消，一分钱不收；\n"
                 "已经开工了，就按做了多少算多少，剩下的退给你。\n\n"
                 "如果是我这边临时有事（生病、设备故障、突发情况），我会第一时间告诉你，\n"
                 "把时间还给你，或者按你的意思顺延 —— 那是我的问题，不会算在你头上。"},
        {"heading": "五、账号安全（请你一定要看完）",
         "body": "需要我登录你的游戏账号时：\n\n"
                 "· 账号和密码请你自己保管，我只在你授权的那段时间里按约定操作\n"
                 "  （做什么、做多少，我们事先说清楚）；\n"
                 "· 我不会改你的密码、不会动你的绑定手机和邮箱、不会把你的账号信息告诉任何人；\n"
                 "· 服务结束后，请你立刻改一次密码（这一步很重要，别嫌麻烦）；\n"
                 "· 进度我可以用截图 / 录屏给你看，包月代肝按约定返图。\n\n"
                 "账号出现异常请第一时间告诉我，我会停下手里的事跟你一起看。"},
        {"heading": "六、责任范围",
         "body": "说得实在一点：\n\n"
                 "· 我按约定操作、按时交付；因我个人原因耽误的时间，我会补回来。\n"
                 "· 游戏本身有随机性（掉线、服务器维护、官方规则调整），这些不在我能控制的范围内。\n"
                 "  我不会用任何外挂、脚本或违反游戏规则的方式；\n"
                 "  但如果账号是因为别处的违规行为被官方处理，那部分不在我这边。\n"
                 "· 陪玩不等于「保证上分」：我会认真陪你打，但不承诺每一局的结果。\n"
                 "· 遇到不可抗力（大面积断网、平台故障）当天做不完，我们约个时间顺延，\n"
                 "  或者把没做的部分退给你。\n"
                 "· 我能承担的上限就是这一单本身的费用 —— 我不希望你因为一次陪玩\n"
                 "  承担超出这个范围的东西，反过来我也一样。"},
        {"heading": "七、怎么找我",
         "body": "微信：{wechat}（页面顶部的「加微信」点一下就能复制）。\n\n"
                 "急事直接微信上说，我看到了都会回；白天可能在打单，回得慢一点，别急。\n\n"
                 "这一页有更新时我会改这里并写上日期，继续用我的服务就视为你认可以上的约定。"},
    ],
}

# 分区资料里"允许出现"的字段（保存时按这份白名单清洗，避免奇怪的键越存越多）
ZONE_TEXT_KEYS = {
    "game": 30,            # 游戏名 / 服务名（预约文案里要用："想约守望先锋 PC 端陪玩"）
    "tagline": 60,         # 一句话简介
    "headline": 80,        # 首屏大标题（\n 换行）
    "subheadline": 120,    # 副标题
    "bio": 1200,           # 详细介绍
    "announcement": 200,   # 该分区的公告
    "platform": 30,        # 接单平台
}
ZONE_IMAGE_KEYS = ("background", "heroImage", "shareImage", "priceSheetImage")
ZONE_LIST_KEYS = ("highlights", "promises")     # 字符串数组（highlights 就是原来的 styles）
ZONE_ITEM_LIMIT = 60                            # 一个分区最多多少个价目条目（防止有人贴几万行进来）

# 首次启动时新建分区用的默认资料模板（每一项在后台都能改）
DEFAULT_ZONE_PROFILE = {
    "game": "",                      # 游戏名 / 服务名；留空的话预约文案会自动省略
    "tagline": "",
    "headline": "一起出发。\n尽兴而归。",
    "subheadline": "",
    "bio": "",
    "highlights": [],                # 服务亮点 / 打法标签（原来的 styles）
    "promises": [],                  # 服务承诺（原来的服务说明）
    "announcement": "",
    "platform": "PC 电脑端",
    "background": "",                # 该分区自己的背景图
    "heroImage": "",                 # 该分区自己的首屏主视觉（建议 PNG 透明底）
    "shareImage": "",                # 该分区自己的微信分享缩略图（1200×630）
    "priceSheetImage": "",           # 价目表长图（服务型分区用）
    "backgroundDim": 72,             # 背景压暗程度（0-90）
    # ★ 图片「显示位置」（3×3 九宫格）：分区这一套只管它自己的两张会裁的图
    "backgroundPosition": "center",  # 分区背景图（background-size: cover）
    "heroImagePosition": "center",   # 分区首屏主视觉（object-fit: cover）
    "prices": [],                    # 只有 kind=booking 的分区用：三档价格
    "priceGroups": [],               # 只有 kind=service 的分区用：分组价目表
    "featured": [],                  # 「主推档位」：从 priceGroups 里挑出来的 item id（最多 3 个）
}

# 迁移一个**全新站点**时，给第一个分区（delta）预置的三档价格 ——
# 这样装好就能直接开张，不用先去后台填价格；老站点则用原来的 prices 覆盖它。
# 注意：这里只是"新站的起点"，后台改掉之后就完全由后台说了算。
STARTER_ZONE_PRICES = [
    {"id": "dam", "name": "机密 · 零号大坝", "description": "轻装出发，熟悉每一条撤离路线。", "price": 15},
    {"id": "confidential", "name": "机密行动", "description": "打法听你的，一起把这局打漂亮。", "price": 20},
    {"id": "top-secret", "name": "绝密行动", "description": "高压战场，配合与陪伴始终在线。", "price": 25},
]

# 迁移一个全新站点时，第一个分区预置的打法标签和服务承诺
STARTER_ZONE_HIGHLIGHTS = ["猛攻", "跑刀", "玻璃大炮", "堵桥", "陪做任务", "熟图", "轻松聊天"]
STARTER_ZONE_PROMISES = [
    "以开第一局游戏为起点计时，时间打满；因我个人原因耽误的时间会补回。",
    "哪怕已经玩了 59 分钟，只要未满 1 小时，仍可再开一局，这一局超时不加价。",
    "同一订单 1 陪 1 或 1 陪 2，不额外收取人数差价。",
    "优先提供愉快的陪伴体验，尽力协助撤离；每局具有随机性，不承诺百分百撤离。",
    "设备无问题，可随时视频或截图验证。",
]

# 分区 id 的格式：英文小写字母/数字开头，后面可以跟小写字母/数字/连字符，最长 31 位。
# 它就是要发给客户的网址的一部分（/z/delta），所以不允许中文、大写、下划线。
ZONE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,30}$")

# 默认主色（分区没设 theme 时用它）。深色底 + 荧光绿，和 style.css 里的 --accent 一致。
DEFAULT_THEME = "#c6f04a"

# 服务型分区"起步价"的兜底说明（见 zone_start_price()）
PRICE_NUM_RE = re.compile(r"\d+(?:\.\d+)?")

# ★ 「金额"文字" → 数字」用的正则（见 parse_amount）。
#   只在**要算钱**的地方用（收入流水），绝不回头改客户档案里那串自由文本。
AMOUNT_NUM_RE = re.compile(r"\d+(?:\.\d+)?")

# 一笔收入最多多少钱（元）。只是个上限，防止手滑多打几个 0 把统计撑爆
PAYMENT_AMOUNT_MAX = 999999


def parse_amount(text):
    """
    ★ 从"人类写的价格文本"里把数字抠出来：`20R/月` → 20.0、`推图15R` → 15.0、
    `￥200` → 200.0、`100 元` → 100.0。**抠不到就返回 None**（不是 0！）。

    为什么需要它：客户档案里的金额是**自由文本**（主人想写什么就写什么，一个字都不能改），
    但"这个月收了多少钱"必须是个数字才能加总。所以统计另走一套：把文本里的数字解析出来，
    存进 payments 表（见「二之五」那一章）。**原始文本永远留在 customers.price 里，不会被动。**

    几条规则（都是照着主人真实的写法定的）：

      · `"20R/月"` → 20     —— 最典型的写法，"R"当人民币符号
      · `"推图15R"` → 15    —— 数字可以夹在中文里
      · `"100R/月（含月卡5R）"` → 100  —— **只取第一个数字**（后面那个 5R 是附加说明，
                                          不是这一单的钱；取第一个才不会把 100 算成 105）
      · `"8R/周"` → 8       —— 周期单位不影响数字（周单价就是这个数）
      · `"按需报价"` / `""` / `None` → **None** —— 抠不出数字 = "算不出钱"，
                                          宁可返回 None，也不要假装是 0
      · `"0"` / `"0R"` → 0.0 —— 0 是一个合法的数字，所以照实返回 0.0。
                                          但这**不等于**会记一笔 0 元流水：真正记账那里
                                          （renew_customer / record_booking_payment）
                                          有 `amount > 0` 的判断，0 元一律不记。

    返回 float 或 None。**不做四舍五入**（round 交给写库的 clean_payment 统一做）。
    """
    m = AMOUNT_NUM_RE.search(str(text if text is not None else ""))
    if not m:
        return None
    try:
        return float(m.group())
    except ValueError:                 # pragma: no cover - 正则已经保证是数字，兜底而已
        return None


def clean_amount(value):
    """
    把一个"可能是数字、也可能是文字"的输入变成干净的金额数字。返回 float 或 None。

    用在哪：手动记账 / 续费记账 / 恢复备份。规矩：
      · 数字（含 "100" 这种字符串）→ 直接用；
      · 别的文字 → 交给 parse_amount() 抠数字（这样手动记账那格填 "100R" 也能认）；
      · 抠不出来 / 是负数 / 大得离谱 → 返回 None，让调用方回一句中文错误。

    ⚠️ 返回 None 和返回 0 是两回事：None = "这不是一笔钱"；0 = "确实是 0 元"。
    """
    if value is None:
        return None
    if isinstance(value, bool):        # True/False 是 int 的子类，别让它变成 1/0
        return None
    if isinstance(value, (int, float)):
        amount = float(value)
    else:
        text = str(value).strip()
        if not text:
            return None
        amount = parse_amount(text)
        if amount is None:
            return None
    if amount < 0 or amount > PAYMENT_AMOUNT_MAX:
        return None
    return round(amount, 2)


def clean_link(raw):
    """
    ★ 清洗展示墙的「外链」：**只接受 http:// 或 https:// 开头的地址，其余一律存空字符串。**

    为什么必须卡这一道（防 XSS，这是安全底线，不是洁癖）：
      展示墙的链接最后会变成前台页面上的 <a href="……" target="_blank">。
      如果有人往这个字段里塞 `javascript:alert(1)`，那么**将来任何一个访客点一下那张图，
      就会在那个页面上执行别人写的代码**（能偷 Cookie、能假装成网站弹"请重新登录"骗密码、
      能跳去钓鱼页）。同理 `data:text/html;base64,…` 也是一样能当代码跑的地址。
      这两种写法浏览器都认，所以"看着像网址"不等于安全 —— 只能按**协议白名单**卡死：
      开头必须是 http:// 或 https://，别的（包括 javascript: / data: / vbscript: /
      file: / 相对路径 / 空的）统统存成空字符串，前台就当成"这张图没有外链"。

    另外两点也是故意的：
      · 用 .strip() 去掉首尾空格（手机上粘贴很容易带一个空格，带空格浏览器就点不动了）；
      · 长度截到 GALLERY_LINK_MAX，免得有人塞一条 10 万字的"链接"进来。

    返回字符串（可能是空的）。
    """
    text = str(raw or "").strip()
    if not text:
        return ""
    lowered = text.lower()
    if not (lowered.startswith("http://") or lowered.startswith("https://")):
        return ""
    return text[:GALLERY_LINK_MAX]

def clean_image_position(value):
    """
    ★ 把「图片显示位置」洗成 9 个合法值之一（左上 / 上 / 右上 / 左 / 居中 / 右 / 左下 / 下 / 右下）。

    返回的是**能直接写进 CSS 的字符串**（"left top"、"center"…），前台拿到就塞进
    `background-position` / `object-position`，不做任何转换 —— 少一层转换就少一处能对不上的地方。

    认不出来的一律退回 "center"，这一点是故意的：
      · "center" 就是这一版之前的行为（CSS 里本来就写着 background-position: center），
        所以老数据、脏数据、有人手改过数据库 —— 最坏的结果也只是"回到原来的观感"，不会变形。
      · 另外顺手认几个简写（"top" = "center top"），省得写接口、写测试的人踩坑。

    用在哪：get_profile()（首页那三张）和 clean_zone_profile()（分区那两张）读取时各洗一遍，
    所以**库里存了脏值也不怕** —— 读出来永远是合法的 9 个值之一。
    """
    text = " ".join(str(value or "").strip().lower().split())
    text = POSITION_ALIASES.get(text, text)
    return text if text in POSITION_VALUES else POSITION_DEFAULT


def clean_texts(raw):
    """
    ★ 把前端传来的文案洗一遍：只认目录里有的 key，去首尾空白、按各自的上限截断。

    **空字符串（或者填得和默认一模一样）直接丢掉 = "这一句用默认"**。
    为什么"和默认一样也不存"：后台的输入框里预填的就是当前生效的那句话，
    如果原样存下来，主人只是点开看一眼再保存，所有句子就被"钉死"成今天这版默认值了 ——
    以后默认值改好了（比如换了更好的说法）他那边反而收不到。只存真正改过的，才符合直觉。

    第 2 条约定也在这一层守住：**文案是纯文本**，这里不做任何 HTML 处理（前台会转义）。
    返回 {key: 文案}，可以直接 json.dumps 进 store。
    """
    raw = raw if isinstance(raw, dict) else {}
    out = {}
    for key, default in DEFAULT_TEXTS.items():
        if key not in raw:
            continue
        text = str(raw.get(key) or "").strip()[:TEXT_LIMITS[key]]
        if not text or text == default:
            continue
        out[key] = text
    return out

os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(UPLOAD_DIR, exist_ok=True)


# ============================================================================
# 二、数据库：7 张表
#   store    键值仓库（全局资料、大类列表、展示墙分类、密码、通知配置…）
#   locks    被锁定的时段（★ 带 confirmed：客户自助预约先算"待确认"）
#   rules    固定休息规则
#   gallery  展示墙（★ 带 category 分类 id + link 外链）
#   zones    ★ 分区（陪玩/代肝/剪辑…每个分区一行，资料存在 profile 字段里）
#   customers ★ 客户档案（包月代肝哪天到期、该催谁续费 —— 见「二之四」那一章）
#   payments ★ 收入流水（钱从哪来、该推什么 —— 见「二之五」那一章）
#
#   ⚠️ payments 是全站**唯一**要求"金额是数字"的地方：
#      客户档案的 price 和价目表的价格都是自由文本（"20R/月"），照旧不动；
#      只有要加总、要排行的时候，才另存一个数字（见 parse_amount）。
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
    created_at INTEGER DEFAULT 0,                   -- 下单时间（毫秒时间戳；老数据是 0）
    confirmed  INTEGER DEFAULT 1,                   -- ★ 1=已确认（默认）/ 0=待确认（客户自助预约先存这个）
    confirmed_at INTEGER DEFAULT 0                  -- ★ 老板点「已确认」的时间（毫秒；没确认过是 0）
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
    category   TEXT DEFAULT 'screenshot',       -- 分类 id（默认三类：screenshot/review/work，可在后台改）
    image      TEXT DEFAULT '',                 -- /api/media/xxx.webp
    visible    INTEGER DEFAULT 0,               -- 是否在首页公开
    archived   INTEGER DEFAULT 0,               -- 是否归档（不在后台主列表显示）
    created_at INTEGER NOT NULL,
    zone       TEXT DEFAULT '',                 -- 这张图出现在哪个分区页
    link       TEXT DEFAULT ''                  -- ★ 外链（B 站视频 / 自己做的网站），只能 http(s)
);
CREATE TABLE IF NOT EXISTS zones (              -- ★ 分区：一个分区 = 一个网址（/z/delta）
    id         TEXT PRIMARY KEY,                -- 短标识，网址里用：英文小写/数字/连字符
    category   TEXT NOT NULL,                   -- → categories.id（陪玩 / 代肝 / 其他服务）
    name       TEXT NOT NULL,                   -- 全称：三角洲行动
    short      TEXT DEFAULT '',                 -- 简称（顶部标签用）：三角洲
    icon       TEXT DEFAULT '',                 -- 分区图标（/api/media/xxx）
    theme      TEXT DEFAULT '',                 -- 主色 #rrggbb（空则用 DEFAULT_THEME）
    sort       INTEGER DEFAULT 0,               -- 排序（越小越靠前）
    visible    INTEGER DEFAULT 1,               -- 是否出现在前台（隐藏后 /z/<id> 仍可直接访问）
    profile    TEXT DEFAULT '{}',               -- ★ 该分区自己的资料 JSON（见 DEFAULT_ZONE_PROFILE）
    created_at INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS customers (          -- ★ 客户档案 + 到期提醒（见「二之四」那一章）
    id           TEXT PRIMARY KEY,              -- uuid4().hex（内部用，不显示给客户）
    name         TEXT NOT NULL,                 -- 客户称呼（微信昵称就行，如"小明"）
    contact      TEXT DEFAULT '',               -- 联系方式（微信号 / QQ 号 / 闲鱼号）
    contact_type TEXT DEFAULT 'wechat',         -- 渠道：wechat 微信 / qq QQ / xianyu 闲鱼
    zone         TEXT DEFAULT '',               -- 关联哪个分区（如 arknights）；空 = 不关联
    item         TEXT DEFAULT '',               -- 买的档位（自由文本，如"精托托管"）
    price        TEXT DEFAULT '',               -- ★ 金额是**自由文本**！保留"20R/月""推图15R"的写法
    start_date   TEXT DEFAULT '',               -- 开始日期 "YYYY-MM-DD"（可以为空）
    end_date     TEXT DEFAULT '',               -- 到期日期 "YYYY-MM-DD"；★空 = 一次性/长期，不算到期
    status       TEXT DEFAULT 'active',         -- active 进行中 / paused 已暂停 / ended 已结束
    note         TEXT DEFAULT '',               -- 备注（账号情况、特殊要求、返图时间…）
    created_at   INTEGER DEFAULT 0,             -- 建档时间（毫秒时间戳）
    updated_at   INTEGER DEFAULT 0              -- 最后一次改动时间（毫秒时间戳）
);
CREATE TABLE IF NOT EXISTS payments (           -- ★ 收入流水（见「二之五」那一章）
    id          TEXT PRIMARY KEY,               -- uuid4().hex（内部用）
    date        TEXT NOT NULL,                  -- 收款日期 "YYYY-MM-DD"（★ 北京时间的那一天）
    amount      REAL NOT NULL,                  -- ★★ 金额是**数字**（全站唯一一个必须数字化的地方）
                                                --    因为要加总、要排行 —— "20R/月"这种文本没法算钱
    zone        TEXT DEFAULT '',                -- 哪个分区 / 服务（zones.id）；空 = 没归类
    item        TEXT DEFAULT '',                -- 项目 / 档位名（如"精托托管"、"机密·零号大坝"）
    customer    TEXT DEFAULT '',                -- 客户称呼（客户档案被删了也还留着他叫什么）
    customer_id TEXT DEFAULT '',                -- 关联客户档案 id（可选，用来做"谁最舍得"的排行）
    source      TEXT DEFAULT 'manual',          -- booking=确认预约自动记 / renew=续费自动记 /
                                                -- manual=手动补记（剪辑、PS、建站这类一次性单子）
    ref         TEXT DEFAULT '',                -- 关联单据（锁 id / 客户id+新到期日），★用来防重复记账
    note        TEXT DEFAULT '',                -- 备注
    created_at  INTEGER DEFAULT 0               -- 这条流水是什么时候写进来的（毫秒；和 date 不是一回事）
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
            ("zone", "TEXT DEFAULT ''"),            # ★ 这单是哪个分区的（见第五章迁移）
            # ★ 「待确认」用的两列。
            #   ⚠️ confirmed 的默认值**必须是 1**：老库里那些锁全是老板自己弄的
            #   （或者升级前客户自助预约的），不该在升级的那一刻突然集体变成"待确认"
            #   —— 那会让后台一打开就跳出几十条待办，把真正要处理的淹掉。
            #   只有**新提交的**客户自助预约才会显式写成 confirmed=0（见 create_booking）。
            ("confirmed", "INTEGER DEFAULT 1"),
            ("confirmed_at", "INTEGER DEFAULT 0"),
        ) if ensure_column(conn, "locks", col, decl)]
        if added:
            print("[迁移] locks 表补上了新字段：%s（老数据没动）" % "、".join(added), flush=True)
        # gallery 表也要补 zone（该图属于哪个分区）
        if ensure_column(conn, "gallery", "zone", "TEXT DEFAULT ''"):
            print("[迁移] gallery 表补上了 zone 字段（老图默认归第一个分区）", flush=True)
        # gallery 表补 link（★ 展示墙的外链：B 站视频地址 / 自己做的网站）
        if ensure_column(conn, "gallery", "link", "TEXT DEFAULT ''"):
            print("[迁移] gallery 表补上了 link 字段（展示墙外链，老的图都是空的）", flush=True)
        if conn.execute("SELECT 1 FROM store WHERE key='profile'").fetchone() is None:
            conn.execute("INSERT INTO store(key, value) VALUES('profile', ?)",
                         (json.dumps(DEFAULT_PROFILE, ensure_ascii=False),))
            conn.execute("INSERT INTO store(key, value) VALUES('revision', '1')")
        if conn.execute("SELECT 1 FROM store WHERE key='password'").fetchone() is None:
            conn.execute("INSERT INTO store(key, value) VALUES('password', ?)",
                         (hash_password(DEFAULT_PASSWORD),))
    # ★ 两级分区：建大类 + 把"单游戏时代"的老资料搬进第一个分区（只做一次）
    migrate_to_zones()


# ============================================================================
# 二之二、资料（全局）：只放跨分区共用的字段
# ============================================================================

def get_profile():
    with connect() as conn:
        row = conn.execute("SELECT value FROM store WHERE key='profile'").fetchone()
        profile = json.loads(row["value"]) if row else dict(DEFAULT_PROFILE)
    merged = dict(DEFAULT_PROFILE)
    merged.update(profile)          # 老数据缺字段时用默认值补齐
    # ★ 三个「图片显示位置」读出来就洗一遍：后台的九宫格、前台的内联样式、备份
    #   走的都是这一份数据，所以只要在这里守住，下面就不可能拿到非法值。
    for key in PROFILE_POSITION_KEYS:
        merged[key] = clean_image_position(merged.get(key))
    return merged


def site_summary():
    """
    给前台用的"全局资料"精简版（首页、分区页、条款页都要它）。
    故意只挑这几个字段：客户信息里只该出现品牌、昵称、微信、头像、信任信号。

    ★ 这里还多带两样"前台每一页都要用"的东西：
      · texts          —— 客户能看到的每一句中文（默认值已经补齐，见 get_texts）；
      · imagePositions —— 首页三张图的显示位置（九宫格选出来的那个值）。
    """
    p = get_profile()
    return {
        "brand": str(p.get("brand") or DEFAULT_PROFILE["brand"]),
        "name": str(p.get("name") or ""),
        "tagline": str(p.get("tagline") or ""),
        "wechat": str(p.get("wechat") or ""),
        "xianyu": str(p.get("xianyu") or ""),
        "avatar": str(p.get("avatar") or ""),
        "bio": str(p.get("bio") or ""),
        "trust": [str(t) for t in (p.get("trust") or []) if str(t).strip()],
        "wechatQr": str(p.get("wechatQr") or ""),
        "services_intro": str(p.get("services_intro") or ""),
        "accepting": bool(p.get("accepting", True)),
        "background": str(p.get("background") or ""),
        "backgroundDim": int(p.get("backgroundDim") or 0),
        # ★ 下面这两样是"首页、分区页、条款页都要用"的（文案表 / 图片显示位置）。
        #   ⚠️ 为什么把它们放在中间、而不是放在最后：__PAGE__ 那段 JSON 是**嵌套**的，
        #   如果一个嵌套对象正好是最后一个键，序列化出来就会出现 "}}" ——
        #   而 tests\test_zones.py 有一条老断言在查"首页 HTML 里不该有 {{ 或 }} 残留"
        #   （它防的是模板占位符漏渲染）。嵌套对象后面跟一个字符串字段就永远不会撞上。
        "texts": get_texts(),
        # 首页那三张图的显示位置（键名和 profile 里的字段一一对应）
        "imagePositions": {
            "avatar": p["avatarPosition"],
            "background": p["backgroundPosition"],
            "heroImage": p["heroImagePosition"],
        },
        "heroImage": str(p.get("heroImage") or ""),      # 主页首屏主视觉（选填，独立于任何分区）
    }


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


# ============================================================================
# 二之三、分区与大类（★ 这次升级的核心）
#
#   大类 categories：陪玩（booking）/ 代肝（service）/ 其他服务，存在 store 里，不能删
#   分区 zones     ：三角洲行动、守望先锋、明日方舟……一行一个，后台自助增删改
#
#   kind="booking" 的分区 → 前台显示预约时间表，用扁平 prices（三档）
#   kind="service" 的分区 → 前台不显示时间表，用分组价目表 priceGroups
#
#   ⚠️ **时间表全局共用**：locks 表里没有"分区"这个维度上的隔离，
#      客户的预约记录只带一个 zone 字段用来"标记这单是哪个分区的"，
#      但格子的占用状态是全局的 —— 守望的客户和三角洲的客户抢的是同一批小时。
# ============================================================================


def clean_categories(items):
    """把大类数组洗干净（id 不能空、kind 只能是 booking/service、sort 是数字）。"""
    out, seen = [], set()
    for i, c in enumerate(items or []):
        if not isinstance(c, dict):
            continue
        cid = str(c.get("id") or "").strip()[:32]
        if not cid or cid in seen:
            continue
        seen.add(cid)
        kind = "service" if c.get("kind") == "service" else "booking"
        hint = str(c.get("hint") or "").strip()[:20]
        if not hint:      # 老数据没这个字段：按 id 查默认值，再按类型兜底
            hint = DEFAULT_CATEGORY_HINTS.get(cid) or ("按小时预约" if kind == "booking" else "按需报价")
        out.append({
            "id": cid,
            "name": str(c.get("name") or cid)[:20],
            "hint": hint,
            "kind": kind,
            "sort": int(c.get("sort") or (i + 1)),
        })
    return out


def list_categories():
    """读大类列表。数据库里没存过 / 存坏了，就回退到默认三档（永远不返回空列表）。"""
    raw = get_store("categories")
    if raw:
        try:
            data = json.loads(raw)
        except ValueError:
            data = None
        if isinstance(data, list):
            out = clean_categories(data)
            if out:
                out.sort(key=lambda c: (c["sort"], c["id"]))
                return out
    return [dict(c) for c in DEFAULT_CATEGORIES]


def category_map():
    return {c["id"]: c for c in list_categories()}


def category_kind(category_id):
    """这个大类是预约制（booking）还是服务制（service）？认不出来就按预约制处理。"""
    return (category_map().get(category_id) or {}).get("kind", "booking")


def set_categories(items):
    set_store("categories", json.dumps(items, ensure_ascii=False))


# ---------- ★ 展示墙的分类（和上面的"大类"是一个套路，只是存另一个 key）----------
#
#   为什么单独一套：大类决定"要不要时间表"，展示墙分类只决定"这张图算哪一类"，
#   两者互不相干 —— 一个分区可以有游戏截图、老板评价、作品三种图。
#
#   ⚠️ 两条不能改错的规矩：
#     1. **id 不许改**（改名可以）：老图的 category 字段存的就是 id，
#        改了 id 那些图就"对不上号"了，只能靠前台兜底显示，体验很差；
#     2. **不允许删掉"还有图片在用"的分类** —— 见 admin_gallery_categories 里的取舍说明。

def clean_gallery_categories(items):
    """
    把展示墙分类数组洗干净（照抄 clean_categories 的写法）：
      · id 必须符合 GALLERY_CAT_ID_RE（英文小写字母/数字/连字符，字母数字开头，≤21 位）
      · id 不能重复（重复的直接丢掉后面那条，不报错）
      · name 为空就用 id 顶上（宁可显示得难看，也不能让分类没有名字）
      · sort 是数字，没给就按数组顺序 1、2、3…
    返回一份"字段一定齐全"的新数组（不改原数组）。
    """
    out, seen = [], set()
    for i, c in enumerate(items or []):
        if not isinstance(c, dict):
            continue
        cid = str(c.get("id") or "").strip().lower()
        if not GALLERY_CAT_ID_RE.match(cid or "") or cid in seen:
            continue
        seen.add(cid)
        try:
            sort = int(c.get("sort") or (i + 1))
        except (TypeError, ValueError):
            sort = i + 1
        out.append({
            "id": cid,
            "name": str(c.get("name") or "").strip()[:20] or cid,
            "sort": sort,
        })
    return out


def list_gallery_categories():
    """
    读展示墙分类列表。数据库里没存过 / 存坏了 / 洗完之后一条不剩，
    就回退到默认三类（游戏截图 / 老板评价 / 作品）—— **永远不返回空列表**。
    """
    raw = get_store("gallery_categories")
    if raw:
        try:
            data = json.loads(raw)
        except ValueError:
            data = None
        if isinstance(data, list):
            out = clean_gallery_categories(data)
            if out:
                out.sort(key=lambda c: (c["sort"], c["id"]))
                return out
    return [dict(c) for c in DEFAULT_GALLERY_CATEGORIES]


def gallery_category_map():
    return {c["id"]: c for c in list_gallery_categories()}


def set_gallery_categories(items):
    set_store("gallery_categories", json.dumps(items, ensure_ascii=False))


def suggest_gallery_category_id(name, known=None):
    """
    新增分类时，从名字里猜一个能用的 id（和 admin.js 的 suggestZoneId 一个思路）。

    中文名字猜不出英文（"作品" → 猜不出来），所以猜不出来就退回 g1 / g2 / g3 这种编号 ——
    **id 只是内部对号用的，真正显示给客户看的是 name**，所以编号完全够用。
    主人想自己填 id 也行（新增分类的接口认 body 里的 id，只要格式对、不重复）。
    """
    known = set(known or (c["id"] for c in list_gallery_categories()))
    ascii_id = re.sub(r"[^a-z0-9-]", "", str(name or "").lower()).strip("-")
    if ascii_id and GALLERY_CAT_ID_RE.match(ascii_id[:21]) and ascii_id[:21] not in known:
        return ascii_id[:21]
    i = 1
    while ("g%d" % i) in known:
        i += 1
    return "g%d" % i


def gallery_category_counts():
    """每个分类下有多少张图（**只算没归档的**，和后台列表显示的口径一致）。"""
    counts = {c["id"]: 0 for c in list_gallery_categories()}
    for item in list_gallery(archived_only=False):
        cid = item.get("category") or ""
        counts[cid] = counts.get(cid, 0) + 1
    return counts


def gallery_categories_for_public():
    """
    前台筛选按钮用的分类列表。

    ⚠️ 兜底（老数据兼容）：如果某张图的 category **不在配置里**（比如配置被改过、
    或者图是很早以前用别的 id 存的），就把它临时补进列表末尾显示出来，
    名字直接用那个 id —— **宁可难看也不能让图"人间蒸发"**。
    完全没人用的历史分类不会显示（否则筛选栏会越用越长）。
    """
    cats = list_gallery_categories()
    known = {c["id"] for c in cats}
    extra = []
    for item in list_gallery(only_public=True):
        cid = str(item.get("category") or "").strip()
        if cid and cid not in known and cid not in extra:
            extra.append(cid)
    for i, cid in enumerate(extra):
        cats.append({"id": cid, "name": cid, "sort": 1000 + i})
    return cats


# ---------- 分区资料的清洗 ----------

def _clean_text_list(value, limit=12, each=80):
    """把 ["a","b"] 这种数组洗干净：去空行、去首尾空格、截断、最多 limit 条。"""
    if isinstance(value, str):
        value = re.split(r"[\n，,、]+", value)
    out = []
    for item in (value or []):
        text = str(item or "").strip()[:each]
        if text and text not in out:
            out.append(text)
        if len(out) >= limit:
            break
    return out


def clean_zone_profile(raw):
    """
    把前端传来的分区资料洗干净（白名单 + 长度 + 类型）。
    返回一份"字段一定齐全"的资料，这样前台永远不用做空值判断。
    """
    raw = raw if isinstance(raw, dict) else {}
    out = dict(DEFAULT_ZONE_PROFILE)
    for key, limit in ZONE_TEXT_KEYS.items():
        out[key] = str(raw.get(key) or "")[:limit]
    for key in ZONE_IMAGE_KEYS:
        out[key] = str(raw.get(key) or "")[:300]
    out["highlights"] = _clean_text_list(raw.get("highlights"), limit=12, each=20)
    out["promises"] = _clean_text_list(raw.get("promises"), limit=12, each=200)
    # ★ 图片显示位置：不是截断就行，得按 9 个合法值校验（认不出来退回 center）
    for key in ZONE_POSITION_KEYS:
        out[key] = clean_image_position(raw.get(key))
    try:
        out["backgroundDim"] = max(0, min(90, int(raw.get("backgroundDim", 72))))
    except (TypeError, ValueError):
        out["backgroundDim"] = 72

    # ★ 三档价格：只有预约制分区用。price 存的是数字（元），前台显示 "¥15/小时"。
    prices = []
    for i, p in enumerate(raw.get("prices") or []):
        if not isinstance(p, dict):
            continue
        name = str(p.get("name") or "").strip()[:30]
        if not name:
            continue
        try:
            amount = float(p.get("price") or 0)
        except (TypeError, ValueError):
            amount = 0
        prices.append({
            "id": str(p.get("id") or ("p%d" % (i + 1)))[:24],
            "name": name,
            "description": str(p.get("description") or "")[:80],
            "price": max(0, min(99999, int(round(amount)))),
        })
        if len(prices) >= ZONE_ITEM_LIMIT:
            break
    out["prices"] = prices

    # ★ 分组价目表：只有服务制分区用。价格是**自由文本**（"20R/月"、"推图15R"），
    #   一个字都不要动 —— 主人就是这么写的，改成 ¥ 反而不对。
    groups, item_ids = [], []
    for gi, g in enumerate(raw.get("priceGroups") or []):
        if not isinstance(g, dict):
            continue
        gname = str(g.get("name") or "").strip()[:30]
        if not gname:
            continue
        items = []
        for ii, it in enumerate(g.get("items") or []):
            if not isinstance(it, dict):
                continue
            iname = str(it.get("name") or "").strip()[:40]
            if not iname:
                continue
            iid = str(it.get("id") or ("g%di%d" % (gi + 1, ii + 1)))[:24]
            items.append({
                "id": iid,
                "name": iname,
                "price": str(it.get("price") or "")[:30],       # 自由文本，原样保留
                "note": str(it.get("note") or "")[:300],
                "hot": bool(it.get("hot")),
            })
            item_ids.append(iid)
            if len(items) >= ZONE_ITEM_LIMIT:
                break
        groups.append({"id": str(g.get("id") or ("g%d" % (gi + 1)))[:24],
                       "name": gname, "note": str(g.get("note") or "")[:120], "items": items})
        if len(groups) >= 20:
            break
    out["priceGroups"] = groups

    # 「主推档位」：最多 3 个，而且必须是真实存在的 item id（旧的会自动丢掉）
    featured = []
    for fid in (raw.get("featured") or []):
        fid = str(fid or "").strip()
        if fid and fid in item_ids and fid not in featured:
            featured.append(fid)
        if len(featured) >= 3:
            break
    # 一个都没指定时，默认挑前三项里标了 ⭐ 的（主人勾了"主推"却忘了选，也别让首屏空着）
    if not featured and item_ids:
        hot = [it["id"] for g in groups for it in g["items"] if it["hot"]]
        featured = (hot or item_ids)[:3]
    out["featured"] = featured
    return out


def merge_zone_profile(zone_profile):
    """把库里存的（可能是老结构、缺字段的）分区资料补齐成完整结构。"""
    return clean_zone_profile(zone_profile)


def zone_start_price(zone):
    """
    算"起步价"文案（首页服务卡片上那行小字）。规则写在这里，前台不再自己算：

      · 预约制（kind=booking）：取 profile.prices 里**最小的数字** → "¥15/小时起"
        （价格本来就是数字，直接比大小；一档都没有就返回空字符串，卡片上不显示）

      · 服务制（kind=service）：价格是自由文本（"20R/月"、"推图15R"），没法可靠地比大小，
        所以规则是：**按"组 → 项"的顺序找第一个价格文本**，并补一个"起"字 → "20R/月起"。
        （主人把最便宜的那档放在最前面即可。）

        两个小改进，都是为了让"8R/周"这种更便宜但周期更短的项目不抢戏：
          1. 先看带"月"的（月付/包月最常见），有就优先在它们里选；
          2. 同一批里按文本里第一个数字升序，数字最小的排前面 ——
             这样即使主人把顺序放反了，显示的也尽量是最便宜的那一档。

    返回字符串（可能为空）。
    """
    profile = merge_zone_profile(zone.get("profile"))
    kind = category_kind(zone.get("category"))
    if kind == "booking":
        nums = [int(p.get("price") or 0) for p in profile["prices"]]
        nums = [n for n in nums if n > 0]
        return ("¥%d/小时起" % min(nums)) if nums else ""
    texts = []
    for g in profile["priceGroups"]:
        for it in g["items"]:
            text = str(it.get("price") or "").strip()
            if text:
                texts.append(text)
    if not texts:
        return ""

    def sort_key(text):
        hit = PRICE_NUM_RE.search(text)
        return (0 if "月" in text else 1,                      # 月付优先
                float(hit.group()) if hit else float("inf"))    # 再按数字大小

    first = sorted(texts, key=sort_key)[0]
    return first + ("" if first.endswith("起") else "起")


# ---------- 分区的读 ----------

def _row_to_zone(row):
    return {
        "id": row["id"],
        "category": row["category"],
        "name": row["name"],
        "short": row["short"] or row["name"],
        "icon": row["icon"] or "",
        "theme": row["theme"] or "",
        "sort": int(row["sort"] or 0),
        "visible": bool(row["visible"]),
        "profile": merge_zone_profile(_json_or(row["profile"], {})),
        "created_at": int(row["created_at"] or 0),
    }


def _json_or(raw, default):
    try:
        data = json.loads(raw) if raw else default
    except ValueError:
        return default
    return data if isinstance(data, type(default)) else default


def list_zones(only_visible=False):
    """按 sort 排好的分区列表（含隐藏的，后台要看到全部）"""
    sql = "SELECT * FROM zones"
    if only_visible:
        sql += " WHERE visible=1"
    sql += " ORDER BY sort, id"
    with connect() as conn:
        rows = conn.execute(sql).fetchall()
    return [_row_to_zone(r) for r in rows]


def get_zone(zone_id):
    if not zone_id:
        return None
    with connect() as conn:
        row = conn.execute("SELECT * FROM zones WHERE id=?", (zone_id,)).fetchone()
    return _row_to_zone(row) if row else None


def default_zone():
    """
    默认分区：第一个"可见"的分区；一个都没有就退回第一个分区；再没有就 None。
    不传 ?zone= 的时候用它（`/`、`/api/public`、老前端）。
    """
    zones = list_zones(only_visible=True)
    if not zones:
        zones = list_zones()
    return zones[0] if zones else None


def resolve_zone(zone_id):
    """
    把请求里的 zone 参数解析成分区对象。
    返回 (分区, 错误说明)：分区找不到时返回 (None, "中文原因")。
    """
    if zone_id:
        zone = get_zone(zone_id)
        if not zone:
            return None, "找不到「%s」这个分区。" % str(zone_id)[:32]
        return zone, None
    zone = default_zone()
    if not zone:
        return None, "网站上还没有任何分区，请先在后台「分区管理」里建一个。"
    return zone, None


def zone_effective_profile(zone):
    """
    分区"实际生效"的资料：分区自己的资料 + 老全局字段的兜底。

    为什么要兜底：升级前「游戏名 / 接单平台」存在全局资料里（单游戏时代）。
    升级后它们归每个分区自己管，但如果分区没填，就继续用全局那份 ——
    这样老的「资料与报价 → 全局资料」通道不会突然失灵。

    ⚠️ 前台显示、预约文案、通知都用这一个函数，保证"看到什么就约到什么"。
    """
    profile = merge_zone_profile(zone.get("profile")) if zone else dict(DEFAULT_ZONE_PROFILE)
    global_profile = get_profile()
    for key in ("game", "platform"):
        if not str(profile.get(key) or "").strip():
            profile[key] = str(global_profile.get(key) or "")[:30]
    return profile


def zone_public_view(zone):
    """
    给前台看的分区视图：把"属于这个分区"的资料和"属于全站"的资料拼在一起，
    这样前端的 zone.js 只用一个对象就能渲染整页。
    """
    site = site_summary()
    if zone:
        profile = zone_effective_profile(zone)
        kind = category_kind(zone.get("category"))
        return {
            "id": zone["id"],
            "name": zone["name"],
            "short": zone["short"] or zone["name"],
            "icon": zone["icon"],
            "theme": zone["theme"] or DEFAULT_THEME,
            "category": zone["category"],
            "kind": kind,                       # booking=有时间表 / service=没有时间表
            "sort": zone["sort"],
            "visible": zone["visible"],
            "profile": profile,
            "startPrice": zone_start_price(zone),
            "share": {
                "brand": site["brand"], "name": site["name"], "wechat": site["wechat"],
                "wechatQr": site["wechatQr"], "trust": site["trust"],
                "tagline": site["tagline"],
            },
        }
    # 一个分区都没有的兜底（正常不会发生：迁移一定会建出第一个分区）
    profile = zone_effective_profile(None)
    return {
        "id": "", "name": "", "short": "", "icon": "", "theme": DEFAULT_THEME,
        "category": "", "kind": "booking", "sort": 0, "visible": True,
        "profile": profile, "startPrice": "",
        "share": {"brand": site["brand"], "name": site["name"], "wechat": site["wechat"],
                  "wechatQr": site["wechatQr"], "trust": site["trust"],
                  "tagline": site["tagline"]},
    }


def zone_cover(zone):
    """
    首页服务卡片上那张图用哪个，按优先级挑：
      分区图标 → 分区首屏主视觉 → 该分区展示墙的第一张公开图
    （主人会自己做图，所以这里尽量"有什么就用什么"，都没有就显示一个色块占位）
    """
    if zone.get("icon"):
        return zone["icon"]
    profile = merge_zone_profile(zone.get("profile"))
    for key in ("heroImage", "shareImage", "background"):
        if profile.get(key):
            return profile[key]
    for item in list_gallery(only_public=True, zone=zone["id"]):
        if item.get("image"):
            return item["image"]
    return ""


def zone_brief(zone):
    """前台导航 / 首页卡片用的精简信息（不含整套资料）"""
    return {
        "id": zone["id"], "name": zone["name"], "short": zone["short"] or zone["name"],
        "icon": zone["icon"], "cover": zone_cover(zone),
        "theme": zone["theme"] or DEFAULT_THEME,
        "category": zone["category"], "kind": category_kind(zone["category"]),
        "tagline": merge_zone_profile(zone.get("profile")).get("tagline", ""),
        "startPrice": zone_start_price(zone),
        "visible": zone["visible"],
    }


# ---------- 分区的写 ----------

def create_zone(zone_id, category, name, short="", theme="", icon=""):
    """新建分区。返回 (分区, 错误信息)。id 的格式和查重都在这里管。"""
    zone_id = str(zone_id or "").strip().lower()
    if not ZONE_ID_RE.match(zone_id or ""):
        return None, "短标识只能是英文小写字母、数字和连字符（如 delta、ow-2），且以字母或数字开头。"
    if get_zone(zone_id):
        return None, "已经有一个分区用「%s」这个短标识了，换一个吧（比如 %s-2）。" % (zone_id, zone_id[:26])
    if category not in category_map():
        return None, "没有「%s」这个大类，请刷新页面重新选择。" % str(category)[:20]
    name = str(name or "").strip()[:30]
    if not name:
        return None, "请填写分区全称（例如：守望先锋）。"
    zone = {
        "id": zone_id, "category": category, "name": name,
        "short": str(short or "").strip()[:12] or name[:12],
        "icon": str(icon or "")[:300], "theme": str(theme or "")[:20],
        "sort": (max([z["sort"] for z in list_zones()] or [0]) + 1),
        "visible": True,
        "profile": dict(DEFAULT_ZONE_PROFILE),
        "created_at": int(time.time() * 1000),
    }
    zone["profile"]["game"] = name            # 游戏名默认跟分区全称一样，省得再填一遍
    zone["profile"]["tagline"] = ""
    with _db_lock, connect() as conn:
        conn.execute("""INSERT INTO zones(id, category, name, short, icon, theme, sort,
                        visible, profile, created_at) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                     (zone["id"], zone["category"], zone["name"], zone["short"], zone["icon"],
                      zone["theme"], zone["sort"], 1,
                      json.dumps(zone["profile"], ensure_ascii=False), zone["created_at"]))
    return get_zone(zone_id), None


def update_zone(zone_id, **fields):
    """改分区的基本信息（名字、简称、图标、主色、大类、可见、排序）。返回 (分区, 错误信息)。"""
    zone = get_zone(zone_id)
    if not zone:
        return None, "找不到这个分区。"
    sets, values = [], []
    if "category" in fields:
        category = str(fields["category"] or "")
        if category not in category_map():
            return None, "没有「%s」这个大类。" % category[:20]
        sets.append("category=?")
        values.append(category)
    if "name" in fields:
        name = str(fields["name"] or "").strip()[:30]
        if not name:
            return None, "分区全称不能是空的。"
        sets.append("name=?")
        values.append(name)
    if "short" in fields:
        sets.append("short=?")
        values.append(str(fields["short"] or "").strip()[:12])
    if "icon" in fields:
        sets.append("icon=?")
        values.append(str(fields["icon"] or "")[:300])
    if "theme" in fields:
        sets.append("theme=?")
        values.append(str(fields["theme"] or "")[:20])
    if "visible" in fields:
        sets.append("visible=?")
        values.append(1 if fields["visible"] else 0)
    if "sort" in fields:
        try:
            sets.append("sort=?")
            values.append(int(fields["sort"]))
        except (TypeError, ValueError):
            pass
    if not sets:
        return zone, None
    values.append(zone_id)
    with _db_lock, connect() as conn:
        conn.execute("UPDATE zones SET %s WHERE id=?" % ", ".join(sets), values)
    return get_zone(zone_id), None


def save_zone_profile(zone_id, raw_profile):
    """
    保存分区资料（会先清洗）。返回 (分区, 错误信息)。

    ⚠️ 这里是"**部分更新**"：没传的字段保留原来数据库里的值。
       为什么这样设计：后台的「批量导入价目表」只关心 priceGroups，
       如果整体替换，它就得先把整份资料读一遍再发回来 —— 少传一个字段就会把
       价格/文案/图片清空。合并之后再清洗，怎么调用都不会误删主人的内容。
       想清空某个字段，显式传空字符串（""）或空数组即可。
    """
    zone = get_zone(zone_id)
    if not zone:
        return None, "找不到这个分区。"
    merged = dict(zone.get("profile") or {})
    if isinstance(raw_profile, dict):
        merged.update(raw_profile)
    profile = clean_zone_profile(merged)
    with _db_lock, connect() as conn:
        conn.execute("UPDATE zones SET profile=? WHERE id=?",
                     (json.dumps(profile, ensure_ascii=False), zone_id))
    return get_zone(zone_id), None


def sort_zones(order):
    """按前端传来的 id 顺序重排（没提到的分区排在后面，保持原有相对顺序）"""
    ids = [str(x) for x in (order or [])]
    zones = list_zones()
    known = [z["id"] for z in zones]
    seq = [z for z in ids if z in known] + [z for z in known if z not in ids]
    with _db_lock, connect() as conn:
        for i, zone_id in enumerate(seq, 1):
            conn.execute("UPDATE zones SET sort=? WHERE id=?", (i, zone_id))
    return list_zones()


def zone_delete_summary(zone_id):
    """删除分区前先看看会牵连什么（返回给后台弹确认框用）"""
    zone = get_zone(zone_id)
    if not zone:
        return None
    with connect() as conn:
        gallery = conn.execute("SELECT COUNT(*) AS n FROM gallery WHERE zone=?", (zone_id,)).fetchone()["n"]
        locks = conn.execute("SELECT COUNT(*) AS n FROM locks WHERE zone=?", (zone_id,)).fetchone()["n"]
        total = conn.execute("SELECT COUNT(*) AS n FROM zones").fetchone()["n"]
    return {"id": zone_id, "name": zone["name"], "gallery": int(gallery or 0),
            "locks": int(locks or 0), "zones": int(total or 0)}


def delete_zone(zone_id):
    """
    真删一个分区。返回 (摘要, 错误信息)。

    ⚠️ 删掉的是"这个分区的招牌页"本身：
       · 分区资料（背景图/首屏图/价目表）跟着没了；
       · 展示墙图片**不删**，只是它们的 zone 变成空（后台仍能看到，可以改挂到别的分区）；
       · 预约记录**不删**，只把 zone 清空（时间是这个分区卖出去的，但账不能烂）。
    这正是"默认只隐藏、真删要二次确认"的原因。
    """
    summary = zone_delete_summary(zone_id)
    if not summary:
        return None, "找不到这个分区。"
    with _db_lock, connect() as conn:
        conn.execute("DELETE FROM zones WHERE id=?", (zone_id,))
        conn.execute("UPDATE gallery SET zone='' WHERE zone=?", (zone_id,))
        conn.execute("UPDATE locks SET zone='' WHERE zone=?", (zone_id,))
    return summary, None


# ---------- 价目表批量导入 ----------

def parse_price_lines(text):
    """
    解析"批量粘贴导入价目表"的文本。每行格式：

        名称 | 价格 | 说明       （说明可以省略；竖线用半角 | 或全角 ｜ 都行）

    返回 (成功解析出来的条目列表, 错误列表)。错误列表里每项是 {"line": 行号, "reason": "原因"}。

    规则：
      · 空行 / 以 # 开头的注释行 → 直接忽略（不算错）
      · 只写名称（没有竖线）→ 也算一条，价格留空（主人可能想先列项目再填价）
      · 名称必须非空，否则记一条错误
      · 行数上限 200，超出的部分截断并报一条错误
    """
    items, errors = [], []
    lines = str(text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    for no, raw in enumerate(lines, 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if len(items) >= 200:
            errors.append({"line": no, "reason": "一次最多导入 200 项，后面的就先不管了。"})
            break
        parts = [p.strip() for p in re.split(r"[|｜]", line)]
        name = parts[0][:40]
        if not name:
            errors.append({"line": no, "reason": "这一行没有名称（竖线前面是空的）。"})
            continue
        price = (parts[1] if len(parts) > 1 else "")[:30]
        note = (" | ".join(parts[2:]) if len(parts) > 2 else "")[:300]
        items.append({"name": name, "price": price, "note": note, "hot": False})
    return items, errors


def import_prices(zone_id, group_name, text):
    """
    把粘贴进来的价目追加到某个分区的某一组里（组不存在就新建）。
    返回 (摘要, 错误信息) 或 (含解析结果的字典, None)。
    """
    zone = get_zone(zone_id)
    if not zone:
        return None, "找不到这个分区。"
    group_name = str(group_name or "").strip()[:30]
    if not group_name:
        return None, "请先填写要导入到哪一组（例如：托管）。"
    items, errors = parse_price_lines(text)
    if not items:
        return {"imported": 0, "errors": errors, "group": group_name,
                "profile": zone["profile"]}, None
    profile = merge_zone_profile(zone["profile"])
    group = next((g for g in profile["priceGroups"] if g["name"] == group_name), None)
    if not group:
        group = {"id": "g%d" % (len(profile["priceGroups"]) + 1), "name": group_name,
                 "note": "", "items": []}
        profile["priceGroups"].append(group)
    for it in items:
        it["id"] = "i%s" % uuid.uuid4().hex[:8]
        group["items"].append(it)
    saved, err = save_zone_profile(zone_id, profile)
    if err:
        return None, err
    return {"imported": len(items), "errors": errors, "group": group_name,
            "profile": saved["profile"]}, None


# ---------- 从首屏图自动提取主色（PIL 可选，没装就优雅降级） ----------

def extract_dominant_color(data_url):
    """
    从一张图里算出主色，返回 #rrggbb。

    怎么做：缩到 80×80 → 量化成 8 个颜色 → 挑"出现最多"的那个 → 提一点饱和度和亮度。
    为什么提亮：主人的图多半是深色游戏截图，直接取出来的颜色偏灰偏暗，
    当主色（高亮色）会看不清。

    ⚠️ PIL 是唯一的第三方依赖，而且**只是"锦上添花"**：
       import 失败、图片坏掉、格式不认识……一律返回 (None, 中文原因)，绝不让网站起不来。
    """
    try:
        from PIL import Image
    except Exception:
        return None, "这台机器没有装 PIL，不能自动取色；你可以手动填一个色号。"
    m = re.match(r"^data:image/(png|jpeg|jpg|webp);base64,(.+)$", str(data_url or ""), re.S)
    if not m:
        return None, "请先上传图片（支持 JPG / PNG / WebP）。"
    try:
        blob = base64.b64decode(m.group(2))
    except Exception:
        return None, "图片解析失败。"
    if len(blob) > MAX_UPLOAD:
        return None, "图片太大，请压缩到 8 MB 以内。"
    try:
        img = Image.open(io.BytesIO(blob)).convert("RGB")
        img.thumbnail((80, 80))
        quant = img.quantize(colors=8, method=Image.Quantize.MEDIANCUT).convert("RGB")
        counts = {}
        for pixel in quant.getdata():
            counts[pixel] = counts.get(pixel, 0) + 1
        r, g, b = max(counts.items(), key=lambda kv: kv[1])[0]
        r, g, b = _brighten(r, g, b)
        return "#%02x%02x%02x" % (r, g, b), None
    except Exception as e:
        return None, "读不出这张图的颜色（%s）。" % type(e).__name__


def _brighten(r, g, b, min_value=150):
    """把颜色往"亮"的方向推，保证它当主色时在深色背景上看得清。"""
    top = max(r, g, b, 1)
    scale = min(2.2, max(1.0, min_value / float(top)))
    r, g, b = [min(255, int(round(v * scale))) for v in (r, g, b)]
    # 灰度色（三个通道差不多）就换成默认主色，否则"取色"等于没取
    if max(r, g, b) - min(r, g, b) < 24:
        return 198, 240, 74
    return r, g, b


# ---------- 老数据迁移：单游戏站 → 两级分区（★ 一条数据都不能丢） ----------

def migrate_to_zones():
    """
    把"升级前的老库"平滑搬到两级分区结构上。**只做一次**。

    什么时候做：store 里没有 zones_migrated 标记，**而且** zones 表是空的。
    做了之后：
      1. 建 3 个默认大类（categories）
      2. 用全局 profile 里的老内容建第一个分区 delta（陪玩类）
         —— 老的 tagline/headline/bio/platform/styles/prices/promises/announcement/
            heroImage/background/backgroundDim 全部搬进它的 profile
      3. 老的 gallery 全部挂到 delta
      4. 老的 locks 全部标记成 delta
      5. 全局 profile 只保留 brand/name/avatar/wechat/xianyu/accepting，并补上 trust

    ⚠️ 这个函数只"添加"和"搬走"，**从不删除任何一行数据**：
      老的 locks/gallery 一条都不动，只是多了一个 zone 字段的值。
    """
    with connect() as conn:
        if conn.execute("SELECT 1 FROM store WHERE key='zones_migrated'").fetchone():
            return False                                   # 已经迁过了
        has_zones = conn.execute("SELECT COUNT(*) AS n FROM zones").fetchone()["n"]
        if has_zones:
            conn.execute("INSERT INTO store(key, value) VALUES('zones_migrated', '1')")
            return False                                   # 已有分区数据 → 不重复迁移

        old_profile = {}
        row = conn.execute("SELECT value FROM store WHERE key='profile'").fetchone()
        if row:
            old_profile = _json_or(row["value"], {})

        # ---- 1. 大类 ----
        cats = list_categories()
        set_categories(cats)
        print("[迁移] 已创建 %d 个大类：%s" % (len(cats), "、".join(c["name"] for c in cats)), flush=True)

        # ---- 2. 第一个分区 delta ----
        now_ms = int(time.time() * 1000)
        zone_profile = dict(DEFAULT_ZONE_PROFILE)
        zone_profile["game"] = str(old_profile.get("game") or "三角洲行动")[:30]
        zone_profile["tagline"] = str(old_profile.get("tagline") or "")[:60]
        zone_profile["headline"] = str(old_profile.get("headline") or DEFAULT_ZONE_PROFILE["headline"])[:80]
        zone_profile["bio"] = str(old_profile.get("bio") or "")[:1200]
        zone_profile["announcement"] = str(old_profile.get("announcement") or "")[:200]
        zone_profile["platform"] = str(old_profile.get("platform") or "")[:30]
        zone_profile["promises"] = _clean_text_list(old_profile.get("promises")) or STARTER_ZONE_PROMISES
        zone_profile["highlights"] = _clean_text_list(
            old_profile.get("highlights") if old_profile.get("highlights") else old_profile.get("styles"),
            limit=12, each=20) or STARTER_ZONE_HIGHLIGHTS
        zone_profile["prices"] = old_profile.get("prices") or STARTER_ZONE_PRICES
        zone_profile["background"] = str(old_profile.get("background") or "")[:300]
        zone_profile["heroImage"] = str(old_profile.get("heroImage") or "")[:300]
        zone_profile["backgroundDim"] = old_profile.get("backgroundDim", 72)
        zone_profile = clean_zone_profile(zone_profile)

        zone_name = str(old_profile.get("game") or "三角洲行动")[:30]
        conn.execute("""INSERT INTO zones(id, category, name, short, icon, theme, sort,
                        visible, profile, created_at) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                     ("delta", "play", zone_name, "三角洲", "", "", 1, 1,
                      json.dumps(zone_profile, ensure_ascii=False), now_ms))
        print("[迁移] 已创建第一个分区：delta（%s，陪玩类，含老报价 %d 档 / 服务承诺 %d 条 / 亮点 %d 个）"
              % (zone_name, len(zone_profile["prices"]), len(zone_profile["promises"]),
                 len(zone_profile["highlights"])), flush=True)

        # ---- 3./4. 老展示墙、老预约记录全部归到 delta ----
        gal = conn.execute("UPDATE gallery SET zone='delta' WHERE zone IS NULL OR zone=''").rowcount
        lk = conn.execute("UPDATE locks SET zone='delta' WHERE zone IS NULL OR zone=''").rowcount
        print("[迁移] 老展示墙 %d 张、老预约记录 %d 条已标记为 delta 分区（数据一条没动）"
              % (gal, lk), flush=True)

        # ---- 5. 全局资料：只留跨分区共用的 ----
        new_global = dict(DEFAULT_PROFILE)
        new_global.update({
            "brand": str(old_profile.get("brand") or DEFAULT_PROFILE["brand"]),
            "name": str(old_profile.get("name") or DEFAULT_PROFILE["name"]),
            "tagline": str(old_profile.get("tagline") or ""),
            "wechat": str(old_profile.get("wechat") or ""),
            "xianyu": str(old_profile.get("xianyu") or ""),
            "avatar": str(old_profile.get("avatar") or ""),
            "bio": str(old_profile.get("bio") or ""),
            "accepting": bool(old_profile.get("accepting", True)),
            "trust": _clean_text_list(old_profile.get("trust"), limit=10, each=60),
            # ↓ 老字段继续留着：老接口和老后台还在读它们，删掉会让老工具失效
            "game": str(old_profile.get("game") or ""),
            "platform": str(old_profile.get("platform") or ""),
        })
        conn.execute("UPDATE store SET value=? WHERE key='profile'",
                     (json.dumps(new_global, ensure_ascii=False),))
        conn.execute("INSERT INTO store(key, value) VALUES('zones_migrated', '1')")
        print("[迁移] 全局资料已精简为跨分区共用的字段（品牌/昵称/微信/头像/介绍/信任信号）",
              flush=True)
    return True


# ============================================================================
# 二之四、★ 客户档案 + 到期提醒（为了让主人每天花 1 分钟就知道"该催谁了"）
#
#   主人是一个人靠这个吃饭：陪玩按小时、代肝**包月**、剪辑/PS/建站按项目。
#   包月的单子最怕的不是打不动，而是**到期了没人提醒** ——
#   客户忘了，单子就悄悄没了（丢一个 50R/月的客户 = 一年 600R）。
#
#   所以这一章只解决一件事：**今天谁快到期了**。
#     · customers 表一行 = 一个客户（可以关联一个分区和档位，也可以什么都不关联）
#     · end_date 留空 = 一次性/长期，永远不算"到期"（不会天天来烦他）
#     · days_left() + customer_level() 把"还剩几天"翻译成人能一眼看懂的颜色分档
#
#   ⚠️ 两条不能改错的规矩：
#     1. price 是**自由文本**：他写 "20R/月"、"推图15R"，一个字都不要动、不要加 ¥；
#     2. "今天是几号"一律按**北京时间**算（today_cn()），不能受服务器时区影响 ——
#        服务器在 UTC 时，北京时间早上 8 点前都还算"昨天"，续费就从错的那天开始推了。
# ============================================================================

# 客户的三种状态（status 白名单）
CUSTOMER_STATUSES = ("active", "paused", "ended")
CUSTOMER_STATUS_NAMES = {"active": "进行中", "paused": "已暂停", "ended": "已结束"}

# ★ 到期分档（整个功能的灵魂）。days = days_left(end_date)：
#     days is None      → none     长期 / 一次性（不算到期，列表里排最后）
#     days < 0          → expired  已超期 N 天     ← 红色，最优先
#     days == 0         → today    今天到期        ← 红色
#     1 <= days <= 3    → soon     3 天内到期      ← 橙色
#     4 <= days <= 7    → week     一周内到期      ← 黄色
#     days > 7          → ok       正常            ← 灰/绿
#   status != 'active'（暂停 / 已结束）时，用状态单独一档，不参与上面这套算账。
CUSTOMER_LEVELS = {
    "expired": {"label": "已超期", "color": "var(--danger)", "rank": 0},
    "today":   {"label": "今天到期", "color": "var(--danger)", "rank": 1},
    "soon":    {"label": "3 天内", "color": "var(--accent2)", "rank": 2},
    "week":    {"label": "一周内", "color": "#e8d24a", "rank": 3},
    "ok":      {"label": "正常", "color": "var(--muted)", "rank": 4},
    "paused":  {"label": "已暂停", "color": "var(--muted)", "rank": 5},
    "ended":   {"label": "已结束", "color": "var(--muted)", "rank": 6},
    "none":    {"label": "长期", "color": "var(--muted)", "rank": 7},
}

# 联系方式渠道：客户档案复用预约那一套（wechat / xianyu / qq），另外多一个"其他"
CUSTOMER_CONTACT_TYPES = {"wechat": "微信", "qq": "QQ", "xianyu": "闲鱼", "other": "其他"}

# 建档时没写渠道就用微信（和客户自助预约 CONTACT_DEFAULT 保持一致）
CUSTOMER_DEFAULT_CONTACT = CONTACT_DEFAULT

# 日期必须是标准的 "YYYY-MM-DD"。写别的一律当成"没填"（见 clean_customer 的说明）。
CUSTOMER_DATE_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")


def today_cn():
    """
    返回**北京时间**的今天，格式 "YYYY-MM-DD"。

    为什么不直接 datetime.now()：那是"服务器所在时区的现在"。
    免费服务器（Render / PythonAnywhere）跑在 UTC 上，比北京时间晚 8 小时 ——
    北京时间今天早上 7 点，服务器那边还是昨天。这时候如果用 datetime.now()，
    "今天到期"的客户会被算成"还有 1 天"，续费也会从昨天开始推，一天天错下去。

    所以这里跟全站其它地方一样：**用 now() + 8 小时**（CN 就是 +8 时区），
    中国全年不用夏令时，所以这样算永远是对的。
    """
    return datetime.now(CN).strftime("%Y-%m-%d")


def parse_date(text):
    """
    把 "YYYY-MM-DD" 解析成 date 对象；空 / 格式不对 / 日历上没有这一天（如 2024-13-45）→ None。

    为什么自己写正则 + 再交给 strptime：strptime("2024-1-5") 居然能过（它容忍单位数），
    但那种写法存进数据库以后，前端和排序就得跟着猜，不如一开始就统一成 10 位。
    """
    m = CUSTOMER_DATE_RE.match(str(text or "").strip())
    if not m:
        return None
    try:
        return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3))).date()
    except ValueError:
        return None


def days_left(end_date):
    """
    距离到期还有几天：0 = 今天到期，负数 = 已经超期几天（-2 就是超期 2 天）。

    end_date 是空字符串 / 格式不对 / 日历上不存在的日期 → 返回 None，
    意思是"**不适用**"（一次性、长期、或者日期还没填）—— 这种客户永远不进"待催"列表。
    """
    end = parse_date(end_date)
    if not end:
        return None
    today = parse_date(today_cn())
    return (end - today).days


def add_months(date_obj, months):
    """
    给一个日期加 N 个自然月（1 月 31 日 + 1 个月 → 2 月 28/29 日）。

    规则：**先加月份，日期超出那个月的天数就压到当月最后一天**。
    Python 标准库没有 relativedelta，所以这里手写；这样"每个月同一个号到期"最符合直觉。
    """
    total = date_obj.month - 1 + int(months)
    year = date_obj.year + total // 12
    month = total % 12 + 1
    day = date_obj.day
    while day > 1:
        try:
            return datetime(year, month, day).date()
        except ValueError:
            day -= 1                      # 2 月没有 30 号 → 压到 28/29 号
    return datetime(year, month, 1).date()


def months_between(start_text, end_text):
    """两个日期之间"大约几个月"（只用来给主人一句提示，不参与任何算钱）"""
    start, end = parse_date(start_text), parse_date(end_text)
    if not start or not end:
        return 0
    months = (end.year - start.year) * 12 + (end.month - start.month)
    if end.day < start.day:
        months -= 1
    return max(0, months)


def customer_level(days, status):
    """
    ★ 状态分档（前端拿这个字符串去上色、排序、筛选）。规则见 CUSTOMER_LEVELS 上面的注释。

    days 是 days_left() 的结果（数字或 None），status 是 active / paused / ended。
    先把"暂停 / 已结束"挑出来单独给一档，剩下的才按天数分档 ——
    这样一个暂停中的客户不会因为"超期 10 天"天天排在待催列表最上面。
    """
    if str(status or "active") == "paused":
        return "paused"
    if str(status or "active") == "ended":
        return "ended"
    if days is None:
        return "none"
    if days < 0:
        return "expired"
    if days == 0:
        return "today"
    if days <= 3:
        return "soon"
    if days <= 7:
        return "week"
    return "ok"


def customer_view(row):
    """
    把数据库里的一行客户变成前端要的样子，顺手算好 daysLeft 和 level。

    前端**不再自己算日期**：它只管按 level 上色、按 daysLeft 显示文字，
    这样"还剩几天"永远只有一处逻辑（就是这里），不会出现"后端说超期、前端说还有 1 天"。
    """
    data = dict(row)
    data["contact_type"] = str(data.get("contact_type") or CUSTOMER_DEFAULT_CONTACT)
    data["status"] = str(data.get("status") or "active")
    days = days_left(data.get("end_date"))
    level = customer_level(days, data["status"])
    data["daysLeft"] = days
    data["level"] = level
    data["levelLabel"] = CUSTOMER_LEVELS[level]["label"]
    data["levelColor"] = CUSTOMER_LEVELS[level]["color"]
    data["statusLabel"] = CUSTOMER_STATUS_NAMES.get(data["status"], "进行中")
    data["zoneName"] = (get_zone(data.get("zone")) or {}).get("name", "") if data.get("zone") else ""
    data["created_at"] = int(data.get("created_at") or 0)
    data["updated_at"] = int(data.get("updated_at") or 0)
    return data


def clean_customer(data):
    """
    清洗前端传来的客户资料。返回 (干净的字段字典, 中文错误信息)。

    规则（这些是**故意选的**，不是漏写）：
      · name 必填且非空 —— 一个没名字的档案没法催，直接报错"客户称呼不能是空的。"
      · start_date / end_date 必须是 "YYYY-MM-DD"：**不合法就置空，不报错**。
        为什么置空而不报错：主人常常先建个档案、日期以后再补；要是因为打错一个
        日期就把整表单打回去，他填的档位、金额、备注全白填了（这是真实的手感问题）。
        置空之后这条客户就是"长期"，永远不会误报到期 —— 比存一个错的日期安全得多。
      · price / item / note 都是自由文本，原样保留（"20R/月"绝不变形）
      · status 走白名单，认不出来的一律当 active
    """
    data = data if isinstance(data, dict) else {}
    name = str(data.get("name") or "").strip()[:30]
    if not name:
        return None, "客户称呼不能是空的。"
    status = str(data.get("status") or "active").strip()
    if status not in CUSTOMER_STATUSES:
        status = "active"
    contact_type = str(data.get("contact_type") or "").strip()
    if contact_type not in CUSTOMER_CONTACT_TYPES:
        contact_type = CUSTOMER_DEFAULT_CONTACT
    zone = str(data.get("zone") or "").strip()[:32]
    if zone and not get_zone(zone):
        zone = ""                       # 分区被删了：不报错，只是不再关联
    out = {
        "name": name,
        "contact": str(data.get("contact") or "").strip()[:60],
        "contact_type": contact_type,
        "zone": zone,
        "item": str(data.get("item") or "").strip()[:40],
        "price": str(data.get("price") or "").strip()[:30],      # ★ 自由文本，一个字符都不改
        "start_date": str(data.get("start_date") or "").strip()[:10],
        "end_date": str(data.get("end_date") or "").strip()[:10],
        "status": status,
        "note": str(data.get("note") or "").strip()[:300],
    }
    # 日期：格式不对就置空（见上面注释里的取舍）
    for key in ("start_date", "end_date"):
        if out[key] and not parse_date(out[key]):
            out[key] = ""
    return out, None


def list_customers():
    """
    全部客户，**按"紧急度"排好序**（前端还会按需再筛选，但顺序由这里定）：

        已超期（超期最久的最前）→ 今天到期 → 3 天内 → 一周内 → 正常 → 已暂停 → 已结束 → 长期

    为什么超期最久的排最前：那种单子基本已经黄了，要么赶紧问一句，要么干脆放弃，
    总之比"还有 2 天到期"的更该先看到。
    """
    with connect() as conn:
        rows = conn.execute("SELECT * FROM customers").fetchall()
    items = [customer_view(r) for r in rows]

    def sort_key(c):
        rank = CUSTOMER_LEVELS.get(c["level"], CUSTOMER_LEVELS["ok"])["rank"]
        # expired：daysLeft 越小（超期越久）越前；其它：越快到期的越前
        return (rank, c["daysLeft"] if c["daysLeft"] is not None else 9999, c["name"])

    items.sort(key=sort_key)
    return items


def get_customer(cid):
    """按 id 取一条客户档案；没有就返回 None。"""
    if not cid:
        return None
    with connect() as conn:
        row = conn.execute("SELECT * FROM customers WHERE id=?", (str(cid),)).fetchone()
    return customer_view(row) if row else None


def save_customer(data):
    """
    新建或更新一个客户。返回 (客户, 中文错误信息)。

    带 id 且这个 id 真的存在 → 更新（created_at 保留，updated_at 刷新）
    没带 id（或 id 已经不存在了）→ 新建一条，id 用 uuid4().hex
    """
    fields, err = clean_customer(data)
    if err:
        return None, err
    now_ms = int(time.time() * 1000)
    cid = str((data or {}).get("id") or "").strip()
    existing = get_customer(cid) if cid else None
    with _db_lock, connect() as conn:
        if existing:
            conn.execute("""UPDATE customers SET name=?, contact=?, contact_type=?, zone=?,
                            item=?, price=?, start_date=?, end_date=?, status=?, note=?,
                            updated_at=? WHERE id=?""",
                         (fields["name"], fields["contact"], fields["contact_type"], fields["zone"],
                          fields["item"], fields["price"], fields["start_date"], fields["end_date"],
                          fields["status"], fields["note"], now_ms, cid))
        else:
            cid = uuid.uuid4().hex
            conn.execute("""INSERT INTO customers(id, name, contact, contact_type, zone, item,
                            price, start_date, end_date, status, note, created_at, updated_at)
                            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                         (cid, fields["name"], fields["contact"], fields["contact_type"],
                          fields["zone"], fields["item"], fields["price"], fields["start_date"],
                          fields["end_date"], fields["status"], fields["note"], now_ms, now_ms))
    return get_customer(cid), None


def delete_customer(cid):
    """删掉一个客户档案。返回 True/False（有没有这一条）。客户删了不影响任何预约记录。"""
    if not cid:
        return False
    with _db_lock, connect() as conn:
        cur = conn.execute("DELETE FROM customers WHERE id=?", (str(cid),))
        return cur.rowcount > 0


def renew_customer(cid, months=1, weeks=0, days=0, mode="extend"):
    """
    ★ 续费：把 end_date 往后推。返回 (ok, 新的到期日 or 中文错误信息)。

    两种算法，用途完全不同，别搞混：

      mode="extend"（默认）—— 从**原来的到期日**往后推。
          · 客户还在服务期内（或刚超期一两天）：不损失他已经付过的天数。
            例：8 月 20 日到期，续 1 个月 → 9 月 20 日到期（不是 9 月 10 日）。
          · 已经超期很久（比如 3 个月没动静）：还从原来那天推，算出来的新到期日
            **可能还是过去的日期**（2 月到期 + 1 个月 = 3 月，而今天已经 6 月了）。
            这时候返回的 ok 仍是 True，但提示里会让他改用「从今天算」——
            因为"补上那段空窗"不是他想要的效果。

      mode="from_today" —— 从**今天**往后推（北京时间今天）。
          适合"停了一阵子、现在重新开始"的客户：今天 + 1 个月就对了。

    没有到期日（长期/一次性）的客户不能续费 —— 会返回中文错误，让他先去填一个到期日。

    months / weeks / days 可以叠加（比如 months=1, days=0）。
    """
    customer = get_customer(cid)
    if not customer:
        return False, "找不到这个客户。"
    try:
        months, weeks, days = int(months or 0), int(weeks or 0), int(days or 0)
    except (TypeError, ValueError):
        return False, "续费时长只能是数字。"
    months = max(0, min(120, months))
    weeks = max(0, min(520, weeks))
    days = max(0, min(3650, days))
    if not (months or weeks or days):
        return False, "请选择续多久（至少 1 个月 / 1 周 / 1 天）。"

    end = parse_date(customer.get("end_date"))
    if not end:
        return False, "这个客户还没有到期日（长期或一次性），先点「编辑」填一个再来续费。"
    mode = "from_today" if mode == "from_today" else "extend"
    base = parse_date(today_cn()) if mode == "from_today" else end
    if not base:
        return False, "算不出今天的日期，请稍后再试。"
    new_end = add_months(base, months) + timedelta(weeks=weeks, days=days)
    new_text = new_end.strftime("%Y-%m-%d")

    with _db_lock, connect() as conn:
        conn.execute("UPDATE customers SET end_date=?, updated_at=? WHERE id=?",
                     (new_text, int(time.time() * 1000), customer["id"]))
    return True, new_text


def customer_stats():
    """
    ★ 后台「客户」标签页顶部那 4 张统计卡的数据。

      total    客户总数
      expired  已超期
      today    今天到期
      soon     3 天内到期（**含今天**，所以 soon >= today）
      week     7 天内到期（**含前两档**，所以 week >= soon）
      active   进行中的（status=active，不管哪天到期）
      remind   需要催的 = 已超期 + 7 天内到期（"待催"筛选按钮用的就是这个数）

    暂停 / 已结束的客户不进 expired / today / soon / week（它们有自己的档），
    但照样算在 total 里。
    """
    items = list_customers()
    stats = {"total": len(items), "expired": 0, "today": 0, "soon": 0, "week": 0,
             "active": 0, "remind": 0}
    for c in items:
        if c["status"] == "active":
            stats["active"] += 1
        if c["level"] == "expired":
            stats["expired"] += 1
        elif c["level"] == "today":
            stats["today"] += 1
        elif c["level"] == "soon":
            stats["soon"] += 1
        elif c["level"] == "week":
            stats["week"] += 1
    # 三档是"以内"累计的：3 天内 = 超期 + 今天 + 3 天内，一周内 = 上面全部 + 4~7 天
    stats["soon"] += stats["expired"] + stats["today"]
    stats["week"] += stats["soon"]
    stats["remind"] = stats["week"]        # 「待催」= 已超期 + 7 天内到期
    return stats


# ---------- 时段与规则 ----------

def list_locks():
    with connect() as conn:
        rows = conn.execute("SELECT * FROM locks ORDER BY start").fetchall()
    return [dict(r) for r in rows]


# 锁定记录的完整字段（写全再插入，老库补完字段后和新库行为完全一致）
LOCK_COLUMNS = ("id", "kind", "start", "end", "client", "note",
                "source", "mode", "price", "contact", "contact_type", "created_at", "zone",
                "confirmed", "confirmed_at")


def insert_lock(conn, **fields):
    """插一条锁定时段。source='owner' 是自己锁的，'client' 是客户自助预约。
    zone 记的是"这单来自哪个分区"——时间本身仍然是全局共用的。
    confirmed=0 表示「待确认」（客户自助预约的默认状态）。"""
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


def list_gallery(only_public=False, zone=None, archived_only=None):
    """
    展示墙列表。
      only_public=True   → 只要公开且没归档的（前台用）
      zone="delta"       → 只要这个分区的图（前台分区页、后台分区下拉都用它）
      zone=""            → 只要"没挂分区"的图（删分区之后留下来的孤儿图）
      zone=None          → 全部（后台总览 / 备份用）
      archived_only=True → 只要归档的；False → 只要没归档的；None → 都要
                           （后台的"查看归档"开关和"分类下还剩几张图"都用它）
    """
    where, values = [], []
    if only_public:
        where.append("visible=1 AND archived=0")
    if archived_only is not None:
        where.append("archived=1" if archived_only else "archived=0")
    if zone is not None:
        where.append("zone=?")
        values.append(zone)
    sql = "SELECT * FROM gallery"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY created_at DESC"
    with connect() as conn:
        rows = conn.execute(sql, values).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["visible"] = bool(d["visible"])
        d["archived"] = bool(d["archived"])
        d["zone"] = d.get("zone") or ""
        d["category"] = d.get("category") or DEFAULT_GALLERY_CATEGORY
        # ★ 外链：老库刚补完字段时是 None，这里统一成字符串，前端就不用判空
        d["link"] = d.get("link") or ""
        out.append(d)
    return out


# ============================================================================
# 二之五、★ 收入登记 + 月度统计（"第三刀"）
#
#   主人是**一个人靠这个吃饭**：陪玩按小时、代肝包月、剪辑/PS/建站按项目。
#   客户那一章解决的是"该催谁了"，这一章解决的是另一个问题：
#     **钱从哪来、该推什么。**
#
#   核心设计原则只有一条：
#     ★ **登记必须发生在他本来就要做的动作里**，不能让他每天单独记账。
#       所以三个记账入口全都挂在已有的动作上：
#         ① 点「已确认」（confirm-lock）→ 自动记一笔（source='booking'）
#         ② 续费时填了金额        → 自动记一笔（source='renew'）
#         ③ 剪辑 / PS / 建站这种一次性单子 → 点「+ 记一笔」手动补（source='manual'）
#       前两个他本来就要做，第三个是唯一"多出来"的动作，但那种单子本来就不在时间表里。
#
#   ⚠️ 三条不能改错的规矩：
#     1. **payments.amount 是数字**，而 customers.price 仍然是自由文本 ——
#        两者不是一回事，别想着"统一一下"（自由文本是主人要的，数字是统计要的）；
#     2. **幂等**：同一单（同一个 source + ref）只能有一条流水。
#        老板可能反复点「已确认」，也可能恢复一次备份之后又点一遍 ——
#        没有这道防线，流水里会出现两条一模一样的钱，统计就假了；
#     3. **"本月"按北京时间算**（CURRENT_DATE_RE + today_cn），和客户到期那一套完全一致。
#        服务器在 UTC 时，北京时间 10 月 1 日早上 7 点还是"9 月"，那一个月的账就错了。
# ============================================================================

# 三种来源 → 后台流水表上那个小徽章的中文
PAYMENT_SOURCES = ("booking", "renew", "manual")
PAYMENT_SOURCE_NAMES = {"booking": "确认预约", "renew": "续费", "manual": "手动补记"}

# 大类名 → 图标（和 static/admin.js 的 GROUP_ICON 是同一套；认不出来用 🎯）
INCOME_CATEGORY_ICONS = {"陪玩": "🎮", "代肝": "⚡", "其他服务": "🎨"}

# 按"钱多钱少"排的排行榜各取前几名
INCOME_TOP_N = 5

# 收款日期必须是标准的 "YYYY-MM-DD"（和客户档案的日期一个格式，但**不必**存在真实日历上吗？
# 不，仍然要求真实存在：2026-02-30 会被拒绝）。格式不对 / 日期不存在 → 用今天（见 clean_payment）。
PAYMENT_DATE_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")

# 「哪个大类」那一栏的兜底：付款记录没有分区（或者分区被删了）时归到这里
PAYMENT_UNCATEGORIZED = "未分类"
PAYMENT_UNCATEGORIZED_ICON = "❓"

# 收入页几个时间范围的按钮（顺序 = 界面上从左到右）
INCOME_RANGES = ("month", "last-month", "3m", "year", "all")
INCOME_RANGE_NAMES = {"month": "本月", "last-month": "上月", "3m": "近 3 个月",
                      "year": "今年", "all": "全部"}

# 流水明细表一次最多给多少条（统计仍然是全量算的，只是"列出来"截断）
INCOME_LIST_LIMIT = 100

# 一次给前端多少条流水（防着一个"慢"字：几万条流水塞进 JSON 会让后台越用越卡）。
# 统计永远是全量算的，只有"列表"截断；超过这个数，后台会写一句"只显示最近 N 条"。
PAYMENT_LIST_MAX = 2000


def add_days_text(text, days):
    """
    "YYYY-MM-DD" 加几天（返回同样的格式）。解析不了就返回 ""。

    为什么要它：算"上个月是几号到几号"必须**先把日期对象造出来再减一天** ——
    直接用字符串拼 "2026-10-01" 减一天是算不出 9 月 30 日的。
    """
    d = parse_date(text)
    if not d:
        return ""
    return (d + timedelta(days=days)).strftime("%Y-%m-%d")


def month_start_text(text):
    """
    取 `text` 所在月份的 1 号，返回 "YYYY-MM-DD"。⚠️ 参数是**日期**不是时间戳。

    （叫 month_start_text 而不是 month_start，是为了和"毫秒时间戳"那套函数一眼区分开：
      时段计算用的是毫秒，收入用的是"YYYY-MM-DD"文本，混起来最容易出错。）
    """
    d = parse_date(text)
    if not d:
        return ""
    return d.replace(day=1).strftime("%Y-%m-%d")


def add_months_text(text, months):
    """
    在"YYYY-MM-DD"上加 N 个自然月（用的就是客户续费那套 add_months，压到月末）。

    为什么复用而不是重写一份：续费算到期日、统计算月份边界，**必须是同一套算法**。
    要是这里自己写一个"每月 30 天"，那"上月"的范围就会和客户看到的到期日差一两天。
    """
    d = parse_date(text)
    if not d:
        return ""
    return add_months(d, months).strftime("%Y-%m-%d")


def income_range_bounds(pay_range, today=None):
    """
    ★ 把界面上的范围按钮翻成一对日期边界 `(start, end)`，**闭区间**，两端都含。

    为什么是闭区间：主人想的是"10 月 1 日到 10 月 31 日的钱"，
    而流水里存的就是"哪天收的"（不是精确到秒）。用闭区间，边界那天的账不会被漏掉。

      month      本月      → 今天所在月的 1 号 ~ 今天所在月的最后一天
                            （最后一天 = 下月 1 号 - 1 天，这样闰年 2 月也不会算错）
      last-month 上月      → 上个月 1 号 ~ 上个月最后一天
      "3m"（内部叫 3m）   → 本月 1 号往前推 2 个月的 1 号 ~ 本月最后一天
                            （也就是"这三个月"，含本月）
      year       今年      → 今年 1 月 1 日 ~ 今年 12 月 31 日
      all        全部      → ("", "") —— 空字符串表示这一端不设限

    认不出来的范围一律退回 "month"（本月）。
    返回的两个值都是 "YYYY-MM-DD" 文本，直接能和 payments.date 比大小（字典序 = 日期序）。
    """
    today = today or today_cn()
    if pay_range not in INCOME_RANGES:
        pay_range = "month"
    first = month_start_text(today)
    next_first = add_months_text(first, 1)
    last = add_days_text(next_first, -1) if next_first else ""
    if pay_range == "month":
        return first, last
    if pay_range == "last-month":
        prev_first = add_months_text(first, -1)
        return prev_first, add_days_text(first, -1)
    if pay_range == "3m":
        return add_months_text(first, -2), last
    if pay_range == "year":
        return first[:4] + "-01-01", first[:4] + "-12-31"
    return "", ""          # all


def payment_in_range(pay_date, start, end):
    """这条流水的日期在不在范围内（空字符串 = 那一端不设限）。"""
    if start and pay_date < start:
        return False
    if end and pay_date > end:
        return False
    return True


def payment_zone_map():
    """{分区id: 分区对象}，取一次用一整个统计过程（别在循环里一次次查库）。"""
    return {z["id"]: z for z in list_zones()}


def payment_zone_label(zone_id, zones=None):
    """
    这条流水该显示成哪个服务。

    分区还在 → 用它现在的名字（删掉分区之后，记录里的 zone 会被清空，
              所以这里查不到也不用慌）；查不到但有 id → 原样显示 id（比显示空白强）。
    """
    if not zone_id:
        return ""
    zone = (zones or payment_zone_map()).get(zone_id)
    return zone["name"] if zone else str(zone_id)


def payment_category_label(zone_id, zones=None, categories=None):
    """
    这条流水归到哪个"大类"（陪玩 / 代肝 / 其他服务 / 未分类）。

    规则：分区还在 → 看它的 category 属于哪个大类；分区没了或者是空的 → 未分类。
    返回 (大类id, 大类名, 图标)。"未分类"不是错误，**它本身就是一个有用的信号** ——
    说明有单子没挂在任何分区上（剪辑、PS 这种一次性单子多半就在这儿）。
    """
    cats = categories if categories is not None else list_categories()
    zone = (zones or payment_zone_map()).get(zone_id) if zone_id else None
    cat = next((c for c in cats if c["id"] == (zone or {}).get("category")), None)
    if not cat:
        return "", PAYMENT_UNCATEGORIZED, PAYMENT_UNCATEGORIZED_ICON
    return cat["id"], cat["name"], INCOME_CATEGORY_ICONS.get(cat["name"], "🎯")


def income_amount(value):
    """
    金额怎么显示：整数就显示成 ¥120（不带小数点），有小数就保留两位 → ¥120.50。

    为什么不用 `%.2f` 一路写死：流水里绝大多数是整数（¥30、¥100），
    满屏的 `¥30.00` 会让这张表看起来像财务报表，主人扫一眼的速度会变慢。
    """
    try:
        amount = float(value or 0)
    except (TypeError, ValueError):
        amount = 0.0
    if abs(amount - round(amount)) < 0.005:
        return "¥%d" % int(round(amount))
    return "¥%.2f" % amount


def clean_payment(data):
    """
    清洗一条收入流水（手动记账、续费记账、恢复备份都用它）。
    返回 (干净字段字典, 中文错误信息)。

    规矩（每一条都是"宁可拒绝，也不要记错账"）：
      · **金额必须有、必须 > 0** —— 0 元和负数一律 400 报中文错。
        为什么要拦 0 元：流水里一堆 0 元会把"这个月收了多少"这件事搞浑，
        也会让"哪个服务最来钱"的排行全是 0（那是噪声，不是信息）。
      · **日期格式不对 → 用今天**（北京时间），不报错。取舍和客户档案的日期一样：
        为一个打错的日期把整张表单打回去，他填的金额、项目、客户全白填了。
        但这里**会**在返回里带一句 `dateTip` 说清楚"我按今天记了"（客户档案那边是静默置空）。
      · source 走白名单，认不出来一律当 manual（手动补记）——
        宁可显示成"手动补记"，也不要凭空冒出一个"确认预约"（那会让人以为系统乱记账）。
      · zone 不存在 → 不报错，只是不关联（分区可能刚被删）。
      · customer_id 不存在 → 同理，清空（但 customer 那个**名字**留着，账不能因为删档案就烂掉）。

    返回的字典里多带一个 `dateFallback`（True = 日期不合法、是兜底成今天的），
    调用方（admin_payments）拿它决定要不要提示。
    """
    data = data if isinstance(data, dict) else {}

    amount = clean_amount(data.get("amount"))
    if amount is None:
        return None, "金额要填一个大于 0 的数字（例如 30）。"
    if amount <= 0:
        return None, "金额要大于 0 —— 0 元不用记，流水里一堆 0 会把统计搞浑。"

    date_text = str(data.get("date") or "").strip()[:10]
    date_fallback = False
    if not parse_date(date_text):
        # 空 / 格式不对 / 日历上没有这一天 → 一律按**北京时间今天**记，并让调用方提示一句
        date_text = today_cn()
        date_fallback = True

    source = str(data.get("source") or "manual").strip()
    if source not in PAYMENT_SOURCES:
        source = "manual"

    zone = str(data.get("zone") or "").strip()[:32]
    if zone and not get_zone(zone):
        zone = ""                       # 分区被删了：不报错，只是不再关联

    customer_id = str(data.get("customer_id") or "").strip()[:64]
    customer = str(data.get("customer") or "").strip()[:30]
    if customer_id:
        hit = get_customer(customer_id)
        if hit:
            if not customer:
                customer = hit["name"]      # 没手填称呼就自动带上档案里的名字
        else:
            customer_id = ""                # 档案删了：连名字都不给的话就只剩 customer 字段

    return {
        "date": date_text,
        "amount": amount,
        "zone": zone,
        "item": str(data.get("item") or "").strip()[:40],
        "customer": customer,
        "customer_id": customer_id,
        "source": source,
        "ref": str(data.get("ref") or "").strip()[:120],
        "note": str(data.get("note") or "").strip()[:200],
        "dateFallback": date_fallback,
    }, None


def list_payments(limit=None):
    """
    全部收入流水，**新的在前**（同一天按写入时间倒序）。

    排序口径：date 倒序 → created_at 倒序。
    为什么不按日期正序：这张表是"回顾这个月收了什么"，最近的那几笔最该在第一眼看到。
    `limit` 只影响"列表显示"，不影响统计（统计是 income_stats() 自己算的）。
    """
    sql = "SELECT * FROM payments ORDER BY date DESC, created_at DESC"
    with connect() as conn:
        rows = conn.execute(sql).fetchall()
    out = [dict(r) for r in rows]
    return out[:limit] if limit else out


def get_payment(pid):
    """按 id 取一条流水；没有就返回 None。"""
    if not pid:
        return None
    with connect() as conn:
        row = conn.execute("SELECT * FROM payments WHERE id=?", (str(pid),)).fetchone()
    return dict(row) if row else None


def insert_payment(conn, fields, pid=None):
    """
    插一条收入流水，返回它的 id。

    ⚠️ 这个函数**要在 `with _db_lock` 里面调用**，而且里面只做 `conn.execute` ——
       它不调用任何会写库的函数（比如 set_store / get_customer）。
       理由见「坑 21」：_db_lock 是普通 Lock，不可重入，在它里面再拿一次就是全站卡死。
       （字段的清洗在调用它之前、锁的外面做完，见 clean_payment。）
    """
    pid = pid or uuid.uuid4().hex
    conn.execute("""INSERT INTO payments(id, date, amount, zone, item, customer, customer_id,
                    source, ref, note, created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                 (pid, fields["date"], fields["amount"], fields["zone"], fields["item"],
                  fields["customer"], fields["customer_id"], fields["source"], fields["ref"],
                  fields["note"], int(fields.get("created_at") or time.time() * 1000)))
    return pid


def payment_exists(source, ref):
    """
    ★ 幂等检查：这个 source + ref 是不是已经记过一笔了？

    ref 是"这条流水对应哪张单据"：
      · 确认预约 → 锁的 id（一条锁只该有一条 booking 流水）
      · 续费     → 客户id + 新到期日（同一个客户续到同一天，只算一次）
      · 手动补记 → 空（ref 为空的**不参与**幂等检查，见下面）
    ref 为空时**永远返回 False**：手动记账没有单据号，不能因为"两条都是空的"就当成重复。

    为什么必须有它：老板会反复点「已确认」（他可能不记得点过没有），
    恢复一次备份之后也可能再点一遍 —— 没有这道防线，流水里会出现两笔一模一样的钱。
    """
    ref = str(ref or "").strip()
    if not ref:
        return False
    with connect() as conn:
        row = conn.execute("SELECT 1 FROM payments WHERE source=? AND ref=? LIMIT 1",
                           (str(source), ref)).fetchone()
    return row is not None


def renew_payment_ref(cid, new_end):
    """
    续费流水的防重标记：`客户id@新到期日`。

    为什么用"新到期日"而不是"续费时间"：同一个客户同一个到期日，就是同一笔续费 ——
    网络卡了再点一次「确认续费」不该变成两笔钱。而续第二次一定会产生新的到期日，
    所以正常续费永远不会被误判成重复。
    """
    return "%s@%s" % (str(cid or ""), str(new_end or ""))


def record_payment(data, source, ref=""):
    """
    记一笔收入（**幂等**）：已经记过同一个 source+ref 就什么都不做。
    返回 (流水 or None, 说明文字)：说明文字是给日志/接口用的中文。

    ⚠️ 返回的流水是 `income_view()` 洗过的**视图**（带 amountText / label / sourceLabel），
       不是数据库那一行 —— 调用方（确认预约 / 续费）要拿它拼"已记一笔收入 ¥30 …"给人看，
       直接用原始行会 KeyError（这个坑我当场踩过，所以在这里写清楚）。

    这个方法会自己拿 _db_lock，所以**必须从 `with _db_lock` 外面调用**
    （第三刀最容易踩的坑就是这个：在 admin_schedule 的锁里直接调它会死锁）。
    """
    fields, err = clean_payment(dict(data, source=source, ref=ref))
    if err:
        return None, err
    if ref and payment_exists(source, ref):
        return None, "这笔已经记过了，不重复记（防重复记账）。"
    with _db_lock, connect() as conn:
        pid = insert_payment(conn, fields)
    return income_view(get_payment(pid)), "已记一笔"


def record_booking_payment(lock):
    """
    ★ 记账入口①：老板在后台点了「已确认」→ 自动记一笔。

    记什么：日期 = **这单开始那天的日期**（北京时间），金额 = 这条锁的 price。
    为什么按"开始那天"而不是"点确认那天"：他多半是打完之后才点确认的，
    按开始那天算，收入才会落在"这单真正发生"的那个月里（跨月的时候尤其明显）。

    两条不记的规矩（都是为了让流水干净）：
      · **只记 kind='booking' 的** —— 休息锁（rest）不是生意，别往收入里塞；
      · **price 是 0 或空的跳过** —— 老板自己锁的时段、免费补偿的单子都不该出现
        "¥0"这种流水（那只会让统计表变脏，一点信息量都没有）。

    返回 (流水 or None, 说明文字)。
    """
    lock = lock or {}
    if str(lock.get("kind") or "") != "booking":
        return None, "这不是预约单（休息锁不记收入）。"
    try:
        price = float(lock.get("price") or 0)
    except (TypeError, ValueError):
        price = 0
    if price <= 0:
        return None, "这条预约没有金额（price=0），不记流水。"
    lock_id = str(lock.get("id") or "")
    start_ms = int(lock.get("start") or 0)
    if start_ms <= 0:
        return None, "这条预约没有开始时间，记不了账。"
    zone_id = str(lock.get("zone") or "")
    return record_payment({
        "date": datetime.fromtimestamp(start_ms / 1000, CN).strftime("%Y-%m-%d"),
        "amount": price,
        "zone": zone_id,
        "item": str(lock.get("mode") or ""),
        "customer": str(lock.get("client") or lock.get("contact") or ""),
        "note": "确认预约自动记账",
    }, "booking", lock_id)


def income_stats(pay_range="month", today=None):
    """
    ★ 收入统计：收入页上所有数字都从这里来。

    返回的字段（前端照着画就行，不在前端重算任何东西）：
      range / rangeLabel   这次是哪个范围（month / last-month / 3m / year / all）
      start / end          范围的边界（闭区间；空字符串 = 不设限）
      total / count        这个范围的总额（元）和笔数
      prevTotal / prevLabel 上一个月的总额（用来算"比上月 +18%"）
      momPercent / momText  环比：算不出来时 momPercent 是 None（前端显示"上月没有记录"）
      categories [{id,name,icon,amount,count}]  按大类分的小卡（含"未分类"）
      topServices [{name, amount, count}]       哪个服务最来钱（前 5）
      topCustomers [{name, amount, count}]      哪个客户最舍得（前 5）
      records      这个范围的流水（新的在前，最多 INCOME_LIST_LIMIT 条）
      recordTotal  这个范围总共有几条（> records 的长度就说明被截断了）
      allTotal / allCount  全部收入（不设范围，用来在空状态里给一句"累计"）

    为什么环比用"上一个自然月"而不是"上一个等长区间"：
    主人想的是"这个月比上个月怎么样"，不是"最近 30 天比前 30 天"。
    （"上月"那个按钮用的也是同一个上一个自然月，两处口径一致。）
    """
    today = today or today_cn()
    if pay_range not in INCOME_RANGES:
        pay_range = "month"
    start, end = income_range_bounds(pay_range, today)

    payments = list_payments()                 # 全部（倒序）
    zones = payment_zone_map()
    categories = list_categories()

    cat_rows, cat_seen = [], {}
    for cat in categories:
        cat_seen[cat["id"]] = {"id": cat["id"], "name": cat["name"],
                               "icon": INCOME_CATEGORY_ICONS.get(cat["name"], "🎯"),
                               "amount": 0.0, "count": 0}
        cat_rows.append(cat_seen[cat["id"]])
    uncategorized = {"id": "", "name": PAYMENT_UNCATEGORIZED,
                     "icon": PAYMENT_UNCATEGORIZED_ICON, "amount": 0.0, "count": 0}

    service_totals, customer_totals = {}, {}
    records, total, count = [], 0.0, 0
    all_total, all_count = 0.0, 0
    for pay in payments:
        amount = float(pay.get("amount") or 0)
        all_total += amount
        all_count += 1
        if not payment_in_range(str(pay.get("date") or ""), start, end):
            continue
        count += 1
        total += amount
        # 归到哪个大类：这条流水的分区属于哪个大类（分区没了或者是空的 → "未分类"）
        cat_id = payment_category_label(pay.get("zone"), zones, categories)[0]
        bucket = cat_seen.get(cat_id) or uncategorized
        bucket["amount"] += amount
        bucket["count"] += 1
        zname = payment_zone_label(pay.get("zone"), zones) or PAYMENT_UNCATEGORIZED
        svc = service_totals.setdefault(zname, {"name": zname, "amount": 0.0, "count": 0})
        svc["amount"] += amount
        svc["count"] += 1
        cname = str(pay.get("customer") or "").strip()
        if cname:
            who = customer_totals.setdefault(cname, {"name": cname, "amount": 0.0, "count": 0})
            who["amount"] += amount
            who["count"] += 1
        if len(records) < INCOME_LIST_LIMIT:
            records.append(income_view(pay, zones))

    # 小卡上的 0 元大类不显示（空着的按钮只会占地方），但"未分类"有金额时一定显示
    cat_rows = [c for c in cat_rows if c["count"]] + ([uncategorized] if uncategorized["count"] else [])

    # 上一个自然月（环比用）
    first = month_start_text(today)
    prev_start = add_months_text(first, -1)
    prev_end = add_days_text(first, -1)
    prev_total, prev_count = 0.0, 0
    for pay in payments:
        if payment_in_range(str(pay.get("date") or ""), prev_start, prev_end):
            prev_total += float(pay.get("amount") or 0)
            prev_count += 1
    # 上月一笔都没有 → momPercent 保持 None，**绝不显示 0%**（那是骗人：
    # "从无到有"和"没变化"完全是两件事）
    mom_percent = None
    if prev_count > 0:
        mom_percent = int(round((total - prev_total) / prev_total * 100)) if prev_total else None
    if prev_count == 0:
        mom_text = "上月没有记录"
    elif prev_total == 0:
        mom_text = "上月没收到钱，这个月有进账"
    else:
        mom_text = "比上月 %s%d%%" % ("+" if mom_percent >= 0 else "", mom_percent)

    def top(items):
        return sorted(items, key=lambda x: (-x["amount"], x["name"]))[:INCOME_TOP_N]

    return {
        "range": pay_range,
        "rangeLabel": INCOME_RANGE_NAMES.get(pay_range, "本月"),
        "start": start, "end": end, "today": today,
        "total": round(total, 2), "count": count,
        "prevTotal": round(prev_total, 2), "prevCount": prev_count,
        "prevLabel": prev_start[:7] if prev_start else "",
        "momPercent": mom_percent, "momText": mom_text,
        "categories": [dict(c, amount=round(c["amount"], 2)) for c in cat_rows],
        "topServices": top(list(service_totals.values())),
        "topCustomers": top(list(customer_totals.values())),
        "records": records, "recordTotal": count, "listLimit": INCOME_LIST_LIMIT,
        "allTotal": round(all_total, 2), "allCount": all_count,
    }


def income_view(pay, zones=None):
    """
    把数据库里的一行流水变成后台要显示的样子（金额、来源、服务名都翻成中文）。

    和 customer_view() 一个思路：**能算的全在后端算好**，前端只负责显示。
    前端要给人看的字段：date / amountText / zoneName / item / customer / sourceLabel / note。
    """
    zones = zones if zones is not None else payment_zone_map()
    amount = float(pay.get("amount") or 0)
    source = str(pay.get("source") or "manual")
    zone_name = payment_zone_label(pay.get("zone"), zones)
    item = str(pay.get("item") or "")
    # 表格里"服务 / 项目"那一格：有分区就"三角洲行动 · 机密·零号大坝"，
    # 只有其中一个就只显示那一个，都没有就留一个破折号（别留空白格子）
    if zone_name and item:
        label = "%s · %s" % (zone_name, item)
    else:
        label = zone_name or item or "—"
    return {
        "id": pay.get("id"),
        "date": str(pay.get("date") or ""),
        "amount": amount,
        "amountText": income_amount(amount),
        "zone": str(pay.get("zone") or ""),
        "zoneName": zone_name,
        "item": item,
        "label": label,
        "customer": str(pay.get("customer") or ""),
        "customer_id": str(pay.get("customer_id") or ""),
        "source": source,
        "sourceLabel": PAYMENT_SOURCE_NAMES.get(source, PAYMENT_SOURCE_NAMES["manual"]),
        "ref": str(pay.get("ref") or ""),
        "note": str(pay.get("note") or ""),
        "created_at": int(pay.get("created_at") or 0),
    }


def income_payload(pay_range="month", today=None, with_list=True):
    """
    收入页要的全部数据，一个字典装完（/api/admin、/api/admin/payments、
    以及每个会动到钱的接口都回它）。

    为什么要打包成一个函数：收入页上有 6 块内容（大字、小卡、两个排行、流水表、空状态），
    它们必须**同一时刻的口径**。分开查两次库，就可能出现"大字说 ¥1240，明细加起来 ¥1230"
    这种自己跟自己打架的情况（第二刀的 pendingLocks 也是同一个套路）。

    `with_list=False` 时只回统计、不回那条**长列表** ——
    点「已确认」/「续费」的响应里塞两千条流水纯属浪费（那是运营几年的量），
    所以那些接口只带统计，前端切到「收入」页时再单独拉一次全量。
    """
    today = today or today_cn()
    pay_range = pay_range if pay_range in INCOME_RANGES else "month"
    out = {
        "income": income_stats(pay_range, today),
        "paymentSources": PAYMENT_SOURCE_NAMES,
        "incomeRanges": [{"id": r, "name": INCOME_RANGE_NAMES[r]} for r in INCOME_RANGES],
        "today": today,
    }
    if with_list:
        out["payments"] = list_payments(limit=PAYMENT_LIST_MAX)
    return out


# ---------- 备份与恢复（免费档重新部署会清空 data/，所以这两个函数很重要） ----------

MIME_BY_EXT = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png",
               "webp": "image/webp", "gif": "image/gif"}
MAX_BACKUP_IMAGE = 3 * 1024 * 1024      # 单张超过 3MB 就不塞进备份，避免备份文件过大
MAX_UPLOAD = 8 * 1024 * 1024            # 单张上传上限 8MB


PROFILE_IMAGE_KEYS = ("avatar", "wechatQr", "background", "heroImage")
# 全局资料里的图片：头像 / 微信二维码 / 首页背景图 / 主页首屏图
# ★ 这几个都要打进备份，否则换台电脑恢复后图就丢了


def zone_image_map(zone):
    """把某个分区资料里的 4 张图读成 {字段名: data:image/...}（备份/恢复共用）"""
    out = {}
    profile = merge_zone_profile(zone.get("profile"))
    for key in ZONE_IMAGE_KEYS:
        data_url = image_to_data_url(profile.get(key))
        if data_url:
            out[key] = data_url
    return out


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
    """
    导出全部业务数据。★ 两级分区之后，备份里多了 categories 和 zones：
      · categories —— 大类列表（改名/改类型/排序的结果）
      · zones      —— 每个分区的资料（价格、文案、主题色、是否隐藏…）
      · customers  —— ★ 客户档案（谁哪天到期、买的什么档位、多少钱）
      · zoneImages —— ★ 每个分区那 4 张图（背景/首屏/分享/价目表长图）本身
                      （不打包图片的话，恢复之后分区页会变成一片空白）
      · galleryCategories   —— ★ 展示墙分类（游戏截图/老板评价/作品…改过的名字和顺序）
      · autoReleaseHours    —— ★ "待确认超过几小时自动释放"的设置
      · autoReleased        —— ★ 最近自动释放了哪几单（顺便带走，恢复后还看得到）
      · payments            —— ★ 收入流水（第三刀）。理由见下面那段。
      老版本的备份里没有这几项，apply_backup() 会用默认值补上。

    ⚠️ **payments 必须跟着备份走**（这是第三刀最容易漏、后果最重的一项）：
       收入流水是"算出来的"，重建不了 —— 手动补记的那几笔（剪辑 / PS / 建站）
       连来源单据都没有，锁上的 price 以后还可能被改。
       少带这一项，换台电脑恢复之后就只剩"这个月 ¥0"，而且永远补不回来。
    """
    profile = get_profile()
    # 全局资料里的图片（头像 / 微信二维码）也一起打包，否则恢复后会变成空图
    profile_images = {}
    for key in PROFILE_IMAGE_KEYS:
        data_url = image_to_data_url(profile.get(key))
        if data_url:
            profile_images[key] = data_url
    # ★ 展示墙：link（外链）和 category（分类）都在这一行里，跟着备份一起走
    gallery = list_gallery()
    for g in gallery:
        g["imageData"] = image_to_data_url(g.get("image"))     # 展示墙图片一起打包
    zones = list_zones()
    zone_images = {}
    for z in zones:
        packed = zone_image_map(z)
        if packed:
            zone_images[z["id"]] = packed
    return {
        "app": "peiwan-site",
        "version": 2,
        "exportedAt": int(time.time() * 1000),
        "profile": profile,
        "profileImages": profile_images,
        "profileRevision": get_revision(),
        "categories": list_categories(),
        "zones": zones,
        "zoneImages": zone_images,
        "locks": list_locks(),              # ★ 每条锁都带 confirmed / confirmed_at
        "rules": list_rules(),
        "gallery": gallery,                 # ★ 每条都带 link / category / zone
        "customers": list_customers(),      # ★ 客户档案（老备份里没有这一项 → 恢复时跳过）
        "galleryCategories": list_gallery_categories(),   # ★ 展示墙分类
        "autoReleaseHours": get_auto_release_hours(),     # ★ 自动释放设置
        "autoReleased": get_auto_released_log(),          # ★ 最近自动释放记录
        "payments": list_payments(),        # ★ 收入流水（老备份里没有这一项 → 恢复时跳过）
        # ★ 文案表：这里放的是**主人改过的那几句**（没改的不占地方，也不写进来）。
        #   老备份里没有这一项 → 恢复时跳过，绝不清空他现在的话。
        "texts": get_text_overrides(),
        # ★ 服务条款：整份都带上（标题 + 小节 + 最后更新日期）。
        #   老备份里没有这一项 → 同样跳过，他改过的条款不会被一份老备份冲掉。
        "terms": terms_view(),
    }


def apply_backup(data):
    """
    把备份写回数据库。返回 (摘要, 错误信息)。会整体覆盖现在的数据。

    ★ 两级分区：
      · 备份里有 categories/zones → 大类、分区、分区资料、分区图片全部恢复
      · 备份里没有 zones（老备份文件）→ 只恢复全局资料和时段，
        分区表原样保留（不会把主人辛苦配好的分区清空）
      · 备份里没有 customers / payments（老备份文件）→ 那两张表**一条都不动**，
        恢复照常成功（见下面 has_customers / has_payments 那两段）
    """
    if not isinstance(data, dict) or not isinstance(data.get("profile"), dict):
        return None, "备份文件格式不正确。"

    profile = dict(DEFAULT_PROFILE)
    profile.update(data["profile"])

    locks, rules, gallery = [], [], []
    images = 0

    # ---- 把全局资料里的图片还原回磁盘 ----
    for key, data_url in (data.get("profileImages") or {}).items():
        if key not in PROFILE_IMAGE_KEYS:
            continue
        url, _err = save_data_url(data_url)
        if url:
            profile[key] = url
            images += 1

    # ---- 大类 / 分区（老备份里没有这两项 → 保持现状，不覆盖）----
    categories = data.get("categories")
    if isinstance(categories, list) and categories:
        cleaned = clean_categories(categories) or [dict(c) for c in DEFAULT_CATEGORIES]
        set_categories(cleaned)

    # ---- ★ 展示墙分类（老备份里没有这一项 → 保持现状）----
    # 和上面的 categories 同一个套路：备份里有就整体替换，没有就一条都不动。
    # 一条都不动是对的：主人辛苦调好的"游戏截图 / 老板评价 / 作品"不该被一份老备份冲掉。
    backup_gallery_cats = data.get("galleryCategories")
    if isinstance(backup_gallery_cats, list) and backup_gallery_cats:
        cleaned_cats = clean_gallery_categories(backup_gallery_cats)
        if cleaned_cats:
            set_gallery_categories(cleaned_cats)

    # ---- ★ 自动释放设置（老备份里没有这一项 → 保持现状，也就是默认的"关闭"）----
    if data.get("autoReleaseHours") is not None:
        set_auto_release_hours(data.get("autoReleaseHours"))
    if isinstance(data.get("autoReleased"), list):
        _set_auto_released_log(clean_auto_released_log(data["autoReleased"]))

    # ---- ★ 文案表（老备份里没有这一项 → 一句都不动）----
    # 和 customers / payments 同一条规矩，理由也一样：
    # 主人一句一句改出来的口气（"看看我能做什么"改成他自己的说法）没有第二份，
    # 一份老备份不该把它冲回默认。**备份里有 texts 才覆盖**（可能是空字典 = 全是默认）。
    has_texts = isinstance(data.get("texts"), dict)
    if has_texts:
        set_store(TEXTS_STORE_KEY, json.dumps(clean_texts(data["texts"]), ensure_ascii=False))

    # ---- ★ 服务条款（老备份里没有这一项 → 一个字都不动）----
    has_terms = isinstance(data.get("terms"), dict)
    if has_terms:
        # ⚠️ set_store 必须在下面那个 `with _db_lock` **之前**调（它自己也要拿这把锁）。
        set_store(TERMS_STORE_KEY,
                  json.dumps(clean_terms(data["terms"])[0], ensure_ascii=False))

    zones, zone_images = [], {}
    known_ids = {c["id"] for c in list_categories()}
    fallback_category = list_categories()[0]["id"] if known_ids else "play"
    incoming_zones = data.get("zones")
    has_zones = isinstance(incoming_zones, list) and bool(incoming_zones)
    if has_zones:
        zone_pics = data.get("zoneImages") or {}
        for i, z in enumerate(incoming_zones):
            if not isinstance(z, dict):
                continue
            zid = str(z.get("id") or "").strip().lower()
            if not ZONE_ID_RE.match(zid or ""):
                continue
            zone_profile = merge_zone_profile(z.get("profile"))
            # 分区的那 4 张图：先从备份里写回磁盘，再记下新的网址
            packed = zone_pics.get(zid) if isinstance(zone_pics, dict) else None
            for key, data_url in (packed or {}).items():
                if key not in ZONE_IMAGE_KEYS:
                    continue
                url, _err = save_data_url(data_url)
                if url:
                    zone_profile[key] = url
                    images += 1
            category = str(z.get("category") or "")
            if category not in known_ids:
                category = fallback_category
            zones.append((zid, category, str(z.get("name") or zid)[:30],
                          str(z.get("short") or "")[:12], str(z.get("icon") or "")[:300],
                          str(z.get("theme") or "")[:20],
                          int(z.get("sort") or (i + 1)), 1 if z.get("visible", True) else 0,
                          json.dumps(zone_profile, ensure_ascii=False),
                          int(z.get("created_at") or time.time() * 1000)))
        zone_images = {z[0]: z[8] for z in zones}

    default_zone_id = zones[0][0] if zones else (list_zones()[0]["id"] if list_zones() else "")

    for lk in data.get("locks") or []:
        # 新字段（source/mode/price/contact/created_at/zone/confirmed）缺席时用默认值，
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
        # ★ 老备份里没有 confirmed 字段 → 一律恢复成"已确认"。
        #   和 init_db() 的字段默认值同一个道理：不能让一次"恢复备份"凭空造出一堆待确认，
        #   那会把主人刚清空的待办列表又淹掉。备份里**显式**写了 0 的才恢复成待确认。
        try:
            confirmed = 0 if lk.get("confirmed") is not None and int(lk["confirmed"]) == 0 else 1
        except (TypeError, ValueError):
            confirmed = 1
        try:
            confirmed_at = max(0, int(lk.get("confirmed_at") or 0))
        except (TypeError, ValueError):
            confirmed_at = 0
        try:
            locks.append((str(lk.get("id") or uuid.uuid4().hex),
                          "rest" if lk.get("kind") == "rest" else "booking",
                          int(lk["start"]), int(lk["end"]),
                          str(lk.get("client") or "")[:40], str(lk.get("note") or "")[:200],
                          "client" if lk.get("source") == "client" else "owner",
                          str(lk.get("mode") or "")[:40], price,
                          str(lk.get("contact") or "")[:40], contact_type, created_at,
                          str(lk.get("zone") or "")[:32] or default_zone_id,
                          confirmed, confirmed_at))
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
        # ★ 分类：以前是写死的 screenshot/review，现在按备份里的分类表洗一遍。
        #   配置里没有的分类**保留原值**（前台有兜底，图不会消失）——
        #   这里不能像以前那样"不是 review 就当 screenshot"，
        #   否则一份带了「作品（work）」的备份导回来，作品会被悄悄混进游戏截图。
        gcat = str(g.get("category") or "").strip().lower()
        if not GALLERY_CAT_ID_RE.match(gcat or ""):
            gcat = DEFAULT_GALLERY_CATEGORY
        gallery.append((str(g.get("id") or uuid.uuid4().hex),
                        str(g.get("title") or "")[:60], str(g.get("caption") or "")[:300],
                        gcat, image, 1 if g.get("visible") else 0, 1 if g.get("archived") else 0,
                        int(g.get("createdAt") or time.time() * 1000),
                        str(g.get("zone") or "")[:32] or default_zone_id,
                        clean_link(g.get("link"))))     # ★ 外链一起恢复（顺手再洗一遍）

    # ---- ★ 客户档案 ----
    # ⚠️ 两个必须守住的行为：
    #  1. 老备份文件里**没有** customers 这一项 → has_customers 是 False →
    #     一条都不动（绝不清空主人现有的客户），恢复照常成功；
    #  2. 坏行（没名字 / 日期乱写）跳过或按规则置空，不让整个恢复失败 ——
    #     备份是"最后一道保险"，宁可少恢复一条，也不能整份导不进来。
    customers, has_customers = [], isinstance(data.get("customers"), list)
    for c in (data.get("customers") or []):
        if not isinstance(c, dict):
            continue
        fields, err = clean_customer(c)
        if err:
            continue
        customers.append((str(c.get("id") or uuid.uuid4().hex), fields["name"], fields["contact"],
                          fields["contact_type"], fields["zone"], fields["item"], fields["price"],
                          fields["start_date"], fields["end_date"], fields["status"], fields["note"],
                          int(c.get("created_at") or 0), int(c.get("updated_at") or 0)))

    # ---- ★ 收入流水 ----
    # ⚠️ 和客户档案**完全一样的两条规矩**（这是第三刀必须守住的行为）：
    #  1. 老备份文件里没有 payments 这一项 → has_payments 是 False → 一条都不动。
    #     **绝不清空主人现有的流水** —— 一份"第一刀"时期导出的备份，
    #     不该把他几个月记下来的收入抹掉；
    #  2. 坏行（金额不是数字 / 金额 <= 0 / 日期乱写）跳过，不让整个恢复失败。
    #     日期乱写的用 clean_payment 兜底成"恢复那天"，比丢掉一整笔钱强。
    payments, has_payments = [], isinstance(data.get("payments"), list)
    for p in (data.get("payments") or []):
        if not isinstance(p, dict):
            continue
        fields, err = clean_payment(p)
        if err:
            continue
        payments.append((str(p.get("id") or uuid.uuid4().hex), fields["date"], fields["amount"],
                         fields["zone"], fields["item"], fields["customer"], fields["customer_id"],
                         fields["source"], fields["ref"], fields["note"],
                         int(p.get("created_at") or 0)))

    with _db_lock, connect() as conn:
        conn.execute("DELETE FROM locks")
        conn.execute("DELETE FROM rules")
        conn.execute("DELETE FROM gallery")
        if has_customers:
            # 备份里带了客户档案 → 整体替换（和 locks/gallery 一样是"覆盖"语义）
            conn.execute("DELETE FROM customers")
            conn.executemany("""INSERT INTO customers(id, name, contact, contact_type, zone, item,
                                price, start_date, end_date, status, note, created_at, updated_at)
                                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""", customers)
        if has_payments:
            # 备份里带了收入流水 → 整体替换（老备份没带就一条都不动，见上面那段）
            conn.execute("DELETE FROM payments")
            conn.executemany("""INSERT INTO payments(id, date, amount, zone, item, customer,
                                customer_id, source, ref, note, created_at)
                                VALUES(?,?,?,?,?,?,?,?,?,?,?)""", payments)
        if zones:
            # 备份里带了分区 → 用备份里的分区表整体替换
            conn.execute("DELETE FROM zones")
            conn.executemany("""INSERT INTO zones(id, category, name, short, icon, theme, sort,
                                visible, profile, created_at) VALUES(?,?,?,?,?,?,?,?,?,?)""", zones)
        conn.executemany("""INSERT INTO locks(id, kind, start, end, client, note, source,
                            mode, price, contact, contact_type, created_at, zone,
                            confirmed, confirmed_at)
                            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", locks)
        conn.executemany("""INSERT INTO rules(id, days, start_hour, end_hour, from_ts, until_ts,
                            note, enabled) VALUES(?,?,?,?,?,?,?,?)""", rules)
        conn.executemany("""INSERT INTO gallery(id, title, caption, category, image, visible,
                            archived, created_at, zone, link)
                            VALUES(?,?,?,?,?,?,?,?,?,?)""", gallery)
        row = conn.execute("SELECT value FROM store WHERE key='revision'").fetchone()
        new_rev = (int(row["value"]) if row else 1) + 1
        conn.execute("UPDATE store SET value=? WHERE key='profile'",
                     (json.dumps(profile, ensure_ascii=False),))
        conn.execute("UPDATE store SET value=? WHERE key='revision'", (str(new_rev),))

    return {"locks": len(locks), "rules": len(rules), "gallery": len(gallery),
            "zones": len(zones), "categories": len(list_categories()),
            "customers": len(customers) if has_customers else len(list_customers()),
            "payments": len(payments) if has_payments else len(list_payments()),
            # 文案报"自定义了几句"（老备份没带就是现在库里的数），条款报"恢复了几节"
            "texts": len(get_text_overrides()),
            "terms": len((terms_view().get("sections") or [])),
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


def booking_head(zone, site):
    """
    预约文案的第一句。**游戏名取自分区自己的资料**（profile.game），
    接单平台也一样（profile.platform），所以守望分区的客户生成的是
    "想约守望先锋 PC 电脑端陪玩"，三角洲分区的是"想约三角洲行动 PC 电脑端陪玩"。

      分区 game=守望先锋 平台=PC 电脑端 → 你好，X！想约守望先锋 PC 电脑端陪玩。
      分区 game=守望先锋 平台留空       → 你好，X！想约守望先锋陪玩。
      分区 game 留空                    → 你好，X！我想约你的陪玩服务。
    """
    name = str(site.get("name") or "").strip()
    profile = zone_effective_profile(zone)
    game = str(profile.get("game") or "").strip()
    platform = str(profile.get("platform") or "").strip()
    what = " ".join(x for x in (game, platform) if x)
    if what:
        return "你好，%s！想约%s陪玩。" % (name, what)
    return "你好，%s！我想约你的陪玩服务。" % name


def booking_message(zone, site, mode_name, segments, total_hours, total_price,
                    contact, contact_type):
    """拼要推给老板的那段订单信息（客户预约成功后，老板收到的通知正文）"""
    lines = ["%d. %s–%s" % (i, cn_label(seg["start"]), cn_hhmm(seg["end"]))
             for i, seg in enumerate(segments, 1)]
    profile = zone_effective_profile(zone)
    game = str(profile.get("game") or "").strip()
    return "\n".join(filter(None, [
        booking_head(zone, site),
        ("项目：%s" % zone["name"]) if (zone and game and zone["name"] != game) else "",
        "模式：%s" % mode_name,
        "游玩时间（北京时间）：",
    ] + lines + [
        "共 %d 段、%d 小时，参考费用 ¥%d。" % (len(segments), total_hours, total_price),
        "我的%s：%s，加我确认一下就行。" % (contact_label(contact_type), contact),
    ]))


def create_booking(zone, hours, mode_name, contact, contact_type, note):
    """
    客户自助预约的核心：复查空档 → 分段 → 落库。
    返回一个字典：成功是 {"ok": True, "locks": [...], ...}；
    失败是 {"ok": False, "error": "中文原因", "status": 400/409}（前端按 status 处理）。
    空档复查和写入都在同一把写锁里，两个人同时点也只会有一个成功。

    ⚠️ 价格档位（prices）来自**这个分区自己的资料**，
       但空档状态来自**全局时间表** —— 两个分区的客户抢的是同一批小时。
    """
    site = site_summary()
    profile = zone_effective_profile(zone)
    mode = next((p for p in (profile.get("prices") or [])
                 if str(p.get("name")) == mode_name), None)
    if not mode:
        return {"ok": False, "error": "没有「%s」这个模式，请刷新页面重新选择。" % mode_name,
                "status": 400}
    unit_price = int(round(float(mode.get("price") or 0)))
    zone_id = zone["id"] if zone else ""

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
            # ★ confirmed=0：客户自助预约先算「待确认」，等老板在后台点一下才真正定下来。
            #   时间已经锁走了（别的客户约不到），但后台会一直提醒他"这单还没确认"。
            insert_lock(conn, id=uuid.uuid4().hex, kind="booking", start=seg["start"],
                        end=seg["end"], client=contact, note=note, source="client",
                        mode=mode_name, price=seg["price"], contact=contact,
                        contact_type=contact_type, created_at=now_ms, zone=zone_id,
                        confirmed=0, confirmed_at=0)

    total_hours = sum(seg["hours"] for seg in created)
    total_price = sum(seg["price"] for seg in created)
    return {"ok": True, "locks": created, "totalPrice": total_price, "totalHours": total_hours,
            "mode": mode_name, "contact": contact, "contactType": contact_type,
            "zone": zone_id, "zoneName": zone["name"] if zone else "",
            "message": booking_message(zone, site, mode_name, created, total_hours, total_price,
                                       contact, contact_type)}


def booking_notice(zone, mode_name, segments, total_hours, total_price,
                   contact, contact_type, note):
    """
    客户预约成功后，推给老板的内容（标题 + 正文）。
    标题里带"联系渠道"，一眼就知道去哪联系人；正文第一行写清是**哪个分区**的单
    （提醒文案要短，手机上才看得全，所以分区名放正文不放标题）。
    """
    span = "、".join("%s–%s" % (cn_label(seg["start"]), cn_hhmm(seg["end"])) for seg in segments)
    zone_name = zone["name"] if zone else "未分区"
    content = ("分区：%s\n模式：%s\n时间（北京时间）：%s\n共 %d 小时，合计 ¥%d\n"
               "联系方式：%s %s\n备注：%s"
               % (zone_name, mode_name, span, total_hours, total_price,
                  contact_label(contact_type), contact, note or "（没填）"))
    return "新的陪玩预约（%s）" % contact_label(contact_type), content


# 预约限流：{IP: [最近几次提交的时间戳]}，只放在内存里，进程一重启就重置（个人站够用了）
_book_hits = {}
_book_hits_lock = threading.Lock()


# ============================================================================
# 二之六、★ 「待确认」处理台 + 自动释放（第二刀）
#
#   客户在前台自助预约时，时间**立刻就锁走了**（这是主人指定的流程：点一下就占住）。
#   但"占住"不等于"这单成立"：他没加微信、联系不上，那段时间就白占着 ——
#   而时段就是库存，库存就是钱。
#
#   所以客户自助预约默认是 **confirmed=0（待确认）**：
#     · 后台「预约与时间」最上面有一条醒目的待确认区，一眼看到"谁在等我"；
#     · 老板点「已确认」→ confirmed=1（这单就这样了）；
#     · 老板点「释放时段」→ 把锁删掉，时间回到可约（跟取消预约是一个效果）。
#
#   为什么老数据默认 confirmed=1：老库里的锁全是老板自己弄的，
#   升级的那一刻不该突然集体变成"待确认"（那会跳出几十条假待办）。
#
#   ⚠️ 自动释放（AUTO_RELEASE_DEFAULT_HOURS）**默认 0 = 关闭**，
#      必须由主人自己在后台打开 —— 悄悄删掉客户占的时间这种事，
#      不能替他做主（客户可能是半夜下的单，早上才看到）。
# ============================================================================

def lock_confirmed(lock):
    """这条锁算不算"已确认"？老数据里字段可能是 None/缺失，一律当成已确认（见上面那段说明）。"""
    value = lock.get("confirmed")
    return True if value is None else bool(int(value))


def pending_locks(now_ms=None):
    """
    所有"待确认"的锁，**紧急的排前面**（下单越早 = 等得越久 = 越该先处理）。

    条件（三个都要满足）：
      · source == 'client'  —— 只有客户自助预约才会"待确认"，老板自己锁的不用确认
      · confirmed 是 0      —— 还没点过「已确认」
      · end > now           —— 已经结束的单不再提醒（时间都过去了，确认没有意义）
    返回原始字段的 dict 列表（confirmed / confirmed_at 一起带着，后台直接看得到）。
    """
    now_ms = now_ms or int(time.time() * 1000)
    out = [dict(lk) for lk in list_locks()
           if lk.get("source") == "client" and not lock_confirmed(lk) and int(lk["end"]) > now_ms]
    out.sort(key=lambda lk: (int(lk.get("created_at") or 0), int(lk["start"])))
    return out


def pending_count(now_ms=None):
    """待确认的单有几条（后台顶部「今天待办条」用的就是这个数）。"""
    return len(pending_locks(now_ms))


def backdate_lock(lock_id, hours=0, created_at_ms=None):
    """
    把某条锁的 created_at 往前拨（**只给自动测试用**，别在正常流程里调它）。

    为什么测试需要它：自动释放的规则是"下单超过 N 小时还没确认就释放"，
    真等 24 小时没法测。所以测试把某条锁的 created_at 改成"24 小时前"，
    再触发一次检查 —— 效果和"真的等了 24 小时"完全一样。

    hours：往前拨几小时（会被换算成毫秒减掉）；created_at_ms：直接指定一个绝对时间。
    返回这次真的改到的行数（找不到那个 id 就是 0）。
    """
    if created_at_ms is None:
        created_at_ms = int(time.time() * 1000) - int(hours * HOUR_MS)
    with _db_lock, connect() as conn:
        cur = conn.execute("UPDATE locks SET created_at=? WHERE id=?",
                           (int(created_at_ms), str(lock_id)))
        return cur.rowcount



def get_auto_release_hours():
    """
    读"待确认超过 N 小时自动释放"的设置。**默认 0 = 关闭**。
    数据库里存的是文本，读坏了（有人手动改过库）也回 0 —— 宁可不动手，也不能乱删。
    """
    raw = get_store("auto_release_hours")
    try:
        hours = int(float(str(raw).strip()))
    except (TypeError, ValueError):
        return AUTO_RELEASE_DEFAULT_HOURS
    return max(0, min(AUTO_RELEASE_HOURS_MAX, hours))


def set_auto_release_hours(hours):
    """保存设置（0 = 关闭）。返回存进去的那个整数。"""
    try:
        value = int(float(hours or 0))
    except (TypeError, ValueError):
        value = 0
    value = max(0, min(AUTO_RELEASE_HOURS_MAX, value))
    set_store("auto_release_hours", str(value))
    return value


def get_auto_released_log():
    """
    读「最近自动释放」的日志（最新的在前）。

    为什么落库（store key='auto_released_log'）而不是只放在接口响应里：
    老板不一定在"自动释放刚发生"的那一秒打开后台 —— 他可能第二天才来看。
    存下来他随时能看到"昨天夜里悄悄释放了哪两单"，这是"删了东西必须留痕"的底线。
    只留最近 AUTO_RELEASE_LOG_MAX（10）条，不会越攒越多。
    """
    raw = get_store("auto_released_log")
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except ValueError:
        return []
    return [d for d in data if isinstance(d, dict)][:AUTO_RELEASE_LOG_MAX] if isinstance(data, list) else []


def _set_auto_released_log(items):
    set_store("auto_released_log", json.dumps(items[:AUTO_RELEASE_LOG_MAX], ensure_ascii=False))


def clean_auto_released_log(items):
    """
    清洗"最近自动释放"的日志（恢复备份时用）：只留认识的字段、坏行跳过。
    备份是最后一道保险，宁可少恢复一条记录，也不能让整份备份导不进来。
    顺便按时间倒序排好、只留最近 AUTO_RELEASE_LOG_MAX 条。
    """
    out = []
    for d in (items or []):
        if not isinstance(d, dict):
            continue
        try:
            start = int(d.get("start") or 0)
            at = int(d.get("at") or 0)
        except (TypeError, ValueError):
            continue
        if start <= 0:
            continue
        out.append({"name": str(d.get("name") or "")[:40], "start": start, "at": at,
                    "hours": max(0, min(AUTO_RELEASE_HOURS_MAX, int(d.get("hours") or 0)))})
    out.sort(key=lambda d: d["at"], reverse=True)
    return out[:AUTO_RELEASE_LOG_MAX]


# 惰性清理的节流时间戳（放内存里就够：进程重启后多查一次没有副作用）
_auto_release_check_at = [0.0]
# 上一次看到的设置值：设置一变就把节流清掉，这样刚点完"保存"立刻生效，不用等 30 秒
_auto_release_last_hours = [None]


def auto_release_expired(now_ms=None, force=False):
    """
    ★ 自动释放：把"待确认太久"的客户自助预约删掉（时间回到可约）。

    为什么是"惰性清理"而不是定时任务：这个站跑在免费托管上（Render / PythonAnywhere），
    **没有常驻的定时器**可言，加了 cron 也是又一件要维护的事。
    所以在 /api/public 和 /api/admin 每次请求时顺手看一眼就够了 ——
    老板一定会来后台看，客户一定会来刷时间表，检查的时机正好是需要它生效的时机。

    四道安全阀（**任何一个不满足都不删**，这是"删数据"的功能，必须保守）：
      1. 设置是 0 → 直接返回（默认就是 0，等于这个功能根本不存在）；
      2. 只删 source='client' 且 confirmed=0 的 —— 老板自己锁的、已经确认过的一律不碰；
      3. **只删 start > now 的**，也就是"还没开始的时间"；已经开始或已经过去的不动 ——
         客户昨天真的打过了，那单不能因为"你没点确认"就被抹掉；
      4. created_at 是 0 的老数据**不参与**（算不出等了多久，宁可不删）。

    返回这次真的删掉的条数。
    """
    hours = get_auto_release_hours()
    if hours <= 0:
        return 0
    now_ms = now_ms or int(time.time() * 1000)
    limit_ms = int(hours) * HOUR_MS
    if _auto_release_last_hours[0] != hours:
        _auto_release_check_at[0] = 0.0            # 设置刚改过 → 立刻查一次
        _auto_release_last_hours[0] = hours
    now_sec = time.time()
    if not force and now_sec - _auto_release_check_at[0] < AUTO_RELEASE_CHECK_SECONDS:
        return 0                      # 刚查过，别让每次刷新页面都去查一遍库
    _auto_release_check_at[0] = now_sec

    victims = []
    for lk in pending_locks(now_ms):
        created = int(lk.get("created_at") or 0)
        if created <= 0:
            continue
        if now_ms - created < limit_ms:
            continue
        if int(lk["start"]) <= now_ms:
            continue                  # ★ 第 3 道安全阀：已经开始的绝不碰
        victims.append(lk)
    if not victims:
        return 0

    released_at = int(time.time() * 1000)
    with _db_lock, connect() as conn:
        for lk in victims:
            conn.execute("DELETE FROM locks WHERE id=?", (lk["id"],))

    # 留痕：老板要能看到"刚才悄悄释放了哪几单"
    log = get_auto_released_log()
    for lk in victims:
        log.insert(0, {
            "name": str(lk.get("client") or "")[:40],
            "start": int(lk["start"]),
            "at": released_at,
            "hours": int(hours),
        })
    _set_auto_released_log(log)
    print("[自动释放] 待确认超过 %d 小时的客户预约已释放 %d 单：%s"
          % (hours, len(victims),
             "、".join("%s %s" % (str(lk.get("client") or "客户")[:20], cn_label(int(lk["start"])))
                       for lk in victims)), flush=True)
    return len(victims)


def pending_locks_view(now_ms=None):
    """
    给后台看的待确认列表：在 pending_locks() 的基础上补一句中文摘要。

    summary 是后台每行显示的那句人话，例如：
        「小明 · 微信 wx_xiaoming · 三角洲行动 · 机密·零号大坝 · 10月15日 20:00–22:00 · 2小时 ¥30」
    前端再在后面自己接一句"已经等了 N 小时"（N 用 created_at 算，见 admin.js 的 waitedText）。
    """
    now_ms = now_ms or int(time.time() * 1000)
    out = []
    for lk in pending_locks(now_ms):
        hours = max(1, int(round((int(lk["end"]) - int(lk["start"])) / float(HOUR_MS))))
        parts = [
            str(lk.get("client") or "（没填称呼）"),
            ("%s %s" % (contact_label(lk.get("contact_type")), lk.get("contact")))
            if lk.get("contact") else "没留联系方式",
        ]
        zone = get_zone(lk.get("zone")) if lk.get("zone") else None
        if zone:
            parts.append(zone["name"])
        if lk.get("mode"):
            parts.append(str(lk["mode"]))
        parts.append("%s–%s" % (cn_label(int(lk["start"])), cn_hhmm(int(lk["end"]))))
        parts.append("%d小时" % hours)
        if int(lk.get("price") or 0) > 0:
            parts.append("¥%d" % int(lk["price"]))
        if lk.get("note"):
            parts.append("备注%s" % str(lk["note"])[:60])
        item = dict(lk)
        item["confirmed"] = 0
        item["summary"] = " · ".join(str(p) for p in parts if str(p).strip())
        out.append(item)
    return out


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


_login_hits = {}                 # {ip: [失败时间戳, ...]}
_login_hits_lock = threading.Lock()


def login_rate_limited(ip, now=None):
    """
    同一个 IP 在 LOGIN_RATE_WINDOW 秒内密码错超过 LOGIN_RATE_MAX 次，就先拦住。
    返回 (是否被拦, 还要等几秒)。

    ⚠️ 只统计"密码错了"的次数 —— 正常登录（哪怕一天登几十次）完全不受影响。
    """
    now = now or time.time()
    with _login_hits_lock:
        hits = [t for t in _login_hits.get(ip, []) if now - t < LOGIN_RATE_WINDOW]
        _login_hits[ip] = hits
        if len(hits) >= LOGIN_RATE_MAX:
            return True, int(LOGIN_RATE_WINDOW - (now - hits[0])) + 1
        if len(_login_hits) > 5000:      # 字典别一直涨
            for key in [k for k, v in _login_hits.items() if not v or now - v[-1] >= LOGIN_RATE_WINDOW]:
                _login_hits.pop(key, None)
    return False, 0


def login_record_failure(ip, now=None):
    """记一次"密码错了" """
    now = now or time.time()
    with _login_hits_lock:
        _login_hits.setdefault(ip, []).append(now)


def login_reset(ip):
    """密码对了 → 把这个 IP 的失败记录清掉（免得之前手滑几次就一直背着）"""
    with _login_hits_lock:
        _login_hits.pop(ip, None)


# ============================================================================
# 二之七、★ 文案表 + 服务条款（客户能看到的每一句中文，都在这一章）
#
#   这一章的存在理由只有一个（主人的原话）：
#     "每一个客户能看到的中文文案，都必须能在后台改。"
#
#   两块东西：
#     · texts（store key='texts'）—— 78 句散落在首页 / 分区页 / 弹窗 / 页脚里的话，
#       目录写在「一、配置」的 TEXT_GROUPS 里（默认值就是以前写死的那句）。
#       **只存主人改过的那几句**，空 = 用默认（见 clean_texts）。
#     · terms（store key='terms'）—— 服务条款页（/terms）的标题 + 小节，
#       默认内容在「一、配置」的 DEFAULT_TERMS 里。
#
#   ⚠️ 写库的两条规矩（都是踩过的坑，别再犯）：
#     1. 这里所有函数**自己会拿 _db_lock**（走 set_store），
#        所以它们**绝不能被 from 到 `with _db_lock` 里面去调** —— 普通 Lock 不可重入，
#        在锁里再拿一次 = 全站卡死（见交接说明的坑 21 / 30）。
#     2. 备份恢复时这两项都要"备份里有才覆盖，没有就一条都不动" ——
#        老备份（还没有文案和条款这一版导出的）不该把主人刚改好的话冲掉。
# ============================================================================


def get_text_overrides():
    """
    读**主人自己改过的**那几句（原始覆盖值）。

    返回 {key: 文案}，键一定在目录里（读出来也洗一遍 —— 手改过数据库、
    或者跨版本升级留下了已经不存在的 key，都在这一步被清掉）。
    """
    raw = get_store(TEXTS_STORE_KEY)
    try:
        data = json.loads(raw) if raw else {}
    except ValueError:                 # 库里存了坏 JSON：当成"一句都没改过"
        data = {}
    return clean_texts(data)


def get_texts():
    """
    前台 / 后台要用的**完整文案表**：默认值打底，再把主人改过的覆盖上去。

    所以前台永远拿得到每一句（哪怕他一句都没改过），JS 那边不用自己写兜底；
    而"留空 = 用默认"这件事，也就在这一个地方生效。
    """
    texts = dict(DEFAULT_TEXTS)
    texts.update(get_text_overrides())
    return texts


def save_texts(raw):
    """
    保存文案。返回 (覆盖值, 一共自定义了几句)。

    规矩（和 clean_texts 一致）：空字符串、以及和默认一字不差的，都不存 ——
    这样"恢复默认"只需要把某一批 key 从这份覆盖表里删掉。
    """
    overrides = clean_texts(raw)
    set_store(TEXTS_STORE_KEY, json.dumps(overrides, ensure_ascii=False))
    return overrides, len(overrides)


def reset_texts(keys=None):
    """
    「恢复默认」：把指定的（或全部）文案从覆盖表里删掉，让它们回到默认那句话。

    传 keys（比如后台"① 主页"那一组的全部 key）= 只恢复这一组；
    不传 = 整页恢复默认。返回剩下的覆盖值。
    """
    overrides = get_text_overrides()
    if keys:
        for key in keys:
            overrides.pop(str(key), None)
    else:
        overrides = {}
    set_store(TEXTS_STORE_KEY, json.dumps(overrides, ensure_ascii=False))
    return overrides


def text_groups_for_admin():
    """
    给后台「文案」标签页用的目录：分组 + 每条的说明、默认值、现在生效的值。

    ★ 为什么把目录发给前端而不是在 admin.html 里手写 78 个输入框：
      一份目录三处用（后台渲染 / 前台取值 / 备份），加一句文案只要在这里加一行；
      写在 HTML 里的话，加一句要改三个文件，迟早对不上。
    """
    texts = get_texts()
    groups = []
    for group in TEXT_GROUPS:
        fields = []
        for f in group["fields"]:
            fields.append({
                "key": f["key"],
                "label": f["label"],
                "default": f["default"],
                "max": TEXT_LIMITS[f["key"]],
                "multiline": bool(f.get("multiline")),
                "value": texts[f["key"]],
                "custom": texts[f["key"]] != f["default"],   # 这一句主人改过没有
            })
        groups.append({"id": group["id"], "name": group["name"],
                       "hint": group.get("hint", ""), "fields": fields})
    return groups


def clean_terms(raw):
    """
    把服务条款洗干净。返回 (条款字典, 被丢掉的小节数)。

    和别的清洗函数一个风格：**宁可少一节，也不能整份存不进去**。
      · 小节标题和正文**都空**的 → 当成"这一节被你删掉了"，直接丢（不算错）；
      · 标题为空 → 退回默认标题（后台点「保存」时另有一道 400，见 admin_terms）；
      · 正文长度、小节数量都有上限。
    """
    raw = raw if isinstance(raw, dict) else {}
    title = str(raw.get("title") or "").strip()[:TERMS_TITLE_MAX] or DEFAULT_TERMS["title"]
    updated = str(raw.get("updated") or "").strip()[:20]
    sections, dropped = [], 0
    for item in (raw.get("sections") or []):
        if not isinstance(item, dict):
            dropped += 1
            continue
        heading = str(item.get("heading") or "").strip()[:TERMS_HEADING_MAX]
        body = str(item.get("body") or "").strip()[:TERMS_BODY_MAX]
        if not heading and not body:
            dropped += 1
            continue
        sections.append({"heading": heading, "body": body})
        if len(sections) >= TERMS_SECTION_MAX:
            break
    return {"title": title, "updated": updated, "sections": sections}, dropped


def get_terms_raw():
    """库里存着的条款（从没保存过就返回 None —— 那是"用出厂默认"的意思）"""
    raw = get_store(TERMS_STORE_KEY)
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def terms_view():
    """
    页面和后台要用的条款（出厂默认也会被补齐）。

    "最后更新"那行：存过就用存的那天；出厂默认（从没保存过）就用**今天** ——
    因为那份默认条款本来就是今天生成给他的，写个空日期或者很久以前的日期都不诚实。
    """
    stored = get_terms_raw()
    if stored is None:
        terms = json.loads(json.dumps(DEFAULT_TERMS))    # 深拷贝，别让调用方改到默认值
        terms["updated"] = today_cn()
        return terms
    terms, _dropped = clean_terms(stored)
    if not terms["updated"]:
        terms["updated"] = today_cn()
    return terms


def save_terms(raw):
    """
    保存条款。返回 (条款, 被丢掉的小节数)。

    ★ 「最后更新」是**服务器自己写上的**（北京时间今天）：主人每次点保存，
      日期就跟着变成那天 —— 不让他手填，是因为手填的日期百分之百会忘记改。
      （恢复备份时可以带一个明确的日期进来，那种情况就照他备份里的写。）
    """
    raw = raw if isinstance(raw, dict) else {}
    terms, dropped = clean_terms(raw)
    terms["updated"] = str(raw.get("updated") or "").strip()[:20] or today_cn()
    set_store(TERMS_STORE_KEY, json.dumps(terms, ensure_ascii=False))
    return terms, dropped


def reset_terms():
    """「恢复默认条款」：把库里的清掉，回到「一、配置」里那份出厂条款。"""
    set_store(TERMS_STORE_KEY, "")
    return terms_view()


def terms_fill_vars(text, wechat):
    """
    条款正文里的 **{wechat}** 换成主人的微信号。

    为什么要有它：默认条款的「七、怎么找我」里写着微信号。写死的话，
    主人一换微信号就得记得回来改条款 —— 忘了就会出现"条款上写着旧号"这种最尴尬的事。
    他要是没填微信号，就换成"页面顶部的「加微信」"，那句话永远是对的。
    """
    return str(text or "").replace("{wechat}", str(wechat or "").strip() or "页面顶部的「加微信」")


def terms_sections_html(terms, wechat):
    """
    把条款小节拼成 HTML（给 /terms 用）。

    ⚠️ 每一段都过 escape_html：**文案是纯文本，不认 HTML**。
       这里要是图省事直接拼字符串，条款正文就成了一个能注入 <script> 的地方
       （谁的账号登进来了谁就能改 —— 而主人是要把后台地址存进手机书签的）。

    换行的处理（主人写条款一定会用到）：
      · 空行 = 新的一段（<p>…</p>）；
      · 单换行 = 段内换行（<br>），所以"每行一条 ·"这种列表不会挤成一坨。
    """
    out = []
    for sec in terms.get("sections") or []:
        heading = escape_html(sec.get("heading") or "")
        body = escape_html(terms_fill_vars(sec.get("body") or "", wechat))
        paras = [p for p in re.split(r"\n{2,}", body) if p.strip()]
        body_html = "".join("<p>%s</p>" % p.replace("\n", "<br>") for p in paras)
        out.append('<section class="terms-section">%s%s</section>'
                   % (("<h2>%s</h2>" % heading) if heading else "", body_html))
    return "\n".join(out)


def terms_description(terms, wechat):
    """/terms 的微信分享简介：拿第一节正文的开头几句（空格压平、截断）"""
    for sec in terms.get("sections") or []:
        text = " ".join(terms_fill_vars(sec.get("body") or "", wechat).split())
        if text:
            return text[:120]
    return ""


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

    def share_image_url(self, zone=None):
        """
        微信分享缩略图挑哪张。**分区页优先用这个分区自己的图**，
        这样客户转发守望分区的链接时，显示的就是守望的图，不是三角洲的。

          分区页：分区 shareImage → 分区 heroImage → 该分区展示墙第一张公开图
                  → 全局头像 → 自带默认分享图
          个人主页：全局头像 → 第一个可见分区的 heroImage → 该分区展示墙第一张 → 默认图
        """
        site = site_summary()
        if zone:
            profile = merge_zone_profile(zone.get("profile"))
            for key in ("shareImage", "heroImage"):
                url = str(profile.get(key) or "")
                if url:
                    return url if url.startswith("http") else self.site_origin() + url
            for item in list_gallery(only_public=True, zone=zone["id"]):
                if item.get("image"):
                    return self.site_origin() + item["image"]
            url = str(site.get("avatar") or "")
            if url:
                return url if url.startswith("http") else self.site_origin() + url
            return self.site_origin() + "/static/img/share-default.jpg"
        url = str(site.get("avatar") or "")
        if url:
            return url if url.startswith("http") else self.site_origin() + url
        first = default_zone()
        if first:
            profile = merge_zone_profile(first.get("profile"))
            hero = str(profile.get("heroImage") or "")
            if hero:
                return hero if hero.startswith("http") else self.site_origin() + hero
            for item in list_gallery(only_public=True, zone=first["id"]):
                if item.get("image"):
                    return self.site_origin() + item["image"]
        for item in list_gallery(only_public=True):
            if item.get("image"):
                return self.site_origin() + item["image"]
        return self.site_origin() + "/static/img/share-default.jpg"

    def theme_style(self, theme):
        """
        把分区主色注入页面。

        做法：在 HTML 里插一段 <style>，覆盖 style.css 顶部的三个 CSS 变量：
          --accent      高亮色（按钮、边框、强调文字）
          --accent-rgb  "198, 240, 74" 这种分量写法，给 rgba() 用
                        （CSS 没法直接把 #rrggbb 拆成三个数字，所以在服务端算好）
          --accent-ink  主色上的文字颜色（亮底用深色字，暗底用白字）
        这样"每个分区一个主色"只需要改三个变量，样式表一行都不用动。
        颜色不合法（不是 #rrggbb）就返回空字符串，页面用默认主色。
        """
        color = str(theme or "").strip()
        if not re.fullmatch(r"#?[0-9A-Fa-f]{6}", color):
            return ""
        color = color if color.startswith("#") else "#" + color
        r, g, b = (int(color[i:i + 2], 16) for i in (1, 3, 5))
        # 感知亮度（人眼对绿色最敏感）：亮底配深字，暗底配白字
        luma = (0.299 * r + 0.587 * g + 0.114 * b)
        ink = "#10160f" if luma > 140 else "#ffffff"
        return ("<style>:root{--accent:%s;--accent-rgb:%d, %d, %d;--accent-ink:%s}</style>"
                % (color, r, g, b, ink))

    def nav_model(self):
        """
        顶部导航 + 首页服务卡片共用的数据：按大类分组的分区（只含可见的）。
        结构：[{大类信息, zones: [分区精简信息…]}…]，空大类不显示。
        """
        zones = [zone_brief(z) for z in list_zones(only_visible=True)]
        groups = []
        for cat in list_categories():
            members = [z for z in zones if z["category"] == cat["id"]]
            if members:
                groups.append({"id": cat["id"], "name": cat["name"], "kind": cat["kind"],
                               "hint": cat.get("hint") or "", "zones": members})
        return groups

    def render_page(self, template, zone=None):
        """
        把 HTML 模板里的 {{占位符}} 换成真实内容，再发出去。
        个人主页和分区页共用这一条路：传了 zone 就是分区页。
        """
        path = os.path.join(TEMPLATE_DIR, template)
        if not os.path.isfile(path):
            return self.send_json({"error": "缺少模板文件。"}, 500)
        with open(path, encoding="utf-8") as f:
            html = f.read()
        site = site_summary()
        name = str(site.get("name") or "个人服务")
        brand = str(site.get("brand") or name)
        if zone:
            profile = merge_zone_profile(zone.get("profile"))
            game = str(profile.get("game") or zone["name"])
            title = "%s · %s" % (game, name)
            desc = " ".join(str(profile.get("tagline") or profile.get("bio")
                                  or site.get("tagline") or "").split())
            if not desc:
                desc = "%s的%s服务：%s" % (name, game, zone["name"])
            og_url = "%s/z/%s" % (self.site_origin(), zone["id"])
            payload = {"zone": zone_public_view(zone), "site": site,
                       "nav": self.nav_model(), "mode": "zone"}
            theme = zone.get("theme") or DEFAULT_THEME
        else:
            tagline = str(site.get("tagline") or "")
            # 首页标题用**品牌名**打头（"Nya · 三角洲行动 · PC 端个人陪玩"），
            # 品牌是主人想让客户记住的那个词；昵称则出现在正文和「关于我」里。
            title = ("%s · %s" % (brand, tagline)) if tagline else brand
            desc = " ".join(str(site.get("tagline") or site.get("bio") or "").split())
            if not desc:
                desc = "%s 的个人服务主页：陪玩、代肝、剪辑……点进去看详情和价目。" % name
            og_url = self.site_origin() + "/"
            payload = {"site": site, "nav": self.nav_model(), "mode": "home"}
            theme = DEFAULT_THEME
        for key, value in (("{{TITLE}}", title), ("{{NAME}}", brand),
                           ("{{TAGLINE}}", str(site.get("tagline") or "")),
                           ("{{DESC}}", desc[:120]),
                           ("{{OG_IMAGE}}", self.share_image_url(zone)),
                           ("{{OG_URL}}", og_url),
                           ("{{THEME_STYLE}}", self.theme_style(theme)),
                           ("{{PAGE_JSON}}", json.dumps(payload, ensure_ascii=False)
                            .replace("</", "<\\/")),
                           ("{{ZONE_ID}}", zone["id"] if zone else "")):
            html = html.replace(key, escape_html(value) if key not in
                                ("{{THEME_STYLE}}", "{{PAGE_JSON}}") else value)
        html = re.sub(r"\{\{[A-Z_]+\}\}", "", html)      # 有漏掉的占位符就清掉，别让客户看到
        body = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")     # 改了资料立刻生效
        self.end_headers()
        self.write_body(body)

    def serve_index(self):
        """个人主页（品牌 + 我能做什么 + 信任信号 + 加微信）"""
        return self.render_page("index.html")

    def serve_terms(self):
        """
        ★ 服务条款页（/terms）—— 服务端渲染，**不用一行 JavaScript**。

        为什么单独一个方法、不复用 render_page：
          · render_page 是给"由 JS 画出来"的首页/分区页用的（它塞一份 __PAGE__ JSON）；
          · 条款页的内容（标题、小节）直接在服务器上拼好发出去，这样
            **客户不装 JS、搜索引擎爬、微信抓分享卡片，看到的都是完整内容**；
          · 分享卡片（og:title / og:description / og:image / og:url）也都在这拼好 ——
            条款页是要能直接发链接给客户的，卡片不能是空的。

        ⚠️ 所有从数据库里取出来的文字都过 escape_html（文案是纯文本，不认 HTML）。
        """
        path = os.path.join(TEMPLATE_DIR, "terms.html")
        if not os.path.isfile(path):
            return self.send_json({"error": "缺少模板文件。"}, 500)
        with open(path, encoding="utf-8") as f:
            html = f.read()
        site = site_summary()
        texts = site["texts"]
        terms = terms_view()
        wechat = str(site.get("wechat") or "")
        name = str(site.get("name") or "个人服务")
        title = "%s · %s" % (terms["title"], name)
        desc = terms_description(terms, wechat) or "我的服务说明：怎么下单、怎么付钱、账号安全、责任范围。"
        # 「最后更新：2026-10-14」——日期空着时整行不显示（不留一个光秃秃的"最后更新："）
        updated = ("%s%s" % (texts["termsUpdatedLabel"], terms.get("updated") or "")
                   if terms.get("updated") else "")
        # 底部微信号那一行：没填微信号就整行不要（不能显示一个空的"微信号："）
        wechat_line = ('<p class="done-wechat-id">%s<b>%s</b></p>'
                       % (escape_html(texts["wechatLabel"]), escape_html(wechat))) if wechat else ""
        payload = {
            "{{TITLE}}": title,
            "{{DESC}}": desc[:120],
            "{{OG_IMAGE}}": self.share_image_url(None),
            "{{OG_URL}}": self.site_origin() + "/terms",
            "{{THEME_STYLE}}": self.theme_style(DEFAULT_THEME),
            "{{BRAND}}": str(site.get("brand") or name),
            "{{NAME}}": name,
            "{{NAV_SERVICES}}": texts["navServices"],
            "{{NAV_ABOUT}}": texts["navAbout"],
            "{{NAV_TERMS}}": texts["navTerms"],
            "{{TERMS_TITLE}}": terms["title"],
            "{{TERMS_UPDATED}}": updated,
            "{{TERMS_SECTIONS}}": terms_sections_html(terms, wechat),
            "{{TERMS_BACK}}": texts["termsBackHome"],
            "{{WECHAT_LINE}}": wechat_line,
            "{{FOOTER_DISCLAIMER}}": texts["footerDisclaimer"],
            "{{FOOTER_TIP}}": texts["footerTip"],
        }
        for key, value in payload.items():
            # 只有这三样是我们自己拼的 HTML（里面的文字都已经 escape 过了），其余一律转义。
            # ⚠️ WECHAT_LINE 是一整段 <p>…</p>，忘了放进这一组的话，
            #    客户在条款页底部看到的就是一串源码（截图里真抓到过）。
            safe = key in ("{{THEME_STYLE}}", "{{TERMS_SECTIONS}}", "{{WECHAT_LINE}}")
            html = html.replace(key, value if safe else escape_html(value))
        html = re.sub(r"\{\{[A-Z_]+\}\}", "", html)      # 漏掉的占位符清掉，别给客户看到
        body = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")     # 改了条款立刻生效
        self.end_headers()
        self.write_body(body)

    def serve_zone(self, zone_id):
        """
        分区页：/z/delta、/z/overwatch……
        · 找不到这个分区 → 404
        · 分区被隐藏（visible=0）→ **仍然可以打开**（已经发出去的链接不能失效），
          只是它不会出现在首页和顶部导航里。
        """
        zone = get_zone(zone_id)
        if not zone:
            return self.send_json({"error": "找不到「%s」这个分区。" % str(zone_id)[:32]}, 404)
        return self.render_page("zone.html", zone)

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

    def public_payload(self, zone):
        """
        GET /api/public 的返回内容（分区页和个人主页共用）。

        ⚠️ **slots 和 zone 无关**：不管客户在哪个分区，返回的都是同一份 168 格 ——
           他只有一份时间。分区只影响"这一页显示什么资料、哪几张展示墙的图"。

        为了不让老前端（和老测试）失效，顶层还保留了 profile / gallery 两个键：
          · profile：全局资料 + 这个分区的资料（合并视图）
          · gallery：这个分区的公开图
        """
        site = site_summary()
        view = zone_public_view(zone) if zone else {
            "id": "", "name": "", "short": "", "icon": "", "theme": DEFAULT_THEME,
            "category": "", "kind": "booking", "sort": 0, "visible": True,
            "profile": dict(DEFAULT_ZONE_PROFILE), "startPrice": "",
            "share": site,
        }
        # 顶层这个 profile 是"老接口的兼容视图"：老前端只认识 /api/public 里的平铺字段。
        # 合并顺序 = 分区的资料打底，再用**全局资料里真的存了值的字段**盖上去
        #（全局的 game/platform/prices 是"单游戏时代"留下来的字段，老后台还在写它们，
        #  所以它们也得能生效 —— 分区自己填了就以分区为准，见 zone_public_view）。
        merged = dict(view["profile"])
        merged.update({k: v for k, v in get_profile().items() if v not in ("", None, [], {})})
        merged.update({k: v for k, v in site.items() if k in ("name", "brand", "wechat", "xianyu",
                                                              "avatar", "bio", "trust",
                                                              "wechatQr", "accepting")})
        merged["name"] = site["name"]
        merged["brand"] = site["brand"]
        merged["wechat"] = site["wechat"]
        merged["accepting"] = site["accepting"]
        return {
            "site": site,
            "profile": merged,
            "categories": list_categories(),
            "zones": [zone_brief(z) for z in list_zones(only_visible=True)],
            "zone": view,
            "nav": self.nav_model(),
            "slots": build_slots(),
            "gallery": list_gallery(only_public=True, zone=view["id"]) if view["id"] else [],
            # ★ 展示墙的分类（前台筛选按钮照它生成；不在配置里的历史分类会兜底补在最后）
            "galleryCategories": gallery_categories_for_public(),
            "serverTime": int(time.time() * 1000),
        }

    def do_GET(self):
        parsed = urlparse(self.path)
        # 浏览器会把中文文件名转成 %E8%83%8C... 这种编码，这里解码回来，
        # 否则你放一张"背景图.jpg"就会 404
        path, query = unquote(parsed.path), parse_qs(parsed.query)

        # 页面
        if path in ("/", "/index.html"):
            return self.serve_index()
        # ★ 服务条款页（/terms）。放在这里（和首页并列）：它只是个普通页面，
        #   不会和分区 id 抢网址（分区永远在 /z/ 下面）。
        if path in ("/terms", "/terms/"):
            return self.serve_terms()
        if path in (ADMIN_PATH, ADMIN_PATH + "/"):
            return self.send_file(os.path.join(TEMPLATE_DIR, "admin.html"), "text/html; charset=utf-8")
        # ⚠️ 旧地址 /admin 故意不给任何提示（不跳转、不提示"请改用新地址"）——
        #    否则等于告诉扫描器"后台搬到这儿了"。它现在就是个普通 404。
        # ★ 分区页：/z/delta、/z/overwatch……（放在后台、/static、/api 之后，
        #   所以分区 id 永远抢不走后台和接口的网址）
        if path == "/z" or path.startswith("/z/"):
            rest = unquote(parsed.path[2:]).strip("/")
            if not rest:
                return self.send_json({"error": "请指定分区，例如 /z/delta。"}, 404)
            return self.serve_zone(rest)

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
            # ★ 两级分区的主接口：?zone=delta 取哪个分区（不传就用第一个可见分区）
            # ★ 顺手做一次「自动释放」的惰性清理：客户在刷时间表的那一刻，
            #   正是"把过期没人认领的时段还回去"最该生效的时机（设置是 0 时什么都不做）
            auto_release_expired()
            zone, err = resolve_zone((query.get("zone") or [""])[0].strip())
            if err:
                return self.send_json({"error": err}, 404)
            return self.send_json(self.public_payload(zone))

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
            # ★ 顺手做一次「自动释放」的惰性清理（见 auto_release_expired 的说明）。
            #   放在这里是因为老板一定会来后台看；设置是 0（默认）时它什么都不做。
            auto_release_expired()
            notify = get_notify_config()
            zones = list_zones()
            return self.send_json({
                "profile": get_profile(),
                "profileRevision": get_revision(),
                "lastExportAt": int(get_store("lastExportAt") or 0),
                "dataDir": DATA_DIR,   # 让后台显示"数据放在哪里"
                "schedule": {"locks": list_locks(), "rules": list_rules()},
                "gallery": list_gallery(),
                "categories": list_categories(),        # ★ 大类（陪玩/代肝/其他服务）
                "zones": [zone_public_view(z) for z in zones],   # ★ 全部分区（含隐藏的）
                "zoneBriefs": [zone_brief(z) for z in zones],
                "site": site_summary(),
                "notifyChannel": notify["channel"],     # 预约通知：渠道 / 目标 / 上次结果
                "notifyTarget": notify["target"],
                "notifyLast": notify["last"],
                "customers": list_customers(),           # ★ 客户档案（已按紧急度排好）
                "customerStats": customer_stats(),       # ★ 顶部那 4 张统计卡
                "customerLevels": CUSTOMER_LEVELS,       # ★ 分档的中文名和颜色（前端上色用）
                "today": today_cn(),                     # ★ 北京时间今天（前端别自己算）
                "pendingLocks": pending_locks_view(),    # ★ 待确认的单（第二刀的核心）
                "autoReleaseHours": get_auto_release_hours(),   # ★ 0 = 不自动释放（默认）
                "autoReleased": get_auto_released_log(),        # ★ 最近自动释放了哪几单
                "galleryCategories": list_gallery_categories(),  # ★ 展示墙分类（可配置）
                "galleryCategoryCounts": gallery_category_counts(),
                "income": income_stats("month"),                 # ★ 收入统计（第三刀，默认看本月）
                "payments": list_payments(limit=PAYMENT_LIST_MAX),   # ★ 收入流水（老的新在前）
                "paymentSources": PAYMENT_SOURCE_NAMES,          # ★ 来源的中文名（前端上色用）
                "incomeRanges": [{"id": r, "name": INCOME_RANGE_NAMES[r]} for r in INCOME_RANGES],
                # ★ 文案表（第四刀）：完整文案 + 目录 + 主人改过的那几句 + 九宫格选项
                "texts": get_texts(),                     # 每一句的当前值（默认值已补齐）
                "textGroups": text_groups_for_admin(),    # 后台「文案」页照它生成输入框
                "textOverrides": get_text_overrides(),    # 只存了这几句（"恢复默认"用得上）
                "terms": terms_view(),                    # 服务条款（标题 + 小节）
                "positionChoices": [{"value": v, "name": n} for v, n in POSITION_CHOICES],
                "serverTime": int(time.time() * 1000),   # 前端算"已经等了 N 小时"要用它
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
            ip = self.client_ip()
            limited, wait = login_rate_limited(ip)
            if limited:
                # 密码错太多次了：先拦住，避免有人拿脚本一直猜
                return self.send_json(
                    {"error": "密码错误次数太多了，请 %d 秒后再试。" % wait}, 429)
            if not verify_password(str(body["password"])):
                login_record_failure(ip)
                time.sleep(0.6)                      # 稍微拖延，防止暴力猜密码
                return self.send_json({"error": "密码不正确。"}, 401)
            login_reset(ip)                          # 登录成功 → 清掉失败记录
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
        if path == "/api/admin/categories":              # ★ 大类：改名 / 改类型 / 排序
            return self.admin_categories(body)
        if path == "/api/admin/zones":                   # ★ 分区：增删改查 / 排序 / 显隐
            return self.admin_zones(body)
        if path == "/api/admin/import-prices":           # ★ 批量粘贴导入价目表
            return self.admin_import_prices(body)
        if path == "/api/admin/extract-color":           # ★ 从首屏图自动提取主色（PIL）
            return self.admin_extract_color(body)
        if path == "/api/admin/customers":               # ★ 客户档案（增删改 / 续费）
            return self.admin_customers(body)
        if path == "/api/admin/payments":                # ★ 收入流水（手动记一笔 / 改 / 删）
            return self.admin_payments(body)
        if path == "/api/admin/gallery-categories":      # ★ 展示墙分类（增删改排序）
            return self.admin_gallery_categories(body)
        if path == "/api/admin/texts":                   # ★ 文案表（保存 / 恢复默认）
            return self.admin_texts(body)
        if path == "/api/admin/terms":                   # ★ 服务条款（保存 / 恢复默认）
            return self.admin_terms(body)

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
            # ★ 三个「图片显示位置」入库前再洗一遍（前台那道校验只是省事，后端这道才是防线）：
            #   不是九宫格里的值就退回 center，绝不把奇怪的东西写进库里。
            for key in PROFILE_POSITION_KEYS:
                if key in profile:
                    profile[key] = clean_image_position(profile.get(key))
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
        请求：{"zone":"delta", "hours":[毫秒时间戳...], "mode":"机密 · 零号大坝",
               "contact":"联系方式", "contactType":"wechat|xianyu|qq", "note":"备注"}
        成功：{"ok":true, "locks":[…], "totalPrice":45, "totalHours":3, "mode":"…", "message":"微信文案"}
        失败状态码：400 参数不对 / 409 这几格被抢走了 / 429 提交太频繁

        ★ zone：分区 id。不传就用第一个可见分区（兼容老前端）。
          分区必须是"预约制"大类下的（kind=booking），否则返回 400 ——
          代肝、剪辑这种包干的服务本来就没有时间表，不该能来占时间。
        """
        if not isinstance(body, dict):
            return self.send_json({"error": "请用 JSON 格式提交预约。"}, 400)

        # 0. 先认分区：这次预约是"哪个招牌页"来的
        zone, err = resolve_zone(str(body.get("zone") or "").strip())
        if err:
            return self.send_json({"error": err}, 400)
        if zone_public_view(zone)["kind"] != "booking":
            return self.send_json(
                {"error": "「%s」是包干/服务类项目，不支持时段预约，加微信直接下单就行。"
                          % zone["name"]}, 400)

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
        result = create_booking(zone, hours, mode_name, contact, contact_type, note)
        if not result.get("ok"):
            payload = {"error": result.get("error") or "预约没有成功，请重试。"}
            if result.get("conflicts"):
                payload["conflicts"] = result["conflicts"]
            return self.send_json(payload, result.get("status") or 400)

        # 5. 通知老板（后台线程发，客户不用等）
        title, content = booking_notice(zone, result["mode"], result["locks"], result["totalHours"],
                                        result["totalPrice"], contact, contact_type, note)
        notify_owner(title, content)
        return self.send_json(result)

    # ---------- 管理动作 ----------

    def admin_schedule(self, body):
        """
        POST /api/admin/schedule —— 时段与规则的全部动作，靠 action 区分。

        其中两个是「待确认」处理台用的（见「二之六」那一章）：
          confirm-lock  {action, id} → 这条客户自助预约"我认了"：confirmed=1 + 记下确认时间
          release-lock  {action, id} → 释放这条待确认的单：把锁删掉，时间回到可约

        ⚠️ 为什么不复用 remove-lock 而要单独一个 release-lock：
          两者**做的事情确实一样**（都是 DELETE 一行），但语义完全不同 ——
          remove-lock 是"取消一个已经成立的预约"，release-lock 是"这单我还没接，把时间还给别人"。
          分开的好处有三个，都跟"以后改代码不出事"有关：
            1. 前端语义清楚：待确认区那一排按钮只发 release-lock，改动不会波及"取消预约"那条路；
            2. 将来想在"释放"时顺手做别的（发个通知、记一笔日志、回一句不同的话）不用再分叉；
            3. 后端能针对"释放"单独做校验 —— 比如**只允许释放待确认的单**（见下面），
               这样即使前端写错 id，也不会误删一条已经确认过的预约。
        """
        action = body.get("action")
        # ⚠️ 「自动释放设置」必须在 _db_lock **外面**处理：
        #    set_auto_release_hours() 里面会走 set_store()，而 set_store() 自己也要拿 _db_lock。
        #    _db_lock 是普通的 Lock（不是可重入的 RLock），在锁里面再拿一次 = **直接死锁**，
        #    整个网站会卡住不响应（这个坑我踩过一次，所以单独拎出来并写清楚）。
        if action == "set-auto-release":
            hours = set_auto_release_hours(body.get("hours"))
            return self.send_json({
                "ok": True, "autoReleaseHours": hours,
                "schedule": {"locks": list_locks(), "rules": list_rules()},
                "pendingLocks": pending_locks_view(),
                "autoReleased": get_auto_released_log(),
                "serverTime": int(time.time() * 1000),
            })
        # 后台自己锁的时段也可以标一个分区（可选），这样后台列表上能看到"这是哪个招牌卖的"
        lock_zone = str(body.get("zone") or "").strip()[:32]
        if lock_zone and not get_zone(lock_zone):
            lock_zone = ""
        released_note = ""
        # ★ 记账入口①：点「已确认」之后要自动记一笔收入（见「二之五」那一章）。
        #   ⚠️ 这里只是"记下待会儿要记哪一笔"，**绝对不能**在 with _db_lock 里面
        #      直接调 record_booking_payment() —— 它会自己拿 _db_lock，
        #      而 _db_lock 是普通 Lock（不可重入），那就等于全站卡死（坑 21）。
        #      所以真正的记账放在 with 结束之后，用下面这个变量把锁里的那条锁带出来。
        confirmed_lock = None
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
                                contact="", contact_type="", created_at=int(time.time() * 1000),
                                zone=lock_zone, confirmed=1, confirmed_at=0)

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
                                mode="", price=0, contact="", contact_type="", created_at=created,
                                zone=lock_zone, confirmed=1, confirmed_at=0)

            elif action == "confirm-lock":
                lock_id = body.get("id")
                row = conn.execute("SELECT * FROM locks WHERE id=?", (lock_id,)).fetchone()
                if not row:
                    return self.send_json({"error": "找不到这条预约（可能已经被删掉了）。"}, 404)
                conn.execute("UPDATE locks SET confirmed=1, confirmed_at=? WHERE id=?",
                             (int(time.time() * 1000), lock_id))
                # ★ 记账入口①：把这条锁带出 with（真正的记账在下面、锁外面做）
                confirmed_lock = dict(row)

            elif action == "release-lock":
                lock_id = body.get("id")
                row = conn.execute("SELECT * FROM locks WHERE id=?", (lock_id,)).fetchone()
                if not row:
                    return self.send_json({"error": "找不到这条预约（可能已经被删掉了）。"}, 404)
                lock = dict(row)
                # ★ 只允许释放"待确认"的单：万一前端传错 id，也绝不会删掉一条已经确认过的预约
                if lock.get("source") != "client" or lock_confirmed(lock):
                    return self.send_json(
                        {"error": "这条不是待确认的客户预约，不能在这里释放。"
                                  "要取消它请在上面的时段列表里点「取消」。"}, 400)
                conn.execute("DELETE FROM locks WHERE id=?", (lock_id,))
                released_note = "已释放 %s 的时段，这段时间重新开放预约。" % (
                    str(lock.get("client") or "这位客户")[:20])

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

        # ★ 记账入口①：确认预约 → 自动记一笔收入（**在 with _db_lock 外面做**，见上面的说明）。
        #   幂等由 record_payment 里那道 source+ref 检查兜住：老板反复点「已确认」、
        #   或者恢复备份之后又点一遍，都只会有一条流水（ref = 这条锁的 id）。
        income_note = ""
        if confirmed_lock is not None:
            payment, note = record_booking_payment(confirmed_lock)
            if payment:
                income_note = "已记一笔收入：%s %s" % (payment["amountText"], payment["label"])
            elif "没有金额" in note or "休息" in note:
                # 0 元 / 休息锁：这本来就是正常的，不用在界面上弹任何提示
                income_note = ""
            else:
                income_note = note

        # 每个动作都把"最新的全量"回给前端（时段 + 待确认 + 自动释放设置/日志 + 收入），
        # 前端拿到直接整块重画 —— 和 /api/admin/zones 一个套路，省得前端自己猜状态
        payload = {
            "ok": True,
            "schedule": {"locks": list_locks(), "rules": list_rules()},
            "pendingLocks": pending_locks_view(),
            "autoReleaseHours": get_auto_release_hours(),
            "autoReleased": get_auto_released_log(),
            "serverTime": int(time.time() * 1000),      # 前端算"已经等了 N 小时"要用它
        }
        if confirmed_lock is not None:
            # 顺手把收入统计一起回给前端（后台顶部/收入页的数字要跟着变）。
            # 只带统计不带长列表：确认一单不该拖两千条流水回来。
            payload.update(income_payload("month", with_list=False))
        if released_note:
            payload["message"] = released_note
        if income_note:
            # 前端把它当一句普通提示 toast 出来（不是错误）
            payload["message"] = (payload.get("message") or "") or income_note
        return self.send_json(payload)


    def admin_gallery_create(self, body):
        """
        新增一张展示墙图片。★ 现在必须知道"这张图属于哪个分区"：
        传了 zone 用传的，没传就挂到第一个（默认）分区上。

        link：点开这张图要跳去哪（B 站视频地址 / 你做的网站）。选填，
        只有 http:// 和 https:// 开头才认（见 clean_link 里那段防 XSS 的说明）。
        """
        title = str(body.get("title") or "")[:60]
        caption = str(body.get("caption") or "")[:300]
        # ★ 分类从"写死的两个"变成"配置里有的"：不在配置里的一律退回第一个分类
        category = str(body.get("category") or "").strip()
        cats = list_gallery_categories()
        if category not in {c["id"] for c in cats}:
            category = cats[0]["id"] if cats else DEFAULT_GALLERY_CATEGORY
        link = clean_link(body.get("link"))
        visible = 1 if body.get("visible") else 0
        zone_id = str(body.get("zone") or "").strip()[:32]
        if zone_id:
            if not get_zone(zone_id):
                return self.send_json({"error": "找不到「%s」这个分区。" % zone_id}, 400)
        else:
            first = default_zone()
            zone_id = first["id"] if first else ""
        if not title:
            return self.send_json({"error": "请填写标题。"}, 400)
        url, error = save_data_url(body.get("image") or "")
        if error:
            return self.send_json({"error": error}, 400)
        with _db_lock, connect() as conn:
            conn.execute("""INSERT INTO gallery(id, title, caption, category, image, visible,
                            archived, created_at, zone, link) VALUES(?,?,?,?,?,?,0,?,?,?)""",
                         (uuid.uuid4().hex, title, caption, category, url,
                          visible, int(time.time() * 1000), zone_id, link))
        return self.send_json({"ok": True, "gallery": list_gallery(),
                               "zoneGallery": list_gallery(zone=zone_id),
                               "galleryCategories": list_gallery_categories()})

    def admin_media_upload(self, body):
        """通用图片上传：头像、微信二维码、分区背景图/首屏图/分享图都用它。只返回网址，不改资料。"""
        url, error = save_data_url(body.get("image") or "")
        if error:
            return self.send_json({"error": error}, 400)
        return self.send_json({"ok": True, "url": url})

    def admin_gallery_update(self, body):
        """
        改一张已有的展示墙图片（PATCH /api/admin/gallery）。
        这里也是"部分更新"：**只改 body 里出现的字段**，没传的保持原样。
        link 传空字符串就是"取消外链"；传 javascript: 这种会被 clean_link 洗成空。
        """
        gid = body.get("id")
        fields, values = [], []
        for key in ("title", "caption"):
            if key in body:
                fields.append("%s=?" % key)
                values.append(str(body[key])[:300])
        if "category" in body:
            # 分类必须是配置里存在的；不认识的退回第一个分类（和新增时一个规矩）
            category = str(body["category"] or "").strip()
            cats = list_gallery_categories()
            if category not in {c["id"] for c in cats}:
                category = cats[0]["id"] if cats else DEFAULT_GALLERY_CATEGORY
            fields.append("category=?")
            values.append(category)
        if "link" in body:
            # ★ 外链再洗一遍：前端卡了，后端也必须卡（前端可以被绕过）
            fields.append("link=?")
            values.append(clean_link(body["link"]))
        if "zone" in body:                     # 换分区（把图挪到另一个招牌下去）
            zone_id = str(body["zone"] or "")[:32]
            if zone_id and not get_zone(zone_id):
                return self.send_json({"error": "找不到「%s」这个分区。" % zone_id}, 400)
            fields.append("zone=?")
            values.append(zone_id)
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

    def admin_gallery_categories(self, body):
        """
        POST /api/admin/gallery-categories —— 展示墙分类的增删改排序。
        写法照抄 admin_categories（大类那一套），只有一处不同：**这里允许删除**。

          save   改名 / 排序 {action, id, name, sort?}   ★ id 不许改（改了老图就对不上号）
          create 新增分类   {action, name}                → id 由后端从名字生成，不合规才退回编号
          sort   上下移动   {action, order:[id,…]}
          delete 删除分类   {action, id}

        删除的取舍（★ 我选的是"拦住不让删"，理由在下面 delete 那一段里写清了）。
        """
        action = str(body.get("action") or "save")
        cats = list_gallery_categories()
        known = {c["id"] for c in cats}

        if action == "save":
            cid = str(body.get("id") or "").strip()
            target = next((c for c in cats if c["id"] == cid), None)
            if not target:
                return self.send_json({"error": "找不到「%s」这个分类。" % cid[:20]}, 400)
            if "name" in body:
                name = str(body.get("name") or "").strip()[:20]
                if not name:
                    return self.send_json({"error": "分类名字不能是空的。"}, 400)
                target["name"] = name
            if "sort" in body:
                try:
                    target["sort"] = int(body["sort"])
                except (TypeError, ValueError):
                    pass
            set_gallery_categories(cats)
            return self.send_json({"ok": True, "galleryCategories": list_gallery_categories(),
                                   "galleryCategoryCounts": gallery_category_counts()})

        if action == "create":
            name = str(body.get("name") or "").strip()[:20]
            if not name:
                return self.send_json({"error": "请填写分类名字（例如：作品）。"}, 400)
            cid = str(body.get("id") or "").strip().lower()
            if not GALLERY_CAT_ID_RE.match(cid or ""):
                cid = suggest_gallery_category_id(name, known)
            if cid in known:
                return self.send_json(
                    {"error": "已经有一个叫「%s」的分类了（id=%s），换个名字或换个短标识。"
                              % (name, cid)}, 400)
            cats.append({"id": cid, "name": name, "sort": max([c["sort"] for c in cats] or [0]) + 1})
            set_gallery_categories(cats)
            return self.send_json({"ok": True, "id": cid,
                                   "galleryCategories": list_gallery_categories(),
                                   "galleryCategoryCounts": gallery_category_counts()})

        if action == "sort":
            order = [str(x).strip() for x in (body.get("order") or [])]
            seq = [cid for cid in order if cid in known] + [c["id"] for c in cats if c["id"] not in order]
            for i, cid in enumerate(seq, 1):
                next(c for c in cats if c["id"] == cid)["sort"] = i
            set_gallery_categories(cats)
            return self.send_json({"ok": True, "galleryCategories": list_gallery_categories(),
                                   "galleryCategoryCounts": gallery_category_counts()})

        if action == "delete":
            # ★ 取舍：**不允许删掉"还有图片在用"的分类**（不自动把图搬家）。
            #   理由：展示墙的图是主人一张张挑出来给客户看的，自动把它们归到"第一个分类"
            #   会悄悄改变客户看到的分组（比如把"作品"混进"游戏截图"），而主人不会意识到
            #   自己刚才那一下动了什么。宁可让他先手动把图挪走 —— 5 张图点 5 下，
            #   但每一步他都看得见。空分类随便删，一点都不啰嗦。
            cid = str(body.get("id") or "").strip()
            target = next((c for c in cats if c["id"] == cid), None)
            if not target:
                return self.send_json({"error": "找不到「%s」这个分类。" % cid[:20]}, 400)
            if len(cats) <= 1:
                return self.send_json(
                    {"error": "至少要留一个分类（不然展示墙的图会没有归类）。"}, 400)
            used = gallery_category_counts().get(cid, 0)
            if used:
                return self.send_json(
                    {"error": "「%s」下面还有 %d 张图在用，不能删除。"
                              "请先把这些图改到别的分类（在下面列表里点「编辑」），"
                              "或者把它们删掉/归档，再回来删这个分类。"
                              % (target["name"], used)}, 400)
            cats = [c for c in cats if c["id"] != cid]
            for i, c in enumerate(cats, 1):
                c["sort"] = i
            set_gallery_categories(cats)
            return self.send_json({"ok": True, "deleted": target["name"],
                                   "galleryCategories": list_gallery_categories(),
                                   "galleryCategoryCounts": gallery_category_counts()})

        return self.send_json({"error": "不支持的操作。"}, 400)

    def admin_texts(self, body):
        """
        POST /api/admin/texts —— ★ 文案表（后台「文案」标签页）。

          save   保存   {action:"save", texts:{key: 文案, …}}
                        （只发改动过的也行：没出现在 texts 里的 key 保持原样 ——
                          但后台的「保存文案」是整页一起发，这样"清空某一格 = 回默认"最直观）
          reset  恢复默认 {action:"reset", keys:["heroIntro", …]}  ← 不传 keys = 整页恢复

        返回：{"ok":true, "texts":完整文案表, "textOverrides":改过的那几句, "saved"/"reset":几条}
        前端拿到直接重画那一页，不用自己猜状态（和 /api/admin/zones 一个套路）。
        """
        action = str(body.get("action") or "save")

        if action == "reset":
            keys = body.get("keys")
            keys = [str(k) for k in keys] if isinstance(keys, list) else None
            overrides = reset_texts(keys)
            return self.send_json({"ok": True, "texts": get_texts(),
                                   "textOverrides": overrides,
                                   "reset": len(keys) if keys else len(DEFAULT_TEXTS)})

        if action == "save":
            raw = body.get("texts")
            if not isinstance(raw, dict):
                return self.send_json({"error": "文案格式不正确（要一个 JSON 对象）。"}, 400)
            # ⚠️ save_texts 里面会走 set_store（它自己拿 _db_lock），所以这里不能待在任何 with 里
            overrides, saved = save_texts(raw)
            return self.send_json({"ok": True, "texts": get_texts(),
                                   "textOverrides": overrides, "saved": saved})

        return self.send_json({"error": "不支持的操作。"}, 400)

    def admin_terms(self, body):
        """
        POST /api/admin/terms —— ★ 服务条款（后台「文案」标签页里那一块）。

          save  保存 {action:"save", title, sections:[{heading, body}, …]}
                    节序就是数组顺序 —— 后台的 ↑↓ 就是在调这个数组。
                    「最后更新」由服务器写成今天（北京时间），不用前端传。
          reset 恢复默认 {action:"reset"}  ← 回到 app.py 里那份出厂条款

        校验：
          · 标题空 → 400（标题是页面的门面，不能空着）；
          · 标题+正文都空的小节 → 丢掉（= 你把这一节删了），不算错，
            但返回里带一个 dropped，前端可以提一句"空小节已忽略"。
        """
        action = str(body.get("action") or "save")

        if action == "reset":
            return self.send_json({"ok": True, "terms": reset_terms(), "reset": True})

        if action == "save":
            if not isinstance(body.get("sections"), list):
                return self.send_json({"error": "条款小节格式不正确（要一个数组）。"}, 400)
            if not str(body.get("title") or "").strip():
                return self.send_json({"error": "条款标题不能是空的。"}, 400)
            terms, dropped = save_terms(body)
            return self.send_json({"ok": True, "terms": terms, "dropped": dropped})

        return self.send_json({"error": "不支持的操作。"}, 400)

    def admin_import(self, body):
        """从备份 JSON 恢复：整体覆盖预约时段、休息规则、资料、展示墙、分区和收入流水。"""
        data = body.get("backup") if isinstance(body, dict) and isinstance(body.get("backup"), dict) else body
        summary, error = apply_backup(data)
        if error:
            return self.send_json({"error": error}, 400)
        zones = list_zones()
        auto_release_expired(force=True)     # 恢复完顺手清一次（设置是 0 时什么都不做）
        return self.send_json({
            "ok": True, "summary": summary,
            "profile": get_profile(), "profileRevision": summary["profileRevision"],
            "schedule": {"locks": list_locks(), "rules": list_rules()},
            "gallery": list_gallery(),
            "categories": list_categories(),
            "zones": [zone_public_view(z) for z in zones],
            "zoneBriefs": [zone_brief(z) for z in zones],
            "site": site_summary(),
            "customers": list_customers(),           # ★ 老备份没带客户 → 这里的还是原来那些
            "customerStats": customer_stats(),
            "today": today_cn(),
            "pendingLocks": pending_locks_view(),            # ★ 待确认（第二刀）
            "autoReleaseHours": get_auto_release_hours(),    # ★ 自动释放设置
            "autoReleased": get_auto_released_log(),
            "galleryCategories": list_gallery_categories(),  # ★ 展示墙分类
            "galleryCategoryCounts": gallery_category_counts(),
            # ★ 收入流水（第三刀）：老备份没带 payments → 这里的还是原来那些（绝不清空）
            "income": income_stats("month"),
            "payments": list_payments(limit=PAYMENT_LIST_MAX),
            "paymentSources": PAYMENT_SOURCE_NAMES,
            "incomeRanges": [{"id": r, "name": INCOME_RANGE_NAMES[r]} for r in INCOME_RANGES],
            # ★ 文案和条款：老备份没带这两项 → 这里的还是他现在的那份（绝不清空）
            "texts": get_texts(),
            "textGroups": text_groups_for_admin(),
            "textOverrides": get_text_overrides(),
            "terms": terms_view(),
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

    def admin_categories(self, body):
        """
        POST /api/admin/categories
          {"action":"save", "id":"play", "name":"陪玩", "kind":"booking"}   改名 / 改类型
          {"action":"sort", "order":["boost","play","service"]}              排序

        大类**不能删除**（删了分区就无家可归），要停用就把里面的分区隐藏起来。
        """
        action = str(body.get("action") or "save")
        cats = list_categories()
        if action == "save":
            cid = str(body.get("id") or "").strip()
            target = next((c for c in cats if c["id"] == cid), None)
            if not target:
                return self.send_json({"error": "找不到「%s」这个大类。" % cid[:20]}, 400)
            if "name" in body:
                name = str(body.get("name") or "").strip()[:20]
                if not name:
                    return self.send_json({"error": "大类名字不能是空的。"}, 400)
                target["name"] = name
            if "hint" in body:
                target["hint"] = str(body.get("hint") or "").strip()[:20]
            if "kind" in body:
                kind = str(body.get("kind") or "")
                if kind not in ("booking", "service"):
                    return self.send_json({"error": "大类类型只能是预约制或服务制。"}, 400)
                target["kind"] = kind
            set_categories(cats)
            return self.send_json({"ok": True, "categories": list_categories(),
                                   "zones": [zone_brief(z) for z in list_zones()]})
        if action == "sort":
            order = [str(x) for x in (body.get("order") or [])]
            known = [c["id"] for c in cats]
            seq = [c for c in order if c in known] + [c for c in known if c not in order]
            for i, cid in enumerate(seq, 1):
                next(c for c in cats if c["id"] == cid)["sort"] = i
            set_categories(cats)
            return self.send_json({"ok": True, "categories": list_categories()})
        if action == "delete":
            return self.send_json(
                {"error": "大类不能删除（分区会无家可归）。想停用就把里面的分区隐藏起来。"}, 400)
        return self.send_json({"error": "不支持的操作。"}, 400)

    def admin_zones(self, body):
        """
        POST /api/admin/zones —— 分区的所有管理动作，靠 action 区分：

          create  新建       {id, category, name, short}
          update  改基本信息 {id, name, short, icon, theme, category, visible, sort}
          profile 存资料     {id, profile:{…}}       ← 报价、文案、图片网址都在 profile 里
          sort    拖排序     {order:[id,…]}
          show    显示       {id}
          hide    隐藏       {id}
          delete  真删       {id, confirm:true}      ← 不传 confirm 只返回"会牵连什么"
        """
        action = str(body.get("action") or "")
        zone_id = str(body.get("id") or body.get("zone") or "").strip()

        if action == "create":
            zone, err = create_zone(body.get("id") or body.get("zone") or body.get("zoneId") or "",
                                    str(body.get("category") or ""),
                                    body.get("name") or "", body.get("short") or "",
                                    body.get("theme") or "", body.get("icon") or "")
            if err:
                return self.send_json({"error": err}, 400)
            return self.send_json({"ok": True, "zone": zone_public_view(zone),
                                   "zones": [zone_public_view(z) for z in list_zones()],
                                   "zoneBriefs": [zone_brief(z) for z in list_zones()]})

        if action == "sort":
            zones = sort_zones(body.get("order") or [])
            return self.send_json({"ok": True,
                                   "zones": [zone_public_view(z) for z in zones],
                                   "zoneBriefs": [zone_brief(z) for z in zones]})

        zone = get_zone(zone_id)
        if not zone:
            return self.send_json({"error": "找不到这个分区。"}, 404)

        if action == "update":
            fields = {k: body[k] for k in ("name", "short", "icon", "theme", "category",
                                          "visible", "sort") if k in body}
            zone, err = update_zone(zone_id, **fields)
            if err:
                return self.send_json({"error": err}, 400)
            return self.send_json({"ok": True, "zone": zone_public_view(zone),
                                   "zoneBriefs": [zone_brief(z) for z in list_zones()]})

        if action == "profile":
            zone, err = save_zone_profile(zone_id, body.get("profile"))
            if err:
                return self.send_json({"error": err}, 400)
            return self.send_json({"ok": True, "zone": zone_public_view(zone),
                                   "zoneBriefs": [zone_brief(z) for z in list_zones()]})

        if action in ("hide", "show"):
            zone, err = update_zone(zone_id, visible=(action == "show"))
            if err:
                return self.send_json({"error": err}, 400)
            return self.send_json({"ok": True, "zone": zone_public_view(zone),
                                   "zoneBriefs": [zone_brief(z) for z in list_zones()]})

        if action == "delete":
            summary = zone_delete_summary(zone_id)
            if not summary:
                return self.send_json({"error": "找不到这个分区。"}, 404)
            if not body.get("confirm"):
                # 第一次点"删除"：只把"会牵连什么"告诉后台，让主人自己确认
                return self.send_json({
                    "ok": False, "needConfirm": True, "summary": summary,
                    "detail": "删除「%s」会移除这个分区页和它的资料（价格、文案、背景图）。"
                              "它名下的 %d 张展示墙图片**不会删**，会变成「未分区」；"
                              "它名下的 %d 条预约记录也会保留（只是不再标注分区）。"
                              % (summary["name"], summary["gallery"], summary["locks"])})
            summary, err = delete_zone(zone_id)
            if err:
                return self.send_json({"error": err}, 400)
            zones = list_zones()
            return self.send_json({"ok": True, "deleted": summary,
                                   "zones": [zone_public_view(z) for z in zones],
                                   "zoneBriefs": [zone_brief(z) for z in zones],
                                   "gallery": list_gallery()})

        return self.send_json({"error": "不支持的操作。"}, 400)

    def admin_import_prices(self, body):
        """
        POST /api/admin/import-prices —— 批量粘贴导入价目表
        请求：{"zone":"arknights", "group":"托管", "text":"普通托管 | 20R/月 | 日双清+返图\\n…"}
        返回：{"ok":true, "imported":N, "errors":[{"line":3,"reason":"…"}], "profile":{…}}

        行格式：名称 | 价格 | 说明（说明可省略，竖线半角 | / 全角 ｜ 都认；空行和 # 注释行忽略）
        """
        result, err = import_prices(str(body.get("zone") or "").strip(),
                                    body.get("group") or "", body.get("text") or "")
        if err:
            return self.send_json({"error": err}, 400)
        return self.send_json({"ok": True, "imported": result["imported"],
                               "errors": result["errors"], "group": result["group"],
                               "profile": result["profile"]})

    def admin_extract_color(self, body):
        """
        POST /api/admin/extract-color —— 从一张图里算主色（后台「从首屏图自动提取主色」按钮）
        请求：{"image":"data:image/png;base64,…"} 或 {"url":"/api/media/xxx.png"}
        返回：{"ok":true, "color":"#8fa832"}
        """
        data_url = body.get("image") or ""
        if not data_url and body.get("url"):
            key = os.path.basename(str(body["url"]))
            full = os.path.join(UPLOAD_DIR, key)
            if not os.path.isfile(full):
                return self.send_json({"error": "找不到这张图片。"}, 404)
            with open(full, "rb") as f:
                blob = base64.b64encode(f.read()).decode()
            ext = key.rsplit(".", 1)[-1].lower()
            mime = "jpeg" if ext in ("jpg", "jpeg") else ext
            data_url = "data:image/%s;base64,%s" % (mime, blob)
        color, err = extract_dominant_color(data_url)
        if err:
            return self.send_json({"error": err}, 400)
        return self.send_json({"ok": True, "color": color})

    def admin_customers(self, body):
        """
        POST /api/admin/customers —— 客户档案的全部动作，靠 action 区分：

          create / update  新建或改档案 {action, id?, name, contact, contact_type, zone,
                                        item, price, start_date, end_date, status, note}
          delete           删除       {action:"delete", id}
          renew            续费       {action:"renew", id, months/week/days, mode, amount?}

        ★ 第三刀：renew 多了一个**可选**参数 `amount`（这次实际收了多少钱，数字）。
          填了就顺手记一笔收入（source='renew'，ref = 客户id + 新到期日，幂等）；
          不填就什么都不做 —— **绝不能让他觉得"必须填"**（很多人是先续后收钱，
          甚至忘了收，逼着他填只会让他干脆不用这个功能）。

        三个动作都回同一份"最新全量"（customers + customerStats + today + 收入统计），
        这样前端不用自己猜状态，直接整块重画就行（和 /api/admin/zones 一个套路）。
        """
        action = str(body.get("action") or "create")

        if action in ("create", "update"):
            customer, err = save_customer(body)
            if err:
                return self.send_json({"error": err}, 400)
            return self.send_json({"ok": True, "customer": customer,
                                   "customers": list_customers(),
                                   "customerStats": customer_stats(), "today": today_cn()})

        if action == "delete":
            if not delete_customer(body.get("id")):
                return self.send_json({"error": "找不到这个客户（可能已经删过了）。"}, 404)
            return self.send_json({"ok": True, "customers": list_customers(),
                                   "customerStats": customer_stats(), "today": today_cn()})

        if action == "renew":
            customer = get_customer(body.get("id"))
            if not customer:
                return self.send_json({"error": "找不到这个客户。"}, 404)
            ok, result = renew_customer(body.get("id"), body.get("months") or 0,
                                        body.get("weeks") or 0, body.get("days") or 0,
                                        body.get("mode") or "extend")
            if not ok:
                return self.send_json({"error": result}, 400)
            # ★ 记账入口②：填了金额就记一笔（在 _db_lock 外面，见 record_payment 的说明）
            paid = None
            if clean_amount(body.get("amount")) is not None:
                paid, pay_note = record_payment({
                    "date": today_cn(),               # 续费的钱按"今天"算（不是到期日那天）
                    "amount": body.get("amount"),
                    "zone": customer.get("zone") or "",
                    "item": customer.get("item") or "",
                    "customer": customer.get("name") or "",
                    "customer_id": customer.get("id") or "",
                    "note": "续费自动记账（%s → %s）" % (customer.get("end_date") or "？", result),
                }, "renew", renew_payment_ref(customer.get("id"), result))
            # 顺手给一句人话提示：超期太久还从原到期日推，算出来的日期可能还是过去的
            tip = ""
            if str(body.get("mode") or "extend") != "from_today":
                left = days_left(result)
                if left is not None and left < 0:
                    tip = ("注意：这次是从**原到期日**往后推的，算出来还是过去的日期。"
                           "如果客户是停了一阵子现在重新开始，请改用「从今天算」。")
            payload = {"ok": True, "newEndDate": result, "tip": tip,
                       "customer": get_customer(body.get("id")),
                       "customers": list_customers(),
                       "customerStats": customer_stats(), "today": today_cn()}
            if paid:
                payload["message"] = "已记一笔收入：%s %s" % (paid["amountText"], paid["label"])
                payload.update(income_payload("month", with_list=False))
            return self.send_json(payload)

        return self.send_json({"error": "不支持的操作。"}, 400)

    def admin_payments(self, body):
        """
        POST /api/admin/payments —— ★ 记账入口③：手动记一笔（剪辑 / PS / 建站这类一次性单子）。

        为什么要留这个手动入口：前两个入口都挂在"他本来就要做的动作"上
        （确认预约、续费），但剪辑、PS、建站这些**根本不在时间表里**，
        没有"确认"也没有"到期"这一步 —— 不给他一个手记的地方，这部分收入就是盲区，
        而"哪个服务最来钱"恰恰要靠它们才看得出来。

          create  记一笔 {action, date?, amount, zone?, item?, customer?, customer_id?, note?}
          update  改一笔 {action, id, …同上…}
          delete  删一笔 {action, id}
          range   换时间范围 {action:"range", range:"month|last-month|3m|year|all"}
                  ← 只回统计，不改任何数据。为什么让它走 POST 而不是 GET：
                    这个接口整条路都是"要登录 + 一发一收 JSON"，多开一条 GET 只是多一处要维护的路由。

        三个写动作都回**同一份最新全量**（payments + income + today）——
        和 /api/admin/customers 一个套路，前端拿到直接整块重画，不用自己猜状态。

        校验（中文错误）：
          · 金额必须有、且 > 0   → 400「金额要填一个大于 0 的数字（例如 30）。」
          · 日期格式不合法 / 日历上没有这一天 → **不报错，按今天（北京时间）记**，
            并在返回里带一句 dateTip 说清楚（取舍理由写在 clean_payment 的注释里）
        """
        action = str(body.get("action") or "create")

        if action == "range":
            # 只换范围、只回统计（前端切「本月 / 上月 / 近 3 个月 / 今年 / 全部」时用它）
            return self.send_json(income_payload(str(body.get("range") or "month")))

        if action in ("create", "update"):
            fields, err = clean_payment(body)
            if err:
                return self.send_json({"error": err}, 400)
            pid = str(body.get("id") or "").strip()
            if action == "update":
                if not pid or not get_payment(pid):
                    return self.send_json({"error": "找不到这条流水（可能已经删掉了）。"}, 404)
                with _db_lock, connect() as conn:
                    conn.execute("""UPDATE payments SET date=?, amount=?, zone=?, item=?,
                                    customer=?, customer_id=?, source=?, ref=?, note=?
                                    WHERE id=?""",
                                 (fields["date"], fields["amount"], fields["zone"], fields["item"],
                                  fields["customer"], fields["customer_id"], fields["source"],
                                  fields["ref"], fields["note"], pid))
                saved = get_payment(pid)
            else:
                with _db_lock, connect() as conn:
                    pid = insert_payment(conn, fields)
                saved = get_payment(pid)
            payload = {"ok": True, "payment": income_view(saved) if saved else None}
            if fields["dateFallback"]:
                # 说清我的选择：日期不合法的**不报错**，按今天记（宁可记成今天，也别打回整张表单）
                payload["dateTip"] = "日期没填或格式不对，我按今天（%s）记上了 —— 可以点「编辑」改。" % fields["date"]
                payload["dateFallback"] = True
            payload.update(income_payload("month"))
            return self.send_json(payload)

        if action == "delete":
            pid = str(body.get("id") or "").strip()
            with _db_lock, connect() as conn:
                cur = conn.execute("DELETE FROM payments WHERE id=?", (pid,))
            if not cur.rowcount:
                return self.send_json({"error": "找不到这条流水（可能已经删掉了）。"}, 404)
            out = {"ok": True}
            out.update(income_payload("month"))
            return self.send_json(out)

        return self.send_json({"error": "不支持的操作。"}, 400)

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
    zones = list_zones(only_visible=True)
    # flush=True：日志立刻输出。Render 的日志窗口是按行读的，不加这句会"卡住不显示"
    print("=" * 60, flush=True)
    print(" 个人服务招牌站已启动", flush=True)
    print(" 本地访问：http://127.0.0.1:%d" % PORT, flush=True)
    # ⚠️ 这里必须用 ADMIN_PATH，不能写死 "/admin" ——
    #    后台网址是个"暗号"，写死的话启动日志会把暗号说错，主人照着敲就是 404。
    print(" 管理后台：http://127.0.0.1:%d%s" % (PORT, ADMIN_PATH), flush=True)
    print(" 分区（%d 个）：%s" % (len(zones),
                                "、".join("/z/" + z["id"] for z in zones) or "（还没有分区）"), flush=True)
    print(" 数据文件：%s" % DB_PATH, flush=True)
    print(" 预约通知：%s" % (NOTIFY_NAMES.get(notify["channel"], notify["channel"])
                          + ("（已配置，客户预约会推送）" if notify["target"] else "（未配置）")), flush=True)
    print("=" * 60, flush=True)
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
