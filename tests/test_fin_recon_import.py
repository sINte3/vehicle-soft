# -*- coding: utf-8 -*-
"""FIN-RECON-STORAGE-001: импорт снимка (fin_recon/importer.py) и сверка.

Держится здесь:

* импорт одной транзакцией; повтор того же пакета -- ни одной записи, файл
  базы байт в байт тот же;
* изменённый v1 (та же версия, другой хеш) -- конфликт, старый v1 не тронут;
* v2 со ссылкой на хеш v1 -- новая версия, v1 не тронут, история видна;
  v2 с чужим хешем, v3 без v2 -- конфликт;
* сбой на любой таблице посреди транзакции -- в базе ничего;
* неизвестная зарплата лежит NULL, а не 0; выдачи без записи нет вовсе;
* межмесячная работа не размножается при повторе; загруженный соседний
  месяц даёт КАНДИДАТА в дубль с обеих сторон, ничего не исключая;
* «живые» таблицы приложения (drone_works, drone_flights, dji_area_*) импорт
  не читает и не пишет, а их изменение после импорта не меняет снимок;
* сверка `verify_db` находит правку строки в обход триггеров.

ВСЕ ДАННЫЕ СИНТЕТИЧЕСКИЕ. Запуск:
  python -m unittest tests.test_fin_recon_import -v
"""

import copy
import hashlib
import io
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest

from contextlib import redirect_stdout
from decimal import Decimal

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

import migration_utils  # noqa: E402
import migrate_fin_recon_storage_001 as mig  # noqa: E402
from fin_recon import contract, importer, package, schema, store  # noqa: E402
from fin_recon import verify as fverify  # noqa: E402
from tests import fin_recon_fixture as fx  # noqa: E402

LIVE_TABLES = ('drone_works', 'drone_flights', 'dji_area_calculations')


def file_sha(path):
    with open(path, 'rb') as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def migrate(path):
    saved = migration_utils.DB_PATH
    try:
        with redirect_stdout(io.StringIO()):
            return mig.run(path)
    finally:
        migration_utils.DB_PATH = saved


def make_live_db(path):
    """База с «живыми» таблицами приложения -- синтетическими."""
    con = sqlite3.connect(path)
    con.execute('CREATE TABLE drone_works (id INTEGER PRIMARY KEY, '
                'area_ha FLOAT, amount FLOAT, received_kind TEXT)')
    con.execute('CREATE TABLE drone_flights (id INTEGER PRIMARY KEY, '
                'dji_flight_id BIGINT, area_ha FLOAT)')
    con.execute('CREATE TABLE dji_area_calculations (id INTEGER PRIMARY KEY, '
                'flight_id BIGINT, accepted_m2 FLOAT)')
    con.executemany('INSERT INTO drone_works VALUES (?, ?, ?, ?)',
                    [(1, 10.0, 2000000.0, 'received'), (2, 5.5, None, None)])
    con.executemany('INSERT INTO drone_flights VALUES (?, ?, ?)',
                    [(1, 900000001, 0.012), (2, 900000003, 0.3)])
    con.executemany('INSERT INTO dji_area_calculations VALUES (?, ?, ?)',
                    [(1, 900000001, 0.0), (2, 900000003, 3000.0)])
    con.commit()
    con.close()


def live_state(path):
    con = sqlite3.connect(path)
    try:
        return {t: con.execute('SELECT * FROM %s ORDER BY id' % t).fetchall()
                for t in LIVE_TABLES}
    finally:
        con.close()


class ImportBase(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='fin_recon_imp_')
        self.db = os.path.join(self.tmp, 'transport.db')
        make_live_db(self.db)
        self.assertEqual(migrate(self.db), 0)
        self.n = 0

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def pkg(self, snapshot=None, **kwargs):
        self.n += 1
        root = os.path.join(self.tmp, 'pkg%d' % self.n)
        fx.write_package(root, snapshot=snapshot, **kwargs)
        return package.verify(root)

    def query(self, sql, args=()):
        con = sqlite3.connect(self.db)
        try:
            return con.execute(sql, args).fetchall()
        finally:
            con.close()

    def counts(self):
        con = sqlite3.connect(self.db)
        try:
            return {t: con.execute('SELECT COUNT(*) FROM %s' % t).fetchone()[0]
                    for t in schema.TABLE_NAMES}
        finally:
            con.close()


