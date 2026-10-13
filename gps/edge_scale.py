# -*- coding: utf-8 -*-
"""GPS EDGE, ROUND 2 -- edge driving measured against the site's own pass step.

REJECTED by the owner's review of 13.10.2026 (roadmap 2.13, condition 2): on
three sites of cotton pickers -- two John Deere 9970 and a CASE CE 420 -- the
owner marked removed ground as worked that day, and each site lost more than
the B-2 tolerance. Those pieces hold no track point at all, so no rule over the
track can tell such work from empty ground. Conditions 1 and 3 passed (V-6:
median |deviation| 23.69 % -> 18.85 %). The owner closed the topic the same
day. The switch stays off; this module stays so that the run can be repeated.

A CANDIDATE RULE behind a switch (`gps.area.work_sites(..., edge_rule=
EDGE_SCALE_VERSION)`); the nightly computation does not ask for it, so the
method in force (A7) is unchanged until the owner adopts it. Pre-registration:
docs/GPS_PLAN_FAKT_VISION_ROADMAP.md, section 2.13.

THE OWNER'S DECISION, 03.10.2026: driving in the work speed window near the
edge of a field -- entries, exits, runs along the headland, roads to and from
the field -- is not work, "не считать".

ROUND 1 (gps/edge.py, "core and margin") was rejected by the data run of
10-11.10.2026 (roadmap 2.12, condition 4). It KEPT only the ground it
recognised as field -- a whitelist -- and cut whatever field it failed to
recognise: sprayers whose trackers write only near the ends of a pass, passes
driven in pairs, the uneven outer passes of cultivation. Round 2 inverts the
burden of proof: it removes only ground it can positively show is not between
neighbouring passes, and leaves alone everything it cannot classify.

HOW IT WAS CHOSEN (roadmap 2.13): four families built independently on a bench
of real days (the V-5 days, the operators' answers of 08.10, twenty September
days, New Holland 01.10) and synthetic days; three judges on data the builders
never saw (the 17 hand-measured contours of 27.07, the 15 spraying days of
12.08, 131 hidden synthetic days, 288 days built to break the rules) and on
stability (noise, knife edges, runtime). The judges' amendments are in.

THE RULE IN THREE SENTENCES (for the owner)
A7 glues together any two traversals up to about 20 m apart, because its
alpha is never below 10 m; on ploughing and cultivation the passes lie 1-9 m
apart, so the glue also catches roads and runs beside the field. On each site
whose own pass step is soil-work scale, the rule removes only an EMPTY GAP at
the edge wider than neighbouring passes of that site can leave, and a NARROW
STRIP along which the machine drove for 150 m or more without laying passes
side by side -- a road driven there and back, a lap round a block. Sites
where the passes are as wide as the alpha floor (spraying) are left exactly as
A7 has them.

THE RULE, on the work-window points in time order (positions and order only:
neither the timestamps nor the reported speeds are used, so a KML day and a
tracker day are treated alike):

 0. The engine's own triangles: the densified track (5 m, segments under
    150 m), its Delaunay triangulation, the triangles with circumradius
    < alpha -- exactly A7's shape, triangle by triangle.
 1. LOCAL STEP, per site of today's shape (piece >= the work floor): the
    engine's own estimator (`pass_votes`) over the site's points;
      g     = max(median vote, the day's spacing)
      reach = max(ALPHA_M, 2 x 1.2 x g)
    The site is left exactly as it is when 1.2 x g >= ALPHA_M (A7's alpha is
    set by the step, not by the floor), or when the 75th percentile of its
    votes >= ALPHA_M (wide-pass work in it), or when no step can be measured.
 2. EMPTY GAPS AT THE EDGE. A triangle "spans" the gap between the traversals
    it joins (its height over its shortest side). Triangles spanning more than
    the reach, connected to each other, are one gap. A gap is removed when it
    opens to the outside of the shape AND no track runs inside it: no point of
    the densified track is surrounded by gap triangles only.
 3. NARROW STRIPS. On what is left, the parts of each active site narrower
    than ALPHA_M (an opening with a 5 m disc) are strips. A strip is removed
    when most of the track inside it (by length) belongs to stretches of at
    least RETURN_ALONG_M driven without leaving the strip -- unless three or
    more such traversals (a stretch that turns back counts once per leg) lie
    side by side with the outermost two more than one densify step apart
    across the strip: those are passes, not a road.
 4. The result is today's shape minus the removed ground -- it only removes --
    as flat polygons. The engine then counts the pieces of at least the work
    floor, as it does today; a site disappears only when all of it was removed
    or what remains is below the floor.

KNOWN LIMITS, declared in the pre-registration:
  * a detached strip of three plough passes (4.6 m across) or a 2-pass tongue
    driven along for 150 m or more is taken for a road and removed -- it is
    geometrically the same as a road driven out and back or three times;
  * a road driven three or more times over more than 5 m of width is kept;
    so is a yard, and a road loop on a site with wide-pass votes;
  * spraying is left as A7 has it: there A7 is already short of the field
    (it stops at the outermost pass centrelines and misses sparse passes), and
    removing the roads glued to it would push the day further from the hand
    measurement -- a separate step of the method, not this rule;
  * a soil machine whose tracker is silent along its passes (90-120 s between
    messages, 150 m and more) leaves undensified passes with no points in
    the gaps between them, and a band between two such passes at the edge of
    the field can be taken for an empty gap. Seen only on synthetic days; on
    the real sparse-tracker days the long segments lie on roads.

This module decides nothing about billing.
"""

