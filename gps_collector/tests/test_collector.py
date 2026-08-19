# -*- coding: utf-8 -*-
"""The four acceptance criteria of GPS-1, and the guards around them.

Criteria (docs/GPS_PLAN_FAKT_VISION_ROADMAP.md section 5.1):
  1. a repeat run over the same interval adds not one row;
  2. a failure between loading and writing does not move the watermark --
     checked by killing the process between those two steps;
  3. a night over the fleet holds the agreed pace and logs in exactly once;
  4. points of a month land in that month's file, and an interval crossing
     midnight of the first neither loses nor duplicates a point.

Run:
  python -m unittest discover -s gps_collector/tests -t .
"""

import os
import subprocess
import sys
import tempfile
import time
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from gps_collector import config, storage                          # noqa: E402
from gps_collector.main import collect                             # noqa: E402
from gps_collector.tests.support import (FakeServer, Installed,    # noqa: E402
                                         LAT, LON, count_points, epoch)
from gps_collector.wialon import Client                            # noqa: E402

NOW = epoch("2026-08-10 07:00")
QUIET = lambda *args, **kwargs: None                               # noqa: E731


def run_once(server, folder, now=NOW, **kwargs):
    with Installed(server):
        client = Client(pause=0.0, log=QUIET)
        client.login("test-token-not-a-real-one")
        summary = collect(client, folder, now=now, log=QUIET, **kwargs)
        client.logout()
    return summary, client


class RepeatRun(unittest.TestCase):
    """Criterion 1."""

    def setUp(self):
        self.folder = tempfile.mkdtemp()
        self.server = FakeServer(fleet=[{"id": 101, "last_t": NOW - 3600}])

    def test_second_run_over_the_same_interval_adds_no_rows(self):
        first, _ = run_once(self.server, self.folder)
        self.assertGreater(first.points_written, 0)
        stored = count_points(self.folder, "202608")

        second, _ = run_once(self.server, self.folder)
        self.assertEqual(second.points_written, 0)
        self.assertEqual(count_points(self.folder, "202608"), stored)
        # This one passes because the watermark stops the request. That is the
        # criterion as written, but it says nothing about what happens when an
        # interval IS re-requested -- which the next test does.
        self.assertEqual(second.units_uptodate, 1)

    def test_a_reset_watermark_replays_the_interval_without_duplicating_it(self):
        # The realistic way a repeat happens: a watermark rolled back by hand,
        # or by the crash of criterion 2. The points must not double.
        run_once(self.server, self.folder)
        stored = count_points(self.folder, "202608")
        storage.set_watermark(self.folder, 101, NOW - 6 * 3600)
        again, _ = run_once(self.server, self.folder)
        self.assertEqual(count_points(self.folder, "202608"), stored)
        self.assertEqual(again.points_written, 0)
        self.assertGreater(again.points_seen, 0,
                           "the server was asked again -- otherwise this "
                           "proves nothing about the key")


class CrashBetweenLoadAndWrite(unittest.TestCase):
    """Criterion 2, with its own negative control.

    The child process is killed with os._exit between the two steps. Two runs
    are compared:

      correct order  (points, then watermark) -- watermark must NOT have moved
      wrong order    (watermark, then points) -- watermark HAS moved

    The second case is the defect this design exists to prevent. It is run
    here on purpose: without it, a check that only ever sees the correct code
    could be passing for the wrong reason.
    """

    def child(self, folder, mode):
        return subprocess.run(
            [sys.executable, "-m", "gps_collector.tests.crash_child",
             folder, mode],
            cwd=REPO_ROOT, capture_output=True, text=True, timeout=120)

    def test_correct_order_loses_no_history_and_wrong_order_does(self):
        good = tempfile.mkdtemp()
        killed = self.child(good, "crash")
        self.assertEqual(killed.returncode, 9,
                         "the child was meant to be killed, not to finish: %s"
                         % killed.stdout)
        self.assertIsNone(storage.read_watermark(good, 101),
                          "the watermark moved although no point was written")
        self.assertEqual(count_points(good, "202608"), 0)

        # and the interval is collected in full when the run is repeated
        finished = self.child(good, "normal")
        self.assertEqual(finished.returncode, 0, finished.stderr)
        self.assertGreater(count_points(good, "202608"), 0)
        self.assertIsNotNone(storage.read_watermark(good, 101))

        bad = tempfile.mkdtemp()
        killed = self.child(bad, "wrong-order-crash")
        self.assertEqual(killed.returncode, 9)
        self.assertEqual(count_points(bad, "202608"), 0)
        self.assertIsNotNone(
            storage.read_watermark(bad, 101),
            "the negative control did not reproduce the defect, so the "
            "assertion above proves nothing")


