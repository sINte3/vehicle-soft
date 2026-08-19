# -*- coding: utf-8 -*-
"""GPS-7: журнал приёма точек — миграция, тождество и экран.

Что проверяется и почему именно это:

1. **Тождество сходится.** `messages_seen = points_written + points_duplicate
   + points_no_position`. Прежний сборщик дронов всегда писал
   `records_written == records_seen`, и по такому журналу нельзя было
   ответить, приехало ли что-нибудь новое. Здесь корзины раздельные, и их
   сумма проверяется на настоящем прогоне коллектора, а не на выдуманных
   числах.

2. **Журнал никогда не останавливает сбор.** Нет базы, нет таблицы, база
   заблокирована — прогон обязан закончиться нормально, а точки лечь.

3. **Прерванный прогон оставляет о себе запись.** Иначе утром он неотличим от
   прогона, которого не было.

4. **Экран отличает «не сходится» от «всё хорошо»** и показывает это первым,
   а не прячет в столбце.

Запуск:
  python -m unittest tests.test_gps_sync_log -v
"""
import os
import sqlite3
import sys
import tempfile
import unittest

from datetime import datetime, timedelta

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from tests.harness import app, db, reset_db, create_admin, login
from models import GpsSyncLog, User

import migration_utils
import migrate_gps_sync_log_001 as sync_mig

from gps_collector import config, storage, sync_log
from gps_collector.main import collect
from gps_collector.tests.support import FakeServer, Installed, epoch
from gps_collector.wialon import Client

NOW = epoch("2026-08-10 07:00")
QUIET = lambda *args, **kwargs: None                               # noqa: E731


def _query(path, sql, args=()):
    con = sqlite3.connect(path)
    try:
        return con.execute(sql, args).fetchall()
    finally:
        con.close()


def _tables(path):
    return {r[0] for r in _query(
        path, "SELECT name FROM sqlite_master WHERE type='table'")}


