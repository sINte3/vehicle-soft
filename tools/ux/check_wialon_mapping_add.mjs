// tools/ux/check_wialon_mapping_add.mjs -- "Dobavit" na ekrane sopostavleniya
// Wialon dejstvitelno sohranyaet novuyu stroku.
//
// Zachem
// ------
// S 04.06.2026 proverka dublya v wialon_mapping_save nahodila samu
// sohranyaemuyu stroku (autoflush), i "Dobavit" vsegda otvechal "Takoy
// Wialon obekt uzhe sushchestvuet". check_wialon_interactions.mjs otkryvaet
// formu dobavleniya, no ne otpravlyaet ee -- poetomu defekt i prozhil chetyre
// mesyaca. Zdes forma zapolnyaetsya i otpravlyaetsya kak u vladeltsa: imya,
// galochka "Net v sisteme", "Sohranit" -- i proveryaetsya rezultat na ekrane.
//
// Trebuet podnyatogo ekzemplyara (tools/ux/serve_ephemeral.py).
// Zapusk (iz tools/ux):
//   node check_wialon_mapping_add.mjs --base http://127.0.0.1:5099

import { chromium } from 'playwright';

const CHROME = '/opt/pw-browsers/chromium-1194/chrome-linux/chrome';
const BASE = (() => {
  const i = process.argv.indexOf('--base');
  return i > -1 && process.argv[i + 1] ? process.argv[i + 1] : 'http://127.0.0.1:5099';
})();
// Imya iz spiska vladeltsa 01.10.2026; dvojnoy probel -- kak v Wialon.
const NAME = 'Labo 80  482 CAA (Латипов Мухаммад)';
const STORED = NAME.replace(/\s+/g, ' ');

let fail = 0;
const check = (name, cond, extra = '') => {
  console.log((cond ? 'ok   ' : 'FAIL ') + name + (extra ? '  ' + extra : ''));
  if (!cond) fail++;
};

const browser = await chromium.launch({ executablePath: CHROME });
const page = await (await browser.newContext({ viewport: { width: 1440, height: 900 } })).newPage();
await page.goto(BASE + '/login', { waitUntil: 'domcontentloaded' });
await page.fill('input[name="username"]', 'ux_admin');
await page.fill('input[name="password"]', 'ux-audit-local');
await Promise.all([
  page.waitForLoadState('domcontentloaded'),
  page.click('form.vs-login-form button[type="submit"]'),
]);
if (page.url().includes('/login')) throw new Error('login failed');

const counter = async () => {
  const text = (await page.locator('#mappingVisibleCount').textContent()) || '';
  const match = text.match(/(\d+)\/(\d+)/);
  return match ? Number(match[2]) : -1;
};

await page.goto(BASE + '/wialon/mapping', { waitUntil: 'networkidle' });
const before = await counter();
check('mapping: counter read before saving', before >= 0, 'total=' + before);

await page.click('.vs-toolbar button.vs-btn');
check('mapping: add-form opens', await page.locator('#addForm').isVisible());
await page.fill('#wmName', NAME);
await page.check('#addForm input[name="skip"]');
await Promise.all([
  page.waitForNavigation({ waitUntil: 'networkidle' }),
  page.click('#addForm button[type="submit"]'),
]);

const body = (await page.locator('body').textContent()) || '';
check('mapping: success message shown', body.includes('Маппинг сохранён'));
check('mapping: no "already exists" refusal', !body.includes('уже существует'));
const after = await counter();
check('mapping: counter grew by one', after === before + 1, before + ' -> ' + after);
const row = page.locator('tr.mapping-row', { hasText: STORED });
const rows = await row.count();
check('mapping: the new row is in the list', rows === 1, 'rows=' + rows);
// bez stroki chitat nechego: proverka govorit FAIL, a ne padaet po taymautu
const rowText = rows ? ((await row.first().textContent()) || '') : '';
check('mapping: the new row is marked "not in the system"',
  rowText.includes('Нет в системе'));

await browser.close();
console.log(fail ? `\n${fail} FAILED` : '\nmapping add passed');
process.exit(fail ? 1 : 0);
