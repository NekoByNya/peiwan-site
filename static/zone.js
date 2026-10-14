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
const CONTACT_PLACEHOLDER = {
  wechat: '你的微信号',
  xianyu: '你的闲鱼用户名',
  qq: '你的 QQ 号',
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

const SELECTION_HINT = () => `先点选想玩的时间，连续或分开都可以（一次最多 ${MAX_HOURS} 小时）。`;

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
}

/* ---------- 分区首屏 ---------- */
function applyBackground() {
  const p = state.zone.profile || {};
  const bg = $('#zoneHeroBg');
  if (!p.background) { bg.style.display = 'none'; return; }
  bg.style.display = '';
  bg.style.backgroundImage = `url("${p.background}")`;
  let dim = Number(p.backgroundDim);
  if (!Number.isFinite(dim)) dim = 72;
  bg.style.setProperty('--dim', String(Math.min(90, Math.max(0, dim)) / 100));
}

function renderHero() {
  const z = state.zone;
  const p = z.profile || {};
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
  $('#acceptingText').textContent = site.accepting ? '开放接单' : '暂时休整';

  const wechatLine = $('#wechatLine');
  if (site.wechat) {
    wechatLine.innerHTML = `微信号：<b id="wechatValue">${escapeHtml(site.wechat)}</b>`
      + (IN_WECHAT || typeof IN_WECHAT === 'undefined'
        ? '<span class="wechat-tip">长按可复制</span>'
        : '<span class="wechat-tip">点一下复制，去微信粘贴</span>');
    wechatLine.hidden = false;
  } else {
    wechatLine.hidden = true;
  }

  const hero = p.heroImage || '';
  $('#heroMedia').hidden = !hero;
  $('#heroMedia').innerHTML = hero
    ? `<img src="${hero}" alt="${escapeHtml(p.game || z.name)}">` : '';
  $('#heroGrid').classList.toggle('with-media', !!hero);

  // 首屏主按钮：预约制 → 看空档；服务制 → 看价目表。
  // ⚠️ 服务制以前写的是"加微信直接下单"，可旁边就有一个「加微信」按钮 ——
  //    两个按钮做同一件事，客户会懵。而且刚进页面的人最想知道的是"多少钱"，
  //    所以主按钮指向价目表，加微信交给旁边那个按钮。
  const primary = $('#heroPrimary');
  if (z.kind === 'booking') {
    primary.textContent = '看空档时间';
    primary.href = '#schedule';
  } else {
    primary.textContent = '看价目表';
    primary.href = '#servicePriceBlock';
  }
  primary.hidden = false;

  $('#zoneHero').classList.toggle('has-bg', !!p.background);
  applyBackground();

  $('#footerName').textContent = [site.brand || site.name, p.game || z.name]
    .filter(Boolean).join(' · ');
  $('#footerHint').textContent = z.kind === 'booking'
    ? '选好时间点一下就锁上了，我这边立刻收到消息。'
    : '还没想好要哪一档？加我问一句，说清需求和工期就行。';
  $('#footerZone').textContent = `当前页面：${z.name}（/z/${z.id}）`;

  // 页脚收尾区：二维码 + 微信号（读完这页的人，这里是他最后的出口）
  const fq = $('#footerQr');
  if (fq) {
    if (site.wechatQr) { fq.src = site.wechatQr; fq.hidden = false; }
    else { fq.hidden = true; fq.removeAttribute('src'); }
  }
  const fcap = $('#footerQrCaption');
  if (fcap) fcap.hidden = IN_WECHAT || !site.wechatQr;
  const fid = $('#footerWechatId');
  if (fid) fid.textContent = site.wechat || '（还没设置微信号）';
}

/* ---------- A. 预约制：三档价格 ---------- */
function renderPriceCards() {
  const prices = (state.zone.profile || {}).prices || [];
  $('#priceTitle').textContent = `玩的方式你选，陪伴我来。`;
  $('#priceCards').innerHTML = prices.map((it, i) => `
    <article class="card">
      <div class="no">模式 / ${pad(i + 1)}</div>
      <h3>${escapeHtml(it.name)}</h3>
      <p>${escapeHtml(it.description || '')}</p>
      <div class="price">¥${Number(it.price) || 0}<small> / 小时</small></div>
    </article>`).join('') || '<div class="empty-state">这个分区还没有设置报价，去后台加一档。</div>';
}

/* ---------- B. 服务制：主推档位 + 分组价目表 ---------- */
function allItems() {
  const groups = (state.zone.profile || {}).priceGroups || [];
  return groups.flatMap((g) => (g.items || []).map((it) => ({ ...it, group: g.name })));
}

