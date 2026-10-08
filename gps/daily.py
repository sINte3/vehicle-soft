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

A DAY THE COLLECTOR HAS NOT FINISHED IS NOT A DAY (GPS-11)
The collector's watermark moves only after the points are committed, and a
truncated answer leaves it on the last message stored. So a watermark before
the end of the day is proof that the day's tail has not been fetched yet. The
first live run of 07.09.2026 hit exactly that: 104 objects truncated at 50 000
messages over an 18-day backlog, and the day was computed on partial points
and published as a fact. Now such a day gets the reason `sbor_nepolnyy`, its
earlier polygons and the operator's answers on them stay untouched, and
`--catch-up` recomputes it once the watermark has passed.

SPECIAL MACHINERY: THE TRACK WITHOUT THE HECTARES (A1, 28.09.2026)
A machine in a category of gps.exclusion.TRACK_ONLY_CATEGORIES -- loaders,
excavators -- gets its day measured exactly like any other (points, km,
interval, satellites, gaps, jumps) and the reason `spetstekhnika` instead of
sites. Polygons an earlier computation left for that day are NOT touched, for
the same reason as under sbor_nepolnyy: an operator may have answered on them,
and the category is a decision a person can reverse. `--catch-up` brings the
window in line with the category in both directions.

Run (PowerShell, one command per line; needs the geo venv, see gps/README.md):

  cd C:\\transport-report
  & C:\\gps_venv\\Scripts\\python.exe -m gps.daily --date 2026-07-27
  & C:\\gps_venv\\Scripts\\python.exe -m gps.daily --catch-up

Console output is ASCII.

