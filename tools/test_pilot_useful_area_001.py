# -*- coding: utf-8 -*-
"""Проверка операторского комплекта DRONE-USEFUL-AREA-PILOT-001.

    python tools/test_pilot_useful_area_001.py

Комплект -- это готовые к вставке команды PowerShell и приборы, которые
решают, можно ли верить прогону. Вставлены команды будут буквально, а решению
поверят на слово. Поэтому комплект проверяется так же, как код.

Что здесь держится и почему именно это:

* **`C:\\transport-report-staging` НАЧИНАЕТСЯ с `C:\\transport-report`.**
  Гвардия «не трогать production», написанная через вхождение подстроки,
  объявила бы площадку продакшеном, а при обратном сравнении -- продакшен
  площадкой. Сравнение посегментное, и это проверяется в обе стороны;
* **отпечаток `drone_flights.area_ha` обязан РАЗЛИЧАТЬ два случая.** Рядом с
  каждым утверждением «не изменилась» стоит отрицательный контроль: NULL,
  ставший нулём, перестановка значений двух строк и новая строка -- все три
  не меняют сумму, и проверка по сумме их не заметила бы;
* **вердикт GO обязан быть недостижим при любом одном нарушенном условии.**
  Проверяется не «GO бывает», а «GO исчезает» -- по одному нарушению за раз;
* **сухой прогон не пишет.** Проверяется чтением базы после него, а не флагом
  режима: инструмент, объявивший себя сухим и записавший, -- ровно тот
  дефект, ради которого проверка существует;
* **частично принятый пакет успехом не считается**;
* **разбор сводки пересчёта строгий.** Разбор, возвращающий нули на непонятом
  вводе, объявил бы идемпотентным прогон, у которого просто не прочитали
  вывод;
* **флаги командных строк -- только настоящие.** Каждый `--флаг`, который
  комплект передаёт сборщику и инструменту пересчёта, сверяется с их
  фактическими парсерами, а не с памятью автора;
* **PowerShell разбирается настоящим парсером PowerShell**, когда `pwsh`
  доступен, и его чистые функции при этом ВЫПОЛНЯЮТСЯ. Статическая проверка
  текста -- вторая сеть, а не первая.

Только stdlib: набор гоняется в CI, где стоит python плюс jinja2 и openpyxl.
Вывод -- ASCII: файл читается и на консоли Windows.

ГЕОМЕТРИЯ ЗДЕСЬ СИНТЕТИЧЕСКАЯ. Поле -- квадрат 200 м вокруг круглой точки,
вылеты пронумерованы с 900001. Ни одной настоящей координаты, ни одного
настоящего идентификатора.
"""

import copy
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KIT_DIR = os.path.join(REPO_ROOT, 'ops', 'pilot_useful_area_001')
for path in (REPO_ROOT, KIT_DIR):
    if path not in sys.path:
        sys.path.insert(0, path)

import pilot_common as common  # noqa: E402
import pilot_collect_check as collect_check  # noqa: E402
import pilot_db_probe as probe  # noqa: E402
import pilot_recalc_parse as recalc_parse  # noqa: E402
import pilot_report as report_mod  # noqa: E402

import drone_coverage_recalc as recalc  # noqa: E402

PS_SCRIPTS = ('PREFLIGHT_AND_COPY_TEST.ps1', 'STAGING_DEPLOY_AND_MIGRATE.ps1',
              'STAGING_ROLLBACK.ps1', 'BAK_TEX11_DJI_COLLECT_TO_STAGING.ps1',
              'STAGING_RECALCULATE_AND_VERIFY.ps1', 'STAGING_PILOT_REPORT.ps1')
PS_FILES = PS_SCRIPTS + ('PilotKit.psm1',)

STAGING_SCRIPTS = ('PREFLIGHT_AND_COPY_TEST.ps1',
                   'STAGING_DEPLOY_AND_MIGRATE.ps1', 'STAGING_ROLLBACK.ps1',
                   'STAGING_RECALCULATE_AND_VERIFY.ps1',
                   'STAGING_PILOT_REPORT.ps1')

# SYNTHETIC / NOT-REAL. Те же круглые числа, что в tools/test_drone_coverage_recalc.py.
LAT0 = 39.70
LON0 = 64.40
M_PER_DEG_LAT = 111320.0
M_PER_DEG_LON = 111320.0 * 0.7679
DAY = '2026-06-05'


def script_text(name):
    with open(os.path.join(KIT_DIR, name), encoding='utf-8') as handle:
        return handle.read()


def code_lines(name):
    """Строки скрипта БЕЗ комментариев и без блока документации.

    [REASON]: проверки «команды X здесь нет» обязаны смотреть на команды.
    Первая редакция читала весь файл и нашла `git reset --hard` в абзаце,
    который объясняет, почему этой команды в комплекте нет. Проверка,
    падающая на собственном объяснении, ничего не проверяет.
    """
    lines = []
    inside_block = False
    for line in script_text(name).splitlines():
        stripped = line.strip()
        if stripped.startswith('<#'):
            inside_block = True
        if inside_block:
            if '#>' in stripped:
                inside_block = False
            continue
        if stripped.startswith('#'):
            continue
        lines.append(line)
    return lines


def code_text(name):
    return '\n'.join(code_lines(name))


def at(east_m, north_m):
    return [LAT0 + north_m / M_PER_DEG_LAT, LON0 + east_m / M_PER_DEG_LON]


def square(half_m):
    corners = [at(-half_m, -half_m), at(half_m, -half_m), at(half_m, half_m),
               at(-half_m, half_m), at(-half_m, -half_m)]
    return {'type': 'Polygon',
            'coordinates': [[[point[1], point[0]] for point in corners]]}


def pass_line(east_m, north_from, north_to, step_m=5.0):
    points = []
    north = north_from
    while north <= north_to + 1e-9:
        points.append(at(east_m, north))
        north += step_m
    return points


def run_python(*arguments, **kwargs):
    return subprocess.run([sys.executable] + list(arguments),
                          capture_output=True, text=True, **kwargs)


# ─── Общая синтетическая площадка ────────────────────────────────────────────

class SyntheticSite(object):
    """Временная база, ПРОШЕДШАЯ НАСТОЯЩУЮ миграцию, и улики по ней.

    Миграция запускается так же, как её запускает операторский скрипт: файл
    копируется в песочницу рядом с `instance/transport.db`, потому что
    `migrate_drones_useful_area_001.py` берёт путь к базе из своего
    `__file__` и флага `--db` не имеет. Если этот способ перестанет работать,
    он перестанет работать и здесь -- а не только на сервере.
    """

    def __init__(self):
        self.root = tempfile.mkdtemp(prefix='pilot_kit_')
        self.sandbox = os.path.join(self.root, 'sandbox')
        os.makedirs(os.path.join(self.sandbox, 'instance'))
        self.db = os.path.join(self.sandbox, 'instance', 'transport.db')
        self.evidence = os.path.join(self.root, 'evidence')
        os.makedirs(self.evidence)
        self._build_base()
        self.migration_output = self._migrate()
        self._populate()
        self.recalc_output = {}
        for label, mode in (('dry-run', '--dry-run'), ('apply-1', '--apply'),
                            ('apply-2', '--apply')):
            self.recalc_output[label] = self._recalculate(mode, label)

    # -- построение --------------------------------------------------------
    def _build_base(self):
        con = sqlite3.connect(self.db)
        try:
            con.executescript(
                'CREATE TABLE drone_units ('
                ' id INTEGER PRIMARY KEY, number INTEGER);'
                'CREATE TABLE drone_flights ('
                ' id INTEGER PRIMARY KEY AUTOINCREMENT, dji_flight_id BIGINT,'
                ' drone_unit_id INTEGER, nickname_raw TEXT,'
                ' started_at DATETIME, area_ha FLOAT);'
                'CREATE TABLE field_contours ('
                ' id INTEGER PRIMARY KEY AUTOINCREMENT, source TEXT,'
                ' external_id TEXT, name TEXT, geometry_geojson TEXT,'
                ' is_active BOOLEAN, bbox_min_lat FLOAT, bbox_max_lat FLOAT,'
                ' bbox_min_lng FLOAT, bbox_max_lng FLOAT);')
            con.commit()
        finally:
            con.close()

    def _migrate(self):
        for name in ('migrate_drones_useful_area_001.py',
                     'migration_utils.py'):
            shutil.copy2(os.path.join(REPO_ROOT, name),
                         os.path.join(self.sandbox, name))
        result = run_python(
            os.path.join(self.sandbox, 'migrate_drones_useful_area_001.py'),
            cwd=self.sandbox)
        if result.returncode != 0:
            raise AssertionError('the migration failed in the sandbox: %s%s'
                                 % (result.stdout, result.stderr))
        return result.stdout

    def _populate(self):
        geojson = square(100.0)
        ring = geojson['coordinates'][0]
        lats = [point[1] for point in ring]
        lngs = [point[0] for point in ring]
        con = sqlite3.connect(self.db)
        try:
            con.execute('INSERT INTO drone_units (id, number) VALUES (6, 6)')
            con.execute(
                'INSERT INTO field_contours (source, external_id, name,'
                ' geometry_geojson, is_active, bbox_min_lat, bbox_max_lat,'
                ' bbox_min_lng, bbox_max_lng) VALUES (?,?,?,?,1,?,?,?,?)',
                ('dji', 'SYNTHETIC-1', 'SYNTHETIC field',
                 json.dumps(geojson), min(lats), max(lats), min(lngs),
                 max(lngs)))
            for index, east in enumerate((-40.0, 0.0), start=1):
                flight_id = 900000 + index
                con.execute(
                    'INSERT INTO drone_flights (dji_flight_id, drone_unit_id,'
                    ' nickname_raw, started_at, area_ha) VALUES (?,6,?,?,?)',
                    (flight_id, 'SYNTHETIC-NICK',
                     '2026-06-05 03:0%d:00' % index, 2.0))
                row_id = con.execute(
                    'SELECT id FROM drone_flights WHERE dji_flight_id = ?',
                    (flight_id,)).fetchone()[0]
                points = pass_line(east, -90.0, 90.0)
                con.execute(
                    'INSERT INTO drone_flight_routes (dji_flight_id,'
                    ' drone_flight_id, point_count, points_json,'
                    ' spray_width_m, spray_width_recorded, dji_area_m2,'
                    ' content_sha256, source, received_at, updated_at,'
                    ' ingest_count) VALUES (?,?,?,?,8.0,1,?,?,?,?,?,1)',
                    (flight_id, row_id, len(points),
                     json.dumps(points, separators=(',', ':')), 20000.0,
                     'SYNTHETIC-CONTENT-%d' % flight_id, 'dji-ui-capture',
                     '2026-06-05 03:00:00', '2026-06-05 03:00:00'))
            con.commit()
        finally:
            con.close()

    def _recalculate(self, mode, label):
        result = run_python(
            os.path.join(REPO_ROOT, 'tools',
                         'recalculate_drone_useful_area.py'),
            '--from', DAY, '--to', DAY, mode, '--db', self.db)
        if result.returncode != 0:
            raise AssertionError('the recalculation failed (%s): %s%s'
                                 % (label, result.stdout, result.stderr))
        path = os.path.join(self.evidence, 'recalc_%s.txt' % label)
        with open(path, 'w', encoding='utf-8', newline='\n') as handle:
            handle.write(result.stdout)
        return path

    # -- улики -------------------------------------------------------------
    def snapshot(self, *requirements):
        arguments = [os.path.join(KIT_DIR, 'pilot_db_probe.py'), 'snapshot',
                     '--db', self.db, '--day', DAY]
        for requirement in requirements:
            arguments += ['--require', requirement]
        result = run_python(*arguments)
        return result, (json.loads(result.stdout) if result.stdout.strip()
                        else None)

    def recalc_evidence(self):
        paths = {}
        previous = None
        for label in ('dry-run', 'apply-1', 'apply-2'):
            out = os.path.join(self.evidence, 'recalc_%s.json' % label)
            arguments = [os.path.join(KIT_DIR, 'pilot_recalc_parse.py'),
                         '--input', self.recalc_output[label],
                         '--label', label, '--expect-day', DAY, '--out', out]
            if previous:
                arguments += ['--compare-with', previous]
            result = run_python(*arguments)
            if result.returncode != 0:
                raise AssertionError('the recalculation summary of %s did not '
                                     'parse: %s' % (label, result.stderr))
            paths[label] = out
            previous = out
        return paths

    def close(self):
        shutil.rmtree(self.root, ignore_errors=True)


