# -*- coding: utf-8 -*-
"""Pull raw tracks for named machines and days -- read-only, resumable.

WHY A SECOND PULLER
wialon_probe5_spraying.py is welded to fifteen unit-days of one work type: it
was written to fetch a pre-registered verification set and it must stay that
way, because the git history of that file is part of the proof that the area
method was frozen before the data arrived. Labelling needs a different thing --
any machines, any days, chosen by name.

WHAT IT IS FOR
The work/transit corpus is built from ten machines doing two jobs, cultivation
and spraying. The pre-registered rule (roadmap 2.8.1) has to be judged on
machines and jobs it has never seen: ploughing, sowing, harvesting, lorries.
This pulls their tracks; tools/gps_label_sites.py turns them into a page of
questions.

READ-ONLY. Services: token/login, core/search_items, messages/load_interval,
messages/unload, core/logout. No create / update / delete anywhere.

Token: wialon_token.txt next to the script, or WIALON_TOKEN. Never printed.

PACE. One request per two seconds by default (decision G9), the same as the
fleet probe, and for the same reason: access from the cluster's address was
lost on 14.08 during an unpaced run and the cause is still not proven.

NO GEO STACK NEEDED. Standard library only -- the plain Python install runs it,
the C:\\gps_venv environment is for tools that import gps/ and this is not one.

Run (PowerShell, one command per line):

  cd C:\\diag\\wialon
  & "C:\\Program Files\\Python314\\python.exe" C:\\diag\\wialon\\wialon_pull_tracks.py --unit "261 EA" --unit "kombayn" --from 2026-08-01 --to 2026-08-10

Every match is printed BEFORE anything is fetched, so a mask that caught forty
objects can be stopped with Ctrl+C. Interrupted runs continue with --resume:
the csv itself is the ledger.

Console output is ASCII.
"""

import argparse
import csv
import json
import os
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

BASE_URL = "https://web.gpstrack.uz"
AJAX = BASE_URL.rstrip("/") + "/wialon/ajax.html"
TIMEOUT = 60
TZ = timezone(timedelta(hours=5))
HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.getcwd()

PACE = {"pause": 2.0}
RETRIES = 3
RETRY_PAUSE_S = (3, 8, 20)
# [REASON]: a labelling track needs every point, unlike the health probe which
# only needs medians. A tractor writes 1500-13000 messages a day; 50 000 is
# well above that and still far under the 2 GB per-response limit.
MAX_MESSAGES_PER_DAY = 50000
LIMIT_ERRORS = (1003, 1004, 1005)
LIMIT_BACKOFF_S = 30.0

ERRORS = {1: "invalid session", 2: "invalid service", 4: "invalid input",
          5: "request failed", 7: "access denied",
          1001: "no messages for the interval",
          1003: "only one request is allowed at the moment",
          1004: "the limit of messages has been exceeded",
          1005: "the execution time has exceeded the limit",
          1011: "your IP has changed, or the session has expired"}

_last_call_at = [0.0]


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
    last = None
    for attempt in range(RETRIES):
        waiting = PACE["pause"] - (time.monotonic() - _last_call_at[0])
        if waiting > 0:
            time.sleep(waiting)
        _last_call_at[0] = time.monotonic()
        try:
            request = urllib.request.Request(
                AJAX, data=urllib.parse.urlencode(query).encode("utf-8"),
                headers={"Content-Type": "application/x-www-form-urlencoded"})
            with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
                raw = response.read().decode("utf-8", "replace")
            try:
                return json.loads(raw)
            except ValueError:
                return {"_unparsed": raw[:500]}
        except Exception as exc:                                   # noqa: BLE001
            last = exc
            if attempt + 1 < RETRIES:
                print("    svyaz propala (%s), povtor cherez %d s"
                      % (type(exc).__name__, RETRY_PAUSE_S[attempt]), flush=True)
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


