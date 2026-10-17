/* ==========================================================================
   管理后台逻辑（两级分区版）
   八个标签页：预约与时间 / ★客户 / ★收入 / 分区管理 / 资料 / 报价 / 展示墙 / 安全与备份

   三条主线：
     · state.profile —— 全局资料（品牌、昵称、微信、头像、信任信号…）
     · state.zones   —— 分区列表，每一项都带自己的 profile（价格、文案、图片）
     · state.payments / state.income —— ★ 收入流水和统计（第三刀）

   客户那条线（★ 每天都会用的）：
     · state.customers —— 全部客户档案，后端已按"紧急度"排好序
     · 剩余天数和红/橙/黄分档都由后端算好（daysLeft / level），前端只负责显示

   收入那条线（★ 第三刀）：
     · 所有数字（总额、环比、小卡、排行）都是后端 income_stats() 算好的，
       前端**一个数都不自己算**（避免"大字说 ¥1240、明细加起来 ¥1230"这种自相矛盾）
     · "本月"的口径 = 北京时间，也在后端（income_range_bounds）
   ========================================================================== */

const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));

const CN_OFFSET = 8 * 3600 * 1000;
const HOUR = 3600 * 1000;
const DAY = 24 * HOUR;
const WEEK = ['周日', '周一', '周二', '周三', '周四', '周五', '周六'];
const pad = (n) => String(n).padStart(2, '0');

const CONTACT_TEXT = { wechat: '微信', xianyu: '闲鱼', qq: 'QQ' };   // 联系方式渠道的中文名
// 客户档案的渠道多一个"其他"（预约只有三种）
const CUSTOMER_CONTACT_TEXT = { wechat: '微信', qq: 'QQ', xianyu: '闲鱼', other: '其他' };
const CUSTOMER_STATUS_TEXT = { active: '进行中', paused: '已暂停', ended: '已结束' };
// 大类名 → 图标（首页和后台都用同一套；认不出来就用默认的 🎯）
const GROUP_ICON = { '陪玩': '🎮', '代肝': '⚡', '其他服务': '🎨' };
const KIND_TEXT = { booking: '预约制', service: '服务制' };

const state = {
  profile: null,          // 全局资料
  revision: 1,
  categories: [],
  zones: [],              // 每个分区都是一份完整的 zone_public_view
  currentZoneId: '',      // 「资料与报价」正在编辑哪个分区
  editHome: false,        // true = 正在编辑「个人主页」（而不是某个分区）
  galleryZoneId: '',      // 「展示墙」正在看哪个分区
  locks: [],
  rules: [],
  gallery: [],
  publicSlots: [],
  lastExportAt: 0,
  notifyChannel: 'off',
  notifyTarget: '',
  notifyLast: {},
  date: null,             // 当前查看的日期（毫秒，北京时间 00:00）
  batch: new Set(),       // 多选批量锁定的整点
  editingLockId: null,
  editingRuleId: null,
  showArchived: false,
  // ---- ★ 客户档案 + 到期提醒 ----
  customers: [],          // 全部客户（后端已经按紧急度排好序，前端不再自己排）
  customerStats: {},      // 顶部 4 张统计卡的数字
  customerToday: '',      // 北京时间的今天（后端给的，前端不自己算，免得时区出岔子）
  customerFilter: 'all',  // all 全部 / remind 待催 / expired 已超期 / active / paused / ended
  customerSearch: '',     // 搜索框里的字（按称呼 / 联系方式 / 档位 / 分区匹配）
  editingCustomerId: null,// 弹窗里正在编辑哪个客户（null = 新增）
  renewingCustomerId: null,// 续费弹窗里正在给谁续
  // ---- ★ 第二刀：待确认 + 自动释放 ----
  pendingLocks: [],       // 待确认的客户自助预约（后端已按"等得最久的排最前"）
  autoReleaseHours: 0,    // "待确认超过几小时自动释放"，0 = 关闭（默认就是关的）
  autoReleased: [],       // 最近自动释放了哪几单（后端只留最近 10 条）
  serverTime: 0,          // 服务器时间（算"已经等了 N 小时"要用它，别用本机时间）
  // ---- ★ 第一刀：展示墙分类（可配置） ----
  galleryCategories: [],  // [{id, name, sort}]，默认 游戏截图/老板评价/作品
  galleryCategoryCounts: {},  // {分类id: 几张图}，用来拦"还有图在用"的删除
  editingGalleryId: null,     // 正在"展开编辑"的那条展示墙记录（改分类 / 改外链）
  // ---- ★ 第三刀：收入登记 + 月度统计 ----
  payments: [],           // 全部收入流水（后端已按"新的在前"排好）
  income: {},             // 当前范围的统计（总额 / 环比 / 小卡 / 排行 / 明细）
  incomeRanges: [],       // 范围按钮：[{id:'month', name:'本月'}, …]
  payRange: 'month',      // 现在看的是哪个范围（和后端 INCOME_RANGES 的 id 对上）
  editingPaymentId: null, // 弹窗里正在改哪一条（null = 记一笔新的）
  // ---- ★ 第四刀：文案 + 服务条款 + 图片显示位置 ----
  texts: {},              // 完整文案表（后端把默认值补齐了）
  textGroups: [],         // 目录：分组 + 每条的说明 / 默认值 / 现在的值
  textOverrides: {},      // 只存了这几句（"恢复默认"和"改过几句"都看它）
  terms: { title: '', updated: '', sections: [] },   // 服务条款
  // ★ 位置值现在是在图上拖出来的（九宫格已经删掉），所以这份"9 个选项"后台界面
  //   不再拿它画按钮了 —— 保留它只是为了老版本的页面/接口对得上（后端还在下发）。
  //   拖框要用的"关键词 → 比例"表在下面 POS_FRACTIONS 里，两份含义不同，别搞混。
  positionChoices: [],    // 老的 9 个关键词 [{value:'left top', name:'左上'}, …]
};

/* ---------- 时间工具（一律按北京时间算） ---------- */
function cnParts(ms) {
  const d = new Date(ms + CN_OFFSET);
  return { y: d.getUTCFullYear(), m: d.getUTCMonth() + 1, d: d.getUTCDate(),
           h: d.getUTCHours(), w: d.getUTCDay() };
}
const midnightOf = (ms) => {
  const p = cnParts(ms);
  return Date.UTC(p.y, p.m - 1, p.d) - CN_OFFSET;
};
const dateValue = (ms) => { const p = cnParts(ms); return `${p.y}-${pad(p.m)}-${pad(p.d)}`; };
const msFromDateValue = (v) => {
  if (!v) return null;
  const [y, m, d] = v.split('-').map(Number);
  return Date.UTC(y, m - 1, d) - CN_OFFSET;
};
const timeLabel = (h) => `${pad(h)}:00`;
const titleOf = (ms) => { const p = cnParts(ms); return `${p.m}月${p.d}日 ${WEEK[p.w]}`; };
const dateTimeLabel = (ms) => {
  const p = cnParts(ms);
  const mi = new Date(ms + CN_OFFSET).getUTCMinutes();
  return `${p.m}月${p.d}日 ${pad(p.h)}:${pad(mi)}`;
};

/* ---------- 通用 ---------- */
async function api(path, options = {}) {
  const res = await fetch(path, {
    ...options,
    headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
  });
  const text = await res.text();
  const data = text ? JSON.parse(text) : {};
  if (res.status === 401) {
    showLogin();
    throw new Error(data.error || '登录已失效，请重新登录。');
  }
  if (!res.ok) throw new Error(data.error || `请求失败（${res.status}）`);
  return data;
}

let toastTimer = null;
function toast(message, isError = false) {
  const el = $('#toast');
  el.textContent = message;
  el.className = 'toast show' + (isError ? ' error' : '');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.className = 'toast'; }, 2600);
}

function escapeHtml(text) {
  return String(text == null ? '' : text).replace(/[&<>"']/g, (c) => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]
  ));
}

/**
 * 从文案表里取一句话（和前台 site.js / zone.js 的 tx() 是同一个东西）。
 *
 * 后台为什么也需要它：后台里有几句"解释这个功能怎么用"的话（比如密码打码的提示），
 * 它们也在 app.py 的 TEXT_GROUPS 目录里（第 ⑥ 组），
 * 所以主人换个说法之后，后台这边要跟着变。
 * 服务端已经把默认值补齐了（state.texts），第二个参数只是"连文案表都没拿到"时的保险。
 * {zone} 这类变量用第三个参数传进来（后台这边主要是 {zone}）。
 */
function tx(key, fallback, vars) {
  const value = (state.texts || {})[key];
  const text = (value === undefined || value === null || value === '') ? (fallback || '') : value;
  const map = vars || {};
  return String(text).replace(/\{(\w+)\}/g, (m, k) => (map[k] !== undefined ? map[k] : m));
}

const groupIcon = (name) => GROUP_ICON[name] || '🎯';
const zoneById = (id) => state.zones.find((z) => z.id === id) || null;
const currentZone = () => zoneById(state.currentZoneId);

/* ---------- 登录 ---------- */
function showLogin() {
  $('#loginView').style.display = '';
  $('#adminView').style.display = 'none';
}
function showAdmin() {
  $('#loginView').style.display = 'none';
  $('#adminView').style.display = '';
}

$('#loginForm').addEventListener('submit', async (e) => {
  e.preventDefault();
  const btn = $('#loginBtn');
  btn.disabled = true;
  btn.textContent = '验证中…';
  try {
    await api('/api/auth', { method: 'POST', body: JSON.stringify({ password: $('#password').value }) });
    $('#password').value = '';
    showAdmin();
    await loadAll();
    // 从登录页进来时也要初始化"锁定一个时段"表单，
    // 否则开始/结束时间会停在 00:00–24:00（浏览器实测发现的 bug）
    resetLockForm();
  } catch (err) {
    $('#loginHint').textContent = err.message;
    $('#loginHint').className = 'hint error';
  } finally {
    btn.disabled = false;
    btn.textContent = '进入管理终端';
  }
});

$('#logoutBtn').addEventListener('click', async () => {
  await api('/api/auth', { method: 'DELETE' });
  location.reload();
});

/* ---------- 备份提醒：免费实例随时可能清空数据 ---------- */
function renderBackupState() {
  const last = Number(state.lastExportAt) || 0;
  const days = last ? Math.floor((Date.now() - last) / 86400000) : null;
  const hours = last ? Math.floor((Date.now() - last) / 3600000) : null;
  const text = !last ? '还没有导出过备份'
    : (hours < 1 ? '上次导出：刚刚' : (hours < 24 ? `上次导出：${hours} 小时前` : `上次导出：${days} 天前`));
  if ($('#exportState')) $('#exportState').textContent = text;

  const notice = $('#backupNotice');
  if (!notice) return;
  const overdue = !last || days >= 7;
  if (!overdue) { notice.hidden = true; return; }
  notice.hidden = false;
  notice.className = 'notice warn';
  notice.innerHTML = !last
    ? '⚠️ 你还没有导出过备份。免费实例休眠就会清空所有预约记录和图片——先去「安全与备份」点一次「下载备份 JSON」。'
    : `⚠️ 已经 ${days} 天没导出备份了。免费实例随时可能休眠清空数据，建议现在去「安全与备份」导出一份。`;
}

/* ---------- 数据加载 ---------- */
async function loadAll() {
  const [admin, pub] = await Promise.all([api('/api/admin'), api('/api/public')]);
  state.profile = admin.profile;
  state.revision = admin.profileRevision;
  state.lastExportAt = admin.lastExportAt || 0;
  state.dataDir = admin.dataDir || '';
  state.notifyChannel = admin.notifyChannel || 'off';
  state.notifyTarget = admin.notifyTarget || '';
  state.notifyLast = admin.notifyLast || {};
  state.categories = admin.categories || [];
  state.zones = admin.zones || [];
  state.locks = admin.schedule.locks;
  state.rules = admin.schedule.rules;
  state.gallery = admin.gallery;
  state.customers = admin.customers || [];              // ★ 客户档案
  state.customerStats = admin.customerStats || {};
  state.customerToday = admin.today || '';
  state.pendingLocks = admin.pendingLocks || [];        // ★ 待确认（第二刀）
  state.autoReleaseHours = admin.autoReleaseHours || 0;
  state.autoReleased = admin.autoReleased || [];
  state.serverTime = admin.serverTime || Date.now();
  state.galleryCategories = admin.galleryCategories || [];   // ★ 展示墙分类（第一刀）
  state.galleryCategoryCounts = admin.galleryCategoryCounts || {};
  state.payments = admin.payments || [];                    // ★ 收入流水（第三刀）
  state.income = admin.income || {};
  state.incomeRanges = admin.incomeRanges || [];
  state.texts = admin.texts || {};                          // ★ 文案（第四刀）
  state.textGroups = admin.textGroups || [];
  state.textOverrides = admin.textOverrides || {};
  state.terms = admin.terms || { title: '', updated: '', sections: [] };
  state.faq = admin.faq || { title: '', subtitle: '', items: [] };        // ★ 常见问题
  state.steps = admin.steps || { title: '', items: [] };                  // ★ 服务流程
  state.positionChoices = admin.positionChoices || [];      // ★ 九宫格的 9 个选项
  state.publicSlots = pub.slots;
  if (!state.date) state.date = midnightOf(pub.serverTime);
  // 正在编辑的分区：保持原来的选择；没有就选第一个；一个分区都没有就留空
  if (!zoneById(state.currentZoneId)) state.currentZoneId = state.zones.length ? state.zones[0].id : '';
  if (!zoneById(state.galleryZoneId)) state.galleryZoneId = state.currentZoneId;
  state.batch.clear();
  renderAll();
}

async function loadSchedule() {
  const data = await api('/api/admin');
  state.locks = data.schedule.locks;
  state.rules = data.schedule.rules;
  state.pendingLocks = data.pendingLocks || [];        // ★ 待确认（每次动时段都跟着刷新）
  state.autoReleaseHours = data.autoReleaseHours || 0;
  state.autoReleased = data.autoReleased || [];
  state.serverTime = data.serverTime || Date.now();
  renderStats();
  renderTodoBar();
  renderAdminGrid();
  renderLockList();
  renderRuleList();
}

function renderAll() {
  renderStats();
  renderTodoBar();
  renderPending();
  renderAdminGrid();
  renderLockList();
  renderRuleList();
  renderCustomers();          // ★ 客户标签页（统计卡 / 待催 / 列表）
  renderIncome();             // ★ 收入标签页（大字 / 小卡 / 排行 / 流水）
  renderZoneAdmin();
  fillZonePickers();
  fillZoneForm();
  fillSiteForm();
  renderGallery();
  renderBackupState();
  renderNotify();
  renderDataHint();
  renderTexts();              // ★ 文案标签页（照着目录生成输入框）
  renderTerms();              // ★ 服务条款（标题 + 小节编辑器）
  renderFaq();                // ★ 常见问题（一条条增删改排序）
  renderSteps();              // ★ 服务流程（2~6 步）
  renderPosPickers();         // ★ 图片显示位置：把五张图按当前位置摆好
  bindPositionDragging();     // ★ 让那五个拖框能拖（鼠标 + 手指）
  bindPreviewWidthPickers();  // ★ 「预览屏宽」那两排按钮（只挂一次，和上面同一个道理）
  $('#adminWho').textContent = `已登录 · ${state.profile.name || ''}`;
  $('#adminTitle').textContent = `${state.profile.name || ''} 的管理终端`;
}

/* ---------- 「数据放在哪里」 ---------- */
function renderDataHint() {
  const el = $('#dataHint');
  if (!el) return;
  const dir = state.dataDir || '（服务器没有返回路径）';
  const rows = [
    `<b>数据在服务器的这个目录里：</b><code>${escapeHtml(dir)}</code>`,
    '· <code>site.db</code> — 预约记录、休息规则、你的资料、<b>分区（价格/文案）</b>、展示墙信息',
    '· <code>uploads/</code> — 展示墙图片、头像、分区背景图/首屏图等文件',
    '',
    '在 Render 上，这个路径由环境变量 <code>DATA_DIR</code> 决定'
      + '（默认 <code>./data</code>；挂了持久磁盘就是 <code>/var/data</code>）。',
    '⚠️ 免费实例休眠或重新部署时，这个目录会被清空 —— 所以请定期点上面的「下载备份 JSON」，'
      + '真丢了用「从备份恢复」导回去。',
  ];
  el.innerHTML = rows.map((r) => (r ? `<p style="margin:4px 0">${r}</p>` : '<p style="margin:8px 0"></p>')).join('');
}

/* ---------- 统计 ---------- */
function renderStats() {
  const today = midnightOf(Date.now());
  const todayLocks = state.locks.filter((l) => l.start < today + DAY && today < l.end);
  const bookings = todayLocks.filter((l) => l.kind === 'booking');
  const todaySlots = state.publicSlots.filter((s) => s.start >= today && s.start < today + DAY);
  const free = todaySlots.filter((s) => s.status === 'free').length;
  const activeRules = state.rules.filter((r) => r.enabled).length;

  $('#stats').innerHTML = `
    <div class="stat"><span>今日预约</span><strong>${bookings.length}<small>单</small></strong></div>
    <div class="stat"><span>客户自助预约（待确认）</span><strong>${state.pendingLocks.length}<small>单</small></strong></div>
    <div class="stat"><span>今日可预约</span><strong>${free}<small>小时</small></strong></div>    <div class="stat"><span>启用中的休息规则</span><strong>${activeRules}<small>条</small></strong></div>
    <div class="stat"><span>分区（含隐藏）</span><strong>${state.zones.length}<small>个</small></strong></div>
    <div class="stat"><span>展示墙已公开</span><strong>${state.gallery.filter((g) => g.visible && !g.archived).length}<small>条</small></strong></div>`;
}

/* ==========================================================================
   ★ 今日待办条（在标签栏上方那一条）
   --------------------------------------------------------------------------
   存在的理由：每天要做的三件事本来分散在两个标签页里（预约与时间 / 客户），
   打开后台还得自己算。这里把它压成一行字：**今天要打几单、要催几个人、几单还没确认**。

   三个数字的口径（都只用 /api/admin 已经返回的数据，没有新接口）：
     📌 今天要打  = 今天（北京时间）的 booking 类锁数量，算法和 renderStats() 完全一样
                   （todayLocks() 就抽的是那两行，两边永远同一个数）
     📞 客户要催  = customerStats.remind（后端算的"已超期 + 7 天内到期"，和「客户」页一致）
     ✅ 待确认    = pendingLocks 的条数（第二刀的待确认单）
   ========================================================================== */

/** 今天（北京时间 0 点起 24 小时）里所有"和今天有交集"的锁 —— renderStats 用的就是它 */
function todayLocks() {
  const today = midnightOf(Date.now());
  return state.locks.filter((l) => l.start < today + DAY && today < l.end);
}

/** 今天要打几单：今天的 booking 类锁（休息锁不算单） */
const todayBookingCount = () => todayLocks().filter((l) => l.kind === 'booking').length;

/** 今天要不要催客户：后端算好的"待催"人数（超期 + 7 天内到期） */
const remindCount = () => Number((state.customerStats || {}).remind) || 0;

/** 有几单还没确认 */
const pendingCount = () => state.pendingLocks.length;

function renderTodoBar() {
  const el = $('#todoBar');
  if (!el) return;
  const items = [
    // [数字, 文案, 点了去哪个标签页, 想高亮哪一块]
    // ⚠️ 文案格式是「N 个客户要催」「N 单待确认」这种 —— docs\上线后第一周.md 里
    //    引用的是「N 个客户要催」，改这里的话文档也要跟着改（tools\check_docs.py 会查）。
    [todayBookingCount(), '单要打', 'schedule', 'grid'],
    [remindCount(), '个客户要催', 'customers', 'remind'],
    [pendingCount(), '单待确认', 'schedule', 'pending'],
  ];
  const total = items.reduce((sum, it) => sum + it[0], 0);
  const head = state.customerToday
    ? `📌 今天（${cnDate(state.customerToday)}）：` : '📌 今天：';
  if (!total) {
    // 全为 0 时不摆一排灰数字，给一句轻松的
    el.innerHTML = `<span class="todo-head">${head}</span>
      <span class="todo-clear">今天没有待办，安心打单～</span>`;
    return;
  }
  // 数字用醒目的颜色（有就红/橙），0 的那一项整块灰掉
  el.innerHTML = `<span class="todo-head">${head}</span>` + items.map(([n, label, tab, focus]) => (
    `<button class="todo-item ${n ? 'hot' : 'zero'}" data-todo-tab="${tab}" data-todo-focus="${focus}"
       title="${n ? '点一下去处理' : '这一项今天没有'}">${n} ${label}</button>`
  )).join('<span class="todo-sep">·</span>');
}

/** 点了待办条上的某一项：跳到对应标签页，并把要处理的那一块滚到眼前 */
function gotoTab(tab, focus) {
  // ⚠️ 同样按 data-tab 精确匹配（原因见下面 .tab 点击事件里那段注释）
  const panel = $('[data-panel="' + (tab || '') + '"]');
  if (!panel) return;
  $$('.tab').forEach((t) => t.classList.toggle('active', t.dataset.tab === tab));
  $$('[data-panel]').forEach((p) => { p.style.display = p === panel ? '' : 'none'; });
  const target = focus === 'pending' ? $('#pendingPanel')
    : (focus === 'remind' ? $('#customerRemind') : $('#adminGrid'));
  if (target && target.scrollIntoView) {
    target.scrollIntoView({ behavior: 'smooth', block: focus === 'grid' ? 'start' : 'center' });
  }
}

/* ==========================================================================
   ★ 待确认处理台（第二刀的核心）
   --------------------------------------------------------------------------
   每一行一句话说清"谁、怎么联系、哪一场、多久了"，右边两个按钮：
     「已确认」   → confirmed=1，这单就这样了
     「释放时段」 → 把这条锁删掉，时间回到可约（要二次确认，因为删了就回不来了）
   ========================================================================== */

/** "已经等了 3 小时" / "已经等了 25 分钟"（用**服务器时间**算，不信客户端的钟） */
function waitedText(createdAt) {
  if (!createdAt) return '下单时间不明';
  const now = state.serverTime || Date.now();
  const mins = Math.max(0, Math.floor((now - Number(createdAt)) / 60000));
  if (mins < 60) return `已经等了 ${mins} 分钟`;
  if (mins < 60 * 24) return `已经等了 ${Math.floor(mins / 60)} 小时`;
  return `已经等了 ${Math.floor(mins / (60 * 24))} 天`;
}

function renderPending() {
  const list = $('#pendingList');
  if (!list) return;
  const items = state.pendingLocks || [];
  const countEl = $('#pendingCount');
  if (countEl) countEl.textContent = `${items.length} 单`;
  list.innerHTML = items.length ? items.map((lk) => `
    <article class="record pending-record">
      <span class="badge client">客户自助预约</span>
      ${zoneBadgeHtml(lk.zone)}
      <div class="grow">
        <strong>${escapeHtml(lk.summary || lk.client || '')}</strong>
        <p class="pending-wait">⏳ ${escapeHtml(waitedText(lk.created_at))}还没确认</p>
      </div>
      <button class="button small primary" data-confirm-lock="${lk.id}">已确认</button>
      <button class="button small danger" data-release-lock="${lk.id}">释放时段</button>
    </article>`).join('')
    : '<div class="empty-state ok">没有待确认的预约 ✓</div>';

  // 自动释放设置 + 最近释放记录
  const box = $('#autoReleaseHours');
  if (box && document.activeElement !== box) box.value = String(state.autoReleaseHours || 0);
  const stateEl = $('#autoReleaseState');
  if (stateEl) {
    stateEl.className = (state.autoReleaseHours > 0) ? 'hint ok' : 'hint';
    stateEl.textContent = (state.autoReleaseHours > 0)
      ? `已开启：待确认超过 ${state.autoReleaseHours} 小时会自动释放`
      : '当前关闭（不会自动释放任何东西）';
  }
  const logBox = $('#autoReleasedBox');
  if (logBox) {
    const log = state.autoReleased || [];
    logBox.innerHTML = log.length ? `<p class="hint" style="margin:10px 0 4px"><b>最近自动释放：</b></p>`
      + log.slice(0, 10).map((r) => `<p class="hint" style="margin:2px 0">
          · ${escapeHtml(r.name || '这位客户')} ${escapeHtml(dateTimeLabel(r.start))}
          （超过 ${Number(r.hours) || 0} 小时没确认）</p>`).join('')
      : '<p class="hint" style="margin:10px 0 4px">还没有自动释放过任何预约。</p>';
  }
}

/** 把 /api/admin/schedule 的返回写回本地状态（前端不用自己猜，整块重画） */
function applyScheduleResult(data, message = '') {
  if (data.schedule) {
    state.locks = data.schedule.locks;
    state.rules = data.schedule.rules;
  }
  if (data.pendingLocks) state.pendingLocks = data.pendingLocks;
  if (data.autoReleaseHours !== undefined) state.autoReleaseHours = data.autoReleaseHours || 0;
  if (data.autoReleased) state.autoReleased = data.autoReleased;
  if (data.serverTime) state.serverTime = data.serverTime;
  renderStats();
  renderTodoBar();
  renderPending();
  renderAdminGrid();
  renderLockList();
  // ★ 点「已确认」会顺手记一笔收入（后端做的），这里把新统计画出来
  //   （返回里没有 payments 长列表，所以只更新统计，列表保持原样）
  if (data.income) {
    state.income = data.income;
    if (data.incomeRanges) state.incomeRanges = data.incomeRanges;
    renderIncome();
  }
  if (message) toast(message);
}

/* ---------- 预约通知设置 ---------- */
const NOTIFY_PLACEHOLDER = {
  off: '关闭通知时不用填',
  pushplus: 'PushPlus 的 token（32 位字符）',
  serverchan: 'Server酱的 SendKey（SCT 开头）',
  webhook: 'https://你的地址/hook',
};

function renderNotify() {
  $('#notifyChannel').value = state.notifyChannel || 'off';
  $('#notifyTarget').value = state.notifyTarget || '';
  applyNotifyPlaceholder();
  renderNotifyState(state.notifyLast);
}

function applyNotifyPlaceholder() {
  const channel = $('#notifyChannel').value;
  $('#notifyTarget').placeholder = NOTIFY_PLACEHOLDER[channel] || 'Token 或网址';
}

function renderNotifyState(last) {
  const el = $('#notifyState');
  if (!el) return;
  if (!last || !last.time) {
    el.className = 'hint';
    el.textContent = '还没有发送过通知。配好渠道后点「发送测试通知」验证一下。';
    return;
  }
  const when = dateTimeLabel(last.time);
  if (last.ok) {
    el.className = 'hint ok';
    el.textContent = `上次发送成功（${when}）：${last.detail || ''}`;
  } else {
    el.className = 'hint error';
    el.textContent = `上次发送失败（${when}）：${last.detail || '未知原因'}`;
  }
}

