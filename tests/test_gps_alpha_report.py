# -*- coding: utf-8 -*-
"""tools/gps_alpha_report.py -- допуск сшивания участков по машино-суткам.

[REASON]: 02.10.2026 владелец отверг ширину агрегата, и невозможные контуры
надо объяснить без неё. Гипотеза -- допуск метода (альфа), раздутый шагом
между дорогами. Отчёт кладёт гектары опубликованных суток по корзинам допуска
относительно самого широкого допуска на работе с ручным замером (44,64 м) и
показывает, в каких сутках операторы ответили «работа» и «проезд».

Здесь: корзины на самых границах, только опубликованные сутки и только
период, исключённые объекты -- отдельно и не в списке сутки, сутки с
разногласием допуска между участками, ответы операторов, поиск по имени и
номеру, разбор суток, ASCII, только чтение -- и то, что константы те же, что
у метода и у таблицы набора 27.07.
"""
import contextlib
import hashlib
import io
import os
import re
import shutil
import sqlite3
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

import tools.gps_alpha_report as gar                                # noqa: E402
from tests.test_gps_units_inventory import DDL                      # noqa: E402

TRACTOR, HOLLAND, NO_MACHINE, NO_MAPPING, ALIEN, TRUCK = (
    387, 393, 7001, 8854, 5001, 319)
WORK, PASSAGE = 'работа', 'проезд'


class World(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.folder, True)
        self.db = os.path.join(self.folder, 'transport.db')
        con = sqlite3.connect(self.db)
        try:
            for statement in DDL:
                con.execute(statement)
            con.commit()
        finally:
            con.close()
        tractor = self.equipment('МТЗ-80.1', 'mtz', '80 239 NA')
        holland = self.equipment('New Holland 7060', 'tractor', '80 080 HA')
        truck = self.equipment('Isuzu', 'special', '80 258 JAA')
        car = self.equipment('Damas', 'passenger', '80 574 GCA')
        self.mapping('МТЗ 239', TRACTOR, tractor)
        self.mapping('NH 7060', HOLLAND, holland)
        self.mapping('Камаз 7001', NO_MACHINE, None)
        self.mapping('Isuzu 258', TRUCK, truck)
        self.mapping('Damas 574', ALIEN, car, skip=1)
        # NO_MAPPING строки нет вовсе
        # (дата, объект, допуск, шаг, [(га, минуты, предложено, ответ)])
        self.day('2026-09-28', TRACTOR, 10.0, 7.9, [
            (8.3287, 649.2, WORK, WORK), (0.7313, 13.8, PASSAGE, PASSAGE)])
        self.day('2026-09-27', TRACTOR, 20.0, 16.667, [
            (5.0, 300.0, WORK, WORK)])                       # ровно 20
        self.day('2026-09-26', HOLLAND, 44.64, 37.2, [
            (2.0, 90.0, WORK, None)])                        # ровно 44,64
        self.day('2026-09-25', HOLLAND, 44.65, 37.208, [
            (30.0, 40.0, WORK, PASSAGE)])                    # чуть выше
        self.day('2026-09-24', NO_MACHINE, 300.0, 250.0, [
            (100.0, 60.0, WORK, None)])                      # ровно 300
        self.day('2026-09-23', NO_MAPPING, 300.1, 250.08, [
            (200.0, 30.0, WORK, None), (50.0, 10.0, WORK, None)])
        self.day('2026-09-22', ALIEN, 900.0, 750.0, [
            (400.0, 20.0, WORK, None)])                      # «не наша»
        self.day('2026-09-21', TRACTOR, None, None, [
            (1.0, 30.0, WORK, None)])                        # допуска нет
        self.day('2026-09-20', TRACTOR, (12.0, 15.0), (10.0, 12.5), [
            (1.5, 60.0, WORK, None), (2.5, 70.0, WORK, None)])  # разногласие
        # неопубликованные сутки, спецтехника и сутки вне периода не читаются
        self.day('2026-09-19', TRACTOR, 999.0, 830.0, [
            (700.0, 5.0, WORK, None)], reason='sbor_nepolnyy')
        self.day('2026-09-28', TRUCK, 999.0, 830.0, [
            (230.23, 20.0, WORK, None)], reason='spetstekhnika')
        self.day('2026-08-31', TRACTOR, 999.0, 830.0, [
            (500.0, 5.0, WORK, None)])
        self.day('2026-10-01', TRACTOR, 999.0, 830.0, [
            (500.0, 5.0, WORK, None)])

    def sql(self, statement, args=()):
        con = sqlite3.connect(self.db)
        try:
            cursor = con.execute(statement, args)
            con.commit()
            return cursor.lastrowid
        finally:
            con.close()

    def equipment(self, name, category, plate):
        return self.sql('INSERT INTO equipment (name, plate, category, '
                        'organization_id, is_active) VALUES (?, ?, ?, 1, 1)',
                        (name, plate, category))

    def mapping(self, name, wialon_id, equipment_id, skip=0):
        self.sql('INSERT INTO vialon_mappings (vialon_name, wialon_id, '
                 'equipment_id, skip) VALUES (?, ?, ?, ?)',
                 (name, wialon_id, equipment_id, skip))

    def day(self, work_date, unit, alpha, spacing, sites, reason=None):
        self.sql('INSERT INTO gps_daily_aggregates (work_date, wialon_id, '
                 'points_total, points_work, track_km, reason) '
                 'VALUES (?, ?, 100, 80, 12.5, ?)', (work_date, unit, reason))
        for number, (area, minutes, suggested, operator) in enumerate(sites, 1):
            site_alpha = alpha[number - 1] if isinstance(alpha, tuple) else alpha
            site_spacing = (spacing[number - 1] if isinstance(spacing, tuple)
                            else spacing)
            self.sql('INSERT INTO gps_work_polygons (work_date, wialon_id, '
                     'site_number, area_ha, minutes, polygon_geojson, '
                     'contour_id, alpha_used_m, pass_spacing_m, '
                     'suggested_label, operator_label) '
                     'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
                     (work_date, unit, number, area, minutes, '{}',
                      6573 if number == 1 else None, site_alpha, site_spacing,
                      suggested, operator))

    def read(self, since='2026-09-01', until='2026-09-30'):
        con = gar.open_readonly(self.db)
        try:
            days = gar.machine_days(con, since, until)
            kinds = gar.kinds_of(con, sorted({row['wialon_id'] for row in days}))
            return days, kinds
        finally:
            con.close()

    def run_main(self, *argv):
        with contextlib.redirect_stdout(io.StringIO()) as out, \
                contextlib.redirect_stderr(io.StringIO()) as err:
            code = gar.main(['--db', self.db] + list(argv))
        return code, out.getvalue(), err.getvalue()

    def digest(self, path):
        with open(path, 'rb') as handle:
            return hashlib.sha256(handle.read()).hexdigest()


