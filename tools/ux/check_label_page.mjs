// Does the labelling page actually work when clicked? Screenshots prove
// nothing; this clicks two cards and reads what the answer box contains.
import { chromium } from 'playwright';

const CHROME = '/opt/pw-browsers/chromium-1194/chrome-linux/chrome';
const page_url = 'file://' + process.argv[2];

const browser = await chromium.launch({ executablePath: CHROME });
const page = await browser.newPage();
const errors = [];
page.on('pageerror', e => errors.push(String(e)));
page.on('console', m => { if (m.type() === 'error') errors.push(m.text()); });
await page.goto(page_url);

const checks = [];
const cards = await page.locator('.card').count();
checks.push(['cards on the page', cards > 0, cards]);
const total = await page.locator('#total').textContent();
checks.push(['counter knows the total', Number(total) === cards, total]);

// every card must carry a drawing with a real polygon, not an empty frame
const paths = await page.locator('.card svg path').count();
checks.push(['every card has a polygon', paths >= cards, paths]);
const emptyBoxes = await page.evaluate(() =>
  Array.from(document.querySelectorAll('.card svg')).filter(s => {
    const b = s.getBBox();
    return b.width < 5 || b.height < 5;
  }).length);
checks.push(['no degenerate drawings', emptyBoxes === 0, emptyBoxes]);

// click through: first card work, second transit
const first = page.locator('.card').nth(0);
const second = page.locator('.card').nth(1);
const firstId = await first.getAttribute('data-id');
const secondId = await second.getAttribute('data-id');
await first.getByRole('button', { name: 'Работа' }).click();
await second.getByRole('button', { name: 'Проезд' }).click();
const out = await page.locator('#out').inputValue();
checks.push(['answer box holds both clicks',
  out === `${firstId};работа\n${secondId};проезд`, JSON.stringify(out)]);
checks.push(['done counter moved', (await page.locator('#done').textContent()) === '2',
  await page.locator('#done').textContent()]);

// changing your mind must replace, not append
await first.getByRole('button', { name: 'Проезд' }).click();
const out2 = await page.locator('#out').inputValue();
checks.push(['re-answering replaces the line',
  out2 === `${firstId};проезд\n${secondId};проезд`, JSON.stringify(out2)]);
checks.push(['the card shows it is answered',
  await first.evaluate(el => el.classList.contains('done')), '']);
checks.push(['no javascript errors', errors.length === 0, errors.join(' | ')]);

let ok = true;
for (const [name, pass, detail] of checks) {
  if (!pass) ok = false;
  console.log(`${pass ? 'OK  ' : 'FAIL'} ${name}${pass ? '' : '  -> ' + detail}`);
}
console.log('RESULT: ' + (ok ? 'OK' : 'FAIL'));
await browser.close();
process.exit(ok ? 0 : 1);