def already_pulled(path):
    """(unit_id, date) pairs already in the csv -- the file is its own ledger."""
    done = set()
    if not os.path.isfile(path):
        return done
    with open(path, encoding="utf-8-sig") as fh:
        for row in csv.DictReader(fh, delimiter=";"):
            if row.get("unit_id") and row.get("date"):
                done.add((row["unit_id"], row["date"]))
    return done


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--unit", action="append", default=[], required=True,
                        help="part of the machine name, case-insensitive; may repeat")
    parser.add_argument("--from", dest="date_from", required=True)
    parser.add_argument("--to", dest="date_to", required=True)
    parser.add_argument("--pause", type=float, default=PACE["pause"])
    parser.add_argument("--resume", action="store_true",
                        help="skip machine-days already in the csv")
    parser.add_argument("--out", default="label_tracks.csv")
    args = parser.parse_args()
    PACE["pause"] = max(0.0, args.pause)

    first = datetime.strptime(args.date_from, "%Y-%m-%d").replace(tzinfo=TZ)
    last = datetime.strptime(args.date_to, "%Y-%m-%d").replace(tzinfo=TZ)
    if last < first:
        sys.stderr.write("ERROR: --to is earlier than --from\n")
        return 2
    days = []
    cursor = first
    while cursor <= last:
        days.append(cursor)
        cursor += timedelta(days=1)

    token = read_token()
    try:
        login = call("token/login", {"token": token})
    except Exception as exc:                                       # noqa: BLE001
        sys.stderr.write(
            "\nERROR: ne udalos soedinitsya s %s (%s).\n"
            "Proverte v PowerShell odnoy strokoy:\n"
            "  Test-NetConnection web.gpstrack.uz -Port 443\n" % (BASE_URL, type(exc).__name__))
        return 3
    problem = err(login)
    if problem:
        print("login failed: %s" % problem)
        return 1
    sid = login.get("eid")
    print("login OK")

    listing = call("core/search_items", {
        "spec": {"itemsType": "avl_unit", "propName": "sys_name",
                 "propValueMask": "*", "sortType": "sys_name"},
        "force": 1, "flags": 1, "from": 0, "to": 0}, sid)
    problem = err(listing)
    if problem:
        print("unit list failed: %s" % problem)
        return 1
    masks = [m.strip().lower() for m in args.unit if m.strip()]
    units = [(item.get("id"), item.get("nm") or "")
             for item in (listing.get("items") or [])
             if item.get("id") and any(m in (item.get("nm") or "").lower()
                                       for m in masks)]
    # [REASON]: printed BEFORE the first message request. A mask like "mtz"
    # matches most of the fleet, and finding that out an hour in is expensive.
    print("matched %d objects for %d day(s):" % (len(units), len(days)))
    for unit_id, name in units:
        print("   %-8s %s" % (unit_id, name))
    if not units:
        print("nothing matched -- check the masks against gps_fleet_health.csv")
        call("core/logout", {}, sid)
        return 1
    print("estimated time: about %d min at one request per %.1f s"
          % (len(units) * len(days) * 2 * PACE["pause"] / 60.0, PACE["pause"]))

    path = os.path.join(OUT, args.out)
    done = already_pulled(path) if args.resume else set()
    if done:
        print("resume: %d machine-days already in %s" % (len(done), args.out))
    fresh = not os.path.isfile(path) or not args.resume
    fh = open(path, "a" if (args.resume and not fresh) else "w",
              encoding="utf-8-sig", newline="")
    writer = csv.writer(fh, delimiter=";")
    if fresh:
        writer.writerow(["unit_id", "date", "time", "lat", "lon",
                         "speed", "course", "sats"])

    written = empty = failed = 0
    for number, (unit_id, name) in enumerate(units, 1):
        for day in days:
            stamp = day.strftime("%Y-%m-%d")
            if (str(unit_id), stamp) in done:
                continue
            print("  [%d/%d] %s %s" % (number, len(units), name[:32], stamp),
                  flush=True)
            start = int(day.timestamp())
            finish = int((day + timedelta(days=1)).timestamp())
            try:
                answer = call("messages/load_interval",
                              {"itemId": unit_id, "timeFrom": start,
                               "timeTo": finish, "flags": 0, "flagsMask": 0,
                               "loadCount": MAX_MESSAGES_PER_DAY}, sid)
                if err_code(answer) in LIMIT_ERRORS:
                    print("    server prosit podozhdat: %s -- pauza %d s"
                          % (err(answer), int(LIMIT_BACKOFF_S)), flush=True)
                    failed += 1
                    try:
                        call("messages/unload", {}, sid)
                    except Exception:                              # noqa: BLE001
                        pass
                    time.sleep(LIMIT_BACKOFF_S)
                    continue
                rows = 0
                if err(answer) is None:
                    for message in (answer.get("messages") or []):
                        position = message.get("pos") or {}
                        if position.get("x") is None or position.get("y") is None:
                            continue
                        moment = datetime.fromtimestamp(message.get("t"), TZ)
                        writer.writerow([unit_id, moment.strftime("%Y-%m-%d"),
                                         moment.strftime("%H:%M:%S"),
                                         position.get("y"), position.get("x"),
                                         int(position.get("s") or 0),
                                         int(position.get("c") or 0),
                                         position.get("sc")
                                         if position.get("sc") is not None else ""])
                        rows += 1
                fh.flush()
                written += rows
                if rows == 0:
                    empty += 1
                    print("      net soobshcheniy", flush=True)
                call("messages/unload", {}, sid)
            except Exception:                                      # noqa: BLE001
                failed += 1
                try:
                    call("messages/unload", {}, sid)
                except Exception:                                  # noqa: BLE001
                    pass
                continue
    fh.close()
    call("core/logout", {}, sid)
    print("\npoints written: %d | machine-days with no messages: %d | failed: %d"
          % (written, empty, failed))
    print("file: %s" % args.out)
    print("next: gps_label_sites.py --csv %s --names gps_fleet_health.csv"
          % args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