class NightlyRun(unittest.TestCase):
    """Criterion 3: pace and one login for the whole fleet."""

    def setUp(self):
        self.folder = tempfile.mkdtemp()
        self.fleet = [{"id": 100 + i, "last_t": NOW - 3600} for i in range(5)]

    def test_one_login_and_two_requests_per_object(self):
        server = FakeServer(fleet=self.fleet)
        summary, client = run_once(server, self.folder)
        self.assertEqual(summary.units_asked, 5)
        self.assertEqual(client.logins, 1)
        self.assertEqual(server.count("token/login"), 1)
        # load_interval + unload, and nothing else per object
        self.assertEqual(server.count("messages/load_interval"), 5)
        self.assertEqual(server.count("messages/unload"), 5)
        self.assertEqual(client.requests,
                         1 + 1 + 5 * config.REQUESTS_PER_UNIT + 1,
                         "login + unit list + 2 per object + logout")

    def test_the_pace_is_real_seconds_not_a_promise(self):
        server = FakeServer(fleet=self.fleet[:2])
        with Installed(server):
            client = Client(pause=0.05, log=QUIET)
            started = time.monotonic()
            client.login("test-token-not-a-real-one")
            collect(client, self.folder, now=NOW, log=QUIET)
            elapsed = time.monotonic() - started
        # 1 list + 2 objects x 2 requests = 5 paced requests after the login
        self.assertGreaterEqual(elapsed, 5 * 0.05)

    def test_objects_silent_longer_than_the_limit_are_not_asked_about(self):
        fleet = [{"id": 101, "last_t": NOW - 3600},
                 {"id": 102, "last_t": NOW - 40 * 86400},
                 {"id": 103, "last_t": None}]
        server = FakeServer(fleet=fleet)
        summary, _ = run_once(server, self.folder)
        self.assertEqual(summary.units_silent, 2)
        self.assertEqual([load[0] for load in server.loads], [101])

    def test_an_object_already_up_to_date_costs_no_request(self):
        server = FakeServer(fleet=[{"id": 101, "last_t": NOW - 3600}])
        run_once(server, self.folder)
        asked_before = len(server.loads)
        summary, _ = run_once(server, self.folder, now=NOW)
        self.assertEqual(summary.units_uptodate, 1)
        self.assertEqual(len(server.loads), asked_before,
                         "the watermark already stands past what the lag "
                         "allows asking for")


class MonthBoundary(unittest.TestCase):
    """Criterion 4."""

    def setUp(self):
        self.folder = tempfile.mkdtemp()

    def test_an_interval_across_the_first_neither_loses_nor_duplicates(self):
        # The run happens at 01:30 on 1 August and collects back through the
        # end of July: the lag puts t_to at 01:00, the backfill at 01:00 on 31
        # July, so the interval straddles midnight of the first.
        now = epoch("2026-08-01 01:30")
        server = FakeServer(fleet=[{"id": 101, "last_t": now - 600}], step_s=60)
        summary, _ = run_once(server, self.folder, now=now)

        july = count_points(self.folder, "202607")
        august = count_points(self.folder, "202608")
        self.assertGreater(july, 0)
        self.assertGreater(august, 0)
        self.assertEqual(july + august, summary.points_written)
        # 23 hours at a point a minute, plus the first hour of August
        # Literals, not arithmetic on the fake's own step: an expectation
        # written through the constant it tests moves with a mutation and
        # passes on broken code. This project has been bitten by that twice.
        self.assertEqual(july, 1379)          # 31.07 01:01 .. 23:59, a minute apart
        self.assertEqual(august, 60)          # 01.08 00:00 .. 00:59

        # every August point really is in August, and every July one in July
        import sqlite3
        for month, expected in (("202607", "202607"), ("202608", "202608")):
            con = sqlite3.connect(storage.points_path(self.folder, month))
            try:
                stamps = [r[0] for r in con.execute("SELECT t FROM points")]
            finally:
                con.close()
            self.assertTrue(all(storage.month_key(t) == expected for t in stamps),
                            "a point landed in the wrong month's file")

        repeat, _ = run_once(server, self.folder, now=now)
        self.assertEqual(repeat.points_written, 0)


