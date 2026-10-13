# -*- coding: utf-8 -*-
"""tools/gps_multipass_scan.py -- признаки двойного прохода по суткам периода.

Окружение расчёта (геометрия). Держится то, что делает выгрузку пригодной
для разметки владельцем, а не просто списком чисел:
  * меряются ровно опубликованные сутки периода с точками на диске;
    исключённый владельцем объект не берётся, сутки без точек и сутки вне
    периода -- тоже, и каждая такая причина названа числом;
  * признаки в CSV -- те же, что у `gps.multipass.measure_day`: поле,
    пройденное дважды за сутки, и поле вдоль и поперёк выделяются, один
    проход -- нет;
  * контроль: участки совпали со строкой базы, строка подменена (MISMATCH),
    строку записала другая версия метода;
  * отбор: сильнейшие по признаку, случайные выше уровня, случайные ниже
    уровня; маленький участок не отбирается; одна машина -- не больше двух
    суток выше уровня; два направления без повторного хода -- тоже выше
    уровня;
  * разметка вслепую: в KML и шаблоне ответов нет ни чисел признаков, ни
    группы отбора, папки идут по sha1; группа и номер папки -- только в CSV;
  * в каждой папке -- участки и трек четырьмя цветами по времени суток;
    номера папок и строк шаблона совпадают;
  * ни база, ни файлы точек не меняются, пишутся только названные файлы;
    неверный ввод отказывается кодом 2 до чтения; Flask и `models` не
    импортируются.

Запуск из корня репозитория окружением расчёта:
  python -m unittest gps.tests.test_multipass_scan
"""
import contextlib
import csv
import hashlib
import io
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
import unittest.mock
import xml.etree.ElementTree as ET

import migrate_gps_daily_001 as migration
import tools.gps_multipass_scan as scan
from gps.area import PREVIOUS_METHOD_VERSION
from gps.daily import compute_day, write_day
from gps.tests.test_edge_replay import stamped
from gps.tests.test_multipass import (HOUR, drifting_noise, last_time,
                                      picker_field, shuttle)
from gps_collector import storage
from tests.test_gps_units_inventory import DDL

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
NS = {'k': 'http://www.opengis.net/kml/2.2'}

(ONE, TWICE, CROSS, ALIEN, NO_POINTS, SMALL, TAMPERED, OLD, LATE, CALM1, CALM2,
 SPARSE, REPEATER, TWO_FIELDS) = range(701, 715)
SINCE, UNTIL = '2026-10-01', '2026-10-05'
ABOVE = {('2026-10-02', TWICE), ('2026-10-03', CROSS), ('2026-10-04', SPARSE),
         ('2026-10-01', REPEATER), ('2026-10-02', REPEATER),
         ('2026-10-03', REPEATER)}
BELOW = {('2026-10-01', ONE), ('2026-10-04', TAMPERED), ('2026-10-05', OLD),
         ('2026-10-03', CALM1), ('2026-10-05', CALM2), ('2026-10-02', TWO_FIELDS)}


def twice_in_a_day(seed=7):
    first = picker_field()
    return first + picker_field(start=last_time(first) + 3 * HOUR,
                                noise=drifting_noise(1.0, seed=seed))


def along_and_across(spacing_m=3.5):
    along = shuttle(150.0, 150.0, spacing_m, along='north', speed_kmh=8.0)
    return along + shuttle(150.0, 150.0, spacing_m, along='east', speed_kmh=8.0,
                           start=last_time(along) + 600.0)


def two_fields():
    """Два поля в 850 м друг от друга, каждое -- один проход."""
    first = picker_field()
    second = [(t, lon + 0.01, lat, speed) for t, lon, lat, speed
              in picker_field(start=last_time(first) + HOUR)]
    return first + second


def small_twice():
    """0,6 га, пройдено дважды: ниже нижней границы отбора (1 га)."""
    first = shuttle(42.0, 150.0, 3.5)
    return first + shuttle(42.0, 150.0, 3.5, start=last_time(first) + 3 * HOUR,
                           noise=drifting_noise(1.0, seed=3))


def digest(path):
    with open(path, 'rb') as handle:
        return hashlib.sha256(handle.read()).hexdigest()


def sha(pair):
    return hashlib.sha1(('%s:%d' % pair).encode('ascii')).hexdigest()


