# -*- coding: utf-8 -*-
"""Build a labelling page: every work site drawn, two buttons under each.

WHY THIS EXISTS
Telling work from a transit is, so far, something only a person can do. Eight
measured features were tried on 51 labelled sites and exactly one -- the area
itself -- beats the "call everything work" baseline, and it beats it in a way
that costs money: the errors are large transits billed as work (roadmap
section 2.8). So the module will ask the operator, and the answers become the
training set for the next attempt.

That makes the price of one answer the thing to optimise. The first round cost
the owner an evening: a spreadsheet with time intervals he could not interpret,
then screen recordings, then a Word file typed by hand. This page costs a
glance and a click.

WHAT IT PRODUCES
  gps_label.html   one card per site: the day's track in grey, the site drawn
                   over it, area and time under it, and the buttons
                   "Работа" / "Проезд" / "Не знаю". The answers gather in a
                   box at the bottom -- copy it and send it back.
  gps_label.csv    the same sites with every measured feature, so the answers
                   can be joined to numbers later without recomputing anything.

The page is one self-contained file: no internet, no libraries, no map tiles.
It opens by double click, works offline, and nothing leaves the machine.

Run (PowerShell, one command per line):

  cd C:\\diag\\wialon
  & C:\\gps_venv\\Scripts\\python.exe C:\\transport-report\\tools\\gps_label_sites.py --csv verify2_tracks.csv --wln .

Console output is ASCII.
"""

import argparse
import csv
import glob
import html
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    import numpy as np                                         # noqa: E402
    import shapely                                             # noqa: E402
    from gps.area import return_share, to_utm, work_sites      # noqa: E402
except ImportError as problem:
    # [REASON]: unlike the Wialon probes, this one computes geometry, so it
    # needs both the geo venv AND its place inside the repository -- gps/ is
    # found relative to the script. Copied into C:\diag\wialon it fails with a
    # bare ImportError that says nothing about either condition.
    sys.stderr.write(
        "\nERROR: %s\n\n"
        "Etot skript, v otlichie ot zondov Wialon, schitaet geometriyu.\n"
        "Emu nuzhny DVE veshchi:\n"
        "  1) zapusk IZ rabochey kopii repozitoriya, ne iz C:\\diag\\wialon:\n"
        "     C:\\transport-report\\tools\\gps_label_sites.py\n"
        "  2) otdelnoe okruzhenie s geostekom. Esli ego net, dve stroki:\n"
        '     & "C:\\Program Files\\Python314\\python.exe" -m venv C:\\gps_venv\n'
        "     & C:\\gps_venv\\Scripts\\python.exe -m pip install -r "
        "C:\\transport-report\\gps\\requirements.txt\n\n"
        "Rabochie fayly (csv, html) pri etom mogut lezhat v C:\\diag\\wialon --\n"
        "skript pishet v tekushchuyu papku, a ne ryadom s soboy.\n" % problem)
    sys.exit(2)

TZ = timezone(timedelta(hours=5))
# [REASON]: lower than the engine's 0.3 ha default on purpose. The corpus is
# starved of transits -- 16 of 51 labels, and only 4 of them big enough to
# measure anything on. The borderline pieces between 0.15 and 0.3 ha are
# exactly the ones worth asking about.
MIN_SITE_HA = 0.15
SVG_SIZE = 320
SVG_PAD_M = 30.0


def read_csv_tracks(path):
    """unit -> day -> [(t, lon, lat, speed)] from a probe's tracks csv."""
    out = defaultdict(lambda: defaultdict(list))
    with open(path, encoding="utf-8-sig") as fh:
        for row in csv.DictReader(fh, delimiter=";"):
            try:
                hh, mm, ss = row["time"].split(":")
                midnight = datetime.strptime(row["date"], "%Y-%m-%d") \
                    .replace(tzinfo=TZ).timestamp()
                out[row["unit_id"]][row["date"]].append(
                    (midnight + int(hh) * 3600 + int(mm) * 60 + int(ss),
                     float(row["lon"]), float(row["lat"]), float(row["speed"])))
            except (KeyError, ValueError):
                continue
    return out


