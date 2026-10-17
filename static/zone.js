/* ==========================================================================
   分区页逻辑（/z/<分区id>）
     · booking 类分区（陪玩）：三档价格 + 完整的预约时间表（选格子 → 选模式 → 立即锁定）
     · service 类分区（代肝 / 剪辑 / 设计 / 建站）：主推档位 + 分组价目表，**没有时间表**

   ⚠️ 时间表是全局共用的：不管在哪个分区，拿到的 slots 都是同一份 168 格。
   ========================================================================== */

const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));

const CN_OFFSET = 8 * 3600 * 1000;   // 北京时间 = UTC+8
const HOUR = 3600 * 1000;
const MAX_HOURS = 6;                 // 一次最多能选几小时（和后端 MAX_BOOK_HOURS 保持一致）

const CONTACT_TEXT = { wechat: '微信', xianyu: '闲鱼', qq: 'QQ' };
// 输入框里的灰字：每一种联系方式一句（都在后台「文案」里能改）
const CONTACT_PLACEHOLDER_KEYS = {
  wechat: ['contactPhWechat', '你的微信号'],
  xianyu: ['contactPhXianyu', '你的闲鱼用户名'],
  qq: ['contactPhQq', '你的 QQ 号'],
};

const INITIAL = window.__PAGE__ || {};
const state = {
  site: INITIAL.site || {},
  nav: INITIAL.nav || [],
  zone: INITIAL.zone || { profile: {}, id: window.__ZONE_ID__ || '' },
  slots: [],
  gallery: [],
  galleryCategories: [],           // ★ 展示墙分类（后台可配置，前端按它生成筛选按钮）
  selected: new Set(),   // 已选中的整点时间戳
  dayIndex: 0,
  galleryFilter: 'all',
  modeIndex: 0,
  syncedAt: 0,
};

/* ---------- ★ 文案表：客户看到的每一句中文，都由后台「文案」决定 ----------
   服务端（app.py 的 site_summary）已经把默认值补齐了；tx() 的第二个参数是最后一道保险。
   花括号是"变量"：{name} 昵称 / {zone} 分区名 / {max} 一次最多几小时。 */
function TEXTS() { return (state.site && state.site.texts) || {}; }

function tx(key, fallback, vars) {
  const value = TEXTS()[key];
  const text = (value === undefined || value === null || value === '') ? (fallback || '') : value;
  const map = vars || {};
  return String(text).replace(/\{(\w+)\}/g, (m, k) => (map[k] !== undefined ? map[k] : m));
}

/** 分区资料的取值小工具（每一处都写 state.zone.profile 太啰嗦） */
function profile() { return state.zone.profile || {}; }

/** 图片的显示位置（后台九宫格选的）。没设置过就是 center —— 和以前一模一样 */
function pos(key) { return profile()[key + 'Position'] || 'center'; }

const SELECTION_HINT = () => tx('selectionHint',
  `先点选想玩的时间，连续或分开都可以（一次最多 ${MAX_HOURS} 小时）。`, { max: MAX_HOURS });

/* ---------- 客户在什么环境里打开的？----------
   这件事很关键：微信个人二维码是**微信私有格式**，只有微信自己能解析成"加好友"。
   在微信里长按 = 能识别；在别的浏览器里长按 = 读出链接但打不开加好友页。
   所以"怎么加我"这句话必须分环境说，否则等于让客户做一件他做不到的事。 */
const UA_TEXT = navigator.userAgent || '';
const IN_WECHAT = /MicroMessenger/i.test(UA_TEXT);
const BROWSER_NAME = (() => {
  if (IN_WECHAT) return '微信';
  if (/QQBrowser/i.test(UA_TEXT)) return 'QQ 浏览器';
  if (/UCBrowser|UCWEB/i.test(UA_TEXT)) return 'UC 浏览器';
  if (/HuaweiBrowser/i.test(UA_TEXT)) return '华为浏览器';
  if (/MiuiBrowser|XiaoMi/i.test(UA_TEXT)) return '小米浏览器';
  if (/HeyTapBrowser|OppoBrowser/i.test(UA_TEXT)) return 'OPPO 浏览器';
  if (/VivoBrowser/i.test(UA_TEXT)) return 'vivo 浏览器';
  if (/Quark/i.test(UA_TEXT)) return '夸克浏览器';
  if (/baidubrowser|BIDUBrowser|baidu/i.test(UA_TEXT)) return '百度浏览器';
  if (/Weibo/i.test(UA_TEXT)) return '微博';
  if (/EdgA|EdgiOS/i.test(UA_TEXT)) return 'Edge';
  if (/CriOS/i.test(UA_TEXT)) return 'Chrome';
  if (/Safari/i.test(UA_TEXT)) return 'Safari';
  return '这个浏览器';
})();

/* ---------- 时间小工具：始终按北京时间显示，不受访客电脑时区影响 ---------- */
function cn(ms) {
  const d = new Date(ms + CN_OFFSET);
  return {
    y: d.getUTCFullYear(), m: d.getUTCMonth() + 1, d: d.getUTCDate(),
    h: d.getUTCHours(), w: d.getUTCDay(),
  };
}
const WEEK = ['周日', '周一', '周二', '周三', '周四', '周五', '周六'];
const pad = (n) => String(n).padStart(2, '0');
const hhmm = (ms) => pad(cn(ms).h) + ':00';
const dateLabel = (ms) => `${cn(ms).m}月${cn(ms).d}日`;
const fullLabel = (ms) => `${dateLabel(ms)} ${WEEK[cn(ms).w]} ${hhmm(ms)}`;

/* ---------- 通用请求 ---------- */
async function api(path, options = {}) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 15000);
  try {
    const res = await fetch(path, {
      ...options,
      signal: controller.signal,
      headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
    });
    const text = await res.text();
    const data = text ? JSON.parse(text) : {};
    if (!res.ok) {
      const err = new Error(data.error || `请求失败（${res.status}）`);
      err.status = res.status;
      err.data = data;
      throw err;
    }
    return data;
  } catch (err) {
    if (err.name === 'AbortError') throw new Error('连接网站超时，请刷新后重试。');
    if (err instanceof TypeError) throw new Error('连接网站失败，请检查网络后重试。');
    throw err;
  } finally {
    clearTimeout(timer);
  }
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

