# -*- coding: utf-8 -*-
"""GPS-21: что изменила запись зеркала контуров -- копия до против базы после.

Что проверяется и почему именно это:

1. **НИЧЕГО НЕ ПИШЕТ.** Обе базы после прогона побайтно те же: `mode=ro` --
   запрет SQLite, а не обещание скрипта.

2. **Неверный вход -- отказ с кодом 2 до записи.** Отсутствующая база не
   создаётся; --before и --after -- один файл, в том числе записанный иначе;
   `#` и `%` в пути (без проверки SQLite открыл бы другой файл); базы без
   `field_contours` и файл не базы; --out на месте базы, её -wal или любого
   файла SQLite; нет папки; --out открыт в Excel.

3. **Виды изменений различаются.** Каждый вид -- своя зона в одной паре баз:
   имя с дописанным районом, имя с лишним пробелом, те же координаты другой
   записью, то же кольцо с другого начала и в другую сторону, те же точки в
   другом порядке, сдвиг на 50 м, увеличенный контур, контур появился,
   пропал, не читается, новая зона, пропавшая зона, `is_active`. Строка DJI
   с тем же `external_id` не сравнивается.

4. **Площадь и сдвиг -- в метрах, а не в градусах,** и метры не взяты у
   самого инструмента: квадрат 0,001 x 0,001 градуса у 40 с. ш. -- 0,9472 га,
   сдвиг на 0,001 градуса к северу -- 111,19 м. Сдвиг -- центра площади:
   точки, добавленные на одну сторону, контур не двигают.

5. **Сетка тождества точек -- 1e-7 градуса (~1 см):** 3e-8 -- та же точка,
   1e-7 -- другая. Корзины площади и сдвига, их строки в консоли, знак
   разницы, число точек, BOM для Excel.

6. **Консоль -- ASCII и без имён:** имена зон -- часто имена людей.

Запуск:
  python -m unittest tests.test_gps_mirror_diff -v
"""
import csv
import hashlib
import io
import json
import math
import os
import sqlite3
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

import tools.gps_mirror_diff as gd                                  # noqa: E402
from gps_collector import config                                    # noqa: E402

FIELD_CONTOURS_DDL = (
    'CREATE TABLE field_contours (id INTEGER PRIMARY KEY AUTOINCREMENT, '
    'source VARCHAR(20) NOT NULL, external_id VARCHAR(100), '
    'name VARCHAR(300) NOT NULL, name_uz VARCHAR(300), '
    'geometry_geojson TEXT, area_ha FLOAT, customer_id INTEGER, '
    'season_year INTEGER, is_active BOOLEAN DEFAULT 1, '
    'created_at DATETIME, updated_at DATETIME, '
    'CONSTRAINT uq_field_contours_source_external UNIQUE (source, external_id))')

LAT0, LON0 = 40.0, 64.4
M_PER_DEG_LAT = math.radians(1) * gd.EARTH_RADIUS_M
M_PER_DEG_LON = M_PER_DEG_LAT * math.cos(math.radians(LAT0))


def square(east_m=0.0, north_m=0.0, side_m=100.0):
    """Кольцо квадрата со стороной side_m, сдвинутого от (LON0, LAT0) в метрах."""
    lon = LON0 + east_m / M_PER_DEG_LON
    lat = LAT0 + north_m / M_PER_DEG_LAT
    dlon, dlat = side_m / M_PER_DEG_LON, side_m / M_PER_DEG_LAT
    return [[lon, lat], [lon + dlon, lat], [lon + dlon, lat + dlat],
            [lon, lat + dlat], [lon, lat]]


def text(ring, **dumps):
    """Так пишет зеркало (`gps_mirror_contours.geometry_geojson`)."""
    return json.dumps({'type': 'Polygon', 'coordinates': [ring]},
                      ensure_ascii=False, **dumps)


BASE = square()
PENTAGON = [[64.40, 40.00], [64.41, 40.00], [64.412, 40.006], [64.405, 40.01],
            [64.398, 40.006], [64.40, 40.00]]
