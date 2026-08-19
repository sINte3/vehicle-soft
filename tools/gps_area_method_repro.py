"""GPS-2 frozen area method: independent reproduction and self-check.

Reimplements, from its written specification alone, the frozen area pipeline
of the GPS plan-fact track (docs/PLAN.md phase 2, GPS-2; docs/tracks/
gps-plan-fakt.md section 2):

    points of the interval
      -> filter: speed 1..15 km/h AND inside the contour polygon
      -> densify the path every 5 m (only segments shorter than 150 m)
      -> alpha shape: Delaunay triangulation, keep triangles whose
         circumradius < alpha, alpha = 10 m
      -> union of kept triangles
      -> clip by the contour polygon
      -> area in UTM 41N (EPSG:32641)

The contour is identified BY THE TRACK (which zone polygons contain moving
points), never by name: the zone directory contains duplicate names by
construction ("1508 ..." exists as zones 3207 and 3208).

Purpose. The method was fixed on 2026-07-29 from calibration work done in a
research chat. This script is the proof that the written spec is complete:
run against the same raw inputs it must land on the numbers recorded in the
track file. It is also the tool for the pending spraying calibration: when
the owner collects the next 10-12 raw manual measurements, compute the
frozen-method figures FIRST with this script, then open the measurements
(pre-registration protocol, docs/PLAN.md section 5).

Expected values recorded in the track file / handoff (verification set of
2026-07-27, 7 tractors, 17 440 points):
    zone 10204 polygon area          42.30 ha   (passport 42.16)
    work on zone 3208, unit 3464      8.51 ha   (939 moving points inside)
    sum over the 17 selected works   51.71 ha   (informational: the exact
        row selection lives in the owner's workbook, not in the raw data)

Two modes:
    default        frozen-method calculator: identify zones by track, compute
                   every (unit, zone) work, write the tables, exit 0. Use
                   this for FUTURE pre-registered sets (any tracks csv).
    --self-check   additionally assert the recorded 2026-07-27 figures; only
                   meaningful against that day's verify_tracks.csv:
        C1  zone 10204 polygon area within 0.05 ha of 42.30
        C2  zone 3208 work area within 0.05 ha of 8.51 and 939 points inside
        C3  negative control: convex hull total exceeds alpha total by
            > 1.25x. Proves the comparison can tell methods apart, and
            catches an over-inflated alpha (alpha 60 m drags the ratio down
            to 1.22 because the alpha shape degenerates toward the hull)
        C4  alpha stability: areas at alpha 8 and 15 m within 2 percent of
            the alpha 10 m figure on the largest work
    Negative controls verified on 2026-08-12: densification disabled ->
    C2 4.56 ha vs 8.51 (FAIL) and C4 0.256 (FAIL); alpha 60 m -> C3 1.222
    (FAIL). A check that cannot fail proves nothing.

Inputs (NOT in the repository; the owner keeps them in C:\\diag\\wialon):
    verify_tracks.csv   tracks of the verification day (wialon_probe4.py)
    wialon_zones.json   17 812 zones with geometry (wialon_probe2.py)
    --tracks NAME       use another tracks csv (same columns) for new sets

Dependencies: shapely, scipy, pyproj, numpy. These are deliberately NOT in
the application requirements.txt -- this is a diagnostic script run outside
the server, like the drone_collector it keeps its own environment. On the
owner's machine (PowerShell, one line at a time):

    cd C:\diag\wialon
    & "C:\Program Files\Python314\python.exe" -m venv venv
    & C:\diag\wialon\venv\Scripts\python.exe -m pip install shapely scipy pyproj numpy
    & C:\diag\wialon\venv\Scripts\python.exe C:\transport-report\tools\gps_area_method_repro.py --data-dir C:\diag\wialon --self-check

Console output is ASCII only; the full table with Cyrillic zone names goes
to gps_area_repro_report.txt (UTF-8) and gps_area_repro_works.csv next to
the data.
"""

import argparse
import csv
import json
import math
import sys
from collections import defaultdict