function renderServicePrices() {
  const p = state.zone.profile || {};
  const groups = p.priceGroups || [];
  const items = allItems();
  const featuredIds = p.featured || [];
  // 主推：先按后台指定的顺序取，不够 3 个就顺手补"标了 ⭐ 的"
  let featured = featuredIds.map((id) => items.find((it) => it.id === id)).filter(Boolean);
  if (featured.length < 3) {
    const extra = items.filter((it) => it.hot && !featured.includes(it));
    featured = featured.concat(extra).slice(0, 3);
  }

  $('#serviceTitle').textContent = `${p.game || state.zone.name} · 价目表`;
  // 副标题那句小字来自「大类的小字」（后台 → 分区管理 → 大类那一行的第二个输入框），
  // 不再写死在 HTML 里 —— 想改成"包月 / 按量""按需报价"都行
  const hintEl = $('#serviceHint');
  if (hintEl) {
    const cat = (state.nav || []).find((g) => g.id === state.zone.category);
    const hint = (cat && cat.hint) || (state.zone.kind === 'booking' ? '按小时预约' : '按需报价');
    hintEl.textContent = `加微信直接下单 · ${hint}`;
  }
  $('#featuredTitle').textContent = featured.length ? '主推档位' : '';
  $('#featuredCards').innerHTML = featured.map((it) => `
    <article class="card featured-card">
      ${it.hot ? '<span class="badge hot">⭐ 推荐</span>' : ''}
      <div class="no">${escapeHtml(it.group || '')}</div>
      <h3>${escapeHtml(it.name)}</h3>
      <div class="price-text">${escapeHtml(it.price || '价格私聊')}</div>
      <p>${escapeHtml(it.note || '')}</p>
    </article>`).join('')
    || '<div class="empty-state">还没设置主推档位。去后台「分区管理 → 编辑 → 价目表」勾几个 ⭐ 主推。</div>';

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
            <td>${escapeHtml(it.name)}${it.hot ? ' <span class="badge hot">⭐</span>' : ''}</td>
            <td class="money">${escapeHtml(it.price || '—')}</td>
            <td>${escapeHtml(it.note || '')}</td>
          </tr>`).join('')}</tbody>
      </table>
    </details>`).join('')
    || '<div class="empty-state">还没有价目表。去后台「分区管理 → 编辑 → 价目表」加一组，或者用「批量粘贴导入」。</div>';

  const sheet = p.priceSheetImage || '';
  $('#priceSheetBox').hidden = !sheet;
  if (sheet) {
    $('#priceSheetImg').src = sheet;
    $('#priceSheetImg').dataset.zoom = sheet;
  }
  $('#priceSheetHint').hidden = !groups.length;
}

/* ---------- 服务承诺 ---------- */
function renderPromises() {
  const p = state.zone.profile || {};
  const booking = (state.zone.kind || 'booking') === 'booking';
  $('#promiseTitle').textContent = booking ? '下单前，聊清楚这几件事' : '服务说明';
  $('#promiseList').innerHTML = (p.promises || [])
    .map((t) => `<li>${escapeHtml(t)}</li>`).join('')
    || `<li>还没有填写${booking ? '服务承诺' : '服务说明'}，去后台这个分区的资料里补上。</li>`;
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
  return { free: '可预约', booking: '已预约', rest: '休息', past: '已过去', closed: '停接' }[status] || status;
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
        selected ? '已选' : statusText(slot.status)}</small></button>`;
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
 * @param {boolean} hasQr  有没有上传二维码
 * @param {string}  tail   加上好友之后要做什么（例如"把上面的时间发我一下。"）
 */
function wechatAddHint(hasQr, tail) {
  if (!IN_WECHAT) {
    // 微信以外的浏览器：长按基本识别不了，直接说唯一的可靠办法
    return '若网页无法识别二维码，请直接复制我的微信并添加';
  }
  if (!hasQr) {
    return `搜下面的微信号加我。${tail}`;
  }
  return `长按二维码识别加我；<b>识别不出来就点下面的「复制微信号」</b>。${tail}`;
}


function currentContactType() {
  const el = $('#bookContactType');
  return el && CONTACT_TEXT[el.value] ? el.value : 'wechat';
}

function applyContactPlaceholder() {
  $('#bookContact').placeholder = CONTACT_PLACEHOLDER[currentContactType()] || '你的联系方式';
}

function renderModeModal() {
  const prices = (state.zone.profile || {}).prices || [];
  if (state.modeIndex >= prices.length) state.modeIndex = 0;
  $('#modeCards').innerHTML = prices.map((it, i) => `
    <article class="card selectable ${i === state.modeIndex ? 'active' : ''}" data-mode="${i}">
      <div class="no">模式 / ${pad(i + 1)}</div>
      <h3>${escapeHtml(it.name)}</h3>
      <p>${escapeHtml(it.description || '')}</p>
      <div class="price">¥${Number(it.price) || 0}<small> / 小时</small></div>
    </article>`).join('') || '<div class="empty-state">还没有设置报价，请先在后台添加。</div>';

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
  const price = ((state.zone.profile || {}).prices || [])[state.modeIndex];
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
      wxHint.innerHTML = wechatAddHint(!!qr, '加上之后把时间发我一下。');
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
    toast('时间已锁定，记得把文案发给我');
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
  const site = state.site || {};
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
  // 这一句是主人自己定的文案（后台改不了，下次要换直接说）
  $('#doneNote').textContent = '时间预约成功Ciallo～(∠・ω< )⌒★';
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

/** 有外链时图上那个小角标。★ 文案想改就改这一处（比如改成「看视频 ↗」「看我做的站 ↗」） */
const GALLERY_LINK_BADGE = '点开看 ↗';

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
             <span class="gallery-link-badge">${GALLERY_LINK_BADGE}</span>
           </a>`
        // 没外链：保持原来的行为 —— 点一下放大看图
        : `<img src="${g.image}" alt="${escapeHtml(g.title)}" data-zoom="${g.image}" loading="lazy">`}
      <div class="meta">
        <h4>${escapeHtml(g.title)}</h4>
        <p>${escapeHtml(g.caption || '')}</p>
      </div>
    </article>`).join('')
    : '<div class="empty-state" style="grid-column:1/-1">这个分区的展示墙还在装修中，先来找我玩一局吧。</div>';
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
  // 分区资料可能刚在后台改过（报价/价格），这里整页重画一次，保持和服务端一致
  renderTabs();
  renderHero();
  renderPrices();
  renderPromises();
  if (isBooking()) {
    renderSchedule();
    renderSelection();
  }
  renderGallery();
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
  renderTabs();
  renderHero();
  renderPrices();
  renderPromises();
  renderGallery();
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
