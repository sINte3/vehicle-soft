// GPS · /gps/fact · выбор машины и крупная карта в настоящем браузере.
//
// Замечания владельца 09.10.2026: машину искать набором номера с
// подсказками, фильтры по организации и виду техники, имена вместо
// четырёхзначных номеров; карта крупнее, во весь экран, масштаб колесом.
// DOM-тесты Python (tests/test_gps_fact_picker.py) видят разметку, но не
// поведение -- его проверяет этот файл:
//   * набор «156ca» латиницей находит «80 156 СА», записанный русскими
//     буквами; Enter открывает машину; стрелки и щелчок мышью -- тоже;
//     «zzz» -- «нет такой», Enter ничего не делает; Escape возвращает имя;
//   * смена организации и «только без ответа» сразу показывают новый
//     список с начала; смена суток оставляет ту же машину;
//   * набранный номер машины, которую спрятал фильтр, назван строкой «скрыто
//     фильтрами: N» со ссылкой, открывающей её без фильтров;
//   * карта по высоте экрана; кнопка «во весь экран» разворачивает её и
//     сворачивает обратно;
//   * колесо меняет масштаб сразу, без щелчка, но не отнимает прокрутку
//     страницы, пока страница листается;
//   * ответ «проезд» возвращает к той же машине, к строке карты и к тому же
//     виду карты, а участок перекрашен;
//   * карта не всплывает поверх липкой шапки при прокрутке (с отрицательным
//     контролем: без isolation проверка это видит);
//   * карта и участки рядом на широком экране и одна под другой на узком;
//     горизонтальной прокрутки нет; axe (serious/critical), если задан AXE.
//
// Стенд: python tools/ux/serve_gps_fact.py --port 5099 --state-dir <dir>
// Запуск: node tools/ux/check_gps_fact_picker.mjs --base http://127.0.0.1:5099 \
//           --state <dir>/ux_admin.json --state-uz <dir>/ux_admin_uz.json
// Окружение: CHROME -- путь к браузеру; AXE -- путь к axe.min.js.
// Код выхода: 0 -- всё прошло, 1 -- есть провалы (каждый строкой '!!').
// Проверка отвечает «проездом» и снимает ответ обратно: стенд остаётся как был.

import { existsSync } from 'node:fs';

const args = Object.fromEntries(process.argv.slice(2).reduce((acc, v, i, a) =>
  (v.startsWith('--') ? acc.concat([[v.slice(2), a[i + 1]]]) : acc), []));
const BASE = args.base || 'http://127.0.0.1:5099';
const DAY = BASE + '/gps/fact?date=2026-07-27';
const TRACTOR = DAY + '&unit=3464';
const { chromium } = await import(process.env.PLAYWRIGHT_MODULE || 'playwright');

let failures = 0;
const bad = (msg) => { failures += 1; console.log('!! ' + msg); };
const ok = (msg) => console.log('ok ' + msg);
const expect = (cond, msg) => (cond ? ok(msg) : bad(msg));

const launch = process.env.CHROME ? { executablePath: process.env.CHROME } : {};
const browser = await chromium.launch(launch);

async function open(state, url, size) {
  const ctx = await browser.newContext({ viewport: { width: size[0], height: size[1] }, storageState: state });
  const page = await ctx.newPage();
  const errors = [];
  page.on('pageerror', (e) => errors.push(String(e)));
  page.on('console', (m) => {
    if (m.type() === 'error' && !/Failed to load resource/.test(m.text())) errors.push(m.text());
  });
  await page.goto(url, { waitUntil: 'load' });
  await page.waitForSelector('[data-vs-combobox-ready="1"]', { timeout: 10000 }).catch(() => {});
  if (await page.locator('[data-vs-map]').count()) {
    await page.waitForSelector('[data-vs-map-ready="1"]', { timeout: 10000 }).catch(() => {});
  }
  return { ctx, page, errors };
}

const shownOptions = (page) => page.$$eval('[role="option"]', (els) => els
  .filter((e) => !e.hidden).map((e) => e.querySelector('.vs-combobox-main').textContent.trim()));