def read_wln(path):
    """Wialon message export -> day -> [(t, lon, lat, speed)]."""
    days = defaultdict(list)
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if not line.startswith("REG;"):
                continue
            parts = line.split(";")
            try:
                stamp = int(parts[1])
                lon, lat, speed = float(parts[2]), float(parts[3]), float(parts[4])
            except (IndexError, ValueError):
                continue
            # 0/0 is a message with no fix, not a position in the ocean
            if lon == 0.0 and lat == 0.0:
                continue
            days[datetime.fromtimestamp(stamp, TZ).strftime("%Y-%m-%d")].append(
                (float(stamp), lon, lat, speed))
    return days


def hhmm(stamp):
    return datetime.fromtimestamp(stamp, TZ).strftime("%H:%M")


def ring_to_path(coords, box, scale):
    left, bottom = box[0], box[1]
    steps = ["%s%.1f,%.1f" % ("M" if i == 0 else "L",
                              (x - left) * scale,
                              SVG_SIZE - (y - bottom) * scale)
             for i, (x, y) in enumerate(coords)]
    return " ".join(steps) + " Z"


def draw(site, day_xy, site_xy):
    """One SVG: the day's track in grey, this site over it."""
    minx, miny, maxx, maxy = site.polygon.bounds
    minx, miny, maxx, maxy = (minx - SVG_PAD_M, miny - SVG_PAD_M,
                              maxx + SVG_PAD_M, maxy + SVG_PAD_M)
    span = max(maxx - minx, maxy - miny, 1.0)
    scale = SVG_SIZE / span
    parts = ['<svg viewBox="0 0 %d %d" width="100%%">' % (SVG_SIZE, SVG_SIZE),
             '<rect width="%d" height="%d" fill="#f6f6f3"/>' % (SVG_SIZE, SVG_SIZE)]
    # the whole day, so the site is seen in the context of the machine's route
    trail = ["%.1f,%.1f" % ((x - minx) * scale, SVG_SIZE - (y - miny) * scale)
             for x, y in day_xy
             if minx - span <= x <= maxx + span and miny - span <= y <= maxy + span]
    if len(trail) > 1:
        parts.append('<polyline points="%s" fill="none" stroke="#c9c9c2" '
                     'stroke-width="1"/>' % " ".join(trail))
    for piece in getattr(site.polygon, "geoms", [site.polygon]):
        path = ring_to_path(list(piece.exterior.coords), (minx, miny), scale)
        for hole in piece.interiors:
            path += " " + ring_to_path(list(hole.coords), (minx, miny), scale)
        parts.append('<path d="%s" fill="#2f7d4f" fill-opacity="0.20" '
                     'stroke="#2f7d4f" stroke-width="1.5" fill-rule="evenodd"/>'
                     % path)
    inside = ["%.1f,%.1f" % ((x - minx) * scale, SVG_SIZE - (y - miny) * scale)
              for x, y in site_xy]
    if len(inside) > 1:
        parts.append('<polyline points="%s" fill="none" stroke="#1b4332" '
                     'stroke-width="1.2" stroke-opacity="0.85"/>' % " ".join(inside))
    # scale bar: 100 m, so size is judged from the picture and not guessed
    bar = 100.0 * scale
    if bar < SVG_SIZE * 0.8:
        parts.append('<line x1="10" y1="%d" x2="%.1f" y2="%d" stroke="#333" '
                     'stroke-width="2"/>' % (SVG_SIZE - 12, 10 + bar, SVG_SIZE - 12))
        parts.append('<text x="10" y="%d" font-size="11" fill="#333">100 m</text>'
                     % (SVG_SIZE - 18))
    parts.append("</svg>")
    return "".join(parts)


