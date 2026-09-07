# -*- coding: utf-8 -*-
"""GPS-LABEL-014: ответы с экрана «Факт по технике» доходят до судьи без потерь.

Что проверяется и почему именно это:

1. **Судья читает выгрузку своими же функциями.** Файлы проверяются не на вид,
   а `read_answers` / `read_features` из `tools/gps_label_evaluate.py` и его
   `judge()`: формат, который совпадает только по описанию, судья молча
   выбросит строками «no matching site».

2. **В партию входят только ответы человека.** Участок без ответа и участок со
   снятым ответом -- не метки.

3. **Имя машины -- из связки, иначе голый id.** Чужое имя не подставляется;
   строка «не наша» связкой не считается.

4. **База не меняется.** Соединение только для чтения -- проверено байтами.

Запуск:
  python -m unittest tests.test_gps_label_export -v
"""
import io
import os
import sqlite3
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

import migration_utils                                             # noqa: E402
import migrate_gps_daily_001 as gps_mig                            # noqa: E402
import tools.gps_label_evaluate as judge                           # noqa: E402
import tools.gps_label_export as export                            # noqa: E402

FIELD_CONTOURS_DDL = (
    'CREATE TABLE field_contours (id INTEGER PRIMARY KEY, source TEXT, '
    'external_id TEXT, name TEXT, geometry_geojson TEXT, area_ha REAL, '
    'is_active BOOLEAN DEFAULT 1)')
MAPPING_DDL = (
    'CREATE TABLE vialon_mappings (id INTEGER PRIMARY KEY AUTOINCREMENT, '
    'vialon_name VARCHAR(300) NOT NULL UNIQUE, wialon_id INTEGER, '
    'equipment_id INTEGER, skip BOOLEAN)',
    'CREATE TABLE equipment (id INTEGER PRIMARY KEY AUTOINCREMENT, '
    'name VARCHAR(200) NOT NULL, plate VARCHAR(50))',
)
SQUARE = ('{"type": "Polygon", "coordinates": [[[64.55, 39.99], [64.56, 39.99], '
          '[64.56, 40.0], [64.55, 40.0], [64.55, 39.99]]]}')


