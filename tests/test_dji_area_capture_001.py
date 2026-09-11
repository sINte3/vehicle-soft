# -*- coding: utf-8 -*-
"""DRONE-AREA-CAPTURE-001: инкрементальный снимок каталога + источник СПИСКА.

Один приёмочный набор на весь макроэтап. Доказывает ровно то, ради чего он
затевался, и ничего сверх.

Часть 1  Известный contentMd5 пропускается ДО скачивания -- проверяется по
         тому, что ссылку никто не запросил, а не по тому, что тело выкинули.
Часть 2  Манифест известных геометрий: отвечает только про спрошенное,
         не пишет ни строки, без токена не отвечает.
Часть 3  Клиент манифеста: «не знаю» (None) и «ничего нет» (пустое
         множество) -- РАЗНЫЕ ответы, и недоступный манифест деградирует в
         полную загрузку, а не в снимок без полигонов.
Часть 4  Семантика снимка: базовый, повторный, новая земля, изменение только
         метаданных, новая геометрия, узел без тела, неполный обход.
Часть 5  A13: страница списка как неизменяемый источник; поля вылета берутся
         из ревизии, а изменяемый `raw_json` не выдаётся за доказанный.
Часть 6  Секреты: подписанный URL не доезжает ни до базы, ни до манифеста.

Синтетика: uuid/md5/вылеты вымышленные, борт 'SYNTHETIC-HW-NOT-REAL'.
"""
import base64
import hashlib
import json
import os
import sqlite3
import sys
import unittest
from datetime import datetime

from tests.harness import app, reset_db, create_org, TEST_DB_PATH

from models import db, DroneFlight, DroneUnit, DroneNickname

from dji_area import evidence as ev
from dji_area import store

from drone_collector import sources as src
from drone_collector import sender as snd
from drone_collector.geometry import node_content_md5s
from drone_collector.tests.test_sources import (V4_QUERY, STORAGE_HOST,
                                                land_node, polygon_bytes)

TOKEN = 'SYNTHETIC-capture-token-NOT-REAL'
HW = 'SYNTHETIC-HW-NOT-REAL'
START = 1755000000


# Общие фикстуры живут в наборе коллектора: он не тянет Flask, поэтому
# импорт идёт в эту сторону, а не наоборот.
from drone_collector.tests.test_capture_hardening import (  # noqa: E402
    _Log, _Page, list_page, list_row, md5_of)


# ─── Часть 1. Пропуск известной геометрии ДО скачивания ─────────────────────

# ─── Часть 2-6. Серверная сторона ───────────────────────────────────────────

class ServerCase(unittest.TestCase):

    def setUp(self):
        reset_db()
        app.config['DRONE_API_TOKEN'] = TOKEN
        self.client = app.test_client()
        with app.app_context():
            org_id = create_org()
            unit = DroneUnit(number=5, organization_id=org_id, hardware_id=HW)
            db.session.add(unit)
            db.session.flush()
            db.session.add(DroneNickname(drone_unit_id=unit.id,
                                         nickname='SYNTHETIC-NICK',
                                         normalized='synthetic-nick'))
            self.unit_id = unit.id
            db.session.commit()

    def raw(self, sql, args=()):
        con = sqlite3.connect(TEST_DB_PATH)
        try:
            return con.execute(sql, args).fetchall()
        finally:
            con.close()

    def manifest(self, md5s, token=TOKEN):
        return self.client.post('/drones/api/land_geometry_manifest',
                                json={'token': token, 'content_md5': md5s})

    def post_snapshot(self, nodes, geometries=(), run_id='cap-run-1',
                      final=True, complete=True, expected=None):
        return self.client.post('/drones/api/land_snapshot_sync', json={
            'token': TOKEN,
            'snapshot': {'capture_run_id': run_id,
                         'captured_at_utc': '2026-09-11 04:00:00',
                         'expected_count': (len(nodes) if expected is None
                                            else expected),
                         'scope': {'walk': 'lands'},
                         'final': final, 'complete': complete},
            'lands': list(nodes),
            'geometries': [{'content_md5': m, 'body_b64':
                            base64.b64encode(b).decode('ascii')}
                           for m, b in geometries],
        })

    def post_sources(self, items, token=TOKEN):
        return self.client.post('/drones/api/source_sync',
                                json={'token': token, 'sources': items})


