# -*- coding: utf-8 -*-
"""Приёмочный набор к tools/dji_polygon_audit_001.py.

Аудит нельзя прогнать здесь на живой базе -- прода из среды разработки нет.
Поэтому набор обязан доказать не «скрипт запускается», а то, что он
РАЗЛИЧАЕТ: годный полигон от самопересекающегося, поле от препятствия,
му от квадратных метров, и что объявленное расхождение он показывает тем
числом, каким оно есть.

[REASON]: первый тест -- калибровка собственной линейки. Всё остальное
измеряет расхождение в единицах процента, поэтому если площадь квадрата со
известной стороной считается неверно, весь аудит выдаёт уверенные неверные
числа. Проверка идёт на квадрате 100 и 300 м: отклонение должно быть на
два порядка меньше того, что мы собираемся измерять.

Запуск:
    python tools/test_dji_polygon_audit_001.py
"""
import hashlib
import io
import json
import math
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from drone_collector.geometry import EARTH_RADIUS_M, ring_area_m2   # noqa: E402
from tools import dji_polygon_audit_001 as audit                    # noqa: E402

LAT = 39.77
LNG = 64.42


# ─── фикстуры ────────────────────────────────────────────────────────────────

def square_ring(side_m, lat=LAT, lng=LNG):
    """Замкнутое кольцо-квадрат со стороной side_m. Координаты [lng, lat]."""
    dlat = side_m / (EARTH_RADIUS_M * math.pi / 180.0)
    dlng = dlat / math.cos(math.radians(lat))
    return [[lng, lat], [lng + dlng, lat], [lng + dlng, lat + dlat],
            [lng, lat + dlat], [lng, lat]]


def bowtie_ring(side_m, lat=LAT, lng=LNG):
    """Самопересекающаяся «восьмёрка» -- два верхних угла переставлены.

    [REASON]: несимметричная восьмёрка проходит проверку площади и даёт
    уверенное неверное число. Трек уже ловил этот дефект на ревью PR #107,
    поэтому он обязан быть в наборе.
    """
    ring = square_ring(side_m, lat, lng)
    ring[2], ring[3] = ring[3], ring[2]
    return ring


def feature(ring, func_type='PlantZone'):
    return {'type': 'Feature',
            'properties': {'funcType': func_type},
            'geometry': {'type': 'Polygon', 'coordinates': [ring]}}


def document(*features):
    return {'type': 'FeatureCollection', 'features': list(features)}


def body_of(doc):
    return json.dumps(doc, ensure_ascii=False).encode('utf-8')


GEOMETRY_DDL = """
CREATE TABLE dji_land_geometries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    content_md5 VARCHAR(32) NOT NULL UNIQUE,
    sha256 VARCHAR(64) NOT NULL,
    size_bytes INTEGER NOT NULL,
    md5_verified BOOLEAN NOT NULL DEFAULT 0,
    body_blob BLOB NOT NULL,
    parse_status VARCHAR(20),
    ring_count INTEGER,
    source_revision_id INTEGER,
    first_seen_at DATETIME NOT NULL)"""

REVISION_DDL = """
CREATE TABLE dji_land_revisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    land_uuid VARCHAR(40) NOT NULL,
    external_id VARCHAR(120),
    serial_number VARCHAR(50),
    name VARCHAR(300),
    total_area_raw FLOAT,
    work_area_raw FLOAT,
    obstacle_area_raw FLOAT,
    area_unit VARCHAR(10) NOT NULL DEFAULT 'mu',
    geometry_md5 VARCHAR(32),
    geometry_storage_uuid VARCHAR(40),
    land_type VARCHAR(40),
    created_at_source DATETIME,
    updated_at_source DATETIME,
    center_lat FLOAT,
    center_lng FLOAT,
    raw_json TEXT NOT NULL,
    raw_sha256 VARCHAR(64) NOT NULL,
    first_seen_snapshot_id INTEGER NOT NULL,
    last_seen_snapshot_id INTEGER NOT NULL,
    seen_count INTEGER NOT NULL DEFAULT 1)"""


