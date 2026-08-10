// tools/ux/check_bilingual_labels.mjs -- proverka, chto podpis' menyaetsya
// vmeste s yazykom uchetnoy zapisi.
//
// Zachem
// ------
// t(key) pri otsutstvii klyucha v translations.py vozvrashchaet SAM KLYUCH.
// Otkaz molchalivyy: shablon vyglyadit dvuyazychnym, stranica rendersya bez
// oshibki, a na ekrane stoit ishodnaya stroka. Imenno tak shest podpisey
// modulya Vialon ostavalis russkimi v uzbekskom interfeyse, i nashel eto
// vladelec glazami, a ne proverka.
//
// Cheker t-key-missing lovit prichinu v ishodnike. Etot skript proveryaet
// SLEDSTVIE v brauzere: odna i ta zhe podpis dolzhna otlichatsya u ru- i
// uz-uchetnoy zapisi. Sravnenie idet po ZNACHENIYU s dvuh storon -- sovpadenie
// dopuskaetsya tolko tam, gde slovo odinakovo v oboih yazykah, i takie sluchai
// perechisleny yavno.
//
// [REASON]: yazyk beretsya iz UCHETNOY ZAPISI, a ne iz adresa. Progon "na
// uzbekskom" cherez parametr v URL snimetsya na russkom i nichego ne skazhet.
//
// Zapusk:
//   PLAYWRIGHT_BROWSERS_PATH=/opt/pw-browsers node tools/ux/check_bilingual_labels.mjs
//   ... --base http://127.0.0.1:5104

import { chromium } from 'playwright';

const CHROME = '/opt/pw-browsers/chromium-1194/chrome-linux/chrome';
function arg(name, fallback) {
  const i = process.argv.indexOf('--' + name);
  return i > -1 && process.argv[i + 1] ? process.argv[i + 1] : fallback;
}
const BASE = arg('base', 'http://127.0.0.1:5104');

// Slova, sovpadayushchie v oboih yazykah po sushchestvu: brendy, tekhnicheskie
// sokrashcheniya i terminy, nazvannye vladelcem odinakovymi ("Фильтр",
// "Объект"). Sovpadenie zdes -- ne defekt, i eto zapisano zaranee, a ne
// podognano po rezultatu.
const SAME_BY_DESIGN = new Set([
  'IP', 'OK', 'N', '\u2116', 'Excel', 'TELEGRAM', 'Виалон', 'Маппинг',
  'Импорт', 'Авто-маппинг', 'Категория', 'Роль', 'РОЛЬ', 'Логин', 'ЛОГИН',
  'Модель', 'Фильтр', 'Объект', 'Модуль', 'Каталог', 'ФАЙЛ', 'Техника',
]);

// Uzly, tekst kotorykh -- DANNYE, a ne podpis. .vs-card-title na otchete po
// zagruzke neset imya organizacii iz spravochnika: ono odinakovo v oboikh
// yazykakh po prirode. Spisok yavnyy i korotkiy imenno potomu, chto kazhdaya
// zapis zdes oslablyaet proverku, i eto dolzhno byt vidno.
const DATA_NODES = [
  '/wialon/report::.vs-card-title',
];

// [REASON]: berutsya tolko PODPISI. Dannye (nazvaniya tekhniki, organizacii,
// loginy) odinakovy v oboikh yazykakh po prirode, i esli ikh schitat, otchet
// utonet v nikh -- pervaya zhe versiya etoy proverki dala 123 "narusheniya",
// iz kotorykh vse do odnogo byli strokami spravochnika. Placeholder <option>
// (value="") -- podpis, ostalnye <option> -- dannye.
const LABELS = '.vs-label, .vs-card-title, .vs-btn, th, .vs-empty-title, ' +
               '.vs-empty-sub, .vs-table-scope, option[value=""]';

const SCREENS = [
  { url: '/admin/audit', pick: LABELS },
  { url: '/admin/users', pick: LABELS },
  { url: '/wialon', pick: LABELS },
  { url: '/wialon/mapping', pick: LABELS },
  { url: '/wialon/auto_match', pick: LABELS },
  { url: '/wialon/report', pick: LABELS },
  { url: '/ref/equipment', pick: LABELS },
];

async function login(page, role) {
  await page.goto(BASE + '/login', { waitUntil: 'domcontentloaded' });
  await page.fill('input[name="username"]', role);
  await page.fill('input[name="password"]', 'ux-audit-local');
  await Promise.all([
    page.waitForLoadState('domcontentloaded'),
    page.click('form.vs-login-form button[type="submit"]'),
  ]);
  if (page.url().includes('/login')) throw new Error('login failed: ' + role);
}

async function collect(role) {
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await ctx.newPage();
  await login(page, role);
  const out = {};
  for (const s of SCREENS) {
    const resp = await page.goto(BASE + s.url, { waitUntil: 'networkidle' });
    if (!resp || resp.status() >= 400 || page.url().includes('/login')) {
      out[s.url] = null;
      continue;
    }
    out[s.url] = await page.evaluate(([sel, url, skip]) =>
      [...document.querySelectorAll(sel)]
        .filter((n) => ![...n.classList].some((c) => skip.includes(url + '::.' + c)))
        .map((n) => n.textContent.replace(/\s+/g, ' ').trim())
        .filter((x) => x && x.length < 90), [s.pick, s.url, DATA_NODES]);
  }
  await ctx.close();
  return out;
}

const browser = await chromium.launch({ executablePath: CHROME });
const ru = await collect('ux_admin');
const uz = await collect('ux_admin_uz');
await browser.close();

let same = 0, differ = 0, problems = 0;
for (const s of SCREENS) {
  const a = ru[s.url], b = uz[s.url];
  if (!a || !b) { console.log(`SKIP ${s.url} -- ne otkrylsya`); continue; }
  const n = Math.min(a.length, b.length);
  const hits = [];
  for (let i = 0; i < n; i++) {
    if (a[i] === b[i]) {
      same++;
      if (!SAME_BY_DESIGN.has(a[i])) { hits.push(a[i]); problems++; }
    } else differ++;
  }
  console.log(`${s.url}: uzlov ${n}, otlichayutsya ${a.filter((x, i) => i < n && x !== b[i]).length}` +
              `, sovpadayut ${a.filter((x, i) => i < n && x === b[i]).length}`);
  for (const h of [...new Set(hits)]) console.log(`   !! odinakovo v oboih yazykah: ${JSON.stringify(h)}`);
}
console.log(`\nitogo: otlichayutsya ${differ}, sovpadayut ${same}, iz nih neob'yasnennyh ${problems}`);
process.exit(problems ? 1 : 0);
