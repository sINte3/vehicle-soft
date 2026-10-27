# -*- coding: utf-8 -*-
"""DRONES_WORKS_JUN2026_MERGED_CUSTOMER_LINK_001 and
DRONES_WORKS_JUN2026_ZAMINLARI_ANCHOR_137_001: заказчик из объединённой ячейки.

Первая правка ставит drone_customer_id 31 июньской строке: 21 -- канонической
карточке «Заминлари» №137 (решение владельца 27.10.2026), 10 -- №138. Вторая,
отдельная, переносит одну уже сохранённую июньскую работу (id 997) с №136 на
№137. Меняется одно поле; всё остальное обязано остаться побайтно прежним.

Главные риски:
  - ЛИСТ-ТЁЗКА: «свод ичи (Сайфулло)» есть и в майской, и в июньской книге
    Агрокластера, и майские строки тоже пустые и без заказчика. Отрицательный
    контроль: обе правки обязаны оставить май как был, включая майские работы
    №136 (закрытый период);
  - УГАДАННЫЙ ID: карточка 137 сверяется по имени и активности. Отрицательные
    контроли: переименованная, выключенная и отсутствующая 137 -- отказ, и
    никакой подмены на 136.

Четыре пути устава и отрицательные контроли на каждую сеть. Проверка, дающая
одинаковый результат на верной и на испорченной базе, проверкой не является.

Run:
  python -m unittest tests.test_drone_works_merged_customer_link_001 -v
"""
import contextlib
import hashlib
import io
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(REPO_ROOT, 'migrate_drones_works_merged_customer_link_001.py')
sys.path.insert(0, REPO_ROOT)

import migrate_drones_works_merged_customer_link_001 as migration  # noqa: E402

SCHEMA = """
CREATE TABLE drone_customers (id INTEGER PRIMARY KEY, name TEXT NOT NULL,
  customer_type TEXT, is_active BOOLEAN DEFAULT 1);
CREATE TABLE drone_works (id INTEGER PRIMARY KEY AUTOINCREMENT,
  period_month TEXT NOT NULL, work_date_from TEXT, drone_operator_id INTEGER,
  drone_customer_id INTEGER, customer_raw TEXT NOT NULL, area_ha NUMERIC NOT NULL,
  price_per_ha NUMERIC, amount NUMERIC, other_costs NUMERIC, received_amount NUMERIC,
  payment_type TEXT NOT NULL, source_file TEXT, source_sheet TEXT, source_row INTEGER,
  import_batch TEXT, UNIQUE (source_file, source_sheet, source_row));
"""

CARDS = ((136, 'Бухоро Агрокластер Заминлари'), (137, 'Бухоро Агрокластер Заминлари МЧЖ'),
         (138, 'Бухоро Сервис Агрокластер'), (209, 'Заминлари Агрокластер'), (500, 'Чужое ФХ'))
JUNE = migration.SCOPES['june']['groups']
ANCHOR = migration.SCOPES['june-anchor']['groups'][0]
# The closed May of the namesake sheet, as on production: the first row of
# the merged range on 136, eight empty rows, and a second May work on 136.
MAY_FILE, MAY_SHEET = 'Агрокластер Дрон маълумот МАЙ.xlsx', 'свод ичи (Сайфулло)'
MAY_EMPTY_IDS = list(range(14, 22))
MAY_ON_136 = (13, 32)


def _insert(conn, wid, period, card, raw, area, amount, source_file, sheet, row_no):
    conn.execute('INSERT INTO drone_works (id, period_month, work_date_from, drone_customer_id, '
                 'customer_raw, area_ha, price_per_ha, amount, payment_type, source_file, '
                 'source_sheet, source_row, import_batch) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',
                 (wid, period, period + '-10', card, raw, area, 85632.59, amount, 'transfer',
                  source_file, sheet, row_no, 'b'))


