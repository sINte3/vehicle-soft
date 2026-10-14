# -*- coding: utf-8 -*-
"""GPS-18: группы геозон ресурса Wialon -- где территории подразделений.

Что проверяется и почему именно это:

1. **НИЧЕГО НЕ ПИШЕТ.** База после прогона побайтно та же, и `open_readonly`
   физически отвергает запись -- запрет SQLite, а не обещание скрипта. К
   Wialon -- только читающие сервисы, и выход из сессии всегда.

2. **Отсутствующая база -- отказ, и файл не создаётся.** Как и база без
   `field_contours`: обе проверки до входа в Wialon.

3. **CSV говорят то, что прислал сервер:** две группы, зона в обеих, зона ни
   в одной; зеркало считается только по `source='wialon'`.

4. **Пустой ответ о группах -- отказ с кодом 4, а не «групп нет».** Флаг групп
   подтверждён только выдачей поиска по документации; неверный флаг ошибки не
   даёт, сервер молча не присылает часть. Консервированный сервер здесь
   отдаёт `zg` только при бите 0x00100000 в запрошенных флагах -- как
   настоящий: без флага части нет в ответе. Поэтому мутант с другим флагом
   ловится не тавтологией «константа равна себе», а поведением.

5. **Консоль ASCII и без имён.** Имена зон и групп -- часто имена людей; в
   консоль не попадают ни сами, ни транслитом.

6. **Отказ входа называет код ошибки**, как у GPS-12.

Сети не требуется: сервер заменён консервами, код скрипта выполняется целиком.

Запуск:
  python -m unittest tests.test_gps_zone_groups -v
"""
import csv
import hashlib
import io
import json
import os
import sqlite3
import sys
import tempfile
import unittest
import urllib.parse

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

import tools.gps_zone_groups as gz                                  # noqa: E402
from gps_collector import config                                    # noqa: E402
from tests.test_gps_link_mappings import (FakeResponse, FakeServer,  # noqa: E402
                                          Installed)

RESOURCE = 285

FIELD_CONTOURS_DDL = (
    'CREATE TABLE field_contours (id INTEGER PRIMARY KEY AUTOINCREMENT, '
    'source VARCHAR(20) NOT NULL, external_id VARCHAR(100), '
    'name VARCHAR(300) NOT NULL, name_uz VARCHAR(300), '
    'geometry_geojson TEXT, area_ha FLOAT, customer_id INTEGER, '
    'season_year INTEGER, is_active BOOLEAN DEFAULT 1, '
    'created_at DATETIME, updated_at DATETIME, '
    'CONSTRAINT uq_field_contours_source_external UNIQUE (source, external_id))')

# Гараж -- в группе 11; автобаза -- в 11 и 12; завод (окружность) -- в 12;
# поле фермера -- ни в одной. U+04B2 «Ҳ» -- буква, что убила прогон 18.08.
GARAGE, DEPOT, PLANT, FIELD = 101, 102, 103, 104
ZONES = [
    {'id': GARAGE, 'n': 'Гараж Ҳамроев', 't': 2},
    {'id': DEPOT, 'n': 'Автобаза №2', 't': 2},
    {'id': PLANT, 'n': 'Завод Бобохон', 't': 3},
    {'id': FIELD, 'n': '1508 Нурхон Бобохон', 't': 2},
]
GROUPS = {
    '11': {'id': 11, 'n': 'Гаражлар Ҳамроев', 'd': 'Ташкилот', 'f': 0,
           'zns': [GARAGE, DEPOT]},
    '12': {'id': 12, 'n': 'Автобазалар', 'd': '', 'f': 0,
           'zns': [DEPOT, PLANT]},
}
NAMES = [z['n'] for z in ZONES] + [g['n'] for g in GROUPS.values()] + [
    'Ташкилот', 'Бухоро агрокластер']

ABSENT = object()
READ_ONLY_SERVICES = {'token/login', 'core/search_items',
                      'resource/get_zone_data', 'core/logout'}


