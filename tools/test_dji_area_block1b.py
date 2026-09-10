# -*- coding: utf-8 -*-
"""Самотест tools/dji_area_block1b.py -- инструмент этапа 1B.

Инструмент вставляется в консоль сервера и читает БОЕВУЮ базу. Поэтому
проверяется не «отработал без исключения», а три свойства, каждое из которых
дёшево сломать и дорого заметить:

  1. он НЕ ПИШЕТ. Не «мы так задумали», а sha256 файла базы до и после, плюс
     DELETE, который SQLite обязан отклонить сам;
  1a. сводка V4 берётся ПО `v4_summary_id` расчёта, а не по `flight_id`. В
     базе намеренно лежит вторая сводка того же вылета с другими числами:
     связь через flight_id либо размножит строку, либо подставит чужую;
  1b. историческая идентичность контура выгружается и называется словами:
     если исторический полигон не доказан, вывод `UNKNOWN...`, а не молчаливое
     использование сегодняшней ревизии;
  1c. остаток `total - obstacle - work` выносится отдельно и НЕ называется
     safety margin;
  2. на кейсе с намеренным стопроцентным перекрытием S = 2U, и кейс помечен
     различающим. Это единственная конфигурация, где сравнение S и U вообще
     способно отличить километражный интеграл от объединения; рядом стоит
     отрицательный контроль -- одиночный проход, где S = U и метка снята;
  1d. выборка фильтруется по версии алгоритма и версии резолвера поля. В
     базе намеренно лежит ЖИВАЯ строка предыдущей версии того же вылета с
     площадью 999999: без фильтра bundle вернул бы её вместе с текущей;
  3. коды возврата: нет базы -> 2 и файл НЕ создан; база без миграции -> 1.

Фикстуру строит сам, в отдельном временном каталоге: ни сети, ни сервера, ни
боевой базы. Stdlib плюс DDL, прочитанный из САМОЙ миграции.
"""

import hashlib
import json
import math
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from dji_area import AREA_ALGORITHM_VERSION, FIELD_RESOLVER_VERSION  # noqa: E402
from dji_area import store  # noqa: E402
from tests.test_dji_area_core import frame, v4_bytes  # noqa: E402

TOOL = os.path.join(ROOT, 'tools', 'dji_area_block1b.py')
MIGRATION = os.path.join(ROOT, 'migrate_dji_area_evidence_001.py')
HW = 'SYNTHETIC-HW-NOT-REAL'
FLIGHT = 900201
T0 = 1785526013
LAT0, LON0 = 39.9, 64.4
M_PER_DEG = 6371000.0 * math.pi / 180.0
PASS_M, PASS_FRAMES, WIDTH = 100.0, 21, 6.0
DECOY_WIDTH = 99.0


def _ddl():
    with open(MIGRATION, encoding='utf-8') as fh:
        text = fh.read()
    return re.findall(r'CREATE TABLE IF NOT EXISTS \w+ \(.*?\n    \)', text, re.S)


def _pass_frames(start_ms):
    out = []
    for i in range(PASS_FRAMES):
        out.append(frame(start_ms + i * 1000, area=10.0 + i * 0.05, width=WIDTH,
                         spray_flag=1, flow=100,
                         lat=LAT0 + (PASS_M / M_PER_DEG) * i / (PASS_FRAMES - 1),
                         lng=LON0))
    return out


