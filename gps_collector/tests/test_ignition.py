# -*- coding: utf-8 -*-
"""GPS-24 -- the collector stores ignition; the refill brings it to old points.

The «time» method (owner's decision of 09.10.2026: an engine hour is time
with the ignition on) stands on one value per point: the raw value of the
parameter of the object's ignition sensor. GPS-23 found the sensors on
production on 19.10.2026: 621 objects, 620 with an «engine operation» sensor,
parameters io_239 (543), in (76) and spn183 (1).

What is held here:
  * the parameter comes from the unit list, in the same request as before;
  * io_239 is read from `p`, «in» and «inN» from the inputs field `i`;
  * the value is stored raw, a replay fills a missing one and changes nothing
    else, a known one is never replaced;
  * old month files get the column and keep their points;
  * the refill fills points that exist and inserts none, moves no
    watermark, and asks nothing the second time.

Run:
  python -m unittest discover -s gps_collector/tests -t .
"""

import io
import os
import sqlite3
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from gps_collector import config, refill, storage                  # noqa: E402
from gps_collector.main import collect                             # noqa: E402
from gps_collector.tests.support import (FakeServer, Installed,    # noqa: E402
                                         LAT, LON, epoch)
from gps_collector.wialon import (Client, ignition_param,         # noqa: E402
                                   ignition_value)

NOW = epoch("2026-09-10 07:00")
QUIET = lambda *args, **kwargs: None                               # noqa: E731

SENS_IO = {"1": {"n": "Топливо", "t": "fuel level", "p": "io_201"},
           "2": {"n": "Зажигание", "t": "engine operation", "p": "io_239"}}
SENS_IN = {"1": {"n": "Питание", "t": "voltage", "p": "pwr_ext"},
           "4": {"n": "Зажигание", "t": "engine operation", "p": "in"}}
SENS_NONE = {"1": {"n": "Питание", "t": "voltage", "p": "pwr_ext"}}


def io_239(index, stamp):
    """Ignition on for the first ten messages of every twenty."""
    return {"p": {"io_239": 1 if index % 20 < 10 else 0, "pwr_ext": 27.9}}


