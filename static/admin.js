/* ==========================================================================
   管理后台逻辑
   四个标签页：预约与时间 / 资料与报价 / 展示墙 / 安全与备份
   ========================================================================== */

const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));

const CN_OFFSET = 8 * 3600 * 1000;
const HOUR = 3600 * 1000;
const DAY = 24 * HOUR;
const WEEK = ['周日', '周一', '周二', '周三', '周四', '周五', '周六'];
const pad = (n) => String(n).padStart(2, '0');

const state = {
  profile: null,
  revision: 1,
  locks: [],
  rules: [],
  gallery: [],
  publicSlots: [],
  lastExportAt: 0,
  date: null,          // 当前查看的日期（毫秒，北京时间 00:00）
  batch: new Set(),    // 多选批量锁定的整点
  editingLockId: null,
  editingRuleId: null,
  showArchived: false,
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
  state.locks = admin.schedule.locks;
  state.rules = admin.schedule.rules;
  state.gallery = admin.gallery;
  state.publicSlots = pub.slots;
  if (!state.date) state.date = midnightOf(pub.serverTime);
  state.batch.clear();
  renderAll();
}

async function loadSchedule() {
  const data = await api('/api/admin');
  state.locks = data.schedule.locks;
  state.rules = data.schedule.rules;
  renderStats();
  renderAdminGrid();
  renderLockList();
  renderRuleList();
}

