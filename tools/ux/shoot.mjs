// tools/ux/shoot.mjs -- vizualnyy bazis Vehicle Soft.
//
// Snimaet marshruty iz docs/ux/11-baseline/routes.json na chetyreh vyeportah,
// zamerjaet priznaki kazhdoy stranicy i progonyaet axe. Rezultat -- PNG plus
// mashinochitaemyy otchet.
//
// Determinizm obyazatelen: bazis peresnimaetsya posle kazhdogo PR fazy
// migracii i sravnivaetsya s predydushchim. Poetomu vyklyucheny animacii i
// karetka, zafiksirovany chasovoy poyas, lokal i deviceScaleFactor, i pered
// snimkom zhdetsya document.fonts.ready.
//
// Obhod idet po YAVNOMU spisku URL, bez perekhoda po ssylkam: nabor ssylok
// zavisit ot prav i dannyh i mezhdu progonami ne vosproizvoditsya.
//
// Zapusk (server dolzhen byt podnyat otdelno):
//   python tools/ux/serve_ephemeral.py --port 5099 &
//   PLAYWRIGHT_BROWSERS_PATH=/opt/pw-browsers node tools/ux/shoot.mjs
//   ... --role ux_operator --lang uz --viewports mobile --limit 20

import { chromium } from 'playwright';
import { AxeBuilder } from '@axe-core/playwright';
import fs from 'node:fs';
import path from 'node:path';

const ROOT = path.resolve(import.meta.dirname, '..', '..');
const OUT = path.join(ROOT, 'docs', 'ux', '11-baseline');
const BASE = process.env.UX_BASE || 'http://127.0.0.1:5099';
const CHROME = '/opt/pw-browsers/chromium-1194/chrome-linux/chrome';

const VIEWPORTS = {
  desktop: { width: 1920, height: 1080 },
  laptop:  { width: 1440, height: 900 },
  tablet:  { width: 1024, height: 768 },
  mobile:  { width: 390,  height: 844 },
};

function arg(name, fallback) {
  const i = process.argv.indexOf('--' + name);
  return i > -1 && process.argv[i + 1] ? process.argv[i + 1] : fallback;
}

const ROLE = arg('role', 'ux_admin');
const PASSWORD = arg('password', 'ux-audit-local');
const LANG = arg('lang', '');
const LIMIT = parseInt(arg('limit', '0'), 10);
const ONLY = arg('viewports', Object.keys(VIEWPORTS).join(',')).split(',');
const RUN_AXE = arg('axe', '1') === '1';

// Gasit vsyo, chto menyaetsya mezhdu progonami samo po sebe.
const FREEZE_CSS = `
*, *::before, *::after {
  animation-duration: 0s !important; animation-delay: 0s !important;
  transition-duration: 0s !important; transition-delay: 0s !important;
  caret-color: transparent !important;
}
html { scroll-behavior: auto !important; }`;

// Zamer priznakov stranicy. Vypolnyaetsya v brauzere.
const PROBE = () => {
  const emoji = /[\u{1F000}-\u{1FAFF}\u{2600}-\u{27BF}\u{2B00}-\u{2BFF}]/gu;
  const tables = [...document.querySelectorAll('table')];
  const de = document.documentElement;
  return {
    title: document.title,
    htmlBytes: document.documentElement.outerHTML.length,
    tables: tables.length,
    tableClasses: [...new Set(tables.map(t => t.className.trim() || '(no class)'))].sort(),
    inlineStyleBlocks: document.querySelectorAll('style').length,
    inlineStyleAttrs: document.querySelectorAll('[style]').length,
    glyphs: (document.body.innerText.match(emoji) || []).length,
    // Gorizontalnoe perepolnenie -- pryamoe obnaruzhenie slomannoy adaptivnosti.
    horizontalOverflow: de.scrollWidth > de.clientWidth + 1,
    overflowBy: Math.max(0, de.scrollWidth - de.clientWidth),
    forms: document.querySelectorAll('form').length,
    // Polya bez dostupnogo imeni -- to, chto axe schitaet po-svoemu, no zdes
    // deshevle i tochnee dlya form.
    inputsWithoutLabel: [...document.querySelectorAll('input,select,textarea')]
      .filter(el => {
        if (el.type === 'hidden') return false;
        if (el.getAttribute('aria-label') || el.getAttribute('aria-labelledby')) return false;
        if (el.id && document.querySelector(`label[for="${CSS.escape(el.id)}"]`)) return false;
        return !el.closest('label');
      }).length,
  };
};

async function login(page) {
  await page.goto(`${BASE}/login`, { waitUntil: 'domcontentloaded' });
  await page.fill('input[name="username"]', ROLE);
  await page.fill('input[name="password"]', PASSWORD);
  // [REASON]: na stranice vhoda DVE formy -- pereklyuchatel yazyka stoit v
  // razmetke RANSHE formy vhoda, i ego knopki tozhe type="submit". Selektor
  // bez privyazki k .vs-login-form nazhimaet yazyk i vhod ne proiskhodit.
  await Promise.all([
    page.waitForLoadState('domcontentloaded'),
    page.click('form.vs-login-form button[type="submit"]'),
  ]);
  if (page.url().includes('/login')) throw new Error(`login failed for ${ROLE}`);
}

