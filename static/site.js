/* ==========================================================================
   个人主页逻辑（/）—— 品牌 + 我能做什么 + 信任信号 + 加微信

   数据是服务端渲染时塞进 window.__PAGE__ 的（见 app.py 的 render_page），
   这里只负责把它画成卡片。分区页的交互在 zone.js 里，两边互不影响。
   ========================================================================== */

const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));

const PAGE = window.__PAGE__ || { site: {}, nav: [] };
const SITE = PAGE.site || {};

/* ---------- ★ 文案表：客户看到的每一句中文，都由后台「文案」决定 ----------
   服务端（app.py 的 site_summary）已经把**默认值补齐**了，所以这里几乎不会拿到空值；
   tx() 的第二个参数（fallback）只是"连文案表都没拿到"时的最后一道保险。
   花括号里的 {name} 是"变量"，在这里被换成昵称 —— 你改昵称，那句话跟着变。 */
const TEXTS = SITE.texts || {};
const POSITIONS = SITE.imagePositions || {};

function tx(key, fallback, vars) {
  const value = TEXTS[key];
  const text = (value === undefined || value === null || value === '') ? (fallback || '') : value;
  const map = vars || {};
  return String(text).replace(/\{(\w+)\}/g, (m, k) => (map[k] !== undefined ? map[k] : m));
}

/** 图片的显示位置（后台九宫格选的）。没设置过就是 center —— 和以前一模一样 */
function pos(key) { return POSITIONS[key] || 'center'; }

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
  // ★ 显示位置：铺满屏幕时图会被裁掉一部分，这一行决定保留哪一块（后台九宫格选的）
  bg.style.backgroundPosition = pos('background');
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
  // 首屏那句自我介绍（{name} = 昵称）；没填昵称时顺手把"我是 ，"这种断句抹掉
  $('#heroSub').textContent = tx('heroIntro', '下面这些都是我一个人在做。', { name: SITE.name || '' })
    .replace(/我是\s*[，,]\s*/, '')
    .replace(/\s{2,}/g, ' ');
  $('#heroHeadline').style.whiteSpace = 'pre-line';

  // 导航和首屏按钮上的字（这些以前是写死在 HTML 里的）
  $('#navServices').textContent = tx('navServices', '我能做什么');
  $('#navWhy').textContent = tx('navWhy', '为什么找我');
  $('#navAbout').textContent = tx('navAbout', '关于我');
  $('#navTerms').textContent = tx('navTerms', '服务条款');
  $('#heroCta').textContent = tx('heroCta', '看看我能做什么');
  $('#copyWechat').textContent = tx('wechatCta', '加微信');

  $('#acceptingDot').classList.toggle('on', !!SITE.accepting);
  $('#acceptingText').textContent = SITE.accepting
    ? tx('acceptingOn', '开放接单') : tx('acceptingOff', '暂时休整');

  const wechatLine = $('#wechatLine');
  if (SITE.wechat) {
    wechatLine.innerHTML = `${escapeHtml(tx('wechatLabel', '微信号：'))}<b id="wechatValue">${escapeHtml(SITE.wechat)}</b>`
      + (IN_WECHAT
        ? `<span class="wechat-tip">${escapeHtml(tx('wechatCopyTip', '长按可复制'))}</span>`
        : `<span class="wechat-tip">${escapeHtml(tx('wechatCopyTipBrowser', '点一下复制，去微信粘贴'))}</span>`);
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
  // ★ 这张图是 object-fit: cover（高过 430px 就会被裁），位置由后台九宫格决定
  $('#heroMedia').innerHTML = cover
    ? `<img src="${cover}" alt="" style="object-position:${escapeHtml(pos('heroImage'))}">` : '';
  $('#heroGrid').classList.toggle('with-media', !!cover);
  applyBackground();
}

/* ---------- 服务区：按大类分组 ---------- */
const GROUP_ICON = { '陪玩': '🎮', '代肝': '⚡', '其他服务': '🎨' };

function renderServices() {
  const groups = PAGE.nav || [];
  $('#servicesTitle').textContent = SITE.services_intro || '我能做什么';
  $('#servicesHint').textContent = tx('servicesHint', '点开看详情与价目');
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
              <p>${escapeHtml(z.tagline || tx('zoneCardDefault', '点开看详情与价目'))}</p>
              ${z.startPrice ? `<span class="zone-price">${escapeHtml(z.startPrice)}</span>` : ''}
            </div>
          </a>`).join('')}
      </div>
    </div>`).join('')
    : `<div class="empty-state">${escapeHtml(tx('servicesEmpty', '还没有分区。去后台「分区管理」新建一个，这里就会出现卡片。'))}</div>`;
}