/* ---------- 时间格子 ---------- */
function ruleCovers(rule, slotStart) {
  const startHour = Number(rule.start_hour);
  const endHour = Number(rule.end_hour);
  const span = endHour === 24 ? 24 - startHour : (((endHour - startHour) % 24) + 24) % 24;
  if (span <= 0) return false;
  for (const back of [0, 1]) {
    const base = midnightOf(slotStart) - back * DAY;
    if (base < rule.from_ts) continue;
    if (rule.until_ts && base > rule.until_ts) continue;
    if (!rule.days.includes(cnParts(base).w)) continue;
    const ws = base + startHour * HOUR;
    const we = ws + span * HOUR;
    if (slotStart >= ws && slotStart < we) return true;
  }
  return false;
}

function statusFor(start) {
  const now = Date.now();
  if (start + HOUR <= now) return 'past';
  for (const lk of state.locks) {
    if (lk.start < start + HOUR && start < lk.end) return lk.kind === 'booking' ? 'booking' : 'rest';
  }
  for (const rule of state.rules) {
    if (rule.enabled && ruleCovers(rule, start)) return 'rest';
  }
  if (!state.profile.accepting) return 'closed';
  return 'free';
}

const STATUS_TEXT = { free: '空闲', booking: '已预约', rest: '休息', past: '已过去', closed: '停接' };

function renderAdminGrid() {
  $('#adminDate').value = dateValue(state.date);
  const cells = [];
  for (let h = 0; h < 24; h++) {
    const start = state.date + h * HOUR;
    const status = statusFor(start);
    const selected = state.batch.has(start);
    const clickable = status === 'free' || status === 'rest';
    cells.push(`<button class="hour-cell ${status} ${selected ? 'selected' : ''}"
      data-start="${start}" ${clickable ? '' : 'disabled'}>
      ${timeLabel(h)}<small>${selected ? '已选' : STATUS_TEXT[status]}</small></button>`);
  }
  $('#adminGrid').innerHTML = cells.join('');
  $('#batchCount').textContent = state.batch.size;
}

/* ---------- 锁定时段 ---------- */
function fillHourSelects() {
  $('#lockStart').innerHTML = Array.from({ length: 24 }, (_, h) =>
    `<option value="${h}">${timeLabel(h)}</option>`).join('');
  $('#lockEnd').innerHTML = Array.from({ length: 24 }, (_, i) =>
    `<option value="${i + 1}">${timeLabel(i + 1)}</option>`).join('');
  $('#lockEnd').value = '24';
}

function resetLockForm() {
  state.editingLockId = null;
  state.batch.clear();
  $('#lockFormTitle').textContent = '锁定一个时段';
  $('#lockKind').value = 'booking';
  $('#lockClient').value = '';
  $('#lockNote').value = '';
  const nowHour = new Date(Date.now() + CN_OFFSET).getUTCHours();
  const startHour = cnParts(state.date).d === cnParts(Date.now()).d ? Math.min(23, nowHour + 1) : 18;
  $('#lockStart').value = String(startHour);
  $('#lockEnd').value = String(Math.min(24, startHour + 1));
  renderAdminGrid();
}

function editLock(lockId) {
  const lk = state.locks.find((l) => l.id === lockId);
  if (!lk) return;
  state.editingLockId = lk.id;
  state.batch.clear();
  const startHour = cnParts(lk.start).h;
  const endAtMidnight = cnParts(lk.end).h === 0 && lk.end - midnightOf(lk.end) === 0;
  const endHour = endAtMidnight ? 24 : cnParts(lk.end).h;
  state.date = midnightOf(lk.start);
  $('#lockFormTitle').textContent = '编辑已有锁定（改完点保存）';
  $('#lockKind').value = lk.kind === 'rest' ? 'rest' : 'booking';
  $('#lockClient').value = lk.client || '';
  $('#lockNote').value = lk.note || '';
  renderAdminGrid();
  $('#lockStart').value = String(startHour);
  $('#lockEnd').value = String(endHour);
  window.scrollTo({ top: $('#adminGrid').offsetTop - 120, behavior: 'smooth' });
}

/** 这条锁是哪个分区的（带分区名和主色的小标签） */
function zoneBadgeHtml(zoneId) {
  if (!zoneId) return '';
  const z = zoneById(zoneId);
  const name = z ? z.name : zoneId;
  const color = z && z.theme ? z.theme : 'var(--muted)';
  return `<span class="badge" style="color:${escapeHtml(color)};border-color:${escapeHtml(color)}">${escapeHtml(name)}</span>`;
}

function renderLockList() {
  const dayEnd = state.date + DAY;
  const items = state.locks.filter((l) => l.start < dayEnd && state.date < l.end);
  $('#lockList').innerHTML = items.length ? items.map((l) => {
    const label = `${titleOf(l.start)} ${timeLabel(cnParts(l.start).h)} – ${
      l.end - midnightOf(l.end) === 0 && cnParts(l.end).h === 0
        ? timeLabel(24) : timeLabel(cnParts(l.end).h)}`;
    const isClient = l.source === 'client';
    const badge = isClient
      ? '<span class="badge client">客户自助预约</span>'
      : '<span class="badge">自己锁定</span>';
    const brief = [];
    if (l.mode) brief.push(`模式：${escapeHtml(l.mode)}`);
    if (Number(l.price) > 0) brief.push(`<b class="money">¥${Number(l.price)}</b>`);
    if (isClient) {
      brief.push(l.contact
        ? `${CONTACT_TEXT[l.contact_type] || '微信'}：${escapeHtml(l.contact)}`
        : '客户没留联系方式');
    } else {
      brief.push(l.client ? `老板：${escapeHtml(l.client)}` : '未填写称呼');
      if (l.contact) brief.push(`${CONTACT_TEXT[l.contact_type] || '微信'}：${escapeHtml(l.contact)}`);
    }
    if (l.note) brief.push(`备注：${escapeHtml(l.note)}`);
    if (l.created_at) brief.push(`下单时间：${dateTimeLabel(l.created_at)}`);
    return `<article class="record">
      ${badge}
      ${zoneBadgeHtml(l.zone)}
      <span class="badge ${l.kind === 'booking' ? 'warn' : ''}">${l.kind === 'booking' ? '预约' : '休息'}</span>
      <div class="grow">
        <strong>${escapeHtml(label)}</strong>
        <p>${brief.join(' · ')}</p>
      </div>
      <button class="button small ghost" data-edit-lock="${l.id}">编辑</button>
      ${isClient ? `<button class="button small ghost" data-lock-customer="${l.id}"
        title="把这位老板存进「客户」档案，顺手记上到期日">存为客户</button>` : ''}
      <button class="button small danger" data-del-lock="${l.id}">取消</button>
    </article>`;
  }).join('') : '<div class="empty-state">这一天还没有预约或临时休息。</div>';
}

/* ---------- 休息规则 ---------- */
function renderRuleDays(selected = [0, 1, 2, 3, 4, 5, 6]) {
  const order = [1, 2, 3, 4, 5, 6, 0];
  $('#ruleDays').innerHTML = order.map((d) =>
    `<label class="tag" style="cursor:pointer">
      <input type="checkbox" value="${d}" ${selected.includes(d) ? 'checked' : ''}
        style="margin-right:6px;vertical-align:-1px">${WEEK[d]}
    </label>`).join('');
}

function fillRuleHourSelects() {
  const options = (max, from) => Array.from({ length: max - from + 1 }, (_, i) =>
    `<option value="${i + from}">${timeLabel(i + from)}</option>`).join('');
  $('#ruleStart').innerHTML = options(23, 0);
  $('#ruleEnd').innerHTML = options(24, 1);
}

function renderRuleList() {
  $('#ruleList').innerHTML = state.rules.length ? state.rules.map((r) => {
    const days = r.days.slice().sort((a, b) => ((a + 6) % 7) - ((b + 6) % 7))
      .map((d) => WEEK[d]).join(' / ');
    const span = r.end_hour === 24 ? 24 - r.start_hour : (((r.end_hour - r.start_hour) % 24) + 24) % 24;
    const range = `${timeLabel(r.start_hour)}–${r.end_hour < r.start_hour || r.end_hour === 24 ? '次日 ' : ''}${timeLabel(r.end_hour)}`;
    return `<article class="record">
      <span class="badge ${r.enabled ? 'ok' : 'off'}">${r.enabled ? '已启用' : '已暂停'}</span>
      <div class="grow">
        <strong>${escapeHtml(r.note || '固定休息')}</strong>
        <p>${escapeHtml(days)} · ${range}（共 ${span} 小时）· 生效 ${
          dateValue(r.from_ts)}${r.until_ts ? ' 至 ' + dateValue(r.until_ts) : ' 起长期'}</p>
      </div>
      <button class="button small ghost" data-toggle-rule="${r.id}" data-enabled="${r.enabled ? 0 : 1}">
        ${r.enabled ? '暂停' : '启用'}</button>
      <button class="button small ghost" data-edit-rule="${r.id}">编辑</button>
      <button class="button small danger" data-del-rule="${r.id}">移除</button>
    </article>`;
  }).join('') : '<div class="empty-state">还没有设置固定休息。建议先加一条"每天 02:00–10:00 睡觉休息"。</div>';
}

function resetRuleForm() {
  state.editingRuleId = null;
  renderRuleDays();
  $('#ruleStart').value = '2';
  $('#ruleEnd').value = '10';
  $('#ruleFrom').value = dateValue(Date.now());
  $('#ruleUntil').value = '';
  $('#ruleNote').value = '睡觉休息';
}

/* ==========================================================================
   ★ 客户档案 + 到期提醒（独立标签页「客户」）
   --------------------------------------------------------------------------
   这一块只解决一件事：**今天谁快到期了、该催谁了**。
   每天打开后台 →「客户」→ 看统计卡 → 看待催列表 → 点「复制催续费话术」→ 发微信。

   几个关键约定：
     · 剩余几天（daysLeft）和分档（level）都是**后端算好的**，前端只负责显示和上色，
       这样"还剩几天"全站只有一处逻辑，不会前后端各算一遍算岔；
     · 金额（price）是**自由文本**，原样显示，永远不加 ¥、不转数字；
     · 日期显示成「8月20日」这种中文写法（客户看得懂），存还是存 "YYYY-MM-DD"。
   ========================================================================== */

/** "2026-08-20" → "8月20日"（解析不了就原样返回） */
function cnDate(text) {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(text || ''));
  if (!m) return String(text || '');
  return `${Number(m[2])}月${Number(m[3])}日`;
}

/** 到期那一段人话：「还有 2 天」/「今天到期」/「已超期 2 天」/「长期」 */
function daysText(c) {
  const d = c.daysLeft;
  if (d === null || d === undefined) return '长期 / 一次性';
  if (d < 0) return `已超期 ${-d} 天`;
  if (d === 0) return '今天到期';
  return `还有 ${d} 天`;
}

/** 剩余天数的彩色徽章 */
function customerBadge(c) {
  const color = c.levelColor || 'var(--muted)';
  return `<span class="badge" style="color:${escapeHtml(color)};border-color:${escapeHtml(color)}">${
    escapeHtml(c.levelLabel || '')} · ${escapeHtml(daysText(c))}</span>`;
}

/** 联系方式那一格：渠道 + 号码（复制的时候也是复制这个） */
function contactText(c) {
  const label = CUSTOMER_CONTACT_TEXT[c.contact_type] || '微信';
  return c.contact ? `${label}：${c.contact}` : '（没填联系方式）';
}

/* ==========================================================================
   ★ 游戏账号六件事 + 密码打码（主人点名要的两件事）
   --------------------------------------------------------------------------
   1. **账号不打码，只有密码打码**。为什么不一样对待：
      账号（手机号）他经常要看着拨号、拿去搜索，打码了反而碍事；
      密码是"别人站你身后瞟一眼就能记住"的那种东西，所以默认挡住，
      点一下小眼睛才显示 —— 防的是身后的人，不是防他自己。
   2. **默认折叠、不自动展开、刷新后回到打码**：
      显示状态只存在于这一次渲染的 DOM 里，不写进 state、不写进 localStorage。
      为什么不记住"我看过哪个客户的密码"：那等于把密码长期摊在屏幕上，
      下次他打开后台（可能正在跟别人语音）就直接露出来了。
   3. 「复制密码」**不需要先点显示** —— 复制走的是数据里的原文，
      不用把明文渲染到页面上就能复制走（少一次"明文出现在屏幕上"的机会）。
   ========================================================================== */

/** 打码后的显示：••••••（固定 6 个点，不按真实长度 —— 免得从点数猜出密码多长） */
const PASSWORD_MASK = '••••••';

/**
 * 游戏账号那一格的 HTML：游戏 / 区服 一行，账号 / 密码 / 角色 / 练度 一行。
 * 全空就显示一个 —（比显示一堆空标签干净）。
 *
 * ⚠️ 练度那一段读的是 `c.gameLevel`，**不是 `c.level`** ——
 *    `c.level` 是"到期分档"算出来的（expired / today / soon…），
 *    两个意思挤同一个名字的话，客户列表上"练度"那一行会显示成 "none"。
 */
function accountCellHtml(c) {
  const head = [c.game, c.server].filter(Boolean).join(' · ');
  const rows = [];
  if (c.account) rows.push(`账号：${escapeHtml(c.account)}`);
  if (c.password) {
    // ⚠️ 明文放在 data-pw 属性里，默认渲染出来的是打码后的 6 个点；
    //    点小眼睛时 JS 把 textContent 换成 data-pw 的值（不重新拼 HTML）。
    rows.push(`密码：<span class="pw-value" data-pw="${escapeHtml(c.password)}">${
      PASSWORD_MASK}</span>
      <button class="button small ghost pw-toggle" data-pw-toggle="${c.id}"
              title="显示 / 隐藏密码">👁 显示</button>
      <button class="button small ghost" data-pw-copy="${c.id}"
              title="复制密码（不用先显示）">📋 复制</button>`);
  }
  if (c.role) rows.push(`角色：${escapeHtml(c.role)}`);
  if (c.gameLevel) rows.push(`练度：${escapeHtml(c.gameLevel)}`);
  if (!head && !rows.length) return '—';
  return (head ? `<strong>${escapeHtml(head)}</strong>` : '')
    + rows.map((r) => `<p class="acc-row">${r}</p>`).join('');
}

/** 「客户」页顶部那句提示（文字来自后台「文案」⑥ 后台提示） */
function renderCustomerPasswordHint() {
  const el = $('#customerPasswordHint');
  if (el) el.textContent = tx('adminPasswordMaskHint',
    '密码默认打码，点小眼睛才显示 —— 防的是别人站你身后看到屏幕。');
}

/**
 * ★ 催续费话术（想改口气就改这一个函数，改完刷新页面立刻生效）。
 *
 * 默认生成：
 *   老板，你的明日方舟 精托托管 8月20日到期啦（还有 2 天），要续吗？
 *   续的话还是 20R/月，跟我说一声就行～
 *
 * 四条规矩（都是为了让这句话听起来像人说的）：
 *   · 超期的换成「已经到期 2 天啦」（别催得像没事一样）
 *   · 没填档位就不提档位；连分区都没填就写成「你的服务」，不留一个空荡荡的"你的"
 *   · 没填金额就不提价格（免得客户以为你要涨价）
 *   · 没有到期日的（长期/一次性）换成问候口气，不硬说"到期"
 */
function renewMessage(c) {
  const days = c.daysLeft;
  // "你的[明日方舟 精托托管]"这一截；什么都没填就退回"你的服务"
  const parts = [c.zoneName || '', c.item || ''].filter(Boolean).join(' ');
  const what = parts || '服务';
  // 到期那一句：超期 / 今天 / 还有几天 / 没有到期日
  let when;
  if (days === null || days === undefined) {
    when = '';
  } else if (days < 0) {
    when = `已经到期 ${-days} 天啦`;
  } else if (days === 0) {
    when = '今天到期啦';
  } else {
    when = `${cnDate(c.end_date)}到期啦（还有 ${days} 天）`;
  }
  const line1 = when
    ? `老板，你的${what} ${when}，要续吗？`
    : `老板，你的${what}还顺利吗？要接着包的话跟我说一声就行～`;
  const lines = [line1];
  if (c.price) lines.push(`续的话还是 ${c.price}，跟我说一声就行～`);
  else lines.push('要续的话跟我说一声就行～');
  return lines.join('\n');
}

/** 复制文字：clipboard → execCommand → 长按兜底（和前台 zone.js 同一套三级兜底） */
async function copyText(text, okMessage = '已复制') {
  if (!text) return toast('这条没有内容可以复制', true);
  if (navigator.clipboard && window.isSecureContext) {
    try {
      await navigator.clipboard.writeText(text);
      toast(okMessage);
      return;
    } catch { /* 落到下一级 */ }
  }
  try {
    const ta = document.createElement('textarea');
    ta.value = text;
    ta.setAttribute('readonly', '');
    ta.style.cssText = 'position:fixed;left:-9999px;top:0;opacity:0';
    document.body.appendChild(ta);
    ta.select();
    ta.setSelectionRange(0, ta.value.length);
    const ok = document.execCommand('copy');
    document.body.removeChild(ta);
    if (ok) { toast(okMessage); return; }
    throw new Error('execCommand 不支持');
  } catch {
    showCopyFallback(text);
  }
}

/** 三级兜底的最后一级：弹个框让他长按复制（后台和前台用的同一个弹窗样式） */
function showCopyFallback(text) {
  const box = $('#copyTextBox');
  box.value = text;
  $('#copyModal').classList.add('open');
  setTimeout(() => { box.focus(); box.select(); }, 50);
  toast('请长按框里的文字复制', true);
}

/** 顶部 4 张统计卡（点一下 = 按这一档筛选客户列表） */
function renderCustomerStats() {
  const s = state.customerStats || {};
  const cards = [
    ['expired', '🔴 已超期', s.expired || 0, '先问一句，或者干脆放弃'],
    ['soon', '🟠 3 天内到期', s.soon || 0, '含今天到期和已超期'],
    ['week', '🟡 一周内到期', s.week || 0, '含上面两档'],
    ['active', '⚪ 进行中', s.active || 0, '还在服务期里的客户'],
  ];
  $('#customerStats').innerHTML = cards.map(([key, label, n, tip]) => `
    <button class="stat customer-stat ${state.customerFilter === key ? 'active' : ''}"
            data-cus-filter="${key}" title="${escapeHtml(tip)}">
      <span>${label}</span><strong>${n}<small>人</small></strong>
    </button>`).join('');
  $('#customerToday').textContent = state.customerToday
    ? `今天是 ${cnDate(state.customerToday)}（北京时间）· 共 ${s.total || 0} 位客户，`
      + `需要催的有 ${s.remind || 0} 位。`
    : '';
}

/** ② 本周需要催的客户（今天的待办清单） */
function renderCustomerRemind() {
  const items = state.customers.filter((c) => c.level === 'expired' || c.level === 'today'
    || c.level === 'soon' || c.level === 'week');
  $('#customerRemind').innerHTML = items.length ? items.map((c) => `
    <article class="record">
      ${customerBadge(c)}
      ${c.status !== 'active'
        ? `<span class="badge off">${escapeHtml(CUSTOMER_STATUS_TEXT[c.status] || '')}</span>` : ''}
      <div class="grow">
        <strong>${escapeHtml(c.name)}</strong>
        <p>${escapeHtml([c.zoneName, c.item, c.price].filter(Boolean).join(' · ') || '（没填服务和金额）')}
           · ${escapeHtml(c.end_date ? `${cnDate(c.end_date)}到期` : '')}</p>
      </div>
      <button class="button small ghost" data-cus-copy="${c.id}">复制催续费话术</button>
      <button class="button small primary" data-cus-renew="${c.id}">续费</button>
    </article>`).join('')
    : '<div class="empty-state">这周没有要催的，安心打单～</div>';
}

/**
 * ③ 客户列表的筛选
 *
 * ★ 搜索范围**故意包含**游戏 / 区服 / 账号 / 角色名（但不含密码）：
 *   主人最常用的动作就是"搜一个手机号 / 角色名，看看是谁、买的哪一档" ——
 *   这几项以前只能塞在备注里，搜起来全靠缘分。
 *   密码不进搜索：搜不出密码对他没损失（他都是从列表点「复制」），
 *   但"搜到一半把明文密码高亮出来"这种事能免则免。
 */
function filteredCustomers() {
  const q = state.customerSearch.trim().toLowerCase();
  return state.customers.filter((c) => {
    if (!customerMatchesFilter(c)) return false;
    if (!q) return true;
    return [c.name, c.contact, c.item, c.zoneName, c.price, c.note,
            c.game, c.server, c.account, c.role, c.gameLevel]
      .some((v) => String(v || '').toLowerCase().includes(q));
  });
}

function customerMatchesFilter(c) {
  switch (state.customerFilter) {
    case 'remind': return c.level === 'expired' || c.level === 'today'
      || c.level === 'soon' || c.level === 'week';
    case 'expired': return c.level === 'expired';
    case 'active': return c.status === 'active';
    case 'paused': return c.status === 'paused';
    case 'ended': return c.status === 'ended';
    default: return true;
  }
}

const CUSTOMER_FILTERS = [
  ['all', '全部'], ['remind', '待催（超期+7天内）'], ['expired', '已超期'],
  ['active', '进行中'], ['paused', '已暂停'], ['ended', '已结束'],
];

function renderCustomerList() {
  $('#customerFilters').innerHTML = CUSTOMER_FILTERS.map(([key, label]) => `
    <button class="filter ${state.customerFilter === key ? 'active' : ''}"
            data-cus-filter="${key}">${label}</button>`).join('');
  const items = filteredCustomers();
  const table = $('#customerTable');

  if (!state.customers.length) {
    table.innerHTML = '';
    $('#customerListState').textContent =
      '还没有客户档案。点右上角「+ 新增客户」，或者去「预约与时间」在客户自助预约那条右边点「存为客户」。';
    return;
  }
  if (!items.length) {
    table.innerHTML = '';
    $('#customerListState').textContent = state.customerSearch
      ? `没有匹配「${state.customerSearch}」的客户。`
      : '这个筛选下没有客户。';
    return;
  }
  $('#customerListState').textContent = `显示 ${items.length} / ${state.customers.length} 位客户`;

  // 一行一个客户。手机上靠 data-label 变成"标签: 值"，不用横向滚动（见 style.css）
  table.innerHTML = `
    <thead><tr>
      <th>客户</th><th>联系方式</th><th>服务 / 档位</th><th>游戏 / 区服</th><th>金额</th>
      <th>周期</th><th>剩余</th><th>操作</th>
    </tr></thead>
    <tbody>${items.map((c) => `
      <tr>
        <td data-label="客户"><strong>${escapeHtml(c.name)}</strong>${
          c.status !== 'active'
            ? ` <span class="badge off">${escapeHtml(CUSTOMER_STATUS_TEXT[c.status] || '')}</span>`
            : ''}${c.note ? `<p title="${escapeHtml(c.note)}">${escapeHtml(c.note)}</p>` : ''}</td>
        <td data-label="联系方式">${escapeHtml(contactText(c))}</td>
        <td data-label="服务 / 档位">${escapeHtml([c.zoneName, c.item].filter(Boolean).join(' · ')
          || '—')}</td>
        <td data-label="游戏 / 区服" class="account-cell">${accountCellHtml(c)}</td>
        <td data-label="金额" class="money">${escapeHtml(c.price || '—')}</td>
        <td data-label="周期">${escapeHtml(c.start_date ? cnDate(c.start_date) : '—')} → ${
          escapeHtml(c.end_date ? cnDate(c.end_date) : '长期')}</td>
        <td data-label="剩余">${customerBadge(c)}</td>
        <td data-label="操作">
          <button class="button small ghost" data-cus-edit="${c.id}">编辑</button>
          <button class="button small ghost" data-cus-renew="${c.id}">续费</button>
          <button class="button small ghost" data-cus-copy="${c.id}">复制话术</button>
          <button class="button small danger" data-cus-del="${c.id}">删除</button>
        </td>
      </tr>`).join('')}</tbody>`;
}

/** 客户标签页的总渲染（renderAll 和每次接口返回后都调它） */
function renderCustomers() {
  if (!$('#customerStats')) return;
  renderCustomerStats();
  renderCustomerRemind();
  renderCustomerPasswordHint();     // ★ 「密码默认打码…」那句提示（文案可改）
  renderCustomerList();
  fillCustomerZonePicker();
}

/** 把后端的返回写回本地状态（三个动作都回同一份全量） */
function applyCustomerResult(data) {
  if (data.customers) state.customers = data.customers;
  if (data.customerStats) state.customerStats = data.customerStats;
  if (data.today) state.customerToday = data.today;
  renderCustomers();
  renderTodoBar();      // ★ 待办条上的「N 个客户要催」跟着变（录了新客户 / 续了费都要刷新）
  // ★ 续费时填了金额 → 后端顺手记了一笔收入，这里把它一起画出来（不用等切标签页）
  if (data.income) applyIncomeResult(data);
}

/** 客户弹窗里的「服务 / 分区」下拉（选填，可以留空） */
function fillCustomerZonePicker() {
  const sel = $('#cZone');
  if (!sel) return;
  const keep = sel.value;
  const options = state.categories.map((cat) => {
    const members = zonesOfCategory(cat.id);
    if (!members.length) return '';
    return `<optgroup label="${escapeHtml(cat.name)}">${members.map((z) =>
      `<option value="${escapeHtml(z.id)}">${escapeHtml(z.name)}（/z/${escapeHtml(z.id)}）</option>`
    ).join('')}</optgroup>`;
  }).join('');
  sel.innerHTML = '<option value="">（不关联 / 手动填在备注里）</option>' + options;
  sel.value = zoneById(keep) ? keep : '';
}

/* ---------- 新增 / 编辑客户弹窗 ---------- */

/** 打开弹窗：customer 为空 = 新增；预填字段用于「存为客户」那条路 */
function openCustomerModal(customer = null, prefill = null) {
  state.editingCustomerId = customer ? customer.id : null;
  $('#customerModalTitle').textContent = customer ? `编辑「${customer.name}」` : '新增客户';
  $('#cName').value = customer ? (customer.name || '') : ((prefill && prefill.name) || '');
  $('#cContact').value = customer ? (customer.contact || '') : ((prefill && prefill.contact) || '');
  $('#cContactType').value = (customer && customer.contact_type)
    || (prefill && prefill.contact_type) || 'wechat';
  $('#cItem').value = (customer && customer.item) || '';
  $('#cPrice').value = (customer && customer.price) || '';
  $('#cStart').value = (customer && customer.start_date) || state.customerToday || '';
  $('#cEnd').value = (customer && customer.end_date) || '';
  $('#cStatus').value = (customer && customer.status) || 'active';
  // ★ 游戏账号六件事（老档案里没有这几个字段 → ''，不影响）
  $('#cGame').value = (customer && customer.game) || '';
  $('#cServer').value = (customer && customer.server) || '';
  $('#cAccount').value = (customer && customer.account) || '';
  // ⚠️ 编辑时密码框里放的是**明文**（就是要让他能改、能核对）；
  //    打码只在"列表显示"那一层做 —— 存进库里的永远是原文，
  //    不然列表那个「📋 复制密码」就复制不出东西了。
  $('#cPassword').value = (customer && customer.password) || '';
  $('#cRole').value = (customer && customer.role) || '';
  $('#cLevel').value = (customer && customer.gameLevel) || '';
  $('#cNote').value = (customer && customer.note) || ((prefill && prefill.note) || '');
  $('#cFormState').textContent = '';
  $('#cFormState').className = 'hint';
  fillCustomerZonePicker();
  $('#cZone').value = (customer && customer.zone) || ((prefill && prefill.zone) || '');
  if (!zoneById($('#cZone').value)) $('#cZone').value = '';
  $('#customerModal').classList.add('open');
  setTimeout(() => $('#cName').focus(), 40);
}

