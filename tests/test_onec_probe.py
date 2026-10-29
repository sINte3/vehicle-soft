# -*- coding: utf-8 -*-
"""tools/onec_probe.ps1 -- разведка базы 1С перед недельной выгрузкой (GPS-36).

Держится:
  * описываются отчёты, чей синоним похож на реестр ПК-17 и вывоз по дате
    брутто (или названные в -Report): схема компоновки -- параметры, наборы
    данных с текстом запроса, поля, вычисляемые поля и итоги, варианты; у
    старого отчёта -- реквизиты и макеты; прочие отчёты только в списке;
  * подсистемы -- деревом, состав -- только у «заготовки хлопка»; документы и
    регистры -- списком; в конце -- какие члены видит PowerShell;
  * член 1С спрашивается по-английски, затем по-русски; коллекция -- перебором
    PowerShell, иначе Count()/Get(); часть, которая упала, -- строка «!» с
    именем члена, остальное пишется (код 4);
  * пароль не попадает ни в файл, ни в консоль, в том числе из текста ошибки
    1С; в консоль -- только ASCII;
  * коды: 0 -- файл записан целиком, 4 -- записан, часть упала, 2 -- неверный
    ввод (ничего не записано), 3 -- 1С отказала в соединении;
  * файл учётных данных: login=/password=, UTF-8 с BOM или cp1251,
    незнакомый ключ -- отказ с номером строки, без значения.

PowerShell-часть идёт против стенда (`tests/onec_probe_harness.ps1`): объекты
PowerShell с именами членов COM-объектов 1С. В 1С ничего не ходит.

Запуск: ONEC_POWERSHELL=pwsh (или powershell) python -m unittest tests.test_onec_probe
"""
import os
import re
import shutil
import subprocess
import tempfile
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROBE = os.path.join(REPO, 'tools', 'onec_probe.ps1')
HARNESS = os.path.join(REPO, 'tests', 'onec_probe_harness.ps1')
POWERSHELL = os.environ.get('ONEC_POWERSHELL')

LOGIN = 'Админ'
PASSWORD = 'Секрет"7'
CREDENTIALS = 'login=%s\r\npassword=%s\r\n' % (LOGIN, PASSWORD)
CONNECTION = 'Srvr="srv-1c";Ref="uzpaxta";Usr="Админ";Pwd="Секрет""7";'


def pattern(name):
    with open(PROBE, encoding='ascii') as handle:
        text = handle.read()
    found = re.search(r"^\$%s = '([^']*)'" % name, text, re.M)
    return re.sub(r'\\u([0-9a-f]{4})', lambda m: chr(int(m.group(1), 16)), found.group(1))


class Static(unittest.TestCase):

    def test_both_scripts_are_ascii(self):
        # Windows PowerShell 5.1 reads a file without a BOM as ANSI.
        for path in (PROBE, HARNESS):
            with open(path, 'rb') as handle:
                handle.read().decode('ascii')

    def test_search_words_decode_to_the_report_names(self):
        words = pattern('ReportPattern')
        for word in ('реестр', 'вывоз', 'взвеш', 'брутто', 'жадвал', 'пк-?17'):
            self.assertIn(word, words)
        self.assertTrue(re.search(words, 'Вывоз по дате взвешивания брутто'))
        self.assertTrue(re.search(words, 'реестр ПК-17 (жадвал 7)'.lower()))
        self.assertIsNone(re.search(words, 'остатки на складах'))
        subsystems = pattern('SubsystemPattern')
        for word in ('хлоп', 'заготов', 'пахта'):
            self.assertIn(word, subsystems)