def keys(days):
    return sorted((row['day'], row['wialon_id']) for row in days)


class Buckets(World):

    def test_the_edges_go_to_the_lower_bucket(self):
        labels = gar.bucket_labels()
        self.assertEqual(labels, ['10 (floor)', '10-20', '20-44.64',
                                  '44.64-100', '100-300', '>300', 'unknown'])
        cases = [(10.0, '10 (floor)'), (10.0004, '10 (floor)'),
                 (10.001, '10-20'), (20.0, '10-20'), (20.001, '20-44.64'),
                 (44.64, '20-44.64'), (44.65, '44.64-100'),
                 (100.0, '44.64-100'), (300.0, '100-300'), (300.1, '>300'),
                 (None, 'unknown')]
        for alpha, label in cases:
            with self.subTest(alpha=alpha):
                self.assertEqual(gar.bucket_of(alpha), label)

    def test_only_published_days_of_the_period(self):
        days, _kinds = self.read()
        self.assertEqual(keys(days), [
            ('2026-09-20', TRACTOR), ('2026-09-21', TRACTOR),
            ('2026-09-22', ALIEN), ('2026-09-23', NO_MAPPING),
            ('2026-09-24', NO_MACHINE), ('2026-09-25', HOLLAND),
            ('2026-09-26', HOLLAND), ('2026-09-27', TRACTOR),
            ('2026-09-28', TRACTOR)])

    def test_the_table_by_bucket(self):
        days, kinds = self.read()
        table = gar.summarise(days, kinds)
        expect = {  # days, sites, ha, field_ha, work, passage, hid_days, hid_ha
            '10 (floor)': (1, 2, 9.06, 9.06, 1, 1, 0, 0.0),
            '10-20': (2, 3, 9.0, 9.0, 1, 0, 0, 0.0),
            '20-44.64': (1, 1, 2.0, 2.0, 0, 0, 0, 0.0),
            '44.64-100': (1, 1, 30.0, 30.0, 0, 1, 0, 0.0),
            '100-300': (1, 1, 100.0, 0.0, 0, 0, 0, 0.0),
            '>300': (1, 2, 250.0, 0.0, 0, 0, 1, 400.0),
            'unknown': (1, 1, 1.0, 1.0, 0, 0, 0, 0.0),
        }
        for label, (n_days, sites, area, field, work, passage, hid_days,
                    hid_ha) in expect.items():
            cell = table[label]
            with self.subTest(bucket=label):
                self.assertEqual(cell['days'], n_days)
                self.assertEqual(cell['sites'], sites)
                self.assertAlmostEqual(cell['ha'], area, places=4)
                self.assertAlmostEqual(cell['field_ha'], field, places=4)
                self.assertEqual(cell['work'], work)
                self.assertEqual(cell['passage'], passage)
                self.assertEqual(cell['hidden_days'], hid_days)
                self.assertAlmostEqual(cell['hidden_ha'], hid_ha, places=4)

    def test_a_day_whose_sites_disagree_is_named_and_takes_the_wider(self):
        days, _kinds = self.read()
        mixed = [(row['day'], row['wialon_id']) for row in days if row['mixed']]
        self.assertEqual(mixed, [('2026-09-20', TRACTOR)])
        row = [r for r in days if r['day'] == '2026-09-20'][0]
        self.assertEqual((row['alpha'], row['spacing']), (15.0, 12.5))

    def test_operator_answers_carry_the_alpha_of_their_day(self):
        days, kinds = self.read()
        work = gar.answers(days, kinds, 'work')
        passage = gar.answers(days, kinds, 'passage')
        self.assertEqual((work['sites'], work['median'], work['max'],
                          work['above']), (2, 15.0, 20.0, 0))
        self.assertEqual((passage['sites'], passage['max'], passage['above']),
                         (2, 44.65, 1))
        self.assertAlmostEqual(passage['median'], 27.325)


