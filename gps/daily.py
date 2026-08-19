# -*- coding: utf-8 -*-
"""GPS-2 -- one object-day turned into an aggregate and its work polygons.

Input:  points of one object for one local day, from the monthly file GPS-1
        writes (gps_collector/storage.py).
Output: one row in gps_daily_aggregates and one row per work site in
        gps_work_polygons, both in instance/transport.db.

WHY THIS DOES NOT IMPORT app OR models
`app = create_app()` calls `db.create_all()` at import time, which turns any
script that imports it into a writer. The tables are created by
migrate_gps_daily_001.py and written here through stdlib sqlite3. The Flask
application only ever READS these two tables (screen GPS-3).

REFUSING TO PUBLISH IS PART OF THE JOB, NOT AN EXCEPTION
A day whose median recording interval is worse than MAX_INTERVAL_S gets an
aggregate with a reason and no polygons at all. Thinning our own tracks showed
a rarer interval always costs area and never adds it: doubling the interval
took 5.5 percent off the median work and up to 45 percent off small ones
(roadmap 2.10). A quietly understated hectare on an invoice is worse than a
hectare not issued.

Run (PowerShell, one command per line; needs the geo venv, see gps/README.md):

  cd C:\\transport-report
  & C:\\gps_venv\\Scripts\\python.exe -m gps.daily --date 2026-07-27

Console output is ASCII.
"""

import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime, timedelta

import numpy as np
import shapely
from pyproj import Transformer
from shapely.geometry import shape as shapely_shape
from shapely.ops import transform as shapely_transform

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from gps.area import (METHOD_VERSION, SPEED_MAX_KMH, SPEED_MIN_KMH,  # noqa: E402
                      UTM_41N, to_utm, track_quality, work_sites)
# [REASON]: the reader takes the writer's definition of where the points are
# and what shape they are in, instead of keeping a second copy of the path and
# the schema. gps_collector is standard library only, so importing it here
# pulls no dependency into anything; the arrow never points the other way --
# the collector must never import gps.area.
from gps_collector import storage                                   # noqa: E402
from gps_collector.config import TZ, points_dir                     # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(ROOT, 'instance', 'transport.db')

# [REASON]: measured, section 2.10 of the roadmap. 24 percent of the field
# fleet writes a point exactly every 30 s and must pass; 6 percent writes
# rarer than once a minute and must not. The comparison is therefore strictly
# greater -- exactly 30 is the common case, not the bad one.
MAX_INTERVAL_S = 30.0

# The pre-registered work/transit rule, frozen 2026-08-15 (roadmap 2.8.1) and
# kept identical to tools/gps_label_evaluate.py, which is its referee. It is
# stored as a SUGGESTION only: `suggested_label` is what the machine thinks and
# `operator_label` is what a person answered, and the two are never merged.
RULE_AREA_HA = 1.3
RULE_MINUTES = 25.0
WORK, PASSAGE = "работа", "проезд"

# [REASON]: the same 300 s cap the labelling page uses when it sums time on a
# site (tools/gps_label_sites.py). The corpus and the thresholds above were
# built with it; measuring the feature differently here would judge the rule by
# a number it was never fitted on.
SITE_GAP_CAP_S = 300.0

REASON_NO_POINTS = "net_tochek"
REASON_NO_MOTION = "net_dvizheniya"
REASON_RARE = "redkaya_zapis"

# [REASON]: how much two polygons must share before a human answer given about
# one is carried onto the other. Half of EACH area, in both directions: a small
# old site may not lend its answer to a large new one, nor the reverse. This is
# a matching tolerance, not a business rule -- what the answer MEANS is the
# operator's, and an answer that finds no home is reported, never quietly
# dropped.
LABEL_CARRY_MIN_SHARE = 0.5

_TO_WGS84 = Transformer.from_crs(UTM_41N, "EPSG:4326", always_xy=True)


# --- measuring ---------------------------------------------------------------

def median(values):
    """Median, or None for an empty sequence."""
    ordered = sorted(values)
    if not ordered:
        return None
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return float(ordered[middle])
    return (float(ordered[middle - 1]) + float(ordered[middle])) / 2.0


def interval_median_s(points):
    """Median seconds between consecutive messages of the day.

    The same definition tools/wialon_probe6_fleet_health.py measured the fleet
    with, and therefore the one MAX_INTERVAL_S was calibrated against. Parking
    does not dominate it: a working day has thousands of dense points against
    a few dozen half-hourly ones, and a median counts rows, not seconds.
    """
    return median([b[0] - a[0] for a, b in zip(points, points[1:])])


