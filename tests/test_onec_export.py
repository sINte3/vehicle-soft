# -*- coding: utf-8 -*-
"""tools/onec_export.ps1 -- недельная выгрузка 1С для GPS-35 (GPS-37).

Держится:
  * неделя -- с понедельника 00:00:00 по воскресенье 23:59:59; без -Week --
    прошлая полная неделя от сегодняшнего дня; -Week не понедельник -- отказ;
  * два запроса из tools/onec_export/*.json; строки -- в CSV (UTF-8 с BOM,
    «;», всё в кавычках) с колонками выгрузки соседнего чата; госномер вывоза
    вырезан из конца названия транспорта, а где в названии его нет -- взят
    из реквизита справочника «ГосНомер»; время -- «ГГГГ-ММ-ДД ЧЧ:ММ:СС»;
  * GPS-34/35 читают эти CSV тем же `read_trips`, что и xlsx;
  * файлы заменяются, только когда оба запроса ответили: упал запрос или
    старый файл открыт -- не тронут ни один; коды 0/1/2/3; пароль нигде;
    консоль -- ASCII.

PowerShell-часть идёт против стенда (`tests/onec_export_harness.ps1`).

Запуск: ONEC_POWERSHELL=pwsh (или powershell) python -m unittest tests.test_onec_export
"""
import csv
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from datetime import datetime

from tools import gps_onec_trips_check as check

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXPORT = os.path.join(REPO, 'tools', 'onec_export.ps1')
COMMON = os.path.join(REPO, 'tools', 'onec_com.ps1')
HARNESS = os.path.join(REPO, 'tests', 'onec_export_harness.ps1')
SPECS = os.path.join(REPO, 'tools', 'onec_export')
POWERSHELL = os.environ.get('ONEC_POWERSHELL')

PASSWORD = 'Секрет"7'
CREDENTIALS = 'login=Админ\r\npassword=%s\r\n' % PASSWORD
CONNECTION = 'Srvr="srv-1c";Ref="uzpaxta";Usr="Админ";Pwd="Секрет""7";'
VYVOZ = 'onec_vyvoz_po_brutto_nedelya.csv'
REESTR = 'onec_pk17_reestr_nedelya.csv'


def spec(name):
    with open(os.path.join(SPECS, name + '.json'), encoding='utf-8') as handle:
        return json.load(handle)


class Specs(unittest.TestCase):
    """Запросы повторяют схемы отчётов, прочитанные разведкой 29.10."""

    def test_the_scripts_are_ascii(self):
        for path in (EXPORT, COMMON, HARNESS):
            with open(path, 'rb') as handle:
                handle.read().decode('ascii')

    def test_removal_query_is_the_report_schema(self):
        vyvoz = spec('vyvoz')
        query = vyvoz['query']
        self.assertIn('Документ.ВывозХлопкаСырцаИзПунктовПриемаНаЗавод_Шлюз', query)
        self.assertIn('НЕ Вывоз.ПометкаУдаления', query)
        self.assertIn('Вывоз.ВремяВторогоВзвешивания МЕЖДУ &Since И &Until', query)
        # госномер -- реквизит справочника транспорта (разведка v3, 29.10)
        self.assertIn('Вывоз.Транспорт.ГосНомер КАК Plate', query)
        plate = [c for c in vyvoz['columns'] if c['header'] == 'Гос номер (из названия)']
        self.assertEqual(plate, [{'plate_from': 'TransportRef', 'plate_attribute': 'Plate',
                                  'header': 'Гос номер (из названия)'}])
        self.assertEqual(vyvoz['file'], VYVOZ)
        self.assertEqual(vyvoz['sum'], 'Net')
        headers = [column['header'] for column in vyvoz['columns']]
        # то, что читает GPS-34/35, -- среди колонок
        for name in check.COLUMNS[check.VYVOZ] + ('Вес нетто, кг',):
            self.assertIn(name, headers)

    def test_register_query_is_the_report_schema(self):
        reestr = spec('reestr')
        query = reestr['query']
        self.assertIn('Документ.ПоступлениеХлопкаСырца', query)
        self.assertIn('Поступление.Проведен', query)
        self.assertIn('Поступление.ДатаПроводки МЕЖДУ &Since И &Until', query)
        self.assertEqual(reestr['file'], REESTR)
        self.assertEqual(reestr['sum'], 'Mass')
        headers = [column['header'] for column in reestr['columns']]
        for name in check.COLUMNS[check.REESTR] + ('Масса физическая,кг',):
            self.assertIn(name, headers)

    def test_no_person_is_selected(self):
        # ни фермер, ни товаровед не выбираются
        for name in ('vyvoz', 'reestr'):
            query = spec(name)['query']
            for field in ('Контрагент', 'Товаровед', 'Ответственный'):
                self.assertNotIn(field, query)


