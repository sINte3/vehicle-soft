# -*- coding: utf-8 -*-
"""GPS-22: поле посчитанного участка заново -- по нынешнему зеркалу контуров.

Что проверяется и почему:
  1. Выбор поля -- тот же, что у ночного расчёта: сутки фикстуры 3464 за
     27.07, посчитанные `run_day` при зеркале с зоной 3208, инструмент не
     трогает (всё «то же поле»). Отрицательный контроль -- ниже: стоит
     сменить контур под строкой, и он же предлагает другую строку.
  2. Перестановка, как 15.10: под номером строки, на который ссылается
     участок, теперь лежит другая зона далеко, а контур поля -- под другим
     номером. План -- «другое поле», запись ставит новый номер.
  3. Запись меняет ТОЛЬКО `contour_id`: площадь, многоугольник, ответ
     оператора и время ответа -- те же байты. Сухой прогон не меняет базу.
  4. Участок получает поле, теряет его, не читается -- каждое называется.
  5. Участок, пересчитанный между планом и записью, пропускается (код 3);
     справочник, изменившийся между планом и записью, -- откат (код 1).
  6. Неверный вход -- код 2 и ничего не записано; консоль -- ASCII без имён.

Запуск (геостек, см. gps/README.md):
  python -m unittest gps.tests.test_reattach_contours
"""

import csv
import hashlib
import io
import json
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

import migration_utils                                             # noqa: E402
import migrate_gps_daily_001 as gps_mig                            # noqa: E402
import tools.gps_reattach_contours as gr                           # noqa: E402
from gps import daily                                              # noqa: E402
from gps.tests.test_daily import read_fixture_track, read_fixture_zone, zone_geojson  # noqa: E402,E501
from gps_collector import storage                                  # noqa: E402

DAY = "2026-07-27"
UNIT = 3464
QUIET = lambda *a: None                                            # noqa: E731

# схема справочника -- как в базе (migrate_core_foundation_001)
FIELD_CONTOURS_DDL = (
    'CREATE TABLE field_contours (id INTEGER PRIMARY KEY AUTOINCREMENT, '
    'source VARCHAR(20) NOT NULL, external_id VARCHAR(100), '
    'name VARCHAR(300) NOT NULL, name_uz VARCHAR(300), '
    'geometry_geojson TEXT, area_ha FLOAT, customer_id INTEGER, '
    'season_year INTEGER, is_active BOOLEAN DEFAULT 1, '
    'created_at DATETIME, updated_at DATETIME, '
    'CONSTRAINT uq_field_contours_source_external UNIQUE (source, external_id))')

FAR = json.dumps({"type": "Polygon", "coordinates": [[
    [65.40, 40.40], [65.41, 40.40], [65.41, 40.41], [65.40, 40.41],
    [65.40, 40.40]]]})


