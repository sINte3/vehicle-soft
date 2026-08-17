# -*- coding: utf-8 -*-
"""How long does ONE message request really take, and what makes it faster.

WHY
The fleet sweep was planned at 44 seconds per object -- eleven sampled days,
two requests each, two seconds of pacing apiece. The real run of 16-18.08 did
183 objects in about 48 hours: sixteen minutes per object, twenty times the
estimate. The estimate counted only the pauses, as if the server answered
instantly. It does not, and nothing in the code measured that.

This tool measures it, on the actual objects, before another three days are
spent. Four variants per object:

  A  whole day, loadCount 20000   -- what the fleet probe does now
  B  whole day, loadCount 3000    -- is the payload the cost?
  C  window 08-14, loadCount 20000 -- is the scanned interval the cost?
  D  window 08-14, loadCount 3000  -- both

If A and C are alike, the interval is not the cost and narrowing the window
buys nothing. If B beats A, the cost is in shipping messages. The numbers
decide, not the guess.

READ-ONLY. Services: token/login, core/search_items, messages/load_interval,
messages/unload, core/logout.

Run (PowerShell, one command per line):

  cd C:\\diag\\wialon
  & "C:\\Program Files\\Python314\\python.exe" C:\\diag\\wialon\\wialon_measure_cost.py --unit "damas 80 331 nba" --unit "mtz 261" --day 2026-08-05

Takes about five minutes. Console output is ASCII.
"""

import argparse
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
HARD_DEADLINE_S = 200.0
TZ = timezone(timedelta(hours=5))
HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.getcwd()
PAUSE_S = 2.0

VARIANTS = [("A", None, 20000), ("B", None, 3000),
            ("C", (8, 14), 20000), ("D", (8, 14), 3000)]


# [REASON]: the charter says console output is ASCII, and this is the run that
# proved why: on 18.08 the fleet probe died on "Niva 80 350 SBA (Hokimiyat)"
# because U+04B2 has no place in cp1251. Half this fleet is named in Uzbek
# Cyrillic. Files keep the real name; the console gets a transliteration.
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
    """Anything printable, whatever the console code page is."""
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
        return {"error": "deadline"}, HARD_DEADLINE_S, 0
    if "error" in box:
        return {"error": str(box["error"])}, 0.0, 0
    raw = box.get("raw", "")
    try:
        return json.loads(raw), 0.0, len(raw)
    except ValueError:
        return {"_unparsed": raw[:200]}, 0.0, len(raw)


def timed(svc, params, sid=None):
    started = time.monotonic()
    answer, _, size = call(svc, params, sid)
    return answer, time.monotonic() - started, size


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--unit", action="append", default=[], required=True,
                        help="part of a machine name; may repeat")
    parser.add_argument("--day", required=True, help="one day, YYYY-MM-DD")
    args = parser.parse_args()

    day = datetime.strptime(args.day, "%Y-%m-%d").replace(tzinfo=TZ)
    token = read_token()
    login, _, _ = timed("token/login", {"token": token})
    if "error" in login:
        print("login failed: %s" % login["error"])
        return 1
    sid = login.get("eid")
    print("login OK")

    listing, _, _ = timed("core/search_items", {
        "spec": {"itemsType": "avl_unit", "propName": "sys_name",
                 "propValueMask": "*", "sortType": "sys_name"},
        "force": 1, "flags": 1, "from": 0, "to": 0}, sid)
    masks = [m.strip().lower() for m in args.unit if m.strip()]
    units = [(item.get("id"), item.get("nm") or "")
             for item in (listing.get("items") or [])
             if item.get("id") and any(m in (item.get("nm") or "").lower()
                                       for m in masks)]
    if not units:
        print("nothing matched the masks")
        call("core/logout", {}, sid)
        return 1
    print("measuring on %d object(s), day %s\n" % (len(units), args.day))

    results = {}
    print("%-30s %-4s %8s %9s %10s" % ("object", "var", "sec", "messages", "bytes"))
    for unit_id, name in units[:3]:
        for tag, window, load_count in VARIANTS:
            if window is None:
                start = int(day.timestamp())
                finish = int((day + timedelta(days=1)).timestamp())
            else:
                start = int((day + timedelta(hours=window[0])).timestamp())
                finish = int((day + timedelta(hours=window[1])).timestamp())
            time.sleep(PAUSE_S)
            answer, seconds, size = timed(
                "messages/load_interval",
                {"itemId": unit_id, "timeFrom": start, "timeTo": finish,
                 "flags": 0, "flagsMask": 0, "loadCount": load_count}, sid)
            count = len(answer.get("messages") or []) if isinstance(answer, dict) else 0
            note = "" if "error" not in answer else " ERROR %s" % answer["error"]
            print("%-30s %-4s %8.1f %9d %10d%s"
                  % (ascii_only(name)[:30], tag, seconds, count, size, note),
                  flush=True)
            results.setdefault(tag, []).append(seconds)
            time.sleep(PAUSE_S)
            call("messages/unload", {}, sid)

    call("core/logout", {}, sid)

    print("\naverage seconds per request:")
    for tag, _, _ in VARIANTS:
        values = results.get(tag) or [0]
        print("  %s  %.1f s" % (tag, sum(values) / len(values)))

    best = min(results, key=lambda t: sum(results[t]) / len(results[t]))
    slowest = max(results, key=lambda t: sum(results[t]) / len(results[t]))
    gain = (sum(results[slowest]) / len(results[slowest])) / \
           max(0.1, sum(results[best]) / len(results[best]))
    print("\nfastest variant: %s, %.1f times quicker than %s" % (best, gain, slowest))
    flags = {"A": "(nothing to change)",
             "B": "--max-messages 3000",
             "C": "--hours 8-14",
             "D": "--hours 8-14 --max-messages 3000"}
    print("flags for wialon_probe6_fleet_health.py: %s" % flags[best])
    print("\nSend this whole output back -- the choice is made from these numbers,\n"
          "not from the flag name.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
