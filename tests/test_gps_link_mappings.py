# -*- coding: utf-8 -*-
"""GPS-10: связка vialon_mappings.wialon_id с объектами Wialon.

Что проверяется и почему именно это:

1. **Пишется только с `--apply`** и только то, что совпало ровно с одним
   объектом. Сухой прогон обязан оставить базу нетронутой.

2. **Ничего не угадывается.** Имя на двух объектах, имя без объекта, две наши
   строки с одним именем с точностью до пробелов и регистра — всё это в CSV,
   а не в базу. Взять первый из двух значит посчитать чужой трек.

3. **Стоящий `wialon_id` никогда не перезаписывается** — ни планом, ни самим
   UPDATE: между планом и записью строку мог тронуть человек.

4. **Латиница и кириллица — разные буквы.** Отрицательный контроль
   нормализации: «MT3» не должно совпасть с «МТЗ», иначе нормализация
   превращается в угадывание.

5. **Пустой парк — отказ, а не пустой план.** 481 объект не исчезает за ночь.

6. **Консоль ASCII** при узбекских именах — та самая буква U+04B2, что убила
   прогон 18.08 на cp1251.

7. **Всё или ничего.** Если хоть одна строка изменилась между планом и
   записью, не записывается ни одна.

Сети не требуется: сервер заменён консервами, код скрипта выполняется целиком.

Запуск:
  python -m unittest tests.test_gps_link_mappings -v
"""
import csv
import io
import json
import os
import sqlite3
import sys
import tempfile
import unittest
import urllib.parse
import urllib.request
from datetime import datetime

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

import tools.gps_link_mappings as link  # noqa: E402
from gps_collector import config  # noqa: E402

DDL = (
    'CREATE TABLE vialon_mappings (id INTEGER PRIMARY KEY AUTOINCREMENT, '
    'vialon_name VARCHAR(300) NOT NULL UNIQUE, wialon_id INTEGER, '
    'equipment_id INTEGER, skip BOOLEAN, created_by INTEGER, '
    'created_at DATETIME, updated_at DATETIME)',
    'CREATE INDEX ix_vialon_mappings_wialon_id ON vialon_mappings (wialon_id)',
    'CREATE TABLE equipment (id INTEGER PRIMARY KEY AUTOINCREMENT, '
    'name VARCHAR(200) NOT NULL, plate VARCHAR(50), '
    'category VARCHAR(20) NOT NULL, eq_type VARCHAR(100), '
    'organization_id INTEGER NOT NULL, default_price FLOAT, '
    'default_unit VARCHAR(30), is_active BOOLEAN, model_id INTEGER)',
)

# База до миграции CORE_FOUNDATION_001: колонки wialon_id ещё нет.
DDL_WITHOUT_COLUMN = (
    'CREATE TABLE vialon_mappings (id INTEGER PRIMARY KEY AUTOINCREMENT, '
    'vialon_name VARCHAR(300) NOT NULL UNIQUE, equipment_id INTEGER, '
    'skip BOOLEAN, created_by INTEGER, created_at DATETIME, '
    'updated_at DATETIME)',
    DDL[2],
)

MORNING = int(datetime(2026, 8, 19, 6, 0, tzinfo=config.TZ).timestamp())
EVENING = int(datetime(2026, 8, 19, 19, 30, tzinfo=config.TZ).timestamp())


class FakeResponse:
    def __init__(self, payload):
        self._raw = json.dumps(payload).encode('utf-8')

    def read(self):
        return self._raw

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeServer:
    """Консервированный Wialon: список объектов и, при желании, отказ входа."""

    def __init__(self, units, login_error=None):
        self.units = units                      # (id, name, last_t)
        self.login_error = login_error
        self.calls = []

    def urlopen(self, request, timeout=None):
        query = urllib.parse.parse_qs(request.data.decode('utf-8'))
        svc = query['svc'][0]
        self.calls.append(svc)
        if svc == 'token/login':
            if self.login_error is not None:
                return FakeResponse({'error': self.login_error})
            return FakeResponse({'eid': 'SESSION'})
        if svc == 'core/search_items':
            items = []
            for unit_id, name, last_t in self.units:
                item = {'id': unit_id, 'nm': name}
                if last_t is not None:
                    item['pos'] = {'t': last_t}
                items.append(item)
            return FakeResponse({'items': items})
        return FakeResponse({})


