/* ==========================================================================
   个人主页逻辑（/）—— 品牌 + 我能做什么 + 信任信号 + 加微信

   数据是服务端渲染时塞进 window.__PAGE__ 的（见 app.py 的 render_page），
   这里只负责把它画成卡片。分区页的交互在 zone.js 里，两边互不影响。
   ========================================================================== */

const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));

const PAGE = window.__PAGE__ || { site: {}, nav: [] };
const SITE = PAGE.site || {};

/* ---------- 客户在什么环境里打开的？----------
   微信个人二维码是微信私有格式，只有微信自己能解析成"加好友"：
   在微信里长按能识别，在别的浏览器里长按读不出可用结果。
   所以提示语要分环境说，否则等于让客户做一件他做不到的事。 */
const SITE_UA = navigator.userAgent || '';
const IN_WECHAT = /MicroMessenger/i.test(SITE_UA);

/* ---------- 小工具 ---------- */
function escapeHtml(text) {
  return String(text == null ? '' : text).replace(/[&<>"']/g, (c) => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]
  ));
}

let toastTimer = null;
function toast(message, isError = false) {
  const el = $('#toast');
  if (!el) return;
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

/* ---------- 首页背景图：有就铺满并压暗，没有就干净底色 ---------- */
function applyBackground() {
  const url = SITE.background;
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
  let dim = Number(SITE.backgroundDim);
  if (!Number.isFinite(dim)) dim = 72;
  bg.style.setProperty('--dim', String(Math.min(90, Math.max(0, dim)) / 100));
}

/* ---------- 首屏 ---------- */
function renderHero() {
  const brand = SITE.brand || SITE.name || '个人服务';
  document.title = SITE.tagline ? `${brand} · ${SITE.tagline}` : brand;
  $('#brandMark').textContent = brand;
  $('#brandName').textContent = SITE.name || '';
  $('#heroEyebrow').textContent = SITE.tagline || '个人服务';
  $('#heroHeadline').textContent = brand;
  $('#heroSub').textContent = SITE.name
    ? `我是 ${SITE.name}，下面这些都是我一个人在做。`
    : '下面这些都是我一个人在做。';
  $('#heroHeadline').style.whiteSpace = 'pre-line';
  $('#acceptingDot').classList.toggle('on', !!SITE.accepting);
  $('#acceptingText').textContent = SITE.accepting ? '开放接单' : '暂时休整';

  const wechatLine = $('#wechatLine');
  if (SITE.wechat) {
    wechatLine.innerHTML = `微信号：<b id="wechatValue">${escapeHtml(SITE.wechat)}</b>`
      + (IN_WECHAT
        ? '<span class="wechat-tip">长按可复制</span>'
        : '<span class="wechat-tip">点一下复制，去微信粘贴</span>');
    wechatLine.hidden = false;
  } else {
    wechatLine.hidden = true;
  }

  // 主页首屏主视觉：只认「🏠 主页设置」里自己传的那张。
  // ★ 以前是"借第一个分区的首屏图"，会有两个毛病：
  //   1) 背景图和分区首屏图一样时，同一张图在首页出现好几次
  //   2) 分区隐藏 / 换顺序，主页首屏图会莫名其妙跟着变
  // 现在完全独立：传了才显示，不传就没有这块。
  const cover = SITE.heroImage || '';
  $('#heroMedia').hidden = !cover;
  $('#heroMedia').innerHTML = cover ? `<img src="${cover}" alt="">` : '';
  $('#heroGrid').classList.toggle('with-media', !!cover);
  applyBackground();
}

/* ---------- 服务区：按大类分组 ---------- */
const GROUP_ICON = { '陪玩': '🎮', '代肝': '⚡', '其他服务': '🎨' };

function renderServices() {
  const groups = PAGE.nav || [];
  $('#servicesTitle').textContent = SITE.services_intro || '我能做什么';
  $('#serviceGroups').innerHTML = groups.length ? groups.map((g) => `
    <div class="service-group">
      <div class="group-head">
        <span class="group-icon">${GROUP_ICON[g.name] || '🎯'}</span>
        <h3>${escapeHtml(g.name)}</h3>
        <span class="hint">${escapeHtml(g.hint || (g.kind === 'booking' ? '按小时预约' : '按需报价'))}</span>
      </div>
      <div class="zone-cards">
        ${(g.zones || []).map((z) => `
          <a class="zone-card" href="/z/${encodeURIComponent(z.id)}" style="--zone:${escapeHtml(z.theme)}">
            <div class="zone-cover"${z.cover ? ` style="background-image:url('${escapeHtml(z.cover)}')"` : ''}>
              ${z.cover ? '' : `<span>${escapeHtml((z.short || z.name).slice(0, 4))}</span>`}
            </div>
            <div class="zone-body">
              <strong>${escapeHtml(z.name)}</strong>
              <p>${escapeHtml(z.tagline || '点开看详情与价目')}</p>
              ${z.startPrice ? `<span class="zone-price">${escapeHtml(z.startPrice)}</span>` : ''}
            </div>
          </a>`).join('')}
      </div>
    </div>`).join('')
    : '<div class="empty-state">还没有分区。去后台「分区管理」新建一个，这里就会出现卡片。</div>';
}

/* ---------- 信任信号 ---------- */
function renderTrust() {
  const trust = SITE.trust || [];
  const box = $('#trustList');
  // 一条都没有的时候，整块隐藏 —— 这句后台提示不能给客户看到
  // （主人想知道去哪填，看后台「分区管理 → 首页预览」或本文档即可）
  const section = box.closest('section') || box.parentElement;
  if (!trust.length) {
    box.innerHTML = '';
    if (section) section.hidden = true;
    const navLink = document.querySelector('a[href="#trust"]');
    if (navLink) navLink.hidden = true;
    return;
  }
  if (section) section.hidden = false;
  box.innerHTML = trust.map((t) => `
    <div class="trust-item"><span class="trust-dot"></span>${escapeHtml(t)}</div>`).join('');
}

/* ---------- 关于我 + 底部 ---------- */
function renderAbout() {
  $('#aboutName').textContent = SITE.name || SITE.brand || '';
  $('#aboutHint').textContent = SITE.tagline || '';
  $('#aboutBio').textContent = SITE.bio || '';
  $('#avatarBox').innerHTML = SITE.avatar
    ? `<img class="avatar" src="${SITE.avatar}" alt="${escapeHtml(SITE.name || '')} 的头像">`
    : `<div class="avatar empty">🐾</div>`;

  const card = $('#wechatCard');
  const qr = $('#wechatQrImg');
  if (SITE.wechatQr) {
    qr.src = SITE.wechatQr;
    qr.hidden = false;
  } else {
    qr.hidden = true;
    qr.removeAttribute('src');
  }
  $('#wechatBig').textContent = SITE.wechat ? `微信：${SITE.wechat}` : '';
  card.hidden = !SITE.wechat && !SITE.wechatQr;

  $('#footerName').textContent = SITE.brand || SITE.name || '';

  // 页脚收尾区（和个人主页/分区页共用同一套结构）
  const fq = $('#footerQr');
  if (fq) {
    if (SITE.wechatQr) { fq.src = SITE.wechatQr; fq.hidden = false; }
    else { fq.hidden = true; fq.removeAttribute('src'); }
  }
  const fcap = $('#footerQrCaption');
  if (fcap) fcap.hidden = IN_WECHAT || !SITE.wechatQr;
  const fid = $('#footerWechatId');
  if (fid) fid.textContent = SITE.wechat || '（还没设置微信号）';
  $('#footerHint').textContent = SITE.tagline || '陪玩 / 代肝 / 剪辑 / 设计 / 建站 —— 都是我一个人做。';
}

/* ---------- 事件 ---------- */
function copyWechat() {
  if (!SITE.wechat) return toast('还没设置微信号，请先在后台填写。', true);
  copyText(SITE.wechat, `微信号已复制：${SITE.wechat}`);
}

function bindEvents() {
  $('#copyWechat').addEventListener('click', copyWechat);
  $('#footerWechat').addEventListener('click', copyWechat);
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
(function main() {
  try {
    renderHero();
    renderServices();
    renderTrust();
    renderAbout();
    bindEvents();
  } catch (err) {
    toast(`页面渲染出错：${err.message}`, true);
  }
})();