class Base(unittest.TestCase):

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix='polyaudit-')
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        self.db = os.path.join(self.dir, 'transport.db')
        con = sqlite3.connect(self.db)
        con.execute(GEOMETRY_DDL)
        con.execute(REVISION_DDL)
        con.commit()
        con.close()
        self._md5 = 0

    def add_geometry(self, body, md5=None, verified=1):
        self._md5 += 1
        md5 = md5 or ('%032x' % self._md5)
        con = sqlite3.connect(self.db)
        con.execute(
            'INSERT INTO dji_land_geometries (content_md5, sha256, '
            'size_bytes, md5_verified, body_blob, first_seen_at) '
            'VALUES (?,?,?,?,?,?)',
            (md5, hashlib.sha256(body).hexdigest(), len(body), verified,
             body, '2026-09-12 00:00:00'))
        con.commit()
        con.close()
        return md5

    def add_land(self, md5, total=None, work=None, obstacle=None,
                 unit='mu', uuid=None, name='field'):
        uuid = uuid or ('land-%s' % md5[-6:])
        con = sqlite3.connect(self.db)
        con.execute(
            'INSERT INTO dji_land_revisions (land_uuid, name, '
            'total_area_raw, work_area_raw, obstacle_area_raw, area_unit, '
            'geometry_md5, raw_json, raw_sha256, first_seen_snapshot_id, '
            'last_seen_snapshot_id) VALUES (?,?,?,?,?,?,?,?,?,?,?)',
            (uuid, name, total, work, obstacle, unit, md5, '{}', 'x' * 64,
             1, 1))
        con.commit()
        con.close()
        return uuid

    def run_audit(self):
        con = audit.connect_read_only(self.db)
        try:
            return audit.audit(con)
        finally:
            con.close()

    def only_row(self, rows, status=None):
        if status is not None:
            rows = [r for r in rows if r['geometry_status'] == status]
        self.assertEqual(len(rows), 1, [r['geometry_status'] for r in rows])
        return rows[0]

    def mu(self, area_m2):
        return area_m2 / audit.MU_M2


# ─── 1. Калибровка линейки ───────────────────────────────────────────────────

class TheRulerItself(unittest.TestCase):
    """Площадь квадрата с известной стороной. Без этого всё остальное -- вера."""

    def test_a_known_square_measures_its_own_side(self):
        for side in (100.0, 300.0, 750.0):
            got = abs(ring_area_m2(square_ring(side)))
            error_pct = abs(got - side * side) / (side * side) * 100.0
            self.assertLess(error_pct, 0.01,
                            'сторона %.0f м: отклонение %.4f%%'
                            % (side, error_pct))

    def test_the_error_is_far_below_what_we_measure(self):
        """Линейка обязана быть точнее измеряемого эффекта, иначе она его и есть."""
        side = 300.0
        error_pct = (abs(abs(ring_area_m2(square_ring(side))) - side * side)
                     / (side * side) * 100.0)
        # Аудит рассуждает о расхождениях порядка процента.
        self.assertLess(error_pct * 100, 1.0)

    def test_mu_is_not_rounded_to_666(self):
        # [REASON]: 666.0 вместо 2000/3 даёт систематический сдвиг 0.1% --
        # ровно в том порядке, который мы измеряем.
        self.assertAlmostEqual(audit.MU_M2, 666.6666666, places=6)
        self.assertNotEqual(audit.MU_M2, 666.0)


# ─── 2. Согласие и расхождение ───────────────────────────────────────────────

