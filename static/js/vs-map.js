/* vs-map.js -- obshchaya karta programmy: GPS, Drony, budushchie reysy.
 *
 * ODIN komponent na programmu, a ne po karte na modul: treki, uchastki i
 * kontury poley lezhat na odnoy zemle, i N realizaciy -- eto N bibliotek,
 * versiy i naborov oshibok. Biblioteka -- Leaflet 1.9.4, polozhennaya v
 * repozitoriy (static/vendor/leaflet/), bez CDN: vsyo on-premises. Soglasie
 * vladelca na Leaflet -- 28.09.2026 (docs/tracks/gps-plan-fakt.md).
 * Servernaya polovina -- vs_map.py (podlozhki), stili -- static/css/vs-map.css.
 *
 * KONTRAKT RAZMETKI
 *   <div class="vs-map" data-vs-map="ID">
 *     <div class="vs-map-fallback">...zapasnaya kartinka...</div>
 *   </div>
 *   <script type="application/json" id="ID">{...}</script>
 *
 * JSON:
 *   base:   [{key, title, url, attribution, maxZoom}]  -- pervaya vklyuchena
 *   layers: [{kind, title, group, ...}], v poryadke snizu vverh:
 *     outline  {geojson}                     kontur polya, bez zalivki
 *     track    {segments: [[[lat, lon], ...], ...]}
 *     area     {geojson, label, tone: 'primary' | 'success' | 'danger'}
 *   group -- imya sloya v pereklyuchatele (neobyazatelno).
 *
 * [REASON]: dannye -- v <script type="application/json">, a ne v atribute.
 * Filtr tojson ne ekraniruet dvoynuyu kavychku, i v atribute s dvoynymi
 * kavychkami parser oborval by znachenie molcha (CLAUDE.md, klassy defektov).
 *
 * [REASON]: podpisi prihodyat s servera gotovymi na yazyke interfeysa i
 * vstavlyayutsya TEKSTOM (textContent), nikogda kak HTML: imya kontura
 * prihodit iz Wialon, gde ego mozhet napisat kto ugodno.
 *
 * Esli Leaflet ne zagruzilsya ili JSON bityy -- komponent nichego ne delaet,
 * i v konteynere ostayotsya zapasnaya kartinka. Pustoy seryy pryamougolnik
 * vmesto nee byl by huzhe.
 */
(function () {
  'use strict';

  function readData(host) {
    var node = document.getElementById(host.getAttribute('data-vs-map'));
    if (!node) return null;
    try {
      return JSON.parse(node.textContent);
    } catch (e) {
      return null;
    }
  }

  function escapeHtml(text) {
    var span = document.createElement('span');
    span.textContent = String(text);
    return span.innerHTML;
  }

  function textElement(text) {
    var span = document.createElement('span');
    span.textContent = String(text);
    return span;
  }

  function trackLayer(item) {
    var segments = (item.segments || []).filter(function (s) { return s.length > 1; });
    if (!segments.length) return null;
    // [REASON]: trek razbit na kuski serverom tam, gde treker molchal dolshe
    // pyati minut. Odna liniya cherez razryv narisovala by pryamuyu cherez
    // pole, po kotoroy mashina ne ezdila.
    return L.polyline(segments, { className: 'vs-map-track' });
  }

  function geoLayer(item, className) {
    if (!item.geojson) return null;
    try {
      return L.geoJSON(item.geojson, {
        style: function () { return { className: className }; }
      });
    } catch (e) {
      return null;
    }
  }

  function buildLayer(item) {
    var layer = null;
    if (item.kind === 'track') {
      layer = trackLayer(item);
    } else if (item.kind === 'outline') {
      layer = geoLayer(item, 'vs-map-outline');
    } else if (item.kind === 'area') {
      layer = geoLayer(item, 'vs-map-area is-' + (item.tone || 'primary'));
    }
    if (!layer) return null;
    if (item.kind === 'area' && item.label) {
      // Nomer uchastka pryamo na karte -- tot zhe, chto v tablice pod ney.
      // Podrobnosti -- po shchelchku: vtoraya vsplyvayushchaya podskazka na
      // tom zhe sloe zamenila by nomer.
      layer.bindTooltip(textElement(item.label), {
        permanent: true, direction: 'center', className: 'vs-map-label'
      });
      if (item.title) layer.bindPopup(textElement(item.title));
    } else if (item.title) {
      layer.bindTooltip(textElement(item.title), { sticky: true });
    }
    return layer;
  }

  function extend(bounds, layer) {
    var b = layer.getBounds && layer.getBounds();
    if (!b || !b.isValid()) return bounds;
    if (!bounds) return L.latLngBounds(b.getSouthWest(), b.getNorthEast());
    return bounds.extend(b);
  }

  function mount(host) {
    if (!window.L || host.getAttribute('data-vs-map-ready')) return;
    var data = readData(host);
    if (!data || !data.base || !data.base.length) return;

    var built = [];
    (data.layers || []).forEach(function (item) {
      var layer = buildLayer(item);
      if (layer) built.push({ item: item, layer: layer });
    });
    if (!built.length) return;

    var canvas = document.createElement('div');
    canvas.className = 'vs-map-canvas';
    host.appendChild(canvas);
    // [REASON]: koleso myshi ne zahvatyvaetsya, poka chelovek ne shchyolknul
    // po karte: inache stranica perestayot prokruchivatsya, kak tolko kursor
    // proshyol nad kartoy.
    var map = L.map(canvas, { scrollWheelZoom: false, zoomSnap: 0.5 });
    map.once('click', function () { map.scrollWheelZoom.enable(); });

    var bases = {};
    data.base.forEach(function (base, index) {
      var tiles = L.tileLayer(base.url, {
        attribution: base.attribution || '',
        maxZoom: base.maxZoom || 19
      });
      bases[escapeHtml(base.title)] = tiles;
      if (index === 0) tiles.addTo(map);
    });

    var overlays = {};
    var bounds = null;
    built.forEach(function (entry) {
      bounds = extend(bounds, entry.layer);
      if (!entry.item.group) {
        entry.layer.addTo(map);
        return;
      }
      var name = escapeHtml(entry.item.group);
      if (!overlays[name]) overlays[name] = L.layerGroup().addTo(map);
      overlays[name].addLayer(entry.layer);
    });
    if (Object.keys(bases).length > 1 || Object.keys(overlays).length) {
      L.control.layers(bases, overlays, { collapsed: true }).addTo(map);
    }
    L.control.scale({ imperial: false }).addTo(map);

    // Zapasnaya kartinka pryachetsya TOLKO kogda karta uzhe stoit.
    Array.prototype.forEach.call(host.querySelectorAll('.vs-map-fallback'), function (node) {
      node.hidden = true;
    });
    host.setAttribute('data-vs-map-ready', '1');
    map.invalidateSize();
    if (bounds) {
      map.fitBounds(bounds, { padding: [24, 24], maxZoom: 17 });
    } else {
      map.setView([39.77, 64.42], 10);
    }
  }

  function mountAll() {
    Array.prototype.forEach.call(document.querySelectorAll('[data-vs-map]'), mount);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', mountAll);
  } else {
    mountAll();
  }
})();
