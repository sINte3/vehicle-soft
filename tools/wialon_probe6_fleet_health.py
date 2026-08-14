# -*- coding: utf-8 -*-
"""
wialon_probe6_fleet_health.py -- health of every tracker in the fleet, one run.

Answers one question: WHICH MACHINES SHOULD A MECHANIC VISIT. For each object
visible to the token it pulls one day of messages, measures four things and
throws the points away again, so memory stays flat whatever the fleet size.

  sats_median   median satellites. Healthy machines sit at 15-21. Below 12 the
                every point wobbles by metres and the worked area cannot be
                traced -- by the operator's eye either.
  interval_s    median seconds between messages. 7-30 s is normal. Two or
                three seconds is the tracker logging far too often: thousands
                of jittering points that no method can read.
  jumps         positions the machine could not have reached: at least 5 s
                apart, yet demanding a speed 40 km/h above the one the tracker
                itself reports. Real ones are rare -- two in 32 machine-days.
  motion_gaps   the tracker went silent WHILE MOVING. Parking is not counted:
                the machine stops, the engine goes off, the tracker sleeps.

READ-ONLY. Services: token/login, core/search_items, messages/load_interval,
messages/unload, core/logout. No create / update / delete anywhere.

Token: wialon_token.txt next to the script, or WIALON_TOKEN. Never printed.

Run (PowerShell, one command per line). The date is the day to examine --
pick an ordinary working day:

  cd C:\\diag\\wialon
  & "C:\\Program Files\\Python314\\python.exe" C:\\transport-report\\tools\\wialon_probe6_fleet_health.py --date 2026-08-13

Takes roughly a minute per 30 machines: about 15 minutes for the whole fleet.
Send back gps_fleet_health.csv -- the sick machines are at the top, and the
console prints them too.
"""

import argparse
import csv
import json
import math
import os
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

BASE_URL = "https://web.gpstrack.uz"
AJAX = BASE_URL.rstrip("/") + "/wialon/ajax.html"
TIMEOUT = 300
TZ = timezone(timedelta(hours=5))
HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.getcwd()

MIN_JUMP_SECONDS = 5.0
JUMP_MARGIN_KMH = 40.0
WARN_SATS = 12.0
WARN_JUMPS = 3
# [REASON]: a machine that barely moved says nothing about its tracker. On the
# fleet run of 13.08 only 193 of 481 objects produced 200 points or more; the
# rest were parked, and flagging them buried the real list.
MIN_POINTS_TO_JUDGE = 200
EARTH = 6378137.0

ERRORS = {1: "invalid session", 2: "invalid service", 4: "invalid input",
          5: "request failed", 7: "access denied",
          1001: "no messages for the interval", 1003: "one request at a time",
          1004: "concurrent request limit"}

log = []
say = lambda s="": log.append(str(s))


def read_token():
    for folder in (OUT, HERE):
        path = os.path.join(folder, "wialon_token.txt")
        if os.path.isfile(path):
            with open(path, encoding="utf-8-sig") as fh:
                value = fh.readline().strip()
            if value:
                return value
    value = os.environ.get("WIALON_TOKEN", "").strip()
    if value:
        return value
    sys.stderr.write("ERROR: wialon_token.txt not found next to the script "
                     "and WIALON_TOKEN is not set\n")
    sys.exit(2)


