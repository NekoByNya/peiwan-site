# 部署到 Render · 一步步照做

目标：让 `http://127.0.0.1:8123` 变成给别人用的公网网址，比如 `https://peiwan-site.onrender.com`。

**整个过程大约 20 分钟，唯一的前提是有一个 GitHub 账号（免费）。**
Render 不支持从本机直接上传文件，它只能从 GitHub / GitLab / Bitbucket 拉代码，所以这一步绕不开。

---

## 第 0 步：先本地确认一切正常（2 分钟）

```bat
cd peiwan-site
python app.py
```

打开 <http://127.0.0.1:8123> 能出页面 → 打开 `/admin`，用密码 `baixu123` 能进 → 说明没问题。

---

## 第 1 步：注册 / 登录 GitHub

1. 打开 <https://github.com/signup>，用邮箱注册（免费）。
2. 注册完记住你的**用户名**，等下要用。

---

## 第 2 步：把代码传上去

> 我检查过你的电脑：**`python` 已经有了（`start.bat` 能直接用），但没有装 `git`**。
> 所以直接走**路线 A（纯网页上传）**，不用装任何软件。

### 路线 C（可选）：用脚本自动传（也不需要 git）

不想手动拖 27 个文件的话，用项目自带的脚本，一条命令推上去：

```bat
cd peiwan-site
python tools\push_to_github.py --repo 你的用户名/peiwan-site --dry-run    :: 先空跑，看清单
set GITHUB_TOKEN=你的令牌
python tools\push_to_github.py --repo 你的用户名/peiwan-site              :: 真推
```

令牌用 **Fine-grained token**：`Repository access` 只勾这一个仓库，
权限只给 **Contents: Read and write**。传完立刻去令牌页面删掉它。

脚本自己会校验：本地文件数 vs 仓库文件数不一致会报出来，也绝不会把 `data/` 传上去。

### 路线 A：不装任何软件，纯网页上传（推荐你用这条）

1. 打开 <https://github.com/new>
2. **Repository name** 填 `peiwan-site`，选 **Public**（公开），**不要**勾选 "Add a README file"
3. 点 **Create repository**
4. 在新页面点 **uploading an existing file**（或 `Add file → Upload files`）
5. 打开你电脑上的 `peiwan-site` 文件夹，**把这 6 样拖进去**：
   `app.py`、`requirements.txt`、`render.yaml`、`start.bat`、`README.md`、`.gitignore`，
   以及 **`static`、`templates`、`tests` 三个文件夹**（整个文件夹拖，会自动带上里面所有文件）
6. ⚠️ **千万不要拖 `data` 文件夹**（里面是数据库和你上传的图片，还包含密码哈希）
7. 下面的 Commit message 随便写，例如"第一版"，点 **Commit changes**

### 路线 B：装了 Git 的话（命令行）

> 你的电脑现在**没有 git**。想走这条路要先装：<https://git-scm.com/download/win>（一路下一步即可）。
> 装完重开一个命令行窗口再执行下面的命令。以后网站要更新，也是用这几条命令最方便。

```bat
cd peiwan-site
git init
git add .
git commit -m "第一版"
git branch -M main
git remote add origin https://github.com/你的用户名/peiwan-site.git
git push -u origin main
```

（第一次推送会弹出登录窗口，用浏览器授权即可。）

---

## 第 3 步：在 Render 建服务

两条路选一条。**路线甲点得最少，建议先用它**；不顺手再走路线乙。

### 路线甲：Blueprint 自动建（推荐，约 1 分钟）

你的仓库里已经有 `render.yaml`，Render 能直接读它把服务建好：

1. 打开 <https://dashboard.render.com/blueprints>
2. 第一次会让你 **Sign in with GitHub** → 授权（这一步就是登录，不用另外注册）
3. 点 **New Blueprint Instance** → 选中 `peiwan-site` 仓库 → **Connect**
4. Render 会读到 `render.yaml`，列出要创建的服务：

   | 它会自动配好 | 值 |
   |---|---|
   | 类型 | Web Service |
   | 运行环境 | Python |
   | 实例档位 | Free |
   | 机房 | Singapore |
   | 启动命令 | `python app.py` |
   | 健康检查 | `/api/ping` |
   | 自动部署 | 每次推送到 main 就重新部署 |

5. **它会弹窗让你输入 `ADMIN_PASSWORD`**——这是 `render.yaml` 里写了 `sync: false` 的效果，
   密码不会存在文件里，只有你自己知道。输一个你自己的密码（别用 `baixu123`）。
6. 点 **Apply**，等 1–2 分钟。

### 路线乙：手动填表（Blueprint 不顺手时用）

1. 打开 <https://dashboard.render.com/web/new>
2. 选 **Git Provider** → 找到 `peiwan-site` → 点 **Connect**
3. 表单这么填：