class Report(World):

    def test_the_report_prints_the_table_the_share_and_the_ranking(self):
        code, out, err = self.run_main('--since', '2026-09-01',
                                       '--until', '2026-09-30')
        self.assertEqual((code, err), (0, ''))
        self.assertIn('published machine-days with sites: 8 (11 sites, '
                      '401.06 ha); excluded objects, hidden on the screen: 1',
                      out)
        self.assertIn('machine-days whose sites disagree on alpha or '
                      'spacing: 1', out)
        self.assertRegex(out, r'\n>300\s+1\s+2\s+250\.00\s+62\.3\s+0\.00\s+0'
                              r'\s+0\s+1\s+400\.00\n')
        self.assertIn('hectares on machine-days with alpha above 44.64 m: '
                      '380.00 of 401.06 (94.7%)', out)
        self.assertIn('  work    : 2 sites, alpha median 15.00 m, max 20.00 m, '
                      'above 44.64 m: 0', out)
        self.assertIn('  passage : 2 sites, alpha median 27.32 m, max 44.65 m, '
                      'above 44.64 m: 1', out)
        ranking = out.split('top 8 counted machine-days by alpha:')[1]
        order = re.findall(r'\n(2026-\d\d-\d\d)\s+(\d+)\s', ranking)
        self.assertEqual(order[:8], [
            ('2026-09-23', str(NO_MAPPING)), ('2026-09-24', str(NO_MACHINE)),
            ('2026-09-25', str(HOLLAND)), ('2026-09-26', str(HOLLAND)),
            ('2026-09-27', str(TRACTOR)), ('2026-09-20', str(TRACTOR)),
            ('2026-09-28', str(TRACTOR)), ('2026-09-21', str(TRACTOR))])
        # «не наша» -- в столбцах скрытых, но не в списке
        self.assertNotIn('\n2026-09-22 ', ranking.split('\n\n')[0])
        self.assertIn('New Holland 7060 - 80 080 HA', out)
        out.encode('ascii')

    def test_find_by_name_ignores_case_and_spaces_or_takes_the_number(self):
        for needle in ('80 080', '80080ha', str(HOLLAND)):
            with self.subTest(needle=needle):
                code, out, _err = self.run_main('--since', '2026-09-01',
                                                '--find', needle)
                self.assertEqual(code, 0)
                self.assertIn('machine-day(s)', out)
                found = out.split('find ')[1]
                self.assertIn(': 2 machine-day(s)', found)
                self.assertIn('2026-09-25  wialon_id %d  field  alpha 44.65 m'
                              % HOLLAND, found)
                self.assertIn('2026-09-26  wialon_id %d' % HOLLAND, found)
        code, out, _err = self.run_main('--since', '2026-09-01',
                                        '--find', 'nothing like it')
        self.assertIn(": 0 machine-day(s)", out)

    def test_a_picked_day_lists_its_sites_with_the_answers(self):
        code, out, _err = self.run_main(
            '--since', '2026-09-01', '--day', '2026-09-28:%d' % TRACTOR,
            '--day', '2026-09-19:%d' % TRACTOR, '--day', '2026-09-15:%d' % TRACTOR)
        self.assertEqual(code, 0)
        picked = out.split('day 2026-09-28:%d' % TRACTOR)[1]
        self.assertIn('alpha 10.00 m  spacing 7.90 m  day published  '
                      'MTZ-80.1 - 80 239 NA', picked)
        self.assertRegex(picked, r'\n\s+1\s+8\.3287\s+649\.2\s+6573\s+10\.00'
                                 r'\s+7\.90\s+rabota\s+rabota\n')
        self.assertRegex(picked, r'\n\s+2\s+0\.7313\s+13\.8\s+-\s+10\.00'
                                 r'\s+7\.90\s+proezd\s+proezd\n')
        # неопубликованные сутки тоже разбираются -- с их причиной
        self.assertIn('day sbor_nepolnyy', out.split('day 2026-09-19')[1])
        self.assertIn('no daily row for this machine-day',
                      out.split('day 2026-09-15')[1])

    def test_bad_input_is_refused_before_the_database_is_opened(self):
        for argv in (('--since', '01.09.2026'),
                     ('--since', '2026-09-01', '--day', '2026-09-28'),
                     ('--since', '2026-09-01', '--day', 'x:393')):
            with self.subTest(argv=argv):
                code, _out, err = self.run_main(*argv)
                self.assertEqual(code, 2)
                self.assertIn('ERROR', err)

    def test_nothing_is_written_and_no_database_is_created(self):
        before = self.digest(self.db)
        code, out, _err = self.run_main('--since', '2026-09-01', '--find',
                                        '80 080', '--day', '2026-09-28:387')
        self.assertEqual(code, 0)
        self.assertIn('nothing was written', out)
        self.assertEqual(self.digest(self.db), before)
        missing = os.path.join(self.folder, 'absent.db')
        with contextlib.redirect_stdout(io.StringIO()), \
                contextlib.redirect_stderr(io.StringIO()):
            code = gar.main(['--db', missing, '--since', '2026-09-01'])
        self.assertEqual(code, 2)
        self.assertFalse(os.path.exists(missing))