function closeCustomerModal() {
  $('#customerModal').classList.remove('open');
  state.editingCustomerId = null;
}

async function saveCustomerForm() {
  const name = $('#cName').value.trim();
  if (!name) {
    $('#cFormState').textContent = '客户称呼不能是空的 —— 至少要能叫出他。';
    $('#cFormState').className = 'hint error';
    return;
  }
  const btn = $('#cSave');
  btn.disabled = true;
  const isUpdate = !!state.editingCustomerId;      // ⚠️ 要在关弹窗前记住（关的时候会清空）
  try {
    const data = await api('/api/admin/customers', {
      method: 'POST',
      body: JSON.stringify({
        action: isUpdate ? 'update' : 'create',
        id: state.editingCustomerId || undefined,
        name,
        contact: $('#cContact').value.trim(),
        contact_type: $('#cContactType').value,
        zone: $('#cZone').value,
        item: $('#cItem').value.trim(),
        price: $('#cPrice').value.trim(),          // ★ 自由文本，原样发过去
        start_date: $('#cStart').value,
        end_date: $('#cEnd').value,
        status: $('#cStatus').value,
        note: $('#cNote').value.trim(),
        // ★ 游戏账号六件事（也全是自由文本；密码发的是明文，后端原样存）
        //   ⚠️ 练度这一项对外叫 gameLevel（不是 level）—— level 是"到期分档"用的名字
        game: $('#cGame').value.trim(),
        server: $('#cServer').value.trim(),
        account: $('#cAccount').value.trim(),
        password: $('#cPassword').value.trim(),
        role: $('#cRole').value.trim(),
        gameLevel: $('#cLevel').value.trim(),
      }),
    });
    applyCustomerResult(data);
    closeCustomerModal();
    toast(isUpdate ? '客户已更新' : `客户「${name}」已建档`);
  } catch (err) {
    $('#cFormState').textContent = err.message;
    $('#cFormState').className = 'hint error';
  } finally {
    btn.disabled = false;
  }
}

/* ---------- 续费弹窗 ---------- */

/**
 * 加 N 个自然月，日期超出那个月的天数就压到月末（1月31日 + 1月 = 2月28/29日）。
 * ⚠️ 这里必须和后端 app.py 的 add_months() **完全一样**：
 *    JS 的 setUTCMonth() 遇到溢出的日期会往后滚（1月31日 + 1月 = 3月3日），
 *    那样弹窗预览会说 3 月 3 日、真正存进去却是 2 月 28 日 —— 两边对不上就坑人了。
 */
function addMonths(date, months) {
  const y = date.getUTCFullYear();
  const m = date.getUTCMonth() + months;
  const d = date.getUTCDate();
  const lastDay = new Date(Date.UTC(y, m + 1, 0)).getUTCDate();
  return new Date(Date.UTC(y, m, Math.min(d, lastDay)));
}

/** 按"续多久 + 从哪天算"算出新的到期日（**只给弹窗预览用**，真正的计算在后端） */
function previewRenewEnd(customer) {
  const span = $('#renewSpan').value;
  const mode = $('#renewMode').value;
  let months = 0, weeks = 0, days = 0;
  if (span === '1m') months = 1;
  else if (span === '3m') months = 3;
  else if (span === '1w') weeks = 1;
  else days = Math.max(1, Math.min(3650, Number($('#renewDays').value) || 1));
  // 从原到期日推（保留客户已付的天数）还是从今天推（停了一阵子重新开始）
  const baseText = mode === 'from_today' ? state.customerToday : customer.end_date;
  const base = msFromDateValue(baseText);
  if (base == null) return { text: '', months, weeks, days };
  const end = addMonths(new Date(base + CN_OFFSET), months);
  const ms = end.getTime() - CN_OFFSET + (weeks * 7 + days) * DAY;
  return { text: dateValue(ms), months, weeks, days };
}

function openRenewModal(customer) {
  state.renewingCustomerId = customer.id;
  $('#renewTitle').textContent = `给「${customer.name}」续费`;
  $('#renewSub').textContent = customer.end_date
    ? `现在是 ${cnDate(customer.end_date)}到期（${daysText(customer)}）。`
    : '这个客户还没有到期日，先去「编辑」填一个再来续费。';
  $('#renewSpan').value = '1m';
  $('#renewMode').value = 'extend';
  $('#renewDaysField').hidden = true;
  $('#renewDays').value = '30';
  // ★ 这次收了多少钱：从客户档案那串自由文本里猜一个默认值（"100R/月" → 100），
  //   猜不出来就留空 —— 留空 = 这次不记收入，绝不逼他填。
  $('#renewAmount').value = payAmountFromText(customer.price);
  renderRenewPreview();
  $('#renewModal').classList.add('open');
}

function renderRenewPreview() {
  const c = state.customers.find((x) => x.id === state.renewingCustomerId);
  const el = $('#renewPreview');
  if (!c) { el.textContent = ''; return; }
  if (!c.end_date) {
    el.textContent = '没有到期日就没法续费 —— 点「取消」，用「编辑」先填一个到期日期。';
    return;
  }
  const r = previewRenewEnd(c);
  const from = $('#renewMode').value === 'from_today' ? '今天' : '原到期日';
  el.textContent = `当前：${cnDate(c.end_date)} 到期  →  续费后：${r.text ? cnDate(r.text) : '（算不出来）'}`
    + ` 到期（从${from}往后推）`;
}

async function confirmRenew() {
  const c = state.customers.find((x) => x.id === state.renewingCustomerId);
  if (!c) return;
  const r = previewRenewEnd(c);
  const btn = $('#renewConfirm');
  btn.disabled = true;
  try {
    const data = await api('/api/admin/customers', {
      method: 'POST',
      body: JSON.stringify({
        action: 'renew', id: c.id, months: r.months, weeks: r.weeks, days: r.days,
        mode: $('#renewMode').value,
        // ★ 选了/填了金额才发过去；空着 = 这次不记收入（后端也是这么判断的）
        amount: $('#renewAmount').value.trim() || undefined,
      }),
    });
    applyCustomerResult(data);
    $('#renewModal').classList.remove('open');
    toast(`已续费：${cnDate(c.end_date)} → ${cnDate(data.newEndDate)}`);
    if (data.message) toast(data.message);           // "已记一笔收入 ¥100 …"
    if (data.tip) toast(data.tip, true);
  } catch (err) {
    toast(err.message, true);
  } finally {
    btn.disabled = false;
  }
}

/** 点「复制话术」：复制到剪贴板 + toast（失败走三级兜底） */
function copyRenewMessage(id) {
  const c = state.customers.find((x) => x.id === id);
  if (!c) return;
  copyText(renewMessage(c), `已复制「${c.name}」的催续费话术，粘贴到微信发给他就行`);
}

/** 删除客户（不可恢复，但只删档案本身，预约记录一条不动） */
async function deleteCustomerFlow(id) {
  const c = state.customers.find((x) => x.id === id);
  if (!c) return;
  if (!confirm(`删除客户「${c.name}」的档案？\n\n`
    + `只删这条档案（称呼、档位、金额、到期日、备注）。\n`
    + `预约记录和展示墙**一条都不会动**。\n\n想留着记录只想清理列表的话，点「取消」。`)) return;
  try {
    const data = await api('/api/admin/customers', {
      method: 'POST', body: JSON.stringify({ action: 'delete', id }),
    });
    applyCustomerResult(data);
    toast(`已删除客户「${c.name}」`);
  } catch (err) {
    toast(err.message, true);
  }
}

/** ★ 从预约记录一键建档：名称 / 联系方式 / 渠道已经填好，他补档位和日期就行 */
function customerFromBooking(lockId) {
  const lk = state.locks.find((l) => l.id === lockId);
  if (!lk) return;
  const note = [];
  if (lk.mode) note.push(`预约模式：${lk.mode}`);
  if (lk.zone) note.push(`来自分区：${(zoneById(lk.zone) || {}).name || lk.zone}`);
  if (lk.start) note.push(`首次预约：${titleOf(lk.start)} ${timeLabel(cnParts(lk.start).h)}`);
  if (lk.note) note.push(`客户备注：${lk.note}`);
  // 跳到「客户」标签页，把弹窗开好（名称取联系方式：客户自助预约时留的就是他的号）
  $$('.tab').forEach((t) => t.classList.toggle('active', t.dataset.tab === 'customers'));
  $$('[data-panel]').forEach((p) => {
    p.style.display = p.dataset.panel === 'customers' ? '' : 'none';
  });
  openCustomerModal(null, {
    name: lk.client || lk.contact || '',
    contact: lk.contact || '',
    contact_type: lk.contact_type === 'qq' || lk.contact_type === 'xianyu'
      ? lk.contact_type : 'wechat',
    zone: lk.zone || '',
    note: note.join('\n'),
  });
  toast('补上「买的档位」和「到期日期」就能建档（到期日留空 = 长期）');
}

/* ---------- 客户标签页的事件 ---------- */

// 统计卡和筛选按钮都是同一批 data-cus-filter，点哪边都行
$('#customerStats').addEventListener('click', (e) => {
  const hit = e.target.closest('[data-cus-filter]');
  if (!hit) return;
  state.customerFilter = hit.dataset.cusFilter;
  renderCustomerStats();
  renderCustomerList();
});

$('#customerFilters').addEventListener('click', (e) => {
  const hit = e.target.closest('[data-cus-filter]');
  if (!hit) return;
  state.customerFilter = hit.dataset.cusFilter;
  renderCustomerStats();
  renderCustomerList();
});

$('#customerSearch').addEventListener('input', (e) => {
  state.customerSearch = e.target.value;
  renderCustomerList();
});

$('#newCustomer').addEventListener('click', () => openCustomerModal());

// 待催区和客户列表共用一套 data-cus-* 按钮，所以绑在 section 上（一次搞定两处）
$('#customerRemind').addEventListener('click', customerAction);
$('#customerTable').addEventListener('click', customerAction);

async function customerAction(e) {
  const edit = e.target.closest('[data-cus-edit]');
  if (edit) {
    const c = state.customers.find((x) => x.id === edit.dataset.cusEdit);
    if (c) openCustomerModal(c);
    return;
  }
  const renew = e.target.closest('[data-cus-renew]');
  if (renew) {
    const c = state.customers.find((x) => x.id === renew.dataset.cusRenew);
    if (c) openRenewModal(c);
    return;
  }
  const copy = e.target.closest('[data-cus-copy]');
  if (copy) return copyRenewMessage(copy.dataset.cusCopy);
  // ★ 密码的小眼睛：只改**这一个** span 的文字，别的行、别的客户都不动 ✓
  //   （主人明确要求：切换只影响当前这一条，刷新后又回到打码）
  const eye = e.target.closest('[data-pw-toggle]');
  if (eye) {
    const span = eye.closest('td').querySelector('.pw-value');
    if (!span) return;
    const shown = span.dataset.shown === '1';
    span.textContent = shown ? PASSWORD_MASK : (span.dataset.pw || '');
    span.dataset.shown = shown ? '0' : '1';
    span.classList.toggle('revealed', !shown);
    eye.textContent = shown ? '👁 显示' : '🙈 隐藏';
    return;
  }
  // ★ 复制密码：**不用先点显示** —— 直接从 data-pw 拿原文去复制，
  //   少一次"明文出现在屏幕上"的机会（站着的人看不到，剪贴板里已经有了）
  const pwCopy = e.target.closest('[data-pw-copy]');
  if (pwCopy) {
    const span = pwCopy.closest('td').querySelector('.pw-value');
    return copyText((span && span.dataset.pw) || '', '密码已复制');
  }
  const del = e.target.closest('[data-cus-del]');
  if (del) return deleteCustomerFlow(del.dataset.cusDel);
}

$('#cSave').addEventListener('click', saveCustomerForm);
$('#cCancel').addEventListener('click', closeCustomerModal);
$('#customerModal').addEventListener('click', (e) => {
  if (e.target === $('#customerModal')) closeCustomerModal();     // 点空白处关掉
});

$('#renewSpan').addEventListener('change', () => {
  $('#renewDaysField').hidden = $('#renewSpan').value !== 'custom';
  renderRenewPreview();
});
$('#renewDays').addEventListener('input', renderRenewPreview);
$('#renewMode').addEventListener('change', renderRenewPreview);
$('#renewConfirm').addEventListener('click', confirmRenew);
$('#renewCancel').addEventListener('click', () => $('#renewModal').classList.remove('open'));
$('#renewModal').addEventListener('click', (e) => {
  if (e.target === $('#renewModal')) $('#renewModal').classList.remove('open');
});

$('#selectAllCopy').addEventListener('click', () => {
  const box = $('#copyTextBox');
  box.focus();
  box.select();
});
$('#closeCopyModal').addEventListener('click', () => $('#copyModal').classList.remove('open'));
$('#copyModal').addEventListener('click', (e) => {
  if (e.target === $('#copyModal')) $('#copyModal').classList.remove('open');
});

/* ==========================================================================
   ★ 收入登记 + 月度统计（第三刀：钱从哪来、该推什么）
   --------------------------------------------------------------------------
   三件事：
     ① 大字 + 时间范围：本月收了 ¥1240，比上月 +18%（**没有上月数据就说"上月没有记录"**，
        绝不显示 0% —— "从无到有"和"没变化"完全是两件事）
     ② 按大类 / 按服务 / 按客户 三个维度的拆分（全部由后端 income_stats() 算好）
     ③ 流水明细表：每一行可删（原生 confirm，和后台其它危险操作保持一致）

   三个记账入口（设计原则：**登记必须发生在他本来就要做的动作里**）：
     · 确认预约 → 后端在 confirm-lock 里自动记一笔（source='booking'）
     · 续费时填了金额 → 后端在 renew 里自动记一笔（source='renew'）
     · 剪辑 / PS / 建站这类一次性单子 → 这里的「+ 记一笔」（source='manual'）

   ⚠️ 前端**不自己算任何数字**：总额、环比、排行、条数全是后端给的。
      自己算一遍迟早会出现"大字和明细对不上"，那时候主人就再也不信这一页了。
   ========================================================================== */

/** "2026-10-13" → "10月13日"（解析不了就原样返回；和客户页的 cnDate 一个套路） */
function shortDate(text) {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(text || ''));
  return m ? `${Number(m[2])}月${Number(m[3])}日` : String(text || '');
}

/**
 * 从客户档案那串**自由文本**里抠出金额数字（只用于续费弹窗的"默认值"）。
 * "100R/月" → 100、"推图15R" → 15、"按需报价" → null。
 * ⚠️ 它只用来**预填**那个输入框，抠不出来就空着 —— 客户档案里的原文一个字都不改
 *    （后端也有一个同样的 parse_amount()，两边的规矩一样：只取第一个数字）。
 */
function payAmountFromText(text) {
  const m = /\d+(?:\.\d+)?/.exec(String(text == null ? '' : text));
  if (!m) return '';
  const n = Number(m[0]);
  return Number.isFinite(n) && n > 0 ? String(n) : '';
}

/** 来源小徽章：确认预约 / 续费 / 手动补记（三种颜色，一眼看出来钱是怎么进来的） */
function paymentSourceBadge(source, label) {
  const cls = { booking: 'src-booking', renew: 'src-renew', manual: 'src-manual' }[source] || 'off';
  return `<span class="badge ${cls}">${escapeHtml(label || '手动补记')}</span>`;
}

/** 收入页的「服务（选填）」下拉：按大类分组列全部**可见**分区，另加一个"（不选）" */
function fillPaymentZonePicker() {
  const sel = $('#payZone');
  if (!sel) return;
  const keep = sel.value;
  const options = state.categories.map((cat) => {
    const members = zonesOfCategory(cat.id).filter((z) => z.visible);
    if (!members.length) return '';
    return `<optgroup label="${escapeHtml(cat.name)}">${members.map((z) =>
      `<option value="${escapeHtml(z.id)}">${escapeHtml(z.name)}（/z/${escapeHtml(z.id)}）</option>`
    ).join('')}</optgroup>`;
  }).join('');
  sel.innerHTML = '<option value="">（不选：没归类 / 剪辑这种一次性单子）</option>' + options;
  sel.value = zoneById(keep) ? keep : '';
}

/** 一键卡片：按大类拆开的收入小卡（陪玩 / 代肝 / 其他服务 / 未分类） */
function renderIncomeCategories() {
  const stats = state.income || {};
  const rows = stats.categories || [];
  $('#incomeCategoryStats').innerHTML = rows.length ? rows.map((c) => `
    <div class="stat income-cat">
      <span>${escapeHtml(c.icon || '🎯')} ${escapeHtml(c.name || '未分类')}</span>
      <strong class="money">${escapeHtml(incomeMoney(c.amount))}</strong>
      <small>${Number(c.count) || 0} 笔</small>
    </div>`).join('')
    : '<div class="stat income-cat"><span>这个范围还没有收入</span><strong>—</strong></div>';
}

/** 排行榜的一行（前 5 名，带名次） */
function rankRows(items, suffix) {
  const rows = items || [];
  if (!rows.length) return '<div class="empty-state">这个范围还没有数据。</div>';
  return rows.map((it, i) => `
    <div class="record rank-row">
      <span class="rank-no">${i + 1}</span>
      <div class="grow">
        <strong>${escapeHtml(it.name)}</strong>
        <p>${Number(it.count) || 0} ${suffix}</p>
      </div>
      <span class="rank-money">${escapeHtml(incomeMoney(it.amount))}</span>
    </div>`).join('');
}

/** "¥480" / "¥480.50"（后端给的 amountText；这里只是兜底，逻辑只有后端一处） */
function incomeMoney(value) {
  const n = Number(value) || 0;
  return Math.abs(n - Math.round(n)) < 0.005 ? `¥${Math.round(n)}` : `¥${n.toFixed(2)}`;
}

/** 一次都没记过时的空状态：把三个记账入口写清楚（这是他唯一需要读的一段话） */
const INCOME_EMPTY = `
    <p style="margin:0 0 8px"><b>还没有收入记录。</b></p>
    <ul class="empty-steps">
      <li>客户自助预约被你点「已确认」→ <b>自动记一笔</b></li>
      <li>客户续费时填了金额 → <b>自动记一笔</b></li>
      <li>剪辑 / PS / 建站这类一次性单子 → 点右上角「+ 记一笔」补上</li>
    </ul>`;

function renderIncomeTable() {
  const stats = state.income || {};
  const rows = stats.records || [];
  const table = $('#incomeTable');
  const stateEl = $('#incomeListState');
  if (!rows.length) {
    // 一次都没记过（或者这个范围里没有）：换上空状态，把**三个记账入口**写清楚 ——
    // 这是他唯一需要读的一段话：钱是怎么自动进来的、哪一步要他动手。
    // 注意：只有"全部加起来一笔都没有"时才写"还没有收入记录"，
    // 别的范围是空的就说"这个范围没有"（免得他以为账全丢了）。
    table.innerHTML = '';
    stateEl.className = 'hint empty-state';
    // ★ 三种空法，说的话不一样（**别一律说"还没有收入记录"**）：
    //   ① 一笔都没有（allCount=0）→ 教他三个记账入口在哪（这是他唯一需要读的一段话）
    //   ② 全部有、但这个范围没有 → 提一句"换个范围"；要是他就在看"本月"，
    //      再补一句"本月的边界按北京时间算" —— 免得他以为账丢了
    //   ③ 范围里没有但也不是本月 → 就只说换个范围
    if (!Number(stats.allCount)) {
      stateEl.innerHTML = INCOME_EMPTY;
    } else if (stats.range === 'month') {
      stateEl.innerHTML = '这个月还没有收入记录。'
        + '（「本月」是按<b>北京时间</b>的月份算的；如果你刚记的那笔是别的月份的，点上面的范围看看。）';
    } else {
      stateEl.innerHTML = `这个范围（${escapeHtml(stats.rangeLabel || '')}）里没有收入记录，换个范围看看。`;
    }
    return;
  }
  table.innerHTML = `
    <thead><tr>
      <th>日期</th><th>服务 / 项目</th><th>客户</th><th>金额</th><th>来源</th><th>操作</th>
    </tr></thead>
    <tbody>${rows.map((p) => `
      <tr>
        <td data-label="日期">${escapeHtml(shortDate(p.date))}</td>
        <td data-label="服务 / 项目">${escapeHtml(p.label || '—')}${
          p.note ? `<p title="${escapeHtml(p.note)}">${escapeHtml(p.note)}</p>` : ''}</td>
        <td data-label="客户">${escapeHtml(p.customer || '——')}</td>
        <td data-label="金额" class="money">${escapeHtml(p.amountText || incomeMoney(p.amount))}</td>
        <td data-label="来源">${paymentSourceBadge(p.source, p.sourceLabel)}</td>
        <td data-label="操作">
          <button class="button small ghost" data-pay-edit="${p.id}">改</button>
          <button class="button small danger" data-pay-del="${p.id}">删</button>
        </td>
      </tr>`).join('')}</tbody>`;

  const total = Number(stats.recordTotal) || 0;
  const limit = Number(stats.listLimit) || 100;
  const bits = [`这个范围共 ${total} 笔`];
  if (total > rows.length) bits.push(`只显示最近 ${limit} 条`);
  stateEl.className = 'hint';
  stateEl.textContent = bits.join(' · ');
}

/** 收入标签页的总渲染（renderAll 和每次接口返回后都调它） */
function renderIncome() {
  if (!$('#incomeTotal')) return;
  const stats = state.income || {};
  $('#incomeRangeLabel').textContent = `${stats.rangeLabel || '本月'}收入`;
  $('#incomeTotal').textContent = incomeMoney(stats.total);
  const mom = $('#incomeMom');
  mom.textContent = stats.momText || '还没有收入记录';
  mom.className = 'income-mom'
    + (stats.momPercent === null || stats.momPercent === undefined ? ''
      : (stats.momPercent >= 0 ? ' up' : ' down'));
  const ranges = state.incomeRanges || [];
  $('#incomeRanges').innerHTML = ranges.map((r) => `
    <button class="filter ${state.payRange === r.id ? 'active' : ''}"
            data-pay-range="${escapeHtml(r.id)}">${escapeHtml(r.name)}</button>`).join('');
  renderIncomeCategories();
  $('#incomeTopServices').innerHTML = rankRows(stats.topServices, '单');
  $('#incomeTopCustomers').innerHTML = rankRows(stats.topCustomers, '笔');
  renderIncomeTable();
  fillPaymentZonePicker();
}

/** 把收入接口的返回写回本地状态（几个接口回的是同一份全量，整块重画就行） */
function applyIncomeResult(data) {
  if (data.income) state.income = data.income;
  if (data.payments) state.payments = data.payments;
  if (data.incomeRanges) state.incomeRanges = data.incomeRanges;
  renderIncome();
}

/** 切换时间范围：重新跟后端要一份（**不在前端算**，口径只有后端一处） */
async function loadIncomeRange(payRange) {
  state.payRange = payRange;
  try {
    const data = await api('/api/admin/payments', {
      method: 'POST', body: JSON.stringify({ action: 'range', range: payRange }),
    });
    applyIncomeResult(data);
  } catch (err) {
    toast(err.message, true);
  }
}

/** 弹窗：编辑已有的一条流水（预填它现在的内容） */
function openPaymentEdit(pay) {
  state.editingPaymentId = pay.id;
  $('#paymentModalTitle').textContent = '改这一笔';
  $('#payDate').value = pay.date || '';
  $('#payAmount').value = pay.amount || '';
  fillPaymentZonePicker();
  $('#payZone').value = zoneById(pay.zone) ? pay.zone : '';
  $('#payItem').value = pay.item || '';
  $('#payCustomer').value = pay.customer || '';
  $('#payNote').value = pay.note || '';
  $('#payFormState').textContent = '';
  $('#payFormState').className = 'hint';
  $('#paymentModal').classList.add('open');
  setTimeout(() => $('#payAmount').focus(), 40);
}

/* ---------- 收入标签页的事件 ---------- */

/** 点「+ 记一笔」：默认日期是**北京时间的今天**（后端给的 state.customerToday，别用本机时间） */
$('#newPayment').addEventListener('click', () => {
  state.editingPaymentId = null;
  $('#paymentModalTitle').textContent = '记一笔收入';
  $('#payDate').value = state.customerToday || '';
  $('#payAmount').value = '';
  $('#payItem').value = '';
  $('#payCustomer').value = '';
  $('#payNote').value = '';
  fillPaymentZonePicker();
  $('#payZone').value = '';
  $('#payFormState').textContent = '';
  $('#payFormState').className = 'hint';
  $('#paymentModal').classList.add('open');
  setTimeout(() => $('#payAmount').focus(), 40);
});

$('#incomeRanges').addEventListener('click', (e) => {
  const hit = e.target.closest('[data-pay-range]');
  if (hit) loadIncomeRange(hit.dataset.payRange);
});

$('#incomeTable').addEventListener('click', async (e) => {
  const edit = e.target.closest('[data-pay-edit]');
  if (edit) {
    const pay = (state.income.records || []).find((p) => p.id === edit.dataset.payEdit)
      || state.payments.find((p) => p.id === edit.dataset.payEdit);
    if (pay) openPaymentEdit(pay);
    return;
  }
  const del = e.target.closest('[data-pay-del]');
  if (!del) return;
  const id = del.dataset.payDel;
  const pay = (state.income.records || []).find((p) => p.id === id);
  if (!confirm(`删掉这一笔收入记录吗？\n\n${pay ? `${pay.date} ${pay.label} ${pay.amountText}` : ''}\n\n`
    + `只删这条流水，预约记录和客户档案都不受影响。`)) return;
  try {
    const data = await api('/api/admin/payments', {
      method: 'POST', body: JSON.stringify({ action: 'delete', id }),
    });
    applyIncomeResult(data);
    toast('这条收入记录已删除');
  } catch (err) {
    toast(err.message, true);
  }
});

