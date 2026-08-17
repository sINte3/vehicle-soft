# -*- coding: utf-8 -*-
"""Self-test for the track puller -- no network, canned answers.

Three things are worth proving before this runs against the live server for an
hour: that a name mask selects what it should and nothing else, that longitude
and latitude land in the columns they are named after, and that --resume skips
machine-days already in the file.

The lat/lon check is not paranoia. Wialon calls longitude `x` and latitude `y`,
the csv calls them `lat` and `lon`, and a swap produces a file that looks
perfectly well-formed while putting every field in Bukhara somewhere off the
coast of Somalia.

Run:
  & "C:\\Program Files\\Python314\\python.exe" C:\\transport-report\\tools\\test_wialon_pull_tracks.py
"""

import csv
import importlib.util
import io
import json
import os
import sys
import tempfile
import urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))

# a lonely point near Bukhara: longitude 64.4, latitude 39.7
LON, LAT = 64.4123, 39.7456
# [REASON]: one name carries U+04B2, the Uzbek letter that killed the fleet
# run of 18.08 when it reached the console. Half this fleet is named so.
UNITS = [(101, "МТЗ 261 EA"), (102, "Комбайн 741 KA (Ҳокимият)"),
         (103, "МТЗ 873 GA")]


def load_tool():
    path = os.path.join(HERE, "wialon_pull_tracks.py")
    spec = importlib.util.spec_from_file_location("puller", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeResponse:
    def __init__(self, payload):
        self._raw = json.dumps(payload).encode("utf-8")

    def read(self):
        return self._raw

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class Server:
    def __init__(self):
        self.loaded = []

    def urlopen(self, request, timeout=None):
        query = urllib.parse.parse_qs(request.data.decode("utf-8"))
        svc = query["svc"][0]
        if svc == "token/login":
            return FakeResponse({"eid": "SESSION"})
        if svc == "core/search_items":
            return FakeResponse({"items": [{"id": uid, "nm": name}
                                           for uid, name in UNITS]})
        if svc == "messages/load_interval":
            params = json.loads(query["params"][0])
            self.loaded.append(params["itemId"])
            return FakeResponse({"messages": [
                {"t": params["timeFrom"] + 3600 + step * 30,
                 "pos": {"x": LON, "y": LAT, "s": 7, "c": 180, "sc": 16}}
                for step in range(5)]})
        return FakeResponse({})


def run(tool, workdir, server, argv):
    tool.OUT = workdir
    import urllib.request
    original = urllib.request.urlopen
    urllib.request.urlopen = server.urlopen
    saved = sys.argv
    sys.argv = ["puller"] + argv
    try:
        return tool.main()
    finally:
        urllib.request.urlopen = original
        sys.argv = saved


def check(condition, message):
    print("%-5s %s" % ("OK" if condition else "FAIL", message))
    return bool(condition)


def main():
    os.environ.setdefault("WIALON_TOKEN", "test-token-not-a-real-one")
    tool = load_tool()
    passed = True
    period = ["--from", "2026-08-01", "--to", "2026-08-02", "--pause", "0"]

    with tempfile.TemporaryDirectory() as workdir:
        out = os.path.join(workdir, "label_tracks.csv")
        server = Server()
        # a console that cannot encode the fleet's own names, as on Windows
        narrow = io.TextIOWrapper(io.BytesIO(), encoding="cp1251", errors="strict")
        real_stdout = sys.stdout
        sys.stdout = narrow
        try:
            code = run(tool, workdir, server,
                       ["--unit", "261 EA", "--unit", "комбайн"] + period)
            crashed = None
        except UnicodeEncodeError as problem:
            code, crashed = None, problem
        finally:
            sys.stdout = real_stdout
        passed &= check(crashed is None,
                        "a name outside cp1251 did not stop the pull (%s)" % crashed)
        passed &= check(code == 0, "run finished with code 0")
        passed &= check(sorted(set(server.loaded)) == [101, 102],
                        "mask picked exactly the two machines meant, got %s"
                        % sorted(set(server.loaded)))
        with open(out, encoding="utf-8-sig") as fh:
            rows = list(csv.DictReader(fh, delimiter=";"))
        passed &= check(len(rows) == 2 * 2 * 5,
                        "every machine-day written: %d rows (want 20)" % len(rows))
        passed &= check(all(abs(float(r["lat"]) - LAT) < 1e-9 for r in rows),
                        "latitude is in the lat column")
        passed &= check(all(abs(float(r["lon"]) - LON) < 1e-9 for r in rows),
                        "longitude is in the lon column")
        passed &= check(all(r["sats"] == "16" for r in rows), "satellites kept")
        passed &= check(sorted({r["date"] for r in rows}) ==
                        ["2026-08-01", "2026-08-02"], "both days present")

        # resume: nothing left to do, so the server must not be asked again
        again = Server()
        run(tool, workdir, again,
            ["--unit", "261 EA", "--unit", "комбайн", "--resume"] + period)
        passed &= check(again.loaded == [],
                        "resume asked for nothing, got %s" % again.loaded)
        with open(out, encoding="utf-8-sig") as fh:
            after = list(csv.DictReader(fh, delimiter=";"))
        passed &= check(len(after) == len(rows),
                        "resume did not duplicate rows: %d" % len(after))

    print("RESULT: %s" % ("OK" if passed else "FAIL"))
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
