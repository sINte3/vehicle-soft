# -*- coding: utf-8 -*-
"""
wialon_probe5_spraying.py -- read-only pull for the spraying verification set.

Fifteen unit-days measured by hand by the owner between 2026-07-01 and
2026-08-08, all of them spraying ("Суспензия"). This is the set that decides
whether the frozen area method needs the adaptive-alpha correction: on the
27.07 set spraying came out at -8.6 percent against -2.7 for cultivation,
and the proposed fix was tuned on the very data it improved, so it was NOT
adopted. This set was never seen by the method.

Pre-registration note. The method is frozen in git (commit 4bc610c,
2026-08-12) BEFORE these measurements arrived: speed window 1-15 km/h,
densify 5 m, alpha 10 m, clip by contour, UTM 41N. The parameters therefore
cannot have been fitted to this set, and the git history proves it.

TWO TASKS, each in its own try block so one failure does not lose the other.

A. Zone refresh. The existing wialon_zones.json was dumped on 2026-07-27,
   but these works run to 08.08 and the agro-work module CREATES geozones in
   this same resource. A contour created after 27.07 would be missing from
   the old dump and its work would silently find no zone. The refreshed file
   is written as wialon_zones_YYYYMMDD.json, the old one is left untouched.

B. Track pull for the fifteen unit-days.

READ-ONLY. Services: token/login, core/search_items, resource/get_zone_data,
messages/load_interval, messages/unload, core/logout. No create / update /
delete anywhere. messages/unload clears this session's read buffer only.

Token: wialon_token.txt next to the script, or the WIALON_TOKEN environment
variable. Never printed, never written to any output file.

Run (PowerShell, one command per line):
  cd C:\\diag\\wialon
  & "C:\\Program Files\\Python314\\python.exe" C:\\transport-report\\tools\\wialon_probe5_spraying.py

Send back (none of them contains the token):
  wialon_probe5_report.txt
  verify2_tracks.csv            unit_id;date;time;lat;lon;speed;course;sats
  wialon_zones_YYYYMMDD.json    only if it differs in size from the old dump
"""

import json
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
OUT = os.getcwd() if os.path.isdir(os.getcwd()) else HERE
P = lambda n: os.path.join(OUT, n)

# [REASON]: the fifteen hand-measured spraying works, resolved to wialon_id
# from wialon_units.csv by exact object name -- every one of them matched a
# single object, no ambiguity to carry. Names are transliterated here because
# this script prints them to a console that cannot render Cyrillic.
UNIT_DAYS = [
    (1729, "MTZ 261 EA",             "2026-08-08"),
    (1729, "MTZ 261 EA",             "2026-08-07"),
    (3100, "MTZ 25 GA 691 (575 HA)", "2026-08-07"),
    (1729, "MTZ 261 EA",             "2026-08-05"),
    (1729, "MTZ 261 EA",             "2026-08-01"),
    (1729, "MTZ 261 EA",             "2026-07-31"),
    (3453, "MTZ 266 EA",             "2026-07-20"),
    (1730, "MTZ 873 GA",             "2026-07-17"),
    (1730, "MTZ 873 GA",             "2026-07-16"),
    (6824, "TD5 681 GA (187)",       "2026-07-13"),
    (952,  "MTZ-80 X 540 GA",        "2026-07-07"),
    (942,  "MTZ-80 X 514 GA",        "2026-07-07"),
    (1730, "MTZ 873 GA",             "2026-07-07"),
    (1730, "MTZ 873 GA",             "2026-07-04"),
    (1730, "MTZ 873 GA",             "2026-07-01"),
]

RESOURCE_ID = 285          # 'Бухоро агрокластер' -- ours; dealer 170 has none
ZONE_LIST_FLAGS = 0x1001   # confirmed working by probe 2
ZONE_DATA_FLAGS = 0xFFFFFFFF

ERRORS = {1: "invalid session", 2: "invalid service name", 4: "invalid input",
          5: "error performing request", 7: "access denied",
          1001: "no messages for selected interval",
          1003: "only one request of given type at a time",
          1004: "limit of concurrent requests reached"}

log = []
say = lambda s="": log.append(str(s))


def read_token():
    for name in ("wialon_token.txt",):
        for folder in (OUT, HERE):
            p = os.path.join(folder, name)
            if os.path.isfile(p):
                with open(p, encoding="utf-8-sig") as fh:
                    v = fh.readline().strip()
                if v:
                    return v
    v = os.environ.get("WIALON_TOKEN", "").strip()
    if v:
        return v
    sys.stderr.write("ERROR: wialon_token.txt not found and WIALON_TOKEN "
                     "not set. Run this from the folder holding the token.\n")
    sys.exit(2)


