# -*- coding: utf-8 -*-
"""DJI-AREA-EVIDENCE-001: приёмники источников и снимков + пересчёт end-to-end.

Синтетика: вылеты с 900001, borт 'SYNTHETIC-HW-NOT-REAL', карточки и
маршруты собраны здесь же (protobuf -- теми же builder'ами, что и в
тестах сборщика), V4 -- синтетическими кадрами из test_dji_area_core.

Что доказывается:
* ревизия неизменяема и идемпотентна: те же байты -> duplicates, другие ->
  новая ревизия, старая остаётся;
* тело с маркером подписанного URL и тело с несовпавшим SHA отклоняются;
* dji_flight_evidence собирается из карточки/маршрута/V4; чужой embedded id
  маршрута -> ROUTE_IDENTITY_ERROR и борт из маршрута не берётся;
* пересчёт (tools/dji_area_recalc.py) на тестовой базе даёт статусы матрицы,
  повторный --apply -> unchanged, NULL остаётся NULL в базе (typeof);
* drone_flights.area_ha не тронута ни приёмом, ни пересчётом;
* снимок каталога: ревизии append-only, второй снимок не переписывает первый,
  геометрия хранится байт в байт с проверкой md5.
"""
import base64
import hashlib
import json
import os
import sqlite3
import struct
import subprocess
import sys
import unittest
from datetime import date, datetime

from tests.harness import app, reset_db, create_org, TEST_DB_PATH
from tests.test_dji_area_core import (counter_series, f_bytes, frame,
                                      v4_bytes, MS, START)

from models import db, DroneFlight, DroneUnit, DroneNickname

from dji_area import evidence as ev
from dji_area import resolver as rs
from dji_area import store
from drone_collector.tests.test_route_decode import route_record, response

TOKEN = 'SYNTHETIC-source-sync-token-NOT-REAL'
HW = 'SYNTHETIC-HW-NOT-REAL'
FLIGHT_A = 900001
FLIGHT_B = 900002
TOOL = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    'tools', 'dji_area_recalc.py')


def card_json(flight_id, area=10000.0, hardware=HW, md5='', start=START,
              end=START + 120, width=6.0, mode=4, manual=False):
    doc = {'status': 200, 'code': 0, 'message': 'OK', 'data': {
        'id': flight_id, 'hardware_id': hardware, 'new_work_area': area,
        'geometry_md5': md5, 'mode_name': mode, 'manual_mode': manual,
        'spray_width': width, 'start_timestamp': start, 'end_timestamp': end,
        'app_version': '0.0.0', 'drone_type': 'T40', 'create_date': 20260801,
        'nickname': 'SYNTHETIC-NICK'}}
    return json.dumps(doc, ensure_ascii=False).encode('utf-8')


def route_bytes(embedded_id):
    # route_record() already carries FIELD_DEVICE (9) with the fixture
    # hardware id FIXTURE0000000000000 -- a value that must never be adopted
    # from a quarantined route.
    return response([route_record(flight_id=embedded_id, area_m2=10000.0)])


def linear(first, last, seconds):
    """Кадры раз в секунду от first до last: окно без разрывов > 1 с."""
    n = int(seconds) + 1
    if n == 1:
        return [first]
    return [first + (last - first) * i / float(n - 1) for i in range(n)]


def source(flight_id, source_type, body, **extra):
    item = {'flight_id': flight_id, 'source_type': source_type,
            'body_b64': base64.b64encode(body).decode('ascii'),
            'sha256': hashlib.sha256(body).hexdigest(),
            'size_bytes': len(body),
            'captured_at_utc': '2026-08-01 05:00:00',
            'capture_run_id': 'SYNTHETIC-RUN'}
    item.update(extra)
    return item


class Base(unittest.TestCase):

    def setUp(self):
        reset_db()
        app.config['DRONE_API_TOKEN'] = TOKEN
        self.client = app.test_client()
        org_id = create_org('SYNTHETIC Org')
        with app.app_context():
            unit = DroneUnit(number=6, organization_id=org_id, hardware_id=HW)
            db.session.add(unit)
            db.session.flush()
            db.session.add(DroneNickname(drone_unit_id=unit.id,
                                         nickname='SYNTHETIC-NICK',
                                         normalized='synthetic-nick'))
            self.unit_id = unit.id
            db.session.commit()

    def add_flight(self, flight_id, area_m2=10000.0, start=START, end=None,
                   mode=4, width=6.0, manual=False, unit=True):
        end = end if end is not None else start + 120
        raw = {'id': flight_id, 'new_work_area': area_m2,
               'start_timestamp': start, 'end_timestamp': end,
               'mode_name': mode, 'spray_width': width, 'manual_mode': manual,
               'nickname': 'SYNTHETIC-NICK', 'serial_number': 'R-NOT-REAL'}
        with app.app_context():
            db.session.add(DroneFlight(
                dji_flight_id=flight_id,
                drone_unit_id=self.unit_id if unit else None,
                nickname_raw='SYNTHETIC-NICK',
                started_at=datetime.utcfromtimestamp(start),
                finished_at=datetime.utcfromtimestamp(end),
                mode_name=mode, spray_width=width, manual_mode=manual,
                area_ha=area_m2 / 10000.0, raw_json=json.dumps(raw)))
            db.session.commit()

    def post_sources(self, items, token=TOKEN):
        return self.client.post('/drones/api/source_sync',
                                json={'token': token, 'sources': items})

    def raw(self, sql, args=()):
        con = sqlite3.connect(TEST_DB_PATH)
        try:
            return con.execute(sql, args).fetchall()
        finally:
            con.close()

    def recalc(self, *extra):
        args = [sys.executable, TOOL, '--from', '2026-08-01', '--to',
                '2026-08-01', '--db', TEST_DB_PATH, '--quiet'] + list(extra)
        return subprocess.run(args, capture_output=True, text=True,
                              cwd=os.path.dirname(os.path.dirname(TOOL)))