def build_db(path, mutate=None):
    """Production as measured on 2026-10-22, the owner's cards of 2026-10-27, and neighbours."""
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA)
    for cid, name in CARDS:
        conn.execute('INSERT INTO drone_customers VALUES (?, ?, ?, 1)', (cid, name, 'external'))
    for g in JUNE:
        anchor_id, anchor_row = g['anchor']
        if anchor_id == ANCHOR['targets'][0][0]:
            _w, _r, period, area, amount = ANCHOR['targets'][0]
        else:
            period, area, amount = '2026-06', 20.0, 1712651.8
        _insert(conn, anchor_id, period, g['anchor_cards'][0], g['anchor_raw'], area, amount,
                g['file'], g['sheet'], anchor_row)
        for work_id, row_no, per, area, amount in g['targets']:
            _insert(conn, work_id, per, None, '', area, amount, g['file'], g['sheet'], row_no)
        # a neighbour of the same sheet with its OWN farm -- must stay as it is
        conn.execute('INSERT INTO drone_works (period_month, drone_customer_id, customer_raw, area_ha, '
                     'amount, other_costs, received_amount, payment_type, source_file, source_sheet, '
                     "source_row) VALUES ('2026-06', 500, 'Чужое ФХ', 7.5, 1500000, 50000, 1000000, "
                     "'cash', ?, ?, 200)", (g['file'], g['sheet']))
    _insert(conn, MAY_ON_136[0], '2026-05', 136, 'Бухоро Агрокластер Заминлари', 20.0, 1712651.8,
            MAY_FILE, MAY_SHEET, 16)
    for n, wid in enumerate(MAY_EMPTY_IDS):
        _insert(conn, wid, '2026-05', None, '', 10.0 + n, 856329.6, MAY_FILE, MAY_SHEET, 17 + n)
    _insert(conn, MAY_ON_136[1], '2026-05', 136, 'Бухоро Агрокластер Заминлари', 47.3, 4050446.41,
            'Другая майская книга.xlsx', 'свод ичи (Шахзод)', 9)
    # an unrelated book of June with an empty customer -- must stay unlinked
    conn.execute("INSERT INTO drone_works (period_month, customer_raw, area_ha, amount, payment_type, "
                 "source_file, source_sheet, source_row) VALUES ('2026-06', '', 3.3, 660000, 'cash', "
                 "'Другая книга Июнь.xlsx', 'свод ичи (Сайфулло)', 13)")
    if mutate:
        mutate(conn)
    conn.commit()
    conn.close()


def data_hash(path):
    conn = sqlite3.connect(path)
    try:
        h = hashlib.sha256()
        for table in ('drone_customers', 'drone_works'):
            for row in conn.execute('SELECT * FROM %s ORDER BY id' % table):
                h.update(repr(row).encode('utf-8'))
        return h.hexdigest()
    finally:
        conn.close()


def registry(path):
    conn = sqlite3.connect(path)
    try:
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'schema_migrations'").fetchone():
            return []
        return [r[0] for r in conn.execute('SELECT name FROM schema_migrations ORDER BY name')]
    finally:
        conn.close()


JUNE_ID = migration.SCOPES['june']['migration_id']
ANCHOR_ID = migration.SCOPES['june-anchor']['migration_id']