def build(db_path, passes, historical=1, with_geometry=True):
    """База с одним вылетом из `passes` одинаковых проходов по одной земле."""
    con = sqlite3.connect(db_path)
    for stmt in _ddl():
        con.execute(stmt)

    def cols(table):
        return {r[1]: (r[2], r[3], r[4], r[5])
                for r in con.execute('PRAGMA table_info(%s)' % table)}

    def ins(table, **vals):
        info, row = cols(table), dict(vals)
        for name, (typ, notnull, dflt, pk) in info.items():
            if pk or name in row:
                continue
            if notnull and dflt is None:
                t = (typ or '').upper()
                row[name] = 0 if any(k in t for k in ('INT', 'FLOAT', 'REAL',
                                                      'BOOL')) else 'x'
        keys = list(row)
        con.execute('INSERT INTO %s (%s) VALUES (%s)'
                    % (table, ','.join(keys), ','.join('?' * len(keys))),
                    [row[k] for k in keys])

    frames, t = [], T0 * 1000
    for _ in range(passes):
        frames.extend(_pass_frames(t))
        # Пауза между проходами: связующий отрезок обязан стать разрывом
        # записи, а не полосой длиной в сто метров.
        t += PASS_FRAMES * 1000 + 120000
    body = v4_bytes(frames)
    sha = hashlib.sha256(body).hexdigest()
    root = store.source_root(os.path.abspath(db_path))
    rel = store.body_relpath(sha, store.ENCODING_RAW)
    target = os.path.join(root, rel.replace('/', os.sep))
    os.makedirs(os.path.dirname(target), exist_ok=True)
    with open(target, 'wb') as fh:
        fh.write(body)

    ins('dji_source_revisions', id=1, flight_id=FLIGHT, source_type='v4',
        sha256=sha, size_bytes=len(body), captured_at_utc='2026-09-09',
        storage_kind='file', body_path=rel, body_encoding=store.ENCODING_RAW,
        received_at='2026-09-09')
    ins('dji_area_calculations', flight_id=FLIGHT, hardware_id=HW,
        start_at_utc='2026-08-18T00:53:00', end_at_utc='2026-08-18T00:58:00',
        report_start_date='2026-08-18', raw_area_m2=PASS_M * WIDTH * passes,
        area_status='RAW_CORROBORATED_QUALIFIED', application_activity='PRESENT',
        application_channel_quality='INFORMATIVE', superseded_at=None,
        v4_summary_id=2, area_algorithm_version=AREA_ALGORITHM_VERSION)
    # [REASON]: строка ПРЕДЫДУЩЕЙ версии алгоритма остаётся живой -- store
    # закрывает только строки своей версии. Без фильтра по версии выборка
    # вернула бы обе, и bundle посчитал бы вылет дважды.
    ins('dji_area_calculations', flight_id=FLIGHT, hardware_id=HW,
        start_at_utc='2026-08-18T00:53:00', end_at_utc='2026-08-18T00:58:00',
        report_start_date='2026-08-18', raw_area_m2=999999.0,
        area_status='RAW_CORROBORATED_QUALIFIED', application_activity='PRESENT',
        application_channel_quality='INFORMATIVE', superseded_at=None,
        v4_summary_id=1,
        area_algorithm_version=AREA_ALGORITHM_VERSION + '-OLD')
    ins('dji_flight_evidence', flight_id=FLIGHT, hardware_id=HW,
        v4_revision_id=1, list_spray_width=WIDTH, list_start_ts=T0,
        list_end_ts=T0 + 300, updated_at='2026-09-09')
    # Ловушка: ПЕРВАЯ по id сводка того же вылета -- чужая, от другой
    # ревизии V4. Связь через flight_id подцепила бы именно её.
    ins('dji_v4_summaries', id=1, flight_id=FLIGHT, source_revision_id=999,
        frame_count=1, span_s=1.0, moving_application_distance_m=0.0,
        width_min=DECOY_WIDTH, width_max=DECOY_WIDTH, application_frames=0)
    ins('dji_v4_summaries', id=2, flight_id=FLIGHT, source_revision_id=1,
        frame_count=len(frames), span_s=300.0,
        moving_application_distance_m=PASS_M * passes,
        width_min=WIDTH, width_max=WIDTH, application_frames=len(frames))
    ins('dji_field_attributions', flight_id=FLIGHT,
        field_attribution_tier='TIER2_STRONG', field_land_uuid='uuid-1',
        field_name_at_snapshot='SYNTHETIC-FIELD', superseded_at=None,
        land_snapshot_id=7, land_revision_id=1,
        historical_geometry_available=historical,
        field_resolver_version=FIELD_RESOLVER_VERSION)
    # Ревизия, на которую ссылается атрибуция...
    # Контур, накрывающий ЮЖНУЮ половину прохода: полоса обязана разделиться
    # на «внутри поля» и «снаружи поля», а не остаться одним числом.
    geom_md5 = None
    if with_geometry:
        half = PASS_M / 2.0 / M_PER_DEG
        wide = 40.0 / (M_PER_DEG * math.cos(math.radians(LAT0)))
        doc = {'type': 'FeatureCollection', 'features': [{
            'type': 'Feature', 'properties': {'funcType': 'PlantZone'},
            'geometry': {'type': 'Polygon', 'coordinates': [[
                [LON0 - wide, LAT0 - half], [LON0 + wide, LAT0 - half],
                [LON0 + wide, LAT0 + half], [LON0 - wide, LAT0 + half],
                [LON0 - wide, LAT0 - half]]]}}]}
        body = json.dumps(doc, ensure_ascii=False).encode('utf-8')
        geom_md5 = hashlib.md5(body).hexdigest()
        ins('dji_land_geometries', content_md5=geom_md5,
            sha256=hashlib.sha256(body).hexdigest(), size_bytes=len(body),
            md5_verified=1, body_blob=body, parse_status='OK', ring_count=1,
            first_seen_at='2026-09-09')
    ins('dji_land_revisions', id=1, land_uuid='uuid-1', name='SYNTHETIC-FIELD',
        total_area_raw=30.0, work_area_raw=25.0, obstacle_area_raw=2.0,
        area_unit='mu', raw_json='{}', raw_sha256='s1', geometry_md5=geom_md5,
        first_seen_snapshot_id=7, last_seen_snapshot_id=7)
    # ...и ДРУГАЯ ревизия того же участка, которую нельзя смешивать с первой.
    ins('dji_land_revisions', id=2, land_uuid='uuid-1', name='SYNTHETIC-FIELD',
        total_area_raw=44.0, work_area_raw=44.0, obstacle_area_raw=0.0,
        area_unit='mu', raw_json='{}', raw_sha256='s2',
        first_seen_snapshot_id=9, last_seen_snapshot_id=9)
    con.commit()
    con.close()