def airlines_derived(v4_path='kr-ag2-api.invalid/api/web/v2/flight_datas/'
                              'objects/airline_v4/1/dji_service_NOT_REAL'):
    """Производный документ airlines -- РОВНО в той форме, в какой его пишут
    оба производителя.

    [REASON]: ключ `file_v4_url_path`, а не `file_v4_url`. Форма взята из
    `drone_collector.sources.airlines_bytes` и совпадает с
    `tools/dji_area_import_sources.airlines_paths`; сырое тело DJI с
    подписанными ссылками не хранится нигде и сюда попасть не может.
    """
    return json.dumps({'code': 0, 'status': 200,
                       'file_v4_url_path': v4_path,
                       'std_detail_url_path': None,
                       'std_summary_url_path': None},
                      ensure_ascii=False, sort_keys=True,
                      separators=(',', ':')).encode('utf-8')


class SourceIngest(Base):

    def test_no_v4_at_dji_is_named_so_and_not_called_not_captured(self):
        """Ревизия airlines без ссылки на V4 -- это НАЙДЕННОЕ отсутствие.

        [REASON]: проверка искала `"file_v4_url":null`, а оба производителя
        пишут `file_v4_url_path`. Совпадений не было никогда, и «у DJI нет
        V4» записывалось как `NOT_CAPTURED` -- «не захватили», то есть вина
        сборщика вместо факта об источнике. Живьём это не проявлялось
        только потому, что ревизий airlines не существовало вовсе:
        сборщик слушал не тот путь.
        """
        self.add_flight(FLIGHT_A)
        resp = self.post_sources([source(
            FLIGHT_A, 'airlines', airlines_derived(v4_path=None),
            schema_version='airlines-paths-only')])
        self.assertEqual(resp.get_json()['errors'], 0, resp.get_json())
        row = self.raw('SELECT v4_absent_reason, airlines_revision_id '
                       'FROM dji_flight_evidence WHERE flight_id=?',
                       (FLIGHT_A,))[0]
        self.assertIsNotNone(row[1], 'ревизия airlines не привязалась')
        self.assertEqual(row[0], 'NO_V4_URL_AT_SOURCE')

    def test_a_v4_url_at_dji_means_the_body_is_merely_not_captured(self):
        """ОТРИЦАТЕЛЬНЫЙ КОНТРОЛЬ: не всякая ревизия airlines даёт этот
        вердикт. Ссылка есть, тела нет -- это именно `NOT_CAPTURED`."""
        self.add_flight(FLIGHT_A)
        self.post_sources([source(FLIGHT_A, 'airlines', airlines_derived(),
                                  schema_version='airlines-paths-only')])
        row = self.raw('SELECT v4_absent_reason FROM dji_flight_evidence '
                       'WHERE flight_id=?', (FLIGHT_A,))[0]
        self.assertEqual(row[0], 'NOT_CAPTURED')

    def test_the_derived_airlines_document_carries_no_credential(self):
        """Приёмник не должен получить подписанную ссылку -- и не получает:
        в очередь идёт только производный документ. Здесь проверяется, что
        сырое тело он бы ОТВЕРГ, то есть защита работает в обе стороны."""
        self.add_flight(FLIGHT_A)
        raw_body = json.dumps({
            'status': 200, 'code': 0,
            'data': {'airline': {'file_v4_url':
                                 'https://kr-ag2-api.invalid/x?Expires=1'
                                 '&OSSAccessKeyId=NOT-REAL'
                                 '&Signature=NOT-REAL'}}}).encode('utf-8')
        resp = self.post_sources([source(FLIGHT_A, 'airlines', raw_body,
                                         schema_version='airlines-paths-only')])
        self.assertEqual(resp.get_json()['errors'], 1, resp.get_json())
        self.assertEqual(
            self.raw('SELECT COUNT(*) FROM dji_source_revisions '
                     'WHERE source_type=?', ('airlines',))[0][0], 0)

    def test_wrong_token_is_401_then_right_token_200(self):
        self.add_flight(FLIGHT_A)
        item = source(FLIGHT_A, 'card', card_json(FLIGHT_A))
        self.assertEqual(self.post_sources([item], token='wrong').status_code, 401)
        resp = self.post_sources([item])
        self.assertEqual(resp.status_code, 200, resp.get_json())
        body = resp.get_json()
        self.assertEqual((body['seen'], body['new'], body['duplicates'],
                          body['errors']), (1, 1, 0, 0))
        self.assertEqual(body['refreshed'], 1)

    def test_same_bytes_are_duplicates_different_bytes_new_revision(self):
        self.add_flight(FLIGHT_A)
        first = card_json(FLIGHT_A, area=10000.0)
        self.post_sources([source(FLIGHT_A, 'card', first)])
        again = self.post_sources([source(FLIGHT_A, 'card', first)]).get_json()
        self.assertEqual((again['new'], again['duplicates']), (0, 1))
        second = card_json(FLIGHT_A, area=10006.6667)
        newer = self.post_sources([source(FLIGHT_A, 'card', second)]).get_json()
        self.assertEqual((newer['new'], newer['duplicates']), (1, 0))
        rows = self.raw('SELECT sha256, ingest_count FROM dji_source_revisions '
                        'WHERE flight_id=? AND source_type=? ORDER BY id',
                        (FLIGHT_A, 'card'))
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0][1], 2)
        self.assertEqual(rows[0][0], hashlib.sha256(first).hexdigest())
        # evidence points at the NEWEST card
        ev_row = self.raw('SELECT card_raw_area_m2 FROM dji_flight_evidence '
                          'WHERE flight_id=?', (FLIGHT_A,))[0]
        self.assertAlmostEqual(ev_row[0], 10006.6667, 3)

    def test_sha_mismatch_and_secret_marker_are_rejected(self):
        self.add_flight(FLIGHT_A)
        bad = source(FLIGHT_A, 'card', card_json(FLIGHT_A))
        bad['sha256'] = '0' * 64
        secret = card_json(FLIGHT_A).replace(
            b'"nickname"', b'"url": "https://x.invalid/a?Signature=abc", "nickname"')
        resp = self.post_sources([bad, source(FLIGHT_A, 'card', secret)])
        body = resp.get_json()
        self.assertEqual((body['new'], body['errors']), (0, 2))
        self.assertEqual(self.raw('SELECT COUNT(*) FROM dji_source_revisions')[0][0], 0)

    def test_evidence_assembles_hardware_and_route_identity(self):
        self.add_flight(FLIGHT_A)
        resp = self.post_sources([
            source(FLIGHT_A, 'card', card_json(FLIGHT_A, md5='a' * 32)),
            source(FLIGHT_A, 'route', route_bytes(FLIGHT_A),
                   parser_version='route-decode-2'),
            source(FLIGHT_A, 'v4', v4_bytes(counter_series([0.0, 15.0])),
                   request_context={'path': '/x/airline_v4/%d/y' % FLIGHT_A,
                                    'association': 'url_path'})])
        self.assertEqual(resp.status_code, 200, resp.get_json())
        row = self.raw('SELECT hardware_id, hardware_id_source, '
                       'route_identity_status, route_embedded_flight_id, '
                       'v4_identity_status, card_geometry_md5, '
                       'list_raw_area_m2, card_raw_area_m2 FROM '
                       'dji_flight_evidence WHERE flight_id=?', (FLIGHT_A,))[0]
        self.assertEqual(row[0], HW)
        self.assertEqual(row[1], 'card')
        self.assertEqual(row[2], 'OK')
        self.assertEqual(row[3], FLIGHT_A)
        self.assertEqual(row[4], 'URL_PATH_MATCH')
        self.assertEqual(row[5], 'a' * 32)
        self.assertEqual(row[6], 10000.0)

    def test_route_of_another_flight_is_quarantined(self):
        self.add_flight(FLIGHT_A)
        self.post_sources([source(FLIGHT_A, 'route', route_bytes(FLIGHT_B))])
        row = self.raw('SELECT route_identity_status, route_embedded_flight_id, '
                       'hardware_id FROM dji_flight_evidence WHERE flight_id=?',
                       (FLIGHT_A,))[0]
        self.assertEqual(row[0], 'ROUTE_IDENTITY_ERROR')
        self.assertEqual(row[1], FLIGHT_B)
        # no card -> hardware must NOT come from a quarantined route
        self.assertIsNone(row[2])

    def test_v4_path_of_another_flight_is_mismatch(self):
        self.add_flight(FLIGHT_A)
        self.post_sources([source(FLIGHT_A, 'v4', v4_bytes(counter_series([0.0, 15.0])),
                                  request_context={'path': '/airline_v4/%d/y' % FLIGHT_B})])
        row = self.raw('SELECT v4_identity_status FROM dji_flight_evidence '
                       'WHERE flight_id=?', (FLIGHT_A,))[0]
        self.assertEqual(row[0], 'MISMATCH')

    def test_large_body_goes_to_the_file_store_and_reads_back(self):
        self.add_flight(FLIGHT_A)
        frames = counter_series([0.01 * i for i in range(3000)])
        body = v4_bytes(frames)
        self.assertGreater(len(body), store.INLINE_MAX_BYTES)
        resp = self.post_sources([source(FLIGHT_A, 'v4', body)])
        self.assertEqual(resp.get_json()['new'], 1)
        row = self.raw('SELECT storage_kind, body_path, body_encoding, sha256 '
                       'FROM dji_source_revisions WHERE source_type=?',
                       ('v4',))[0]
        self.assertEqual(row[0], 'file')
        self.assertEqual(row[2], 'gzip')
        root = store.source_root(TEST_DB_PATH)
        self.assertTrue(os.path.exists(os.path.join(root, row[1].replace('/', os.sep))))
        con = store.connect(TEST_DB_PATH)
        try:
            rev = store.revision_by_id(con, self.raw(
                'SELECT id FROM dji_source_revisions WHERE source_type=?',
                ('v4',))[0][0])
            self.assertEqual(store.read_body(root, rev), body)
        finally:
            con.close()

    def test_nothing_touches_drone_flights_or_sync_logs(self):
        self.add_flight(FLIGHT_A, area_m2=12345.0)
        before = self.raw('SELECT area_ha, typeof(area_ha), raw_json FROM '
                          'drone_flights')
        self.post_sources([source(FLIGHT_A, 'card', card_json(FLIGHT_A, area=1.0))])
        self.assertEqual(self.raw('SELECT area_ha, typeof(area_ha), raw_json '
                                  'FROM drone_flights'), before)
        self.assertEqual(self.raw('SELECT COUNT(*) FROM drone_sync_logs')[0][0], 0)

    def test_batch_cap_and_list_shape(self):
        self.assertEqual(self.client.post('/drones/api/source_sync', json={
            'token': TOKEN, 'sources': 'x'}).status_code, 400)
        many = [source(FLIGHT_A, 'card', card_json(FLIGHT_A))] * 51
        self.assertEqual(self.post_sources(many).status_code, 413)