class Installed:
    def __init__(self, server):
        self.server = server

    def __enter__(self):
        self.original = urllib.request.urlopen
        urllib.request.urlopen = self.server.urlopen
        return self.server

    def __exit__(self, *exc):
        urllib.request.urlopen = self.original
        return False


class Base(unittest.TestCase):
    ddl = DDL

    def setUp(self):
        self.folder = tempfile.mkdtemp()
        self.db = os.path.join(self.folder, 'transport.db')
        self.plan_csv = os.path.join(self.folder, 'plan.csv')
        con = sqlite3.connect(self.db)
        for statement in self.ddl:
            con.execute(statement)
        con.commit()
        con.close()
        os.environ['WIALON_TOKEN'] = 'test-token-not-a-real-one'

    def equipment(self, name, plate=''):
        con = sqlite3.connect(self.db)
        try:
            cursor = con.execute(
                'INSERT INTO equipment (name, plate, category, organization_id, '
                'is_active) VALUES (?, ?, ?, 1, 1)', (name, plate, 'tractor'))
            con.commit()
            return cursor.lastrowid
        finally:
            con.close()

    def mapping(self, name, equipment_id=None, wialon_id=None, skip=0):
        con = sqlite3.connect(self.db)
        try:
            cursor = con.execute(
                'INSERT INTO vialon_mappings (vialon_name, wialon_id, '
                'equipment_id, skip, created_at) VALUES (?, ?, ?, ?, ?)',
                (name, wialon_id, equipment_id, skip, '2026-08-01 00:00:00'))
            con.commit()
            return cursor.lastrowid
        finally:
            con.close()

    def run_tool(self, server, *extra):
        out = io.StringIO()
        saved = sys.stdout
        sys.stdout = out
        try:
            with Installed(server):
                code = link.main(['--db', self.db, '--pause', '0',
                                  '--plan-out', self.plan_csv] + list(extra))
        finally:
            sys.stdout = saved
        return code, out.getvalue()

    def rows(self):
        con = sqlite3.connect(self.db)
        try:
            con.row_factory = sqlite3.Row
            return {r['id']: dict(r) for r in con.execute(
                'SELECT * FROM vialon_mappings')}
        finally:
            con.close()

    def plan_rows(self):
        with open(self.plan_csv, encoding='utf-8-sig', newline='') as fh:
            return list(csv.DictReader(fh, delimiter=';'))

    def plan_row(self, **where):
        found = [row for row in self.plan_rows()
                 if all(row[key] == str(value) for key, value in where.items())]
        self.assertEqual(len(found), 1, (where, self.plan_rows()))
        return found[0]


class Linking(Base):
    """Пункты 1 и 4."""

    def test_an_exact_name_links_only_with_apply(self):
        eq = self.equipment('МТЗ 873', '80 873 GA')
        row = self.mapping('МТЗ 873 GA', eq)
        server = FakeServer([(5, 'МТЗ 873 GA', MORNING)])

        code, log = self.run_tool(server)
        self.assertEqual(code, 0, log)
        self.assertIn('to link: 1', log)
        self.assertIn('dry run: nothing was written', log)
        self.assertIsNone(self.rows()[row]['wialon_id'])
        planned = self.plan_row(mapping_id=row)
        self.assertEqual(planned['status'], link.LINK)
        self.assertEqual(planned['wialon_id_match'], '5')
        self.assertEqual(planned['equipment'], 'МТЗ 873')
        self.assertEqual(planned['last_message'], '2026-08-19 06:00')

        code, log = self.run_tool(server, '--apply')
        self.assertEqual(code, 0, log)
        self.assertIn('written: 1 links, 0 unlinks', log)
        after = self.rows()[row]
        self.assertEqual(after['wialon_id'], 5)
        self.assertIsNotNone(after['updated_at'])
        # equipment_id и skip не тронуты: скрипт ставит только id объекта
        self.assertEqual(after['equipment_id'], eq)
        self.assertEqual(after['skip'], 0)

    def test_a_second_apply_changes_nothing(self):
        row = self.mapping('МТЗ 873 GA', self.equipment('МТЗ 873'))
        server = FakeServer([(5, 'МТЗ 873 GA', MORNING)])
        self.run_tool(server, '--apply')
        before = self.rows()[row]['updated_at']

        code, log = self.run_tool(server, '--apply')
        self.assertEqual(code, 0, log)
        self.assertIn('already linked: 1', log)
        self.assertIn('to link: 0', log)
        self.assertIn('nothing to write', log)
        self.assertEqual(self.rows()[row]['updated_at'], before)
        self.assertEqual(self.plan_row(mapping_id=row)['status'], link.ALREADY)

    def test_spaces_and_case_are_not_a_different_name(self):
        row = self.mapping('  МТЗ  873 GA ', self.equipment('МТЗ 873'))
        server = FakeServer([(5, 'мтз 873 ga', MORNING)])
        code, log = self.run_tool(server, '--apply')
        self.assertEqual(code, 0, log)
        self.assertEqual(self.rows()[row]['wialon_id'], 5)

    def test_latin_lookalikes_are_a_different_name(self):
        # Отрицательный контроль: латинские M, T и цифра 3 против
        # кириллических М, Т, З. Похоже — не значит то же.
        row = self.mapping('MT3 873 GA', self.equipment('МТЗ 873'))
        server = FakeServer([(5, 'МТЗ 873 GA', MORNING)])
        code, log = self.run_tool(server, '--apply')
        self.assertEqual(code, 0, log)
        self.assertIn('name not found in Wialon: 1', log)
        self.assertIsNone(self.rows()[row]['wialon_id'])
        self.assertEqual(self.plan_row(mapping_id=row)['status'], link.NOT_FOUND)