class MigrationPaths(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.mkdtemp()
        self.db = os.path.join(self.folder, 'transport.db')
        self._saved = (sync_mig.DB_PATH, migration_utils.DB_PATH)
        sync_mig.DB_PATH = self.db
        migration_utils.DB_PATH = self.db

    def tearDown(self):
        sync_mig.DB_PATH, migration_utils.DB_PATH = self._saved

    def _make_db(self, with_registry=True):
        con = sqlite3.connect(self.db)
        if with_registry:
            con.execute('CREATE TABLE schema_migrations (id INTEGER PRIMARY '
                        'KEY, name TEXT, applied_at TEXT, checksum TEXT, '
                        'description TEXT)')
        else:
            con.execute('CREATE TABLE users (id INTEGER PRIMARY KEY)')
        con.commit()
        con.close()

    def test_clean_database_creates_the_table(self):
        self._make_db()
        sync_mig.run()
        self.assertIn('gps_sync_log', _tables(self.db))
        columns = [r[1] for r in _query(self.db,
                                        'PRAGMA table_info(gps_sync_log)')]
        self.assertEqual(sorted(columns), sorted(sync_mig.EXPECTED_COLUMNS))

    def test_second_run_is_a_no_op(self):
        self._make_db()
        sync_mig.run()
        sync_mig.run()
        rows = _query(self.db, 'SELECT COUNT(*) FROM schema_migrations '
                               'WHERE name=?', (sync_mig.MIGRATION_ID,))
        self.assertEqual(rows[0][0], 1)

    def test_missing_database_exits_with_code_2_and_creates_no_file(self):
        with self.assertRaises(SystemExit) as caught:
            sync_mig.run()
        self.assertEqual(caught.exception.code, 2)
        self.assertFalse(os.path.exists(self.db))

    def test_a_database_that_is_not_ours_is_refused(self):
        self._make_db(with_registry=False)
        # migration_utils сам заводит реестр, поэтому предусловие проверяется
        # на базе, где реестра нет и создать его некому.
        con = sqlite3.connect(self.db)
        con.execute('DROP TABLE IF EXISTS schema_migrations')
        con.commit()
        con.close()
        original = migration_utils.ensure_schema_migrations_table
        migration_utils.ensure_schema_migrations_table = lambda: None
        sync_mig.ensure_schema_migrations_table = lambda: None
        try:
            with self.assertRaises(SystemExit) as caught:
                sync_mig.run()
        finally:
            migration_utils.ensure_schema_migrations_table = original
            sync_mig.ensure_schema_migrations_table = original
        self.assertEqual(caught.exception.code, 1)
        self.assertNotIn('gps_sync_log', _tables(self.db))

    def test_the_model_and_the_migration_describe_one_table(self):
        model = {c.name for c in GpsSyncLog.__table__.columns}
        self.assertEqual(model, set(sync_mig.EXPECTED_COLUMNS))
        self.assertEqual(GpsSyncLog.__tablename__, 'gps_sync_log')


class Identity(unittest.TestCase):
    """Пункт 1: тождество проверяется на НАСТОЯЩЕМ прогоне коллектора."""

    def setUp(self):
        self.folder = tempfile.mkdtemp()
        self.db = os.path.join(self.folder, 'transport.db')
        self._saved = (sync_mig.DB_PATH, migration_utils.DB_PATH)
        sync_mig.DB_PATH = self.db
        migration_utils.DB_PATH = self.db
        con = sqlite3.connect(self.db)
        con.execute('CREATE TABLE schema_migrations (id INTEGER PRIMARY KEY, '
                    'name TEXT, applied_at TEXT, checksum TEXT, '
                    'description TEXT)')
        con.commit()
        con.close()
        sync_mig.run()

    def tearDown(self):
        sync_mig.DB_PATH, migration_utils.DB_PATH = self._saved

    def _run(self, server, now=NOW):
        started = datetime.now(config.TZ)
        with Installed(server):
            client = Client(pause=0.0, log=QUIET)
            client.login('test-token-not-a-real-one')
            summary = collect(client, self.folder, now=now, log=QUIET)
            client.logout()
        sync_log.record(summary, started, datetime.now(config.TZ),
                        client=client, db_path=self.db, log=QUIET)
        return summary

    def _rows(self):
        con = sqlite3.connect(self.db)
        try:
            con.row_factory = sqlite3.Row
            return [dict(r) for r in con.execute(
                'SELECT * FROM gps_sync_log ORDER BY id')]
        finally:
            con.close()

    def test_a_first_run_balances_and_writes_everything_it_saw(self):
        summary = self._run(FakeServer(fleet=[{'id': 101, 'last_t': NOW - 3600}]))
        self.assertTrue(summary.balances())
        row = self._rows()[0]
        self.assertEqual(row['messages_seen'],
                         row['points_written'] + row['points_duplicate']
                         + row['points_no_position'])
        self.assertGreater(row['points_written'], 0)
        self.assertEqual(row['points_duplicate'], 0)
        self.assertEqual(row['status'], 'ok')
        self.assertEqual(row['logins'], 1)

    def test_a_replayed_interval_lands_in_the_duplicate_bucket(self):
        # [REASON]: именно здесь прежний сборщик дронов врал. Повтор обязан
        # показать дубли, а не «ничего не приехало» и не «всё новое».
        server = FakeServer(fleet=[{'id': 101, 'last_t': NOW - 3600}])
        self._run(server)
        storage.set_watermark(self.folder, 101, NOW - 6 * 3600)
        summary = self._run(server)
        self.assertTrue(summary.balances())
        row = self._rows()[1]
        self.assertEqual(row['points_written'], 0)
        self.assertGreater(row['points_duplicate'], 0)
        self.assertEqual(row['messages_seen'], row['points_duplicate'])

    def test_messages_without_a_fix_are_their_own_bucket(self):
        class NoFix(FakeServer):
            def messages(self, unit_id, t_from, t_to):
                found = FakeServer.messages(self, unit_id, t_from, t_to)
                for index, message in enumerate(found):
                    if index % 2 == 0:
                        message['pos'] = {}      # сообщение без координат
                return found

        summary = self._run(NoFix(fleet=[{'id': 101, 'last_t': NOW - 3600}]))
        self.assertTrue(summary.balances())
        row = self._rows()[0]
        self.assertGreater(row['points_no_position'], 0)
        self.assertEqual(row['messages_seen'],
                         row['points_written'] + row['points_duplicate']
                         + row['points_no_position'])

    def test_an_object_that_could_not_be_read_makes_the_run_partial(self):
        class Broken(FakeServer):
            def urlopen(self, request, timeout=None):
                import json
                import urllib.parse
                query = urllib.parse.parse_qs(request.data.decode('utf-8'))
                if query['svc'][0] == 'messages/load_interval':
                    self.calls.append(('messages/load_interval',
                                       json.loads(query['params'][0])))
                    raise OSError('connection reset')
                return FakeServer.urlopen(self, request, timeout)

        saved = config.RETRY_PAUSE_S
        config.RETRY_PAUSE_S = (0, 0, 0)
        try:
            self._run(Broken(fleet=[{'id': 101, 'last_t': NOW - 3600}]))
        finally:
            config.RETRY_PAUSE_S = saved
        row = self._rows()[0]
        self.assertEqual(row['status'], 'partial')
        self.assertEqual(row['units_failed'], 1)

    def test_the_interval_asked_for_is_recorded(self):
        self._run(FakeServer(fleet=[{'id': 101, 'last_t': NOW - 3600}]))
        row = self._rows()[0]
        self.assertIsNotNone(row['interval_from'])
        self.assertIsNotNone(row['interval_to'])
        self.assertLess(row['interval_from'], row['interval_to'])


class TheJournalNeverStopsTheCollection(unittest.TestCase):
    """Пункт 2. Точки важнее записи о точках."""

    def setUp(self):
        self.folder = tempfile.mkdtemp()

    def _summary(self):
        server = FakeServer(fleet=[{'id': 101, 'last_t': NOW - 3600}])
        with Installed(server):
            client = Client(pause=0.0, log=QUIET)
            client.login('test-token-not-a-real-one')
            summary = collect(client, self.folder, now=NOW, log=QUIET)
            client.logout()
        return summary, client

    def test_a_missing_database_is_reported_and_swallowed(self):
        summary, client = self._summary()
        written = sync_log.record(
            summary, datetime.now(config.TZ), datetime.now(config.TZ),
            client=client, db_path=os.path.join(self.folder, 'nope.db'),
            log=QUIET)
        self.assertFalse(written)
        self.assertGreater(summary.points_written, 0)

    def test_a_database_that_cannot_be_opened_at_all_is_swallowed_too(self):
        # [REASON]: два предыдущих теста возвращаются РАНЬШЕ любого исключения
        # -- по проверкам «нет файла» и «нет таблицы». Широкий except при этом
        # не выполняется вовсе, и мутация, сузившая его до ValueError,
        # проходила незамеченной. Здесь путь существует, но открыть его как
        # базу нельзя: сюда же попадут «база заблокирована» и «нет колонки».
        summary, client = self._summary()
        self.assertFalse(sync_log.record(
            summary, datetime.now(config.TZ), datetime.now(config.TZ),
            client=client, db_path=self.folder, log=QUIET))
        self.assertGreater(summary.points_written, 0)

    def test_a_database_without_the_table_is_reported_and_swallowed(self):
        path = os.path.join(self.folder, 'transport.db')
        sqlite3.connect(path).close()
        summary, client = self._summary()
        self.assertFalse(sync_log.record(
            summary, datetime.now(config.TZ), datetime.now(config.TZ),
            client=client, db_path=path, log=QUIET))
        self.assertGreater(summary.points_written, 0)


class Screen(unittest.TestCase):
    """Пункты 3 и 4."""

    def setUp(self):
        reset_db()
        self.admin_id = create_admin()
        with app.app_context():
            User.query.get(self.admin_id).language = 'ru'
            db.session.commit()

    def _add(self, **kwargs):
        values = dict(
            started_at=datetime(2026, 8, 10, 3, 0, 0),
            finished_at=datetime(2026, 8, 10, 3, 20, 0), status='ok',
            units_total=481, units_asked=298, units_silent=183,
            units_uptodate=0, units_empty=12, units_failed=0,
            messages_seen=1000, points_written=900, points_duplicate=80,
            points_no_position=20, requests=598, logins=1, abandoned=0,
            truncated=0)
        values.update(kwargs)
        with app.app_context():
            db.session.add(GpsSyncLog(**values))
            db.session.commit()

    def _html(self):
        client = app.test_client()
        login(client, self.admin_id)
        response = client.get('/gps/sync')
        self.assertEqual(response.status_code, 200)
        return response.get_data(as_text=True)

    def test_an_empty_journal_says_the_collector_never_ran(self):
        html = self._html()
        self.assertIn('Прогонов ещё не было', html)

    def test_a_good_run_is_shown_with_its_buckets(self):
        self._add()
        html = self._html()
        self.assertIn('Полный', html)
        self.assertIn('900', html)
        self.assertNotIn('счётчики не сходятся', html)

    def test_counters_that_do_not_balance_are_shouted_about(self):
        self._add(points_no_position=19)     # 900 + 80 + 19 != 1000
        html = self._html()
        self.assertIn('Счётчики не сходятся', html)
        self.assertIn('счётчики не сходятся', html)

    def test_an_interrupted_run_left_its_record(self):
        self._add(status='error', error='TimeoutError')
        html = self._html()
        self.assertIn('Прерван', html)
        self.assertIn('TimeoutError', html)

    def test_a_partial_run_is_neither_green_nor_red(self):
        self._add(status='partial', units_failed=7)
        html = self._html()
        self.assertIn('Частичный', html)
        self.assertNotIn('Прерван', html)

    def test_the_balance_property_matches_the_screen(self):
        with app.app_context():
            good = GpsSyncLog(messages_seen=10, points_written=6,
                              points_duplicate=3, points_no_position=1,
                              started_at=datetime.utcnow(),
                              finished_at=datetime.utcnow(), status='ok')
            bad = GpsSyncLog(messages_seen=10, points_written=6,
                             points_duplicate=3, points_no_position=0,
                             started_at=datetime.utcnow(),
                             finished_at=datetime.utcnow(), status='ok')
            self.assertTrue(good.balances)
            self.assertFalse(bad.balances)


if __name__ == '__main__':
    unittest.main()