class RecalcEndToEnd(Base):

    def seed_matrix(self):
        """Четыре записи одного борта на 2026-08-01:
        normal / stale-flat retained / V4 absent / RAW=0 + application."""
        s0 = START
        self.add_flight(900011, area_m2=10000.0, start=s0, end=s0 + 300,
                        width=6.0)
        # цепочка resume: base(4, width) -> bridge(1, null) -> target(4, null)
        self.add_flight(900012, area_m2=8980.0, start=s0 + 400, end=s0 + 700,
                        width=6.66)
        self.add_flight(900013, area_m2=0.0, start=s0 + 700, end=s0 + 720,
                        mode=1, width=None)
        self.add_flight(900014, area_m2=8980.0, start=s0 + 720, end=s0 + 780,
                        width=None)
        self.add_flight(900015, area_m2=4693.0, start=s0 + 1000, end=s0 + 1300)
        self.add_flight(900016, area_m2=0.0, start=s0 + 2000, end=s0 + 2300,
                        mode=0, width=None)
        items = [
            source(900011, 'card', card_json(900011, start=s0, end=s0 + 300)),
            source(900011, 'v4', v4_bytes(counter_series(
                linear(0.0, 15.0, 300), start_ms=s0 * 1000, step_ms=1000)),
                request_context={'path': '/airline_v4/900011/x'}),
            source(900012, 'card', card_json(900012, area=8980.0, start=s0 + 400,
                                             end=s0 + 700, width=6.66)),
            source(900014, 'card', card_json(900014, area=8980.0, start=s0 + 720,
                                             end=s0 + 780, width=None)),
            source(900014, 'v4', v4_bytes(counter_series(
                [14.43] * 61, start_ms=(s0 + 720) * 1000, step_ms=1000)),
                request_context={'path': '/airline_v4/900014/x'}),
            source(900015, 'card', card_json(900015, area=4693.0, start=s0 + 1000,
                                             end=s0 + 1300)),
            source(900016, 'card', card_json(900016, area=0.0, start=s0 + 2000,
                                             end=s0 + 2300, mode=0, width=None)),
            source(900016, 'v4', v4_bytes(counter_series(
                [None] * 301, start_ms=(s0 + 2000) * 1000, step_ms=1000,
                mode=0, spray_flag=1, flow=500)),
                request_context={'path': '/airline_v4/900016/x'}),
        ]
        resp = self.post_sources(items)
        self.assertEqual(resp.get_json()['errors'], 0, resp.get_json())

    def test_dry_run_writes_nothing_and_apply_is_idempotent(self):
        self.seed_matrix()
        dry = self.recalc('--dry-run')
        self.assertEqual(dry.returncode, 0, dry.stdout + dry.stderr)
        self.assertEqual(self.raw('SELECT COUNT(*) FROM dji_area_calculations')[0][0], 0)
        self.assertIn('Nothing was written', dry.stdout)
        first = self.recalc('--apply')
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        self.assertEqual(self.raw('SELECT COUNT(*) FROM dji_area_calculations')[0][0], 6)
        second = self.recalc('--apply')
        self.assertIn('calc writes       : unchanged=6', second.stdout)
        self.assertEqual(self.raw('SELECT COUNT(*) FROM dji_area_calculations')[0][0], 6)
        self.assertEqual(self.raw('SELECT COUNT(*) FROM dji_area_calculations '
                                  'WHERE superseded_at IS NOT NULL')[0][0], 0)

    def test_statuses_and_null_semantics_in_the_database(self):
        self.seed_matrix()
        applied = self.recalc('--apply')
        self.assertEqual(applied.returncode, 0, applied.stdout + applied.stderr)
        rows = {r[0]: r for r in self.raw(
            'SELECT flight_id, area_status, corrected_recorded_area_m2, '
            'typeof(corrected_recorded_area_m2), controller_delta_area_m2, '
            'typeof(controller_delta_area_m2), raw_area_m2, '
            'application_without_area, structural_candidate, '
            'scalar_source_check, aggregation_eligibility, billable_area_m2, '
            'report_start_date FROM dji_area_calculations WHERE '
            'superseded_at IS NULL')}
        self.assertEqual(rows[900011][1], rs.RAW_CORROBORATED)
        self.assertEqual(rows[900011][2], 10000.0)
        self.assertAlmostEqual(rows[900011][4], 10000.0, 2)
        self.assertEqual(rows[900014][1], rs.COUNTER_FLAT_RAW_OVERSTATED)
        self.assertEqual(rows[900014][2], 0.0)
        self.assertEqual(rows[900014][3], 'real')
        self.assertEqual(rows[900014][6], 8980.0)
        self.assertEqual(rows[900014][8], 1)
        self.assertEqual(rows[900014][9], 1)
        self.assertEqual(rows[900015][1], rs.RAW_UNVERIFIED)
        self.assertEqual(rows[900015][5], 'null')
        self.assertEqual(rows[900016][1], rs.APPLICATION_WITHOUT_MEASURED_AREA)
        self.assertEqual(rows[900016][3], 'null')
        self.assertEqual(rows[900016][7], 1)
        for row in rows.values():
            self.assertIsNone(row[11])  # billable stays NULL
            self.assertEqual(row[12], '2026-08-01')
        # base and bridge of the retained candidate
        base = self.raw('SELECT candidate_base_flight_id, bridge_flight_ids_json '
                        'FROM dji_area_calculations WHERE flight_id=900014')[0]
        self.assertEqual(base[0], 900012)
        self.assertEqual(json.loads(base[1]), [900013])
        # area_ha untouched
        self.assertEqual(self.raw('SELECT area_ha FROM drone_flights WHERE '
                                  'dji_flight_id=900014')[0][0], 0.898)

    def test_new_v4_revision_supersedes_and_keeps_history(self):
        self.seed_matrix()
        applied = self.recalc('--apply')
        self.assertEqual(applied.returncode, 0, applied.stdout + applied.stderr)
        # a later V4 for 900015 arrives: full window, agrees with RAW
        self.post_sources([source(900015, 'v4', v4_bytes(counter_series(
            linear(0.0, 7.04, 300), start_ms=(START + 1000) * 1000,
            step_ms=1000)),
            request_context={'path': '/airline_v4/900015/x'})])
        out = self.recalc('--apply')
        self.assertEqual(out.returncode, 0, out.stdout)
        rows = self.raw('SELECT area_status, superseded_at IS NULL FROM '
                        'dji_area_calculations WHERE flight_id=900015 ORDER BY id')
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0][0], rs.RAW_UNVERIFIED)
        self.assertEqual(rows[0][1], 0)
        self.assertEqual(rows[1][0], rs.RAW_CORROBORATED)
        self.assertEqual(rows[1][1], 1)

    def test_missing_database_exits_2_and_creates_no_file(self):
        missing = TEST_DB_PATH + '.missing'
        out = subprocess.run([sys.executable, TOOL, '--from', '2026-08-01',
                              '--to', '2026-08-01', '--db', missing, '--dry-run'],
                             capture_output=True, text=True)
        self.assertEqual(out.returncode, 2)
        self.assertFalse(os.path.exists(missing))


