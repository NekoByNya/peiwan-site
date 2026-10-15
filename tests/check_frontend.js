// 检查 JS 里用到的元素 id 在 HTML 里是否都存在（防止手写代码时打错 id）
const fs = require('fs');
const path = require('path');
const root = path.join(__dirname, '..');

// 两级分区之后有两套前台页面：个人主页（/）和分区页（/z/<id>），各配一个脚本。
// ⚠️ templates/terms.html（服务条款页）**故意不在这里**：它没有配套的 JS
//    （整页由 app.py 服务端渲染，客户不装 JS 也能看全），所以没有"id 对不上"这回事。
//    但它的 class 要参与下面那一项检查，否则 style.css 里给它写的样式会被报成"没人用"。
const pairs = [
  ['static/site.js', 'templates/index.html'],
  ['static/zone.js', 'templates/zone.html'],
  ['static/admin.js', 'templates/admin.html'],
];

let bad = 0;
for (const [js, html] of pairs) {
  const src = fs.readFileSync(path.join(root, js), 'utf8');
  const page = fs.readFileSync(path.join(root, html), 'utf8');
  const ids = [...new Set([...src.matchAll(/\$\('#([A-Za-z0-9_-]+)'\)/g)].map((m) => m[1]))];
  const missing = ids.filter((id) => !page.includes(`id="${id}"`));
  console.log(`${js}: 用到 ${ids.length} 个 id，${html} 中缺失: ${missing.length ? missing.join(', ') : '无'}`);
  bad += missing.length;
}

// 顺带检查 CSS 里的类名有没有被用到（找明显打错的）
const cssRaw = fs.readFileSync(path.join(root, 'static/style.css'), 'utf8');
// 先把注释和 url(...) 去掉，否则注释里的文件名、图片路径里的 ".jpg" 会被误当成类名
const css = cssRaw
  .replace(/\/\*[\s\S]*?\*\//g, '')
  .replace(/url\([^)]*\)/g, 'url()');
const classes = [...new Set([...css.matchAll(/\.([a-z][a-z0-9-]{2,})/g)].map((m) => m[1]))];
const jsAll = ['static/site.js', 'static/zone.js', 'static/admin.js']
  .map((f) => fs.readFileSync(path.join(root, f), 'utf8')).join('');
const htmlAll = ['templates/index.html', 'templates/zone.html', 'templates/admin.html',
                 'templates/terms.html']
  .map((f) => fs.readFileSync(path.join(root, f), 'utf8')).join('');
const unused = classes.filter((c) => !jsAll.includes(c) && !htmlAll.includes(c));
console.log(`style.css: 定义了 ${classes.length} 个类，未被使用的: ${unused.length ? unused.join(', ') : '无'}`);

process.exit(bad ? 1 : 0);