import numpy as np
import shapely
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components
from scipy.spatial import Delaunay, cKDTree
from shapely.geometry import MultiPolygon
from shapely.ops import unary_union

from gps.area import (ALPHA_M, ALPHA_SPACING_FACTOR, DENSIFY_MAX_SEG_M,
                      DENSIFY_STEP_M, EDGE_SCALE_VERSION, MIN_WORK_AREA_HA,
                      RETURN_ALONG_M, pass_votes)

__all__ = ["EDGE_SCALE_VERSION", "site_scales", "trim"]

# [REASON]: the reach of a site, max(ALPHA_M, 2 x 1.2 x g). A7 with alpha =
# 1.2 x g (its own pre-registered margin) joins two traversals up to about
# twice its alpha apart, so 2 x 1.2 x g is the widest gap the engine itself
# accepts between neighbouring passes at step g; it protects the uneven outer
# passes of cultivation (up to 1.6 g, "3309 Маруф"). The floor ALPHA_M: soil
# work never has a step above ~9 m, so an empty gap wider than the alpha floor
# at the edge of a soil-work site is not between neighbouring passes.
REACH_FACTOR = 2.0 * ALPHA_SPACING_FACTOR

# [REASON]: wide-pass work. Soil work never has a step of ALPHA_M (plough 1-5,
# cultivation 5-9 m; spraying 10-26 m). A road retraced many times beside a
# sprayer field pulls the MEDIAN vote of the site down (its retraces vote
# 2-8 m) -- on held-out spraying days (12.08: 5 of 15; 27.07: units 957 and
# 1106) it fell below the switch of step 1 -- but it cannot pull the UPPER
# QUARTILE of a sprayer field below the field's own step. The quartile, not a
# higher percentile: the votes across the gaps and roads of a soil site must
# not switch the site off -- the 90th switches ploughed fields themselves off
# (V-5 ploughing back to A7's 56.7 %). Nor a lower one: the 60th gives the same
# development numbers, but on the judges' sets it cuts sprayed ground (12.08:
# 0.71 % removed against 0.12 %; the attack days lose 7.3 ha against 4.4).
# Roadmap 2.13, sensitivity.
WIDE_WORK_PERCENTILE = 75.0

# [REASON]: a strip is the part of a site narrower than the alpha floor: what a
# disc of diameter ALPHA_M cannot enter. Roads driven out and back lie 2-8 m
# apart (New Holland 01.10: 2.5 and 4.2 m), a road driven several times
# spreads over its width plus GPS error -- inside 10 m; a strip that narrow
# holds at most one step of the widest soil work.
STRIP_HALF_WIDTH_M = ALPHA_M / 2.0