/* ---------- ★ 服务流程（几步的图）+ 常见问题（折叠块） ----------
   这两块放在服务卡片下面，位置的意义是：
     · 服务流程 = "那我怎么开始"（刚看完你能做什么，紧接着就是这个）
     · 常见问题 = 客户心里那几个"算了不问了"的问题，答案就摆在下面
   两块的内容都是**主人自己在后台整条写的**（后台 →「文案」→ 服务流程 / 常见问题），
   默认值由服务端补齐（SITE.faq / SITE.steps），所以这里不用写兜底文案。 */

/** 把 {wechat} 换成微信号（前台的 tx() 只认 {name}/{zone}/{max}，这里补一个）
    ⚠️ 主页目前没有用到它，但留着：以后你在「文案」里给主页某句话写 {wechat} 时，
       它才能被换成你的微信号 —— 而且分区页的 zone.js 也用同名函数，两边保持一致。 */
function fillWechat(text) {
  return String(text || '').replace(/\{wechat\}/g, SITE.wechat || '');
}

/* ⚠️ 主页【故意没有】常见问题那一块：问答都是针对具体服务的，
   客户在主页还没选服务，提前看一整套太多余 ✗
   每个分区页各有一份（在 static/zone.js 里）—— 那才是对的时机 ✓ */
/* ---------- 信任信号 ---------- */
function renderTrust() {  const trust = SITE.trust || [];
  const box = $('#trustList');
  $('#whyTitle').textContent = tx('whyTitle', '为什么找我。');
  $('#whyHint').textContent = tx('whyHint', '个人接单 · 无中间商');
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
  $('#aboutTitle').textContent = tx('aboutTitle', '你好，我是 {name}。', { name: SITE.name || '' })
    .replace(/我是\s*。/, '。');        // 没填昵称时不显示"我是 。"
  $('#aboutHint').textContent = SITE.tagline || '';
  $('#aboutBio').textContent = SITE.bio || '';
  $('#wechatCardHint').textContent = tx('wechatCardHint', '加我微信，直接聊需求');
  // 头像是 object-fit: cover（不是正方形就会被裁），位置由后台九宫格决定
  $('#avatarBox').innerHTML = SITE.avatar
    ? `<img class="avatar" src="${SITE.avatar}" style="object-position:${escapeHtml(pos('avatar'))}"
         alt="${escapeHtml(SITE.name || '')} 的头像">`
    : `<div class="avatar empty">🐾</div>`;

  const card = $('#wechatCard');
  const qr = $('#wechatQrImg');
  qr.alt = tx('wechatQrAlt', '我的微信二维码');
  if (SITE.wechatQr) {
    qr.src = SITE.wechatQr;
    qr.hidden = false;
  } else {
    qr.hidden = true;
    qr.removeAttribute('src');
  }
  $('#wechatBig').textContent = SITE.wechat
    ? `${tx('wechatLabel', '微信号：')}${SITE.wechat}` : '';
  card.hidden = !SITE.wechat && !SITE.wechatQr;

  $('#footerName').textContent = SITE.brand || SITE.name || '';

  // 页脚收尾区（和个人主页/分区页共用同一套结构）
  const fq = $('#footerQr');
  if (fq) {
    fq.alt = tx('wechatQrAlt', '我的微信二维码');
    if (SITE.wechatQr) { fq.src = SITE.wechatQr; fq.hidden = false; }
    else { fq.hidden = true; fq.removeAttribute('src'); }
  }
  const fcap = $('#footerQrCaption');
  if (fcap) {
    fcap.textContent = tx('footerQrCaption', '长按上面的二维码就能加我（部分浏览器支持）');
    fcap.hidden = IN_WECHAT || !SITE.wechatQr;
  }
  const fid = $('#footerWechatId');
  if (fid) fid.textContent = SITE.wechat || tx('wechatEmpty', '（还没设置微信号）');
  $('#footerTip').textContent = tx('footerTip', '加我之后直接说：哪个游戏 / 哪一档，我来安排。');
  $('#footerDisclaimer').textContent = tx('footerDisclaimer',
    '本页为个人服务页面，与游戏官方无隶属关系；游戏素材版权归原权利人所有。');
  const fAbout = $('#footerAboutLink');
  if (fAbout) fAbout.textContent = tx('navAbout', '关于我');
  const fTerms = $('#footerTermsLink');
  if (fTerms) fTerms.textContent = tx('navTerms', '服务条款');
  $('#footerHint').textContent = SITE.tagline
    || tx('footerHomeHint', '陪玩 / 代肝 / 剪辑 / 设计 / 建站 —— 都是我一个人做。');
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
