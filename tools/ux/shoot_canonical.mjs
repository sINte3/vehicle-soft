// tools/ux/shoot_canonical.mjs -- kadry "bylo / stalo" po semi etalonnym ekranam.
//
// Snimaet ODIN I TOT ZHE spisok URL s DVUH podnyatyh ekzemplyarov:
//   --before http://127.0.0.1:5098   kod do treka
//   --after  http://127.0.0.1:5099   rabochee derevo
// i sobiraet HTML-list sravneniya, gde para kadrov stoit ryadom.
//
// [REASON]: bazis P1 (docs/ux/11-baseline/) v git ne lezhit -- PNG v
// .gitignore. V novom kontejnere kadrov "bylo" prosto net, i sravnivat
// "stalo" ne s chem. Poetomu oba sostoyaniya snimayutsya v odnom progone, na
// odnih i tekh zhe fiksturah: fikstury kopiruyutsya v derevo "do", inache
// raznica DANNYH chitalas by kak uluchshenie interfeysa.
//
// Yazyk beretsya iz UCHETNOY ZAPISI, ne iz adresa: --role ux_admin_uz. Parametr
// v URL prilozhenie ignoriruet, i progon "na uzbekskom" molcha snyalsya by na
// russkom.
//
// Zapusk (oba servera poднyaty otdelno, brauzery uzhe ustanovleny):
//   PLAYWRIGHT_BROWSERS_PATH=/opt/pw-browsers node tools/ux/shoot_canonical.mjs

import { chromium } from 'playwright';
import { AxeBuilder } from '@axe-core/playwright';
import fs from 'node:fs';
import path from 'node:path';

const ROOT = path.resolve(import.meta.dirname, '..', '..');
const CHROME = '/opt/pw-browsers/chromium-1194/chrome-linux/chrome';

function arg(name, fallback) {
  const i = process.argv.indexOf('--' + name);
  return i > -1 && process.argv[i + 1] ? process.argv[i + 1] : fallback;
}

const BEFORE = arg('before', 'http://127.0.0.1:5098');
const AFTER = arg('after', 'http://127.0.0.1:5099');
const PASSWORD = 'ux-audit-local';

// [REASON]: v P6 modulya shest, i kazhdyy trebuet svoego lista "bylo/stalo" s
// temi zhe pravilami -- sverka fikstur, proverka na redirect v /login, axe na
// desktope I na 390 px. Kopirovat skript pod kazhdyy modul znachit razmnozhit
// eti pravila i dat im razoytis. Poetomu spisok ekranov -- vhodnoy parametr,
// a ne konstanta; bez nego skript vedet sebya kak ran'she i snimaet sem
// etalonov P4.
const SCREENS_FILE = arg('screens', '');
const OUT = path.join(ROOT, 'docs', 'ux', arg('out', '50-canonical'));
const FINGERPRINT_URL = arg('fingerprint', '/ref/equipment');

const VIEWPORTS = {
  desktop: { width: 1920, height: 1080 },
  laptop: { width: 1440, height: 900 },
  tablet: { width: 1024, height: 768 },
  mobile: { width: 390, height: 844 },
};

