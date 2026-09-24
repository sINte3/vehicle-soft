# -*- coding: utf-8 -*-
"""DJI-AREA-PRODUCTIONIZATION-001: манифест адресного захвата V4.

Что здесь держится -- то, что ломается молча и стоит дорого:

* кандидат экрана попадает в манифест, и предел числа идентификаторов режет
  ТОЛЬКО контроль: кандидат из-за предела не теряется никогда;
* запись с уже захваченным V4 больше не запрашивается -- иначе захват ходил бы
  в кабинет DJI за теми же байтами каждый день;
* `NO_V4_URL_AT_SOURCE` -- отдельное ВИДИМОЕ состояние, а не бесконечное
  посещение;
* контроль детерминирован на день, покрывает разные борта и НЕ ползёт: после
  захвата сегодняшнего контроля завтрашний прогон не добирает новую порцию;
* правило не скопировано: кандидата считает замороженный экран.

Stdlib sqlite3, без Flask и без приложения.
"""

import json
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from dji_area import capture_manifest as cm  # noqa: E402
from dji_area import store  # noqa: E402
from tests.test_dji_area_core import frame, v4_bytes  # noqa: E402
from tests.test_dji_area_identity_001 import _ddl, ts  # noqa: E402

DAY = date(2026, 9, 2)
DRONES = 4


def _flights():
    """Четыре борта; у каждого цепочка A -> B -> C и шесть обычных вылетов."""
    rows = []
    for unit in range(1, DRONES + 1):
        base = 970000 + unit * 100
        hour = 6 + unit
        rows += [
            (base + 1, unit, '%02d:00:00' % hour, '%02d:07:00' % hour, 4, 6.0,
             9000.0 + unit),
            (base + 2, unit, '%02d:07:00' % hour, '%02d:07:30' % hour, 1, None,
             600.0),
            (base + 3, unit, '%02d:07:30' % hour, '%02d:08:30' % hour, 4, None,
             9000.0 + unit),
        ]
        for n in range(6):
            minute = 10 + n * 8
            rows.append((base + 10 + n, unit,
                         '%02d:%02d:00' % (hour, minute),
                         '%02d:%02d:00' % (hour, minute + 6), 4, 6.0,
                         5000.0 + unit * 10 + n))
    return rows


FLIGHTS = _flights()
CANDIDATES = sorted(970000 + u * 100 + 3 for u in range(1, DRONES + 1))