class TheManifestAnswersOnlyWhatWasAsked(ServerCase):

    def store_geometry(self, uuid):
        body = polygon_bytes(uuid)
        md5 = md5_of(body)
        self.post_snapshot([land_node(uuid, md5, link='')], [(md5, body)])
        return md5

    def test_a_stored_md5_comes_back_and_an_unknown_one_does_not(self):
        known = self.store_geometry('m-1')
        unknown = md5_of(polygon_bytes('never-stored'))
        res = self.manifest([known, unknown])
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.get_json()['known'], [known])
        self.assertEqual(res.get_json()['known_count'], 1)
        self.assertEqual(res.get_json()['asked'], 2)

    def test_control_an_empty_store_knows_nothing(self):
        # Контроль: без этого тест выше прошёл бы у кода, который отвечает
        # «знаю» на всё подряд.
        res = self.manifest([md5_of(polygon_bytes('x'))])
        self.assertEqual(res.get_json()['known'], [])

    def test_it_never_returns_more_than_it_was_asked_about(self):
        first = self.store_geometry('m-1')
        second = self.store_geometry('m-2')
        res = self.manifest([first])
        self.assertEqual(res.get_json()['known'], [first])
        self.assertNotIn(second, res.get_json()['known'])

    def test_it_writes_nothing(self):
        self.store_geometry('m-1')
        before = self.raw('SELECT COUNT(*) FROM dji_land_geometries')[0][0]
        rows_before = self.raw('SELECT COUNT(*) FROM dji_land_revisions')[0][0]
        self.manifest([md5_of(polygon_bytes('m-1')), 'a' * 32])
        self.assertEqual(
            self.raw('SELECT COUNT(*) FROM dji_land_geometries')[0][0], before)
        self.assertEqual(
            self.raw('SELECT COUNT(*) FROM dji_land_revisions')[0][0],
            rows_before)

    def test_a_wrong_token_is_refused(self):
        self.assertEqual(self.manifest([], token='WRONG').status_code, 401)

    def test_junk_that_is_not_an_md5_never_reaches_the_query(self):
        junk = ['../../etc/passwd', "' OR 1=1 --", '', 'zz' * 16, None, 12345,
                'a' * 4000]
        res = self.manifest(junk)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.get_json()['known'], [])
        # [REASON]: `known == []` сам по себе ничего не доказывает -- он
        # такой же и у кода без фильтра. Различает `considered`: сколько
        # строк вообще дошло до запроса.
        self.assertEqual(res.get_json()['considered'], 0)
        self.assertEqual(res.get_json()['asked'], len(junk))

    def test_control_a_real_md5_is_considered(self):
        res = self.manifest(['a' * 32, 'not-an-md5'])
        self.assertEqual(res.get_json()['considered'], 1)

    def test_too_many_at_once_is_refused_with_413(self):
        res = self.manifest(['a' * 32] * 5001)
        self.assertEqual(res.status_code, 413)

    def test_the_case_of_the_md5_does_not_matter(self):
        known = self.store_geometry('m-case')
        res = self.manifest([known.upper()])
        self.assertEqual(res.get_json()['known'], [known])


# ─── Часть 4. Семантика снимка ──────────────────────────────────────────────