def run_main(*argv):
    with contextlib.redirect_stdout(io.StringIO()) as out, \
            contextlib.redirect_stderr(io.StringIO()) as err:
        code = scan.main([str(arg) for arg in argv])
    return code, out.getvalue(), err.getvalue()


WORLD = {}


def setUpModule():
    root = tempfile.mkdtemp()
    folder = os.path.join(root, 'points')
    os.mkdir(folder)
    db = os.path.join(root, 'transport.db')
    con = sqlite3.connect(db)
    try:
        for statement in DDL[:2]:                 # vialon_mappings, equipment
            con.execute(statement)
        con.execute(migration.CREATE_DAILY_AGGREGATES)
        con.execute(migration.CREATE_WORK_POLYGONS)
        for name, plate in (('John Deere 9970', '80 743 KA'),
                            ('New Holland 7060', '80 584 CA')):
            con.execute("INSERT INTO equipment (name, plate, category, "
                        "organization_id, is_active) VALUES (?, ?, 'mtz', 1, 1)",
                        (name, plate))
        for title, unit, machine, skip in (
                ('JohnDeere 80 743 KA', ONE, 1, 0),
                ('Комбайн 702', TWICE, None, 0),
                ('New Holland 584 FA', CROSS, 2, 0),
                ('Чужой 704', ALIEN, None, 1)):
            con.execute("INSERT INTO vialon_mappings (vialon_name, wialon_id, "
                        "equipment_id, skip) VALUES (?, ?, ?, ?)",
                        (title, unit, machine, skip))
        con.commit()
        for day, unit, track in (
                ('2026-10-01', ONE, picker_field()),
                ('2026-10-02', TWICE, twice_in_a_day()),
                ('2026-10-03', CROSS, along_and_across()),
                ('2026-10-02', ALIEN, twice_in_a_day()),
                ('2026-10-04', SMALL, small_twice()),
                ('2026-10-04', TAMPERED, picker_field()),
                ('2026-10-05', OLD, picker_field()),
                ('2026-10-20', LATE, twice_in_a_day()),
                ('2026-10-03', CALM1, picker_field()),
                ('2026-10-05', CALM2, shuttle(120.0, 200.0, 4.0)),
                # вдоль и поперёк через 6 м: повторный ход этого не видит,
                # два направления видят -- выше уровня сутки по ним
                ('2026-10-04', SPARSE, along_and_across(6.0)),
                # одна машина, трое суток двойного прохода подряд
                ('2026-10-01', REPEATER, twice_in_a_day(seed=21)),
                ('2026-10-02', REPEATER, twice_in_a_day(seed=22)),
                ('2026-10-03', REPEATER, twice_in_a_day(seed=23)),
                ('2026-10-02', TWO_FIELDS, two_fields())):
            points = stamped(track, day)
            storage.write_points(folder, [(unit, t, lon, lat, speed, None, sats)
                                          for t, lon, lat, speed, sats in points])
            write_day(con, day, unit, compute_day(points), '2026-10-13 01:00:00')
        con.execute("UPDATE gps_work_polygons SET area_ha = area_ha + 1 "
                    "WHERE wialon_id = ?", (TAMPERED,))
        con.execute("UPDATE gps_daily_aggregates SET method_version = ? "
                    "WHERE wialon_id = ?", (PREVIOUS_METHOD_VERSION, OLD))
        # опубликованная строка без точек на диске
        con.execute("INSERT INTO gps_daily_aggregates (work_date, wialon_id, "
                    "method_version, computed_at) VALUES ('2026-10-03', ?, ?, "
                    "'2026-10-04 01:00:00')", (NO_POINTS, PREVIOUS_METHOD_VERSION))
        con.commit()
    finally:
        con.close()
    WORLD.update(root=root, folder=folder, db=db)
    out = os.path.join(root, 'out')
    os.mkdir(out)
    paths = {key: os.path.join(out, name) for key, name in (
        ('csv', 'scan.csv'), ('kml', 'scan.kml'), ('answers', 'answers.csv'))}
    code, stdout, stderr = run_main('--db', db, '--dir', folder, '--since', SINCE,
                                    '--until', UNTIL, '--csv', paths['csv'],
                                    '--kml', paths['kml'], '--answers',
                                    paths['answers'])
    WORLD.update(code=code, stdout=stdout, stderr=stderr, out=out, **paths)


def tearDownModule():
    shutil.rmtree(WORLD['root'], True)