class NoGuessing(Base):
    """Пункты 2 и 3."""

    def test_a_name_on_two_objects_is_not_guessed(self):
        row = self.mapping('МТЗ 873 GA', self.equipment('МТЗ 873'))
        server = FakeServer([(5, 'МТЗ 873 GA', MORNING),
                             (6, 'МТЗ 873 GA', EVENING)])
        code, log = self.run_tool(server, '--apply')
        self.assertEqual(code, 0, log)
        self.assertIn('name on several objects: 1', log)
        self.assertIn('to link: 0', log)
        self.assertIsNone(self.rows()[row]['wialon_id'])
        planned = self.plan_row(mapping_id=row)
        self.assertEqual(planned['status'], link.AMBIGUOUS)
        self.assertEqual(planned['wialon_id_match'], '5 6')
        # время последнего сообщения обоих: по нему человек выберет живой трекер
        self.assertEqual(planned['last_message'],
                         '2026-08-19 06:00 / 2026-08-19 19:30')

    def test_an_unknown_name_gets_candidates_by_plate_but_no_link(self):
        eq = self.equipment('Трактор Беларус', '80 873 GA')
        row = self.mapping('Трактор Беларус 873', eq)
        server = FakeServer([(5, 'МТЗ 873 GA', MORNING),
                             (7, 'Niva 80 350 SBA', MORNING)])
        code, log = self.run_tool(server, '--apply')
        self.assertEqual(code, 0, log)
        self.assertIsNone(self.rows()[row]['wialon_id'])
        planned = self.plan_row(mapping_id=row)
        self.assertEqual(planned['status'], link.NOT_FOUND)
        self.assertIn('5 МТЗ 873 GA', planned['candidates'])
        self.assertNotIn('Niva', planned['candidates'])

    def test_an_existing_link_is_never_overwritten(self):
        row = self.mapping('МТЗ 873 GA', self.equipment('МТЗ 873'), wialon_id=999)
        server = FakeServer([(5, 'МТЗ 873 GA', MORNING)])
        code, log = self.run_tool(server, '--apply')
        self.assertEqual(code, 0, log)
        self.assertIn('linked to another object than the name says: 1', log)
        self.assertEqual(self.rows()[row]['wialon_id'], 999)
        planned = self.plan_row(mapping_id=row)
        self.assertEqual(planned['status'], link.CONFLICT)
        self.assertEqual(planned['wialon_id_now'], '999')
        self.assertEqual(planned['wialon_id_match'], '5')

    def test_skipped_rows_are_left_alone(self):
        row = self.mapping('Чужой 111 AA', skip=1)
        server = FakeServer([(9, 'Чужой 111 AA', MORNING)])
        code, log = self.run_tool(server, '--apply')
        self.assertEqual(code, 0, log)
        self.assertIn('skipped (not ours): 1', log)
        self.assertIn('to link: 0', log)
        self.assertIsNone(self.rows()[row]['wialon_id'])
        # в план строка не попадает, и объект не считается «без строки»:
        # «не наша» — тоже знание о нём
        self.assertEqual(self.plan_rows(), [])

    def test_two_rows_with_one_name_up_to_spelling_block_each_other(self):
        first = self.mapping('МТЗ 873 GA', self.equipment('Первая'))
        second = self.mapping('мтз 873 ga', self.equipment('Вторая'))
        server = FakeServer([(5, 'МТЗ 873 GA', MORNING)])
        code, log = self.run_tool(server, '--apply')
        self.assertEqual(code, 0, log)
        self.assertIn('name collision inside mappings: 2', log)
        self.assertIsNone(self.rows()[first]['wialon_id'])
        self.assertIsNone(self.rows()[second]['wialon_id'])
        self.assertEqual(self.plan_row(mapping_id=first)['status'], link.COLLISION)

    def test_renamed_and_gone_objects_are_reported_not_touched(self):
        renamed = self.mapping('Старое имя', self.equipment('A'), wialon_id=5)
        gone = self.mapping('Ушедший', self.equipment('B'), wialon_id=6)
        server = FakeServer([(5, 'Новое имя', MORNING)])
        code, log = self.run_tool(server, '--apply')
        self.assertEqual(code, 0, log)
        self.assertIn('renamed in Wialon: 1 | gone from Wialon: 1', log)
        self.assertEqual(self.rows()[renamed]['wialon_id'], 5)
        self.assertEqual(self.rows()[gone]['wialon_id'], 6)
        planned = self.plan_row(mapping_id=renamed)
        self.assertEqual(planned['status'], link.RENAMED)
        self.assertEqual(planned['wialon_name_live'], 'Новое имя')
        self.assertEqual(self.plan_row(mapping_id=gone)['status'], link.GONE)

    def test_apply_is_all_or_nothing(self):
        # Пункт 7: вторая строка изменилась между планом и записью.
        clean = self.mapping('A')
        taken = self.mapping('B', wialon_id=777)
        con = sqlite3.connect(self.db)
        try:
            self.assertIsNone(link.apply_plan(con, [(clean, 5), (taken, 6)]))
        finally:
            con.close()
        self.assertIsNone(self.rows()[clean]['wialon_id'])
        self.assertEqual(self.rows()[taken]['wialon_id'], 777)

    def test_unset_is_all_or_nothing_too(self):
        linked = self.mapping('A', wialon_id=5)
        empty = self.mapping('B')
        con = sqlite3.connect(self.db)
        try:
            self.assertIsNone(link.apply_plan(con, [], [linked, empty]))
        finally:
            con.close()
        self.assertEqual(self.rows()[linked]['wialon_id'], 5)