class Base(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.mkdtemp()
        self.db = os.path.join(self.folder, 'transport.db')
        con = sqlite3.connect(self.db)
        con.execute(FIELD_CONTOURS_DDL)
        for ddl in MAPPING_DDL:
            con.execute(ddl)
        con.commit()
        con.close()
        self._saved = (gps_mig.DB_PATH, migration_utils.DB_PATH)
        gps_mig.DB_PATH = self.db
        migration_utils.DB_PATH = self.db
        gps_mig.run()
        self.answers = os.path.join(self.folder, 'otvety.txt')
        self.features = os.path.join(self.folder, 'gps_label.csv')

    def tearDown(self):
        gps_mig.DB_PATH, migration_utils.DB_PATH = self._saved

    def machine(self, wialon_id, name, skip=0):
        con = sqlite3.connect(self.db)
        try:
            cur = con.execute('INSERT INTO equipment (name, plate) VALUES (?, ?)',
                              (name, ''))
            con.execute('INSERT INTO vialon_mappings (vialon_name, wialon_id, '
                        'equipment_id, skip) VALUES (?, ?, ?, ?)',
                        ('obj %d' % wialon_id, wialon_id, cur.lastrowid, skip))
            con.commit()
        finally:
            con.close()

    def site(self, day, wialon_id, number, area_ha, minutes, label=None,
             suggested='работа'):
        con = sqlite3.connect(self.db)
        try:
            con.execute(
                'INSERT INTO gps_work_polygons (work_date, wialon_id, site_number, '
                'area_ha, minutes, polygon_geojson, contour_id, alpha_used_m, '
                'pass_spacing_m, quality_flag, suggested_label, operator_label, '
                'decided_at) VALUES (?, ?, ?, ?, ?, ?, NULL, 10.0, 7.5, NULL, ?, ?, ?)',
                (day, wialon_id, number, area_ha, minutes, SQUARE, suggested,
                 label, '2026-09-07 12:00' if label else None))
            con.commit()
        finally:
            con.close()

    def run_tool(self, *extra):
        out, saved = io.StringIO(), sys.stdout
        sys.stdout = out
        try:
            code = export.main(['--db', self.db, '--answers-out', self.answers,
                                '--features-out', self.features] + list(extra))
        finally:
            sys.stdout = saved
        return code, out.getvalue()


class ThroughTheReferee(Base):
    """Пункты 1 и 2."""

    def test_the_referee_reads_the_export_with_its_own_functions(self):
        self.machine(3464, 'МТЗ 873 GA')
        self.site('2026-09-01', 3464, 1, 8.772, 317.4, label='работа')
        self.site('2026-09-01', 3464, 2, 0.41, 6.0, label='проезд')
        self.site('2026-09-01', 3464, 3, 0.55, 9.0)            # без ответа
        code, log = self.run_tool()
        self.assertEqual(code, 0, log)

        answers = judge.read_answers(self.answers)
        features = judge.read_features(self.features)
        self.assertEqual(len(answers), 2)
        self.assertEqual(set(answers), set(features))
        site_ids = sorted(answers)
        self.assertEqual(site_ids, ['МТЗ 873 GA|2026-09-01|#1|8.77 га',
                                    'МТЗ 873 GA|2026-09-01|#2|0.41 га'])
        self.assertEqual(answers[site_ids[0]], 'работа')
        self.assertEqual(answers[site_ids[1]], 'проезд')
        self.assertAlmostEqual(features[site_ids[0]]['area_ha'], 8.772)
        self.assertAlmostEqual(features[site_ids[0]]['minutes'], 317.4)
        self.assertEqual(features[site_ids[0]]['machine'], 'МТЗ 873 GA')
        self.assertEqual(features[site_ids[0]]['day'], '2026-09-01')

        # и сам судья принимает партию как невиданную, не споткнувшись
        rows = [dict(features[site_id], label=label)
                for site_id, label in answers.items()]
        out, saved = io.StringIO(), sys.stdout
        sys.stdout = out
        try:
            judge.judge(rows, unseen=True)
        finally:
            sys.stdout = saved
        self.assertIn('UNSEEN BATCH: 2 sites', out.getvalue())

    def test_a_cleared_answer_is_not_a_label(self):
        self.site('2026-09-01', 3464, 1, 2.0, 30.0, label='работа')
        con = sqlite3.connect(self.db)
        try:
            con.execute('UPDATE gps_work_polygons SET operator_label = NULL, '
                        'decided_at = NULL')
            con.commit()
        finally:
            con.close()
        code, log = self.run_tool()
        self.assertEqual(code, 0, log)
        self.assertEqual(judge.read_answers(self.answers), {})
        self.assertIn('labelled sites: 0', log)
        self.assertIn('nothing labelled yet', log)

    def test_the_window_is_inclusive_on_both_ends(self):
        for day in ('2026-08-19', '2026-08-20', '2026-09-06', '2026-09-07'):
            self.site(day, 3464, 1, 2.0, 30.0, label='работа')
        code, log = self.run_tool('--from', '2026-08-20', '--to', '2026-09-06')
        self.assertEqual(code, 0, log)
        days = sorted(row['day'] for row in judge.read_features(self.features).values())
        self.assertEqual(days, ['2026-08-20', '2026-09-06'])


class Names(Base):
    """Пункт 3."""

    def test_the_name_comes_from_the_link_and_falls_back_to_the_id(self):
        self.machine(3464, 'МТЗ 873 GA')
        self.machine(5555, 'Чужая', skip=1)
        self.site('2026-09-01', 3464, 1, 2.0, 30.0, label='работа')
        self.site('2026-09-01', 5555, 1, 2.0, 30.0, label='работа')
        self.site('2026-09-01', 7777, 1, 2.0, 30.0, label='проезд')
        code, log = self.run_tool()
        self.assertEqual(code, 0, log)
        machines = sorted(row['machine'] for row in judge.read_features(self.features).values())
        self.assertEqual(machines, ['5555', '7777', 'МТЗ 873 GA'])
        self.assertTrue(log.isascii(), log)

    def test_without_the_mapping_tables_the_id_is_the_name(self):
        con = sqlite3.connect(self.db)
        try:
            con.execute('DROP TABLE vialon_mappings')
            con.execute('DROP TABLE equipment')
            con.commit()
        finally:
            con.close()
        self.site('2026-09-01', 3464, 1, 2.0, 30.0, label='работа')
        code, log = self.run_tool()
        self.assertEqual(code, 0, log)
        self.assertEqual([row['machine'] for row in
                          judge.read_features(self.features).values()], ['3464'])


class Counting(Base):
    def test_the_counter_measures_the_distance_to_the_criterion(self):
        self.machine(3464, 'МТЗ 873 GA')
        for number in range(1, 4):
            self.site('2026-09-01', 3464, number, 2.0, 30.0, label='работа')
        self.site('2026-09-01', 3464, 4, 0.4, 5.0, label='проезд')
        code, log = self.run_tool()
        self.assertEqual(code, 0, log)
        self.assertIn('labelled sites: 4 | work: 3 | passage: 1', log)
        self.assertIn('batch size: 4 of 40 short', log)
        self.assertIn('transits:   1 of 15 short', log)
        self.assertIn('machine-days: 1', log)


class ReadOnly(Base):
    """Пункт 4."""

    def test_the_database_is_not_touched(self):
        self.site('2026-09-01', 3464, 1, 2.0, 30.0, label='работа')
        with open(self.db, 'rb') as fh:
            before = fh.read()
        code, log = self.run_tool()
        self.assertEqual(code, 0, log)
        with open(self.db, 'rb') as fh:
            self.assertEqual(fh.read(), before)

    def test_the_connection_cannot_write(self):
        con = export.open_read_only(self.db)
        try:
            with self.assertRaises(sqlite3.OperationalError):
                con.execute('DELETE FROM gps_work_polygons')
        finally:
            con.close()

    def test_a_missing_database_is_refused(self):
        saved, sys.stderr = sys.stderr, io.StringIO()
        try:
            code = export.main(['--db', os.path.join(self.folder, 'nope.db')])
        finally:
            sys.stderr = saved
        self.assertEqual(code, 2)
        self.assertFalse(os.path.exists(os.path.join(self.folder, 'nope.db')))

    def test_a_database_without_the_table_is_refused(self):
        bare = os.path.join(self.folder, 'bare.db')
        sqlite3.connect(bare).close()
        saved, sys.stderr = sys.stderr, io.StringIO()
        try:
            code = export.main(['--db', bare])
            problem = sys.stderr.getvalue()
        finally:
            sys.stderr = saved
        self.assertEqual(code, 2)
        self.assertIn('migrate_gps_daily_001', problem)


if __name__ == '__main__':
    unittest.main()
