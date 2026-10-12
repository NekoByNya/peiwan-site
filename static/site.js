/* ==========================================================================
   首页逻辑：读接口 → 渲染 → 选时间 → 生成预约文案
   没有框架、没有打包，浏览器直接跑
   ========================================================================== */

const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));

const CN_OFFSET = 8 * 3600 * 1000;   // 北京时间 = UTC+8
const HOUR = 3600 * 1000;
const MAX_HOURS = 6;                 // 一次最多能选几小时（和后端 MAX_BOOK_HOURS 保持一致）

// 联系方式渠道：文案里、后台里都用这三个中文写法
const CONTACT_TEXT = { wechat: '微信', xianyu: '闲鱼', qq: 'QQ' };
const CONTACT_PLACEHOLDER = {
  wechat: '你的微信号',
  xianyu: '你的闲鱼用户名',
  qq: '你的 QQ 号',
};

// 选时间那一行的默认提示语（被抢单/提示后要还原成这句，所以抽出来）
const SELECTION_HINT = `先点选想玩的时间，连续或分开都可以（一次最多 ${MAX_HOURS} 小时）。`;

const state = {
  profile: null,
  slots: [],
  gallery: [],
  selected: new Set(),   // 已选中的整点时间戳
  dayIndex: 0,
  galleryFilter: 'all',
  modeIndex: 0,
  syncedAt: 0,
};

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

/* ---------- 通用请求：把网络错误翻译成人话 ----------
   出错时会把 HTTP 状态码和响应内容挂在 Error 上（err.status / err.data），
   预约那一段要靠它区分"被抢走了 409 / 太频繁 429 / 参数不对 400"。 */
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

/**
 * 复制文字。三级兜底，因为客户多半是在微信内置浏览器里打开：
 *   1. navigator.clipboard —— 现代浏览器、HTTPS 环境最顺
 *   2. execCommand('copy') —— 老的 WebView 还能用这招
 *   3. 弹出文本框让客户长按手动复制 —— 前两招都不行时的最后保障
 */
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

/* ---------- 主页背景图：有就铺满并压暗，没有就干净底色 ---------- */
function applyBackground() {
  const url = state.profile.background;
  let bg = document.querySelector('.site-bg');
  if (!url) {
    if (bg) bg.remove();
    return;
  }
  if (!bg) {
    bg = document.createElement('div');
    bg.className = 'site-bg';
    document.body.prepend(bg);
  }
  bg.style.backgroundImage = `url("${url}")`;
  let dim = Number(state.profile.backgroundDim);
  if (!Number.isFinite(dim)) dim = 72;
  bg.style.setProperty('--dim', String(Math.min(90, Math.max(0, dim)) / 100));
}

/* ---------- 首屏图片：没上传就整块隐藏 ---------- */
function renderHeroMedia() {
  const url = state.profile.heroImage;
  const box = $('#heroMedia');
  box.hidden = !url;
  box.innerHTML = url
    ? `<img src="${url}" alt="${escapeHtml(state.profile.name)} 的游戏图">` : '';
  $('#heroGrid').classList.toggle('with-media', !!url);
}