class TheAgreementIsMeasuredNotAsserted(Base):

    def test_a_matching_declaration_reads_as_zero(self):
        ring = square_ring(300.0)
        area = abs(ring_area_m2(ring))
        md5 = self.add_geometry(body_of(document(feature(ring))))
        self.add_land(md5, total=self.mu(area))
        rows, summary = self.run_audit()
        row = self.only_row(rows)
        self.assertEqual(row['geometry_status'], 'OK')
        self.assertLess(abs(row['pct_vs_total']), 0.01)
        self.assertEqual(summary['counters']['geometries_ok'], 1)

    def test_a_declaration_ten_percent_small_reads_as_plus_ten(self):
        """Контроль знака и величины: без него «ноль» мог быть случайным."""
        ring = square_ring(300.0)
        area = abs(ring_area_m2(ring))
        md5 = self.add_geometry(body_of(document(feature(ring))))
        # Объявлено меньше на 10% => (c-d)/d = +11.11%
        self.add_land(md5, total=self.mu(area / 1.10))
        row = self.only_row(self.run_audit()[0])
        self.assertAlmostEqual(row['pct_vs_total'], 10.0, places=2)

    def test_fifteen_mu_is_exactly_one_hectare(self):
        """Независимый якорь единицы: 15 му = 10 000 м2 по определению.

        [REASON]: остальные тесты объявляют площадь через `self.mu()`, то
        есть делят на ту же константу, на которую аудит потом умножает.
        Неверная константа в такой паре сокращается и остаётся невидимой.
        Здесь число 15.0 вписано руками, поэтому подмена MU_M2 ломает тест.
        """
        ring = square_ring(100.0)               # 100 x 100 м = 1 га
        md5 = self.add_geometry(body_of(document(feature(ring))))
        self.add_land(md5, total=15.0)
        row = self.only_row(self.run_audit()[0])
        self.assertEqual(row['declared_total_m2'], 10000.0)
        self.assertLess(abs(row['pct_vs_total']), 0.01)

    def test_a_declaration_in_square_metres_is_not_read_as_mu(self):
        ring = square_ring(300.0)
        area = abs(ring_area_m2(ring))
        md5 = self.add_geometry(body_of(document(feature(ring))))
        self.add_land(md5, total=area, unit='m2')
        row = self.only_row(self.run_audit()[0])
        self.assertEqual(row['unit_status'], 'OK')
        self.assertLess(abs(row['pct_vs_total']), 0.01)

    def test_an_unknown_unit_is_refused_not_guessed(self):
        """Угаданная единица даёт ошибку в 667 раз и убедительные числа."""
        ring = square_ring(300.0)
        md5 = self.add_geometry(body_of(document(feature(ring))))
        self.add_land(md5, total=123.0, unit='acres')
        rows, summary = self.run_audit()
        row = self.only_row(rows)
        self.assertTrue(row['unit_status'].startswith('UNIT_UNKNOWN'))
        self.assertIsNone(row['pct_vs_total'])
        # И строка не попала в статистику.
        self.assertEqual(summary['agreement']['pct_vs_total']['n'], 0)

    def test_the_best_match_is_chosen_by_the_data(self):
        """Сумма PlantZone сверяется со всеми тремя, ближайшее выбирается."""
        ring = square_ring(300.0)
        area = abs(ring_area_m2(ring))
        md5 = self.add_geometry(body_of(document(feature(ring))))
        # work объявлен точно, total -- завышен вдвое.
        self.add_land(md5, total=self.mu(area * 2.0), work=self.mu(area))
        summary = self.run_audit()[1]
        self.assertEqual(summary['best_match']['field'], 'pct_vs_work')
        self.assertLess(summary['best_match']['median_abs_pct'], 0.01)


# ─── 3. Битая геометрия ──────────────────────────────────────────────────────

class BrokenGeometryIsNamedNotSilentlyUsed(Base):

    def test_a_self_intersecting_ring_is_rejected(self):
        ring = bowtie_ring(300.0)
        md5 = self.add_geometry(body_of(document(feature(ring))))
        self.add_land(md5, total=10.0)
        rows, summary = self.run_audit()
        row = self.only_row(rows)
        self.assertEqual(row['geometry_status'], 'REJECTED')
        self.assertIn('intersects itself', row['reasons'])
        self.assertEqual(summary['counters']['geometries_rejected_by_validator'],
                         1)

    def test_a_rejected_polygon_never_reaches_the_statistics(self):
        """Иначе площадь неопределённого полигона задаёт полосу closure."""
        good = square_ring(300.0)
        area = abs(ring_area_m2(good))
        md5_ok = self.add_geometry(body_of(document(feature(good))))
        self.add_land(md5_ok, total=self.mu(area), uuid='good')
        md5_bad = self.add_geometry(body_of(document(feature(
            bowtie_ring(300.0)))))
        self.add_land(md5_bad, total=self.mu(area * 3), uuid='bad')
        summary = self.run_audit()[1]
        # В выборке только годный, иначе медиана уехала бы к 200%.
        self.assertEqual(summary['agreement']['pct_vs_total']['n'], 1)
        self.assertLess(summary['agreement']['pct_vs_total']['median_abs_pct'],
                        0.01)

    def test_an_unclosed_ring_is_rejected(self):
        ring = square_ring(300.0)[:-1]          # без замыкающей точки
        md5 = self.add_geometry(body_of(document(feature(ring))))
        self.add_land(md5, total=10.0)
        row = self.only_row(self.run_audit()[0])
        self.assertEqual(row['geometry_status'], 'REJECTED')
        self.assertIn('not closed', row['reasons'])

    def test_a_body_that_is_not_json_is_unreadable_not_a_crash(self):
        md5 = self.add_geometry(b'\x00\x01 not json at all')
        self.add_land(md5, total=10.0)
        rows, summary = self.run_audit()
        row = self.only_row(rows)
        self.assertEqual(row['geometry_status'], 'UNREADABLE')
        self.assertEqual(summary['counters']['geometries_unreadable'], 1)
        self.assertTrue(summary['read_errors'])

    def test_a_document_without_plant_zone_is_its_own_class(self):
        ring = square_ring(300.0)
        md5 = self.add_geometry(body_of(document(
            feature(ring, func_type='ObstacleZone'))))
        self.add_land(md5, total=10.0)
        rows, summary = self.run_audit()
        row = self.only_row(rows)
        self.assertEqual(row['geometry_status'], 'NO_PLANT_ZONE')
        self.assertEqual(summary['counters']['no_plant_zone'], 1)
        self.assertEqual(row['plant_area_m2'], 0.0)
        self.assertGreater(row['obstacle_area_m2'], 0.0)