КОДЫ ВОЗВРАТА. 0 -- посчитано всё, что можно было посчитать; 2 -- отказ на
разборе аргументов, не сделано ничего; 5 -- часть суток не посчиталась из-за
сбоя. При 5 прогон ДОШЁЛ до конца и посчитал всё остальное: сбойные сутки
перечислены в конце вывода поимённо, строк в базу по ним не записано, и
следующий `--catch-up` возьмёт их снова.
"""

import argparse
import json
import os
import sqlite3
import sys
from collections import Counter
from datetime import datetime, timedelta

import numpy as np
import shapely
from pyproj import Transformer
from shapely.geometry import shape as shapely_shape
from shapely.ops import transform as shapely_transform

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from gps.area import (METHOD_VERSION, SPEED_MAX_KMH, SPEED_MIN_KMH,  # noqa: E402
                      UTM_41N, method_version, repair_polygon, to_utm,
                      track_quality, work_sites)
from gps.exclusion import (REASON_TRACK_ONLY, excluded_units,       # noqa: E402
                           track_only_units)
# [REASON]: the reader takes the writer's definition of where the points are
# and what shape they are in, instead of keeping a second copy of the path and
# the schema. gps_collector is standard library only, so importing it here
# pulls no dependency into anything; the arrow never points the other way --
# the collector must never import gps.area.
from gps_collector import storage                                   # noqa: E402
from gps_collector.config import TZ, ascii_only, points_dir         # noqa: E402

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
REASON_INCOMPLETE = "sbor_nepolnyy"
# REASON_TRACK_ONLY ("spetstekhnika") is imported from gps.exclusion above:
# the Flask screen needs the same word and cannot import this module.

# [REASON]: how much two polygons must share before a human answer given about
# one is carried onto the other. Half of EACH area, in both directions: a small
# old site may not lend its answer to a large new one, nor the reverse. This is
# a matching tolerance, not a business rule -- what the answer MEANS is the
# operator's, and an answer that finds no home is reported, never quietly
# dropped.
LABEL_CARRY_MIN_SHARE = 0.5

# The words of the line run_day prints when a recomputation dropped answers.
# tools/gps_recompute_days.py looks for them, so they are defined once, here.
DROPPED_MARK = "otvetov operatora poteryano pri pereschete"


class AnswersWouldBeLost(Exception):
    """write_day(keep_answers=True) refused: an answer would find no new site."""


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


def compute_day(points, contours=None, track_only=False, overflow_cap=True):
    """points: [(t, lon, lat, speed, sats)] of one object, one local day.

    `track_only` -- the machine's category takes no hectares (special
    machinery): every measurement of the track is made, no site is sought.
    `overflow_cap` -- the A7 spacing rule (`gps.area.pass_spacing_on_overflow`),
    the method since 2026-10-07. False reproduces the previous method, and
    the row then says so: its `method_version` is PREVIOUS_METHOD_VERSION.
    """
    aggregate = {"points_total": len(points), "points_work": 0, "track_km": 0.0,
                 "interval_median_s": None, "sats_median": None,
                 "motion_gaps": 0, "lost_seconds": 0.0, "gps_jumps": 0,
                 "reason": None, "method_version": method_version(overflow_cap)}
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

    if track_only:
        # [REASON]: the category decides before the measurements do. A loader
        # that stood all day and one that drove all day show the same reason,
        # because neither would have had hectares -- "no motion" or "rare
        # recording" next to special machinery would suggest that more motion
        # or a denser tracker would have produced some. The measurements above
        # are complete either way: they are the track the owner asked to see.
        aggregate["reason"] = REASON_TRACK_ONLY
        return DayResult(aggregate, [])
    if aggregate["points_work"] == 0:
        # "We looked and the machine did not move" -- a fact worth a row.
        aggregate["reason"] = REASON_NO_MOTION
        return DayResult(aggregate, [])
    median_s = aggregate["interval_median_s"]
    # [REASON]: a day holding ONE message has no interval at all, so the
    # median is None -- and `None > 30.0` is a TypeError, which is how
    # --catch-up died on 27.09.2026 over the objects whose history starts
    # one day back. None is not a measurement waiting to be filled in: it
    # means the interval cannot be measured, which is strictly worse than
    # any finite one, and an area from a single point is not a number.
    # gps.area.track_quality spells the same len < 2 case out; here it was
    # left to the comparison.
    if median_s is None or median_s > MAX_INTERVAL_S:
        aggregate["reason"] = REASON_RARE
        return DayResult(aggregate, [])

    sites, _ = work_sites(points, contours=contours, overflow_cap=overflow_cap)
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

def load_contours(con, log=None):
    """contour_id -> UTM polygon, from the shared field_contours directory.

    Empty until the Wialon directory is mirrored (step 5 of the track), and an
    empty answer is fine: a site with no contour is real work on unregistered
    ground, which is the normal case and never a zero.

    [REASON]: контуры чинятся ЗДЕСЬ, один раз на прогон, а не в каждом
    пересечении. Зоны Wialon рисуют мышкой, самопересечение там обычное дело,
    и GEOS на таком контуре поднимает исключение вместо пустого пересечения --
    27.09 это убило --catch-up на 400+ объектах. Починенное и выброшенное
    считается и называется: контур, по которому нельзя назвать участок, --
    это расхождение со справочником, а не мелочь.
    """
    contours = {}
    try:
        rows = con.execute(
            "SELECT id, geometry_geojson FROM field_contours "
            "WHERE geometry_geojson IS NOT NULL AND geometry_geojson != '' "
            "AND (is_active IS NULL OR is_active = 1)").fetchall()
    except sqlite3.OperationalError:
        return contours
    repaired = dropped = 0
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
        raw = shapely.Polygon(zip(xs, ys))
        fixed = repair_polygon(raw)
        if fixed is None:
            dropped += 1
            continue
        if fixed is not raw:
            repaired += 1
        contours[contour_id] = fixed
    if log and (repaired or dropped):
        log("  contours: %d repaired (self-intersecting), %d dropped as "
            "unusable" % (repaired, dropped))
    return contours


def _utm_polygon_from_geojson(text):
    try:
        polygon = shapely_shape(json.loads(text))
    except (ValueError, TypeError, AttributeError):
        return None
    if polygon.is_empty or polygon.geom_type != "Polygon":
        return None
    xs, ys = to_utm(*polygon.exterior.coords.xy)
    # [REASON]: тот же ремонт, что и у контуров, и по той же причине --
    # `_carry_labels` пересекает старый полигон с новым, и исключение из GEOS
    # здесь стоило бы не суток расчёта, а ОТВЕТОВ ОПЕРАТОРА: they are
    # hand-entered data and the training set of the work/transit rule.
    return repair_polygon(shapely.Polygon(zip(xs, ys)))


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


def write_day(con, day, unit_id, result, computed_at=None, keep_answers=False):
    """Replace the day's rows in one transaction. Returns (carried, dropped).

    A recomputation REPLACES: the aggregate is upserted and the polygons are
    deleted and re-inserted, so the same day computed five times is one row and
    five sites, not five rows and twenty-five sites. The operator's answers are
    carried across by overlap; any that find no home are counted and reported
    by the caller rather than disappearing.

    keep_answers=True writes nothing at all when an answer would find no home:
    AnswersWouldBeLost is raised and the transaction rolled back, so the old
    rows and the answer stay exactly as they were.
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
        # [REASON]: the nightly run reports a lost answer and goes on -- its day
        # is new and an answer rarely exists yet. A recompute of past days
        # (tools/gps_recompute_days.py) runs while the program works, and an
        # answer given meanwhile on a site the rule removes would be deleted by
        # this very transaction: production data gone automatically. There the
        # object-day is left untouched instead, and the run says why.
        if keep_answers and len(carried) < len(existing):
            raise AnswersWouldBeLost(
                "%d of %d operator answer(s) of %s unit %s find no new site; "
                "the old rows are kept" % (len(existing) - len(carried),
                                           len(existing), day, unit_id))

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

        _upsert_aggregate(con, day, unit_id, result.aggregate, computed_at)
        con.commit()
    except Exception:
        con.rollback()
        raise
    return len(carried), len(existing) - len(carried)