class MergedCustomerLinkTest(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.tmp.name, 'transport.db')
        self.receipt = os.path.join(self.tmp.name, 'receipt.json')

    def tearDown(self):
        self.tmp.cleanup()

    def run_main(self, *args):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = migration.main(['--db', self.db] + list(args))
        return rc, buf.getvalue()

    def apply(self, scope='june', receipt=None):
        return self.run_main('--scope', scope, '--apply', '--receipt', receipt or self.receipt)

    def links(self, ids):
        conn = sqlite3.connect(self.db)
        try:
            return {r[0]: r[1] for r in conn.execute(
                'SELECT id, drone_customer_id FROM drone_works WHERE id IN (%s)'
                % ','.join(str(i) for i in ids))}
        finally:
            conn.close()

    def may_state(self):
        return self.links(MAY_EMPTY_IDS + list(MAY_ON_136))

    # ---- the four paths -----------------------------------------------------------

    def test_dry_run_writes_nothing(self):
        build_db(self.db)
        before = data_hash(self.db)
        rc, out = self.run_main()
        self.assertEqual(rc, 0)
        self.assertIn('DRY RUN', out)
        self.assertEqual(data_hash(self.db), before)
        self.assertEqual(registry(self.db), [])

    def test_apply_links_21_rows_to_137_and_10_to_138(self):
        build_db(self.db)
        rc, out = self.apply()
        self.assertEqual(rc, 0, out)
        agro, service = JUNE
        self.assertEqual(set(self.links([t[0] for t in agro['targets']]).values()), {137})
        self.assertEqual(len(agro['targets']), 21)
        self.assertEqual(set(self.links([t[0] for t in service['targets']]).values()), {138})
        self.assertEqual(len(service['targets']), 10)
        self.assertEqual(registry(self.db), [JUNE_ID])
        self.assertIn('31 rows / 900.30 ha / 77095080.10', out)
        self.assertIn('B12:B33: 21 row(s) / 755.60 ha  card none -> 137', out)
        self.assertIn('B33:B43: 10 row(s) / 144.70 ha  card none -> 138', out)

    def test_the_june_link_leaves_the_stored_anchor_on_136(self):
        build_db(self.db)
        self.apply()
        self.assertEqual(self.links([997]), {997: 136})

    def test_second_apply_is_a_no_op(self):
        build_db(self.db)
        self.apply()
        after_first = data_hash(self.db)
        os.remove(self.receipt)
        rc, out = self.apply()
        self.assertEqual(rc, 0)
        self.assertIn('already applied', out)
        self.assertEqual(data_hash(self.db), after_first)

    def test_missing_database_gives_code_two_and_creates_nothing(self):
        r = subprocess.run([sys.executable, SCRIPT, '--db', self.db], capture_output=True, text=True)
        self.assertEqual(r.returncode, 2)
        self.assertFalse(os.path.exists(self.db))

    def test_failed_precondition_gives_code_one_and_changes_nothing(self):
        build_db(self.db, lambda c: c.execute('UPDATE drone_works SET drone_customer_id = 500 WHERE id = 1005'))
        before = data_hash(self.db)
        rc, out = self.apply()
        self.assertEqual(rc, 1)
        self.assertIn('PRECONDITION FAILED', out)
        self.assertEqual(data_hash(self.db), before)
        self.assertEqual(registry(self.db), [])
        self.assertFalse(os.path.exists(self.receipt))

    # ---- what must NOT move ------------------------------------------------------

    def test_may_is_not_touched_by_either_june_fix(self):
        # Negative control: the May rows satisfy "NULL card and empty customer"
        # in a sheet of the same name, and two May works sit on 136 -- only the
        # file name, the id list and the period guard keep them out.
        build_db(self.db)
        before = self.may_state()
        self.assertEqual(self.apply()[0], 0)
        self.assertEqual(self.apply('june-anchor', self.receipt + '.anchor')[0], 0)
        self.assertEqual(self.may_state(), before)
        self.assertEqual(set(self.links(MAY_EMPTY_IDS).values()), {None})
        self.assertEqual(self.links(list(MAY_ON_136)), {13: 136, 32: 136})

    def test_there_is_no_may_scope_any_more(self):
        build_db(self.db)
        before = data_hash(self.db)
        with self.assertRaises(SystemExit) as raised, contextlib.redirect_stderr(io.StringIO()):
            migration.main(['--db', self.db, '--scope', 'may'])
        self.assertEqual(raised.exception.code, 2)
        self.assertEqual(sorted(migration.SCOPES), ['june', 'june-anchor'])
        self.assertEqual(data_hash(self.db), before)

    def test_unrelated_empty_customer_rows_and_neighbours_stay_as_they_were(self):
        build_db(self.db)
        self.apply()
        conn = sqlite3.connect(self.db)
        try:
            other = conn.execute("SELECT drone_customer_id FROM drone_works WHERE source_file = "
                                 "'Другая книга Июнь.xlsx'").fetchone()[0]
            neighbours = {r[0] for r in conn.execute('SELECT drone_customer_id FROM drone_works '
                                                     'WHERE source_row = 200')}
        finally:
            conn.close()
        self.assertIsNone(other)
        self.assertEqual(neighbours, {500})

    def test_money_area_and_every_other_field_do_not_move(self):
        build_db(self.db)
        conn = sqlite3.connect(self.db)
        cols = [r[1] for r in conn.execute('PRAGMA table_info(drone_works)') if r[1] != 'drone_customer_id']
        before = conn.execute('SELECT %s FROM drone_works ORDER BY id' % ','.join(cols)).fetchall()
        conn.close()
        self.apply()
        self.apply('june-anchor', self.receipt + '.anchor')
        conn = sqlite3.connect(self.db)
        after = conn.execute('SELECT %s FROM drone_works ORDER BY id' % ','.join(cols)).fetchall()
        conn.close()
        self.assertEqual(before, after)

    def test_old_cards_are_not_deleted_or_renamed(self):
        build_db(self.db)
        conn = sqlite3.connect(self.db)
        cards_before = conn.execute('SELECT * FROM drone_customers ORDER BY id').fetchall()
        conn.close()
        self.apply()
        self.apply('june-anchor', self.receipt + '.anchor')
        conn = sqlite3.connect(self.db)
        try:
            self.assertEqual(conn.execute('SELECT * FROM drone_customers ORDER BY id').fetchall(),
                             cards_before)
        finally:
            conn.close()

    # ---- every precondition net, each with its own failing database -----------

    def assert_refused(self, mutate, needle, scope='june'):
        build_db(self.db, mutate)
        before = data_hash(self.db)
        rc, out = self.apply(scope)
        self.assertEqual(rc, 1, out)
        self.assertIn(needle, out)
        self.assertEqual(data_hash(self.db), before)
        self.assertEqual(registry(self.db), [])

    def test_area_drift_on_one_row_is_refused(self):
        self.assert_refused(lambda c: c.execute('UPDATE drone_works SET area_ha = 38.9 WHERE id = 998'),
                            'id 998: area or amount differs')

    def test_amount_drift_on_one_row_is_refused(self):
        self.assert_refused(lambda c: c.execute('UPDATE drone_works SET amount = amount + 1 WHERE id = 1050'),
                            'id 1050: area or amount differs')

    def test_a_reimported_book_with_shifted_ids_is_refused(self):
        self.assert_refused(lambda c: c.execute('UPDATE drone_works SET source_row = 99 WHERE id = 1043'),
                            'id 1043: key or period differs')

    def test_an_extra_unlinked_row_in_the_same_sheet_is_refused(self):
        self.assert_refused(
            lambda c: c.execute("INSERT INTO drone_works (period_month, customer_raw, area_ha, amount, "
                                "payment_type, source_file, source_sheet, source_row) VALUES ('2026-06', '', "
                                "1.0, 85632.59, 'transfer', 'ИЮНЬ Агрокластер.xlsx', 'свод ичи (Сайфулло)', 34)"),
            'other unlinked row(s)')

    def test_an_unlinked_first_row_of_the_range_is_refused(self):
        self.assert_refused(lambda c: c.execute('UPDATE drone_works SET drone_customer_id = NULL WHERE id = 1042'),
                            'first row of the range')

    def test_a_first_row_on_a_third_card_is_refused(self):
        self.assert_refused(lambda c: c.execute('UPDATE drone_works SET drone_customer_id = 209 WHERE id = 997'),
                            'first row of the range (id 997')

    def test_a_first_row_already_moved_to_137_is_accepted(self):
        build_db(self.db, lambda c: c.execute('UPDATE drone_works SET drone_customer_id = 137 WHERE id = 997'))
        rc, out = self.apply()
        self.assertEqual(rc, 0, out)

    def test_a_renamed_card_138_is_refused(self):
        self.assert_refused(lambda c: c.execute("UPDATE drone_customers SET name = 'Сервис' WHERE id = 138"),
                            'target card 138: is not named as the owner wrote it')

    def test_card_137_renamed_is_refused_not_guessed(self):
        self.assert_refused(lambda c: c.execute("UPDATE drone_customers SET name = 'Заминлари' WHERE id = 137"),
                            'target card 137: is not named as the owner wrote it')

    def test_card_137_switched_off_is_refused(self):
        self.assert_refused(lambda c: c.execute('UPDATE drone_customers SET is_active = 0 WHERE id = 137'),
                            'target card 137: is switched off')

    def test_card_137_missing_is_refused_and_136_is_not_used_instead(self):
        self.assert_refused(lambda c: c.execute('DELETE FROM drone_customers WHERE id = 137'),
                            'target card 137: does not exist')

    def test_card_137_with_a_null_active_flag_counts_as_active(self):
        build_db(self.db, lambda c: c.execute('UPDATE drone_customers SET is_active = NULL WHERE id = 137'))
        rc, out = self.apply()
        self.assertEqual(rc, 0, out)

    def test_a_row_with_a_customer_spelling_is_refused(self):
        self.assert_refused(lambda c: c.execute("UPDATE drone_works SET customer_raw = 'X' WHERE id = 1001"),
                            'id 1001: customer_raw is not empty')

    # ---- every postcondition net, proven by breaking the write ------------------

    def assert_post_refused(self, extra_sql, scope='june'):
        build_db(self.db)
        before = data_hash(self.db)
        original = migration.apply_changes

        def broken(conn, scope_):
            rows = original(conn, scope_)
            conn.execute(extra_sql)
            return rows
        migration.apply_changes = broken
        try:
            rc, out = self.apply(scope)
        finally:
            migration.apply_changes = original
        self.assertEqual(rc, 1, out)
        self.assertIn('POSTCONDITION FAILED', out)
        self.assertEqual(data_hash(self.db), before)
        self.assertEqual(registry(self.db), [])
        self.assertFalse(os.path.exists(self.receipt))
        return out

    def test_postcondition_sees_another_field_of_a_target_row_moving(self):
        out = self.assert_post_refused('UPDATE drone_works SET payment_type = \'cash\' WHERE id = 1010')
        self.assertIn('a field other than drone_customer_id moved', out)

    def test_postcondition_sees_a_row_outside_the_list_changing(self):
        out = self.assert_post_refused("UPDATE drone_works SET drone_customer_id = 137 WHERE source_file = "
                                       "'Другая книга Июнь.xlsx'")
        self.assertIn('a row outside the target list changed', out)

    def test_postcondition_sees_a_target_row_relinked_elsewhere(self):
        out = self.assert_post_refused('UPDATE drone_works SET drone_customer_id = 500 WHERE id = 1052')
        self.assertIn('id 1052: not linked to card 138', out)

    def test_postcondition_sees_a_may_row_moving_with_the_anchor(self):
        out = self.assert_post_refused('UPDATE drone_works SET drone_customer_id = 137 WHERE id = 13',
                                       scope='june-anchor')
        self.assertIn('card 136: expected -1 rows', out)

    def test_postcondition_is_silent_on_a_correct_apply(self):
        build_db(self.db)
        rc, out = self.apply()
        self.assertEqual(rc, 0)
        self.assertNotIn('FAILED', out)

    # ---- history and rollback -----------------------------------------------------

    def test_apply_without_a_receipt_is_refused(self):
        build_db(self.db)
        before = data_hash(self.db)
        rc, out = self.run_main('--apply')
        self.assertEqual(rc, 1)
        self.assertEqual(data_hash(self.db), before)
        open(self.receipt, 'w').close()
        rc, out = self.apply()
        self.assertEqual(rc, 1)
        self.assertEqual(data_hash(self.db), before)

    def test_an_unwritable_receipt_commits_nothing(self):
        build_db(self.db)
        before = data_hash(self.db)
        self.receipt = os.path.join(self.tmp.name, 'no_such_dir', 'receipt.json')
        rc, out = self.apply()
        self.assertEqual(rc, 1, out)
        self.assertIn('RESULT: NOT APPLIED', out)
        self.assertEqual(data_hash(self.db), before)
        self.assertEqual(registry(self.db), [])

    def test_receipt_holds_every_row_before_and_after_and_the_rollback(self):
        build_db(self.db)
        self.apply()
        with open(self.receipt, encoding='utf-8') as fh:
            rec = json.load(fh)
        self.assertEqual(rec['migration_id'], JUNE_ID)
        self.assertEqual(rec['status'], 'APPLIED')
        self.assertFalse(os.path.exists(self.receipt + '.pending'))
        self.assertEqual(len(rec['rows']), 31)
        self.assertTrue(all(r['drone_customer_id_before'] is None for r in rec['rows']))
        self.assertEqual({r['drone_customer_id_after'] for r in rec['rows']}, {137, 138})
        self.assertEqual(sum(1 for r in rec['rows'] if r['drone_customer_id_after'] == 137), 21)
        self.assertEqual(len(rec['rollback_sql']), 3)

    def test_printed_rollback_restores_the_database_exactly(self):
        build_db(self.db)
        before = data_hash(self.db)
        rc, out = self.apply()
        self.assertEqual(rc, 0)
        self.assertNotEqual(data_hash(self.db), before)
        sql = [line.strip() for line in out.splitlines() if line.strip().startswith(('UPDATE', 'DELETE'))]
        conn = sqlite3.connect(self.db)
        for statement in sql:
            conn.execute(statement)
        conn.commit()
        conn.close()
        self.assertEqual(data_hash(self.db), before)
        self.assertEqual(registry(self.db), [])

    # ---- the anchor move: its own migration, its own receipt ---------------------

    def test_anchor_dry_run_writes_nothing(self):
        build_db(self.db)
        before = data_hash(self.db)
        rc, out = self.run_main('--scope', 'june-anchor')
        self.assertEqual(rc, 0, out)
        self.assertIn('1 rows / 27.00 ha / 2312079.93', out)
        self.assertEqual(data_hash(self.db), before)

    def test_anchor_moves_997_only_and_has_its_own_receipt(self):
        build_db(self.db)
        rc, out = self.apply('june-anchor')
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.links([997]), {997: 137})
        self.assertEqual(registry(self.db), [ANCHOR_ID])
        with open(self.receipt, encoding='utf-8') as fh:
            rec = json.load(fh)
        self.assertEqual(rec['migration_id'], ANCHOR_ID)
        self.assertEqual([(r['id'], r['drone_customer_id_before'], r['drone_customer_id_after'])
                          for r in rec['rows']], [(997, 136, 137)])
        self.assertIn('UPDATE drone_works SET drone_customer_id = 136 WHERE id IN (997) AND '
                      'drone_customer_id = 137;', rec['rollback_sql'])

    def test_the_two_migrations_are_independent_in_either_order(self):
        for n, (first, second) in enumerate((('june', 'june-anchor'), ('june-anchor', 'june'))):
            build_db(self.db)
            self.assertEqual(self.apply(first, '%s.%d.%s' % (self.receipt, n, first))[0], 0)
            self.assertEqual(self.apply(second, '%s.%d.%s' % (self.receipt, n, second))[0], 0)
            self.assertEqual(registry(self.db), sorted([JUNE_ID, ANCHOR_ID]))
            agro = [t[0] for t in JUNE[0]['targets']] + [997]
            self.assertEqual(set(self.links(agro).values()), {137})
            os.remove(self.db)

    def test_anchor_rerun_is_a_no_op(self):
        build_db(self.db)
        self.apply('june-anchor')
        after = data_hash(self.db)
        os.remove(self.receipt)
        rc, out = self.apply('june-anchor')
        self.assertEqual(rc, 0)
        self.assertIn('already applied', out)
        self.assertEqual(data_hash(self.db), after)

    def test_anchor_refuses_another_june_work_on_136(self):
        self.assert_refused(
            lambda c: c.execute('UPDATE drone_works SET drone_customer_id = 136 WHERE source_row = 200 '
                                "AND source_file = 'Сервис Дрон маълумот Июнь.xlsx'"),
            'card 136 in 2026-06 holds ids', scope='june-anchor')

    def test_anchor_refuses_997_on_another_card(self):
        self.assert_refused(lambda c: c.execute('UPDATE drone_works SET drone_customer_id = 500 WHERE id = 997'),
                            'id 997: on card 500, expected 136', scope='june-anchor')

    def test_anchor_refuses_a_renamed_source_card(self):
        self.assert_refused(lambda c: c.execute("UPDATE drone_customers SET name = 'X' WHERE id = 136"),
                            'source card 136: is not named', scope='june-anchor')

    def test_anchor_refuses_a_renamed_137(self):
        self.assert_refused(lambda c: c.execute("UPDATE drone_customers SET name = 'X' WHERE id = 137"),
                            'target card 137: is not named', scope='june-anchor')

    def test_anchor_printed_rollback_restores_the_database_exactly(self):
        build_db(self.db)
        before = data_hash(self.db)
        rc, out = self.apply('june-anchor')
        self.assertEqual(rc, 0)
        sql = [line.strip() for line in out.splitlines() if line.strip().startswith(('UPDATE', 'DELETE'))]
        conn = sqlite3.connect(self.db)
        for statement in sql:
            conn.execute(statement)
        conn.commit()
        conn.close()
        self.assertEqual(data_hash(self.db), before)

    def test_anchor_moved_without_registry_row_is_needs_recovery(self):
        build_db(self.db, lambda c: c.execute('UPDATE drone_works SET drone_customer_id = 137 WHERE id = 997'))
        before = data_hash(self.db)
        rc, out = self.apply('june-anchor')
        self.assertEqual(rc, 3, out)
        self.assertEqual(data_hash(self.db), before)

    # ---- atomicity, interruption and recovery (ChatGPT review of 23.10.2026) ----

    def state(self):
        """(links of the 31 June rows to their card, registry names)."""
        want = {t[0]: g['card'] for g in JUNE for t in g['targets']}
        got = self.links(list(want))
        return sum(1 for i, c in want.items() if got.get(i) == c), registry(self.db)

    def patched(self, **funcs):
        saved = {name: getattr(migration, name) for name in funcs}
        for name, func in funcs.items():
            setattr(migration, name, func)
        self.addCleanup(lambda: [setattr(migration, n, f) for n, f in saved.items()])

    def test_dry_run_creates_no_registry_table_and_no_file(self):
        build_db(self.db)
        with open(self.db, 'rb') as fh:
            file_before = hashlib.sha256(fh.read()).hexdigest()
        rc, out = self.run_main('--receipt', self.receipt)
        self.assertEqual(rc, 0, out)
        self.assertIn('RESULT: DRY RUN OK', out)
        with open(self.db, 'rb') as fh:
            self.assertEqual(hashlib.sha256(fh.read()).hexdigest(), file_before)
        conn = sqlite3.connect(self.db)
        try:
            self.assertIsNone(conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'schema_migrations'").fetchone())
        finally:
            conn.close()
        self.assertEqual(sorted(os.listdir(self.tmp.name)), ['transport.db'])

    def test_a_failing_registry_helper_cannot_split_links_from_the_registry(self):
        # The defect ChatGPT reproduced: registry written by migration_utils
        # after COMMIT, failing -> 31 links, no registry row. Whatever the old
        # helper does now, links and registry row must land together.
        build_db(self.db)

        def broken(*args, **kwargs):
            raise RuntimeError('registry unavailable')
        saved = migration.migration_utils.record_migration
        migration.migration_utils.record_migration = broken
        self.addCleanup(setattr, migration.migration_utils, 'record_migration', saved)
        self.apply()
        self.assertIn(self.state(), ((31, [JUNE_ID]), (0, [])))

    def test_registry_insert_failing_inside_the_transaction_leaves_nothing(self):
        build_db(self.db)
        before = data_hash(self.db)

        def broken(*args, **kwargs):
            raise sqlite3.OperationalError('disk I/O error')
        self.patched(_record_in_transaction=broken)
        rc, out = self.apply()
        self.assertEqual(rc, 1, out)
        self.assertIn('RESULT: NOT APPLIED', out)
        self.assertEqual(data_hash(self.db), before)
        self.assertEqual(self.state(), (0, []))
        self.assertFalse(os.path.exists(self.receipt))
        self.assertFalse(os.path.exists(self.receipt + '.pending'))

    def test_commit_failing_before_it_happens_leaves_nothing_and_says_so(self):
        build_db(self.db)
        before = data_hash(self.db)

        def broken(conn):
            raise sqlite3.OperationalError('database is locked')
        self.patched(_commit=broken)
        rc, out = self.apply()
        self.assertEqual(rc, 1, out)
        self.assertIn('RESULT: NOT APPLIED', out)
        self.assertEqual(data_hash(self.db), before)
        self.assertEqual(self.state(), (0, []))
        self.assertFalse(os.path.exists(self.receipt))
        self.assertFalse(os.path.exists(self.receipt + '.pending'))
        self.assertTrue(any('.aborted-' in f for f in os.listdir(self.tmp.name)))

    def test_commit_raising_after_it_happened_is_reported_as_applied(self):
        build_db(self.db)
        real = migration._commit

        def committed_then_raised(conn):
            real(conn)
            raise sqlite3.OperationalError('error reported after commit')
        self.patched(_commit=committed_then_raised)
        rc, out = self.apply()
        self.assertEqual(rc, 0, out)
        self.assertIn('RESULT: APPLIED', out)
        self.assertEqual(self.state(), (31, [JUNE_ID]))
        with open(self.receipt, encoding='utf-8') as fh:
            self.assertEqual(json.load(fh)['status'], 'APPLIED')

    def test_crash_after_commit_before_the_receipt_is_recovered_by_a_rerun(self):
        build_db(self.db)
        real = migration._finalize_receipt

        def killed(*args):
            raise SystemExit('process killed')
        migration._finalize_receipt = killed
        try:
            with self.assertRaises(SystemExit):
                self.apply()
        finally:
            migration._finalize_receipt = real
        self.assertEqual(self.state(), (31, [JUNE_ID]))
        self.assertTrue(os.path.exists(self.receipt + '.pending'))
        self.assertFalse(os.path.exists(self.receipt))
        rc, out = self.apply()
        self.assertEqual(rc, 0, out)
        self.assertIn('RESULT: APPLIED', out)
        self.assertFalse(os.path.exists(self.receipt + '.pending'))
        with open(self.receipt, encoding='utf-8') as fh:
            rec = json.load(fh)
        self.assertEqual(rec['status'], 'APPLIED')
        self.assertIn('recovered_at_utc', rec)
        self.assertEqual(self.state(), (31, [JUNE_ID]))

    def test_crash_before_commit_leaves_nothing_and_a_rerun_applies_cleanly(self):
        build_db(self.db)
        before = data_hash(self.db)
        real = migration._commit

        def killed(conn):
            raise SystemExit('process killed')
        migration._commit = killed
        try:
            with self.assertRaises(SystemExit):
                self.apply()
        finally:
            migration._commit = real
        self.assertEqual(data_hash(self.db), before)
        self.assertEqual(self.state(), (0, []))
        self.assertTrue(os.path.exists(self.receipt + '.pending'))
        rc, out = self.apply()
        self.assertEqual(rc, 0, out)
        self.assertIn('stopped before COMMIT', out)
        self.assertEqual(self.state(), (31, [JUNE_ID]))
        self.assertTrue(any('.aborted-' in f for f in os.listdir(self.tmp.name)))
        with open(self.receipt, encoding='utf-8') as fh:
            self.assertEqual(json.load(fh)['status'], 'APPLIED')

    def test_registry_row_without_links_is_needs_recovery(self):
        build_db(self.db)
        migration.migration_utils.DB_PATH = self.db
        migration.migration_utils.record_migration(JUNE_ID, description='hand-made', checksum='x')
        before = data_hash(self.db)
        for args in ((), ('--apply', '--receipt', self.receipt)):
            rc, out = self.run_main(*args)
            self.assertEqual(rc, 3, out)
            self.assertIn('RESULT: NEEDS_RECOVERY', out)
        self.assertEqual(data_hash(self.db), before)

    def test_links_without_registry_row_is_needs_recovery(self):
        build_db(self.db, lambda c: c.execute('UPDATE drone_works SET drone_customer_id = 137 WHERE id = 998'))
        before = data_hash(self.db)
        rc, out = self.apply()
        self.assertEqual(rc, 3, out)
        self.assertIn('RESULT: NEEDS_RECOVERY', out)
        self.assertEqual(data_hash(self.db), before)
        self.assertEqual(registry(self.db), [])

    def test_a_receipt_of_a_change_the_database_does_not_hold_is_needs_recovery(self):
        build_db(self.db)
        with open(self.receipt, 'w', encoding='utf-8') as fh:
            json.dump({'migration_id': JUNE_ID, 'status': 'APPLIED'}, fh)
        before = data_hash(self.db)
        rc, out = self.apply()
        self.assertEqual(rc, 3, out)
        self.assertEqual(data_hash(self.db), before)

    def test_preconditions_are_checked_again_under_the_write_lock(self):
        build_db(self.db)
        seen = []
        real = migration.check_precondition

        def spy(conn, scope):
            seen.append(conn.in_transaction)
            return real(conn, scope)
        self.patched(check_precondition=spy)
        rc, out = self.apply()
        self.assertEqual(rc, 0, out)
        self.assertEqual(seen, [True])

    def test_the_console_never_leaks_cyrillic(self):
        build_db(self.db, lambda c: c.execute('UPDATE drone_works SET area_ha = 1 WHERE id = 998'))
        r = subprocess.run([sys.executable, SCRIPT, '--db', self.db], capture_output=True)
        self.assertEqual(r.returncode, 1)
        r.stdout.decode('ascii')
        build_db_ok = os.path.join(self.tmp.name, 'ok.db')
        build_db(build_db_ok)
        for scope in ('june', 'june-anchor'):
            r = subprocess.run([sys.executable, SCRIPT, '--db', build_db_ok, '--scope', scope],
                               capture_output=True)
            self.assertEqual(r.returncode, 0)
            r.stdout.decode('ascii')


if __name__ == '__main__':
    unittest.main()