# ─── 4. Поле и препятствие ───────────────────────────────────────────────────

class ObstaclesAreNotField(Base):

    def test_the_obstacle_is_excluded_from_the_field_area(self):
        """`describe_geometry` суммирует ВСЕ фигуры -- разложение обязано быть."""
        field = square_ring(300.0)
        hole = square_ring(50.0, lat=LAT + 0.0005, lng=LNG + 0.0005)
        field_area = abs(ring_area_m2(field))
        md5 = self.add_geometry(body_of(document(
            feature(field), feature(hole, func_type='ObstacleZone'))))
        self.add_land(md5, total=self.mu(field_area))
        row = self.only_row(self.run_audit()[0])
        self.assertEqual(row['geometry_status'], 'OK')
        # Площадь поля -- только PlantZone, препятствие посчитано отдельно.
        self.assertLess(abs(row['pct_vs_total']), 0.01)
        self.assertAlmostEqual(row['obstacle_area_m2'],
                               abs(ring_area_m2(hole)), delta=1.0)


# ─── 5. Область действия и связи ─────────────────────────────────────────────

class ScopeAndLinkage(Base):

    def test_one_polygon_serving_two_lands_gives_two_rows(self):
        ring = square_ring(300.0)
        area = abs(ring_area_m2(ring))
        md5 = self.add_geometry(body_of(document(feature(ring))))
        self.add_land(md5, total=self.mu(area), uuid='a')
        self.add_land(md5, total=self.mu(area), uuid='b')
        rows, summary = self.run_audit()
        self.assertEqual(len(rows), 2)
        self.assertEqual({r['land_uuid'] for r in rows}, {'a', 'b'})
        # Тело одно -- счётчик тел не должен удваиваться.
        self.assertEqual(summary['counters']['geometries_in_store'], 1)
        self.assertEqual(summary['counters']['current_lands_with_body'], 2)

    def test_only_the_current_revision_drives_the_headline(self):
        """Старая ревизия с другим полигоном не обязана портить сегодняшнее число."""
        old = square_ring(300.0)
        new = square_ring(300.0, lat=LAT + 0.01)
        area = abs(ring_area_m2(new))
        md5_old = self.add_geometry(body_of(document(feature(old))))
        md5_new = self.add_geometry(body_of(document(feature(new))))
        # Одна земля, две ревизии: старая объявляет чушь, новая -- правду.
        self.add_land(md5_old, total=self.mu(area * 5), uuid='same')
        self.add_land(md5_new, total=self.mu(area), uuid='same')
        rows, summary = self.run_audit()
        current = [r for r in rows if r['scope'] == 'CURRENT_REVISION']
        self.assertEqual(len(current), 1)
        self.assertEqual(current[0]['content_md5'], md5_new)
        self.assertEqual(summary['agreement']['pct_vs_total']['n'], 1)
        self.assertLess(summary['agreement']['pct_vs_total']['median_abs_pct'],
                        0.01)

    def test_a_body_no_current_land_needs_is_counted_and_not_compared(self):
        ring = square_ring(300.0)
        self.add_geometry(body_of(document(feature(ring))))   # ничей
        rows, summary = self.run_audit()
        row = self.only_row(rows)
        self.assertEqual(row['scope'], 'BODY_WITHOUT_CURRENT_LAND')
        self.assertEqual(row['unit_status'], 'NO_CURRENT_REVISION')
        self.assertEqual(
            summary['counters']['geometries_not_referenced_by_current'], 1)
        self.assertEqual(summary['agreement']['pct_vs_total']['n'], 0)

    def test_a_land_without_a_geometry_reference_is_counted(self):
        self.add_land(None, total=10.0, uuid='no-poly')
        summary = self.run_audit()[1]
        self.assertEqual(
            summary['counters']['current_lands_without_geometry_md5'], 1)


