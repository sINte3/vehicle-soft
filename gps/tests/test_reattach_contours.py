# -*- coding: utf-8 -*-
"""GPS-22: поле посчитанного участка заново -- по нынешнему зеркалу контуров.

Что проверяется и почему:
  1. Выбор поля -- тот же, что у ночного расчёта: сутки фикстуры 3464 за
     27.07, посчитанные `run_day` при зеркале с зоной 3208, инструмент не
     трогает (всё «то же поле»), в том числе когда одинаковых зон две (в
     Wialon дубли -- по конструкции) и равное перекрытие решает порядок.
     Наибольшее перекрытие, а не центр участка; дыры участка сохраняются;
     участок-«бабочка» чинится; выключенная строка не поле. Отрицательный
     контроль: стоит сменить контур под строкой -- предлагается другая.
  2. Перестановка, как 15.10: под номером строки, на который ссылается
     участок, теперь лежит другая зона далеко, а контур поля -- под другим
     номером. План -- «другое поле», запись ставит новый номер.
  3. Запись меняет ТОЛЬКО `contour_id`: площадь, многоугольник, ответ
     оператора и время ответа -- те же байты. Сухой прогон не меняет базу.
  4. Участок получает поле, теряет его, не читается -- каждое называется.
  5. Участок, пересчитанный между планом и записью, пропускается (код 3);
     справочник, изменившийся между планом и записью, -- откат (код 1), а
     земли DJI без контура, переписанные сбором дронов, -- не изменение.
     База занята -- код 4; отчёт не записался -- код 5, строки записаны;
     постусловие не сошлось или инструмент упал внутри транзакции -- откат.
     Справочник с контурами, который не прочитался, -- код 1, ничего не
     посчитано: иначе запись сняла бы поле у всех участков.
  6. Неверный вход -- код 2 и ничего не записано; сухой прогон открывает
     базу только на чтение; консоль -- ASCII без имён.

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
import shapely                                                      # noqa: E402
from shapely.errors import GEOSException                            # noqa: E402

from gps import daily                                              # noqa: E402
from gps.daily import polygon_geojson                              # noqa: E402
from gps.tests.test_daily import read_fixture_track, read_fixture_zone, zone_geojson  # noqa: E402,E501
from gps_collector import storage                                  # noqa: E402
from tools.gps_territory_report import site_polygon                # noqa: E402

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


class Choice(World):
    """Выбор поля -- как у ночного расчёта и там, где ошибиться легко."""

    def site_utm(self):
        con = sqlite3.connect(self.db)
        try:
            text = con.execute("SELECT polygon_geojson FROM gps_work_polygons"
                               ).fetchone()[0]
        finally:
            con.close()
        return site_polygon(text)

    def test_duplicate_zones_tie_like_the_nightly(self):
        # [REASON]: одинаковая зона под двумя id -- обычное дело в Wialon;
        # «>=» вместо «>» или другой порядок перебора отдали бы участок
        # второй строке, и запись сменила бы поле у тысяч участков.
        self.contour(77, "A", self.field)
        self.contour(79, "B", self.field)
        self.compute()
        self.run_tool()
        self.assertEqual({row["status"] for row in self.plan_rows()}, {gr.SAME})

    def test_the_largest_overlap_wins_not_the_centre(self):
        self.compute()
        site = self.site_utm()
        centre = site.centroid
        minx, miny, maxx, maxy = site.bounds
        small = shapely.box(centre.x - 15, centre.y - 15, centre.x + 15, centre.y + 15)
        notch = shapely.box(centre.x - 25, centre.y - 25, maxx + 50, centre.y + 25)
        big = shapely.box(minx - 50, miny - 50, maxx + 50, maxy + 50).difference(notch)
        self.assertFalse(big.contains(centre))
        self.contour(90, "small", polygon_geojson(small))
        self.contour(91, "big", polygon_geojson(big))
        self.run_tool()
        self.assertEqual({(row["status"], row["contour_stalo"])
                          for row in self.plan_rows()}, {(gr.GAIN, "91")})

    def test_the_holes_of_a_site_are_kept(self):
        self.compute()
        site = self.site_utm()
        minx, miny, maxx, maxy = site.bounds
        hole = site.buffer(-25)
        ring = site.difference(hole)
        self.assertTrue(len(ring.interiors) >= 1)
        self.execute("UPDATE gps_work_polygons SET polygon_geojson = ?",
                     (polygon_geojson(ring),))
        # контур в дыре больше полосы на кольце -- но участок в дыре не лежит
        self.contour(92, "hole", polygon_geojson(hole.buffer(-1)))
        band = shapely.box(minx - 50, miny - 50, maxx + 50,
                           miny + (maxy - miny) * 0.3)
        self.contour(93, "band", polygon_geojson(band))
        self.run_tool()
        self.assertEqual({row["contour_stalo"] for row in self.plan_rows()}, {"93"})

    def test_a_switched_off_row_is_never_chosen(self):
        self.contour(77, "off", self.field)
        self.execute("UPDATE field_contours SET is_active = 0 WHERE id = 77")
        self.compute()
        site = self.site_utm()
        minx, miny, maxx, maxy = site.bounds
        self.contour(78, "part", polygon_geojson(
            shapely.box(minx, miny, (minx + maxx) / 2, maxy)))
        self.run_tool()
        self.assertEqual({row["contour_stalo"] for row in self.plan_rows()}, {"78"})

    def test_a_self_touching_site_is_repaired_not_failed(self):
        self.compute()
        site = self.site_utm()
        minx, miny, maxx, maxy = site.bounds
        bow = shapely.Polygon([(minx, miny), (maxx, maxy), (maxx, miny),
                               (minx, maxy), (minx, miny)])
        self.assertFalse(bow.is_valid)
        self.execute("UPDATE gps_work_polygons SET polygon_geojson = ?",
                     (polygon_geojson(bow),))
        self.contour(94, "lower", polygon_geojson(
            shapely.box(minx, miny, maxx, (miny + maxy) / 2)))
        self.run_tool()
        self.assertEqual({(row["status"], row["contour_stalo"])
                          for row in self.plan_rows()}, {(gr.GAIN, "94")})


class ApplyPaths(World):
    """Каждый код выхода записи и каждая её защита."""

    def setUp(self):
        super().setUp()
        self.contour(77, "Поле 3208", self.field)
        self.compute()
        self.remap()

    def fields(self):
        return {row[7] for row in self.sites()}

    def test_a_geometry_failure_keeps_the_field(self):
        with mock.patch.object(gr, "best_contour", side_effect=GEOSException("x")):
            self.run_tool("--apply")
        self.assertEqual({row["status"] for row in self.plan_rows()}, {gr.GEOS})
        self.assertEqual(self.fields(), {77})

    def test_a_field_changed_after_the_plan_is_skipped(self):
        con = sqlite3.connect(self.db, isolation_level=None)
        items = gr.plan(con, log=QUIET)
        fingerprint = gr.directory_fingerprint(con)
        con.execute("UPDATE gps_work_polygons SET contour_id = 79 WHERE id = ?",
                    (items[0]["row_id"],))
        con.execute("BEGIN IMMEDIATE")
        written, skipped = gr.apply(con, items, fingerprint)
        con.execute("COMMIT")
        con.close()
        self.assertEqual([item["row_id"] for item in skipped], [items[0]["row_id"]])

    def test_skipped_sites_give_exit_3_and_are_named(self):
        real_plan = gr.plan

        def plan_then_recompute(con, *args, **kwargs):
            items = real_plan(con, *args, **kwargs)
            self.execute("UPDATE gps_work_polygons SET polygon_geojson = "
                         "polygon_geojson || ' '")
            return items

        with mock.patch.object(gr, "plan", side_effect=plan_then_recompute):
            code, stdout, _ = self.run_tool("--apply")
        self.assertEqual(code, gr.EXIT_SKIPPED, stdout)
        self.assertTrue(all(row["primechanie"] for row in self.plan_rows()))

    def test_a_report_that_fails_after_commit_gives_exit_5(self):
        with mock.patch.object(gr, "write_plan", side_effect=PermissionError("x")):
            code, stdout, _ = self.run_tool("--apply")
        self.assertEqual(code, gr.EXIT_REPORT_FAILED, stdout)
        self.assertEqual(self.fields(), {78})

    def test_a_busy_database_gives_exit_4_and_nothing_written(self):
        holder = sqlite3.connect(self.db, isolation_level=None)
        holder.execute("BEGIN IMMEDIATE")
        try:
            with mock.patch.object(gr, "BUSY_TIMEOUT_S", 0.2):
                code, stdout, _ = self.run_tool("--apply")
        finally:
            holder.execute("ROLLBACK")
            holder.close()
        self.assertEqual(code, gr.EXIT_BUSY, stdout)
        self.assertEqual(self.fields(), {77})

    def test_a_failed_postcondition_rolls_back(self):
        # триггер портит запись -- постусловие обязано это увидеть и откатить
        self.execute("CREATE TRIGGER spoil AFTER UPDATE OF contour_id ON "
                     "gps_work_polygons WHEN NEW.contour_id IS NOT 1 BEGIN "
                     "UPDATE gps_work_polygons SET contour_id = 1 WHERE id = NEW.id; END")
        code, stdout, _ = self.run_tool("--apply")
        self.assertEqual(code, gr.EXIT_FAILED, stdout)
        self.assertEqual(self.fields(), {77})

    def test_a_crash_inside_the_transaction_rolls_back(self):
        real_apply = gr.apply

        def apply_then_crash(con, items, fingerprint):
            real_apply(con, items, fingerprint)
            raise ValueError("boom")

        with mock.patch.object(gr, "apply", side_effect=apply_then_crash):
            with self.assertRaises(ValueError):
                self.run_tool("--apply")
        self.assertEqual(self.fields(), {77})

    def test_until_limits_the_sites(self):
        self.run_tool("--until", "2026-07-26")
        self.assertEqual(self.plan_rows(), [])

    def test_the_dry_run_connection_is_read_only(self):
        real_plan = gr.plan
        refused = []

        def plan_and_try_to_write(con, *args, **kwargs):
            try:
                con.execute("UPDATE gps_work_polygons SET contour_id = contour_id")
            except sqlite3.OperationalError as problem:
                refused.append(str(problem))
            return real_plan(con, *args, **kwargs)

        with mock.patch.object(gr, "plan", side_effect=plan_and_try_to_write):
            self.run_tool()
        self.assertTrue(refused and "readonly" in refused[0], refused)

    def test_a_new_or_changed_outline_after_the_plan_aborts(self):
        for change in (
                "INSERT INTO field_contours (id, source, external_id, name, "
                "geometry_geojson, is_active, updated_at) VALUES (500, 'wialon', "
                "'500', 'n', '%s', 1, '2000-01-01')" % FAR,
                "UPDATE field_contours SET geometry_geojson = geometry_geojson "
                "|| ' ' WHERE id = 78",
                "UPDATE field_contours SET is_active = 0 WHERE id = 78"):
            con = sqlite3.connect(self.db, isolation_level=None)
            items = gr.plan(con, log=QUIET)
            fingerprint = gr.directory_fingerprint(con)
            con.execute(change)
            con.execute("BEGIN IMMEDIATE")
            with self.assertRaises(RuntimeError):
                gr.apply(con, items, fingerprint)
            con.execute("ROLLBACK")
            con.close()

    def test_a_dji_land_without_an_outline_is_not_a_change(self):
        self.execute("INSERT INTO field_contours (id, source, external_id, name, "
                     "geometry_geojson, updated_at) VALUES (600, 'dji', 'L1', "
                     "'land', NULL, '2026-10-01')")
        con = sqlite3.connect(self.db, isolation_level=None)
        items = gr.plan(con, log=QUIET)
        fingerprint = gr.directory_fingerprint(con)
        con.execute("UPDATE field_contours SET updated_at = '2030-01-01' WHERE id = 600")
        con.execute("BEGIN IMMEDIATE")
        written, skipped = gr.apply(con, items, fingerprint)
        con.execute("COMMIT")
        con.close()
        self.assertTrue(written)
        self.assertEqual(self.fields(), {78})

    def test_a_directory_of_switched_off_outlines_is_not_unreadable(self):
        # все контуры выключены -- поля нет ни у кого, и это не сбой чтения
        self.execute("UPDATE field_contours SET is_active = 0")
        code, stdout, _ = self.run_tool()
        self.assertEqual(code, gr.EXIT_OK, stdout)
        self.assertEqual({row["status"] for row in self.plan_rows()}, {gr.LOSE})

    def test_an_unreadable_directory_writes_nothing(self):
        # [REASON]: пустой справочник от упавшего чтения значил бы «поле
        # потеряно» у каждого участка
        with mock.patch.object(gr, "load_contours", return_value={}):
            code, stdout, _ = self.run_tool("--apply")
        self.assertEqual(code, gr.EXIT_FAILED, stdout)
        self.assertEqual(self.fields(), {77})


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