function renderAll() {
  renderStats();
  renderAdminGrid();
  renderLockList();
  renderRuleList();
  fillProfileForm();
  renderGallery();
  renderBackupState();
  $('#adminWho').textContent = `已登录 · ${state.profile.name || ''}`;
  $('#adminTitle').textContent = `${state.profile.name || ''} 的管理终端`;
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
    <div class="stat"><span>今日可预约</span><strong>${free}<small>小时</small></strong></div>
    <div class="stat"><span>启用中的休息规则</span><strong>${activeRules}<small>条</small></strong></div>
    <div class="stat"><span>展示墙已公开</span><strong>${state.gallery.filter((g) => g.visible && !g.archived).length}<small>条</small></strong></div>`;
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

function renderLockList() {
  const dayEnd = state.date + DAY;
  const items = state.locks.filter((l) => l.start < dayEnd && state.date < l.end);
  $('#lockList').innerHTML = items.length ? items.map((l) => {
    const label = `${titleOf(l.start)} ${timeLabel(cnParts(l.start).h)} – ${
      l.end - midnightOf(l.end) === 0 && cnParts(l.end).h === 0
        ? timeLabel(24) : timeLabel(cnParts(l.end).h)}`;
    return `<article class="record">
      <span class="badge ${l.kind === 'booking' ? 'warn' : ''}">${l.kind === 'booking' ? '预约' : '休息'}</span>
      <div class="grow">
        <strong>${escapeHtml(label)}</strong>
        <p>${escapeHtml(l.client ? '老板：' + l.client : '未填写称呼')}${
          l.note ? ' · ' + escapeHtml(l.note) : ''}</p>
      </div>
      <button class="button small ghost" data-edit-lock="${l.id}">编辑</button>
      <button class="button small danger" data-del-lock="${l.id}">取消锁定</button>
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

function setImagePreview(key, url) {
  const el = $('#' + key + 'Preview');
  if (el) el.src = url || '';
}

/* ---------- 资料与报价 ---------- */
function fillProfileForm() {
  const p = state.profile;
  $('#pName').value = p.name || '';
  $('#pTagline').value = p.tagline || '';
  $('#pPlatform').value = p.platform || '';
  $('#pWechat').value = p.wechat || '';
  $('#pXianyu').value = p.xianyu || '';
  $('#pHeadline').value = p.headline || '';
  $('#pBio').value = p.bio || '';
  $('#pExperience').value = p.experience || '';
  $('#pStyles').value = (p.styles || []).join('，');
  $('#pAnnouncement').value = p.announcement || '';
  $('#pAccepting').checked = !!p.accepting;
  $('#pPromises').value = (p.promises || []).join('\n');
  // 三张图 + 背景压暗
  setImagePreview('avatar', p.avatar);
  setImagePreview('background', p.background);
  setImagePreview('heroImage', p.heroImage);
  const dim = Number.isFinite(Number(p.backgroundDim)) ? Number(p.backgroundDim) : 72;
  $('#pBackgroundDim').value = String(dim);
  $('#dimValue').textContent = String(dim);
  renderPriceEditor();
}

function renderPriceEditor() {
  const prices = state.profile.prices || [];
  $('#priceEditor').innerHTML = prices.map((it, i) => `
    <div class="grid-2" style="margin-bottom:10px;align-items:end">
      <div class="field" style="margin:0"><label>名称</label>
        <input data-price="${i}" data-key="name" value="${escapeHtml(it.name)}" maxlength="30"></div>
      <div class="field" style="margin:0"><label>说明</label>
        <input data-price="${i}" data-key="description" value="${escapeHtml(it.description || '')}" maxlength="60"></div>
      <div class="field" style="margin:0"><label>每小时价格（元）</label>
        <input data-price="${i}" data-key="price" type="number" min="0" step="1" value="${Number(it.price) || 0}"></div>
      <div><button class="button small danger" data-del-price="${i}">删除这一档</button></div>
    </div>`).join('') || '<div class="empty-state">还没有报价档位。</div>';
}

function collectProfile() {
  const prices = (state.profile.prices || []).map((it, i) => {
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
  return {
    ...state.profile,
    name: $('#pName').value.trim(),
    tagline: $('#pTagline').value.trim(),
    platform: $('#pPlatform').value.trim(),
    wechat: $('#pWechat').value.trim(),
    xianyu: $('#pXianyu').value.trim(),
    headline: $('#pHeadline').value,
    bio: $('#pBio').value,
    experience: $('#pExperience').value,
    styles: $('#pStyles').value.split(/[，,]/).map((s) => s.trim()).filter(Boolean),
    announcement: $('#pAnnouncement').value.trim(),
    accepting: $('#pAccepting').checked,
    backgroundDim: Number($('#pBackgroundDim').value) || 0,
    promises: $('#pPromises').value.split('\n').map((s) => s.trim()).filter(Boolean),
    prices,
  };
}

/* ---------- 三张图的上传与清除 ---------- */
const IMAGE_FIELDS = {
  avatar: { input: '#avatarFile', max: 800, label: '头像' },
  background: { input: '#backgroundFile', max: 1920, label: '主页背景图' },
  heroImage: { input: '#heroImageFile', max: 1200, label: '首屏图片' },
};

Object.entries(IMAGE_FIELDS).forEach(([key, cfg]) => {
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
      stateEl.className = 'hint ok';
      stateEl.textContent = `${cfg.label}已上传，记得点下面的「保存全部资料」`;
      toast(`${cfg.label}已上传`);
    } catch (err) {
      stateEl.className = 'hint error';
      stateEl.textContent = err.message;
      toast(err.message, true);
    } finally {
      e.target.value = '';      // 允许连续选同一个文件
    }
  });
});

$$('[data-clear-image]').forEach((btn) => {
  btn.addEventListener('click', () => {
    const key = btn.dataset.clearImage;
    state.profile[key] = '';
    setImagePreview(key, '');
    $('#uploadState').className = 'hint';
    $('#uploadState').textContent = '已清除，记得点「保存全部资料」';
  });
});

$('#pBackgroundDim').addEventListener('input', (e) => {
  $('#dimValue').textContent = e.target.value;
});

