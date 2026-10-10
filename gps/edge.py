# -*- coding: utf-8 -*-
"""GPS EDGE -- driving at work speed beside a field is not work (candidate rule).

THE OWNER'S DECISION, 03.10.2026: driving in the work speed window near the
edge of a field -- entries, exits, runs along the headland -- is not work,
"не считать". The alpha of the area method (at least 10 m) bridges gaps up to
about twice itself, so such driving was glued to the field: New Holland 7060
80 080 HA on 01.10.2026 got 8.33 ha for two fields the owner measured by hand
at 7.805 ha; removing five hand-labelled groups of trips gave 7.80 ha
(roadmap 2.12).

WHERE THE RULE COMES FROM
Four families were built independently on a common bench (synthetic days with
a known field part, the New Holland day from its KML, unit 3464 on 27.07) and
judged three ways: accuracy on held-out days, days built to break them, and
fitness for this engine. The family kept here never erased a field of normal
size on any judged day; its errors go towards keeping too much rather than
deleting work. Two changes came from the judges: its scales are measured per
SITE rather than per day (a day-level scale cut the minority field on days
with two kinds of work), and a site where no pass lies beside another is left
exactly as it is today -- the rule never deletes a whole site on its own
evidence. The bench and the judges' reports stay in the session's working
folder; the pre-registration and every number are in roadmap section 2.12.

THE RULE, on the work-window points in time order, positions and order only
(time and speed are not used: a KML day and a tracker day are treated alike):

  1. CORE. Along the densified track (the engine's own 5 m points), a point is
     core when another pass lies directly beside it on the left AND on the
     right. Beside means: another pass (reached the long way -- the engine's
     detour rule -- or driven back the other way), running alongside (within
     45 degrees), within one densify step ahead or behind, at least one densify
     step to the side, and within 2 x alpha (the engine's stitching reach).
     The inside of a field is core; its outermost pass, its turns and every
     road are not.
  2. PER SITE OF TODAY'S SHAPE, from that site's own core:
       g  the step to the pass beside (median over the core),
       d  how far the site's turns reach beyond its core (90th percentile of
          the excursions that leave the core and come back within 150 m),
       T = max(5 m, 1.2 x g / 2, 1.2 x d)   along the passes, beyond their ends,
       S = 1.2 x g + 5 m                    across them.
  3. MARGIN. A point of the site is kept when it lies in the ellipse T x S
     around some core point of the site, oriented along the passes there. The
     rest of the site's points are dropped.
  4. A site without a single core point is left as it is today, and so is a
     site whose trimmed remainder would fall below the work floor: the rule
     trims sites, it never deletes one.
  5. The shape is rebuilt from the kept points the way the engine builds it
     (a dropped point breaks the line, so nothing is bridged across it) with
     the same alpha, and INTERSECTED with today's shape: the rule can only
     remove ground, never add it.

This module decides nothing about billing. `gps.area.work_sites` calls it only
when asked (`edge_rule=True`); the nightly computation does not ask, so the
method in force is unchanged until the owner adopts the rule.
"""

import math

import numpy as np
import shapely
from scipy.spatial import cKDTree
from shapely.ops import unary_union

from gps.area import (ALPHA_M, ALPHA_SPACING_FACTOR, DENSIFY_MAX_SEG_M,
                      DENSIFY_STEP_M, EDGE_RULE_VERSION, MIN_WORK_AREA_HA,
                      PASS_SPACING_DETOUR_RATIO, RETURN_ALONG_M, alpha_shape,
                      densify)

__all__ = ["EDGE_RULE_VERSION", "analyse", "keep_mask", "shape_of_kept", "trim"]

# [REASON]: closer to parallel than to perpendicular -- the bisector, the one
# angle that favours neither. Used for "alongside" and for "driven back".
ALONGSIDE_DEG = 45.0
_COS_ALONGSIDE = math.cos(math.radians(ALONGSIDE_DEG))

# [REASON]: the turning area is as deep as nine turns in ten, not as deep as
# the single strangest excursion -- a short run along the headland or a
# transfer also leaves the core and comes back within 150 m.
TURN_QUANTILE = 90.0

# [REASON]: the outermost points of a site ARE its boundary; a hair of
# tolerance puts them inside.
ON_SITE_M = 0.01