const unitOf = (page) => new URL(page.url()).searchParams.get('unit');
const headName = (page) => page.locator('.gps-fact-head-name').textContent().then((t) => t.trim());
const viewOf = (page) => page.$eval('input[data-vs-map-view-of="gps-fact-map"]', (e) => e.value).catch(() => '');
const zoomOf = (view) => parseFloat((view || 'NaN').split('/')[0]);

async function checkTyping(state) {
  const tag = 'picker';
  const { ctx, page, errors } = await open(state, TRACTOR, [1440, 900]);
  const input = page.locator('#gps-fact-machine');
  expect((await input.inputValue()) === 'МТЗ-80.1 — 80 261 EA', `${tag} field shows the open machine by name`);

  await input.click();
  const all = await shownOptions(page);
  expect(all.length === 5 && !all.some((t) => /^\d+$/.test(t)),
    `${tag} focus opens the whole list, no bare numbers (${JSON.stringify(all)})`);
  expect(all.includes('Т-28 80 990 HA'), `${tag} object without a mapping is named from the collector`);
  expect((await input.getAttribute('aria-expanded')) === 'true', `${tag} aria-expanded follows the list`);

  await input.pressSequentially('156ca');
  const found = await shownOptions(page);
  expect(found.length === 1 && found[0] === 'New Holland 7060 — 80 156 СА',
    `${tag} latin "156ca" finds the plate written in cyrillic (${JSON.stringify(found)})`);
  await Promise.all([page.waitForURL(/unit=102/, { timeout: 5000 }).catch(() => {}), input.press('Enter')]);
  expect(unitOf(page) === '102', `${tag} Enter opens the only match (${page.url()})`);
  expect((await headName(page)) === 'New Holland 7060 — 80 156 СА', `${tag} the opened machine is named in the head`);

  await page.waitForSelector('[data-vs-combobox-ready="1"]');
  await input.click();
  await input.pressSequentially('777');
  await input.press('ArrowDown');
  const active = await input.getAttribute('aria-activedescendant');
  expect(active === 'gps-fact-machine-103', `${tag} ArrowDown marks the match (${active})`);
  await Promise.all([page.waitForURL(/unit=103/, { timeout: 5000 }).catch(() => {}), input.press('Enter')]);
  expect(unitOf(page) === '103', `${tag} Enter on the marked option opens it (${page.url()})`);

  await page.waitForSelector('[data-vs-combobox-ready="1"]');
  await input.click();
  await input.pressSequentially('zzz');
  const empty = await page.locator('.vs-combobox-empty').isVisible();
  expect(empty && (await shownOptions(page)).length === 0, `${tag} nothing found is said`);
  const before = page.url();
  await input.press('Enter');
  await page.waitForTimeout(600);
  expect(page.url() === before, `${tag} Enter with nothing found stays put`);
  await input.press('Escape');
  expect((await input.inputValue()) === 'Камаз 80 777 KA', `${tag} Escape gives the name back`);

  await input.click();
  await Promise.all([page.waitForURL(/unit=7002/, { timeout: 5000 }).catch(() => {}),
    page.locator('#gps-fact-machine-7002').click()]);
  expect(unitOf(page) === '7002', `${tag} a click on a suggestion opens it (${page.url()})`);
  expect(errors.length === 0, `${tag} no page errors (${errors.join(' | ')})`);
  await ctx.close();
}

