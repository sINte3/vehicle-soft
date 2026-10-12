# -*- coding: utf-8 -*-
"""FIN-RECON-STORAGE-001: инструмент оператора tools/fin_recon_storage.py.

Ранбук владельца читает у инструмента код выхода и последнюю строку
`RESULT=...`. Здесь держится каждый код, на который ранбук опирается:

  0 PASS, 2 аргументы/нет базы (и ничего не создано), 3 конфликт версий,
  4 пакет негоден, 5 миграция не применена, 6 сверка не сошлась;

плюс: вывод только ASCII (консоль сервера -- кодовая страница OEM), флаги
нельзя сокращать, восстановление идёт только в НОВЫЙ пустой каталог, и
повторный импорт в восстановленную базу не меняет её ни на байт.

Инструмент запускается настоящим процессом (`python -I`), как на сервере.
ВСЕ ДАННЫЕ СИНТЕТИЧЕСКИЕ. Запуск:
  python -m unittest tests.test_fin_recon_cli -v
"""

import copy
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from tests import fin_recon_fixture as fx  # noqa: E402

TOOL = os.path.join(REPO_ROOT, 'tools', 'fin_recon_storage.py')
MIGRATION = os.path.join(REPO_ROOT, 'migrate_fin_recon_storage_001.py')


def run(*args):
    proc = subprocess.run([sys.executable, '-I', TOOL] + list(args),
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                          cwd=REPO_ROOT)
    text = proc.stdout.decode('ascii')     # не ASCII -- тест упадёт здесь
    return proc.returncode, text


def last(text):
    lines = [line for line in text.splitlines() if line.strip()]
    return lines[-1] if lines else ''


class CliBase(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='fin_recon_cli_')
        self.pkg = os.path.join(self.tmp, 'pkg')
        self.paths = fx.write_package(self.pkg)
        self.db = os.path.join(self.tmp, 'clone.db')
        sqlite3.connect(self.db).close()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def migrate(self):
        proc = subprocess.run([sys.executable, '-I', MIGRATION, '--db',
                               self.db], stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT, cwd=REPO_ROOT)
        self.assertEqual(proc.returncode, 0, proc.stdout)


class Codes(CliBase):

    def test_check_package_pass_and_fail(self):
        code, out = run('check-package', '--package', self.pkg, '--expect',
                        self.paths['expect'])
        self.assertEqual(code, 0, out)
        self.assertTrue(last(out).startswith('RESULT=PASS'), out)
        self.assertIn('CONTROL WORKS_COUNT=38', out)
        with open(os.path.join(self.pkg, 'snapshot.json'), 'ab') as fh:
            fh.write(b' ')
        code, out = run('check-package', '--package', self.pkg)
        self.assertEqual(code, 4, out)
        self.assertEqual(last(out), 'RESULT=FAIL code=4')

    def test_import_without_migration_is_5(self):
        code, out = run('import', '--db', self.db, '--package', self.pkg)
        self.assertEqual(code, 5, out)
        self.assertIn('apply migration FIN_RECON_STORAGE_001', out)

    def test_import_missing_db_is_2_and_creates_nothing(self):
        missing = os.path.join(self.tmp, 'none.db')
        code, out = run('import', '--db', missing, '--package', self.pkg)
        self.assertEqual(code, 2, out)
        self.assertFalse(os.path.exists(missing))

    def test_import_repeat_conflict_and_verify(self):
        self.migrate()
        code, out = run('import', '--db', self.db, '--package', self.pkg,
                        '--imported-by', 'cli-test')
        self.assertEqual(code, 0, out)
        self.assertIn('import=IMPORTED', last(out))
        code, out = run('import', '--db', self.db, '--package', self.pkg)
        self.assertEqual(code, 0, out)
        self.assertIn('import=ALREADY_IMPORTED', last(out))
        code, out = run('verify', '--db', self.db, '--package', self.pkg,
                        '--expect', self.paths['expect'])
        self.assertEqual(code, 0, out)
        self.assertIn('EXPECTATIONS 24 checked, 0 failed', out)
        changed = copy.deepcopy(self.paths['snapshot'])
        changed['works'][0]['farm'] = 'синтетик: бошқа'
        other = os.path.join(self.tmp, 'pkg_changed')
        fx.write_package(other, snapshot=changed)
        code, out = run('import', '--db', self.db, '--package', other)
        self.assertEqual(code, 3, out)
        self.assertIn('CONFLICT', out)
        code, out = run('verify', '--db', self.db, '--package', other)
        self.assertEqual(code, 6, out)

    def test_status_reads_only(self):
        self.migrate()
        run('import', '--db', self.db, '--package', self.pkg)
        code, out = run('status', '--db', self.db)
        self.assertEqual(code, 0, out)
        self.assertIn('SNAPSHOT 2031-03 v1 status=RECONCILED_WITH_EXCEPTIONS '
                      'sealed=yes works=38 operators=7 anomalies=39', out)

    def test_flags_cannot_be_abbreviated(self):
        code, _out = run('check-package', '--pack', self.pkg)
        self.assertEqual(code, 2)

    def test_no_command_is_usage(self):
        self.assertEqual(run()[0], 2)


class RestoreTest(CliBase):

    def test_restore_into_a_new_empty_folder(self):
        work = os.path.join(self.tmp, 'restore')
        code, out = run('restore-test', '--package', self.pkg, '--workdir',
                        work, '--expect', self.paths['expect'])
        self.assertEqual(code, 0, out)
        self.assertIn('import #1=IMPORTED', out)
        self.assertIn('import #2=ALREADY_IMPORTED rows_written=0', out)
        self.assertIn('unchanged=True', out)
        self.assertTrue(last(out).startswith('RESULT=PASS restore-test'))

    def test_restore_refuses_a_non_empty_folder(self):
        work = os.path.join(self.tmp, 'restore')
        os.makedirs(work)
        with open(os.path.join(work, 'old.db'), 'w') as fh:
            fh.write('x')
        code, out = run('restore-test', '--package', self.pkg, '--workdir',
                        work)
        self.assertEqual(code, 2, out)
        self.assertIn('not empty', out)

    def test_restore_with_a_wrong_expectation_fails(self):
        import json
        with open(self.paths['expect'], encoding='utf-8') as fh:
            expect = json.load(fh)
        expect['expect']['check:WORKS_COUNT'] = 37
        bad = os.path.join(self.tmp, 'bad_expect.json')
        with open(bad, 'w', encoding='utf-8') as fh:
            json.dump(expect, fh)
        code, out = run('restore-test', '--package', self.pkg, '--workdir',
                        os.path.join(self.tmp, 'restore2'), '--expect', bad)
        self.assertEqual(code, 6, out)
        self.assertIn('EXPECT FAIL check:WORKS_COUNT', out)


if __name__ == '__main__':
    unittest.main()
