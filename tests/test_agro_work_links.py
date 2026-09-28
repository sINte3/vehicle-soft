# -*- coding: utf-8 -*-
"""agro-work B1: ручные связки владельца -- сухой прогон, замки, откат.

Ответ владельца на вопрос 7 (28.09): 21 машину с нестандартным номером он
свяжет вручную один раз, через CSV, и связки переживают повторный импорт.
Здесь держится:

  * без --apply база не меняется ни на байт (sha256 до и после);
  * CSV читается в тех видах, в которых его вернёт Excel: `;` и cp1251,
    `,` и UTF-8 с BOM;
  * каждое неверное решение -- отказ всего пакета, база та же;
  * снятая связка остаётся строкой с отметкой, журнал пишет обе стороны;
  * связка, записанная инструментом, переживает следующий импорт.

Запуск: python -m unittest tests.test_agro_work_links -v
"""

import hashlib
import io
import os
import sqlite3
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from agro_work import importer, store                       # noqa: E402
from agro_work.client import Client, build_opener           # noqa: E402
from tests import agro_work_db as dbh                       # noqa: E402
from tests import agro_work_fake as fake_api                # noqa: E402
from tools import agro_work_links as tool                   # noqa: E402

T_STANDARD = fake_api.uuid_for(0xB, 1)
T_ODD = fake_api.uuid_for(0xB, 9)
T_ODD2 = fake_api.uuid_for(0xB, 10)


def sha(path):
    with open(path, 'rb') as fh:
        return hashlib.sha256(fh.read()).hexdigest()