# [REASON]: a strip is a road when MOST of the track inside it (by length)
# belongs to stretches of RETURN_ALONG_M driven without leaving it -- the
# engine's "far longer than any turn at the headland", so a turn bulging out
# of a field never qualifies. The majority is the one share that favours
# neither answer; on the bench the share is bimodal (0 or above 0.9), and 0.3 /
# 0.7 give the same development numbers (roadmap 2.13, sensitivity).
STRIP_ALONG_SHARE = 0.5

# [REASON]: passes side by side. A road driven again stays inside its lane:
# the New Holland retraces of 01.10 lie 2.5-4.2 m apart, inside one densify
# step. Three or more long traversals of a strip whose outermost two run
# beside each other for RETURN_ALONG_M farther apart than that ACROSS the
# strip are passes laid side by side -- real work -- and the strip stays. Two
# traversals are out and back and prove nothing; three are the fewest that can
# show a sideways progression. Known cost: a road driven four times over 13 m
# of width (V-5 row 6) is kept too -- the acceptance condition punishes cut
# work far more than edge driving left in.
PASSES_MIN_TRAVERSALS = 3
LANE_SPREAD_M = DENSIFY_STEP_M

# [REASON]: a stretch that turns back inside the strip is several traversals.
# Three passes worked in one visit are ONE unbroken stretch inside the strip
# (the recorded turns are chords inside it), and without this split they were
# counted once and removed whole. "Turns back": the 20 m chords before and
# after a point -- one tracker step, ~20 m along a pass on this fleet -- make
# more than 120 degrees. A U-turn between passes up to 10 m apart (the widest
# step the rule acts on) reads 152-180 degrees on such chords, a bend of a road
# 90 degrees or less; 120 is halfway between.
TURN_CHORD_STEPS = 4                      # x DENSIFY_STEP_M = 20 m
TURN_BACK_COS = -0.5                      # 120 degrees

# [REASON]: the outermost points of a strip ARE its boundary (the shape is
# made of the track); a hair of tolerance puts them inside.
ON_EDGE_M = 0.5

# [REASON]: deliberately absent -- a test that would keep an "empty" gap when
# a long undensified segment (the tracker silent for 150 m or more) runs
# through it. It was built and judged: it changed nothing on any real or bench
# day (317 days), it was the only part of the rule that read timestamps, and
# it caused the largest flicker found -- one dropped message on V-5 row 5 made
# a 182 m chord "track" and kept a 0.62 ha strip (+1.6 % -> +91.6 %). Its
# purpose is a declared limit instead (the module docstring).


# --- the engine's triangles --------------------------------------------------------

def _dense(xy):
    """The engine's densified track (`gps.area.densify`, vectorised, the same
    point list) with the distance driven to each point and the id of the
    unbroken stretch it lies on."""
    n = len(xy)
    seg = np.diff(xy, axis=0)
    length = np.hypot(seg[:, 0], seg[:, 1])
    along_raw = np.concatenate(([0.0], np.cumsum(length)))
    filled = (length > 0) & (length < DENSIFY_MAX_SEG_M)
    count = np.where(filled, np.floor(length / DENSIFY_STEP_M), 0).astype(int)
    # the engine: k = 1 .. floor(L / 5) while k * 5 / L < 1
    count -= (filled & (np.abs(count * DENSIFY_STEP_M - length) < 1e-12)).astype(int)
    count = np.maximum(count, 0)
    k = np.repeat(np.arange(n - 1), count)
    first = np.concatenate(([0], np.cumsum(count)[:-1]))
    m = np.arange(len(k)) - np.repeat(first, count) + 1
    f = m * DENSIFY_STEP_M / np.where(length[k] > 0, length[k], 1.0)
    order = np.argsort(np.concatenate([np.arange(n, dtype=float), k + f * 0.999999]),
                       kind="stable")
    P = np.concatenate([xy, xy[k] + f[:, None] * seg[k]])[order]
    along = np.concatenate([along_raw, along_raw[k] + f * length[k]])[order]
    raw = np.concatenate([np.arange(n), k])[order]
    # [REASON]: only a segment of DENSIFY_MAX_SEG_M or more is a track gap and
    # breaks a stretch. A zero-length segment (the tracker wrote the same
    # position twice: 0.34 % of real segments, up to 9.3 % on some days) is
    # not densified either, but it is no gap -- treated as one, doubling every
    # point switched the strip test off.
    stretch = np.concatenate(([0], np.cumsum(length >= DENSIFY_MAX_SEG_M)))[raw]
    return P, along, stretch


