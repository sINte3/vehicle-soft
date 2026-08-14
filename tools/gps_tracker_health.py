# -*- coding: utf-8 -*-
"""Tracker health report -- find the machines whose GPS makes work unmeasurable.

Written on the owner's request of 2026-08-14: "better still, spot such tracker
anomalies in time, find the cause and fix it". The point is not to flag a
number after the fact but to hand a mechanic a list of machines to visit.

WHAT THE DIAGNOSIS ACTUALLY TURNED OUT TO BE
The owner called MTZ 572 HA on 19.06 hell to measure by hand. The first guess
-- the tracker "shooting stars", i.e. reporting positions the machine never
occupied -- did NOT survive the data:

  * satellites 9-10 all day (the healthy machines sit at 15-19);
  * the tracker wrote a point every 2 SECONDS (healthy ones: 7-11 s),
    12 626 points in one day against 1 400-1 750;
  * of 295 transitions that looked impossible, only 2 were real position
    jumps. The other 293 were pairs of messages one second apart -- at
    9 km/h a machine covers 2.5 m per second, so a 30 m step "in 1 s" is a
    timestamp artefact, not a teleport.

So the day is hard to measure for a different reason than assumed: a very
dense cloud of individually imprecise points (few satellites) does not form
clean passes, and neither the eye nor an alpha shape can trace an edge
through it. The same check cleared MTZ 873 GA, which the owner said looked
fine visually and which the earlier, wrong metric had flagged worse than the
"hell" machine: all of its flagged transitions are one second apart.

Hence four independent indicators, each measured, none of them a verdict:

  interval_s     median seconds between messages -- how dense the log is
  sats_median    median satellites -- how precise each point is
  jumps          transitions at least MIN_JUMP_SECONDS apart that still
                 demand a speed far above the one the tracker reports; this
                 is the honest "shooting star" count
  motion_gaps    the tracker went silent WHILE MOVING (see gps/area.py)

Thresholds live here as REPORTING defaults only. What makes a machine worth
a mechanic's visit is the owner's call.

Input: any tracks CSV produced by the probes (unit_id;date;time;lat;lon;
speed;course;sats) and/or Wialon .wln message exports.

Run (PowerShell, one line at a time):
  cd C:\\diag\\wialon
  & C:\\gps_venv\\Scripts\\python.exe C:\\transport-report\\tools\\gps_tracker_health.py --csv verify2_tracks.csv --wln .

Console output is ASCII; the full table goes to gps_tracker_health.csv.
"""

import argparse
import csv
import glob
import math
import os
import re
import sys
from collections import defaultdict

# [REASON]: a jump is only credible when the two messages are far enough
# apart in time that the distance really is impossible. Below this, a large
# step is a timestamp artefact: the export puts two records in the same
# second and the implied speed explodes. Using 1 s transitions counted 295
# "stars" on a machine that had 2.
MIN_JUMP_SECONDS = 5.0
JUMP_MARGIN_KMH = 40.0

# Reporting defaults, not business rules.
WARN_SATS = 12.0
WARN_JUMPS = 3
# [REASON]: a machine that barely moved says nothing about its tracker. On the
# fleet run of 13.08 only 193 of 481 objects produced 200 points or more; the
# rest were parked, and flagging them buried the real list.
MIN_POINTS_TO_JUDGE = 200

EARTH = 6378137.0


def metres(lon1, lat1, lon2, lat2):
    """Local flat approximation -- plenty for a distance of a few hundred m."""
    mean_lat = math.radians((lat1 + lat2) / 2.0)
    dx = math.radians(lon2 - lon1) * EARTH * math.cos(mean_lat)
    dy = math.radians(lat2 - lat1) * EARTH
    return math.hypot(dx, dy)


def median(values):
    if not values:
        return 0.0
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return float(ordered[middle])
    return (ordered[middle - 1] + ordered[middle]) / 2.0


def read_csv_tracks(path):
    """(machine, day) -> [(t, lon, lat, speed, sats)]"""
    out = defaultdict(list)
    with open(path, encoding="utf-8-sig") as fh:
        for row in csv.DictReader(fh, delimiter=";"):
            try:
                hh, mm, ss = row["time"].split(":")
                out[(row["unit_id"], row["date"])].append(
                    (int(hh) * 3600 + int(mm) * 60 + int(ss),
                     float(row["lon"]), float(row["lat"]),
                     float(row["speed"]), int(row.get("sats") or -1)))
            except (KeyError, ValueError):
                continue
    return out