def run(db_path, out_dir):
    return subprocess.run(
        [sys.executable, TOOL, '--db', db_path, '--date', '2026-08-18',
         '--hardware', HW, '--out', out_dir],
        capture_output=True, text=True)


def sha_of(path):
    with open(path, 'rb') as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def load(out_dir, name):
    import json
    with open(os.path.join(out_dir, name), encoding='utf-8') as fh:
        return json.load(fh)


def csv_rows(out_dir, name):
    import csv as _csv
    with open(os.path.join(out_dir, name), encoding='utf-8', newline='') as fh:
        return list(_csv.DictReader(fh))


def coverage_row(out_dir):
    rows = load(out_dir, 'coverage.json')
    assert len(rows) == 1, 'expected one flight, got %d' % len(rows)
    return rows[0]


class Base(unittest.TestCase):

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix='block1b-')
        self.db = os.path.join(self.dir, 'transport.db')
        self.out = os.path.join(self.dir, 'out')

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)


class ItNeverWrites(Base):

    def test_database_bytes_are_identical_before_and_after(self):
        build(self.db, passes=2)
        before = sha_of(self.db)
        res = run(self.db, self.out)
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual(sha_of(self.db), before)

    def test_control_sqlite_itself_refuses_a_write_on_this_handle(self):
        # Доказывает, что режим ro настоящий, а не «мы просто не пишем».
        build(self.db, passes=1)
        uri = 'file:%s?mode=ro' % self.db
        con = sqlite3.connect(uri, uri=True)
        with self.assertRaises(sqlite3.OperationalError):
            con.execute('DELETE FROM dji_area_calculations')
        con.close()


class OverlapIsTheDiscriminatingCase(Base):

    def test_two_identical_passes_give_S_twice_U_and_are_flagged(self):
        build(self.db, passes=2)
        self.assertEqual(run(self.db, self.out).returncode, 0)
        row = coverage_row(self.out)
        self.assertEqual(row['coverage_status'], 'ESTIMATE')
        self.assertAlmostEqual(row['unique_application_ha'], 0.06, delta=0.006)
        self.assertAlmostEqual(row['swath_integral_ha'], 0.12, delta=0.006)
        self.assertGreater(row['s_over_u'], 1.7)
        self.assertTrue(row['discriminating'])

    def test_control_a_single_pass_is_not_discriminating(self):
        # Без этого контроля проверка выше прошла бы и у кода, который метит
        # различающим КАЖДЫЙ вылет.
        build(self.db, passes=1)
        self.assertEqual(run(self.db, self.out).returncode, 0)
        row = coverage_row(self.out)
        self.assertAlmostEqual(row['unique_application_ha'], 0.06, delta=0.006)
        self.assertLess(abs(row['s_over_u'] - 1.0), 0.25)
        self.assertFalse(row['discriminating'])


class ExitCodes(Base):

    def test_missing_database_is_code_2_and_creates_no_file(self):
        missing = os.path.join(self.dir, 'nope', 'transport.db')
        res = run(missing, self.out)
        self.assertEqual(res.returncode, 2)
        self.assertFalse(os.path.exists(missing))
        self.assertFalse(os.path.isdir(os.path.dirname(missing)))

    def test_database_without_the_migration_is_code_1(self):
        sqlite3.connect(self.db).execute('CREATE TABLE x (a INT)')
        res = run(self.db, self.out)
        self.assertEqual(res.returncode, 1)
        self.assertIn('migration is not applied', res.stdout)


