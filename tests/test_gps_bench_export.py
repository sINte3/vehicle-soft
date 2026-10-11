# -*- coding: utf-8 -*-
"""tools/gps_bench_export.py -- точки суток и контуры для стенда сессии.

Что проверяется и почему именно это:

1. **Точки -- в формате выгрузок зондов**, местные дата и время. Стенд
   читает их тем же разбором, что наборы 27.07 и 12.08; сутки, разрезанные по
   UTC, дали бы стенду чужие сутки.
2. **Набор приёмки задаёт сутки сам** (столбцы day и wialon_id) -- и
   настоящий файл В-5 в репозитории читается.
3. **Контуры -- только зеркала Wialon**, с номером зоны и именем; недостающие
   названы.
4. **Сутки без точек на диске названы, код 3**, остальное записано.
5. **Только чтение**: база и файлы точек побайтно те же, каждое соединение
   `mode=ro`; неверный ввод -- код 2, и ничего не записано.

Запуск:
  python -m unittest tests.test_gps_bench_export -v
"""
import contextlib
import csv
import hashlib
import io
import json
import os
import shutil
import sqlite3
import tempfile
import unittest
import unittest.mock
from datetime import datetime

import migrate_core_foundation_001 as foundation
import migrate_gps_daily_001 as daily_migration
import tools.gps_bench_export as export
from gps_collector import config as collector_config
from gps_collector import storage

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
V5_SET = os.path.join(REPO, 'docs', 'gps_v5_acceptance_set_2026-10-09.csv')
SQUARE = {'type': 'Polygon',
          'coordinates': [[[64.5, 40.0], [64.501, 40.0], [64.501, 40.001],
                           [64.5, 40.001], [64.5, 40.0]]]}


def local(day, clock):
    return int(datetime.strptime('%s %s' % (day, clock), '%Y-%m-%d %H:%M:%S')
               .replace(tzinfo=collector_config.TZ).timestamp())


def run(*argv):
    with contextlib.redirect_stdout(io.StringIO()) as out, \
            contextlib.redirect_stderr(io.StringIO()) as err:
        code = export.main([str(arg) for arg in argv])
    return code, out.getvalue(), err.getvalue()


def digest(path):
    with open(path, 'rb') as handle:
        return hashlib.sha256(handle.read()).hexdigest()


class World(unittest.TestCase):

    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root, True)
        self.folder = os.path.join(self.root, 'points')
        os.mkdir(self.folder)
        self.db = os.path.join(self.root, 'transport.db')
        con = sqlite3.connect(self.db)
        try:
            con.execute(foundation.CREATE_FIELD_CONTOURS)
            con.execute(daily_migration.CREATE_WORK_POLYGONS)
            for day, unit, site, label in (('2026-09-16', 3533, 1, 'работа'),
                                           ('2026-09-16', 3533, 2, None),
                                           ('2026-09-02', 1729, 2, 'проезд')):
                con.execute('INSERT INTO gps_work_polygons (work_date, wialon_id, '
                            'site_number, area_ha, minutes, polygon_geojson, '
                            'operator_label) VALUES (?, ?, ?, 1.25, 30, ?, ?)',
                            (day, unit, site, json.dumps(SQUARE), label))
            for source, external, name in (('wialon', '3208', '1508 Нурхон Бобохон'),
                                           ('wialon', '7694', '1580 Маликова'),
                                           ('drones', '3208', 'чужой источник')):
                con.execute('INSERT INTO field_contours (source, external_id, name, '
                            'geometry_geojson) VALUES (?, ?, ?, ?)',
                            (source, external, name, json.dumps(SQUARE)))
            con.commit()
        finally:
            con.close()
        # two machine-days; the second one starts just after local midnight
        storage.write_points(self.folder, [
            (3533, local('2026-09-16', '08:00:00'), 64.55, 40.01, 7.5, None, 11),
            (3533, local('2026-09-16', '08:00:30'), 64.5502, 40.0101, 8.0, None, 11),
            (1729, local('2026-09-02', '00:00:05'), 64.6, 40.02, 0.0, None, 9)])
        self.out = os.path.join(self.root, 'out')
        os.mkdir(self.out)

    def rows(self, path):
        with open(path, encoding='utf-8', newline='') as handle:
            return list(csv.DictReader(handle, delimiter=';'))


class Points(World):

    def test_points_are_written_with_the_local_clock(self):
        path = os.path.join(self.out, 'p.csv')
        code, out, err = run('--db', self.db, '--dir', self.folder, '--day',
                             '2026-09-16:3533', '--day', '2026-09-02:1729',
                             '--out-points', path)
        self.assertEqual(code, 0, out + err)
        with open(path, encoding='utf-8') as handle:
            self.assertEqual(handle.readline(), 'unit_id;date;time;lat;lon;speed\n')
        rows = self.rows(path)
        self.assertEqual([(r['unit_id'], r['date'], r['time']) for r in rows],
                         [('3533', '2026-09-16', '08:00:00'),
                          ('3533', '2026-09-16', '08:00:30'),
                          ('1729', '2026-09-02', '00:00:05')])
        self.assertEqual((rows[1]['lat'], rows[1]['lon'], rows[1]['speed']),
                         ('40.0101000', '64.5502000', '8'))
        self.assertIn('2026-09-16 unit 3533: 2 points', out)
        out.encode('ascii')

    def test_the_set_gives_the_days(self):
        path = os.path.join(self.out, 'set.csv')
        with open(path, 'w', encoding='utf-8') as handle:
            handle.write('n,day,wialon_id\n1,2026-09-16,3533\n2,2026-09-02,1729\n')
        self.assertEqual(export.set_days(path), [('2026-09-16', 3533),
                                                 ('2026-09-02', 1729)])
        pairs = export.set_days(V5_SET)
        self.assertEqual(len(pairs), 12)
        self.assertEqual(pairs[0], ('2026-09-16', 3533))
        self.assertIn(('2026-09-04', 942), pairs)

    def test_a_day_without_points_is_named_and_the_rest_written(self):
        path = os.path.join(self.out, 'p.csv')
        code, out, _err = run('--db', self.db, '--dir', self.folder, '--day',
                              '2026-09-16:3533', '--day', '2026-08-01:999',
                              '--out-points', path)
        self.assertEqual(code, 3, out)
        self.assertIn('2026-08-01 unit 999: no points on disk', out)
        self.assertEqual(len(self.rows(path)), 2)


