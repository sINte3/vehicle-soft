# -*- coding: utf-8 -*-
"""tools/onec_weekly.ps1 -- недельное задание Планировщика: выгрузка 1С и книга GPS-35 (GPS-38).

Держится:
  * неделя -- прошлая полная пн--вс от сегодняшнего дня или -Week; выгрузка
    получает её явно и пишет в <Out>\\vygruzka\\<понедельник>;
  * книга GPS-35 строится из двух файлов этой папки, базы и --out-dir <Out>;
  * упала выгрузка -- книги нет, код 10 + код выгрузки; упала книга -- код
    20 + её код; шаг не запустился -- 19 / 29; неверный ввод -- 2, ничего
    не запущено;
  * всё, что печатают шаги, -- в <Out>\\perevozki_1c.log со временем;
    консоль -- ASCII, пароль нигде;
  * по умолчанию шаги -- настоящие tools/onec_export.ps1 и
    tools/gps_onec_hauling_report.py рядом со скриптом.

Шаги подменяются скриптами-заглушками; настоящие проверяются там, где они
отвечают без 1С и без таблиц agro-work.

Запуск: ONEC_POWERSHELL=pwsh (или powershell) python -m unittest tests.test_onec_weekly
"""
import csv
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEEKLY = os.path.join(REPO, 'tools', 'onec_weekly.ps1')
TASK = os.path.join(REPO, 'tools', 'onec_weekly_task.ps1')
SPECS = os.path.join(REPO, 'tools', 'onec_export')
POWERSHELL = os.environ.get('ONEC_POWERSHELL')

PASSWORD = 'Секрет"7'
VYVOZ = 'onec_vyvoz_po_brutto_nedelya.csv'
REESTR = 'onec_pk17_reestr_nedelya.csv'

FAKE_EXPORT = r'''param([string]$Server, [string]$Base, [string]$CredentialsFile, [string]$OutDir, [string]$Week)
[System.IO.File]::WriteAllLines((Join-Path $env:FAKE_DIR 'export_args.txt'), [string[]]@($Server, $Base, $CredentialsFile, $OutDir, $Week))
Write-Host ('fake export | week ' + $Week)
$code = [int]$env:FAKE_EXPORT_CODE
if ($code -eq 0) {
  foreach ($name in @('onec_vyvoz_po_brutto_nedelya.csv', 'onec_pk17_reestr_nedelya.csv')) {
    Copy-Item -LiteralPath (Join-Path $env:FAKE_DIR $name) -Destination (Join-Path $OutDir $name)
  }
}
exit $code
'''

FAKE_REPORT = r'''import json, os, sys
with open(os.path.join(os.environ['FAKE_DIR'], 'report_args.json'), 'w') as handle:
    json.dump(sys.argv, handle)
print('fake book | %d arguments' % (len(sys.argv) - 1))
code = int(os.environ.get('FAKE_REPORT_CODE', '0'))
if code:
    sys.stderr.write('ERROR: fake book failed\n')
else:
    out = sys.argv[sys.argv.index('--out-dir') + 1]
    open(os.path.join(out, 'perevozki_1c_fake.xlsx'), 'w').close()
sys.exit(code)
'''


def headers(name):
    with open(os.path.join(SPECS, name + '.json'), encoding='utf-8') as handle:
        return [column['header'] for column in json.load(handle)['columns']]


def write_csv(path, rows):
    with open(path, 'w', encoding='utf-8-sig', newline='') as handle:
        csv.writer(handle, delimiter=';', quoting=csv.QUOTE_ALL).writerows(rows)


class WeeklyScript(unittest.TestCase):

    def test_the_scripts_are_ascii(self):
        # Windows PowerShell 5.1 читает файл без BOM как ANSI
        for path in (WEEKLY, TASK):
            with open(path, 'rb') as handle:
                handle.read().decode('ascii')


def run_task(*args):
    done = subprocess.run([POWERSHELL, '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', TASK]
                          + list(args), capture_output=True, timeout=300)
    console = (done.stdout + done.stderr).decode('utf-8', 'replace')
    return done.returncode, console