class WatermarkRules(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.mkdtemp()

    def test_watermark_stops_at_the_last_message_when_the_answer_is_truncated(self):
        # [REASON]: an answer cut off by the message limit may have more after
        # it. Moving the watermark to the end of the interval would step over
        # the rest for ever.
        cap = 20
        server = FakeServer(fleet=[{"id": 101, "last_t": NOW - 3600}],
                            step_s=30, cap=cap)
        saved = config.MAX_MESSAGES_PER_INTERVAL
        config.MAX_MESSAGES_PER_INTERVAL = cap
        try:
            summary, _ = run_once(server, self.folder)
        finally:
            config.MAX_MESSAGES_PER_INTERVAL = saved
        self.assertEqual(summary.truncated, 1)
        watermark = storage.read_watermark(self.folder, 101)
        self.assertLess(watermark, NOW - config.LAG_MINUTES * 60,
                        "the watermark jumped past the messages not returned")
        self.assertEqual(summary.points_written, cap)

    def test_a_silent_interval_still_moves_the_watermark(self):
        # The server answered "no messages for this interval". That is a fact
        # about the interval, not a failure: asking again tomorrow for the same
        # empty window would grow the request for ever.
        server = FakeServer(fleet=[{"id": 101, "last_t": NOW - 3600}],
                            step_s=10 ** 9)
        summary, _ = run_once(server, self.folder)
        self.assertEqual(summary.units_empty, 1)
        self.assertEqual(storage.read_watermark(self.folder, 101),
                         NOW - config.LAG_MINUTES * 60)

    def test_a_failed_read_leaves_the_watermark_alone(self):
        class Broken(FakeServer):
            def urlopen(self, request, timeout=None):
                import json
                import urllib.parse
                query = urllib.parse.parse_qs(request.data.decode("utf-8"))
                if query["svc"][0] == "messages/load_interval":
                    self.calls.append(("messages/load_interval",
                                       json.loads(query["params"][0])))
                    raise OSError("connection reset")
                return FakeServer.urlopen(self, request, timeout)

        server = Broken(fleet=[{"id": 101, "last_t": NOW - 3600}])
        saved = config.RETRY_PAUSE_S
        config.RETRY_PAUSE_S = (0, 0, 0)
        try:
            summary, _ = run_once(server, self.folder)
        finally:
            config.RETRY_PAUSE_S = saved
        self.assertEqual(summary.units_failed, 1)
        self.assertIsNone(storage.read_watermark(self.folder, 101))


class Positions(unittest.TestCase):
    def test_longitude_and_latitude_are_not_swapped(self):
        folder = tempfile.mkdtemp()
        server = FakeServer(fleet=[{"id": 101, "last_t": NOW - 3600}])
        run_once(server, folder)
        day = storage.read_day(folder, 101, "2026-08-10")
        self.assertTrue(day)
        for _, lon, lat, speed, sats in day:
            self.assertAlmostEqual(lon, LON, delta=0.1)
            self.assertAlmostEqual(lat, LAT, delta=0.1)
            # A swap is not a near miss: it would put longitude at 39.7 and
            # latitude at 64.4 -- Bukhara moved off the coast of Somalia.
            self.assertGreater(lon, 60.0)
            self.assertLess(lat, 45.0)
            self.assertEqual(sats, 14)
            self.assertAlmostEqual(speed, 8.0)


if __name__ == "__main__":
    unittest.main()