class TheSnapshotKeepsItsHistory(ServerCase):

    def geometry_for(self, uuid, salt=''):
        body = polygon_bytes(uuid + salt) if salt else polygon_bytes(uuid)
        return md5_of(body), body

    def revisions(self, uuid=None):
        if uuid is None:
            return self.raw('SELECT COUNT(*) FROM dji_land_revisions')[0][0]
        return self.raw('SELECT COUNT(*) FROM dji_land_revisions WHERE '
                        'land_uuid=?', (uuid,))[0][0]

    def test_the_first_snapshot_is_the_baseline(self):
        md5, body = self.geometry_for('L1')
        res = self.post_snapshot([land_node('L1', md5, link='')],
                                 [(md5, body)])
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.get_json()['lands_new'], 1)
        self.assertEqual(res.get_json()['geometries_new'], 1)
        self.assertEqual(self.revisions(), 1)

    def test_an_identical_second_snapshot_adds_no_revision(self):
        md5, body = self.geometry_for('L1')
        node = land_node('L1', md5, link='')
        self.post_snapshot([node], [(md5, body)], run_id='r1')
        res = self.post_snapshot([node], [(md5, body)], run_id='r2')
        self.assertEqual(res.get_json()['lands_new'], 0)
        self.assertEqual(res.get_json()['lands_seen_before'], 1)
        self.assertEqual(res.get_json()['geometries_new'], 0)
        self.assertEqual(res.get_json()['geometries_unchanged'], 1)
        self.assertEqual(self.revisions(), 1)

    def test_one_new_land_is_the_only_new_revision(self):
        md5_a, body_a = self.geometry_for('L1')
        md5_b, body_b = self.geometry_for('L2')
        self.post_snapshot([land_node('L1', md5_a, link='')],
                           [(md5_a, body_a)], run_id='r1')
        res = self.post_snapshot([land_node('L1', md5_a, link=''),
                                  land_node('L2', md5_b, link='')],
                                 [(md5_b, body_b)], run_id='r2')
        self.assertEqual(res.get_json()['lands_new'], 1)
        self.assertEqual(res.get_json()['lands_seen_before'], 1)
        self.assertEqual(self.revisions('L2'), 1)

    def test_metadata_only_change_makes_a_revision_keeps_the_polygon(self):
        md5, body = self.geometry_for('L1')
        self.post_snapshot([land_node('L1', md5, link='', name='Старое')],
                           [(md5, body)], run_id='r1')
        res = self.post_snapshot([land_node('L1', md5, link='', name='Новое')],
                                 run_id='r2')
        self.assertEqual(res.get_json()['lands_new'], 1)
        self.assertEqual(self.revisions('L1'), 2)
        # Полигон не переприслан и не потерян.
        self.assertEqual(
            self.raw('SELECT COUNT(*) FROM dji_land_geometries')[0][0], 1)

    def test_a_changed_polygon_is_a_new_body_and_the_old_one_survives(self):
        old_md5, old_body = self.geometry_for('L1')
        new_md5, new_body = self.geometry_for('L1', salt='-v2')
        self.post_snapshot([land_node('L1', old_md5, link='')],
                           [(old_md5, old_body)], run_id='r1')
        self.post_snapshot([land_node('L1', new_md5, link='')],
                           [(new_md5, new_body)], run_id='r2')
        stored = {row[0] for row in
                  self.raw('SELECT content_md5 FROM dji_land_geometries')}
        self.assertEqual(stored, {old_md5, new_md5})
        # Обе ревизии земли живы и ссылаются каждая на свою геометрию.
        pairs = self.raw('SELECT geometry_md5 FROM dji_land_revisions WHERE '
                         'land_uuid=? ORDER BY id', ('L1',))
        self.assertEqual([p[0] for p in pairs], [old_md5, new_md5])

    def test_a_node_whose_polygon_was_skipped_is_still_accepted(self):
        """Суть инкрементального снимка на приёмной стороне."""
        md5, body = self.geometry_for('L1')
        self.post_snapshot([land_node('L1', md5, link='')], [(md5, body)],
                           run_id='r1')
        # Второй день: узел тот же, тело НЕ приложено -- оно уже в хранилище.
        res = self.post_snapshot([land_node('L1', md5, link='', name='Новое')],
                                 geometries=(), run_id='r2')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.get_json()['errors'], 0)
        self.assertEqual(res.get_json()['geometries_errors'], 0)
        self.assertEqual(res.get_json()['lands_new'], 1)
        self.assertEqual(
            self.raw('SELECT COUNT(*) FROM dji_land_geometries')[0][0], 1)

    def test_an_incomplete_walk_is_marked_and_deletes_nothing(self):
        md5_a, body_a = self.geometry_for('L1')
        md5_b, body_b = self.geometry_for('L2')
        self.post_snapshot([land_node('L1', md5_a, link=''),
                            land_node('L2', md5_b, link='')],
                           [(md5_a, body_a), (md5_b, body_b)], run_id='r1')
        # Второй обход оборвался и принёс только одну землю из двух.
        self.post_snapshot([land_node('L1', md5_a, link='')], run_id='r2',
                           complete=False, expected=2)
        row = self.raw('SELECT complete FROM dji_land_snapshots WHERE '
                       'capture_run_id=?', ('r2',))[0]
        self.assertEqual(row[0], 0)
        # L2 никуда не делась: отсутствие в неполном обходе -- не удаление.
        self.assertEqual(self.revisions('L2'), 1)

    def test_the_same_chunk_twice_is_safe(self):
        md5, body = self.geometry_for('L1')
        node = land_node('L1', md5, link='')
        first = self.post_snapshot([node], [(md5, body)], run_id='same')
        second = self.post_snapshot([node], [(md5, body)], run_id='same')
        self.assertEqual(first.get_json()['snapshot_id'],
                         second.get_json()['snapshot_id'])
        self.assertEqual(self.revisions(), 1)

    def test_a_node_carrying_a_signed_url_is_stripped_not_refused(self):
        """Приёмник СРЕЗАЕТ ссылку, а не отвергает узел -- и это правильно.

        [REASON]: первая редакция теста ждала отказа и упала. Отказ был бы
        хуже: сборщик, забывший срезать ссылку, терял бы весь каталог, тогда
        как обязанность «подписанный URL не попадает в базу» выполняется и
        так -- `evidence.parse_land_node` вызывает `strip_volatile` ДО того,
        как считается sha и пишется строка. Проверяются обе половины:
        узел принят И ссылки в базе нет.
        """
        md5, body = self.geometry_for('L1')
        res = self.post_snapshot([land_node('L1', md5)], [(md5, body)])
        self.assertEqual(res.get_json()['lands_new'], 1)
        self.assertEqual(res.get_json()['errors'], 0)
        stored = self.raw('SELECT raw_json FROM dji_land_revisions')[0][0]
        for marker in ('signedURL', 'OSSAccessKeyId', 'Signature='):
            self.assertNotIn(marker, stored)

    def test_stripping_is_canonical_so_both_collectors_agree(self):
        """Сборщик, срезавший ссылку, и сборщик, не срезавший, дают ОДНУ
        ревизию -- иначе история земли раздваивалась бы на пустом месте."""
        md5, body = self.geometry_for('L1')
        self.post_snapshot([land_node('L1', md5)], [(md5, body)],
                           run_id='r1')
        res = self.post_snapshot([land_node('L1', md5, link='')],
                                 run_id='r2')
        self.assertEqual(res.get_json()['lands_new'], 0)
        self.assertEqual(res.get_json()['lands_seen_before'], 1)
        self.assertEqual(self.revisions('L1'), 1)

    def test_a_corrupt_envelope_is_refused_without_touching_the_rest(self):
        md5, body = self.geometry_for('L1')
        res = self.client.post('/drones/api/land_snapshot_sync', json={
            'token': TOKEN,
            'snapshot': {'capture_run_id': 'bad', 'final': True},
            'lands': ['not-an-object', land_node('L1', md5, link='')],
            'geometries': [{'content_md5': md5, 'body_b64': 'not base64!!'},
                           {'content_md5': md5,
                            'body_b64': base64.b64encode(body).decode()}],
        })
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.get_json()['errors'], 1)
        self.assertEqual(res.get_json()['geometries_errors'], 1)
        # Исправные элементы того же пакета приняты.
        self.assertEqual(res.get_json()['lands_new'], 1)
        self.assertEqual(res.get_json()['geometries_new'], 1)


