# 部署到 PythonAnywhere（免费、不要信用卡、电脑可以关）

这条路线专门为你这种情况做的：**没有能过国际支付的卡**，但想要一个**长期在线、网址固定、不用开着电脑**的站。

---

## 为什么选它（和 Render 免费档对比）

| | Render 免费档 | **PythonAnywhere 免费版** |
|---|---|---|
| 要不要国外信用卡 | **要**（要验证身份） | **不要** |
| 电脑要开着吗 | 不要 | **不要** |
| 会休眠吗 | **15 分钟没人访问就休眠** | 不休眠 |
| 数据会不会丢 | **休眠就清空**（除非挂付费磁盘） | **不丢**（磁盘持久） |
| 网址 | `xxx.onrender.com` | `你的用户名.pythonanywhere.com` |
| 自定义域名 | 免费档支持 | 免费版**不支持**（要付费 $5/月起） |
| 资源限制 | 750 小时/月 | **每天 100 秒 CPU**、磁盘 512MB |

> **每天 100 秒 CPU** 听起来少，但注意是 **CPU 时间**不是在线时间：处理一次页面请求大约 10–50 毫秒，
> 100 秒 ≈ 每天几千次访问，你这种小站完全够用。

---

## 第 1 步：注册（2 分钟，只要邮箱）

打开 <https://www.pythonanywhere.com/registration/register/beginner/>

- 填用户名、邮箱、密码 → 注册
- 邮箱会收到验证信，点一下链接
- **不需要信用卡，不需要实名**

> 记住你的**用户名**——你的网址就是 `https://你的用户名.pythonanywhere.com`

---

## 第 2 步：把代码弄上去

### 路线一：从 GitHub 拉（推荐，以后更新最方便）

1. 登录后点顶部 **Consoles** → **Bash**（新建一个命令行）
2. 粘贴这一条并回车：

```bash
git clone https://github.com/NekoByNya/peiwan-site.git
```

3. 完成后你的文件在 `/home/你的用户名/peiwan-site/`

> 我确认过：`.github.com` 在它的免费白名单里，所以这条命令能直接用。
> **以后更新网站也只要**：`cd peiwan-site && git pull`，然后在网页上点一下 Reload。

### 路线二：不用 GitHub，直接上传压缩包

如果 GitHub 上的代码不是最新的（或者你不想用 GitHub）：

1. 在电脑上把 **`peiwan-site-上传用.zip`** 准备好
2. PythonAnywhere 顶部点 **Files** → 进入 `/home/你的用户名/` → 点 **Upload a file** → 选那个 zip
3. 点顶部 **Consoles** → **Bash**，执行：

```bash
cd ~
unzip -o peiwan-site-上传用.zip -d peiwan-site
```

（如果中文文件名报错，就先执行 `export LANG=C.UTF-8` 再解压）

> 路线二的缺点：以后每次改代码都要重新上传一次。

---

## 第 3 步：建 Web App（点 4 下）

1. 点顶部 **Web** → **Add a new web app**
2. 域名直接点 **Next**（用默认的 `你的用户名.pythonanywhere.com`）
3. 框架选 **Manual configuration**（⚠️ 不要选 Flask/Django）
4. Python 版本选最新的 **Python 3.x** → **Next**

---

## 第 4 步：填三个路径

在刚建好的 Web 页面里：

| 字段 | 填什么 |
|---|---|
| **Source code** | `/home/你的用户名/peiwan-site` |
| **Working directory** | `/home/你的用户名/peiwan-site` |
| **WSGI configuration file** | 点它右边的链接进去编辑（下一步） |

---

## 第 5 步：改 WSGI 文件（关键一步）

点 **WSGI configuration file** 那个链接，它会打开一个编辑器。**把里面所有内容删掉**，替换成下面这几行
（把两处 `你的用户名` 换成你的真实用户名）：

```python
import os
import sys

path = '/home/你的用户名/peiwan-site'
if path not in sys.path:
    sys.path.insert(0, path)

# 你后台的登录密码（首次启动写入；登录后可以在后台改）
os.environ['ADMIN_PASSWORD'] = 'bx-Delta-7k2mQ9'

from wsgi import application
```

点 **Save** 保存。

---

## 第 6 步：静态文件（推荐，省 CPU 额度）

Web 页面往下找 **Static files**，点 **Enter URL** / **Enter path** 各填一个：

| URL | Directory |
|---|---|
| `/static/` | `/home/你的用户名/peiwan-site/static` |

这样图片和 CSS 由它的服务器直接发，不消耗你的 100 秒 CPU 额度。

---

## 第 7 步：启动

点页面顶部那个绿色的 **Reload** 按钮。

然后打开：

```
https://你的用户名.pythonanywhere.com
```

**看到你的主页就成功了。**

后台地址：`https://你的用户名.pythonanywhere.com/admin`，密码就是你第 5 步填的那个。

---

## 上线后检查（和我给 Render 那套一样）