# те же пять точек, но порядок -- не круг: 0, 2, 1, 3, 4
SHUFFLED = [PENTAGON[0], PENTAGON[2], PENTAGON[1], PENTAGON[3], PENTAGON[4],
            PENTAGON[0]]
ROTATED = PENTAGON[2:5] + PENTAGON[0:2] + [PENTAGON[2]]
REVERSED = PENTAGON[::-1]

# id: (до, после); строка -- (имя, геометрия, is_active); None -- строки нет
CASES = {
    '1': (('Поле 1', text(BASE), 1), ('Поле 1', text(BASE), 1)),
    '2': (('Поле 2', text(BASE), 1), ('Шофиркон | Поле 2', text(BASE), 1)),
    '3': (('Поле  3', text(BASE), 1), ('Поле 3', text(BASE), 1)),
    '4': (('Поле 4', text(BASE), 1), ('Поле 4', text(BASE, indent=1), 1)),
    '5': (('Поле 5', text(PENTAGON), 1), ('Поле 5', text(ROTATED), 1)),
    '6': (('Поле 6', text(PENTAGON), 1), ('Поле 6', text(REVERSED), 1)),
    '7': (('Поле 7', text(PENTAGON), 1), ('Поле 7', text(SHUFFLED), 1)),
    '8': (('Поле 8', text(BASE), 1), ('Поле 8', text(square(east_m=50)), 1)),
    '9': (('Поле 9', text(BASE), 1), ('Поле 9', text(square(side_m=200)), 1)),
    '10': (('Поле 10', None, 1), ('Поле 10', text(BASE), 1)),
    '11': (('Поле 11', text(BASE), 1), ('Поле 11', None, 1)),
    '12': (('Поле 12', text(BASE), 1), ('Поле 12', '{"type": "Point"}', 1)),
    '13': (None, ('Когонча ПТМ', text(BASE), 1)),
    '14': (('Ali Valiyev фх', text(BASE), 1), None),
    '15': (('Поле 15', text(BASE), 1), ('Поле 15', text(BASE), 0)),
    '16': (('Шофиркон | Поле 16', text(BASE), 1),
           ('Гиждувон | Поле 16', text(square(north_m=500)), 1)),
}
NAMES = sorted({row[0] for pair in CASES.values() for row in pair if row})


def make_db(path, side):
    con = sqlite3.connect(path)
    con.execute(FIELD_CONTOURS_DDL)
    for key, pair in CASES.items():
        row = pair[side]
        if row is not None:
            con.execute('INSERT INTO field_contours (source, external_id, name, '
                        'geometry_geojson, is_active) VALUES (?, ?, ?, ?, ?)',
                        ('wialon', key, row[0], row[1], row[2]))
    # контур DJI с тем же external_id, изменившийся -- не дело зеркала
    con.execute('INSERT INTO field_contours (source, external_id, name, '
                'geometry_geojson) VALUES (?, ?, ?, ?)',
                ('dji', '1', 'DJI %d' % side,
                 text(square(east_m=1000 * side))))
    con.commit()
    con.close()


def digest(path):
    with open(path, 'rb') as handle:
        return hashlib.sha256(handle.read()).hexdigest()