const main = async () => {
  const routes = JSON.parse(fs.readFileSync(path.join(OUT, 'routes.json'), 'utf8')).routes
    .filter(r => !r.url.startsWith('/api/'))
    .filter(r => !/logout|export|\.xlsx|\.pdf|download/i.test(r.url));
  const list = LIMIT ? routes.slice(0, LIMIT) : routes;

  const browser = await chromium.launch({ executablePath: CHROME });
  const results = [];
  const a11y = {};
  const errors = [];

  for (const vp of ONLY) {
    if (!VIEWPORTS[vp]) continue;
    const ctx = await browser.newContext({
      viewport: VIEWPORTS[vp],
      deviceScaleFactor: 1,
      locale: 'ru-RU',
      timezoneId: 'Asia/Tashkent',
      reducedMotion: 'reduce',
    });
    const page = await ctx.newPage();
    await page.addStyleTag({ content: FREEZE_CSS }).catch(() => {});
    await login(page);

    const dir = path.join(OUT, LANG ? `${vp}-${LANG}` : vp);
    fs.mkdirSync(dir, { recursive: true });

    for (const route of list) {
      const url = BASE + route.url + (LANG ? (route.url.includes('?') ? '&' : '?') + 'lang=' + LANG : '');
      const started = Date.now();
      let status = 0;
      try {
        const resp = await page.goto(url, { waitUntil: 'networkidle', timeout: 30000 });
        status = resp ? resp.status() : 0;
        await page.addStyleTag({ content: FREEZE_CSS }).catch(() => {});
        await page.evaluate(() => document.fonts && document.fonts.ready).catch(() => {});
        await page.evaluate(() => window.scrollTo(0, 0));

        const probe = await page.evaluate(PROBE);
        const file = path.join(dir, route.slug + '.png');
        await page.screenshot({ path: file, fullPage: true });

        results.push({ viewport: vp, lang: LANG || 'default', role: ROLE,
          url: route.url, endpoint: route.endpoint, status,
          ms: Date.now() - started, file: path.relative(ROOT, file), ...probe });

        if (RUN_AXE && vp === 'desktop') {
          try {
            const scan = await new AxeBuilder({ page })
              .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa']).analyze();
            const bySeverity = {};
            for (const v of scan.violations) {
              bySeverity[v.impact || 'unknown'] = (bySeverity[v.impact || 'unknown'] || 0) + v.nodes.length;
            }
            a11y[route.url] = { bySeverity,
              rules: scan.violations.map(v => ({ id: v.id, impact: v.impact, nodes: v.nodes.length }))
                .sort((a, b) => a.id.localeCompare(b.id)) };
          } catch (e) { a11y[route.url] = { error: String(e.message).slice(0, 200) }; }
        }
        if (status >= 400) errors.push({ viewport: vp, url: route.url, status, note: 'HTTP error' });
      } catch (e) {
        errors.push({ viewport: vp, url: route.url, status, note: String(e.message).slice(0, 200) });
      }
    }
    await ctx.close();
    console.log(`${vp}${LANG ? '/' + LANG : ''}: ${list.length} routes done`);
  }
  await browser.close();

  const suffix = (LANG ? `-${LANG}` : '') + (ROLE === 'ux_admin' ? '' : `-${ROLE}`);
  results.sort((a, b) => (a.viewport + a.url).localeCompare(b.viewport + b.url));
  errors.sort((a, b) => (a.viewport + a.url).localeCompare(b.viewport + b.url));

  // [REASON]: vtoroy argument JSON.stringify -- eto REPLACER. Massiv klyuchey
  // rabotaet kak belyy spisok SVOYSTV, poetomu JSON.stringify(a11y, keys, 1)
  // vyrezal bySeverity/rules i ostavlyal {} po kazhdomu marshrutu. Poryadok
  // klyuchey nuzhno zadavat peresborkoy obyekta, a ne replacerom.
  const sortedKeys = (obj) => Object.fromEntries(
    Object.keys(obj).sort().map((k) => [k, obj[k]]));

  fs.writeFileSync(path.join(OUT, `pages${suffix}.json`), JSON.stringify(results, null, 1) + '\n');
  if (RUN_AXE) fs.writeFileSync(path.join(OUT, `a11y-baseline${suffix}.json`),
    JSON.stringify(sortedKeys(a11y), null, 1) + '\n');
  fs.writeFileSync(path.join(OUT, `errors${suffix}.json`), JSON.stringify(errors, null, 1) + '\n');

  const overflow = results.filter(r => r.horizontalOverflow);
  console.log(`shots: ${results.length}, errors: ${errors.length}, horizontal overflow: ${overflow.length}`);
};

main().catch(e => { console.error('FAILED:', e.message); process.exit(1); });