def _dense_track(xy):
    """The engine's densified track with distance driven and direction.

    Points every DENSIFY_STEP_M on segments shorter than DENSIFY_MAX_SEG_M --
    the same points `gps.area.densify` makes -- each with the distance from the
    first point along the track and its unit direction of travel. Returns
    (P, along, direction, defined, raw) where `raw[i]` is the index in P of
    the i-th input point.
    """
    n = len(xy)
    seg = np.diff(xy, axis=0)
    length = np.hypot(seg[:, 0], seg[:, 1])
    along_raw = np.concatenate(([0.0], np.cumsum(length)))
    unit = np.zeros_like(seg)
    moved = length > 0
    unit[moved] = seg[moved] / length[moved][:, None]
    # direction at an input point: the mean of its incoming and outgoing steps
    direction = np.zeros((n, 2))
    direction[:-1] += unit
    direction[1:] += unit
    norm = np.hypot(direction[:, 0], direction[:, 1])
    defined = norm > 1e-9
    direction[defined] /= norm[defined][:, None]
    count = np.where(moved & (length < DENSIFY_MAX_SEG_M),
                     np.ceil(length / DENSIFY_STEP_M) - 1, 0)
    count = np.maximum(count, 0).astype(int)
    k = np.repeat(np.arange(n - 1), count)
    first = np.concatenate(([0], np.cumsum(count)[:-1]))
    m = np.arange(len(k)) - np.repeat(first, count) + 1
    f = m * DENSIFY_STEP_M / np.where(length[k] > 0, length[k], 1.0)
    order = np.argsort(np.concatenate([np.arange(n, dtype=float), k + 0.999 * f]),
                       kind="stable")
    P = np.concatenate([xy, xy[k] + f[:, None] * seg[k]])[order]
    along = np.concatenate([along_raw, along_raw[k] + f * length[k]])[order]
    T = np.concatenate([direction, unit[k]])[order]
    ok = np.concatenate([defined, np.ones(len(k), bool)])[order]
    raw = np.where(order < n)[0]
    return P, along, T, ok, raw


def _beside_pairs(P, along, T, ok, reach, tree, query, chunk=1000):
    """(x, y, side) for x in `query`: y is another pass directly beside x.

    `side` is the signed distance of y to the side of x, left positive.
    Neighbours are fetched in chunks, so memory stays bounded.
    """
    xs, ys, sides = [], [], []
    for start in range(0, len(query), chunk):
        q = query[start:start + chunk]
        hits = cKDTree(P[q]).sparse_distance_matrix(tree, reach,
                                                    output_type="ndarray")
        if len(hits) == 0:
            continue
        i = q[hits["i"].astype(np.int64)]
        j = hits["j"].astype(np.int64)
        dv = P[j] - P[i]
        ti = T[i]
        ahead = ti[:, 0] * dv[:, 0] + ti[:, 1] * dv[:, 1]
        keep = (np.abs(ahead) <= DENSIFY_STEP_M) & (i != j) & ok[i] & ok[j]
        i, j, dv, ti = i[keep], j[keep], dv[keep], ti[keep]
        tj = T[j]
        cos = ti[:, 0] * tj[:, 0] + ti[:, 1] * tj[:, 1]
        dist = np.hypot(dv[:, 0], dv[:, 1])
        # [REASON]: another pass is reached the long way (the engine's detour
        # rule) -- or is driven back the other way: next to the short turn
        # that joins two neighbouring passes the detour is small, yet they
        # are two passes.
        other = ((np.abs(along[i] - along[j]) >= PASS_SPACING_DETOUR_RATIO * dist)
                 | (cos <= -_COS_ALONGSIDE))
        alongside = np.abs(cos) >= _COS_ALONGSIDE
        side = ti[:, 0] * dv[:, 1] - ti[:, 1] * dv[:, 0]
        # [REASON]: at least one densify step to the side. A road driven
        # again or out and back lies within a few metres of itself (New
        # Holland 01.10: the second exit stays within 4.2 m of the first, the
        # out-and-back run's two lines are 2.5 m apart) and must not count as
        # two passes beside each other.
        valid = other & alongside & (np.abs(side) >= DENSIFY_STEP_M)
        xs.append(i[valid])
        ys.append(j[valid])
        sides.append(side[valid])
    if not xs:
        empty = np.zeros(0, dtype=np.int64)
        return empty, empty, np.zeros(0)
    return np.concatenate(xs), np.concatenate(ys), np.concatenate(sides)


