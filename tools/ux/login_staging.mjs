/* Выпустить готовую сессию для агента проверки — без передачи ему пароля.
 *
 * [REASON]: UI-P9. Прогон 3 встал на нуле: среда агента запрещает ему вводить
 * пароль даже когда пароль дан в сообщении, а задание требовало войти формой.
 * Спор двух правил решается не уговорами, а тем, что войти должен владелец:
 * агент получает storageState и вообще не имеет причины спрашивать пароль.
 * Побочный выигрыш — пароль перестаёт попадать в переписку и в отчёты.
 *
 * Запуск (пароль берётся из переменной окружения, в командную строку не
 * попадает и в истории PowerShell не остаётся):
 *
 *   $env:VS_PASSWORD = '...'
 *   node C:\qa\login_staging.mjs --base http://10.103.25.14:5051 --user admin
 *   Remove-Item Env:\VS_PASSWORD
 *
 * Удобнее — обёрткой login-ui-p9.ps1, которая спрашивает пароль скрыто.
 *
 * Вывод в консоль только ASCII: журналы службы на Windows читаются в cp1251.
 */
import { chromium } from 'playwright';

function arg(name, fallback) {
  const i = process.argv.indexOf('--' + name);
  return i > -1 && process.argv[i + 1] ? process.argv[i + 1] : fallback;
}

const BASE = arg('base', 'http://10.103.25.14:5051');
const USER = arg('user', 'admin');
const OUT = arg('out', 'C:\\qa\\auth-ui-p9.json');
const PASS = process.env.VS_PASSWORD;

if (!PASS) {
  console.error('ERROR: env VS_PASSWORD is not set. Nothing to do.');
  process.exit(2);
}

// [REASON]: na servere Playwright nahodit svoy Chromium sam, i --exe ne nuzhen.
// Opciya ostavlena rovno dlya sred, gde brauzer lezhit v drugom meste (CI,
// kontener razrabotki): bez nee skript tam ne zapuskaetsya vovse i proverit
// ego pered vydachey nelzya.
const EXE = arg('exe', process.env.VS_CHROMIUM || undefined);
const browser = await chromium.launch(EXE ? { executablePath: EXE } : {});
const ctx = await browser.newContext();
const page = await ctx.newPage();

await page.goto(BASE + '/login', { waitUntil: 'domcontentloaded' });
await page.fill('input[name="username"]', USER);
await page.fill('input[name="password"]', PASS);
await Promise.all([
  page.waitForLoadState('domcontentloaded'),
  page.click('form.vs-login-form button[type="submit"]'),
]);

if (page.url().includes('/login')) {
  console.error('ERROR: login failed for user ' + USER + ' (still on /login)');
  await browser.close();
  process.exit(1);
}

// [REASON]: proveryaem imenno zashchishchennuyu strancu, a ne fakt redirekta.
// Redirekt s /login sam po sebe ne dokazyvaet, chto sessiya rabochaya.
await page.goto(BASE + '/entry', { waitUntil: 'domcontentloaded' });
if (page.url().includes('/login')) {
  console.error('ERROR: session did not survive a second request');
  await browser.close();
  process.exit(1);
}

const active = await page.getAttribute('form.vs-lang button.is-active', 'value')
  .catch(() => null);

await ctx.storageState({ path: OUT });
await browser.close();

console.log('OK   session saved to ' + OUT);
console.log('     user            ' + USER);
console.log('     interface lang  ' + (active || 'not detected'));
console.log('     check url       ' + BASE + '/entry -> reachable');
console.log('');
console.log('Give the agent the FILE PATH, not the password.');
console.log('Delete the file when the run is over: Remove-Item ' + OUT);
