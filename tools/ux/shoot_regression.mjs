// tools/ux/shoot_regression.mjs -- kriteriy vyhoda P5 punkt 3.
//
// "Vizualnyy diff na NEMIGRIROVANNYH stranicah <= soglasovannogo poroga --
//  fundament ne imeet prava menyat to, chto eshche ne migrirovali."
//
// Snimaet stranicy, kotoryh trek NE trogal, s dvuh ekzemplyarov (do i posle)
// i schitaet dolyu razlichayushchihsya pikseley. Fikstury na oboih storonah
// sverяyutsya pered pervym kadrom tem zhe sposobom, chto v shoot_canonical.mjs:
// bez etogo sravnivalis by raznye dannye, i lyuboe chislo bylo by shumom.
//
// [REASON]: vyeporty vybrany po TOMU, GDE PRAVKA MOZHET SKAZATSYA, a ne "vse
// chetyre dlya polnoty". Perenos sboroki saydbara s 900 na 1024 (DD-024) menyaet
// razmetku rovno v polose 900-1024 px, poetomu 1024 obyazatelen; 1440 -- kontrol
// "vyshe polosy nichego ne dvinulos"; 390 -- kontrol nizhney vetki.
//
// Zapusk:
//   PLAYWRIGHT_BROWSERS_PATH=/opt/pw-browsers node tools/ux/shoot_regression.mjs

import { chromium } from 'playwright';
import fs from 'node:fs';
import path from 'node:path';
import { PNG } from 'pngjs';

const ROOT = path.resolve(import.meta.dirname, '..', '..');
const OUT = path.join(ROOT, 'docs', 'ux', '51-regression');
const CHROME = '/opt/pw-browsers/chromium-1194/chrome-linux/chrome';

function arg(name, fallback) {
  const i = process.argv.indexOf('--' + name);
  return i > -1 && process.argv[i + 1] ? process.argv[i + 1] : fallback;
}
const BEFORE = arg('before', 'http://127.0.0.1:5098');
const AFTER = arg('after', 'http://127.0.0.1:5099');
const LIMIT = parseInt(arg('limit', '0'), 10);

const VIEWPORTS = {
  laptop: { width: 1440, height: 900 },
  tablet: { width: 1024, height: 768 },
  // [REASON]: 600 px dobavlen, kogda pravila shablonov poehali s 700/760/640/600
  // na shkalu. Bez nego polosa 480-768, gde imenno i menyaetsya chislo kolonok,
  // ne snimalas voobshche: 1440 i 1024 vyshe nee, 390 nizhe. Proverka, ne
  // popadayushchaya v izmenennuyu vetku, daet odinakovyy otvet pri vernom i
  // nevernom kode -- to est proverkoy ne yavlyaetsya.
  narrow: { width: 600, height: 900 },
  mobile: { width: 390, height: 844 },
};

// Ekrany, kotorye trek uzhe pereodel: ih diff osmyslen i pokazan otdelno
// (docs/ux/50-canonical). Zdes nuzhny imenno OSTALNYE.
const MIGRATED = new Set([
  '/', '/entry', '/report', '/fuel/transactions', '/drones/works',
  '/__ui/kitchen-sink',
]);
const MIGRATED_PREFIX = ['/spare-parts/1'];

// [REASON]: stranicy, soderzhimoe kotoryh zavisit ot TOGO, SKOLKO RAZ krauler
// zashel, a ne ot koda. Zhurnal audita pishet kazhdyy vhod; krauler loginiysya
// v kazhdyy ekzemplyar svoe chislo raz, i stranica chestno pokazyvaet raznoe.
// V pervom progone eto dalo 21.82 % na noutbuke -- edinstvennyy vybros sredi
// 73 stranic, i vyglyadel on kak regressiya verstki. Sravnenie tekstom
// pokazalo: 37 yacheek iz 40 sovpadayut, a raznica v CHISLE strok.
const DATA_VOLATILE = new Set(['/admin/audit']);

const FREEZE_CSS = `
*, *::before, *::after {
  animation-duration: 0s !important; animation-delay: 0s !important;
  transition-duration: 0s !important; transition-delay: 0s !important;
  caret-color: transparent !important;
}
html { scroll-behavior: auto !important; }`;

async function login(page, base, role) {
  await page.goto(base + '/login', { waitUntil: 'domcontentloaded' });
  await page.fill('input[name="username"]', role);
  await page.fill('input[name="password"]', 'ux-audit-local');
  await Promise.all([
    page.waitForLoadState('domcontentloaded'),
    page.click('form.vs-login-form button[type="submit"]'),
  ]);
  if (page.url().includes('/login')) throw new Error('login failed at ' + base);
}

async function fingerprint(page, base) {
  await page.goto(base + '/ref/equipment', { waitUntil: 'networkidle' });
  return page.evaluate(() =>
    [...document.querySelectorAll('table td')].map((td) => td.textContent.trim())
      .filter(Boolean).sort().join('|'));
}

