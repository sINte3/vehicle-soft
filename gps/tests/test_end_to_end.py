# -*- coding: utf-8 -*-
"""Сквозная проверка: от ответа Wialon до строки в transport.db.

ЗАЧЕМ ОТДЕЛЬНЫЙ НАБОР
Каждый кусок тракта проверен сам по себе: коллектор — против консервированного
сервера, суточный расчёт — на фикстуре, экраны — на синтетических строках.
Ни один из этих наборов не проверяет ШВЫ между ними, а ломаются обычно они:
сообщение теряет поле по дороге в хранилище, помесячный файл читается не за те
сутки, расчёт получает точки в другом порядке. На сервере такой дефект
обнаружился бы утром после первого прогона, и разбирать его пришлось бы по
пустому экрану.

ЧТО ЗДЕСЬ ПРОИСХОДИТ
Консервированный сервер отдаёт НАСТОЯЩИЙ трек юнита 3464 за 27.07.2026 — тот
самый, на котором проверен метод, — сообщение за сообщением, в том виде, в
каком его отдаёт Wialon. Дальше работает настоящий код: коллектор,
`messages/unload`, watermark, помесячный файл, суточный расчёт, миграция,
запись в `transport.db`.

Записанные **8,772 га** обязаны выйти на том конце. Если между слоями что-то
теряется, здесь это видно числом, а не рассуждением.

Запуск (нужен геостек, см. gps/README.md):
  python -m unittest discover -s gps/tests -t .
"""

import csv
import json
import os
import sqlite3
import sys
import tempfile
import unittest
import urllib.parse
import urllib.request

from datetime import datetime, timedelta, timezone

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

import migration_utils                                             # noqa: E402
import migrate_gps_daily_001 as gps_mig                            # noqa: E402
import migrate_gps_sync_log_001 as sync_mig                        # noqa: E402
from gps import daily                                              # noqa: E402
from gps_collector import config, storage, sync_log                # noqa: E402
from gps_collector.main import collect                             # noqa: E402
from gps_collector.wialon import Client                            # noqa: E402

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")
TZ = timezone(timedelta(hours=5))
UNIT = 3464
DAY = "2026-07-27"
# Прогон стоит утром 28-го: сутки 27-го уже закрыты и целиком попадают в
# интервал при --backfill-days 2.
NOW = int(datetime.strptime("2026-07-28 12:00", "%Y-%m-%d %H:%M")
          .replace(tzinfo=TZ).timestamp())

QUIET = lambda *args, **kwargs: None                               # noqa: E731

FIELD_CONTOURS_DDL = (
    'CREATE TABLE field_contours (id INTEGER PRIMARY KEY, source TEXT, '
    'external_id TEXT, name TEXT, geometry_geojson TEXT, area_ha REAL, '
    'is_active BOOLEAN DEFAULT 1)')


def fixture_messages():
    """Настоящий трек 3464 за 27.07 в том виде, в каком его отдаёт Wialon."""
    out = []
    with open(os.path.join(FIXTURES, "track_3464_20260727.csv"),
              encoding="utf-8-sig") as fh:
        for row in csv.DictReader(fh, delimiter=";"):
            hours, minutes, seconds = row["time"].split(":")
            midnight = datetime.strptime(row["date"], "%Y-%m-%d") \
                .replace(tzinfo=TZ).timestamp()
            out.append({
                "t": int(midnight) + int(hours) * 3600 + int(minutes) * 60
                     + int(seconds),
                # Wialon зовёт долготу x, широту y. Перепутать их здесь --
                # значит перенести Бухару к берегам Сомали, и ни одна проверка
                # формата этого не заметит.
                "pos": {"x": float(row["lon"]), "y": float(row["lat"]),
                        "s": float(row["speed"]), "c": int(row["course"]),
                        "sc": int(row["sats"] or 0)}})
    return sorted(out, key=lambda m: m["t"])


