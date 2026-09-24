// DRONE-AREA-CONTROL-V2 · UX · «Контроль площади DJI» в настоящем браузере.
//
// То, чего DOM-тесты Python проверить не могут: скрипт дерева и ширину.
//   * по умолчанию видны только дроны;
//   * «Развернуть всё» раскрывает ВСЕ уровни, включая детали;
//   * «Развернуть проблемные» -- ровно пути к записям с data-problem и их детали;
//   * «Только дроны» сворачивает и помнит раскрытое внутри, «Свернуть всё» -- сбрасывает;
//   * одиночная кнопка раскрывает/сворачивает свой узел;
//   * на 1280/1366/1440/1920 нет горизонтальной прокрутки ни у документа, ни у таблицы;
//     шапка колонок не обрезана; строка вылета не вытянута в высоту;
//   * на 1024 документ не шире окна (таблица может прокручиваться сама -- запасной режим);
//   * при прокрутке шапка с командами остаётся под верхней панелью и не перекрыта;
//   * RU и UZ; axe-core (serious/critical), если задан AXE.
//
// Стенд: python tools/ux/serve_area_control.py --port 5099 --state-dir <dir>
// Запуск: node tools/ux/check_area_control.mjs --base http://127.0.0.1:5099 \
//           --state <dir>/ux_admin.json --state-uz <dir>/ux_admin_uz.json [--shots <dir>]
// Окружение: PLAYWRIGHT_MODULE -- путь/URL модуля playwright, если он не
// установлен рядом (по умолчанию 'playwright'); CHROME -- путь к браузеру,
// если нужен не встроенный; AXE -- путь к axe.min.js.
// Код выхода: 0 -- всё прошло, 1 -- есть провалы (каждый строкой '!!').

import { existsSync } from 'node:fs';
import { join } from 'node:path';

const args = Object.fromEntries(process.argv.slice(2).reduce((acc, v, i, a) =>
  (v.startsWith('--') ? acc.concat([[v.slice(2), a[i + 1]]]) : acc), []));
const BASE = args.base || 'http://127.0.0.1:5099';
const URL = BASE + '/drones/area-control';
const { chromium } = await import(process.env.PLAYWRIGHT_MODULE || 'playwright');

const DESKTOP = [[1280, 800], [1366, 768], [1440, 900], [1920, 1080]];
const FALLBACK = [[1024, 768]];
const TOP_BAR = 62;             // --vs-top-h
const MAX_FLIGHT_ROW_H = 72;    // две строки текста в ячейке и плашка -- максимум

let failures = 0;
const bad = (msg) => { failures += 1; console.log('!! ' + msg); };
const ok = (msg) => console.log('ok ' + msg);
const expect = (cond, msg) => (cond ? ok(msg) : bad(msg));

const launch = process.env.CHROME ? { executablePath: process.env.CHROME } : {};
const browser = await chromium.launch(launch);

// Снимок состояния дерева в странице.
function snapshot() {
  const table = document.querySelector('[data-area-tree]');
  const rows = [...table.querySelectorAll('tbody tr[data-level]')];
  const shown = (r) => !r.hidden && r.getClientRects().length > 0;
  const by = (level, onlyShown) => rows.filter((r) => r.dataset.level === level && (!onlyShown || shown(r)));
  const ids = (list, attr) => list.map((r) => r.getAttribute(attr)).sort();
  return {
    total: { l1: by('1').length, l2: by('2').length, l3: by('3').length, l4: by('4').length },
    shown: { l1: by('1', true).length, l2: by('2', true).length, l3: by('3', true).length, l4: by('4', true).length },
    shownDetails: ids(by('4', true), 'data-detail-for'),
    problemFlights: ids(rows.filter((r) => r.dataset.level === '3' && r.hasAttribute('data-problem')), 'data-flight'),
    problemDays: rows.filter((r) => r.dataset.level === '2' && r.hasAttribute('data-problem')).length,
    // Раскрыт -- кнопка узла в состоянии aria-expanded; видимость дня зависит
    // от дрона, раскрытость -- от самого дня.
    openDays: ids(by('2').filter((r) => r.querySelector('button[data-toggle]').getAttribute('aria-expanded') === 'true'), 'data-node'),
    problemDayIds: ids(rows.filter((r) => r.dataset.level === '2' && r.hasAttribute('data-problem')), 'data-node'),
    shownFlightParents: [...new Set(by('3', true).map((r) => r.dataset.parent))].sort(),
    heads: [...table.querySelectorAll('thead th')].length,
  };
}