class Reporting(Base):
    def test_a_machine_with_two_objects_is_linked_and_flagged(self):
        eq = self.equipment('МТЗ 873')
        first = self.mapping('МТЗ 873 GA', eq)
        second = self.mapping('МТЗ 873 GA (старый трекер)', eq)
        server = FakeServer([(5, 'МТЗ 873 GA', EVENING),
                             (6, 'МТЗ 873 GA (старый трекер)', MORNING)])
        code, log = self.run_tool(server, '--apply')
        self.assertEqual(code, 0, log)
        self.assertIn('equipment with several objects after linking: 1', log)
        self.assertEqual(self.rows()[first]['wialon_id'], 5)
        self.assertEqual(self.rows()[second]['wialon_id'], 6)
        self.assertEqual(self.plan_row(mapping_id=first)['equipment_objects'], '2')
        self.assertEqual(self.plan_row(mapping_id=second)['equipment_objects'], '2')

    def test_objects_without_a_row_are_listed(self):
        self.mapping('A', self.equipment('A'))
        server = FakeServer([(5, 'A', MORNING), (8, 'Новый трактор', EVENING)])
        code, log = self.run_tool(server)
        self.assertEqual(code, 0, log)
        self.assertIn('objects in Wialon without a mapping row: 1', log)
        planned = self.plan_row(status=link.NO_ROW)
        self.assertEqual(planned['wialon_id_match'], '8')
        self.assertEqual(planned['wialon_name_live'], 'Новый трактор')
        self.assertEqual(planned['last_message'], '2026-08-19 19:30')

    def test_the_console_stays_ascii_with_uzbek_names(self):
        # Пункт 6. U+04B2 в имени, и в совпавшем, и в ненайденном.
        self.mapping('Комбайн 741 KA (Ҳокимият)', self.equipment('Комбайн', '80 741 KA'))
        self.mapping('Niva Ҳокимият', self.equipment('Niva', '80 350 SBA'))
        server = FakeServer([(5, 'Комбайн 741 KA (Ҳокимият)', MORNING),
                             (7, 'Niva 80 350 SBA (Ҳокимият)', MORNING)])
        code, log = self.run_tool(server, '--apply')
        self.assertEqual(code, 0, log)
        self.assertTrue(log.isascii(), log)
        # а в CSV имена настоящие
        with open(self.plan_csv, encoding='utf-8-sig') as fh:
            body = fh.read()
        self.assertIn('Ҳокимият', body)


