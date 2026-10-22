// GPS A3 · UX · карточка машины за период (/gps/machine) в настоящем браузере.
//
// То, чего DOM-тесты Python проверить не могут:
//   * карта периода встаёт из блока данных страницы: трек настоящей
//     фикстуры 3464, участок и контур поля; постоянных номеров участков на
//     ней нет, сутки и гектары -- в подсказке под курсором;
//   * сводка: 8,77 га участка фикстуры;
//   * быстрая кнопка оставляет машину;
//   * поле «Машина» подсказывает по части госномера и открывает выбранную;
//   * спецтехника: трек есть, участков нет, причина словами;
//   * нет горизонтальной прокрутки на 1280 и на 390 (телефон);
//   * RU и UZ; axe-core (serious/critical), если задан AXE.
//
// Стенд: python tools/ux/serve_gps_fact.py --port 5099 --state-dir <dir>
// Запуск: node tools/ux/check_gps_machine.mjs --base http://127.0.0.1:5099 \
//           --state <dir>/ux_admin.json --state-uz <dir>/ux_admin_uz.json
// Окружение: CHROME -- путь к браузеру; AXE -- путь к axe.min.js.
// Код выхода: 0 -- всё прошло, 1 -- есть провалы (каждый строкой '!!').

import { existsSync } from 'node:fs';

const args = Object.fromEntries(process.argv.slice(2).reduce((acc, v, i, a) =>
  (v.startsWith('--') ? acc.concat([[v.slice(2), a[i + 1]]]) : acc), []));
const BASE = args.base || 'http://127.0.0.1:5099';
const PERIOD = 'from=2026-07-26&to=2026-07-27';
const TRACTOR = `${BASE}/gps/machine?${PERIOD}&unit=3464`;
const LOADER = `${BASE}/gps/machine?${PERIOD}&unit=9001`;
const { chromium } = await import(process.env.PLAYWRIGHT_MODULE || 'playwright');

let failures = 0;
const bad = (msg) => { failures += 1; console.log('!! ' + msg); };
const ok = (msg) => console.log('ok ' + msg);
const expect = (cond, msg) => (cond ? ok(msg) : bad(msg));

const launch = process.env.CHROME ? { executablePath: process.env.CHROME } : {};
const browser = await chromium.launch(launch);

const TEXT = {
  ru: { ha: 'Обработано, га', legend: 'трек по суткам', week: 'Последние 7 суток',
        reason: 'Спецтехника — гектары не считаются', holland: 'New Holland' },
  uz: { ha: 'Ишланган, га', legend: 'кунлар бўйича трек', week: 'Охирги 7 кун',
        reason: 'Махсус техника — гектар ҳисобланмайди', holland: 'New Holland' },
};

function pageState(haLabel) {
  const host = document.querySelector('[data-vs-map]');
  const canvas = document.querySelector('.vs-map-canvas');
  const track = [...document.querySelectorAll('path.vs-map-track')];
  const de = document.documentElement;
  const box = canvas ? canvas.getBoundingClientRect() : { width: 0, height: 0 };
  const stat = [...document.querySelectorAll('.vs-stat')]
    .find((s) => s.querySelector('.vs-stat-label').textContent.trim() === haLabel);
  return {
    ready: host ? host.getAttribute('data-vs-map-ready') : null,
    tracks: track.length,
    vertices: track.reduce((n, p) => n + ((p.getAttribute('d') || '').match(/L/g) || []).length, 0),
    areas: document.querySelectorAll('path.vs-map-area').length,
    outlines: document.querySelectorAll('path.vs-map-outline').length,
    labels: document.querySelectorAll('.leaflet-tooltip.vs-map-label').length,
    canvas: [Math.round(box.width), Math.round(box.height)],
    doc: [de.scrollWidth, de.clientWidth],
    legend: (document.querySelector('.vs-map-legend') || { textContent: '' }).textContent,
    ha: stat ? stat.querySelector('.vs-stat-value').textContent.trim() : null,
    rows: document.querySelectorAll('tbody tr').length,
    body: document.body.innerText,
  };
}

async function open(state, url, size) {
  const ctx = await browser.newContext({ viewport: { width: size[0], height: size[1] }, storageState: state });
  const page = await ctx.newPage();
  const errors = [];
  page.on('pageerror', (e) => errors.push(String(e)));
  page.on('console', (m) => {
    // Плитки из интернета в песочнице могут не грузиться: это не ошибка страницы.
    if (m.type() === 'error' && !/Failed to load resource/.test(m.text())) errors.push(m.text());
  });
  const response = await page.goto(url, { waitUntil: 'load' });
  await page.waitForSelector('[data-vs-map-ready="1"]', { timeout: 10000 }).catch(() => {});
  return { ctx, page, errors, status: response ? response.status() : 0 };
}