PAGE_HEAD = """<!doctype html>
<meta charset="utf-8">
<title>Разметка участков — работа или проезд</title>
<style>
 body{font:15px/1.45 system-ui,Segoe UI,Arial;margin:0;background:#fff;color:#1c1c1c}
 header{position:sticky;top:0;background:#fff;border-bottom:1px solid #ddd;
        padding:12px 18px;z-index:5}
 h1{font-size:18px;margin:0 0 4px}
 .hint{color:#555;font-size:13px}
 .grid{display:flex;flex-wrap:wrap;gap:14px;padding:16px}
 .card{border:1px solid #ddd;border-radius:8px;padding:10px;width:340px}
 .card.done{border-color:#2f7d4f;background:#f4faf6}
 .meta{font-size:13px;color:#333;margin:6px 0}
 .meta b{font-size:15px}
 button{font:14px system-ui;padding:7px 12px;border:1px solid #bbb;border-radius:6px;
        background:#fafafa;cursor:pointer;margin-right:6px}
 button.on{background:#2f7d4f;color:#fff;border-color:#2f7d4f}
 #out{width:calc(100% - 36px);height:150px;margin:0 18px 24px;font:13px monospace}
 .count{font-weight:600}
</style>
<header>
 <h1>Работа или проезд</h1>
 <div class="hint">Под каждой картинкой — один вопрос. Зелёным закрашен найденный
  участок, серым — весь маршрут машины за день, тёмной линией — путь внутри
  участка. Ответили: <span class="count" id="done">0</span> из <span id="total"></span>.
  Когда закончите — нажмите «Скопировать ответы» внизу и пришлите текст.</div>
</header>
<div class="grid">
"""