def track_km(xs, ys):
    if len(xs) < 2:
        return 0.0
    pts = np.column_stack([np.asarray(xs, dtype=float),
                           np.asarray(ys, dtype=float)])
    return float(np.linalg.norm(np.diff(pts, axis=0), axis=1).sum()) / 1000.0


def site_minutes(track, xs, ys, polygon):
    """Minutes the machine spent inside this site, and points inside it.

    Definition copied deliberately from tools/gps_label_sites.py, which built
    the labelled corpus: points in the work speed window that fall inside the
    polygon, summing consecutive gaps up to SITE_GAP_CAP_S. Measuring it any
    other way would feed the frozen rule a feature it was not fitted on -- the
    first round of labelling did exactly that, comparing the SPAN from first to
    last point instead of time, and the feature looked useless until it was
    fixed (roadmap 2.8.1).
    """
    prepared = shapely.prepared.prep(polygon)
    inside = [(row[0], float(x), float(y))
              for row, x, y in zip(track, xs, ys)
              if SPEED_MIN_KMH <= row[3] <= SPEED_MAX_KMH
              and prepared.contains(shapely.Point(float(x), float(y)))]
    stamps = [t for t, _, _ in inside]
    seconds = sum(b - a for a, b in zip(stamps, stamps[1:])
                  if b - a <= SITE_GAP_CAP_S)
    return seconds / 60.0, len(inside)


def suggested_label(area_ha, minutes):
    """The pre-registered rule, as a suggestion for the operator to overrule."""
    return WORK if (area_ha >= RULE_AREA_HA or minutes >= RULE_MINUTES) else PASSAGE


def quality_flag(quality):
    """Measured facts about the day's track, not a verdict about it.

    [REASON]: only presence and counts, never a threshold. Whether a day is too
    broken to bill is question V-3 and belongs to the owner; the engine's
    README says so in as many words. What is recorded here is what happened.
    """
    marks = []
    if quality.motion_gaps:
        marks.append("razryvy=%d" % quality.motion_gaps)
    if quality.gps_jumps:
        marks.append("pryzhki=%d" % quality.gps_jumps)
    return ";".join(marks) or None


def polygon_geojson(polygon, ndigits=6):
    """UTM polygon -> GeoJSON text in WGS84, the way field_contours stores it.

    Six decimals is about 0.1 m at this latitude -- far finer than the tracker,
    and it keeps a day's polygons to kilobytes rather than tens of them.
    """
    def to_degrees(x, y):
        lon, lat = _TO_WGS84.transform(x, y)
        return np.round(lon, ndigits), np.round(lat, ndigits)

    return json.dumps(shapely.geometry.mapping(
        shapely_transform(to_degrees, polygon)), ensure_ascii=False)


# --- one day -----------------------------------------------------------------

class DayResult:
    """What one object-day came to. `sites` is empty whenever `reason` is set."""

    def __init__(self, aggregate, sites):
        self.aggregate = aggregate
        self.sites = sites

    @property
    def reason(self):
        return self.aggregate["reason"]


def compute_day(points, contours=None):
    """points: [(t, lon, lat, speed, sats)] of one object, one local day."""
    aggregate = {"points_total": len(points), "points_work": 0, "track_km": 0.0,
                 "interval_median_s": None, "sats_median": None,
                 "motion_gaps": 0, "lost_seconds": 0.0, "gps_jumps": 0,
                 "reason": None, "method_version": METHOD_VERSION}
    if not points:
        aggregate["reason"] = REASON_NO_POINTS
        return DayResult(aggregate, [])

    points = sorted(points)
    speeds = [row[3] for row in points]
    xs, ys = to_utm([row[1] for row in points], [row[2] for row in points])
    quality = track_quality([row[0] for row in points], speeds,
                            points_xy=list(zip(xs, ys)))
    aggregate.update({
        "points_work": int(sum(1 for s in speeds
                               if SPEED_MIN_KMH <= s <= SPEED_MAX_KMH)),
        "track_km": round(track_km(xs, ys), 3),
        "interval_median_s": interval_median_s(points),
        "sats_median": median([row[4] for row in points if row[4] is not None
                               and row[4] >= 0]),
        "motion_gaps": quality.motion_gaps,
        "lost_seconds": round(quality.lost_seconds, 1),
        "gps_jumps": quality.gps_jumps})

    if aggregate["points_work"] == 0:
        # "We looked and the machine did not move" -- a fact worth a row.
        aggregate["reason"] = REASON_NO_MOTION
        return DayResult(aggregate, [])
    if aggregate["interval_median_s"] > MAX_INTERVAL_S:
        aggregate["reason"] = REASON_RARE
        return DayResult(aggregate, [])

    sites, _ = work_sites(points, contours=contours)
    flag = quality_flag(quality)
    rows = []
    for number, site in enumerate(sites, 1):
        minutes, inside = site_minutes(points, xs, ys, site.polygon)
        rows.append({
            "site_number": number,
            "area_ha": round(site.area_ha, 4),
            "minutes": round(minutes, 1),
            "polygon_geojson": polygon_geojson(site.polygon),
            "contour_id": site.contour_id,
            "alpha_used_m": round(site.alpha_used_m, 3),
            "pass_spacing_m": (None if site.pass_spacing_m is None
                               else round(site.pass_spacing_m, 3)),
            "quality_flag": flag,
            "suggested_label": suggested_label(site.area_ha, minutes),
            "points_inside": inside})
    return DayResult(aggregate, rows)


