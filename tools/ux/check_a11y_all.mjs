// tools/ux/check_a11y_all.mjs -- axe-core po VSEMU spisku marshrutov, a ne po
// vyborke iz pyati ekranov na modul.
//
// Zachem
// ------
// V P6 axe gonyalsya po 5-8 ekranam na modul i daval nol. Progon na staging
// (UI-P7) proshel vse 80 marshrutov i nashel 25 serious i 33 critical --
// pravila `label` i `select-name` na ekranah, kotorye v vyborku ne popali.
// To est nol byl chestnym dlya vyborki i nevernym dlya modulya.
//
// [REASON]: marshruty beryutsya iz togo zhe routes.json, chto i obhod, i iz
// nego ubirayutsya JSON-endpointy. U nih net ni <title>, ni lang -- axe
// spravedlivo soobshchaet ob etom, i eti desyat "narusheniy" v progone na
// staging byli shumom. Filtr `/api/` dolzhen smotret ves put, a ne tolko ego
// nachalo: `/fuel/api/fuel_ping` nachinaetsya ne s `/api/`.
//
// Zapusk:
//   PLAYWRIGHT_BROWSERS_PATH=/opt/pw-browsers node tools/ux/check_a11y_all.mjs
//   ... --base http://127.0.0.1:5110 --viewports 1440,390

import { chromium } from 'playwright';
import { AxeBuilder } from '@axe-core/playwright';
import fs from 'node:fs';
import path from 'node:path';

const ROOT = path.resolve(import.meta.dirname, '..', '..');
const CHROME = '/opt/pw-browsers/chromium-1194/chrome-linux/chrome';
function arg(name, fallback) {
  const i = process.argv.indexOf('--' + name);
  return i > -1 && process.argv[i + 1] ? process.argv[i + 1] : fallback;
}
const BASE = arg('base', 'http://127.0.0.1:5110');
const ROLE = arg('role', 'ux_admin');
const VIEWPORTS = arg('viewports', '1440,390').split(',').map((v) => {
  const w = parseInt(v, 10);
  return { name: String(w), width: w, height: w >= 1000 ? 900 : 844 };
});

const routes = JSON.parse(fs.readFileSync(
  path.join(ROOT, 'docs', 'ux', '11-baseline', 'routes.json'), 'utf8')).routes
  .filter((r) => !/(^|\/)api(\/|$)/.test(r.url))
  .filter((r) => !/logout|export|download|\.xlsx|\.pdf|\/pdf/i.test(r.url));

async function login(page) {
  await page.goto(BASE + '/login', { waitUntil: 'domcontentloaded' });
  await page.fill('input[name="username"]', ROLE);
  await page.fill('input[name="password"]', 'ux-audit-local');
  await Promise.all([
    page.waitForLoadState('domcontentloaded'),
    page.click('form.vs-login-form button[type="submit"]'),
  ]);
  if (page.url().includes('/login')) throw new Error('login failed');
}

const browser = await chromium.launch({ executablePath: CHROME });
const findings = [];
let scanned = 0, skipped = 0;

for (const vp of VIEWPORTS) {
  const ctx = await browser.newContext({ viewport: { width: vp.width, height: vp.height } });
  const page = await ctx.newPage();
  await login(page);
  for (const route of routes) {
    let resp;
    try {
      resp = await page.goto(BASE + route.url, { waitUntil: 'domcontentloaded', timeout: 30000 });
    } catch { skipped++; continue; }
    if (!resp || resp.status() >= 400 || page.url().includes('/login')) { skipped++; continue; }
    const result = await new AxeBuilder({ page }).analyze();
    scanned++;
    for (const v of result.violations) {
      if (v.impact !== 'serious' && v.impact !== 'critical') continue;
      findings.push({ url: route.url, viewport: vp.name, rule: v.id, impact: v.impact,
                      nodes: v.nodes.length,
                      target: v.nodes.slice(0, 2).map((n) => n.target.join(' ')).join(' ; ') });
    }
  }
  await ctx.close();
}
await browser.close();

const byRule = {};
for (const f of findings) {
  const k = f.rule + ' [' + f.impact + ']';
  byRule[k] = (byRule[k] || 0) + f.nodes;
}
console.log(`marshrutov v spiske ${routes.length}, proskanirovano ${scanned}, propushcheno ${skipped}`);
console.log(`serious+critical: ${findings.length} strok, ${Object.values(byRule).reduce((a, b) => a + b, 0)} uzlov`);
for (const [rule, n] of Object.entries(byRule).sort((a, b) => b[1] - a[1])) {
  console.log(`  ${String(n).padStart(4)}  ${rule}`);
}
for (const f of findings.slice(0, 40)) {
  console.log(`  !! ${f.viewport.padEnd(5)} ${f.url.padEnd(34)} ${f.rule} x${f.nodes}  ${f.target.slice(0, 70)}`);
}
process.exit(findings.length ? 1 : 0);