class Fixture(object):

    def __init__(self, day=DAY):
        self.day = day
        self.tmp = tempfile.mkdtemp(prefix='manifest_')
        self.db = os.path.join(self.tmp, 'instance', 'test.db')
        os.makedirs(os.path.dirname(self.db))
        con = sqlite3.connect(self.db)
        con.execute('CREATE TABLE drone_units (id INTEGER PRIMARY KEY, '
                    'hardware_id TEXT)')
        con.execute('CREATE TABLE drone_flights (id INTEGER PRIMARY KEY, '
                    'dji_flight_id BIGINT UNIQUE, started_at TEXT, '
                    'finished_at TEXT, raw_json TEXT, drone_unit_id INTEGER, '
                    'nickname_raw TEXT)')
        for stmt in _ddl():
            con.execute(stmt)
        for unit in range(1, DRONES + 1):
            con.execute('INSERT INTO drone_units VALUES (?, ?)',
                        (unit, 'BODY-%d' % unit))
        rows = []
        for fid, unit, start, end, mode, width, raw in FLIGHTS:
            s = '%s %s' % (day.isoformat(), start)
            e = '%s %s' % (day.isoformat(), end)
            con.execute(
                'INSERT INTO drone_flights (dji_flight_id, started_at, '
                'finished_at, raw_json, drone_unit_id, nickname_raw) '
                'VALUES (?,?,?,?,?,?)',
                (fid, s, e, None, unit, 'SYNTHETIC-%d' % unit))
            rows.append({'id': fid, 'new_work_area': raw, 'mode_name': mode,
                         'manual_mode': mode != 4, 'spray_width': width,
                         'start_timestamp': ts(s), 'end_timestamp': ts(e),
                         'nickname': 'SYNTHETIC-%d' % unit})
        con.commit()
        con.close()
        body = json.dumps({'code': 0, 'data': rows}).encode('utf-8')
        con = store.connect(self.db)
        root = store.source_root(os.path.abspath(self.db))
        store.begin_immediate(con)
        for row in rows:
            store.upsert_source_revision(con, root, 'list', body,
                                         flight_id=row['id'],
                                         inline_max_bytes=0)
            store.refresh_flight_evidence(con, root, row['id'])
        con.execute('COMMIT')
        con.close()

    def add_v4(self, flight_id):
        row = [f for f in FLIGHTS if f[0] == flight_id][0]
        t0 = ts('%s %s' % (self.day.isoformat(), row[2]))
        frames = [frame((t0 + i) * 1000, area=float(i)) for i in range(5)]
        con = store.connect(self.db)
        root = store.source_root(os.path.abspath(self.db))
        store.begin_immediate(con)
        store.upsert_source_revision(con, root, 'v4', v4_bytes(frames),
                                     flight_id=flight_id)
        store.refresh_flight_evidence(con, root, flight_id)
        con.execute('COMMIT')
        con.close()

    def add_airlines(self, flight_id, body, **kwargs):
        """An airlines revision through the frozen store, as the receiver
        keeps it -- no shortcut to the evidence row."""
        con = store.connect(self.db)
        root = store.source_root(os.path.abspath(self.db))
        store.begin_immediate(con)
        store.upsert_source_revision(con, root, 'airlines', body,
                                     flight_id=flight_id, **kwargs)
        store.refresh_flight_evidence(con, root, flight_id)
        con.execute('COMMIT')
        con.close()

    def absent_reason(self, flight_id):
        con = sqlite3.connect(self.db)
        try:
            return con.execute('SELECT v4_absent_reason FROM '
                               'dji_flight_evidence WHERE flight_id = ?',
                               (flight_id,)).fetchone()[0]
        finally:
            con.close()

    def mark_no_v4_at_source(self, flight_id):
        con = sqlite3.connect(self.db)
        con.execute("UPDATE dji_flight_evidence SET v4_absent_reason = "
                    "'NO_V4_URL_AT_SOURCE' WHERE flight_id = ?", (flight_id,))
        con.commit()
        con.close()

    def manifest(self, **kwargs):
        con = sqlite3.connect('file:%s?mode=ro'
                              % self.db.replace('\\', '/'), uri=True)
        con.row_factory = sqlite3.Row
        try:
            return cm.build_manifest(con, kwargs.pop('date_from', self.day),
                                     kwargs.pop('date_to', self.day), **kwargs)
        finally:
            con.close()

    def close(self):
        shutil.rmtree(self.tmp, ignore_errors=True)


class Base(unittest.TestCase):

    def setUp(self):
        self.fx = Fixture()
        self.addCleanup(self.fx.close)

    @staticmethod
    def ids(entries, reason=None):
        return sorted(e['flight_id'] for e in entries
                      if reason is None or e['reason'] == reason)


class Candidates(Base):

    def test_every_structural_candidate_is_in_the_manifest(self):
        manifest = self.fx.manifest()
        self.assertEqual(self.ids(manifest['capture'], cm.REASON_CANDIDATE),
                         CANDIDATES)
        entry = [e for e in manifest['capture']
                 if e['flight_id'] == CANDIDATES[0]][0]
        self.assertEqual(entry['base_flight_id'], CANDIDATES[0] - 2)
        self.assertEqual(entry['bridge_flight_ids'], [CANDIDATES[0] - 1])
        self.assertEqual(entry['report_day'], DAY.isoformat())
        self.assertEqual(entry['drone'], 'SYNTHETIC-1')
        self.assertEqual(entry['evidence'],
                         {'list': True, 'card': False, 'route': False,
                          'v4': False})

    def test_the_bridge_is_never_asked_for_as_a_candidate(self):
        manifest = self.fx.manifest()
        bridges = {c - 1 for c in CANDIDATES}
        self.assertFalse(bridges & set(
            self.ids(manifest['capture'], cm.REASON_CANDIDATE)))

    def test_the_frozen_screen_decides_not_a_copy_of_the_rule(self):
        from dji_area import structural
        real = structural.screen_all
        structural.screen_all = lambda records: [
            structural.not_applicable('TEST') for _ in records]
        self.addCleanup(setattr, structural, 'screen_all', real)
        manifest = self.fx.manifest()
        self.assertEqual(manifest['counts']['candidates_total'], 0)

    def test_the_cap_cuts_controls_and_never_a_candidate(self):
        manifest = self.fx.manifest(max_ids=5)
        self.assertEqual(self.ids(manifest['capture'], cm.REASON_CANDIDATE),
                         CANDIDATES)
        self.assertEqual(len(manifest['capture']), 5)
        self.assertGreater(manifest['counts']['controls_dropped_by_cap'], 0)
        self.assertFalse(manifest['over_cap'])

    def test_more_candidates_than_the_cap_still_all_come_back(self):
        manifest = self.fx.manifest(max_ids=2)
        self.assertEqual(self.ids(manifest['capture'], cm.REASON_CANDIDATE),
                         CANDIDATES)
        self.assertTrue(manifest['over_cap'])
        self.assertEqual(self.ids(manifest['capture'], cm.REASON_CONTROL), [])