/** 保存（新增 or 改）：金额是必填的数字，0 或空都会由后端回一句中文错误 */
async function savePaymentForm() {
  const amount = Number($('#payAmount').value);
  if (!amount || amount <= 0) {
    $('#payFormState').textContent = '金额要填一个大于 0 的数字（例如 160）。';
    $('#payFormState').className = 'hint error';
    return;
  }
  const btn = $('#paySave');
  btn.disabled = true;
  const isUpdate = !!state.editingPaymentId;   // 关弹窗时会清空，所以先记下来
  try {
    const data = await api('/api/admin/payments', {
      method: 'POST',
      body: JSON.stringify({
        action: isUpdate ? 'update' : 'create',
        id: state.editingPaymentId || undefined,
        date: $('#payDate').value,             // 留空 → 后端按今天（北京时间）记
        amount,
        zone: $('#payZone').value,
        item: $('#payItem').value.trim(),
        customer: $('#payCustomer').value.trim(),
        note: $('#payNote').value.trim(),
      }),
    });
    applyIncomeResult(data);
    const saved = data.payment || {};
    $('#paymentModal').classList.remove('open');
    state.editingPaymentId = null;
    // ★ 记的那笔**不在当前范围里**时要明说一句：他刚点了保存，表格里却没有那一行，
    //   不说清楚就会以为"没记上"（比如他在看"本月"，却补记了一笔上个月的）。
    const stats = state.income || {};
    const outside = saved.date && stats.start && stats.end
      && (saved.date < stats.start || saved.date > stats.end);
    toast(isUpdate
      ? (outside ? `这一笔已改到 ${shortDate(saved.date)}（不在当前范围里，换个范围能看到）` : '这一笔已更新')
      : (outside
        ? `已记一笔收入 ${incomeMoney(amount)}（${shortDate(saved.date)}，不在${stats.rangeLabel || '当前'}范围里）`
        : `已记一笔收入 ${incomeMoney(amount)}`));
    if (data.dateTip) toast(data.dateTip, true);   // 日期不合法的兜底提示（说清我的选择）
  } catch (err) {
    $('#payFormState').textContent = err.message;
    $('#payFormState').className = 'hint error';
  } finally {
    btn.disabled = false;
  }
}

$('#paySave').addEventListener('click', savePaymentForm);
$('#payCancel').addEventListener('click', () => {
  $('#paymentModal').classList.remove('open');
  state.editingPaymentId = null;
});
$('#paymentModal').addEventListener('click', (e) => {
  if (e.target === $('#paymentModal')) {
    $('#paymentModal').classList.remove('open');
    state.editingPaymentId = null;
  }
});

/* ==========================================================================
   分区管理
   ========================================================================== */

function zonesOfCategory(catId) {
  return state.zones.filter((z) => z.category === catId);
}

function renderZoneAdmin() {
  const el = $('#zoneAdminList');
  if (!state.categories.length) {
    el.innerHTML = '<div class="empty-state">还没有大类。刷新一下页面，系统会自动建好「陪玩 / 代肝 / 其他服务」。</div>';
    return;
  }
  el.innerHTML = state.categories.map((cat) => {
    const members = zonesOfCategory(cat.id);
    return `<div class="zone-admin-group">
      <div class="zone-admin-head">
        <span class="group-icon">${groupIcon(cat.name)}</span>
        <div class="field" style="margin:0;max-width:180px">
          <input data-cat-name="${cat.id}" value="${escapeHtml(cat.name)}" maxlength="20" title="大类名字">
        </div>
        <div class="field" style="margin:0;max-width:170px">
          <input data-cat-hint="${cat.id}" value="${escapeHtml(cat.hint || '')}" maxlength="20"
                 placeholder="按小时预约" title="跟着大类名字显示的小字（首页和分区页都会用到）">
        </div>
        <select data-cat-kind="${cat.id}" title="这个大类下的分区是预约制还是服务制">
          <option value="booking" ${cat.kind === 'booking' ? 'selected' : ''}>预约制（显示时间表）</option>
          <option value="service" ${cat.kind === 'service' ? 'selected' : ''}>服务制（只显示价目表）</option>
        </select>
        <button class="button small ghost" data-save-cat="${cat.id}">保存大类</button>
        <button class="button small ghost" data-move-cat="${cat.id}" data-dir="-1">↑</button>
        <button class="button small ghost" data-move-cat="${cat.id}" data-dir="1">↓</button>
        <span class="hint">${members.length} 个分区</span>
      </div>
      ${members.map((z, i) => `
        <div class="zone-admin-row ${z.visible ? '' : 'hidden-zone'}">
          ${z.icon
            ? `<img class="zone-icon" src="${escapeHtml(z.icon)}" alt="">`
            : `<span class="zone-icon-empty">${groupIcon(cat.name)}</span>`}
          <div class="grow">
            <strong>${escapeHtml(z.name)}</strong>
            <span class="theme-dot" style="background:${escapeHtml(z.theme || '#c6f04a')}"></span>
            <p>
              /z/${escapeHtml(z.id)} · 简称「${escapeHtml(z.short)}」 ·
              ${KIND_TEXT[z.kind] || '预约制'} ·
              ${z.startPrice ? escapeHtml(z.startPrice) : '还没填价格'} ·
              ${z.visible ? '前台可见' : '已隐藏'}
            </p>
          </div>
          <button class="button small ghost" data-zone-up="${z.id}" data-dir="-1" ${i === 0 ? 'disabled' : ''}>↑</button>
          <button class="button small ghost" data-zone-up="${z.id}" data-dir="1" ${i === members.length - 1 ? 'disabled' : ''}>↓</button>
          <button class="button small ghost" data-zone-vis="${z.id}" data-visible="${z.visible ? 0 : 1}">
            ${z.visible ? '👁 隐藏' : '👁 显示'}</button>
          <button class="button small ghost" data-zone-edit="${z.id}">✏️ 编辑</button>
          <button class="button small danger" data-zone-del="${z.id}">🗑 删除</button>
        </div>`).join('') || '<div class="empty-state">这个大类下还没有分区。</div>'}
      <button class="button ghost small" data-zone-new="${cat.id}" style="margin-top:10px">+ 新增分区</button>
    </div>`;
  }).join('');

  // 首页预览：客户看到的分组和卡片
  const groups = state.categories
    .map((c) => ({ ...c, members: zonesOfCategory(c.id).filter((z) => z.visible) }))
    .filter((g) => g.members.length);
  $('#zonePreview').innerHTML = groups.length ? groups.map((g) => `
    <div style="margin-bottom:12px">
      <b>${groupIcon(g.name)} ${escapeHtml(g.name)}</b>
      <span class="hint">（${escapeHtml(g.hint || (g.kind === 'booking' ? '按小时预约' : '按需报价'))}）</span>
      <div class="tags" style="margin-top:8px">
        ${g.members.map((z) => `<span class="tag">${escapeHtml(z.name)} · ${
          z.startPrice ? escapeHtml(z.startPrice) : '未填价格'}</span>`).join('')}
      </div>
    </div>`).join('')
    : '<div class="empty-state">还没有可见的分区，首页会是空的。先新建一个分区，或者把隐藏的显示出来。</div>';
}

/** 把分区下拉框（资料页 / 展示墙）填好 */
function fillZonePickers() {
  const options = state.categories.map((cat) => {
    const members = zonesOfCategory(cat.id);
    if (!members.length) return '';
    return `<optgroup label="${escapeHtml(cat.name)}">${members.map((z) =>
      `<option value="${escapeHtml(z.id)}">${escapeHtml(z.name)}（/z/${escapeHtml(z.id)}）${
        z.visible ? '' : ' · 已隐藏'}</option>`).join('')}</optgroup>`;
  }).join('');
  const picker = $('#zonePick');
  // 第一项永远是「个人主页」—— 选它就只编辑首页，不会动到任何分区
  picker.innerHTML = '<option value="__home__">🏠 个人主页（首页设置）</option>'
    + (options || '<option value="">（还没有分区）</option>');
  picker.value = state.editHome ? '__home__' : state.currentZoneId;
  const gzone = $('#gZone');
  const keep = state.galleryZoneId;
  gzone.innerHTML = options || '<option value="">（还没有分区）</option>';
  if (zoneById(keep)) gzone.value = keep;
  else if (state.zones.length) { gzone.value = state.zones[0].id; state.galleryZoneId = state.zones[0].id; }

  // 报价页也有一个分区下拉，两个保持同步
  const pricePick = $('#priceZonePick');
  if (pricePick) {
    // ⚠️ 必须先记住用户当前选的那个再重置 innerHTML —— 一重置，value 就掉回第一项了
    const want = zoneById(pricePick.value) ? pricePick.value : state.currentZoneId;
    pricePick.innerHTML = options || '<option value="">（还没有分区）</option>';
    if (zoneById(want)) pricePick.value = want;
    else if (state.zones.length) pricePick.value = state.zones[0].id;
  }

  const link = $('#zoneOpenLink');
  const z = currentZone();
  if (state.editHome) {
    link.href = '/'; link.textContent = '打开个人主页'; link.style.display = '';
  } else if (z) {
    link.href = `/z/${encodeURIComponent(z.id)}`; link.textContent = '打开这个分区页'; link.style.display = '';
  } else {
    link.style.display = 'none';
  }
}

/* ---------- 「资料与报价」：分区部分 ---------- */
function fillZoneForm() {
  const z = currentZone();
  const booking = !z || z.kind === 'booking';
  // 分区类型决定显示哪一块：三档价格 还是 分组价目表
  $('#zonePricesPanel').hidden = !booking;
  $('#zoneGroupsPanel').hidden = booking;
  const kindText = z
    ? `（${KIND_TEXT[z.kind] || '预约制'}：${booking ? '客户可以选时段预约' : '客户直接加微信下单，没有时间表'}）`
    : '';
  $('#zoneKindHint').textContent = kindText;
  const pk = $('#priceKindHint');
  if (pk) pk.textContent = kindText;
  if (!z) {
    $('#zoneState').textContent = '还没有分区，先去「分区管理」新建一个。';
    return;
  }
  const p = z.profile || {};
  $('#zName').value = z.name || '';
  $('#zShort').value = z.short || '';
  $('#zGame').value = p.game || '';
  $('#zPlatform').value = p.platform || '';
  $('#zTagline').value = p.tagline || '';
  $('#zSubheadline').value = p.subheadline || '';
  $('#zHeadline').value = p.headline || '';
  $('#zBio').value = p.bio || '';
  $('#zHighlights').value = (p.highlights || []).join('，');
  $('#zAnnouncement').value = p.announcement || '';
  $('#zPromises').value = (p.promises || []).join('\n');
  $('#zTheme').value = /^#[0-9a-fA-F]{6}$/.test(z.theme || '') ? z.theme : '#c6f04a';
  const dim = Number.isFinite(Number(p.backgroundDim)) ? Number(p.backgroundDim) : 72;
  $('#zBackgroundDim').value = String(dim);
  $('#zDimValue').textContent = String(dim);
  setImagePreview('zIcon', z.icon);
  setImagePreview('zBackground', p.background);
  setImagePreview('zHeroImage', p.heroImage);
  setImagePreview('zShareImage', p.shareImage);
  setImagePreview('zPriceSheetImage', p.priceSheetImage);
  renderPriceEditor();
  renderGroupEditor();
  renderPosPickers();   // ★ 分区这两张图的显示位置（九宫格）
  applyEditMode();      // 最后按"编辑主页 / 编辑分区"切换显示哪一块
}

/** 收集分区表单（不含图片，图片在上传时就写进 state 了） */
function collectZoneForm() {
  const z = currentZone();
  if (!z) return null;
  const p = { ...(z.profile || {}) };
  p.game = $('#zGame').value.trim();
  p.platform = $('#zPlatform').value.trim();
  p.tagline = $('#zTagline').value.trim();
  p.subheadline = $('#zSubheadline').value.trim();
  p.headline = $('#zHeadline').value;
  p.bio = $('#zBio').value;
  p.highlights = $('#zHighlights').value.split(/[，,]/).map((s) => s.trim()).filter(Boolean);
  p.announcement = $('#zAnnouncement').value.trim();
  p.promises = $('#zPromises').value.split('\n').map((s) => s.trim()).filter(Boolean);
  p.backgroundDim = Number($('#zBackgroundDim').value) || 0;
  p.prices = collectPrices();
  p.priceGroups = collectGroups();
  return {
    id: z.id,
    name: $('#zName').value.trim() || z.name,
    short: $('#zShort').value.trim() || z.name,
    theme: $('#zTheme').value,
    icon: z.icon || '',        // ← 图标是分区自己的字段，必须一起交上去（漏了它就会出现"传了图刷新就没了"）
    profile: p,
  };
}

/** 保存分区（基本信息和资料一起存） */
async function saveZone(silent = false) {
  const data = collectZoneForm();
  if (!data) return toast('还没有分区可以保存', true);
  const btn = $('#saveZone');
  if (btn) btn.disabled = true;
  try {
    await api('/api/admin/zones', {
      method: 'POST',
      body: JSON.stringify({
        action: 'update', id: data.id, name: data.name, short: data.short,
        theme: data.theme, icon: data.icon,       // icon 不能漏！否则上传的图标刷新后就没了
      }),
    });
    const saved = await api('/api/admin/zones', {
      method: 'POST',
      body: JSON.stringify({ action: 'profile', id: data.id, profile: data.profile }),
    });
    applyZoneResult(saved);
    renderZoneAdmin();
    fillZonePickers();
    fillZoneForm();
    if (!silent) {
      $('#zoneState').textContent = '已保存，前台立刻生效（客户刷新就能看到）';
      toast('分区已保存');
    }
    return true;
  } catch (err) {
    toast(err.message, true);
    return false;
  } finally {
    if (btn) btn.disabled = false;
  }
}

/** 把后端的返回写回本地状态 */
function applyZoneResult(data) {
  if (data.zones) state.zones = data.zones;
  if (data.categories) state.categories = data.categories;
}

function setImagePreview(key, url) {
  const el = $('#' + key + 'Preview');
  if (el) el.src = url || '';
}

/* ---------- 三档价格编辑器（预约制分区） ---------- */
function renderPriceEditor() {
  const z = currentZone();
  const prices = (z && z.profile && z.profile.prices) || [];
  $('#priceEditor').innerHTML = prices.map((it, i) => `
    <div class="grid-2" style="margin-bottom:10px;align-items:end">
      <div class="field" style="margin:0"><label>名称</label>
        <input data-price="${i}" data-key="name" value="${escapeHtml(it.name)}" maxlength="30"></div>
      <div class="field" style="margin:0"><label>说明</label>
        <input data-price="${i}" data-key="description" value="${escapeHtml(it.description || '')}" maxlength="80"></div>
      <div class="field" style="margin:0"><label>每小时价格（元）</label>
        <input data-price="${i}" data-key="price" type="number" min="0" step="1" value="${Number(it.price) || 0}"></div>
      <div><button class="button small danger" data-del-price="${i}">删除这一档</button></div>
    </div>`).join('') || '<div class="empty-state">还没有报价档位。</div>';
}

function collectPrices() {
  const z = currentZone();
  const prices = (z && z.profile && z.profile.prices) || [];
  return prices.map((it, i) => {
    const name = $(`[data-price="${i}"][data-key="name"]`);
    const desc = $(`[data-price="${i}"][data-key="description"]`);
    const price = $(`[data-price="${i}"][data-key="price"]`);
    return {
      id: it.id || `p${i + 1}`,
      name: name ? name.value.trim() : it.name,
      description: desc ? desc.value.trim() : it.description,
      price: price ? Number(price.value) || 0 : it.price,
    };
  });
}

/* ---------- 分组价目表编辑器（服务制分区） ---------- */
function renderGroupEditor() {
  const z = currentZone();
  const p = (z && z.profile) || {};
  const groups = p.priceGroups || [];
  const featured = p.featured || [];
  $('#groupEditor').innerHTML = groups.map((g, gi) => `
    <div class="group-editor" data-group="${gi}">
      <div class="group-head-row">
        <div class="field" style="min-width:150px;flex:1">
          <label>组名</label>
          <input data-group="${gi}" data-gkey="name" value="${escapeHtml(g.name)}" maxlength="30">
        </div>
        <div class="field" style="min-width:150px;flex:2">
          <label>这一组的备注（选填）</label>
          <input data-group="${gi}" data-gkey="note" value="${escapeHtml(g.note || '')}" maxlength="120">
        </div>
        <button class="button small ghost" data-import-to="${escapeHtml(g.name)}">📋 导入到这里</button>
        <button class="button small ghost" data-group-up="${gi}" data-dir="-1" ${gi === 0 ? 'disabled' : ''}>↑</button>
        <button class="button small ghost" data-group-up="${gi}" data-dir="1" ${gi === groups.length - 1 ? 'disabled' : ''}>↓</button>
        <button class="button small danger" data-del-group="${gi}">删除这一组</button>
      </div>
      ${(g.items || []).map((it, ii) => `
        <div class="item-row">
          <div class="field"><label>名称</label>
            <input data-item="${gi}-${ii}" data-ikey="name" value="${escapeHtml(it.name)}" maxlength="40"></div>
          <div class="field"><label>价格（自由文本）</label>
            <input data-item="${gi}-${ii}" data-ikey="price" value="${escapeHtml(it.price || '')}" maxlength="30" placeholder="20R/月"></div>
          <div class="field"><label>说明</label>
            <input data-item="${gi}-${ii}" data-ikey="note" value="${escapeHtml(it.note || '')}" maxlength="300"></div>
          <div class="item-actions">
            <button class="button small ghost" data-hot="${gi}-${ii}" data-value="${it.hot ? 0 : 1}"
              title="标记为⭐主推">${it.hot ? '⭐' : '☆'}</button>
            <button class="button small ghost" data-feature="${escapeHtml(it.id)}"
              title="放进「主推档位」（最多 3 个）">${featured.includes(it.id) ? '已主推' : '主推'}</button>
            <button class="button small danger" data-del-item="${gi}-${ii}">🗑</button>
          </div>
        </div>
        <!-- ★ 详细说明（选填）：客户点开「这个包含什么 ▾」才看到的那段。
             为什么要这个字段：一行「说明」装不下"多花的钱买到了什么"——
             客户看到三档托管只差一个价格，根本不知道差别在哪。
             多行文本，用 textarea 而不是 input（他要一条一条写清单）。 -->
        <div class="field item-detail-field">
          <label for="idet-${gi}-${ii}">详细说明（选填，客户点开「这个包含什么」才看到）</label>
          <textarea id="idet-${gi}-${ii}" data-item="${gi}-${ii}" data-ikey="detail"
                    maxlength="1500" placeholder="一条一行，例如：&#10;本月活动全刻章&#10;肉鸽每月至少 20 级&#10;计划练度制定">${escapeHtml(it.detail || '')}</textarea>
        </div>`).join('') || '<p class="hint">这一组还没有项目，点下面的「📋 批量粘贴导入」最快。</p>'}
      <button class="button ghost small" data-add-item="${gi}">+ 增加一项</button>
    </div>`).join('') || '<div class="empty-state">还没有价目表。先「+ 增加一组」，再用「📋 批量粘贴导入」把整张价目表贴进来。</div>';

  $('#groupEditor').insertAdjacentHTML('beforeend', `
    <p class="hint" style="margin-top:4px">当前主推档位：${featured.length
      ? featured.map((id) => {
          const hit = groups.flatMap((g) => g.items || []).find((it) => it.id === id);
          return hit ? `<span class="badge hot">${escapeHtml(hit.name)}</span>` : '';
        }).join(' ')
      : '（还没选，首屏会显示前三个标了 ⭐ 的档位）'}</p>`);
}

function collectGroups() {
  const z = currentZone();
  const groups = ((z && z.profile && z.profile.priceGroups) || []).map((g, gi) => {
    const nameEl = $(`[data-group="${gi}"][data-gkey="name"]`);
    const noteEl = $(`[data-group="${gi}"][data-gkey="note"]`);
    const items = (g.items || []).map((it, ii) => {
      const get = (key) => {
        const el = $(`[data-item="${gi}-${ii}"][data-ikey="${key}"]`);
        return el ? el.value : (it[key] || '');
      };
      return {
        id: it.id,
        name: get('name').trim(),
        price: get('price').trim(),     // ★ 自由文本，原样保留（不做数字转换）
        note: get('note').trim(),
        detail: get('detail').trim(),   // ★ 多行详细说明（客户点开「这个包含什么」才看到）
        hot: !!it.hot,
      };
    }).filter((it) => it.name);
    return {
      id: g.id,
      name: nameEl ? nameEl.value.trim() : g.name,
      note: noteEl ? noteEl.value.trim() : g.note,
      items,
    };
  }).filter((g) => g.name);
  return groups;
}

/* ---------- 分区图片上传 ---------- */
const ZONE_IMAGE_FIELDS = {
  zIcon: { key: 'icon', input: '#zIconFile', max: 600, label: '分区图标', withZone: true },
  zBackground: { key: 'background', input: '#zBackgroundFile', max: 1920, label: '分区背景图' },
  zHeroImage: { key: 'heroImage', input: '#zHeroImageFile', max: 1400, label: '首屏主视觉' },
  zShareImage: { key: 'shareImage', input: '#zShareImageFile', max: 1200, label: '微信分享图' },
  zPriceSheetImage: { key: 'priceSheetImage', input: '#zPriceSheetImageFile', max: 1400, label: '价目表长图' },
};

Object.entries(ZONE_IMAGE_FIELDS).forEach(([preview, cfg]) => {
  $(cfg.input).addEventListener('change', async (e) => {
    const file = e.target.files[0];
    if (!file) return;
    const z = currentZone();
    if (!z) return toast('请先在「分区管理」里新建一个分区', true);
    const stateEl = $('#zoneState');
    stateEl.className = 'hint';
    stateEl.textContent = `正在处理${cfg.label}…`;
    try {
      const dataUrl = await compressImage(file, cfg.max);
      const data = await api('/api/admin/media', {
        method: 'POST', body: JSON.stringify({ image: dataUrl }),
      });
      if (cfg.withZone) {
        z.icon = data.url;                       // 图标是分区自己的字段（不在 profile 里）
      } else {
        z.profile = z.profile || {};
        z.profile[cfg.key] = data.url;
      }
      setImagePreview(preview, data.url);
      // ★ 换图之后预览框必须重画一次：首屏主视觉那个框的比例是拿**这张图自己的**
      //   宽高比算的（列宽 ÷ 图片宽高比），换一张比例不同的图，框也得跟着变。
      renderPosPickers();
      stateEl.className = 'hint ok';
      stateEl.textContent = `${cfg.label}已上传，记得点「保存这个分区」`;
      toast(`${cfg.label}已上传`);
    } catch (err) {
      stateEl.className = 'hint error';
      stateEl.textContent = err.message;
      toast(err.message, true);
    } finally {
      e.target.value = '';
    }
  });
});

$$('[data-clear-zone-image]').forEach((btn) => {
  btn.addEventListener('click', () => {
    const key = btn.dataset.clearZoneImage;
    const z = currentZone();
    if (!z) return;
    if (key === 'icon') {
      z.icon = '';
      setImagePreview('zIcon', '');
    } else {
      z.profile = z.profile || {};
      z.profile[key] = '';
      const preview = Object.entries(ZONE_IMAGE_FIELDS).find(([, c]) => c.key === key);
      if (preview) setImagePreview(preview[0], '');
    }
    renderPosPickers();   // 图没了 → 预览框也要重画（换成"先上传一张图"的样子）
    $('#zoneState').className = 'hint';
    $('#zoneState').textContent = '已清除，记得点「保存这个分区」';
  });
});

$('#zBackgroundDim').addEventListener('input', (e) => {
  $('#zDimValue').textContent = e.target.value;
});

$('#pSiteDim').addEventListener('input', (e) => {
  $('#siteDimValue').textContent = e.target.value;
});

/* 「从首屏图自动提取主色」——把分区首屏图发给后端，用 PIL 算主色 */
$('#zPickColor').addEventListener('click', async () => {
  const z = currentZone();
  const url = (z && z.profile && z.profile.heroImage) || '';
  const btn = $('#zPickColor');
  const hint = $('#zColorState');
  if (!url) return toast('先上传一张分区首屏图，再点这个按钮', true);
  btn.disabled = true;
  hint.className = 'hint';
  hint.textContent = '正在算…';
  try {
    const data = await api('/api/admin/extract-color', {
      method: 'POST', body: JSON.stringify({ url }),
    });
    $('#zTheme').value = data.color;
    hint.className = 'hint ok';
    hint.textContent = `取到 ${data.color}（不满意可以直接改）`;
  } catch (err) {
    hint.className = 'hint error';
    hint.textContent = err.message;
  } finally {
    btn.disabled = false;
  }
});

/* ---------- 全局资料（所有分区共用） ---------- */
function fillSiteForm() {
  const p = state.profile || {};
  $('#pBrand').value = p.brand || '';
  $('#pName').value = p.name || '';
  $('#pTagline').value = p.tagline || '';
  $('#pServicesIntro').value = p.services_intro || '';
  $('#pWechat').value = p.wechat || '';
  $('#pXianyu').value = p.xianyu || '';
  $('#pBio').value = p.bio || '';
  $('#pTrust').value = (p.trust || []).join('\n');
  $('#pAccepting').checked = !!p.accepting;
  const dim = Number.isFinite(Number(p.backgroundDim)) ? Number(p.backgroundDim) : 72;
  $('#pSiteDim').value = String(dim);
  $('#siteDimValue').textContent = String(dim);
  setImagePreview('avatar', p.avatar);
  setImagePreview('wechatQr', p.wechatQr);
  setImagePreview('siteBackground', p.background);
  setImagePreview('homeHero', p.heroImage);   // ★ 顺手补上：以前漏了这一行，刷新后首屏图预览是空的
  renderPosPickers();                          // ★ 首页那三张图的显示位置（九宫格）
}

function collectSiteForm() {
  return {
    ...state.profile,
    brand: $('#pBrand').value.trim(),
    name: $('#pName').value.trim(),
    tagline: $('#pTagline').value.trim(),
    services_intro: $('#pServicesIntro').value.trim(),
    wechat: $('#pWechat').value.trim(),
    xianyu: $('#pXianyu').value.trim(),
    bio: $('#pBio').value,
    trust: $('#pTrust').value.split('\n').map((s) => s.trim()).filter(Boolean),
    accepting: $('#pAccepting').checked,
    backgroundDim: Number($('#pSiteDim').value) || 0,
  };
}

/* ---------- 全局资料的图片（头像 / 微信二维码 / 首页背景图） ---------- */
const SITE_IMAGE_FIELDS = {
  avatar: { input: '#avatarFile', max: 800, label: '头像' },
  wechatQr: { input: '#wechatQrFile', max: 800, label: '微信二维码' },
  background: { input: '#siteBackgroundFile', max: 1920, label: '首页背景图' },
  heroImage: { input: '#homeHeroFile', max: 1400, label: '主页首屏主视觉' },
};

Object.entries(SITE_IMAGE_FIELDS).forEach(([key, cfg]) => {
  $(cfg.input).addEventListener('change', async (e) => {
    const file = e.target.files[0];
    if (!file) return;
    const stateEl = $('#uploadState');
    stateEl.className = 'hint';
    stateEl.textContent = `正在处理${cfg.label}…`;
    try {
      const dataUrl = await compressImage(file, cfg.max);
      const data = await api('/api/admin/media', {
        method: 'POST', body: JSON.stringify({ image: dataUrl }),
      });
      state.profile[key] = data.url;
      setImagePreview(key, data.url);
      // ★ 换图之后预览框必须重画一次（首屏主视觉那个框是按图片自己的宽高比算的）
      renderPosPickers();
      stateEl.className = 'hint ok';
      stateEl.textContent = `${cfg.label}已上传，记得点下面的「保存全局资料」`;
      toast(`${cfg.label}已上传`);
    } catch (err) {
      stateEl.className = 'hint error';
      stateEl.textContent = err.message;
      toast(err.message, true);
    } finally {
      e.target.value = '';
    }
  });
});

