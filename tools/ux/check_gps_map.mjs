// GPS A2 · UX · карта суток на /gps/fact в настоящем браузере.
//
// То, чего DOM-тесты Python проверить не могут: что Leaflet и vs-map.js
// действительно ставят карту из блока данных страницы.
//   * карта встала (data-vs-map-ready), запасная картинка спрятана;
//   * трек настоящей фикстуры 3464 нарисован сотнями вершин, участок и контур
//     поля -- по одному, номер участка стоит на карте, щелчок даёт подробности;
//   * цвета взяты из токенов дизайн-системы, а не синий Leaflet по умолчанию;
//   * подложка запрошена (плитки OSM; в песочнице сеть до них может быть
//     закрыта -- карта от этого не зависит, и это тоже проверяется);
//   * спецтехника: трек есть, участков нет, причина словами;
//   * в выпадающем списке машина с госномером;
//   * Leaflet не загрузился -> карта не встаёт, но запасная картинка видна и
//     ошибок страницы нет;
//   * нет горизонтальной прокрутки на 1280 и на 390 (телефон);
//   * RU и UZ; axe-core (serious/critical), если задан AXE.
//
// Стенд: python tools/ux/serve_gps_fact.py --port 5099 --state-dir <dir>
// Запуск: node tools/ux/check_gps_map.mjs --base http://127.0.0.1:5099 \
//           --state <dir>/ux_admin.json --state-uz <dir>/ux_admin_uz.json
// Окружение: CHROME -- путь к браузеру; AXE -- путь к axe.min.js.
// Код выхода: 0 -- всё прошло, 1 -- есть провалы (каждый строкой '!!').

import { existsSync } from 'node:fs';

const args = Object.fromEntries(process.argv.slice(2).reduce((acc, v, i, a) =>
  (v.startsWith('--') ? acc.concat([[v.slice(2), a[i + 1]]]) : acc), []));
const BASE = args.base || 'http://127.0.0.1:5099';
const TRACTOR = BASE + '/gps/fact?date=2026-07-27&unit=3464';
const LOADER = BASE + '/gps/fact?date=2026-07-27&unit=9001';
const { chromium } = await import(process.env.PLAYWRIGHT_MODULE || 'playwright');

let failures = 0;
const bad = (msg) => { failures += 1; console.log('!! ' + msg); };
const ok = (msg) => console.log('ok ' + msg);
const expect = (cond, msg) => (cond ? ok(msg) : bad(msg));

const launch = process.env.CHROME ? { executablePath: process.env.CHROME } : {};
const browser = await chromium.launch(launch);

const TEXT = {
  ru: { legend: 'трек за сутки', reason: 'Спецтехника — гектары не считаются',
        tractor: 'МТЗ-80.1 — 80 261 EA', loader: 'Погрузчик Amkodor — 80 373 HA' },
  uz: { legend: 'кунлик трек', reason: 'Махсус техника — гектар ҳисобланмайди',
        tractor: 'МТЗ-80.1 — 80 261 EA', loader: 'Погрузчик Amkodor — 80 373 HA' },
};