async function checkFilters(state) {
  const tag = 'filters';
  const { ctx, page, errors } = await open(state, TRACTOR, [1440, 900]);
  await Promise.all([page.waitForURL(/org=/, { timeout: 5000 }).catch(() => {}),
    page.selectOption('select[name="org"]', { label: 'Когон ПТЗ' })]);
  const url = new URL(page.url());
  expect(url.searchParams.get('org') && !url.searchParams.get('unit') && !url.searchParams.get('q'),
    `${tag} organisation applies at once and starts the list anew (${page.url()})`);
  expect(JSON.stringify(await shownOptions(page)) === JSON.stringify(['New Holland 7060 — 80 156 СА']),
    `${tag} only that organisation is listed (${JSON.stringify(await shownOptions(page))})`);
  expect((await headName(page)) === 'New Holland 7060 — 80 156 СА', `${tag} its machine is opened`);

  await page.goto(DAY, { waitUntil: 'load' });
  await Promise.all([page.waitForURL(/open=1/, { timeout: 5000 }).catch(() => {}),
    page.locator('input[name="open"]').check()]);
  const left = await shownOptions(page);
  expect(JSON.stringify(left) === JSON.stringify(['МТЗ-80.1 — 80 261 EA']),
    `${tag} "only unanswered" lists machines with a site left (${JSON.stringify(left)})`);

  await page.goto(TRACTOR, { waitUntil: 'load' });
  await Promise.all([page.waitForURL(/date=2026-07-26/, { timeout: 5000 }).catch(() => {}),
    page.selectOption('select[name="date"]', '2026-07-26')]);
  expect(unitOf(page) === '3464' && (await headName(page)) === 'МТЗ-80.1 — 80 261 EA',
    `${tag} another day keeps the same machine (${page.url()})`);
  expect(!(await page.locator('.gps-fact-found .vs-hint:visible').count()),
    `${tag} and does not claim a failed search`);
  expect(errors.length === 0, `${tag} no page errors (${errors.join(' | ')})`);
  await ctx.close();
}

// Совпадения, которые спрятали фильтры (шаг 4 v1.27: «527» не нашёл Puma-210,
// её прятала галочка «только без ответа»). С галочкой в списке только трактор
// 80 261 EA; New Holland, Камаз, Т-28 и погрузчик скрыты.
async function checkHidden(state, lang) {
  const tag = `hidden ${lang}`;
  const { ctx, page, errors } = await open(state, DAY + '&open=1', [1440, 900]);
  const input = page.locator('#gps-fact-machine');
  const more = page.locator('#gps-fact-more');
  const moreText = () => page.locator('[data-vs-more-text]').textContent().then((t) => t.trim());
  const say = (q, n) => (lang === 'ru' ? `По «${q}» скрыто фильтрами машин: ${n}.`
    : `«${q}» бўйича фильтрлар яширган машиналар: ${n}.`);
  expect(!(await more.isVisible()), `${tag} no line before anything is typed`);

  await input.click();
  await input.pressSequentially('156');
  expect((await more.isVisible()) && (await moreText()) === say('156', 1),
    `${tag} a number the filter hides is counted (${await moreText()})`);
  expect(await page.locator('.vs-combobox-empty').isVisible(), `${tag} and the list says none of the shown match`);
  const href = await page.locator('[data-vs-more-link]').getAttribute('href');
  expect(/[?&]q=156#gps-fact-work$/.test(href) && !/open=/.test(href),
    `${tag} the way to it keeps the number and drops the filters (${href})`);

  await input.fill('{n}ew');
  expect((await moreText()) === say('{n}ew', 1), `${tag} typed braces stay as typed (${await moreText()})`);
  await input.fill('261');
  expect(!(await more.isVisible()), `${tag} a number only the shown machine has: no line`);
  await input.fill('80');
  expect((await moreText()) === say('80', 4), `${tag} every hidden match is counted (${await moreText()})`);
  await input.press('Escape');
  expect(!(await more.isVisible()), `${tag} Escape takes the line away with the list`);

  await input.fill('');
  await input.pressSequentially('156');
  await Promise.all([page.waitForURL(/q=156/, { timeout: 5000 }).catch(() => {}),
    page.locator('[data-vs-more-link]').click()]);
  await page.waitForSelector('[data-vs-combobox-ready="1"]', { timeout: 10000 }).catch(() => {});
  expect((await headName(page)) === 'New Holland 7060 — 80 156 СА'
    && !(await page.locator('input[name="open"]').isChecked()),
  `${tag} the link opens the hidden machine with the filters off (${page.url()})`);
  expect(!(await more.isVisible()), `${tag} and there is nothing hidden any more`);
  expect(errors.length === 0, `${tag} no page errors (${errors.join(' | ')})`);
  await ctx.close();
}

async function canvasBox(page) {
  return page.$eval('.vs-map-canvas', (e) => { const r = e.getBoundingClientRect(); return [Math.round(r.width), Math.round(r.height)]; });
}