def _triangles(P, alpha):
    """Delaunay of the dense points; A7's triangles (circumradius < alpha) and
    the gap each triangle spans (its height over its shortest side)."""
    tri = Delaunay(P)
    S = tri.simplices
    a, b, c = P[S[:, 0]], P[S[:, 1]], P[S[:, 2]]
    la = np.linalg.norm(b - c, axis=1)
    lb = np.linalg.norm(a - c, axis=1)
    lc = np.linalg.norm(a - b, axis=1)
    s = (la + lb + lc) / 2.0
    area = np.sqrt(np.maximum(s * (s - la) * (s - lb) * (s - lc), 1e-12))
    kept = la * lb * lc / (4.0 * area) < alpha
    span = 2.0 * area / np.maximum(np.minimum(np.minimum(la, lb), lc), 1e-9)
    return tri, kept, span


def _components(nbr, mask):
    """Connected components of the triangles in `mask` (shared edges)."""
    rows, cols = [], []
    for e in range(3):
        j = nbr[:, e]
        ok = mask & (j >= 0)
        ok[ok] = mask[j[ok]]
        rows.append(np.nonzero(ok)[0])
        cols.append(j[ok])
    rows, cols = np.concatenate(rows), np.concatenate(cols)
    graph = coo_matrix((np.ones(len(rows)), (rows, cols)),
                       shape=(len(mask), len(mask)))
    return connected_components(graph, directed=False)[1]


def _faces_outside(nbr, kept):
    """For each kept triangle: does one of its edges face the OUTSIDE of the
    shape (the hull exterior or empty ground connected to it), as opposed to a
    hole inside a site."""
    empty = ~kept
    lab = _components(nbr, empty)
    on_hull = empty & (nbr == -1).any(axis=1)
    outside_lab = np.zeros(lab.max() + 1, bool)
    outside_lab[lab[on_hull]] = True
    outside = empty & outside_lab[lab]
    j = np.maximum(nbr, 0)
    return kept & ((nbr == -1) | outside[j]).any(axis=1)


def _polys(geom):
    """Every Polygon inside `geom`, however deeply nested."""
    if geom is None or geom.is_empty:
        return []
    if geom.geom_type == "Polygon":
        return [geom]
    out = []
    for g in getattr(geom, "geoms", []):
        out.extend(_polys(g))
    return out


def _flat(geom):
    """Polygons only, one level: a Polygon, a MultiPolygon or None.

    [REASON]: `gps.area.work_sites` iterates the shape's parts and counts each
    as a site; a MultiPolygon nested in a GeometryCollection (what make_valid
    or a difference can return) would count as ONE site, and a stray line
    would break the area sum.
    """
    parts = _polys(geom)
    if not parts:
        return None
    if len(parts) == 1:
        return parts[0]
    flat = MultiPolygon(parts)
    if not flat.is_valid:
        parts = _polys(shapely.union_all(parts))
        flat = parts[0] if len(parts) == 1 else MultiPolygon(parts)
    return flat


def _tri_union(P, S):
    coords = np.stack([P[S[:, 0]], P[S[:, 1]], P[S[:, 2]], P[S[:, 0]]], axis=1)
    return shapely.union_all(shapely.polygons(coords))


# --- step 1: the local step of each site ---------------------------------------------