# --- storing -----------------------------------------------------------------

def load_contours(con):
    """contour_id -> UTM polygon, from the shared field_contours directory.

    Empty until the Wialon directory is mirrored (step 5 of the track), and an
    empty answer is fine: a site with no contour is real work on unregistered
    ground, which is the normal case and never a zero.
    """
    contours = {}
    try:
        rows = con.execute(
            "SELECT id, geometry_geojson FROM field_contours "
            "WHERE geometry_geojson IS NOT NULL AND geometry_geojson != '' "
            "AND (is_active IS NULL OR is_active = 1)").fetchall()
    except sqlite3.OperationalError:
        return contours
    for contour_id, geometry in rows:
        try:
            polygon = shapely_shape(json.loads(geometry))
        except (ValueError, TypeError, AttributeError):
            continue
        if polygon.is_empty:
            continue
        xs, ys = to_utm(*polygon.exterior.coords.xy) if polygon.geom_type == "Polygon" \
            else (None, None)
        if xs is None:
            continue
        contours[contour_id] = shapely.Polygon(zip(xs, ys))
    return contours


def _utm_polygon_from_geojson(text):
    try:
        polygon = shapely_shape(json.loads(text))
    except (ValueError, TypeError, AttributeError):
        return None
    if polygon.is_empty or polygon.geom_type != "Polygon":
        return None
    xs, ys = to_utm(*polygon.exterior.coords.xy)
    return shapely.Polygon(zip(xs, ys))


def _carry_labels(existing, sites):
    """Map new site_number -> (operator_label, decided_at) from the old rows.

    Answers are matched by GROUND, not by row number: site numbers shift the
    moment the points change, and an answer moved to the wrong patch is worse
    than an answer lost, because nothing would ever say so. Each old answer is
    carried at most once, to its best-overlapping new site.
    """
    carried, taken = {}, set()
    for row in existing:
        old = _utm_polygon_from_geojson(row["polygon_geojson"])
        if old is None or old.area <= 0:
            continue
        best, best_number = 0.0, None
        for site in sites:
            if site["site_number"] in taken:
                continue
            new = _utm_polygon_from_geojson(site["polygon_geojson"])
            if new is None or new.area <= 0:
                continue
            overlap = old.intersection(new).area
            share = min(overlap / old.area, overlap / new.area)
            if share >= LABEL_CARRY_MIN_SHARE and share > best:
                best, best_number = share, site["site_number"]
        if best_number is not None:
            taken.add(best_number)
            carried[best_number] = (row["operator_label"], row["decided_at"])
    return carried