def inputs(index, stamp):
    """Inputs 1 and 3 on in even minutes, everything off in odd ones."""
    return {"i": 0b101 if (stamp // 60) % 2 == 0 else 0, "p": {"pwr_ext": 13.1}}


def rows_of(folder, month, unit_id):
    con = sqlite3.connect(storage.points_path(folder, month))
    try:
        return con.execute(
            "SELECT t, lon, lat, speed, course, sats, ign FROM points "
            "WHERE unit_id = ? ORDER BY t", (unit_id,)).fetchall()
    finally:
        con.close()


def columns_of(folder, month):
    con = sqlite3.connect(storage.points_path(folder, month))
    try:
        return [row[1] for row in con.execute("PRAGMA table_info(points)")]
    finally:
        con.close()


OLD_POINTS = """
    CREATE TABLE points (
        unit_id INTEGER NOT NULL, t INTEGER NOT NULL, lon REAL NOT NULL,
        lat REAL NOT NULL, speed REAL NOT NULL, course INTEGER, sats INTEGER,
        PRIMARY KEY (unit_id, t)) WITHOUT ROWID
"""


def old_month_file(folder, month, rows):
    """A month file exactly as the collector wrote it before GPS-24."""
    con = sqlite3.connect(storage.points_path(folder, month))
    try:
        con.execute("PRAGMA journal_mode=WAL")
        con.execute(OLD_POINTS)
        con.executemany("INSERT INTO points VALUES (?, ?, ?, ?, ?, ?, ?)", rows)
        con.commit()
    finally:
        con.close()


def point(unit_id, stamp, ign=None, shift=0.0):
    row = (unit_id, stamp, LON + shift, LAT + shift, 8.0, 90, 14)
    return row if ign is None else row + (ign,)


class IgnitionParam(unittest.TestCase):
    def test_the_engine_operation_sensor_gives_its_parameter(self):
        self.assertEqual(ignition_param({"sens": SENS_IO}), "io_239")
        self.assertEqual(ignition_param({"sens": SENS_IN}), "in")

    def test_an_object_without_one_has_none(self):
        self.assertIsNone(ignition_param({"sens": SENS_NONE}))
        self.assertIsNone(ignition_param({}))
        self.assertIsNone(ignition_param({"sens": {"3": {
            "t": "engine operation", "p": "  "}}}))

    def test_of_two_the_smallest_id_wins_numerically(self):
        sens = {"10": {"t": "engine operation", "p": "in2"},
                "9": {"t": "engine operation", "p": "io_239"}}
        self.assertEqual(ignition_param({"sens": sens}), "io_239")

    def test_a_list_of_sensors_is_read_too(self):
        self.assertEqual(ignition_param({"sens": [
            {"t": "voltage", "p": "pwr_ext"},
            {"t": "engine operation", "p": "spn183"}]}), "spn183")


class IgnitionValue(unittest.TestCase):
    def test_a_parameter_of_p_is_taken_raw(self):
        self.assertEqual(ignition_value({"p": {"io_239": 1}}, "io_239"), 1.0)
        self.assertEqual(ignition_value({"p": {"io_239": 0}}, "io_239"), 0.0)
        # [REASON]: сырое значение, не «вкл/выкл»: правило считает расчёт.
        self.assertEqual(ignition_value({"p": {"spn183": 220}}, "spn183"), 220.0)
        self.assertEqual(ignition_value({"p": {"pwr_ext": 27.9}}, "pwr_ext"), 27.9)

    def test_in_is_the_sum_of_the_inputs_of_the_field_i(self):
        message = {"i": 0b10101, "p": {"io_239": 0}}
        self.assertEqual(ignition_value(message, "in"), 3.0)
        self.assertEqual(ignition_value({"i": 0}, "in"), 0.0)

    def test_in_n_is_bit_n_counted_from_one_on_the_right(self):
        message = {"i": 0b10100}
        self.assertEqual(ignition_value(message, "in1"), 0.0)
        self.assertEqual(ignition_value(message, "in3"), 1.0)
        self.assertEqual(ignition_value(message, "in5"), 1.0)
        self.assertEqual(ignition_value({"i": 1 << 31}, "in32"), 1.0)

    def test_inputs_are_never_looked_for_in_p(self):
        # GPS-23 looked in `p` and called all 76 «in» objects silent.
        self.assertIsNone(ignition_value({"p": {"in": 1, "in1": 1}}, "in"))
        self.assertIsNone(ignition_value({"p": {"in1": 1}}, "in1"))

    def test_impossible_inputs_give_none(self):
        self.assertIsNone(ignition_value({"i": 7}, "in0"))
        self.assertIsNone(ignition_value({"i": 7}, "in33"))
        self.assertIsNone(ignition_value({"i": True}, "in"))
        self.assertIsNone(ignition_value({"i": "5"}, "in"))

    def test_a_message_without_the_parameter_gives_none(self):
        self.assertIsNone(ignition_value({"p": {"pwr_ext": 27.9}}, "io_239"))
        self.assertIsNone(ignition_value({}, "io_239"))
        self.assertIsNone(ignition_value({"p": {"io_239": 1}}, None))

    def test_nothing_is_guessed(self):
        self.assertIsNone(ignition_value({"p": {"io_239": "1"}}, "io_239"))
        self.assertIsNone(ignition_value({"p": {"io_239": 1}}, "io_239*const1"))


class IgnitionColumn(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.mkdtemp()
        self.stamp = epoch("2026-09-10 06:00")

    def test_a_new_month_file_has_the_column(self):
        storage.write_points(self.folder, [point(101, self.stamp, 1)])
        self.assertIn("ign", columns_of(self.folder, "202609"))
        self.assertEqual(rows_of(self.folder, "202609", 101)[0][6], 1.0)

    def test_an_old_month_file_gets_it_and_keeps_its_points(self):
        old_month_file(self.folder, "202609",
                       [point(101, self.stamp + 30 * i) for i in range(5)])
        self.assertNotIn("ign", columns_of(self.folder, "202609"))
        result = storage.write_points(self.folder,
                                      [point(101, self.stamp + 600, 1)])
        self.assertEqual(result, (1, 0, 0))
        self.assertIn("ign", columns_of(self.folder, "202609"))
        stored = rows_of(self.folder, "202609", 101)
        self.assertEqual([r[6] for r in stored], [None] * 5 + [1.0])
        self.assertEqual(len(storage.read_day(self.folder, 101, "2026-09-10")), 6)

    def test_a_column_added_meanwhile_by_another_run_is_not_an_error(self):
        storage.write_points(self.folder, [point(101, self.stamp, 1)])

        class Stale:
            """Saw the file before the other run added the column."""

            def __init__(self, con):
                self.con = con

            def execute(self, sql, *args):
                if sql.startswith("PRAGMA table_info"):
                    return [(0, "unit_id"), (1, "t")]
                return self.con.execute(sql, *args)

            def commit(self):
                self.con.commit()

        con = sqlite3.connect(storage.points_path(self.folder, "202609"))
        try:
            storage._ensure_ignition_column(Stale(con))
        finally:
            con.close()
        bare = sqlite3.connect(os.path.join(self.folder, "bare.db"))
        try:
            with self.assertRaises(sqlite3.OperationalError):
                storage._ensure_ignition_column(Stale(bare))
        finally:
            bare.close()

    def test_seven_field_rows_still_work_and_carry_no_ignition(self):
        self.assertEqual(storage.write_points(self.folder,
                                              [point(101, self.stamp)]),
                         (1, 0, 0))
        self.assertIsNone(rows_of(self.folder, "202609", 101)[0][6])

    def test_a_replay_fills_a_missing_ignition_and_changes_nothing_else(self):
        storage.write_points(self.folder,
                             [point(101, self.stamp + 30 * i) for i in range(4)])
        replay = [point(101, self.stamp + 30 * i, i % 2, shift=0.5)
                  for i in range(4)]
        self.assertEqual(storage.write_points(self.folder, replay), (0, 4, 4))
        stored = rows_of(self.folder, "202609", 101)
        self.assertEqual([r[6] for r in stored], [0.0, 1.0, 0.0, 1.0])
        for r in stored:
            self.assertAlmostEqual(r[1], LON)
            self.assertAlmostEqual(r[2], LAT)

    def test_a_known_ignition_is_never_replaced_nor_erased(self):
        storage.write_points(self.folder, [point(101, self.stamp, 1)])
        self.assertEqual(storage.write_points(self.folder,
                                              [point(101, self.stamp, 0)]),
                         (0, 1, 0))
        self.assertEqual(storage.write_points(self.folder,
                                              [point(101, self.stamp)]),
                         (0, 1, 0))
        self.assertEqual(rows_of(self.folder, "202609", 101)[0][6], 1.0)


class FillIgnition(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.mkdtemp()
        self.stamp = epoch("2026-09-10 06:00")

    def test_it_fills_points_that_exist_and_inserts_none(self):
        storage.write_points(self.folder,
                             [point(101, self.stamp + 60 * i) for i in range(10)])
        offered = [(101, self.stamp + 30 * i, 1.0) for i in range(20)]
        self.assertEqual(storage.fill_ignition(self.folder, offered), (10, 20))
        stored = rows_of(self.folder, "202609", 101)
        self.assertEqual(len(stored), 10)
        self.assertEqual({r[6] for r in stored}, {1.0})

    def test_it_never_replaces_a_known_value(self):
        storage.write_points(self.folder, [point(101, self.stamp, 0)])
        self.assertEqual(storage.fill_ignition(
            self.folder, [(101, self.stamp, 1.0)]), (0, 1))
        self.assertEqual(rows_of(self.folder, "202609", 101)[0][6], 0.0)

    def test_rows_without_a_value_are_not_offered(self):
        storage.write_points(self.folder, [point(101, self.stamp)])
        self.assertEqual(storage.fill_ignition(
            self.folder, [(101, self.stamp, None)]), (0, 0))

    def test_a_month_never_collected_is_not_created(self):
        self.assertEqual(storage.fill_ignition(
            self.folder, [(101, epoch("2026-07-05 06:00"), 1.0)]), (0, 1))
        self.assertFalse(os.path.exists(storage.points_path(self.folder,
                                                            "202607")))

    def test_gaps_count_only_points_without_ignition_in_the_period(self):
        storage.write_points(self.folder,
                             [point(101, self.stamp + 60 * i, 1) for i in range(3)]
                             + [point(101, self.stamp + 600 + 60 * i)
                                for i in range(4)]
                             + [point(102, self.stamp)])
        self.assertEqual(storage.ignition_gaps(self.folder, 101, self.stamp,
                                               self.stamp + 86400),
                         (4, self.stamp + 600, self.stamp + 780))
        self.assertEqual(storage.ignition_gaps(self.folder, 101, self.stamp,
                                               self.stamp + 600),
                         (0, None, None))
        self.assertEqual(storage.ignition_coverage(self.folder, "202609"), (8, 3))
        self.assertEqual(storage.ignition_coverage(self.folder, "202608"), (0, 0))

    def test_gaps_in_an_old_file_are_all_of_its_points(self):
        old_month_file(self.folder, "202609",
                       [point(101, self.stamp + 30 * i) for i in range(5)])
        self.assertEqual(storage.ignition_gaps(self.folder, 101, self.stamp,
                                               self.stamp + 3600),
                         (5, self.stamp, self.stamp + 120))
        self.assertEqual(storage.ignition_coverage(self.folder, "202609"), (5, 0))
        self.assertNotIn("ign", columns_of(self.folder, "202609"),
                         "counting must not alter the file")

    def test_gaps_span_the_months_of_the_period(self):
        storage.write_points(self.folder, [
            point(101, epoch("2026-08-31 23:00")),
            point(101, epoch("2026-09-01 01:00"))])
        self.assertEqual(storage.ignition_gaps(
            self.folder, 101, epoch("2026-08-31 00:00"),
            epoch("2026-09-02 00:00")),
            (2, epoch("2026-08-31 23:00"), epoch("2026-09-01 01:00")))
        self.assertEqual(storage._months_between(epoch("2026-11-30 00:00"),
                                                 epoch("2027-01-01 00:00")),
                         ["202611", "202612"])


def run_collect(server, folder, now=NOW, **kwargs):
    with Installed(server):
        client = Client(pause=0.0, log=QUIET)
        client.login("test-token-not-a-real-one")
        summary = collect(client, folder, now=now, log=QUIET, **kwargs)
        client.logout()
    return summary


class CollectorStoresIgnition(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.mkdtemp()
        self.fleet = [{"id": 101, "last_t": NOW - 3600, "sens": SENS_IO},
                      {"id": 102, "last_t": NOW - 3600, "sens": SENS_IN},
                      {"id": 103, "last_t": NOW - 3600, "sens": SENS_NONE}]
        self.server = FakeServer(fleet=self.fleet,
                                 extras={101: io_239, 102: inputs, 103: inputs})

    def test_each_point_carries_its_raw_ignition_value(self):
        summary = run_collect(self.server, self.folder)
        io = rows_of(self.folder, "202609", 101)
        self.assertTrue(io)
        # [REASON]: расхождения считаются, а не сравниваются списки целиком:
        # на 2 880 точках difflib строит дифф минутами, и упавший тест
        # выглядит как зависший.
        self.assertEqual([(i, r[6]) for i, r in enumerate(io)
                          if r[6] != (1.0 if i % 20 < 10 else 0.0)][:3], [])
        inputs_ = rows_of(self.folder, "202609", 102)
        self.assertEqual([(r[0], r[6]) for r in inputs_
                          if r[6] != (2.0 if (r[0] // 60) % 2 == 0 else 0.0)][:3],
                         [])
        self.assertEqual({r[6] for r in inputs_}, {2.0, 0.0})
        # 103 carries inputs too, but has no ignition sensor: nothing is read.
        self.assertEqual({r[6] for r in rows_of(self.folder, "202609", 103)},
                         {None})
        self.assertEqual(summary.units_no_ignition, 1)
        self.assertEqual(summary.points_with_ignition, len(io) + len(inputs_))
        self.assertTrue(summary.balances())
        self.assertTrue(summary.ignition_line().isascii())

    def test_the_sensors_come_with_the_unit_list_request(self):
        run_collect(self.server, self.folder)
        lists = [params for svc, params in self.server.calls
                 if svc == "core/search_items"]
        self.assertEqual(len(lists), 1)
        # GPS-25 добавил счётчики (0x2000): 0x3401 -- флаги прогона GPS-23.
        self.assertEqual(lists[0]["flags"], 0x3401)
        self.assertEqual(lists[0]["flags"] & 0x1000, 0x1000, "sensors")
        self.assertEqual(config.FLAG_UNIT_LIST & 0x0401, 0x0401,
                         "name and last position are still asked for")

    def test_a_replay_brings_ignition_to_points_stored_without_it(self):
        before = FakeServer(fleet=[dict(u, sens=None) for u in self.fleet],
                            extras=self.server.extras)
        first = run_collect(before, self.folder)
        self.assertEqual(first.points_with_ignition, 0)
        stored = rows_of(self.folder, "202609", 101)
        self.assertEqual({r[6] for r in stored}, {None})
        con = storage.open_state(self.folder)
        try:
            con.execute("DELETE FROM collector_watermarks")
            con.commit()
        finally:
            con.close()
        second = run_collect(self.server, self.folder)
        self.assertEqual(second.points_written, 0)
        self.assertEqual(second.ignition_filled, second.points_with_ignition)
        self.assertGreater(second.ignition_filled, 0)
        again = rows_of(self.folder, "202609", 101)
        self.assertEqual(len(again), len(stored))
        self.assertEqual([(a, b) for a, b in zip(again, stored)
                          if a[:6] != b[:6]][:3], [])
        self.assertNotIn(None, {r[6] for r in again})


class CounterSnapshots(unittest.TestCase):
    """GPS-25: счётчик моточасов Wialon -- снимком каждым прогоном."""

    def setUp(self):
        self.folder = tempfile.mkdtemp()

    def snapshots(self):
        con = storage.open_counters(self.folder)
        try:
            return con.execute(
                "SELECT unit_id, last_t, cneh, cfl FROM collector_counters "
                "ORDER BY unit_id, last_t").fetchall()
        finally:
            con.close()

    def test_every_run_notes_the_counter_with_the_last_message_time(self):
        fleet = [{"id": 101, "last_t": NOW - 3600, "sens": SENS_IO,
                  "cneh": 8560.25, "cfl": 0x713},
                 {"id": 102, "last_t": NOW - 40 * 86400, "cneh": 12.5,
                  "cfl": 0x710},
                 {"id": 103, "last_t": NOW - 3600},
                 {"id": 104, "last_t": None, "cneh": 3.0, "cfl": 0x713}]
        summary = run_collect(FakeServer(fleet=fleet), self.folder)
        # silent 102 is noted too: one list answers for the whole fleet
        self.assertEqual(self.snapshots(),
                         [(101, NOW - 3600, 8560.25, 0x713),
                          (102, NOW - 40 * 86400, 12.5, 0x710)])
        self.assertEqual(summary.counters_written, 2)
        self.assertIn("engine-hour counters noted: 2", summary.ignition_line())

    def test_a_moment_already_noted_is_not_noted_again(self):
        fleet = [{"id": 101, "last_t": NOW - 3600, "cneh": 10.0, "cfl": 0x713}]
        run_collect(FakeServer(fleet=fleet), self.folder)
        again = run_collect(FakeServer(fleet=[dict(fleet[0], cneh=99.0)]),
                            self.folder, now=NOW + 60)
        self.assertEqual(again.counters_written, 0)
        self.assertEqual(self.snapshots(), [(101, NOW - 3600, 10.0, 0x713)])
        later = [dict(fleet[0], last_t=NOW + 3000, cneh=11.5)]
        run_collect(FakeServer(fleet=later), self.folder, now=NOW + 7200)
        self.assertEqual([r[2] for r in self.snapshots()], [10.0, 11.5])

    def test_the_unit_list_reads_counters_as_numbers_only(self):
        server = FakeServer(fleet=[{"id": 101, "last_t": NOW, "cneh": 1.5,
                                    "cfl": 1811},
                                   {"id": 102, "last_t": NOW}])
        with Installed(server):
            client = Client(pause=0.0, log=QUIET)
            client.login("test-token-not-a-real-one")
            units = client.list_units()
        self.assertEqual([(u["cneh"], u["cfl"]) for u in units],
                         [(1.5, 1811), (None, None)])

    def test_read_ignition_spans_months_and_never_writes(self):
        old_month_file(self.folder, "202609",
                       [point(101, epoch("2026-09-30 23:00"))])
        storage.write_points(self.folder,
                             [point(101, epoch("2026-10-01 01:00"), 1),
                              point(101, epoch("2026-10-01 02:00"), 0),
                              point(102, epoch("2026-10-01 01:00"), 1)])
        self.assertEqual(
            storage.read_ignition(self.folder, 101, epoch("2026-09-30 00:00"),
                                  epoch("2026-10-01 01:00")),
            [(epoch("2026-09-30 23:00"), None), (epoch("2026-10-01 01:00"), 1.0)])
        self.assertNotIn("ign", columns_of(self.folder, "202609"),
                         "reading must not alter an old file")
        self.assertEqual(storage.read_ignition(
            self.folder, 101, epoch("2026-08-01 00:00"),
            epoch("2026-08-31 00:00")), [])
        self.assertFalse(os.path.exists(storage.points_path(self.folder,
                                                            "202608")))


class Refill(unittest.TestCase):
    """The refill: ignition into points already there, nothing else."""

    def setUp(self):
        self.folder = tempfile.mkdtemp()
        self.start = epoch("2026-09-01 00:00")
        self.end = epoch("2026-09-03 00:00")
        self.fleet = [{"id": 101, "last_t": NOW - 3600, "sens": SENS_IO},
                      {"id": 102, "last_t": NOW - 3600, "sens": SENS_IN},
                      {"id": 103, "last_t": NOW - 3600, "sens": SENS_NONE}]
        self.extras = {101: io_239, 102: inputs, 103: inputs}
        # [REASON]: только каждая вторая секунда сервера лежит точкой, как
        # если бы ночной сбор часть сообщений не получил. Дозаполнение
        # вставкой показало бы это ростом числа точек.
        for unit_id, first in ((101, "2026-09-01 08:00"),
                               (102, "2026-09-02 09:00"),
                               (103, "2026-09-01 08:00")):
            base = epoch(first)
            storage.write_points(self.folder, [point(unit_id, base + 60 * i)
                                               for i in range(120)])

    def run_refill(self, server, **kwargs):
        with Installed(server):
            client = Client(pause=0.0, log=QUIET)
            client.login("test-token-not-a-real-one")
            summary = refill.refill(client, self.folder, self.start, self.end,
                                    log=QUIET, **kwargs)
            client.logout()
        return summary

    def test_it_fills_the_points_and_inserts_none(self):
        server = FakeServer(fleet=self.fleet, extras=self.extras)
        summary = self.run_refill(server)
        io = rows_of(self.folder, "202609", 101)
        self.assertEqual(len(io), 120)
        self.assertNotIn(None, {r[6] for r in io})
        self.assertEqual({r[6] for r in rows_of(self.folder, "202609", 102)},
                         {2.0, 0.0})
        self.assertEqual(len(rows_of(self.folder, "202609", 102)), 120)
        self.assertEqual({r[6] for r in rows_of(self.folder, "202609", 103)},
                         {None})
        self.assertEqual([load[0] for load in server.loads], [101, 102])
        self.assertEqual(summary.filled, 240)
        self.assertEqual(summary.points_missing, 240)
        self.assertEqual(summary.units_no_ignition, 1)
        self.assertGreater(summary.with_value, summary.filled,
                           "the server had seconds that are not points")

    def test_the_first_and_the_last_point_are_inside_the_request(self):
        server = FakeServer(fleet=self.fleet[:1], extras=self.extras)
        self.run_refill(server)
        io = rows_of(self.folder, "202609", 101)
        self.assertIsNotNone(io[0][6])
        self.assertIsNotNone(io[-1][6])

    def test_it_moves_no_watermark(self):
        storage.set_watermark(self.folder, 101, NOW - 7200)
        self.run_refill(FakeServer(fleet=self.fleet, extras=self.extras))
        self.assertEqual(storage.read_watermark(self.folder, 101), NOW - 7200)
        self.assertIsNone(storage.read_watermark(self.folder, 102))

    def test_a_second_run_asks_nothing(self):
        self.run_refill(FakeServer(fleet=self.fleet, extras=self.extras))
        server = FakeServer(fleet=self.fleet, extras=self.extras)
        summary = self.run_refill(server)
        self.assertEqual(server.loads, [])
        self.assertEqual(summary.units_complete, 2)
        self.assertEqual(summary.filled, 0)

    def test_truncated_pieces_go_on_to_the_end(self):
        server = FakeServer(fleet=self.fleet[:1], extras=self.extras, cap=50)
        with mock.patch.object(config, "MAX_MESSAGES_PER_INTERVAL", 50):
            summary = self.run_refill(server)
        self.assertGreater(summary.truncated, 2)
        self.assertNotIn(None, {r[6] for r in rows_of(self.folder, "202609", 101)})

    def test_a_failed_object_does_not_stop_the_others(self):
        class Broken(FakeServer):
            def messages(self, unit_id, t_from, t_to):
                if unit_id == 101:
                    raise RuntimeError("socket gone")
                return super().messages(unit_id, t_from, t_to)

        with mock.patch.object(config, "RETRY_PAUSE_S", (0, 0, 0)):
            summary = self.run_refill(Broken(fleet=self.fleet,
                                             extras=self.extras))
        self.assertEqual(summary.units_failed, 1)
        self.assertEqual({r[6] for r in rows_of(self.folder, "202609", 101)},
                         {None})
        self.assertNotIn(None, {r[6] for r in rows_of(self.folder, "202609", 102)})

    def test_only_limits_the_run(self):
        server = FakeServer(fleet=self.fleet, extras=self.extras)
        self.run_refill(server, only=["102"])
        self.assertEqual([load[0] for load in server.loads], [102])


class RefillCommand(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.mkdtemp()
        storage.write_points(self.folder, [point(101, epoch("2026-09-02 08:00")
                                                 + 60 * i) for i in range(30)])
        self.server = FakeServer(
            fleet=[{"id": 101, "last_t": NOW - 3600, "sens": SENS_IO}],
            extras={101: io_239})

    def run_main(self, *argv, server=None):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.dict(os.environ, {"WIALON_TOKEN": "test-token"}), \
                Installed(server or self.server), \
                redirect_stdout(out), redirect_stderr(err):
            code = refill.main(
                list(argv), now=NOW,
                client_factory=lambda pause: Client(pause=0.0, log=QUIET))
        return code, out.getvalue(), err.getvalue()

    def test_a_clean_run_reports_before_and_after_in_ascii(self):
        code, out, err = self.run_main("--from", "2026-09-01", "--to",
                                       "2026-09-05", "--dir", self.folder)
        self.assertEqual(code, refill.EXIT_OK, err)
        self.assertIn("202609: points 30, with ignition 0", out)
        self.assertIn("202609: points 30, with ignition 30", out)
        self.assertIn("REFILL ONLY", out)
        self.assertTrue(out.isascii())

    def test_to_is_inclusive_and_the_lag_is_kept(self):
        code, out, _ = self.run_main("--from", "2026-09-02", "--to",
                                     "2026-09-02", "--dir", self.folder)
        self.assertEqual(code, refill.EXIT_OK)
        self.assertIn("2026-09-02 00:00 -> 2026-09-03 00:00", out)
        code, out, _ = self.run_main("--from", "2026-09-10", "--to",
                                     "2026-09-30", "--dir", self.folder)
        self.assertIn("-> 2026-09-10 06:30", out)

    def test_bad_input_is_code_2(self):
        for argv in (("--from", "01.09.2026", "--to", "2026-09-05"),
                     ("--from", "2026-09-05", "--to", "2026-09-01"),
                     ("--from", "2026-09-11", "--to", "2026-09-20")):
            code, _, err = self.run_main(*argv, "--dir", self.folder)
            self.assertEqual(code, refill.EXIT_BAD_INPUT, argv)
            self.assertTrue(err.startswith("ERROR"), argv)
        code, _, _ = self.run_main("--from", "2026-09-01", "--to", "2026-09-05",
                                   "--dir", os.path.join(self.folder, "none"))
        self.assertEqual(code, refill.EXIT_BAD_INPUT)

    def test_a_failed_object_is_code_1(self):
        class Broken(FakeServer):
            def messages(self, unit_id, t_from, t_to):
                raise RuntimeError("socket gone")

        broken = Broken(fleet=self.server.fleet, extras=self.server.extras)
        with mock.patch.object(config, "RETRY_PAUSE_S", (0, 0, 0)):
            code, out, _ = self.run_main("--from", "2026-09-01", "--to",
                                         "2026-09-05", "--dir", self.folder,
                                         server=broken)
        self.assertEqual(code, refill.EXIT_PARTIAL)
        self.assertIn("failed: 1", out)


if __name__ == "__main__":
    unittest.main()