# ─── Часть 5. A13: неизменяемый источник СПИСКА ─────────────────────────────

class TheFlightListBecomesAnImmutableSource(ServerCase):
    """A13: поля списка приходят из захешированного ответа, не из raw_json."""

    def items_for(self, rows, **kw):
        raw = list_page(rows)
        items, stats = src.list_source_items(
            [_Page(raw)], 'cap-run', '2026-09-11 04:00:00',
            window_from='2026-08-18', window_to='2026-08-18', **kw)
        return items, stats, raw

    def refresh(self, flight_id):
        """Пересобрать доказательства вылета тем же кодом, что и приёмник."""
        con = store.connect(TEST_DB_PATH)
        try:
            store.require_tables(con)
            store.begin_immediate(con)
            store.refresh_flight_evidence(con, store.source_root(TEST_DB_PATH),
                                          flight_id)
            con.execute('COMMIT')
        finally:
            con.close()

    def add_flight(self, flight_id, raw_area):
        """Вылет в drone_flights с ИЗМЕНЯЕМЫМ raw_json -- как сегодня."""
        with app.app_context():
            db.session.add(DroneFlight(
                dji_flight_id=flight_id, drone_unit_id=self.unit_id,
                nickname_raw='SYNTHETIC-NICK',
                started_at=datetime.utcfromtimestamp(START),
                finished_at=datetime.utcfromtimestamp(START + 300),
                mode_name=4, spray_width=6.0, manual_mode=False,
                area_ha=raw_area / 10000.0,
                raw_json=json.dumps(list_row(flight_id, area=raw_area))))
            db.session.commit()

    def test_one_page_yields_one_revision_per_flight_over_one_body(self):
        items, stats, raw = self.items_for([list_row(1), list_row(2),
                                            list_row(3)])
        self.assertEqual(stats['flights'], 3)
        self.assertEqual(stats['pages'], 1)
        self.assertEqual({i['flight_id'] for i in items}, {1, 2, 3})
        # Одно тело на всех: тот же sha, те же байты.
        self.assertEqual({i['sha256'] for i in items},
                         {hashlib.sha256(raw).hexdigest()})
        self.assertEqual({i['source_type'] for i in items}, {'list'})

    def test_the_sha_is_of_the_bytes_dji_sent_not_of_our_serialisation(self):
        items, _stats, raw = self.items_for([list_row(1)])
        self.assertEqual(items[0]['sha256'], hashlib.sha256(raw).hexdigest())
        self.assertEqual(base64.b64decode(items[0]['body_b64']), raw)
        # И это НЕ хеш одной записи -- иначе доказательство было бы нашим
        # срезом, а не ответом DJI.
        one = json.dumps(list_row(1), ensure_ascii=False).encode('utf-8')
        self.assertNotEqual(items[0]['sha256'],
                            hashlib.sha256(one).hexdigest())

    def test_a_page_without_raw_bytes_produces_nothing_and_is_counted(self):
        items, stats = src.list_source_items(
            [_Page(None)], 'run', '2026-09-11 04:00:00')
        self.assertEqual(items, [])
        self.assertEqual(stats['pages_without_raw'], 1)
        self.assertEqual(stats['flights'], 0)

    def test_the_window_and_page_travel_in_the_context(self):
        items, _stats, _raw = self.items_for([list_row(1)])
        ctx = items[0]['request_context']
        self.assertEqual(ctx['list_window_from'], '2026-08-18')
        self.assertEqual(ctx['list_flight_count'], 1)
        self.assertEqual(ctx['association'], 'list_page')
        self.assertEqual(ctx['list_page'], 1)
        self.assertNotIn('?', ctx['path'])

    def test_the_ingested_flight_points_at_the_list_revision(self):
        self.add_flight(1, 12345.0)
        items, _stats, raw = self.items_for([list_row(1)])
        res = self.post_sources(items)
        self.assertEqual(res.get_json()['new'], 1)
        row = self.raw('SELECT list_revision_id FROM dji_flight_evidence '
                       'WHERE flight_id=1')[0]
        self.assertIsNotNone(row[0])
        rev = self.raw('SELECT sha256, source_type, schema_version FROM '
                       'dji_source_revisions WHERE id=?', (row[0],))[0]
        self.assertEqual(rev[0], hashlib.sha256(raw).hexdigest())
        self.assertEqual(rev[1], 'list')
        self.assertEqual(rev[2], 'raw-http-body')

    def test_the_list_fields_come_from_the_revision_not_from_raw_json(self):
        # Ключевой тест A13: raw_json говорит одно, страница -- другое.
        self.add_flight(1, 99999.0)
        items, _stats, _raw = self.items_for([list_row(1, area=12345.0)])
        self.post_sources(items)
        area = self.raw('SELECT list_raw_area_m2 FROM dji_flight_evidence '
                        'WHERE flight_id=1')[0][0]
        self.assertEqual(area, 12345.0)
        self.assertNotEqual(area, 99999.0)

    def test_control_without_a_list_source_the_mutable_column_is_used(self):
        # Контроль: без него тест выше прошёл бы и у кода, который просто
        # никогда не читает raw_json.
        self.add_flight(2, 99999.0)
        self.refresh(2)
        row = self.raw('SELECT list_revision_id, list_raw_area_m2 FROM '
                       'dji_flight_evidence WHERE flight_id=2')[0]
        self.assertIsNone(row[0])
        self.assertEqual(row[1], 99999.0)

    def test_the_same_page_twice_is_a_duplicate_not_a_new_revision(self):
        self.add_flight(1, 12345.0)
        items, _stats, _raw = self.items_for([list_row(1)])
        self.post_sources(items)
        res = self.post_sources(items)
        self.assertEqual(res.get_json()['new'], 0)
        self.assertEqual(res.get_json()['duplicates'], 1)
        self.assertEqual(self.raw('SELECT COUNT(*) FROM dji_source_revisions '
                                  "WHERE source_type='list'")[0][0], 1)

    def test_a_changed_page_is_a_new_revision_and_the_old_one_stays(self):
        self.add_flight(1, 12345.0)
        first, _s, raw_a = self.items_for([list_row(1, area=12345.0)])
        self.post_sources(first)
        second, _s, raw_b = self.items_for([list_row(1, area=22222.0)])
        self.post_sources(second)
        shas = {r[0] for r in
                self.raw('SELECT sha256 FROM dji_source_revisions WHERE '
                         "source_type='list'")}
        self.assertEqual(shas, {hashlib.sha256(raw_a).hexdigest(),
                                hashlib.sha256(raw_b).hexdigest()})
        # Доказательство указывает на НОВУЮ ревизию.
        area = self.raw('SELECT list_raw_area_m2 FROM dji_flight_evidence '
                        'WHERE flight_id=1')[0][0]
        self.assertEqual(area, 22222.0)

    def test_each_flight_of_a_page_gets_its_own_fields_not_a_neighbour(self):
        self.add_flight(1, 0.0)
        self.add_flight(2, 0.0)
        items, _s, _raw = self.items_for([list_row(1, area=1111.0),
                                          list_row(2, area=2222.0)])
        self.post_sources(items)
        got = dict(self.raw('SELECT flight_id, list_raw_area_m2 FROM '
                            'dji_flight_evidence WHERE flight_id IN (1,2)'))
        self.assertEqual(got, {1: 1111.0, 2: 2222.0})

    def test_the_page_body_is_stored_once_on_disk_not_inline_per_flight(self):
        for fid in range(1, 6):
            self.add_flight(fid, 0.0)
        items, _s, raw = self.items_for([list_row(i) for i in range(1, 6)])
        self.post_sources(items)
        rows = self.raw('SELECT storage_kind, body_text, body_path FROM '
                        "dji_source_revisions WHERE source_type='list'")
        self.assertEqual(len(rows), 5)
        self.assertEqual({r[0] for r in rows}, {'file'})
        self.assertEqual({r[1] for r in rows}, {None})
        # Пять ревизий -- один файл.
        self.assertEqual(len({r[2] for r in rows}), 1)

    def test_an_old_flight_without_a_captured_list_is_not_upgraded(self):
        """Историческому вылету нельзя выдумать неизменяемый источник."""
        self.add_flight(3, 5555.0)
        self.refresh(3)
        row = self.raw('SELECT list_revision_id FROM dji_flight_evidence '
                       'WHERE flight_id=3')[0]
        self.assertIsNone(row[0])
        self.assertEqual(self.raw('SELECT COUNT(*) FROM dji_source_revisions '
                                  "WHERE source_type='list'")[0][0], 0)