import numpy as np
from pyproj import Transformer
from scipy.spatial import Delaunay
from shapely import make_valid
from shapely.geometry import MultiPoint, Point, Polygon
from shapely.ops import unary_union
from shapely.prepared import prep

# [REASON]: frozen method parameters, fixed 2026-07-29 (docs/PLAN.md GPS-2).
# They are constants on purpose: changing any of them invalidates the
# calibration and requires a new pre-registered verification set. The
# adaptive alpha rule max(10; 1.2 x pass spacing) is intentionally absent --
# it was tuned on the same data it improved and is not part of the method.
ALPHA_M = 10.0
DENSIFY_STEP_M = 5.0
DENSIFY_MAX_SEG_M = 150.0
SPEED_MIN_KMH, SPEED_MAX_KMH = 1.0, 15.0

# [REASON]: zone-identification threshold only. Pairs below it are not
# reported at all; pairs above it may still be mere passage (0.0-0.1 ha).
# The work-vs-passage boundary is a BUSINESS rule the owner has not set yet
# (open question in the track file) -- do not encode one here.
MIN_MOVING_PTS_IN_ZONE = 10

UTM = Transformer.from_crs("EPSG:4326", "EPSG:32641", always_xy=True)

EXPECT_ZONE_10204_HA = 42.30
EXPECT_ZONE_3208_HA = 8.51
EXPECT_ZONE_3208_PTS = 939
RECORDED_VERIFY_TOTAL_HA = 51.71  # informational, see docstring


def load_tracks(path):
    """unit_id -> [(t_seconds, x_utm, y_utm, speed_kmh)] sorted by time."""
    per_unit = defaultdict(list)
    with open(path, encoding="utf-8-sig") as f:
        for row in csv.DictReader(f, delimiter=";"):
            hh, mm, ss = row["time"].split(":")
            t = int(hh) * 3600 + int(mm) * 60 + int(ss)
            x, y = UTM.transform(float(row["lon"]), float(row["lat"]))
            per_unit[int(row["unit_id"])].append((t, x, y, float(row["speed"])))
    for unit in per_unit:
        per_unit[unit].sort(key=lambda r: r[0])
    return dict(per_unit)


def load_zones(path):
    """zone_id -> (name, valid shapely Polygon in UTM); polygons only."""
    zones = {}
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    for zid, z in raw.items():
        if z.get("type") != 2 or len(z.get("points", [])) < 3:
            continue
        xs, ys = UTM.transform([p["x"] for p in z["points"]],
                               [p["y"] for p in z["points"]])
        poly = Polygon(zip(xs, ys))
        if not poly.is_valid:
            poly = make_valid(poly)
        if poly.is_empty:
            continue
        zones[int(zid)] = (z["name"], poly)
    return zones