def site_scales(points_xy, today, day_spacing, min_area_ha=MIN_WORK_AREA_HA):
    """Every site of `today` with its step, and whether the rule acts on it.

    Returns [dict site, zone, area_m2, votes, median, p75, g, reach, on, off]
    -- `site` is the site's number as the engine numbers them (by area,
    largest first), `off` names the reason a site is left alone (None when the
    rule acts).
    """
    xy = np.asarray(points_xy, dtype=float)
    pieces = [p for p in _polys(today) if p.area / 1e4 >= min_area_ha]
    rank = {id(p): n for n, p in enumerate(sorted(pieces, key=lambda p: -p.area), 1)}
    out = []
    for site in pieces:
        zone = site.buffer(0.01)
        inside = shapely.contains_xy(zone, xy[:, 0], xy[:, 1])
        votes = pass_votes([tuple(p) for p in xy[inside]])
        median = float(np.median(votes)) if votes else None
        p75 = float(np.percentile(votes, WIDE_WORK_PERCENTILE)) if votes else None
        g = max(median or 0.0, day_spacing or 0.0)
        info = {"site": rank[id(site)], "zone": zone, "area_m2": site.area,
                "votes": len(votes),
                "median": median, "p75": p75, "g": g,
                "reach": max(ALPHA_M, REACH_FACTOR * g), "on": True, "off": None}
        if median is None and day_spacing is None:
            # [REASON]: no step can be measured at all -- too few points for
            # the estimator and none for the day: there is no evidence of
            # soil-scale work to measure the edge against (on an 11-point day a
            # two-pass sprayer strip was deleted without this).
            info.update(on=False, off="no step")
        elif ALPHA_SPACING_FACTOR * g >= ALPHA_M:
            # [REASON]: here A7's alpha is the site's own step with the
            # engine's margin -- nothing is held only by the floor. It is
            # spraying, where A7 is often already short and a road is what
            # keeps a day in the green band; it is also what leaves the
            # road-only sites of a sprayer day alone (their own votes are road
            # votes, the day's spacing is the sprayer's).
            info.update(on=False, off="step sets alpha")
        elif p75 is not None and p75 >= ALPHA_M:
            info.update(on=False, off="wide-pass work")
        out.append(info)
    return out


# --- step 2: empty gaps at the edge --------------------------------------------------

def _empty_gaps(P, tri, kept, span, sites):
    """[(triangle indices, site index)] of the gaps wider than their site's
    reach that open to the outside and hold no track."""
    S, nbr = tri.simplices, tri.neighbors
    reach = np.full(len(S), np.inf)
    owner_site = np.full(len(S), -1)
    centre = P[S].mean(axis=1)
    for i, s in enumerate(sites):
        if not s["on"]:
            continue
        here = kept & shapely.contains_xy(s["zone"], centre[:, 0], centre[:, 1])
        reach[here] = s["reach"]
        owner_site[here] = i
    wide = kept & (span > reach)
    if not wide.any():
        return []
    lab = _components(nbr, wide)
    opens = np.zeros(lab.max() + 1, bool)
    opens[lab[wide & _faces_outside(nbr, kept)]] = True
    # a point of the track inside the gap: every triangle around it is gap
    around = np.bincount(S.ravel(), minlength=len(P))
    in_gap = np.bincount(S[wide].ravel(), minlength=len(P))
    hull = np.zeros(len(P), bool)
    hull[tri.convex_hull.ravel()] = True
    inner = (in_gap == around) & (around > 0) & ~hull
    owner = np.full(len(P), -1)
    w = np.nonzero(wide)[0]
    for e in range(3):
        owner[S[w, e]] = lab[w]
    holds_track = np.bincount(owner[inner], minlength=len(opens)) > 0
    out = []
    for c in np.nonzero(opens & ~holds_track)[0]:
        members = np.nonzero(wide & (lab == c))[0]
        out.append((members, int(owner_site[members[0]])))
    return out


# --- step 3: narrow strips driven along ----------------------------------------------