def _upsert_aggregate(con, day, unit_id, aggregate, computed_at):
    """The day's aggregate row, inserted or replaced. No transaction of its own."""
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


def write_track_only(con, day, unit_id, aggregate, computed_at=None):
    """The aggregate of a track-only day; the day's polygons are NOT touched.

    [REASON]: write_day() with zero sites would delete the polygons an earlier
    computation left and report every operator answer on them as lost. For
    special machinery that loss buys nothing: the screen does not show those
    polygons under this reason, and the day they are wanted again -- the owner
    moves the machine to a field category -- write_day() carries the answers
    onto the new sites by overlap, as any recomputation does. The same choice
    mark_incomplete() makes for sbor_nepolnyy, for the same reason.
    """
    computed_at = computed_at or datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S")
    con.execute("BEGIN")
    try:
        _upsert_aggregate(con, day, unit_id, aggregate, computed_at)
        con.commit()
    except Exception:
        con.rollback()
        raise


# --- completeness of the collection (GPS-11) ---------------------------------

def day_bounds(day):
    """(start, finish) epoch seconds of one local day."""
    start = int(datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=TZ).timestamp())
    return start, start + 86400


def collection_state(folder, unit_id, day):
    """(complete, watermark) -- has the collector asked for the whole day?

    [REASON]: the watermark is written only after the points are committed,
    and a truncated answer leaves it on the last message stored
    (gps_collector/main.py), so "watermark before the end of the day" proves
    that the tail of the day has not been fetched. No watermark file, or no
    row for the object, means the points did not come through the collector
    -- a fixture, a hand import -- and there is nothing to compare against:
    such a day is computed. The state file is only READ here; opening it
    through storage.open_state would create it, and a reader must not.
    """
    if not os.path.exists(storage.state_path(folder)):
        return True, None
    watermark = storage.read_watermark(folder, unit_id)
    if watermark is None:
        return True, None
    _, finish = day_bounds(day)
    return watermark >= finish, watermark


def _reason_only_aggregate(points_total, reason):
    return {"points_total": points_total, "points_work": 0, "track_km": 0.0,
            "interval_median_s": None, "sats_median": None, "motion_gaps": 0,
            "lost_seconds": 0.0, "gps_jumps": 0, "reason": reason,
            "method_version": METHOD_VERSION}