class FakeResponse:
    def __init__(self, payload):
        self._raw = json.dumps(payload).encode("utf-8")

    def read(self):
        return self._raw

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class RealTrackServer:
    """Консервированный Wialon, отдающий фикстуру по-настоящему нарезанной."""

    def __init__(self, messages):
        self.messages = messages
        self.loads = []
        self.unloads = 0

    def urlopen(self, request, timeout=None):
        query = urllib.parse.parse_qs(request.data.decode("utf-8"))
        svc = query["svc"][0]
        params = json.loads(query["params"][0])
        if svc == "token/login":
            return FakeResponse({"eid": "SESSION"})
        if svc == "core/search_items":
            return FakeResponse({"items": [
                {"id": UNIT, "nm": "МТЗ 261 EA",
                 "pos": {"t": self.messages[-1]["t"]}}]})
        if svc == "messages/load_interval":
            self.loads.append((params["timeFrom"], params["timeTo"]))
            found = [m for m in self.messages
                     if params["timeFrom"] <= m["t"] < params["timeTo"]]
            if not found:
                return FakeResponse({"error": 1001})
            return FakeResponse({"messages": found})
        if svc == "messages/unload":
            self.unloads += 1
        return FakeResponse({})


class Installed:
    def __init__(self, server):
        self.server = server

    def __enter__(self):
        self.original = urllib.request.urlopen
        urllib.request.urlopen = self.server.urlopen
        return self.server

    def __exit__(self, *exc):
        urllib.request.urlopen = self.original
        return False