async function checkTractor(state, lang, size) {
  const tag = `${lang} ${size[0]}x${size[1]}`;
  const { ctx, page, errors, status } = await open(state, TRACTOR, size);
  expect(status === 200 && !page.url().includes('/login'), `${tag} page loads (${status})`);
  const s = await page.evaluate(pageState, TEXT[lang].ha);
  expect(s.ready === '1', `${tag} map is mounted`);
  expect(s.tracks >= 1 && s.vertices > 100, `${tag} real track drawn (${s.tracks} line(s), ${s.vertices} vertices)`);
  expect(s.areas === 1, `${tag} one work site on the map (${s.areas})`);
  expect(s.outlines === 1, `${tag} one field contour on the map (${s.outlines})`);
  expect(s.labels === 0, `${tag} no permanent site numbers on the period map (${s.labels})`);
  expect(s.ha === '8.77', `${tag} hectares of the period (${s.ha})`);
  expect(s.rows === 2, `${tag} a row for each day of the period (${s.rows})`);
  expect(s.legend.includes(TEXT[lang].legend), `${tag} legend in ${lang}`);
  const minH = size[0] <= 768 ? 300 : 440;
  expect(s.canvas[0] > 250 && s.canvas[1] >= minH, `${tag} map has room (${s.canvas.join('x')})`);
  expect(s.doc[0] <= s.doc[1], `${tag} no horizontal page scroll (${s.doc.join(' > ')})`);
  // подсказка над участком: сутки и гектары
  // Без участка на карте наводить не на что: провал уже назван выше, а
  // ожидание элемента остановило бы весь прогон.
  let tip = '';
  if (s.areas) {
    await page.locator('path.vs-map-area').first().hover({ force: true, timeout: 3000 }).catch(() => {});
    tip = await page.locator('.leaflet-tooltip').first().textContent({ timeout: 3000 }).catch(() => '');
  }
  expect(/27\.07\.2026/.test(tip) && /8\.77/.test(tip), `${tag} hover names the day and the hectares (${JSON.stringify(tip)})`);
  // быстрая кнопка оставляет машину
  const week = await page.locator('.vs-pill', { hasText: TEXT[lang].week }).getAttribute('href');
  expect(/unit=3464/.test(week || '') && /preset=week/.test(week || ''), `${tag} quick button keeps the machine (${week})`);
  expect(errors.length === 0, `${tag} no page errors (${errors.join(' | ')})`);
  if (lang === 'ru' && size[0] >= 1280) await axe(page, tag);
  await ctx.close();
}

async function checkPicker(state, lang) {
  const tag = `${lang} picker`;
  const { ctx, page, errors } = await open(state, TRACTOR, [1280, 800]);
  const input = page.locator('#gps-machine-pick');
  await input.fill('156');
  const option = page.locator('[role="option"]:visible', { hasText: TEXT[lang].holland }).first();
  const seen = await option.isVisible({ timeout: 3000 }).catch(() => false);
  expect(seen, `${tag} typing part of the plate suggests the machine`);
  if (seen) {
    await Promise.all([page.waitForNavigation({ waitUntil: 'load' }), option.click()]);
    expect(/unit=102/.test(page.url()), `${tag} choosing it opens that machine (${page.url()})`);
  }
  expect(errors.length === 0, `${tag} no page errors (${errors.join(' | ')})`);
  await ctx.close();
}

async function checkLoader(state, lang) {
  const tag = `${lang} loader`;
  const { ctx, page, errors } = await open(state, LOADER, [1280, 800]);
  const s = await page.evaluate(pageState, TEXT[lang].ha);
  expect(s.ready === '1', `${tag} map is mounted`);
  expect(s.tracks >= 1 && s.areas === 0, `${tag} track without sites (${s.tracks}/${s.areas})`);
  expect(s.body.includes(TEXT[lang].reason), `${tag} reason spelled out in ${lang}`);
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
  await checkPicker(state, lang);
  await checkLoader(state, lang);
}

await browser.close();
console.log(failures ? `FAILED: ${failures}` : 'ALL OK');
process.exit(failures ? 1 : 0);