@unittest.skipUnless(POWERSHELL, 'ONEC_POWERSHELL is not set')
class ProbeAgainstStandIn(unittest.TestCase):

    def setUp(self):
        self.folder = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.folder, True)
        self.out = os.path.join(self.folder, 'probe.txt')

    def run_case(self, scenario, credentials=None, raw=None):
        path = os.path.join(self.folder, 'credentials.txt')
        if raw is not None:
            with open(path, 'wb') as handle:
                handle.write(raw)
        elif credentials is not False:
            with open(path, 'w', encoding='utf-8-sig', newline='') as handle:
                handle.write(CREDENTIALS if credentials is None else credentials)
        with open(os.path.join(self.folder, 'expected.txt'), 'w', encoding='utf-8') as handle:
            handle.write(CONNECTION)
        done = subprocess.run(
            [POWERSHELL, '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', HARNESS,
             '-Probe', PROBE, '-Scenario', scenario, '-Folder', self.folder],
            capture_output=True, timeout=120)
        console = (done.stdout + done.stderr).decode('utf-8', 'replace')
        text = None
        if os.path.exists(self.out):
            with open(self.out, encoding='utf-8-sig') as handle:
                text = handle.read()
        return done.returncode, console, text

    def assertClean(self, console, text):
        """Пароль нигде; консоль -- ASCII."""
        self.assertNotIn('Секрет', console)
        self.assertTrue(console.isascii(), console)
        if text is not None:
            self.assertNotIn('Секрет', text)

    def test_describes_the_cotton_reports(self):
        code, console, text = self.run_case('ok')
        self.assertEqual(code, 0, console)
        self.assertIn('connected; reports 4; described 3; failed parts 0', console)
        self.assertIn('READ ONLY: nothing was written to 1C', console)
        self.assertClean(console, text)
        lines = text.splitlines()
        self.assertEqual(lines[:7], [
            'onec_probe v3',
            'configuration: UzPaxta | Узпахта | version 1.0.5',
            '=== reports: 4',
            '* РеестрПК17 | Реестр ПК-17 (жадвал 7) | schema yes',
            '* ВывозПоДатеБрутто | Вывоз по дате взвешивания брутто | schema no',
            '  Остатки | Остатки на складах | schema yes',
            '* ЖурналВзвешиваний | Журнал взвешиваний | schema yes'])
        start = lines.index('=== report РеестрПК17 | Реестр ПК-17 (жадвал 7)')
        self.assertEqual(lines[start + 1:start + 29], [
            '  main schema: ОсновнаяСхемаКомпоновкиДанных',
            '  parameters: 1',
            '    P НачалоПериода | Начало периода | type S<date-type> | value S<standard-period>',
            '  data sets: 2',
            '    D НаборДанных1 | query',
            '      F Масса | Масса | Масса физическая,кг',
            '      F ВывозТранспорт | ВывозТранспорт | Транспорт | presentation: '
            'ВывозТранспорт.Наименование + " " + ВывозТранспорт.ГосНомер',
            '      query:',
            '    | ВЫБРАТЬ',
            '    |     Взвешивание.МассаФизическая КАК Масса',
            '    | ИЗ',
            '    |     Документ.Взвешивание КАК Взвешивание',
            '    D Объединение | union',
            '      D Вложенный | query',
            '        query:',
            '    | ВЫБРАТЬ 1',
            '  CalculatedFields: 1',
            '    C Тонны | Тонны | Масса / 1000',
            '  TotalFields: 1',
            '    C Масса |  | Сумма(Масса)',
            '  settings variants: 1',
            '    V Основной | Основной',
            '=== report ВывозПоДатеБрутто | Вывоз по дате взвешивания брутто',
            '  A Период | Период | S<date-type>',
            '  T Макет',
            '  main schema: none (an old-style report: its logic is in the module)',
            '=== report ЖурналВзвешиваний | Журнал взвешиваний',
            '  main schema: ОсновнаяСхемаКомпоновкиДанных'])
        self.assertTrue(lines[start + 29].startswith('  schema not read: '))
        self.assertIn('template is not available', lines[start + 29])
        # оба документа выгрузки -- с реквизитами; транспорт -- до справочника
        at = lines.index('=== document ВывозХлопкаСырцаИзПунктовПриемаНаЗавод_Шлюз '
                         '| Вывоз хлопка сырца на завод')
        self.assertEqual(lines[at + 1:at + 7], [
            '  A Организация | Организация | S<org-type>',
            '  A Транспорт | Транспорт | S<transport-ref>',
            '    -> Справочник.ТранспортХлопка | Транспорт хлопка | presentation S<as-description> '
            '| code length 9 | description length 50',
            '      CA ГосНомер | Гос номер | S<string-type>',
            '=== document ПоступлениеХлопкаСырца | Поступление хлопка сырца',
            '  A ТранспортноеСредство | Транспортное средство | S<string-ref>'])
        self.assertEqual(lines[at + 7], '=== subsystems')
        # отчёт вне шаблона только в списке
        self.assertNotIn('=== report Остатки | Остатки на складах', lines)
        tail = lines[lines.index('=== subsystems'):]
        # документы -- коллекция без перебора: прочитаны через Count()/Get()
        self.assertEqual(tail[:13], [
            '=== subsystems',
            '  S ЗаготовкаХлопкаСырца | Заготовка хлопка сырца | objects 2',
            '      - Отчет.РеестрПК17',
            '      - Документ.Взвешивание',
            '    S Настройки | Настройки | objects 1',
            '  S Бухгалтерия | Бухгалтерия | objects 1',
            '=== documents: 3',
            '  Взвешивание | Взвешивание хлопка',
            '  ВывозХлопкаСырцаИзПунктовПриемаНаЗавод_Шлюз | Вывоз хлопка сырца на завод',
            '  ПоступлениеХлопкаСырца | Поступление хлопка сырца',
            '=== accumulation registers: 1',
            '  ОстаткиХлопка | Остатки хлопка',
            '=== information registers: 0'])
        self.assertEqual(tail[13], '=== members seen by PowerShell')
        self.assertTrue(tail[14].startswith('  connection: '), tail[14])
        self.assertIn('Metadata', tail[14])
        self.assertTrue(tail[15].startswith('  metadata: '), tail[15])
        self.assertEqual(len(tail), 16)

    def test_named_reports_replace_the_search(self):
        # -Report через -File приходит одной строкой «A,B»
        code, console, text = self.run_case('named')
        self.assertEqual(code, 0, console)
        self.assertIn('described 2', console)
        lines = text.splitlines()
        self.assertIn('  РеестрПК17 | Реестр ПК-17 (жадвал 7) | schema yes', lines)
        self.assertIn('* Остатки | Остатки на складах | schema yes', lines)
        self.assertIn('* ВывозПоДатеБрутто | Вывоз по дате взвешивания брутто | schema no', lines)
        self.assertIn('=== report Остатки | Остатки на складах', lines)
        self.assertNotIn('=== report РеестрПК17 | Реестр ПК-17 (жадвал 7)', lines)

    def test_a_refused_connection_is_code_three_and_masked(self):
        code, console, text = self.run_case('refused')
        self.assertEqual(code, 3, console)
        self.assertIn('ERROR: 1C refused the connection', console)
        self.assertClean(console, text)
        self.assertIn('connection refused by 1C:', text)
        self.assertIn('Идентификация пользователя не выполнена', text)
        self.assertIn('Pwd="***', text)

    def test_a_failing_part_is_named_masked_and_the_rest_is_read(self):
        # Count() коллекции отчётов падает с текстом, где есть пароль
        code, console, text = self.run_case('broken')
        self.assertEqual(code, 4, console)
        self.assertClean(console, text)
        lines = text.splitlines()
        failed = [line for line in lines if line.startswith('  ! reports failed: ')]
        self.assertEqual(len(failed), 1, text)
        self.assertIn('***', failed[0])
        self.assertIn('=== members seen by PowerShell', lines)

    def test_russian_member_names_are_the_fallback(self):
        code, console, text = self.run_case('russian')
        self.assertEqual(code, 0, console)
        self.assertIn('failed parts 0', console)
        lines = text.splitlines()
        self.assertEqual(lines[1], 'configuration: UzPaxta | Узпахта | version 1.0.5')
        self.assertIn('* РеестрПК17 | Реестр ПК-17 (жадвал 7) | schema yes', lines)
        connection = [line for line in lines if line.startswith('  connection: ')]
        self.assertIn('Метаданные', connection[0])

    def test_one_missing_part_does_not_stop_the_others(self):
        code, console, text = self.run_case('partial')
        self.assertEqual(code, 4, console)
        self.assertIn('failed parts 2', console)
        lines = text.splitlines()
        self.assertIn('=== document ПоступлениеХлопкаСырца', lines)
        self.assertIn('  ! document ПоступлениеХлопкаСырца failed: 1C has no document '
                      'ПоступлениеХлопкаСырца', lines)
        at = lines.index('=== subsystems')
        self.assertEqual(lines[at + 1],
                         '  ! subsystems failed: 1C does not know the member Subsystems / Подсистемы')
        self.assertEqual(lines[at + 2:at + 4], ['=== documents: 2', '  Взвешивание | Взвешивание хлопка'])
        self.assertIn('=== report РеестрПК17 | Реестр ПК-17 (жадвал 7)', lines)

    def test_bad_input_is_code_two_and_nothing_is_written(self):
        cases = (
            ('no file', False, 'credentials file not found'),
            ('typo', 'login=a\npasword=b\n', 'line 2: unknown key'),
            ('no password', 'login=a\n', 'credentials file has no password='),
            ('empty password', 'login=a\npassword=\n', 'credentials file has no password='),
            ('no equals', 'login\npassword=b\n', "line 1 has no '='"),
        )
        for label, credentials, message in cases:
            with self.subTest(label):
                code, console, text = self.run_case('ok', credentials=credentials)
                self.assertEqual(code, 2, console)
                self.assertIn('ERROR: ' + message, console)
                self.assertIsNone(text)
                self.assertTrue(console.isascii(), console)
        code, console, text = self.run_case('no-folder')
        self.assertEqual(code, 2, console)
        self.assertIn('ERROR: no folder for -Out', console)

    def test_comments_and_cp1251_credentials(self):
        # Блокнот старой Windows пишет ANSI: кириллический пароль не искажён
        raw = ('# 1C\r\n\r\nlogin=%s\r\npassword=%s\r\n' % (LOGIN, PASSWORD)).encode('cp1251')
        code, console, text = self.run_case('ok', raw=raw)
        self.assertEqual(code, 0, console)
        self.assertClean(console, text)

    def test_the_real_entry_point_reaches_the_connector(self):
        # Без зарегистрированного V83.COMConnector (CI) соединения нет: код 3,
        # текст ошибки -- в файле, пароль скрыт. Доказывает, что параметры
        # доходят до Invoke-Main, а его код -- до exit.
        path = os.path.join(self.folder, 'credentials.txt')
        with open(path, 'w', encoding='utf-8-sig', newline='') as handle:
            handle.write(CREDENTIALS)
        done = subprocess.run(
            [POWERSHELL, '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', PROBE,
             '-Server', 'srv-1c', '-Base', 'uzpaxta', '-CredentialsFile', path,
             '-Out', self.out], capture_output=True, timeout=120)
        console = (done.stdout + done.stderr).decode('utf-8', 'replace')
        self.assertEqual(done.returncode, 3, console)
        with open(self.out, encoding='utf-8-sig') as handle:
            text = handle.read()
        self.assertClean(console, text)
        self.assertIn('connection refused by 1C:', text)


if __name__ == '__main__':
    unittest.main()