$$('[data-clear-image]').forEach((btn) => {
  btn.addEventListener('click', () => {
    const key = btn.dataset.clearImage;
    state.profile[key] = '';
    setImagePreview(key, '');
    renderPosPickers();   // 图没了 → 预览框也要重画
    $('#uploadState').className = 'hint';
    $('#uploadState').textContent = '已清除，记得点「保存全局资料」';
  });
});

/* ==========================================================================
   ★ 展示墙（第一刀）
   --------------------------------------------------------------------------
   两条新东西：
     1. **分类可配置**：分类列表来自 state.galleryCategories（后端 store 里的配置），
        不再是写死的"游戏截图 / 老板评价"两个。列表里显示的是分类的中文名，
        认不出来的 id 直接显示 id 本身（老图不会被藏起来）。
     2. **外链**：每条可以带一个 link，前台点图就新窗口打开它（剪辑作品、建站作品）。
        后台列表里用一个 🔗 角标标出来，一眼能看到哪些项有链接。

   ⚠️ 「展开编辑」那一小块（改分类 + 改外链）是 templates/admin.html 里的
      <template id="galleryEditTemplate"> 克隆出来的，所以 geCategory / geLink / geState
      这几个 id 在 admin.html 里能找到（tests/check_frontend.js 就是查这个）。
      为什么用 template 而不是在 JS 里拼字符串：拼出来的那块 HTML 校验器扫不到，
      以后谁打错一个 id 也发现不了 —— 放回模板里，它就能一直帮我们盯着。
   ========================================================================== */

/** 分类 id → 中文名（认不出来就把 id 原样显示，绝不让它"消失"） */
function galleryCategoryName(id) {
  const hit = (state.galleryCategories || []).find((c) => c.id === id);
  return hit ? hit.name : String(id || '未分类');
}

/**
 * 「展开编辑」那一小块：从 admin.html 的 <template id="galleryEditTemplate"> 克隆一份，
 * 把分类下拉和链接填好、再把这一条的 id 写到两个按钮上（点的时候靠它认是哪条）。
 *
 * 用 template 的好处：那块 HTML 一直躺在 admin.html 里，
 * tests/check_frontend.js 能查到它的 id（geCategory / geLink / geState），
 * 以后谁打错 id，一跑校验就报出来。
 */
function galleryEditBoxHtml(g) {
  const tpl = $('#galleryEditTemplate');
  if (!tpl) return '';
  const box = tpl.content.firstElementChild.cloneNode(true);
  const sel = box.querySelector('#geCategory');
  sel.innerHTML = (state.galleryCategories || []).map((c) =>
    `<option value="${escapeHtml(c.id)}"${c.id === g.category ? ' selected' : ''}>${
      escapeHtml(c.name)}</option>`).join('');
  box.querySelector('#geLink').value = g.link || '';
  box.querySelector('[data-save-gal]').dataset.saveGal = g.id;
  box.querySelector('[data-edit-gal]').dataset.editGal = g.id;
  return box.outerHTML;
}

/** 把分类下拉框填好（新增展示那一个），尽量保持当前选中的那个 */
function fillGalleryCategoryPicker() {
  const sel = $('#gCategory');
  if (!sel) return;
  const cats = state.galleryCategories || [];
  const keep = sel.value;
  sel.innerHTML = cats.map((c) =>
    `<option value="${escapeHtml(c.id)}">${escapeHtml(c.name)}</option>`).join('')
    || '<option value="screenshot">游戏截图</option>';
  if (cats.some((c) => c.id === keep)) sel.value = keep;
}

/** 分类管理那一小块（改名 / ↑↓ / 删除，风格和「分区管理」里大类那一行一致） */
function renderGalleryCategoryAdmin() {
  const el = $('#galleryCategoryAdmin');
  if (!el) return;
  const cats = state.galleryCategories || [];
  const counts = state.galleryCategoryCounts || {};
  if (!cats.length) {
    el.innerHTML = '<div class="empty-state">（还没读到分类，刷新一下页面）</div>';
    return;
  }
  el.innerHTML = cats.map((c, i) => {
    const used = Number(counts[c.id] || 0);
    return `<div class="zone-admin-head" style="margin-bottom:8px">
      <div class="field" style="margin:0;max-width:200px">
        <input data-gcat-name="${escapeHtml(c.id)}" value="${escapeHtml(c.name)}" maxlength="20"
               title="分类名字（客户在前台看到的就是它）">
      </div>
      <span class="hint" style="min-width:150px">id：<code>${escapeHtml(c.id)}</code>（不可改）</span>
      <button class="button small ghost" data-save-gcat="${escapeHtml(c.id)}">保存</button>
      <button class="button small ghost" data-move-gcat="${escapeHtml(c.id)}" data-dir="-1"
        ${i === 0 ? 'disabled' : ''}>↑</button>
      <button class="button small ghost" data-move-gcat="${escapeHtml(c.id)}" data-dir="1"
        ${i === cats.length - 1 ? 'disabled' : ''}>↓</button>
      <span class="badge ${used ? '' : 'off'}">${used} 张图</span>
      <button class="button small danger" data-del-gcat="${escapeHtml(c.id)}"
        ${used ? 'title="这个分类下面还有图，系统会拦住不让删"' : ''}>🗑 删除</button>
    </div>`;
  }).join('');
}

function renderGallery() {
  fillGalleryCategoryPicker();
  renderGalleryCategoryAdmin();
  const filtering = !!state.galleryZoneId;
  const items = state.gallery
    .filter((g) => !filtering || g.zone === state.galleryZoneId)
    .filter((g) => state.showArchived || !g.archived);
  const z = zoneById(state.galleryZoneId);
  $('#galleryList').innerHTML = items.length ? items.map((g) => `
    <article class="record">
      <img class="thumb" src="${g.image}" alt="">
      <div class="grow">
        <strong>${escapeHtml(g.title)}</strong>
        <p>${escapeHtml(g.caption || '（无说明）')}</p>
        <p>${escapeHtml(galleryCategoryName(g.category))} · ${
          g.visible ? '已公开' : '已隐藏'}${g.archived ? ' · 已归档' : ''} · 分区：${
          escapeHtml((zoneById(g.zone) || {}).name || '未分区')}${
          g.link ? ` · <span class="badge link">🔗 有外链</span>` : ''}</p>
        ${g.link ? `<p class="hint">点开跳转：${escapeHtml(g.link)}</p>` : ''}
        ${state.editingGalleryId === g.id ? galleryEditBoxHtml(g) : ''}
      </div>
      <button class="button small ghost" data-edit-gal="${g.id}">${
        state.editingGalleryId === g.id ? '收起' : '编辑'}</button>
      <button class="button small ghost" data-vis="${g.id}" data-value="${g.visible ? 0 : 1}">
        ${g.visible ? '隐藏' : '公开'}</button>
      <button class="button small ghost" data-arc="${g.id}" data-value="${g.archived ? 0 : 1}">
        ${g.archived ? '恢复' : '归档'}</button>
      <button class="button small danger" data-del-gal="${g.id}">删除</button>
    </article>`).join('')
    : `<div class="empty-state">${z ? `「${escapeHtml(z.name)}」还没有展示内容，上传第一张吧。` : '还没有展示内容。'}</div>`;
}

/** 浏览器端压缩：把大图缩到指定宽度（默认 1600px）的 WebP，省流量也省存储 */
async function compressImage(file, maxWidth = 1600) {
  if (!file.type.startsWith('image/')) throw new Error('请选择 JPG、PNG 或 WebP 图片。');
  if (file.size > 12 * 1024 * 1024) throw new Error('原图不能超过 12 MB。');
  const bitmap = await createImageBitmap(file);
  const scale = Math.min(1, maxWidth / Math.max(bitmap.width, bitmap.height));
  const w = Math.max(1, Math.round(bitmap.width * scale));
  const h = Math.max(1, Math.round(bitmap.height * scale));
  const canvas = document.createElement('canvas');
  canvas.width = w;
  canvas.height = h;
  canvas.getContext('2d').drawImage(bitmap, 0, 0, w, h);
  bitmap.close && bitmap.close();
  const webp = canvas.toDataURL('image/webp', 0.86);
  return webp.startsWith('data:image/webp') ? webp : canvas.toDataURL('image/jpeg', 0.86);
}

/* ==========================================================================
   事件绑定
   ========================================================================== */

$$('.tab').forEach((tab) => {
  tab.addEventListener('click', () => {
    // ⚠️ 只有"真的对应一个页"的按钮才切页。后台还有其他长得像 .tab 的按钮
    //    （比如「资料 → 分区图片」里的「预览屏宽」那一排），它们没有 data-tab ——
    //    不拦一下的话，点它们会把**所有页藏起来**（屏幕上只剩顶栏和标签栏）。
    //    所以这里改成按 data-tab 精确匹配，而不是"不是当前这个就藏"。
    const panel = $('[data-panel="' + (tab.dataset.tab || '') + '"]');
    if (!panel) return;
    $$('.tab').forEach((t) => t.classList.toggle('active', t === tab));
    $$('[data-panel]').forEach((p) => {
      p.style.display = p === panel ? '' : 'none';
    });
    // ★ 切到「收入」时顺手拉一次数据：点「已确认」/「续费」回来的只有统计，
    //   那条长列表（几千条流水）不该塞进那些响应里，所以在这里补一次。
    if (tab.dataset.tab === 'income') loadIncomeRange(state.payRange);
  });
});

$('#syncBtn').addEventListener('click', async () => {
  await loadAll();
  toast('已同步最新数据');
});

/* --- 时间表格子 --- */
$('#adminGrid').addEventListener('click', (e) => {
  const cell = e.target.closest('.hour-cell');
  if (!cell || cell.disabled) return;
  const start = Number(cell.dataset.start);
  if ($('#multiSelect').checked) {
    if (state.batch.has(start)) state.batch.delete(start);
    else if (state.batch.size >= 72) return toast('一次最多选择 72 小时', true);
    else state.batch.add(start);
    renderAdminGrid();
    return;
  }
  const h = cnParts(start).h;
  $('#lockStart').value = String(h);
  $('#lockEnd').value = String(Math.min(24, h + 1));
  $('#lockFormTitle').textContent = '锁定一个时段（已选好开始时间）';
});

$('#adminDate').addEventListener('change', (e) => {
  const ms = msFromDateValue(e.target.value);
  if (ms == null) return;
  state.date = ms;
  state.batch.clear();
  renderAdminGrid();
  renderLockList();
});

/* --- 锁定表单 --- */
$('#saveLock').addEventListener('click', async () => {
  try {
    const kind = $('#lockKind').value;
    const client = $('#lockClient').value.trim();
    const note = $('#lockNote').value.trim();
    if ($('#multiSelect').checked && state.batch.size) {
      await api('/api/admin/schedule', {
        method: 'POST',
        body: JSON.stringify({ action: 'save-batch', hours: Array.from(state.batch), kind, client, note }),
      });
      toast(`已锁定 ${state.batch.size} 小时`);
    } else {
      const startHour = Number($('#lockStart').value);
      const endHour = Number($('#lockEnd').value);
      const start = state.date + startHour * HOUR;
      const end = state.date + endHour * HOUR;
      if (end <= start) return toast('结束时间必须晚于开始时间', true);
      await api('/api/admin/schedule', {
        method: 'POST',
        body: JSON.stringify({ action: 'save-lock', id: state.editingLockId || undefined, start, end, kind, client, note }),
      });
      toast('时段已保存，公开时间表已更新');
    }
    state.editingLockId = null;
    state.batch.clear();
    $('#lockFormTitle').textContent = '锁定一个时段';
    $('#lockClient').value = '';
    $('#lockNote').value = '';
    await loadSchedule();
    await refreshPublicSlots();
  } catch (err) {
    toast(err.message, true);
  }
});

$('#resetLock').addEventListener('click', resetLockForm);

$('#lockList').addEventListener('click', async (e) => {
  const edit = e.target.closest('[data-edit-lock]');
  if (edit) return editLock(edit.dataset.editLock);
  const toCustomer = e.target.closest('[data-lock-customer]');
  if (toCustomer) return customerFromBooking(toCustomer.dataset.lockCustomer);
  const del = e.target.closest('[data-del-lock]');
  if (!del) return;
  if (!confirm('取消后这段时间会重新开放（客户自助预约的取消后也会变成可预约），确定吗？')) return;
  await api('/api/admin/schedule', {
    method: 'POST', body: JSON.stringify({ action: 'remove-lock', id: del.dataset.delLock }),
  });
  await loadSchedule();
  await refreshPublicSlots();
  toast('已取消，这段时间重新开放');
});

/* --- 休息规则 --- */
$('#saveRule').addEventListener('click', async () => {
  try {
    const days = $$('#ruleDays input:checked').map((i) => Number(i.value));
    if (!days.length) return toast('请至少选择一个星期', true);
    const startHour = Number($('#ruleStart').value);
    const endHour = Number($('#ruleEnd').value);
    if (startHour === endHour) return toast('开始和结束时间不能相同', true);
    const from = msFromDateValue($('#ruleFrom').value) || midnightOf(Date.now());
    const until = msFromDateValue($('#ruleUntil').value);
    await api('/api/admin/schedule', {
      method: 'POST',
      body: JSON.stringify({
        action: 'save-rule', id: state.editingRuleId || undefined, days,
        start_hour: startHour, end_hour: endHour, from_ts: from, until_ts: until,
        note: $('#ruleNote').value.trim(), enabled: true,
      }),
    });
    toast('休息规则已保存');
    resetRuleForm();
    await loadSchedule();
    await refreshPublicSlots();
  } catch (err) {
    toast(err.message, true);
  }
});

$('#resetRule').addEventListener('click', resetRuleForm);

$('#ruleList').addEventListener('click', async (e) => {
  const toggle = e.target.closest('[data-toggle-rule]');
  if (toggle) {
    await api('/api/admin/schedule', {
      method: 'POST',
      body: JSON.stringify({ action: 'toggle-rule', id: toggle.dataset.toggleRule, enabled: toggle.dataset.enabled === '1' }),
    });
    await loadSchedule();
    return;
  }
  const edit = e.target.closest('[data-edit-rule]');
  if (edit) {
    const r = state.rules.find((x) => x.id === edit.dataset.editRule);
    if (!r) return;
    state.editingRuleId = r.id;
    renderRuleDays(r.days);
    $('#ruleStart').value = String(r.start_hour);
    $('#ruleEnd').value = String(Math.max(r.end_hour, 1));
    $('#ruleFrom').value = dateValue(r.from_ts);
    $('#ruleUntil').value = r.until_ts ? dateValue(r.until_ts) : '';
    $('#ruleNote').value = r.note || '';
    toast('已载入规则，改完点保存');
    return;
  }
  const del = e.target.closest('[data-del-rule]');
  if (del) {
    if (!confirm('删除这条休息规则？')) return;
    await api('/api/admin/schedule', {
      method: 'POST', body: JSON.stringify({ action: 'remove-rule', id: del.dataset.delRule }),
    });
    await loadSchedule();
    await refreshPublicSlots();
    toast('规则已删除');
  }
});

/* ==========================================================================
   分区管理的事件
   ========================================================================== */

/** 生成一个"能用的短标识"建议值：优先取分区名的拼音太难，就用用户输入 + 序号 */
function suggestZoneId(name) {
  const ascii = String(name || '').toLowerCase().replace(/[^a-z0-9-]/g, '');
  if (ascii && /^[a-z0-9]/.test(ascii)) return ascii.slice(0, 20);
  let i = 1;
  while (zoneById(`zone-${i}`)) i += 1;
  return `zone-${i}`;
}

async function createZoneFlow(categoryId) {
  const cat = state.categories.find((c) => c.id === categoryId);
  const name = window.prompt(`新建分区 —— 输入分区全称（例如：守望先锋）\n所属大类：${cat ? cat.name : categoryId}`);
  if (!name) return;
  const short = window.prompt('输入简称（顶部标签用，2–4 个字最好）', name.slice(0, 3)) || name.slice(0, 3);
  const suggested = suggestZoneId(short);
  let zoneId = window.prompt(
    `输入短标识（英文小写字母/数字/连字符，会变成网址 /z/xxx）\n不能和已有的重复。`, suggested);
  if (!zoneId) return;
  zoneId = zoneId.trim().toLowerCase();
  if (!/^[a-z0-9][a-z0-9-]{0,30}$/.test(zoneId)) {
    return toast('短标识只能用英文小写字母、数字和连字符，并且以字母或数字开头', true);
  }
  try {
    const data = await api('/api/admin/zones', {
      method: 'POST',
      body: JSON.stringify({ action: 'create', id: zoneId, category: categoryId, name, short }),
    });
    applyZoneResult(data);
    state.currentZoneId = zoneId;
    state.galleryZoneId = zoneId;
    renderAll();
    // 建完直接跳到「资料与报价」，省得主人再点一次
    $$('.tab').forEach((t) => t.classList.toggle('active', t.dataset.tab === 'profile'));
    $$('[data-panel]').forEach((p) => { p.style.display = p.dataset.panel === 'profile' ? '' : 'none'; });
    toast(`分区 ${name} 建好了，先去传图、填价格`);
    $('#zoneState').textContent = '新分区已建立：先传背景图/首屏图，再填价格，最后点「保存这个分区」。';
  } catch (err) {
    toast(err.message, true);
  }
}

$('#zoneAdminList').addEventListener('click', async (e) => {
  const saveCat = e.target.closest('[data-save-cat]');
  const moveCat = e.target.closest('[data-move-cat]');
  const up = e.target.closest('[data-zone-up]');
  const vis = e.target.closest('[data-zone-vis]');
  const edit = e.target.closest('[data-zone-edit]');
  const del = e.target.closest('[data-zone-del]');
  const neu = e.target.closest('[data-zone-new]');
  try {
    if (saveCat) {
      const cid = saveCat.dataset.saveCat;
      const name = $(`[data-cat-name="${cid}"]`).value.trim();
      const hintEl = $(`[data-cat-hint="${cid}"]`);
      const hint = hintEl ? hintEl.value.trim() : '';
      const kind = $(`[data-cat-kind="${cid}"]`).value;
      const data = await api('/api/admin/categories', {
        method: 'POST', body: JSON.stringify({ action: 'save', id: cid, name, hint, kind }),
      });
      applyZoneResult(data);
      renderZoneAdmin();
      fillZonePickers();
      fillZoneForm();
      toast('大类已保存');
      return;
    }
    if (moveCat) {
      const cid = moveCat.dataset.moveCat;
      const dir = Number(moveCat.dataset.dir);
      const ids = state.categories.map((c) => c.id);
      const at = ids.indexOf(cid);
      const to = at + dir;
      if (to < 0 || to >= ids.length) return;
      [ids[at], ids[to]] = [ids[to], ids[at]];
      const data = await api('/api/admin/categories', {
        method: 'POST', body: JSON.stringify({ action: 'sort', order: ids }),
      });
      applyZoneResult(data);
      renderZoneAdmin();
      toast('大类顺序已更新');
      return;
    }
    if (up) {
      // 只在本大类内部上下移动：先算出这一类的新顺序，再整体发给后端
      const z = zoneById(up.dataset.zoneUp);
      const dir = Number(up.dataset.dir);
      const members = zonesOfCategory(z.category).map((x) => x.id);
      const at = members.indexOf(z.id);
      const to = at + dir;
      if (to < 0 || to >= members.length) return;
      [members[at], members[to]] = [members[to], members[at]];
      // 其它分区的相对顺序保持不变
      const others = state.zones.filter((x) => x.category !== z.category).map((x) => x.id);
      const data = await api('/api/admin/zones', {
        method: 'POST', body: JSON.stringify({ action: 'sort', order: members.concat(others) }),
      });
      applyZoneResult(data);
      renderZoneAdmin();
      fillZonePickers();
      toast('顺序已更新');
      return;
    }
    if (vis) {
      const data = await api('/api/admin/zones', {
        method: 'POST',
        body: JSON.stringify({ action: vis.dataset.visible === '1' ? 'show' : 'hide', id: vis.dataset.zoneVis }),
      });
      applyZoneResult(data);
      renderZoneAdmin();
      fillZonePickers();
      toast(vis.dataset.visible === '1' ? '已显示在首页和导航里' : '已隐藏（网址仍然能打开）');
      return;
    }
    if (edit) {
      if (state.currentZoneId !== edit.dataset.zoneEdit) {
        // 换分区之前先把当前编辑的内容存下来，免得白填
        await saveZone(true);
      }
      state.currentZoneId = edit.dataset.zoneEdit;
      state.galleryZoneId = edit.dataset.zoneEdit;
      fillZonePickers();
      fillZoneForm();
      $$('.tab').forEach((t) => t.classList.toggle('active', t.dataset.tab === 'profile'));
      $$('[data-panel]').forEach((p) => { p.style.display = p.dataset.panel === 'profile' ? '' : 'none'; });
      toast('已载入这个分区，改完点「保存这个分区」');
      return;
    }
    if (del) {
      // 第一次：只问后端"会牵连什么"，让主人自己确认（删除分区只隐藏是不够的，要二次确认）
      const first = await fetch('/api/admin/zones', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action: 'delete', id: del.dataset.zoneDel }),
      }).then((r) => r.json());
      if (!first.needConfirm) {
        toast(first.error || '删除失败', true);
        return;
      }
      const s = first.summary;
      if (!confirm(`真的要删除分区「${s.name}」吗？\n\n会删掉：分区页 /z/${s.id} 和它的资料（价格、文案、背景图、首屏图）\n`
        + `不会删：它名下的 ${s.gallery} 张展示墙图片、${s.locks} 条预约记录（这些会变成"未分区"）\n\n`
        + `如果你只是不想让它出现在首页上，点「取消」，改用「👁 隐藏」。\n\n确定删除请点「确定」。`)) return;
      const data = await api('/api/admin/zones', {
        method: 'POST', body: JSON.stringify({ action: 'delete', id: del.dataset.zoneDel, confirm: true }),
      });
      state.zones = data.zones || [];
      state.gallery = data.gallery || state.gallery;
      if (state.currentZoneId === del.dataset.zoneDel) {
        state.currentZoneId = state.zones.length ? state.zones[0].id : '';
      }
      if (state.galleryZoneId === del.dataset.zoneDel) {
        state.galleryZoneId = state.currentZoneId;
      }
      renderAll();
      toast('分区已删除');
      return;
    }
    if (neu) return createZoneFlow(neu.dataset.zoneNew);
  } catch (err) {
    toast(err.message, true);
  }
});

/* ==========================================================================
   ★ 文案 + 服务条款（第四刀：客户看到的每一句话都能在后台改）
   --------------------------------------------------------------------------
   两条设计：
     1. **目录在 app.py 的 TEXT_GROUPS 里**，这里只负责"照着目录画出来"。
        八十多句文案，如果一句写一个 <input>，以后加一句要改 HTML + JS 两处，
        迟早对不上（这个项目已经因为"两处要同步"踩过好几次坑了）。
     2. **只存改过的那几句**：输入框里预填的是"现在生效的那句"，
        保存时后端会把"和默认一字不差"的丢掉 —— 所以主人只是点开看一眼再保存，
        不会把所有默认值都钉死（以后默认值改好了他还能收到）。

   ⚠️ 这一页的行是 JS 生成的，所以用 class + data-* 而不是 id（几十行重复的 id 不合法）。
      真要找某一格，用 [data-text-key="heroIntro"] 这种选择器。
   ========================================================================== */

/**
 * 每一格"能拖的图"：
 *   box    拖框（外层，**监听挂在它身上** —— 里面那张图是 pointer-events: none，
 *          不然浏览器会把 pointerdown 记到图片上，做不出"按住图拖"的手感）
 *   key    位置值存到哪个字段
 *   imgKey 这张图本身存在哪个图片字段（用来把上传的图同步过来）
 *   image  拖框里那张 <img> 的 id（用来设 object-position）
 *   value  下面那行小字（当前值）的 id
 *   zone   true = 存在分区资料里，否则存在全局资料里
 *   hero   true = 首屏主视觉（它的预览比例要用"列宽 × min(高, 430)"那条公式算，
 *          见 previewBoxRatio —— 背景图那几张不用，它们只跟屏宽有关）
 */
const POSITION_PICKERS = [
  { box: 'posDragAvatar', key: 'avatarPosition', imgKey: 'avatar',
    image: 'posImgAvatar', value: 'posValueAvatar' },
  { box: 'posDragHomeBackground', key: 'backgroundPosition', imgKey: 'background',
    image: 'posImgHomeBackground', value: 'posValueHomeBackground' },
  { box: 'posDragHomeHero', key: 'heroImagePosition', imgKey: 'heroImage',
    image: 'posImgHomeHero', value: 'posValueHomeHero', hero: true },
  { box: 'posDragZoneBackground', key: 'backgroundPosition', imgKey: 'background',
    image: 'posImgZoneBackground', value: 'posValueZoneBackground', zone: true },
  { box: 'posDragZoneHero', key: 'heroImagePosition', imgKey: 'heroImage',
    image: 'posImgZoneHero', value: 'posValueZoneHero', zone: true, hero: true },
];

/** 这个拖框该读哪个对象：首页那几张存全局资料，分区那两张存分区资料 */
function positionTarget(cfg) {
  if (cfg.zone) {
    const z = currentZone();
    return z ? (z.profile = z.profile || {}) : null;
  }
  return state.profile || null;
}

/* ---------- 位置值的换算（和后端 app.py 里那套规则一一对应） ----------
   ⚠️ 两代值都要认，而且**不许互相改写**：
     老关键词（"left top"、"center top"…）是主人线上库里现在存着的，
     新百分比（"42% 18%"）是这一版拖出来的。CSS 两样都认，所以两边都原样存、原样发。
     只有"现在该从哪儿开始拖"才需要把老值翻译成比例 —— 就是下面这个函数。 */
const POS_FRACTIONS = {
  'left top': [0, 0], 'center top': [0.5, 0], 'right top': [1, 0],
  'left center': [0, 0.5], center: [0.5, 0.5], 'right center': [1, 0.5],
  'left bottom': [0, 1], 'center bottom': [0.5, 1], 'right bottom': [1, 1],
};
const POS_ALIASES = {
  'center center': 'center', top: 'center top', bottom: 'center bottom',
  left: 'left center', right: 'right center',
};

/** 把位置值归一化：小写、去多余空格、补简写（对应后端的 POSITION_ALIASES） */
function normPos(value) {
  const text = String(value == null ? '' : value).trim().toLowerCase().split(/\s+/).join(' ');
  return POS_ALIASES[text] || text;
}