def call(svc, params, sid=None):
    query = {"svc": svc, "params": json.dumps(params, ensure_ascii=False)}
    if sid:
        query["sid"] = sid
    request = urllib.request.Request(
        AJAX, data=urllib.parse.urlencode(query).encode("utf-8"),
        headers={"Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
        raw = response.read().decode("utf-8", "replace")
    try:
        return json.loads(raw)
    except ValueError:
        return {"_unparsed": raw[:500]}


def err(result):
    if isinstance(result, dict) and "error" in result:
        code = result["error"]
        return "error %s (%s)" % (code, ERRORS.get(code, "unknown"))
    return None


def metres(lon1, lat1, lon2, lat2):
    mean_lat = math.radians((lat1 + lat2) / 2.0)
    dx = math.radians(lon2 - lon1) * EARTH * math.cos(mean_lat)
    dy = math.radians(lat2 - lat1) * EARTH
    return math.hypot(dx, dy)


def median(values):
    if not values:
        return -1.0
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return float(ordered[middle])
    return (ordered[middle - 1] + ordered[middle]) / 2.0


def examine(points):
    """points: [(t, lon, lat, speed, sats)] -- returns the four indicators."""
    intervals, sats, jumps, gaps = [], [], 0, 0
    for (t1, lon1, lat1, sp1, s1), (t2, lon2, lat2, sp2, s2) in zip(points, points[1:]):
        delta = t2 - t1
        intervals.append(delta)
        if s1 >= 0:
            sats.append(s1)
        if delta >= MIN_JUMP_SECONDS:
            implied = metres(lon1, lat1, lon2, lat2) / delta * 3.6
            if implied > max(sp1, sp2) + JUMP_MARGIN_KMH:
                jumps += 1
        if delta > 300 and sp1 >= 1.0:
            gaps += 1
    return {"points": len(points), "interval_s": median(intervals),
            "sats_median": median(sats), "jumps": jumps, "motion_gaps": gaps}


def reasons_for(row):
    # [REASON]: dense logging alone is NOT a fault. On the fleet run 46 objects
    # wrote a point every 1-3 s with 13-20 satellites -- those are the cars,
    # configured differently on purpose. It only matters when the points are
    # also imprecise, and then "few satellites" already says so.
    if row["points"] < MIN_POINTS_TO_JUDGE:
        return "malo dannyh za den"
    reasons = []
    if 0 <= row["sats_median"] < WARN_SATS:
        reasons.append("malo sputnikov")
    if row["jumps"] >= WARN_JUMPS:
        reasons.append("pryzhki koordinat")
    if row["motion_gaps"]:
        reasons.append("molchit na hodu")
    return "; ".join(reasons)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument("--date", required=True, help="day to examine, YYYY-MM-DD")
    parser.add_argument("--out", default="gps_fleet_health.csv")
    args = parser.parse_args()

    token = read_token()
    day = datetime.strptime(args.date, "%Y-%m-%d").replace(tzinfo=TZ)
    start, finish = int(day.timestamp()), int((day + timedelta(days=1)).timestamp())

    say("Wialon probe 6 -- read-only -- fleet tracker health")
    say("date examined: %s (local day, UTC+5)" % args.date)
    say("run at: %s" % datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S %z"))

    login = call("token/login", {"token": token})
    problem = err(login)
    if problem:
        say("login failed: %s" % problem)
        write_log()
        print("login failed, see gps_fleet_health_report.txt")
        return 1
    sid = login.get("eid")
    say("login OK")

    listing = call("core/search_items", {
        "spec": {"itemsType": "avl_unit", "propName": "sys_name",
                 "propValueMask": "*", "sortType": "sys_name"},
        "force": 1, "flags": 1, "from": 0, "to": 0}, sid)
    problem = err(listing)
    if problem:
        say("unit list failed: %s" % problem)
        write_log()
        return 1
    units = [(item.get("id"), item.get("nm") or "")
             for item in (listing.get("items") or []) if item.get("id")]
    say("units visible: %d" % len(units))
    say("")

    rows = []
    for number, (unit_id, name) in enumerate(units, 1):
        try:
            answer = call("messages/load_interval",
                          {"itemId": unit_id, "timeFrom": start,
                           "timeTo": finish, "flags": 0, "flagsMask": 0,
                           "loadCount": 0xFFFFFFFF}, sid)
            problem = err(answer)
            if problem:
                rows.append({"unit_id": unit_id, "name": name, "points": 0,
                             "interval_s": -1.0, "sats_median": -1.0,
                             "jumps": 0, "motion_gaps": 0,
                             "reasons": "net dannyh: %s" % problem})
                call("messages/unload", {}, sid)
                time.sleep(0.3)
                continue
            points = []
            for message in (answer.get("messages") or []):
                position = message.get("pos") or {}
                if position.get("x") is None or position.get("y") is None:
                    continue
                points.append((message.get("t"), position.get("x"),
                               position.get("y"), position.get("s") or 0,
                               position.get("sc")
                               if position.get("sc") is not None else -1))
            call("messages/unload", {}, sid)
            points.sort()
            if len(points) < 2:
                rows.append({"unit_id": unit_id, "name": name,
                             "points": len(points), "interval_s": -1.0,
                             "sats_median": -1.0, "jumps": 0, "motion_gaps": 0,
                             "reasons": "net dannyh za den"})
            else:
                row = examine(points)
                row.update(unit_id=unit_id, name=name)
                row["reasons"] = reasons_for(row)
                rows.append(row)
            del points
            if number % 25 == 0:
                print("...%d of %d" % (number, len(units)))
            time.sleep(0.3)
        except Exception as exc:                               # noqa: BLE001
            rows.append({"unit_id": unit_id, "name": name, "points": 0,
                         "interval_s": -1.0, "sats_median": -1.0, "jumps": 0,
                         "motion_gaps": 0,
                         "reasons": "sboy: %s" % type(exc).__name__})

    # sick first, then by satellites: the worst antenna at the top
    rows.sort(key=lambda r: (not r["reasons"], r["sats_median"]))
    path = os.path.join(OUT, args.out)
    with open(path, "w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.writer(fh, delimiter=";")
        writer.writerow(["wialon_id", "mashina", "tochek", "interval_sek",
                         "sputnikov", "pryzhkov", "molchaniy_na_hodu",
                         "chto_ne_tak"])
        for r in rows:
            writer.writerow([r["unit_id"], r["name"], r["points"],
                             "%.1f" % r["interval_s"], "%.1f" % r["sats_median"],
                             r["jumps"], r["motion_gaps"], r["reasons"]])

    flagged = [r for r in rows if r["reasons"] and "net dannyh" not in r["reasons"]]
    say("machines examined: %d, flagged: %d" % (len(rows), len(flagged)))
    say("")
    say("worst first:")
    for r in flagged[:40]:
        say("  id=%-6s sats=%4.1f interval=%5.1fs jumps=%3d gaps=%2d  %s"
            % (r["unit_id"], r["sats_median"], r["interval_s"], r["jumps"],
               r["motion_gaps"], r["reasons"]))
    call("core/logout", {}, sid)
    say("")
    say("logout done")
    write_log()
    print("done. flagged %d of %d machines. see %s"
          % (len(flagged), len(rows), args.out))
    return 0


def write_log():
    with open(os.path.join(OUT, "gps_fleet_health_report.txt"),
              "w", encoding="utf-8") as fh:
        fh.write("\n".join(log) + "\n")


if __name__ == "__main__":
    sys.exit(main())
