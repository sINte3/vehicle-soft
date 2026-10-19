// GPS · «Сверка» (agro-work) · один пункт, вкладки, фильтры, Excel -- в браузере.
//
// Просьба владельца 19.10.2026: «Заявка → работа» и «Работа → заявка»
// объединить в один пункт, сверху -- фильтры по организации, статусу и типу
// оплаты, выгрузка в Excel. DOM-тесты Python (tests/test_agro_work_screen.py,
// OneScreen) видят разметку и числа; этот файл проверяет то, что видно только
// в браузере:
//   * в меню модуля один пункт «Сверка», под ним три вкладки, активная --
//     та, на которой стоишь; переход по вкладке не теряет фильтр;
//   * выбор «Наличные» в списке «Тип оплаты» и «Показать» -- в адресе
//     pay=CASH и в таблице только заявки с наличной оплатой;
//   * «Скачать Excel» отдаёт книгу xlsx с тем же фильтром;
//   * на 1440 и 390 px нет горизонтальной прокрутки страницы, строка фильтров
//     и вкладки видны целиком; узбекский интерфейс -- кириллицей;
//   * axe (serious/critical), если задан AXE -- путь к axe.min.js.
//
// Стенд: python tools/ux/serve_agro_work.py --port 5099 --state-dir <dir>
// Запуск: node tools/ux/check_agro_work_screen.mjs --base http://127.0.0.1:5099 \
//           --state <dir>/ux_admin.json --state-uz <dir>/ux_admin_uz.json
// Окружение: CHROME -- путь к браузеру; AXE -- путь к axe.min.js.
// Код выхода: 0 -- всё прошло, 1 -- есть провалы (каждый строкой '!!').
// Проверка ничего не пишет: стенд только читается.

import { existsSync, readFileSync } from 'node:fs';

const args = Object.fromEntries(process.argv.slice(2).reduce((acc, v, i, a) =>
  (v.startsWith('--') ? acc.concat([[v.slice(2), a[i + 1]]]) : acc), []));
const BASE = args.base || 'http://127.0.0.1:5099';
const PERIOD = 'from=2026-09-10&to=2026-09-20';
const TABS = [['/agro-work/', 'Свод'], ['/agro-work/applications', 'Заявка → работа'],
              ['/agro-work/work-days', 'Работа → заявка']];
const { chromium } = await import(process.env.PLAYWRIGHT_MODULE || 'playwright');

let failures = 0;
const bad = (msg) => { failures += 1; console.log('!! ' + msg); };
const ok = (msg) => console.log('ok ' + msg);
const expect = (cond, msg) => (cond ? ok(msg) : bad(msg));

const launch = process.env.CHROME ? { executablePath: process.env.CHROME } : {};
const browser = await chromium.launch(launch);
const axeSource = process.env.AXE && existsSync(process.env.AXE)
  ? readFileSync(process.env.AXE, 'utf8') : null;

async function open(state, url, size) {
  const ctx = await browser.newContext({ viewport: { width: size[0], height: size[1] }, storageState: state, acceptDownloads: true });
  const page = await ctx.newPage();
  const errors = [];
  page.on('pageerror', (e) => errors.push(String(e)));
  page.on('console', (m) => {
    if (m.type() === 'error' && !/Failed to load resource/.test(m.text())) errors.push(m.text());
  });
  await page.goto(url, { waitUntil: 'load' });
  return { ctx, page, errors };
}

const numbers = (page) => page.$$eval('table.vs-table td', (cells) => [...new Set(cells
  .map((c) => (c.textContent.match(/APP-UX-\d{3}/) || [])[0]).filter(Boolean))].sort());
const noOverflow = (page) => page.evaluate(() =>
  document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1);

async function axe(page, tag) {
  if (!axeSource) return;
  await page.addScriptTag({ content: axeSource });
  const found = await page.evaluate(async () => {
    const result = await window.axe.run(document, { resultTypes: ['violations'] });
    return result.violations.filter((v) => v.impact === 'serious' || v.impact === 'critical')
      .map((v) => `${v.id} (${v.nodes.length})`);
  });
  expect(found.length === 0, `${tag} axe serious/critical: ${found.join(', ') || 'none'}`);
}