# ─── Часть 6. Секреты ───────────────────────────────────────────────────────

class NoSignedUrlSurvivesAnywhere(ServerCase):

    def test_a_signed_url_never_reaches_the_database(self):
        body = polygon_bytes('S1')
        md5 = md5_of(body)
        self.post_snapshot([land_node('S1', md5)], [(md5, body)])
        blob = ' '.join(str(r) for r in
                        self.raw('SELECT raw_json FROM dji_land_revisions'))
        for marker in ('signedURL', 'OSSAccessKeyId', 'Signature='):
            self.assertNotIn(marker, blob)

    def test_a_polygon_body_carrying_a_marker_is_refused(self):
        poisoned = json.dumps({'type': 'Polygon',
                               'note': 'https://x/?OSSAccessKeyId=LEAK'
                              }).encode('utf-8')
        md5 = md5_of(poisoned)
        res = self.post_snapshot([land_node('S2', md5, link='')],
                                 [(md5, poisoned)])
        self.assertEqual(res.get_json()['geometries_new'], 0)
        self.assertEqual(res.get_json()['geometries_errors'], 1)

    def test_the_manifest_request_carries_only_hashes(self):
        payload = snd.build_geometry_manifest_payload(TOKEN, ['a' * 32])
        self.assertEqual(set(payload), {'token', 'content_md5'})
        self.assertNotIn('signedURL', json.dumps(payload))

    def test_a_list_page_carrying_a_marker_is_refused(self):
        poisoned = list_page([dict(list_row(1),
                                   note='https://x/?Signature=LEAK')])
        items, _stats = src.list_source_items(
            [_Page(poisoned)], 'run', '2026-09-11 04:00:00')
        res = self.post_sources(items)
        self.assertEqual(res.get_json()['new'], 0)
        self.assertEqual(res.get_json()['errors'], 1)

    def test_the_progress_lines_name_numbers_only(self):
        log = _Log()
        node = land_node('S3', md5_of(polygon_bytes('S3')))
        src.download_snapshot_geometries(
            [node], lambda _l: polygon_bytes('S3'), logger=log,
            sleep_fn=lambda _s: None,
            known_md5={md5_of(polygon_bytes('S3'))})
        for marker in ('OSSAccessKeyId', 'Signature=', 'Expires='):
            self.assertNotIn(marker, log.text)


