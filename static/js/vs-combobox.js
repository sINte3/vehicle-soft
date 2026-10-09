/* vs-combobox.js -- pole s podskazkami: nabor teksta suzhaet spisok.
 *
 * Zachem: vladelec 09.10.2026 o vybore mashiny na /gps/fact -- "s
 * vozmozhnostyu nabirat vruchnuyu nomer mashiny, programma predlagaet
 * varianty, glazami dolgo iskat nuzhnuyu mashinu". Obychnyy <select> na
 * dvesti mashin iskat glazami i zastavlyal.
 *
 * KONTRAKT RAZMETKI
 *   <div data-vs-combobox [data-vs-combobox-submit]>
 *     <input role="combobox" aria-controls="LIST" aria-expanded="false" name="q">
 *     <input type="hidden" data-vs-combobox-value name="...">
 *     <ul role="listbox" id="LIST" hidden>
 *       <li role="option" id="..." data-value="ZNACHENIE">
 *         <span class="vs-combobox-main">chto ishchetsya i vstavlyaetsya</span>
 *         <span class="vs-combobox-note">poyasnenie, ne ishchetsya</span>
 *       </li>
 *     </ul>
 *     <div class="vs-combobox-empty" hidden>nichego ne naydeno</div>
 *   </div>
 *
 * Vybor varianta kladet ego data-value v skrytoe pole, a tekst -- v vidimoe;
 * s data-vs-combobox-submit forma srazu otpravlyaetsya. Bez skripta pole
 * ostaetsya obychnym tekstovym: nabrannoe ishchet server tem zhe klyuchom.
 *
 * Klaviatura: strelki -- po spisku, Enter -- vybrat (bez strelok -- pervyy
 * naydennyy, kak sdelal by server), Escape -- zakryt i vernut prezhnee imya.
 *
 * [REASON]: varianty sushchestvuyut v razmetke s servera i tolko pryachutsya i
 * pokazyvayutsya. Ni odin tekst ne vstavlyaetsya kak HTML: imya mashiny prihodit
 * iz Wialon, gde ego mozhet napisat kto ugodno.
 */