class LinksCase(unittest.TestCase):
    def setUp(self):
        self.server = fake_api.FakeAgroWork()
        self.db = dbh.make_db()
        self.folder = os.path.dirname(self.db)
        con = sqlite3.connect(self.db)
        dbh.add_org(con, 1, 'Buxoro')
        dbh.add_equipment(con, 11, '80 001 EA')
        dbh.add_equipment(con, 12, '80 002 EA')
        dbh.add_equipment(con, 50, '', name='Экскаватор Hyundai')
        con.commit()
        con.close()
        self.server.work_types = [fake_api.work_type(1)]
        self.server.transports = [fake_api.transport(1),
                                  fake_api.transport(9, plate='ALFAKLAS12'),
                                  fake_api.transport(10, plate='БУХОРОАГ01')]
        self.server.applications = [fake_api.application(
            9, transport=9, created='2026-09-09T08:00:00+05:00')]
        self.server.histories = {fake_api.uuid_for(0xA, 9): fake_api.history(
            created='2026-09-09T08:00:00+05:00',
            completed='2026-09-09T19:00:00+05:00')}
        self.import_once()

    def tearDown(self):
        self.server.close()

    def import_once(self):
        client = Client({'login': fake_api.LOGIN, 'password': fake_api.PASSWORD},
                        base_url=self.server.base_url, pause=0,
                        log=lambda text: None, opener=build_opener(proxies={}))
        con = store.connect(self.db)
        try:
            return importer.run_import(client, con, 10, log=lambda text: None)
        finally:
            con.close()

    def main(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = tool.main(['--db', self.db] + list(args))
        return code, out.getvalue(), err.getvalue()

    def rows(self, sql, args=()):
        con = sqlite3.connect(self.db)
        con.row_factory = sqlite3.Row
        try:
            return [dict(r) for r in con.execute(sql, args)]
        finally:
            con.close()

    def write_csv(self, name, text, encoding):
        path = os.path.join(self.folder, name)
        with open(path, 'w', encoding=encoding, newline='') as fh:
            fh.write(text)
        return path

    def transport(self, transport_id):
        return self.rows('SELECT equipment_id, match_status FROM '
                         'agro_work_transports WHERE id = ?', (transport_id,))[0]


class DryRunAndApply(LinksCase):
    def test_without_apply_not_a_byte_changes(self):
        before = sha(self.db)
        code, out, _ = self.main('--set', 'ALFAKLAS12=50')
        self.assertEqual(code, 0)
        self.assertIn('link   ALFAKLAS12 -> equipment 50', out)
        self.assertIn('dry run: nothing was written', out)
        self.assertEqual(sha(self.db), before)

    def test_apply_writes_the_link_the_journal_and_the_resolved_state(self):
        code, out, err = self.main('--set', 'ALFAKLAS12=50', '--apply',
                                   '--note', 'owner 28.09')
        self.assertEqual(code, 0, err)
        self.assertTrue(out.isascii())
        link = self.rows('SELECT * FROM agro_work_transport_links')[0]
        self.assertEqual((link['agro_transport_id'], link['equipment_id'],
                          link['plate_at_link'], link['note']),
                         (T_ODD, 50, 'ALFAKLAS12', 'owner 28.09'))
        self.assertEqual(self.transport(T_ODD),
                         {'equipment_id': 50, 'match_status': 'manual'})
        journal = self.rows("SELECT source, entity, field, old_value, new_value "
                            "FROM agro_work_changes WHERE entity = 'link'")
        self.assertEqual(journal, [{'source': 'links', 'entity': 'link',
                                    'field': 'equipment_id', 'old_value': None,
                                    'new_value': '50'}])

    def test_the_link_survives_the_next_imports(self):
        self.main('--set', 'ALFAKLAS12=50', '--apply')
        for _ in range(2):
            self.import_once()
            self.assertEqual(self.transport(T_ODD),
                             {'equipment_id': 50, 'match_status': 'manual'})

    def test_unset_marks_the_row_and_the_machine_goes_back_to_unmatched(self):
        self.main('--set', 'ALFAKLAS12=50', '--apply')
        code, out, err = self.main('--unset', 'ALFAKLAS12', '--apply')
        self.assertEqual(code, 0, err)
        links = self.rows('SELECT unlinked_at FROM agro_work_transport_links')
        self.assertEqual(len(links), 1)
        self.assertIsNotNone(links[0]['unlinked_at'])
        self.assertEqual(self.transport(T_ODD),
                         {'equipment_id': None, 'match_status': 'none'})
        journal = self.rows("SELECT old_value, new_value FROM agro_work_changes "
                            "WHERE entity = 'link' ORDER BY id")
        self.assertEqual([(j['old_value'], j['new_value']) for j in journal],
                         [(None, '50'), ('50', None)])


class EquipmentList(LinksCase):
    def test_our_equipment_list_is_written_and_nothing_else_changes(self):
        import csv
        before = sha(self.db)
        path = os.path.join(self.folder, 'equipment.csv')
        code, out, err = self.main('--equipment-csv', path, '--set',
                                   'ALFAKLAS12=50', '--apply')
        self.assertEqual(code, 0, err)
        self.assertIn('our equipment: 3 row(s)', out)
        self.assertEqual(sha(self.db), before)       # --set не исполнялся
        with open(path, encoding='utf-8-sig') as fh:
            rows = list(csv.DictReader(fh, delimiter=';'))
        self.assertEqual([r['equipment_id'] for r in rows], ['11', '12', '50'])
        self.assertEqual(rows[2]['name'], 'Экскаватор Hyundai')
        self.assertEqual(rows[0]['organization'], 'Buxoro')


class FromCsv(LinksCase):
    HEADER = ('status;agro_transport_id;plate_number;brand;model;category;'
              'company;in_registry;applications;last_application_day;'
              'candidates;equipment_id\n')

    def test_excel_ru_semicolon_cp1251_is_read(self):
        path = self.write_csv(
            'ru.csv', self.HEADER
            + 'none;%s;ALFAKLAS12;MTZ;80.1;Чопиқ;Бухоро;1;1;2026-09-09;;50\n'
            .replace('Чопиқ', 'Чопик') % T_ODD
            + 'none;%s;БУХОРОАГ01;MTZ;80.1;Чопик;Бухоро;1;0;;;\n' % T_ODD2,
            'cp1251')
        code, out, err = self.main('--from-csv', path, '--apply')
        self.assertEqual(code, 0, err)
        self.assertEqual(self.transport(T_ODD)['equipment_id'], 50)
        self.assertEqual(self.transport(T_ODD2)['match_status'], 'none')

    def test_excel_en_comma_utf8_bom_is_read(self):
        path = self.write_csv(
            'en.csv', self.HEADER.replace(';', ',')
            + 'none,%s,ALFAKLAS12,MTZ,80.1,x,y,1,1,2026-09-09,,50\n' % T_ODD,
            'utf-8-sig')
        code, _, err = self.main('--from-csv', path, '--apply')
        self.assertEqual(code, 0, err)
        self.assertEqual(self.transport(T_ODD)['equipment_id'], 50)


class Refusals(LinksCase):
    def assert_refused(self, *args, expect=None):
        before = sha(self.db)
        code, out, err = self.main(*(args + ('--apply',)))
        self.assertEqual(code, 2, out + err)
        self.assertEqual(sha(self.db), before)
        if expect:
            self.assertIn(expect, err)
        self.assertTrue(err.isascii())

    def test_equipment_that_does_not_exist(self):
        self.assert_refused('--set', 'ALFAKLAS12=999', expect='does not exist')

    def test_a_plate_nobody_has(self):
        self.assert_refused('--set', 'NOSUCH777=50', expect='no agro-work machine')

    def test_our_machine_already_held_by_another_agro_machine(self):
        # 11 уже держит машина 1 (точное совпадение номера).
        self.assert_refused('--set', 'ALFAKLAS12=11', expect='already belongs')

    def test_a_second_link_needs_an_unset_first(self):
        self.main('--set', 'ALFAKLAS12=50', '--apply')
        self.assert_refused('--set', 'ALFAKLAS12=12', expect='--unset it first')

    def test_one_bad_row_refuses_the_whole_file(self):
        path = self.write_csv(
            'bad.csv', FromCsv.HEADER
            + 'none;%s;ALFAKLAS12;;;;;1;1;;;50\n' % T_ODD
            + 'none;%s;БУХОРОАГ01;;;;;1;0;;;пятьдесят\n' % T_ODD2, 'utf-8-sig')
        self.assert_refused('--from-csv', path, expect='must be a number')
        self.assertEqual(self.rows('SELECT COUNT(*) AS n FROM '
                                   'agro_work_transport_links'), [{'n': 0}])

    def test_a_csv_without_the_columns(self):
        path = self.write_csv('cols.csv', 'plate;id\nALFAKLAS12;50\n', 'utf-8')
        self.assert_refused('--from-csv', path, expect='column agro_transport_id')

    def test_a_database_without_the_migration(self):
        bare = os.path.join(self.folder, 'bare.db')
        sqlite3.connect(bare).close()
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = tool.main(['--db', bare, '--set', 'X=1'])
        self.assertEqual(code, 2)
        self.assertIn('migrate_agro_work_001.py', err.getvalue())


if __name__ == '__main__':
    unittest.main()
