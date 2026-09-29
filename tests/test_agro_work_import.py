# -*- coding: utf-8 -*-
"""agro-work B1: импорт заявок -- идемпотентность, журнал, история, связки.

Сквозь настоящий HTTP (поддельный сервер agro-work на 127.0.0.1) и настоящую
схему (DDL миграции AGRO_WORK_001). Каждая проверка здесь -- ПЕРЕХОД: прогон,
изменение на их стороне, второй прогон. Один прогон доказывал бы только, что
код что-то пишет.

Что держится:
  * повторная загрузка ничего не дублирует и не пишет в журнал;
  * изменившаяся заявка обновляется, каждое поле -- строкой журнала;
  * история статусов -- только закрытым заявкам, только новым и изменившимся,
    не больше лимита за прогон, новейшие первыми;
  * ни одно персональное значение не доходит до базы;
  * пропавшая из ПОЛНОЙ выгрузки заявка отмечается, а не удаляется; из
    неполной -- не отмечается вовсе;
  * связка владельца переживает повторный импорт;
  * в agro-work не уходит ничего, кроме GET и входа.

Запуск: python -m unittest tests.test_agro_work_import -v
"""

import csv
import hashlib
import io
import os
import sqlite3
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from agro_work import importer, store                           # noqa: E402
from agro_work.client import ApiError, Client, build_opener     # noqa: E402
from tests import agro_work_db as dbh                           # noqa: E402
from tests import agro_work_fake as fake_api                    # noqa: E402
from tools import agro_work_import as tool                      # noqa: E402

CREDENTIALS = {'login': fake_api.LOGIN, 'password': fake_api.PASSWORD}


def app_id(number):
    return fake_api.uuid_for(0xA, number)


class ImportCase(unittest.TestCase):
    def setUp(self):
        self.server = fake_api.FakeAgroWork()
        self.db = dbh.make_db()
        con = sqlite3.connect(self.db)
        dbh.add_org(con, 1, 'Buxoro')
        dbh.add_equipment(con, 11, '80 001 ЕА')     # кириллические Е и А
        dbh.add_equipment(con, 12, '80 002 EA')
        con.commit()
        con.close()
        self.server.work_types = [fake_api.work_type(1)]
        self.server.transports = [fake_api.transport(1), fake_api.transport(2)]
        self.server.applications = [
            fake_api.application(1, created='2026-09-01T08:00:00+05:00'),
            fake_api.application(2, created='2026-09-02T08:00:00+05:00',
                                 transport=2),
            fake_api.application(3, status='IN_PROGRESS',
                                 created='2026-09-03T08:00:00+05:00',
                                 updated='2026-09-03T08:00:00+05:00'),
            fake_api.application(4, status='PENDING',
                                 created='2026-09-04T08:00:00+05:00',
                                 updated='2026-09-04T08:00:00+05:00'),
            fake_api.application(5, status='CANCELLED',
                                 created='2026-09-05T08:00:00+05:00',
                                 updated='2026-09-05T12:00:00+05:00'),
        ]
        self.server.histories = {
            app_id(1): fake_api.history(created='2026-09-01T08:00:00+05:00',
                                        completed='2026-09-03T18:00:00+05:00'),
            app_id(2): fake_api.history(created_status='COMPLETED',
                                        created='2026-09-02T08:00:00+05:00',
                                        completed=None),
            app_id(3): fake_api.history(created='2026-09-03T08:00:00+05:00',
                                        completed=None),
            app_id(4): fake_api.history(created_status='PENDING',
                                        created='2026-09-04T08:00:00+05:00',
                                        completed=None),
            app_id(5): fake_api.history(created='2026-09-05T08:00:00+05:00',
                                        completed=None,
                                        cancelled='2026-09-05T12:00:00+05:00'),
        }

    def tearDown(self):
        self.server.close()

    def client(self):
        return Client(CREDENTIALS, base_url=self.server.base_url, pause=0,
                      log=lambda text: None, opener=build_opener(proxies={}))

    def run_import(self, max_history=100):
        con = store.connect(self.db)
        try:
            return importer.run_import(self.client(), con, max_history,
                                       log=lambda text: None)
        finally:
            con.close()

    def rows(self, sql, args=()):
        con = sqlite3.connect(self.db)
        con.row_factory = sqlite3.Row
        try:
            return [dict(r) for r in con.execute(sql, args)]
        finally:
            con.close()

    def history_requests(self):
        return [p for p in self.server.paths('GET') if p.endswith('/history/')]