class Idempotence(ImportBase):

    def test_import_then_repeat_writes_nothing(self):
        pkg = self.pkg()
        first = importer.import_package(self.db, pkg, imported_by='t')
        self.assertEqual(first['result'], importer.RESULT_IMPORTED)
        self.assertEqual(first['row_counts']['fin_recon_work_row'], 38)
        self.assertEqual(first['row_counts']['fin_recon_operator_row'], 7)
        self.assertEqual(first['row_counts']['fin_recon_anomaly'], 39)
        self.assertEqual(first['row_counts']['fin_recon_cross_period_link'], 2)
        before, counts = file_sha(self.db), self.counts()
        again = importer.import_package(self.db, self.pkg(), imported_by='t')
        self.assertEqual(again['result'], importer.RESULT_ALREADY)
        self.assertEqual(again['rows_written'], 0)
        self.assertEqual(file_sha(self.db), before)
        self.assertEqual(self.counts(), counts)

    def test_cross_period_works_not_multiplied(self):
        pkg = self.pkg()
        for _ in range(3):
            importer.import_package(self.db, pkg, imported_by='t')
        self.assertEqual(self.query(
            'SELECT COUNT(*) FROM fin_recon_cross_period_link')[0][0], 2)
        self.assertEqual(self.query(
            "SELECT COUNT(*) FROM fin_recon_work_row WHERE work_key IN "
            "('W10', 'W27')")[0][0], 2)
        self.assertEqual(self.query(
            'SELECT salary_amount FROM fin_recon_cross_period_link ORDER BY '
            'position'), [('96000.00',), ('150000.00',)])


class Versions(ImportBase):

    def setUp(self):
        super(Versions, self).setUp()
        self.v1 = fx.build_snapshot()
        importer.import_package(self.db, self.pkg(self.v1), imported_by='t')
        self.v1_sha = contract.content_sha256(self.v1)
        self.v1_rows = self.query('SELECT * FROM fin_recon_anomaly ORDER BY id')

    def test_changed_v1_is_a_conflict_and_v1_stays(self):
        changed = copy.deepcopy(self.v1)
        changed['anomalies'][0]['rule'] = 'синтетик: бошқа қоида'
        before = file_sha(self.db)
        with self.assertRaises(importer.ImportConflict) as ctx:
            importer.import_package(self.db, self.pkg(changed),
                                    imported_by='t')
        self.assertIn('is a NEW version', str(ctx.exception))
        self.assertEqual(file_sha(self.db), before)

    def test_v2_supersedes_v1_without_touching_it(self):
        def change(s):
            s['anomalies'][0]['status'] = 'УЧТЕНО'
        v2 = fx.version_two(self.v1, change)
        result = importer.import_package(self.db, self.pkg(v2),
                                         imported_by='t')
        self.assertEqual(result['result'], importer.RESULT_IMPORTED)
        self.assertEqual(
            self.query('SELECT * FROM fin_recon_anomaly WHERE snapshot_id = 1 '
                       'ORDER BY id'), self.v1_rows)
        con = store.connect_ro(self.db)
        try:
            versions = store.versions_of(con, fx.PERIOD)
            latest = store.find_snapshot(con, fx.PERIOD)
            old = store.find_snapshot(con, fx.PERIOD, 1)
            self.assertEqual([v['version'] for v in versions], [1, 2])
            self.assertEqual(latest['version'], 2)
            self.assertEqual(latest['supersedes_id'], old['id'])
            self.assertEqual(old['content_sha256'], self.v1_sha)
            self.assertEqual(contract.content_sha256(
                store.load_snapshot(con, old['id'])), self.v1_sha)
            listing = store.list_snapshots(con)
            self.assertEqual(
                [r['superseded_by'] for r in listing if r['version'] == 1],
                [latest['id']])
        finally:
            con.close()

    def test_v2_with_a_foreign_hash_refused(self):
        v2 = fx.version_two(self.v1, lambda s: None)
        v2['header']['supersedes']['content_sha256'] = 'a' * 64
        with self.assertRaises(importer.ImportConflict) as ctx:
            importer.import_package(self.db, self.pkg(v2), imported_by='t')
        self.assertIn('supersedes v1 with content', str(ctx.exception))

    def test_v3_without_v2_refused(self):
        v2 = fx.version_two(self.v1, lambda s: None)
        v3 = fx.version_two(v2, lambda s: None)
        v3['header']['version'] = 3
        v3['header']['supersedes'] = {'version': 2, 'content_sha256':
                                      contract.content_sha256(v2)}
        with self.assertRaises(importer.ImportConflict) as ctx:
            importer.import_package(self.db, self.pkg(v3), imported_by='t')
        self.assertIn('which is not in the database', str(ctx.exception))


