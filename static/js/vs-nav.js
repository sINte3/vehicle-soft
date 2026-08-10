/* vs-nav.js -- zakrytie vypadayushchego spiska razdelov modulya.
 *
 * [REASON]: etot obrabotchik sushchestvoval DVAZHDY -- inline vnutri
 * templates/_spare_nav.html i templates/fuel/_fuel_nav.html, po 17 strok,
 * razlichalis tolko id elementa. Vynesennyy syuda, on rabotaet dlya lyubogo
 * .vs-modulenav-group, skolko by ih ni bylo na stranice, i ne trebuet
 * pridumyvat id kazhdomu novomu.
 *
 * <details>/<summary> ostaetsya polnostyu rabotosposobnym i BEZ etogo fayla:
 * on fokusiruem, otkryvaetsya s Enter/Space i zakryvaetsya povtornym
 * nazhatiem. Skript dobavlyaet dva udobstva -- zakrytie po shchelchku snaruzhi
 * i po Escape, -- a ne delaet element rabotosposobnym.
 */
(function () {
  'use strict';

  function closeAll(except) {
    document.querySelectorAll('details.vs-modulenav-group[open]').forEach(function (d) {
      if (d !== except) d.removeAttribute('open');
    });
  }

  document.addEventListener('click', function (e) {
    var inside = e.target.closest && e.target.closest('details.vs-modulenav-group');
    closeAll(inside);
  });

  document.addEventListener('keydown', function (e) {
    if (e.key !== 'Escape') return;
    var open = document.querySelector('details.vs-modulenav-group[open]');
    if (!open) return;
    open.removeAttribute('open');
    // Fokus vozvrashchaetsya na trigger: spisok, otkrytyy s klaviatury,
    // dolzhen s klaviatury zhe i zakryvatsya, ne teryaya mesta.
    var summary = open.querySelector('summary');
    if (summary) summary.focus();
  });
})();
