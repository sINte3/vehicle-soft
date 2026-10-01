# -*- coding: utf-8 -*-
"""agro-work B3: отчёт сверки файлом -- числа книги равны числам ядра.

Отчёт появляется раньше экрана (экран уедет с релизом), и владелец будет
принимать по нему решения, в том числе о N. Поэтому держится:

  * каждое число листа «Свод» -- то же, что у ядра сверки на тех же данных;
  * строк на листах «Заявка-работа» и «Работа-заявка» -- столько же, сколько
    строк у ядра;
  * база не меняется ни на байт (открыта mode=ro);
  * предпросмотр N помечен в книге как неутверждённый.

Запуск: python -m unittest tests.test_agro_work_reconcile_tool -v
"""

import hashlib
import io
import os
import sqlite3
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import date
from unittest import mock

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from openpyxl import load_workbook                            # noqa: E402

from agro_work import reconcile as rc                         # noqa: E402
from tests.test_agro_work_reconcile import Fixture            # noqa: E402
from tools import agro_work_reconcile as tool                 # noqa: E402


def sha(path):
    with open(path, 'rb') as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def book_of(path):
    with open(path, 'rb') as fh:
        return load_workbook(fh)


class ReportTool(unittest.TestCase):
    def setUp(self):
        self.fx = Fixture()
        self.fx.app(created=10, completed=11)                  # работа была
        self.fx.app(created=11, completed=13)                  # работы не было
        self.fx.app(transport='T8')                            # не сопоставлена
        self.fx.app(created=12, completed=12, initial='COMPLETED')
        self.fx.con.close()
        self.out = os.path.join(os.path.dirname(self.fx.path), 'report.xlsx')

    def main(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = tool.main(['--db', self.fx.path, '--from', '2026-09-10',
                              '--to', '2026-09-20', '--out', self.out]
                             + list(args))
        return code, out.getvalue(), err.getvalue()

    def test_the_book_repeats_the_core_and_the_database_is_untouched(self):
        before = sha(self.fx.path)
        code, out, err = self.main()
        self.assertEqual(code, 0, err)
        self.assertTrue(out.isascii())
        self.assertEqual(sha(self.fx.path), before)

        con = sqlite3.connect(self.fx.path)
        try:
            ctx = rc.Reconciliation(con, date(2026, 9, 10), date(2026, 9, 20))
            forward, reverse = ctx.forward_rows(), ctx.reverse_rows()
        finally:
            con.close()
        _, total = ctx.summary(forward, reverse)
        self.assertIn('applications: %d | work confirmed %d | NO WORK %d'
                      % (total['applications'], total['app_rabota_est'],
                         total['app_rabota_net']), out)

        book = book_of(self.out)
        self.assertEqual(book.sheetnames, ['Свод', 'Заявка-работа',
                                           'Работа-заявка', 'Открытые заявки',
                                           'Причины', 'Замер N'])
        summary = [row for row in book['Свод'].iter_rows(values_only=True)]
        totals = [row for row in summary if row[0] == 'Итого / Жами'][0]
        self.assertEqual(totals[2:], (total['machines'], total['applications'],
                                      total['app_rabota_est'],
                                      total['app_rabota_net'],
                                      total['app_bez_verdikta'],
                                      total['work_days'], total['day_pokryta'],
                                      total['day_bez_zayavki'],
                                      total['day_bez_verdikta'],
                                      total[rc.DAY_LATE]))
        self.assertEqual(book['Заявка-работа'].max_row - 1, len(forward))
        self.assertEqual(book['Работа-заявка'].max_row - 1, len(reverse))
        notes = ' '.join(str(r[0]) for r in summary if r and r[0])
        self.assertIn('N для заявок, заведённых задним числом: 2 '
                      '(утверждён владельцем)', notes)

    def test_the_late_application_and_the_open_list_reach_the_book(self):
        # B4, пункты 4 и 5: заявка задним числом от 18-го для работы 13-го
        # (опоздание 5 суток при N = 2) и открытая заявка от 10-го.
        self.fx.con = sqlite3.connect(self.fx.path)
        self.fx.app(transport='T2', created=18, completed=18,
                    initial='COMPLETED')
        self.fx.app(transport='T1', status='IN_PROGRESS', created=10,
                    completed=None)
        self.fx.con.close()
        code, out, err = self.main()
        self.assertEqual(code, 0, err)
        self.assertIn('of them with a later backdated application: 1 '
                      '(days late: min 5, max 5)', out)
        days_open = (date.today() - date(2026, 9, 10)).days
        self.assertIn('open applications: 1 | oldest open %d days since entry'
                      % days_open, out)
        book = book_of(self.out)
        day13 = [row for row in book['Работа-заявка'].iter_rows(values_only=True)
                 if row[0] is not None and str(row[0]).startswith('2026-09-13')
                 and '80 012 EA' in (row[1] or '')][0]
        self.assertEqual(day13[-2:], ('N-005', 5))
        opened = list(book['Открытые заявки'].iter_rows(values_only=True))[1:]
        self.assertEqual([(r[2], r[-1]) for r in opened], [('N-006', days_open)])
        totals = [row for row in book['Свод'].iter_rows(values_only=True)
                  if row and row[0] == 'Итого / Жами'][0]
        self.assertEqual(totals[-1], 1)

    def test_preview_names_itself_and_the_approved_n(self):
        code, out, err = self.main('--lookback-preview', '3')
        self.assertEqual(code, 0, err)
        self.assertIn('PREVIEW N=3', out)
        notes = ' '.join(str(r[0]) for r in book_of(self.out)['Свод']
                         .iter_rows(values_only=True) if r and r[0])
        self.assertIn('ПРЕДПРОСМОТР: N = 3; утверждённое владельцем N = 2',
                      notes)

    def test_without_an_approved_n_the_book_says_so(self):
        with mock.patch.object(rc, 'BACKDATED_LOOKBACK_DAYS', None):
            code, out, err = self.main()
            self.assertEqual(code, 0, err)
            notes = ' '.join(str(r[0]) for r in book_of(self.out)['Свод']
                             .iter_rows(values_only=True) if r and r[0])
            self.assertIn('N для заявок, заведённых задним числом: не '
                          'утверждён', notes)
            code, out, err = self.main('--lookback-preview', '2')
            notes = ' '.join(str(r[0]) for r in book_of(self.out)['Свод']
                             .iter_rows(values_only=True) if r and r[0])
            self.assertIn('ПРЕДПРОСМОТР: N = 2 не утверждён владельцем', notes)

    def test_lag_measurement_is_printed_and_written(self):
        code, out, err = self.main()
        self.assertEqual(code, 0, err)
        self.assertIn('completion lag', out)
        rows = list(book_of(self.out)['Замер N'].iter_rows(values_only=True))
        self.assertEqual(rows[1][1], 1)                  # одна заявка в замере

    def test_refusals(self):
        bare = os.path.join(os.path.dirname(self.fx.path), 'bare.db')
        sqlite3.connect(bare).close()
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            self.assertEqual(tool.main(['--db', bare]), 2)
            self.assertEqual(tool.main(['--db', self.fx.path, '--from',
                                        '2026-09-20', '--to', '2026-09-10']), 2)
            self.assertEqual(tool.main(['--db', self.fx.path, '--to', '20.09']), 2)
        self.assertFalse(os.path.exists(self.out))


if __name__ == '__main__':
    unittest.main()