def read_csv(path, encoding='utf-8'):
    with open(path, encoding=encoding, newline='') as handle:
        return list(csv.DictReader(handle))


def folders(path):
    return ET.parse(path).getroot().findall('.//k:Folder', NS)


def csv_rows(path=None):
    return {(row['day'], int(row['wialon_id'])): row
            for row in read_csv(path or WORLD['csv'])}


def run_picked(*extra):
    """({(день, объект): группа}, консоль) отдельного запуска с ключами отбора."""
    out = tempfile.mkdtemp()
    try:
        path = os.path.join(out, 'scan.csv')
        code, stdout, stderr = run_main('--db', WORLD['db'], '--dir', WORLD['folder'],
                                        '--since', SINCE, '--until', UNTIL,
                                        '--csv', path, *extra)
        assert code == 0, stderr
        return ({key: row['picked_as'] for key, row in csv_rows(path).items()
                 if row['picked_as']}, stdout)
    finally:
        shutil.rmtree(out, True)


class Period(unittest.TestCase):

    def setUp(self):
        self.rows = csv_rows()

    def test_the_run_succeeds(self):
        self.assertEqual(WORLD['code'], 0, WORLD['stderr'])
        self.assertIn('machine-days measured      : 13', WORLD['stdout'])
        self.assertIn('published, no points       : 1', WORLD['stdout'])
        self.assertIn('skipped (excluded/special) : 1', WORLD['stdout'])
        self.assertIn('failed (not measured)      : 0', WORLD['stdout'])

    def test_exactly_the_published_days_of_the_period_with_points(self):
        self.assertEqual(set(self.rows), ABOVE | BELOW | {('2026-10-04', SMALL)})
        units = {unit for _day, unit in self.rows}
        self.assertNotIn(ALIEN, units)       # исключён владельцем
        self.assertNotIn(NO_POINTS, units)   # точек на диске нет
        self.assertNotIn(LATE, units)        # вне периода

    def test_the_measures_tell_twice_and_across_from_once(self):
        self.assertGreaterEqual(float(self.rows[('2026-10-02', TWICE)]['repeat']), 0.4)
        cross = self.rows[('2026-10-03', CROSS)]
        self.assertGreaterEqual(float(cross['two_dirs']), 0.6)
        self.assertGreaterEqual(float(cross['repeat']), 0.3)
        for key in (('2026-10-01', ONE), ('2026-10-03', CALM1), ('2026-10-05', CALM2)):
            self.assertLessEqual(float(self.rows[key]['repeat']), 0.02, key)
            self.assertLessEqual(float(self.rows[key]['two_dirs']), 0.03, key)

    def test_machine_names_follow_the_screen(self):
        self.assertEqual(self.rows[('2026-10-01', ONE)]['machine'],
                         'John Deere 9970 — 80 743 KA')
        self.assertEqual(self.rows[('2026-10-02', TWICE)]['machine'], 'Комбайн 702')
        self.assertEqual(self.rows[('2026-10-03', CALM1)]['machine'], str(CALM1))
        self.assertEqual(self.rows[('2026-10-01', ONE)]['kind'], 'field')

    def test_control_against_the_database(self):
        self.assertEqual(self.rows[('2026-10-01', ONE)]['control'], 'ok')
        tampered = self.rows[('2026-10-04', TAMPERED)]
        self.assertEqual(tampered['control'], 'MISMATCH')
        self.assertAlmostEqual(float(tampered['stored_ha']),
                               float(tampered['a7_ha']) + 1, places=3)
        self.assertEqual(self.rows[('2026-10-05', OLD)]['control'], 'other version')

    def test_sites_are_kept_for_the_month_view(self):
        row = self.rows[('2026-10-02', TWICE)]
        geometries = json.loads(row['sites_geojson'])
        self.assertEqual(len(geometries), int(row['sites']))
        self.assertEqual(geometries[0]['type'], 'Polygon')
        lon, lat = geometries[0]['coordinates'][0][0]
        self.assertAlmostEqual(lon, 64.40, delta=0.01)
        self.assertAlmostEqual(lat, 39.99, delta=0.01)


