// GPS A2b · UX · подложки карты /gps/fact: чёткий Esri и свежий Sentinel-2.
//
// Сети до Esri и Copernicus в проверке нет и не должно быть: ответы сервисов
// подменяются в браузере (page.route), а стенд кладёт НЕНАСТОЯЩИЕ ключ и
// идентификатор. Проверяется НАШ код -- что он спрашивает и что показывает:
//   * по умолчанию открыт чёткий Esri, и квота Copernicus не тратится:
//     до переключения ни одного запроса плиток Sentinel-2;
//   * дата снимка спрашивается у WFS один раз: DSS2, окно дат суток работы,
//     MAXCC=30, рамка в EPSG:3857 над Бухарой;
//   * под картой -- дата самого свежего снимка из ответа;
//   * после переключения плитки просят ровно эту дату, истинные цвета,
//     облачность, EPSG:3857, без логотипа;
//   * снимков нет -> подпись «нет безоблачного снимка»; WFS не ответил ->
//     «дату узнать не удалось», и ошибок страницы нет ни там, ни там;
//   * узбекский интерфейс подписывает по-узбекски.
//
// Стенд: python tools/ux/serve_gps_fact.py --port 5099 --state-dir <dir> --imagery
// Запуск: node tools/ux/check_gps_imagery.mjs --base http://127.0.0.1:5099 \
//           --state <dir>/ux_admin.json --state-uz <dir>/ux_admin_uz.json
// Окружение: CHROME -- путь к браузеру.
// Код выхода: 0 -- всё прошло, 1 -- есть провалы (каждый строкой '!!').

const args = Object.fromEntries(process.argv.slice(2).reduce((acc, v, i, a) =>
  (v.startsWith('--') ? acc.concat([[v.slice(2), a[i + 1]]]) : acc), []));
const BASE = args.base || 'http://127.0.0.1:5099';
const PAGE = BASE + '/gps/fact?date=2026-07-27&unit=3464';
const { chromium } = await import(process.env.PLAYWRIGHT_MODULE || 'playwright');

// Ненастоящие значения стенда (tools/ux/serve_gps_fact.py, --imagery).
const FAKE_KEY = 'AAPK-ux-stand-not-a-real-key';
const FAKE_INSTANCE = '00000000-0000-4000-8000-00000000ux01';
const PNG = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==', 'base64');

let failures = 0;
const bad = (msg) => { failures += 1; console.log('!! ' + msg); };
const ok = (msg) => console.log('ok ' + msg);
const expect = (cond, msg) => (cond ? ok(msg) : bad(msg));

const launch = process.env.CHROME ? { executablePath: process.env.CHROME } : {};
const browser = await chromium.launch(launch);

const SCENES = { features: [
  { type: 'Feature', properties: { date: '2026-07-25', cloudCoverPercentage: 3 } },
  { type: 'Feature', properties: { date: '2026-08-09', cloudCoverPercentage: 0 } },
  { type: 'Feature', properties: { date: '2026-08-02', cloudCoverPercentage: 12 } },
] };

async function open(state, wfs) {
  const ctx = await browser.newContext({ viewport: { width: 1280, height: 800 }, storageState: state });
  const page = await ctx.newPage();
  const seen = { esri: [], wms: [], wfs: [], osm: [], errors: [] };
  page.on('pageerror', (e) => seen.errors.push(String(e)));
  page.on('console', (m) => {
    if (m.type() === 'error' && !/Failed to load resource/.test(m.text())) seen.errors.push(m.text());
  });
  await page.route('https://ibasemaps-api.arcgis.com/**', (r) => { seen.esri.push(r.request().url()); r.fulfill({ body: PNG, contentType: 'image/png' }); });
  await page.route('https://tile.openstreetmap.org/**', (r) => { seen.osm.push(r.request().url()); r.fulfill({ body: PNG, contentType: 'image/png' }); });
  await page.route('https://sh.dataspace.copernicus.eu/ogc/wms/**', (r) => { seen.wms.push(r.request().url()); r.fulfill({ body: PNG, contentType: 'image/png' }); });
  await page.route('https://sh.dataspace.copernicus.eu/ogc/wfs/**', (r) => {
    seen.wfs.push(r.request().url());
    if (wfs === 'fail') return r.abort();
    return r.fulfill({ body: JSON.stringify(wfs), contentType: 'application/json',
                       headers: { 'access-control-allow-origin': '*' } });
  });
  const response = await page.goto(PAGE, { waitUntil: 'load' });
  await page.waitForSelector('[data-vs-map-ready="1"]', { timeout: 10000 }).catch(() => {});
  await page.waitForFunction(() => {
    const n = document.querySelector('[data-vs-map-note="fresh"]');
    return n && !n.hidden;
  }, null, { timeout: 5000 }).catch(() => {});
  return { ctx, page, seen, status: response ? response.status() : 0 };
}

const note = (page) => page.evaluate(() => {
  const n = document.querySelector('[data-vs-map-note="fresh"]');
  return n ? { hidden: n.hidden, text: n.textContent.trim() } : null;
});
const param = (url, name) => new URL(url).searchParams.get(name);