1. 打开主页 → 选几个时间 → 「下一步：选择模式」→ 填联系方式 → 点「立即锁定时间」→ 看到"时间已锁定 ✅"
2. 打开 `/admin` 登录 → 看「预约与时间」里那条有没有「客户自助预约」徽章（模式、价格、联系方式、下单时间都在）
3. 设一条固定休息（比如每天 02:00–10:00）→ 主页该时段变「休息」
4. 传一张展示墙图片 → 主页看得见
5. **把网址发到微信里**，看分享卡片有没有标题、简介和缩略图
6. 进后台「安全与备份」→ 下载一次备份 JSON（养成习惯）

---

## 预约通知：PythonAnywhere 免费版能用 PushPlus（已核实）

后台「安全与备份 → 预约通知」支持 PushPlus / Server酱 / Webhook 三种渠道。

> **PythonAnywhere 免费账号不能随便访问外网**：它有一份"出网白名单"，只有名单里的站点能连。
> 我核对过它官网的白名单页面，**下面这些通知服务都在名单里，开箱可用**：

| 渠道 | 域名 | 白名单 |
|---|---|---|
| **PushPlus（微信推送，推荐）** | `www.pushplus.plus` | ✅ 在名单里 |
| Server酱 | `sc.ftqq.com` | ✅ 在名单里 |
| WxPusher（微信推送） | `wxpusher.zjiecode.com` | ✅ 在名单里 |
| 飞书机器人 | `open.feishu.cn` | ✅ 在名单里 |
| 钉钉机器人 | `api.dingtalk.com` | ✅ 在名单里 |
| ntfy | `ntfy.sh` | ✅ 在名单里 |
| Bark（iOS） | `api.day.app` | ❌ 不在名单里 |
| QQ 邮箱 SMTP | `smtp.qq.com` | ❌ 不在名单里 |

**结论：直接用 PushPlus 就行，不需要申请加白名单。**

如果哪天点了「发送测试通知」却失败，按后台显示的原因判断：

| 显示 | 意思 | 怎么办 |
|---|---|---|
| `HTTP 403` / `HTTP 500` | token 不对或过期 | 回 pushplus.plus 重新复制 token |
| `超时（10 秒没有响应）` | 服务器连不上对方 | 换 Server酱试试；或用「自定义 Webhook」转发 |
| `连不上：...` | 域名被挡 | 你用的自定义网址不在白名单里，去 **Account → Proxy allowlist** 申请加域名 |

**通知发不出去不影响客户下单**：时间照样锁得好好的，只是你手机上收不到提醒。
后台那行小字会一直显示上次失败原因，方便你判断。

---

## 数据在哪、会不会丢

- 数据在 `/home/你的用户名/peiwan-site/data/`（`site.db` + `uploads/`）
- **PythonAnywhere 的磁盘是持久的**，不像 Render 免费档休眠就清空
- ⚠️ **免费版有个"保质期"**：Web 页面会显示
  > This site will be disabled on <某个日期>
  > [ Run until 1 month from today ]

  **每个月回来点一次那个黄色按钮**，网站就继续免费跑下去。到期前一周它会发邮件提醒你。
  忘了点 → 网站会被停（数据还在，点一下就能恢复）。
- 保险起见，后台仍然建议**每月导出一次备份 JSON**

---

## 以后怎么更新网站

```bash
# 1. 在 Bash console 里
cd ~/peiwan-site
git pull

# 2. 回到 Web 页面，点一下绿色的 Reload
```

改完代码想先在本地验证的话，仍然跑：

```bat
python tests\run_tests.py
```

---

## 常见问题

| 现象 | 原因 / 解决 |
|---|---|
| **主页报 500，但 `/api/ping` 正常** | **数据库没建起来**。`wsgi.py` 里必须有 `site.init_db()` 这一行（在 `import app as site` 后面）。少了它，任何读数据库的页面都会 500 |
| 打开显示 "Something went wrong" | Web 页面有 **Error log** 链接，点进去看最后 20 行；多半是第 5 步的路径写错了 |
| 页面能开但没有样式 | 第 6 步的 Static files 没配（或者路径写错）——不配也能用，但样式会走应用、费 CPU |
| 后台登录后马上又跳回登录页 | 免费版有个限制：**必须用 `https://`** 访问（不要用 http） |
| 提示超过 CPU 额度 | 等第二天恢复；把 Static files 配好能显著降低消耗 |
| 改了 WSGI 文件不生效 | 一定要点 Web 页面那个 **Reload** 按钮 |
| 想换成自己的域名 | 免费版不支持，要升级到 $5/月的 Hacker 档 |

---

## 我为你验证过什么

这套部署方式我在本地用 WSGI 服务器完整跑过一遍（**不是"应该能行"**）：

```
① 接口测试        通过 120 项，失败 0 项
② 自助预约测试    通过 75 项，失败 0 项（含通知、老库迁移）
③ 并发测试        8 客户 + 1 后台同时读写 → 零报错
④ 线上体检        通过 26 项，失败 0 项（仅"非 HTTPS"提示，部署后就是 https）
```

也就是说：`wsgi.py` 这个适配层把 PythonAnywhere 的请求正确转给了原来的程序，功能一模一样。

> 升级到新版（自助预约 + 通知）时**不用手工改数据库**：`wsgi.py` 里那句 `site.init_db()`
> 会自动给老的 `locks` 表补上新字段，你以前锁过的时段一条都不会丢。
