// tools/ux/check_module_render.mjs -- proverka MODULYA V RENDERE.
//
// Zachem, esli est check_design_system.py
// --------------------------------------
// Chekery chitayut ISHODNIKI shablonov. Etogo ne hvataet po trem prichinam,
// i vse tri nayideny na zhivom module:
//
//   1. Shablon mozhet byt chist, a stranica padat: pereimenovanie klyucha
//      konteksta (status_colors -> status_tones) daet HTTP 500, kotorogo v
//      ishodnike ne vidno voobshche.
//   2. Glif mozhet prihodit iz DANNYH ili iz vklyuchennogo partiala, a ne iz
//      shablona modulya.
//   3. Tablica mozhet lezhat v partiale, i "vse tablicy modulya ob'yavili
//      rezhim" po fayl-maske okazhetsya nepravdoy.
//
// Skript otkryvaet kazhdyy ekran nastoyashchim brauzerom i trebuet: HTTP 200
// bez redirekta v /login, nol glifov v vidimom tekste, nol tablic .vs-table
// bez rezhima, nol oshibok JS na stranice.
//
// Trebuet podnyatogo ekzemplyara (tools/ux/serve_ephemeral.py).
// Zapusk:
//   PLAYWRIGHT_BROWSERS_PATH=/opt/pw-browsers node tools/ux/check_module_render.mjs \
//     --base http://127.0.0.1:5099 --urls docs/ux/41-task-specs/UI-P6-004-urls.json

import { chromium } from 'playwright';
const CHROME='/opt/pw-browsers/chromium-1194/chrome-linux/chrome';
const arg=(n,d)=>{const i=process.argv.indexOf('--'+n);return i>-1&&process.argv[i+1]?process.argv[i+1]:d;};
const BASE=arg('base','http://127.0.0.1:5099');
import fs from 'node:fs';
const URLS=JSON.parse(fs.readFileSync(arg('urls','docs/ux/41-task-specs/UI-P6-004-urls.json'),'utf8'));
async function login(page){await page.goto(BASE+'/login',{waitUntil:'domcontentloaded'});
 await page.fill('input[name="username"]','ux_admin');await page.fill('input[name="password"]','ux-audit-local');
 await Promise.all([page.waitForLoadState('domcontentloaded'),page.click('form.vs-login-form button[type="submit"]')]);}
// [REASON]: STALYY PROCESS -- samaya chastaya lozh etoy proverki. Ekzemplyar
// keshiruet shablony na vremya zhizni processa (auto_reload idet za app.debug,
// a on False), poetomu server, podnyatyy DO pravki shablona, otdaet staruyu
// razmetku i proverka rugaetsya na uzhe ispravlennoe. Tri raza za fazu eto
// dalo lozhnye padeniya, odin raz -- 12 iz 14.
// Proverka po logu ("Address already in use") okazalas nedostatochnoy.
// Nadezhno tolko sravnenie vremen: process obyazan byt MOLOZHE samogo svezhego
// shablona i samogo svezhego CSS.
{
  // Vremya samogo svezhego shablona ili CSS. Sravnite ego so vremenem starta
  // ekzemplyara (`ps -eo pid,lstart,args | grep serve_ephemeral`): esli server
  // STARSHE -- on otdaet keshirovannye shablony, i lyuboe padenie nizhe lozhno.
  const newest = Math.max(
    ...['templates', 'static/css'].flatMap(function walk(d) {
      return fs.readdirSync(d, {withFileTypes: true}).flatMap((e) =>
        e.isDirectory() ? walk(d + '/' + e.name)
                        : [fs.statSync(d + '/' + e.name).mtimeMs]);
    }));
  console.log('newest template/CSS: ' + new Date(newest).toISOString()
    + '  -- stand must have started AFTER this');
}

const b=await chromium.launch({executablePath:CHROME});
const ctx=await b.newContext({viewport:{width:1440,height:900}});
const p=await ctx.newPage(); await login(p);
let bad=0;
const errs=[]; p.on('pageerror',e=>errs.push(e.message));
for(const u of URLS){
  errs.length=0;
  const r=await p.goto(BASE+u,{waitUntil:'networkidle',timeout:25000}).catch(()=>null);
  const st=r?r.status():0;
  const glyph=await p.evaluate(()=>{const t=document.body.innerText;
    return (t.match(/[\u{1F000}-\u{1FAFF}☀-➿]/gu)||[]).length;});
  const noMode=await p.evaluate(()=>[...document.querySelectorAll('table.vs-table')]
    .filter(t=>!/is-(static|paged|stream)/.test(t.className)).length);
  const ok = st===200 && !p.url().includes('/login') && glyph===0 && noMode===0 && errs.length===0;
  if(!ok) bad++;
  console.log(`${ok?'ok  ':'FAIL'} ${String(st).padStart(3)} glyph=${glyph} noMode=${noMode} js=${errs.length} ${u}${errs.length?' :: '+errs[0].slice(0,70):''}`);
}
await b.close(); console.log(bad?`\n${bad} FAILED`:`\nall ${URLS.length} screens render clean`);
process.exit(bad?1:0);