// Sem etalonnyh ekranov iz docs/ux/00-workflow.md, s prichinoy vybora.
// vp -- vyeporty, na kotoryh ekran snimaetsya. Sedmoy ekran -- tot zhe
// fuel/transactions na 390 px: eto ОТДЕЛЬНЫЙ etalon adaptivnosti, a ne punkt
// chek-lista, poetomu on stoit v spiske otdelnoy strokoy.
const P4_SCREENS = [
  { n: 1, slug: 'index', url: '/', role: 'ux_admin',
    title: 'Дашборд', why: 'главный экран, 44 КБ — проверка, что система не ломает уже сделанное' },
  { n: 2, slug: 'drones-works', url: '/drones/works', role: 'ux_admin',
    title: 'Дроны — работы', why: 'крупнейшая таблица, 190 вхождений vs-* — масштаб на самом плотном экране' },
  { n: 3, slug: 'fuel-transactions', url: '/fuel/transactions', role: 'ux_admin',
    title: 'АЗС — выдачи Topaz', why: 'таблица на легаси-переменных + свой класс — эталон миграции' },
  { n: 4, slug: 'daily-entry', url: '/entry?org_id=1', role: 'ux_admin',
    title: 'Ввод данных', why: '83 % всей работы человека; худшая форма — 386 строк своего CSS' },
  { n: 5, slug: 'spare-part-detail', url: '/spare-parts/1', role: 'ux_admin',
    title: 'Запчасти — заявка', why: 'карточка записи: таймлайн, степпер, 23 эмодзи — максимум по PR-013' },
  { n: 6, slug: 'report', url: '/report', role: 'ux_admin',
    title: 'Отчёт', why: 'единственный носитель языка --ux-* + фильтры и период' },
  { n: 7, slug: 'fuel-transactions-mobile', url: '/fuel/transactions', role: 'ux_admin',
    title: 'АЗС — выдачи, 390 px', why: 'тот же экран в мобильном — отдельный эталон адаптивности',
    only: ['mobile'] },
  // Progon na uzbekskom obyazatelen, ne podmnozhestvo: uzbekskie podpisi na
  // 19-47 % dlinnee russkih v tekh zhe polyakh (metriki M6).
  { n: 8, slug: 'daily-entry-uz', url: '/entry?org_id=1', role: 'ux_admin_uz',
    title: 'Ввод данных (узбекский)', why: 'подписи на 19–47 % длиннее — проверка, что раскладка держит',
    only: ['desktop', 'mobile'] },
  { n: 9, slug: 'kitchen-sink', url: '/__ui/kitchen-sink', role: 'ux_admin',
    title: 'Kitchen sink', why: 'все компоненты во всех состояниях; на стороне «было» его нет',
    afterOnly: true, only: ['desktop'] },
];

const SCREENS = SCREENS_FILE
  ? JSON.parse(fs.readFileSync(path.resolve(SCREENS_FILE), 'utf8'))
  : P4_SCREENS;
if (!Array.isArray(SCREENS) || !SCREENS.length) {
  console.error('screens list is empty: nothing to shoot');
  process.exit(1);
}
for (const s of SCREENS) {
  // Skript pishet kadry po slug i sobiraet list po nemu zhe: propushchennyy
  // slug tiho slozhil by neskolko ekranov v odin fayl.
  if (!s.slug || !s.url || !s.role) {
    console.error('screen without slug/url/role: ' + JSON.stringify(s));
    process.exit(1);
  }
}

const FREEZE_CSS = `
*, *::before, *::after {
  animation-duration: 0s !important; animation-delay: 0s !important;
  transition-duration: 0s !important; transition-delay: 0s !important;
  caret-color: transparent !important;
}
html { scroll-behavior: auto !important; }`;

async function login(page, base, role) {
  await page.goto(base + '/login', { waitUntil: 'domcontentloaded' });
  await page.fill('input[name="username"]', role);
  await page.fill('input[name="password"]', PASSWORD);
  // [REASON]: na stranice vhoda DVE formy -- pereklyuchatel yazyka stoit v
  // razmetke RANSHE formy vhoda, i ego knopki tozhe type="submit". Selektor
  // 'button[type="submit"]' nazhimaet YAZYK, vhod ne proiskhodit, i dalshe
  // KAZHDYY marshrut otdaet redirect na /login. Snimok pri etom poluchaetsya
  // -- stranicy vhoda, -- i vse zamery vyhodyat odinakovymi. Imenno tak i
  // sluchilos v pervom progone: 55 kadrov, u vsekh style=2, attrs=1, emoji=0.
  await Promise.all([
    page.waitForLoadState('domcontentloaded'),
    page.click('form.vs-login-form button[type="submit"]'),
  ]);
  if (page.url().includes('/login')) throw new Error(`login failed for ${role} at ${base}`);
}