class NoEndlessCapture(Base):

    def test_a_candidate_with_v4_is_not_asked_for_again(self):
        self.fx.add_v4(CANDIDATES[0])
        manifest = self.fx.manifest()
        self.assertEqual(self.ids(manifest['capture'], cm.REASON_CANDIDATE),
                         CANDIDATES[1:])
        self.assertEqual(manifest['counts']['candidates_with_v4'], 1)
        # Кандидатом она быть не перестала -- просто захват больше не нужен.
        self.assertEqual(manifest['counts']['candidates_total'],
                         len(CANDIDATES))

    def test_no_v4_at_source_is_a_visible_state_not_a_daily_visit(self):
        self.fx.mark_no_v4_at_source(CANDIDATES[1])
        manifest = self.fx.manifest()
        self.assertNotIn(CANDIDATES[1], self.ids(manifest['capture']))
        self.assertEqual(self.ids(manifest['no_v4_at_source']),
                         [CANDIDATES[1]])
        self.assertEqual(manifest['no_v4_at_source'][0]['v4_state'],
                         cm.V4_ABSENT_AT_SOURCE)
        self.assertEqual(manifest['counts']['candidates_no_v4_at_source'], 1)

    def test_a_confirmed_descriptor_404_stops_the_daily_visit(self):
        """Live case 715984635 through the frozen store, no shortcut.

        The collector's record of a descriptor answered with HTTP 404 (and
        confirmed by its control request) is an airlines revision like any
        other: the store reads it as NO_V4_URL_AT_SOURCE, and the manifest
        names the candidate instead of asking for it every day.
        """
        from drone_collector.sources import (
            DESCRIPTOR_ASSOCIATION, SCHEMA_AIRLINES_DESCRIPTOR_ABSENT,
            airlines_bytes, descriptor_absence_document)
        target = CANDIDATES[1]
        answer = b'404 page not found\n'
        body = airlines_bytes(descriptor_absence_document(
            target, answer, 'text/plain; charset=utf-8'))
        self.fx.add_airlines(
            target, body, schema_version=SCHEMA_AIRLINES_DESCRIPTOR_ABSENT,
            request_context={'path': '/api/web/v2/airlines/%d' % target,
                             'association': DESCRIPTOR_ASSOCIATION,
                             'http_status': 404,
                             'control_flight_id': CANDIDATES[0],
                             'control_http_status': 200})
        self.assertEqual(self.fx.absent_reason(target), 'NO_V4_URL_AT_SOURCE')
        manifest = self.fx.manifest()
        self.assertNotIn(target, self.ids(manifest['capture']))
        self.assertEqual(self.ids(manifest['no_v4_at_source']), [target])
        self.assertEqual(manifest['no_v4_at_source'][0]['v4_state'],
                         cm.V4_ABSENT_AT_SOURCE)
        # The other candidates are still asked for: nothing leaked.
        for other in (CANDIDATES[0], CANDIDATES[2], CANDIDATES[3]):
            self.assertIn(other, self.ids(manifest['capture']))

    def test_the_404_bytes_stored_as_they_came_are_not_absence(self):
        """NEGATIVE CONTROL: the same 19 bytes, kept as if they were the
        descriptor, say nothing about V4 -- the flight is still asked for."""
        target = CANDIDATES[1]
        self.fx.add_airlines(target, b'404 page not found\n')
        self.assertEqual(self.fx.absent_reason(target), 'NOT_CAPTURED')
        self.assertIn(target, self.ids(self.fx.manifest()['capture']))

    def test_the_other_absent_reason_still_needs_a_capture(self):
        # Отрицательный контроль: «не захвачено» -- НЕ «у DJI нет».
        manifest = self.fx.manifest()
        self.assertIn(CANDIDATES[1], self.ids(manifest['capture']))

    def test_captured_controls_do_not_pull_in_a_new_portion(self):
        """Контроль дня фиксирован: захватили -- и на этот день всё."""
        first = self.fx.manifest()
        controls = self.ids(first['capture'], cm.REASON_CONTROL)
        self.assertTrue(controls)
        for flight_id in controls:
            self.fx.add_v4(flight_id)
        second = self.fx.manifest()
        self.assertEqual(self.ids(second['capture'], cm.REASON_CONTROL), [])
        self.assertEqual(second['counts']['controls_selected'],
                         first['counts']['controls_selected'])


