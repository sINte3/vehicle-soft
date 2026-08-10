// tools/ux/check_ref_admin_interactions.mjs -- UI-P6-001, povedenie posle
// migracii razmetki.
//
// Zachem otdelnyy skript, a ne test v tests/
// -----------------------------------------
// Testy proekta rendaryat HTML i sravnivayut stroki -- v nih net dvizhka JS.
// Migraciya modulya spravochnikov perevela obshchie stroki redaktirovaniya s
// inline-stilya `display:none` na atribut `hidden`, a pokaz/skrytie -- s
// `el.style.display` na `el.hidden`. Eto povedencheskaya pravka: shablon
// rendaritsya odinakovo verno v oboih sluchayah, i lyuboy strokovyy test
// molcha proshel by na slomannom kode.
//
// Vtoraya prichina: klientskaya sortirovka (DD-029) pereklAdyvaet VSE stroki
// tbody, vklyuchaya skrytye sluzhebnye. Nuzhno dokazatelstvo, chto posle
// sortirovki oni ostalis skrytymi, a otkrytaya forma po-prezhnemu vstaet pod
// svoyu stroku.
//
// Trebuet podnyatogo ekzemplyara (tools/ux/serve_ephemeral.py).
// Zapusk:
//   PLAYWRIGHT_BROWSERS_PATH=/opt/pw-browsers \
//     node tools/ux/check_ref_admin_interactions.mjs --base http://127.0.0.1:5099

import { chromium } from 'playwright';
const CHROME='/opt/pw-browsers/chromium-1194/chrome-linux/chrome';
const BASE = (() => { const i = process.argv.indexOf('--base');
  return i > -1 && process.argv[i+1] ? process.argv[i+1] : 'http://127.0.0.1:5099'; })();
async function login(page, role){
  await page.goto(BASE+'/login',{waitUntil:'domcontentloaded'});
  await page.fill('input[name="username"]',role);
  await page.fill('input[name="password"]','ux-audit-local');
  await Promise.all([page.waitForLoadState('domcontentloaded'),
    page.click('form.vs-login-form button[type="submit"]')]);
}
const b=await chromium.launch({executablePath:CHROME});
const ctx=await b.newContext({viewport:{width:1440,height:900}});
const p=await ctx.newPage(); await login(p,'ux_admin');
let fail=0;
const check=(name,cond,extra='')=>{ console.log((cond?'ok   ':'FAIL ')+name+(extra?'  '+extra:'')); if(!cond) fail++; };

// --- work types ---
await p.goto(BASE+'/ref/work_types',{waitUntil:'networkidle'});
check('wt: edit row hidden on load', await p.locator('#wt-shared-edit-row').isHidden());
const firstWt = p.locator('tr[data-wt-id]').first();
const wtName = await firstWt.getAttribute('data-name');
await firstWt.locator('button.vs-btn-icon').first().click();
check('wt: edit row visible after click', await p.locator('#wt-shared-edit-row').isVisible());
const filled = await p.inputValue('#wt-shared-name');
check('wt: name copied into the form', filled === wtName, `"${filled}" vs "${wtName}"`);
const pos = await p.evaluate(() => {
  const r=document.querySelector('tr.wt-editing'), e=document.getElementById('wt-shared-edit-row');
  return r && r.nextElementSibling === e;
});
check('wt: edit row sits right under the edited row', pos);
await p.click('#wt-shared-edit-row button.vs-btn:not(.vs-btn-primary)');
check('wt: cancel hides it again', await p.locator('#wt-shared-edit-row').isHidden());

// sorting must not reveal the hidden rows
await p.click('table.vs-table th.vs-th-sort >> nth=1');
check('wt: still hidden after a client sort', await p.locator('#wt-shared-edit-row').isHidden());
check('wt: delete-form row still hidden', await p.locator('#wt-shared-delete-row').isHidden());

// --- equipment ---
await p.goto(BASE+'/ref/equipment',{waitUntil:'networkidle'});
check('eq: edit row hidden on load', await p.locator('#ref-equipment-edit-row').isHidden());
const firstEq = p.locator('tr[data-eq-id]').first();
const eqPlate = await firstEq.getAttribute('data-plate');
const eqId = await firstEq.getAttribute('data-eq-id');
await firstEq.locator('button.vs-btn-icon').first().click();
check('eq: edit row visible after click', await p.locator('#ref-equipment-edit-row').isVisible());
check('eq: edited row itself is hidden', await p.locator('#eq-row-'+eqId).isHidden());
check('eq: plate copied', (await p.inputValue('#ref-eq-edit-plate')) === eqPlate);
const orgOpts = await p.locator('#ref-eq-edit-organization-id option').count();
check('eq: org select populated by JS', orgOpts > 1, 'options='+orgOpts);
await p.click('#ref-equipment-edit-row button.vs-btn:not(.vs-btn-primary)');
check('eq: cancel restores the row', await p.locator('#eq-row-'+eqId).isVisible()
      && await p.locator('#ref-equipment-edit-row').isHidden());

// --- new-model box toggling (hidden attribute) ---
await p.selectOption('#eqNewModel','__new__');
check('eq: new-model box appears', await p.locator('#add-new-model-name').isVisible());
await p.selectOption('#eqNewModel','');
check('eq: new-model box hides again', await p.locator('#add-new-model-name').isHidden());

// --- multiselect still initialises ---
const trig = await p.locator('#ms-cats-eq .ms-trigger').textContent();
check('eq: multiselect label rendered by initMultiselect', (trig||'').trim().length>0, JSON.stringify(trig));

// --- admin users edit ---
await p.goto(BASE+'/admin/users',{waitUntil:'networkidle'});
await p.locator('table.vs-table tbody tr').first().locator('.vs-row-actions-cell button.vs-btn-icon').first().click();
const uname = await p.inputValue('#form-username');
check('users: edit fills the form', uname.length>0, uname);

await b.close();
console.log(fail? `\n${fail} FAILED` : '\nall interactive checks passed');
process.exit(fail?1:0);