PAGE_TAIL = """</div>
<div style="padding:0 18px 8px"><button id="copyBtn">Скопировать ответы</button>
 <span id="copied" style="color:#2f7d4f"></span></div>
<textarea id="out" readonly></textarea>
<script>
var answers = {};
var cards = Array.prototype.slice.call(document.querySelectorAll('.card'));
cards.forEach(function(card){
  card.querySelectorAll('button').forEach(function(button){
    button.addEventListener('click', function(){
      answers[card.dataset.id] = button.dataset.value;
      card.querySelectorAll('button').forEach(function(b){ b.classList.remove('on'); });
      button.classList.add('on');
      card.classList.add('done');
      document.getElementById('done').textContent = Object.keys(answers).length;
      render();
    });
  });
});
function render(){
  var lines = [];
  cards.forEach(function(card){
    var value = answers[card.dataset.id];
    if (value) { lines.push(card.dataset.id + ';' + value); }
  });
  document.getElementById('out').value = lines.join('\\n');
}
document.getElementById('copyBtn').addEventListener('click', function(){
  var box = document.getElementById('out');
  box.select();
  document.execCommand('copy');
  document.getElementById('copied').textContent = 'скопировано';
});
document.getElementById('total').textContent = cards.length;
</script>
"""


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--csv", action="append", default=[],
                        help="tracks csv from a probe; may repeat")
    parser.add_argument("--wln", action="append", default=[],
                        help="folder with .wln exports; may repeat")
    # [REASON]: a tracks csv from the probes carries the wialon id and nothing
    # else, so cards would be headed "1729" -- a number that tells the person
    # answering nothing at all. gps_fleet_health.csv already maps id to name.
    parser.add_argument("--names", default=None,
                        help="csv with wialon_id;mashina (e.g. gps_fleet_health.csv)")
    parser.add_argument("--min-ha", type=float, default=MIN_SITE_HA)
    parser.add_argument("--out", default="gps_label.html")
    parser.add_argument("--csv-out", default="gps_label.csv")
    args = parser.parse_args()

    names = {}
    if args.names:
        with open(args.names, encoding="utf-8-sig") as fh:
            for row in csv.DictReader(fh, delimiter=";"):
                name = (row.get("mashina") or row.get("name") or "").strip()
                key = (row.get("wialon_id") or row.get("unit_id") or "").strip()
                if key and name:
                    names[key] = name

    days = {}                       # (machine, day) -> track
    for path in args.csv:
        for unit, per_day in read_csv_tracks(path).items():
            for day, points in per_day.items():
                days[(names.get(unit, unit), day)] = sorted(points)
    for folder in args.wln:
        for path in sorted(glob.glob(os.path.join(folder, "*.wln"))):
            machine = os.path.splitext(os.path.basename(path))[0]
            for day, points in read_wln(path).items():
                days[(machine, day)] = sorted(points)
    if not days:
        print("no input: pass --csv and/or --wln")
        return 2

    cards, rows = [], []
    for (machine, day) in sorted(days):
        track = days[(machine, day)]
        if len(track) < 20:
            continue
        sites, quality = work_sites(track, min_area_ha=args.min_ha)
        if not sites:
            continue
        xs, ys = to_utm([r[1] for r in track], [r[2] for r in track])
        day_xy = [(float(x), float(y)) for x, y in zip(xs, ys)]
        for number, site in enumerate(sites, 1):
            prepared = shapely.prepared.prep(site.polygon)
            inside = [(float(x), float(y), row[0], row[3])
                      for row, x, y in zip(track, xs, ys)
                      if 1.0 <= row[3] <= 15.0
                      and prepared.contains(shapely.Point(float(x), float(y)))]
            if not inside:
                continue
            site_xy = [(x, y) for x, y, _, _ in inside]
            stamps = [t for _, _, t, _ in inside]
            seconds = sum(b - a for a, b in zip(stamps, stamps[1:]) if b - a <= 300.0)
            speeds = np.array([s for _, _, _, s in inside])
            # [REASON]: the area is part of the id on purpose. The index
            # alone shifts when --min-ha changes, and answers returned
            # without their csv would then join to the wrong site.
            site_id = "%s|%s|#%d|%.2f га" % (machine, day, number,
                                             site.area_ha)
            rows.append({
                "site_id": site_id,
                "machine": machine, "day": day, "site": number,
                "area_ha": round(site.area_ha, 3),
                "points_inside": len(inside),
                "minutes": round(seconds / 60.0, 1),
                "hours_per_ha": round((seconds / 3600.0) / site.area_ha, 3),
                "speed_median": round(float(np.median(speeds)), 1),
                "pass_spacing_m": (None if site.pass_spacing_m is None
                                   else round(site.pass_spacing_m, 2)),
                "return_share": (lambda v: None if v is None else round(v, 3))(
                    return_share(site_xy)),
                "motion_gaps": quality.motion_gaps,
                "gps_jumps": quality.gps_jumps})
            # [REASON]: the site id goes into a data attribute and the handlers
            # are attached in script -- never interpolated into an inline
            # onclick. A name with an apostrophe in it would otherwise end the
            # JavaScript string early and the button would silently stop
            # working, which is exactly how this project has been bitten before
            # (see CLAUDE.md on tojson in attributes).
            cards.append(
                '<div class="card" data-id="%s"><div>%s</div>%s'
                '<div class="meta"><b>%.2f га</b> &middot; %s&ndash;%s &middot; '
                '%.0f мин &middot; %d точек</div>'
                '<div><button data-value="работа">Работа</button>'
                '<button data-value="проезд">Проезд</button>'
                '<button data-value="не знаю">Не знаю</button>'
                '</div></div>'
                % (html.escape(site_id),
                   html.escape("%s, %s, участок %d" % (machine, day, number)),
                   draw(site, day_xy, site_xy), site.area_ha,
                   hhmm(stamps[0]), hhmm(stamps[-1]), seconds / 60.0, len(inside)))

    if not cards:
        print("no sites above %.2f ha in the input" % args.min_ha)
        return 1

    with open(args.out, "w", encoding="utf-8") as fh:
        fh.write(PAGE_HEAD)
        fh.write("\n".join(cards))
        fh.write(PAGE_TAIL)

    with open(args.csv_out, "w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, delimiter=";", fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    print("machine-days: %d, sites to label: %d" % (len(days), len(rows)))
    print("open %s, answer, copy the box, send the text back" % args.out)
    print("features for later joining: %s" % args.csv_out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