/** 复制文字：clipboard → execCommand → 手动长按，三级兜底 */
async function copyText(text, okMessage = '已复制') {
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

function showCopyFallback(text) {
  const box = $('#copyTextBox');
  box.value = text;
  $('#copyModal').classList.add('open');
  setTimeout(() => { box.focus(); box.select(); }, 50);
  toast('请长按框里的文字复制', true);
}

/* ---------- 顶部导航：按大类分组的标签 ---------- */
function renderTabs() {
  const current = state.zone.id;
  $('#zoneTabs').innerHTML = (state.nav || []).map((g) => `
    <span class="zone-group">
      <span class="zone-group-name">${escapeHtml(g.name)}</span>
      ${(g.zones || []).map((z) => `
        <a class="zone-tab ${z.id === current ? 'active' : ''}"
           href="/z/${encodeURIComponent(z.id)}"
           style="--zone:${escapeHtml(z.theme)}">${escapeHtml(z.short || z.name)}</a>`).join('')}
    </span>`).join('');
  $('#brandMark').textContent = state.site.brand || state.site.name || 'Nya';
  $('#brandName').textContent = state.site.name || '';
  $('#navTerms').textContent = tx('navTerms', '服务条款');
}

/* ---------- 分区首屏 ---------- */
function applyBackground() {
  const p = profile();
  const bg = $('#zoneHeroBg');
  if (!p.background) { bg.style.display = 'none'; return; }
  bg.style.display = '';
  bg.style.backgroundImage = `url("${p.background}")`;
  // ★ 显示位置：分区背景图是 background-size: cover（铺满就会被裁），
  //   这一行决定保留哪一块（后台「分区图片」里的九宫格选的）
  bg.style.backgroundPosition = pos('background');
  let dim = Number(p.backgroundDim);
  if (!Number.isFinite(dim)) dim = 72;
  bg.style.setProperty('--dim', String(Math.min(90, Math.max(0, dim)) / 100));
}

/** 页脚那一套（分区页和首页共用同一批文案，这里单独一个函数免得散在各处） */
function renderFooterTexts() {
  const site = state.site;
  const z = state.zone;
  const booking = (z.kind || 'booking') === 'booking';
  $('#footerZone').textContent = `${tx('footerZoneLabel', '当前页面：')}${z.name}（/z/${z.id}）`;
  // 这两句是分区页页脚收尾的话（陪玩一句、代肝/剪辑一句）
  $('#footerHint').textContent = booking
    ? tx('footerHintBooking', '选好时间点一下就锁上了，我这边立刻收到消息。')
    : tx('footerHintService', '还没想好要哪一档？加我问一句，说清需求和工期就行。');
  $('#footerTip').textContent = tx('footerTip', '加我之后直接说：哪个游戏 / 哪一档，我来安排。');
  $('#footerDisclaimer').textContent = tx('footerDisclaimer',
    '本页为个人服务页面，与游戏官方无隶属关系；游戏素材版权归原权利人所有。');
  $('#footerAboutLink').textContent = tx('navAbout', '关于我');
  $('#footerTermsLink').textContent = tx('navTerms', '服务条款');

  // 页脚收尾区：二维码 + 微信号（读完这页的人，这里是他最后的出口）
  const fq = $('#footerQr');
  if (fq) {
    fq.alt = tx('wechatQrAlt', '我的微信二维码');
    if (site.wechatQr) { fq.src = site.wechatQr; fq.hidden = false; }
    else { fq.hidden = true; fq.removeAttribute('src'); }
  }
  const fcap = $('#footerQrCaption');
  if (fcap) {
    fcap.textContent = tx('footerQrCaption', '长按上面的二维码就能加我（部分浏览器支持）');
    fcap.hidden = IN_WECHAT || !site.wechatQr;
  }
  const fid = $('#footerWechatId');
  if (fid) fid.textContent = site.wechat || tx('wechatEmpty', '（还没设置微信号）');
}

function renderHero() {
  const z = state.zone;
  const p = profile();
  const site = state.site;
  document.body.dataset.kind = z.kind || 'booking';

  $('#heroEyebrow').textContent = p.tagline || z.name;
  $('#heroHeadline').textContent = p.headline || p.game || z.name;
  $('#heroSub').textContent = p.subheadline
    || [p.game, p.platform].filter(Boolean).join(' · ');
  $('#heroTags').innerHTML = (p.highlights || [])
    .map((t) => `<span class="tag">${escapeHtml(t)}</span>`).join('');
  $('#announcement').textContent = p.announcement || '';
  $('#acceptingDot').classList.toggle('on', !!site.accepting);
  $('#acceptingText').textContent = site.accepting
    ? tx('acceptingOn', '开放接单') : tx('acceptingOff', '暂时休整');
  $('#copyWechat').textContent = tx('wechatCta', '加微信');

  const wechatLine = $('#wechatLine');
  if (site.wechat) {
    wechatLine.innerHTML = `${escapeHtml(tx('wechatLabel', '微信号：'))}<b id="wechatValue">${escapeHtml(site.wechat)}</b>`
      + (IN_WECHAT || typeof IN_WECHAT === 'undefined'
        ? `<span class="wechat-tip">${escapeHtml(tx('wechatCopyTip', '长按可复制'))}</span>`
        : `<span class="wechat-tip">${escapeHtml(tx('wechatCopyTipBrowser', '点一下复制，去微信粘贴'))}</span>`);
    wechatLine.hidden = false;
  } else {
    wechatLine.hidden = true;
  }

  const hero = p.heroImage || '';
  $('#heroMedia').hidden = !hero;
  // ★ 首屏主视觉是 object-fit: cover（太高就会被裁），位置由后台九宫格决定
  $('#heroMedia').innerHTML = hero
    ? `<img src="${hero}" alt="${escapeHtml(p.game || z.name)}"
         style="object-position:${escapeHtml(pos('heroImage'))}">` : '';
  $('#heroGrid').classList.toggle('with-media', !!hero);

  // 首屏主按钮：预约制 → 看空档；服务制 → 看价目表。
  // ⚠️ 服务制以前写的是"加微信直接下单"，可旁边就有一个「加微信」按钮 ——
  //    两个按钮做同一件事，客户会懵。而且刚进页面的人最想知道的是"多少钱"，
  //    所以主按钮指向价目表，加微信交给旁边那个按钮。
  //    这两句话现在都在后台「文案」里能改（zoneCtaBooking / zoneCtaService）。
  const primary = $('#heroPrimary');
  if (z.kind === 'booking') {
    primary.textContent = tx('zoneCtaBooking', '看空档时间');
    primary.href = '#schedule';
  } else {
    primary.textContent = tx('zoneCtaService', '看价目表');
    primary.href = '#servicePriceBlock';
  }
  primary.hidden = false;

  $('#zoneHero').classList.toggle('has-bg', !!p.background);
  applyBackground();
  renderFooterTexts();

  $('#footerName').textContent = [site.brand || site.name, p.game || z.name]
    .filter(Boolean).join(' · ');
}

/** 时间表 / 图例 / 各种标题上的字：全部来自文案表（renderHero 之后调一次） */
function renderTexts() {
  $('#priceTitle').textContent = tx('priceTitle', '玩的方式你选，陪伴我来。');
  $('#priceHint').textContent = tx('priceHint', '明码标价 · 个人直约 · 按实际游玩时间计费');
  $('#featuredTitle').textContent = tx('featuredTitle', '主推档位');
  $('#priceSheetTitle').textContent = tx('priceSheetTitle', '完整价目表');
  $('#priceSheetHint').textContent = tx('priceSheetHint', '点标题展开 / 收起');
  $('#priceSheetImageLabel').textContent = tx('priceSheetImageLabel', '完整价目表（长图）');
  $('#scheduleTitle').textContent = tx('scheduleTitle', '你的时间，我来配合。');
  $('#scheduleHint').textContent = tx('scheduleHint',
    '每格 1 小时 · 北京时间 · 点击空闲格多选，可跨日期');
  $('#sharedNotice').textContent = tx('sharedNotice',
    '这份时间表是全部服务共用的：不管约哪个游戏，占的都是同一批时段。');
  $('#legendFree').textContent = tx('legendFree', '可预约');
  $('#legendBooking').textContent = tx('legendBooking', '已预约');
  $('#legendRest').textContent = tx('legendRest', '休息 / 不接单');
  $('#legendPast').textContent = tx('legendPast', '已过去');
  $('#selectionHint').textContent = SELECTION_HINT();
  $('#nextStep').textContent = tx('nextStep', '下一步：选择模式');
  // 待确认弹窗里那些标签和小字
  $('#modeTitle').textContent = tx('modeTitle', '选择模式');
  $('#modeHint').textContent = tx('modeHint',
    '选好模式、留下联系方式，点「立即锁定时间」这几个小时就归你了。');
  $('#contactTypeLabel').textContent = tx('contactTypeLabel', '联系方式');
  $('#contactTypeHint').textContent = tx('contactTypeHint', '填哪个都行，方便联系。');
  $('#contactLabel').textContent = tx('contactLabel', '你的联系方式（必填）');
  $('#contactHint').textContent = tx('contactHint',
    '请填真实能加上的号——填错了我加不到你，这单就联系不上了（也可以直接加我，见页面顶部）');
  $('#noteLabel').textContent = tx('noteLabel', '备注（选填）');
  $('#bookNote').placeholder = tx('notePlaceholder', '例如：想玩大坝、晚上可能晚到 10 分钟');
  $('#lockWarn').textContent = tx('lockWarn',
    '⚠️ 点「立即锁定时间」后，这几个小时会立刻被锁定，别人就点不动了；如需取消请微信联系我。');
  $('#doneTitle').textContent = tx('doneTitle', '时间已锁定 ✅');
  $('#doneNote').textContent = tx('doneNote', '时间预约成功Ciallo～(∠・ω< )⌒★');
  $('#doneWechatTitle').textContent = tx('doneWechatTitle', '下一步：加我微信');
  $('#doneCloseHint').textContent = tx('doneCloseHint', '要取消或改时间，微信上跟我说一声就行。');
  applyContactPlaceholder();
}

/* ---------- A. 预约制：三档价格 ---------- */
function renderPriceCards() {
  const prices = (profile().prices) || [];
  $('#priceCards').innerHTML = prices.map((it, i) => `
    <article class="card">
      <div class="no">模式 / ${pad(i + 1)}</div>
      <h3>${escapeHtml(it.name)}</h3>
      <p>${escapeHtml(it.description || '')}</p>
      <div class="price">¥${Number(it.price) || 0}<small> / 小时</small></div>
    </article>`).join('')
    || `<div class="empty-state">${escapeHtml(tx('priceEmptyBooking', '这个分区还没有设置报价，去后台加一档。'))}</div>`;
  // ★ 卡片画完了才知道有几张 → 小圆点跟着卡片数量走（手机端才显示）
  renderPriceDots();
}

/* ---------- B. 服务制：主推档位 + 分组价目表 ---------- */
function allItems() {
  const groups = (state.zone.profile || {}).priceGroups || [];
  return groups.flatMap((g) => (g.items || []).map((it) => ({ ...it, group: g.name })));
}

/**
 * ★ 「这个包含什么 ▾」：价目项那个可选的详细说明（detail）。
 *
 * 为什么要有它：客户在主推档位上只看到一行说明，**他不知道多花的钱买到了什么**。
 * 让他点一下就能看到清单（"精托比普通多做了这几件事"），比把表格撑得很长好得多。
 *
 * 三条细节：
 *  · 没有 detail 的条目**一个字都不多显示**（老数据、没填过的档位完全看不出变化）；
 *  · 用原生 <details>（能点开、键盘和读屏都认），外层 `.detail` + 里面 `.detail-body`；
 *  · 正文用 white-space: pre-line 渲染（主人在后台是一行一条敲的清单），
 *    但**仍然是纯文本**（escapeHtml）—— 文案不认 HTML 是全站的安全底线。
 *
 * ⚠️ 类名故意写成两个**字面量**（.detail / .detail-body）而不是用 `${cls}` 拼，
 *    因为 tests/check_frontend.js 是靠"字符串里有没有出现过这个类名"来查
 *    "CSS 里有没有没人用的样式"的 —— 拼出来的类名它认不出来，
 *    于是那两条样式会被报成"未被使用"（第一版就是这样，校验器当场报了）。
 */
function detailHtml(it) {
  const detail = String((it && it.detail) || '').trim();
  if (!detail) return '';
  return `<details class="detail">
    <summary>这个包含什么 ▾</summary>
    <div class="detail-body">${escapeHtml(detail)}</div>
  </details>`;
}

function renderServicePrices() {
  const p = profile();
  const groups = p.priceGroups || [];
  const items = allItems();
  const featuredIds = p.featured || [];
  // 主推：先按后台指定的顺序取，不够 3 个就顺手补"标了 ⭐ 的"
  let featured = featuredIds.map((id) => items.find((it) => it.id === id)).filter(Boolean);
  if (featured.length < 3) {
    const extra = items.filter((it) => it.hot && !featured.includes(it));
    featured = featured.concat(extra).slice(0, 3);
  }

  // 标题里带 {zone}（= 游戏名 / 分区名）；改了游戏名，标题跟着变
  $('#serviceTitle').textContent = tx('servicePriceTitle', `${p.game || state.zone.name} · 价目表`,
    { zone: p.game || state.zone.name });
  // 副标题那句小字来自「大类的小字」（后台 → 分区管理 → 大类那一行的第二个输入框），
  // 不再写死在 HTML 里 —— 想改成"包月 / 按量""按需报价"都行
  const hintEl = $('#serviceHint');
  if (hintEl) {
    const cat = (state.nav || []).find((g) => g.id === state.zone.category);
    const hint = (cat && cat.hint) || (state.zone.kind === 'booking' ? '按小时预约' : '按需报价');
    hintEl.textContent = `${tx('servicePriceHint', '加微信直接下单')} · ${hint}`;
  }
  $('#featuredTitle').textContent = featured.length ? tx('featuredTitle', '主推档位') : '';
  $('#featuredCards').innerHTML = featured.map((it) => `
    <article class="card featured-card">
      ${it.hot ? '<span class="badge hot">⭐ 推荐</span>' : ''}
      <div class="no">${escapeHtml(it.group || '')}</div>
      <h3>${escapeHtml(it.name)}</h3>
      <div class="price-text">${escapeHtml(it.price || '价格私聊')}</div>
      <p>${escapeHtml(it.note || '')}</p>
      ${detailHtml(it)}
    </article>`).join('')
    || `<div class="empty-state">${escapeHtml(tx('priceEmptyService',
      '还没设置主推档位。去后台「分区管理 → 编辑 → 价目表」勾几个 ⭐ 主推。'))}</div>`;

  $('#priceGroups').innerHTML = groups.map((g, gi) => `
    <details class="price-group" ${gi === 0 ? 'open' : ''}>
      <summary>
        <span class="group-title">${escapeHtml(g.name)}</span>
        <span class="hint">${(g.items || []).length} 项${g.note ? ' · ' + escapeHtml(g.note) : ''}</span>
      </summary>
      <table class="price-table">
        <thead><tr><th>名称</th><th>价格</th><th>说明</th></tr></thead>
        <tbody>${(g.items || []).map((it) => `
          <tr class="${it.hot ? 'hot' : ''}">
            <td>${escapeHtml(it.name)}${it.hot ? ' <span class="badge hot">⭐</span>' : ''}${
              detailHtml(it)}</td>
            <td class="money">${escapeHtml(it.price || '—')}</td>
            <td>${escapeHtml(it.note || '')}</td>
          </tr>`).join('')}</tbody>
      </table>
    </details>`).join('')
    || `<div class="empty-state">${escapeHtml(tx('groupsEmpty',
      '还没有价目表。去后台「分区管理 → 编辑 → 价目表」加一组，或者用「批量粘贴导入」。'))}</div>`;

  const sheet = p.priceSheetImage || '';
  $('#priceSheetBox').hidden = !sheet;
  if (sheet) {
    $('#priceSheetImg').src = sheet;
    $('#priceSheetImg').dataset.zoom = sheet;
  }
  $('#priceSheetHint').hidden = !groups.length;
}

/* ---------- ★ 服务流程（几步的图）+ 常见问题（折叠块） ----------
   位置（2026-10-16 主人重新定过的，见 zone.html 顶部那两段注释）：
     · 服务流程 = **档位卡下面**（价格区里面）；
     · 承诺 / 须知 = **预约前面**；
     · 常见问题 = **预约后面**（独立的 <section id="faq">）。

   三块都能折叠：
     · 流程、承诺 → 永远是 <details>（两端都一样，手机上也折叠）；
     · 常见问题   → 每条一个 <details>（默认收起），整块要不要再套一层折叠
                    由后台的 faqPanelSummary **留不留空**决定（留空 = 不套）。

   两块内容都是主人自己在后台整条写的（后台「文案」→ 服务流程 / 常见问题），
   默认值由服务端补齐（state.site.faq / state.site.steps），所以这里不写兜底文案。 */

/** 把 {wechat} 换成微信号（前台的 tx() 只认 {name}/{zone}/{max}，这里补一个） */
function fillWechat(text) {
  return String(text || '').replace(/\{wechat\}/g, (state.site && state.site.wechat) || '');
}

/**
 * ★ 折叠块摘要行里"自动拼出来的那串名字"。
 *
 * 为什么要自动拼：主人改了后台的步骤名 / 承诺条目，摘要就得跟着变 ——
 * 要是把摘要写死在代码或 HTML 里，改完名字摘要还是旧的，比没有摘要还糟。
 *
 * @param {string[]} names   每条的名字（步骤标题 / 承诺那条文字）
 * @param {number}   maxShow 最多显示几条（超过就截断，默认 4）
 */
function autoSummary(names, maxShow = 5, budget = 40) {
  const list = (names || [])
    .map((t) => String(t == null ? '' : t).trim().replace(/\s+/g, ' '))
    .map((t) => shortenName(t))
    // 只有标点的垃圾条目（数据里真出现过一条 \”、\”✗）直接丢掉
    .filter((t) => t && /[^\s·、，,;；.。!！?？…\-]/.test(t));
  if (!list.length) return '';
  const out = [];
  let used = 0;
  for (const n of list) {
    if (out.length >= maxShow) break;                    // 条数上限
    if (out.length && used + n.length > budget) break;   // 字数预算（免得摘要比正文还长）
    out.push(n);
    used += n.length + 3;                                // +3 是分隔符 " · "
  }
  const more = out.length < list.length;
  return out.join(' · ') + (more ? ' …' : '');
}

/**
 * ★ 摘要里每一条的"短名字"：短的就原样用，长的就砍一小段。
 *
 * 为什么要这一步（2026-10-16 踩的）：
 *   「怎么开始」那几步骤本身有短标题（"挑时间 / 说需求"）✓ 拼起来正好一行 ✓
 *   但「下单前须知」那几条是【整句话】✗ 直接拼出来占了整整两行 ✗
 *   （截图里那行长得像一段正文，折叠就等于没折 ✗）
 * 规矩：
 *   · ≤ 14 字 → 原样用（步骤名走这条，一个字都不动 ✓）
 *   · > 14 字 → 在第一个标点处断开；断不了就硬切 14 字 + 省略号
 */
function shortenName(text, limit = 14) {
  const s = String(text == null ? '' : text).trim();
  if (s.length <= limit) return s;
  const cut = s.search(/[。；！？，、,;!?]/);
  if (cut >= 4 && cut <= limit) return s.slice(0, cut);
  return s.slice(0, limit) + '…';
}

/** 「怎么开始」摘要行的名字来源：后台的步骤数据（改了步骤名，这里自动跟着变） */
function stepsSummaryText() {
  const s = (state.site && state.site.steps) || { items: [] };
  return autoSummary((s.items || []).map((it) => it.title));
}

/** 「下单前，聊清楚这几件事」摘要行的名字来源：这个分区资料里的承诺条目 */
function promisesSummaryText() {
  return autoSummary(((profile().promises) || []).map((t) => t));
}

/** 摘要行右侧那串小字：后台填过就用主人写的，留空就用自动拼的（这个函数两块共用） */
function panelTip(key, auto) {
  return tx(key, '') || auto || '';
}

function renderSteps() {
  const s = (state.site && state.site.steps) || { items: [] };
  const items = s.items || [];
  const panel = $('#stepsPanel');
  if (!panel) return;
  if (!items.length) {                     // 理论上不会（后端兜底成默认 4 步）
    panel.hidden = true;
    return;
  }
  $('#stepsTitle').textContent = s.title || '怎么开始';
  $('#stepsSummary').textContent = panelTip('stepsSummary', stepsSummaryText());
  // ★ 步骤的渲染逻辑一个字没改（01/02/03 编号圆圈 + 标题 + 说明），只是搬进了 <details> 里
  $('#stepsList').innerHTML = items.map((it) => `
    <li><b>${escapeHtml(it.title || '')}</b>${
      it.desc ? `<span>${escapeHtml(fillWechat(it.desc))}</span>` : ''}</li>`).join('');
  panel.hidden = false;
}

function renderFaq() {
  const f = (state.site && state.site.faq) || { items: [] };
  const items = f.items || [];
  const panel = $('#faqPanel');
  if (!panel) return;
  if (!items.length) {
    panel.hidden = true;
    return;
  }
  const title = f.title || '常见问题';
  const sub = $('#faqSubtitle');
  if (sub) {
    sub.textContent = f.subtitle || '';
    sub.hidden = !f.subtitle;
  }
  // ★ 这一段在 2026-10-16 重排过顺序，但**外面看起来一模一样**：
  //   整块要不要再套一层折叠？只有主人填了 faqPanelSummary 才套（留空 = 不套）。
  //   两条路径用的是同一份条目 HTML 渲染，不会出现"两种状态长得不一样"。
  //   （折叠那一路是"重画一块干净的 HTML"，所以标题和副标题也由它自己填一遍。）
  const override = tx('faqPanelSummary', '');
  if (override) {
    $('#faqPanel').innerHTML = `
      <details class="faq-panel-open">
        <summary><span class="panel-title">${escapeHtml(title)}</span><span class="panel-tip">${escapeHtml(override)}</span></summary>
        <div class="faq-body">
          ${f.subtitle ? `<p class="faq-sub">${escapeHtml(f.subtitle)}</p>` : ''}
          <div class="faq-list" id="faqList"></div>
        </div>
      </details>`;
  } else {
    $('#faqPanel').innerHTML = `
      <div class="section-head compact">
        <div><h2>${escapeHtml(title)}</h2></div>
        <p class="faq-sub">${escapeHtml(f.subtitle || '')}</p>
      </div>
      <div class="faq-list" id="faqList"></div>`;
    const sub2 = $('#faqPanel .faq-sub');
    if (sub2) sub2.hidden = !f.subtitle;
  }
  // 每条一个原生 <details>，**全部默认收起**（折叠块的意思：先看到问题，想看了再点开）
  $('#faqList').innerHTML = items.map((it) => `
    <details class="faq-item">
      <summary>${escapeHtml(it.q || '')}</summary>
      <p class="faq-answer">${escapeHtml(fillWechat(it.a || ''))}</p>
    </details>`).join('');
  panel.hidden = false;
}

/* ---------- 服务承诺 ---------- */
function renderPromises() {
  const p = profile();
  const booking = (state.zone.kind || 'booking') === 'booking';
  $('#promiseTitle').textContent = booking
    ? tx('promiseTitleBooking', '下单前，聊清楚这几件事')
    : tx('promiseTitleService', '服务说明');
  $('#promiseList').innerHTML = (p.promises || [])
    .map((t) => `<li>${escapeHtml(t)}</li>`).join('')
    || `<li>${escapeHtml(booking
      ? tx('promiseEmptyBooking', '还没有填写服务承诺，去后台这个分区的资料里补上。')
      : tx('promiseEmptyService', '还没有填写服务说明，去后台这个分区的资料里补上。'))}</li>`;
  // ★ 摘要行：条目名自动拼（超过 4 条只显示前 3 条 + …），后台填了 promisesSummary 就用主人写的
  const tip = $('#promisesSummary');
  if (tip) tip.textContent = panelTip('promisesSummary', promisesSummaryText());
}

/* ==========================================================================
   ★ 左侧固定导航栏（只在分区页、只在 ≥1440px 的宽屏上出现）
   --------------------------------------------------------------------------
   ★★ 一条铁律：**导航项绝不手写列表**。
   主人专门点过这件事：页面从上到下是 A、B、C、D，导航栏却写成 A、C、D、B ——
   客户按导航跳，顺序对不上，越点越糊涂。

   所以这里只做一件事：**按 DOM 顺序**把带 data-nav 的块收集起来，一个一个生成导航项。
     · 谁进导航 = 谁在 zone.html 里带了 data-nav（首屏和页脚故意没带）；
     · 导航文字 = data-nav 的值就是后台文案表里的 key（tx() 取出来）；
     · 顺序     = 块在页面里出现的先后（querySelectorAll 天生按文档顺序返回）；
     · 目标锚点 = 块自己的 id。
   于是"挪动一块 / 删掉一块 / 新加一块"都不用改这里的代码，导航自动跟着变，
   顺序**永远**和页面一致（scroll-spy 也用同一份 items 算，两边不可能不同步）。
   ========================================================================== */

/** 收集要进导航的块（DOM 顺序 = 页面从上到下的顺序） */
function navBlocks() {
  return $$('[data-nav]').filter((el) => el && el.id);
}

/** 导航栏现在是不是"看得见"（窄屏时 CSS 把它 display:none 了） */
function navVisible() {
  const nav = $('#zoneNav');
  if (!nav || !navBlocks().length) return false;
  return getComputedStyle(nav).display !== 'none';
}

/** 点导航 / 顶部标签跳过去时要留出的高度：吸顶导航栏 + 一点呼吸（不然标题贴着屏幕边）
 *  ★ 2026-10-16（手机端改造）：手机上挡在正文上面的**不止**顶部导航那一条了 ——
 *    芯片条（#zoneNav）改成了吸顶（CSS 里 position: sticky; top: 60px），
 *    它自己也有 30~40px 高。跳转时只减顶部导航的高度 ✗ → 目标块的标题正好被芯片条盖住 ✗
 *    所以这里改成量**两条的真实高度之和** ✓
 *    · 电脑端：#zoneNav 是 position:fixed（不在文档流里，但高度照样量得出来），
 *      不过电脑端的 chipBarVisible() 返回 false → 不加它 → 结果和以前**一模一样** ✓
 *      （这一点很关键：电脑端的跳转落点一个像素都不能变 ✗）
 *    · 手机端：60（顶部导航）+ 约 38（芯片条）+ 14（呼吸）= 约 112px */
function navOffset() {
  const bar = document.querySelector('.nav');
  const barH = bar ? bar.getBoundingClientRect().height : 60;
  const chip = chipBarVisible() ? document.getElementById('zoneNav') : null;
  const chipH = chip ? chip.getBoundingClientRect().height : 0;
  return Math.round(barH + chipH + 14);
}

/**
 * ★ 芯片条现在是不是"吸在顶上的一条"？（只有手机端才是）
 *
 * 为什么不能直接看 display：电脑端那个左侧竖列也是 display:block，但它 position:fixed
 * 贴在正文左边的空地里，**不挡正文** —— 它的高度不该算进跳转偏移里 ✗
 * 判据取 position:sticky（手机端 CSS 里就是这么写的），一刀切干净：
 *   · 电脑端 fixed  → false → 不加高度 → 落点和以前一模一样 ✓
 *   · 手机端 sticky → true  → 加上芯片条高度 ✓
 * 用 getComputedStyle 现场判，不写死屏幕宽度 —— 以后改断点也不会漏改这里 ✓ */
function chipBarVisible() {
  const nav = document.getElementById('zoneNav');
  if (!nav) return false;
  const cs = getComputedStyle(nav);
  return cs.display !== 'none' && cs.position === 'sticky';
}

function renderNav() {
  const nav = $('#zoneNav');
  const list = $('#zoneNavList');
  if (!nav || !list) return;
  $('#zoneNavTitle').textContent = tx('navZoneTitle', '本页内容');

  const blocks = navBlocks();
  // ⚠️ 服务制分区没有时间表那一块：它是 hidden 的，但 hidden 的块**照样会被
  //    querySelectorAll 选到**。所以"缺哪块就少哪一项"必须在这里用 hidden 过滤掉 ——
  //    否则服务制分区的导航里会多出一个点了没反应的「预约」。
  const shown = blocks.filter((el) => !el.hidden);
  list.innerHTML = shown.map((el) => `
    <a class="zone-nav-item" href="#${escapeHtml(el.id)}"
       data-nav-target="${escapeHtml(el.id)}">${escapeHtml(tx(el.dataset.nav, el.dataset.nav))}</a>`).join('');
  nav.hidden = !shown.length;
  updateActiveNav();
}

/** 滚到某一页块：平滑滚动 + 留出吸顶导航的高度（不然目标会被顶栏挡住） */
function scrollToNavBlock(id) {
  const el = document.getElementById(id);
  if (!el) return;
  const top = Math.max(0, Math.round(el.getBoundingClientRect().top + window.scrollY - navOffset()));
  window.scrollTo({ top, behavior: 'smooth' });
}

/**
 * ★ scroll-spy：按**同一份 DOM 顺序**算出"现在读到哪一块了"，给导航项加 .active。
 *   · 判据：块顶已经越过"吸顶导航下沿"的那些里，最靠下的那一个就是当前块；
 *   · 都没越过 → 高亮第一项（客户在首屏，还没进第一块）；
 *   · 滚到页面最底部 → 高亮最后一块（最后一块可能永远越不过那条线，得单独判）。
 */
function updateActiveNav() {
  const list = $('#zoneNavList');
  if (!list || !navVisible()) return;
  const blocks = navBlocks().filter((el) => !el.hidden);
  if (!blocks.length) return;
  const line = navOffset() + 8;
  // 文档坐标（滚动时不会变），用它比"越过线没有"最稳
  const tops = blocks.map((el) => el.getBoundingClientRect().top + window.scrollY);
  let idx = 0;
  for (let i = 0; i < tops.length; i++) {
    if (tops[i] - window.scrollY <= line) idx = i;
  }
  const atBottom = window.innerHeight + window.scrollY >= document.documentElement.scrollHeight - 4;
  if (atBottom) idx = blocks.length - 1;
  $$('#zoneNavList .zone-nav-item').forEach((a, i) => a.classList.toggle('active', i === idx));
}

function bindNav() {
  const nav = $('#zoneNav');
  const list = $('#zoneNavList');
  if (!nav || !list) return;
  // ① 点击：平滑滚动到那一块（并且立刻把高亮切过去，别等滚动结束才反应）
  list.addEventListener('click', (e) => {
    const a = e.target.closest('.zone-nav-item');
    if (!a) return;
    e.preventDefault();
    const id = a.dataset.navTarget || (a.getAttribute('href') || '').slice(1);
    $$('#zoneNavList .zone-nav-item').forEach((x) => x.classList.toggle('active', x === a));
    scrollToNavBlock(id);
  });
  // ② 滚动：高亮"当前读到哪一块"（rAF 节流，滚动时不做多余计算）
  let ticking = false;
  const onScroll = () => {
    if (ticking) return;
    ticking = true;
    requestAnimationFrame(() => { ticking = false; updateActiveNav(); });
  };
  window.addEventListener('scroll', onScroll, { passive: true });
  // ③ 窗口尺寸变了：导航可能"出现 / 消失"，重画一次（也顺手对齐高亮）
  window.addEventListener('resize', () => {
    list.querySelectorAll('.zone-nav-item').forEach((a) => a.classList.remove('active'));
    updateActiveNav();
  });
  updateActiveNav();
}

/* ==========================================================================
   ★ 档位卡下面的小圆点（2026-10-16 手机端改造）
   --------------------------------------------------------------------------
   手机上三张档位卡从"竖着排"改成"横向可滑、滑一下正好停一张"（CSS 里的 scroll-snap）。
   但光靠右边露出来的那条边，客户不一定知道一共几张 —— 圆点负责把这件事说清楚。

   ★ 两条和导航一样的铁律：
     · **圆点绝不手写**：数量 = #priceCards 里真实的卡片数（后台加一档，圆点自己多一个）；
     · 只剩 1 张卡时**一个圆点都不显示**（CSS 里 .cards > .card:only-child 让卡片铺满）。
   ⚠️ 圆点只在手机上显示（CSS 里 @media (min-width:1360px) 之外才 display:flex），
      电脑端那一排三张本来就一眼看全，不需要圆点 ✗
   ========================================================================== */

/** 档位卡的滑动容器（就是 #priceCards 自己，mobile 下 CSS 把它变成横向滚动条） */
function priceScroller() { return $('#priceCards'); }

function renderPriceDots() {
  const box = $('#priceDots');
  const scroller = priceScroller();
  if (!box || !scroller) return;
  const cards = $$('#priceCards > .card');
  // 一张卡（或空状态）时不显示圆点 —— 下面是空盒子，CSS 也不给它占位
  box.innerHTML = cards.length < 2 ? '' : cards.map((card, i) => {
    // 圆点上的说明文字来自文案表：后台「文案 → 分区页」能改，
    // {n} = 第几张，{name} = 这一档叫什么（取卡片里的 h3）
    const name = (card.querySelector('h3') || {}).textContent || '';
    const label = tx('priceDotLabel', '第 {n} 张：{name}', { n: i + 1, name: name.trim() });
    return `<button type="button" class="price-dot" data-dot="${i}"
      aria-label="${escapeHtml(label)}" title="${escapeHtml(label)}"></button>`;
  }).join('');
  updateActiveDot();
}

/** 把"现在滑到第几张"同步到圆点（高亮那一个） */
function updateActiveDot() {
  const box = $('#priceDots');
  const scroller = priceScroller();
  if (!box || !scroller) return;
  const cards = $$('#priceCards > .card');
  if (cards.length < 2) { box.hidden = true; return; }
  // 判据：卡片的左边缘离"容器的左内边距"最近的那一张，就是当前这张
  //（用真实测量的位置，不靠 index 猜 —— 手指滑到哪算哪 ✓）
  const base = scroller.getBoundingClientRect().left;
  let idx = 0;
  let best = Infinity;
  cards.forEach((card, i) => {
    const d = Math.abs(card.getBoundingClientRect().left - base);
    if (d < best) { best = d; idx = i; }
  });
  $$('#priceDots .price-dot').forEach((b, i) => b.classList.toggle('active', i === idx));
}

function bindPriceDots() {
  const box = $('#priceDots');
  const scroller = priceScroller();
  if (!box || !scroller) return;
  // ① 滑动时跟着变（rAF 节流，滚动过程中不做多余计算）
  let ticking = false;
  scroller.addEventListener('scroll', () => {
    if (ticking) return;
    ticking = true;
    requestAnimationFrame(() => { ticking = false; updateActiveDot(); });
  }, { passive: true });
  // ② 点圆点也能切到那一张（可选功能，做了更顺手）
  box.addEventListener('click', (e) => {
    const b = e.target.closest('.price-dot');
    if (!b) return;
    const card = $$('#priceCards > .card')[Number(b.dataset.dot)];
    if (!card) return;
    // ★ 这里只滚**容器自己**（scrollLeft），绝不碰 window.scrollTo ✗
    //   否则客户点一下圆点，整页会跳到别处去
    scroller.scrollTo({ left: scroller.scrollLeft + (card.getBoundingClientRect().left - scroller.getBoundingClientRect().left), behavior: 'smooth' });
  });
  // ③ 窗口尺寸变了（横屏 / 拉宽）：卡片宽度变了，当前是第几张得重算
  window.addEventListener('resize', () => updateActiveDot());
}

/* ---------- 时间表（只有预约制分区会跑到这里） ---------- */
function slotsByDay() {
  const days = [];
  for (const slot of state.slots) {
    const key = dateLabel(slot.start);
    let day = days.find((d) => d.label === key);
    if (!day) { day = { label: key, start: slot.start, slots: [] }; days.push(day); }
    day.slots.push(slot);
  }
  return days;
}

function statusText(status) {
  // 格子上写的字（图例那几句是另外一组 key，两句可以不一样长）
  return {
    free: tx('slotFree', '可预约'), booking: tx('slotBooking', '已预约'),
    rest: tx('slotRest', '休息'), past: tx('slotPast', '已过去'),
    closed: tx('slotClosed', '停接'),
  }[status] || status;
}

function renderSchedule() {
  const days = slotsByDay();
  if (!days.length) return;
  if (state.dayIndex >= days.length) state.dayIndex = 0;

  $('#dayTabs').innerHTML = days.map((day, i) => {
    const free = day.slots.filter((s) => s.status === 'free').length;
    return `<div class="day-tab ${i === state.dayIndex ? 'active' : ''}" data-day="${i}">
      ${day.label}<b>${free} 个空闲</b></div>`;
  }).join('');

  const day = days[state.dayIndex];
  const freeCount = day.slots.filter((s) => s.status === 'free').length;
  $('#dayTitle').textContent = `${day.label} ${WEEK[cn(day.start).w]}`;
  $('#dayFree').textContent = freeCount ? `${freeCount} 个空闲时段` : '这一天已排满';

  $('#hourGrid').innerHTML = day.slots.map((slot) => {
    const selected = state.selected.has(slot.start);
    const clickable = slot.status === 'free';
    return `<button class="hour-cell ${slot.status} ${selected ? 'selected' : ''}"
      data-start="${slot.start}" ${clickable ? '' : 'disabled'}
      title="${fullLabel(slot.start)}">${hhmm(slot.start)}<small>${
        selected ? escapeHtml(tx('slotSelected', '已选')) : escapeHtml(statusText(slot.status))}</small></button>`;
  }).join('');
}

function renderSelection() {
  const hours = Array.from(state.selected).sort((a, b) => a - b);
  $('#selectedHours').textContent = hours.length;
  $('#selectedList').innerHTML = hours.map((h) =>
    `<span class="chip">${fullLabel(h)}<button data-remove="${h}" title="移除">×</button></span>`).join('');
  $('#nextStep').disabled = hours.length === 0;
  $('#syncState').textContent = state.syncedAt ? `已同步 ${hhmm(state.syncedAt)}` : '';
}

function toggleHour(start) {
  if (state.selected.has(start)) {
    state.selected.delete(start);
  } else {
    if (state.selected.size >= MAX_HOURS) return toast(`一次最多选择 ${MAX_HOURS} 小时`, true);
    state.selected.add(start);
  }
  $('#selectionHint').className = 'hint';
  $('#selectionHint').textContent = SELECTION_HINT();
  renderSchedule();
  renderSelection();
}

async function verifySelected() {
  const hours = Array.from(state.selected).sort((a, b) => a - b);
  if (!hours.length) return false;
  const data = await api('/api/availability?hours=' + hours.join(','));
  const bad = data.slots.filter((s) => s.status !== 'free');
  if (bad.length) {
    $('#selectionHint').className = 'hint error';
    $('#selectionHint').textContent =
      `这些时间已不可约：${bad.map((s) => fullLabel(s.start)).join('、')}，请移除后重新选择。`;
    return false;
  }
  return true;
}

/* 这里原本有个 buildMessage()：拼一段文案让客户复制发给我。
   已删除（2024 版）：客户在网页上复制一段话发给谁？—— 他还没加我微信，这一步走不通。
   现在改成：锁定成功后直接让他「加我微信」（二维码 / 复制微信号），
   订单信息由后端 booking_message() 拼好推给我，客户不用做任何复制动作。 */

/**
 * 「怎么加我」这句话 —— **两个弹窗共用这一份**。
 *
 * 为什么要抽出来：之前预约成功弹窗和「加微信下单」弹窗各写各的，
 * 结果同一个意思两处说法不一样（客户看到的指导语前后不一致，显得不专业）。
 *
 * ⚠️ 而且不要再假设"长按二维码一定能识别出来"：微信个人二维码是微信私有格式，
 *    微信里长按通常能识别，但版本/机型/图片压缩都可能让它认不出来。
 *    所以**微信内也要把"识别不出来就复制微信号"这条退路说清楚**。
 *
 * ★ 三种情况三句话，都在后台「文案」里能改（wechatAddQr / wechatAddNoQr / wechatAddBrowser）；
 *   返回的是**纯文本**（调用方用 textContent 放进去），不认 HTML 标签。
 *
 * @param {boolean} hasQr  有没有上传二维码
 * @param {string}  tail   加上好友之后要做什么（例如"把上面的时间发我一下。"）
 */
function wechatAddHint(hasQr, tail) {
  if (!IN_WECHAT) {
    // 微信以外的浏览器：长按基本识别不了，直接说唯一的可靠办法
    return tx('wechatAddBrowser', '若网页无法识别二维码，请直接复制我的微信并添加');
  }
  if (!hasQr) {
    return `${tx('wechatAddNoQr', '搜下面的微信号加我。')}${tail}`;
  }
  return `${tx('wechatAddQr', '长按二维码识别加我；识别不出来就点下面的「复制微信号」。')}${tail}`;
}


function currentContactType() {
  const el = $('#bookContactType');
  return el && CONTACT_TEXT[el.value] ? el.value : 'wechat';
}

function applyContactPlaceholder() {
  const [key, fallback] = CONTACT_PLACEHOLDER_KEYS[currentContactType()] || CONTACT_PLACEHOLDER_KEYS.wechat;
  $('#bookContact').placeholder = tx(key, fallback);
}

function renderModeModal() {
  const prices = (profile().prices) || [];
  if (state.modeIndex >= prices.length) state.modeIndex = 0;
  $('#modeCards').innerHTML = prices.map((it, i) => `
    <article class="card selectable ${i === state.modeIndex ? 'active' : ''}" data-mode="${i}">
      <div class="no">模式 / ${pad(i + 1)}</div>
      <h3>${escapeHtml(it.name)}</h3>
      <p>${escapeHtml(it.description || '')}</p>
      <div class="price">¥${Number(it.price) || 0}<small> / 小时</small></div>
    </article>`).join('')
    || `<div class="empty-state">${escapeHtml(tx('priceEmptyBooking', '这个分区还没有设置报价，去后台加一档。'))}</div>`;

  const price = prices[state.modeIndex];
  const total = state.selected.size;
  $('#estimate').textContent = price
    ? `已选 ${total} 小时 · 参考费用 ¥${((Number(price.price) || 0) * total).toFixed(2)}`
      + `（按实际游玩时间结算，一次最多 ${MAX_HOURS} 小时）`
    : '';
  applyContactPlaceholder();
}

/* ---------- 主路径：立即锁定时间 ---------- */
async function bookNow() {
  const price = (profile().prices || [])[state.modeIndex];
  const hours = Array.from(state.selected).sort((a, b) => a - b);
  if (!price) return toast('还没有设置报价，请先在后台添加。', true);
  if (!hours.length) return toast('请先选择时间', true);

  const contact = $('#bookContact').value.trim();
  const contactType = currentContactType();
  const box = $('#bookState');
  if (contact.length < 2 || contact.length > 40) {
    box.className = 'hint error';
    box.textContent = '请填写你的联系方式（微信 / 闲鱼 / QQ），2–40 个字符，不然我加不上你。';
    toast('请先填写联系方式', true);
    return;
  }

  const btn = $('#bookNow');
  btn.disabled = true;
  btn.textContent = '正在锁定…';
  box.className = 'hint';
  box.textContent = '';
  try {
    const data = await api('/api/book', {
      method: 'POST',
      body: JSON.stringify({
        zone: state.zone.id, hours, mode: price.name, contact, contactType,
        note: $('#bookNote').value.trim(),
      }),
    });
    state.selected.clear();
    await loadPublic();
    $('#modeModal').classList.remove('open');
    renderDone(data);
    // 弹窗里的「加我微信」区：有二维码就显示（微信里长按能识别），没有就只给微信号
    const qr = state.site.wechatQr || '';
    const qrImg = $('#doneQr');
    if (qrImg) {
      if (qr) { qrImg.src = qr; qrImg.hidden = false; } else { qrImg.hidden = true; qrImg.removeAttribute('src'); }
    }
    const wxId = state.site.wechat || '';
    const wxEl = $('#doneWechat');
    if (wxEl) wxEl.textContent = wxId || '（还没设置微信号）';
    const wxHint = $('#doneWechatHint');
    if (wxHint) {
      // 纯文本，所以用 textContent（见 wechatAddHint 的说明）
      wxHint.textContent = wechatAddHint(!!qr, tx('wechatAddTail', '加上之后，把上面的时间发我一下就行。'));
    }
    // 不在微信里时：二维码缩小 + 「复制微信号」变主按钮（那才是他能做成的事）
    const copyWxBtn = $('#copyDoneWechat');
    if (copyWxBtn) {
      copyWxBtn.classList.toggle('primary', !IN_WECHAT);
      copyWxBtn.classList.toggle('ghost', IN_WECHAT);
      copyWxBtn.textContent = IN_WECHAT ? '复制微信号' : '复制微信号，去微信加我';
    }
    const wxBox = document.querySelector('.done-wechat');
    if (wxBox) wxBox.classList.toggle('compact', !IN_WECHAT);
    const qrCap = $('#doneQrCaption');
    if (qrCap) qrCap.hidden = IN_WECHAT || !qr;
    $('#doneModal').classList.add('open');
    toast(tx('bookedToast', '时间已锁定，记得加我微信'));
  } catch (err) {
    handleBookError(err);
  } finally {
    btn.disabled = false;
    btn.textContent = '立即锁定时间';
  }
}

/** 预约失败时的提示：409 被抢走 / 429 太频繁 / 400 参数不对 / 其它（网络） */
function handleBookError(err) {
  const box = $('#bookState');
  const detail = err.data || {};
  if (err.status === 409) {
    (detail.conflicts || []).forEach((h) => state.selected.delete(h));
    renderSchedule();
    renderSelection();
    box.className = 'hint error';
    box.textContent = err.message;
    toast('有几段时间刚被别人约走了，请重新选择', true);
    syncStatuses();
    return;
  }
  if (err.status === 429 || err.status === 400) {
    box.className = 'hint error';
    box.textContent = err.message;
    toast(err.message, true);
    return;
  }
  toast(`${err.message}（可以再点一次「立即锁定时间」试试）`, true);
}

/** 锁定成功页 */
function renderDone(data) {
  $('#doneLocks').innerHTML = (data.locks || []).map((seg) => `
    <article class="record">
      <span class="badge ok">已锁定</span>
      <div class="grow">
        <strong>${fullLabel(seg.start)} – ${hhmm(seg.end)}</strong>
        <p>${seg.hours} 小时 · ¥${seg.price}</p>
      </div>
    </article>`).join('');
  $('#doneSummary').innerHTML =
    `项目：<b>${escapeHtml(state.zone.name || '')}</b> · 模式：<b>${escapeHtml(data.mode)}</b>`
    + ` · 共 <b>${data.totalHours}</b> 小时 · 合计 <b>¥${data.totalPrice}</b>`
    + ` · 联系方式：<b>${CONTACT_TEXT[data.contactType] || '微信'} ${escapeHtml(data.contact || '')}</b>`;
  // 这一句（"时间预约成功Ciallo～(∠・ω< )⌒★"）以前写死在代码里，现在在后台「文案」里能改
  $('#doneNote').textContent = tx('doneNote', '时间预约成功Ciallo～(∠・ω< )⌒★');
}

/* ==========================================================================
   展示墙（只显示这个分区的图）
   --------------------------------------------------------------------------
   两条规矩（第一刀加的）：
     · **分类可配置**：筛选按钮不是写死的，而是按 /api/public 返回的 galleryCategories
       现场生成（后台「展示墙 → 分类管理」里加一个分类，这里就多一个按钮）。
       万一某张图的分类不在配置里（老数据），下面会把它的 id 兜底补一个按钮出来 ——
       宁可多一个按钮，也不能让客户看不到你的图。
     · **有外链的图点开是跳转**（新窗口打开 B 站视频 / 你做的网站），
       没外链的图才是点开放大看。这两件事用同一个点击事件区分，
       所以带链接的那张用 <a target="_blank" rel="noopener noreferrer">，
       而且**不给它 data-zoom** —— 不然会既跳转又弹放大层。
   ========================================================================== */

/** 分类 id → 中文名（认不出来就显示 id 本身，绝不返回空字符串） */
function categoryName(id) {
  const hit = (state.galleryCategories || []).find((c) => c.id === id);
  return hit ? hit.name : String(id || '');
}

/** 有外链时图上那个小角标。★ 这句话在后台「文案」里能改（galleryLinkBadge） */
function galleryBadge() { return tx('galleryLinkBadge', '点开看 ↗'); }

function renderGalleryFilters() {
  const box = $('#galleryFilters');
  if (!box) return;
  const cats = state.galleryCategories || [];
  // 「全部」永远在第一个（它在 HTML 里）；后面按配置补，选中的那个保持 active
  box.innerHTML = '<button class="filter' + (state.galleryFilter === 'all' ? ' active' : '')
    + '" data-cat="all">全部</button>'
    + cats.map((c) => `<button class="filter${state.galleryFilter === c.id ? ' active' : ''}"
        data-cat="${escapeHtml(c.id)}">${escapeHtml(c.name)}</button>`).join('');
  // 选中的分类被删掉了 → 悄悄回到「全部」，别让客户看到一片空白
  if (state.galleryFilter !== 'all' && !cats.some((c) => c.id === state.galleryFilter)) {
    state.galleryFilter = 'all';
    $$('#galleryFilters .filter').forEach((b) => b.classList.toggle('active', b.dataset.cat === 'all'));
  }
}

function renderGallery() {
  renderGalleryFilters();
  const items = state.gallery.filter((g) =>
    state.galleryFilter === 'all' || g.category === state.galleryFilter);
  $('#galleryGrid').innerHTML = items.length ? items.map((g) => `
    <article class="gallery-item">
      ${g.link
        // 有外链：整张图是一个新窗口打开的链接（点了去看视频/网站，不弹放大层）
        ? `<a class="gallery-link" href="${escapeHtml(g.link)}" target="_blank"
              rel="noopener noreferrer" title="${escapeHtml(g.link)}">
             <img src="${g.image}" alt="${escapeHtml(g.title)}" loading="lazy">
             <span class="gallery-link-badge">${escapeHtml(galleryBadge())}</span>
           </a>`
        // 没外链：保持原来的行为 —— 点一下放大看图
        : `<img src="${g.image}" alt="${escapeHtml(g.title)}" data-zoom="${g.image}" loading="lazy">`}
      <div class="meta">
        <h4>${escapeHtml(g.title)}</h4>
        <p>${escapeHtml(g.caption || '')}</p>
      </div>
    </article>`).join('')
    : `<div class="empty-state" style="grid-column:1/-1">${escapeHtml(tx('galleryEmpty',
      '这个分区的展示墙还在装修中，先来找我玩一局吧。'))}</div>`;
}

/* ---------- 加载 ---------- */
async function loadPublic(showToast = false) {
  const data = await api('/api/public?zone=' + encodeURIComponent(state.zone.id));
  state.site = data.site || state.site;
  state.nav = data.nav || state.nav;
  state.zone = data.zone || state.zone;
  state.slots = data.slots || [];
  state.gallery = data.gallery || [];
  state.galleryCategories = data.galleryCategories || [];   // ★ 分类（筛选按钮照它生成）
  state.syncedAt = data.serverTime;
  // 分区资料可能刚在后台改过（报价/价格/文案/图片位置），这里整页重画一次，保持和服务端一致
  renderTabs();
  renderTexts();       // ★ 文案先铺一遍，下面各块再用（顺序无所谓，但先铺更直观）
  renderHero();
  renderPrices();
  renderSteps();       // ★ 服务流程（档位卡下面，折叠块）
  renderFaq();         // ★ 常见问题（独立的 section，排在预约后面）
  renderPromises();
  if (isBooking()) {
    renderSchedule();
    renderSelection();
  }
  renderGallery();
  renderNav();         // ★ 左侧导航：块都画完了再收集，免得漏掉刚显示出来的那一块
  if (showToast) toast('时间表已刷新');
}

async function syncStatuses() {
  try {
    const data = await api('/api/public?zone=' + encodeURIComponent(state.zone.id));
    state.slots = data.slots || [];
    state.syncedAt = data.serverTime;
    renderSchedule();
    renderSelection();
    const map = new Map(state.slots.map((s) => [s.start, s.status]));
    const bad = Array.from(state.selected).filter((h) => map.get(h) && map.get(h) !== 'free');
    if (bad.length) {
      $('#selectionHint').className = 'hint error';
      $('#selectionHint').textContent =
        `注意：${bad.map(fullLabel).join('、')} 刚刚已被占用或已过去，请移除后重新选择。`;
    }
  } catch {
    /* 静默失败，下一次轮询再试 */
  }
}

const isBooking = () => (state.zone.kind || 'booking') === 'booking';

/** 按分区类型决定显示哪一块价格区、要不要时间表 */
function renderPrices() {
  const booking = isBooking();
  $('#bookingPriceBlock').hidden = !booking;
  $('#servicePriceBlock').hidden = booking;
  $('#schedule').hidden = !booking;
  $('#anchorSchedule').hidden = !booking;
  $('#anchorPrice').textContent = booking ? '报价' : '价目';
  if (booking) renderPriceCards();
  else renderServicePrices();
}

/* ---------- 事件 ---------- */
function bindEvents() {
  // ⚠️ 服务制分区（代肝/剪辑…）没有时间表那一块，这些元素不存在，
  //    所以时间表相关的事件只在"有这块 HTML"的时候才绑定。
  const scheduleReady = !!$('#hourGrid');
  if (scheduleReady) {
    $('#dayTabs').addEventListener('click', (e) => {
      const tab = e.target.closest('.day-tab');
      if (!tab) return;
      state.dayIndex = Number(tab.dataset.day);
      renderSchedule();
    });

    $('#hourGrid').addEventListener('click', (e) => {
      const cell = e.target.closest('.hour-cell');
      if (!cell || cell.disabled) return;
      toggleHour(Number(cell.dataset.start));
    });

    $('#selectedList').addEventListener('click', (e) => {
      const btn = e.target.closest('[data-remove]');
      if (!btn) return;
      state.selected.delete(Number(btn.dataset.remove));
      renderSchedule();
      renderSelection();
    });

    $('#clearSelection').addEventListener('click', () => {
      state.selected.clear();
      renderSchedule();
      renderSelection();
    });

    $('#refreshSlots').addEventListener('click',
      () => loadPublic(true).catch((e) => toast(e.message, true)));

    $('#nextStep').addEventListener('click', async () => {
      const btn = $('#nextStep');
      btn.disabled = true;
      btn.textContent = '核对空档…';
      try {
        if (await verifySelected()) {
          renderModeModal();
          $('#modeModal').classList.add('open');
        }
      } catch (err) {
        toast(err.message, true);
      } finally {
        btn.disabled = state.selected.size === 0;
        btn.textContent = '下一步：选择模式';
      }
    });

    $('#modeCards').addEventListener('click', (e) => {
      const card = e.target.closest('[data-mode]');
      if (!card) return;
      state.modeIndex = Number(card.dataset.mode);
      renderModeModal();
    });

    $('#bookContactType').addEventListener('change', applyContactPlaceholder);

    $('#bookNow').addEventListener('click', bookNow);
    $('#closeModal').addEventListener('click', () => $('#modeModal').classList.remove('open'));
    $('#modeModal').addEventListener('click', (e) => {
      if (e.target === $('#modeModal')) $('#modeModal').classList.remove('open');
    });

    $('#copyDoneWechat').addEventListener('click',
      () => copyText(state.site.wechat || '', `微信号已复制：${state.site.wechat || ''}`));
    $('#closeDone').addEventListener('click', () => $('#doneModal').classList.remove('open'));
    $('#doneModal').addEventListener('click', (e) => {
      if (e.target === $('#doneModal')) $('#doneModal').classList.remove('open');
    });
  }

  // ★ 档位卡的小圆点：绑定和"有没有卡"无关（#priceDots 这个盒子永远在），
  //   服务制分区那一路 #priceCards 是空的 → renderPriceDots 早退，圆点一个都不出现 ✓
  bindPriceDots();

  $('#galleryFilters').addEventListener('click', (e) => {
    const btn = e.target.closest('.filter');
    if (!btn) return;
    state.galleryFilter = btn.dataset.cat;
    $$('#galleryFilters .filter').forEach((b) => b.classList.toggle('active', b === btn));
    renderGallery();
  });

  // 点图放大（展示墙的图 + 价目表长图）
  document.addEventListener('click', (e) => {
    const img = e.target.closest('[data-zoom]');
    if (!img) return;
    const src = img.dataset.zoom || img.getAttribute('src') || '';
    if (!src) return;
    $('#imageModalImg').src = src;
    $('#imageModal').classList.add('open');
  });
  $('#closeImage').addEventListener('click', () => $('#imageModal').classList.remove('open'));
  $('#imageModal').addEventListener('click', (e) => {
    if (e.target === $('#imageModal')) $('#imageModal').classList.remove('open');
  });


  const copyWechat = () => {
    if (!state.site.wechat) return toast('还没设置微信号，请先在后台填写。', true);
    copyText(state.site.wechat, `微信号已复制：${state.site.wechat}`);
  };
  $('#copyWechat').addEventListener('click', copyWechat);
  $('#footerWechat').addEventListener('click', copyWechat);
  // 首屏主按钮现在两种分区都是锚点跳转（#schedule / #servicePriceBlock），交给浏览器处理 ✓

  $('#selectAllCopy').addEventListener('click', () => {
    const box = $('#copyTextBox');
    box.focus();
    box.select();
    box.setSelectionRange(0, box.value.length);
  });
  $('#closeCopyModal').addEventListener('click', () => $('#copyModal').classList.remove('open'));
  $('#copyModal').addEventListener('click', (e) => {
    if (e.target === $('#copyModal')) $('#copyModal').classList.remove('open');
  });
}

/* ---------- 启动 ---------- */
(async function main() {
  bindEvents();
  bindNav();           // ★ 左侧导航（只在宽屏上看得见；窄屏是隐藏的，绑了也不碍事）
  renderTabs();
  renderTexts();       // ★ 文案（默认值 + 后台改过的）先铺一遍
  renderHero();
  renderPrices();
  renderSteps();       // ★ 服务流程（档位卡那一块里，紧跟价格，默认收起）
  renderFaq();         // ★ 常见问题（独立的一块 —— 预约后面、展示墙前面）
  renderPromises();
  renderGallery();
  renderNav();
  try {
    await loadPublic();
  } catch (err) {
    toast(err.message, true);
  }
  // 预约制分区每 30 秒同步一次，别人抢了你的时间你能立刻看到
  setInterval(() => {
    if (!document.hidden && isBooking()) syncStatuses();
  }, 30000);
})();