class Atomicity(ImportBase):

    def test_failure_on_any_table_leaves_nothing(self):
        empty = self.counts()
        before_live = live_state(self.db)
        for table in schema.CHILD_TABLES:
            with self.subTest(table=table):
                with self.assertRaises(RuntimeError):
                    importer.import_package(self.db, self.pkg(),
                                            imported_by='t', fail_after=table)
                self.assertEqual(self.counts(), empty)
        self.assertEqual(live_state(self.db), before_live)

    def test_failure_keeps_the_previous_version(self):
        v1 = fx.build_snapshot()
        importer.import_package(self.db, self.pkg(v1), imported_by='t')
        counts = self.counts()
        v2 = fx.version_two(v1, lambda s: None)
        with self.assertRaises(RuntimeError):
            importer.import_package(self.db, self.pkg(v2), imported_by='t',
                                    fail_after='fin_recon_work_row')
        self.assertEqual(self.counts(), counts)

    def test_schema_missing(self):
        db = os.path.join(self.tmp, 'bare.db')
        sqlite3.connect(db).close()
        with self.assertRaises(importer.SchemaMissing):
            importer.import_package(db, self.pkg(), imported_by='t')


class NullIsNotZero(ImportBase):

    def setUp(self):
        super(NullIsNotZero, self).setUp()
        importer.import_package(self.db, self.pkg(), imported_by='t')

    def test_unknown_salary_is_null(self):
        self.assertEqual(self.query(
            "SELECT value, evidence FROM fin_recon_metric WHERE metric_key = "
            "'UNKNOWN_SALARY_AMOUNT'"), [(None, 'UNKNOWN')])
        self.assertEqual(self.query(
            "SELECT COUNT(*) FROM fin_recon_metric WHERE evidence = 'UNKNOWN' "
            "AND value IS NOT NULL")[0][0], 0)
        self.assertEqual(self.query(
            "SELECT COUNT(*) FROM fin_recon_work_row WHERE salary_status = "
            "'NOT_ACCRUED_UNKNOWN'")[0][0], 3)

    def test_no_payout_for_unknown_salary_and_no_paid_flag(self):
        columns = {r[1] for t in schema.TABLE_NAMES
                   for r in self.query('PRAGMA table_info(%s)' % t)}
        self.assertFalse({'paid', 'is_paid', 'received_amount',
                          'received_kind'} & columns)
        op5 = self.query("SELECT kind, amount FROM fin_recon_payout WHERE "
                         "operator_key = 'OP5'")
        # одна текстовая запись книги на наличный блок -- и ничего на справку
        self.assertEqual(op5, [('BOOK_TEXT_RECORD', '300000.00')])

    def test_owner_recognition_is_not_book_text(self):
        self.assertEqual(self.query(
            "SELECT kind, evidence, record_date, text FROM fin_recon_payout "
            "WHERE operator_key = 'OP2'"),
            [('OWNER_RECOGNIZED', 'OWNER_INTERPRETATION', None, None)])