def mark_incomplete(con, day, unit_id, points_total, computed_at=None):
    """The aggregate row gets the reason; NOTHING else is touched.

    [REASON]: the polygons of an earlier, partial computation stay in place on
    purpose. An operator may already have answered on them, and write_day()
    with zero sites would report every one of those answers as lost.
    --catch-up recomputes the day once the watermark has passed it and carries
    the answers by overlap, as any recomputation does. Until then the screen
    shows the reason instead of a number -- which is the whole point.
    """
    computed_at = computed_at or datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S")
    con.execute("BEGIN")
    try:
        con.execute(
            "INSERT INTO gps_daily_aggregates (work_date, wialon_id, "
            "points_total, points_work, track_km, interval_median_s, "
            "sats_median, motion_gaps, lost_seconds, gps_jumps, reason, "
            "method_version, computed_at) "
            "VALUES (?, ?, ?, 0, 0, NULL, NULL, 0, 0, 0, ?, ?, ?) "
            "ON CONFLICT(work_date, wialon_id) DO UPDATE SET "
            "points_total = excluded.points_total, reason = excluded.reason, "
            "computed_at = excluded.computed_at",
            (day, int(unit_id), int(points_total), REASON_INCOMPLETE,
             METHOD_VERSION, computed_at))
        con.commit()
    except Exception:
        con.rollback()
        raise


def run_day(day, unit_id, folder=None, db_path=None, contours=None, log=print,
            track_only=None, keep_answers=False):
    """Compute and store one object-day. Returns the DayResult.

    `track_only` -- whether the object's category takes no hectares; None
    means "look it up", which the loops avoid by asking once per run.
    `keep_answers` -- passed to write_day: raise AnswersWouldBeLost instead of
    dropping an answer.
    """
    folder = folder or points_dir()
    db_path = db_path or DB_PATH
    points = storage.read_day(folder, unit_id, day)
    complete, _ = collection_state(folder, unit_id, day)
    con = sqlite3.connect(db_path, timeout=30)
    try:
        if not complete:
            mark_incomplete(con, day, unit_id, len(points))
            return DayResult(_reason_only_aggregate(len(points),
                                                    REASON_INCOMPLETE), [])
        if track_only is None:
            track_only = int(unit_id) in track_only_units(con)
        if track_only:
            result = compute_day(points, track_only=True)
            write_track_only(con, day, unit_id, result.aggregate)
            return result
        if contours is None:
            contours = load_contours(con)
        result = compute_day(points, contours=contours or None)
        carried, dropped = write_day(con, day, unit_id, result,
                                     keep_answers=keep_answers)
    finally:
        con.close()
    if dropped:
        # [REASON]: an operator's answer is hand-entered data and the training
        # set for the work/transit rule. Losing one silently would shrink the
        # corpus invisibly, which is exactly how a set stops being trustworthy.
        log("    VNIMANIE: %s: %d" % (DROPPED_MARK, dropped))
    return result


def pending_days(con):
    """(day, unit_id) of every aggregate still waiting for the collector.

    Excluded objects are left exactly as they are: the sbor_nepolnyy row of a
    machine the owner has since called not ours stays, and it is honest -- we
    did stop looking. The screen hides the object; nothing is deleted, and
    nothing is recomputed behind the owner's back.
    """
    excluded = excluded_units(con)
    return [(row[0], int(row[1])) for row in con.execute(
        "SELECT work_date, wialon_id FROM gps_daily_aggregates "
        "WHERE reason = ? ORDER BY work_date, wialon_id", (REASON_INCOMPLETE,))
            if int(row[1]) not in excluded]


CATCH_UP_WINDOW_DAYS = 30


def window_days(until, count):
    """The last `count` local days ending at `until` (YYYY-MM-DD), inclusive."""
    last = datetime.strptime(until, "%Y-%m-%d")
    return [(last - timedelta(days=offset)).strftime("%Y-%m-%d")
            for offset in range(max(0, count - 1), -1, -1)]


def days_without_a_row(con, folder, days):
    """(day, unit_id) that have points in the file but no aggregate at all.

    [REASON]: the second live run of 08.09.2026. An object whose tail the
    collector had not fetched yet had NO points for its later days, so the
    day's computation never listed it -- no row, no reason, nothing for
    --catch-up to find -- and once the tail arrived nobody came back for those
    days. A missing row reads as "we did not look", which is honest, but the
    nightly job computes only yesterday, so "we did not look" would have stayed
    for ever. Here the window is walked and every such day is picked up.
    """
    if not days:
        return []
    excluded = excluded_units(con)
    have = {(row[0], int(row[1])) for row in con.execute(
        "SELECT work_date, wialon_id FROM gps_daily_aggregates "
        "WHERE work_date >= ? AND work_date <= ?", (days[0], days[-1]))}
    missing = []
    for day in days:
        for unit_id in storage.units_with_points(folder, day):
            if (day, unit_id) not in have and unit_id not in excluded:
                missing.append((day, unit_id))
    return missing