# ─── 6. Только чтение ────────────────────────────────────────────────────────

class ItReallyOnlyReads(Base):

    def sha(self):
        with io.open(self.db, 'rb') as handle:
            return hashlib.sha256(handle.read()).hexdigest()

    def test_the_database_is_byte_identical_afterwards(self):
        ring = square_ring(300.0)
        md5 = self.add_geometry(body_of(document(feature(ring))))
        self.add_land(md5, total=10.0)
        before = self.sha()
        self.run_audit()
        self.assertEqual(self.sha(), before)

    def test_a_write_would_be_refused_by_the_connection(self):
        """Контроль: без него равенство хешей доказывало бы лишь что мы не писали."""
        con = audit.connect_read_only(self.db)
        try:
            with self.assertRaises(sqlite3.OperationalError):
                con.execute('DELETE FROM dji_land_geometries')
        finally:
            con.close()

    def test_a_missing_database_exits_2_and_creates_nothing(self):
        absent = os.path.join(self.dir, 'nope.db')
        with self.assertRaises(SystemExit) as caught:
            audit.connect_read_only(absent)
        self.assertEqual(caught.exception.code, 2)
        self.assertFalse(os.path.exists(absent),
                         'аудит завёл базу, которой не было')

    def test_a_database_without_the_tables_exits_2(self):
        other = os.path.join(self.dir, 'other.db')
        sqlite3.connect(other).close()
        con = audit.connect_read_only(other)
        try:
            with self.assertRaises(SystemExit) as caught:
                audit.require_tables(con)
        finally:
            con.close()
        self.assertEqual(caught.exception.code, 2)


# ─── 7. Вывод ────────────────────────────────────────────────────────────────

class TheOutput(Base):

    def build_one(self):
        ring = square_ring(300.0)
        area = abs(ring_area_m2(ring))
        md5 = self.add_geometry(body_of(document(feature(ring))))
        self.add_land(md5, total=self.mu(area))
        return area

    def test_the_console_carries_no_cyrillic(self):
        """Консоль сервера ломает кириллицу -- это правило устава, не вкус."""
        self.build_one()
        lines = audit.console_lines(self.run_audit()[1])
        joined = '\n'.join(lines)
        self.assertTrue(joined)
        joined.encode('ascii')          # бросит UnicodeEncodeError, если есть

    def test_the_verdict_file_does_carry_the_explanation(self):
        self.build_one()
        text = '\n'.join(audit.verdict_lines(self.run_audit()[1]))
        self.assertIn('ТОЧНОСТЬ КОНТУРА', text)
        # Ограничение обязано быть названо, а не подразумеваться.
        self.assertIn('оба числа приходят от', text)

    def test_the_run_writes_three_files_and_exits_zero(self):
        self.build_one()
        out = os.path.join(self.dir, 'out')
        code = audit.main(['--db', self.db, '--out-dir', out])
        self.assertEqual(code, 0)
        runs = os.listdir(out)
        self.assertEqual(len(runs), 1)
        made = sorted(os.listdir(os.path.join(out, runs[0])))
        self.assertEqual(made, ['POLYGON_AUDIT.csv',
                                'POLYGON_AUDIT_SUMMARY.json', 'VERDICT.txt'])

    def test_every_csv_column_is_filled_by_the_row_builder(self):
        """Колонка, которой нет в строке, уехала бы в файл пустой навсегда."""
        self.build_one()
        rows = self.run_audit()[0]
        for column in audit.CSV_COLUMNS:
            self.assertIn(column, rows[0], column)


if __name__ == '__main__':
    unittest.main(verbosity=2)