class LiveTablesAndDji(ImportBase):

    def test_import_does_not_touch_live_tables(self):
        before = live_state(self.db)
        importer.import_package(self.db, self.pkg(), imported_by='t')
        self.assertEqual(live_state(self.db), before)

    def test_live_dji_change_does_not_change_the_snapshot(self):
        pkg = self.pkg()
        importer.import_package(self.db, pkg, imported_by='t')
        con = sqlite3.connect(self.db)
        con.execute('UPDATE dji_area_calculations SET accepted_m2 = 0')
        con.execute('UPDATE drone_flights SET area_ha = area_ha * 2')
        con.execute('DELETE FROM drone_works')
        con.commit()
        con.close()
        con = store.connect_ro(self.db)
        try:
            report = fverify.verify_db(con, pkg)
            dji = store.load_snapshot(con, report['snapshot']['id'])['dji']
        finally:
            con.close()
        self.assertTrue(report['ok'], report['checks'])
        self.assertEqual(dji['accepted_ha'], '880.00340000')
        self.assertEqual(dji['raw_ha'], '900.1234')
        self.assertEqual(len(dji['decisions']), 4)


class CrossPeriodCandidates(ImportBase):

    def april_with_the_same_work(self, area_override=None):
        """Синтетический «апрель», где лежит та же работа, что W10 марта.

        Остальные работы апреля -- другие: даты сдвинуты на месяц вперёд, а у
        декабрьской работы W27 другой день, чтобы совпадение было ровно одно.
        """
        march = fx.build_snapshot()
        april = copy.deepcopy(march)
        april['header']['period'] = fx.NEXT_MONTH
        for w in april['works']:
            w['ledger_month'] = fx.NEXT_MONTH
            if w['key'] == 'W27':
                w['work_dates'] = ['%s-15' % fx.PREV_MONTH]
            elif w['key'] != 'W10':
                w['work_dates'] = [d.replace(fx.PERIOD, fx.NEXT_MONTH)
                                   for d in w['work_dates']]
                w['work_month'] = fx.NEXT_MONTH
        if area_override:
            next(w for w in april['works']
                 if w['key'] == 'W10')['area_ha'] = area_override
        april['cross_period_links'] = [
            dict(x, ledger_month=fx.NEXT_MONTH, accrual_month=fx.NEXT_MONTH)
            for x in april['cross_period_links'] if x['work'] == 'W27']
        for o in april['operators']:
            own = [w for w in april['works'] if w['operator'] == o['key']]
            slots = {}
            for w in own:
                slot = (w['work_month'], w['block'])
                slots[slot] = slots.get(slot, Decimal(0)) + Decimal(w['area_ha'])
            o['area']['breakdown'] = [
                {'work_month': m, 'block': b, 'area_ha': format(v, 'f')}
                for (m, b), v in sorted(slots.items())]
            o['area']['total_ha'] = format(sum(slots.values(), Decimal(0)),
                                           'f')
        vs = contract.vs_counts(april)
        april['vs']['db_area_ha'] = format(vs['db_area_ha'], 'f')
        april['metrics'] = fx.build_metrics(april)
        self.assertEqual(contract.validate(april), [])
        return march, april

    def test_neighbour_month_gives_a_candidate_both_ways(self):
        march, april = self.april_with_the_same_work()
        importer.import_package(self.db, self.pkg(march), imported_by='t')
        march_works = self.query('SELECT COUNT(*) FROM fin_recon_work_row')[0][0]
        con = store.connect_ro(self.db)
        try:
            march_id = store.find_snapshot(con, fx.PERIOD)['id']
            self.assertEqual(store.cross_period_candidates(con, march_id), [])
        finally:
            con.close()
        importer.import_package(self.db, self.pkg(april), imported_by='t')
        con = store.connect_ro(self.db)
        try:
            march_id = store.find_snapshot(con, fx.PERIOD)['id']
            april_id = store.find_snapshot(con, fx.NEXT_MONTH)['id']
            out = store.cross_period_candidates(con, march_id)
            back = store.cross_period_candidates(con, april_id)
        finally:
            con.close()
        self.assertEqual([(c['link_key'], c['other_period'],
                           c['other_work_key'], c['direction'])
                          for c in out],
                         [('L1', fx.NEXT_MONTH, 'W10', 'outgoing')])
        self.assertIn(('L1', fx.PERIOD, 'incoming'),
                      [(c['link_key'], c['link_period'], c['direction'])
                       for c in back])
        # ничего не исключено: работы марта на месте
        self.assertEqual(self.query(
            'SELECT COUNT(*) FROM fin_recon_work_row WHERE snapshot_id = ?',
            (march_id,))[0][0], march_works)

    def test_other_area_is_not_a_candidate(self):
        """Отрицательный контроль: другая площадь -- не кандидат."""
        march, april = self.april_with_the_same_work(area_override='99.99')
        importer.import_package(self.db, self.pkg(march), imported_by='t')
        importer.import_package(self.db, self.pkg(april), imported_by='t')
        con = store.connect_ro(self.db)
        try:
            march_id = store.find_snapshot(con, fx.PERIOD)['id']
            self.assertEqual(store.cross_period_candidates(con, march_id), [])
        finally:
            con.close()