// --- A: снимки есть ---------------------------------------------------------
{
  const { ctx, page, seen, status } = await open(args.state, SCENES);
  expect(status === 200, `A page loads (${status})`);
  expect(seen.esri.length > 0 && seen.esri.every((u) => u.includes('token=' + FAKE_KEY)),
    `A sharp Esri opens first, with the key (${seen.esri.length} tiles)`);
  expect(seen.wms.length === 0, `A Copernicus quota untouched until switched (${seen.wms.length} WMS tiles)`);
  expect(seen.wfs.length === 1, `A one WFS date lookup (${seen.wfs.length})`);
  if (seen.wfs.length) {
    const w = seen.wfs[0];
    expect(w.startsWith('https://sh.dataspace.copernicus.eu/ogc/wfs/' + FAKE_INSTANCE + '?'), 'A WFS goes to our instance');
    expect(param(w, 'TYPENAMES') === 'DSS2', `A WFS asks Sentinel-2 L2A (${param(w, 'TYPENAMES')})`);
    expect(param(w, 'TIME') === '2026-06-27/2026-08-11', `A WFS window around the work day (${param(w, 'TIME')})`);
    expect(param(w, 'MAXCC') === '30', `A WFS cloud cap (${param(w, 'MAXCC')})`);
    expect(param(w, 'SRSNAME') === 'EPSG:3857', `A WFS in web mercator (${param(w, 'SRSNAME')})`);
    const b = (param(w, 'BBOX') || '').split(',').map(Number);
    // Бухара: x ~ 7,18e6, y ~ 4,86e6 в EPSG:3857; перепутанные оси дали бы наоборот
    expect(b.length === 4 && b[0] > 7.1e6 && b[0] < 7.3e6 && b[1] > 4.8e6 && b[1] < 4.95e6 && b[2] > b[0] && b[3] > b[1],
      `A WFS bbox over Bukhara, x before y (${b.map((v) => Math.round(v)).join(',')})`);
  }
  const n = await note(page);
  expect(n && !n.hidden && n.text.includes('09.08.2026'), `A note names the freshest scene (${n && n.text})`);
  // переключиться на свежий снимок
  await page.hover('.leaflet-control-layers');
  await page.getByText('Свежий снимок (Sentinel-2, 10 м)').click();
  await page.waitForTimeout(800);
  expect(seen.wms.length > 0, `A switching asks for Sentinel-2 tiles (${seen.wms.length})`);
  if (seen.wms.length) {
    const t = seen.wms[seen.wms.length - 1];
    expect(t.startsWith('https://sh.dataspace.copernicus.eu/ogc/wms/' + FAKE_INSTANCE + '?'), 'A WMS goes to our instance');
    expect(param(t, 'TIME') === '2026-08-09/2026-08-09', `A tiles ask exactly that date (${param(t, 'TIME')})`);
    expect(param(t, 'LAYERS') === 'TRUE_COLOR', `A true colour (${param(t, 'LAYERS')})`);
    expect(param(t, 'MAXCC') === '30', `A tile cloud cap (${param(t, 'MAXCC')})`);
    expect(param(t, 'PRIORITY') === 'mostRecent', `A most recent first (${param(t, 'PRIORITY')})`);
    expect(param(t, 'SHOWLOGO') === 'false', `A no logo on tiles (${param(t, 'SHOWLOGO')})`);
    expect(param(t, 'CRS') === 'EPSG:3857', `A web mercator (${param(t, 'CRS')})`);
  }
  const attribution = await page.locator('.leaflet-control-attribution').textContent();
  expect(/Copernicus Sentinel data 2026/.test(attribution), 'A Copernicus attribution shown');
  expect(seen.errors.length === 0, `A no page errors (${seen.errors.join(' | ')})`);
  await ctx.close();
}

// --- B: безоблачных снимков нет ---------------------------------------------
{
  const { ctx, page, seen } = await open(args.state, { features: [] });
  const n = await note(page);
  expect(n && !n.hidden && n.text.includes('Безоблачного снимка Sentinel-2 за 27.06.2026–11.08.2026 нет'),
    `B no scene is said so (${n && n.text})`);
  expect(seen.errors.length === 0, `B no page errors (${seen.errors.join(' | ')})`);
  await ctx.close();
}

// --- C: WFS не ответил ------------------------------------------------------
{
  const { ctx, page, seen } = await open(args.state, 'fail');
  const n = await note(page);
  expect(n && !n.hidden && n.text.includes('Дату снимка Sentinel-2 узнать не удалось'),
    `C failed lookup is said so (${n && n.text})`);
  expect(seen.errors.length === 0, `C no page errors (${seen.errors.join(' | ')})`);
  await ctx.close();
}

// --- D: узбекский интерфейс -------------------------------------------------
if (args['state-uz']) {
  const { ctx, page, seen } = await open(args['state-uz'], SCENES);
  const n = await note(page);
  expect(n && n.text.includes('09.08.2026') && n.text.includes('санадаги'), `D uzbek note (${n && n.text})`);
  const labels = await page.locator('.leaflet-control-layers-base label').allTextContents();
  expect(labels.some((l) => l.includes('Янги сурат (Sentinel-2, 10 м)')), `D uzbek layer name (${labels.join(' / ')})`);
  expect(seen.errors.length === 0, `D no page errors (${seen.errors.join(' | ')})`);
  await ctx.close();
} else {
  bad('no --state-uz');
}

await browser.close();
console.log(failures ? `FAILED: ${failures}` : 'ALL OK');
process.exit(failures ? 1 : 0);
