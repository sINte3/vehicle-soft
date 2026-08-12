# -*- coding: utf-8 -*-
"""GPS-ENGINE-001 -- the frozen worked-area method, as an importable engine.

This module is the single implementation of the area method of the GPS
plan-fact track. It computes, for one machine over one interval, how many
hectares it actually worked, together with a quality flag describing how
trustworthy the underlying track is.

    points of the interval
      -> filter: speed 1..15 km/h AND inside the contour
      -> densify the path every 5 m (segments shorter than 150 m only)
      -> alpha shape: Delaunay triangulation, keep triangles whose
         circumradius < alpha, alpha = 10 m
      -> union of the kept triangles
      -> clip by the contour polygon
      -> area in UTM 41N (EPSG:32641)

WHY THESE PARAMETERS ARE CONSTANTS, NOT SETTINGS
The method was fixed in writing on 2026-07-29 and verified on a set the
method had never seen: median deviation from the owner's manual measurement
3.3 percent over 17 works. Every parameter below was measured, not chosen:
points along a pass stand 35-55 m apart while neighbouring passes are 5-14 m
apart, and it is that gap of scales -- not the tracker -- which makes a raw
alpha fragile. Densification removes the gap; after it alpha is stable
anywhere in 8-15 m. Changing any constant invalidates the calibration and
requires a new pre-registered verification set (docs/PLAN.md section 5).

The adaptive rule alpha = max(10; 1.2 x measured pass spacing) is
deliberately ABSENT. It improves spraying from -8.6 to -1.0 percent, but it
was tuned on the same data it improved, and a correction measured on its own
training set is not a result. It enters the method only after an unseen set
confirms it.

WHAT THIS MODULE DOES NOT DO
It contains no business rules. It does not decide what counts as work rather
than passage, what deviation is acceptable, or when a track is too broken to
bill: those are the owner's to set (open questions V-2 and V-3 of
docs/GPS_PLAN_FAKT_VISION_ROADMAP.md). It returns measured numbers and lets
the caller apply thresholds.

PACKAGING
Like drone_collector, this package is NOT part of the Flask application. It
imports neither `app` nor `models`, never touches transport.db, and keeps its
own requirements.txt: the web service must not grow a numpy/scipy/shapely
dependency because of it. Windows wheels for CPython 3.14 exist for all four
libraries (checked on PyPI 2026-08-12), so the server needs no compiler.
"""

import math
from dataclasses import dataclass, field

import numpy as np
import shapely
from pyproj import Transformer
from scipy.spatial import Delaunay
from shapely.geometry import MultiPoint, Polygon
from shapely.ops import unary_union

# --- frozen method parameters ------------------------------------------------
SPEED_MIN_KMH = 1.0
SPEED_MAX_KMH = 15.0
DENSIFY_STEP_M = 5.0
DENSIFY_MAX_SEG_M = 150.0
ALPHA_M = 10.0
UTM_41N = "EPSG:32641"

METHOD_VERSION = "frozen-2026-07-29"

# [REASON]: the tracker writes at most every 30 s while moving (parameter
# 10050) and transmits every 60 s (10055), so 5 minutes between two CONSECUTIVE
# messages, at least one of which shows motion, cannot be normal operation --
# it is lost data.
#
# Both obvious definitions are wrong, and both were tried on real tracks:
#   - gaps over all messages regardless of speed: a parked machine reports
#     once per 30 minutes (10000), so every lunch break becomes a "broken
#     track". This is the mistake the project already made once.
#   - gaps between successive MOVING messages, skipping what lies between:
#     the same stop reappears, because the messages bridging it are simply
#     not looked at. Measured on unit 3464 (2026-07-27) this reported 23 gaps
#     and 5.9 hours lost on a track whose raw stream has no gap over 5
#     minutes at all.
# What is left is the correct one: walk CONSECUTIVE raw messages and require
# motion at one end.
MOTION_GAP_SECONDS = 300.0

_TO_UTM = Transformer.from_crs("EPSG:4326", UTM_41N, always_xy=True)


# --- data carriers -----------------------------------------------------------

@dataclass(frozen=True)
class TrackQuality:
    """How much of the track survived, in plain measured numbers."""

    points_total: int
    points_moving: int
    points_used: int          # moving AND inside the contour
    motion_gaps: int          # gaps longer than MOTION_GAP_SECONDS
    lost_seconds: float       # total duration of those gaps
    span_seconds: float       # first to last message of the interval

    @property
    def lost_share(self):
        """Share of the interval lost to gaps, 0.0 when nothing is known."""
        if self.span_seconds <= 0:
            return 0.0
        return self.lost_seconds / self.span_seconds