# Код возврата, когда часть суток не посчиталась из-за сбоя. Ночной .bat
# коды не проверяет, но человек и будущая обёртка должны иметь по чему отличить
# «всё посчитано» от «посчитано не всё».
EXIT_SOME_DAYS_FAILED = 5


def area_rule_drift(con, folder, days, track_only):
    """Days of the window whose stored row disagrees with the category rule.

    Returns (drift, no_points): (day, unit_id) to recompute, and how many such
    days were left alone because their points are no longer on disk.

    [REASON]: the category of a machine is the switch between "track only" and
    "track and hectares", and a person flips it on the equipment card -- after
    the days were computed. Without this pass a loader moved to special
    machinery would keep its old hectares on screen for as long as the rows
    live, and a machine moved out of it would never get its hectares at all:
    the nightly job computes only yesterday. Both directions are the same
    disagreement and are mended the same way.

    Left alone: sbor_nepolnyy (the pending pass owns those days), net_tochek
    (a day without points computes to the same row under either rule), the
    excluded objects (the screen hides them, nothing is recomputed behind the
    owner's back) -- and any day whose points are gone. Recomputing a day from
    an empty file would overwrite a measured track with net_tochek; the points
    are deleted by retention after 90 days, and a --window-days wider than
    that must not turn history into "no points".
    """
    if not days:
        return [], 0
    excluded = excluded_units(con)
    rows = con.execute(
        "SELECT work_date, wialon_id, reason FROM gps_daily_aggregates "
        "WHERE work_date >= ? AND work_date <= ? "
        "AND (reason IS NULL OR reason NOT IN (?, ?)) "
        "ORDER BY work_date, wialon_id",
        (days[0], days[-1], REASON_INCOMPLETE, REASON_NO_POINTS)).fetchall()
    drift, no_points, with_points = [], 0, {}
    for day, unit_id, reason in rows:
        unit_id = int(unit_id)
        if unit_id in excluded:
            continue
        if (unit_id in track_only) == (reason == REASON_TRACK_ONLY):
            continue
        if day not in with_points:
            with_points[day] = set(storage.units_with_points(folder, day))
        if unit_id not in with_points[day]:
            no_points += 1
            continue
        drift.append((day, unit_id))
    return drift, no_points


def _guarded_day(day, unit_id, folder, db_path, contours, log, failures,
                 track_only=None, keep_answers=False):
    """run_day, но сбой ОДНИХ суток не уносит весь прогон. None при сбое.

    [REASON]: дважды за два дня одна кривая строка убивала прогон по 400+
    объектам: 27.09 -- `None > 30.0` на сутках с единственной точкой, 28.09 --
    `TopologyException` на самопересекающемся контуре. В обоих случаях всё, до
    чего цикл не дошёл, осталось непосчитанным, и прогон не оставил следа
    почему. Причины разные, форма отказа одна, и лечится она здесь, а не
    добавлением ещё одного частного случая.

    Сбой НЕ прячется: он печатается строкой, считается, перечисляется в конце и
    меняет код возврата. Строка в базу не пишется вовсе -- сутки остаются «мы
    не смотрели», что правда, и следующий --catch-up возьмёт их снова; когда
    причина сбоя уйдёт, сутки досчитаются сами.
    """
    try:
        return run_day(day, unit_id, folder=folder, db_path=db_path,
                       contours=contours, log=log, track_only=track_only,
                       keep_answers=keep_answers)
    except Exception as problem:                                   # noqa: BLE001
        failures.append((day, unit_id, type(problem).__name__,
                         ascii_only(str(problem))[:160]))
        log("  %s %-8s SBOY: %s" % (day, unit_id, type(problem).__name__))
        return None


