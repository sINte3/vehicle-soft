# -*- coding: utf-8 -*-
"""The point store: monthly split, the (unit_id, t) key, and the watermark.

Run:
  python -m unittest discover -s gps_collector/tests -t .
"""

import os
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from gps_collector import storage                                  # noqa: E402
from gps_collector.tests.support import epoch, LAT, LON            # noqa: E402


def row(unit_id, stamp, index=0):
    return (unit_id, stamp, LON + index * 1e-5, LAT + index * 1e-5, 8.0, 90, 14)


class MonthKey(unittest.TestCase):
    def test_local_evening_of_the_last_day_stays_in_its_month(self):
        # 2026-08-01 00:10 local is 2026-07-31 19:10 UTC. A UTC month key would
        # file this point under July; the hectares it belongs to are booked on
        # 1 August.
        self.assertEqual(storage.month_key(epoch("2026-08-01 00:10")), "202608")
        self.assertEqual(storage.month_key(epoch("2026-07-31 23:50")), "202607")

    def test_boundary_is_exactly_midnight_local(self):
        self.assertEqual(storage.month_key(epoch("2026-08-01 00:00")), "202608")
        self.assertEqual(storage.month_key(epoch("2026-08-01 00:00") - 1),
                         "202607")


class WritePoints(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.mkdtemp()

    def test_repeat_write_of_the_same_points_inserts_nothing(self):
        rows = [row(101, epoch("2026-07-27 06:00") + 30 * i, i) for i in range(50)]
        self.assertEqual(storage.write_points(self.folder, rows), 50)
        self.assertEqual(storage.write_points(self.folder, rows), 0)

    def test_a_second_object_at_the_same_second_is_a_different_row(self):
        stamp = epoch("2026-07-27 06:00")
        self.assertEqual(
            storage.write_points(self.folder, [row(101, stamp), row(102, stamp)]),
            2)

    def test_an_interval_crossing_the_first_of_the_month_is_split(self):
        rows = ([row(101, epoch("2026-07-31 23:00") + 60 * i, i) for i in range(60)]
                + [row(101, epoch("2026-08-01 00:00") + 60 * i, i) for i in range(60)])
        self.assertEqual(storage.write_points(self.folder, rows), 120)
        july = storage.points_path(self.folder, "202607")
        august = storage.points_path(self.folder, "202608")
        self.assertTrue(os.path.exists(july) and os.path.exists(august))
        import sqlite3
        for path, expected in ((july, 60), (august, 60)):
            con = sqlite3.connect(path)
            try:
                self.assertEqual(
                    con.execute("SELECT COUNT(*) FROM points").fetchone()[0],
                    expected, path)
            finally:
                con.close()

    def test_read_day_returns_that_day_only_and_in_order(self):
        rows = ([row(101, epoch("2026-07-26 22:00") + 60 * i, i) for i in range(30)]
                + [row(101, epoch("2026-07-27 06:00") + 60 * i, i) for i in range(40)]
                + [row(102, epoch("2026-07-27 06:00") + 60 * i, i) for i in range(10)])
        storage.write_points(self.folder, rows)
        day = storage.read_day(self.folder, 101, "2026-07-27")
        self.assertEqual(len(day), 40)
        self.assertEqual([p[0] for p in day], sorted(p[0] for p in day))
        self.assertEqual(storage.units_with_points(self.folder, "2026-07-27"),
                         [101, 102])

    def test_read_day_of_a_month_never_collected_is_empty_not_an_error(self):
        self.assertEqual(storage.read_day(self.folder, 101, "2025-01-05"), [])
        self.assertEqual(storage.units_with_points(self.folder, "2025-01-05"), [])


class Watermark(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.mkdtemp()

    def test_unknown_object_has_no_watermark(self):
        self.assertIsNone(storage.read_watermark(self.folder, 101))

    def test_watermark_is_stored_and_replaced_not_appended(self):
        storage.set_watermark(self.folder, 101, 1000)
        storage.set_watermark(self.folder, 101, 2000)
        self.assertEqual(storage.read_watermark(self.folder, 101), 2000)
        con = storage.open_state(self.folder)
        try:
            self.assertEqual(con.execute(
                "SELECT COUNT(*) FROM collector_watermarks").fetchone()[0], 1)
        finally:
            con.close()

    def test_watermarks_live_outside_the_monthly_files(self):
        # Retention deletes a month; the watermarks must survive it, or the
        # next run would re-fetch the whole history for every object.
        storage.write_points(self.folder,
                             [row(101, epoch("2026-07-27 06:00"))])
        storage.set_watermark(self.folder, 101, epoch("2026-07-27 06:00"))
        os.remove(storage.points_path(self.folder, "202607"))
        self.assertEqual(storage.read_watermark(self.folder, 101),
                         epoch("2026-07-27 06:00"))


if __name__ == "__main__":
    unittest.main()