class ZoneServer(FakeServer):
    """Консервированный Wialon с ресурсом геозон и группами.

    Части ответа -- только по запрошенным битам, как у сервера: `nm` при 0x1,
    `zl` при 0x1000, группы при 0x00100000.
    """

    def __init__(self, zones=None, groups=None, resource=RESOURCE,
                 login_error=None, search_error=None, listed_ids=None,
                 data_ids=None):
        super().__init__([], login_error=login_error)
        self.zones = ZONES if zones is None else zones
        self.groups = GROUPS if groups is None else groups
        self.resource = resource
        self.search_error = search_error
        self.listed_ids = (listed_ids if listed_ids is not None
                           else [z['id'] for z in self.zones])
        self.data_ids = data_ids
        self.flags_asked = []
        self.data_asked = []

    def urlopen(self, request, timeout=None):
        query = urllib.parse.parse_qs(request.data.decode('utf-8'))
        svc = query['svc'][0]
        params = json.loads(query['params'][0])
        if svc == 'core/search_items':
            self.calls.append(svc)
            if self.search_error is not None:
                return FakeResponse({'error': self.search_error})
            flags = int(params['flags'])
            self.flags_asked.append(flags)
            item = {'id': self.resource}
            dealer = {'id': 170}
            if flags & 0x1:
                item['nm'] = 'Бухоро агрокластер'
                dealer['nm'] = 'Дилер'
            if flags & 0x1000:
                item['zl'] = {str(i): {'id': i} for i in self.listed_ids}
                dealer['zl'] = {}
            if flags & 0x00100000 and self.groups is not ABSENT:
                item['zg'] = self.groups
                dealer['zg'] = {}
            return FakeResponse({'items': [item, dealer]})
        if svc == 'resource/get_zone_data':
            self.calls.append(svc)
            wanted = set(params.get('col') or [])
            self.data_asked.append(sorted(wanted))
            served = (wanted if self.data_ids is None
                      else wanted & set(self.data_ids))
            return FakeResponse([z for z in self.zones if z['id'] in served])
        return super().urlopen(request, timeout)


