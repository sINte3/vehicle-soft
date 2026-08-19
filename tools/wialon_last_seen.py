# -*- coding: utf-8 -*-
"""When did each object last report? One request for the whole fleet.

WHY
The sweep of 18.08 sampled every 7th day and found 115 objects with not a
single message -- 91 of them field or heavy machinery, including 52 MTZ and
12 of the 15 John Deere, in the middle of the season. That number cannot be
read as "the tracker is dead", and saying so would be an invention: eleven
sampled days out of seventy-six catch a machine that worked ten days only by
luck. Silence in the sample and silence on the server are different facts.

This separates them, and costs one request. core/search_items with the
"last position" flag returns, for every object at once, the time of its last
message. A tracker last heard from in May is dead; one heard from yesterday
simply did not work on the sampled days.

No messages are loaded at all, so this puts almost nothing on the server --
unlike the sweep, it is safe to run at any hour.

READ-ONLY. Services: token/login, core/search_items, core/logout.

Token: wialon_token.txt next to the script, or WIALON_TOKEN. Never printed.

Run (PowerShell, one command per line):

  cd C:\\diag\\wialon
  & "C:\\Program Files\\Python314\\python.exe" C:\\diag\\wialon\\wialon_last_seen.py

Writes gps_last_seen.csv and prints the silent ones. Console output is ASCII.
"""

import argparse
import csv
import json
import os
import sys
import threading
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

BASE_URL = "https://web.gpstrack.uz"
AJAX = BASE_URL.rstrip("/") + "/wialon/ajax.html"
TIMEOUT = 60
HARD_DEADLINE_S = 150.0
TZ = timezone(timedelta(hours=5))
HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.getcwd()
# [REASON]: 1 is the name, 1024 is the last known position with its timestamp.
# Summed, as Wialon's flags always are. Nothing else is asked for: no sensors,
# no counters, no messages.
FLAG_NAME_AND_LAST_POSITION = 1025

TRANSLIT = {
    "\u0430": "a", "\u0431": "b", "\u0432": "v", "\u0433": "g", "\u0434": "d",
    "\u0435": "e", "\u0451": "e", "\u0436": "zh", "\u0437": "z", "\u0438": "i",
    "\u0439": "y", "\u043a": "k", "\u043b": "l", "\u043c": "m", "\u043d": "n",
    "\u043e": "o", "\u043f": "p", "\u0440": "r", "\u0441": "s", "\u0442": "t",
    "\u0443": "u", "\u0444": "f", "\u0445": "h", "\u0446": "ts", "\u0447": "ch",
    "\u0448": "sh", "\u0449": "sch", "\u044a": "", "\u044b": "y", "\u044c": "",
    "\u044d": "e", "\u044e": "yu", "\u044f": "ya", "\u045e": "o", "\u049b": "q",
    "\u0493": "g", "\u04b3": "h", "\u0456": "i", "\u0457": "yi", "\u0454": "e",
}


def ascii_only(text):
    """Console output must survive cp1251 -- see the crash of 18.08."""
    out = []
    for char in str(text):
        if char.isascii():
            out.append(char)
            continue
        replacement = TRANSLIT.get(char.lower())
        if replacement is None:
            out.append("?")
        elif char.isupper():
            out.append(replacement.upper() if len(replacement) == 1
                       else replacement.capitalize())
        else:
            out.append(replacement)
    return "".join(out)


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
    sys.stderr.write("ERROR: wialon_token.txt not found and WIALON_TOKEN unset\n")
    sys.exit(2)


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
    box = {}
    worker = threading.Thread(target=_fetch, args=(query, box), daemon=True)
    worker.start()
    worker.join(HARD_DEADLINE_S)
    if worker.is_alive():
        raise TimeoutError("no answer in %d s" % int(HARD_DEADLINE_S))
    if "error" in box:
        raise box["error"]
    raw = box.get("raw", "")
    try:
        return json.loads(raw)
    except ValueError:
        return {"_unparsed": raw[:500]}


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", default="gps_last_seen.csv")
    parser.add_argument("--quiet-days", type=int, default=14,
                        help="call an object silent after this many days (14)")
    args = parser.parse_args()

    token = read_token()
    try:
        login = call("token/login", {"token": token})
    except Exception as exc:                                   # noqa: BLE001
        sys.stderr.write("\nERROR: ne udalos soedinitsya s %s (%s)\n"
                         % (BASE_URL, type(exc).__name__))
        return 3
    if "error" in login:
        print("login failed: error %s" % login["error"])
        return 1
    sid = login.get("eid")
    print("login OK")

    listing = call("core/search_items", {
        "spec": {"itemsType": "avl_unit", "propName": "sys_name",
                 "propValueMask": "*", "sortType": "sys_name"},
        "force": 1, "flags": FLAG_NAME_AND_LAST_POSITION, "from": 0, "to": 0}, sid)
    if "error" in listing:
        print("unit list failed: error %s" % listing["error"])
        return 1

    now = datetime.now(TZ)
    rows = []
    for item in (listing.get("items") or []):
        if not item.get("id"):
            continue
        position = item.get("pos") or {}
        stamp = position.get("t")
        if stamp:
            last = datetime.fromtimestamp(stamp, TZ)
            days = (now - last).total_seconds() / 86400.0
            rows.append({"wialon_id": item["id"], "mashina": item.get("nm") or "",
                         "posledneye_soobshchenie": last.strftime("%Y-%m-%d %H:%M"),
                         "dney_nazad": round(days, 1)})
        else:
            # [REASON]: no last position at all is a different fact from an old
            # one -- the object has never reported, or its history was cleared.
            rows.append({"wialon_id": item["id"], "mashina": item.get("nm") or "",
                         "posledneye_soobshchenie": "", "dney_nazad": ""})
    call("core/logout", {}, sid)

    rows.sort(key=lambda r: (r["dney_nazad"] == "", -float(r["dney_nazad"] or 0)))
    path = os.path.join(OUT, args.out)
    with open(path, "w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, delimiter=";", fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    never = [r for r in rows if r["dney_nazad"] == ""]
    silent = [r for r in rows if r["dney_nazad"] != ""
              and float(r["dney_nazad"]) >= args.quiet_days]
    alive = len(rows) - len(never) - len(silent)
    print("objects: %d | reported within %d days: %d | silent longer: %d | never: %d"
          % (len(rows), args.quiet_days, alive, len(silent), len(never)))
    print("\nsilent longest first:")
    for r in (silent + never)[:60]:
        print("  %-8s %-42s %s"
              % (r["wialon_id"], ascii_only(r["mashina"])[:42],
                 ("%s (%s dney nazad)" % (r["posledneye_soobshchenie"],
                                          r["dney_nazad"]))
                 if r["dney_nazad"] != "" else "NIKOGDA ne soobshchal"))
    print("\nfile: %s" % args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