def read_wln(path):
    """Wialon message export: REG;time;lon;lat;speed;course;...SATS:n..."""
    rows = []
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
            # [REASON]: 0/0 is not the Gulf of Guinea, it is a message with no
            # GPS fix at all. Counting it as a position would invent a journey.
            if lon == 0.0 and lat == 0.0:
                continue
            found = re.search(r"SATS:(\d+)", line)
            rows.append((stamp, lon, lat, speed,
                         int(found.group(1)) if found else -1))
    rows.sort()
    return rows


def examine(points):
    """Four indicators for one machine-day."""
    points = sorted(points)
    intervals, jumps, gaps, sats = [], 0, 0, []
    for (t1, lon1, lat1, sp1, s1), (t2, lon2, lat2, sp2, s2) in zip(points, points[1:]):
        delta = t2 - t1
        intervals.append(delta)
        if s1 >= 0:
            sats.append(s1)
        if delta >= MIN_JUMP_SECONDS:
            implied = metres(lon1, lat1, lon2, lat2) / delta * 3.6
            if implied > max(sp1, sp2) + JUMP_MARGIN_KMH:
                jumps += 1
        # silence while moving -- same definition as gps/area.py
        if delta > 300 and sp1 >= 1.0:
            gaps += 1
    return {"points": len(points), "interval_s": median(intervals),
            "sats_median": median(sats) if sats else -1.0,
            "jumps": jumps, "motion_gaps": gaps}


def verdict(row):
    # See reasons_for() in wialon_probe6_fleet_health.py for why dense logging
    # is not a fault on its own.
    if row["points"] < MIN_POINTS_TO_JUDGE:
        return "too little data to judge"
    reasons = []
    if 0 <= row["sats_median"] < WARN_SATS:
        reasons.append("few satellites")
    if row["interval_s"] and row["interval_s"] < WARN_INTERVAL_S:
        reasons.append("logging too dense")
    if row["jumps"] >= WARN_JUMPS:
        reasons.append("position jumps")
    if row["motion_gaps"]:
        reasons.append("silent while moving")
    return ", ".join(reasons)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--csv", action="append", default=[],
                        help="tracks csv from the probes; may repeat")
    parser.add_argument("--wln", action="append", default=[],
                        help="folder with .wln exports; may repeat")
    parser.add_argument("--out", default="gps_tracker_health.csv")
    args = parser.parse_args()

    machine_days = {}
    for path in args.csv:
        for key, points in read_csv_tracks(path).items():
            machine_days[key] = points
    for folder in args.wln:
        for path in sorted(glob.glob(os.path.join(folder, "*.wln"))):
            points = read_wln(path)
            if points:
                name = os.path.splitext(os.path.basename(path))[0]
                machine_days[(name, "")] = points

    if not machine_days:
        print("no input: pass --csv and/or --wln")
        return 2

    rows = []
    for (machine, day), points in machine_days.items():
        if len(points) < 2:
            continue
        row = examine(points)
        row.update(machine=machine, day=day, reasons=verdict(row))
        rows.append(row)
    rows.sort(key=lambda r: (not r["reasons"], r["sats_median"]))

    with open(args.out, "w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.writer(fh, delimiter=";")
        writer.writerow(["machine", "day", "points", "interval_s",
                         "sats_median", "jumps", "motion_gaps", "reasons"])
        for r in rows:
            writer.writerow([r["machine"], r["day"], r["points"],
                             "%.1f" % r["interval_s"], "%.1f" % r["sats_median"],
                             r["jumps"], r["motion_gaps"], r["reasons"]])

    flagged = [r for r in rows if r["reasons"]]
    print("machine-days examined: %d, flagged: %d" % (len(rows), len(flagged)))
    print("%-22s %-11s %7s %9s %6s %6s %6s  %s"
          % ("machine", "day", "points", "interval", "sats", "jumps", "gaps",
             "reasons"))
    for r in rows:
        print("%-22s %-11s %7d %8.1fs %6.1f %6d %6d  %s"
              % (r["machine"][:22], r["day"], r["points"], r["interval_s"],
                 r["sats_median"], r["jumps"], r["motion_gaps"], r["reasons"]))
    print("written: %s" % args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