class FirstAndRepeatedRun(ImportCase):
    def test_first_run_imports_everything_and_the_identity_holds(self):
        run_id, status, counters, detail = self.run_import()
        self.assertEqual(status, store.RUN_OK)
        self.assertEqual(counters['rows_new'], 5)
        run = self.rows('SELECT * FROM agro_work_import_runs WHERE id = ?',
                        (run_id,))[0]
        self.assertEqual(run['rows_seen'],
                         run['rows_new'] + run['rows_updated']
                         + run['rows_unchanged'] + run['rows_rejected']
                         + run['rows_duplicate'])
        self.assertEqual(run['api_count'], 5)
        self.assertEqual(len(self.rows('SELECT id FROM agro_work_applications')), 5)
        self.assertEqual(len(self.rows('SELECT id FROM agro_work_transports')), 2)
        self.assertEqual(len(self.rows('SELECT id FROM agro_work_work_types')), 1)
        self.assertEqual(detail['by_status'], {'COMPLETED': 2, 'IN_PROGRESS': 1,
                                               'PENDING': 1, 'CANCELLED': 1})

    def test_repeated_run_duplicates_nothing_and_journals_nothing(self):
        self.run_import()
        before = self.rows('SELECT * FROM agro_work_applications ORDER BY id')
        history_before = len(self.history_requests())
        _, status, counters, _ = self.run_import()
        self.assertEqual(status, store.RUN_OK)
        self.assertEqual((counters['rows_new'], counters['rows_updated'],
                          counters['rows_unchanged']), (0, 0, 5))
        self.assertEqual(len(self.history_requests()), history_before)
        self.assertEqual(self.rows('SELECT COUNT(*) AS n FROM agro_work_changes'),
                         [{'n': 0}])
        after = self.rows('SELECT * FROM agro_work_applications ORDER BY id')
        for row in before + after:
            row.pop('last_seen_run_id')
        self.assertEqual(before, after)

    def test_a_changed_application_is_updated_with_a_journal_line_per_field(self):
        self.run_import()
        self.server.applications[0]['volume'] = '7.00'
        self.server.applications[0]['updated_at'] = '2026-09-20T09:00:00+05:00'
        run_id, _, counters, _ = self.run_import()
        self.assertEqual(counters['rows_updated'], 1)
        journal = self.rows('SELECT field, old_value, new_value, run_id FROM '
                            'agro_work_changes ORDER BY field')
        self.assertEqual([(j['field'], j['old_value'], j['new_value'])
                          for j in journal],
                         [('updated_at', '2026-09-11T17:40:00+05:00',
                           '2026-09-20T09:00:00+05:00'),
                          ('volume', '5.00', '7.00')])
        self.assertTrue(all(j['run_id'] == run_id for j in journal))
        self.assertEqual(self.rows('SELECT volume FROM agro_work_applications '
                                   'WHERE id = ?', (app_id(1),)),
                         [{'volume': '7.00'}])

    def test_nothing_but_reads_and_the_login_reach_their_server(self):
        self.run_import()
        self.run_import()
        writes = {(r['method'], r['path']) for r in self.server.requests
                  if r['method'] != 'GET'}
        self.assertEqual(writes, {('POST', '/api/v1/auth/token/')})