async function checkMap(state, lang) {
  const tag = `map ${lang}`;
  const { ctx, page, errors } = await open(state, TRACTOR, [1440, 900]);
  const box = await canvasBox(page);
  expect(box[1] >= 560, `${tag} map is tall: by the screen, not 460 px (${box.join('x')})`);
  const label = lang === 'ru' ? 'Развернуть карту на весь экран' : 'Харитани тўлиқ экранга ёйиш';
  const tool = page.locator(`a.vs-map-tool[aria-label="${label}"]`);
  expect((await tool.count()) === 1, `${tag} full-screen button is there and named in ${lang}`);
  await tool.click();
  await page.waitForTimeout(600);
  const full = await page.evaluate(() => {
    const host = document.querySelector('[data-vs-map]');
    return { api: document.fullscreenElement === host, cls: host.classList.contains('is-expanded'),
      flag: host.getAttribute('data-vs-map-full') };
  });
  const big = await canvasBox(page);
  expect((full.api || full.cls) && full.flag === '1',
    `${tag} the button expands the map (${JSON.stringify(full)})`);
  expect(big[0] >= 1400 && big[1] >= 820, `${tag} expanded map fills the screen (${big.join('x')})`);
  const exitLabel = lang === 'ru' ? 'Свернуть карту' : 'Харитани кичрайтириш';
  expect((await page.locator(`a.vs-map-tool[aria-label="${exitLabel}"]`).count()) === 1,
    `${tag} the same button now says how to go back`);
  const fullFlag = () => page.$eval('[data-vs-map]', (e) => e.getAttribute('data-vs-map-full'));
  await page.locator('a.vs-map-tool').first().click();
  await page.waitForTimeout(600);
  let back = await canvasBox(page);
  expect((await fullFlag()) === '0' && back[1] === box[1], `${tag} the button brings it back (${back.join('x')})`);

  // Escape в настоящем браузере выводит из полноэкранного режима сам браузер
  // (странице клавиша не приходит) -- здесь это его выход, exitFullscreen().
  await page.locator('a.vs-map-tool').first().click();
  await page.waitForTimeout(400);
  await page.evaluate(() => document.fullscreenElement && document.exitFullscreen());
  await page.waitForTimeout(600);
  back = await canvasBox(page);
  expect((await fullFlag()) === '0' && back[1] === box[1], `${tag} leaving full screen by the browser brings it back (${back.join('x')})`);

  // Браузер без Fullscreen API или отказавший в нём (iPhone): карта ложится
  // поверх страницы классом, и Escape обрабатывает уже сама страница.
  await page.evaluate(() => { Element.prototype.requestFullscreen = () => Promise.reject(new Error('denied')); });
  await page.locator('a.vs-map-tool').first().click();
  await page.waitForTimeout(600);
  const expanded = await page.$eval('[data-vs-map]', (e) => e.classList.contains('is-expanded'));
  const over = await canvasBox(page);
  expect(expanded && over[0] >= 1400 && over[1] >= 820, `${tag} refused full screen falls back to covering the page (${over.join('x')})`);
  await page.keyboard.press('Escape');
  await page.waitForTimeout(600);
  back = await canvasBox(page);
  expect((await fullFlag()) === '0' && back[1] === box[1], `${tag} Escape closes the covering map (${back.join('x')})`);
  expect(errors.length === 0, `${tag} no page errors (${errors.join(' | ')})`);
  await ctx.close();
}