def _pass_direction(P, T, core, cell):
    """Axial mean direction of the core in the 3 x 3 squares around each core
    point -- the direction of the passes there, not of whatever track happens
    to be core at the edge."""
    idx = np.where(core)[0]
    twice = 2.0 * np.arctan2(T[idx, 1], T[idx, 0])
    q = np.floor((P[idx] - P[idx].min(axis=0)) / cell).astype(np.int64) + 1
    width = int(q[:, 1].max()) + 2
    keys = q[:, 0] * width + q[:, 1]
    cells, inv = np.unique(keys, return_inverse=True)
    sc = np.bincount(inv, weights=np.cos(twice), minlength=len(cells))
    ss = np.bincount(inv, weights=np.sin(twice), minlength=len(cells))
    bc = np.zeros(len(cells))
    bs = np.zeros(len(cells))
    for dx in (-1, 0, 1):
        for dy in (-1, 0, 1):
            nb = cells + dx * width + dy
            pos = np.minimum(np.searchsorted(cells, nb), len(cells) - 1)
            found = cells[pos] == nb
            bc[found] += sc[pos[found]]
            bs[found] += ss[pos[found]]
    half = 0.5 * np.arctan2(bs[inv], bc[inv])
    return idx, np.stack([np.cos(half), np.sin(half)], axis=1)


def _turn_depths(P, along, core):
    """Depth beyond the core of every excursion that leaves the core and comes
    back to it within RETURN_ALONG_M of driving."""
    n = len(P)
    edges = np.diff(np.concatenate(([0], (~core).astype(np.int8), [0])))
    starts = np.where(edges == 1)[0]
    ends = np.where(edges == -1)[0]                       # exclusive
    bounded = (starts > 0) & (ends < n)
    starts, ends = starts[bounded], ends[bounded]
    short = (along[ends] - along[starts - 1]) <= RETURN_ALONG_M
    starts, ends = starts[short], ends[short]
    if len(starts) == 0:
        return np.zeros(0)
    lengths = ends - starts
    run = np.repeat(np.arange(len(starts)), lengths)
    offsets = np.repeat(starts - np.concatenate(([0], np.cumsum(lengths)[:-1])),
                        lengths)
    points = offsets + np.arange(lengths.sum())
    dist, _ = cKDTree(P[core]).query(P[points])
    depth = np.zeros(len(starts))
    np.maximum.at(depth, run, dist)
    return depth


def _pieces(shape, min_area_ha):
    if shape is None or shape.is_empty:
        return []
    return [g for g in getattr(shape, "geoms", [shape])
            if g.geom_type == "Polygon" and g.area / 10000.0 >= min_area_ha]


def analyse(points_xy, today, alpha, min_area_ha=MIN_WORK_AREA_HA):
    """What the rule decides from: the core, and per site its scales.

    `points_xy` -- the work-window points in time order, UTM metres;
    `today` -- today's alpha shape of those points; `alpha` -- today's alpha.
    Returns a dict with the dense track and a list of sites, each with the
    indices of its input points, its core mask and, when it has a core,
    g, d, T, S and the number of turns measured.
    """
    xy = np.asarray(points_xy, dtype=float)
    reach = 2.0 * alpha
    P, along, T, ok, raw = _dense_track(xy)
    n = len(P)
    tree = cKDTree(P)
    # [REASON]: speed only, the result is the same. A point with a pass
    # beside it on both sides within the smallest reach the engine ever has
    # (2 x the 10 m alpha floor) gains nothing from looking farther; only the
    # others look out to 2 x alpha.
    first = min(reach, 2.0 * ALPHA_M)
    X, _, S = _beside_pairs(P, along, T, ok, first, tree, np.arange(n))
    left = np.zeros(n, bool)
    right = np.zeros(n, bool)
    left[X[S > 0]] = True
    right[X[S < 0]] = True
    if reach > first:
        rest = np.where(~(left & right) & ok)[0]
        X2, _, S2 = _beside_pairs(P, along, T, ok, reach, tree, rest)
        left[X2[S2 > 0]] = True
        right[X2[S2 < 0]] = True
        X, S = np.concatenate([X, X2]), np.concatenate([S, S2])
    core = left & right
    step = np.full(n, np.inf)
    if len(X):
        np.minimum.at(step, X, np.abs(S))
    sites = []
    for piece in _pieces(today, min_area_ha):
        zone = piece.buffer(ON_SITE_M)
        mine = np.nonzero(shapely.contains_xy(zone, xy[:, 0], xy[:, 1]))[0]
        site_core = core & shapely.contains_xy(zone, P[:, 0], P[:, 1])
        site = {"area_ha": piece.area / 10000.0, "points": mine,
                "core": site_core, "core_points": int(site_core.sum())}
        if site_core.any():
            g = float(np.median(step[site_core]))
            depth = _turn_depths(P, along, site_core)
            d = float(np.percentile(depth, TURN_QUANTILE)) if len(depth) else 0.0
            site.update(g=g, d=d, turns=int(len(depth)),
                        T=max(DENSIFY_STEP_M, ALPHA_SPACING_FACTOR * g / 2.0,
                              ALPHA_SPACING_FACTOR * d),
                        S=ALPHA_SPACING_FACTOR * g + DENSIFY_STEP_M)
        sites.append(site)
    return {"P": P, "T": T, "raw": raw, "reach": reach, "sites": sites}