class StatusHistory(ImportCase):
    def test_only_closed_applications_get_a_history(self):
        self.run_import()
        fetched = sorted(p.split('/')[4] for p in self.history_requests())
        self.assertEqual(fetched, sorted([app_id(1), app_id(2), app_id(5)]))

    def test_dates_come_from_the_history(self):
        self.run_import()
        rows = {r['id']: r for r in self.rows(
            'SELECT id, initial_status, completed_day, completed_at, '
            'cancelled_at, created_day FROM agro_work_applications')}
        self.assertEqual(rows[app_id(1)]['initial_status'], 'IN_PROGRESS')
        self.assertEqual(rows[app_id(1)]['completed_day'], '2026-09-03')
        self.assertEqual(rows[app_id(1)]['created_day'], '2026-09-01')
        # Заведена сразу выполненной: закрытие -- момент ввода.
        self.assertEqual(rows[app_id(2)]['initial_status'], 'COMPLETED')
        self.assertEqual(rows[app_id(2)]['completed_day'], '2026-09-02')
        self.assertEqual(rows[app_id(5)]['cancelled_at'],
                         '2026-09-05T12:00:00+05:00')
        # Открытым история не бралась -- дат нет, и это не «нет закрытия».
        self.assertIsNone(rows[app_id(3)]['initial_status'])

    def test_an_open_application_gets_its_history_when_it_closes(self):
        self.run_import()
        self.assertNotIn(app_id(3), ' '.join(self.history_requests()))
        self.server.applications[2]['status'] = 'COMPLETED'
        self.server.applications[2]['updated_at'] = '2026-09-06T19:00:00+05:00'
        self.server.histories[app_id(3)] = fake_api.history(
            created='2026-09-03T08:00:00+05:00',
            completed='2026-09-06T19:00:00+05:00')
        before = len(self.history_requests())
        self.run_import()
        new = self.history_requests()[before:]
        self.assertEqual([p.split('/')[4] for p in new], [app_id(3)])
        self.assertEqual(self.rows('SELECT completed_day FROM '
                                   'agro_work_applications WHERE id = ?',
                                   (app_id(3),)),
                         [{'completed_day': '2026-09-06'}])

    def test_the_limit_stretches_the_first_load_newest_first(self):
        # Десять выполненных заявок, по две истории за прогон.
        self.server.applications = [fake_api.application(
            n, created='2026-09-%02dT08:00:00+05:00' % n) for n in range(1, 11)]
        self.server.histories = {app_id(n): fake_api.history(
            created='2026-09-%02dT08:00:00+05:00' % n,
            completed='2026-09-%02dT18:00:00+05:00' % n) for n in range(1, 11)}
        _, _, counters, _ = self.run_import(max_history=2)
        self.assertEqual(counters['history_fetched'], 2)
        self.assertEqual(counters['history_pending'], 8)
        self.assertEqual(sorted(p.split('/')[4] for p in self.history_requests()),
                         sorted([app_id(10), app_id(9)]))
        _, _, counters, _ = self.run_import(max_history=2)
        self.assertEqual(counters['history_pending'], 6)
        self.assertEqual(sorted(p.split('/')[4] for p in self.history_requests()[2:]),
                         sorted([app_id(8), app_id(7)]))
        _, _, counters, _ = self.run_import(max_history=0)
        self.assertEqual(counters['history_fetched'], 0)
        self.assertEqual(len(self.history_requests()), 4)

    def test_reclosed_application_takes_the_last_closing(self):
        events = fake_api.history(created='2026-09-01T08:00:00+05:00',
                                  completed='2026-09-02T18:00:00+05:00')
        events.append({'action': 'status_changed', 'old_status': 'COMPLETED',
                       'new_status': 'IN_PROGRESS',
                       'changed_at': '2026-09-03T09:00:00+05:00', 'changes': {}})
        events.append({'action': 'status_changed', 'old_status': 'IN_PROGRESS',
                       'new_status': 'COMPLETED',
                       'changed_at': '2026-09-05T20:00:00+05:00', 'changes': {}})
        self.server.histories[app_id(1)] = events
        self.run_import()
        self.assertEqual(self.rows('SELECT completed_day FROM '
                                   'agro_work_applications WHERE id = ?',
                                   (app_id(1),)),
                         [{'completed_day': '2026-09-05'}])

    def test_a_refetched_history_does_not_duplicate_events(self):
        self.run_import()
        count = self.rows('SELECT COUNT(*) AS n FROM agro_work_status_events')
        self.server.applications[0]['updated_at'] = '2026-09-21T10:00:00+05:00'
        self.run_import()
        self.assertEqual(self.rows('SELECT COUNT(*) AS n FROM '
                                   'agro_work_status_events'), count)


class PersonalData(ImportCase):
    def test_no_personal_value_reaches_the_database(self):
        self.run_import()
        self.server.applications[0]['updated_at'] = '2026-09-22T10:00:00+05:00'
        self.run_import()
        text = dbh.all_text(self.db)
        self.assertIn('APP-BKHRA-2026-0000001', text)   # что-то записано
        for marker in fake_api.PII_MARKERS + (fake_api.PASSWORD,
                                              fake_api.ACCESS,
                                              fake_api.REFRESH):
            self.assertNotIn(marker, text, marker)

    def test_history_keeps_field_names_not_values(self):
        self.run_import()
        rows = self.rows("SELECT changed_fields FROM agro_work_status_events "
                         "WHERE new_status = 'COMPLETED' AND action = "
                         "'status_changed'")
        self.assertEqual(rows[0]['changed_fields'], '["farm", "status"]')