async function checkWheel(state) {
  const tag = 'wheel';
  const { ctx, page, errors } = await open(state, TRACTOR, [1440, 900]);
  const rect = await page.$eval('.vs-map-canvas', (e) => { const r = e.getBoundingClientRect(); return { x: r.x, y: r.y, w: r.width, h: r.height }; });
  const x = rect.x + rect.w / 2;
  const y = Math.min(rect.y + rect.h / 2, 860);
  await page.mouse.move(x, y);

  // Страница листается -- колесо над картой листает её дальше, масштаб стоит.
  const z0 = zoomOf(await viewOf(page));
  const y0 = await page.evaluate(() => window.scrollY);
  await page.evaluate(() => window.scrollBy(0, 30));
  await page.mouse.wheel(0, 240);
  await page.waitForTimeout(500);
  const y1 = await page.evaluate(() => window.scrollY);
  const zGuard = zoomOf(await viewOf(page));
  expect(y1 - y0 > 120 && zGuard === z0,
    `${tag} a page in the middle of scrolling keeps scrolling (scrollY ${y0} -> ${y1}, zoom ${z0} -> ${zGuard})`);

  // Страница стоит -- колесо над картой меняет масштаб, без щелчка.
  await page.waitForTimeout(800);
  const yStill = await page.evaluate(() => window.scrollY);
  const rect2 = await page.$eval('.vs-map-canvas', (e) => { const r = e.getBoundingClientRect(); return { x: r.x, y: r.y, w: r.width, h: r.height }; });
  await page.mouse.move(rect2.x + rect2.w / 2, Math.max(rect2.y + 60, Math.min(rect2.y + rect2.h / 2, 860)));
  await page.mouse.wheel(0, -300);
  await page.waitForTimeout(900);
  const z1 = zoomOf(await viewOf(page));
  const y2 = await page.evaluate(() => window.scrollY);
  expect(z1 > z0 && y2 === yStill, `${tag} a still page gives the wheel to the map (zoom ${z0} -> ${z1}, scrollY ${yStill} -> ${y2})`);
  expect(errors.length === 0, `${tag} no page errors (${errors.join(' | ')})`);
  await ctx.close();
}

async function checkAnswerKeepsPlace(state) {
  const tag = 'answer';
  const { ctx, page, errors } = await open(state, TRACTOR + '&cat=mtz', [1440, 900]);
  const rect = await page.$eval('.vs-map-canvas', (e) => { const r = e.getBoundingClientRect(); return { x: r.x, y: r.y, w: r.width, h: r.height }; });
  await page.mouse.move(rect.x + rect.w / 2, Math.min(rect.y + rect.h / 2, 860));
  await page.waitForTimeout(700);
  await page.mouse.wheel(0, -300);
  await page.waitForTimeout(900);
  const before = await viewOf(page);
  await Promise.all([page.waitForURL(/view=/, { timeout: 8000 }).catch(() => {}),
    page.locator('.gps-fact-sites button[value="проезд"]').first().click()]);
  await page.waitForSelector('[data-vs-map-ready="1"]', { timeout: 10000 }).catch(() => {});
  await page.waitForTimeout(500);
  const url = new URL(page.url());
  expect(url.searchParams.get('unit') === '3464' && url.searchParams.get('cat') === 'mtz' && url.hash === '#gps-fact-work',
    `${tag} returns to the same machine with its filters and to the map row (${page.url()})`);
  const after = await viewOf(page);
  const [za, la, oa] = before.split('/').map(parseFloat);
  const [zb, lb, ob] = after.split('/').map(parseFloat);
  expect(za === zb && Math.abs(la - lb) < 1e-4 && Math.abs(oa - ob) < 1e-4,
    `${tag} the map stays where it was left (${before} -> ${after})`);
  const top = await page.$eval('#gps-fact-work', (e) => Math.round(e.getBoundingClientRect().top));
  expect(top >= 0 && top <= 140, `${tag} the page opens at the map row (top ${top})`);
  expect((await page.locator('path.vs-map-area.is-danger').count()) === 1, `${tag} the site is painted as a passage`);
  // вернуть стенд как был
  await Promise.all([page.waitForURL(/view=/, { timeout: 8000 }).catch(() => {}),
    page.locator('.gps-fact-sites button[value=""]').first().click()]);
  await page.waitForSelector('[data-vs-map-ready="1"]', { timeout: 10000 }).catch(() => {});
  expect((await page.locator('path.vs-map-area.is-primary').count()) === 1, `${tag} the answer is taken back`);
  expect(errors.length === 0, `${tag} no page errors (${errors.join(' | ')})`);
  await ctx.close();
}