def digest(path):
    with open(path, "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()


class World(unittest.TestCase):
    """Сутки 3464 посчитаны ночным расчётом при зеркале с полем 3208."""

    def setUp(self):
        self.folder = tempfile.mkdtemp()
        self.db = os.path.join(self.folder, "transport.db")
        con = sqlite3.connect(self.db)
        con.execute(FIELD_CONTOURS_DDL)
        con.commit()
        con.close()
        self._saved = (gps_mig.DB_PATH, migration_utils.DB_PATH)
        gps_mig.DB_PATH = self.db
        migration_utils.DB_PATH = self.db
        gps_mig.run()
        storage.write_points(self.folder, [
            (UNIT, t, lon, lat, speed, 90, sats)
            for t, lon, lat, speed, sats in read_fixture_track()])
        self.field = zone_geojson(read_fixture_zone())
        self.out = os.path.join(self.folder, "reattach.csv")

    def tearDown(self):
        gps_mig.DB_PATH, migration_utils.DB_PATH = self._saved

    def execute(self, sql, params=()):
        con = sqlite3.connect(self.db)
        try:
            con.execute(sql, params)
            con.commit()
        finally:
            con.close()

    def contour(self, row_id, name, geometry, external=None, stamp="2026-09-07"):
        self.execute(
            "INSERT INTO field_contours (id, source, external_id, name, "
            "geometry_geojson, is_active, updated_at) VALUES "
            "(?, 'wialon', ?, ?, ?, 1, ?)",
            (row_id, external or str(row_id), name, geometry, stamp))

    def compute(self):
        result = daily.run_day(DAY, UNIT, folder=self.folder, db_path=self.db,
                               log=QUIET)
        self.assertIsNone(result.reason)

    def sites(self):
        con = sqlite3.connect(self.db)
        try:
            return con.execute(
                "SELECT id, work_date, wialon_id, site_number, area_ha, minutes, "
                "polygon_geojson, contour_id, alpha_used_m, pass_spacing_m, "
                "quality_flag, suggested_label, operator_label, decided_at "
                "FROM gps_work_polygons ORDER BY id").fetchall()
        finally:
            con.close()

    def run_tool(self, *extra, db=None, out=None):
        stdout, stderr = io.StringIO(), io.StringIO()
        argv = ["--db", db or self.db, "--out", out or self.out] + list(extra)
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = gr.main(argv)
        return code, stdout.getvalue(), stderr.getvalue()

    def plan_rows(self):
        with open(self.out, encoding="utf-8-sig", newline="") as handle:
            return list(csv.DictReader(handle, delimiter=";"))

    def remap(self):
        """Как 15.10: под строкой 77 теперь другая зона далеко, поле -- под 78."""
        self.execute("UPDATE field_contours SET name = 'Нурафшон АЗС', "
                     "geometry_geojson = ?, updated_at = '2026-10-15' "
                     "WHERE id = 77", (FAR,))
        self.contour(78, "Поле Шофиркон 3208", self.field, stamp="2026-10-15")


class SameAsTheNightly(World):
    def test_the_nightly_choice_is_kept(self):
        self.contour(77, "Поле 3208", self.field)
        self.contour(79, "Дальнее поле", FAR)
        self.compute()
        stored = self.sites()
        self.assertTrue(stored and all(row[7] == 77 for row in stored))
        code, stdout, _ = self.run_tool()
        self.assertEqual(code, gr.EXIT_OK)
        self.assertEqual({row["status"] for row in self.plan_rows()},
                         {gr.SAME})
        self.assertIn("changes that --apply would make: 0", stdout)

    def test_a_changed_outline_under_the_row_is_seen(self):
        # отрицательный контроль к тесту выше: та же база, контур под 77 сменён
        self.contour(77, "Поле 3208", self.field)
        self.compute()
        self.remap()
        self.run_tool()
        self.assertEqual({(row["status"], row["contour_bylo"], row["contour_stalo"])
                          for row in self.plan_rows()},
                         {(gr.CHANGE, "77", "78")})


class Remap(World):
    def setUp(self):
        super().setUp()
        self.contour(77, "Поле 3208", self.field)
        self.compute()
        con = sqlite3.connect(self.db)
        con.execute("UPDATE gps_work_polygons SET operator_label = 'rabota', "
                    "decided_at = '2026-08-01 10:00:00'")
        con.commit()
        con.close()
        self.remap()

    def test_a_dry_run_writes_nothing(self):
        before = digest(self.db)
        code, stdout, _ = self.run_tool()
        self.assertEqual(code, gr.EXIT_OK)
        self.assertEqual(digest(self.db), before)
        self.assertIn("DRY RUN", stdout)
        row = self.plan_rows()[0]
        # экран сейчас называет поле по строке 77 -- чужой зоной
        self.assertEqual((row["imya_bylo_seychas"], row["imya_stalo"],
                          row["otvet_operatora"]),
                         ("Нурафшон АЗС", "Поле Шофиркон 3208", "da"))

    def test_apply_changes_only_contour_id(self):
        before = self.sites()
        code, stdout, _ = self.run_tool("--apply")
        self.assertEqual(code, gr.EXIT_OK, stdout)
        after = self.sites()
        self.assertEqual(len(after), len(before))
        for old, new in zip(before, after):
            self.assertEqual(old[:7] + old[8:], new[:7] + new[8:])
            self.assertEqual((old[7], new[7]), (77, 78))
        self.assertIn("written: %d site(s)" % len(after), stdout)

    def test_apply_twice_changes_nothing_the_second_time(self):
        self.run_tool("--apply")
        before = digest(self.db)
        code, stdout, _ = self.run_tool("--apply")
        self.assertEqual(code, gr.EXIT_OK)
        self.assertIn("written: 0 site(s)", stdout)
        self.assertEqual({row["status"] for row in self.plan_rows()}, {gr.SAME})

    def test_a_site_recomputed_after_the_plan_is_skipped(self):
        con = sqlite3.connect(self.db, isolation_level=None)
        items = gr.plan(con, log=QUIET)
        fingerprint = gr.directory_fingerprint(con)
        first = items[0]
        con.execute("UPDATE gps_work_polygons SET polygon_geojson = "
                    "polygon_geojson || ' ' WHERE id = ?", (first["row_id"],))
        con.execute("BEGIN IMMEDIATE")
        written, skipped = gr.apply(con, items, fingerprint)
        con.execute("COMMIT")
        con.close()
        self.assertEqual([item["row_id"] for item in skipped], [first["row_id"]])
        self.assertEqual(len(written), len(items) - 1)
        stored = {row[0]: row[7] for row in self.sites()}
        self.assertEqual(stored[first["row_id"]], 77)

    def test_a_directory_changed_after_the_plan_rolls_back(self):
        real_plan = gr.plan

        def plan_then_mirror(con, *args, **kwargs):
            items = real_plan(con, *args, **kwargs)
            writer = sqlite3.connect(self.db)
            writer.execute("UPDATE field_contours SET updated_at = '2026-10-16' "
                           "WHERE id = 78")
            writer.commit()
            writer.close()
            return items

        before = [row[7] for row in self.sites()]
        with mock.patch.object(gr, "plan", side_effect=plan_then_mirror):
            code, stdout, _ = self.run_tool("--apply")
        self.assertEqual(code, gr.EXIT_FAILED)
        self.assertIn("rolled back", stdout)
        self.assertEqual([row[7] for row in self.sites()], before)

    def test_the_console_is_ascii_and_carries_no_names(self):
        _, stdout, _ = self.run_tool()
        stdout.encode("ascii")
        for word in ("Нурафшон", "Шофиркон", "Nurafshon", "Shofirkon"):
            self.assertNotIn(word, stdout)


class GainLoseUnreadable(World):
    def test_a_site_gets_a_field_and_another_loses_it(self):
        # сутки посчитаны при пустом зеркале: поля нет
        self.compute()
        self.assertTrue(all(row[7] is None for row in self.sites()))
        self.contour(80, "Поле 3208", self.field)
        self.run_tool()
        self.assertEqual({row["status"] for row in self.plan_rows()}, {gr.GAIN})
        self.run_tool("--apply")
        self.assertTrue(all(row[7] == 80 for row in self.sites()))
        # поле ушло далеко -- участок поля лишается
        self.execute("UPDATE field_contours SET geometry_geojson = ? WHERE id = 80",
                     (FAR,))
        self.run_tool("--apply")
        self.assertEqual({row["status"] for row in self.plan_rows()}, {gr.LOSE})
        self.assertTrue(all(row[7] is None for row in self.sites()))

    def test_an_unreadable_outline_is_kept_and_named(self):
        self.contour(77, "Поле 3208", self.field)
        self.compute()
        self.execute("UPDATE gps_work_polygons SET polygon_geojson = 'broken'")
        self.remap()
        code, stdout, _ = self.run_tool("--apply")
        self.assertEqual(code, gr.EXIT_OK)
        self.assertEqual({row["status"] for row in self.plan_rows()},
                         {gr.UNREADABLE})
        self.assertTrue(all(row[7] == 77 for row in self.sites()))

    def test_the_window_limits_the_sites(self):
        self.contour(77, "Поле 3208", self.field)
        self.compute()
        self.remap()
        self.run_tool("--since", "2026-07-28")
        self.assertEqual(self.plan_rows(), [])
        self.run_tool("--since", DAY, "--until", DAY)
        self.assertTrue(self.plan_rows())


class BadInput(World):
    def assert_refused(self, code, stderr):
        self.assertEqual(code, gr.EXIT_BAD_INPUT, stderr)
        self.assertTrue(stderr.startswith("ERROR: "), stderr)

    def test_a_missing_database_is_refused_and_not_created(self):
        missing = os.path.join(self.folder, "nope.db")
        code, _, stderr = self.run_tool(db=missing)
        self.assert_refused(code, stderr)
        self.assertFalse(os.path.exists(missing))

    def test_a_hash_in_the_path_is_refused(self):
        odd = os.path.join(self.folder, "copy#1.db")
        with open(self.db, "rb") as src, open(odd, "wb") as dst:
            dst.write(src.read())
        code, _, stderr = self.run_tool(db=odd)
        self.assert_refused(code, stderr)
        self.assertFalse(os.path.exists(os.path.join(self.folder, "copy")))

    def test_bad_dates_are_refused(self):
        for extra in (("--since", "2026-7-27"), ("--until", "27.07.2026"),
                      ("--since", "2026-07-28", "--until", "2026-07-27")):
            code, _, stderr = self.run_tool(*extra)
            self.assert_refused(code, stderr)

    def test_out_on_the_database_is_refused(self):
        before = digest(self.db)
        code, _, stderr = self.run_tool(out=self.db)
        self.assert_refused(code, stderr)
        self.assertEqual(digest(self.db), before)

    def test_a_missing_out_folder_is_refused(self):
        code, _, stderr = self.run_tool(
            out=os.path.join(self.folder, "no_such", "plan.csv"))
        self.assert_refused(code, stderr)

    def test_a_database_without_the_tables_is_refused(self):
        other = os.path.join(self.folder, "other.db")
        con = sqlite3.connect(other)
        con.execute("CREATE TABLE t (x)")
        con.commit()
        con.close()
        code, _, stderr = self.run_tool(db=other)
        self.assert_refused(code, stderr)

    def test_a_directory_without_updated_at_is_refused(self):
        other = os.path.join(self.folder, "old.db")
        con = sqlite3.connect(other)
        con.execute("CREATE TABLE field_contours (id INTEGER PRIMARY KEY, "
                    "name TEXT, geometry_geojson TEXT, is_active BOOLEAN)")
        con.execute("CREATE TABLE gps_work_polygons (id INTEGER PRIMARY KEY, "
                    "work_date DATE, wialon_id INTEGER, site_number INTEGER, "
                    "area_ha REAL, polygon_geojson TEXT, contour_id INTEGER, "
                    "operator_label TEXT)")
        con.commit()
        con.close()
        code, _, stderr = self.run_tool(db=other)
        self.assert_refused(code, stderr)


if __name__ == "__main__":
    unittest.main()