def densify(points_xy):
    """Insert points every 5 m along segments shorter than 150 m.

    [REASON]: point spacing along a pass (35-55 m) exceeds the spacing
    between passes (5-14 m); without densification no single alpha can
    stitch neighbouring passes without also bridging real gaps. Segments
    of 150 m and longer are track gaps and must NOT be bridged.
    """
    out = []
    for i, (x, y) in enumerate(points_xy):
        out.append((x, y))
        if i + 1 >= len(points_xy):
            break
        x2, y2 = points_xy[i + 1]
        d = math.hypot(x2 - x, y2 - y)
        if 0 < d < DENSIFY_MAX_SEG_M:
            n = int(d // DENSIFY_STEP_M)
            for k in range(1, n + 1):
                f = k * DENSIFY_STEP_M / d
                if f < 1.0:
                    out.append((x + f * (x2 - x), y + f * (y2 - y)))
    return out


def alpha_shape(points_xy, alpha_m):
    """Union of Delaunay triangles with circumradius < alpha_m."""
    pts = np.asarray(points_xy)
    if len(pts) < 4:
        return None
    tri = Delaunay(pts)
    a = pts[tri.simplices[:, 0]]
    b = pts[tri.simplices[:, 1]]
    c = pts[tri.simplices[:, 2]]
    la = np.linalg.norm(b - c, axis=1)
    lb = np.linalg.norm(a - c, axis=1)
    lc = np.linalg.norm(a - b, axis=1)
    s = (la + lb + lc) / 2.0
    area = np.sqrt(np.maximum(s * (s - la) * (s - lb) * (s - lc), 1e-12))
    circum_r = la * lb * lc / (4.0 * area)
    keep = tri.simplices[circum_r < alpha_m]
    if len(keep) == 0:
        return None
    return unary_union([Polygon(pts[t]) for t in keep])


def frozen_area_ha(seq_xy, zone_poly):
    """The frozen method for one (unit, zone) work; returns hectares."""
    dense = densify(seq_xy)
    shape = alpha_shape(dense, ALPHA_M)
    if shape is None:
        return 0.0
    return shape.intersection(zone_poly).area / 10000.0


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--data-dir", default=".",
                    help="directory with the tracks csv and wialon_zones.json")
    ap.add_argument("--tracks", default="verify_tracks.csv",
                    help="tracks csv file name inside --data-dir")
    ap.add_argument("--self-check", action="store_true",
                    help="assert the recorded 2026-07-27 verification "
                         "figures (only valid against that day's tracks)")
    args = ap.parse_args()

    tracks = load_tracks(f"{args.data_dir}/{args.tracks}")
    zones = load_zones(f"{args.data_dir}/wialon_zones.json")
    n_pts = sum(len(v) for v in tracks.values())
    print(f"loaded: {n_pts} track points, {len(tracks)} units, "
          f"{len(zones)} zone polygons")

    checks = []

    # C1: projection control against the recorded passport cross-check
    area_10204 = zones[10204][1].area / 10000.0
    ok = abs(area_10204 - EXPECT_ZONE_10204_HA) <= 0.05
    checks.append(("C1 zone 10204 polygon", area_10204,
                   EXPECT_ZONE_10204_HA, ok))

    # zone identification by track + frozen method on every candidate pair
    works = []  # (unit, zone_id, zone_name, pts_inside, alpha_ha, hull_ha)
    for unit, rows in sorted(tracks.items()):
        moving = [(t, x, y) for (t, x, y, sp) in rows
                  if SPEED_MIN_KMH <= sp <= SPEED_MAX_KMH]
        if not moving:
            continue
        xs = [x for _, x, _ in moving]
        ys = [y for _, _, y in moving]
        bbox = (min(xs) - 50, min(ys) - 50, max(xs) + 50, max(ys) + 50)
        for zid, (zname, zpoly) in zones.items():
            zb = zpoly.bounds
            if (zb[0] > bbox[2] or zb[2] < bbox[0]
                    or zb[1] > bbox[3] or zb[3] < bbox[1]):
                continue
            pz = prep(zpoly)
            inside = [(x, y) for (_, x, y) in moving if pz.contains(Point(x, y))]
            if len(inside) < MIN_MOVING_PTS_IN_ZONE:
                continue
            alpha_ha = frozen_area_ha(inside, zpoly)
            hull_ha = (MultiPoint(inside).convex_hull
                       .intersection(zpoly).area / 10000.0)
            works.append((unit, zid, zname, len(inside), alpha_ha, hull_ha))
    works.sort(key=lambda w: -w[4])

    # C2: the recorded single-work figure (duplicate-name zone, correct twin)
    w3208 = [w for w in works if w[1] == 3208]
    got_ha = w3208[0][4] if w3208 else -1.0
    got_pts = w3208[0][3] if w3208 else 0
    ok = (abs(got_ha - EXPECT_ZONE_3208_HA) <= 0.05
          and got_pts == EXPECT_ZONE_3208_PTS)
    checks.append(("C2 zone 3208 work", got_ha, EXPECT_ZONE_3208_HA, ok))

    # C3: negative control -- convex hull must NOT reproduce the figures.
    # [REASON]: threshold 1.25 is calibrated on the 27.07 set: the frozen
    # method gives 1.327, an alpha inflated to 60 m gives 1.222 (the alpha
    # shape degenerates toward the hull). Below 1.25 the pipeline is either
    # oversewn or the control lost its discriminating power.
    sig = [w for w in works if w[4] >= 0.3]
    total_alpha = sum(w[4] for w in sig)
    total_hull = sum(w[5] for w in sig)
    inflation = total_hull / total_alpha if total_alpha else 0.0
    ok = inflation > 1.25
    checks.append(("C3 hull inflation x", inflation, 1.25, ok))

    # C4: alpha stability window 8..15 m on the largest work
    unit, zid, _, _, a10, _ = works[0]
    zpoly = zones[zid][1]
    pz = prep(zpoly)
    moving = [(x, y) for (t, x, y, sp) in tracks[unit]
              if SPEED_MIN_KMH <= sp <= SPEED_MAX_KMH
              and pz.contains(Point(x, y))]
    dense = densify(moving)
    devs = []
    for alt in (8.0, 15.0):
        sh = alpha_shape(dense, alt)
        aa = sh.intersection(zpoly).area / 10000.0 if sh else 0.0
        devs.append(abs(aa - a10) / a10 if a10 else 1.0)
    ok = max(devs) <= 0.02
    checks.append(("C4 alpha 8/15 max dev", max(devs), 0.02, ok))

    # report files (UTF-8, Cyrillic names allowed there, not on the console)
    rep_path = f"{args.data_dir}/gps_area_repro_report.txt"
    csv_path = f"{args.data_dir}/gps_area_repro_works.csv"
    with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
        wr = csv.writer(f, delimiter=";")
        wr.writerow(["unit_id", "zone_id", "zone_name", "moving_pts_inside",
                     "alpha_area_ha", "convex_hull_ha"])
        for unit, zid, zname, n, a, h in works:
            wr.writerow([unit, zid, zname, n, f"{a:.3f}", f"{h:.3f}"])
    with open(rep_path, "w", encoding="utf-8") as f:
        f.write("GPS area method reproduction -- frozen parameters: "
                f"speed {SPEED_MIN_KMH}-{SPEED_MAX_KMH} km/h, densify "
                f"{DENSIFY_STEP_M} m (<{DENSIFY_MAX_SEG_M} m), alpha "
                f"{ALPHA_M} m, UTM 41N\n\n")
        f.write(f"{'unit':>6} {'zone':>6} {'pts':>5} {'alpha_ha':>9} "
                f"{'hull_ha':>9}  name\n")
        for unit, zid, zname, n, a, h in works:
            f.write(f"{unit:>6} {zid:>6} {n:>5} {a:>9.3f} {h:>9.3f}  {zname}\n")
        f.write(f"\nsum of works with alpha area >= 0.3 ha: "
                f"{total_alpha:.2f} ha over {len(sig)} pairs\n")
        f.write(f"recorded verification total (17 owner-selected rows): "
                f"{RECORDED_VERIFY_TOTAL_HA} ha\n")
        f.write("extra pairs here are works the owner did not measure -- "
                "candidate unbilled work, not an error\n")

    print(f"works detected (>= {MIN_MOVING_PTS_IN_ZONE} moving pts inside): "
          f"{len(works)}; with alpha area >= 0.3 ha: {len(sig)}")
    print(f"sum alpha >= 0.3 ha: {total_alpha:.2f} ha "
          f"(recorded 17-row total: {RECORDED_VERIFY_TOTAL_HA})")
    print(f"details: {rep_path}")
    print(f"         {csv_path}")

    if not args.self_check:
        print("RESULT: computed (no self-check; pass --self-check against "
              "the 2026-07-27 set)")
        return 0

    failed = 0
    for name, got, want, ok in checks:
        print(f"{name:<26} got {got:>7.3f}  ref {want:>6.2f}  "
              f"{'PASS' if ok else 'FAIL'}")
        failed += (not ok)
    print("RESULT:", "ALL CHECKS PASS" if not failed else f"{failed} FAILED")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
