# -*- coding: utf-8 -*-
"""A Wialon server made of canned answers, plus the helpers the tests share.

The collector's own code runs end to end against this -- login, unit list,
message loading, unloading, the point files and the watermark. Only the socket
is replaced.
"""

import json
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

TZ = timezone(timedelta(hours=5))

# A lonely point near Bukhara: longitude 64.4, latitude 39.7. Kept apart by a
# whole degree so a swapped pair cannot look plausible.
LON, LAT = 64.4123, 39.7456

# [REASON]: one fleet name carries U+04B2, the Uzbek letter that killed the
# fleet run of 18.08 when it reached a cp1251 console. Half this fleet is
# named so, and the collector prints names.
FLEET_NAMES = {101: "МТЗ 261 EA", 102: "Комбайн 741 KA (Ҳокимият)",
               103: "Doosan 80 553 HA"}


def epoch(text):
    """'2026-08-10 06:00' local (UTC+5) -> epoch seconds."""
    return int(datetime.strptime(text, "%Y-%m-%d %H:%M")
               .replace(tzinfo=TZ).timestamp())


def local(stamp):
    return datetime.fromtimestamp(int(stamp), TZ)


class FakeResponse:
    def __init__(self, payload):
        self._raw = json.dumps(payload).encode("utf-8")

    def read(self):
        return self._raw

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeServer:
    """Canned Wialon. Counts what it was asked, so the tests can assert on it.

    `step_s` is the interval between the messages it invents; `cap` truncates
    the answer the way the real loadCount limit does.
    """

    def __init__(self, fleet=None, step_s=30, cap=None, speed=8.0):
        self.fleet = fleet if fleet is not None else [
            {"id": 101, "last_t": None}, {"id": 102, "last_t": None}]
        self.step_s = step_s
        self.cap = cap
        self.speed = speed
        self.calls = []                 # (svc, params) in order
        self.loads = []                 # (unit_id, t_from, t_to)

    # --- what the collector sees ------------------------------------------

    def count(self, svc):
        return sum(1 for name, _ in self.calls if name == svc)

    def messages(self, unit_id, t_from, t_to):
        """One message every step_s seconds inside [t_from, t_to)."""
        out = []
        stamp = t_from - (t_from % self.step_s) + self.step_s
        index = 0
        while stamp < t_to:
            out.append({"t": stamp,
                        "pos": {"x": LON + index * 1e-5, "y": LAT + index * 1e-5,
                                "s": self.speed, "c": 90, "sc": 14}})
            stamp += self.step_s
            index += 1
            if self.cap is not None and len(out) >= self.cap:
                break
        return out

    # --- the socket --------------------------------------------------------

    def urlopen(self, request, timeout=None):
        query = urllib.parse.parse_qs(request.data.decode("utf-8"))
        svc = query["svc"][0]
        params = json.loads(query["params"][0])
        self.calls.append((svc, params))
        if svc == "token/login":
            return FakeResponse({"eid": "SESSION"})
        if svc == "core/search_items":
            items = []
            for unit in self.fleet:
                item = {"id": unit["id"],
                        "nm": FLEET_NAMES.get(unit["id"], "unit %s" % unit["id"])}
                if unit.get("last_t") is not None:
                    item["pos"] = {"t": unit["last_t"]}
                items.append(item)
            return FakeResponse({"items": items})
        if svc == "messages/load_interval":
            self.loads.append((params["itemId"], params["timeFrom"],
                               params["timeTo"]))
            found = self.messages(params["itemId"], params["timeFrom"],
                                  params["timeTo"])
            if not found:
                return FakeResponse({"error": 1001})
            return FakeResponse({"messages": found})
        return FakeResponse({})


class Installed:
    """`with Installed(server):` -- urlopen answers from the fake for a while."""

    def __init__(self, server):
        self.server = server
        self.original = None

    def __enter__(self):
        self.original = urllib.request.urlopen
        urllib.request.urlopen = self.server.urlopen
        return self.server

    def __exit__(self, *exc):
        urllib.request.urlopen = self.original
        return False


def count_points(folder, month, unit_id=None):
    """Rows in one monthly file, optionally for one object."""
    import os
    import sqlite3
    from gps_collector import storage
    path = storage.points_path(folder, month)
    if not os.path.exists(path):
        return 0
    con = sqlite3.connect(path)
    try:
        if unit_id is None:
            return con.execute("SELECT COUNT(*) FROM points").fetchone()[0]
        return con.execute("SELECT COUNT(*) FROM points WHERE unit_id = ?",
                           (int(unit_id),)).fetchone()[0]
    finally:
        con.close()