/* ---------- 渲染：首屏与资料 ---------- */
function renderProfile() {
  const p = state.profile;
  document.title = `${p.name} · 个人陪玩预约`;
  $('#brandName').textContent = p.name;
  $('#heroTagline').textContent = p.tagline || '';
  $('#heroHeadline').textContent = p.headline || p.name;
  $('#heroSub').textContent = p.platform ? `${p.platform} · 个人直约 · 时间表空档为准` : '';
  $('#acceptingDot').classList.toggle('on', !!p.accepting);
  $('#acceptingText').textContent = p.accepting ? '开放预约' : '暂时休整';
  $('#announcement').textContent = p.announcement || '';
  // 微信号直接显示在页面上（微信里自动复制经常被拦，客户还能长按选中）
  const wechatLine = $('#wechatLine');
  if (p.wechat) {
    wechatLine.innerHTML = `微信号：<b id="wechatValue">${escapeHtml(p.wechat)}</b>`
      + '<span class="wechat-tip">长按可复制</span>';
    wechatLine.hidden = false;
  } else {
    wechatLine.hidden = true;
    wechatLine.textContent = '';
  }
  $('#footerName').textContent = `${p.name} · 个人陪玩主页`;
  applyBackground();
  renderHeroMedia();

  $('#priceCards').innerHTML = (p.prices || []).map((it, i) => `
    <article class="card">
      <div class="no">OPERATION / ${pad(i + 1)}</div>
      <h3>${escapeHtml(it.name)}</h3>
      <p>${escapeHtml(it.description || '')}</p>
      <div class="price">¥${Number(it.price) || 0}<small> / 小时</small></div>
    </article>`).join('') || '<div class="empty-state">还没有设置报价。</div>';

  $('#promiseList').innerHTML = (p.promises || [])
    .map((t) => `<li>${escapeHtml(t)}</li>`).join('') || '<li>还没有填写服务说明。</li>';

  $('#aboutName').textContent = p.name;
  $('#aboutPlatform').textContent = p.platform || '';
  $('#aboutBio').textContent = p.bio || '';
  $('#aboutExperience').textContent = p.experience || '';
  $('#styleTags').innerHTML = (p.styles || [])
    .map((t) => `<span class="tag">${escapeHtml(t)}</span>`).join('');

  $('#avatarBox').innerHTML = p.avatar
    ? `<img class="avatar" src="${p.avatar}" alt="${escapeHtml(p.name)} 的头像">`
    : `<div class="avatar empty">🎮</div>`;
}

function escapeHtml(text) {
  return String(text == null ? '' : text).replace(/[&<>"']/g, (c) => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]
  ));
}

/* ---------- 渲染：时间表 ---------- */
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
  $('#syncState').textContent = state.syncedAt
    ? `已同步 ${hhmm(state.syncedAt)}` : '';
}

function toggleHour(start) {
  if (state.selected.has(start)) {
    state.selected.delete(start);
  } else {
    if (state.selected.size >= MAX_HOURS) return toast(`一次最多选择 ${MAX_HOURS} 小时`, true);
    state.selected.add(start);
  }  $('#selectionHint').className = 'hint';
  $('#selectionHint').textContent = SELECTION_HINT;
  renderSchedule();
  renderSelection();
}

/* ---------- 空档核对：防止"刚被人抢先" ---------- */
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

/* ---------- 生成"只咨询、不锁定"的预约文案（次路径用） ---------- */
function buildMessage() {
  const p = state.profile;
  const price = (p.prices || [])[state.modeIndex] || { name: '待定', price: 0 };
  const hours = Array.from(state.selected).sort((a, b) => a - b);

  // 把连续的小时合并成"N 段"
  const segments = [];
  for (const h of hours) {
    const last = segments[segments.length - 1];
    if (last && h - last[last.length - 1] === HOUR) last.push(h);
    else segments.push([h]);
  }
  const total = hours.length;
  const fee = Number(((Number(price.price) || 0) * total).toFixed(2));
  const lines = segments.map((seg, i) =>
    `${i + 1}. ${fullLabel(seg[0])}–${hhmm(seg[seg.length - 1] + HOUR)}`).join('\n');

  const contact = $('#bookContact') ? $('#bookContact').value.trim() : '';
  const type = currentContactType();

  // 游戏名 / 平台都来自后台资料 —— 同一套代码可以开任何游戏的预约站，不用改代码
  const what = [p.game, p.platform].map((x) => (x || '').trim()).filter(Boolean).join(' ');
  const head = what ? `你好，${p.name}！想约${what}陪玩。` : `你好，${p.name}！我想约你的陪玩服务。`;
  return `${head}\n`
    + `模式：${price.name}\n`
    + `游玩时间（北京时间）：\n${lines}\n`
    + `共 ${segments.length} 段、${total} 小时，参考费用 ¥${fee}。\n`
    + (contact ? `我的${CONTACT_TEXT[type]}：${contact}\n` : '')
    + `（这段只是咨询，时间还没锁定，麻烦你确认后帮我锁上。）`;
}