@unittest.skipUnless(POWERSHELL and sys.platform == 'win32', 'the Task Scheduler is on Windows')
class TaskRegistration(unittest.TestCase):
    """Настоящий Планировщик: задание ставится таким, как описано, и снимается."""

    def setUp(self):
        self.name = 'VehicleSoft test %d' % os.getpid()
        self.addCleanup(run_task, '-TaskName', self.name, '-Remove')

    def read_task(self):
        query = ("$t = Get-ScheduledTask -TaskName '%s' -ErrorAction Stop; "
                 "@($t.Actions[0].Execute, $t.Actions[0].Arguments, [string]$t.Triggers[0].DaysOfWeek, "
                 "$t.Triggers[0].StartBoundary, [string]$t.Principal.LogonType, "
                 "[string]$t.Principal.RunLevel, [string]$t.Settings.StartWhenAvailable, "
                 "[string]$t.Settings.MultipleInstances, $t.Settings.ExecutionTimeLimit) -join '|'"
                 % self.name)
        done = subprocess.run([POWERSHELL, '-NoProfile', '-Command', query],
                              capture_output=True, timeout=300)
        return done.returncode, done.stdout.decode('utf-8', 'replace').strip()

    def test_the_task_is_registered_as_described_and_removed(self):
        script = r'C:\gps weekly\tools\onec_weekly.ps1'
        code, console = run_task('-TaskName', self.name, '-Script', script)
        self.assertEqual(code, 0, console)
        self.assertTrue(console.isascii(), console)
        self.assertIn('registered: %s | Monday 06:40 | runs as ' % self.name, console)
        code, fields = self.read_task()
        self.assertEqual(code, 0, fields)
        (execute, arguments, days, start, logon, level, missed, instances,
         limit) = fields.split('|')
        self.assertEqual(execute, 'powershell.exe')
        self.assertEqual(arguments, '-NoProfile -ExecutionPolicy Bypass -File "%s"' % script)
        self.assertEqual(days, '2')                       # понедельник
        self.assertIn('T06:40:00', start)
        self.assertIn(logon, ('S4U', '2'))                # без пароля Windows
        self.assertIn(level, ('Highest', '1'))
        self.assertEqual(missed, 'True')
        self.assertIn(instances, ('IgnoreNew', '2'))
        self.assertEqual(limit, 'PT2H')
        code, console = run_task('-TaskName', self.name, '-Remove')
        self.assertEqual(code, 0, console)
        self.assertIn('removed: ' + self.name, console)
        self.assertNotEqual(self.read_task()[0], 0)


@unittest.skipUnless(POWERSHELL and sys.platform != 'win32', 'here the Task Scheduler is missing')
class TaskRegistrationRefused(unittest.TestCase):

    def test_without_the_task_scheduler_it_says_so(self):
        code, console = run_task('-TaskName', 'VehicleSoft test')
        self.assertEqual(code, 1, console)
        self.assertIn('ERROR: the Task Scheduler refused', console)
        self.assertTrue(console.isascii(), console)