// Геометрия: переполнение документа и таблицы, обрезанные заголовки, высота строк.
function geometry() {
  const de = document.documentElement;
  const table = document.querySelector('[data-area-tree]');
  const scroller = table.parentElement;
  const clipped = [...table.querySelectorAll('thead th')].filter((th) => th.scrollWidth > th.clientWidth + 1)
    .map((th) => th.textContent.trim());
  const flightRows = [...table.querySelectorAll('tbody tr[data-level="3"]')].filter((r) => !r.hidden);
  const maxRow = Math.max(0, ...flightRows.map((r) => r.getBoundingClientRect().height));
  const tallest = flightRows.reduce((a, r) => (r.getBoundingClientRect().height > (a ? a.getBoundingClientRect().height : 0) ? r : a), null);
  const detailItems = [...table.querySelectorAll('tbody tr[data-level="4"]:not([hidden]) .vs-def-item')];
  const narrowItem = Math.min(9999, ...detailItems.map((e) => e.getBoundingClientRect().width));
  return {
    doc: [de.scrollWidth, de.clientWidth],
    table: [table.scrollWidth, scroller.clientWidth],
    clipped, maxRow: Math.round(maxRow),
    tallest: tallest ? tallest.getAttribute('data-flight') || tallest.textContent.trim().slice(0, 40) : null,
    narrowItem: Math.round(narrowItem),
    cols: [...table.querySelectorAll('thead th')].map((th) => Math.round(th.getBoundingClientRect().width)),
  };
}

async function command(page, name) {
  await page.click(`[data-tree-expand="${name}"]`);
  return page.evaluate(snapshot);
}

async function checkViewport(state, lang, [w, h], shots) {
  const ctx = await browser.newContext({ viewport: { width: w, height: h }, storageState: state });
  const page = await ctx.newPage();
  const tag = `${lang} ${w}x${h}`;
  const response = await page.goto(URL, { waitUntil: 'load' });
  if (!response || response.status() !== 200 || page.url().includes('/login')) {
    bad(`${tag} page did not load (${response && response.status()}, ${page.url()})`);
    await ctx.close();
    return;
  }
  const desktop = w >= 1280;
  let s = await page.evaluate(snapshot);
  expect(s.heads === 6, `${tag} six column heads (${s.heads})`);
  expect(s.total.l1 > 0 && s.total.l3 > 0 && s.total.l4 > 0, `${tag} fixture has all levels ${JSON.stringify(s.total)}`);
  expect(s.shown.l1 === s.total.l1 && s.shown.l2 === 0 && s.shown.l3 === 0 && s.shown.l4 === 0,
    `${tag} default: drones only ${JSON.stringify(s.shown)}`);
  let g = await page.evaluate(geometry);
  expect(g.doc[0] <= g.doc[1] + 1, `${tag} default: no document overflow ${g.doc}`);
  if (shots) { await page.screenshot({ path: join(shots, `area_${lang}_${w}_default.png`), fullPage: true }); }

  // Развернуть всё: все уровни.
  s = await command(page, 'all');
  expect(JSON.stringify(s.shown) === JSON.stringify(s.total), `${tag} all: every level shown ${JSON.stringify(s.shown)}`);
  g = await page.evaluate(geometry);
  expect(g.doc[0] <= g.doc[1] + 1, `${tag} all: no document overflow ${g.doc}`);
  if (desktop) {
    expect(g.table[0] <= g.table[1] + 1, `${tag} all: table fits, no horizontal scroll ${g.table}`);
    expect(g.clipped.length === 0, `${tag} all: no clipped column head ${JSON.stringify(g.clipped)}`);
    expect(g.maxRow <= MAX_FLIGHT_ROW_H, `${tag} all: tallest flight row ${g.maxRow}px (${g.tallest}), cols ${g.cols}`);
    expect(g.narrowItem >= 160, `${tag} all: detail fields at least 160px wide (${g.narrowItem})`);
  }
  if (shots) { await page.screenshot({ path: join(shots, `area_${lang}_${w}_all.png`), fullPage: true }); }

  // Липкая шапка: прокрутить к середине раскрытого дерева.
  if (desktop) {
    const sticky = await page.evaluate((top) => {
      const table = document.querySelector('[data-area-tree]');
      window.scrollTo(0, table.getBoundingClientRect().top + window.scrollY + table.offsetHeight / 2);
      const head = table.querySelector('thead th').getBoundingClientRect();
      const button = table.querySelector('[data-tree-expand="none"]');
      const r = button.getBoundingClientRect();
      const hit = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
      return { headTop: Math.round(head.top), buttonTop: Math.round(r.top), onTop: hit === button || button.contains(hit), top };
    }, TOP_BAR);
    expect(sticky.buttonTop >= TOP_BAR - 1 && sticky.headTop < TOP_BAR + 120 && sticky.onTop,
      `${tag} scrolled: commands and heads stay under the top bar ${JSON.stringify(sticky)}`);
    if (shots && w === 1366) { await page.screenshot({ path: join(shots, `area_${lang}_${w}_scrolled.png`) }); }
  }

  // Свернуть всё: только дроны.
  s = await command(page, 'none');
  expect(s.shown.l1 === s.total.l1 && s.shown.l2 + s.shown.l3 + s.shown.l4 === 0, `${tag} none: drones only ${JSON.stringify(s.shown)}`);

  // Развернуть проблемные: пути к проблемным и их детали.
  s = await command(page, 'problems');
  expect(s.problemFlights.length > 0, `${tag} fixture has problem flights (${s.problemFlights.length})`);
  expect(JSON.stringify(s.shownDetails) === JSON.stringify(s.problemFlights),
    `${tag} problems: details shown = problem flights (${s.shownDetails.length}/${s.problemFlights.length})`);
  expect(JSON.stringify(s.openDays) === JSON.stringify(s.problemDayIds) && s.openDays.length < s.total.l2,
    `${tag} problems: exactly the problem days are open (${s.openDays.length} of ${s.total.l2})`);
  expect(JSON.stringify(s.shownFlightParents) === JSON.stringify(s.problemDayIds),
    `${tag} problems: flights are shown only under problem days (${s.shownFlightParents.length})`);
  if (shots) { await page.screenshot({ path: join(shots, `area_${lang}_${w}_problems.png`), fullPage: true }); }

  // Одиночные кнопки и память «Только дроны» против сброса «Свернуть всё».
  await command(page, 'none');
  const first = await page.evaluate(() => {
    const d1 = document.querySelector('tr[data-level="1"]').dataset.node;
    return { d1, day: document.querySelector(`tr[data-level="2"][data-parent="${d1}"]`).dataset.node };
  });
  await page.click(`button[data-toggle="${first.d1}"]`);
  s = await page.evaluate(snapshot);
  expect(s.shown.l2 > 0 && s.shown.l3 === 0, `${tag} toggle drone: its days shown, flights hidden ${JSON.stringify(s.shown)}`);
  await page.click(`button[data-toggle="${first.day}"]`);
  const openedFlights = (await page.evaluate(snapshot)).shown.l3;
  expect(openedFlights > 0, `${tag} toggle day: its flights shown (${openedFlights})`);
  await command(page, 'drones');
  await page.click(`button[data-toggle="${first.d1}"]`);
  s = await page.evaluate(snapshot);
  expect(s.shown.l3 === openedFlights, `${tag} «drones» remembers the open day (${s.shown.l3}/${openedFlights})`);
  await command(page, 'none');
  await page.click(`button[data-toggle="${first.d1}"]`);
  s = await page.evaluate(snapshot);
  expect(s.shown.l2 > 0 && s.shown.l3 === 0, `${tag} «none» resets the open day ${JSON.stringify(s.shown)}`);
  await ctx.close();
}