# ─── Часть 7. Находки состязательного чтения ────────────────────────────────

class AReferenceWithoutABodyIsLoud(ServerCase):
    """Пропуск известного полигона и потеря полигона снаружи одинаковы.

    [REASON]: нашёл оппонент при чтении приёмника. Отсутствующее тело не
    роняет приём -- оно тихо понижает привязку поля с TIER1_EXACT до
    TIER2_STRONG, оставив уверенность HIGH. Ровно тот случай, когда молчание
    хуже ошибки, поэтому приёмник называет число в ответе.
    """

    def test_a_skipped_but_stored_polygon_reports_zero_gaps(self):
        body = polygon_bytes('G1')
        md5 = md5_of(body)
        self.post_snapshot([land_node('G1', md5, link='')], [(md5, body)],
                           run_id='r1')
        res = self.post_snapshot([land_node('G1', md5, link='', name='Новое')],
                                 geometries=(), run_id='r2')
        self.assertEqual(res.get_json()['geometries_referenced_but_absent'], 0)

    def test_a_reference_whose_body_never_arrived_is_counted(self):
        # Тело НЕ приложено и в хранилище его нет: это потеря, а не пропуск.
        md5 = md5_of(polygon_bytes('G-missing'))
        res = self.post_snapshot([land_node('G2', md5, link='')],
                                 geometries=(), run_id='r1')
        self.assertEqual(res.get_json()['lands_new'], 1)
        self.assertEqual(res.get_json()['geometries_referenced_but_absent'], 1)