def _split_at_turns(P, idx, first, last):
    """Every stretch [first, last] (positions in idx) split where the track
    turns back (see TURN_BACK_COS), at the most reversed point of each turn."""
    half = TURN_CHORD_STEPS
    nf, nl = [], []
    for a, b in zip(first, last):
        cut = [a]
        if b - a > 2 * half + 2:
            pts = P[idx[a:b + 1]]
            i = np.arange(half, len(pts) - half)
            v1 = pts[i] - pts[i - half]
            v2 = pts[i + half] - pts[i]
            n1 = np.hypot(v1[:, 0], v1[:, 1])
            n2 = np.hypot(v2[:, 0], v2[:, 1])
            ok = (n1 > 1e-6) & (n2 > 1e-6)
            c = np.ones(len(i))
            c[ok] = (v1[ok] * v2[ok]).sum(axis=1) / (n1[ok] * n2[ok])
            rev = c < TURN_BACK_COS
            j = 0
            while j < len(rev):
                if rev[j]:
                    k = j
                    while k + 1 < len(rev) and rev[k + 1]:
                        k += 1
                    cut.append(a + int(i[j + int(np.argmin(c[j:k + 1]))]))
                    j = k + 1
                else:
                    j += 1
        cut.append(b)
        for u, w in zip(cut[:-1], cut[1:]):
            if w > u:
                nf.append(u)
                nl.append(w)
    return np.asarray(nf, int), np.asarray(nl, int)


def _side_by_side(P, along, idx, first, last, driven):
    """Three or more long traversals whose outermost two run beside each other
    farther apart than one lane: passes, not a road."""
    long_runs = [(idx[a], idx[b]) for a, b, d in zip(first, last, driven)
                 if d >= RETURN_ALONG_M]
    if len(long_runs) < PASSES_MIN_TRAVERSALS:
        return False
    runs = [P[a:b + 1] for a, b in long_runs]
    steps = [np.diff(along[a:b + 1]) for a, b in long_runs]
    trees = [cKDTree(r) for r in runs]
    # the separation of the outermost two: the largest typical (median)
    # distance across the strip between two traversals that run beside each
    # other for RETURN_ALONG_M or more -- measured only where they lie side by
    # side, never along a long or branched strip
    spread = 0.0
    for i, tree in enumerate(trees):
        for j, run in enumerate(runs):
            if i == j:
                continue
            dist = tree.query(run)[0]
            beside = dist <= 2.0 * STRIP_HALF_WIDTH_M
            if steps[j][beside[:-1] & beside[1:]].sum() < RETURN_ALONG_M:
                continue
            spread = max(spread, float(np.median(dist[beside])))
    return spread > LANE_SPREAD_M


def _owner(piece, sites):
    """The index of the site of today a piece of the trimmed shape lies in, or
    None for a piece below the work floor.

    [REASON]: every piece of the shape lies inside exactly one piece of today,
    but two sites of today may touch at a point, and a site's zone (0.01 m
    around it) then also meets its neighbour. Asked "which zones meet this
    piece", a narrow road of a site the rule leaves alone (spraying) touching a
    soil site at one point was judged under the soil site and could go -- the
    one promise the rule makes to spraying. A point strictly inside the piece
    lies in its own site's zone; where two zones hold it, the one holding more
    of the piece is its own.
    """
    probe = piece.representative_point()
    hits = [i for i, s in enumerate(sites) if s["zone"].contains(probe)]
    if len(hits) > 1:
        return max(hits, key=lambda i: sites[i]["zone"].intersection(piece).area)
    return hits[0] if hits else None