(function () {
  'use strict';

  // [REASON]: klyuch poiska -- tot zhe, chto u servera (gps_routes.search_key):
  // strochnye, kirillica-dvoynik latinicy zamenena latinicey, probely i znaki
  // vybrosheny. Gosnomer v spravochnike zapisan to latinicey ("80 584 CA"), to
  // russkimi bukvami-dvoynikami ("80 156 SA" s russkimi S i A), a nabirayut ego
  // kak pridetsya. Ravenstvo etoy tablicy serverno zakrepleno testom
  // tests/test_gps_fact_picker.py.
  var LOOKALIKE_FROM = '\u0430\u0432\u0435\u0451\u043a\u043c\u043d\u043e\u0440\u0441\u0442\u0443\u0445';
  var LOOKALIKE_TO = 'abeekmhopctyx';
  var KEEP = /[0-9a-z\u0400-\u04ff]/;

  function searchKey(text) {
    var lower = String(text || '').toLowerCase();
    var out = '';
    for (var i = 0; i < lower.length; i++) {
      var ch = lower.charAt(i);
      var at = LOOKALIKE_FROM.indexOf(ch);
      if (at >= 0) ch = LOOKALIKE_TO.charAt(at);
      if (KEEP.test(ch)) out += ch;
    }
    return out;
  }

  function setup(box) {
    if (box.getAttribute('data-vs-combobox-ready')) return;
    var input = box.querySelector('[role="combobox"]');
    var hidden = box.querySelector('[data-vs-combobox-value]');
    var list = box.querySelector('[role="listbox"]');
    var empty = box.querySelector('.vs-combobox-empty');
    if (!input || !hidden || !list) return;

    var entries = Array.prototype.map.call(list.querySelectorAll('[role="option"]'),
      function (node) {
        var main = node.querySelector('.vs-combobox-main') || node;
        var label = main.textContent.trim();
        return { node: node, label: label, key: searchKey(label),
                 value: node.getAttribute('data-value') };
      });
    var chosen = input.value;
    var shown = [];
    var active = -1;

    function setActive(index) {
      if (active >= 0 && shown[active]) shown[active].node.classList.remove('is-active');
      active = index;
      if (active >= 0 && shown[active]) {
        var node = shown[active].node;
        node.classList.add('is-active');
        input.setAttribute('aria-activedescendant', node.id);
        if (node.scrollIntoView) node.scrollIntoView({ block: 'nearest' });
      } else {
        input.removeAttribute('aria-activedescendant');
      }
    }

    function render(text) {
      var key = searchKey(text);
      shown = [];
      entries.forEach(function (entry) {
        var hit = !key || entry.key.indexOf(key) >= 0;
        entry.node.hidden = !hit;
        if (hit) shown.push(entry);
      });
      setActive(-1);
      list.hidden = shown.length === 0;
      if (empty) empty.hidden = shown.length !== 0;
      input.setAttribute('aria-expanded', shown.length ? 'true' : 'false');
    }

    function close() {
      list.hidden = true;
      if (empty) empty.hidden = true;
      input.setAttribute('aria-expanded', 'false');
      setActive(-1);
    }

    function isOpen() {
      return !list.hidden || (empty !== null && !empty.hidden);
    }

    function choose(entry) {
      input.value = entry.label;
      hidden.value = entry.value;
      chosen = entry.label;
      close();
      var form = input.form;
      if (!form || !box.hasAttribute('data-vs-combobox-submit')) return;
      if (form.requestSubmit) form.requestSubmit(); else form.submit();
    }

    input.addEventListener('focus', function () {
      // Ves spisok srazu, a tekst vydelen: nabor zamenyaet imya, a ne
      // dopisyvaetsya k nemu.
      render('');
      setTimeout(function () { if (document.activeElement === input) input.select(); }, 0);
    });
    // Shchelchok po polyu, uzhe stoyashchemu v fokuse (posle Escape ili
    // vybora), tozhe otkryvaet spisok: focus vo vtoroy raz ne pridet.
    input.addEventListener('click', function () {
      if (!isOpen()) render(input.value === chosen ? '' : input.value);
    });
    input.addEventListener('input', function () {
      render(input.value);
    });
    input.addEventListener('keydown', function (e) {
      if (e.key === 'ArrowDown') {
        e.preventDefault();
        if (!isOpen()) render(input.value === chosen ? '' : input.value);
        if (shown.length) setActive(Math.min(active + 1, shown.length - 1));
      } else if (e.key === 'ArrowUp') {
        e.preventDefault();
        if (shown.length) setActive(Math.max(active - 1, 0));
      } else if (e.key === 'Enter') {
        if (!isOpen()) return;
        e.preventDefault();
        if (active >= 0) {
          choose(shown[active]);
        } else if (input.value !== chosen && shown.length) {
          choose(shown[0]);
        } else if (shown.length) {
          close();
        }
      } else if (e.key === 'Escape') {
        if (!isOpen()) return;
        e.preventDefault();
        close();
        input.value = chosen;
      } else if (e.key === 'Tab') {
        close();
      }
    });
    // mousedown, a ne click: click prishel by posle blur, kogda spisok uzhe
    // zakryt, i vybor by poteryalsya.
    list.addEventListener('mousedown', function (e) {
      var node = e.target && e.target.closest ? e.target.closest('[role="option"]') : null;
      if (!node) return;
      e.preventDefault();
      for (var i = 0; i < entries.length; i++) {
        if (entries[i].node === node) {
          choose(entries[i]);
          return;
        }
      }
    });
    input.addEventListener('blur', close);
    box.setAttribute('data-vs-combobox-ready', '1');
  }

  function setupAll() {
    Array.prototype.forEach.call(document.querySelectorAll('[data-vs-combobox]'), setup);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', setupAll);
  } else {
    setupAll();
  }

  window.vsSearchKey = searchKey;
})();