function mapState() {
  const host = document.querySelector('[data-vs-map]');
  const fallback = document.querySelector('.vs-map-fallback');
  const canvas = document.querySelector('.vs-map-canvas');
  const track = [...document.querySelectorAll('path.vs-map-track')];
  const probe = document.createElement('span');
  document.body.appendChild(probe);
  const token = (name) => { probe.style.color = `var(${name})`; return getComputedStyle(probe).color; };
  const tokens = { purple: token('--vs-purple'), primary: token('--vs-primary'), warning: token('--vs-warning') };
  probe.remove();
  const firstArea = document.querySelector('path.vs-map-area');
  const firstOutline = document.querySelector('path.vs-map-outline');
  const de = document.documentElement;
  const box = canvas ? canvas.getBoundingClientRect() : { width: 0, height: 0 };
  return {
    ready: host ? host.getAttribute('data-vs-map-ready') : null,
    fallbackHidden: fallback ? fallback.hidden : null,
    fallbackPolygons: document.querySelectorAll('.vs-map-fallback polygon').length,
    tracks: track.length,
    vertices: track.reduce((n, p) => n + ((p.getAttribute('d') || '').match(/L/g) || []).length, 0),
    areas: document.querySelectorAll('path.vs-map-area').length,
    outlines: document.querySelectorAll('path.vs-map-outline').length,
    labels: [...document.querySelectorAll('.leaflet-tooltip.vs-map-label')].map((e) => e.textContent.trim()),
    trackStroke: track[0] ? getComputedStyle(track[0]).stroke : null,
    areaStroke: firstArea ? getComputedStyle(firstArea).stroke : null,
    outlineStroke: firstOutline ? getComputedStyle(firstOutline).stroke : null,
    tokens,
    canvas: [Math.round(box.width), Math.round(box.height)],
    doc: [de.scrollWidth, de.clientWidth],
    legend: (document.querySelector('.vs-map-legend') || { textContent: '' }).textContent,
    options: [...document.querySelectorAll('select[name="unit"] option')].map((o) => o.textContent.trim()),
    body: document.body.innerText,
  };
}

async function open(state, url, size, blockLeaflet) {
  const ctx = await browser.newContext({ viewport: { width: size[0], height: size[1] }, storageState: state });
  const page = await ctx.newPage();
  const errors = [];
  const tiles = [];
  page.on('pageerror', (e) => errors.push(String(e)));
  page.on('console', (m) => {
    // Плитки из интернета в песочнице могут не грузиться: это не ошибка страницы.
    if (m.type() === 'error' && !/Failed to load resource/.test(m.text())) errors.push(m.text());
  });
  page.on('request', (r) => { if (/tile\.openstreetmap\.org|arcgis\.com/.test(r.url())) tiles.push(r.url()); });
  if (blockLeaflet) await page.route('**/vendor/leaflet/leaflet.js*', (route) => route.abort());
  const response = await page.goto(url, { waitUntil: 'load' });
  if (!blockLeaflet) {
    await page.waitForSelector('[data-vs-map-ready="1"]', { timeout: 10000 }).catch(() => {});
  } else {
    await page.waitForTimeout(500);
  }
  return { ctx, page, errors, tiles, status: response ? response.status() : 0 };
}