class GoneAndIncomplete(ImportCase):
    def test_missing_from_a_complete_listing_is_marked_not_deleted(self):
        self.run_import()
        removed = self.server.applications.pop(3)
        run_id, status, counters, _ = self.run_import()
        self.assertEqual(status, store.RUN_OK)
        self.assertEqual(counters['rows_gone'], 1)
        row = self.rows('SELECT gone_at FROM agro_work_applications WHERE id = ?',
                        (app_id(4),))[0]
        self.assertIsNotNone(row['gone_at'])
        self.assertEqual(len(self.rows('SELECT id FROM agro_work_applications')), 5)
        self.server.applications.append(removed)
        _, _, counters, _ = self.run_import()
        self.assertEqual(counters['rows_updated'], 1)
        self.assertIsNone(self.rows('SELECT gone_at FROM agro_work_applications '
                                    'WHERE id = ?', (app_id(4),))[0]['gone_at'])
        journal = self.rows("SELECT old_value, new_value FROM agro_work_changes "
                            "WHERE field = 'gone_at' ORDER BY id")
        self.assertEqual(len(journal), 2)
        self.assertIsNone(journal[0]['old_value'])
        self.assertIsNone(journal[1]['new_value'])

    def test_a_rejected_row_blocks_the_gone_mark(self):
        self.run_import()
        self.server.applications.pop(3)
        self.server.applications[0]['created_at'] = '2026-09-01 08:00:00'  # без пояса
        _, status, counters, detail = self.run_import()
        self.assertEqual(status, store.RUN_INCOMPLETE)
        self.assertEqual(counters['rows_rejected'], 1)
        self.assertEqual(counters['rows_gone'], 0)
        self.assertIn('created_at is missing or has no time zone',
                      detail['application_rejections'])
        self.assertEqual(self.rows('SELECT COUNT(*) AS n FROM '
                                   'agro_work_applications WHERE gone_at IS '
                                   'NOT NULL'), [{'n': 0}])