/** 任何一代位置值 → [x, y]（0..1）。认不出来按"居中"算 */
function positionFraction(value) {
  const text = normPos(value);
  if (POS_FRACTIONS[text]) return POS_FRACTIONS[text].slice();
  const m = /^(-?\d+(?:\.\d+)?)\s*%\s*[,\s]\s*(-?\d+(?:\.\d+)?)\s*%?$/.exec(text);
  if (m) {
    const clamp = (n) => Math.min(1, Math.max(0, n / 100));
    return [clamp(parseFloat(m[1])), clamp(parseFloat(m[2]))];
  }
  return [0.5, 0.5];
}

/** [x, y] → 存进库的字符串 "42% 18%"（取整，和后端 position_to_css 一致） */
function fractionToPosition(fx, fy) {
  return `${Math.round(Math.min(1, Math.max(0, fx)) * 100)}% ` +
         `${Math.round(Math.min(1, Math.max(0, fy)) * 100)}%`;
}

/** 那 9 个标准位置的中文名（只有一个分量是 0/50/100 的也算，方便一眼看懂） */
const POS_NAMES = {
  '0% 0%': '左上', '50% 0%': '上', '100% 0%': '右上',
  '0% 50%': '左', '50% 50%': '居中', '100% 50%': '右',
  '0% 100%': '左下', '50% 100%': '下', '100% 100%': '右下',
};

/**
 * 拖框下面那行小字。分两种说法，因为"锁住的方向"最容易被误会：
 *   · 两个方向都能拖 → 「显示位置：42% 18%」；
 *   · 有一个方向锁住了 → 补一句说清为什么，免得主人以为坏了。
 *     比如 2400×600 的横长图放进 16:9 的框里，上下早就顶满了、根本没东西可挪，
 *     这时候 y 就固定 50%，只让你左右拖。
 */
function posLabelText(css, spec) {
  const name = POS_NAMES[css];
  if (spec.locked) return `显示位置：${css}（这张图不会被裁，不用调）`;
  const free = [];
  if (spec.x[0] !== spec.x[1]) free.push('左右');
  if (spec.y[0] !== spec.y[1]) free.push('上下');
  const tip = free.length === 1 ? `（${free[0]}拖）` : '';
  return `显示位置：${name ? name + ' ' : ''}${css}${tip}`;
}
/**
 * ★ 边界怎么算：**图永远铺满容器，不许露出白边**。
 *
 *   object-fit: cover 会把图放大到两边都盖住容器，所以两个方向各自"溢出"多少是不一样
 *   的（coverScale 就是那个放大倍数）：
 *     · 宽图（溢出在上下）→ 左右已经顶满 → **只能上下拖**，x 锁 50%；
 *     · 高图（溢出在左右）→ 上下已经顶满 → **只能左右拖**，y 锁 50%；
 *     · 图和容器比例正好一样 / 图比容器还小 → 两个方向都没得拖 → 整个锁住。
 *
 *   算出来的比例就是 object-position 的取值范围（0% = 贴左上，100% = 贴右下），
 *   所以拖到头自然就停住，永远露不出白边 —— 这一条和前台是同一个 CSS 行为。
 *
 *   ⚠️ "图比容器还小"（比如 200×200 的小图放进 1500×542 的横幅）在 cover 下其实也会被
 *      放大到盖满，所以那种情况照样能拖 —— 这就是为什么下面判断的是"溢出量"而不是
 *      "图片原始尺寸和容器谁大"。
 */
function posConstraints(cfg, box) {
  const spec = { x: [0.5, 0.5], y: [0.5, 0.5], measurable: false, locked: false };
  const img = cfg.image ? $('#' + cfg.image) : null;
  const bw = box.clientWidth, bh = box.clientHeight;
  const iw = img ? img.naturalWidth : 0, ih = img ? img.naturalHeight : 0;
  if (bw > 0 && bh > 0 && iw > 0 && ih > 0) {
    const coverScale = Math.max(bw / iw, bh / ih);
    const dw = iw * coverScale, dh = ih * coverScale;
    if (dw - bw > 0.5) spec.x = [0, 1];
    if (dh - bh > 0.5) spec.y = [0, 1];
    spec.measurable = true;
    spec.locked = spec.x[0] === spec.x[1] && spec.y[0] === spec.y[1];
  }
  // ⚠️ 量不出来的时候（分区那一块在「个人主页」模式下是 hidden 的，尺寸全是 0；
  //    图片还没加载完也是 0）**绝不能判成 locked** —— 否则切一下模式，好好的
  //    "只能左右拖"就变成了"这张图不用裁，拖不动"，看着像坏了。
  //    这种时候就当成"没有限制"，等真正按下去拖（那时元素一定可见）再量一次。
  return spec;
}

/** 把"能拖的范围"限制住：拖不动的方向直接钉在中间 */
function clampPosToSpec(fx, fy, spec) {
  const fx2 = spec.x[0] === spec.x[1] ? 0.5 : Math.min(spec.x[1], Math.max(spec.x[0], fx));
  const fy2 = spec.y[0] === spec.y[1] ? 0.5 : Math.min(spec.y[1], Math.max(spec.y[0], fy));
  return [fx2, fy2];
}

/**
 * 把每个拖框的上传图同步过去（一个位置一份图 —— 缩略图和拖框本质上是同一张图的两个视图，
 * 指向同一个 url 就不会出现"换图了拖框还是老的"）。
 *
 * ⚠️ onload 的监听**不在这里挂**，见 bindPositionDragging() 开头 —— 这里每渲染一次都会
 *    走一遍，挂在这儿会重复绑；而且"图已经加载完了"时再挂就永远等不到 load 事件了。
 */
function syncDragImages() {
  POSITION_PICKERS.forEach((cfg) => {
    const img = cfg.image ? $('#' + cfg.image) : null;
    if (!img) return;
    const target = positionTarget(cfg);
    const url = (target && target[cfg.imgKey]) || '';
    if (img.getAttribute('src') !== (url || null)) img.src = url || '';
    const box = $('#' + cfg.box);
    if (box) box.classList.toggle('has-image', !!url);
  });
}

/* ==========================================================================
   ★ 预览屏宽（图片显示位置的预览框，按"客户实际会看到的比例"来画）
   --------------------------------------------------------------------------
   要解决的问题：预览框以前**写死**了比例（16/9、498/430），和前台对不上 ——
   主人拖完位置，客户那边看到的是另一种裁法。

   前台真实情况（都在 style.css 里，这里一行都不改）：
     · .zone-hero-bg 撑满整个 .zone-hero
         宽 = 客户屏幕宽度                      ← 变量
         高 = 首屏内容自然高（≥1024 时实测恒为 564；见下面的数字来源）
       所以这个框的真实比例是「屏宽 : 首屏高」，**随屏幕变**。
     · 首屏主视觉 .hero-media img 是 width:100% + max-height:430 + object-fit:cover
         框宽 = 那一栏的列宽                        ← 屏宽算得出来（见 heroColumnWidth）
         框高 = min(列宽 ÷ 图片宽高比, 430)          ← 还跟**这张图自己的**宽高比有关
       所以它必须是按公式算的，钉死一个比例一定不对。

   ★ 数字来源（不是拍的，是量出来的，量法见回报）：
     1024 / 1440 / 1920 / 2560 屏宽下，主人真实分区首页的 .zone-hero 实测都是 564.28px；
     430 / 768 下实测 502.75 / 532.22px（此时首屏图还没上传、首屏只有左边那栏文字）。
     所以 564 / 503 / 532 这三个高度是**实测值**，不是我估的。
   ========================================================================== */

/** 记着"上次选的预览屏宽"。
 *  ⚠️ 必须和文案页那个折叠记忆（peiwan.admin.texts.openGroups）用**不同的 key**，
 *     不然两边会互相覆盖。 */
const PREVIEW_WIDTH_KEY = 'peiwan.admin.previewScreenWidth';

/** 首屏背景图：屏宽 → 首屏高度（实测值）。顺序就是按钮从左到右的顺序。 */
const PREVIEW_WIDTHS = [
  { w: 430, h: 503, label: '手机 430' },
  { w: 768, h: 532, label: '平板 768' },
  { w: 1440, h: 564, label: '笔记本 1440' },
  { w: 1920, h: 564, label: '台式 1920' },
  { w: 2560, h: 564, label: '大屏 2560' },
];
const PREVIEW_WIDTH_DEFAULT = 1920;      // 主人最常用的是台式，默认就停在这一档

/**
 * 「背景图预览」需要知道【同一屏那张首屏主视觉有没有图】。
 *
 * 为什么要知道：≤860px 时前台首屏塌成一栏 ✓ 有首屏图时会变成「图上、文字下」叠起来 ✗
 * 首屏就比只有文字时高一大截 ✓ 背景图那个框的比例也跟着变 ✓
 * 这个映射表把「背景图那个框」和「同屏的首屏主视觉图片元素」对上 ✓
 */
const HERO_IMG_OF = {
  posDragZoneBackground: 'posImgZoneHero',
  posDragHomeBackground: 'posImgHomeHero',
};

/** 这个背景图预览所在的那一屏，有没有传首屏主视觉图 */
function hasHeroImage(cfg) {
  const id = HERO_IMG_OF[cfg.box];
  if (!id) return false;
  const img = $('#' + id);
  return !!(img && img.getAttribute('src'));
}

/**
 * 「任意屏宽 → 首屏高度」。
 *
 * 按钮只有 5 档，但电脑屏宽是连续的（1024 / 1280 / 1600 … 都有人用）。
 * 高度只跟"首屏的布局"有关，实测是**分段恒定**的：
 *   ≤860px  首屏塌成一栏，高度由左边那栏文字决定（430→503、768→532）
 *   >860px  两栏，首屏高度恒为 564（1024 / 1440 / 1920 / 2560 实测都是 564.28）
 * 所以这里按"该屏宽下首屏高度的档位值"取 —— 没有按钮的宽度（比如 1024）也能算对。
 */
function previewHeightFor(screenW, hasHeroImage) {
  const hit = PREVIEW_WIDTHS.find((x) => x.w === screenW);
  let h = hit ? hit.h : (screenW <= 860 ? 503 : 564);   // 落在两档之间就用它所在那一段的高度

  // ★ 2026-10-17 补的一条（主人截图发现 430/768 对不上才查出来的）：
  //   ≤860px 时前台首屏塌成一栏 ✓ 如果这个分区【传了首屏图】✗
  //   首屏就变成「图上、文字下」叠起来 ✗ 比只有文字时高出一截 ✓
  //   实测 delta 分区：没图 430→502.75；有图 430→814.75 ✓
  //   差的正好是 图片最大高 280 + 两栏间距 32 = 312 ✓ 所以这里补上 ✓
  //   （564/503/532 这三个基准值本身是实测出来的，不是估的 ✓）
  if (screenW <= 860 && hasHeroImage) h += 280 + 32;
  return h;
}

/** 前台 .container 的宽度：style.css 写着 width: min(1080px, 100% - 32px) */
const CONTAINER_MAX = 1080;
const CONTAINER_GUTTER = 32;

/**
 * 「屏宽 → 首屏主视觉那一栏的列宽」。
 *
 * 前台 .hero-grid 在 ≥861px 时是两栏：minmax(0, 1.05fr) minmax(0, .95fr)，gap 32px；
 * ≤860px 时塌成一栏（.hero-media 还排到前面去）。
 * 所以列宽 = (min(1080, 屏宽 - 32) - 32) × .95 ÷ 2。
 *
 * ★ 这条公式是**量出来核对过**的：430→378.56 / 768→736（单栏）/ 1024→456 /
 *   1440→497.8 / 1920→497.8，公式和实测最多差 0.15px。
 */
function heroColumnWidth(screenW) {
  const container = Math.min(CONTAINER_MAX, screenW - CONTAINER_GUTTER);
  if (screenW <= 860) return Math.max(0, container);      // 单栏：整行宽就是列宽
  return Math.max(0, (container - 32) * 0.95 / 2);        // 两栏：右边那一栏
}

/** 首屏主视觉那张图现在量得出来的宽高比（图还没加载完就返回 0） */
function heroImageRatio(cfg) {
  const img = cfg && cfg.image ? $('#' + cfg.image) : null;
  if (!img || !img.naturalWidth || !img.naturalHeight) return 0;
  return img.naturalWidth / img.naturalHeight;
}

/** 按当前选中的屏宽，算出每个拖框该用什么比例（返回 '宽 / 高' 字符串） */
function previewBoxRatio(cfg, screenW) {
  const h = previewHeightFor(screenW, hasHeroImage(cfg));
  if (cfg.box === 'posDragAvatar') return '1 / 1';        // 头像永远是 1:1（本来就精确）
  if (cfg.hero) {
    // 首屏主视觉：列宽 × min(列宽 ÷ 图片宽高比, 430)
    //   前台 CSS 是 max-height: 430px（≤860px 的屏幕上是 280px）
    const col = heroColumnWidth(screenW);
    const maxH = screenW <= 860 ? 280 : 430;
    const ratio = heroImageRatio(cfg);
    if (!ratio || col <= 0) {
      // 还没量到图片尺寸（刚设完 src / 图没加载完）→ 先按"列宽 × 430"占位，
      // 图一 load 完就会重算一次（bindPositionDragging 里挂了 load 监听）。
      return col > 0 ? col + ' / ' + maxH : '1 / 1';
    }
    return col + ' / ' + Math.min(col / ratio, maxH);
  }
  // 背景图：框宽 = 客户屏幕宽度，框高 = 那档屏宽下的首屏高度
  return screenW + ' / ' + h;
}

/** 把比例写进那四个拖框（头像不动：它本来就是 1:1，精确的） */
function applyPreviewWidths() {
  POSITION_PICKERS.forEach((cfg) => {
    if (cfg.box === 'posDragAvatar') return;
    const box = $('#' + cfg.box);
    if (box) box.style.aspectRatio = previewBoxRatio(cfg, previewScreenWidth);
  });
}

/** 那一排按钮 + 下面那行小字跟着选中项一起变（两个面板里各有一排，一起刷） */
function renderPreviewWidthUI() {
  $$('[data-preview-width]').forEach((btn) => {
    const on = Number(btn.dataset.previewWidth) === previewScreenWidth;
    btn.classList.toggle('active', on);
    btn.setAttribute('aria-pressed', on ? 'true' : 'false');
  });
  const spec = PREVIEW_WIDTHS.find((x) => x.w === previewScreenWidth) || PREVIEW_WIDTHS[3];
  // ★ 这里**故意不写具体的"首屏高度 XX px"**：同一个屏宽下，传了首屏图的分区和不传的
  //   首屏高度不一样（手机上能差 312px），一行字写死一个数字必然对不上 ✗ 反而害人 ✗
  const text = '现在按【' + spec.label + '】这块屏幕给你看 —— 预览框就是客户在这个宽度的屏幕上'
    + '实际会看到的裁剪。'
    + ' ⚠️ 手机 / 平板那两档，前台首屏会塌成一栏 —— 传了首屏图就会叠成「图上、文字下」，'
    + '高度还会随标题长短变一点，所以那两档是【近似】；电脑那三档是精确的。';
  ['#posScreenWidthHint', '#posScreenWidthHint2'].forEach((sel) => {
    const el = $(sel);
    if (el) el.textContent = text;
  });
}

/** 点按钮：记住选择（下次打开还是这一档）+ 立刻把四个框换成新比例 + 重画 */
function setPreviewScreenWidth(w) {
  if (!PREVIEW_WIDTHS.some((x) => x.w === w)) return;
  previewScreenWidth = w;
  try {
    localStorage.setItem(PREVIEW_WIDTH_KEY, String(w));
  } catch (err) {
    /* 隐私模式下 localStorage 会直接抛错，别让它把整页带崩 */
  }
  applyPreviewWidths();
  renderPreviewWidthUI();
  // ★ 位置值一个字节都不动：这里只是"按新比例重画一遍"（拖完不会被重置）
  renderPosPickers();
}

/** 读回上次的选择；没有 / 认不出来 / 存不下 → 用一个安全的默认值（台式 1920） */
function readPreviewScreenWidth() {
  let raw = '';
  try {
    raw = localStorage.getItem(PREVIEW_WIDTH_KEY) || '';
  } catch (err) {
    raw = '';
  }
  const n = parseInt(raw, 10);
  return PREVIEW_WIDTHS.some((x) => x.w === n) ? n : PREVIEW_WIDTH_DEFAULT;
}

/** 给那两排按钮挂点击（只挂一次，在 renderAll 里跟着 bindPositionDragging 一起） */
function bindPreviewWidthPickers() {
  $$('[data-preview-width]').forEach((btn) => {
    btn.addEventListener('click', () => setPreviewScreenWidth(Number(btn.dataset.previewWidth)));
  });
}

/** 当前选中的预览屏宽（全局，页面刚打开时就从 localStorage 读回来） */
let previewScreenWidth = readPreviewScreenWidth();

/**
 * 画出五个拖框：把图按当前位置摆好、按边界锁住拖不动的方向、更新下面的小字。
 * 顺手把"当前值"那行小字也刷一遍（点「居中」/ 拖动 / 切换分区之后都要重画）。
 *
 * ⚠️ 两个"晚一点再算一次"的补丁，都是因为**边界要靠图片真实尺寸 + 拖框真实尺寸**：
 *     ① 图刚设完 src 时还没解码（naturalWidth = 0）→ 由 load 事件再喊一次（见 bindPositionDragging）；
 *     ② 分区那一块在「个人主页」模式下是 hidden 的，尺寸是 0；切过去时如果图早就缓存好了，
 *        不会再触发 load，所以这里再用 requestAnimationFrame 补一次（那时布局已经完成）。
 */
function renderPosPickers() {
  // ★ 第一件事：按当前预览屏宽把每个框的比例摆好。
  //   放在最前面是为了让"换分区 / 重新上传图 / 图 load 完"之后也一定重算一遍
  //   （图片换了 → 自然宽高比变了 → 首屏主视觉那个框跟着变）。
  applyPreviewWidths();
  renderPreviewWidthUI();
  syncDragImages();
  POSITION_PICKERS.forEach((cfg) => {
    const box = $('#' + cfg.box);
    if (!box) return;
    const target = positionTarget(cfg);
    const raw = (target && target[cfg.key]) || 'center';
    const [fx, fy] = positionFraction(raw);
    const spec = posConstraints(cfg, box);
    const [cx, cy] = clampPosToSpec(fx, fy, spec);
    const css = fractionToPosition(cx, cy);
    // ★ 拖框里的图直接吃 object-position —— 和前台 object-fit: cover 那一套完全一致，
    //   所以"后台看到的"就是"客户看到的"（预览框的比例在 HTML 里按容器比例给定）。
    if (cfg.image) {
      const img = $('#' + cfg.image);
      if (img) img.style.objectPosition = css;
    }
    box.classList.toggle('locked', spec.locked);
    const label = cfg.value ? $('#' + cfg.value) : null;
    if (label) label.textContent = posLabelText(css, spec);
  });
  // ② 上面说的"晚一点再算一次"：切到分区那一半之后，这一帧的布局才算完，
  //    下一帧量到的才是真尺寸。只补一次（requestAnimationFrame 不会自己循环）。
  if (!renderPosPickers._again) {
    renderPosPickers._again = true;
    requestAnimationFrame(() => {
      renderPosPickers._again = false;
      renderPosPickers();
    });
  }
}

/** 拖动只改内存（和图片一个规矩：要点「保存这个分区」/「保存主页设置」才生效） */
function bindPositionDragging() {
  POSITION_PICKERS.forEach((cfg) => {
    const box = $('#' + cfg.box);
    if (!box) return;

    // ★★ 图片加载完必须重画一次 —— 这一条踩过坑，别删：
    //    "能拖哪个方向"是用图片的 naturalWidth/naturalHeight 和拖框尺寸算出来的。
    //    刚设完 src 那一瞬间图还没解码完（naturalWidth = 0），算出来就成了"没得拖"，
    //    结果老老实实存着 "left top" 的图，一打开后台却显示成「居中 · 拖不动」。
    //    尤其是分区那一块：它在「个人主页」模式下是 hidden 的，浏览器会把图的加载
    //    一直拖到区块可见才做，所以 load 事件来得比第一次 renderPosPickers 还晚。
    //    ⚠️ 监听只能挂一次（下面有 dataset 标记），而且必须在设 src 之前就挂好。
    const img = cfg.image ? $('#' + cfg.image) : null;
    if (img && !img.dataset.posOnload) {
      img.dataset.posOnload = '1';
      img.addEventListener('load', () => renderPosPickers());
      img.addEventListener('error', () => renderPosPickers());
    }
    let drag = null;

    box.addEventListener('pointerdown', (e) => {
      const target = positionTarget(cfg);
      if (!target) return toast('还没有分区，先去「分区管理」新建一个', true);
      const dimg = cfg.image ? $('#' + cfg.image) : null;
      if (!dimg || !dimg.getAttribute('src')) return toast('先上传一张图，再拖位置', true);
      // 图还没解码完的话量不出比例（naturalWidth = 0），这时候拖了也算不准，先等一下
      if (!dimg.naturalWidth || !dimg.naturalHeight) return;
      const spec = posConstraints(cfg, box);
      if (spec.locked) return;
      const start = positionFraction(target[cfg.key]);
      // ★★ 把拖动增量换算成 object-position 的变化（这段推导错了就会"拖不动"，别凭感觉改）：
      //
      //   cover 之后，图在**拖框坐标系**里的尺寸是：
      //       drawBoxW = max(框宽, 框高 × 图比例)…… 不用记这个式子，直接算：
      //       scale    = cover 的放大倍数 = drawW / 图原始宽
      //       drawBoxW = drawW / scale        （图在框里画出来有多宽）
      //       ovfBoxW  = drawBoxW - 框宽       （图横向比框多出来的部分）
      //   object-position 的 x% 表示"图往左让出 ovfBoxW 的 x%"，
      //   所以 x 每加 1%，图就在框里往左走 ovfBoxW / 100 像素。
      //   → 鼠标右移 dx 像素、要让图也跟着右移 dx 像素，x% 就得**减去**
      //       dx / ovfBoxW × 100 … 换成 0..1 的比例就是 dx / ovfBoxW。
      //
      //   ⚠️ 我第一版把 ovfBoxW 算成了 drawW - 框宽（那是**图片坐标系**里的溢出，
      //      数值被放大了 scale 倍），结果"宽图"算出来是负数 → 判定成"拖不动"，
      //      屏幕上就是鼠标按住拖、图纹丝不动。
      const scale = Math.max(box.clientWidth / dimg.naturalWidth,
                             box.clientHeight / dimg.naturalHeight);
      const drawBoxW = dimg.naturalWidth * scale;      // 图在框里画出来多宽
      const drawBoxH = dimg.naturalHeight * scale;     // 图在框里画出来多高
      const ovfBoxW = drawBoxW - box.clientWidth;      // 横向比框多出来的部分
      const ovfBoxH = drawBoxH - box.clientHeight;     // 纵向比框多出来的部分
      drag = {
        startX: e.clientX, startY: e.clientY,
        fx: start[0], fy: start[1], spec, ovfBoxW, ovfBoxH,
      };
      if (box.setPointerCapture) { try { box.setPointerCapture(e.pointerId); } catch (err) { /* 无所谓 */ } }
      // 手指按下时别让浏览器去滚页面 / 触发长按菜单（CSS 的 touch-action 是主力，这里是双保险）
      e.preventDefault();
    });

    box.addEventListener('pointermove', (e) => {
      if (!drag) return;
      e.preventDefault();
      let fx = drag.fx, fy = drag.fy;
      if (drag.spec.x[0] !== drag.spec.x[1] && drag.ovfBoxW > 0.5) {
        fx = drag.fx - (e.clientX - drag.startX) / drag.ovfBoxW;
      }
      if (drag.spec.y[0] !== drag.spec.y[1] && drag.ovfBoxH > 0.5) {
        fy = drag.fy - (e.clientY - drag.startY) / drag.ovfBoxH;
      }
      const [cx, cy] = clampPosToSpec(fx, fy, drag.spec);
      const target = positionTarget(cfg);
      if (!target) return;
      target[cfg.key] = fractionToPosition(cx, cy);
      renderPosPickers();
    });

    const finish = (e) => {
      if (!drag) return;
      drag = null;
      if (box.releasePointerCapture && e && e.pointerId != null) {
        try { box.releasePointerCapture(e.pointerId); } catch (err) { /* 无所谓 */ }
      }
      // 说清楚"还没生效"：和上传图片一个规矩，得点保存
      const stateEl = cfg.zone ? $('#zoneState') : $('#uploadState');
      if (stateEl) {
        stateEl.className = 'hint';
        stateEl.textContent = cfg.zone
          ? '显示位置已改，记得点「保存这个分区」'
          : '显示位置已改，记得点「保存主页设置」';
      }
    };
    box.addEventListener('pointerup', finish);
    box.addEventListener('pointercancel', finish);
  });

  // 「居中」按钮：拖歪了一键回中间（= 后端默认值 "center"，和以前一模一样）
  //   找 picker 的方式：先按拖框 id 对（box），不对再按图片 id 找（image）——
  //   两种写法都容得下，以后改 HTML 也不会因为换了个 id 就悄悄失灵。
  $$('[data-pos-center]').forEach((btn) => {
    btn.addEventListener('click', () => {
      const id = btn.dataset.posCenter;
      const cfg = POSITION_PICKERS.find((c) => c.box === id || c.image === id);
      if (!cfg) return;
      const target = positionTarget(cfg);
      if (!target) return toast('还没有分区，先去「分区管理」新建一个', true);
      target[cfg.key] = 'center';
      renderPosPickers();
      const stateEl = cfg.zone ? $('#zoneState') : $('#uploadState');
      if (stateEl) {
        stateEl.className = 'hint';
        stateEl.textContent = cfg.zone
          ? '已回到居中，记得点「保存这个分区」' : '已回到居中，记得点「保存主页设置」';
      }
    });
  });
}


/** 把"目录"和"现在的值"重新拼一遍（保存 / 恢复默认之后要用它重画） */
/** 把"目录"和"现在的值"重新拼一遍（保存 / 恢复默认之后要用它重画） */
function syncTextGroups() {
  const texts = state.texts || {};
  state.textGroups = (state.textGroups || []).map((g) => ({
    ...g,
    fields: g.fields.map((f) => {
      const value = (texts[f.key] === undefined || texts[f.key] === '') ? f.default : texts[f.key];
      return { ...f, value, custom: value !== f.default };
    }),
  }));
}

/** 现在一共有几句是主人自己改的 */
const customizedCount = () => Object.keys(state.textOverrides || {}).length;