/* ---------- 展示墙 ---------- */
function renderGallery() {
  const items = state.gallery.filter((g) => state.showArchived || !g.archived);
  $('#galleryList').innerHTML = items.length ? items.map((g) => `
    <article class="record">
      <img class="thumb" src="${g.image}" alt="">
      <div class="grow">
        <strong>${escapeHtml(g.title)}</strong>
        <p>${escapeHtml(g.caption || '（无说明）')}</p>
        <p>${g.category === 'review' ? '老板评价' : '游戏截图'} · ${
          g.visible ? '首页公开' : '已隐藏'}${g.archived ? ' · 已归档' : ''}</p>
      </div>
      <button class="button small ghost" data-vis="${g.id}" data-value="${g.visible ? 0 : 1}">
        ${g.visible ? '隐藏' : '公开'}</button>
      <button class="button small ghost" data-arc="${g.id}" data-value="${g.archived ? 0 : 1}">
        ${g.archived ? '恢复' : '归档'}</button>
      <button class="button small danger" data-del-gal="${g.id}">删除</button>
    </article>`).join('') : '<div class="empty-state">还没有展示内容，上传你的第一张截图吧。</div>';
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
    $$('.tab').forEach((t) => t.classList.toggle('active', t === tab));
    $$('[data-panel]').forEach((p) => {
      p.style.display = p.dataset.panel === tab.dataset.tab ? '' : 'none';
    });
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
  const del = e.target.closest('[data-del-lock]');
  if (!del) return;
  if (!confirm('取消后这段时间会重新开放，确定吗？')) return;
  await api('/api/admin/schedule', {
    method: 'POST', body: JSON.stringify({ action: 'remove-lock', id: del.dataset.delLock }),
  });
  await loadSchedule();
  await refreshPublicSlots();
  toast('已取消锁定');
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

/* --- 资料 --- */
$('#priceEditor').addEventListener('click', (e) => {
  const del = e.target.closest('[data-del-price]');
  if (!del) return;
  state.profile = collectProfile();
  state.profile.prices.splice(Number(del.dataset.delPrice), 1);
  renderPriceEditor();
});

$('#priceEditor').addEventListener('input', () => {
  state.profile = collectProfile();   // 先把当前输入存进内存，重绘才不会丢
});

$('#addPrice').addEventListener('click', () => {
  state.profile = collectProfile();
  state.profile.prices = state.profile.prices || [];
  state.profile.prices.push({ id: `p${Date.now()}`, name: '新档位', description: '', price: 20 });
  renderPriceEditor();
});

$('#saveProfile').addEventListener('click', async () => {
  const btn = $('#saveProfile');
  btn.disabled = true;
  try {
    const data = await api('/api/admin/profile', {
      method: 'PUT',
      body: JSON.stringify({ profile: collectProfile(), revision: state.revision }),
    });
    state.profile = data.profile;
    state.revision = data.profileRevision;
    fillProfileForm();
    renderStats();
    $('#profileState').textContent = `已保存（版本 ${state.revision}）`;
    toast('资料与价格已保存');
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

$('#saveGallery').addEventListener('click', async () => {
  const file = $('#gFile').files[0];
  const btn = $('#saveGallery');
  if (!file) return toast('请先选择图片', true);
  if (!$('#gTitle').value.trim()) return toast('请填写标题', true);
  btn.disabled = true;
  $('#galleryHint').textContent = '正在处理并上传…';
  try {
    const dataUrl = await compressImage(file);
    const data = await api('/api/admin/gallery', {
      method: 'POST',
      body: JSON.stringify({
        title: $('#gTitle').value.trim(),
        caption: $('#gCaption').value.trim(),
        category: $('#gCategory').value,
        visible: $('#gVisible').checked,
        image: dataUrl,
      }),
    });
    state.gallery = data.gallery;
    $('#gTitle').value = '';
    $('#gCaption').value = '';
    $('#gFile').value = '';
    renderGallery();
    renderStats();
    toast('展示已保存，图片上传成功');
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
  try {
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
    if (!confirm(`将要恢复：\n· 预约/休息时段 ${locks} 条\n· 固定休息规则 ${rules} 条\n· 展示墙 ${gallery} 条\n\n会覆盖现在的全部数据，确定吗？`)) return;

    const data = await api('/api/admin/import', { method: 'POST', body: JSON.stringify({ backup }) });
    state.profile = data.profile;
    state.revision = data.profileRevision;
    state.locks = data.schedule.locks;
    state.rules = data.schedule.rules;
    state.gallery = data.gallery;
    state.batch.clear();
    renderAll();
    resetLockForm();
    $('#importState').textContent =
      `已恢复：时段 ${data.summary.locks} 条、规则 ${data.summary.rules} 条、展示 ${data.summary.gallery} 条（含图片 ${data.summary.images} 张）`;
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