| 字段 | 填什么 |
|---|---|
| **Name** | `peiwan-site`（决定网址，比如 `peiwan-site.onrender.com`；重名就换个） |
| **Region** | `Singapore`（离国内最近） |
| **Branch** | `main` |
| **Language** | `Python 3` |
| **Build Command** | `echo 零依赖，无需构建` |
| **Start Command** | `python app.py` |
| **Instance Type** | 先选 **Free** |

4. 展开 **Advanced**，点 **Add Environment Variable**，加两条：

| Key | Value |
|---|---|
| `ADMIN_PASSWORD` | 你自己想设的管理密码（**别再用 baixu123**） |
| `DATA_DIR` | `./data` |

5. 点 **Create Web Service**，等 1–2 分钟。

> 改过 `render.yaml` 想确认没写错？跑一下 `python tools\check_render_yaml.py`——
> 它会把每个字段名拿去 Render 官方规范里核对一遍。

---

## 第 4 步：验收清单

构建完成后，页面顶部会出现你的网址 `https://xxx.onrender.com`。

### 先跑一次"线上体检"（30 秒，强烈建议）

在你电脑上打开命令行，进到项目目录，执行：

```bat
python tests\check_deployed.py https://xxx.onrender.com
```

它会把 26 项检查一次跑完，**全程只读、不改你的数据**：

```
【1】页面能不能打开        【5】静态资源
【2】微信分享卡片          【6】保活接口
【3】公开接口（时间表）    【7】响应速度
【4】后台有没有被保护      【8】安全与协议
```

看到 `通过 26 项 | 注意 1 项 | 失败 0 项` 就说明部署成功了。
（本地跑会有 1 条「注意：当前不是 HTTPS」，线上不应该出现。）

> ⚠️ **千万别用 `tests\test_api.py` 去测线上站点**——那个脚本会改管理密码、覆盖展示墙和资料，
> 是给测试环境用的。它现在有保护：地址不是本机就会直接拒绝运行。

### 再自己动手点一遍

- [ ] 主页能打开，名字/报价/微信号都是你的
- [ ] 时间表显示 7 天，格子能点选，最多 72 小时
- [ ] 点「下一步：选择模式」→ 生成一段微信文案，能复制
- [ ] `https://xxx.onrender.com/admin` 能登录（用你设的 `ADMIN_PASSWORD`）
- [ ] 后台锁一格 → 刷新主页 → 那格变成"已预约"
- [ ] 后台加一条固定休息（如每天 02:00–10:00）→ 主页凌晨格子变成"休息"
- [ ] 传一张展示墙图片 → 主页看得见
- [ ] 点「下载备份 JSON」能下载
- [ ] **把网址发到自己微信里**：能看到标题、简介**和缩略图**（三项都有才算对）
- [ ] 在手机上打开：往后滑到「预约时间」→ 点几个格子 → 点「下一步」→ 生成文案那步能复制

**第一件事：进后台「安全与备份」把密码改掉**（环境变量里的密码只用于首次启动）。
**第二件事：设置保活**（见下面「最重要的一节」），否则 15 分钟后数据就没了。

---

## 第 5 步：以后怎么更新网站

改完本地代码 → 推到 GitHub → Render **自动重新部署**（约 1 分钟）：

```bat
git add .
git commit -m "改了报价"
git push
```

在 Render 的 **Events / Logs** 页面能看到部署进度和报错。

---

## ⚠️ 最重要的一节：免费档会清空你的数据

**Render 官方文档的原话**（我核对过）：

> any changes to your web service's filesystem (**uploaded images, local SQLite databases**, etc.)
> are lost every time the service redeploys, restarts, or **spins down**
>
> Paid services can preserve local filesystem changes by attaching a persistent disk,
> but **Free web services cannot**.

翻译成人话：**免费实例只要 15 分钟没人访问就会休眠，一休眠，你的预约记录和图片就全没了。**
具体场景：你给客户锁好时段 → 出门吃个饭 → 回来打开后台，记录是空的。

### 三个方案，按推荐顺序

| 方案 | 花钱 | 数据安全 | 客户体验 |
|---|---|---|---|
| **A. 免费档 + 定时保活** | 0 | 基本安全（Render 维护重启仍可能丢） | 秒开，不用等冷启动 |
| **B. 付费实例 + 持久磁盘** | ≈ $7.25/月 | 完全安全 | 秒开 |
| **C. 什么都不要，勤导出备份** | 0 | 每次休眠都丢，靠备份恢复 | 首次打开要等约 1 分钟 |

#### 方案 A：定时保活（免费，建议先这样）

原理：让一个定时任务每 5–10 分钟碰一下你的网站，实例就永远不会休眠，数据也就不会被清空。

**两种做法，任选一种（也可以两个都做）：**

**A-1：UptimeRobot（更可靠，推荐）**

1. 注册 <https://uptimerobot.com>（免费版给 50 个监控）
2. **Add New Monitor**
   - Monitor Type：`HTTP(s)`
   - Friendly Name：`陪玩站保活`
   - URL：`https://你的服务名.onrender.com/api/ping`
   - Monitoring Interval：`5 minutes`