async function checkMenuAndTabs(state) {
  for (const [path, label] of TABS) {
    const tag = `tabs ${label}`;
    const { ctx, page, errors } = await open(state, `${BASE}${path}?${PERIOD}&pay=CASH`, [1440, 900]);
    const pills = await page.$$eval('nav.vs-modulenav a', (els) => els.map((e) => e.textContent.trim()));
    expect(JSON.stringify(pills) === JSON.stringify(['Факт по технике', 'Сверка', 'Импорт заявок', 'Приём данных']),
      `${tag}: module menu has one «Сверка» item (${pills.join(' | ')})`);
    const active = await page.$$eval('nav.vs-modulenav a.is-active', (els) => els.map((e) => e.textContent.trim()));
    expect(active.length === 1 && active[0] === 'Сверка', `${tag}: «Сверка» is the current item`);
    const tabs = await page.$$eval('nav.vs-segmented a', (els) => els.map((e) => ({
      text: e.textContent.trim(), href: e.getAttribute('href'), current: e.getAttribute('aria-current') })));
    expect(tabs.length === 3, `${tag}: three tabs`);
    expect(tabs.filter((t) => t.current === 'page').map((t) => t.text).join() === label, `${tag}: the tab of this page is current`);
    expect(tabs.every((t) => t.href.includes('pay=CASH')), `${tag}: every tab keeps the payment filter`);
    const bar = await page.locator('form.vs-toolbar').boundingBox();
    const tabsBox = await page.locator('nav.vs-segmented').boundingBox();
    expect(bar && bar.width > 0 && tabsBox && tabsBox.width > 0, `${tag}: filter bar and tabs are visible`);
    expect(errors.length === 0, `${tag}: no script errors ${errors.join(' ; ')}`);
    await axe(page, tag);
    await ctx.close();
  }
}

async function checkFilterAndExport(state) {
  const tag = 'filter';
  const { ctx, page } = await open(state, `${BASE}/agro-work/applications?${PERIOD}`, [1440, 900]);
  const before = await numbers(page);
  expect(before.length === 5, `${tag}: without a filter all five applications (${before.join(', ')})`);
  await page.selectOption('select[name="pay"]', 'CASH');
  await Promise.all([page.waitForLoadState('load'), page.click('form.vs-toolbar button[type="submit"]')]);
  expect(new URL(page.url()).searchParams.get('pay') === 'CASH', `${tag}: the address carries pay=CASH`);
  const cash = await numbers(page);
  expect(JSON.stringify(cash) === JSON.stringify(['APP-UX-001', 'APP-UX-004']),
    `${tag}: only cash applications (${cash.join(', ')})`);
  await page.click('nav.vs-segmented a:has-text("Работа → заявка")');
  await page.waitForLoadState('load');
  expect(new URL(page.url()).searchParams.get('pay') === 'CASH', `${tag}: the tab switch kept pay=CASH`);
  const days = await page.$$eval('table.vs-table tbody tr td:first-child', (cells) => cells.map((c) => c.textContent.trim().slice(0, 10)));
  expect(!days.includes('13.09.2026') && days.includes('10.09.2026'), `${tag}: the day without an application is hidden (${days.join(', ')})`);
  const [download] = await Promise.all([page.waitForEvent('download'), page.click('a:has-text("Скачать Excel")')]);
  const name = download.suggestedFilename();
  expect(/^sverka_2026-09-10_2026-09-20\.xlsx$/.test(name), `${tag}: Excel file name ${name}`);
  const href = await page.locator('a:has-text("Скачать Excel")').getAttribute('href');
  expect(href.includes('pay=CASH'), `${tag}: the Excel link carries pay=CASH`);
  const response = await page.request.get(BASE + href);
  expect(response.ok() && /spreadsheetml/.test(response.headers()['content-type'] || ''), `${tag}: Excel answers 200 with an xlsx`);
  await ctx.close();
}

async function checkWidths(state, stateUz) {
  for (const [state_, lang] of [[state, 'ru'], [stateUz, 'uz']]) {
    for (const width of [1440, 390]) {
      for (const [path] of TABS) {
        const tag = `${lang} ${width} ${path}`;
        const { ctx, page } = await open(state_, `${BASE}${path}?${PERIOD}`, [width, width >= 1000 ? 900 : 844]);
        expect(await noOverflow(page), `${tag}: no horizontal page scroll`);
        const right = await page.$$eval('form.vs-toolbar .vs-field, form.vs-toolbar .vs-btn, nav.vs-segmented a',
          (els) => Math.max(...els.map((e) => e.getBoundingClientRect().right)));
        expect(right <= width + 1, `${tag}: filters and tabs fit the screen (right edge ${Math.round(right)})`);
        if (lang === 'uz') {
          const texts = await page.$$eval('nav.vs-segmented a, form.vs-toolbar .vs-label, select[name="pay"] option, select[name="status"] option',
            (els) => els.map((e) => e.textContent.trim()));
          // Латиница допустима только в названиях продуктов (устав): GPS, Excel.
          const latin = texts.filter((t) => /[A-Za-z]/.test(t.replace(/GPS|Excel/g, '')));
          expect(latin.length === 0, `${tag}: Uzbek labels are Cyrillic ${latin.join(' | ')}`);
        }
        if (width === 390) await axe(page, tag);
        await ctx.close();
      }
    }
  }
}

await checkMenuAndTabs(args.state);
await checkFilterAndExport(args.state);
await checkWidths(args.state, args['state-uz']);
await browser.close();
console.log(failures ? `FAILED: ${failures}` : 'ALL PASSED');
process.exit(failures ? 1 : 0);