class TheRightV4SummaryIsUsed(unittest.TestCase):
    """`dji_v4_summaries.flight_id` НЕ уникален. Связь -- через расчёт."""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix='block1b-join-')
        self.db = os.path.join(self.dir, 'transport.db')
        self.out = os.path.join(self.dir, 'out')
        build(self.db, passes=1)
        self.assertEqual(run(self.db, self.out).returncode, 0)

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_exactly_one_row_per_flight_no_duplication(self):
        # Связь через flight_id вернула бы ДВЕ строки на один вылет.
        self.assertEqual(len(load(self.out, 'coverage.json')), 1)
        self.assertEqual(len(csv_rows(self.out, 'flights.csv')), 1)

    def test_the_summary_taken_is_the_one_the_calculation_names(self):
        row = csv_rows(self.out, 'flights.csv')[0]
        self.assertEqual(row['v4_summary_id'], '2')
        self.assertEqual(row['v4_summary_row_id'], '2')
        self.assertEqual(float(row['width_max']), WIDTH)
        # Ровно то значение, которое подцепила бы неверная связь.
        self.assertNotEqual(float(row['width_max']), DECOY_WIDTH)


class PolygonVintageIsNamedNotAssumed(unittest.TestCase):

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix='block1b-vintage-')
        self.db = os.path.join(self.dir, 'transport.db')
        self.out = os.path.join(self.dir, 'out')

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_without_proven_historical_geometry_the_verdict_is_unknown(self):
        build(self.db, passes=1, historical=0)
        self.assertEqual(run(self.db, self.out).returncode, 0)
        row = csv_rows(self.out, 'polygon_vintage.csv')[0]
        self.assertEqual(row['polygon_vintage'],
                         'UNKNOWN_CURRENT_SNAPSHOT_ONLY')
        self.assertEqual(load(self.out, 'manifest.json')['counts']
                         ['flights_without_proven_historical_geometry'], 1)

    def test_control_with_proven_geometry_the_verdict_changes(self):
        build(self.db, passes=1, historical=1)
        self.assertEqual(run(self.db, self.out).returncode, 0)
        row = csv_rows(self.out, 'polygon_vintage.csv')[0]
        self.assertEqual(row['polygon_vintage'], 'HISTORICAL_PROVEN')
        self.assertEqual(row['land_revision_id'], '1')
        self.assertEqual(row['land_snapshot_id'], '7')

    def test_the_used_revision_is_marked_apart_from_the_other_one(self):
        build(self.db, passes=1)
        self.assertEqual(run(self.db, self.out).returncode, 0)
        by_id = {r['id']: r for r in csv_rows(self.out, 'land_revisions.csv')}
        self.assertEqual(by_id['1']['revision_role'], 'USED_BY_ATTRIBUTION')
        self.assertEqual(by_id['2']['revision_role'],
                         'OTHER_REVISION_SAME_UUID')


class TheTaskAreaResidualIsNotCalledSafetyMargin(unittest.TestCase):

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix='block1b-area-')
        self.db = os.path.join(self.dir, 'transport.db')
        self.out = os.path.join(self.dir, 'out')
        build(self.db, passes=1)
        self.assertEqual(run(self.db, self.out).returncode, 0)

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_the_unexplained_remainder_is_computed_and_nonzero(self):
        # total 30 mu, obstacle 2 mu, work 25 mu -> остаток 3 mu = 0.2 ha.
        used = [r for r in csv_rows(self.out, 'land_revisions.csv')
                if r['revision_role'] == 'USED_BY_ATTRIBUTION'][0]
        self.assertAlmostEqual(float(used['unexplained_exclusion_ha']),
                               3.0 * (2000.0 / 3.0) / 10000.0, places=6)

    def test_no_output_calls_the_remainder_a_safety_margin(self):
        # Назвать остаток safety margin -- значит объявить формулу
        # проверенной, не проверив третье слагаемое.
        for name in ('land_revisions.csv', 'manifest.json'):
            with open(os.path.join(self.out, name), encoding='utf-8') as fh:
                self.assertNotIn('safety_margin', fh.read().lower())