class Run(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.mkdtemp()
        self.before = os.path.join(self.folder, 'transport_kontury.db')
        self.after = os.path.join(self.folder, 'transport.db')
        self.out = os.path.join(self.folder, 'mirror_diff.csv')
        make_db(self.before, 0)
        make_db(self.after, 1)

    def run_tool(self, *extra, before=None, after=None, out=None):
        stdout, stderr = io.StringIO(), io.StringIO()
        argv = ['--before', before or self.before, '--after', after or self.after,
                '--out', out or self.out] + list(extra)
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = gd.main(argv)
        return code, stdout.getvalue(), stderr.getvalue()

    def rows(self):
        with open(self.out, encoding='utf-8-sig', newline='') as handle:
            return {row['external_id']: row
                    for row in csv.DictReader(handle, delimiter=';')}


class WritesNothing(Run):
    def test_both_databases_are_byte_identical_after_the_run(self):
        before, after = digest(self.before), digest(self.after)
        code, _, _ = self.run_tool()
        self.assertEqual(code, gd.EXIT_OK)
        self.assertEqual(digest(self.before), before)
        self.assertEqual(digest(self.after), after)

    def test_the_connection_refuses_to_write(self):
        con = gd.open_readonly(self.after)
        try:
            with self.assertRaises(sqlite3.OperationalError):
                con.execute("UPDATE field_contours SET name = 'x'")
        finally:
            con.close()


class BadInput(Run):
    def assert_refused(self, code, stderr):
        self.assertEqual(code, gd.EXIT_BAD_INPUT, stderr)
        self.assertTrue(stderr.startswith('ERROR: '), stderr)
        self.assertFalse(os.path.exists(self.out))

    def test_a_missing_database_is_refused_and_not_created(self):
        for side in ('before', 'after'):
            missing = os.path.join(self.folder, 'no_%s.db' % side)
            code, _, stderr = self.run_tool(**{side: missing})
            self.assert_refused(code, stderr)
            self.assertFalse(os.path.exists(missing))

    def test_the_same_file_twice_is_refused(self):
        code, _, stderr = self.run_tool(after=self.before)
        self.assert_refused(code, stderr)

    def test_a_hash_in_the_path_is_refused(self):
        odd = os.path.join(self.folder, 'copy#1.db')
        make_db(odd, 0)
        code, _, stderr = self.run_tool(before=odd)
        self.assert_refused(code, stderr)
        # без проверки адрес обрезался бы на '#': SQLite открыл бы 'copy' на
        # запись и создал бы его
        self.assertIn('#', stderr)
        self.assertFalse(os.path.exists(os.path.join(self.folder, 'copy')))

    def test_a_percent_in_the_path_does_not_open_another_file(self):
        # 'a%41.db' в адресе SQLite -- это 'aA.db': без проверки инструмент
        # сравнил бы файл сам с собой и сказал бы «ничего не изменилось»
        target = os.path.join(self.folder, 'aA.db')
        make_db(target, 0)
        odd = os.path.join(self.folder, 'a%41.db')
        make_db(odd, 1)
        code, _, stderr = self.run_tool(before=target, after=odd)
        self.assert_refused(code, stderr)

    def test_the_same_file_spelled_otherwise_is_refused(self):
        code, _, stderr = self.run_tool(
            after=os.path.join(self.folder, '.', os.path.basename(self.before)))
        self.assert_refused(code, stderr)

    def test_a_file_that_is_not_a_database_is_refused(self):
        bogus = os.path.join(self.folder, 'notes.db')
        with open(bogus, 'w') as handle:
            handle.write('not a database at all, just text ' * 10)
        code, _, stderr = self.run_tool(before=bogus)
        self.assert_refused(code, stderr)

    def test_out_on_the_live_wal_is_refused(self):
        # заголовок WAL -- не 'SQLite format 3': защищает только список путей
        wal = self.after + '-wal'
        with open(wal, 'wb') as handle:
            handle.write(b'\x37\x7f\x06\x82' + b'\0' * 28)
        before = digest(wal)
        code, _, stderr = self.run_tool(out=wal)
        self.assertEqual(code, gd.EXIT_BAD_INPUT, stderr)
        self.assertEqual(digest(wal), before)

    def test_an_out_file_open_in_excel_is_refused_before_the_report(self):
        with open(self.out, 'w', encoding='utf-8') as handle:
            handle.write('previous run')
        real_open = open

        def locked(path, mode='r', *args, **kwargs):
            if os.path.abspath(str(path)) == os.path.abspath(self.out) \
                    and mode[0] in 'aw':
                raise PermissionError(13, 'Permission denied')
            return real_open(path, mode, *args, **kwargs)

        with mock.patch('builtins.open', side_effect=locked):
            code, stdout, stderr = self.run_tool()
        self.assertEqual(code, gd.EXIT_BAD_INPUT, stderr)
        self.assertEqual(stdout, '')
        with open(self.out, encoding='utf-8') as handle:
            self.assertEqual(handle.read(), 'previous run')

    def test_a_database_without_field_contours_is_refused(self):
        empty = os.path.join(self.folder, 'other.db')
        con = sqlite3.connect(empty)
        con.execute('CREATE TABLE t (x)')
        con.commit()
        con.close()
        code, _, stderr = self.run_tool(before=empty)
        self.assert_refused(code, stderr)

    def test_out_on_a_database_or_any_sqlite_file_is_refused(self):
        after = digest(self.after)
        code, _, stderr = self.run_tool(out=self.after)
        self.assertEqual(code, gd.EXIT_BAD_INPUT, stderr)
        self.assertEqual(digest(self.after), after)
        stray = os.path.join(self.folder, 'report.csv')
        make_db(stray, 0)
        stray_digest = digest(stray)
        code, _, stderr = self.run_tool(out=stray)
        self.assertEqual(code, gd.EXIT_BAD_INPUT, stderr)
        self.assertEqual(digest(stray), stray_digest)

    def test_a_missing_out_folder_is_refused(self):
        code, _, stderr = self.run_tool(
            out=os.path.join(self.folder, 'no_such_folder', 'diff.csv'))
        self.assertEqual(code, gd.EXIT_BAD_INPUT, stderr)


class Kinds(Run):
    def setUp(self):
        super().setUp()
        code, self.stdout, stderr = self.run_tool()
        self.assertEqual(code, gd.EXIT_OK, stderr)
        self.found = self.rows()

    def test_an_unchanged_zone_is_not_listed(self):
        self.assertNotIn('1', self.found)

    def test_a_district_added_to_the_name(self):
        row = self.found['2']
        self.assertEqual((row['izmenenie'], row['imya_vnutri_novogo'],
                          row['imya_tolko_probely'], row['geometriya']),
                         ('imya', 'da', 'net', 'tot_zhe_tekst'))

    def test_a_name_that_differs_only_in_spaces(self):
        row = self.found['3']
        self.assertEqual((row['izmenenie'], row['imya_tolko_probely']),
                         ('imya', 'da'))

    def test_the_same_coordinates_written_otherwise(self):
        row = self.found['4']
        self.assertEqual((row['izmenenie'], row['geometriya']),
                         ('geometriya', 'te_zhe_koordinaty'))

    def test_the_same_ring_from_another_start_or_the_other_way(self):
        self.assertEqual(self.found['5']['geometriya'], 'to_zhe_koltso')
        self.assertEqual(self.found['6']['geometriya'], 'to_zhe_koltso')

    def test_the_same_points_in_another_order(self):
        self.assertEqual(self.found['7']['geometriya'], 'te_zhe_tochki')

    def test_a_shifted_outline_is_measured_in_metres(self):
        row = self.found['8']
        self.assertEqual(row['geometriya'], 'drugie_tochki')
        self.assertAlmostEqual(float(row['sdvig_tsentra_m']), 50.0, delta=0.5)
        self.assertAlmostEqual(float(row['ploshchad_do_ga']), 1.0, delta=0.005)
        self.assertAlmostEqual(float(row['raznitsa_ga']), 0.0, delta=0.001)

    def test_an_enlarged_outline(self):
        row = self.found['9']
        self.assertAlmostEqual(float(row['ploshchad_do_ga']), 1.0, delta=0.005)
        self.assertAlmostEqual(float(row['ploshchad_posle_ga']), 4.0, delta=0.02)
        self.assertAlmostEqual(float(row['sdvig_tsentra_m']), 70.7, delta=0.5)

    def test_an_outline_that_appeared_disappeared_or_cannot_be_read(self):
        self.assertEqual(self.found['10']['geometriya'], 'poyavilsya')
        self.assertEqual(self.found['11']['geometriya'], 'propal')
        self.assertEqual(self.found['12']['geometriya'], 'ne_chitaetsya')

    def test_a_new_zone_and_a_gone_zone(self):
        self.assertEqual(self.found['13']['izmenenie'], 'novaya')
        self.assertEqual(self.found['14']['izmenenie'], 'propala')

    def test_is_active_is_compared(self):
        row = self.found['15']
        self.assertEqual((row['izmenenie'], row['is_active_do'],
                          row['is_active_posle']), ('is_active', '1', '0'))

    def test_name_and_outline_together(self):
        row = self.found['16']
        self.assertEqual((row['izmenenie'], row['imya_vnutri_novogo']),
                         ('imya+geometriya', 'net'))
        self.assertAlmostEqual(float(row['sdvig_tsentra_m']), 500.0, delta=2.0)

    def test_dji_rows_are_not_compared_but_counted(self):
        # строка DJI '1' изменилась, но в CSV её нет: '1' -- только как wialon
        self.assertNotIn('1', self.found)
        self.assertIn('dji       : 1 -> 1', self.stdout)
        self.assertIn('wialon    : 15 -> 15', self.stdout)

    def test_the_difference_sign_and_the_point_counts(self):
        self.assertAlmostEqual(float(self.found['9']['raznitsa_ga']), 3.0,
                               delta=0.02)
        self.assertEqual((self.found['8']['tochek_do'],
                          self.found['8']['tochek_posle']), ('4', '4'))

    def test_the_csv_carries_a_bom_for_excel(self):
        with open(self.out, 'rb') as handle:
            self.assertEqual(handle.read(3), b'\xef\xbb\xbf')

    def test_the_console_counts(self):
        expected = [
            'wialon rows before: 15 | after: 15',
            '  only after (added)       : 1',
            '  only before (gone)       : 1',
            '  unchanged                : 1',
            '  changed                  : 13',
            'name changed               : 3',
            '  old name inside the new  : 1',
            '  whitespace only          : 1',
            'geometry changed           : 10',
            '  same coordinates, other text         : 1',
            '  same ring, other start or direction  : 2',
            '  same points, other order             : 1',
            '  other points                         : 3',
            '    under 0.1%    : 2',
            '    10% and more  : 1',
            '    10 - 100 m    : 2',
            '    100 m and more: 1',
            'nothing was written to either database: both were opened mode=ro',
        ]
        for line in expected:
            self.assertIn(line, self.stdout.splitlines(), line)

    def test_the_console_is_ascii_and_carries_no_names(self):
        self.stdout.encode('ascii')
        for name in NAMES:
            for word in name.split():
                if len(word) > 2 and word not in ('|',):
                    self.assertNotIn(word, self.stdout, name)
                    self.assertNotIn(config.ascii_only(word), self.stdout, name)


# кольцо на сетке 1e-7 градуса: сдвиг меньше шага сетки не уводит точку
GRID = [[64.4, 40.0], [64.401, 40.0], [64.401, 40.001], [64.4, 40.001],
        [64.4, 40.0]]


def kind(before, after):
    return gd.compare_geometry(text(before), text(after))[0]


def one(before, after):
    return gd.compare({'k': before}, {'k': after})


class Arithmetic(unittest.TestCase):
    def test_float_noise_is_the_same_point(self):
        noisy = [[lon + 1e-12, lat - 1e-12] for lon, lat in BASE]
        self.assertEqual(kind(BASE, noisy), 'te_zhe_koordinaty')

    def test_three_millimetres_are_the_same_point(self):
        # 3e-8 градуса -- ~3 мм: та же точка на сетке 1e-7
        self.assertEqual(kind(GRID, [[x + 3e-8, y] for x, y in GRID]),
                         'te_zhe_koordinaty')

    def test_one_centimetre_is_another_point(self):
        # 1e-7 градуса -- ~1 см: шаг сетки, уже другая точка
        self.assertEqual(kind(GRID, [[x + 1e-7, y] for x, y in GRID]),
                         'drugie_tochki')

    def test_a_repeated_vertex_is_not_the_same_points(self):
        a, b, c, d = GRID[:4]
        self.assertEqual(kind([a, b, c, d, c, a], [a, b, c, d, d, a]),
                         'drugie_tochki')

    def test_metres_do_not_come_from_the_tool_constant(self):
        # [REASON]: квадраты фикстуры строятся из gd.EARTH_RADIUS_M, и неверный
        # радиус там сократился бы. Здесь числа записаны независимо:
        # 0,001 градуса у 40 с. ш. -- 111,195 м на 85,18 м.
        ring = [tuple(point) for point in GRID[:-1]]
        self.assertAlmostEqual(gd.area_ha(ring, 40.0005), 0.9472, delta=0.002)
        north = [[x, y + 0.001] for x, y in GRID]
        self.assertAlmostEqual(gd.compare_geometry(text(GRID), text(north))[3],
                               111.19, delta=0.2)

    def test_points_added_on_one_side_do_not_move_the_centre(self):
        # [REASON]: среднее вершин сдвинулось бы на 125 м, центр площади -- нет
        sq = square(side_m=300.0)
        a, b, c, d = sq[:4]
        east = [[b[0], b[1] + (c[1] - b[1]) * k / 21.0] for k in range(1, 21)]
        moved = gd.compare_geometry(text(sq), text([a, b] + east + [c, d, a]))[3]
        self.assertLess(moved, 0.5)

    def test_a_ring_with_or_without_the_closing_point(self):
        self.assertEqual(gd.compare_geometry(text(BASE), text(BASE[:-1]))[0],
                         'te_zhe_koordinaty')

    def test_a_square_hectare(self):
        ring = [tuple(point) for point in square()[:-1]]
        self.assertAlmostEqual(gd.area_ha(ring, LAT0), 1.0, delta=0.001)

    def test_no_outline_on_either_side_is_unchanged(self):
        # 39 линий Wialon лежат в зеркале без геометрии
        rows, summary, _, _, _ = one(('Круг', None, 1), ('Круг', None, 1))
        self.assertEqual((rows, summary['bez_izmeneniy']), ([], 1))

    def test_the_name_counters_are_counted_apart(self):
        _, summary, _, _, _ = one(('X', None, 1), ('X Y', None, 1))
        self.assertEqual((summary['imya_vnutri_novogo'],
                          summary['imya_tolko_probely']), (1, 0))

    def test_a_case_only_rename_is_not_whitespace(self):
        self.assertEqual(gd.compare_names('поле 3', 'Поле 3'),
                         (True, False, False))

    def test_a_cycle_needs_the_same_length(self):
        first = [(1, 1), (2, 2), (3, 3)]
        self.assertTrue(gd.same_cycle(first, [(2, 2), (3, 3), (1, 1)]))
        self.assertTrue(gd.same_cycle(first, [(3, 3), (2, 2), (1, 1)]))
        self.assertFalse(gd.same_cycle(first, [(1, 1), (2, 2), (3, 3), (1, 1)]))
        self.assertFalse(gd.same_cycle(first, [(1, 1), (3, 3), (4, 4)]))



class Buckets(unittest.TestCase):
    def area_bucket(self, side_after, side_before=100.0):
        _, _, _, area, _ = one(('n', text(square(side_m=side_before)), 1),
                               ('n', text(square(side_m=side_after)), 1))
        return dict(area)

    def test_area_buckets(self):
        self.assertEqual(self.area_bucket(100.3), {'0.1% - 1%': 1})
        # +10,7 % от прежней площади, -9,7 % от новой
        self.assertEqual(self.area_bucket(105.2), {'10% and more': 1})
        # контур уменьшился -- изменение тоже большое
        self.assertEqual(self.area_bucket(50.0), {'10% and more': 1})

    def test_shift_buckets(self):
        _, _, _, _, shift = one(('n', text(BASE), 1),
                                ('n', text(square(east_m=5)), 1))
        self.assertEqual(dict(shift), {'1 - 10 m': 1})

    def test_bucket_limits(self):
        self.assertEqual(gd._bucket(1.0, gd.SHIFT_BUCKETS), '1 - 10 m')
        self.assertEqual(gd._bucket(0.001, gd.AREA_BUCKETS), '0.1% - 1%')

    def test_a_flat_old_ring_is_the_largest_change(self):
        a, b = GRID[0], GRID[1]
        mid = [(a[0] + b[0]) / 2, a[1]]
        _, _, _, area, _ = one(('n', text([a, mid, b, a]), 1),
                               ('n', text(GRID), 1))
        self.assertEqual(dict(area), {'10% and more': 1})


if __name__ == '__main__':
    unittest.main()
