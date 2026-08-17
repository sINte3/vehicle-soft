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
  run 4: one object stops answering for ever. The run must abandon it and
         finish, reporting that object's days as unloaded rather than healthy
  run 5: the levers that make a sweep affordable -- --only/--skip narrow the
         fleet, --hours narrows the day -- and a ledger written over one window
         is not reused for another, or the report would mix measurements

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
import time
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
        self.intervals = []

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
            self.intervals.append((params["timeFrom"], params["timeTo"]))
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


class StallingServer(Server):
    """Answers, then goes quiet for ever on one object -- the case of 16.08.

    The run stopped at object 63 and stood for over a day without printing a
    single line: not a retry notice, not a limit notice. Every reporting path
    was therefore ruled out, which leaves a read that never returns. urlopen's
    timeout is per socket operation, so a peer that keeps the connection warm
    holds the whole run for ever.
    """

    def __init__(self, stall_on_unit):
        super().__init__()
        self.stall_on = stall_on_unit
        self.stalled = 0

    def urlopen(self, request, timeout=None):
        query = urllib.parse.parse_qs(request.data.decode("utf-8"))
        if query["svc"][0] == "messages/load_interval":
            params = json.loads(query["params"][0])
            if params["itemId"] == self.stall_on:
                self.stalled += 1
                time.sleep(30.0)        # far past the hard deadline in the test
        return super().urlopen(request, timeout)


def stall_test(probe):
    """The run must survive an object whose answers never come."""
    passed = True
    probe.HARD_DEADLINE_S = 0.5
    probe.RETRY_PAUSE_S = (0.0, 0.0, 0.0)
    probe.ABANDONED.clear()
    with tempfile.TemporaryDirectory() as workdir:
        server = StallingServer(stall_on_unit=UNITS[1])
        started = time.monotonic()
        code = run(probe, workdir, server, list(PERIOD_ONE))
        spent = time.monotonic() - started
        passed &= check(code == 0, "stalling object did not stop the run")
        passed &= check(spent < 25.0,
                        "run finished in %.1f s, not held by the dead object"
                        % spent)
        passed &= check(len(probe.ABANDONED) > 0,
                        "abandoned requests counted: %d" % len(probe.ABANDONED))
        rows = csv_rows(workdir)
        passed &= check(len(rows) == len(UNITS),
                        "all %d objects still in the report (%d)"
                        % (len(UNITS), len(rows)))
        sick = [r for r in rows if str(UNITS[1]) == r["wialon_id"]]
        passed &= check(bool(sick) and "dney ne zagruzilos" in sick[0]["chto_ne_tak"],
                        "the dead object is reported as unloaded, not as healthy: %s"
                        % (sick[0]["chto_ne_tak"] if sick else "missing"))
    return passed


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

    # run 5 -- the levers that make a sweep affordable must actually pull:
    # --only narrows the fleet, --hours narrows the day, and a ledger written
    # over one window must not be reused for another
    with tempfile.TemporaryDirectory() as workdir:
        narrow = Server()
        run(probe, workdir, narrow,
            ["--only", "MASHINA 10", "--skip", "103", "--hours", "8-14"]
            + PERIOD_ONE)
        passed &= check(narrow.objects_asked() == [101, 102, 104, 105],
                        "--only/--skip left %s" % narrow.objects_asked())
        spans = {finish - start for start, finish in narrow.intervals}
        passed &= check(spans == {6 * 3600},
                        "--hours asked for 6-hour windows, got %s"
                        % [int(v) for v in spans])
        after = Server()
        run(probe, workdir, after,
            ["--only", "MASHINA 10", "--skip", "103", "--hours", "9-15",
             "--resume"] + PERIOD_ONE)
        passed &= check(after.objects_asked() == [101, 102, 104, 105],
                        "another window is measured again, not resumed: %s"
                        % after.objects_asked())

    # run 4 -- an object that answers nothing at all must not hold the run
    passed &= stall_test(probe)

    print("RESULT: %s" % ("OK" if passed else "FAIL"))
    return 0 if passed else 1


probe_ledger = "gps_fleet_health.partial.jsonl"

if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--child":
        sys.exit(child_gets_killed(sys.argv[2]))
    sys.exit(main())