/* ==========================================================================
   ★ 「文案」页的折叠 + 搜索（第六刀）
   --------------------------------------------------------------------------
   主人的原话：「改编文案啥的太长了吗，把他们拿去折叠一下」。
   实测这一页 97 个输入框、手机上是 20.6 屏 —— 不折根本翻不到底。

   三条设计：
     1. **每组一个 <details>，默认收起**。用原生 details 而不是自己写 JS 折叠：
        键盘（Tab 过去按回车）、读屏软件、浏览器自带的"页内查找"全都认它，
        和前台的「常见问题」折叠块是同一套做法，主人已经见惯了。
     2. **标题上写清楚"这组几句、改过几句"**（`① 主页 · 10 句（改过 2 句）`）。
        这是折叠之后最值钱的一条信息：不用展开就知道哪组动过。
        数是后端 text_groups_for_admin() 算好一起下发的（前端要是自己数，
        就得先把 97 个输入框全渲染出来，折叠就白折了）。
     3. **展开状态记在 localStorage**：刷新回来还是上次打开的那组。
        ⚠️ 但**搜索优先**：一输入关键词就完全按搜索结果来展开，
        不受"上次打开过哪组"影响，清空搜索框才回到那份记忆。

   搜索匹配的是**说明文字 + 现在生效的那句话**（不区分大小写、去首尾空格）。
   为什么连内容一起搜：主人想找"哪句里提到微信"，光搜标签是搜不到的。
   ========================================================================== */

const TEXTS_OPEN_KEY = 'peiwan.admin.texts.openGroups';   // 记着"哪几组是展开的"

/** 读回展开状态（数组，存的是组 id）。读不出来就当"一组都没展开" */
function readOpenGroups() {
  try {
    const raw = localStorage.getItem(TEXTS_OPEN_KEY);
    const arr = raw ? JSON.parse(raw) : [];
    return Array.isArray(arr) ? arr.map(String) : [];
  } catch (err) {
    return [];      // 隐私模式下 localStorage 会直接抛错，别让它把整页带崩
  }
}

/** 记下展开状态（失败就算了，不值得为它报错） */
function writeOpenGroups(ids) {
  try {
    localStorage.setItem(TEXTS_OPEN_KEY, JSON.stringify(ids));
  } catch (err) { /* 无所谓 */ }
}

/** 搜"现在生效的那句话"时要拿内存里的值（用户可能刚改过还没保存） */
function currentTextValue(key) {
  const el = document.querySelector(`[data-text-key="${key}"]`);
  if (el) return el.value;
  return (state.texts || {})[key] || '';
}

/** 这一组里有没有"标签或内容含这个词"的句子 —— 返回命中的 key 集合 */
function searchHitsInGroup(group, needle) {
  const hits = new Set();
  (group.fields || []).forEach((f) => {
    const hay = `${f.label || ''}\n${currentTextValue(f.key) || ''}`.toLowerCase();
    if (hay.includes(needle)) hits.add(f.key);
  });
  return hits;
}

/**
 * 按搜索框的内容显示 / 隐藏句子与分组。
 *
 * 空搜索框 = 恢复正常折叠视图（按 localStorage 的记忆展开）。
 * 有搜索词 = 只留命中的句子；命中所在的分组自动展开；一句都没命中给个提示。
 */
function applyTextSearch() {
  const box = $('#textGroupList');
  if (!box) return;
  const input = $('#textSearch');
  const needle = String((input && input.value) || '').trim().toLowerCase();
  const detailsList = Array.from(box.querySelectorAll('details.text-group'));
  const remembered = readOpenGroups();

  if (!needle) {
    // ---------- 没搜索：回到"记住的折叠状态" ----------
    detailsList.forEach((d) => {
      d.hidden = false;
      d.open = remembered.indexOf(String(d.dataset.group)) >= 0;
      d.querySelectorAll('.text-row').forEach((row) => { row.hidden = false; });
    });
    const tip = $('#textSearchState');
    if (tip) { tip.textContent = ''; tip.className = 'hint'; }
    const clearBtn = $('#textSearchClear');
    if (clearBtn) clearBtn.hidden = true;
    return;
  }

  // ---------- 搜索中：搜索优先，不理会 localStorage 的记忆 ----------
  let totalHits = 0;
  detailsList.forEach((d) => {
    const group = (state.textGroups || []).find((g) => String(g.id) === d.dataset.group);
    const hits = group ? searchHitsInGroup(group, needle) : new Set();
    let groupHits = 0;
    d.querySelectorAll('.text-row').forEach((row) => {
      const key = row.dataset.textKey;
      const show = hits.has(key);
      row.hidden = !show;
      if (show) groupHits += 1;
    });
    totalHits += groupHits;
    d.hidden = groupHits === 0;      // 一句都没命中的组整个藏起来
    d.open = groupHits > 0;          // 命中的组自动展开
  });

  const tip = $('#textSearchState');
  const clearBtn = $('#textSearchClear');
  if (clearBtn) clearBtn.hidden = false;
  if (tip) {
    if (totalHits) {
      tip.className = 'hint ok';
      tip.textContent = `找到 ${totalHits} 句含「${input.value.trim()}」的句子`;
    } else {
      tip.className = 'hint error';
      tip.textContent = `没找到含「${input.value.trim()}」的句子`;
    }
  }
}

/** 画「文案」页：按目录分组，一组一个可折叠的块，一行一个输入框 */
function renderTexts() {
  const box = $('#textGroupList');
  if (!box) return;
  const groups = state.textGroups || [];
  if (!groups.length) {
    box.innerHTML = '<div class="empty-state">没拿到文案目录，点右上角「刷新数据」试试。</div>';
    return;
  }
  const remembered = readOpenGroups();
  void remembered;    // 展开状态由 applyTextSearch() 统一设（它读同一份记忆）
  box.innerHTML = groups.map((g) => {
    const changed = g.fields.filter((f) => f.custom).length;
    // 标题上那行字：句数 + 改过几句（一个都没改 → 「全是默认」；全改了 → 「全部改过」）
    const total = Number.isFinite(g.count) ? g.count : g.fields.length;
    const summary = !changed ? '全是默认'
      : (changed === total ? '全部改过' : `改过 ${changed} 句`);
    // ⚠️ 这里**故意不写 open 属性** = 默认全部收起（主人的要求）。
    //    哪几组要展开由画完之后调 applyTextSearch() 按记忆 / 搜索词补上。
    return `
    <details class="text-group" data-group="${escapeHtml(g.id)}">
      <summary class="text-group-head">
        <span class="text-group-title">${escapeHtml(g.name)}</span>
        <span class="text-group-count">· ${total} 句（${summary}）</span>
        <span class="text-group-caret" aria-hidden="true">▾</span>
      </summary>
      <div class="text-group-body">
        <div class="text-group-tools">
          <span class="hint grow">${escapeHtml(g.hint || '')}</span>
          <button class="button small ghost" type="button"
                  data-reset-group="${escapeHtml(g.id)}">这一组恢复默认</button>
        </div>
        ${g.fields.map((f) => `
        <div class="text-row" data-text-key="${escapeHtml(f.key)}">
          <label class="text-label" for="tf-${escapeHtml(f.key)}">${escapeHtml(f.label)}
            ${f.custom ? '<span class="badge mini">已改</span>' : ''}</label>
          ${f.multiline
            ? `<textarea id="tf-${escapeHtml(f.key)}" data-text-key="${escapeHtml(f.key)}"
                 maxlength="${f.max}"${f.placeholder ? ` placeholder="${escapeHtml(f.placeholder)}"` : ''}>${escapeHtml(f.value)}</textarea>`
            : `<input id="tf-${escapeHtml(f.key)}" data-text-key="${escapeHtml(f.key)}"
                 maxlength="${f.max}"${f.placeholder ? ` placeholder="${escapeHtml(f.placeholder)}"` : ''} value="${escapeHtml(f.value)}">`}
          ${f.custom ? `<p class="text-default">默认那句话：<b>${escapeHtml(f.default)}</b></p>` : ''}
        </div>`).join('')}
      </div>
    </details>`;
  }).join('');
  // 记住展开状态：每次开/关都写回 localStorage
  box.querySelectorAll('details.text-group').forEach((d) => {
    d.addEventListener('toggle', () => {
      // 搜索的时候不记（那是搜索的结果，不是主人的选择）
      const searchBox = $('#textSearch');
      if (searchBox && String(searchBox.value || '').trim()) return;
      const open = box.querySelectorAll('details.text-group[open]');
      writeOpenGroups(Array.from(open).map((x) => x.dataset.group));
    });
  });
  // 画完立刻按"记忆 + 当前搜索词"摆好展开状态
  applyTextSearch();
  const stateEl = $('#textsState');
  if (stateEl) {
    stateEl.className = 'hint';
    stateEl.textContent = customizedCount()
      ? `现在有 ${customizedCount()} 句是你自己改的，其余都是默认`
      : '现在全部是默认文案（你一句都没改过）';
  }
}

/** 收集「文案」页上所有输入框的值（整页一起发：清空某一格 = 那一句回默认） */
function collectTexts() {
  const out = {};
  $$('[data-text-key]').forEach((el) => { out[el.dataset.textKey] = el.value; });
  return out;
}

/** 保存 / 恢复默认之后：把后端返回的最新状态写回本地并重画 */
function applyTextsResult(data) {
  if (data.texts) state.texts = data.texts;
  if (data.textOverrides) state.textOverrides = data.textOverrides;
  syncTextGroups();
  renderTexts();
}

async function saveTextsForm() {
  const btn = $('#saveTexts');
  const el = $('#textsState');
  btn.disabled = true;
  el.className = 'hint';
  el.textContent = '正在保存…';
  try {
    const data = await api('/api/admin/texts', {
      method: 'POST', body: JSON.stringify({ action: 'save', texts: collectTexts() }),
    });
    applyTextsResult(data);
    $('#textsState').className = 'hint ok';
    $('#textsState').textContent = data.saved
      ? `已保存：你改过 ${data.saved} 句，其余用默认（让客户刷新一下页面就看到了）`
      : '已保存：现在全部是默认文案';
    toast('文案已保存');
  } catch (err) {
    $('#textsState').className = 'hint error';
    $('#textsState').textContent = err.message;
    toast(err.message, true);
  } finally {
    btn.disabled = false;
  }
}

/** 恢复默认：传 keys = 只恢复这一组；不传 = 整页恢复 */
async function resetTextsFlow(keys, label) {
  const what = keys ? `「${label}」这一组的文案` : '整页所有的文案';
  if (!confirm(`把${what}恢复成默认？\n（你自己改过的那几句会被删掉，恢复成出厂那句话）`)) return;
  const el = $('#textsState');
  el.className = 'hint';
  el.textContent = '正在恢复…';
  try {
    const body = { action: 'reset' };
    if (keys) body.keys = keys;
    const data = await api('/api/admin/texts', { method: 'POST', body: JSON.stringify(body) });
    applyTextsResult(data);
    $('#textsState').className = 'hint ok';
    $('#textsState').textContent = '已恢复默认';
    toast('已恢复默认文案');
  } catch (err) {
    el.className = 'hint error';
    el.textContent = err.message;
    toast(err.message, true);
  }
}

/* ---------- 服务条款（/terms 那一页） ---------- */

function renderTerms() {
  const t = state.terms || {};
  const titleEl = $('#termsTitleInput');
  if (!titleEl) return;
  titleEl.value = t.title || '服务条款';
  const meta = $('#termsMeta');
  if (meta) {
    meta.textContent = t.updated
      ? `最后更新：${t.updated}（这是你上次点「保存条款」那天，自动写的）`
      : '还没有保存过，现在给客户看的是默认条款。';
  }
  const secs = t.sections || [];
  const box = $('#termsSections');
  box.innerHTML = secs.map((s, i) => `
    <div class="terms-row" data-index="${i}">
      <div class="field">
        <label for="tsh-${i}">第 ${i + 1} 节 · 小标题</label>
        <input id="tsh-${i}" data-term-heading="${i}" maxlength="40"
               value="${escapeHtml(s.heading || '')}">
      </div>
      <div class="field">
        <label for="tsb-${i}">正文（空一行 = 另起一段；单独换行就是换行）</label>
        <textarea id="tsb-${i}" data-term-body="${i}"
                  maxlength="3000">${escapeHtml(s.body || '')}</textarea>
      </div>
      <div class="row-actions">
        <button class="button small ghost" data-term-up="${i}" ${i === 0 ? 'disabled' : ''}>↑ 上移</button>
        <button class="button small ghost" data-term-down="${i}"
                ${i === secs.length - 1 ? 'disabled' : ''}>↓ 下移</button>
        <button class="button small danger" data-term-del="${i}">🗑 删除这一节</button>
      </div>
    </div>`).join('')
    || '<div class="empty-state">现在一节都没有。点下面「+ 新增一节」加一个（客户只会看到标题）。</div>';
}

/** 把编辑框里现在的内容收回内存 —— ↑↓ / 删除 / 新增之前必须先收，否则刚打的字会丢 */
function collectTermsFromForm() {
  const t = { ...(state.terms || {}) };
  const titleEl = $('#termsTitleInput');
  t.title = titleEl ? titleEl.value.trim() : (t.title || '');
  t.sections = $$('[data-term-heading]').map((el) => {
    const i = el.dataset.termHeading;
    const bodyEl = $(`[data-term-body="${i}"]`);
    return { heading: el.value, body: bodyEl ? bodyEl.value : '' };
  });
  return t;
}

function applyTermsResult(data) {
  if (data.terms) state.terms = data.terms;
  renderTerms();
}

async function saveTermsForm() {
  const form = collectTermsFromForm();
  const el = $('#termsState');
  if (!form.title) {
    el.className = 'hint error';
    el.textContent = '条款标题不能是空的。';
    return toast('条款标题不能是空的', true);
  }
  const btn = $('#saveTerms');
  btn.disabled = true;
  el.className = 'hint';
  el.textContent = '正在保存…';
  try {
    const data = await api('/api/admin/terms', {
      method: 'POST',
      body: JSON.stringify({ action: 'save', title: form.title, sections: form.sections }),
    });
    applyTermsResult(data);
    $('#termsState').className = 'hint ok';
    $('#termsState').textContent = data.dropped
      ? `已保存（有 ${data.dropped} 节是空的，已经忽略）—— 客户刷新 /terms 就看到了`
      : '已保存 —— 客户刷新 /terms 就看到了';
    toast('条款已保存');
  } catch (err) {
    $('#termsState').className = 'hint error';
    $('#termsState').textContent = err.message;
    toast(err.message, true);
  } finally {
    btn.disabled = false;
  }
}

$('#saveTexts').addEventListener('click', saveTextsForm);
$('#resetAllTexts').addEventListener('click', () => resetTextsFlow(null, ''));
// ★ 文案页的搜索框：「输入就过滤」用 input 事件（手机上也是打一个字就动一下）
if ($('#textSearch')) {
  $('#textSearch').addEventListener('input', applyTextSearch);
}
if ($('#textSearchClear')) {
  $('#textSearchClear').addEventListener('click', () => {
    $('#textSearch').value = '';
    applyTextSearch();
    $('#textSearch').focus();
  });
}
$('#textGroupList').addEventListener('click', (e) => {
  const btn = e.target.closest('[data-reset-group]');
  if (!btn) return;
  const group = (state.textGroups || []).find((g) => g.id === btn.dataset.resetGroup);
  if (!group) return;
  resetTextsFlow(group.fields.map((f) => f.key), group.name);
});

$('#termsSections').addEventListener('click', (e) => {
  const up = e.target.closest('[data-term-up]');
  const down = e.target.closest('[data-term-down]');
  const del = e.target.closest('[data-term-del]');
  if (!up && !down && !del) return;
  const form = collectTermsFromForm();
  const secs = form.sections || [];
  if (up || down) {
    const i = Number((up || down).dataset[up ? 'termUp' : 'termDown']);
    const j = up ? i - 1 : i + 1;
    if (j < 0 || j >= secs.length) return;
    const tmp = secs[i];
    secs[i] = secs[j];
    secs[j] = tmp;
  } else {
    const i = Number(del.dataset.termDel);
    if (!confirm(`删除第 ${i + 1} 节？\n（点「保存条款」之后客户那边才会变）`)) return;
    secs.splice(i, 1);
  }
  state.terms = { ...form, sections: secs };
  renderTerms();
});

$('#addTermSection').addEventListener('click', () => {
  const form = collectTermsFromForm();
  form.sections = (form.sections || []).concat([{ heading: '', body: '' }]);
  state.terms = form;
  renderTerms();
  toast('新增了一节，填好小标题和正文再点保存');
});

$('#saveTerms').addEventListener('click', saveTermsForm);
$('#resetTerms').addEventListener('click', async () => {
  if (!confirm('恢复默认条款？\n（你改过的标题和小节都会回到出厂那份）')) return;
  const el = $('#termsState');
  el.className = 'hint';
  el.textContent = '正在恢复…';
  try {
    const data = await api('/api/admin/terms', {
      method: 'POST', body: JSON.stringify({ action: 'reset' }),
    });
    applyTermsResult(data);
    $('#termsState').className = 'hint ok';
    $('#termsState').textContent = '已恢复默认条款';
    toast('已恢复默认条款');
  } catch (err) {
    el.className = 'hint error';
    el.textContent = err.message;
    toast(err.message, true);
  }
});

/* ==========================================================================
   ★ 常见问题 + 服务流程（后台「文案」页里的两块）
   --------------------------------------------------------------------------
   这两块的交互和「服务条款」**一模一样**（增 / 删 / ↑↓ 排序），所以我照它写了一份：
     · collectXxxFromForm()  把编辑框里现在的内容收回内存
     · renderXxx()           按内存重画（↑↓ / 删除 / 新增之前必须先 collect，否则刚打的字会丢）
     · save / reset          两个按钮各一个接口（/api/admin/faq、/api/admin/steps）
   为什么要在前端也维护一份 state.faq / state.steps：
     ↑↓ 和删除都是"先改内存、再重画"，没有它就得每次都发一趟接口（慢，而且中途失败很难看）。
   ========================================================================== */

/** 画「常见问题」：一个标题 + 一条条（问题 / 答案），右边 ↑↓ 和删除 */
function renderFaq() {
  const f = state.faq || {};
  const t = $('#faqTitleInput');
  if (!t) return;
  t.value = f.title || '常见问题';
  const sub = $('#faqSubtitleInput');
  if (sub) sub.value = f.subtitle || '';
  const items = f.items || [];
  const box = $('#faqItems');
  box.innerHTML = items.map((it, i) => `
    <div class="terms-row" data-index="${i}">
      <div class="field">
        <label for="fq-${i}">第 ${i + 1} 条 · 问题</label>
        <input id="fq-${i}" data-faq-q="${i}" maxlength="80" value="${escapeHtml(it.q || '')}"
               placeholder="例如：账号安全吗？你会动我的东西吗？">
      </div>
      <div class="field">
        <label for="fa-${i}">答案（空一行 = 另起一段；单独换行就是换行）</label>
        <textarea id="fa-${i}" data-faq-a="${i}" maxlength="1500"
                  placeholder="用你自己的口气说 —— 客户看得出来哪句是真话">${escapeHtml(it.a || '')}</textarea>
      </div>
      <div class="row-actions">
        <button class="button small ghost" data-faq-up="${i}" ${i === 0 ? 'disabled' : ''}>↑ 上移</button>
        <button class="button small ghost" data-faq-down="${i}"
                ${i === items.length - 1 ? 'disabled' : ''}>↓ 下移</button>
        <button class="button small danger" data-faq-del="${i}">🗑 删除这一条</button>
      </div>
    </div>`).join('')
    || '<div class="empty-state">现在一条都没有。点下面「+ 新增一条」，或者点「恢复默认 8 条」。</div>';
  const stateEl = $('#faqState');
  if (stateEl && !stateEl.textContent) {
    stateEl.textContent = `现在有 ${items.length} 条（最多 ${30} 条）`;
  }
}

/** 把「常见问题」编辑框里现在的内容收回内存 */
function collectFaqFromForm() {
  const f = { ...(state.faq || {}) };
  const t = $('#faqTitleInput');
  const sub = $('#faqSubtitleInput');
  f.title = t ? t.value.trim() : (f.title || '');
  f.subtitle = sub ? sub.value.trim() : (f.subtitle || '');
  f.items = $$('[data-faq-q]').map((el) => {
    const i = el.dataset.faqQ;
    const a = $(`[data-faq-a="${i}"]`);
    return { q: el.value, a: a ? a.value : '' };
  });
  return f;
}

function applyFaqResult(data) {
  if (data.faq) state.faq = data.faq;
  renderFaq();
}

async function saveFaqForm() {
  const form = collectFaqFromForm();
  const el = $('#faqState');
  if (!form.title) {
    el.className = 'hint error';
    el.textContent = '常见问题的小标题不能是空的。';
    return toast('小标题不能是空的', true);
  }
  const btn = $('#saveFaq');
  btn.disabled = true;
  el.className = 'hint';
  el.textContent = '正在保存…';
  try {
    const data = await api('/api/admin/faq', {
      method: 'POST',
      body: JSON.stringify({ action: 'save', title: form.title,
                             subtitle: form.subtitle, items: form.items }),
    });
    applyFaqResult(data);
    $('#faqState').className = 'hint ok';
    $('#faqState').textContent = data.dropped
      ? `已保存（有 ${data.dropped} 条是空的，已经忽略）—— 客户刷新页面就看到了`
      : '已保存 —— 客户刷新页面就看到了';
    toast('常见问题已保存');
  } catch (err) {
    $('#faqState').className = 'hint error';
    $('#faqState').textContent = err.message;
    toast(err.message, true);
  } finally {
    btn.disabled = false;
  }
}

/** 画「服务流程」：一个标题 + 2~6 步 */
function renderSteps() {
  const s = state.steps || {};
  const t = $('#stepsTitleInput');
  if (!t) return;
  t.value = s.title || '怎么开始';
  const items = s.items || [];
  $('#stepsItems').innerHTML = items.map((it, i) => `
    <div class="terms-row" data-index="${i}">
      <div class="field">
        <label for="st-${i}">第 ${i + 1} 步 · 一句话</label>
        <input id="st-${i}" data-step-title="${i}" maxlength="30"
               value="${escapeHtml(it.title || '')}" placeholder="例如：挑时间 / 说需求">
      </div>
      <div class="field">
        <label for="sd-${i}">这一步做什么（客户照着做就知道下一步）</label>
        <textarea id="sd-${i}" data-step-desc="${i}" maxlength="200"
                  style="min-height:70px">${escapeHtml(it.desc || '')}</textarea>
      </div>
      <div class="row-actions">
        <button class="button small ghost" data-step-up="${i}" ${i === 0 ? 'disabled' : ''}>↑ 上移</button>
        <button class="button small ghost" data-step-down="${i}"
                ${i === items.length - 1 ? 'disabled' : ''}>↓ 下移</button>
        <button class="button small danger" data-step-del="${i}">🗑 删除这一步</button>
      </div>
    </div>`).join('')
    || '<div class="empty-state">现在一步都没有。点下面「+ 新增一步」，或者点「恢复默认 4 步」。</div>';
  const stateEl = $('#stepsState');
  if (stateEl && !stateEl.textContent) {
    stateEl.textContent = `现在有 ${items.length} 步（要 2~6 步）`;
  }
}

function collectStepsFromForm() {
  const s = { ...(state.steps || {}) };
  const t = $('#stepsTitleInput');
  s.title = t ? t.value.trim() : (s.title || '');
  s.items = $$('[data-step-title]').map((el) => {
    const i = el.dataset.stepTitle;
    const d = $(`[data-step-desc="${i}"]`);
    return { title: el.value, desc: d ? d.value : '' };
  });
  return s;
}

function applyStepsResult(data) {
  if (data.steps) state.steps = data.steps;
  renderSteps();
}

async function saveStepsForm() {
  const form = collectStepsFromForm();
  const el = $('#stepsState');
  if (!form.title) {
    el.className = 'hint error';
    el.textContent = '服务流程的小标题不能是空的。';
    return toast('小标题不能是空的', true);
  }
  const btn = $('#saveSteps');
  btn.disabled = true;
  el.className = 'hint';
  el.textContent = '正在保存…';
  try {
    const data = await api('/api/admin/steps', {
      method: 'POST',
      body: JSON.stringify({ action: 'save', title: form.title, items: form.items }),
    });
    applyStepsResult(data);
    $('#stepsState').className = 'hint ok';
    $('#stepsState').textContent = data.tip
      ? `已保存 —— ${data.tip}`
      : `已保存 ${(data.steps || {}).items ? data.steps.items.length : 0} 步 —— 客户刷新页面就看到了`;
    toast('服务流程已保存');
  } catch (err) {
    $('#stepsState').className = 'hint error';
    $('#stepsState').textContent = err.message;
    toast(err.message, true);
  } finally {
    btn.disabled = false;
  }
}

$('#saveFaq').addEventListener('click', saveFaqForm);
$('#faqItems').addEventListener('click', (e) => {
  const up = e.target.closest('[data-faq-up]');
  const down = e.target.closest('[data-faq-down]');
  const del = e.target.closest('[data-faq-del]');
  if (!up && !down && !del) return;
  const form = collectFaqFromForm();
  const items = form.items || [];
  if (up || down) {
    const i = Number((up || down).dataset[up ? 'faqUp' : 'faqDown']);
    const j = up ? i - 1 : i + 1;
    if (j < 0 || j >= items.length) return;
    const tmp = items[i];
    items[i] = items[j];
    items[j] = tmp;
  } else {
    const i = Number(del.dataset.faqDel);
    if (!confirm(`删除第 ${i + 1} 条？\n（点「保存常见问题」之后客户那边才会变）`)) return;
    items.splice(i, 1);
  }
  state.faq = { ...form, items };
  renderFaq();
});
$('#addFaqItem').addEventListener('click', () => {
  const form = collectFaqFromForm();
  form.items = (form.items || []).concat([{ q: '', a: '' }]);
  state.faq = form;
  renderFaq();
  toast('新增了一条，填好问题和答案再点保存');
});
$('#resetFaq').addEventListener('click', async () => {
  if (!confirm('恢复默认的 8 条常见问题？\n（你自己写的那些会被换成出厂那份）')) return;
  const el = $('#faqState');
  el.className = 'hint';
  el.textContent = '正在恢复…';
  try {
    const data = await api('/api/admin/faq', { method: 'POST', body: JSON.stringify({ action: 'reset' }) });
    applyFaqResult(data);
    $('#faqState').className = 'hint ok';
    $('#faqState').textContent = '已恢复默认 8 条';
    toast('已恢复默认常见问题');
  } catch (err) {
    el.className = 'hint error';
    el.textContent = err.message;
    toast(err.message, true);
  }
});

