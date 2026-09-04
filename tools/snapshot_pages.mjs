/**
 * 对 capture/urls.txt 里的每个页面存一份 SingleFile 完整快照。
 *
 *   npm i single-file-cli
 *   node tools/capture_reference.mjs <起始URL>     # 先跑这个，登录并点一遍
 *   node tools/snapshot_pages.mjs                  # 再跑这个
 *
 * 复用 capture/profile 的登录态，不必重新登录。
 * 若提示找不到浏览器，设置 CHROME_PATH 指向本机 Chrome 可执行文件。
 *
 * 产出 capture/NN-*.snapshot.html：渲染后的 DOM + 内联 CSS/字体/图片，
 * 单文件、可离线打开、可在浏览器里直接量间距取色。
 *
 * 已知限制（实测）：SingleFile 会剥离全部 <script>，存下来的是静态快照，
 * 不是能跑的应用。弹窗、空状态等需要交互才出现的形态，它抓不到——
 * 那些请在 capture_reference.mjs 里手动触发后截图。
 */
import { spawn } from 'child_process';
import fs from 'fs';
import path from 'path';

const OUT = 'capture';
const URLS = path.join(OUT, 'urls.txt');
const PROFILE = path.join(OUT, 'profile');
const POLYFILL = path.join(OUT, '.closeevent.mjs');

if (!fs.existsSync(URLS)) {
  console.error(`找不到 ${URLS}，请先跑 node tools/capture_reference.mjs <起始URL>`);
  process.exit(1);
}

// Node < 23 没有全局 CloseEvent，而 single-file-cli 的依赖 simple-cdp 直接用了它，
// 不补会以 "ReferenceError: CloseEvent is not defined" 崩掉。
fs.writeFileSync(POLYFILL, `
if (typeof globalThis.CloseEvent === 'undefined') {
  globalThis.CloseEvent = class CloseEvent extends Event {
    constructor(type, init = {}) {
      super(type, init);
      this.code = init.code ?? 0;
      this.reason = init.reason ?? '';
      this.wasClean = init.wasClean ?? false;
    }
  };
}
`);

const CLI = 'node_modules/single-file-cli/single-file';
if (!fs.existsSync(CLI)) {
  console.error('未找到 single-file-cli，请先执行：npm i single-file-cli');
  process.exit(1);
}

const slug = (u) => {
  const p = new URL(u).pathname.replace(/^\/|\/$/g, '') || 'index';
  return p.replace(/[^a-zA-Z0-9]+/g, '-').slice(0, 80);
};

const urls = fs.readFileSync(URLS, 'utf-8').split('\n').map(s => s.trim()).filter(Boolean);
console.log(`待快照 ${urls.length} 个页面\n`);

let i = 0, ok = 0;
for (const url of urls) {
  const name = String(++i).padStart(2, '0') + '-' + slug(url) + '.snapshot.html';
  const dest = path.join(OUT, name);
  const args = [
    '--import', './' + POLYFILL, CLI,
    ...(fs.existsSync(PROFILE) ? [`--browser-profile=${PROFILE}`] : []),
    // 找不到浏览器时用 CHROME_PATH 指定，例如：
    //   Windows: set CHROME_PATH=C:\Program Files\Google\Chrome\Application\chrome.exe
    //   macOS:   export CHROME_PATH="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
    ...(process.env.CHROME_PATH
        ? [`--browser-executable-path=${process.env.CHROME_PATH}`] : []),
    '--browser-load-max-time=25000',
    url, dest,
  ];
  const code = await new Promise((res) => {
    const p = spawn('node', args, { stdio: ['ignore', 'ignore', 'pipe'] });
    let err = '';
    p.stderr.on('data', (d) => { err += d; });
    p.on('close', (c) => {
      if (c !== 0) {
        const first = err.split('\n')[0] || 'exit ' + c;
        console.log(`  失败 ${name}: ${first}`);
        if (/executable not found/i.test(first))
          console.log('    → 设置 CHROME_PATH 环境变量指向本机 Chrome 后重试');
      }
      res(c);
    });
  });
  if (code === 0 && fs.existsSync(dest)) {
    ok++;
    console.log(`  ${name}  ${(fs.statSync(dest).size / 1024).toFixed(0)} KB`);
  }
}
fs.rmSync(POLYFILL, { force: true });
console.log(`\n完成 ${ok}/${urls.length} → ${OUT}/`);
console.log('快照是静态 DOM（脚本已被剥离），弹窗与空状态请用截图补齐。');
