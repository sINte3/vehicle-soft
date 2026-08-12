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

ADAPTIVE ALPHA -- introduced 2026-08-12, and here is the whole warrant.
The rule alpha = max(10; 1.2 x measured pass spacing) was written down on
2026-07-29 and deliberately NOT adopted then: it had been tuned on the same
17 works it improved, and a correction measured on its own training set is
not a result. The pre-registration said it enters the method only if an
unseen set confirms it. That set arrived on 2026-08-12 -- 15 hand-measured
spraying works, computed with the frozen code committed BEFORE the data:

  fixed alpha 10 m   median |deviation| 26.4 %, mean -32.1 %
  adaptive           median |deviation| 12.0 %, mean -22.9 %
  on the 8 works free of unmapped land and broken track: 10.9 % -> 3.4 %

It is robust. The pre-registration fixed the rule but not the estimator, so
that degree of freedom was swept: the detour ratio (3 / 5 / 8) changes
nothing at all -- the numbers are identical, passes are separated
unambiguously -- and the multiplier 1.1 / 1.2 / 1.3 moves the median only
between 10.1 and 12.0 percent.

And it costs nothing where the method was already validated. Spraying runs
passes 13-22 m apart, cultivation 5-9 m, so max(10; 1.2 x 8) is 10 -- the
rule is a NO-OP on cultivation by construction, confirmed by replay: the
calibration set of 18-23.07 moves by EXACTLY +0.0 percent (no single work
shifts at all), and the 27.07 set by +0.9 percent, where every work that
moves is a spraying one.

What the set did NOT settle: 7 of the 15 works stay wrong for reasons the
alpha cannot touch -- up to 8.8 ha worked on ground that has no geozone at
all, and tracks losing up to 708 minutes of motion in a day. Those are
input defects, both measured and reported by this module, not method error.

To reproduce the old behaviour exactly, pass alpha_m=ALPHA_M explicitly.

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
from scipy.spatial import Delaunay, cKDTree
from shapely.geometry import MultiPoint, Polygon
from shapely.ops import unary_union

# --- frozen method parameters ------------------------------------------------
SPEED_MIN_KMH = 1.0
SPEED_MAX_KMH = 15.0
DENSIFY_STEP_M = 5.0
DENSIFY_MAX_SEG_M = 150.0
ALPHA_M = 10.0
UTM_41N = "EPSG:32641"

# [REASON]: alpha must exceed the spacing between neighbouring passes, or the
# shape cannot stitch them and the area comes out short. 1.2 is the margin
# pre-registered on 2026-07-29 and confirmed on unseen data on 2026-08-12.
ALPHA_SPACING_FACTOR = 1.2

# [REASON]: a point on the pass alongside is one that is CLOSE in a straight
# line but FAR along the track -- you have to drive to the end of this pass,
# turn, and come back. The first estimator used a time threshold (120 s)
# instead, and a synthetic case caught it overestimating the spacing four-fold
# when a pass took less than the threshold: it skipped the neighbouring pass
# entirely. Overestimation is the dangerous direction, because it inflates
# alpha until the shape bridges genuinely unworked ground. This ratio has no
# units, so it holds for any pass length, speed or point interval.
PASS_SPACING_DETOUR_RATIO = 5.0
PASS_SPACING_MIN_POINTS = 20
PASS_SPACING_SAMPLES = 400
PASS_SPACING_NEIGHBOURS = 80

METHOD_VERSION = "adaptive-alpha-2026-08-12"

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
    # [REASON]: the alpha actually used and the spacing it came from are
    # stored, not recomputed later. A hectare figure goes into an invoice and
    # has to be defensible a year on: without these two numbers the result
    # cannot be reproduced once the rule or the estimator changes.
    alpha_used_m: float = ALPHA_M
    pass_spacing_m: float = None
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


def pass_spacing(points_xy, detour_ratio=PASS_SPACING_DETOUR_RATIO):
    """Distance between neighbouring passes, measured from the track itself.

    For a sample of points, find the nearest point that lies on a DIFFERENT
    pass, and take the median of those distances. A different pass is
    recognised by the detour: driving there along the track is at least
    `detour_ratio` times further than the straight line to it.

    Returns None when the cloud is too small, or when no neighbour looks like
    another pass at all -- a single straight run has no spacing to measure.
    The caller then falls back to the fixed alpha rather than guess.

    [REASON]: the implement width is NEVER used, here or anywhere else. It is
    unknown, the machine changes implements, and every method built on it
    failed in this project. The spacing is a property of the track.
    """
    if len(points_xy) < PASS_SPACING_MIN_POINTS:
        return None
    pts = np.asarray(points_xy, dtype=float)
    # Distance travelled along the track up to each point.
    steps = np.linalg.norm(np.diff(pts, axis=0), axis=1)
    along = np.concatenate(([0.0], np.cumsum(steps)))
    tree = cKDTree(pts)
    sample_step = max(1, len(pts) // PASS_SPACING_SAMPLES)
    neighbours = min(PASS_SPACING_NEIGHBOURS, len(pts))
    found = []
    for i in range(0, len(pts), sample_step):
        distances, indices = tree.query(pts[i], k=neighbours)
        for distance, j in zip(np.atleast_1d(distances), np.atleast_1d(indices)):
            if distance <= 0:
                continue
            if abs(along[j] - along[i]) >= detour_ratio * distance:
                found.append(float(distance))
                break
    return float(np.median(found)) if found else None


def worked_area(track, contour, contour_id=None, alpha_m=None):
    """The method for one machine, one interval, one contour.

    `track` is a sequence of (timestamp_seconds, lon, lat, speed_kmh).
    `contour` is a shapely Polygon already in UTM 41N.
    `alpha_m` pins alpha to a fixed value; the default None selects the
    adaptive rule max(ALPHA_M, ALPHA_SPACING_FACTOR x measured spacing).
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

    if alpha_m is None:
        spacing = pass_spacing(used)
        alpha = (max(ALPHA_M, ALPHA_SPACING_FACTOR * spacing)
                 if spacing is not None else ALPHA_M)
    else:
        spacing, alpha = None, alpha_m

    shape = alpha_shape(densify(used), alpha)
    if shape is None:
        return WorkArea(contour_id, 0.0, None, quality, alpha, spacing)
    clipped = shape.intersection(contour)
    return WorkArea(contour_id, clipped.area / 10000.0, clipped, quality,
                    alpha, spacing)


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