class Matching(ImportCase):
    def test_plates_match_through_the_cyrillic_fold(self):
        self.run_import()
        rows = {r['id']: r for r in self.rows(
            'SELECT id, equipment_id, match_status FROM agro_work_transports')}
        self.assertEqual(rows[fake_api.uuid_for(0xB, 1)]['equipment_id'], 11)
        self.assertEqual(rows[fake_api.uuid_for(0xB, 1)]['match_status'], 'auto')
        self.assertEqual(rows[fake_api.uuid_for(0xB, 2)]['equipment_id'], 12)

    def test_two_of_our_machines_on_one_plate_are_not_guessed(self):
        con = sqlite3.connect(self.db)
        dbh.add_equipment(con, 13, '80002EA')
        con.commit()
        con.close()
        self.run_import()
        row = self.rows('SELECT equipment_id, match_status FROM '
                        'agro_work_transports WHERE id = ?',
                        (fake_api.uuid_for(0xB, 2),))[0]
        self.assertEqual(row, {'equipment_id': None, 'match_status': 'ambiguous'})

    def test_a_plate_without_region_is_only_a_hint(self):
        self.server.transports.append(fake_api.transport(7, plate='80 777 BA'))
        con = sqlite3.connect(self.db)
        dbh.add_equipment(con, 17, '777 BA')
        con.commit()
        con.close()
        self.run_import()
        row = self.rows('SELECT equipment_id, match_status FROM '
                        'agro_work_transports WHERE id = ?',
                        (fake_api.uuid_for(0xB, 7),))[0]
        self.assertEqual(row, {'equipment_id': None, 'match_status': 'none'})

    def test_a_link_that_appears_later_is_journaled_the_first_one_is_not(self):
        self.server.transports.append(fake_api.transport(7, plate='80 777 BA'))
        self.run_import()
        self.assertEqual(self.rows('SELECT COUNT(*) AS n FROM agro_work_changes'),
                         [{'n': 0}])
        con = sqlite3.connect(self.db)
        dbh.add_equipment(con, 17, '80 777 BA')
        con.commit()
        con.close()
        self.run_import()
        journal = self.rows("SELECT entity_id, field, old_value, new_value FROM "
                            "agro_work_changes ORDER BY field")
        self.assertEqual(journal, [
            {'entity_id': fake_api.uuid_for(0xB, 7), 'field': 'equipment_id',
             'old_value': None, 'new_value': '17'},
            {'entity_id': fake_api.uuid_for(0xB, 7), 'field': 'match_status',
             'old_value': 'none', 'new_value': 'auto'}])

    def test_the_owners_link_survives_a_reimport(self):
        self.server.transports.append(fake_api.transport(9, plate='ALFAKLAS12'))
        self.server.applications.append(fake_api.application(
            9, transport=9, created='2026-09-09T08:00:00+05:00'))
        self.server.histories[app_id(9)] = fake_api.history(
            created='2026-09-09T08:00:00+05:00',
            completed='2026-09-09T19:00:00+05:00')
        self.run_import()
        con = sqlite3.connect(self.db)
        con.execute("INSERT INTO agro_work_transport_links (agro_transport_id, "
                    "equipment_id, plate_at_link, linked_at) VALUES (?, 12, "
                    "'ALFAKLAS12', '2026-09-28 10:00:00')",
                    (fake_api.uuid_for(0xB, 9),))
        con.commit()
        con.close()
        for _ in range(2):
            self.run_import()
            row = self.rows('SELECT equipment_id, match_status FROM '
                            'agro_work_transports WHERE id = ?',
                            (fake_api.uuid_for(0xB, 9),))[0]
            self.assertEqual(row, {'equipment_id': 12, 'match_status': 'manual'})
        # Машина 2 автоматически тоже садилась на 12 -- теперь неоднозначна:
        # два реестра на одном нашем треке, связка владельца остаётся.
        row = self.rows('SELECT equipment_id, match_status FROM '
                        'agro_work_transports WHERE id = ?',
                        (fake_api.uuid_for(0xB, 2),))[0]
        self.assertEqual(row, {'equipment_id': None, 'match_status': 'ambiguous'})
        self.assertEqual(self.rows('SELECT COUNT(*) AS n FROM '
                                   'agro_work_transport_links'), [{'n': 1}])


