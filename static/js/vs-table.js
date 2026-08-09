/* vs-table.js -- klientskaya sortirovka dlya .vs-table.is-static (DD-029).
 *
 * Vklyuchaetsya TOLKO na tablicah, kotorye ee poprosili: is-static plus
 * data-sortable. [REASON]: v rezhimah is-paged i is-stream na stranice lezhit
 * ODNA stranica dannyh, a ne vsya vyborka. Klientskaya sortirovka
 * perestavila by stroki vnutri okna i vyglyadela by kak sortirovka vsey
 * tablicy -- otvet byl by tiho nevernym. Tam sortiruet server.
 *
 * Bez zavisimostey i bez inline-koda: vneshniy fayl kешiruetsya i ne
 * uchastvuet v 2836 strokah inline-JS, kotorye trek sokrashchaet.
 */
(function () {
  'use strict';

  var COLLATOR = new Intl.Collator(['ru', 'uz'], { numeric: true, sensitivity: 'base' });

  /* Znachenie yacheyki dlya sravneniya. data-sort-value pobezhdaet tekst:
     [REASON]: na ekrane chislo otformatirovano ("15 878,64 sum", "01.02.2026"),
     i sravnenie po vidimoy stroke otsortirovalo by summy kak tekst, a daty --
     po dnyu mesyaca. Shablon kladet syroe znachenie v atribut. */
  function cellValue(row, index) {
    var cell = row.cells[index];
    if (!cell) return '';
    var raw = cell.getAttribute('data-sort-value');
    return raw === null ? cell.textContent.trim() : raw;
  }

  function compare(a, b) {
    var na = parseFloat(a.replace(',', '.'));
    var nb = parseFloat(b.replace(',', '.'));
    var aNum = a !== '' && !isNaN(na) && /^[-+]?[\d\s.,]+$/.test(a);
    var bNum = b !== '' && !isNaN(nb) && /^[-+]?[\d\s.,]+$/.test(b);
    if (aNum && bNum) return na - nb;
    // Pustaya yacheyka vsegda vnizu, v lyubom napravlenii: "net znacheniya" --
    // ne samoe maloe znachenie, i vsplyvat naverh pri obratnoy sortirovke ono
    // ne dolzhno.
    if (a === '' ) return 1;
    if (b === '' ) return -1;
    return COLLATOR.compare(a, b);
  }

  function sortBy(table, th, index, direction) {
    var body = table.tBodies[0];
    if (!body) return;
    var rows = Array.prototype.slice.call(body.rows);
    var sign = direction === 'descending' ? -1 : 1;
    rows.sort(function (r1, r2) {
      return sign * compare(cellValue(r1, index), cellValue(r2, index));
    });
    rows.forEach(function (row) { body.appendChild(row); });

    // aria-sort stoit ROVNO na odnom zagolovke: tak trebuet specifikaciya, i
    // tak zhe rabotaet CSS-strelka.
    Array.prototype.forEach.call(table.tHead.rows[0].cells, function (cell) {
      cell.removeAttribute('aria-sort');
    });
    th.setAttribute('aria-sort', direction);
  }

  function enhance(table) {
    var head = table.tHead;
    if (!head || !head.rows.length) return;
    Array.prototype.forEach.call(head.rows[0].cells, function (th, index) {
      if (th.dataset.sort === undefined) return;
      var label = th.textContent.trim();
      var button = document.createElement('button');
      button.type = 'button';
      button.textContent = label;
      th.textContent = '';
      th.appendChild(button);
      th.classList.add('vs-th-sort');
      button.addEventListener('click', function () {
        var next = th.getAttribute('aria-sort') === 'ascending' ? 'descending' : 'ascending';
        sortBy(table, th, index, next);
      });
    });

    // Sortirovka po umolchaniyu obyavlyaetsya na ekrane yavno (DD-029), a ne
    // ugadyvaetsya: data-sort-default="3:descending".
    var def = table.dataset.sortDefault;
    if (def) {
      var parts = def.split(':');
      var idx = parseInt(parts[0], 10);
      var th = head.rows[0].cells[idx];
      if (th && th.classList.contains('vs-th-sort')) {
        sortBy(table, th, idx, parts[1] === 'descending' ? 'descending' : 'ascending');
      }
    }
  }

  /* Oblast s gorizontalnoy prokrutkoy dolzhna byt dostizhima s KLAVIATURY.
   *
   * [REASON]: .vs-table-scroll prokruchivaetsya myshyu i palcem, no bez
   * tabindex fokus v nee ne popadaet vovse -- chelovek, rabotayushchiy s
   * klaviatury, prosto ne mozhet uvidet pravye kolonki shirokoy tablicy.
   * Eto pravilo axe scrollable-region-focusable, i najdeno ono bylo tolko
   * kogda proverku zapustili na 390 px: na desktope tablica pomeshchaetsya i
   * narusheniya net. Otsyuda i vtoroe sledstvie -- proverka dostupnosti
   * obyazana gonyatsya na uzkom vyeporte tozhe, inache ona ne razlichaet
   * ispravnyy sluchay i neispravnyy.
   *
   * tabindex stavitsya TOLKO kogda soderzhimoe deystvitelno ne pomeshchaetsya:
   * lishnyaya ostanovka taba na tom, chto i tak vidno celikom, meshaet.
   */
  function markScrollRegions() {
    document.querySelectorAll('.vs-table-scroll').forEach(function (box) {
      var overflows = box.scrollWidth > box.clientWidth + 1;
      if (!overflows) {
        if (box.dataset.vsScrollRegion) {
          box.removeAttribute('tabindex');
          box.removeAttribute('role');
          box.removeAttribute('aria-label');
          delete box.dataset.vsScrollRegion;
        }
        return;
      }
      if (box.dataset.vsScrollRegion) return;
      // Podpis beretsya iz zagolovka kartochki nad tablicey, a ne
      // pridumyvaetsya zdes: JS ne znaet yazyka interfeysa, a v proekte
      // vsyakaya stroka dvuyazychna.
      var card = box.closest('.vs-table-wrap, .vs-card');
      var title = card && card.querySelector('.vs-card-title');
      box.setAttribute('tabindex', '0');
      box.setAttribute('role', 'region');
      box.setAttribute('aria-label', (title ? title.textContent : document.title).trim());
      box.dataset.vsScrollRegion = '1';
    });
  }

  function init() {
    document.querySelectorAll('table.vs-table.is-static[data-sortable]').forEach(enhance);
    markScrollRegions();
    window.addEventListener('resize', markScrollRegions);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