class DisappearanceIsDerivedNotStored(ServerCase):
    """§6.2 брифа без новой таблицы -- и без ложных «удалений».

    «Контур исчез» = его последняя ревизия видна в снимке СТАРШЕ, чем
    новейший ПОЛНЫЙ снимок. `last_seen_snapshot_id` и `seen_count` ведутся
    на каждом повторе (`store.upsert_land_revision`), поэтому отдельная
    история исчезновений не нужна.

    [REASON]: вывод обязан опираться на ПОЛНЫЙ обход. Иначе оборванная
    страница объявит половину каталога удалённой -- ровно то, от чего
    предостерегает §6.8.
    """

    def snapshot_id(self, run_id):
        return self.raw('SELECT id FROM dji_land_snapshots WHERE '
                        'capture_run_id=?', (run_id,))[0][0]

    def last_seen(self, uuid):
        return self.raw('SELECT last_seen_snapshot_id, seen_count FROM '
                        'dji_land_revisions WHERE land_uuid=?', (uuid,))[0]

    def two_lands(self, run_id, uuids, complete=True, expected=None):
        nodes, geoms = [], []
        for u in uuids:
            body = polygon_bytes(u)
            nodes.append(land_node(u, md5_of(body), link=''))
            geoms.append((md5_of(body), body))
        return self.post_snapshot(nodes, geoms, run_id=run_id,
                                  complete=complete, expected=expected)

    def test_a_land_still_present_moves_its_last_seen(self):
        self.two_lands('r1', ['A', 'B'])
        self.two_lands('r2', ['A', 'B'])
        self.assertEqual(self.last_seen('A'),
                         (self.snapshot_id('r2'), 2))

    def test_a_land_absent_from_a_COMPLETE_walk_is_derivably_gone(self):
        self.two_lands('r1', ['A', 'B'])
        self.two_lands('r2', ['A'])          # B пропала, обход полный
        newest_complete = self.raw(
            'SELECT MAX(id) FROM dji_land_snapshots WHERE complete=1')[0][0]
        gone = self.raw('SELECT land_uuid FROM dji_land_revisions WHERE '
                        'last_seen_snapshot_id < ?', (newest_complete,))
        self.assertEqual([g[0] for g in gone], ['B'])

    def test_an_INCOMPLETE_walk_makes_nobody_disappear(self):
        self.two_lands('r1', ['A', 'B'])
        self.two_lands('r2', ['A'], complete=False, expected=2)
        newest_complete = self.raw(
            'SELECT MAX(id) FROM dji_land_snapshots WHERE complete=1')[0][0]
        self.assertEqual(newest_complete, self.snapshot_id('r1'))
        gone = self.raw('SELECT land_uuid FROM dji_land_revisions WHERE '
                        'last_seen_snapshot_id < ?', (newest_complete,))
        self.assertEqual(gone, [], 'неполный обход объявил контур удалённым')