class Tool(ImportCase):
    def setUp(self):
        super().setUp()
        self.folder = os.path.dirname(self.db)
        self.credentials = os.path.join(self.folder, 'creds.txt')
        with open(self.credentials, 'w', encoding='utf-8') as fh:
            fh.write('login=%s\npassword=%s\n' % (fake_api.LOGIN,
                                                   fake_api.PASSWORD))
        self.cwd = os.getcwd()
        os.chdir(self.folder)

    def tearDown(self):
        os.chdir(self.cwd)
        super().tearDown()

    def factory(self, credentials, pause=None):
        return Client(credentials, base_url=self.server.base_url, pause=0,
                      log=lambda text: None, opener=build_opener(proxies={}))

    def main(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = tool.main(list(args), client_factory=self.factory)
        return code, out.getvalue(), err.getvalue()

    def test_a_full_run_prints_ascii_and_writes_the_unmatched_csv(self):
        self.server.transports.append(fake_api.transport(8, plate='BUXAGRO01'))
        self.server.applications.append(fake_api.application(
            8, transport=8, created='2026-09-08T08:00:00+05:00'))
        self.server.histories[app_id(8)] = fake_api.history(
            created='2026-09-08T08:00:00+05:00',
            completed='2026-09-08T19:00:00+05:00')
        code, out, err = self.main('--db', self.db, '--credentials',
                                   self.credentials)
        self.assertEqual(code, 0, err)
        self.assertTrue(out.isascii())
        self.assertIn('applications: seen 6 | new 6', out)
        for secret in (fake_api.PASSWORD, fake_api.ACCESS, fake_api.REFRESH):
            self.assertNotIn(secret, out + err)
        with open(os.path.join(self.folder, 'agro_work_unmatched.csv'),
                  encoding='utf-8-sig') as fh:
            rows = list(csv.DictReader(fh, delimiter=';'))
        self.assertEqual([r['plate_number'] for r in rows], ['BUXAGRO01'])
        self.assertEqual(rows[0]['applications'], '1')
        self.assertEqual(rows[0]['equipment_id'], '')

    def test_check_reads_five_pages_and_opens_no_database(self):
        missing_db = os.path.join(self.folder, 'absent.db')
        code, out, err = self.main('--db', missing_db, '--credentials',
                                   self.credentials, '--check')
        self.assertEqual(code, 0, out + err)
        self.assertIn('check OK', out)
        self.assertIn('ordering=created_at is ascending: yes', out)
        self.assertFalse(os.path.exists(missing_db))
        self.assertEqual(self.server.paths('POST'), ['/api/v1/auth/token/'])
        self.assertEqual(len(self.server.paths('GET')), 4)
        for marker in fake_api.PII_MARKERS:
            self.assertNotIn(marker, out)

    def test_refusals_have_their_exit_codes(self):
        code, _, err = self.main('--db', os.path.join(self.folder, 'x.db'),
                                 '--credentials', self.credentials)
        self.assertEqual(code, 2)
        self.assertFalse(os.path.exists(os.path.join(self.folder, 'x.db')))
        bare = os.path.join(self.folder, 'bare.db')
        sqlite3.connect(bare).close()
        code, _, err = self.main('--db', bare, '--credentials', self.credentials)
        self.assertEqual(code, 2)
        self.assertIn('migrate_agro_work_001.py', err)
        code, _, err = self.main('--db', self.db, '--credentials',
                                 os.path.join(self.folder, 'none.txt'))
        self.assertEqual(code, 2)
        code, _, err = self.main('--db', self.db, '--credentials',
                                 self.credentials, '--pause', '0.2')
        self.assertEqual(code, 2)
        self.assertEqual(self.server.requests, [])

    def test_a_read_only_database_stops_before_any_request(self):
        # Окно без прав администратора открывает копию только на чтение.
        # Первая запись прогона -- его строка в журнале, до первого запроса.
        def digest():
            with open(self.db, 'rb') as fh:
                return hashlib.sha256(fh.read()).hexdigest()
        before = digest()
        with mock.patch.object(store, 'connect', dbh.readonly_connect):
            code, out, err = self.main('--db', self.db, '--credentials',
                                       self.credentials)
        self.assertEqual(code, 2, out + err)
        self.assertIn('read-only for this window', err)
        self.assertIn('nothing was requested from agro-work', err)
        self.assertEqual(self.server.requests, [])
        self.assertEqual(digest(), before)

    def test_wrong_password_exits_3(self):
        with open(self.credentials, 'w', encoding='utf-8') as fh:
            fh.write('login=%s\npassword=wrong-one\n' % fake_api.LOGIN)
        code, out, err = self.main('--db', self.db, '--credentials',
                                   self.credentials)
        self.assertEqual(code, 3)
        self.assertNotIn('wrong-one', out + err)
        run = self.rows('SELECT status, message FROM agro_work_import_runs')
        self.assertEqual(run[0]['status'], 'error')

    def test_a_server_failure_mid_run_keeps_what_was_read(self):
        def broken_history(*args):
            raise ApiError(503, 'GET', '/applications/x/history/', 'busy')
        con = store.connect(self.db)
        client = self.factory(CREDENTIALS)
        client.history = broken_history
        try:
            with self.assertRaises(ApiError):
                importer.run_import(client, con, 10, log=lambda text: None)
        finally:
            con.close()
        run = self.rows('SELECT status, rows_new, message FROM '
                        'agro_work_import_runs')[0]
        self.assertEqual(run['status'], 'error')
        self.assertEqual(run['rows_new'], 5)
        self.assertIn('503', run['message'])
        self.assertEqual(len(self.rows('SELECT id FROM agro_work_applications')), 5)

    def test_a_second_run_while_one_is_running_exits_4(self):
        con = sqlite3.connect(self.db)
        con.execute("INSERT INTO agro_work_import_runs (started_at, status, "
                    "tool_version) VALUES (?, 'running', 'x')",
                    (store.stamp(store.utc_now()),))
        con.commit()
        con.close()
        code, _, err = self.main('--db', self.db, '--credentials',
                                 self.credentials)
        self.assertEqual(code, 4)
        self.assertEqual(self.server.requests, [])


if __name__ == '__main__':
    unittest.main()
