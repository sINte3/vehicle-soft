// tools/ux/check_wialon_interactions.mjs -- UI-P6-002, povedenie posle
// migracii razmetki.
//
// Zachem otdelnyy skript, a ne test v tests/
// -----------------------------------------
// Testy proekta rendaryat HTML i sravnivayut stroki -- v nih net ni dvizhka JS,
// ni kaskada CSS. Imenno tam i lezhal defekt, kotoryy etot skript storozhit:
// atribut `hidden` V RAZMETKE STOYAL, i strokovyy test byl chesten -- no na
// ekrane pole vse ravno risovalos, potomu chto `.vs-field{display:block}`
// perebivaet brauzernoe `[hidden]{display:none}`. Otlichit odno ot drugogo
// mozhet tolko render.
//
// Trebuet podnyatogo ekzemplyara (tools/ux/serve_ephemeral.py).
// Zapusk:
//   PLAYWRIGHT_BROWSERS_PATH=/opt/pw-browsers \
//     node tools/ux/check_wialon_interactions.mjs --base http://127.0.0.1:5099

import { chromium } from 'playwright';

const CHROME = '/opt/pw-browsers/chromium-1194/chrome-linux/chrome';
const BASE = (() => {
  const i = process.argv.indexOf('--base');
  return i > -1 && process.argv[i + 1] ? process.argv[i + 1] : 'http://127.0.0.1:5099';
})();

async function login(page, role) {
  await page.goto(BASE + '/login', { waitUntil: 'domcontentloaded' });
  await page.fill('input[name="username"]', role);
  await page.fill('input[name="password"]', 'ux-audit-local');
  await Promise.all([
    page.waitForLoadState('domcontentloaded'),
    page.click('form.vs-login-form button[type="submit"]'),
  ]);
  if (page.url().includes('/login')) throw new Error('login failed');
}

const browser = await chromium.launch({ executablePath: CHROME });
const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
const page = await ctx.newPage();
await login(page, 'ux_admin');

let fail = 0;
const check = (name, cond, extra = '') => {
  console.log((cond ? 'ok   ' : 'FAIL ') + name + (extra ? '  ' + extra : ''));
  if (!cond) fail++;
};

/* ---- otchet: polya perioda pryachutsya NA EKRANE, a ne tolko v razmetke -- */
await page.goto(BASE + '/wialon/report', { waitUntil: 'networkidle' });
check('report: range fields hidden in day mode',
  await page.locator('#f-range-from').isHidden() && await page.locator('#f-range-to').isHidden());
check('report: "show" button hidden in day mode', await page.locator('#f-range-go').isHidden());
check('report: auto-period label hidden in day mode', await page.locator('#f-auto').isHidden());
check('report: day field visible in day mode', await page.locator('#f-day').isVisible());

// pereklyuchenie rezhima idet bez perezagruzki tolko dlya "range"
await page.click('.vs-toggle-group label:has-text("Ихтиёрий"), .vs-toggle-group label:has-text("Произвольный")');
check('report: range fields appear after switching to range',
  await page.locator('#f-range-from').isVisible() && await page.locator('#f-range-to').isVisible());
check('report: day field hidden in range mode', await page.locator('#f-day').isHidden());

/* ---- import: polya perioda na ekrane zagruzki ------------------------- */
await page.goto(BASE + '/wialon', { waitUntil: 'networkidle' });
check('import: range fields hidden by default',
  await page.locator('#field-range-from').isHidden() && await page.locator('#field-range-to').isHidden());
// [REASON]: v .vs-toggle-group.is-joined sam <input type=radio> vizualno
// skryt, i page.check() po nemu zhdet vidimosti vechno. Nazhimaetsya PODPIS --
// tak zhe, kak eto delaet chelovek.
await page.click('#lbl-range');
check('import: range fields appear on "Ихтиёрий"',
  await page.locator('#field-range-from').isVisible() && await page.locator('#field-range-to').isVisible());
check('import: single date hidden in range mode', await page.locator('#field-day').isHidden());
await page.click('#lbl-week');
check('import: auto label appears for week', await page.locator('#field-auto').isVisible());
const auto = (await page.locator('#autoLabel').textContent() || '').trim();
check('import: auto label carries the computed period', /\d{2}\.\d{2}\.\d{4}/.test(auto), JSON.stringify(auto));

/* ---- mapping list: obshchaya stroka redaktirovaniya -------------------- */
await page.goto(BASE + '/wialon/mapping', { waitUntil: 'networkidle' });
check('mapping: shared edit row hidden on load',
  await page.locator('#wialon-shared-edit-row').isHidden());
check('mapping: add-form hidden on load', await page.locator('#addForm').isHidden());
await page.click('.vs-toolbar button.vs-btn');
check('mapping: add-form opens', await page.locator('#addForm').isVisible());

const firstRow = page.locator('tr.mapping-row').first();
await firstRow.locator('.vs-row-actions-cell button.vs-btn-icon').first().click();
check('mapping: shared edit row opens under the edited row',
  await page.locator('#wialon-shared-edit-row').isVisible());
const opts = await page.locator('#wialon-shared-equipment-id option').count();
check('mapping: equipment options injected by JS', opts > 1, 'options=' + opts);

// klientskaya sortirovka NE dolzhna vytaskivat sluzhebnye stroki
await page.click('table.vs-table th.vs-th-sort >> nth=1');
check('mapping: shared delete row still hidden after a client sort',
  await page.locator('#wialon-shared-delete-row').isHidden());

// filtr po statusu
await page.selectOption('#statusFilter', 'skip');
const visible = await page.locator('tr.mapping-row:visible').count();
const total = await page.locator('tr.mapping-row').count();
check('mapping: status filter narrows the list', visible > 0 && visible < total,
  visible + '/' + total);

/* ---- auto-match: lipkaya panel i filtr --------------------------------- */
await page.goto(BASE + '/wialon/auto_match', { waitUntil: 'networkidle' });
const sticky = await page.locator('.vs-toolbar.is-sticky').count();
check('auto-match: sticky toolbar present', sticky === 1, 'count=' + sticky);
const counter = (await page.locator('#autoVisibleCount').textContent() || '').trim();
check('auto-match: visible counter filled on load', /\d+\/\d+/.test(counter), JSON.stringify(counter));

await browser.close();
console.log(fail ? `\n${fail} FAILED` : '\nall interactive checks passed');
process.exit(fail ? 1 : 0);