/* ---------- 联系方式渠道 ---------- */
function currentContactType() {
  const el = $('#bookContactType');
  return el && CONTACT_TEXT[el.value] ? el.value : 'wechat';
}

function applyContactPlaceholder() {
  $('#bookContact').placeholder = CONTACT_PLACEHOLDER[currentContactType()] || '你的联系方式';
}

function renderModeModal() {
  const p = state.profile;
  const prices = p.prices || [];
  if (state.modeIndex >= prices.length) state.modeIndex = 0;
  $('#modeCards').innerHTML = prices.map((it, i) => `
    <article class="card selectable ${i === state.modeIndex ? 'active' : ''}" data-mode="${i}">
      <div class="no">OPERATION / ${pad(i + 1)}</div>
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
  $('#messageBox').value = buildMessage();
}

/* ---------- 主路径：立即锁定时间 ---------- */
async function bookNow() {
  const price = (state.profile.prices || [])[state.modeIndex];
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
        hours, mode: price.name, contact, contactType, note: $('#bookNote').value.trim(),
      }),
    });
    // 成功：清空选择、刷新时间表（那几格现在已经是「已预约」），再弹出成功页
    state.selected.clear();
    await loadPublic();
    $('#modeModal').classList.remove('open');
    renderDone(data);
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
    // 把已经被抢走的小时从选中列表里去掉，客户直接重新选就行
    (detail.conflicts || []).forEach((h) => state.selected.delete(h));
    renderSchedule();
    renderSelection();
    box.className = 'hint error';
    box.textContent = err.message;
    toast('有几段时间刚被别人约走了，请重新选择', true);
    syncStatuses();
    return;
  }
  if (err.status === 429) {
    box.className = 'hint error';
    box.textContent = err.message || '提交太频繁，请过几分钟再试。';
    toast('提交太频繁，请稍后再试', true);
    return;
  }
  if (err.status === 400) {
    box.className = 'hint error';
    box.textContent = err.message;
    toast(err.message, true);
    return;
  }
  toast(`${err.message}（可以再点一次「立即锁定时间」试试）`, true);
}

/** 锁定成功页：写清楚锁了哪几段、总价，并保留一键复制文案 */
function renderDone(data) {
  const p = state.profile || {};
  $('#doneLocks').innerHTML = (data.locks || []).map((seg) => `
    <article class="record">
      <span class="badge ok">已锁定</span>
      <div class="grow">
        <strong>${fullLabel(seg.start)} – ${hhmm(seg.end)}</strong>
        <p>${seg.hours} 小时 · ¥${seg.price}</p>
      </div>
    </article>`).join('');
  $('#doneSummary').innerHTML =
    `模式：<b>${escapeHtml(data.mode)}</b> · 共 <b>${data.totalHours}</b> 小时`
    + ` · 合计 <b>¥${data.totalPrice}</b> · 联系方式：<b>${CONTACT_TEXT[data.contactType] || '微信'} `
    + `${escapeHtml(data.contact || '')}</b>`;
  $('#doneNote').textContent = p.wechat
    ? `我已收到通知，会尽快加你确认（我的微信号：${p.wechat}）。`
    : '我已收到通知，会尽快加你确认。';
  $('#doneMessage').value = data.message || '';
}

/* ---------- 展示墙 ---------- */
function renderGallery() {
  const items = state.gallery.filter((g) =>
    state.galleryFilter === 'all' || g.category === state.galleryFilter);
  $('#galleryGrid').innerHTML = items.length ? items.map((g) => `
    <article class="gallery-item">
      <img src="${g.image}" alt="${escapeHtml(g.title)}" data-zoom="${g.image}">
      <div class="meta">
        <h4>${escapeHtml(g.title)}</h4>
        <p>${escapeHtml(g.caption || '')}</p>
      </div>
    </article>`).join('')
    : '<div class="empty-state" style="grid-column:1/-1">展示墙还在装修中，先来找我玩一局吧。</div>';
}

/* ---------- 加载与轮询 ---------- */
async function loadPublic(showToast = false) {
  const data = await api('/api/public');
  state.profile = data.profile;
  state.slots = data.slots;
  state.gallery = data.gallery;
  state.syncedAt = data.serverTime;
  renderProfile();
  renderSchedule();
  renderSelection();
  renderGallery();
  if (showToast) toast('时间表已刷新');
}

async function syncStatuses() {
  try {
    const data = await api('/api/public');
    state.profile = data.profile;
    state.slots = data.slots;
    state.syncedAt = data.serverTime;
    renderSchedule();
    renderSelection();
    // 已选时间如果被别人抢先，提示并标红
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

/* ---------- 事件绑定 ---------- */
function bindEvents() {
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

  $('#refreshSlots').addEventListener('click', () => loadPublic(true).catch((e) => toast(e.message, true)));

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

  // 换渠道 → 换输入框提示语；填联系方式 → 文案里也跟着更新
  $('#bookContactType').addEventListener('change', () => {
    applyContactPlaceholder();
    $('#messageBox').value = buildMessage();
  });
  $('#bookContact').addEventListener('input', () => {
    $('#messageBox').value = buildMessage();
  });

  // 主路径：立即锁定时间
  $('#bookNow').addEventListener('click', bookNow);

  // 次路径：只复制文案，不锁定（文案里会写明"时间还没锁定"）
  $('#copyMessage').addEventListener('click', () => copyText($('#messageBox').value, '咨询文案已复制，发给我即可'));
  $('#closeModal').addEventListener('click', () => $('#modeModal').classList.remove('open'));
  $('#modeModal').addEventListener('click', (e) => {
    if (e.target === $('#modeModal')) $('#modeModal').classList.remove('open');
  });

  // 锁定成功页
  $('#copyDoneMessage').addEventListener('click', () => copyText($('#doneMessage').value, '文案已复制，发给我即可'));
  $('#closeDone').addEventListener('click', () => $('#doneModal').classList.remove('open'));
  $('#doneModal').addEventListener('click', (e) => {
    if (e.target === $('#doneModal')) $('#doneModal').classList.remove('open');
  });

  $('#galleryFilters').addEventListener('click', (e) => {
    const btn = e.target.closest('.filter');
    if (!btn) return;
    state.galleryFilter = btn.dataset.cat;
    $$('#galleryFilters .filter').forEach((b) => b.classList.toggle('active', b === btn));
    renderGallery();
  });

  $('#galleryGrid').addEventListener('click', (e) => {
    const img = e.target.closest('[data-zoom]');
    if (!img) return;
    $('#imageModalImg').src = img.dataset.zoom;
    $('#imageModal').classList.add('open');
  });
  $('#closeImage').addEventListener('click', () => $('#imageModal').classList.remove('open'));

  $('#copyWechat').addEventListener('click', async () => {
    const wechat = state.profile && state.profile.wechat;
    if (!wechat) return toast('还没设置微信号，请先在后台填写。', true);
    copyText(wechat, `微信号已复制：${wechat}`);
  });

  // 复制兜底弹窗
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
  try {
    await loadPublic();
  } catch (err) {
    toast(err.message, true);
  }
  // 每 30 秒同步一次，别人抢了你的时间你能立刻看到
  setInterval(() => { if (!document.hidden) syncStatuses(); }, 30000);
})();