class Verify(ImportBase):

    def test_clean_import_verifies_with_expectations(self):
        root = os.path.join(self.tmp, 'pkgv')
        paths = fx.write_package(root)
        pkg = package.verify(root)
        importer.import_package(self.db, pkg, imported_by='t')
        con = store.connect_ro(self.db)
        try:
            report = fverify.verify_db(
                con, pkg, package.load_expectations(paths['expect']))
        finally:
            con.close()
        self.assertTrue(report['ok'])
        self.assertTrue(all(e[3] for e in report['expectations']))
        self.assertEqual(len(report['expectations']), 24)

    def test_tampered_row_is_found(self):
        pkg = self.pkg()
        importer.import_package(self.db, pkg, imported_by='t')
        con = sqlite3.connect(self.db)
        con.execute('DROP TRIGGER trg_fin_recon_payout_no_update')
        con.execute("UPDATE fin_recon_payout SET amount = '1.00' WHERE "
                    "payout_key = 'P01'")
        con.commit()
        con.close()
        con = store.connect_ro(self.db)
        try:
            report = fverify.verify_db(con, pkg)
        finally:
            con.close()
        self.assertFalse(report['ok'])
        failed = [c[0] for c in report['checks'] if not c[3]]
        self.assertIn('content_sha256 (rebuilt from rows)', failed)

    def test_expectation_mismatch_is_reported(self):
        root = os.path.join(self.tmp, 'pkgw')
        paths = fx.write_package(root)
        pkg = package.verify(root)
        importer.import_package(self.db, pkg, imported_by='t')
        expect = package.load_expectations(paths['expect'])
        expect['expect']['check:ACCRUED_SUM'] = '8170000.58'
        con = store.connect_ro(self.db)
        try:
            report = fverify.verify_db(con, pkg, expect)
        finally:
            con.close()
        self.assertFalse(report['ok'])
        self.assertEqual([e[0] for e in report['expectations'] if not e[3]],
                         ['check:ACCRUED_SUM'])

    def test_readonly_connection_cannot_write(self):
        pkg = self.pkg()
        importer.import_package(self.db, pkg, imported_by='t')
        con = store.connect_ro(self.db)
        try:
            with self.assertRaises(sqlite3.OperationalError):
                con.execute('CREATE TABLE x (y)')
        finally:
            con.close()


if __name__ == '__main__':
    unittest.main()
