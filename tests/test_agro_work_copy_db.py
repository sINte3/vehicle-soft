# -*- coding: utf-8 -*-
"""agro-work: копия боевой базы для проверки -- и только копия.

Держится:
  * копия равна источнику по содержимому, источник не меняется ни на байт;
  * в папку transport-report (прод, площадка) копия не пишется никогда;
  * существующая копия не перезаписывается без --replace -- в ней может
    лежать уже сделанный импорт;
  * источник и приёмник -- один файл: отказ.

Запуск: python -m unittest tests.test_agro_work_copy_db -v
"""

import hashlib
import io
import os
import sqlite3
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from tools import agro_work_copy_db as tool                  # noqa: E402


def sha(path):
    with open(path, 'rb') as fh:
        return hashlib.sha256(fh.read()).hexdigest()


class CopyDb(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.mkdtemp(prefix='agro_copy_')
        self.source = os.path.join(self.folder, 'live', 'transport.db')
        os.makedirs(os.path.dirname(self.source))
        con = sqlite3.connect(self.source)
        con.execute('PRAGMA journal_mode=WAL')
        con.execute('CREATE TABLE equipment (id INTEGER PRIMARY KEY, plate TEXT)')
        con.executemany('INSERT INTO equipment (plate) VALUES (?)',
                        [('80 %03d EA' % n,) for n in range(50)])
        con.commit()
        con.close()
        self.target = os.path.join(self.folder, 'VehicleSoft_AgroWork',
                                   'instance', 'transport.db')

    def main(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = tool.main(list(args))
        return code, out.getvalue(), err.getvalue()

    def test_the_copy_has_the_data_and_the_source_is_untouched(self):
        before = sha(self.source)
        code, out, err = self.main('--from', self.source, '--to', self.target)
        self.assertEqual(code, 0, err)
        self.assertIn('integrity of the copy: ok', out)
        self.assertEqual(sha(self.source), before)
        con = sqlite3.connect(self.target)
        try:
            self.assertEqual(con.execute('SELECT COUNT(*) FROM equipment')
                             .fetchone()[0], 50)
        finally:
            con.close()
        self.assertFalse(os.path.exists(self.target + '.part'))

    def test_an_existing_copy_needs_replace(self):
        self.main('--from', self.source, '--to', self.target)
        con = sqlite3.connect(self.target)
        con.execute('CREATE TABLE agro_work_marker (id INTEGER)')
        con.commit()
        con.close()
        code, _, err = self.main('--from', self.source, '--to', self.target)
        self.assertEqual(code, 2)
        self.assertIn('--replace', err)
        con = sqlite3.connect(self.target)
        try:
            tables = {r[0] for r in con.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
        finally:
            con.close()
        self.assertIn('agro_work_marker', tables)
        code, _, err = self.main('--from', self.source, '--to', self.target,
                                 '--replace')
        self.assertEqual(code, 0, err)

    def test_never_into_a_transport_report_folder(self):
        for folder in ('transport-report', 'transport-report-staging'):
            target = os.path.join(self.folder, folder, 'instance', 'x.db')
            code, _, err = self.main('--from', self.source, '--to', target)
            self.assertEqual(code, 2, folder)
            self.assertIn('never land next to a live database', err)
            self.assertFalse(os.path.exists(target))

    def test_same_file_and_missing_source_are_refused(self):
        code, _, err = self.main('--from', self.source, '--to', self.source)
        self.assertEqual(code, 2)
        self.assertIn('same file', err)
        code, _, err = self.main('--from', self.source + '.nope', '--to',
                                 self.target)
        self.assertEqual(code, 2)
        self.assertFalse(os.path.exists(self.target))


if __name__ == '__main__':
    unittest.main()