@dataclass(frozen=True)
class WorkArea:
    """One (machine, interval, contour) result."""

    contour_id: object
    area_ha: float
    polygon: object                     # shapely geometry in UTM 41N, or None
    quality: TrackQuality
    method_version: str = METHOD_VERSION

    @property
    def has_data(self):
        return self.quality.points_used > 0


@dataclass
class JointWorkCheck:
    """Safeguard for several machines on one contour on the same day.

    Billing is the SUM over machines -- confirmed on contour 2714, where two
    tractors did 6.792 + 6.933 ha of a 42.30 ha field. But a sum can hide
    double-counting, so the union of the polygons is computed alongside: if
    the sum exceeds the union, the machines covered the same ground and the
    overlap is real. Reporting it is the point; deciding what to do about it
    is the owner's rule, not this module's.
    """

    sum_ha: float
    union_ha: float
    machines: int
    overlap_ha: float = field(init=False)

    def __post_init__(self):
        self.overlap_ha = max(0.0, self.sum_ha - self.union_ha)

    @property
    def has_overlap(self):
        # [REASON]: 0.01 ha = 100 m2 absorbs floating-point noise in the union
        # of thousands of triangles; below that an "overlap" is arithmetic,
        # not two tractors on one strip.
        return self.overlap_ha > 0.01


# --- geometry primitives -----------------------------------------------------

def to_utm(lons, lats):
    """WGS84 degrees -> UTM 41N metres. Accepts scalars or sequences."""
    return _TO_UTM.transform(lons, lats)


def polygon_from_wialon(points):
    """Build a UTM polygon from a Wialon zone's point list.

    Wialon gives geometry as [{'x': lon, 'y': lat, 'r': radius}, ...].

    [REASON]: points and polygons must live in the SAME coordinate system.
    A polygon left in degrees while the points were projected made
    contains() false for every point and produced a silent zero -- the
    failure looks exactly like "the machine never entered the field".
    """
    if not points or len(points) < 3:
        return None
    xs, ys = to_utm([p["x"] for p in points], [p["y"] for p in points])
    poly = Polygon(zip(xs, ys))
    if not poly.is_valid:
        poly = shapely.make_valid(poly)
    return None if poly.is_empty else poly