class Picking(unittest.TestCase):

    def setUp(self):
        self.rows = csv_rows()
        self.picked = {key: row['picked_as'] for key, row in self.rows.items()
                       if row['picked_as']}

    def test_by_default_the_small_pool_is_picked_whole_but_the_cap(self):
        strongest = {key for key, group in self.picked.items() if group == 'strongest'}
        self.assertEqual(len(strongest), 5)
        self.assertTrue(strongest <= ABOVE)
        self.assertEqual({key for key, group in self.picked.items()
                          if group == 'below'}, BELOW)
        self.assertNotIn('above', self.picked.values())   # выше уровня все взяты

    def test_one_machine_gives_at_most_two_days_above_the_level(self):
        repeater = [key for key in ABOVE if key[1] == REPEATER]
        for key in repeater:
            self.assertGreaterEqual(float(self.rows[key]['repeat']), scan.PICK_LEVEL)
        self.assertEqual(sum(1 for key in repeater if key in self.picked), 2)
        self.assertTrue(ABOVE - set(repeater) <= set(self.picked))

    def test_a_small_site_is_not_put_up_for_review(self):
        small = self.rows[('2026-10-04', SMALL)]
        # пройдено дважды, но участок меньше нижней границы отбора
        self.assertGreaterEqual(float(small['repeat']), scan.PICK_LEVEL)
        self.assertLess(float(small['a7_ha']), scan.PICK_MIN_HA)
        self.assertEqual((small['picked_as'], small['kml_folder']), ('', ''))

    def test_two_directions_alone_are_enough_to_be_above_the_level(self):
        sparse = self.rows[('2026-10-04', SPARSE)]
        self.assertLess(float(sparse['repeat']), scan.PICK_LEVEL)
        self.assertGreaterEqual(float(sparse['two_dirs']), scan.PICK_LEVEL)
        self.assertEqual(sparse['picked_as'], 'strongest')

    def test_the_strongest_is_the_highest_score(self):
        picked, stdout = run_picked('--top', 1, '--spread', 0, '--sample', 0)
        self.assertEqual(picked, {('2026-10-03', CROSS): 'strongest'})
        self.assertIn('KML folders: 1 (strongest 1, above 0, below 0)', stdout)

    def test_the_spread_is_the_sha1_order_of_the_rest_above(self):
        picked, _ = run_picked('--top', 1, '--spread', 2, '--sample', 0)
        expected, per_unit = [], {}
        for key in sorted(ABOVE - {('2026-10-03', CROSS)}, key=sha):
            if per_unit.get(key[1], 0) < scan.PER_UNIT_MAX:
                expected.append(key)
                per_unit[key[1]] = per_unit.get(key[1], 0) + 1
        self.assertEqual({key for key, group in picked.items() if group == 'above'},
                         set(expected[:2]))

    def test_the_sample_is_the_sha1_order_below_the_level(self):
        picked, _ = run_picked('--top', 0, '--spread', 0, '--sample', 2)
        self.assertEqual(set(picked), set(sorted(BELOW, key=sha)[:2]))
        self.assertEqual(set(picked.values()), {'below'})