def write_day(con, day, unit_id, result, computed_at=None):
    """Replace the day's rows in one transaction. Returns (carried, dropped).

    A recomputation REPLACES: the aggregate is upserted and the polygons are
    deleted and re-inserted, so the same day computed five times is one row and
    five sites, not five rows and twenty-five sites. The operator's answers are
    carried across by overlap; any that find no home are counted and reported
    by the caller rather than disappearing.
    """
    computed_at = computed_at or datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S")
    con.execute("BEGIN")
    try:
        existing = [
            {"polygon_geojson": row[0], "operator_label": row[1],
             "decided_at": row[2]}
            for row in con.execute(
                "SELECT polygon_geojson, operator_label, decided_at "
                "FROM gps_work_polygons WHERE work_date = ? AND wialon_id = ? "
                "AND operator_label IS NOT NULL", (day, int(unit_id)))]
        carried = _carry_labels(existing, result.sites) if existing else {}

        con.execute("DELETE FROM gps_work_polygons "
                    "WHERE work_date = ? AND wialon_id = ?", (day, int(unit_id)))
        for site in result.sites:
            label, decided = carried.get(site["site_number"], (None, None))
            con.execute(
                "INSERT INTO gps_work_polygons (work_date, wialon_id, "
                "site_number, area_ha, minutes, polygon_geojson, contour_id, "
                "alpha_used_m, pass_spacing_m, quality_flag, suggested_label, "
                "operator_label, decided_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (day, int(unit_id), site["site_number"], site["area_ha"],
                 site["minutes"], site["polygon_geojson"], site["contour_id"],
                 site["alpha_used_m"], site["pass_spacing_m"],
                 site["quality_flag"], site["suggested_label"], label, decided))

        aggregate = result.aggregate
        con.execute(
            "INSERT INTO gps_daily_aggregates (work_date, wialon_id, "
            "points_total, points_work, track_km, interval_median_s, "
            "sats_median, motion_gaps, lost_seconds, gps_jumps, reason, "
            "method_version, computed_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(work_date, wialon_id) DO UPDATE SET "
            "points_total = excluded.points_total, "
            "points_work = excluded.points_work, "
            "track_km = excluded.track_km, "
            "interval_median_s = excluded.interval_median_s, "
            "sats_median = excluded.sats_median, "
            "motion_gaps = excluded.motion_gaps, "
            "lost_seconds = excluded.lost_seconds, "
            "gps_jumps = excluded.gps_jumps, reason = excluded.reason, "
            "method_version = excluded.method_version, "
            "computed_at = excluded.computed_at",
            (day, int(unit_id), aggregate["points_total"],
             aggregate["points_work"], aggregate["track_km"],
             aggregate["interval_median_s"], aggregate["sats_median"],
             aggregate["motion_gaps"], aggregate["lost_seconds"],
             aggregate["gps_jumps"], aggregate["reason"],
             aggregate["method_version"], computed_at))
        con.commit()
    except Exception:
        con.rollback()
        raise
    return len(carried), len(existing) - len(carried)


def run_day(day, unit_id, folder=None, db_path=None, contours=None, log=print):
    """Compute and store one object-day. Returns the DayResult."""
    folder = folder or points_dir()
    db_path = db_path or DB_PATH
    points = storage.read_day(folder, unit_id, day)
    con = sqlite3.connect(db_path, timeout=30)
    try:
        if contours is None:
            contours = load_contours(con)
        result = compute_day(points, contours=contours or None)
        carried, dropped = write_day(con, day, unit_id, result)
    finally:
        con.close()
    if dropped:
        # [REASON]: an operator's answer is hand-entered data and the training
        # set for the work/transit rule. Losing one silently would shrink the
        # corpus invisibly, which is exactly how a set stops being trustworthy.
        log("    VNIMANIE: otvetov operatora poteryano pri pereschete: %d"
            % dropped)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--date", default=None,
                        help="local day, YYYY-MM-DD. По умолчанию -- вчерашние "
                             "сутки по местному времени")
    parser.add_argument("--unit", action="append", default=[], type=int,
                        help="wialon id; may repeat. Default: every object "
                             "with points that day")
    parser.add_argument("--dir", default=None,
                        help="where the point files live (default: instance/)")
    parser.add_argument("--db", default=None, help="path to transport.db")
    args = parser.parse_args(argv)

    # [REASON]: «вчера» считается здесь, а не в .bat-обёртке. В командном
    # файле Windows это `for /f` вокруг PowerShell со вложенными кавычками --
    # ровно та конструкция, где кавычка съедает половину команды и задача
    # молча не запускается по расписанию. День у ночного расчёта всегда один
    # и тот же: вчерашние сутки по местному времени.
    day = args.date or (datetime.now(TZ) - timedelta(days=1)).strftime("%Y-%m-%d")
    try:
        datetime.strptime(day, "%Y-%m-%d")
    except ValueError:
        sys.stderr.write("ERROR: --date must be YYYY-MM-DD, got %r\n" % day)
        return 2

    folder = args.dir or points_dir()
    db_path = args.db or DB_PATH
    if not os.path.exists(db_path):
        sys.stderr.write("ERROR: database not found at %s\n" % db_path)
        return 2

    units = args.unit or storage.units_with_points(folder, day)
    if not units:
        print("no points for %s in %s" % (day, folder))
        return 0

    con = sqlite3.connect(db_path, timeout=30)
    try:
        contours = load_contours(con)
    finally:
        con.close()
    print("%s: %d object(s), %d contour(s) in the directory"
          % (day, len(units), len(contours)))

    published = refused = sites_total = 0
    for unit_id in units:
        result = run_day(day, unit_id, folder=folder, db_path=db_path,
                         contours=contours)
        if result.reason:
            refused += 1
            print("  %-8s %s" % (unit_id, result.reason))
        else:
            published += 1
            sites_total += len(result.sites)
            print("  %-8s %d site(s), %.2f ha"
                  % (unit_id, len(result.sites),
                     sum(s["area_ha"] for s in result.sites)))
    print("\npublished: %d | not computed: %d | sites: %d"
          % (published, refused, sites_total))
    return 0


if __name__ == "__main__":
    sys.exit(main())
