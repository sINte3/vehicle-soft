// tools/ux/check_overflow.mjs -- gorizontalnoe perepolnenie dokumenta, po vsem
// marshrutam i vsem chetyrem vyeportam, s nazvaniem VINOVNOGO uzla.
//
// Zachem
// ------
// Pikselnyy diff etogo ne vidit: "stranicu vyneslo za kray okna" i "stranica
// prosto drugaya" dayut odinakovye pikseli. Progon na staging (UI-P7) nashel
// sem sluchaev na nastoyashchih dannyh -- na stende s 22 strokami ih net,
// potomu chto perepolnenie daet dlinnoe znachenie, a ne razmetka sama po sebe.
//
// Krome fakta perepolneniya skript nazyvaet element, kotoryy shire okna, i
// ego shirinu: bez etogo ostaetsya iskat vruchnuyu.
//
// Zapusk:
//   PLAYWRIGHT_BROWSERS_PATH=/opt/pw-browsers node tools/ux/check_overflow.mjs
//   ... --base http://127.0.0.1:5112 --only /fuel/balance-report,/deficiencies

import { chromium } from 'playwright';
import fs from 'node:fs';
import path from 'node:path';

const ROOT = path.resolve(import.meta.dirname, '..', '..');
const CHROME = '/opt/pw-browsers/chromium-1194/chrome-linux/chrome';
function arg(name, fallback) {
  const i = process.argv.indexOf('--' + name);
  return i > -1 && process.argv[i + 1] ? process.argv[i + 1] : fallback;
}
const BASE = arg('base', 'http://127.0.0.1:5112');
const ONLY = arg('only', '').split(',').filter(Boolean);
const VIEWPORTS = [
  { name: 'laptop', width: 1440, height: 900 },
  { name: 'tablet', width: 1024, height: 768 },
  { name: 'narrow', width: 600, height: 900 },
  { name: 'mobile', width: 390, height: 844 },
];

const all = JSON.parse(fs.readFileSync(
  path.join(ROOT, 'docs', 'ux', '11-baseline', 'routes.json'), 'utf8')).routes
  .filter((r) => !/(^|\/)api(\/|$)/.test(r.url))
  .filter((r) => !/logout|export|download|\.xlsx|\.pdf|\/pdf/i.test(r.url));
const routes = ONLY.length ? all.filter((r) => ONLY.includes(r.url)) : all;

async function login(page) {
  await page.goto(BASE + '/login', { waitUntil: 'domcontentloaded' });
  await page.fill('input[name="username"]', 'ux_admin');
  await page.fill('input[name="password"]', 'ux-audit-local');
  await Promise.all([
    page.waitForLoadState('domcontentloaded'),
    page.click('form.vs-login-form button[type="submit"]'),
  ]);
  if (page.url().includes('/login')) throw new Error('login failed');
}

const browser = await chromium.launch({ executablePath: CHROME });
const hits = [];
for (const vp of VIEWPORTS) {
  const ctx = await browser.newContext({ viewport: { width: vp.width, height: vp.height } });
  const page = await ctx.newPage();
  await login(page);
  for (const route of routes) {
    let resp;
    try {
      resp = await page.goto(BASE + route.url, { waitUntil: 'domcontentloaded', timeout: 30000 });
    } catch { continue; }
    if (!resp || resp.status() >= 400 || page.url().includes('/login')) continue;
    const info = await page.evaluate((limit) => {
      const de = document.documentElement;
      if (de.scrollWidth <= de.clientWidth + 1) return null;
      // [REASON]: vinovnik -- SAMYY GLUBOKIY uzel shire okna. Ego roditeli
      // shire tozhe, i bez etogo otbora otchet nazyval by <body>.
      let worst = null;
      for (const el of document.querySelectorAll('*')) {
        const r = el.getBoundingClientRect();
        const right = r.left + r.width;
        if (r.width <= limit + 1 && right <= limit + 1) continue;
        if (el.scrollWidth > el.clientWidth + 1) continue;   // umeet prokruchivatsya sam
        if (!worst || el.compareDocumentPosition(worst) & Node.DOCUMENT_POSITION_CONTAINS) worst = el;
      }
      const name = worst
        ? worst.tagName.toLowerCase()
          + (worst.id ? '#' + worst.id : '')
          + (typeof worst.className === 'string' && worst.className
            ? '.' + worst.className.trim().split(/\s+/).slice(0, 3).join('.') : '')
        : '(ne nayden)';
      return { scrollWidth: de.scrollWidth, clientWidth: de.clientWidth,
               culprit: name,
               culpritWidth: worst ? Math.round(worst.getBoundingClientRect().width) : 0,
               text: worst ? (worst.textContent || '').replace(/\s+/g, ' ').trim().slice(0, 60) : '' };
    }, vp.width);
    if (info) hits.push({ url: route.url, viewport: vp.name, ...info });
  }
  await ctx.close();
}
await browser.close();

console.log(`marshrutov ${routes.length} x vyeportov ${VIEWPORTS.length}`);
console.log(`gorizontalnoe perepolnenie: ${hits.length}`);
for (const h of hits) {
  console.log(`  !! ${h.viewport.padEnd(7)} ${h.url.padEnd(30)} ${h.scrollWidth}>${h.clientWidth}` +
              `  vinovnik ${h.culprit} (${h.culpritWidth}px) ${JSON.stringify(h.text)}`);
}
process.exit(hits.length ? 1 : 0);