$('#saveSteps').addEventListener('click', saveStepsForm);
$('#stepsItems').addEventListener('click', (e) => {
  const up = e.target.closest('[data-step-up]');
  const down = e.target.closest('[data-step-down]');
  const del = e.target.closest('[data-step-del]');
  if (!up && !down && !del) return;
  const form = collectStepsFromForm();
  const items = form.items || [];
  if (up || down) {
    const i = Number((up || down).dataset[up ? 'stepUp' : 'stepDown']);
    const j = up ? i - 1 : i + 1;
    if (j < 0 || j >= items.length) return;
    const tmp = items[i];
    items[i] = items[j];
    items[j] = tmp;
  } else {
    const i = Number(del.dataset.stepDel);
    if (!confirm(`删除第 ${i + 1} 步？\n（点「保存服务流程」之后客户那边才会变）`)) return;
    items.splice(i, 1);
  }
  state.steps = { ...form, items };
  renderSteps();
});
$('#addStepItem').addEventListener('click', () => {
  const form = collectStepsFromForm();
  if ((form.items || []).length >= 6) return toast('最多 6 步 —— 再多客户就不看了', true);
  form.items = (form.items || []).concat([{ title: '', desc: '' }]);
  state.steps = form;
  renderSteps();
  toast('新增了一步，填好再点保存');
});
$('#resetSteps').addEventListener('click', async () => {
  if (!confirm('恢复默认的 4 步流程？\n（你自己写的那几步会被换成出厂那份）')) return;
  const el = $('#stepsState');
  el.className = 'hint';
  el.textContent = '正在恢复…';
  try {
    const data = await api('/api/admin/steps', { method: 'POST', body: JSON.stringify({ action: 'reset' }) });
    applyStepsResult(data);
    $('#stepsState').className = 'hint ok';
    $('#stepsState').textContent = '已恢复默认 4 步';
    toast('已恢复默认服务流程');
  } catch (err) {
    el.className = 'hint error';
    el.textContent = err.message;
    toast(err.message, true);
  }
});

/* ==========================================================================
   「资料与报价」的事件
   ========================================================================== */

$('#zonePick').addEventListener('change', async (e) => {
  const next = e.target.value;

  // 切到「个人主页」：只显示首页那一套表单，分区表单整体隐藏
  if (next === '__home__') {
    if (!state.editHome) await saveZone(true);   // 离开分区前先存，免得白填
    state.editHome = true;
    fillZoneForm();
    fillZonePickers();
    $('#zoneState').textContent = '';
    return;
  }

  if (!zoneById(next)) return;
  if (!state.editHome && next !== state.currentZoneId) await saveZone(true);
  state.editHome = false;
  state.currentZoneId = next;
  state.galleryZoneId = next;
  fillZoneForm();
  fillZonePickers();
  renderGallery();
  $('#zoneState').textContent = '';
});

/** 按「正在编辑主页 / 正在编辑某个分区」切换两大块表单 */
function applyEditMode() {
  const home = !!state.editHome;
  const zb = $('#zoneEditBlock');
  const hb = $('#homeEditBlock');
  if (zb) zb.hidden = home;
  if (hb) hb.hidden = !home;

  // 顶部标题也跟着变，免得在主页模式下还写着"分区"
  const title = $('#editTargetTitle');
  const z = currentZone();
  if (title) {
    title.textContent = home ? '正在编辑：个人主页'
      : `正在编辑分区：${z ? z.name : '（还没有分区）'}`;
  }
  const kindHint = $('#zoneKindHint');
  if (kindHint && home) kindHint.textContent = '（首页，不属于任何分区）';

  // ★ 切换"主页 / 分区"会隐藏另一半表单，被隐藏的那几个拖框就**量不到尺寸**了
  //   （clientWidth = 0）。这里重画一次，让"能不能拖、锁哪个方向"跟着可见的那一半更新，
  //   否则切过去之后小字上会留着一句过时的"拖不动"。
  renderPosPickers();
}

$('#saveZone').addEventListener('click', () => saveZone(false));

/* ---------- 「报价」标签页 ---------- */
if ($('#priceZonePick')) {
  $('#priceZonePick').addEventListener('change', async (e) => {
    const next = e.target.value;
    if (!zoneById(next)) return;
    if (next !== state.currentZoneId) await saveZone(true);   // 换之前先存
    state.editHome = false;
    state.currentZoneId = next;
    state.galleryZoneId = next;
    fillZoneForm();
    fillZonePickers();
    renderGallery();
    $('#zoneState').textContent = '';
  });
}
if ($('#savePrices')) {
  $('#savePrices').addEventListener('click', () => {
    const el = $('#priceState');
    if (el) { el.className = 'hint'; el.textContent = '正在保存…'; }
    saveZone(false).then((ok) => {
      if (el) {
        el.className = ok ? 'hint ok' : 'hint error';
        el.textContent = ok ? '已保存，前台立刻生效' : '保存失败，看上面的提示';
      }
    });
  });
}

$('#priceEditor').addEventListener('click', (e) => {
  const del = e.target.closest('[data-del-price]');
  if (!del) return;
  const z = currentZone();
  if (!z) return;
  z.profile.prices = collectPrices();
  z.profile.prices.splice(Number(del.dataset.delPrice), 1);
  renderPriceEditor();
});

$('#priceEditor').addEventListener('input', () => {
  const z = currentZone();
  if (z) z.profile.prices = collectPrices();   // 先把当前输入存进内存，重绘才不会丢
});

$('#addPrice').addEventListener('click', () => {
  const z = currentZone();
  if (!z) return;
  z.profile.prices = collectPrices();
  z.profile.prices.push({ id: `p${Date.now()}`, name: '新档位', description: '', price: 20 });
  renderPriceEditor();
});

/* --- 分组价目表 --- */
$('#groupEditor').addEventListener('input', () => {
  const z = currentZone();
  if (z) z.profile.priceGroups = collectGroups();
});

$('#groupEditor').addEventListener('click', (e) => {
  const z = currentZone();
  if (!z) return;
  const groups = collectGroups();
  const delGroup = e.target.closest('[data-del-group]');
  const delItem = e.target.closest('[data-del-item]');
  const addItem = e.target.closest('[data-add-item]');
  const hot = e.target.closest('[data-hot]');
  const feature = e.target.closest('[data-feature]');
  const groupUp = e.target.closest('[data-group-up]');
  const importTo = e.target.closest('[data-import-to]');

  if (delGroup) {
    if (!confirm('删除这一整组价目？')) return;
    groups.splice(Number(delGroup.dataset.delGroup), 1);
  } else if (delItem) {
    const [gi, ii] = delItem.dataset.delItem.split('-').map(Number);
    groups[gi].items.splice(ii, 1);
  } else if (addItem) {
    const gi = Number(addItem.dataset.addItem);
    groups[gi].items.push({ id: `i${Date.now()}`, name: '新项目', price: '', note: '',
                            detail: '', hot: false });
  } else if (hot) {
    const [gi, ii] = hot.dataset.hot.split('-').map(Number);
    groups[gi].items[ii].hot = hot.dataset.value === '1';
  } else if (feature) {
    const id = feature.dataset.feature;
    const list = (z.profile.featured || []).slice();
    const at = list.indexOf(id);
    if (at >= 0) list.splice(at, 1);
    else {
      if (list.length >= 3) return toast('主推档位最多 3 个，先取消一个再选', true);
      list.push(id);
    }
    z.profile.featured = list;
  } else if (groupUp) {
    const gi = Number(groupUp.dataset.groupUp);
    const dir = Number(groupUp.dataset.dir);
    const to = gi + dir;
    if (to < 0 || to >= groups.length) return;
    [groups[gi], groups[to]] = [groups[to], groups[gi]];
  } else if (importTo) {
    $('#importGroup').value = importTo.dataset.importTo;
    $('#importBox').hidden = false;
    $('#importText').focus();
    return;
  } else {
    return;
  }
  z.profile.priceGroups = groups;
  renderGroupEditor();
});

$('#addGroup').addEventListener('click', () => {
  const z = currentZone();
  if (!z) return;
  z.profile.priceGroups = collectGroups();
  z.profile.priceGroups.push({ id: `g${Date.now()}`, name: '新分组', note: '', items: [] });
  renderGroupEditor();
});

$('#toggleImport').addEventListener('click', () => {
  const box = $('#importBox');
  box.hidden = !box.hidden;
  if (!box.hidden) {
    if (!$('#importGroup').value) {
      const groups = (currentZone() && currentZone().profile.priceGroups) || [];
      $('#importGroup').value = groups.length ? groups[0].name : '';
    }
    $('#importText').focus();
  }
});

$('#runImport').addEventListener('click', async () => {
  const z = currentZone();
  if (!z) return toast('先选一个分区', true);
  const group = $('#importGroup').value.trim();
  const text = $('#importText').value;
  if (!group) return toast('请先填写导入到哪一组', true);
  if (!text.trim()) return toast('请先把价目表粘贴进来', true);
  const btn = $('#runImport');
  btn.disabled = true;
  try {
    // 先把界面上还没保存的改动收进内存，免得导入后被覆盖
    z.profile.priceGroups = collectGroups();
    const data = await api('/api/admin/import-prices', {
      method: 'POST', body: JSON.stringify({ zone: z.id, group, text }),
    });
    z.profile = data.profile;
    renderGroupEditor();
    const errs = data.errors || [];
    $('#importState').className = errs.length ? 'hint error' : 'hint ok';
    $('#importState').textContent = `成功导入 ${data.imported} 项到「${data.group}」`
      + (errs.length ? `；${errs.length} 行有问题：`
          + errs.slice(0, 3).map((x) => `第 ${x.line} 行 ${x.reason}`).join('；') : '')
      + '（记得点「保存这个分区」）';
    toast(`导入了 ${data.imported} 项`);
    if (!errs.length) $('#importText').value = '';
  } catch (err) {
    $('#importState').className = 'hint error';
    $('#importState').textContent = err.message;
    toast(err.message, true);
  } finally {
    btn.disabled = false;
  }
});

/* --- 全局资料 --- */
$('#saveProfile').addEventListener('click', async () => {
  const btn = $('#saveProfile');
  btn.disabled = true;
  try {
    const data = await api('/api/admin/profile', {
      method: 'PUT',
      body: JSON.stringify({ profile: collectSiteForm(), revision: state.revision }),
    });
    state.profile = data.profile;
    state.revision = data.profileRevision;
    fillSiteForm();
    renderStats();
    $('#profileState').textContent = `已保存（版本 ${state.revision}）`;
    toast('全局资料已保存');
  } catch (err) {
    if (err.message.includes('别处更新')) await loadAll();
    toast(err.message, true);
  } finally {
    btn.disabled = false;
  }
});

/* --- 展示墙 --- */
$('#showArchived').addEventListener('change', (e) => {
  state.showArchived = e.target.checked;
  renderGallery();
});

$('#gZone').addEventListener('change', (e) => {
  state.galleryZoneId = e.target.value;
  renderGallery();
});

/* --- ★ 今日待办条：点哪一项就跳到哪个标签页 --- */
$('#todoBar').addEventListener('click', (e) => {
  const btn = e.target.closest('[data-todo-tab]');
  if (!btn) return;
  gotoTab(btn.dataset.todoTab, btn.dataset.todoFocus);
});

/* --- ★ 待确认处理台：已确认 / 释放时段 --- */
$('#pendingList').addEventListener('click', async (e) => {
  const confirmBtn = e.target.closest('[data-confirm-lock]');
  const releaseBtn = e.target.closest('[data-release-lock]');
  try {
    if (confirmBtn) {
      const data = await api('/api/admin/schedule', {
        method: 'POST',
        body: JSON.stringify({ action: 'confirm-lock', id: confirmBtn.dataset.confirmLock }),
      });
      await refreshPublicSlots();       // 时间表没变，但顺手同步一下数字
      // ★ 确认这一下会顺手记一笔收入（后端做的），所以提示里会带上"已记一笔收入 ¥30 …"；
      //   点「确认」这一刻正好告诉他"钱也记上了"，他会更信任这个自动记账。
      applyScheduleResult(data, data.message || '已确认，这单就这样了');
      return;
    }
    if (releaseBtn) {
      if (!confirm('释放后这段时间会重新开放预约，别人可以约走。确定吗？\n'
        + '（客户那边不会收到通知，建议先微信问一句）')) return;
      const data = await api('/api/admin/schedule', {
        method: 'POST',
        body: JSON.stringify({ action: 'release-lock', id: releaseBtn.dataset.releaseLock }),
      });
      await refreshPublicSlots();
      applyScheduleResult(data, data.message || '已释放，这段时间重新开放预约');
    }
  } catch (err) {
    toast(err.message, true);
  }
});

/* --- ★ 自动释放设置 --- */
$('#saveAutoRelease').addEventListener('click', async () => {
  const hours = Number($('#autoReleaseHours').value) || 0;
  if (hours > 0 && !confirm(`打开后：客户自助预约超过 ${hours} 小时还没被你确认，`
    + `系统会**自动把它释放掉**（时段回到可约，客户不会收到通知）。\n\n确定要打开吗？`)) return;
  try {
    const data = await api('/api/admin/schedule', {
      method: 'POST', body: JSON.stringify({ action: 'set-auto-release', hours }),
    });
    applyScheduleResult(data, hours > 0
      ? `已开启：待确认超过 ${hours} 小时自动释放`
      : '已关闭自动释放（不会自动删任何预约）');
  } catch (err) {
    toast(err.message, true);
  }
});

/* --- ★ 展示墙分类管理（改名 / ↑↓ / 删除 / 新增） --- */
$('#galleryCategoryAdmin').addEventListener('click', async (e) => {
  const save = e.target.closest('[data-save-gcat]');
  const move = e.target.closest('[data-move-gcat]');
  const del = e.target.closest('[data-del-gcat]');
  try {
    if (save) {
      const cid = save.dataset.saveGcat;
      const input = $(`[data-gcat-name="${cid}"]`);
      const data = await api('/api/admin/gallery-categories', {
        method: 'POST',
        body: JSON.stringify({ action: 'save', id: cid, name: input ? input.value.trim() : '' }),
      });
      applyGalleryCategoryResult(data);
      toast('分类名字已保存，客户刷新就能看到');
      return;
    }
    if (move) {
      const cid = move.dataset.moveGcat;
      const dir = Number(move.dataset.dir);
      const ids = (state.galleryCategories || []).map((c) => c.id);
      const at = ids.indexOf(cid);
      const to = at + dir;
      if (at < 0 || to < 0 || to >= ids.length) return;
      [ids[at], ids[to]] = [ids[to], ids[at]];
      const data = await api('/api/admin/gallery-categories', {
        method: 'POST', body: JSON.stringify({ action: 'sort', order: ids }),
      });
      applyGalleryCategoryResult(data);
      toast('分类顺序已更新（前台筛选按钮的顺序也跟着变）');
      return;
    }
    if (del) {
      const cid = del.dataset.delGcat;
      if (!confirm(`确定删除「${galleryCategoryName(cid)}」这个分类吗？\n`
        + '（如果它下面还有图，系统会拦住你，先那些图改到别的分类）')) return;
      const data = await api('/api/admin/gallery-categories', {
        method: 'POST', body: JSON.stringify({ action: 'delete', id: cid }),
      });
      applyGalleryCategoryResult(data);
      toast(`分类「${data.deleted || ''}」已删除`);
    }
  } catch (err) {
    toast(err.message, true);
  }
});

$('#addGalleryCategory').addEventListener('click', async () => {
  const input = $('#newGalleryCategory');
  const name = input.value.trim();
  const stateEl = $('#galleryCategoryState');
  if (!name) return toast('请先填写分类名字（例如：作品）', true);
  try {
    const data = await api('/api/admin/gallery-categories', {
      method: 'POST', body: JSON.stringify({ action: 'create', name }),
    });
    applyGalleryCategoryResult(data);
    input.value = '';
    stateEl.className = 'hint ok';
    stateEl.textContent = `已新增分类「${name}」`;
    toast('分类已新增，客户刷新就能看到这个筛选按钮');
  } catch (err) {
    stateEl.className = 'hint error';
    stateEl.textContent = err.message;
    toast(err.message, true);
  }
});

/** 把分类接口的返回写回状态（四个动作回的是同一份全量，整块重画就行） */
function applyGalleryCategoryResult(data) {
  if (data.galleryCategories) state.galleryCategories = data.galleryCategories;
  if (data.galleryCategoryCounts) state.galleryCategoryCounts = data.galleryCategoryCounts;
  renderGallery();
}

$('#saveGallery').addEventListener('click', async () => {
  const file = $('#gFile').files[0];
  const btn = $('#saveGallery');
  if (!file) return toast('请先选择图片', true);
  if (!$('#gTitle').value.trim()) return toast('请填写标题', true);
  const zoneId = $('#gZone').value;
  if (!zoneId) return toast('请先选择这张图属于哪个分区', true);
  // 外链：前端先挡一道（省得白传一张图才被后端洗掉），后端的 clean_link() 还会再挡一道
  const link = $('#gLink').value.trim();
  if (link && !/^https?:\/\//i.test(link)) {
    return toast('链接要以 http:// 或 https:// 开头（比如 https://www.bilibili.com/video/BV…）', true);
  }
  btn.disabled = true;
  $('#galleryHint').textContent = '正在处理并上传…';
  try {
    const dataUrl = await compressImage(file);
    const data = await api('/api/admin/gallery', {
      method: 'POST',
      body: JSON.stringify({
        zone: zoneId,
        title: $('#gTitle').value.trim(),
        caption: $('#gCaption').value.trim(),
        category: $('#gCategory').value,
        link,                                   // ★ 选填，空字符串也行
        visible: $('#gVisible').checked,
        image: dataUrl,
      }),
    });
    state.gallery = data.gallery;
    state.galleryZoneId = zoneId;
    if (data.galleryCategories) state.galleryCategories = data.galleryCategories;
    $('#gTitle').value = '';
    $('#gCaption').value = '';
    $('#gLink').value = '';                     // 链接不能留到下一条（不然会串）
    $('#gFile').value = '';
    fillZonePickers();
    renderGallery();
    renderStats();
    toast(link ? '展示已保存（带外链，客户点图会新窗口打开）' : '展示已保存，图片上传成功');
  } catch (err) {
    toast(err.message, true);
  } finally {
    btn.disabled = false;
    $('#galleryHint').textContent = '';
  }
});

$('#galleryList').addEventListener('click', async (e) => {
  const vis = e.target.closest('[data-vis]');
  const arc = e.target.closest('[data-arc]');
  const del = e.target.closest('[data-del-gal]');
  const edit = e.target.closest('[data-edit-gal]');
  const saveEdit = e.target.closest('[data-save-gal]');
  try {
    if (edit) {
      // 展开 / 收起"改分类 + 改外链"那一小块（一次只开一条，免得页面太长）
      const gid = edit.dataset.editGal;
      state.editingGalleryId = state.editingGalleryId === gid ? null : gid;
      renderGallery();
      return;
    }
    if (saveEdit) {
      const gid = saveEdit.dataset.saveGal;
      const link = $('#geLink').value.trim();
      if (link && !/^https?:\/\//i.test(link)) {
        $('#geState').className = 'hint error';
        $('#geState').textContent = '链接要以 http:// 或 https:// 开头。';
        return;
      }
      const data = await api('/api/admin/gallery', {
        method: 'PATCH',
        body: JSON.stringify({ id: gid, category: $('#geCategory').value, link }),
      });
      state.gallery = data.gallery;
      state.editingGalleryId = null;
      renderGallery();
      toast(link ? '已保存（客户点这张图会新窗口打开外链）' : '已保存（这张图恢复成点开放大）');
      return;
    }
    if (vis) {
      const data = await api('/api/admin/gallery', {
        method: 'PATCH', body: JSON.stringify({ id: vis.dataset.vis, visible: vis.dataset.value === '1' }),
      });
      state.gallery = data.gallery;
      renderGallery();
      renderStats();
      toast('可见性已更新');
    } else if (arc) {
      const data = await api('/api/admin/gallery', {
        method: 'PATCH', body: JSON.stringify({ id: arc.dataset.arc, archived: arc.dataset.value === '1' }),
      });
      state.gallery = data.gallery;
      renderGallery();
      renderStats();
      toast(arc.dataset.value === '1' ? '已归档，可随时恢复' : '已恢复展示');
    } else if (del) {
      if (!confirm('删除后无法恢复（图片文件也会一起删除），确定吗？')) return;
      const data = await api('/api/admin/gallery', {
        method: 'DELETE', body: JSON.stringify({ id: del.dataset.delGal }),
      });
      state.gallery = data.gallery;
      renderGallery();
      renderStats();
      toast('已删除');
    }
  } catch (err) {
    toast(err.message, true);
  }
});

/* --- 安全 --- */
$('#savePassword').addEventListener('click', async () => {
  const next = $('#pwNext').value;
  if (next !== $('#pwConfirm').value) {
    $('#pwState').textContent = '两次新密码不一致。';
    $('#pwState').className = 'hint error';
    return;
  }
  try {
    await api('/api/admin/password', {
      method: 'POST', body: JSON.stringify({ current: $('#pwCurrent').value, next }),
    });
    $('#pwState').textContent = '密码已更新，请重新登录。';
    $('#pwState').className = 'hint ok';
    setTimeout(() => location.reload(), 1200);
  } catch (err) {
    $('#pwState').textContent = err.message;
    $('#pwState').className = 'hint error';
  }
});

/* --- 预约通知 --- */
$('#notifyChannel').addEventListener('change', applyNotifyPlaceholder);

$('#saveNotify').addEventListener('click', async () => {
  try {
    const data = await api('/api/admin/notify', {
      method: 'POST',
      body: JSON.stringify({
        channel: $('#notifyChannel').value,
        target: $('#notifyTarget').value.trim(),
      }),
    });
    state.notifyChannel = data.notifyChannel;
    state.notifyTarget = data.notifyTarget;
    state.notifyLast = data.notifyLast || state.notifyLast;
    renderNotifyState(state.notifyLast);
    toast('通知设置已保存');
  } catch (err) {
    toast(err.message, true);
  }
});

$('#testNotify').addEventListener('click', async () => {
  const btn = $('#testNotify');
  btn.disabled = true;
  btn.textContent = '正在发送…';
  try {
    const data = await api('/api/admin/notify-test', { method: 'POST', body: JSON.stringify({}) });
    state.notifyLast = data.notifyLast || state.notifyLast;
    renderNotifyState(state.notifyLast);
    toast(data.ok ? '测试通知已发出，去手机上看看' : `发送失败：${data.detail}`, !data.ok);
  } catch (err) {
    toast(err.message, true);
  } finally {
    btn.disabled = false;
    btn.textContent = '发送测试通知';
  }
});

/* --- 备份恢复 --- */
$('#importBtn').addEventListener('click', async () => {
  const file = $('#importFile').files[0];
  if (!file) return toast('请先选择备份文件', true);
  const btn = $('#importBtn');
  btn.disabled = true;
  $('#importState').textContent = '正在读取…';
  try {
    const text = await file.text();
    let backup;
    try {
      backup = JSON.parse(text);
    } catch {
      throw new Error('这个文件不是有效的 JSON，请选对备份文件。');
    }
    const locks = (backup.locks || []).length;
    const rules = (backup.rules || []).length;
    const gallery = (backup.gallery || []).length;
    const zones = (backup.zones || []).length;
    const customers = (backup.customers || []).length;
    const payments = (backup.payments || []).length;
    const texts = Object.keys(backup.texts || {}).length;
    const terms = ((backup.terms || {}).sections || []).length;
    const faq = ((backup.faq || {}).items || []).length;
    const steps = ((backup.steps || {}).items || []).length;
    if (!confirm(`将要恢复：\n· 预约/休息时段 ${locks} 条\n· 固定休息规则 ${rules} 条\n`
      + `· 分区 ${zones} 个（含价格与图片）\n· 展示墙 ${gallery} 条\n`
      + `· 客户档案 ${customers} 位${backup.customers ? '' : '（这份老备份里没有客户，你现在的客户档案会原样保留）'}\n`
      + `· 收入流水 ${payments} 笔${backup.payments ? '' : '（这份老备份里没有收入流水，你现在记的账会原样保留）'}\n`
      + `· 文案 ${texts} 句${backup.texts ? '' : '（这份老备份里没有文案，你现在改过的话会原样保留）'}\n`
      + `· 服务条款 ${terms} 节${backup.terms ? '' : '（这份老备份里没有条款，你现在那份会原样保留）'}\n`
      + `· 常见问题 ${faq} 条${backup.faq ? '' : '（这份老备份里没有常见问题，你现在写的会原样保留）'}\n`
      + `· 服务流程 ${steps} 步${backup.steps ? '' : '（这份老备份里没有服务流程，你现在那份会原样保留）'}\n\n`
      + `会覆盖现在的全部数据，确定吗？`)) return;

    const data = await api('/api/admin/import', { method: 'POST', body: JSON.stringify({ backup }) });
    state.profile = data.profile;
    state.revision = data.profileRevision;
    state.locks = data.schedule.locks;
    state.rules = data.schedule.rules;
    state.gallery = data.gallery;
    state.categories = data.categories || state.categories;
    state.zones = data.zones || state.zones;
    state.customers = data.customers || state.customers;
    state.customerStats = data.customerStats || state.customerStats;
    if (data.today) state.customerToday = data.today;
    if (data.income) applyIncomeResult(data);      // ★ 收入流水也跟着恢复（老备份没带就不动）
    // ★ 文案和条款也跟着恢复（老备份没带的话，后端一条都不动，这里拿到的是他现在那份）
    if (data.textGroups) state.textGroups = data.textGroups;
    if (data.texts) state.texts = data.texts;
    if (data.textOverrides) state.textOverrides = data.textOverrides;
    if (data.terms) state.terms = data.terms;
    // ★ 常见问题 / 服务流程也跟着恢复（老备份没带的话，后端一条都不动）
    if (data.faq) state.faq = data.faq;
    if (data.steps) state.steps = data.steps;
    if (!zoneById(state.currentZoneId)) state.currentZoneId = state.zones.length ? state.zones[0].id : '';
    if (!zoneById(state.galleryZoneId)) state.galleryZoneId = state.currentZoneId;
    state.batch.clear();
    renderAll();
    resetLockForm();
    $('#importState').textContent =
      `已恢复：时段 ${data.summary.locks} 条、规则 ${data.summary.rules} 条、`
      + `分区 ${data.summary.zones || 0} 个、展示 ${data.summary.gallery} 条、`
      + `客户 ${data.summary.customers || 0} 位、收入 ${data.summary.payments || 0} 笔、`
      + `文案 ${data.summary.texts || 0} 句、条款 ${data.summary.terms || 0} 节、`
      + `常见问题 ${data.summary.faq || 0} 条、流程 ${data.summary.steps || 0} 步`
      + `（含图片 ${data.summary.images} 张）`;
    $('#importState').className = 'hint ok';
    toast('备份已恢复');
  } catch (err) {
    $('#importState').textContent = err.message;
    $('#importState').className = 'hint error';
    toast(err.message, true);
  } finally {
    btn.disabled = false;
  }
});

async function refreshPublicSlots() {
  const pub = await api('/api/public');
  state.publicSlots = pub.slots;
  state.profile = pub.profile;
  renderStats();
}

/* ==========================================================================
   启动
   ========================================================================== */
(async function main() {
  fillHourSelects();
  fillRuleHourSelects();
  resetRuleForm();
  $('#adminDate').value = dateValue(Date.now());
  try {
    const auth = await api('/api/auth');
    if (auth.authenticated) {
      showAdmin();
      await loadAll();
      resetLockForm();
    } else {
      showLogin();
    }
  } catch {
    showLogin();
  }
})();