class LandSnapshot(Base):

    def node(self, uuid, name, md5, external='ext-1'):
        return {'uuid': uuid, 'externalId': external, 'name': name,
                'totalArea': 44.5, 'workArea': 40.0, 'totalObstacleArea': 0,
                'landType': 'PLANT_LAND', 'createdAt': '2026-08-01T10:00:00+08:00',
                'updatedAt': '2026-08-01T10:00:00+08:00',
                'position': {'lat': 39.7, 'lng': 64.4},
                'geometry': {'storage': {'signedURL': 'https://x.invalid/g?Signature=abc',
                                         'uuid': 'g-1', 'contentMd5': md5}},
                'serialNumber': 'P1'}

    def post(self, body):
        body['token'] = TOKEN
        return self.client.post('/drones/api/land_snapshot_sync', json=body)

    def test_snapshot_revisions_are_append_only(self):
        geo = b'{"type":"FeatureCollection","features":[{"type":"Feature","properties":{"funcType":"PlantZone"},"geometry":{"type":"Polygon","coordinates":[[[64.4,39.7,0],[64.41,39.7,0],[64.41,39.71,0],[64.4,39.7,0]]]}}]}'
        md5 = hashlib.md5(geo).hexdigest()
        u1 = '11111111-2222-3333-4444-555555555555'
        first = self.post({'snapshot': {'capture_run_id': 'SNAP-1',
                                        'captured_at_utc': '2026-09-05 07:00:00',
                                        'expected_count': 1, 'final': True,
                                        'complete': True},
                           'lands': [self.node(u1, 'Karvon', md5)],
                           'geometries': [{'content_md5': md5,
                                           'body_b64': base64.b64encode(geo).decode()}]})
        body = first.get_json()
        self.assertEqual(first.status_code, 200, body)
        self.assertEqual((body['lands_new'], body['geometries_new']), (1, 1))
        # signedURL never stored
        raw = self.raw('SELECT raw_json FROM dji_land_revisions')[0][0]
        self.assertNotIn('Signature', raw)
        self.assertNotIn('signedURL', raw)
        # second snapshot: name edited -> new revision, old kept
        second = self.post({'snapshot': {'capture_run_id': 'SNAP-2',
                                         'expected_count': 1, 'final': True,
                                         'complete': True},
                            'lands': [self.node(u1, 'Karvon 7.04 ga', md5)]})
        self.assertEqual(second.get_json()['lands_new'], 1)
        rows = self.raw('SELECT name, first_seen_snapshot_id, last_seen_snapshot_id, '
                        'seen_count FROM dji_land_revisions ORDER BY id')
        self.assertEqual([r[0] for r in rows], ['Karvon', 'Karvon 7.04 ga'])
        # third snapshot repeats the second: seen_count moves, no new row
        third = self.post({'snapshot': {'capture_run_id': 'SNAP-3', 'final': True},
                           'lands': [self.node(u1, 'Karvon 7.04 ga', md5)]})
        self.assertEqual(third.get_json()['lands_seen_before'], 1)
        self.assertEqual(self.raw('SELECT COUNT(*) FROM dji_land_revisions')[0][0], 2)
        geom = self.raw('SELECT md5_verified, body_blob FROM dji_land_geometries')[0]
        self.assertEqual(geom[0], 1)
        self.assertEqual(bytes(geom[1]), geo)
        # md5 mismatch is refused
        bad = self.post({'snapshot': {'capture_run_id': 'SNAP-4'},
                         'lands': [],
                         'geometries': [{'content_md5': 'f' * 32,
                                         'body_b64': base64.b64encode(geo).decode()}]})
        self.assertEqual(bad.get_json()['geometries_errors'], 1)
        # field_contours untouched by this path
        self.assertEqual(self.raw('SELECT COUNT(*) FROM field_contours')[0][0], 0)

    def test_tier1_via_snapshot_in_recalc(self):
        geo = b'{"type":"FeatureCollection","features":[{"type":"Feature","properties":{"funcType":"PlantZone"},"geometry":{"type":"Polygon","coordinates":[[[64.4,39.7,0],[64.41,39.7,0],[64.41,39.71,0],[64.4,39.7,0]]]}}]}'
        md5 = hashlib.md5(geo).hexdigest()
        u1 = '11111111-2222-3333-4444-555555555555'
        self.post({'snapshot': {'capture_run_id': 'SNAP-1', 'final': True,
                                'complete': True, 'expected_count': 1},
                   'lands': [self.node(u1, 'Karvon', md5)],
                   'geometries': [{'content_md5': md5,
                                   'body_b64': base64.b64encode(geo).decode()}]})
        self.add_flight(900021)
        self.post_sources([source(900021, 'card', card_json(900021, md5=u1 + '__' + md5))])
        applied = self.recalc('--apply')
        self.assertEqual(applied.returncode, 0, applied.stdout + applied.stderr)
        row = self.raw('SELECT field_attribution_tier, linked_land_uuid, '
                       'geometry_holder_land_uuid, historical_geometry_available, '
                       'field_name_at_snapshot FROM dji_field_attributions '
                       'WHERE flight_id=900021')[0]
        self.assertEqual(row[0], 'TIER1_EXACT')
        self.assertEqual(row[1], u1)
        self.assertEqual(row[2], u1)
        self.assertEqual(row[3], 1)
        self.assertEqual(row[4], 'Karvon')
        # area unaffected by the tier
        calc = self.raw('SELECT area_status FROM dji_area_calculations WHERE '
                        'flight_id=900021')[0]
        self.assertEqual(calc[0], rs.RAW_UNVERIFIED)