def keep_mask(points_xy, today, alpha, min_area_ha=MIN_WORK_AREA_HA, info=None):
    """Which of the work-window points stay. Only removes.

    A point outside every site of today's shape stays: it adds nothing to a
    site once the result is intersected with today's shape.
    """
    xy = np.asarray(points_xy, dtype=float)
    out = np.ones(len(xy), bool)
    if len(xy) < 4 or today is None or today.is_empty:
        return out
    info = info or analyse(xy, today, alpha, min_area_ha)
    P = info["P"]
    for site in info["sites"]:
        mine = site["points"]
        if not site["core"].any() or len(mine) == 0:
            # [REASON]: no pass lies beside another anywhere on the site, so
            # the rule has no evidence of a field there to trim around. The
            # judges found what deleting such sites costs -- strips of two to
            # four passes, small jobs, fields with too few passes to have an
            # inside -- and the rule leaves them exactly as they are today.
            continue
        cidx, D = _pass_direction(P, info["T"], site["core"], info["reach"])
        sub = xy[mine]
        pairs = cKDTree(sub).sparse_distance_matrix(
            cKDTree(P[cidx]), max(site["T"], site["S"]), output_type="coo_matrix")
        r, c = pairs.row, pairs.col
        dv = sub[r] - P[cidx[c]]
        along = dv[:, 0] * D[c, 0] + dv[:, 1] * D[c, 1]
        across = dv[:, 0] * D[c, 1] - dv[:, 1] * D[c, 0]
        inside = (along / site["T"]) ** 2 + (across / site["S"]) ** 2 <= 1.0
        kept = np.zeros(len(mine), bool)
        kept[r[inside]] = True
        kept |= site["core"][info["raw"][mine]]       # a core point always stays
        out[mine] &= kept
    return out


def _polygonal(geom):
    if geom is None or geom.is_empty:
        return None
    parts = []
    for g in getattr(geom, "geoms", [geom]):
        if g.geom_type == "Polygon":
            parts.append(g)
        elif g.geom_type in ("MultiPolygon", "GeometryCollection"):
            parts.extend(p for p in getattr(g, "geoms", [])
                         if p.geom_type == "Polygon")
    return unary_union(parts) if parts else None


def shape_of_kept(points_xy, keep, alpha):
    """The engine's shape of the kept points: consecutive kept points are
    densified together, a dropped point breaks the line."""
    dense, run = [], []
    for point, kept in zip(points_xy, keep):
        if kept:
            run.append(point)
        elif run:
            dense.extend(densify(run))
            run = []
    if run:
        dense.extend(densify(run))
    return alpha_shape(dense, alpha) if len(dense) >= 4 else None


def trim(points_xy, today, alpha, min_area_ha=MIN_WORK_AREA_HA):
    """Today's shape with the driving beside the fields taken out.

    Returns (shape, info): `shape` is a polygonal geometry inside `today`
    (or None), `info` the evidence from `analyse` plus the keep mask.
    """
    if today is None or today.is_empty or len(points_xy) < 4:
        return today, None
    info = analyse(points_xy, today, alpha, min_area_ha)
    keep = keep_mask(points_xy, today, alpha, min_area_ha, info=info)
    info["keep"] = keep
    if keep.all():
        return today, info
    rebuilt = shape_of_kept(points_xy, keep, alpha)
    # [REASON]: rebuilding from fewer points can create a triangle today's
    # shape did not have (a few square metres on about a third of the bench
    # days); the intersection makes "only removes" hold by construction.
    shape = None if rebuilt is None else _polygonal(rebuilt.intersection(today))
    # [REASON]: the rule trims sites, it never deletes one. A site whose
    # trimmed remainder has no piece of work size left is put back as it is
    # today: a few points that look like an inside of a field (GPS scatter on
    # a strip of four plough passes, on a road driven many times) are no
    # evidence of a field to trim around, and losing a whole site of real work
    # costs more than leaving a road the operators already mark as passage.
    restored = []
    for piece in _pieces(today, min_area_ha):
        rest = None if shape is None else _polygonal(shape.intersection(piece))
        if not _pieces(rest, min_area_ha):
            restored.append(piece)
    info["restored"] = len(restored)
    if restored:
        shape = unary_union(([shape] if shape is not None else []) + restored)
    return shape, info