def _strips(shape, P, along, stretch, sites):
    """([(part, site index)] removed, [(part, site index)] kept as passes): the
    parts of the active sites narrower than ALPHA_M that the machine drove
    along -- removed unless laid side by side as passes."""
    out, passes = [], []
    for piece in _polys(shape):
        site = _owner(piece, sites)
        if site is None or not sites[site]["on"]:
            continue
        core = piece.buffer(-STRIP_HALF_WIDTH_M).buffer(STRIP_HALF_WIDTH_M)
        for part in _polys(piece.difference(core)):
            # [REASON]: a part that holds a stretch of RETURN_ALONG_M driven
            # along it has a boundary at least that long -- a cheap prefilter.
            if part.length < RETURN_ALONG_M:
                continue
            x0, y0, x1, y1 = part.bounds
            near = np.nonzero((P[:, 0] >= x0 - 1) & (P[:, 0] <= x1 + 1)
                              & (P[:, 1] >= y0 - 1) & (P[:, 1] <= y1 + 1))[0]
            idx = near[shapely.contains_xy(part.buffer(ON_EDGE_M),
                                           P[near, 0], P[near, 1])]
            if len(idx) < 2:
                continue
            brk = np.nonzero((np.diff(idx) > 1) | (np.diff(stretch[idx]) != 0))[0]
            first = np.concatenate(([0], brk + 1))
            last = np.concatenate((brk, [len(idx) - 1]))
            driven = along[idx[last]] - along[idx[first]]
            total = driven.sum()
            if total <= 0 or (driven[driven >= RETURN_ALONG_M].sum()
                              < STRIP_ALONG_SHARE * total):
                continue
            legs_first, legs_last = _split_at_turns(P, idx, first, last)
            legs = along[idx[legs_last]] - along[idx[legs_first]]
            if _side_by_side(P, along, idx, legs_first, legs_last, legs):
                passes.append((part, site))
                continue
            out.append((part, site))
    return out, passes


# --- the rule ------------------------------------------------------------------------

def _piece(kind, geometry, site):
    return {"kind": kind, "site": site["site"], "area_m2": float(geometry.area),
            "polygon": geometry}


def trim(points_xy, today, alpha, spacing, min_area_ha=MIN_WORK_AREA_HA):
    """Today's shape with the edge driving taken out, and the evidence.

    `points_xy` -- the work-window points in time order, UTM metres; `today`
    -- today's alpha shape of those points; `alpha`, `spacing` -- today's
    alpha and the day's pass spacing (None when not measured).

    Returns (shape, evidence): `shape` is a Polygon, a MultiPolygon or None,
    always inside `today`; `evidence` is
      fallback  True when the rule failed and today's shape came back
                unchanged (`error` says why) -- a rule that can only remove
                must never take a day down, and a fallback must be countable;
      sites     per site of today: its number in the engine's order, area,
                votes, median, p75, g, reach, `on` and the `off` reason;
      removed   per removed piece: kind 'gap' or 'strip', the site's number,
                area_m2 and the polygon;
      passes    the narrow strips KEPT because their traversals lie side by
                side (same fields, kind 'passes') -- the decisions that keep
                a road driven several times over a wide lane are visible too.
    What falls below the work floor after the trim is not listed: the caller
    compares the engine's sites before and after.
    """
    evidence = {"fallback": False, "error": None, "sites": [], "removed": [],
                "passes": []}
    xy = np.asarray(points_xy, dtype=float)
    if today is None or today.is_empty or len(xy) < 4:
        return _flat(today), evidence
    try:
        sites = site_scales(xy, today, spacing, min_area_ha)
        evidence["sites"] = sorted(({k: v for k, v in s.items() if k != "zone"}
                                    for s in sites), key=lambda s: s["site"])
        if not any(s["on"] for s in sites):
            return _flat(today), evidence
        P, along, stretch = _dense(xy)
        tri, kept, span = _triangles(P, alpha)
        shape = today
        gaps = _empty_gaps(P, tri, kept, span, sites)
        if gaps:
            cuts = []
            for members, site in gaps:
                cut = _tri_union(P, tri.simplices[members])
                cuts.append(cut)
                evidence["removed"].append(_piece("gap", cut, sites[site]))
            shape = shape.difference(unary_union(cuts))
        strips, passes = _strips(shape, P, along, stretch, sites)
        evidence["passes"] = [_piece("passes", part, sites[site])
                              for part, site in passes]
        if strips:
            for part, site in strips:
                evidence["removed"].append(_piece("strip", part, sites[site]))
            shape = shape.difference(unary_union([p for p, _s in strips]))
        if not shape.is_valid:
            shape = shapely.make_valid(shape)
        return _flat(shape), evidence
    except Exception as exc:                                   # noqa: BLE001
        evidence.update(fallback=True, error="%s: %s" % (type(exc).__name__, exc),
                        removed=[], passes=[])
        return _flat(today), evidence