def densify(points_xy, step_m=DENSIFY_STEP_M, max_segment_m=DENSIFY_MAX_SEG_M):
    """Insert a point every step_m along segments shorter than max_segment_m.

    [REASON]: segments of max_segment_m and longer are track gaps and must NOT
    be filled in. Bridging them would invent work across the part of the field
    the tracker went silent on -- the opposite of what a quality flag is for.
    """
    out = []
    for i, (x, y) in enumerate(points_xy):
        out.append((x, y))
        if i + 1 >= len(points_xy):
            break
        x2, y2 = points_xy[i + 1]
        distance = math.hypot(x2 - x, y2 - y)
        if 0 < distance < max_segment_m:
            for k in range(1, int(distance // step_m) + 1):
                f = k * step_m / distance
                if f < 1.0:
                    out.append((x + f * (x2 - x), y + f * (y2 - y)))
    return out


def alpha_shape(points_xy, alpha_m=ALPHA_M):
    """Union of the Delaunay triangles whose circumradius is below alpha_m.

    Returns None when the cloud cannot form one (fewer than 4 points, or every
    triangle too large). None means "no shape", never "zero area" -- the
    caller must keep the difference visible.
    """
    pts = np.asarray(points_xy, dtype=float)
    if len(pts) < 4:
        return None
    try:
        tri = Delaunay(pts)
    except Exception:                                          # noqa: BLE001
        # [REASON]: QHull refuses a degenerate cloud (all points on one line,
        # or all identical). That is a real answer about the data -- a machine
        # that drove one straight pass has no area -- not a crash to hide.
        return None
    a, b, c = pts[tri.simplices[:, 0]], pts[tri.simplices[:, 1]], pts[tri.simplices[:, 2]]
    la = np.linalg.norm(b - c, axis=1)
    lb = np.linalg.norm(a - c, axis=1)
    lc = np.linalg.norm(a - b, axis=1)
    s = (la + lb + lc) / 2.0
    area = np.sqrt(np.maximum(s * (s - la) * (s - lb) * (s - lc), 1e-12))
    circumradius = la * lb * lc / (4.0 * area)
    keep = tri.simplices[circumradius < alpha_m]
    if len(keep) == 0:
        return None
    return unary_union([Polygon(pts[t]) for t in keep])


# --- the method ---------------------------------------------------------------

def _moving_mask(speeds):
    """The WORK window: slow enough to be working, fast enough to not be idle."""
    speeds = np.asarray(speeds, dtype=float)
    return (speeds >= SPEED_MIN_KMH) & (speeds <= SPEED_MAX_KMH)


def _in_motion(speeds):
    """Moving at all -- no upper bound.

    [REASON]: distinct from _moving_mask on purpose. Driving to the field at
    30 km/h is motion but not work: it must not add area, yet a tracker going
    silent during it IS lost data. Using the work window here would declare
    every road stretch a gap.
    """
    return np.asarray(speeds, dtype=float) >= SPEED_MIN_KMH


def track_quality(timestamps, speeds, points_used=0):
    """Measure the track: how many points, how much time was lost.

    `timestamps` are seconds (any epoch, only differences are used).
    """
    ts = np.asarray(timestamps, dtype=float)
    mask = _moving_mask(speeds)
    span = float(ts[-1] - ts[0]) if len(ts) >= 2 else 0.0
    if len(ts) < 2:
        return TrackQuality(points_total=len(ts), points_moving=int(mask.sum()),
                            points_used=points_used, motion_gaps=0,
                            lost_seconds=0.0, span_seconds=span)

    # Consecutive raw messages, with motion required at one end (see the
    # MOTION_GAP_SECONDS comment for the two definitions this replaces).
    motion = _in_motion(speeds)
    deltas = np.diff(ts)
    is_gap = (deltas > MOTION_GAP_SECONDS) & (motion[:-1] | motion[1:])
    return TrackQuality(points_total=len(ts), points_moving=int(mask.sum()),
                        points_used=points_used, motion_gaps=int(is_gap.sum()),
                        lost_seconds=float(deltas[is_gap].sum()),
                        span_seconds=span)


def worked_area(track, contour, contour_id=None):
    """The frozen method for one machine, one interval, one contour.

    `track` is a sequence of (timestamp_seconds, lon, lat, speed_kmh).
    `contour` is a shapely Polygon already in UTM 41N.
    """
    if contour is None or not track:
        return WorkArea(contour_id, 0.0, None,
                        track_quality([0.0], [0.0]) if not track
                        else track_quality([r[0] for r in track],
                                           [r[3] for r in track]))

    timestamps = [r[0] for r in track]
    speeds = [r[3] for r in track]
    xs, ys = to_utm([r[1] for r in track], [r[2] for r in track])
    xs, ys = np.asarray(xs), np.asarray(ys)

    mask = _moving_mask(speeds) & shapely.contains_xy(contour, xs, ys)
    used = [(float(x), float(y)) for x, y in zip(xs[mask], ys[mask])]
    quality = track_quality(timestamps, speeds, points_used=len(used))
    if not used:
        return WorkArea(contour_id, 0.0, None, quality)

    shape = alpha_shape(densify(used))
    if shape is None:
        return WorkArea(contour_id, 0.0, None, quality)
    clipped = shape.intersection(contour)
    return WorkArea(contour_id, clipped.area / 10000.0, clipped, quality)


def candidate_contours(track, contours, min_moving_points=10):
    """Which contours the machine actually entered, by the track itself.

    `contours` maps contour_id -> shapely Polygon in UTM 41N.
    Returns [(contour_id, moving_points_inside)] ordered by point count.

    [REASON]: a contour is NEVER matched by name. The Wialon directory holds
    duplicate names by construction -- "1508 Нурхон Бобохон" exists as two
    zones of 4.32 and 8.97 ha, and the exact-name lookup picked the one the
    tractor never entered. One customer has about 150 zones with at least four
    repeated numbers. The track is the only reliable identifier.
    """
    if not track or not contours:
        return []
    speeds = [r[3] for r in track]
    xs, ys = to_utm([r[1] for r in track], [r[2] for r in track])
    xs, ys = np.asarray(xs), np.asarray(ys)
    mask = _moving_mask(speeds)
    mx, my = xs[mask], ys[mask]
    if len(mx) == 0:
        return []

    ids = list(contours.keys())
    geoms = [contours[i] for i in ids]
    tree = shapely.STRtree(geoms)
    # Bounding-box prefilter first, exact containment only for survivors.
    hits = tree.query(MultiPoint(list(zip(mx, my))).envelope)
    found = []
    for position in np.atleast_1d(hits):
        geom = geoms[int(position)]
        inside = int(np.count_nonzero(shapely.contains_xy(geom, mx, my)))
        if inside >= min_moving_points:
            found.append((ids[int(position)], inside))
    found.sort(key=lambda pair: -pair[1])
    return found


def joint_work_check(areas):
    """Sum-vs-union safeguard over several machines on one contour."""
    polygons = [a.polygon for a in areas if a.polygon is not None
                and not a.polygon.is_empty]
    total = sum(a.area_ha for a in areas)
    union_ha = unary_union(polygons).area / 10000.0 if polygons else 0.0
    return JointWorkCheck(sum_ha=total, union_ha=union_ha, machines=len(areas))