def _report_failures(failures, log):
    """Перечислить сбойные сутки. Возвращает код возврата прогона."""
    if not failures:
        return 0
    log("")
    log("NE POSCHITANO IZ-ZA SBOYA: %d day(s). Strok v bazu ne zapisano, "
        "--catch-up voz'myot ikh snova:" % len(failures))
    for day, unit_id, kind, message in failures:
        log("  %s %-8s %s: %s" % (day, unit_id, kind, message))
    return EXIT_SOME_DAYS_FAILED


def catch_up(folder, db_path, log=print, window=CATCH_UP_WINDOW_DAYS,
             until=None):
    """Recompute the days marked incomplete whose collection has completed,
    and pick up the days of the window that have points but no row at all.

    The nightly job runs this right after the day's computation, so a backlog
    that took several runs to fetch is published the night it completes, not
    never. Days whose watermark still stands short are left exactly as they
    are (or marked, if they had no row) and counted.

    A third pass brings the window in line with the category rule (special
    machinery: track, no hectares) -- see area_rule_drift().
    """
    until = until or (datetime.now(TZ) - timedelta(days=1)).strftime("%Y-%m-%d")
    days = window_days(until, window)
    con = sqlite3.connect(db_path, timeout=30)
    try:
        pending = pending_days(con)
        missing = days_without_a_row(con, folder, days)
        track_only = track_only_units(con)
        drift, drift_no_points = area_rule_drift(con, folder, days, track_only)
        contours = (load_contours(con, log=log)
                    if (pending or missing or drift) else {})
    finally:
        con.close()
    if not pending and not missing and not drift:
        log("catch-up: nothing is waiting for the collector")
        if drift_no_points:
            log("catch-up: category rule not applied to %d day(s) -- their "
                "points are no longer on disk" % drift_no_points)
        return 0
    failures = []
    recomputed = waiting = 0
    for day, unit_id in pending:
        complete, _ = collection_state(folder, unit_id, day)
        if not complete:
            waiting += 1
            continue
        result = _guarded_day(day, unit_id, folder, db_path, contours, log,
                              failures, track_only=unit_id in track_only)
        if result is None:
            continue
        recomputed += 1
        if result.reason:
            log("  %s %-8s %s" % (day, unit_id, result.reason))
        else:
            log("  %s %-8s %d site(s), %.2f ha"
                % (day, unit_id, len(result.sites),
                   sum(s["area_ha"] for s in result.sites)))
    log("catch-up: recomputed %d | still waiting for the collector: %d"
        % (recomputed, waiting))

    filled = marked = 0
    for day, unit_id in missing:
        result = _guarded_day(day, unit_id, folder, db_path, contours, log,
                              failures, track_only=unit_id in track_only)
        if result is None:
            continue
        if result.reason == REASON_INCOMPLETE:
            marked += 1
        else:
            filled += 1
        log("  %s %-8s %s" % (day, unit_id, result.reason or
                              "%d site(s)" % len(result.sites)))
    log("catch-up window %s..%s: days without a row: %d -- computed %d, "
        "marked %s %d" % (days[0], days[-1], len(missing), filled,
                          REASON_INCOMPLETE, marked))

    to_track = to_area = 0
    for day, unit_id in drift:
        result = _guarded_day(day, unit_id, folder, db_path, contours, log,
                              failures, track_only=unit_id in track_only)
        if result is None:
            continue
        if unit_id in track_only:
            to_track += 1
        else:
            to_area += 1
        log("  %s %-8s %s" % (day, unit_id, result.reason or
                              "%d site(s), %.2f ha" % (
                                  len(result.sites),
                                  sum(s["area_ha"] for s in result.sites))))
    if drift or drift_no_points:
        log("catch-up window %s..%s: category rule -- %d day(s) now track "
            "only (%s), %d day(s) back to hectares, %d left alone: points no "
            "longer on disk" % (days[0], days[-1], to_track, REASON_TRACK_ONLY,
                                to_area, drift_no_points))
    return _report_failures(failures, log)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--catch-up", action="store_true",
                        help="recompute the days marked sbor_nepolnyy whose "
                             "collection has since completed, and pick up the "
                             "days of the window that have points but no row; "
                             "takes no --date")
    parser.add_argument("--window-days", type=int, default=CATCH_UP_WINDOW_DAYS,
                        help="how many days back --catch-up looks for days "
                             "without a row (default %d)" % CATCH_UP_WINDOW_DAYS)
    parser.add_argument("--until", default=None,
                        help="last day of the --catch-up window, YYYY-MM-DD "
                             "(default: yesterday, local)")
    parser.add_argument("--date", default=None,
                        help="local day, YYYY-MM-DD. По умолчанию -- вчерашние "
                             "сутки по местному времени")
    parser.add_argument("--unit", action="append", default=[], type=int,
                        help="wialon id; may repeat. Default: every object "
                             "with points that day")
    parser.add_argument("--dir", default=None,
                        help="where the point files live (default: instance/)")
    parser.add_argument("--db", default=None, help="path to transport.db")
    parser.add_argument("--keep-answers", action="store_true",
                        help="an object-day whose operator answer would find "
                             "no new site is not written: it keeps its old "
                             "rows and counts as failed (used by "
                             "tools/gps_recompute_days.py); not with "
                             "--catch-up")
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

    if args.catch_up:
        if args.date or args.unit:
            # [REASON]: --catch-up picks its own days from the database; a
            # --date next to it would be silently ignored, and a silently
            # ignored argument is how a day gets computed twice by mistake.
            sys.stderr.write("ERROR: --catch-up takes no --date and no --unit\n")
            return 2
        if args.keep_answers:
            sys.stderr.write("ERROR: --keep-answers is for a --date run, "
                             "not --catch-up\n")
            return 2
        if args.window_days < 1:
            sys.stderr.write("ERROR: --window-days must be at least 1\n")
            return 2
        if args.until:
            try:
                datetime.strptime(args.until, "%Y-%m-%d")
            except ValueError:
                sys.stderr.write("ERROR: --until must be YYYY-MM-DD, got %r\n"
                                 % args.until)
                return 2
        return catch_up(folder, db_path, window=args.window_days,
                        until=args.until)

    units = args.unit or storage.units_with_points(folder, day)
    if not units:
        print("no points for %s in %s" % (day, folder))
        return 0

    con = sqlite3.connect(db_path, timeout=30)
    try:
        contours = load_contours(con, log=print)
        excluded = excluded_units(con)
        track_only = track_only_units(con)
    finally:
        con.close()
    # [REASON]: an explicit --unit is a request from a person and is honoured
    # even for an excluded object -- "show me this one" must not answer with
    # silence. The list nobody named is filtered, and WHY each object was left
    # out is printed, because a day that quietly computes fewer objects than
    # yesterday is exactly the kind of change that goes unnoticed for weeks.
    left_out = Counter()
    if not args.unit and excluded:
        keep = []
        for unit_id in units:
            why = excluded.get(unit_id)
            if why is None:
                keep.append(unit_id)
            else:
                left_out[why] += 1
        units = keep
    reasons = ", ".join("%s: %d" % pair for pair in sorted(left_out.items()))
    if not units:
        print("no objects left for %s -- every one is excluded (%s)"
              % (day, reasons))
        return 0
    tracks_only = sum(1 for unit_id in units if unit_id in track_only)
    print("%s: %d object(s), %d contour(s) in the directory%s%s"
          % (day, len(units), len(contours),
             ", left out -- %s" % reasons if reasons else "",
             ", track only (%s): %d" % (REASON_TRACK_ONLY, tracks_only)
             if tracks_only else ""))

    published = refused = incomplete = sites_total = 0
    failures = []
    for unit_id in units:
        result = _guarded_day(day, unit_id, folder, db_path, contours, print,
                              failures, track_only=unit_id in track_only,
                              keep_answers=args.keep_answers)
        if result is None:
            continue
        if result.reason:
            refused += 1
            incomplete += result.reason == REASON_INCOMPLETE
            print("  %-8s %s" % (unit_id, result.reason))
        else:
            published += 1
            sites_total += len(result.sites)
            print("  %-8s %d site(s), %.2f ha"
                  % (unit_id, len(result.sites),
                     sum(s["area_ha"] for s in result.sites)))
    print("\npublished: %d | not computed: %d | sites: %d"
          % (published, refused, sites_total))
    if incomplete:
        print("waiting for the collector: %d -- run the collector again, then "
              "gps.daily --catch-up" % incomplete)
    return _report_failures(failures, print)


if __name__ == "__main__":
    sys.exit(main())