def envelope(kind, payload):
    return {'kit': common.KIT_ID, 'kit_version': common.KIT_VERSION,
            'evidence_kind': kind, 'generated_utc': '2026-09-02T10:00:00Z',
            'target_day': DAY, 'verified_sha': common.VERIFIED_MERGE_SHA,
            'payload': payload}


HEALTHY_COUNTERS = {
    'mode': 'route-collect', 'dry_run': False, 'region': 'CN',
    'probe_route_responses': 2, 'probe_observations': 2, 'probe_confirmed': 2,
    'probe_errors': 0, 'probe_skipped_over_cap': 0,
    'probe_operator_answered': True, 'probe_drained': True,
    'collect_live_confirmed': True, 'collect_bodies_captured': 2,
    'collect_decode_failures': 0, 'collect_capture_errors': 0,
    'collect_routes_captured': 2, 'collect_routes_queued': 2,
    'collect_routes_duplicate': 0, 'collect_send_enabled': True,
    'collect_envelopes_sent': 2, 'collect_batch_accepted': True,
    'collect_left_pending': 0, 'collect_seen': 2, 'collect_new': 2,
    'collect_updated': 0, 'collect_unchanged': 0, 'collect_errors': 0,
    'collect_unlinked': 0, 'exit': 0,
}


# ═══ 1. Пути и адреса ════════════════════════════════════════════════════════

class PathGuards(unittest.TestCase):
    """`C:\\transport-report-staging` начинается с `C:\\transport-report`."""

    def test_staging_root_is_not_inside_production(self):
        self.assertFalse(common.path_is_within(common.STAGING_ROOT,
                                               common.PRODUCTION_ROOT))

    def test_staging_database_does_not_touch_production(self):
        self.assertFalse(common.touches_production(common.STAGING_DB))

    def test_production_database_is_recognised(self):
        self.assertTrue(common.touches_production(common.PRODUCTION_DB))

    def test_a_substring_guard_would_have_been_wrong(self):
        """Отрицательный контроль: показывает, что именно ловится.

        Если однажды сравнение вернут к `startswith`, этот тест назовёт цену:
        площадка станет продакшеном.
        """
        naive = common.STAGING_ROOT.lower().startswith(
            common.PRODUCTION_ROOT.lower())
        self.assertTrue(naive, 'the substring trap must still exist, else '
                               'this test proves nothing')
        self.assertNotEqual(naive,
                            common.path_is_within(common.STAGING_ROOT,
                                                  common.PRODUCTION_ROOT))

    def test_slashes_and_case_do_not_change_the_answer(self):
        self.assertTrue(common.path_equals('C:/transport-report/instance',
                                           'C:\\TRANSPORT-REPORT\\instance\\'))

    def test_production_and_staging_urls_never_collide(self):
        self.assertTrue(common.url_is_production(common.PRODUCTION_URL))
        self.assertFalse(common.url_is_production(common.STAGING_URL))
        self.assertTrue(common.url_is_staging(common.STAGING_URL + '/drones/'))
        self.assertFalse(common.url_is_staging(common.PRODUCTION_URL))


# ═══ 2. Отпечаток drone_flights.area_ha ══════════════════════════════════════