// Dolya razlichayushchihsya pikseley. Razmery mogut ne sovpast -- togda
// sravnivaem po obshchey oblasti i schitaem raznicu vysoty otdelno, chtoby
// "stranica stala korоche" ne vyglyadelo kak "vsyo peremenilos".
function pixelDiff(fileA, fileB) {
  const a = PNG.sync.read(fs.readFileSync(fileA));
  const b = PNG.sync.read(fs.readFileSync(fileB));
  const w = Math.min(a.width, b.width);
  const h = Math.min(a.height, b.height);
  let diff = 0;
  for (let y = 0; y < h; y++) {
    for (let x = 0; x < w; x++) {
      const ia = (a.width * y + x) << 2;
      const ib = (b.width * y + x) << 2;
      if (a.data[ia] !== b.data[ib] || a.data[ia + 1] !== b.data[ib + 1] ||
          a.data[ia + 2] !== b.data[ib + 2]) diff++;
    }
  }
  return {
    pct: +(100 * diff / (w * h)).toFixed(2),
    heightBefore: a.height, heightAfter: b.height,
  };
}

const main = async () => {
  const routes = JSON.parse(fs.readFileSync(
    path.join(ROOT, 'docs', 'ux', '11-baseline', 'routes.json'), 'utf8')).routes
    .filter((r) => !r.url.startsWith('/api/'))
    .filter((r) => !/logout|export|\.xlsx|\.pdf|download|\/pdf/i.test(r.url))
    .filter((r) => !MIGRATED.has(r.url))
    .filter((r) => !MIGRATED_PREFIX.some((p) => r.url.startsWith(p)))
    .filter((r) => !DATA_VOLATILE.has(r.url));
  const list = LIMIT ? routes.slice(0, LIMIT) : routes;

  fs.mkdirSync(path.join(OUT, 'before'), { recursive: true });
  fs.mkdirSync(path.join(OUT, 'after'), { recursive: true });

  const browser = await chromium.launch({ executablePath: CHROME });

  {
    const c1 = await browser.newContext({ viewport: VIEWPORTS.laptop });
    const p1 = await c1.newPage(); await login(p1, BEFORE, 'ux_admin');
    const f1 = await fingerprint(p1, BEFORE); await c1.close();
    const c2 = await browser.newContext({ viewport: VIEWPORTS.laptop });
    const p2 = await c2.newPage(); await login(p2, AFTER, 'ux_admin');
    const f2 = await fingerprint(p2, AFTER); await c2.close();
    if (f1 !== f2) {
      await browser.close();
      console.error('FIXTURE MISMATCH: sravnivat nechego, dannye raznye.');
      process.exit(1);
    }
    console.log(`fixtures match: ${f1.split('|').length} cells identical`);
  }

  const results = [];
  for (const [vpName, vp] of Object.entries(VIEWPORTS)) {
    const ctxs = {};
    for (const side of ['before', 'after']) {
      const base = side === 'before' ? BEFORE : AFTER;
      const ctx = await browser.newContext({
        viewport: vp, deviceScaleFactor: 1, locale: 'ru-RU', timezoneId: 'Asia/Tashkent',
      });
      const page = await ctx.newPage();
      await login(page, base, 'ux_admin');
      ctxs[side] = { ctx, page, base };
    }
    for (const route of list) {
      const files = {};
      let ok = true;
      for (const side of ['before', 'after']) {
        const { page, base } = ctxs[side];
        try {
          const resp = await page.goto(base + route.url, { waitUntil: 'networkidle', timeout: 30000 });
          if (!resp || resp.status() >= 400 || page.url().includes('/login')) { ok = false; break; }
          await page.addStyleTag({ content: FREEZE_CSS }).catch(() => {});
          await page.evaluate(() => document.fonts && document.fonts.ready).catch(() => {});
          await page.evaluate(() => window.scrollTo(0, 0));
          const file = path.join(OUT, side, `${route.slug}-${vpName}.png`);
          await page.screenshot({ path: file, fullPage: true });
          files[side] = file;
        } catch (e) { ok = false; break; }
      }
      if (!ok) continue;
      const d = pixelDiff(files.before, files.after);
      results.push({ url: route.url, slug: route.slug, viewport: vpName, ...d });
    }
    for (const side of ['before', 'after']) await ctxs[side].ctx.close();
    console.log(`${vpName}: ${list.length} routes`);
  }
  await browser.close();

  results.sort((a, b) => b.pct - a.pct);
  fs.writeFileSync(path.join(OUT, 'regression.json'), JSON.stringify(results, null, 1) + '\n');

  const byVp = {};
  for (const r of results) (byVp[r.viewport] = byVp[r.viewport] || []).push(r.pct);
  console.log('\nDolya razlichayushchihsya pikseley, %:');
  for (const [vp, arr] of Object.entries(byVp)) {
    const sorted = [...arr].sort((a, b) => a - b);
    const med = sorted[Math.floor(sorted.length / 2)];
    console.log(`  ${vp.padEnd(8)} stranic ${String(arr.length).padStart(3)} · median ${String(med).padStart(6)} · max ${String(Math.max(...arr)).padStart(6)}`);
  }
  console.log('\nHudshie 12:');
  for (const r of results.slice(0, 12)) {
    console.log(`  ${String(r.pct).padStart(6)}%  ${r.viewport.padEnd(7)} ${r.url}  (vysota ${r.heightBefore} -> ${r.heightAfter})`);
  }
};

main().catch((e) => { console.error('FAILED:', e.message); process.exit(1); });
