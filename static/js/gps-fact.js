/* gps-fact.js -- povedenie polki vybora na /gps/fact.
 *
 * Smena filtra srazu pokazyvaet novyy spisok, bez knopki "Pokazat":
 *   - organizaciya, vid tekhniki, "tolko bez otveta" -- spisok s nachala:
 *     otkrytaya mashina i nabrannyy tekst sbrasyvayutsya, server otkroet
 *     pervuyu mashinu novogo spiska;
 *   - sutki -- ta zhe mashina v drugoy den (esli v tot den ona est), poetomu
 *     sbrasyvaetsya tolko nabrannyy tekst: inache server iskal by imya mashiny,
 *     kotoroy v te sutki net, i soobshchil by "ne naydeno".
 * Bez skripta vsyo eto delaet knopka "Pokazat".
 */
(function () {
  'use strict';

  function init() {
    var form = document.querySelector('form[data-gps-fact-filters]');
    if (!form) return;
    var clear = function (name) {
      var field = form.querySelector('[name="' + name + '"]');
      if (field) field.value = '';
    };
    var submit = function () {
      if (form.requestSubmit) form.requestSubmit(); else form.submit();
    };
    form.addEventListener('change', function (e) {
      var target = e.target;
      if (!target || !target.name) return;
      if (target.name === 'date') {
        clear('q');
        submit();
      } else if (target.hasAttribute('data-gps-fact-filter')) {
        clear('q');
        clear('unit');
        submit();
      }
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