class ByHand(Base):
    """Решения из CSV -- тем же скриптом, с теми же замками."""

    def test_a_decision_from_the_csv_is_applied_by_set(self):
        row = self.mapping('МТЗ 873 GA', self.equipment('МТЗ 873'))
        server = FakeServer([(5, 'МТЗ 873 GA', MORNING),
                             (6, 'МТЗ 873 GA', EVENING)])
        code, log = self.run_tool(server, '--set', '%d=6' % row)
        self.assertEqual(code, 0, log)
        self.assertIn('by hand: link 1, unlink 0', log)
        self.assertIsNone(self.rows()[row]['wialon_id'])      # без --apply
        code, log = self.run_tool(server, '--set', '%d=6' % row, '--apply')
        self.assertEqual(code, 0, log)
        self.assertEqual(self.rows()[row]['wialon_id'], 6)

    def test_set_refuses_an_object_that_is_not_in_wialon_and_writes_nothing(self):
        wrong = self.mapping('Неизвестное', self.equipment('A'))
        clean = self.mapping('МТЗ 873 GA', self.equipment('B'))
        server = FakeServer([(5, 'МТЗ 873 GA', MORNING)])
        saved, sys.stderr = sys.stderr, io.StringIO()
        try:
            code, log = self.run_tool(server, '--set', '%d=999' % wrong, '--apply')
            problem = sys.stderr.getvalue()
        finally:
            sys.stderr = saved
        self.assertEqual(code, 2, log)
        self.assertIn('object 999 is not in Wialon', problem)
        # всё или ничего: автоплан тоже не записан
        self.assertIsNone(self.rows()[wrong]['wialon_id'])
        self.assertIsNone(self.rows()[clean]['wialon_id'])

    def test_set_never_overwrites(self):
        row = self.mapping('A', self.equipment('A'), wialon_id=5)
        server = FakeServer([(5, 'A', MORNING), (6, 'B', MORNING)])
        saved, sys.stderr = sys.stderr, io.StringIO()
        try:
            code, log = self.run_tool(server, '--set', '%d=6' % row, '--apply')
            problem = sys.stderr.getvalue()
        finally:
            sys.stderr = saved
        self.assertEqual(code, 2, log)
        self.assertIn('never overwritten', problem)
        self.assertEqual(self.rows()[row]['wialon_id'], 5)

    def test_set_contradicting_an_exact_name_is_refused(self):
        row = self.mapping('МТЗ 873 GA', self.equipment('A'))
        server = FakeServer([(5, 'МТЗ 873 GA', MORNING), (6, 'Другой', MORNING)])
        saved, sys.stderr = sys.stderr, io.StringIO()
        try:
            code, log = self.run_tool(server, '--set', '%d=6' % row, '--apply')
            problem = sys.stderr.getvalue()
        finally:
            sys.stderr = saved
        self.assertEqual(code, 2, log)
        self.assertIn('matches object 5 exactly', problem)
        self.assertIsNone(self.rows()[row]['wialon_id'])

    def test_set_on_a_skipped_row_is_refused(self):
        row = self.mapping('Чужой', skip=1)
        server = FakeServer([(5, 'Чужой', MORNING)])
        saved, sys.stderr = sys.stderr, io.StringIO()
        try:
            code, log = self.run_tool(server, '--set', '%d=5' % row, '--apply')
        finally:
            sys.stderr = saved
        self.assertEqual(code, 2, log)
        self.assertIsNone(self.rows()[row]['wialon_id'])

    def test_unset_clears_a_link_only_with_apply(self):
        row = self.mapping('A', self.equipment('A'), wialon_id=5)
        server = FakeServer([(5, 'A', MORNING)])
        code, log = self.run_tool(server, '--unset', str(row))
        self.assertEqual(code, 0, log)
        self.assertIn('by hand: link 0, unlink 1', log)
        self.assertEqual(self.rows()[row]['wialon_id'], 5)
        code, log = self.run_tool(server, '--unset', str(row), '--apply')
        self.assertEqual(code, 0, log)
        self.assertIn('written: 0 links, 1 unlinks', log)
        self.assertIsNone(self.rows()[row]['wialon_id'])

    def test_unset_of_an_unlinked_row_is_refused(self):
        row = self.mapping('A', self.equipment('A'))
        server = FakeServer([(7, 'B', MORNING)])
        saved, sys.stderr = sys.stderr, io.StringIO()
        try:
            code, log = self.run_tool(server, '--unset', str(row), '--apply')
            problem = sys.stderr.getvalue()
        finally:
            sys.stderr = saved
        self.assertEqual(code, 2, log)
        self.assertIn('already empty', problem)

    def test_a_malformed_set_is_refused_before_wialon(self):
        self.mapping('A', self.equipment('A'))
        server = FakeServer([(5, 'A', MORNING)])
        saved, sys.stderr = sys.stderr, io.StringIO()
        try:
            code, log = self.run_tool(server, '--set', 'abc', '--apply')
        finally:
            sys.stderr = saved
        self.assertEqual(code, 2, log)
        self.assertEqual(server.calls, [])