# ─── Разбор состязательного ревью: хранилище и конвейер ─────────────────────

class AdjudicatedStoreAndPipeline(Base):
    """Подтверждённые находки состязательного ревью, которые видны только на
    базе. У каждой -- отрицательный контроль на тех же данных."""

    GEO = (b'{"type":"FeatureCollection","features":[{"type":"Feature",'
           b'"properties":{"funcType":"PlantZone"},"geometry":{"type":'
           b'"Polygon","coordinates":[[[64.4,39.7,0],[64.41,39.7,0],'
           b'[64.41,39.71,0],[64.4,39.7,0]]]}}]}')

    def con(self):
        return store.connect(TEST_DB_PATH)

    def big_v4(self, flight_id, values, start):
        """Тело V4 крупнее порога inline: гарантированно уходит в файл.

        Добивка -- НЕИЗВЕСТНОЕ поле верхнего уровня, которое декодер
        пропускает. Нулевые байты дали бы «field number 0 is not legal»,
        и тест доказывал бы не потерю файла, а нечитаемый protobuf.
        """
        return (v4_bytes(counter_series(values, start_ms=start * 1000,
                                        step_ms=1000))
                + f_bytes(200, b'\x11' * 70000))

    # ── Тело источника: отсутствие файла -- StoreError, не OSError ──────
    def test_a_missing_body_file_is_a_store_error_not_an_oserror(self):
        self.add_flight(FLIGHT_A)
        big = self.big_v4(FLIGHT_A, [0.0, 15.0], START)
        resp = self.post_sources([source(FLIGHT_A, 'v4', big)])
        self.assertEqual(resp.get_json()['errors'], 0, resp.get_json())
        root = store.source_root(TEST_DB_PATH)
        con = self.con()
        try:
            row = con.execute('SELECT * FROM dji_source_revisions').fetchone()
            self.assertEqual(row['storage_kind'], store.STORAGE_FILE)
            path = os.path.join(root, row['body_path'].replace('/', os.sep))
            self.assertEqual(store.read_body(root, row), big)   # контроль
            os.unlink(path)
            with self.assertRaises(store.StoreError):
                store.read_body(root, row)
            # и сборка доказательств переживает это, а не роняет весь пакет
            store.refresh_flight_evidence(con, root, FLIGHT_A)
        finally:
            con.close()

    def test_a_later_batch_still_succeeds_after_a_body_is_lost(self):
        self.add_flight(FLIGHT_A)
        big = self.big_v4(FLIGHT_A, [0.0, 15.0], START)
        self.post_sources([source(FLIGHT_A, 'v4', big)])
        root = store.source_root(TEST_DB_PATH)
        con = self.con()
        try:
            row = con.execute('SELECT * FROM dji_source_revisions').fetchone()
            os.unlink(os.path.join(root, row['body_path'].replace('/', os.sep)))
        finally:
            con.close()
        again = self.post_sources([source(FLIGHT_A, 'card',
                                          card_json(FLIGHT_A))])
        self.assertEqual(again.status_code, 200, again.get_json())
        self.assertEqual(again.get_json()['errors'], 0, again.get_json())

    # ── Контекст запроса проверяется на секрет так же, как тело ─────────
    def test_a_signed_url_in_request_context_is_refused(self):
        self.add_flight(FLIGHT_A)
        signed = ('https://storage.invalid/objects/airline_v4/%d/f.bin'
                  '?Expires=1789000000&OSSAccessKeyId=NOT-REAL'
                  '&Signature=NOT-REAL' % FLIGHT_A)
        resp = self.post_sources([source(
            FLIGHT_A, 'card', card_json(FLIGHT_A),
            request_context={'path': signed, 'association': 'url_path'})])
        body = resp.get_json()
        self.assertEqual(body['errors'], 1, body)
        self.assertEqual(self.raw(
            'SELECT COUNT(*) FROM dji_source_revisions')[0][0], 0)
        self.assertNotIn('Signature=NOT-REAL', json.dumps(body))

    def test_control_a_clean_request_context_is_accepted(self):
        self.add_flight(FLIGHT_A)
        resp = self.post_sources([source(
            FLIGHT_A, 'card', card_json(FLIGHT_A),
            request_context={'path': '/airline_v4/%d/f.bin' % FLIGHT_A,
                             'association': 'url_path'})])
        self.assertEqual(resp.get_json()['errors'], 0, resp.get_json())
        stored = self.raw('SELECT request_context_json FROM '
                          'dji_source_revisions')[0][0]
        self.assertIn('/airline_v4/', stored)

    # ── Писатели открывают немедленную транзакцию ───────────────────────
    def test_the_writer_takes_the_write_lock_at_begin(self):
        con = self.con()
        try:
            store.begin_immediate(con)
            other = sqlite3.connect(TEST_DB_PATH, timeout=0.2)
            try:
                with self.assertRaises(sqlite3.OperationalError):
                    other.execute('BEGIN IMMEDIATE')
            finally:
                other.close()
            con.execute('ROLLBACK')
        finally:
            con.close()

    # ── Отпечаток каталога чувствителен к проверке байтов ───────────────
    def seed_geometry(self, verified, snapshot='SNAP-X', name='Karvon'):
        md5 = hashlib.md5(self.GEO).hexdigest()
        con = self.con()
        try:
            store.begin_immediate(con)
            snap = store.create_land_snapshot(con, None, snapshot)
            store.upsert_land_revision(con, snap, self.land_node(md5, name))
            # expected_md5=None -> байты приняты, но НЕ сверены (md5_verified=0)
            store.upsert_land_geometry(con, self.GEO,
                                       md5 if verified else None)
            con.execute('COMMIT')
        finally:
            con.close()
        return md5

    def land_node(self, md5, name):
        return ev.parse_land_node({
            'uuid': 'u-1', 'externalId': 'e', 'name': name,
            'totalArea': 4.0, 'workArea': 4.0, 'totalObstacleArea': 0.0,
            'landType': 'PLANT_LAND', 'serialNumber': 'P1',
            'createdAt': '2026-08-01T10:00:00+08:00',
            'updatedAt': '2026-08-01T10:00:00+08:00',
            'position': {'lat': 39.7, 'lng': 64.4},
            'geometry': {'storage': {'uuid': 'g-1', 'contentMd5': md5}}})

    def test_catalog_fingerprint_moves_when_the_bytes_get_verified(self):
        md5 = self.seed_geometry(verified=False)
        con = self.con()
        try:
            before = store.SqliteCatalog(con).catalog_state_sha()
            self.assertEqual(con.execute(
                'SELECT md5_verified FROM dji_land_geometries').fetchone()[0], 0)
            store.begin_immediate(con)
            con.execute('UPDATE dji_land_geometries SET md5_verified=1 '
                        'WHERE content_md5=?', (md5,))
            con.execute('COMMIT')
            after = store.SqliteCatalog(con).catalog_state_sha()
            self.assertNotEqual(before, after)
            # ни одной строки не добавлено -- меняется только проверка байтов
            self.assertEqual(con.execute(
                'SELECT COUNT(*) FROM dji_land_geometries').fetchone()[0], 1)
        finally:
            con.close()

    # ── Одно поле с двумя ревизиями -- один полигон ─────────────────────
    def test_two_metadata_revisions_of_one_field_are_one_polygon(self):
        md5 = self.seed_geometry(verified=True)
        con = self.con()
        try:
            self.assertEqual(len(store.SqliteCatalog(con).current_polygons()), 1)
            store.begin_immediate(con)
            snap = store.create_land_snapshot(con, None, 'SNAP-Y')
            store.upsert_land_revision(con, snap,
                                       self.land_node(md5, 'Karvon 7.04 ga'))
            con.execute('COMMIT')
            self.assertEqual(con.execute(
                'SELECT COUNT(*) FROM dji_land_revisions').fetchone()[0], 2)
            polygons = store.SqliteCatalog(con).current_polygons()
            self.assertEqual(len(polygons), 1)
            self.assertEqual(polygons[0]['name'], 'Karvon 7.04 ga')
        finally:
            con.close()

    # ── Нечитаемое тело V4 не остаётся деградацией навсегда ─────────────
    def test_a_v4_body_that_comes_back_rewrites_the_row(self):
        s0 = START
        self.add_flight(900031, area_m2=10000.0, start=s0, end=s0 + 60)
        flat = self.big_v4(900031, [14.43] * 61, s0)
        self.post_sources([
            source(900031, 'card', card_json(900031, start=s0, end=s0 + 60)),
            source(900031, 'v4', flat,
                   request_context={'path': '/airline_v4/900031/x'})])
        root = store.source_root(TEST_DB_PATH)
        con = self.con()
        try:
            row = con.execute("SELECT * FROM dji_source_revisions WHERE "
                              "source_type='v4'").fetchone()
            body_path = os.path.join(root, row['body_path'].replace('/', os.sep))
        finally:
            con.close()
        hidden = body_path + '.moved'
        os.rename(body_path, hidden)
        first = self.recalc('--apply')
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        degraded = self.raw('SELECT area_status, anomaly_flags_json '
                            'FROM dji_area_calculations '
                            'WHERE flight_id=900031 AND superseded_at IS NULL')[0]
        self.assertEqual(degraded[0], rs.RAW_UNVERIFIED)
        self.assertIn('V4_BODY_UNREADABLE', degraded[1])
        os.rename(hidden, body_path)
        second = self.recalc('--apply')
        self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
        repaired = self.raw('SELECT area_status, corrected_recorded_area_m2 '
                            'FROM dji_area_calculations WHERE flight_id=900031 '
                            'AND superseded_at IS NULL')[0]
        self.assertEqual(repaired[0], rs.COUNTER_FLAT_RAW_OVERSTATED)
        self.assertEqual(repaired[1], 0.0)
        # прежняя строка не переписана, а вытеснена
        self.assertEqual(self.raw(
            'SELECT COUNT(*) FROM dji_area_calculations WHERE flight_id=900031 '
            'AND superseded_at IS NOT NULL')[0][0], 1)

    # ── Свидетельство канала не зависит от дат в командной строке ───────
    def test_channel_evidence_is_the_month_not_the_cli_window(self):
        s0 = START
        later = s0 + 15 * 86400          # тот же месяц, вне окна пересчёта
        self.add_flight(900041, area_m2=5000.0, start=s0, end=s0 + 60)
        self.add_flight(900042, area_m2=5000.0, start=later, end=later + 60)
        self.post_sources([
            source(900041, 'card', card_json(900041, start=s0, end=s0 + 60)),
            source(900041, 'v4', v4_bytes(counter_series(
                [0.0] * 61, start_ms=s0 * 1000, step_ms=1000)),
                request_context={'path': '/airline_v4/900041/x'}),
            source(900042, 'card', card_json(900042, start=later,
                                             end=later + 60)),
            source(900042, 'v4', v4_bytes(counter_series(
                [0.0] * 61, start_ms=later * 1000, step_ms=1000,
                spray_flag=1, flow=700)),
                request_context={'path': '/airline_v4/900042/x'})])
        day = self.recalc('--apply')
        self.assertEqual(day.returncode, 0, day.stdout + day.stderr)
        one = self.raw('SELECT application_channel_quality, '
                       'application_activity FROM dji_area_calculations '
                       'WHERE flight_id=900041 AND superseded_at IS NULL')[0]
        month = subprocess.run(
            [sys.executable, TOOL, '--from', '2026-08-01', '--to',
             '2026-08-31', '--db', TEST_DB_PATH, '--quiet', '--apply'],
            capture_output=True, text=True,
            cwd=os.path.dirname(os.path.dirname(TOOL)))
        self.assertEqual(month.returncode, 0, month.stdout + month.stderr)
        after = self.raw('SELECT application_channel_quality, '
                         'application_activity FROM dji_area_calculations '
                         'WHERE flight_id=900041 AND superseded_at IS NULL')[0]
        self.assertEqual(tuple(one), tuple(after))
        self.assertEqual(after[0], rs.CH_INFORMATIVE)
        self.assertEqual(after[1], rs.ACT_NOT_OBSERVED)
        # и ни одной новой версии строки: вход не менялся
        self.assertEqual(self.raw(
            'SELECT COUNT(*) FROM dji_area_calculations WHERE '
            'flight_id=900041')[0][0], 1)


if __name__ == '__main__':
    unittest.main()
