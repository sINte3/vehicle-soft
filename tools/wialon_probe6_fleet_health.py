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

A SINGLE DAY IS NOT ENOUGH, and the first fleet run proved it: on 13.08 some
241 of 481 objects produced no message at all, because the season was ending
and the tractors simply stood. A parked machine says nothing about its
tracker. So the run samples a PERIOD -- by default every 7th day -- and judges
each machine on the days it actually worked.

Run (PowerShell, one command per line):

  cd C:\\diag\\wialon
  & "C:\\Program Files\\Python314\\python.exe" C:\\transport-report\\tools\\wialon_probe6_fleet_health.py --from 2026-06-01 --to 2026-08-14

That samples 11 days over the summer for every object. At the default pace
(one request per two seconds, decision G9) a full summer sweep takes about six
hours, so it is a night job. Add --every 14 to halve it at the cost of half
the days, or --pause 1 to halve it at twice the load on the server.

The run may be interrupted at any point -- every finished object is written to
gps_fleet_health.partial.jsonl straight away. Start it again with the SAME
--from/--to/--every plus --resume and it carries on from the break.

Send back gps_fleet_health.csv -- the sick machines are at the top, and the
console prints them too.
"""

import argparse
import csv
import json
import math
import os
import sys
import threading
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

BASE_URL = "https://web.gpstrack.uz"
AJAX = BASE_URL.rstrip("/") + "/wialon/ajax.html"
# [REASON]: 60 s, not 300. A heavy object -- a car logging every second --
# can make load_interval hang, and at a five-minute timeout three such objects
# freeze the run for a quarter of an hour with nothing on screen. Better to
# lose one object than the run.
TIMEOUT = 60
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
# [REASON]: the indicators are medians and counts -- they do not need every
# message of the day. Cars logging every second produce tens of thousands of
# points per day, and asking for all of them is what made the first fleet run
# crawl. Twenty thousand is far more than any tractor writes in a day.
MAX_MESSAGES_PER_DAY = 20000
EARTH = 6378137.0

# Codes as published by Gurtam (help.wialon.com/en/api/user-guide/error-codes).
# 1004 used to be labelled "concurrent request limit" here -- that is wrong,
# it is the MESSAGE limit, and it is the one a fleet sweep can realistically
# reach.
ERRORS = {1: "invalid session", 2: "invalid service", 4: "invalid input",
          5: "request failed", 7: "access denied",
          8: "invalid user name or password",
          1001: "no messages for the interval",
          1003: "only one request is allowed at the moment",
          1004: "the limit of messages has been exceeded",
          1005: "the execution time has exceeded the limit",
          1011: "your IP has changed, or the session has expired"}
# [REASON]: these three mean the server is asking us to slow down, not that the
# object is unreadable. Retrying them at once turns one refusal into a storm.
LIMIT_ERRORS = (1003, 1004, 1005)
LIMIT_BACKOFF_S = 30.0

# [REASON]: Wialon publishes NO cap on the rate of ordinary read requests. The
# documented caps are on logins (120/min from one IP), on SIMULTANEOUS requests
# (3 heavy, 10 total in a session) and on message VOLUME -- 15 mln messages per
# user within two minutes. This probe is single-threaded and logs in once, so
# the only limit it can approach is volume. At the default pace it asks for at
# most 20 000 messages every two seconds -- 1.2 mln per two minutes, 8% of the
# documented ceiling. The pause itself is decision G9 after access from the
# cluster's address was lost during the run of 14.08; the cause of that outage
# is not proven, and until the integrator confirms their own limits the pace
# stays deliberately slower than anything the documentation requires.
PACE = {"pause": 2.0}
MESSAGE_WINDOW_S = 120.0
MESSAGE_WINDOW_LIMIT = 7000000
LEDGER = "gps_fleet_health.partial.jsonl"

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


# [REASON]: a run over the whole fleet makes thousands of requests over an
# hour, and a single dropped connection used to kill it -- the owner lost a
# forty-minute run to one WinError 10060 on the very first call. The network
# to an integrator's server across the public internet is not reliable enough
# to assume; every call now survives three failures before giving up.
RETRIES = 3
RETRY_PAUSE_S = (3, 8, 20)
# [REASON]: the ceiling on ONE request, whatever the socket is doing.
# TIMEOUT above is per socket operation and a trickling peer never
# trips it; this does. Generous enough that a slow but honest answer
# still arrives -- the heaviest real object took about 40 s.
HARD_DEADLINE_S = 150.0
ABANDONED = []


_last_call_at = [0.0]
_messages_window = []


def pace():
    """Hold the agreed request rate, whatever the server answers."""
    waiting = PACE["pause"] - (time.monotonic() - _last_call_at[0])
    if waiting > 0:
        time.sleep(waiting)
    _last_call_at[0] = time.monotonic()


def message_budget(about_to_load):
    """Wait until the rolling two-minute window has room for another load.

    Documented ceiling is 15 mln messages per user in two minutes; half of it
    is used here, so a second tool running against the same account at the same
    time still cannot push the pair of us over.
    """
    while True:
        edge = time.monotonic() - MESSAGE_WINDOW_S
        while _messages_window and _messages_window[0][0] < edge:
            _messages_window.pop(0)
        loaded = sum(count for _, count in _messages_window)
        if loaded + about_to_load <= MESSAGE_WINDOW_LIMIT:
            return
        print("    limit soobshcheniy blizko (%d za 2 min), pauza 5 s"
              % loaded, flush=True)
        time.sleep(5.0)


def note_messages(count):
    _messages_window.append((time.monotonic(), count))


def _fetch(query, box):
    try:
        request = urllib.request.Request(
            AJAX, data=urllib.parse.urlencode(query).encode("utf-8"),
            headers={"Content-Type": "application/x-www-form-urlencoded"})
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            box["raw"] = response.read().decode("utf-8", "replace")
    except Exception as exc:                                   # noqa: BLE001
        box["error"] = exc


def call(svc, params, sid=None):
    query = {"svc": svc, "params": json.dumps(params, ensure_ascii=False)}
    if sid:
        query["sid"] = sid
    last = None
    for attempt in range(RETRIES):
        pace()
        # [REASON]: urlopen's timeout is per socket operation, not per request.
        # A peer that dribbles a byte every half minute keeps the read alive
        # for ever, and that is what a run of 16.08 looked like: object 63,
        # no progress for a day, and not one line of output -- neither a retry
        # notice nor a limit notice, which rules out every path that reports.
        # The request therefore runs in a thread with a hard deadline; a stuck
        # one is abandoned (it is a daemon and dies with the process) and the
        # run goes on instead of standing still.
        box = {}
        worker = threading.Thread(target=_fetch, args=(query, box), daemon=True)
        worker.start()
        worker.join(HARD_DEADLINE_S)
        if worker.is_alive():
            ABANDONED.append(svc)
            print("    otvet ne prishel za %d s, brosayu zapros (%s)"
                  % (int(HARD_DEADLINE_S), svc), flush=True)
            last = TimeoutError("no answer in %d s" % int(HARD_DEADLINE_S))
            if attempt + 1 < RETRIES:
                time.sleep(RETRY_PAUSE_S[attempt])
            continue
        if "error" not in box:
            raw = box.get("raw", "")
            try:
                return json.loads(raw)
            except ValueError:
                return {"_unparsed": raw[:500]}
        else:
            last = box["error"]
            if attempt + 1 < RETRIES:
                print("    svyaz propala (%s), povtor cherez %d s"
                      % (type(last).__name__, RETRY_PAUSE_S[attempt]), flush=True)
                time.sleep(RETRY_PAUSE_S[attempt])
    raise last


def err(result):
    if isinstance(result, dict) and "error" in result:
        code = result["error"]
        return "error %s (%s)" % (code, ERRORS.get(code, "unknown"))
    return None


def err_code(result):
    if isinstance(result, dict) and "error" in result:
        return result["error"]
    return None


# [REASON]: a summer sweep is hours long and the run of 14.08 died halfway
# through when the address lost access to the server -- forty minutes of
# requests bought nothing, because the report was written only at the end.
# Every finished object now lands in the ledger at once, and --resume skips
# what is already there instead of paying for the whole fleet again.
def read_ledger(path, period):
    """unit_id -> row, but only rows measured over the SAME period."""
    done, alien = {}, 0
    if not os.path.isfile(path):
        return done, alien
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if row.get("period") != period:
                alien += 1
                continue
            if row.get("unit_id") is not None:
                done[row["unit_id"]] = row
    return done, alien


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


def reasons_for_period(row):
    """Verdict over the whole period, judged on the days the machine worked."""
    reasons = []
    if 0 <= row["sats_median"] < WARN_SATS:
        reasons.append("malo sputnikov")
    if row["jumps"] >= WARN_JUMPS * row["worked_days"]:
        reasons.append("pryzhki koordinat")
    if row["motion_gaps"] >= 2 * row["worked_days"]:
        reasons.append("chasto molchit na hodu")
    return "; ".join(reasons)


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
    parser.add_argument("--from", dest="date_from", required=True,
                        help="first day of the period, YYYY-MM-DD")
    parser.add_argument("--to", dest="date_to", required=True,
                        help="last day of the period, YYYY-MM-DD")
    parser.add_argument("--every", type=int, default=7,
                        help="sample every Nth day (default 7)")
    parser.add_argument("--pause", type=float, default=PACE["pause"],
                        help="seconds between requests (default 2.0, G9)")
    parser.add_argument("--resume", action="store_true",
                        help="continue an interrupted run over the same period")
    # [REASON]: a full sweep is six hours. Starting it blind after an outage is
    # how the run of 14.08 was lost. --limit 5 exercises the whole path --
    # login, unit list, message loading, unloading, logout -- in under a minute.
    parser.add_argument("--limit", type=int, default=0,
                        help="check only the first N objects (0 = all)")
    parser.add_argument("--out", default="gps_fleet_health.csv")
    args = parser.parse_args()
    PACE["pause"] = max(0.0, args.pause)

    token = read_token()
    first = datetime.strptime(args.date_from, "%Y-%m-%d").replace(tzinfo=TZ)
    last = datetime.strptime(args.date_to, "%Y-%m-%d").replace(tzinfo=TZ)
    if last < first:
        sys.stderr.write("ERROR: --to is earlier than --from\n")
        return 2
    sample_days = []
    cursor = first
    while cursor <= last:
        sample_days.append(cursor)
        cursor += timedelta(days=max(1, args.every))

    period = "%s..%s/%d" % (args.date_from, args.date_to, args.every)
    # [REASON]: a connection check must not poison the real run's ledger. Its
    # five objects carry the same period, so a later --resume would count them
    # as done and the fleet report would silently be short by five machines.
    if args.limit > 0:
        ledger_path = os.path.join(OUT, "gps_fleet_health.limit.jsonl")
        if args.out == "gps_fleet_health.csv":
            args.out = "gps_fleet_health.limit.csv"
    else:
        ledger_path = os.path.join(OUT, LEDGER)
    done, alien = ({}, 0)
    if args.resume:
        done, alien = read_ledger(ledger_path, period)

    say("Wialon probe 6 -- read-only -- fleet tracker health")
    say("period: %s .. %s, every %d-th day -- %d days sampled"
        % (args.date_from, args.date_to, args.every, len(sample_days)))
    say("pace: one request per %.1f s" % PACE["pause"])
    say("run at: %s" % datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S %z"))
    if args.resume:
        say("resume: %d objects taken from the ledger" % len(done))
        if alien:
            say("resume: %d ledger rows ignored -- measured over another period"
                % alien)

    try:
        login = call("token/login", {"token": token})
    except Exception as exc:                                   # noqa: BLE001
        sys.stderr.write(
            "\nERROR: ne udalos soedinitsya s %s (%s).\n"
            "Proverte: otkryvaetsya li sayt v brauzere, i vypolnite v "
            "PowerShell odnu stroku:\n"
            "  Test-NetConnection web.gpstrack.uz -Port 443\n"
            "Esli TcpTestSucceeded: False -- eto set, ne skript. "
            "Podozhdite 10-15 minut i povtorite.\n" % (BASE_URL, type(exc).__name__))
        return 3
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
    if args.limit > 0:
        units = units[:args.limit]
        say("LIMIT: only the first %d objects -- this is a connection check, "
            "not a fleet report" % len(units))
    say("")

    rows = list(done.values())
    ledger = open(ledger_path, "a" if args.resume else "w", encoding="utf-8")
    limit_hits = 0
    for number, (unit_id, name) in enumerate(units, 1):
        if unit_id in done:
            continue
        # [REASON]: aggregates only. Points of one machine-day are examined and
        # dropped before the next day is fetched, so a fleet-wide run over a
        # whole summer never holds more than one day of one machine in memory.
        worked_days, total_points, failed_days = 0, 0, 0
        intervals, sats, jumps, gaps = [], [], 0, 0
        # the clock is printed so a stall can be dated from the console
        print("  [%d/%d] %s %s" % (number, len(units),
                                   datetime.now(TZ).strftime("%H:%M:%S"),
                                   name[:40]), flush=True)
        for day in sample_days:
            start = int(day.timestamp())
            finish = int((day + timedelta(days=1)).timestamp())
            try:
                message_budget(MAX_MESSAGES_PER_DAY)
                answer = call("messages/load_interval",
                              {"itemId": unit_id, "timeFrom": start,
                               "timeTo": finish, "flags": 0, "flagsMask": 0,
                               "loadCount": MAX_MESSAGES_PER_DAY}, sid)
                if err_code(answer) in LIMIT_ERRORS:
                    # the server is asking for room, not refusing the object
                    limit_hits += 1
                    failed_days += 1
                    print("    server prosit podozhdat: %s -- pauza %d s"
                          % (err(answer), int(LIMIT_BACKOFF_S)), flush=True)
                    try:
                        call("messages/unload", {}, sid)
                    except Exception:                          # noqa: BLE001
                        pass
                    time.sleep(LIMIT_BACKOFF_S)
                    continue
                if err(answer) is None:
                    note_messages(len(answer.get("messages") or []))
                    points = []
                    for message in (answer.get("messages") or []):
                        position = message.get("pos") or {}
                        if position.get("x") is None or position.get("y") is None:
                            continue
                        points.append((message.get("t"), position.get("x"),
                                       position.get("y"), position.get("s") or 0,
                                       position.get("sc")
                                       if position.get("sc") is not None else -1))
                    points.sort()
                    if len(points) >= MIN_POINTS_TO_JUDGE:
                        worked_days += 1
                        total_points += len(points)
                        day_row = examine(points)
                        intervals.append(day_row["interval_s"])
                        if day_row["sats_median"] >= 0:
                            sats.append(day_row["sats_median"])
                        jumps += day_row["jumps"]
                        gaps += day_row["motion_gaps"]
                    del points
                call("messages/unload", {}, sid)
            except Exception:                                  # noqa: BLE001
                # a timeout on one day must not cost the object or the run
                failed_days += 1
                try:
                    call("messages/unload", {}, sid)
                except Exception:                              # noqa: BLE001
                    pass
                continue
        row = {"unit_id": unit_id, "name": name, "worked_days": worked_days,
               "sampled_days": len(sample_days), "points": total_points,
               "interval_s": median(intervals), "sats_median": median(sats),
               "jumps": jumps, "motion_gaps": gaps, "period": period}
        # [REASON]: judged on the days it worked, not on the calendar. A machine
        # that never worked in the period is reported as such, not as faulty.
        row["reasons"] = ("ne rabotal v period" if worked_days == 0
                          else reasons_for_period(row))
        if failed_days:
            row["reasons"] = ((row["reasons"] + "; ") if row["reasons"] else "") \
                + "dney ne zagruzilos: %d" % failed_days
        rows.append(row)
        # written before the next object is touched: an interrupted run keeps
        # everything it has already paid for
        ledger.write(json.dumps(row, ensure_ascii=False) + "\n")
        ledger.flush()
    ledger.close()

    # sick first, then by satellites: the worst antenna at the top; machines
    # that never worked go last -- they are a question, not a fault
    rows.sort(key=lambda r: (r["worked_days"] == 0,
                             not r["reasons"], r["sats_median"]))
    path = os.path.join(OUT, args.out)
    with open(path, "w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.writer(fh, delimiter=";")
        writer.writerow(["wialon_id", "mashina", "rabochih_dney",
                         "dney_v_vyborke", "tochek", "interval_sek",
                         "sputnikov", "pryzhkov", "molchaniy_na_hodu",
                         "chto_ne_tak"])
        for r in rows:
            writer.writerow([r["unit_id"], r["name"], r["worked_days"],
                             r["sampled_days"], r["points"],
                             "%.1f" % r["interval_s"], "%.1f" % r["sats_median"],
                             r["jumps"], r["motion_gaps"], r["reasons"]])

    worked = [r for r in rows if r["worked_days"] > 0]
    idle = [r for r in rows if r["worked_days"] == 0]
    flagged = [r for r in worked if r["reasons"]]
    say("objects: %d | worked at least one sampled day: %d | never: %d"
        % (len(rows), len(worked), len(idle)))
    say("of those that worked, flagged: %d" % len(flagged))
    # the honest record of what the server itself asked us to slow down for --
    # if access is lost again, this line says whether Wialon complained first
    say("server limit refusals during the run: %d" % limit_hits)
    say("requests abandoned on the hard deadline: %d" % len(ABANDONED))
    say("")
    say("worst first:")
    for r in flagged[:60]:
        say("  id=%-6s days=%2d/%2d sats=%4.1f interval=%5.1fs jumps=%3d "
            "gaps=%3d  %s"
            % (r["unit_id"], r["worked_days"], r["sampled_days"],
               r["sats_median"], r["interval_s"], r["jumps"],
               r["motion_gaps"], r["reasons"]))
    call("core/logout", {}, sid)
    say("")
    say("logout done")
    write_log()
    print("done. %d objects worked, %d of them flagged, %d never moved. see %s"
          % (len(worked), len(flagged), len(idle), args.out))
    return 0


def write_log():
    with open(os.path.join(OUT, "gps_fleet_health_report.txt"),
              "w", encoding="utf-8") as fh:
        fh.write("\n".join(log) + "\n")


if __name__ == "__main__":
    sys.exit(main())
