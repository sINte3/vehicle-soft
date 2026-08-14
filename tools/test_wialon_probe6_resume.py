# -*- coding: utf-8 -*-
"""Self-test for the resume of the fleet health probe -- no network involved.

The probe now writes every finished object to a ledger and can be restarted
with --resume. That is worth proving rather than believing: a resume that
silently loses objects would produce a report with holes, and nothing in the
report would say so.

The test replaces urllib's urlopen with canned answers, so the probe's own
code runs end to end -- login, unit list, message loading, unloading, the
ledger and the CSV.

  run 1: the run is KILLED after three objects -- a child process ended with
         os._exit, which skips every buffer Python would otherwise flush on
         the way out. Anything less would pass even without the flush after
         each object, and that was checked: removing the flush left an
         interrupt-by-exception test green.
  run 2: --resume must NOT ask the server about those three again, and must
         still produce a report with all five
  run 3: --resume over a DIFFERENT period must ignore the old ledger rows and
         measure everything again -- the negative control, without which the
         test would pass on a resume that mixes periods

Run (PowerShell):
  & "C:\\Program Files\\Python314\\python.exe" C:\\transport-report\\tools\\test_wialon_probe6_resume.py

Output is ASCII.
"""

import csv
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))

UNITS = [101, 102, 103, 104, 105]
POINTS_PER_DAY = 250
PERIOD_ONE = ["--from", "2026-06-01", "--to", "2026-06-03",
              "--every", "1", "--pause", "0"]


def load_probe():
    path = os.path.join(HERE, "wialon_probe6_fleet_health.py")
    spec = importlib.util.spec_from_file_location("probe6", path)
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


def day_of_messages():
    """A quiet working day: enough points to be judged, nothing wrong in it."""
    messages = []
    for step in range(POINTS_PER_DAY):
        messages.append({"t": 1750000000 + step * 10,
                         "pos": {"x": 64.4 + step * 0.00005,
                                 "y": 39.7 + step * 0.00005,
                                 "s": 7, "sc": 17}})
    return messages


KILL_CODE = 9


class Server:
    """Counts what was actually asked of it. That count is the evidence."""

    def __init__(self, kill_after_objects=None):
        self.kill_after = kill_after_objects
        self.loaded_for = []

    def urlopen(self, request, timeout=None):
        query = urllib.parse.parse_qs(request.data.decode("utf-8"))
        svc = query["svc"][0]
        if svc == "token/login":
            return FakeResponse({"eid": "SESSION"})
        if svc == "core/search_items":
            return FakeResponse({"items": [{"id": uid, "nm": "MASHINA %d" % uid}
                                           for uid in UNITS]})
        if svc == "messages/load_interval":
            params = json.loads(query["params"][0])
            unit = params["itemId"]
            if (self.kill_after is not None
                    and unit not in self.loaded_for
                    and len(set(self.loaded_for)) >= self.kill_after):
                # [REASON]: os._exit, not an exception. An exception unwinds
                # and Python flushes the ledger on the way out, so the test
                # would pass even if the probe never flushed on its own.
                sys.stdout.flush()
                os._exit(KILL_CODE)
            self.loaded_for.append(unit)
            return FakeResponse({"messages": day_of_messages()})
        return FakeResponse({})

    def objects_asked(self):
        return sorted(set(self.loaded_for))


def run(probe, workdir, server, argv):
    probe.OUT = workdir
    probe.log.clear()
    import urllib.request
    original = urllib.request.urlopen
    urllib.request.urlopen = server.urlopen
    saved_argv = sys.argv
    sys.argv = ["probe6"] + argv
    try:
        return probe.main()
    finally:
        urllib.request.urlopen = original
        sys.argv = saved_argv


def ledger_rows(workdir):
    path = os.path.join(workdir, probe_ledger)
    if not os.path.isfile(path):
        return []
    with open(path, encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def csv_rows(workdir, name="gps_fleet_health.csv"):
    with open(os.path.join(workdir, name), encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh, delimiter=";"))


def check(condition, message):
    print("%-5s %s" % ("OK" if condition else "FAIL", message))
    return bool(condition)


def child_gets_killed(workdir):
    """Runs the probe until the fake server kills the process outright."""
    run(load_probe(), workdir, Server(kill_after_objects=3), list(PERIOD_ONE))
    return 1                       # reached only if the kill never happened


def main():
    os.environ.setdefault("WIALON_TOKEN", "test-token-not-a-real-one")
    probe = load_probe()
    global probe_ledger
    probe_ledger = probe.LEDGER
    period_one = list(PERIOD_ONE)
    passed = True

    with tempfile.TemporaryDirectory() as workdir:
        # run 1 -- the process is killed after three objects, in a child, so
        # nothing flushes the ledger except the probe itself
        killed = subprocess.run(
            [sys.executable, os.path.abspath(__file__), "--child", workdir],
            capture_output=True, text=True)
        passed &= check(killed.returncode == KILL_CODE,
                        "run 1: child killed hard, exit %s (want %d)"
                        % (killed.returncode, KILL_CODE))
        after_crash = ledger_rows(workdir)
        passed &= check(len(after_crash) == 3,
                        "run 1: ledger holds %d finished objects (want 3)"
                        % len(after_crash))
        passed &= check(all(row["worked_days"] == 3 for row in after_crash),
                        "run 1: every ledger row counted its 3 working days")

        # run 2 -- resume must not pay for the first three again
        resumed = Server()
        code = run(probe, workdir, resumed, period_one + ["--resume"])
        passed &= check(code == 0, "run 2: finished with code 0")
        asked = resumed.objects_asked()
        passed &= check(asked == UNITS[3:],
                        "run 2: server was asked only about %s (want %s)"
                        % (asked, UNITS[3:]))
        report = csv_rows(workdir)
        passed &= check(len(report) == len(UNITS),
                        "run 2: report holds all %d objects (got %d)"
                        % (len(UNITS), len(report)))
        passed &= check(sorted(int(row["wialon_id"]) for row in report) == UNITS,
                        "run 2: report holds exactly the fleet, no holes")
        passed &= check(all(row["rabochih_dney"] == "3" for row in report),
                        "run 2: resumed rows kept their measurements")

        # run 3 -- negative control: another period must not be resumed from
        # the old ledger, otherwise a report would silently mix two periods
        other = Server()
        run(probe, workdir, other,
            ["--from", "2026-07-01", "--to", "2026-07-03", "--every", "1",
             "--pause", "0", "--resume"])
        passed &= check(other.objects_asked() == UNITS,
                        "run 3: a different period re-measured all %d objects"
                        % len(UNITS))

    print("RESULT: %s" % ("OK" if passed else "FAIL"))
    return 0 if passed else 1


probe_ledger = "gps_fleet_health.partial.jsonl"

if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--child":
        sys.exit(child_gets_killed(sys.argv[2]))
    sys.exit(main())