class ControlSample(Base):

    def test_the_sample_is_deterministic_for_the_day(self):
        self.assertEqual(self.ids(self.fx.manifest()['capture']),
                         self.ids(self.fx.manifest()['capture']))

    def test_another_salt_really_gives_another_sample(self):
        # Отрицательный контроль: детерминизм -- свойство жребия, а не того,
        # что выбирать не из чего.
        a = self.ids(self.fx.manifest(risk_per_day=0,
                                      random_per_day=3)['capture'],
                     cm.REASON_CONTROL)
        b = self.ids(self.fx.manifest(risk_per_day=0, random_per_day=3,
                                      salt='another-salt')['capture'],
                     cm.REASON_CONTROL)
        self.assertEqual(len(a), 3)
        self.assertNotEqual(a, b)

    def test_random_controls_come_from_different_drones(self):
        manifest = self.fx.manifest(risk_per_day=0, random_per_day=DRONES)
        drones = [e['chronology_key'] for e in manifest['capture']
                  if e['reason'] == cm.REASON_CONTROL]
        self.assertEqual(len(drones), DRONES)
        self.assertEqual(len(set(drones)), DRONES)

    def test_the_sample_is_small(self):
        manifest = self.fx.manifest()
        self.assertLessEqual(
            manifest['counts']['controls_selected'],
            cm.CONTROLS_RISK_PER_DAY + cm.CONTROLS_RANDOM_PER_DAY)

    def test_a_known_risk_signature_is_preferred(self):
        """RAW, повторяющий предыдущую запись борта, -- первым в контроле."""
        target = 970000 + 100 + 12
        con = sqlite3.connect(self.fx.db)
        previous = con.execute(
            'SELECT list_raw_area_m2 FROM dji_flight_evidence '
            'WHERE flight_id = ?', (target - 1,)).fetchone()[0]
        con.execute('UPDATE dji_flight_evidence SET list_raw_area_m2 = ? '
                    'WHERE flight_id = ?', (previous, target))
        con.commit()
        con.close()
        manifest = self.fx.manifest(risk_per_day=1, random_per_day=0)
        controls = [e for e in manifest['capture']
                    if e['reason'] == cm.REASON_CONTROL]
        self.assertEqual([e['flight_id'] for e in controls], [target])
        self.assertEqual(controls[0]['control_kind'], cm.CONTROL_RISK_EQUAL)

    def test_a_candidate_is_never_also_a_control(self):
        manifest = self.fx.manifest()
        ids = [e['flight_id'] for e in manifest['capture']]
        self.assertEqual(len(ids), len(set(ids)))


class Window(unittest.TestCase):

    def test_the_default_is_the_last_three_report_days(self):
        today = date(2026, 9, 18)
        self.assertEqual(cm.resolve_window(today),
                         (date(2026, 9, 16), today))

    def test_the_window_is_bounded(self):
        today = date(2026, 9, 18)
        with self.assertRaises(cm.ManifestError):
            cm.resolve_window(today, today - timedelta(days=cm.MAX_WINDOW_DAYS),
                              today)
        # Отрицательный контроль: ровно предел -- ещё можно.
        cm.resolve_window(today,
                          today - timedelta(days=cm.MAX_WINDOW_DAYS - 1), today)

    def test_an_inverted_window_is_refused(self):
        with self.assertRaises(cm.ManifestError):
            cm.resolve_window(date(2026, 9, 18), date(2026, 9, 18),
                              date(2026, 9, 17))


class ReadOnlyAndSecretFree(Base):

    def test_the_manifest_does_not_write(self):
        import hashlib

        def digest():
            with open(self.fx.db, 'rb') as fh:
                return hashlib.sha256(fh.read()).hexdigest()

        before = digest()
        self.fx.manifest()
        self.assertEqual(digest(), before)

    def test_the_answer_carries_no_link_cookie_or_body(self):
        text = json.dumps(self.fx.manifest()).lower()
        for marker in ('http', 'cookie', 'signature', 'token', 'body',
                       'sha256', 'raw_json'):
            self.assertNotIn(marker, text, marker)
        # Отрицательный контроль: ответ не пуст.
        self.assertIn('structural_candidate', text)

    def test_the_answer_is_plain_json(self):
        manifest = self.fx.manifest()
        self.assertEqual(json.loads(json.dumps(manifest)), manifest)
        self.assertEqual(manifest['structural_rule_version'],
                         'structural-retained-screen-frozen-1')


if __name__ == '__main__':
    unittest.main()