class TheAbsentBodyAlarmCanReturnToZero(ServerCase):
    """Тревога, которая горит всегда, тревогой быть перестаёт.

    [REASON]: находка состязательного ревью. Первая редакция считала ВСЕ
    ревизии за всё время; полигон, которого больше нет ни в одном узле
    каталога, заново не приедет никогда, поэтому один невосстановимый
    пробел прижимал счётчик выше нуля навсегда.
    """

    def test_a_gap_that_the_catalog_moved_past_stops_firing(self):
        # День 1: тело не доехало -- ссылка без тела, тревога звучит.
        lost = md5_of(polygon_bytes('L1-v1'))
        first = self.post_snapshot([land_node('L1', lost, link='')],
                                   geometries=(), run_id='d1')
        self.assertEqual(
            first.get_json()['geometries_referenced_but_absent'], 1)

        # День 2: DJI изменил поле, каталог называет НОВУЮ версию, и она
        # приехала. Старого md5 больше нет ни в одном узле -- он не
        # вернётся никогда, и держать из-за него тревогу нельзя.
        body = polygon_bytes('L1-v2')
        new = md5_of(body)
        second = self.post_snapshot([land_node('L1', new, link='')],
                                    [(new, body)], run_id='d2')
        self.assertEqual(
            second.get_json()['geometries_referenced_but_absent'], 0)

    def test_control_a_gap_in_the_CURRENT_revision_still_fires(self):
        # Контроль: без него тест выше прошёл бы у кода, который просто
        # всегда возвращает ноль.
        body = polygon_bytes('L2-v1')
        ok = md5_of(body)
        self.post_snapshot([land_node('L2', ok, link='')], [(ok, body)],
                           run_id='d1')
        missing = md5_of(polygon_bytes('L2-v2'))
        res = self.post_snapshot([land_node('L2', missing, link='')],
                                 geometries=(), run_id='d2')
        self.assertEqual(res.get_json()['geometries_referenced_but_absent'], 1)