class TheBundleIsSelfContained(unittest.TestCase):

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix='block1b-blob-')
        self.db = os.path.join(self.dir, 'transport.db')
        self.out = os.path.join(self.dir, 'out')
        build(self.db, passes=2)
        self.assertEqual(run(self.db, self.out).returncode, 0)

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_the_v4_body_travels_with_the_bundle_and_matches_its_digest(self):
        blobs = load(self.out, 'manifest.json')['blobs']
        v4s = [b for b in blobs if b['kind'] == 'v4']
        self.assertEqual(len(v4s), 1)
        self.assertTrue(v4s[0]['sha256_matches'])
        path = os.path.join(self.out, v4s[0]['file'].replace('/', os.sep))
        self.assertTrue(os.path.isfile(path))
        self.assertEqual(sha_of(path), v4s[0]['sha256'])

    def test_the_exported_body_decodes_to_the_same_coverage(self):
        # Ровно то, ради чего bundle делается самодостаточным: расчёт
        # воспроизводится из выгруженных байтов, без доступа к серверу.
        from dji_area import coverage as _cov
        from dji_area import v4 as _v4
        blobs = load(self.out, 'manifest.json')['blobs']
        path = os.path.join(self.out, [b for b in blobs
                                       if b['kind'] == 'v4'][0]['file'])
        with open(path, 'rb') as fh:
            frames = _v4.decode_v4(fh.read()).frames
        again = _cov.coverage_from_v4(frames, 'INFORMATIVE')
        self.assertAlmostEqual(again['unique_application_ha'],
                               coverage_row(self.out)['unique_application_ha'])

    def test_the_manifest_pins_the_exporter_itself(self):
        man = load(self.out, 'manifest.json')
        self.assertEqual(man['exporter_sha256'], sha_of(TOOL))
        self.assertEqual(man['coverage_module_sha256'],
                         sha_of(os.path.join(ROOT, 'dji_area', 'coverage.py')))
        self.assertTrue(man['not_billable'])


class TheHistoricalContourReachesTheCalculation(unittest.TestCase):
    """Без контура «внутри» и «снаружи» не существуют как величины."""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix='block1b-rings-')
        self.db = os.path.join(self.dir, 'transport.db')
        self.out = os.path.join(self.dir, 'out')

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_the_swath_is_split_into_inside_and_outside_the_field(self):
        build(self.db, passes=1, historical=1, with_geometry=True)
        self.assertEqual(run(self.db, self.out).returncode, 0)
        row = coverage_row(self.out)
        self.assertEqual(row['contour_status'], 'CONTOUR_APPLIED')
        self.assertIsNotNone(row['unique_application_inside_field_ha'])
        self.assertIsNotNone(row['application_outside_field_ha'])
        # Контур накрывает половину прохода: обе доли заметно больше нуля и
        # вместе дают всю полосу.
        inside = row['unique_application_inside_field_ha']
        outside = row['application_outside_field_ha']
        self.assertGreater(inside, 0.01)
        self.assertGreater(outside, 0.01)
        self.assertAlmostEqual(inside + outside, row['unique_application_ha'],
                               delta=0.002)

    def test_control_unproven_historical_geometry_yields_no_inside_outside(self):
        # Сентябрьский контур не имеет права стать геометрией августа.
        build(self.db, passes=1, historical=0, with_geometry=True)
        self.assertEqual(run(self.db, self.out).returncode, 0)
        row = coverage_row(self.out)
        self.assertEqual(row['contour_reason'],
                         'HISTORICAL_GEOMETRY_NOT_PROVEN')
        self.assertEqual(row['contour_status'], 'CONTOUR_ABSENT')
        self.assertIsNone(row['unique_application_inside_field_ha'])
        self.assertIsNone(row['application_outside_field_ha'])
        # Полоса при этом посчитана -- отсутствует именно разбиение.
        self.assertIsNotNone(row['unique_application_ha'])

    def test_control_a_missing_geometry_body_is_named_not_guessed(self):
        build(self.db, passes=1, historical=1, with_geometry=False)
        self.assertEqual(run(self.db, self.out).returncode, 0)
        row = coverage_row(self.out)
        self.assertEqual(row['contour_reason'], 'NO_GEOMETRY_MD5')
        self.assertIsNone(row['unique_application_inside_field_ha'])


class TheOutputDirectoryIsNeverSilentlyMixed(Base):

    def test_a_non_empty_output_directory_stops_the_run(self):
        build(self.db, passes=1)
        self.assertEqual(run(self.db, self.out).returncode, 0)
        second = run(self.db, self.out)
        self.assertEqual(second.returncode, 1)
        self.assertIn('exists and is not empty', second.stdout)

    def test_control_a_fresh_directory_is_accepted(self):
        build(self.db, passes=1)
        self.assertEqual(run(self.db, self.out + '-2').returncode, 0)


if __name__ == '__main__':
    unittest.main()