class Review(unittest.TestCase):

    def setUp(self):
        self.folders = folders(WORLD['kml'])
        self.names = [folder.find('k:name', NS).text for folder in self.folders]
        self.answers = read_csv(WORLD['answers'], 'utf-8-sig')
        self.rows = csv_rows()

    def test_folders_go_in_sha1_order_with_the_groups_mixed(self):
        picked = sorted((key for key, row in self.rows.items() if row['picked_as']),
                        key=sha)
        self.assertEqual([(row['day'], int(row['wialon_id'])) for row in self.answers],
                         picked)
        self.assertEqual(len(self.names), 11)

    def test_the_review_is_blind(self):
        """Ни чисел признаков, ни группы отбора -- ни в KML, ни в шаблоне."""
        with open(WORLD['kml'], encoding='utf-8') as handle:
            text = handle.read()
        for word in ('повтор', 'такрорий', 'направлени', 'йўналиш', '%',
                     'кандидат', 'контроль', 'strongest', 'above', 'below'):
            self.assertNotIn(word, text)
        self.assertEqual(tuple(self.answers[0].keys()), scan.ANSWER_COLUMNS)
        # консоль называет группы числом, но не перечисляет сутки
        tail = WORLD['stdout'].split('KML folders:')[1].split('CSV (')[0]
        for unit in range(701, 715):
            self.assertNotIn(str(unit), tail)

    def test_folder_names_numbers_and_answer_rows_match(self):
        self.assertEqual([row['folder'] for row in self.answers],
                         [str(n) for n in range(1, 12)])
        for row, name in zip(self.answers, self.names):
            day = '%s.%s.%s' % tuple(reversed(row['day'].split('-')))
            self.assertEqual(name, '%s. %s · %s' % (row['folder'], row['machine'], day))
            self.assertEqual((row['owner_answer'], row['owner_note']), ('', ''))
            self.assertEqual(self.rows[(row['day'], int(row['wialon_id']))]['kml_folder'],
                             row['folder'])

    def folder_of(self, day, unit):
        number_ = int(self.rows[(day, unit)]['kml_folder'])
        return self.folders[number_ - 1]

    def test_each_site_has_its_own_four_colours_of_time(self):
        for folder in self.folders:
            marks = folder.findall('k:Placemark', NS)
            styles = [mark.find('k:styleUrl', NS).text for mark in marks]
            sites = styles.count('#site')
            self.assertGreaterEqual(sites, 1)
            self.assertEqual([style for style in styles if style.startswith('#t')],
                             ['#t1', '#t2', '#t3', '#t4'] * sites)
            labels = [mark.find('k:name', NS).text for mark in marks]
            for label in labels:
                if label.startswith('Участок ') and ', трек ' in label:
                    self.assertRegex(label, r'^Участок (\d), трек (\d\d:\d\d–\d\d:\d\d) '
                                            r'/ участка \1, трек \2$')

    def test_two_fields_are_coloured_each_by_its_own_time(self):
        folder = self.folder_of('2026-10-02', TWO_FIELDS)
        labels = [mark.find('k:name', NS).text
                  for mark in folder.findall('k:Placemark', NS)]
        first = [label for label in labels if label.startswith('Участок 1, трек ')]
        second = [label for label in labels if label.startswith('Участок 2, трек ')]
        self.assertEqual((len(first), len(second)), (4, 4))

        def span(labels_):
            clocks = [label.split(', трек ')[1].split(' / ')[0] for label in labels_]
            return clocks[0].split('–')[0], clocks[-1].split('–')[1]
        # поля пройдены одно за другим: у каждого свои четверти, и время
        # одного не заходит на время другого (участки нумеруются по площади,
        # а не по времени)
        (start1, end1), (start2, end2) = span(first), span(second)
        self.assertTrue(end1 <= start2 or end2 <= start1, (start1, end1, start2, end2))

    def test_only_the_largest_sites_get_colours(self):
        with unittest.mock.patch.object(scan, 'SITE_COLOURED', 1):
            row = self.rows[('2026-10-02', TWO_FIELDS)]
            points = sorted(scan.read_day_readonly(WORLD['folder'], TWO_FIELDS,
                                                   '2026-10-02'))
            text = scan.folder_kml(1, dict(row, a7_ha=float(row['a7_ha'])), points)
        self.assertEqual(text.count('<styleUrl>#t1</styleUrl>'), 1)
        self.assertNotIn('Участок 2, трек', text)
        self.assertIn('Трек в работе вне участков / участкалардан ташқари иш '
                      'тезлигидаги трек', text)

    def test_a_double_click_flies_to_the_sites(self):
        for folder in self.folders:
            look = folder.find('k:LookAt', NS)
            self.assertIsNotNone(look)
            lon = float(look.find('k:longitude', NS).text)
            lat = float(look.find('k:latitude', NS).text)
            corners = [tuple(map(float, point.split(',')))
                       for mark in folder.findall('k:Placemark', NS)
                       if mark.find('k:styleUrl', NS).text == '#site'
                       for point in mark.find('.//k:coordinates', NS).text.split()]
            self.assertTrue(min(x for x, _ in corners) <= lon <= max(x for x, _ in corners))
            self.assertTrue(min(y for _, y in corners) <= lat <= max(y for _, y in corners))
            self.assertGreaterEqual(float(look.find('k:range', NS).text), scan.LOOK_MIN_M)

    def test_a_tiny_site_is_still_seen_from_a_sensible_height(self):
        tiny = {'sites_geojson': json.dumps([{'type': 'Polygon', 'coordinates': [[
            [64.4, 39.99], [64.4002, 39.99], [64.4002, 39.9902], [64.4, 39.9902],
            [64.4, 39.99]]]}])}
        look = ET.fromstring(scan.look_at(tiny).replace(
            '<LookAt>', '<LookAt xmlns="http://www.opengis.net/kml/2.2">'))
        self.assertEqual(float(look.find('k:range', NS).text), scan.LOOK_MIN_M)
        self.assertAlmostEqual(float(look.find('k:longitude', NS).text), 64.4001, places=6)
        self.assertEqual(scan.look_at({'sites_geojson': '[]'}), '')

    def test_the_question_is_asked_in_both_languages(self):
        text = self.folders[0].find('k:description', NS).text
        self.assertIn('прошла ли машина те же места дважды за эти сутки?', text)
        self.assertIn('машина бир кунда ўша жойлардан икки марта ўтдими?', text)
        self.assertIn('Ответ — в шаблон, строка 1', text)

    def test_the_answers_template_opens_in_excel_as_cyrillic(self):
        with open(WORLD['answers'], 'rb') as handle:
            self.assertEqual(handle.read(3), b'\xef\xbb\xbf')