class WholeChain(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.messages = fixture_messages()
        cls.folder = tempfile.mkdtemp()
        cls.db = os.path.join(cls.folder, "transport.db")

        connection = sqlite3.connect(cls.db)
        connection.execute(FIELD_CONTOURS_DDL)
        connection.commit()
        connection.close()
        cls._saved = (gps_mig.DB_PATH, sync_mig.DB_PATH,
                      migration_utils.DB_PATH)
        gps_mig.DB_PATH = cls.db
        sync_mig.DB_PATH = cls.db
        migration_utils.DB_PATH = cls.db
        gps_mig.run()
        sync_mig.run()

        # --- шаг 1: коллектор против консервированного сервера
        cls.server = RealTrackServer(cls.messages)
        started = datetime.now(config.TZ)
        with Installed(cls.server):
            client = Client(pause=0.0, log=QUIET)
            client.login("test-token-not-a-real-one")
            cls.summary = collect(client, cls.folder, now=NOW, backfill_days=2,
                                  log=QUIET)
            client.logout()
        cls.client = client
        sync_log.record(cls.summary, started, datetime.now(config.TZ),
                        client=client, db_path=cls.db, log=QUIET)

        # --- шаг 2: суточный расчёт по тем самым точкам
        cls.result = daily.run_day(DAY, UNIT, folder=cls.folder,
                                   db_path=cls.db, log=QUIET)

    @classmethod
    def tearDownClass(cls):
        (gps_mig.DB_PATH, sync_mig.DB_PATH,
         migration_utils.DB_PATH) = cls._saved

    def rows(self, table):
        connection = sqlite3.connect(self.db)
        try:
            connection.row_factory = sqlite3.Row
            return [dict(r) for r in connection.execute(
                "SELECT * FROM %s" % table)]
        finally:
            connection.close()

    # --- шов 1: сервер -> хранилище ---------------------------------------

    def test_the_collector_asked_once_and_unloaded_after_itself(self):
        self.assertEqual(len(self.server.loads), 1)
        self.assertEqual(self.server.unloads, 1)
        self.assertEqual(self.client.logins, 1)

    def test_every_message_landed_in_exactly_one_bucket(self):
        self.assertTrue(self.summary.balances())
        self.assertEqual(self.summary.points_seen, len(self.messages))
        # В фикстуре есть пара сообщений в одну секунду -- ключ (unit_id, t)
        # схлопывает её, и вторая обязана оказаться в корзине дублей.
        self.assertEqual(self.summary.points_written, 1598)
        self.assertEqual(self.summary.points_duplicate, 1)
        self.assertEqual(self.summary.points_no_position, 0)

    def test_the_points_are_in_the_month_of_their_own_timestamp(self):
        self.assertTrue(os.path.exists(
            storage.points_path(self.folder, "202607")))
        self.assertFalse(os.path.exists(
            storage.points_path(self.folder, "202608")))

    def test_the_stored_track_is_the_track_that_was_sent(self):
        stored = storage.read_day(self.folder, UNIT, DAY)
        self.assertEqual(len(stored), 1598)
        # координаты не переставлены местами по дороге
        for _, lon, lat, _, _ in stored:
            self.assertGreater(lon, 64.0)
            self.assertLess(lon, 65.0)
            self.assertGreater(lat, 39.0)
            self.assertLess(lat, 41.0)

    # --- шов 2: хранилище -> расчёт ---------------------------------------

    def test_the_recorded_hectares_survive_the_whole_chain(self):
        # 8,772 га -- то же число, что даёт расчёт напрямую на фикстуре
        # (gps/tests/test_daily.py). Если между слоями что-то теряется, оно
        # видно здесь и только здесь.
        polygons = self.rows("gps_work_polygons")
        self.assertEqual(len(polygons), 1)
        self.assertAlmostEqual(polygons[0]["area_ha"], 8.772, delta=0.01)
        self.assertAlmostEqual(polygons[0]["minutes"], 317.4, delta=0.5)
        self.assertEqual(polygons[0]["suggested_label"], "работа")

    def test_the_aggregate_describes_the_same_day(self):
        aggregates = self.rows("gps_daily_aggregates")
        self.assertEqual(len(aggregates), 1)
        row = aggregates[0]
        self.assertIsNone(row["reason"])
        self.assertEqual(row["points_total"], 1598)
        self.assertEqual(row["interval_median_s"], 30.0)
        self.assertEqual(row["sats_median"], 14.0)
        self.assertEqual(row["method_version"], "adaptive-alpha-2026-08-12")

    def test_the_ingestion_journal_agrees_with_what_was_stored(self):
        runs = self.rows("gps_sync_log")
        self.assertEqual(len(runs), 1)
        run = runs[0]
        self.assertEqual(run["status"], "ok")
        self.assertEqual(run["messages_seen"],
                         run["points_written"] + run["points_duplicate"]
                         + run["points_no_position"])
        self.assertEqual(run["points_written"],
                         self.rows_count("points", "202607"))

    def rows_count(self, table, month):
        connection = sqlite3.connect(storage.points_path(self.folder, month))
        try:
            return connection.execute("SELECT COUNT(*) FROM %s" % table
                                      ).fetchone()[0]
        finally:
            connection.close()

    # --- шов 3: повторный прогон ------------------------------------------

    def test_a_second_collection_of_the_same_day_changes_nothing(self):
        server = RealTrackServer(self.messages)
        with Installed(server):
            client = Client(pause=0.0, log=QUIET)
            client.login("test-token-not-a-real-one")
            # watermark сброшен руками -- сервер спросят снова
            storage.set_watermark(self.folder, UNIT, self.messages[0]["t"] - 60)
            again = collect(client, self.folder, now=NOW, backfill_days=2,
                            log=QUIET)
            client.logout()
        self.assertGreater(again.points_seen, 0, "сервер не спросили заново")
        self.assertEqual(again.points_written, 0)
        self.assertEqual(again.points_duplicate, again.points_seen)

        recomputed = daily.run_day(DAY, UNIT, folder=self.folder,
                                   db_path=self.db, log=QUIET)
        self.assertEqual(len(self.rows("gps_work_polygons")), 1)
        self.assertEqual(len(self.rows("gps_daily_aggregates")), 1)
        self.assertAlmostEqual(recomputed.sites[0]["area_ha"], 8.772,
                               delta=0.01)


if __name__ == "__main__":
    unittest.main()