class Zones(World):

    def test_only_wialon_contours_with_number_and_name(self):
        path = os.path.join(self.out, 'z.geojson')
        code, out, err = run('--db', self.db, '--zone', '3208', '--zone', '7694',
                             '--zone', '1', '--out-zones', path)
        self.assertEqual(code, 0, out + err)
        with open(path, encoding='utf-8') as handle:
            data = json.load(handle)
        self.assertEqual(data['type'], 'FeatureCollection')
        self.assertEqual([(f['properties']['zone_id'], f['properties']['name'])
                          for f in data['features']],
                         [('3208', '1508 Нурхон Бобохон'), ('7694', '1580 Маликова')])
        self.assertEqual(data['features'][0]['geometry'], SQUARE)
        self.assertIn('zones written 2, missing 1: 1', out)


class Labels(World):

    def test_labelled_sites_and_their_days(self):
        labels = os.path.join(self.out, 'l.csv')
        points = os.path.join(self.out, 'p.csv')
        code, out, err = run('--db', self.db, '--dir', self.folder, '--labelled',
                             '--out-labels', labels, '--out-points', points)
        self.assertEqual(code, 0, out + err)
        with open(labels, encoding='utf-8', newline='') as handle:
            rows = list(csv.DictReader(handle))
        self.assertEqual([(r['day'], r['wialon_id'], r['site_number'], r['label'])
                          for r in rows],
                         [('2026-09-02', '1729', '2', 'проезд'),
                          ('2026-09-16', '3533', '1', 'работа')])
        self.assertEqual(json.loads(rows[0]['polygon_geojson']), SQUARE)
        self.assertEqual(sorted({(r['unit_id'], r['date']) for r in self.rows(points)}),
                         [('1729', '2026-09-02'), ('3533', '2026-09-16')])
        self.assertIn('labelled sites written 2', out)

    def test_labelled_needs_both_files(self):
        code, _out, err = run('--db', self.db, '--dir', self.folder, '--labelled',
                              '--out-points', os.path.join(self.out, 'p.csv'))
        self.assertEqual(code, 2)
        self.assertIn('--labelled needs --out-labels and --out-points', err)
        self.assertEqual(os.listdir(self.out), [])


class ReadOnly(World):

    def test_nothing_changes_and_every_connection_is_read_only(self):
        files = [self.db] + [os.path.join(self.folder, name)
                             for name in sorted(os.listdir(self.folder))]
        before = [digest(path) for path in files]
        real, seen = sqlite3.connect, []

        def watched(target, *args, **kwargs):
            seen.append((str(target), kwargs.get('uri')))
            return real(target, *args, **kwargs)
        with unittest.mock.patch.object(sqlite3, 'connect', watched):
            code, out, _err = run('--db', self.db, '--dir', self.folder, '--day',
                                  '2026-09-16:3533', '--zone', '3208',
                                  '--out-points', os.path.join(self.out, 'p.csv'),
                                  '--out-zones', os.path.join(self.out, 'z.json'))
        self.assertEqual(code, 0, out)
        self.assertEqual([digest(path) for path in files], before)
        self.assertGreaterEqual(len(seen), 2)
        for target, uri in seen:
            self.assertTrue(target.startswith('file:') and target.endswith('?mode=ro')
                            and uri, target)

    def test_bad_input_writes_nothing(self):
        missing_db = os.path.join(self.root, 'no.db')
        no_folder = os.path.join(self.root, 'nowhere', 'p.csv')
        bad_set = os.path.join(self.out, 'bad.csv')
        with open(bad_set, 'w', encoding='utf-8') as handle:
            handle.write('n,day\n1,2026-09-16\n')
        p = os.path.join(self.out, 'p.csv')
        cases = [
            (('--db', missing_db, '--dir', self.folder, '--day', '2026-09-16:3533',
              '--out-points', p), 'database not found'),
            (('--db', self.db, '--dir', self.folder, '--day', '2026-09-16',
              '--out-points', p), '--day must look like'),
            (('--db', self.db, '--dir', self.folder, '--set', bad_set,
              '--out-points', p), 'has no column(s) wialon_id'),
            (('--db', self.db, '--dir', self.folder, '--day', '2026-09-16:3533',
              '--out-points', no_folder), 'does not exist'),
            (('--db', self.db, '--dir', self.folder), 'nothing to export'),
            (('--db', self.db, '--zone', '3208'), '--zone needs --out-zones'),
            (('--db', self.db, '--day', '2026-09-16:3533', '--out-points', p),
             'need --dir'),
            (('--db', self.db, '--dir', self.folder, '--day', '2026-09-16:3533'),
             'need --out-points'),
        ]
        for argv, word in cases:
            code, out, err = run(*argv)
            self.assertEqual(code, 2, (argv, out, err))
            self.assertIn(word, err, argv)
        self.assertEqual(sorted(os.listdir(self.out)), ['bad.csv'])
        self.assertFalse(os.path.exists(missing_db))


if __name__ == '__main__':
    unittest.main()