class ReadOnly(unittest.TestCase):

    def test_nothing_but_the_named_files_is_written(self):
        before = {name: digest(os.path.join(WORLD['folder'], name))
                  for name in os.listdir(WORLD['folder'])}
        db_before = digest(WORLD['db'])
        out = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, out, True)
        code, _out, err = run_main('--db', WORLD['db'], '--dir', WORLD['folder'],
                                   '--since', SINCE, '--until', UNTIL,
                                   '--kml', os.path.join(out, 'a.kml'))
        self.assertEqual(code, 0, err)
        self.assertEqual(digest(WORLD['db']), db_before)
        self.assertEqual({name: digest(os.path.join(WORLD['folder'], name))
                          for name in os.listdir(WORLD['folder'])}, before)
        self.assertEqual(os.listdir(out), ['a.kml'])

    def test_bad_input_is_refused_before_anything_is_read(self):
        out = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, out, True)
        target = os.path.join(out, 'a.csv')
        base = ('--db', WORLD['db'], '--dir', WORLD['folder'])
        for argv, message in (
                (base + ('--since', '01.10.2026', '--csv', target),
                 '--since must look like YYYY-MM-DD'),
                (base + ('--since', UNTIL, '--until', SINCE, '--csv', target),
                 '--until is before --since'),
                (base + ('--since', SINCE), 'nothing to write'),
                (base + ('--since', SINCE, '--csv', os.path.join(out, 'no', 'a.csv')),
                 'does not exist'),
                (base + ('--since', SINCE, '--csv', target, '--sample', '-1'),
                 'cannot be negative'),
                (('--db', os.path.join(out, 'no.db'), '--dir', WORLD['folder'],
                  '--since', SINCE, '--csv', target), 'database not found'),
                (('--db', WORLD['db'], '--dir', os.path.join(out, 'none'),
                  '--since', SINCE, '--csv', target), 'points folder not found')):
            with unittest.mock.patch.object(scan, 'scan') as scanned:
                code, _out, err = run_main(*argv)
            self.assertEqual(code, 2, argv)
            self.assertIn(message, err)
            scanned.assert_not_called()
        self.assertEqual(os.listdir(out), [])

    def test_a_day_that_fails_is_named_and_the_run_goes_on(self):
        out = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, out, True)
        path = os.path.join(out, 'a.csv')
        real = scan.measure_day

        def measure(points, sites=None):
            # только у суток с двумя полями есть точки в 850 м к востоку
            if any(row[1] > 64.405 for row in points):
                raise ValueError('provoked')
            return real(points, sites)
        with unittest.mock.patch.object(scan, 'measure_day', measure):
            code, stdout, err = run_main('--db', WORLD['db'], '--dir', WORLD['folder'],
                                         '--since', SINCE, '--until', UNTIL,
                                         '--csv', path)
        self.assertEqual(code, 3, err)
        self.assertIn('failed (not measured)      : 1', stdout)
        self.assertIn('2026-10-02 %d ValueError: provoked' % TWO_FIELDS, stdout)
        self.assertEqual(set(csv_rows(path)),
                         (ABOVE | BELOW | {('2026-10-04', SMALL)})
                         - {('2026-10-02', TWO_FIELDS)})

    def test_the_application_is_not_imported(self):
        code = ('import sys, tools.gps_multipass_scan; '
                'print(sorted(m for m in ("flask", "models", "app") if m in sys.modules))')
        result = subprocess.run([sys.executable, '-c', code], cwd=REPO,
                                capture_output=True, text=True, check=True)
        self.assertEqual(result.stdout.strip(), '[]')


if __name__ == '__main__':
    unittest.main()