const PROBE = () => {
  const emoji = /[\u{1F000}-\u{1FAFF}\u{2600}-\u{27BF}\u{2B00}-\u{2BFF}]/gu;
  const de = document.documentElement;
  const tables = [...document.querySelectorAll('table')];
  return {
    inlineStyleBlocks: document.querySelectorAll('style').length,
    inlineStyleAttrs: document.querySelectorAll('[style]').length,
    glyphs: (document.body.innerText.match(emoji) || []).length,
    tableClasses: [...new Set(tables.map((t) => t.className.trim() || '(no class)'))].sort(),
    horizontalOverflow: de.scrollWidth > de.clientWidth + 1,
    scrollWidth: de.scrollWidth,
    clientWidth: de.clientWidth,
    controlsWithoutName: [...document.querySelectorAll('button, a[href], input, select, textarea')]
      .filter((el) => {
        if (el.type === 'hidden') return false;
        const text = (el.innerText || el.value || '').trim();
        const aria = el.getAttribute('aria-label') || el.getAttribute('title') || '';
        const labelled = el.id && document.querySelector(`label[for="${CSS.escape(el.id)}"]`);
        return !text && !aria && !labelled;
      }).length,
  };
};

// Otpechatok DANNYH: vidimyy tekst yacheek spravochnika tehniki.
// [REASON]: sravnenie "bylo / stalo" imeet smysl TOLKO na odinakovyh
// fiksturah. Pervyy progon P4 etogo ne proveryal, i dva ekzemplyara
// napolnilis raznymi dannymi -- derevo "do" ne imelo fayla metrik, poetomu
// seeder molcha ushel na pustye enum-y i ploskuyu dlinu 18. Na kartinkah eto
// vyglyadelo kak izmenenie interfeysa, i vladelec sprosil, ne podmeneny li
// cifry. Vopros pravilnyy, i otvechat na nego dolzhen skript, a ne chelovek.
// /ref/equipment vybran namerenno: trek ego ne trogal, poetomu lyuboe
// rashozhdenie zdes -- eto rashozhdenie DANNYH, a ne razmetki.
/* Otpechatok fikstur: dokazatelstvo, chto oba ekzemplyara derzhat ODNI I TE
   ZHE dannye. Sravnivayutsya teksty yacheek tablicy.
 *
 * [REASON]: stranica otpechatka OBYAZANA lezhat VNE togo, chto pravit tekushchiy
 * PR, i poetomu ona parametr, a ne konstanta. V P4 zdes stoyal /ref/equipment
 * i eto bylo verno. V P6 pervym migriruet imenno modul spravochnikov: posle
 * pravki pustaya yacheyka "edinica izmereniya" stala risovat tire, chislo
 * NEPUSTYH yacheek vyroslo na chetyre, i sverka soobshchila o rashozhdenii
 * DANNYH, kotorogo net. Proverka, reagiruyushchaya na sobstvennuyu pravku
 * razmetki, perestaet otvechat na svoy vopros. */
async function fixtureFingerprint(page, base) {
  await page.goto(base + FINGERPRINT_URL, { waitUntil: 'networkidle' });
  return page.evaluate(() =>
    [...document.querySelectorAll('table td')]
      .map((td) => td.textContent.trim())
      .filter(Boolean)
      .sort()
      .join('|'));
}

