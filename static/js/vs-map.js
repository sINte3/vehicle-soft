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
 * Neobyazatelno v razmetke:
 *   data-vs-map-view="Z/LAT/LON" na hoste -- nachalnyy vid vmesto "vse sloi
 *     celikom" (vozvrat k tomu mestu, gde chelovek ostavil kartu);
 *   <input data-vs-map-view-of="ID"> gde ugodno na stranice -- karta pishet v
 *     nego svoy tekushchiy vid "Z/LAT/LON" posle kazhdogo dvizheniya;
 *   <div class="vs-map-legend"> vnutri hosta -- kartochka karty vstaet PERED
 *     ney, i vo ves ekran legenda razvorachivaetsya vmeste s kartoy.
 *
 * JSON:
 *   base:   [{key, title, url, attribution, maxZoom}]  -- pervaya vklyuchena
 *           kind: 'wms' -- sloy WMS (svezhiy snimok Sentinel-2): parametry v
 *           `wms`, zapros dat snimkov v `dates` (WFS), podpisi v `notes`;
 *           podpis s datoy kladyotsya v element [data-vs-map-note=KEY].
 *   layers: [{kind, title, group, ...}], v poryadke snizu vverh:
 *     outline  {geojson}                     kontur polya, bez zalivki
 *     track    {segments: [[[lat, lon], ...], ...]}
 *     area     {geojson, label, tone: 'primary' | 'success' | 'danger'}
 *   group -- imya sloya v pereklyuchatele (neobyazatelno).
 *   ui:     {fullscreen, exit, fit} -- podpisi knopok "vo ves ekran",
 *           "svernut" i "pokazat vsyo" na yazyke interfeysa (neobyazatelno:
 *           bez podpisey knopok net).
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

  function baseLayer(base) {
    var common = { attribution: base.attribution || '', maxZoom: base.maxZoom || 19 };
    // [REASON]: svezhiy snimok -- 10 m na piksel; plitki melche urovnya 14
    // detaley ne dobavlyayut, a kvotu Copernicus tratyat. Blizhe brauzer sam
    // uvelichivaet plitki urovnya 14 (vs_map.SENTINEL_NATIVE_ZOOM).
    if (base.maxNativeZoom) common.maxNativeZoom = base.maxNativeZoom;
    if (base.kind !== 'wms') return L.tileLayer(base.url, common);
    // [REASON]: vse klyuchi `wms` idut v adres zaprosa (tak ustroen
    // L.TileLayer.WMS), zaglavnymi bukvami -- tak ih zhdyot Sentinel Hub:
    // TIME, MAXCC, PRIORITY, SHOWLOGO.
    return L.tileLayer.wms(base.url, L.Util.extend({ uppercase: true }, common, base.wms));
  }

  function shownDate(iso) {
    var parts = iso.split('-');
    return parts[2] + '.' + parts[1] + '.' + parts[0];
  }

  // Svezhiy snimok: kakaya data realno pokazana. [REASON]: WMS skleivaet
  // mozaiku iz samyh svezhih snimkov okna i datu ne soobshchaet; bez nee
  // "svezhiy snimok" -- obeshchanie, a ne fakt. WFS otdayot spisok snimkov nad
  // uchastkom -- beryotsya samyy svezhiy, sloy prosit rovno ego, i data
  // pishetsya pod kartoy. Ne otvetil WFS -- sloy ostayotsya s oknom dat, a
  // pod kartoy tak i napisano.
  function lookUpDate(base, tiles, bounds, host) {
    var note = document.querySelector('[data-vs-map-note="' + base.key + '"]');
    var say = function (text) {
      if (!note || !text) return;
      note.textContent = text;
      note.hidden = false;
    };
    if (!base.dates || !bounds || !window.fetch) { say(base.notes && base.notes.unknown); return; }
    var sw = L.CRS.EPSG3857.project(bounds.getSouthWest());
    var ne = L.CRS.EPSG3857.project(bounds.getNorthEast());
    var url = base.dates.url + L.Util.getParamString({
      SERVICE: 'WFS', REQUEST: 'GetFeature', VERSION: '2.0.0',
      TYPENAMES: base.dates.typename, OUTPUTFORMAT: 'application/json',
      SRSNAME: 'EPSG:3857', BBOX: [sw.x, sw.y, ne.x, ne.y].join(','),
      TIME: base.dates.time, MAXCC: base.dates.maxcc, MAXFEATURES: 100
    });
    fetch(url, { credentials: 'omit' }).then(function (response) {
      if (!response.ok) throw new Error('WFS ' + response.status);
      return response.json();
    }).then(function (json) {
      var dates = ((json && json.features) || []).map(function (f) {
        return f && f.properties && f.properties.date;
      }).filter(function (d) {
        return typeof d === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(d);
      }).sort();
      if (!dates.length) { say(base.notes && base.notes.none); return; }
      var latest = dates[dates.length - 1];
      tiles.setParams({ time: latest + '/' + latest });
      host.setAttribute('data-vs-map-date-' + base.key, latest);
      say(base.notes && base.notes.found && base.notes.found.replace('{date}', shownDate(latest)));
    }).catch(function () {
      say(base.notes && base.notes.unknown);
    });
  }

  function extend(bounds, layer) {
    var b = layer.getBounds && layer.getBounds();
    if (!b || !b.isValid()) return bounds;
    if (!bounds) return L.latLngBounds(b.getSouthWest(), b.getNorthEast());
    return bounds.extend(b);
  }

  // Vid karty "Z/LAT/LON" -- tot zhe format, chto proveryaet server
  // (gps_routes.parse_map_view). Chuzhoe znachenie -- net vida, a ne oshibka.
  var VIEW = /^(\d{1,2}(?:\.\d{1,2})?)\/(-?\d{1,2}\.\d{1,7})\/(-?\d{1,3}\.\d{1,7})$/;

  function parseView(text) {
    var found = VIEW.exec(text || '');
    if (!found) return null;
    var zoom = parseFloat(found[1]), lat = parseFloat(found[2]), lon = parseFloat(found[3]);
    if (zoom > 22 || Math.abs(lat) > 90 || Math.abs(lon) > 180) return null;
    return { zoom: zoom, center: [lat, lon] };
  }

  function viewText(map) {
    var center = map.getCenter();
    return (Math.round(map.getZoom() * 2) / 2) + '/' + center.lat.toFixed(6) +
      '/' + center.lng.toFixed(6);
  }

  var SVG_NS = 'http://www.w3.org/2000/svg';

  // Piktogrammy knopok -- konturom, cvetom teksta: tak zhe narisovany knopki
  // masshtaba Leaflet ryadom.
  var ICONS = {
    fullscreen: 'M4 9V4h5M15 4h5v5M20 15v5h-5M9 20H4v-5',
    exit: 'M9 4v5H4M20 9h-5V4M15 20v-5h5M4 15h5v5',
    fit: 'M12 3v4M12 17v4M3 12h4M17 12h4M12 8a4 4 0 1 0 0 8a4 4 0 1 0 0-8'
  };

  function icon(name) {
    var svg = document.createElementNS(SVG_NS, 'svg');
    svg.setAttribute('viewBox', '0 0 24 24');
    svg.setAttribute('width', '16');
    svg.setAttribute('height', '16');
    svg.setAttribute('aria-hidden', 'true');
    svg.setAttribute('focusable', 'false');
    var path = document.createElementNS(SVG_NS, 'path');
    path.setAttribute('d', ICONS[name]);
    path.setAttribute('fill', 'none');
    path.setAttribute('stroke', 'currentColor');
    path.setAttribute('stroke-width', '2');
    path.setAttribute('stroke-linecap', 'round');
    path.setAttribute('stroke-linejoin', 'round');
    svg.appendChild(path);
    return svg;
  }

  function toolButton(bar, title, name, onClick) {
    var link = L.DomUtil.create('a', 'vs-map-tool', bar);
    link.href = '#';
    link.setAttribute('role', 'button');
    link.appendChild(icon(name));
    L.DomEvent.on(link, 'click', function (e) {
      L.DomEvent.stop(e);
      onClick();
    });
    setTitle(link, title);
    return link;
  }

  function setTitle(link, title) {
    link.title = title;
    link.setAttribute('aria-label', title);
  }

  // [REASON]: "vo ves ekran" -- Fullscreen API brauzera, a gde ego net (iPhone)
  // ili on otkazal -- klass is-expanded: host lozhitsya poverh stranicy.
  // Vladelec 09.10.2026 prosil kartu "krupnoy, s vozmozhnostyu razvernut na
  // ves ekran"; knopka, kotoraya molcha nichego ne delaet v odnom iz brauzerov,
  // huzhe, chem ee otsutstvie.
  function fullscreenTools(host, map, ui, fit) {
    if (!ui || !ui.fullscreen) return;
    var link = null;
    var isFull = function () {
      return document.fullscreenElement === host || host.classList.contains('is-expanded');
    };
    var refresh = function () {
      var full = isFull();
      link.replaceChild(icon(full ? 'exit' : 'fullscreen'), link.firstChild);
      setTitle(link, full ? (ui.exit || ui.fullscreen) : ui.fullscreen);
      host.setAttribute('data-vs-map-full', full ? '1' : '0');
      map.invalidateSize();
    };
    var expand = function () {
      host.classList.add('is-expanded');
      refresh();
    };
    var toggle = function () {
      if (isFull()) {
        if (document.fullscreenElement === host && document.exitFullscreen) {
          document.exitFullscreen();
        } else {
          host.classList.remove('is-expanded');
          refresh();
        }
        return;
      }
      if (host.requestFullscreen && document.fullscreenEnabled !== false) {
        var asked = host.requestFullscreen();
        if (asked && asked.catch) asked.catch(expand);
      } else {
        expand();
      }
    };
    var Tools = L.Control.extend({
      options: { position: 'topleft' },
      onAdd: function () {
        var bar = L.DomUtil.create('div', 'leaflet-bar vs-map-tools');
        link = toolButton(bar, ui.fullscreen, 'fullscreen', toggle);
        if (ui.fit && fit) toolButton(bar, ui.fit, 'fit', fit);
        L.DomEvent.disableClickPropagation(bar);
        return bar;
      }
    });
    new Tools().addTo(map);
    document.addEventListener('fullscreenchange', refresh);
    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape' && host.classList.contains('is-expanded')) {
        host.classList.remove('is-expanded');
        refresh();
      }
    });
    host.setAttribute('data-vs-map-full', '0');
  }

  // [REASON]: koleso myshi menyaet masshtab srazu, bez shchelchka po karte --
  // vladelec 09.10.2026 ne nashel prezhnego "snachala shchelknite" i prosil
  // koleso. No karta vysotoy v ekran perekhvatyvala by prokrutku stranicy:
  // chelovek listaet vniz, karta podezzhaet pod kursor, i stranica vstaet.
  // Poetomu koleso, prishedshee, poka STRANICA eshche prokruchivaetsya (menshe
  // SCROLL_GUARD_MS posle poslednego sdviga), karte ne otdaetsya i listaet
  // stranicu dalshe. Vo ves ekran listat nechego -- tam koleso vsegda karty.
  var SCROLL_GUARD_MS = 500;
  var lastPageScroll = 0;
  window.addEventListener('scroll', function () { lastPageScroll = Date.now(); },
    { passive: true });

  function guardWheel(host) {
    host.addEventListener('wheel', function (e) {
      if (host.getAttribute('data-vs-map-full') === '1') return;
      if (Date.now() - lastPageScroll < SCROLL_GUARD_MS) e.stopPropagation();
    }, { capture: true, passive: true });
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
    host.insertBefore(canvas, host.querySelector('.vs-map-legend'));
    // Koleso -- srazu; zashchita prokrutki stranicy -- guardWheel() vyshe.
    var map = L.map(canvas, { scrollWheelZoom: true, zoomSnap: 0.5 });
    guardWheel(host);

    var bases = {};
    var dated = [];
    data.base.forEach(function (base, index) {
      var tiles = baseLayer(base);
      bases[escapeHtml(base.title)] = tiles;
      if (index === 0) tiles.addTo(map);
      if (base.kind === 'wms') dated.push({ base: base, tiles: tiles });
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
    var fitAll = function () {
      if (bounds) {
        map.fitBounds(bounds, { padding: [24, 24], maxZoom: 17 });
      } else {
        map.setView([39.77, 64.42], 10);
      }
    };
    var id = host.getAttribute('data-vs-map');
    map.on('moveend', function () {
      var text = viewText(map);
      Array.prototype.forEach.call(document.querySelectorAll('input[data-vs-map-view-of]'),
        function (input) {
          if (input.getAttribute('data-vs-map-view-of') === id) input.value = text;
        });
    });
    var view = parseView(host.getAttribute('data-vs-map-view'));
    if (view) {
      map.setView(view.center, view.zoom);
    } else {
      fitAll();
    }
    fullscreenTools(host, map, data.ui, fitAll);
    dated.forEach(function (entry) {
      lookUpDate(entry.base, entry.tiles, bounds || map.getBounds(), host);
    });
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