@unittest.skipUnless(POWERSHELL, 'ONEC_POWERSHELL is not set')
class ExportAgainstStandIn(unittest.TestCase):

    def setUp(self):
        self.folder = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.folder, True)
        self.out = os.path.join(self.folder, 'out')
        os.mkdir(self.out)
        with open(os.path.join(self.folder, 'credentials.txt'), 'w', encoding='utf-8-sig',
                  newline='') as handle:
            handle.write(CREDENTIALS)
        with open(os.path.join(self.folder, 'expected.txt'), 'w', encoding='utf-8') as handle:
            handle.write(CONNECTION)

    def run_case(self, scenario, week='2026-10-19', today=''):
        args = [POWERSHELL, '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', HARNESS,
                '-Export', EXPORT, '-Scenario', scenario, '-Folder', self.folder]
        if week:
            args += ['-WeekArg', week]
        if today:
            args += ['-TodayArg', today]
        done = subprocess.run(args, capture_output=True, timeout=120)
        console = (done.stdout + done.stderr).decode('utf-8', 'replace')
        self.assertNotIn('Секрет', console)
        self.assertTrue(console.isascii(), console)
        return done.returncode, console

    def rows(self, name):
        with open(os.path.join(self.out, name), encoding='utf-8-sig', newline='') as handle:
            return list(csv.reader(handle, delimiter=';'))

    def test_the_week_goes_to_two_files(self):
        code, console = self.run_case('ok')
        self.assertEqual(code, 0, console)
        self.assertIn('onec_export v3 | week 2026-10-19 .. 2026-10-25', console)
        self.assertIn('vyvoz: 5 rows | sum 17940.5 | ', console)
        self.assertIn('vyvoz plates: from the name 2 | from the GosNomer attribute 2 | none 1',
                      console)
        self.assertIn('reestr: 2 rows | sum 7240 | ', console)
        self.assertNotIn('reestr plates', console)
        self.assertIn('READ ONLY: nothing was written to 1C', console)
        vyvoz = self.rows(VYVOZ)
        self.assertEqual(vyvoz[0], ['Завод', 'Пункт приема', 'Транспорт (как в 1С)',
                                    'Гос номер (из названия)', 'Время первого взвешивания',
                                    'Время второго взвешивания', 'Вес нетто, кг',
                                    'Транспорт (представление)',
                                    'Гос номер (реквизит справочника)'])
        # понедельник 00:00:00 и воскресенье 23:59:59 входят; 18.10 23:59:59 и
        # 26.10 00:00:00 -- нет. Название транспорта -- String() ссылки (с
        # номером), номер -- из него; ПРЕДСТАВЛЕНИЕ() -- только модель. Номер
        # в названии сильнее реквизита (вторая строка); без номера в названии
        # -- реквизит: по образцу номера или как есть; пустой -- пусто
        self.assertEqual(vyvoz[1:], [
            ['OOO "BUXORO AGROKLASTER"', 'Вобкент ПТМ', 'МТЗ-80-1 80 283 HA', '80 283 HA',
             '2026-10-19 07:28:06', '2026-10-19 08:46:56', '6620', 'МТЗ-80-1', '80 283 HA'],
            ['OOO "BUXORO AGROKLASTER"', 'Харгуш ПТМ', 'МТЗ-80-1(калхида) 80 240 HA', '80 240 HA',
             '2026-10-25 22:10:00', '2026-10-25 23:59:59', '5320.5', 'МТЗ-80-1(калхида)',
             '80999HA'],
            ['OOO "BUXORO AGROKLASTER"', 'Вобкент ПТМ', 'Камаз', '',
             '', '2026-10-19 00:00:00', '1000', 'Камаз', '  '],
            ['OOO "BUXORO AGROKLASTER"', 'Харгуш ПТМ', 'Трактор', '80 123 НА',
             '2026-10-21 09:00:00', '2026-10-21 10:15:00', '2000', 'Трактор', '80123НА'],
            ['OOO "BUXORO AGROKLASTER"', 'Вобкент ПТМ', 'ГАЗ-53', '80 A 123 BC',
             '2026-10-22 11:00:00', '2026-10-22 12:30:00', '3000', 'ГАЗ-53', ' 80  A 123 BC ']])
        reestr = self.rows(REESTR)
        self.assertEqual(reestr[0], ['Организация (завод)', 'Пункт приема', 'Транспорт',
                                     'Дата брутто', 'Масса физическая,кг', 'Дата документа',
                                     'Урожай'])
        # дата проводки 26.10 00:00:01 -- за неделей, хотя брутто 25.10
        self.assertEqual(reestr[1:], [
            ['OOO "BUXORO AGROKLASTER"', 'Кумушкент ПТМ', '80 KA 368', '2026-10-20 19:16:47', '4840',
             '2026-10-20 19:20:00', '2026'],
            ['Мирзачул Агрокластер гроуп МЧЖ', 'Х.Нарзи ПТМ', '80 HA 254', '2026-10-25 20:26:10',
             '2400', '2026-10-28 09:00:00', '2026']])
        self.assertEqual(sorted(os.listdir(self.out)), [REESTR, VYVOZ])

    def test_gps_35_reads_the_csv_files(self):
        code, console = self.run_case('ok')
        self.assertEqual(code, 0, console)
        trips = check.read_trips(os.path.join(self.out, VYVOZ), check.VYVOZ,
                                 extra=('Вес нетто, кг',))
        self.assertEqual([(t['point'], t['plate'], t['time']) for t in trips], [
            ('Вобкент ПТМ', '80283HA', datetime(2026, 10, 19, 7, 28, 6)),
            ('Харгуш ПТМ', '80240HA', datetime(2026, 10, 25, 22, 10)),
            ('Вобкент ПТМ', '', None),
            ('Харгуш ПТМ', '80123HA', datetime(2026, 10, 21, 9, 0)),
            ('Вобкент ПТМ', '80A123BC', datetime(2026, 10, 22, 11, 0))])
        self.assertEqual(trips[1]['extra'], {'Вес нетто, кг': '5320.5'})
        trips = check.read_trips(os.path.join(self.out, REESTR), check.REESTR)
        # серия реестра переносится в конец, как у xlsx
        self.assertEqual([t['plate'] for t in trips], ['80368KA', '80254HA'])

    def test_the_last_full_week_by_default(self):
        for today, week in (('2026-10-29', '2026-10-19 .. 2026-10-25'),
                            ('2026-10-26', '2026-10-19 .. 2026-10-25'),
                            ('2026-10-25', '2026-10-12 .. 2026-10-18')):
            with self.subTest(today=today):
                code, console = self.run_case('ok', week='', today=today)
                self.assertEqual(code, 0, console)
                self.assertIn('| week %s' % week, console)

    def test_the_russian_query_type_is_the_fallback(self):
        code, console = self.run_case('russian-type')
        self.assertEqual(code, 0, console)
        self.assertIn('vyvoz: 5 rows', console)

    def test_bad_input_is_code_two(self):
        for week, message in (('2026-10-20', '-Week is not a Monday'),
                              ('20.10.2026', '-Week is YYYY-MM-DD')):
            with self.subTest(week=week):
                code, console = self.run_case('ok', week=week)
                self.assertEqual(code, 2, console)
                self.assertIn('ERROR: ' + message, console)
                self.assertEqual(os.listdir(self.out), [])
        code, console = self.run_case('no-outdir')
        self.assertEqual(code, 2, console)
        self.assertIn('ERROR: no folder -OutDir', console)

    def test_a_refused_connection_is_code_three(self):
        code, console = self.run_case('refused')
        self.assertEqual(code, 3, console)
        self.assertIn('ERROR: 1C refused the connection', console)
        self.assertIn('Pwd="***', console)
        self.assertEqual(os.listdir(self.out), [])

    def old_files(self):
        for name in (VYVOZ, REESTR):
            with open(os.path.join(self.out, name), 'w', encoding='utf-8') as handle:
                handle.write('old week')

    def assertOldFiles(self):
        self.assertEqual(sorted(os.listdir(self.out)), [REESTR, VYVOZ])
        for name in (VYVOZ, REESTR):
            with open(os.path.join(self.out, name), encoding='utf-8') as handle:
                self.assertEqual(handle.read(), 'old week')

    def test_a_failed_query_leaves_both_old_files(self):
        # вывоз ответил, реестр упал: не заменён ни один файл
        self.old_files()
        code, console = self.run_case('reestr-fails')
        self.assertEqual(code, 1, console)
        self.assertIn('ERROR: the reestr query failed, nothing was written', console)
        self.assertIn('***', console)
        self.assertOldFiles()

    def test_an_open_file_leaves_both_old_files(self):
        self.old_files()
        code, console = self.run_case('locked')
        self.assertEqual(code, 1, console)
        self.assertIn('is open in another program, nothing was written', console)
        self.assertOldFiles()

    def test_the_real_entry_point_reaches_the_connector(self):
        # без V83.COMConnector (CI) -- код 3, ничего не записано
        done = subprocess.run(
            [POWERSHELL, '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', EXPORT,
             '-Server', 'srv-1c', '-Base', 'uzpaxta', '-CredentialsFile',
             os.path.join(self.folder, 'credentials.txt'), '-OutDir', self.out,
             '-Week', '2026-10-19'], capture_output=True, timeout=120)
        console = (done.stdout + done.stderr).decode('utf-8', 'replace')
        self.assertEqual(done.returncode, 3, console)
        self.assertNotIn('Секрет', console)
        self.assertEqual(os.listdir(self.out), [])


if __name__ == '__main__':
    unittest.main()