class Constants(unittest.TestCase):

    def test_the_floor_and_the_factor_are_those_of_the_method(self):
        with open(os.path.join(REPO_ROOT, 'gps', 'area.py'),
                  encoding='utf-8') as fh:
            source = fh.read()
        floor = re.search(r'^ALPHA_M = ([\d.]+)$', source, re.M).group(1)
        factor = re.search(r'^ALPHA_SPACING_FACTOR = ([\d.]+)$', source,
                           re.M).group(1)
        self.assertEqual(float(floor), gar.ALPHA_FLOOR_M)
        self.assertEqual(float(factor), gar.ALPHA_SPACING_FACTOR)
        self.assertEqual(gar.VALIDATED_MAX_ALPHA_M, 44.64)

    def test_the_widest_validated_spacing_is_the_max_of_the_2707_table(self):
        with open(os.path.join(REPO_ROOT, 'docs',
                               'GPS_PLAN_FAKT_VISION_ROADMAP.md'),
                  encoding='utf-8') as fh:
            text = fh.read()
        header = '> | Контур | Вид | Шаг, м | Ручной, га | Расчёт, га | Расх., % |'
        block = text[text.index(header):].split('\n')[2:]
        spacings = []
        for line in block:
            if not line.startswith('> | '):
                break
            cells = [cell.strip() for cell in line[2:].strip('|').split('|')]
            spacings.append(float(cells[2].replace(',', '.')))
        self.assertEqual(len(spacings), 17)
        self.assertEqual(max(spacings), gar.VALIDATED_MAX_SPACING_M)


if __name__ == '__main__':
    unittest.main()