async function checkTopbar(state) {
  const tag = 'topbar';
  const { ctx, page } = await open(state, TRACTOR, [1440, 900]);
  // Цель -- кнопки масштаба Leaflet: они есть всегда (плитки в песочнице
  // могут не загрузиться, и в пустом месте карты сравнивать было бы нечего).
  // Страница сдвигается так, чтобы кнопки встали под середину шапки.
  const probe = () => page.evaluate(() => {
    const bar = document.querySelector('.vs-topbar').getBoundingClientRect();
    const zoom = document.querySelector('.leaflet-control-zoom').getBoundingClientRect();
    const middle = bar.top + bar.height / 2;
    window.scrollBy(0, zoom.top + zoom.height / 2 - middle);
    const z2 = document.querySelector('.leaflet-control-zoom').getBoundingClientRect();
    const x = z2.left + z2.width / 2;
    const at = document.elementFromPoint(x, middle);
    return { underBar: z2.top < bar.bottom && z2.bottom > bar.top,
      onTop: at && at.closest('.vs-topbar') ? 'topbar'
        : (at && at.closest('.leaflet-container') ? 'map' : (at ? String(at.className) : 'none')) };
  });
  const seen = await probe();
  expect(seen.underBar && seen.onTop === 'topbar', `${tag} the sticky top bar stays above the map (${JSON.stringify(seen)})`);
  // отрицательный контроль: без собственного контекста наложения карта всплывает
  await page.addStyleTag({ content: '.vs-map-canvas { isolation: auto !important; }' });
  await page.evaluate(() => window.scrollTo(0, 0));
  const control = await probe();
  expect(control.onTop === 'map', `${tag} control: without isolation the check sees the map on top (${JSON.stringify(control)})`);
  await ctx.close();
}

async function checkLayout(state) {
  for (const size of [[1440, 900], [1280, 800], [1024, 768], [390, 844]]) {
    const tag = `layout ${size[0]}`;
    const { ctx, page, errors } = await open(state, TRACTOR, size);
    const s = await page.evaluate(() => {
      const map = document.querySelector('.gps-fact-map-card').getBoundingClientRect();
      const sites = document.querySelector('.gps-fact-sites').getBoundingClientRect();
      const de = document.documentElement;
      return { side: sites.left >= map.right - 1 && Math.abs(sites.top - map.top) < 2,
        below: sites.top >= map.bottom - 1, doc: [de.scrollWidth, de.clientWidth] };
    });
    if (size[0] >= 1280) expect(s.side, `${tag} map and sites side by side`);
    else expect(s.below, `${tag} sites under the map`);
    await page.locator('#gps-fact-machine').click();
    const doc = await page.evaluate(() => [document.documentElement.scrollWidth, document.documentElement.clientWidth]);
    expect(s.doc[0] <= s.doc[1] && doc[0] <= doc[1], `${tag} no horizontal page scroll, list open or not (${s.doc.join('>')}, ${doc.join('>')})`);
    expect(errors.length === 0, `${tag} no page errors (${errors.join(' | ')})`);
    if (size[0] === 1440) await axe(page, tag + ' (list open)');
    await ctx.close();
  }
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

async function checkUzbek(state) {
  const tag = 'uz';
  const { ctx, page, errors } = await open(state, TRACTOR, [1440, 900]);
  const text = await page.evaluate(() => document.body.innerText);
  expect(text.includes('Машина — рақам ёки моделни ёзинг') && text.includes('Фақат жавобсиз участкалари борлар'),
    `${tag} picker speaks uzbek`);
  expect(!/Только с участками|Наберите номер|Все организации/.test(text), `${tag} no russian left in the picker`);
  expect(errors.length === 0, `${tag} no page errors (${errors.join(' | ')})`);
  await ctx.close();
}

if (!args.state) {
  bad('no --state');
} else {
  await checkTyping(args.state);
  await checkFilters(args.state);
  await checkHidden(args.state, 'ru');
  await checkMap(args.state, 'ru');
  await checkWheel(args.state);
  await checkAnswerKeepsPlace(args.state);
  await checkTopbar(args.state);
  await checkLayout(args.state);
}
if (args['state-uz']) {
  await checkMap(args['state-uz'], 'uz');
  await checkHidden(args['state-uz'], 'uz');
  await checkUzbek(args['state-uz']);
} else {
  bad('no --state-uz');
}

await browser.close();
console.log(failures ? `FAILED: ${failures}` : 'ALL OK');
process.exit(failures ? 1 : 0);