async function checkFallback(state, [w, h]) {
  const ctx = await browser.newContext({ viewport: { width: w, height: h }, storageState: state });
  const page = await ctx.newPage();
  await page.goto(URL, { waitUntil: 'load' });
  await page.click('[data-tree-expand="all"]');
  const g = await page.evaluate(geometry);
  expect(g.doc[0] <= g.doc[1] + 1, `ru ${w}x${h} fallback: document never wider than the window ${g.doc} (table ${g.table})`);
  await ctx.close();
}

async function checkAxe(state) {
  const axePath = process.env.AXE;
  if (!axePath || !existsSync(axePath)) { console.log('-- axe: skipped (AXE not set)'); return; }
  const ctx = await browser.newContext({ viewport: { width: 1366, height: 768 }, storageState: state });
  const page = await ctx.newPage();
  await page.goto(URL, { waitUntil: 'load' });
  await page.click('[data-tree-expand="all"]');
  await page.addScriptTag({ path: axePath });
  const found = await page.evaluate(async () => (await window.axe.run(document)).violations
    .filter((v) => v.impact === 'serious' || v.impact === 'critical')
    .map((v) => `${v.id} x${v.nodes.length}: ${v.nodes.slice(0, 2).map((n) => n.target.join(' ')).join(' | ')}`));
  expect(found.length === 0, `axe serious+critical on the expanded tree: ${found.length ? found.join('; ') : 'none'}`);
  await ctx.close();
}

const shots = args.shots || null;
for (const vp of DESKTOP) { await checkViewport(args.state, 'ru', vp, shots); }
for (const vp of [[1366, 768], [1920, 1080]]) { await checkViewport(args['state-uz'], 'uz', vp, shots); }
for (const vp of FALLBACK) { await checkFallback(args.state, vp); }
await checkAxe(args.state);
await browser.close();
console.log(failures ? `FAILED: ${failures}` : 'ALL OK');
process.exit(failures ? 1 : 0);