async function checkTractor(state, lang, size) {
  const tag = `${lang} ${size[0]}x${size[1]}`;
  const { ctx, page, errors, tiles, status } = await open(state, TRACTOR, size, false);
  expect(status === 200 && !page.url().includes('/login'), `${tag} page loads (${status})`);
  const s = await page.evaluate(mapState);
  expect(s.ready === '1', `${tag} map is mounted`);
  expect(s.fallbackHidden === true, `${tag} fallback picture hidden once the map stands`);
  expect(s.tracks >= 1 && s.vertices > 300, `${tag} real track drawn (${s.tracks} line(s), ${s.vertices} vertices)`);
  expect(s.areas === 1, `${tag} one work site on the map (${s.areas})`);
  expect(s.outlines === 1, `${tag} one field contour on the map (${s.outlines})`);
  expect(s.labels.includes('1'), `${tag} site number on the map (${JSON.stringify(s.labels)})`);
  expect(s.trackStroke === s.tokens.purple, `${tag} track colour is the token (${s.trackStroke} vs ${s.tokens.purple})`);
  expect(s.areaStroke === s.tokens.primary, `${tag} site colour is the token (${s.areaStroke} vs ${s.tokens.primary})`);
  expect(s.outlineStroke === s.tokens.warning, `${tag} contour colour is the token (${s.outlineStroke})`);
  expect(s.trackStroke !== 'rgb(51, 136, 255)', `${tag} not the default Leaflet blue`);
  const minH = size[0] <= 768 ? 300 : 440;
  expect(s.canvas[0] > 250 && s.canvas[1] >= minH, `${tag} map has room (${s.canvas.join('x')})`);
  expect(s.doc[0] <= s.doc[1], `${tag} no horizontal page scroll (${s.doc.join(' > ')})`);
  expect(s.legend.includes(TEXT[lang].legend), `${tag} legend in ${lang}`);
  // У погрузчика в списке законный хвост «· нет площади»: сравнивается начало.
  expect(s.options.some((o) => o.startsWith(TEXT[lang].tractor))
    && s.options.some((o) => o.startsWith(TEXT[lang].loader)),
    `${tag} machines listed with plates (${JSON.stringify(s.options)})`);
  expect(tiles.length > 0, `${tag} base layer asked for tiles (${tiles.length})`);
  // щелчок по участку -- подробности
  const area = page.locator('path.vs-map-area').first();
  await area.click({ force: true });
  const popup = await page.locator('.leaflet-popup-content').textContent({ timeout: 3000 }).catch(() => '');
  expect(/8\.77/.test(popup), `${tag} click on the site opens its details (${JSON.stringify(popup)})`);
  // [REASON]: окно подробностей появляется с плавным проявлением (0,2 с), и
  // axe, запущенный посреди него, меряет контраст полупрозрачного текста:
  // первый прогон дал color-contrast(1), три повторных после паузы -- ноль.
  // Проверяется страница, а не кадр анимации.
  await page.waitForTimeout(400);
  expect(errors.length === 0, `${tag} no page errors (${errors.join(' | ')})`);
  if (lang === 'ru' && size[0] >= 1280) await axe(page, tag);
  await ctx.close();
}

async function checkLoader(state, lang) {
  const tag = `${lang} loader`;
  const { ctx, page, errors } = await open(state, LOADER, [1280, 800], false);
  const s = await page.evaluate(mapState);
  expect(s.ready === '1', `${tag} map is mounted`);
  expect(s.tracks >= 1 && s.areas === 0, `${tag} track without sites (${s.tracks}/${s.areas})`);
  expect(s.body.includes(TEXT[lang].reason), `${tag} reason spelled out in ${lang}`);
  expect(errors.length === 0, `${tag} no page errors (${errors.join(' | ')})`);
  await ctx.close();
}

async function checkWithoutLeaflet(state) {
  const tag = 'ru no-leaflet';
  const { ctx, page, errors } = await open(state, TRACTOR, [1280, 800], true);
  const s = await page.evaluate(mapState);
  expect(s.ready === null, `${tag} map does not pretend to stand`);
  expect(s.fallbackHidden === false && s.fallbackPolygons === 1,
    `${tag} fallback picture stays visible (${s.fallbackHidden}, ${s.fallbackPolygons})`);
  expect(errors.length === 0, `${tag} no page errors (${errors.join(' | ')})`);
  await ctx.close();
}

async function axe(page, tag) {
  const axePath = process.env.AXE;
  if (!axePath || !existsSync(axePath)) { console.log('-- axe: skipped (AXE not set)'); return; }
  await page.addScriptTag({ path: axePath });
  const found = await page.evaluate(async () => (await window.axe.run(document)).violations
    .filter((v) => ['serious', 'critical'].includes(v.impact))
    .map((v) => `${v.id}(${v.nodes.length})`));
  expect(found.length === 0, `${tag} axe serious+critical: ${found.length ? found.join('; ') : 'none'}`);
}

for (const [lang, state] of [['ru', args.state], ['uz', args['state-uz']]]) {
  if (!state) { bad(`no --state for ${lang}`); continue; }
  for (const size of [[1280, 800], [390, 844]]) await checkTractor(state, lang, size);
  await checkLoader(state, lang);
}
if (args.state) await checkWithoutLeaflet(args.state);

await browser.close();
console.log(failures ? `FAILED: ${failures}` : 'ALL OK');
process.exit(failures ? 1 : 0);