const main = async () => {
  fs.mkdirSync(path.join(OUT, 'before'), { recursive: true });
  fs.mkdirSync(path.join(OUT, 'after'), { recursive: true });

  const browser = await chromium.launch({ executablePath: CHROME });

  {
    const ctx = await browser.newContext({ viewport: VIEWPORTS.desktop });
    const page = await ctx.newPage();
    await login(page, BEFORE, 'ux_admin');
    const fpBefore = await fixtureFingerprint(page, BEFORE);
    await ctx.close();

    const ctx2 = await browser.newContext({ viewport: VIEWPORTS.desktop });
    const page2 = await ctx2.newPage();
    await login(page2, AFTER, 'ux_admin');
    const fpAfter = await fixtureFingerprint(page2, AFTER);
    await ctx2.close();

    if (fpBefore !== fpAfter) {
      const b = fpBefore.split('|'), a = fpAfter.split('|');
      const diff = b.filter((x, i) => a[i] !== x).slice(0, 6);
      await browser.close();
      console.error('FIXTURE MISMATCH: the two instances hold different data.');
      console.error(`  before: ${b.length} cells, after: ${a.length} cells`);
      console.error(`  first differing values: ${JSON.stringify(diff)}`);
      console.error('  Comparing screenshots now would attribute a data difference');
      console.error('  to the interface. Sync tools/ux/ AND docs/ux/10-metrics/');
      console.error('  into the "before" tree and restart both instances.');
      process.exit(1);
    }
    console.log(`fixtures match: ${fpBefore.split('|').length} cells identical`);
  }

  const results = [];

  for (const side of ['before', 'after']) {
    const base = side === 'before' ? BEFORE : AFTER;
    const byRole = new Map();

    for (const screen of SCREENS) {
      if (side === 'before' && screen.afterOnly) continue;
      const viewports = screen.only || Object.keys(VIEWPORTS);

      for (const vp of viewports) {
        const key = `${screen.role}|${vp}`;
        if (!byRole.has(key)) {
          const ctx = await browser.newContext({
            viewport: VIEWPORTS[vp], deviceScaleFactor: 1,
            locale: 'ru-RU', timezoneId: 'Asia/Tashkent',
          });
          const page = await ctx.newPage();
          await login(page, base, screen.role);
          byRole.set(key, { ctx, page });
        }
        const { page } = byRole.get(key);

        const entry = { side, n: screen.n, slug: screen.slug, viewport: vp,
                        role: screen.role, url: screen.url, title: screen.title,
                        why: screen.why, status: 0 };
        try {
          const resp = await page.goto(base + screen.url, { waitUntil: 'networkidle', timeout: 45000 });
          entry.status = resp ? resp.status() : 0;
          // [REASON]: proverka, chto snimaetsya IMENNO zaproshennyy ekran, a ne
          // stranica vhoda. Redirect na /login otdaet chestnyy HTTP 200, i bez
          // etoy strochki progon vyglyadit uspeshnym, a vse zamery -- ravnymi.
          if (page.url().includes('/login')) {
            throw new Error(`redirected to /login (session lost) at ${screen.url}`);
          }
          await page.addStyleTag({ content: FREEZE_CSS }).catch(() => {});
          await page.evaluate(() => document.fonts && document.fonts.ready).catch(() => {});
          await page.evaluate(() => window.scrollTo(0, 0));
          Object.assign(entry, await page.evaluate(PROBE));

          const file = path.join(OUT, side, `${screen.slug}-${vp}.png`);
          await page.screenshot({ path: file, fullPage: true });
          entry.file = path.relative(OUT, file);

          // [REASON]: axe gonyaetsya na desktope I na 390 px. Narusheniya
          // rasходyatsya po vyeportam: scrollable-region-focusable
          // proyavlyaetsya TOLKO tam, gde tablica ne pomeshchaetsya, to est na
          // uzkom ekrane. Progon odnogo desktopa daval by nol i schitalsya by
          // dokazatelstvom -- proverkoy, kotoraya ne razlichaet ispravnyy
          // sluchay i neispravnyy.
          if (side === 'after' && (vp === 'desktop' || vp === 'mobile')) {
            const scan = await new AxeBuilder({ page })
              .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa']).analyze();
            const bySeverity = {};
            for (const v of scan.violations) {
              bySeverity[v.impact || 'unknown'] =
                (bySeverity[v.impact || 'unknown'] || 0) + v.nodes.length;
            }
            entry.axe = bySeverity;
            entry.axeRules = scan.violations
              .map((v) => ({ id: v.id, impact: v.impact, nodes: v.nodes.length }))
              .sort((a, b) => a.id.localeCompare(b.id));
          }
        } catch (e) {
          entry.error = String(e.message).slice(0, 200);
        }
        results.push(entry);
      }
    }
    for (const { ctx } of byRole.values()) await ctx.close();
    console.log(`${side}: done`);
  }

  await browser.close();
  fs.writeFileSync(path.join(OUT, 'canonical-screens.json'),
    JSON.stringify(results, null, 1) + '\n');

  // Kontaktnyy list. Chistyy HTML bez vneshnih ssylok -- otkryvaetsya s diska.
  const pairs = [];
  for (const screen of SCREENS) {
    const viewports = screen.only || Object.keys(VIEWPORTS);
    for (const vp of viewports) {
      const b = results.find((r) => r.side === 'before' && r.slug === screen.slug && r.viewport === vp);
      const a = results.find((r) => r.side === 'after' && r.slug === screen.slug && r.viewport === vp);
      pairs.push({ screen, vp, b, a });
    }
  }
  const esc = (s) => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  const cell = (r, label) => {
    if (!r) return `<div class="pane empty"><div class="lbl">${label}</div><p>нет кадра — экран не существовал</p></div>`;
    if (r.error) return `<div class="pane empty"><div class="lbl">${label}</div><p>${esc(r.error)}</p></div>`;
    return `<div class="pane"><div class="lbl">${label} · HTTP ${r.status}</div>` +
      `<img src="${esc(r.file)}" alt="${esc(label)}"></div>`;
  };
  const facts = (r) => r && !r.error
    ? `<style-count>inline &lt;style&gt;: <b>${r.inlineStyleBlocks}</b> · style-атрибутов: <b>${r.inlineStyleAttrs}</b>` +
      ` · эмодзи: <b>${r.glyphs}</b> · переполнение по ширине: <b>${r.horizontalOverflow ? 'ДА' : 'нет'}</b>` +
      ` · контролов без имени: <b>${r.controlsWithoutName}</b></style-count>`
    : '<style-count>—</style-count>';

  const html = `<!doctype html><meta charset="utf-8"><title>P4 — эталонные экраны: было / стало</title>
<style>
 body { font: 14px/1.5 system-ui, sans-serif; margin: 0; padding: 24px; background: #eef2f7; color: #182230; }
 h1 { font-size: 23px; margin: 0 0 4px; }
 .sub { color: #5b6b80; margin-bottom: 24px; }
 section { background: #fff; border: 1px solid #e6ecf4; border-radius: 14px; padding: 16px; margin-bottom: 20px; }
 h2 { font-size: 17px; margin: 0 0 2px; }
 .why { color: #5b6b80; font-size: 13px; margin-bottom: 10px; }
 .vp { font-weight: 700; font-size: 12px; text-transform: uppercase; letter-spacing: .5px; color: #5b6b80; margin: 14px 0 6px; }
 .row { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; align-items: start; }
 .pane { border: 1px solid #e6ecf4; border-radius: 10px; overflow: hidden; background: #f7f9fc; }
 .pane.empty { padding: 24px; color: #5b6b80; font-size: 13px; }
 .lbl { padding: 6px 10px; font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: .4px; background: #f1f5fa; border-bottom: 1px solid #e6ecf4; }
 img { display: block; width: 100%; height: auto; }
 style-count { display: block; font-size: 12px; color: #5b6b80; margin-top: 6px; }
 b { color: #182230; }
 .axe { margin-top: 8px; font-size: 12px; }
 .axe b { color: #c0392b; }
</style>
<h1>P4 — семь эталонных экранов: было / стало</h1>
<div class="sub">Слева — код до трека (коммит 493fd72), справа — рабочее дерево. Одни и те же фикстуры с обеих сторон.
Полные страницы, не окно просмотра.</div>
${pairs.map(({ screen, vp, b, a }) => `
<section>
  <h2>${screen.n}. ${esc(screen.title)} — ${vp}</h2>
  <div class="why">${esc(screen.why)} · <code>${esc(screen.url)}</code> · роль ${esc(screen.role)}</div>
  <div class="row">
    ${cell(b, 'БЫЛО')}
    ${cell(a, 'СТАЛО')}
  </div>
  <div class="row">
    <div>${facts(b)}</div>
    <div>${facts(a)}${a && a.axe ? `<div class="axe">axe: serious <b>${a.axe.serious || 0}</b>, critical <b>${a.axe.critical || 0}</b>, moderate ${a.axe.moderate || 0}, minor ${a.axe.minor || 0}</div>` : ''}</div>
  </div>
</section>`).join('')}
`;
  fs.writeFileSync(path.join(OUT, 'index.html'), html);

  const shot = results.filter((r) => !r.error).length;
  const failed = results.filter((r) => r.error || r.status >= 400);
  console.log(`frames: ${shot}, problems: ${failed.length}`);
  for (const f of failed) console.log(`  ${f.side} ${f.slug} ${f.viewport}: ${f.error || 'HTTP ' + f.status}`);
};

main().catch((e) => { console.error('FAILED:', e.message); process.exit(1); });