3. 保存。之后每 5 分钟它会访问一次，你的站就一直是醒着的。

**A-2：GitHub Actions（不用注册新账号）**

你的仓库里已经有一个现成的保活工作流：`.github/workflows/keepwarm.yml`。

1. 打开仓库 → **Actions** 标签页 → 如果看到一个叫「保活」的工作流，说明已经生效
2. 它每 **5 分钟**访问一次 `/api/ping`，用的是 GitHub 的免费额度（公开仓库不消耗额度）
3. 想立刻测一次：Actions → 保活 → **Run workflow**

> 用这个方案的话，先确认里面的网址和你的服务名一致
> （文件里那行 `PING_URL: https://peiwan-site.onrender.com/api/ping`）。
>
> 两个方案的差别：
> - UptimeRobot 更准时，还能给你发"网站挂了"的邮件告警
> - GitHub Actions 不用注册新账号，但 GitHub 的定时任务在高峰期会延迟几分钟，
>   而且**仓库连续 60 天没有新提交会被自动停掉**

> 这个站**专门为此做了 `/api/ping` 接口**：不查数据库、不算时间表，返回几十字节，
> 几乎不耗流量和 CPU，就是用来"叫醒"实例的。

免费额度够用吗？Render 每月给 750 实例小时，一直醒着约 720 小时，**刚好放得下**。

> **诚实提醒**：Render 仍可能因为平台维护随时重启你的实例，重启同样会清空文件系统。
> 所以**方案 A 也必须配合定期导出备份**——后台会在你超过 7 天没导出时弹提醒。

#### 方案 B：付费实例 + 持久磁盘（最稳）

1. Render 服务页面 → **Settings** → Instance Type 改成 `0.5c-512mb`（$7/月）
2. **Disks** → **Add Disk**：Name `peiwan-data`，Mount Path `/var/data`，Size `1 GB`（$0.25/月）
3. **Environment** → 把 `DATA_DIR` 改成 `/var/data`
4. 保存后会自动重新部署，之后数据再也不会丢

（`render.yaml` 里这段配置已经写好，只是被注释掉了；取消注释并重新 Blueprint 同步也可以。）

#### 方案 C：什么都不做

能用，但你要接受"每次休眠清空数据"。那就养成习惯：**每次改完东西，顺手点一次「下载备份 JSON」**。
真丢了也别慌——用「从备份恢复」把 JSON 导回去就行（图片也会一起回来）。

### 我已经替你做的两件事

1. **登录状态不再丢**：以前登录信息存在服务器内存里，一休眠你就要重新输密码；
   现在改成**自带签名的 Cookie**，网站重启后你依然是登录状态。
   （我实测过：关掉网站再用同一份数据启动，Cookie 照样有效、预约记录原封不动。）
2. **备份提醒**：后台会记录你上次导出的时间，超过 7 天没导出就在页面顶部弹一条醒目提醒。

---

## 另外两个坑

| 坑 | 具体表现 | 对策 |
|---|---|---|
| **每月 750 免费小时** | 用完了当月所有免费服务被暂停 | 别建多个免费服务，月底去 Billing 页面看用量 |
| **每月 5 GB 流量** | 超出了会按 $0.15/GB 计费（没绑卡就暂停服务） | 这个站首屏约 161 KB，够约 3 万次访问，个人站用不完 |

---

## 部署失败怎么办

去 Render 服务的 **Logs** 页面看最后 20 行，对号入座：

| 日志里的关键词 | 原因 | 解决 |
|---|---|---|
| `Address already in use` | 你写死了端口 | 确认代码里是 `int(os.environ.get("PORT") or 8123)`，没有写死 |
| `ModuleNotFoundError` | 传漏了文件 | 检查 `app.py` 和 `static/`、`templates/` 是否都上传了 |
| `No such file or directory: templates/index.html` | 上传时漏了 `templates` 文件夹 | 网页上传要**拖整个文件夹**，不是只拖里面的文件 |
| 部署成功但打开报 502 | 服务没监听 `$PORT` 或崩溃了 | 看 Logs 里有没有 Python 报错堆栈 |
| 打开要等一分钟 | 正常，免费档休眠了 | 升级付费实例 |

---

## 小抄：Render 表单里的字段对应到我们代码里的什么

| Render 字段 | 对应 |
|---|---|
| Start Command `python app.py` | 运行 `app.py`，它自己读 `$PORT` 和 `$HOST=0.0.0.0` |
| `DATA_DIR` 环境变量 | `app.py` 第 1 节的 `DATA_DIR`，决定数据库和图片存哪 |
| `ADMIN_PASSWORD` 环境变量 | 首次启动时写入数据库的初始密码，之后在后台改 |
| Persistent Disk `/var/data` | 把 `DATA_DIR` 指过去，数据就不会随部署消失 |