def call(svc, params, sid=None):
    q = {"svc": svc, "params": json.dumps(params, ensure_ascii=False)}
    if sid:
        q["sid"] = sid
    req = urllib.request.Request(
        AJAX, data=urllib.parse.urlencode(q).encode("utf-8"),
        headers={"Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        raw = r.read().decode("utf-8", "replace")
    try:
        return json.loads(raw)
    except ValueError:
        return {"_unparsed": raw[:1000]}


def err(res):
    if isinstance(res, dict) and "error" in res:
        c = res["error"]
        return "error %s (%s)" % (c, ERRORS.get(c, "unknown"))
    return None


def refresh_zones(sid):
    """Task A -- re-dump the zone directory with geometry."""
    say("=" * 62)
    say("A. ZONE REFRESH (resource %s)" % RESOURCE_ID)
    say("=" * 62)
    res = call("core/search_items", {
        "spec": {"itemsType": "avl_resource", "propName": "sys_name",
                 "propValueMask": "*", "sortType": "sys_name"},
        "force": 1, "flags": ZONE_LIST_FLAGS, "from": 0, "to": 0}, sid)
    problem = err(res)
    if problem:
        say("  resource search failed: %s" % problem)
        return
    listed = 0
    for item in res.get("items") or []:
        if item.get("id") == RESOURCE_ID:
            listed = len(item.get("zl") or {})
    say("  zones listed in resource: %s" % listed)

    data = call("resource/get_zone_data",
                {"itemId": RESOURCE_ID, "col": [], "flags": ZONE_DATA_FLAGS}, sid)
    problem = err(data)
    if problem:
        say("  get_zone_data failed: %s" % problem)
        return
    zones = data if isinstance(data, list) else (data.get("items") or [])
    out = {}
    for z in zones:
        zid = z.get("id")
        if zid is None:
            continue
        out[str(zid)] = {"resource_id": RESOURCE_ID, "name": z.get("n"),
                         "type": z.get("t"), "width": z.get("w"),
                         "points": z.get("p") or []}
    stamp = datetime.now(TZ).strftime("%Y%m%d")
    path = P("wialon_zones_%s.json" % stamp)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False)
    with_geom = sum(1 for z in out.values() if z["points"])
    say("  zones downloaded: %s, with geometry: %s" % (len(out), with_geom))
    say("  written: %s" % os.path.basename(path))
    old = P("wialon_zones.json")
    if os.path.isfile(old):
        say("  previous dump 2026-07-27 had %s bytes, this one %s bytes"
            % (os.path.getsize(old), os.path.getsize(path)))
        say("  a larger file means contours were created since 27.07 --")
        say("  send the new file if the sizes differ")


def pull_tracks(sid):
    """Task B -- points for the fifteen measured unit-days."""
    say("")
    say("=" * 62)
    say("B. TRACKS OF THE SPRAYING SET (%s unit-days)" % len(UNIT_DAYS))
    say("=" * 62)
    say("  local day boundaries, UTC+5")
    rows = []
    for uid, label, date_str in UNIT_DAYS:
        try:
            d = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=TZ)
            t0, t1 = int(d.timestamp()), int((d + timedelta(days=1)).timestamp())
            res = call("messages/load_interval",
                       {"itemId": uid, "timeFrom": t0, "timeTo": t1,
                        "flags": 0, "flagsMask": 0, "loadCount": 0xFFFFFFFF}, sid)
            problem = err(res)
            if problem:
                say("  %-24s %-11s unit %-5s %s" % (label, date_str, uid, problem))
                call("messages/unload", {}, sid)
                time.sleep(0.5)
                continue
            msgs = res.get("messages") or []
            n = 0
            for m in msgs:
                pos = m.get("pos") or {}
                if pos.get("y") is None or pos.get("x") is None:
                    continue
                n += 1
                rows.append([uid, date_str,
                             datetime.fromtimestamp(m.get("t"), TZ).strftime("%H:%M:%S"),
                             pos.get("y"), pos.get("x"), pos.get("s"),
                             pos.get("c"), pos.get("sc")])
            say("  %-24s %-11s unit %-5s messages=%-6s with coordinates=%s"
                % (label, date_str, uid, len(msgs), n))
            call("messages/unload", {}, sid)
            time.sleep(0.5)
        except Exception as exc:                              # noqa: BLE001
            say("  %-24s %-11s unit %-5s crashed: %s: %s"
                % (label, date_str, uid, type(exc).__name__, exc))

    with open(P("verify2_tracks.csv"), "w", encoding="utf-8-sig", newline="") as fh:
        fh.write("unit_id;date;time;lat;lon;speed;course;sats\n")
        for r in rows:
            fh.write(";".join("" if x is None else str(x) for x in r) + "\n")
    say("")
    say("verify2_tracks.csv: %s points" % len(rows))
    if not rows:
        say("EMPTY OUTPUT -- every unit-day failed above; do not treat this")
        say("as 'the tractors did not work', read the per-line reasons.")


def main():
    token = read_token()
    say("Wialon probe 5 -- read-only -- spraying verification set")
    say("run at: %s" % datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S %z"))
    say("output folder: %s" % OUT)
    say("")

    login = call("token/login", {"token": token})
    problem = err(login)
    if problem:
        say("login failed: %s" % problem)
        write()
        print("login failed, see wialon_probe5_report.txt")
        return 1
    sid = login.get("eid")
    say("login OK, user id %s" % (login.get("user") or {}).get("id"))
    say("")

    try:
        refresh_zones(sid)
    except Exception as exc:                                  # noqa: BLE001
        say("  zone refresh crashed: %s: %s" % (type(exc).__name__, exc))
    try:
        pull_tracks(sid)
    except Exception as exc:                                  # noqa: BLE001
        say("  track pull crashed: %s: %s" % (type(exc).__name__, exc))

    call("core/logout", {}, sid)
    say("")
    say("logout done")
    write()
    print("done. see wialon_probe5_report.txt")
    return 0


def write():
    with open(P("wialon_probe5_report.txt"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(log) + "\n")


if __name__ == "__main__":
    sys.exit(main())
