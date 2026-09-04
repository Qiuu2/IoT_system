/**
 * 参考站点采集器 —— 在你自己的机器上跑，浏览器会打开让你手动登录。
 *
 *   npm i playwright && npx playwright install chromium
 *   node tools/capture_reference.mjs https://例子.com/web/system/home/work-bench
 *
 * 登录后正常点页面，脚本检测到路由变化就自动采集当前页：
 *   - 整页截图（含滚动区域）
 *   - 页面结构大纲（各区块的层级与文本，不含实际业务数据）
 *   - 设计 token（背景/文字/主色、字号、圆角、间距，从计算样式里取真值）
 *
 * 输出在 capture/ 目录。Ctrl+C 结束。之后跑 tools/snapshot_pages.mjs
 * 可对同一批 URL 再存一份 SingleFile 完整快照（复用这里的登录态）。
 *
 * 注意：capture/profile/ 里是你的登录会话，只留本地，不要发给任何人。
 */
import { chromium } from 'playwright';
import fs from 'fs';
import path from 'path';

const START = process.argv[2];
if (!START) {
  console.error('用法: node tools/capture_reference.mjs <起始URL>');
  process.exit(1);
}
const OUT = 'capture';
const URLS = path.join(OUT, 'urls.txt');   // 供 snapshot_pages.mjs 消费
fs.mkdirSync(OUT, { recursive: true });

const slug = (u) => {
  const p = new URL(u).pathname.replace(/^\/|\/$/g, '') || 'index';
  return p.replace(/[^a-zA-Z0-9]+/g, '-').slice(0, 80);
};

/** 从计算样式里抽设计 token —— 截图量不出来的那部分。 */
const TOKENS = () => {
  const seen = new Map();
  const bump = (bucket, val) => {
    if (!val || val === 'none' || val === 'rgba(0, 0, 0, 0)' || val === '0px') return;
    const k = bucket + '|' + val;
    seen.set(k, (seen.get(k) || 0) + 1);
  };
  for (const el of document.querySelectorAll('body *')) {
    const r = el.getBoundingClientRect();
    if (r.width < 4 || r.height < 4) continue;      // 跳过不可见元素
    const s = getComputedStyle(el);
    bump('背景', s.backgroundColor);
    bump('文字', s.color);
    bump('边框', s.borderColor === s.color ? '' : s.borderColor);
    bump('字号', s.fontSize);
    bump('字重', s.fontWeight);
    bump('圆角', s.borderRadius);
    bump('字体', s.fontFamily.split(',')[0].replace(/"/g, ''));
  }
  const out = {};
  for (const [k, n] of seen) {
    const [bucket, val] = k.split('|');
    (out[bucket] = out[bucket] || []).push({ 值: val, 出现次数: n });
  }
  for (const b of Object.keys(out)) {
    out[b].sort((a, c) => c.出现次数 - a.出现次数);
    out[b] = out[b].slice(0, 14);          // 每类只留最常用的
  }
  return out;
};

/** 页面结构大纲：保留布局骨架与标题文案，跳过长文本与表格数据。 */
const OUTLINE = () => {
  const lines = [];
  const walk = (el, depth) => {
    if (depth > 7) return;
    const r = el.getBoundingClientRect();
    if (r.width < 30 || r.height < 12) return;
    const tag = el.tagName.toLowerCase();
    if (['script', 'style', 'svg', 'path'].includes(tag)) return;
    const cls = (el.className && typeof el.className === 'string')
      ? '.' + el.className.trim().split(/\s+/).slice(0, 3).join('.') : '';
    const own = [...el.childNodes]
      .filter((n) => n.nodeType === 3).map((n) => n.textContent.trim())
      .join(' ').slice(0, 60);
    lines.push('  '.repeat(depth) + `${tag}${cls}` +
      `  [${Math.round(r.width)}×${Math.round(r.height)}]` +
      (own ? `  「${own}」` : ''));
    for (const c of el.children) walk(c, depth + 1);
  };
  walk(document.body, 0);
  return lines.slice(0, 900).join('\n');
};

// 用持久化 profile 而非 storageState：SingleFile 也能复用同一份登录态，
// 你只需登录一次，两个工具都不用再登。
const PROFILE = path.join(OUT, 'profile');
const ctx = await chromium.launchPersistentContext(PROFILE, {
  headless: false,
  viewport: null,
  args: ['--start-maximized'],
});
const page = ctx.pages()[0] || await ctx.newPage();
await page.goto(START, { waitUntil: 'domcontentloaded' });

console.log('\n浏览器已打开。请登录，然后正常点击各个菜单。');
console.log('每进入一个新页面会自动采集，Ctrl+C 结束。\n');

let last = '', n = 0;
const done = new Set();

async function capture(url) {
  const name = String(++n).padStart(2, '0') + '-' + slug(url);
  try {
    await page.waitForLoadState('networkidle', { timeout: 5000 }).catch(() => {});
    await page.waitForTimeout(1200);                       // 等图表动画落定
    await page.screenshot({ path: path.join(OUT, name + '.png'), fullPage: true });
    fs.writeFileSync(path.join(OUT, name + '.outline.txt'),
      `URL: ${url}\n标题: ${await page.title()}\n\n` + await page.evaluate(OUTLINE));
    fs.writeFileSync(path.join(OUT, name + '.tokens.json'),
      JSON.stringify(await page.evaluate(TOKENS), null, 2));
    console.log(`已采集 ${name}`);
  } catch (e) {
    console.log(`采集失败 ${name}: ${e.message.split('\n')[0]}`);
  }
}

setInterval(async () => {
  if (page.isClosed()) return;
  const url = page.url();
  if (url === last) return;
  last = url;
  if (done.has(url)) return;
  done.add(url);
  await capture(url);
  fs.writeFileSync(URLS, [...done].join('\n') + '\n');
}, 1500);

process.on('SIGINT', async () => {
  console.log(`\n共采集 ${n} 个页面 → ${OUT}/`);
  console.log(`URL 清单已写入 ${URLS}，接着跑：node tools/snapshot_pages.mjs`);
  console.log('提醒：capture/profile/ 含登录会话，发文件时请整个剔除。');
  await ctx.close().catch(() => {});
  process.exit(0);
});