@unittest.skipUnless(POWERSHELL, 'ONEC_POWERSHELL is not set')
class WeeklyTask(unittest.TestCase):

    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root, True)
        self.fake = os.path.join(self.root, 'fake')
        self.out = os.path.join(self.root, 'perevozki_1c')
        os.mkdir(self.fake)
        self.export = os.path.join(self.fake, 'export.ps1')
        with open(self.export, 'w', encoding='ascii', newline='\n') as handle:
            handle.write(FAKE_EXPORT)
        self.report = os.path.join(self.fake, 'report.py')
        with open(self.report, 'w', encoding='ascii', newline='\n') as handle:
            handle.write(FAKE_REPORT)
        self.db = os.path.join(self.root, 'transport.db')
        open(self.db, 'wb').close()
        self.credentials = os.path.join(self.root, 'credentials.txt')
        with open(self.credentials, 'w', encoding='utf-8-sig', newline='') as handle:
            handle.write('login=Админ\r\npassword=%s\r\n' % PASSWORD)
        # строки, которые GPS-35 читает как датированные рейсы
        vyvoz = dict.fromkeys(headers('vyvoz'), '')
        vyvoz.update({'Завод': 'OOO "BUXORO AGROKLASTER"', 'Пункт приема': 'Вобкент ПТМ',
                      'Гос номер (из названия)': '80 283 HA',
                      'Время первого взвешивания': '2026-10-27 07:28:06', 'Вес нетто, кг': '6620'})
        reestr = dict.fromkeys(headers('reestr'), '')
        reestr.update({'Организация (завод)': 'OOO "BUXORO AGROKLASTER"',
                       'Пункт приема': 'Кумушкент ПТМ', 'Транспорт': '80 KA 368',
                       'Дата брутто': '2026-10-27 19:16:47', 'Масса физическая,кг': '4840'})
        write_csv(os.path.join(self.fake, VYVOZ), [list(vyvoz), list(vyvoz.values())])
        write_csv(os.path.join(self.fake, REESTR), [list(reestr), list(reestr.values())])

    def run_weekly(self, today='2026-11-04', week='', export_code=0, report_code=0, fakes=True,
                   **overrides):
        args = {'-Out': self.out, '-CredentialsFile': self.credentials, '-Db': self.db,
                '-Python': sys.executable, '-PowerShell': POWERSHELL, '-Today': today}
        if fakes:
            args.update({'-ExportScript': self.export, '-ReportScript': self.report})
        if week:
            args['-Week'] = week
        args.update(overrides)
        argv = [POWERSHELL, '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', WEEKLY]
        for key, value in args.items():
            argv += [key, value]
        env = dict(os.environ, FAKE_DIR=self.fake, FAKE_EXPORT_CODE=str(export_code),
                   FAKE_REPORT_CODE=str(report_code))
        done = subprocess.run(argv, capture_output=True, timeout=300, env=env)
        console = (done.stdout + done.stderr).decode('utf-8', 'replace')
        self.assertTrue(console.isascii(), console)
        self.assertNotIn('Секрет', console)
        return done.returncode, console

    def log(self):
        with open(os.path.join(self.out, 'perevozki_1c.log'), encoding='utf-8') as handle:
            text = handle.read()
        self.assertTrue(text.isascii(), text)
        return text

    def recorded(self, name):
        path = os.path.join(self.fake, name)
        if not os.path.exists(path):
            return None
        with open(path, encoding='utf-8') as handle:
            return json.load(handle) if name.endswith('.json') else handle.read().splitlines()

    def test_a_week_goes_from_the_export_to_the_book(self):
        code, console = self.run_weekly()
        self.assertEqual(code, 0, console)
        folder = os.path.join(self.out, 'vygruzka', '2026-10-26')
        self.assertEqual(self.recorded('export_args.txt'),
                         ['srv-1c', 'uzpaxta', self.credentials, folder, '2026-10-26'])
        self.assertEqual(self.recorded('report_args.json'), [
            self.report, '--db', self.db, '--vyvoz', os.path.join(folder, VYVOZ),
            '--reestr', os.path.join(folder, REESTR), '--out-dir', self.out])
        self.assertEqual(sorted(os.listdir(folder)), [REESTR, VYVOZ])
        self.assertTrue(os.path.exists(os.path.join(self.out, 'perevozki_1c_fake.xlsx')))
        log = self.log()
        for text in ('onec_weekly v1 | week 2026-10-26 .. 2026-11-01 | ' + folder,
                     'export | fake export | week 2026-10-26', 'export: exit code 0',
                     'book | fake book | 8 arguments', 'book: exit code 0',
                     'DONE: the book of the week 2026-10-26 is in ' + self.out):
            self.assertIn(text, log)
        # каждая строка журнала -- со временем
        for line in log.splitlines():
            self.assertRegex(line, r'^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d ')
        # второй прогон дописывает журнал, а не затирает
        self.run_weekly()
        self.assertEqual(self.log().count('DONE:'), 2)

    def test_the_last_full_week_by_default_or_the_given_one(self):
        for today, week, monday in (('2026-11-02', '', '2026-10-26'),
                                    ('2026-11-01', '', '2026-10-19'),
                                    ('2026-11-04', '2026-10-19', '2026-10-19')):
            with self.subTest(today=today, week=week):
                code, console = self.run_weekly(today=today, week=week)
                self.assertEqual(code, 0, console)
                self.assertEqual(self.recorded('export_args.txt')[4], monday)

    def test_a_failed_export_makes_no_book(self):
        code, console = self.run_weekly(export_code=3)
        self.assertEqual(code, 13, console)
        self.assertIsNone(self.recorded('report_args.json'))
        log = self.log()
        self.assertIn('export: exit code 3', log)
        self.assertIn('FAILED: the export, no book this time (exit 13)', log)
        self.assertNotIn('book:', log)

    def test_a_failed_book_is_twenty_and_its_code(self):
        code, console = self.run_weekly(report_code=2)
        self.assertEqual(code, 22, console)
        log = self.log()
        self.assertIn('book | ERROR: fake book failed', log)
        self.assertIn('FAILED: the book (exit 22)', log)
        self.assertNotIn('DONE:', log)

    def test_bad_input_is_code_two_and_runs_nothing(self):
        for overrides, message in (({'week': '2026-10-20'}, 'ERROR: -Week is not a Monday'),
                                   ({'-Db': os.path.join(self.root, 'nope.db')}, 'ERROR: no database'),
                                   ({'-Python': os.path.join(self.root, 'no-python')}, 'ERROR: no Python')):
            with self.subTest(message=message):
                code, console = self.run_weekly(**overrides)
                self.assertEqual(code, 2, console)
                self.assertIn(message, self.log())
                self.assertIsNone(self.recorded('export_args.txt'))
        self.assertFalse(os.path.exists(os.path.join(self.out, 'vygruzka')))

    def test_a_step_that_cannot_start_is_nine(self):
        code, console = self.run_weekly(**{'-PowerShell': os.path.join(self.root, 'no-shell')})
        self.assertEqual(code, 19, console)
        self.assertIn('export: could not be started', self.log())

    def test_the_real_export_is_the_default(self):
        # без 1С (CI) настоящая выгрузка доходит до соединения и отвечает
        # кодом 3: значит, неделю, папку и файл паролей она получила
        code, console = self.run_weekly(fakes=False, **{'-ReportScript': self.report})
        self.assertEqual(code, 13, console)
        log = self.log()
        self.assertIn('export | onec_export v4 | week 2026-10-26 .. 2026-11-01', log)
        self.assertIn('export | ERROR: 1C refused the connection', log)
        self.assertNotIn('Секрет', log)
        self.assertIsNone(self.recorded('report_args.json'))

    def test_the_real_book_is_the_default(self):
        # настоящий GPS-35 принимает аргументы, находит оба файла и папку и
        # читает датированные рейсы; на пустой базе он отказывает кодом 2
        code, console = self.run_weekly(fakes=False, **{'-ExportScript': self.export})
        self.assertEqual(code, 22, console)
        log = self.log()
        self.assertIn('book | ERROR: agro-work tables are missing in ' + self.db, log)
        self.assertNotIn('usage:', log)


if __name__ == '__main__':
    unittest.main()
