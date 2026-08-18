# -*- coding: utf-8 -*-
"""Self-test for the last-seen probe -- no network, canned answers.

What must hold: exactly ONE request loads the fleet (this tool exists because
it is cheap, and a loop over objects would destroy that), the last-message
time is read from the answer rather than invented, an object that never
reported is reported as never rather than as very old, and a name outside
cp1251 does not kill the console.

Run:
  & "C:\\Program Files\\Python314\\python.exe" C:\\transport-report\\tools\\test_wialon_last_seen.py
"""

import csv
import importlib.util
import io
import json
import os
import sys
import tempfile
import urllib.parse
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
TZ = timezone(timedelta(hours=5))


def load_tool():
    path = os.path.join(HERE, "wialon_last_seen.py")
    spec = importlib.util.spec_from_file_location("lastseen", path)
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
    def __init__(self, now):
        self.calls = []
        self.now = now

    def urlopen(self, request, timeout=None):
        query = urllib.parse.parse_qs(request.data.decode("utf-8"))
        svc = query["svc"][0]
        self.calls.append((svc, json.loads(query["params"][0])))
        if svc == "token/login":
            return FakeResponse({"eid": "SESSION"})
        if svc == "core/search_items":
            yesterday = self.now - timedelta(days=1)
            long_ago = self.now - timedelta(days=97)
            return FakeResponse({"items": [
                {"id": 101, "nm": "МТЗ 261 EA",
                 "pos": {"t": int(yesterday.timestamp()), "x": 64.4, "y": 39.7}},
                {"id": 102, "nm": "JohnDeere 80 542 JA (Ҳокимият)",
                 "pos": {"t": int(long_ago.timestamp()), "x": 64.4, "y": 39.7}},
                {"id": 103, "nm": "МТЗ 226 HA"},
            ]})
        return FakeResponse({})


def check(condition, message):
    print("%-5s %s" % ("OK" if condition else "FAIL", message))
    return bool(condition)


def main():
    os.environ.setdefault("WIALON_TOKEN", "test-token-not-a-real-one")
    tool = load_tool()
    passed = True
    now = datetime.now(TZ)

    with tempfile.TemporaryDirectory() as workdir:
        tool.OUT = workdir
        server = Server(now)
        import urllib.request
        original = urllib.request.urlopen
        urllib.request.urlopen = server.urlopen
        saved = sys.argv
        sys.argv = ["lastseen"]
        narrow = io.TextIOWrapper(io.BytesIO(), encoding="cp1251", errors="strict")
        real_stdout = sys.stdout
        sys.stdout = narrow
        try:
            code = tool.main()
            crashed = None
        except UnicodeEncodeError as problem:
            code, crashed = None, problem
        finally:
            sys.stdout = real_stdout
            urllib.request.urlopen = original
            sys.argv = saved

        passed &= check(crashed is None,
                        "a name outside cp1251 did not stop the probe (%s)" % crashed)
        passed &= check(code == 0, "finished with code 0")
        searches = [c for c in server.calls if c[0] == "core/search_items"]
        passed &= check(len(searches) == 1,
                        "the whole fleet took ONE request, not %d" % len(searches))
        passed &= check(searches and searches[0][1]["flags"] == 1025,
                        "asked for name + last position only (flags=%s)"
                        % (searches[0][1]["flags"] if searches else "none"))
        loads = [c for c in server.calls if c[0].startswith("messages/")]
        passed &= check(not loads,
                        "no messages loaded at all, got %s" % [c[0] for c in loads])

        with open(os.path.join(workdir, "gps_last_seen.csv"),
                  encoding="utf-8-sig", newline="") as fh:
            rows = {r["wialon_id"]: r for r in csv.DictReader(fh, delimiter=";")}
        passed &= check(len(rows) == 3, "every object written: %d" % len(rows))
        passed &= check(abs(float(rows["101"]["dney_nazad"]) - 1.0) < 0.1,
                        "a machine heard yesterday is 1 day ago, got %s"
                        % rows["101"]["dney_nazad"])
        passed &= check(abs(float(rows["102"]["dney_nazad"]) - 97.0) < 0.1,
                        "a tracker silent since May is 97 days ago, got %s"
                        % rows["102"]["dney_nazad"])
        passed &= check(rows["103"]["dney_nazad"] == "",
                        "an object that never reported is not given a date, got %r"
                        % rows["103"]["dney_nazad"])
        passed &= check(rows["102"]["mashina"].endswith(")"),
                        "the csv keeps the real name, not the transliteration: %s"
                        % rows["102"]["mashina"])

    print("RESULT: %s" % ("OK" if passed else "FAIL"))
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
