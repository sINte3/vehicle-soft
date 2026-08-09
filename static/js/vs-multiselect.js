/* vs-multiselect.js -- povedenie komponenta .vs-multiselect.
 *
 * [REASON]: eti tridtsat strok zhili INLINE vnutri templates/index.html, i
 * poetomu komponent rabotal rovno na odnom ekrane. Otchet, kotoromu nuzhen
 * takoy zhe vybor neskolkih znacheniy, nes svoyu vtoruyu realizaciyu na
 * klassah .ms-* -- dva vida odnogo elementa upravleniya v odnom produkte.
 * Vynesennyy v otdelnyy fayl, komponent stanovitsya obshchim: razmetka --
 * makros templates/_vs_multiselect.html, povedenie -- zdes.
 *
 * Imena funkciy sohraneny (vsmsToggle/vsmsUpdate/vsmsAll): razmetka zovet ih
 * iz atributov onclick/onchange, i pereimenovanie potrebovalo by pravki
 * kazhdogo mesta radi nichego.
 */
(function (global) {
  'use strict';

  function vsmsToggle(id) {
    var ms = document.getElementById(id);
    if (!ms) return;
    var open = ms.classList.toggle('is-open');
    if (open) {
      document.querySelectorAll('.vs-multiselect.is-open').forEach(function (other) {
        if (other.id !== id) other.classList.remove('is-open');
      });
    }
  }

  function vsmsUpdate(id) {
    var ms = document.getElementById(id);
    if (!ms) return;
    var boxes = ms.querySelectorAll('input[type=checkbox]');
    var total = boxes.length;
    var checked = ms.querySelectorAll('input[type=checkbox]:checked').length;
    var countEl = ms.querySelector('.vs-ms-count');
    var labelEl = ms.querySelector('.vs-ms-label');
    if (!countEl || !labelEl) return;
    var allLabel = labelEl.dataset.allLabel || 'All';
    var selLabel = labelEl.dataset.selectedLabel || 'selected';
    if (checked === 0 || checked === total) {
      countEl.style.display = 'none';
      labelEl.textContent = allLabel;
    } else {
      countEl.style.display = '';
      countEl.textContent = checked + '/' + total;
      labelEl.textContent = checked + ' ' + selLabel;
    }
    // Sostoyanie dolzhno byt slyshno, a ne tolko vidno: trigger -- knopka,
    // otkryvayushchaya spisok, i ona obyazana soobshchat aria-expanded.
    var trigger = ms.querySelector('.vs-multiselect-trigger');
    if (trigger) trigger.setAttribute('aria-expanded', ms.classList.contains('is-open') ? 'true' : 'false');
  }

  function vsmsAll(id, value) {
    var ms = document.getElementById(id);
    if (!ms) return;
    ms.querySelectorAll('input[type=checkbox]').forEach(function (cb) { cb.checked = value; });
    vsmsUpdate(id);
  }

  document.addEventListener('click', function (e) {
    if (!e.target.closest('.vs-multiselect')) {
      document.querySelectorAll('.vs-multiselect.is-open').forEach(function (ms) {
        ms.classList.remove('is-open');
        var trigger = ms.querySelector('.vs-multiselect-trigger');
        if (trigger) trigger.setAttribute('aria-expanded', 'false');
      });
    }
  });

  // Escape zakryvaet spisok i vozvrashchaet fokus na trigger: bez etogo
  // spisok, otkrytyy s klaviatury, s klaviatury zhe ne zakryvaetsya.
  document.addEventListener('keydown', function (e) {
    if (e.key !== 'Escape') return;
    var open = document.querySelector('.vs-multiselect.is-open');
    if (!open) return;
    open.classList.remove('is-open');
    var trigger = open.querySelector('.vs-multiselect-trigger');
    if (trigger) { trigger.setAttribute('aria-expanded', 'false'); trigger.focus(); }
  });

  function init() {
    document.querySelectorAll('.vs-multiselect').forEach(function (ms) { vsmsUpdate(ms.id); });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }

  global.vsmsToggle = vsmsToggle;
  global.vsmsUpdate = vsmsUpdate;
  global.vsmsAll = vsmsAll;
})(window);