class Refusals(Base):
    """Пункт 5 и предусловия."""

    def test_an_empty_fleet_is_a_failure_not_an_empty_plan(self):
        row = self.mapping('A', self.equipment('A'))
        code, log = self.run_tool(FakeServer([]), '--apply')
        self.assertEqual(code, 1)
        self.assertIn('NOT treating', log)
        self.assertIsNone(self.rows()[row]['wialon_id'])
        self.assertFalse(os.path.exists(self.plan_csv))

    def test_a_failed_login_stops_before_the_database(self):
        row = self.mapping('A', self.equipment('A'))
        saved, sys.stderr = sys.stderr, io.StringIO()
        try:
            code, log = self.run_tool(
                FakeServer([(5, 'A', MORNING)], login_error=7), '--apply')
        finally:
            sys.stderr = saved
        self.assertEqual(code, 3)
        self.assertIsNone(self.rows()[row]['wialon_id'])
        self.assertFalse(os.path.exists(self.plan_csv))

    def test_a_missing_database_is_refused_and_not_created(self):
        missing = os.path.join(self.folder, 'nope.db')
        saved, sys.stderr = sys.stderr, io.StringIO()
        try:
            code = link.main(['--db', missing])
        finally:
            sys.stderr = saved
        self.assertEqual(code, 2)
        self.assertFalse(os.path.exists(missing))


class WithoutTheColumn(Base):
    ddl = DDL_WITHOUT_COLUMN

    def test_a_database_without_the_column_is_refused(self):
        self.mapping_without_column('A')
        saved, sys.stderr = sys.stderr, io.StringIO()
        try:
            server = FakeServer([(5, 'A', MORNING)])
            code, log = self.run_tool(server, '--apply')
            problem = sys.stderr.getvalue()
        finally:
            sys.stderr = saved
        self.assertEqual(code, 2)
        self.assertIn('migrate_core_foundation_001', problem)
        # до Wialon дело не дошло
        self.assertEqual(server.calls, [])

    def mapping_without_column(self, name):
        con = sqlite3.connect(self.db)
        try:
            con.execute('INSERT INTO vialon_mappings (vialon_name) VALUES (?)',
                        (name,))
            con.commit()
        finally:
            con.close()


class PureFunctions(unittest.TestCase):
    def test_normalization_rule(self):
        self.assertEqual(link.normalize_name('  МТЗ   873  GA '), 'мтз 873 ga')
        self.assertNotEqual(link.normalize_name('MT3 873 GA'),
                            link.normalize_name('МТЗ 873 GA'))

    def test_plate_suffix_drops_the_region_code_only(self):
        self.assertEqual(link.plate_suffix('80 873 GA'), '873GA')
        self.assertEqual(link.plate_suffix('873 GA'), '873GA')
        self.assertEqual(link.plate_suffix('25 111 AA'), '111AA')
        self.assertEqual(link.plate_suffix(''), '')

    def test_short_plates_give_no_candidates(self):
        units = [{'id': 1, 'name': 'МТЗ 873 GA', 'last_t': None}]
        self.assertEqual(link.candidates_by_plate('873', units), [])
        self.assertEqual([u['id'] for u in link.candidates_by_plate('80 873 GA', units)],
                         [1])


if __name__ == '__main__':
    unittest.main()