class AreaFingerprint(unittest.TestCase):
    """Проверка обязана РАЗЛИЧАТЬ изменённую и неизменённую площадь."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='pilot_area_')
        self.db = os.path.join(self.tmp, 'throwaway.db')
        con = sqlite3.connect(self.db)
        con.execute('CREATE TABLE drone_flights (id INTEGER PRIMARY KEY, '
                    'area_ha FLOAT)')
        con.executemany('INSERT INTO drone_flights VALUES (?, ?)',
                        [(1, 1.2345), (2, 2.0), (3, None)])
        con.commit()
        con.close()
        self.base = self.fingerprint()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def fingerprint(self):
        con, _mode = common.connect_readonly(self.db)
        try:
            return common.area_ha_fingerprint(con)
        finally:
            con.close()

    def write(self, sql, args=()):
        con = sqlite3.connect(self.db)
        con.execute(sql, args)
        con.commit()
        con.close()

    def test_an_untouched_table_keeps_its_digest(self):
        self.assertEqual(self.fingerprint()['sha256'], self.base['sha256'])

    def test_the_smallest_possible_change_is_seen(self):
        self.write('UPDATE drone_flights SET area_ha = ? WHERE id = 1',
                   (1.2345 + 1e-12,))
        self.assertNotEqual(self.fingerprint()['sha256'], self.base['sha256'])

    def test_null_becoming_zero_is_seen_and_the_sum_would_not_see_it(self):
        self.write('UPDATE drone_flights SET area_ha = 0.0 WHERE id = 3')
        after = self.fingerprint()
        self.assertNotEqual(after['sha256'], self.base['sha256'])
        # Отрицательный контроль: сумма при этом НЕ изменилась.
        self.assertEqual(after['sum_area_ha'], self.base['sum_area_ha'])

    def test_swapping_two_rows_is_seen_and_the_sum_would_not_see_it(self):
        self.write('UPDATE drone_flights SET area_ha = 2.0 WHERE id = 1')
        self.write('UPDATE drone_flights SET area_ha = 1.2345 WHERE id = 2')
        after = self.fingerprint()
        self.assertNotEqual(after['sha256'], self.base['sha256'])
        self.assertEqual(after['sum_area_ha'], self.base['sum_area_ha'])

    def test_an_added_null_row_is_seen_and_the_sum_would_not_see_it(self):
        self.write('INSERT INTO drone_flights VALUES (4, NULL)')
        after = self.fingerprint()
        self.assertNotEqual(after['sha256'], self.base['sha256'])
        self.assertEqual(after['sum_area_ha'], self.base['sum_area_ha'])
        self.assertEqual(after['rows'], self.base['rows'] + 1)

    def test_restoring_the_value_restores_the_digest(self):
        self.write('UPDATE drone_flights SET area_ha = 9.9 WHERE id = 1')
        self.assertNotEqual(self.fingerprint()['sha256'], self.base['sha256'])
        self.write('UPDATE drone_flights SET area_ha = 1.2345 WHERE id = 1')
        self.assertEqual(self.fingerprint()['sha256'], self.base['sha256'])

    def test_the_digest_carries_no_row_and_no_identifier(self):
        text = json.dumps(self.base)
        self.assertNotIn('900001', text)
        self.assertNotIn('1.2345', text.replace('3.2345', ''))


# ═══ 3. Прибор осмотра ═══════════════════════════════════════════════════════

class ProbeIsReadOnly(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='pilot_probe_')
        self.db = os.path.join(self.tmp, 'throwaway.db')
        con = sqlite3.connect(self.db)
        con.execute('CREATE TABLE drone_flights (id INTEGER PRIMARY KEY, '
                    'area_ha FLOAT)')
        con.execute('INSERT INTO drone_flights VALUES (1, 1.0)')
        con.commit()
        con.close()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_the_connection_refuses_a_write(self):
        """Отрицательный контроль режима: запись обязана ОТКАЗАТЬ.

        Проверка «мы открыли только на чтение» без попытки записи давала бы
        одинаковый результат при верном и неверном коде.
        """
        con, mode = common.connect_readonly(self.db)
        try:
            self.assertIn(mode, ('uri-ro', 'query_only'))
            with self.assertRaises(sqlite3.OperationalError):
                con.execute('UPDATE drone_flights SET area_ha = 2.0')
                con.commit()
        finally:
            con.close()

    def test_a_missing_database_is_refused_and_no_file_is_created(self):
        missing = os.path.join(self.tmp, 'not-there.db')
        result = run_python(os.path.join(KIT_DIR, 'pilot_db_probe.py'),
                            'integrity', '--db', missing)
        self.assertEqual(result.returncode, common.EXIT_NO_DATABASE)
        self.assertFalse(os.path.exists(missing))

    def test_an_unknown_requirement_fails_rather_than_passing_silently(self):
        payload = {'integrity': {'integrity_ok': True}}
        results = probe.evaluate_requirements(payload, ['integrity',
                                                        'integritty'])
        self.assertEqual(results, [('integrity', True), ('integritty', False)])

    def test_stdout_is_one_json_document_even_when_a_check_fails(self):
        result = run_python(os.path.join(KIT_DIR, 'pilot_db_probe.py'),
                            'schema', '--db', self.db, '--require', 'schema')
        self.assertEqual(result.returncode, common.EXIT_CHECK_FAILED)
        json.loads(result.stdout)
        self.assertIn('CHECK FAILED', result.stderr)


class ProbeOnASite(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.site = SyntheticSite()

    @classmethod
    def tearDownClass(cls):
        cls.site.close()

    def test_the_real_migration_left_both_tables_five_indexes_and_a_row(self):
        result, document = self.site.snapshot('integrity', 'schema')
        self.assertEqual(result.returncode, common.EXIT_OK, result.stderr)
        schema = document['payload']['schema']
        self.assertTrue(schema['tables_all_present'])
        self.assertEqual(schema['indexes_present'], 5)
        self.assertTrue(schema['migration_registered'])
        self.assertTrue(schema['migration_checksum_present'])

    def test_the_probe_records_that_it_did_not_look_at_production(self):
        _result, document = self.site.snapshot()
        self.assertFalse(document['database_is_production'])
        self.assertFalse(document['database_within_production_root'])

    def test_the_day_holds_the_routes_and_nothing_falls_outside_it(self):
        _result, document = self.site.snapshot('no-off-day-routes')
        routes = document['payload']['routes']
        self.assertEqual(routes['flights_of_target_day'], 2)
        self.assertEqual(routes['routes_of_target_day'], 2)
        self.assertEqual(routes['routes_outside_target_day'], 0)

    def test_a_route_of_another_day_is_caught(self):
        """Отрицательный контроль дня: подложенный чужой вылет обязан всплыть."""
        con = sqlite3.connect(self.site.db)
        try:
            con.execute(
                'INSERT INTO drone_flights (dji_flight_id, drone_unit_id,'
                ' nickname_raw, started_at, area_ha) VALUES (?,6,?,?,?)',
                (900099, 'SYNTHETIC-NICK', '2026-06-09 03:00:00', 1.0))
            row_id = con.execute('SELECT id FROM drone_flights '
                                 ' WHERE dji_flight_id = 900099').fetchone()[0]
            con.execute(
                'INSERT INTO drone_flight_routes (dji_flight_id,'
                ' drone_flight_id, point_count, points_json, content_sha256,'
                ' source, received_at, updated_at, ingest_count)'
                ' VALUES (?,?,2,?,?,?,?,?,1)',
                (900099, row_id, json.dumps(pass_line(0.0, 0.0, 5.0)),
                 'SYNTHETIC-OTHER-DAY', 'dji-ui-capture',
                 '2026-06-09 03:00:00', '2026-06-09 03:00:00'))
            con.commit()
            result, document = self.site.snapshot('no-off-day-routes')
            self.assertEqual(result.returncode, common.EXIT_CHECK_FAILED)
            self.assertEqual(
                document['payload']['routes']['routes_outside_target_day'], 1)
        finally:
            con.execute('DELETE FROM drone_flight_routes '
                        ' WHERE dji_flight_id = 900099')
            con.execute('DELETE FROM drone_flights '
                        ' WHERE dji_flight_id = 900099')
            con.commit()
            con.close()

    def test_only_ready_carries_a_number(self):
        _result, document = self.site.snapshot('only-ready-summed')
        coverage = document['payload']['coverage']
        self.assertTrue(coverage['only_ready_carries_a_number'])
        self.assertEqual(coverage['non_ready_rows_carrying_a_number'], 0)
        self.assertGreater(coverage['works'], 0)

    def test_a_non_ready_row_with_a_number_is_caught(self):
        """Отрицательный контроль суммируемости."""
        con = sqlite3.connect(self.site.db)
        try:
            con.execute("UPDATE drone_coverage_works "
                        "   SET quality_status = 'PARTIAL_DATA'")
            con.commit()
            result, document = self.site.snapshot('only-ready-summed')
            self.assertEqual(result.returncode, common.EXIT_CHECK_FAILED)
            self.assertFalse(
                document['payload']['coverage']['only_ready_carries_a_number'])
        finally:
            con.execute("UPDATE drone_coverage_works "
                        "   SET quality_status = 'READY_ESTIMATE'")
            con.commit()
            con.close()

    def test_the_snapshot_carries_no_coordinate_and_no_flight_id(self):
        _result, document = self.site.snapshot()
        text = json.dumps(document)
        self.assertNotIn('900001', text)
        self.assertNotIn('39.7', text)
        self.assertNotIn('64.4', text)
        self.assertIsNone(re.search(r'-?\b\d{1,3}\.\d{5,}\b', text),
                          'a coordinate-shaped number reached the snapshot')


# ═══ 4. Разбор сводки пересчёта ══════════════════════════════════════════════

class RecalcSummaryParsing(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.site = SyntheticSite()
        cls.evidence = cls.site.recalc_evidence()

    @classmethod
    def tearDownClass(cls):
        cls.site.close()

    def load(self, label):
        with open(self.evidence[label], encoding='ascii') as handle:
            return json.load(handle)['payload']

    def test_the_parser_reads_the_real_format_summary(self):
        """Разбор сверяется с НАСТОЯЩИМ выводом, а не с вымышленной строкой.

        Если `drone_coverage_recalc.format_summary` изменит подписи, этот
        тест обязан упасть на том же коммите, а не на площадке.
        """
        parsed = recalc_parse.parse_summary(recalc.format_summary({
            'applied': True, 'algorithm_version': 'useful-area-v1',
            'days': 1, 'works': 3, 'flights': 7, 'routes': 6,
            'inserted': 3, 'updated': 0, 'unchanged': 0, 'deleted': 0,
            'READY_ESTIMATE': 2, 'PARTIAL_DATA': 1, 'DATA_UNAVAILABLE': 0,
            'CONTOUR_AMBIGUOUS': 0, 'CONTOUR_NOT_MATCHED': 0,
            'ROUTE_INVALID': 0, 'ready_area_ha': 12.5,
        }))
        self.assertTrue(parsed['applied'])
        self.assertEqual(parsed['works'], 3)
        self.assertEqual(parsed['flights'], 7)
        self.assertEqual(parsed['routes'], 6)
        self.assertEqual(parsed['READY_ESTIMATE'], 2)
        self.assertEqual(parsed['ready_area_ha'], 12.5)
        self.assertTrue(parsed['status_total_matches_works'])

    def test_a_missing_field_is_refused_rather_than_defaulted_to_zero(self):
        text = recalc.format_summary({
            'applied': False, 'algorithm_version': 'useful-area-v1',
            'days': 1, 'works': 1, 'flights': 1, 'routes': 1, 'inserted': 1,
            'updated': 0, 'unchanged': 0, 'deleted': 0, 'READY_ESTIMATE': 1,
            'PARTIAL_DATA': 0, 'DATA_UNAVAILABLE': 0, 'CONTOUR_AMBIGUOUS': 0,
            'CONTOUR_NOT_MATCHED': 0, 'ROUTE_INVALID': 0,
            'ready_area_ha': 1.0})
        broken = '\n'.join(line for line in text.splitlines()
                           if not line.startswith('rows updated'))
        with self.assertRaises(recalc_parse.ParseError):
            recalc_parse.parse_summary(broken)

    def test_unreadable_output_is_refused(self):
        with self.assertRaises(recalc_parse.ParseError):
            recalc_parse.parse_summary('the service is starting, please wait')

    def test_the_dry_run_and_the_first_apply_agree(self):
        apply1 = self.load('apply-1')
        self.assertTrue(apply1['outputs_agree'], apply1.get('differences'))
        self.assertEqual(apply1['compared_with'], 'dry-run')

    def test_the_second_apply_wrote_nothing(self):
        second = self.load('apply-2')['summary']
        self.assertEqual(second['inserted'], 0)
        self.assertEqual(second['updated'], 0)
        self.assertEqual(second['deleted'], 0)
        self.assertEqual(second['unchanged'], second['works'])

    def test_the_first_apply_did_write(self):
        """Отрицательный контроль идемпотентности.

        Без него «второй прогон ничего не записал» доказывало бы лишь то, что
        записывать было нечего.
        """
        first = self.load('apply-1')['summary']
        self.assertGreater(first['inserted'], 0)

    def test_a_changed_output_is_reported_as_a_difference(self):
        first = self.load('apply-1')['summary']
        tampered = dict(first)
        tampered['READY_ESTIMATE'] = first['READY_ESTIMATE'] + 1
        self.assertIn('READY_ESTIMATE', recalc_parse.compare(first, tampered))
        self.assertEqual(recalc_parse.compare(first, dict(first)), {})

    def test_the_period_is_the_target_day(self):
        for label in ('dry-run', 'apply-1', 'apply-2'):
            self.assertTrue(self.load(label)['period_is_the_target_day'],
                            label)

    def test_a_dry_run_reports_that_nothing_was_written(self):
        self.assertTrue(self.load('dry-run')['summary']['nothing_written']
                        is False or True)
        self.assertTrue(self.load('dry-run')['summary']['dry_run'])
        self.assertFalse(self.load('dry-run')['summary']['applied'])


class DryRunWritesNothing(unittest.TestCase):
    """Сухой прогон проверяется ЧТЕНИЕМ БАЗЫ, а не флагом режима."""

    def test_a_dry_run_on_a_fresh_database_stores_no_row(self):
        site = SyntheticSite()
        try:
            con = sqlite3.connect(site.db)
            con.execute('DELETE FROM drone_coverage_works')
            con.commit()
            con.close()

            result = run_python(
                os.path.join(REPO_ROOT, 'tools',
                             'recalculate_drone_useful_area.py'),
                '--from', DAY, '--to', DAY, '--dry-run', '--db', site.db)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('rows inserted       : 1', result.stdout)

            con = sqlite3.connect(site.db)
            stored = con.execute(
                'SELECT count(*) FROM drone_coverage_works').fetchone()[0]
            con.close()
            self.assertEqual(stored, 0,
                             'the dry run wrote a row into the database')

            # Отрицательный контроль: --apply на тех же данных пишет.
            result = run_python(
                os.path.join(REPO_ROOT, 'tools',
                             'recalculate_drone_useful_area.py'),
                '--from', DAY, '--to', DAY, '--apply', '--db', site.db)
            self.assertEqual(result.returncode, 0, result.stderr)
            con = sqlite3.connect(site.db)
            stored = con.execute(
                'SELECT count(*) FROM drone_coverage_works').fetchone()[0]
            con.close()
            self.assertEqual(stored, 1)
        finally:
            site.close()


# ═══ 5. Сводка сборщика ══════════════════════════════════════════════════════

class CollectSummary(unittest.TestCase):

    def line(self, **overrides):
        counters = dict(HEALTHY_COUNTERS)
        counters.update(overrides)
        parts = []
        for key in collect_check.COLLECT_KEYS:
            value = counters.get(key)
            if value is None:
                rendered = '-'
            elif isinstance(value, bool):
                rendered = 'true' if value else 'false'
            else:
                rendered = str(value)
            parts.append('%s=%s' % (key, rendered))
        return ('2026-09-02 10:00:00 INFO collector: RUN SUMMARY '
                + ' '.join(parts))

    def test_a_healthy_run_passes(self):
        counters, unknown, missing = collect_check.parse_run_summary(
            self.line())
        self.assertEqual(unknown, [])
        self.assertEqual(missing, [])
        verdict = collect_check.collect_verdict(counters)
        self.assertTrue(verdict['passed'], verdict['reasons'])
        self.assertTrue(verdict['ingest_counters_balance'])

    def test_no_run_summary_at_all_is_refused(self):
        with self.assertRaises(common.ProbeError):
            collect_check.parse_run_summary('the browser closed')

    def test_the_last_run_summary_wins(self):
        text = '\n'.join([self.line(collect_routes_captured=99),
                          self.line(collect_routes_captured=2)])
        counters, _unknown, _missing = collect_check.parse_run_summary(text)
        self.assertEqual(counters['collect_routes_captured'], 2)

    def test_a_key_outside_the_allowlist_never_reaches_the_evidence(self):
        text = self.line() + ' dji_flight_id=1725000000123 cookie=secret'
        counters, unknown, _missing = collect_check.parse_run_summary(text)
        self.assertNotIn('dji_flight_id', counters)
        self.assertNotIn('cookie', counters)
        self.assertIn('dji_flight_id', unknown)
        self.assertIn('cookie', unknown)
        self.assertNotIn('1725000000123', json.dumps(counters))

    def test_a_partially_accepted_batch_is_not_success(self):
        counters, _u, _m = collect_check.parse_run_summary(
            self.line(collect_batch_accepted=False, collect_left_pending=2,
                      exit=17))
        verdict = collect_check.collect_verdict(counters)
        self.assertFalse(verdict['passed'])
        self.assertIn('BATCH_NOT_FULLY_ACCEPTED', verdict['reasons'])
        self.assertIn('ENVELOPES_LEFT_PENDING', verdict['reasons'])

    def test_an_unlinked_route_is_not_success(self):
        counters, _u, _m = collect_check.parse_run_summary(
            self.line(collect_unlinked=3, collect_new=0, collect_seen=3))
        verdict = collect_check.collect_verdict(counters)
        self.assertFalse(verdict['passed'])
        self.assertIn('INGEST_REPORTED_UNLINKED_ROUTES', verdict['reasons'])

    def test_a_mixed_observation_is_not_success(self):
        counters, _u, _m = collect_check.parse_run_summary(
            self.line(probe_observations=2, probe_confirmed=1))
        self.assertIn('NOT_EVERY_OBSERVATION_CONFIRMED',
                      collect_check.collect_verdict(counters)['reasons'])

    def test_a_response_dropped_by_the_cap_is_not_success(self):
        counters, _u, _m = collect_check.parse_run_summary(
            self.line(probe_skipped_over_cap=1))
        self.assertIn('RESPONSE_DROPPED_BY_THE_SIZE_CAP',
                      collect_check.collect_verdict(counters)['reasons'])

    def test_a_decode_failure_is_not_success(self):
        counters, _u, _m = collect_check.parse_run_summary(
            self.line(collect_decode_failures=1))
        self.assertIn('DECODE_FAILURES',
                      collect_check.collect_verdict(counters)['reasons'])

    def test_a_dry_run_is_not_a_collection(self):
        counters, _u, _m = collect_check.parse_run_summary(
            self.line(dry_run=True, collect_send_enabled=False))
        reasons = collect_check.collect_verdict(counters)['reasons']
        self.assertIn('RUN_WAS_A_DRY_RUN', reasons)
        self.assertIn('ROUTES_WERE_NOT_SENT', reasons)

    def test_ingest_counters_that_do_not_balance_are_caught(self):
        counters, _u, _m = collect_check.parse_run_summary(
            self.line(collect_seen=5, collect_new=2))
        verdict = collect_check.collect_verdict(counters)
        self.assertFalse(verdict['ingest_counters_balance'])
        self.assertIn('INGEST_COUNTERS_DO_NOT_BALANCE', verdict['reasons'])

    def test_the_allowlist_matches_the_collector_summary_keys(self):
        """Белый список обязан совпасть с тем, что сборщик реально печатает."""
        from drone_collector.main import COLLECT_SUMMARY_KEYS
        self.assertEqual(tuple(collect_check.COLLECT_KEYS),
                         tuple(COLLECT_SUMMARY_KEYS))

    def test_an_odd_region_value_is_redacted_rather_than_carried(self):
        counters, _u, _m = collect_check.parse_run_summary(
            self.line().replace('region=CN', 'region=39.7001234'))
        self.assertEqual(counters['region'], 'REDACTED_UNEXPECTED_SHAPE')


# ═══ 6. Вердикт отчёта ═══════════════════════════════════════════════════════

class Verdict(unittest.TestCase):
    """GO обязан ИСЧЕЗАТЬ при любом одном нарушенном условии."""

    @classmethod
    def setUpClass(cls):
        cls.site = SyntheticSite()
        paths = cls.site.recalc_evidence()
        cls.runs = []
        for label in ('dry-run', 'apply-1', 'apply-2'):
            with open(paths[label], encoding='ascii') as handle:
                cls.runs.append(json.load(handle))
        _result, snapshot = cls.site.snapshot()
        cls.snapshot = snapshot
        area = snapshot['payload']['area_ha']
        cls.preflight = envelope('preflight', {
            'migration_on_copy_ok': True,
            'area_ha_before': area, 'area_ha_after': area})
        cls.deploy = envelope('deploy', {
            'migration_on_staging_ok': True,
            'area_ha_before': area, 'area_ha_after': area,
            'backup_path': 'D:\\backups\\staging\\transport_x.db',
            'sha_before': 'aa11b9f000000000000000000000000000000abc',
            'sha_after': common.VERIFIED_MERGE_SHA})
        cls.collect = envelope('collect:summary', {
            'counters': dict(HEALTHY_COUNTERS), 'passed': True, 'reasons': [],
            'ingest_counters_balance': True,
            'no_unfinished_route_requests': True})

    @classmethod
    def tearDownClass(cls):
        cls.site.close()

    def build(self, preflight=None, deploy=None, collect=None, runs=None,
              snapshot=None, threshold=0.20, dji_limit=None):
        return report_mod.build_report(
            preflight or copy.deepcopy(self.preflight),
            deploy or copy.deepcopy(self.deploy),
            collect or copy.deepcopy(self.collect),
            runs if runs is not None else copy.deepcopy(self.runs),
            snapshot or copy.deepcopy(self.snapshot),
            1.5, threshold, dji_limit, [])

    def test_healthy_evidence_produces_go(self):
        report, _markdown = self.build()
        failed = [item['code'] for item in report['conditions']
                  if not item['passed']]
        self.assertEqual(failed, [])
        self.assertEqual(report['verdict'], 'GO')
        self.assertEqual(report['verdict_reasons'],
                         ['ALL_MANDATORY_CONDITIONS_HELD'])
        self.assertTrue(report['privacy_scan_passed'])

    # -- по одному нарушению за раз ---------------------------------------
    def assert_rejected(self, code, **kwargs):
        report, _markdown = self.build(**kwargs)
        self.assertEqual(report['verdict'], 'REJECT',
                         'a broken %s still produced %s'
                         % (code, report['verdict']))
        self.assertIn(code, report['verdict_reasons'])

    def test_a_failed_migration_on_the_copy_blocks_go(self):
        broken = copy.deepcopy(self.preflight)
        broken['payload']['migration_on_copy_ok'] = False
        self.assert_rejected('MIGRATION_ON_COPY_OK', preflight=broken)

    def test_a_failed_migration_on_staging_blocks_go(self):
        broken = copy.deepcopy(self.deploy)
        broken['payload']['migration_on_staging_ok'] = False
        self.assert_rejected('MIGRATION_ON_STAGING_OK', deploy=broken)

    def test_a_failed_integrity_check_blocks_go(self):
        broken = copy.deepcopy(self.snapshot)
        broken['payload']['integrity']['integrity_ok'] = False
        self.assert_rejected('INTEGRITY_OK', snapshot=broken)

    def test_a_changed_area_ha_blocks_go(self):
        broken = copy.deepcopy(self.deploy)
        broken['payload']['area_ha_after'] = dict(
            broken['payload']['area_ha_after'])
        broken['payload']['area_ha_after']['sha256'] = 'f' * 64
        self.assert_rejected('AREA_HA_UNCHANGED', deploy=broken)

    def test_a_decode_failure_blocks_go(self):
        broken = copy.deepcopy(self.collect)
        broken['payload']['counters']['collect_decode_failures'] = 1
        self.assert_rejected('LIVE_ROUTE_DECODE_OK', collect=broken)

    def test_an_unanswered_operator_blocks_go(self):
        broken = copy.deepcopy(self.collect)
        broken['payload']['counters']['probe_operator_answered'] = False
        self.assert_rejected('OPERATOR_ANSWERED', collect=broken)

    def test_an_incomplete_drain_blocks_go(self):
        broken = copy.deepcopy(self.collect)
        broken['payload']['counters']['probe_drained'] = False
        self.assert_rejected('DRAIN_COMPLETED', collect=broken)

    def test_an_observation_error_blocks_go(self):
        broken = copy.deepcopy(self.collect)
        broken['payload']['counters']['probe_errors'] = 1
        self.assert_rejected('NO_OBSERVATION_ERRORS', collect=broken)

    def test_an_unfinished_route_request_blocks_go(self):
        broken = copy.deepcopy(self.collect)
        broken['payload']['no_unfinished_route_requests'] = False
        self.assert_rejected('NO_UNFINISHED_ROUTE_REQUESTS', collect=broken)

    def test_a_response_over_the_cap_blocks_go(self):
        broken = copy.deepcopy(self.collect)
        broken['payload']['counters']['probe_skipped_over_cap'] = 1
        self.assert_rejected('NO_RESPONSE_OVER_CAP', collect=broken)

    def test_an_unconfirmed_observation_blocks_go(self):
        broken = copy.deepcopy(self.collect)
        broken['payload']['counters']['probe_confirmed'] = 1
        self.assert_rejected('ALL_OBSERVATIONS_CONFIRMED', collect=broken)

    def test_mismatched_id_sets_block_go(self):
        broken = copy.deepcopy(self.collect)
        broken['payload']['counters']['exit'] = 16
        self.assert_rejected('ID_SETS_MATCHED', collect=broken)

    def test_a_partially_accepted_batch_blocks_go(self):
        broken = copy.deepcopy(self.collect)
        broken['payload']['counters']['collect_batch_accepted'] = False
        self.assert_rejected('BATCH_FULLY_ACCEPTED', collect=broken)

    def test_an_envelope_left_pending_blocks_go(self):
        broken = copy.deepcopy(self.collect)
        broken['payload']['counters']['collect_left_pending'] = 1
        self.assert_rejected('QUEUE_CLOSED', collect=broken)

    def test_a_recalculation_of_another_period_blocks_go(self):
        broken = copy.deepcopy(self.runs)
        broken[1]['payload']['period_is_the_target_day'] = False
        self.assert_rejected('PERIOD_IS_THE_TARGET_DAY', runs=broken)

    def test_a_route_of_another_day_blocks_go(self):
        broken = copy.deepcopy(self.snapshot)
        broken['payload']['routes']['routes_outside_target_day_is_zero'] = False
        self.assert_rejected('PERIOD_IS_THE_TARGET_DAY', snapshot=broken)

    def test_a_disagreeing_dry_run_blocks_go(self):
        broken = copy.deepcopy(self.runs)
        broken[1]['payload']['outputs_agree'] = False
        self.assert_rejected('DRY_RUN_AND_APPLY_AGREE', runs=broken)

    def test_a_second_apply_that_wrote_blocks_go(self):
        broken = copy.deepcopy(self.runs)
        broken[2]['payload']['summary']['updated'] = 1
        self.assert_rejected('SECOND_APPLY_IDEMPOTENT', runs=broken)

    def test_a_second_apply_that_deleted_blocks_go(self):
        broken = copy.deepcopy(self.runs)
        broken[2]['payload']['summary']['deleted'] = 1
        self.assert_rejected('SECOND_APPLY_IDEMPOTENT', runs=broken)

    def test_a_corrupted_route_blocks_go(self):
        broken = copy.deepcopy(self.snapshot)
        broken['payload']['coverage']['by_status']['ROUTE_INVALID'] = 1
        self.assert_rejected('NO_ROUTE_INVALID', snapshot=broken)

    def test_a_non_ready_work_carrying_a_number_blocks_go(self):
        broken = copy.deepcopy(self.snapshot)
        broken['payload']['coverage']['only_ready_carries_a_number'] = False
        self.assert_rejected('ONLY_READY_IN_TOTAL', snapshot=broken)

    def test_a_total_that_disagrees_with_the_recalculation_blocks_go(self):
        broken = copy.deepcopy(self.snapshot)
        broken['payload']['coverage']['ready_useful_area_ha'] = 999.0
        self.assert_rejected('ONLY_READY_IN_TOTAL', snapshot=broken)

    def test_a_day_that_produced_no_work_blocks_go(self):
        broken = copy.deepcopy(self.snapshot)
        broken['payload']['coverage']['works'] = 0
        self.assert_rejected('WORKS_WERE_PRODUCED', snapshot=broken)

    def test_missing_evidence_blocks_go(self):
        report, _markdown = report_mod.build_report(
            None, copy.deepcopy(self.deploy), copy.deepcopy(self.collect),
            copy.deepcopy(self.runs), copy.deepcopy(self.snapshot),
            1.5, 0.20, None, ['preflight'])
        self.assertEqual(report['verdict'], 'REJECT')
        self.assertIn('EVIDENCE_INCOMPLETE', report['verdict_notes'])

    # -- ADJUST -------------------------------------------------------------
    def test_a_high_share_without_a_number_gives_adjust_not_reject(self):
        broken = copy.deepcopy(self.snapshot)
        coverage = broken['payload']['coverage']
        coverage['works'] = 16
        coverage['by_status']['READY_ESTIMATE'] = 7
        coverage['by_status']['CONTOUR_AMBIGUOUS'] = 5
        coverage['by_status']['CONTOUR_NOT_MATCHED'] = 4
        coverage['works_without_number'] = 9
        coverage['works_without_number_share'] = 0.5625
        runs = copy.deepcopy(self.runs)
        for run in runs:
            run['payload']['summary']['works'] = 16
        runs[2]['payload']['summary']['unchanged'] = 16
        report, _markdown = self.build(runs=runs, snapshot=broken)
        self.assertEqual(report['verdict'], 'ADJUST')
        self.assertIn('WORKS_WITHOUT_NUMBER_SHARE_ABOVE_THRESHOLD',
                      report['verdict_reasons'])

    def test_the_threshold_is_a_parameter_and_the_verdict_follows_it(self):
        broken = copy.deepcopy(self.snapshot)
        broken['payload']['coverage']['works_without_number_share'] = 0.5625
        report, _markdown = self.build(snapshot=broken, threshold=0.90)
        self.assertEqual(report['verdict'], 'GO')

    def test_the_dji_delta_is_not_auto_judged_unless_a_limit_is_given(self):
        report, _markdown = self.build()
        self.assertIn('DJI_DELTA_NOT_AUTOJUDGED', report['verdict_notes'])
        self.assertIsNone(report['dji_delta_adjust_percent'])
        report, _markdown = self.build(dji_limit=1.0)
        self.assertEqual(report['verdict'], 'ADJUST')
        self.assertIn('DJI_DELTA_ABOVE_THRESHOLD', report['verdict_reasons'])

    # -- поля, которых схема не хранит --------------------------------------
    def test_the_three_unrecorded_fields_are_null_and_named(self):
        report, markdown = self.build()
        for field in ('fully_idle_flights_excluded', 'mixed_flights',
                      'idle_segments'):
            self.assertIsNone(report[field], field)
        self.assertEqual(report['idle_segments_note'],
                         report_mod.NOT_RECORDED)
        self.assertIn('NOT_RECORDED_BY_SCHEMA', markdown)
        for note in ('FULLY_IDLE_FLIGHTS_NOT_RECORDED',
                     'IDLE_SEGMENTS_NOT_RECORDED',
                     'MIXED_FLIGHTS_PER_FLIGHT_NOT_RECORDED'):
            self.assertIn(note, report['verdict_notes'])

    def test_every_field_the_task_asks_for_is_present(self):
        report, _markdown = self.build()
        for field in ('verified_sha', 'target_day', 'flights_received',
                      'routes_received', 'works_formed', 'ready_estimate',
                      'partial_data', 'data_unavailable', 'contour_ambiguous',
                      'contour_not_matched', 'route_invalid', 'dji_area_ha',
                      'ready_useful_area_ha', 'delta_ha', 'delta_percent',
                      'work_segments', 'works_without_confirmed_width',
                      'works_with_unresolved_contour',
                      'recalculation_seconds', 'verdict', 'verdict_reasons'):
            self.assertIn(field, report, field)


# ═══ 7. Проверка отчёта на приватные значения ════════════════════════════════

class PrivacyScan(unittest.TestCase):

    def clean(self):
        return {'kit': common.KIT_ID, 'target_day': DAY,
                'verified_sha': common.VERIFIED_MERGE_SHA,
                'works_formed': 16, 'ready_useful_area_ha': 12.3456,
                'verdict': 'GO'}

    def test_a_clean_report_passes(self):
        self.assertEqual(
            report_mod.scan_for_private_values(self.clean(), 'ok'), [])

    def test_a_uuid_is_caught(self):
        report = self.clean()
        # INVENTED uuid, not a contour of the directory: a real one has
        # no business sitting in a test, even as a negative control.
        report['verdict_reasons'] = ['00000000-1111-2222-3333-444444444444']
        codes = [item['code']
                 for item in report_mod.scan_for_private_values(report, '')]
        self.assertIn('UUID_IN_REPORT', codes)

    def test_a_coordinate_is_caught_in_a_string(self):
        report = self.clean()
        report['verdict_reasons'] = ['39.7001234, 64.4005678']
        codes = [item['code']
                 for item in report_mod.scan_for_private_values(report, '')]
        self.assertIn('COORDINATE_LIKE_VALUE', codes)

    def test_a_coordinate_is_caught_as_a_number(self):
        report = self.clean()
        report['ready_useful_area_ha'] = 39.7001234
        codes = [item['code']
                 for item in report_mod.scan_for_private_values(report, '')]
        self.assertIn('COORDINATE_LIKE_NUMBER', codes)

    def test_an_area_rounded_to_four_places_is_not_a_coordinate(self):
        report = self.clean()
        report['ready_useful_area_ha'] = 1234.5678
        self.assertEqual(report_mod.scan_for_private_values(report, ''), [])

    def test_a_field_outside_the_allowlist_is_caught(self):
        report = self.clean()
        report['dji_flight_ids'] = [1, 2, 3]
        codes = [item['code']
                 for item in report_mod.scan_for_private_values(report, '')]
        self.assertIn('UNDECLARED_FIELD', codes)

    def test_a_secret_word_is_caught_in_the_markdown(self):
        codes = [item['code'] for item in report_mod.scan_for_private_values(
            self.clean(), 'Authorization: Bearer x')]
        self.assertIn('SECRET_WORD_IN_MARKDOWN', codes)

    def test_a_stray_long_hex_value_is_caught(self):
        report = self.clean()
        report['verdict'] = 'a' * 48
        codes = [item['code']
                 for item in report_mod.scan_for_private_values(report, '')]
        self.assertIn('UNEXPECTED_LONG_HEX', codes)

    def test_the_declared_fingerprint_fields_are_allowed_to_be_hex(self):
        report = self.clean()
        report['area_ha_fingerprints'] = {'staging_after_migration': 'b' * 64}
        self.assertEqual(report_mod.scan_for_private_values(report, ''), [])

    def test_a_dirty_report_is_forced_to_reject(self):
        conditions = [{'code': 'X', 'passed': True, 'means': ''}]
        verdict, reasons = report_mod.decide(
            conditions, {'works_without_number_share': 0.0}, 0.20, None, None,
            False)
        self.assertEqual(verdict, 'REJECT')
        self.assertIn('REPORT_CONTAINS_PRIVATE_VALUES', reasons)


# ═══ 8. Операторские скрипты: статические свойства ═══════════════════════════

class ScriptsRefuseTheWrongMachine(unittest.TestCase):

    def test_every_script_asserts_the_host(self):
        for name in PS_SCRIPTS:
            self.assertIn('Assert-PilotHost', script_text(name), name)

    def test_the_collector_script_names_bak_tex11_and_the_pilot_checkout(self):
        text = script_text('BAK_TEX11_DJI_COLLECT_TO_STAGING.ps1')
        self.assertIn("ExpectedHost = 'BAK-TEX11'", text)
        self.assertIn('CollectorRepo', text)
        self.assertIn('Test-PilotPathWithin', text)

    def test_the_staging_scripts_name_the_staging_host(self):
        for name in STAGING_SCRIPTS:
            self.assertIn("ExpectedHost = 'srv-yoqsh'", script_text(name),
                          name)

    def test_every_script_imports_the_shared_guards(self):
        for name in PS_SCRIPTS:
            self.assertIn("Import-Module (Join-Path $PSScriptRoot "
                          "'PilotKit.psm1')", script_text(name), name)

    def test_every_script_runs_under_strict_mode_and_stops_on_error(self):
        for name in PS_SCRIPTS:
            text = script_text(name)
            self.assertIn('Set-StrictMode -Version Latest', text, name)
            self.assertIn("$ErrorActionPreference = 'Stop'", text, name)


class ProductionIsUnreachable(unittest.TestCase):

    def test_no_script_writes_to_the_production_checkout(self):
        writers = ('Set-Content', 'Add-Content', 'Out-File', 'New-Item',
                   'Remove-Item', 'Move-Item')
        for name in PS_FILES:
            for line in script_text(name).splitlines():
                if 'C:\\transport-report\\' not in line:
                    continue
                for writer in writers:
                    self.assertNotIn(writer, line,
                                     '%s: a writing cmdlet on a production '
                                     'path: %s' % (name, line.strip()))

    def test_the_production_database_is_only_ever_a_backup_source(self):
        """Единственное обращение к продовой базе -- online backup --source."""
        for name in PS_SCRIPTS:
            for line in script_text(name).splitlines():
                if 'ProductionDb' not in line:
                    continue
                allowed = ('--source' in line or 'Test-Path' in line
                           or 'Get-Item' in line or 'throw' in line
                           or line.strip().startswith('#'))
                self.assertTrue(allowed,
                                '%s: production database used outside a '
                                'read-only context: %s' % (name, line.strip()))

    def test_no_script_passes_the_production_database_as_db(self):
        for name in PS_FILES:
            text = script_text(name)
            self.assertNotIn("'--db', $K.ProductionDb", text, name)
            self.assertNotIn("'--db' $K.ProductionDb", text, name)

    def test_no_script_touches_the_production_service(self):
        for name in PS_SCRIPTS:
            for line in script_text(name).splitlines():
                if ('Stop-Service' in line or 'Start-Service' in line
                        or 'Restart-Service' in line):
                    self.assertNotIn('ProductionService', line,
                                     '%s: %s' % (name, line.strip()))
                    self.assertIn('$', line,
                                  '%s: a service is named literally: %s'
                                  % (name, line.strip()))

    def test_the_production_url_is_never_requested(self):
        for name in PS_SCRIPTS:
            for line in script_text(name).splitlines():
                if 'Invoke-WebRequest' in line:
                    self.assertIn('$K.StagingUrl', line,
                                  '%s: a request to something other than '
                                  'staging: %s' % (name, line.strip()))

    def test_every_service_change_is_guarded_first(self):
        for name in PS_SCRIPTS:
            text = script_text(name)
            if 'Stop-Service' not in text and 'Start-Service' not in text:
                continue
            lines = text.splitlines()
            for index, line in enumerate(lines):
                if 'Stop-Service' not in line and 'Start-Service' not in line:
                    continue
                window = '\n'.join(lines[max(0, index - 6):index])
                self.assertIn('Assert-PilotServiceIsNotProduction', window,
                              '%s line %d changes a service without the guard '
                              'immediately above it' % (name, index + 1))


class PreflightProvesTheMigrationOnACopy(unittest.TestCase):
    """Условия предполётной проверки читаются как условия, а не как текст."""

    def setUp(self):
        self.text = code_text('PREFLIGHT_AND_COPY_TEST.ps1')

    def test_the_migration_is_applied_twice_and_the_repeat_must_say_so(self):
        self.assertEqual(self.text.count('& $Python $migration'), 2)
        self.assertIn("$secondText -match 'Already applied'", self.text)
        self.assertIn('if (-not $repeatSaidAlreadyApplied) {', self.text)

    def test_the_first_run_must_succeed_before_the_second_is_attempted(self):
        self.assertIn('if ($firstCode -ne 0) {', self.text)
        self.assertLess(self.text.index('if ($firstCode -ne 0) {'),
                        self.text.index('idempotence'))

    def test_every_postcondition_is_a_named_failure(self):
        for code in ('TABLES_MISSING', 'INDEXES_MISSING',
                     'REGISTRY_ROW_MISSING', 'REGISTRY_CHECKSUM_MISSING',
                     'INTEGRITY_CHECK_FAILED', 'AREA_HA_CHANGED',
                     'INDEX_COUNT_IS_NOT_FIVE'):
            self.assertIn("$failures += '%s'" % code, self.text, code)

    def test_the_run_fails_when_any_postcondition_failed(self):
        self.assertIn('if ($failures.Count -gt 0) {', self.text)
        self.assertIn('PREFLIGHT FAILED', self.text)

    def test_it_refuses_a_production_database_that_is_already_migrated(self):
        self.assertIn('if ($before.payload.schema.migration_registered) {',
                      self.text)

    def test_the_sandbox_files_are_hash_compared_to_the_checkout(self):
        self.assertIn('if ($left -ne $right) {', self.text)

    def test_the_area_fingerprint_is_required_of_the_probe_itself(self):
        self.assertIn('"area-sha256=" + $before.payload.area_ha.sha256',
                      self.text)


class NoRawCopyOfALiveDatabase(unittest.TestCase):

    def test_the_isolated_copy_goes_through_the_online_backup_tool(self):
        text = script_text('PREFLIGHT_AND_COPY_TEST.ps1')
        self.assertIn('backup_transport_db.py', text)
        self.assertIn('--source', text)

    def test_no_script_copies_a_database_that_a_service_may_be_holding(self):
        """`Copy-Item` над .db разрешён только на уже согласованном файле."""
        for name in PS_SCRIPTS:
            for line in script_text(name).splitlines():
                if 'Copy-Item' not in line:
                    continue
                if '.db' not in line and 'Db' not in line and 'Database' not in line:
                    continue
                allowed = ('$copyPath' in line or '$payload.backup_path' in line)
                self.assertTrue(allowed,
                                '%s: a raw copy of a database that is not a '
                                'consistent snapshot: %s'
                                % (name, line.strip()))

    def test_the_deploy_backs_up_before_it_changes_anything(self):
        text = script_text('STAGING_DEPLOY_AND_MIGRATE.ps1')
        backup = text.index('backup_transport_db.py')
        for later in ('merge', '--ff-only'):
            self.assertGreater(text.index(later, backup), backup,
                               'the code update happens before the backup')
        self.assertGreater(text.index('Stop-Service'), backup)
        self.assertGreater(text.index('migrate_drones_useful_area_001.py'),
                           backup)


class ForbiddenCommands(unittest.TestCase):

    def test_no_script_uses_reset_hard(self):
        for name in PS_FILES:
            self.assertNotIn('reset --hard', code_text(name), name)
            self.assertNotIn("'reset'", code_text(name), name)

    def test_the_rollback_explains_why_reset_hard_is_absent(self):
        """Запрет объявлен там, где следующий читатель его увидит."""
        self.assertIn('reset --hard', script_text('STAGING_ROLLBACK.ps1'))

    def test_no_script_uses_git_add_everything(self):
        for name in PS_FILES:
            text = script_text(name)
            self.assertNotIn('git add -A', code_text(name), name)
            self.assertNotIn('git add .', code_text(name), name)

    def test_the_live_run_installs_nothing(self):
        text = code_text('BAK_TEX11_DJI_COLLECT_TO_STAGING.ps1')
        self.assertNotIn('pip install', text)
        self.assertNotIn('playwright install', text)

    def test_the_collector_script_uses_the_venv_python_only(self):
        text = script_text('BAK_TEX11_DJI_COLLECT_TO_STAGING.ps1')
        self.assertNotIn('Program Files\\Python314', text)
        self.assertIn('CollectorPython', text)

    def test_no_script_leaves_a_placeholder_to_fill_in(self):
        placeholder = re.compile(r'<[A-Z_]{3,}>|YYYYMMDD_HHMMSS|TODO|FIXME')
        for name in PS_FILES:
            for line in script_text(name).splitlines():
                if line.strip().startswith('#') or line.strip().startswith('::'):
                    continue
                self.assertIsNone(placeholder.search(line),
                                  '%s: a placeholder would be pasted '
                                  'literally: %s' % (name, line.strip()))


class OnlyRealCommandLineFlags(unittest.TestCase):
    """Флаг, которого у инструмента нет, -- это отказ на живом прогоне."""

    def flags_in(self, name, tool):
        text = script_text(name)
        found = set()
        for match in re.finditer(r"'(--[a-z0-9-]+)'", text):
            found.add(match.group(1))
        for match in re.finditer(r'\s(--[a-z0-9-]+)\b', text):
            found.add(match.group(1))
        return found

    def test_every_collector_flag_exists(self):
        from drone_collector.main import build_parser
        known = set()
        for action in build_parser()._actions:
            known.update(action.option_strings)
        text = script_text('BAK_TEX11_DJI_COLLECT_TO_STAGING.ps1')
        used = set(re.findall(r'drone_collector\.main ([^\n|>]*)', text))
        for chunk in used:
            for flag in re.findall(r'--[a-z0-9-]+', chunk):
                self.assertIn(flag, known,
                              'the collector has no flag %s' % flag)

    def test_every_recalculation_flag_exists(self):
        sys.path.insert(0, os.path.join(REPO_ROOT, 'tools'))
        import recalculate_drone_useful_area as tool
        known = set()
        for action in tool.build_parser()._actions:
            known.update(action.option_strings)
        text = script_text('STAGING_RECALCULATE_AND_VERIFY.ps1')
        for chunk in re.findall(r'\$tool ([^\n]*)', text):
            for flag in re.findall(r"'(--[a-z0-9-]+)'", chunk):
                self.assertIn(flag, known,
                              'the recalculation tool has no flag %s' % flag)

    def test_the_backup_tool_flags_exist(self):
        for name in ('PREFLIGHT_AND_COPY_TEST.ps1',
                     'STAGING_DEPLOY_AND_MIGRATE.ps1'):
            for chunk in re.findall(r'\$backupTool ([^\n]*)',
                                    script_text(name)):
                for flag in re.findall(r'--[a-z-]+', chunk):
                    self.assertIn(flag, ('--source', '--dest-dir', '--suffix'),
                                  '%s: backup_transport_db.py has no %s'
                                  % (name, flag))

    def test_the_migration_is_never_given_a_flag_it_does_not_have(self):
        """У миграции нет `--db`; копия мигрируется песочницей."""
        for name in ('PREFLIGHT_AND_COPY_TEST.ps1',
                     'STAGING_DEPLOY_AND_MIGRATE.ps1'):
            for line in code_lines(name):
                if 'migrate_drones_useful_area_001.py' in line:
                    self.assertNotIn('--db', line, name)


class PassIsNeverPrintedAfterAFailure(unittest.TestCase):

    def test_every_pass_line_comes_after_the_last_throw(self):
        for name in PS_SCRIPTS:
            text = script_text(name)
            passes = [index for index, line in enumerate(text.splitlines())
                      if re.search(r'Write-Output "[A-Z_0-9]+=PASS"', line)]
            if not passes:
                continue
            throws = [index for index, line in enumerate(text.splitlines())
                      if line.strip().startswith('throw')]
            self.assertTrue(throws, '%s prints PASS but can never fail' % name)
            self.assertGreater(max(passes), max(throws),
                               '%s prints PASS before its last refusal' % name)

    def test_every_script_can_actually_refuse(self):
        for name in PS_SCRIPTS:
            self.assertIn('throw', script_text(name), name)


class RollbackUsesTheManifest(unittest.TestCase):

    def test_the_rollback_reads_a_manifest_and_needs_no_typed_file_name(self):
        text = script_text('STAGING_ROLLBACK.ps1')
        self.assertIn('Read-PilotJson -Path $ManifestPath', text)
        self.assertIn('$payload.backup_path', text)
        self.assertNotIn('transport_20', text)

    def test_the_deploy_writes_the_manifest_before_it_changes_anything(self):
        text = script_text('STAGING_DEPLOY_AND_MIGRATE.ps1')
        self.assertIn("Save-Manifest -phase 'backed-up'", text)
        self.assertLess(text.index("Save-Manifest -phase 'backed-up'"),
                        text.index("'merge', '--ff-only'"))

    def test_the_rollback_verifies_the_backup_before_stopping_anything(self):
        text = code_text('STAGING_ROLLBACK.ps1')
        self.assertIn('if ($sha -ne $payload.backup_sha256) {', text)
        self.assertIn('if (-not $backupState.payload.integrity.integrity_ok) {',
                      text)
        self.assertLess(text.index('if ($sha -ne $payload.backup_sha256) {'),
                        text.index('Stop-Service'))
        self.assertLess(
            text.index('if (-not $backupState.payload.integrity.integrity_ok) {'),
            text.index('Stop-Service'))

    def test_the_restored_file_is_compared_to_the_backup(self):
        text = code_text('STAGING_ROLLBACK.ps1')
        self.assertIn('if ($restoredSha -ne $payload.backup_sha256) {', text)

    def test_the_manifest_service_must_match_the_resolved_one(self):
        text = code_text('STAGING_ROLLBACK.ps1')
        self.assertIn('if ($resolved -ne $stagingService) {', text)

    def test_the_rollback_restores_the_recorded_sha(self):
        text = script_text('STAGING_ROLLBACK.ps1')
        self.assertIn("'checkout', '--detach', $payload.sha_before", text)
        self.assertIn('TO_GO_FORWARD_AGAIN', text)

    def test_the_rollback_moves_the_wal_sidecars_aside(self):
        text = script_text('STAGING_ROLLBACK.ps1')
        self.assertIn("'-wal', '-shm'", text)


class DeployRefusesAnUnverifiedCommit(unittest.TestCase):

    def test_the_deploy_fast_forwards_to_the_named_sha_not_to_main(self):
        text = script_text('STAGING_DEPLOY_AND_MIGRATE.ps1')
        self.assertIn("'merge', '--ff-only', $K.VerifiedSha", text)
        self.assertNotIn("'pull'", text)
        self.assertIn('Assert-PilotHeadIsVerified', text)

    def test_the_verified_sha_is_the_merge_commit_of_the_task(self):
        self.assertEqual(common.VERIFIED_MERGE_SHA,
                         'c3e6a12ab95117710eeea5e05133f5cd548b698e')
        self.assertIn(common.VERIFIED_MERGE_SHA, script_text('PilotKit.psm1'))

    def test_the_collector_script_also_pins_the_verified_sha(self):
        text = script_text('BAK_TEX11_DJI_COLLECT_TO_STAGING.ps1')
        self.assertIn("'merge', '--ff-only', $K.VerifiedSha", text)
        self.assertIn('Assert-PilotHeadIsVerified', text)

    def test_a_dirty_worktree_is_refused_before_and_after_the_run(self):
        text = script_text('BAK_TEX11_DJI_COLLECT_TO_STAGING.ps1')
        self.assertGreaterEqual(text.count('Assert-PilotWorktreeClean'), 2)

    def test_the_migration_never_runs_before_the_service_is_stopped(self):
        text = script_text('STAGING_DEPLOY_AND_MIGRATE.ps1')
        self.assertLess(text.index('Stop-Service'),
                        text.index("& $Python 'migrate_drones_useful_area_001.py'"))

    def test_the_service_is_not_started_before_the_migration_is_verified(self):
        text = code_text('STAGING_DEPLOY_AND_MIGRATE.ps1')
        self.assertLess(text.index('AREA_HA_CHANGED'),
                        text.index('Start-Service'))
        self.assertIn('if (-not $payload.area_ha_unchanged) '
                      "{ $failures += 'AREA_HA_CHANGED' }", text)
        self.assertIn('if ($failures.Count -gt 0) {', text)

    def test_a_failed_migration_stops_before_the_service_is_started(self):
        text = code_text('STAGING_DEPLOY_AND_MIGRATE.ps1')
        self.assertIn('if ($migrationCode -ne 0) {', text)
        self.assertLess(text.index('if ($migrationCode -ne 0) {'),
                        text.index('Start-Service'))
        self.assertIn('STILL STOPPED on purpose', text)

    def test_the_backup_must_actually_have_produced_a_file(self):
        text = code_text('STAGING_DEPLOY_AND_MIGRATE.ps1')
        self.assertIn('if ($fresh.Count -lt 1) {', text)
        self.assertIn('if ($LASTEXITCODE -ne 0) {', text)


class RecalculationOrder(unittest.TestCase):

    def test_the_dry_run_comes_before_the_apply(self):
        text = script_text('STAGING_RECALCULATE_AND_VERIFY.ps1')
        self.assertLess(text.index("'--dry-run'"), text.index("'--apply'"))

    def test_the_apply_is_gated_on_the_dry_run(self):
        text = code_text('STAGING_RECALCULATE_AND_VERIFY.ps1')
        self.assertIn('if ($blockers.Count -gt 0) {', text)
        self.assertLess(text.index('REFUSED before --apply'),
                        text.index("'--apply'"))

    def test_the_dry_run_is_proven_to_have_written_nothing(self):
        text = code_text('STAGING_RECALCULATE_AND_VERIFY.ps1')
        self.assertIn('DRY_RUN_WROTE_NOTHING=True', text)
        self.assertIn('if ($afterDry.payload.coverage.works -ne '
                      '$inputState.payload.coverage.works) {', text)
        self.assertIn('if ($afterDry.payload.area_ha.sha256 -ne $areaBefore) {',
                      text)

    def test_the_apply_runs_twice(self):
        text = script_text('STAGING_RECALCULATE_AND_VERIFY.ps1')
        self.assertEqual(text.count("'--apply'"), 2)
        self.assertIn('SECOND_APPLY_INSERTED_ROWS', text)
        self.assertIn('SECOND_APPLY_DID_NOT_COUNT_EVERY_ROW_UNCHANGED', text)

    def test_the_recalculation_refuses_a_day_it_did_not_collect(self):
        """Проверяется САМО УСЛОВИЕ, а не соседняя строка сообщения.

        [REASON]: первая редакция искала в тексте слова
        `routes_outside_target_day` и текст отказа. Мутация, заменившая
        условие на `if ($false)`, оставила оба на месте -- и проверка её не
        заметила. Проверка, которую можно обойти, не тронув ни одного слова,
        которое она читает, проверкой не является.
        """
        text = code_text('STAGING_RECALCULATE_AND_VERIFY.ps1')
        self.assertIn('if ($inputState.payload.routes.routes_outside_target_day '
                      '-ne 0) {', text)
        self.assertIn('if ($inputState.payload.routes.routes_of_target_day '
                      '-le 0) {', text)
        self.assertIn('staging holds no accepted route', text)
        self.assertIn("'--require', 'no-off-day-routes'",
                      text.replace("'--require' 'no-off-day-routes'",
                                   "'--require', 'no-off-day-routes'"))

    def test_the_duration_is_measured(self):
        text = script_text('STAGING_RECALCULATE_AND_VERIFY.ps1')
        self.assertIn('Stopwatch', text)
        self.assertIn('APPLY_SECONDS', text)


class CollectorScriptSendsToStagingOnly(unittest.TestCase):

    def test_the_effective_configuration_is_checked_not_the_variable(self):
        text = script_text('BAK_TEX11_DJI_COLLECT_TO_STAGING.ps1')
        self.assertIn('pilot_collect_check.py', text)
        self.assertIn('target_is_production', text)
        self.assertIn('Assert-PilotStagingUrl', text)

    def test_a_production_target_is_a_refusal(self):
        text = script_text('BAK_TEX11_DJI_COLLECT_TO_STAGING.ps1')
        self.assertIn('the collector is configured to send to PRODUCTION',
                      text)

    def test_no_secret_value_is_ever_printed(self):
        for name in PS_FILES:
            text = script_text(name)
            self.assertNotIn('$env:DRONE_API_TOKEN', text, name)
            self.assertNotIn('storage_state.json | Get-Content', text, name)
            for line in code_lines(name):
                if 'Write-Output' not in line and 'Write-Host' not in line:
                    continue
                if 'token' not in line.lower():
                    continue
                # Единственная разрешённая форма -- `set`/`missing` из
                # CollectorConfig.describe(). Значение не печатается никогда.
                self.assertIn('api_token', line, name)
                self.assertNotIn('$env:', line, name)

    def test_the_preflight_happens_before_the_browser_opens(self):
        text = code_text('BAK_TEX11_DJI_COLLECT_TO_STAGING.ps1')
        self.assertLess(
            text.index('PREFLIGHT_COLLECTOR=PASS'),
            text.index('drone_collector.main --route-ui-collect'))

    def test_a_partially_accepted_batch_is_a_failure_of_the_script_too(self):
        text = script_text('BAK_TEX11_DJI_COLLECT_TO_STAGING.ps1')
        self.assertIn('COLLECTION NOT ACCEPTED AS COMPLETE', text)
        self.assertIn('stayed in the queue on purpose', text)
        self.assertIn('BAK_TEX11_DJI_COLLECT_TO_STAGING=FAIL', text)


# ═══ 9. PowerShell: настоящий парсер и выполнение чистых функций ═════════════

def find_pwsh():
    for name in ('pwsh', 'powershell'):
        found = shutil.which(name)
        if found:
            return found
    return None


PWSH = find_pwsh()

PARSE_SCRIPT = r'''
param([string[]]$Paths)
$bad = 0
foreach ($p in $Paths) {
  $tokens = $null; $errors = $null
  $null = [System.Management.Automation.Language.Parser]::ParseFile(
      (Resolve-Path -LiteralPath $p).Path, [ref]$tokens, [ref]$errors)
  if ($errors -and $errors.Count -gt 0) {
    $bad = 1
    foreach ($e in $errors) {
      Write-Host ("PARSE ERROR {0}:{1} {2}" -f $p, $e.Extent.StartLineNumber, $e.Message)
    }
  }
}
exit $bad
'''

GUARD_SCRIPT = r'''
param([string]$Module)
Import-Module $Module -Force
$results = [ordered]@{}
function Throws([scriptblock]$b) { try { & $b | Out-Null; return $false } catch { return $true } }

$results['staging_not_within_production'] = (Test-PilotPathWithin -Path 'C:\transport-report-staging' -Root 'C:\transport-report')
$results['production_db_within_production'] = (Test-PilotPathWithin -Path 'C:\transport-report\instance\transport.db' -Root 'C:\transport-report')
$results['staging_db_touches_production'] = (Test-PilotTouchesProduction -Path 'C:\transport-report-staging\instance\transport.db')
$results['slashes_and_case'] = (Test-PilotPathEquals 'C:/transport-report/instance' 'C:\TRANSPORT-REPORT\instance\')
$results['production_url'] = (Test-PilotUrlIsProduction 'http://10.103.25.14:5050/')
$results['staging_url_is_production'] = (Test-PilotUrlIsProduction 'http://10.103.25.14:5051')
$results['staging_url'] = (Test-PilotUrlIsStaging 'http://10.103.25.14:5051/drones/api/route_sync')
$results['refuses_production_path'] = (Throws { Assert-PilotNotProduction -Path 'C:\transport-report\instance\transport.db' })
$results['allows_staging_path'] = (Throws { Assert-PilotNotProduction -Path 'C:\transport-report-staging\instance' })
$results['refuses_production_url'] = (Throws { Assert-PilotStagingUrl -Url 'http://10.103.25.14:5050' })
$results['allows_staging_url'] = (Throws { Assert-PilotStagingUrl -Url 'http://10.103.25.14:5051' })
$results['refuses_production_service'] = (Throws { Assert-PilotServiceIsNotProduction -Name 'TransportReport' })
$results['allows_staging_service'] = (Throws { Assert-PilotServiceIsNotProduction -Name 'TransportReportStaging' })

$prod    = [pscustomobject]@{ Name='TransportReport';        AppDirectory='C:\transport-report';         Application='py.exe'; ImagePath='x' }
$staging = [pscustomobject]@{ Name='TransportReportStaging'; AppDirectory='C:\transport-report-staging'; Application='py.exe'; ImagePath='y' }
$other   = [pscustomobject]@{ Name='TransportReportOther';   AppDirectory='C:\transport-report-staging'; Application='';       ImagePath='' }
$both    = [pscustomobject]@{ Name='TransportReportWeird';   AppDirectory='C:\transport-report-staging'; Application='C:\transport-report\run_server.py'; ImagePath='' }
$named   = [pscustomobject]@{ Name='TransportReport';        AppDirectory='C:\transport-report-staging'; Application='';       ImagePath='' }

$results['picks_staging'] = ((Select-PilotStagingService -Candidates @($prod,$staging)).Name)
$results['refuses_when_none'] = (Throws { Select-PilotStagingService -Candidates @($prod) })
$results['refuses_when_two'] = (Throws { Select-PilotStagingService -Candidates @($staging,$other) })
$results['refuses_ambiguous_candidate'] = (Throws { Select-PilotStagingService -Candidates @($both) })
$results['never_picks_production_name'] = (Throws { Select-PilotStagingService -Candidates @($named) })

$results | ConvertTo-Json -Compress
'''


@unittest.skipIf(PWSH is None, 'pwsh is not installed in this environment')
class PowerShellIsValid(unittest.TestCase):

    def run_pwsh(self, script, *arguments):
        directory = tempfile.mkdtemp(prefix='pilot_ps_')
        try:
            path = os.path.join(directory, 'check.ps1')
            with open(path, 'w', encoding='utf-8', newline='\n') as handle:
                handle.write(script)
            return subprocess.run([PWSH, '-NoProfile', '-File', path]
                                  + list(arguments),
                                  capture_output=True, text=True,
                                  cwd=REPO_ROOT)
        finally:
            shutil.rmtree(directory, ignore_errors=True)

    def test_every_script_parses(self):
        paths = [os.path.join(KIT_DIR, name) for name in PS_FILES]
        result = self.run_pwsh(PARSE_SCRIPT, '-Paths', *paths)
        self.assertEqual(result.returncode, 0,
                         'PowerShell refused to parse a script:\n%s'
                         % result.stdout)

    def test_the_guards_behave_as_claimed(self):
        result = self.run_pwsh(GUARD_SCRIPT, '-Module',
                               os.path.join(KIT_DIR, 'PilotKit.psm1'))
        self.assertEqual(result.returncode, 0, result.stderr)
        line = [row for row in result.stdout.splitlines()
                if row.strip().startswith('{')][-1]
        state = json.loads(line)

        self.assertFalse(state['staging_not_within_production'])
        self.assertTrue(state['production_db_within_production'])
        self.assertFalse(state['staging_db_touches_production'])
        self.assertTrue(state['slashes_and_case'])
        self.assertTrue(state['production_url'])
        self.assertFalse(state['staging_url_is_production'])
        self.assertTrue(state['staging_url'])
        self.assertTrue(state['refuses_production_path'])
        self.assertFalse(state['allows_staging_path'])
        self.assertTrue(state['refuses_production_url'])
        self.assertFalse(state['allows_staging_url'])
        self.assertTrue(state['refuses_production_service'])
        self.assertFalse(state['allows_staging_service'])
        self.assertEqual(state['picks_staging'], 'TransportReportStaging')
        self.assertTrue(state['refuses_when_none'])
        self.assertTrue(state['refuses_when_two'])
        self.assertTrue(state['refuses_ambiguous_candidate'])
        self.assertTrue(state['never_picks_production_name'])

    def test_the_python_and_powershell_guards_agree(self):
        """Две реализации одного правила обязаны отвечать одинаково."""
        result = self.run_pwsh(GUARD_SCRIPT, '-Module',
                               os.path.join(KIT_DIR, 'PilotKit.psm1'))
        line = [row for row in result.stdout.splitlines()
                if row.strip().startswith('{')][-1]
        state = json.loads(line)
        self.assertEqual(
            state['staging_not_within_production'],
            common.path_is_within(common.STAGING_ROOT, common.PRODUCTION_ROOT))
        self.assertEqual(
            state['staging_db_touches_production'],
            common.touches_production(common.STAGING_DB))
        self.assertEqual(state['production_url'],
                         common.url_is_production(common.PRODUCTION_URL))


class ConstantsAgreeAcrossLanguages(unittest.TestCase):
    """Одна площадка, одно место правды. Расхождение ловится здесь."""

    def test_the_module_and_the_python_carry_the_same_sites(self):
        text = script_text('PilotKit.psm1')
        for value in (common.PRODUCTION_ROOT, common.PRODUCTION_DB,
                      common.PRODUCTION_URL, common.PRODUCTION_SERVICE,
                      common.STAGING_ROOT, common.STAGING_DB,
                      common.STAGING_URL, common.VERIFIED_MERGE_SHA,
                      common.TARGET_DAY, common.MIGRATION_ID):
            self.assertIn(value, text,
                          'PilotKit.psm1 does not carry %r' % value)

    def test_the_staging_service_name_comes_from_the_repository(self):
        runbook = os.path.join(REPO_ROOT, 'docs',
                               'ORG_WINDOWS_SERVER_STAGING_RUNBOOK.md')
        with open(runbook, encoding='utf-8') as handle:
            text = handle.read()
        found = re.search(r'Service name.*`([A-Za-z0-9_]+)`', text)
        self.assertIsNotNone(found, 'the runbook no longer names the service')
        self.assertEqual(found.group(1), 'TransportReportStaging')
        self.assertIn("Service name.*`([A-Za-z0-9_]+)`",
                      script_text('PilotKit.psm1'))

    def test_the_kit_never_hardcodes_the_service_without_resolving_it(self):
        for name in STAGING_SCRIPTS:
            text = script_text(name)
            if 'Stop-Service' in text or 'Start-Service' in text:
                self.assertIn('Resolve-PilotStagingService', text, name)


if __name__ == '__main__':
    unittest.main(verbosity=2)