class Base(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.mkdtemp()
        self.db = os.path.join(self.folder, 'transport.db')
        self.groups_out = os.path.join(self.folder, 'groups.csv')
        self.zones_out = os.path.join(self.folder, 'zones.csv')
        con = sqlite3.connect(self.db)
        try:
            con.execute(FIELD_CONTOURS_DDL)
            con.executemany(
                'INSERT INTO field_contours (source, external_id, name) '
                'VALUES (?, ?, ?)',
                [('wialon', str(GARAGE), 'Гараж Ҳамроев'),
                 ('wialon', str(PLANT), 'Завод Бобохон'),
                 ('wialon', '999', 'Исчезнувшая зона'),
                 # другой источник с тем же номером -- не зеркало Wialon
                 ('manual', str(FIELD), 'Поле вручную')])
            con.commit()
        finally:
            con.close()
        os.environ['WIALON_TOKEN'] = 'test-token-not-a-real-one'

    def run_tool(self, server, *extra, db=None):
        out, err = io.StringIO(), io.StringIO()
        saved = sys.stdout, sys.stderr
        sys.stdout, sys.stderr = out, err
        try:
            with Installed(server):
                code = gz.main(['--db', self.db if db is None else db,
                                '--pause', '0',
                                '--groups-out', self.groups_out,
                                '--zones-out', self.zones_out] + list(extra))
        finally:
            sys.stdout, sys.stderr = saved
        return code, out.getvalue(), err.getvalue()

    def read_csv(self, path):
        with open(path, encoding='utf-8-sig', newline='') as fh:
            return list(csv.DictReader(fh, delimiter=';'))

    def zones_by_id(self):
        return {int(row['zona_id']): row for row in self.read_csv(self.zones_out)}

    def assertNothingWritten(self):
        self.assertFalse(os.path.exists(self.groups_out))
        self.assertFalse(os.path.exists(self.zones_out))


class WritesNothing(Base):
    """Пункт 1."""

    @staticmethod
    def digest(path):
        with open(path, 'rb') as fh:
            return hashlib.sha256(fh.read()).hexdigest()

    def test_the_database_is_byte_identical_after_a_run(self):
        before = self.digest(self.db)
        files_before = set(os.listdir(self.folder))
        code, log, err = self.run_tool(ZoneServer())
        self.assertEqual(code, 0, log + err)
        self.assertEqual(self.digest(self.db), before)
        # и рядом с базой не появилось ни журнала, ни -wal: только два CSV
        self.assertEqual(set(os.listdir(self.folder)) - files_before,
                         {'groups.csv', 'zones.csv'})

    def test_a_readonly_connection_refuses_to_write(self):
        # [REASON]: отрицательный контроль самого запрета: без него «ничего не
        # пишет» -- обещание, тот же тест прошёл бы и на обычном connect.
        con = gz.open_readonly(self.db)
        try:
            with self.assertRaises(sqlite3.OperationalError) as caught:
                con.execute('DELETE FROM field_contours')
            self.assertIn('readonly', str(caught.exception))
        finally:
            con.close()

    def test_only_read_services_are_called_and_the_session_is_closed(self):
        server = ZoneServer()
        code, log, err = self.run_tool(server)
        self.assertEqual(code, 0, log + err)
        self.assertTrue(set(server.calls) <= READ_ONLY_SERVICES, server.calls)
        self.assertEqual(server.calls[0], 'token/login')
        self.assertEqual(server.calls[-1], 'core/logout')
        # все id одним запросом -- как зеркало GPS-4
        self.assertEqual(server.data_asked, [[GARAGE, DEPOT, PLANT, FIELD]])


class Refusals(Base):
    """Пункты 2 и 6."""

    def test_a_missing_database_is_refused_and_not_created(self):
        missing = os.path.join(self.folder, 'nope.db')
        server = ZoneServer()
        code, log, err = self.run_tool(server, db=missing)
        self.assertEqual(code, 2)
        self.assertIn('database not found', err)
        self.assertFalse(os.path.exists(missing))
        self.assertEqual(server.calls, [])          # в Wialon даже не ходили
        self.assertNothingWritten()

    def test_a_database_without_the_mirror_table_is_refused(self):
        other = os.path.join(self.folder, 'other.db')
        con = sqlite3.connect(other)
        con.execute('CREATE TABLE t (x INTEGER)')
        con.commit()
        con.close()
        server = ZoneServer()
        code, log, err = self.run_tool(server, db=other)
        self.assertEqual(code, 2)
        self.assertIn('field_contours is missing', err)
        self.assertEqual(server.calls, [])
        self.assertNothingWritten()

    def test_a_missing_output_folder_is_refused_before_login(self):
        server = ZoneServer()
        code, log, err = self.run_tool(
            server, '--zones-out', os.path.join(self.folder, 'net', 'z.csv'))
        self.assertEqual(code, 2)
        self.assertIn('does not exist', err)
        self.assertEqual(server.calls, [])

    def test_an_output_on_the_database_is_refused(self):
        # Опечатка в пути CSV не должна заменить базу таблицей.
        with open(self.db, 'rb') as fh:
            before = fh.read()
        alias = os.path.join(self.folder, 'alias.csv')
        os.link(self.db, alias)
        for target in (self.db, alias):
            with self.subTest(target=os.path.basename(target)):
                server = ZoneServer()
                code, log, err = self.run_tool(server, '--zones-out', target)
                self.assertEqual(code, 2, log + err)
                self.assertIn('refusing to overwrite', err)
                self.assertEqual(server.calls, [])
                with open(self.db, 'rb') as fh:
                    self.assertEqual(fh.read(), before)

    def test_the_same_file_for_both_outputs_is_refused(self):
        code, log, err = self.run_tool(ZoneServer(), '--zones-out',
                                       self.groups_out)
        self.assertEqual(code, 2)
        self.assertNothingWritten()

    def test_a_resource_id_of_zero_is_refused(self):
        code, log, err = self.run_tool(ZoneServer(), '--resource', '0')
        self.assertEqual(code, 2)

    def test_a_failed_login_names_the_code(self):
        server = ZoneServer(login_error=8)
        code, log, err = self.run_tool(server)
        self.assertEqual(code, 3)
        self.assertIn('error 8', err)
        self.assertIn('the token was not accepted', err)
        self.assertNotIn('core/search_items', server.calls)
        self.assertNothingWritten()

    def test_a_refused_request_names_the_code_and_logs_out(self):
        server = ZoneServer(search_error=7)
        code, log, err = self.run_tool(server)
        self.assertEqual(code, 3)
        self.assertIn('error 7', err)
        self.assertEqual(server.calls[-1], 'core/logout')
        self.assertNothingWritten()

    def test_a_resource_missing_from_the_answer_is_a_failure(self):
        code, log, err = self.run_tool(ZoneServer(resource=999))
        self.assertEqual(code, 3)
        self.assertIn('resource 285 is not in the answer', log)
        self.assertNothingWritten()

    def test_an_empty_zone_list_is_a_failure(self):
        code, log, err = self.run_tool(ZoneServer(listed_ids=[]))
        self.assertEqual(code, 3)
        self.assertIn('NOT treating this as a resource without zones', log)
        self.assertNothingWritten()

    def test_zones_listed_but_not_downloaded_is_a_failure(self):
        code, log, err = self.run_tool(ZoneServer(data_ids=[]))
        self.assertEqual(code, 3)
        self.assertIn('none downloaded', log)
        self.assertNothingWritten()


class NoGroups(Base):
    """Пункт 4."""

    def test_an_empty_group_answer_is_suspicious_not_zero_groups(self):
        server = ZoneServer(groups={})
        code, log, err = self.run_tool(server)
        self.assertEqual(code, gz.EXIT_NO_GROUPS)
        self.assertEqual(code, 4)
        self.assertIn('no groups returned - flag may be wrong', log)
        self.assertIn('NOT treating this as a resource without groups', log)
        self.assertNothingWritten()
        # тяжёлая выгрузка зон не делается, сессия закрыта
        self.assertNotIn('resource/get_zone_data', server.calls)
        self.assertEqual(server.calls[-1], 'core/logout')

    def test_an_answer_without_the_groups_key_names_the_keys_it_had(self):
        code, log, err = self.run_tool(ZoneServer(groups=ABSENT))
        self.assertEqual(code, 4)
        self.assertIn('no groups returned - flag may be wrong', log)
        self.assertIn('without "zg"', log)
        self.assertIn('keys in the answer: id nm zl', log)
        self.assertNothingWritten()

    def test_groups_without_member_ids_are_suspicious(self):
        groups = {'11': {'id': 11, 'n': 'Гаражлар', 'd': '', 'zones': [101]}}
        code, log, err = self.run_tool(ZoneServer(groups=groups))
        self.assertEqual(code, 4)
        self.assertIn('none lists a member zone', log)
        self.assertIn('keys of a group: d id n zones', log)
        self.assertNothingWritten()

    def test_the_groups_flag_is_asked_with_the_zone_list(self):
        server = ZoneServer()
        self.run_tool(server)
        self.assertEqual(gz.ZONE_GROUPS_FLAG, 0x00100000)
        self.assertEqual(len(server.flags_asked), 1)
        self.assertTrue(server.flags_asked[0] & 0x00100000)
        self.assertTrue(server.flags_asked[0] & 0x1000)


class Files(Base):
    """Пункт 3."""

    def test_the_groups_csv(self):
        code, log, err = self.run_tool(ZoneServer())
        self.assertEqual(code, 0, log + err)
        self.assertEqual(self.read_csv(self.groups_out), [
            {'gruppa_id': '11', 'gruppa': 'Гаражлар Ҳамроев',
             'opisanie': 'Ташкилот', 'zon': '2', 'zon_v_zerkale': '1',
             'net_v_spiske_zon': '', 'zona_ids': '101 102'},
            {'gruppa_id': '12', 'gruppa': 'Автобазалар', 'opisanie': '',
             'zon': '2', 'zon_v_zerkale': '1', 'net_v_spiske_zon': '',
             'zona_ids': '102 103'}])

    def test_the_zones_csv(self):
        code, log, err = self.run_tool(ZoneServer())
        self.assertEqual(code, 0, log + err)
        rows = self.zones_by_id()
        self.assertEqual(sorted(rows), [GARAGE, DEPOT, PLANT, FIELD])
        self.assertEqual(rows[GARAGE], {
            'zona_id': '101', 'zona': 'Гараж Ҳамроев', 'tip': 'poligon',
            'tip_kod': '2', 'grupp': '1', 'gruppy': 'Гаражлар Ҳамроев',
            'gruppa_ids': '11', 'v_zerkale': 'da'})
        # зона в двух группах -- обе, в порядке id групп
        self.assertEqual(rows[DEPOT], {
            'zona_id': '102', 'zona': 'Автобаза №2', 'tip': 'poligon',
            'tip_kod': '2', 'grupp': '2',
            'gruppy': 'Гаражлар Ҳамроев | Автобазалар',
            'gruppa_ids': '11 12', 'v_zerkale': 'net'})
        self.assertEqual(rows[PLANT]['tip'], 'krug')
        self.assertEqual(rows[PLANT]['gruppa_ids'], '12')
        self.assertEqual(rows[PLANT]['v_zerkale'], 'da')
        # зона ни в одной группе; контур с тем же номером из другого
        # источника -- не зеркало Wialon
        self.assertEqual(rows[FIELD], {
            'zona_id': '104', 'zona': '1508 Нурхон Бобохон', 'tip': 'poligon',
            'tip_kod': '2', 'grupp': '0', 'gruppy': '', 'gruppa_ids': '',
            'v_zerkale': 'net'})

    def test_zones_in_no_group_come_last(self):
        extra = ZONES + [{'id': 50, 'n': 'Аввал поле', 't': 2}]
        code, log, err = self.run_tool(ZoneServer(zones=extra))
        self.assertEqual(code, 0, log + err)
        order = [int(row['zona_id']) for row in self.read_csv(self.zones_out)]
        # зона 50 по id встала бы первой, «1508 ...» -- первой по имени;
        # обе в конце, потому что ни в одной группе
        self.assertEqual(sorted(order[:3]), [GARAGE, DEPOT, PLANT])
        self.assertEqual(sorted(order[3:]), [50, FIELD])

    def test_a_member_outside_the_zone_list_is_named(self):
        groups = dict(GROUPS)
        groups['13'] = {'id': 13, 'n': 'Пунктлар', 'zns': [DEPOT, 555]}
        code, log, err = self.run_tool(ZoneServer(groups=groups))
        self.assertEqual(code, 0, log + err)
        row = [r for r in self.read_csv(self.groups_out)
               if r['gruppa_id'] == '13'][0]
        self.assertEqual(row['net_v_spiske_zon'], '555')
        self.assertEqual(row['zon'], '2')
        self.assertIn('group 13: 2 zone(s), 1 not in the zone list', log)
        self.assertIn('group members not in the zone list: 1', log)
        # 555 не стал строкой зоны: его нет в ресурсе
        self.assertNotIn(555, self.zones_by_id())
        self.assertEqual(self.zones_by_id()[DEPOT]['gruppa_ids'], '11 12 13')

    def test_a_zone_without_data_stays_in_the_list(self):
        code, log, err = self.run_tool(
            ZoneServer(data_ids=[GARAGE, DEPOT, PLANT]))
        self.assertEqual(code, 0, log + err)
        self.assertEqual(self.zones_by_id()[FIELD]['zona'], '')
        self.assertEqual(self.zones_by_id()[FIELD]['tip'], '')
        self.assertIn('zones listed but without data: 1', log)

    def test_both_csv_open_in_excel(self):
        # BOM и точка с запятой -- то же, что у GPS-12.
        self.run_tool(ZoneServer())
        for path, columns in ((self.groups_out, gz.GROUP_COLUMNS),
                              (self.zones_out, gz.ZONE_COLUMNS)):
            with open(path, 'rb') as fh:
                self.assertEqual(fh.read(3), b'\xef\xbb\xbf')
            with open(path, encoding='utf-8-sig') as fh:
                self.assertEqual(fh.readline().strip().split(';'),
                                 list(columns))


class Console(Base):
    """Пункт 5."""

    def test_the_summary_counts_groups_and_zones(self):
        code, log, err = self.run_tool(ZoneServer())
        self.assertEqual(code, 0, log + err)
        self.assertIn('groups                      : 2', log)
        self.assertIn('  group 11: 2 zone(s)\n', log)
        self.assertIn('  group 12: 2 zone(s)\n', log)
        self.assertIn('zones in no group           : 1', log)
        self.assertIn('zones in two or more groups : 1', log)
        self.assertIn('zones in our mirror         : 2 of 4', log)
        self.assertIn('mirror rows not in the list : 1', log)
        self.assertIn('type poligon      : 3', log)
        self.assertIn('type krug         : 1', log)

    def test_the_console_is_ascii_and_names_no_one(self):
        code, log, err = self.run_tool(ZoneServer())
        self.assertEqual(code, 0, log + err)
        (log + err).encode('ascii')   # падает, если в консоль попало не-ASCII
        for name in NAMES:
            spelled = config.ascii_only(name)
            # отрицательный контроль: транслит различим, иначе проверка пустая
            self.assertTrue(spelled.strip('? ') and spelled != name, name)
            self.assertNotIn(spelled, log + err)
            for word in spelled.split():
                if len(word) >= 5 and not word.isdigit():
                    self.assertNotIn(word, log + err)

    def test_the_refusal_is_ascii_too(self):
        code, log, err = self.run_tool(ZoneServer(groups=ABSENT))
        self.assertEqual(code, 4)
        (log + err).encode('ascii')
        self.assertNotIn(config.ascii_only('Бухоро агрокластер'), log)


if __name__ == '__main__':
    unittest.main()
