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
