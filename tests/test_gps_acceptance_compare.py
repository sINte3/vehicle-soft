# -*- coding: utf-8 -*-
"""tools/gps_acceptance_compare.py -- одно сравнение с ручными замерами.

Что проверяется и почему именно это:

1. **Набор В-5 в репозитории даёт записанный вердикт** (дорожная карта, 2.12,
   «Прогон на данных»): 11 строк в счёте, медиана |расхождения| 26,12 % ->
   13,94 %, строка 2 ушла из зелёного допуска в недосчёт, FAIL. Если файл или
   правило сравнения поменяются, запись в документе перестанет быть правдой
   -- тест это скажет.

2. **Отрицательный контроль на той же строке.** Строка 2 стоит ровно на
   границе зелёного допуска (+10,0 %). С A7 5,1921 вместо 5,192 она жёлтая, и
   вердикт меняется на PASS: проверка различает два случая, а не печатает
   FAIL всегда.

3. **Граница допуска включительно** -- тот же `gps.tolerance`, что у экранов
   сверки; своей копии правила здесь нет.

4. **Строка выходит из счёта только по записи владельца**; пустое число без
   записи -- отказ, а не молчаливое «работы не было».

5. **Неверный ввод -- код 2**, ничего не считалось.

6. **Мало строк в счёте -- не проверено** (раздел 2.13: набор В-6 судится,
   только если в счёт вошло не меньше 10 суток из 14): ни FAIL, ни PASS не
   выносятся, код 3; с достаточным числом строк вердикт прежний.

Запуск:
  python -m unittest tests.test_gps_acceptance_compare -v
"""
import contextlib
import csv
import io
import os
import shutil
import tempfile
import unittest

import tools.gps_acceptance_compare as compare

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
V5_SET = os.path.join(REPO, 'docs', 'gps_v5_acceptance_set_2026-10-09.csv')
HEADER = ['n', 'group', 'day', 'machine', 'gps_ha_a7', 'gps_ha_edge', 'manual_ha',
          'manual_note']


def run(*argv):
    with contextlib.redirect_stdout(io.StringIO()) as out, \
            contextlib.redirect_stderr(io.StringIO()) as err:
        code = compare.main(list(argv))
    return code, out.getvalue(), err.getvalue()


class Base(unittest.TestCase):

    def setUp(self):
        self.scratch = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.scratch, True)

    def write(self, rows, header=HEADER, name='set.csv'):
        path = os.path.join(self.scratch, name)
        with open(path, 'w', encoding='utf-8', newline='') as handle:
            writer = csv.writer(handle, lineterminator='\n')
            writer.writerow(header)
            writer.writerows(rows)
        return path

    def v5_with(self, n, column, value):
        """The real V-5 set with one cell changed."""
        with open(V5_SET, encoding='utf-8', newline='') as handle:
            rows = list(csv.DictReader(handle))
        for row in rows:
            if row['n'] == str(n):
                row[column] = value
        path = os.path.join(self.scratch, 'v5.csv')
        with open(path, 'w', encoding='utf-8', newline='') as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0]),
                                    lineterminator='\n')
            writer.writeheader()
            writer.writerows(rows)
        return path


class TheV5Set(Base):

    def test_the_recorded_verdict(self):
        code, out, err = run('--csv', V5_SET)
        self.assertEqual(code, 4, out + err)
        self.assertIn('counted rows: 11; median |deviation|: base 26.12% -> '
                      'candidate 13.94%', out)
        self.assertIn('green under base -> undercount beyond green under the '
                      'candidate: n=2\n', out)
        self.assertIn('VERDICT (roadmap 2.12, condition 4): FAIL -- 1 machine-day(s) '
                      'leave the green band into undercount', out)
        self.assertIn('  9 O  2026-09-08 out of the count by the owner\'s note', out)
        self.assertIn('P ploughing       rows 4: median |dev| base 56.7% -> candidate '
                      '3.0%; sum base +22.8% -> candidate +4.0%', out)
        self.assertIn('S spraying        rows 4: median |dev| base 23.9% -> candidate '
                      '28.6%; sum base -21.8% -> candidate -44.3%', out)
        self.assertIn('stage 2 criterion (median <= 5%, spraying sum >= -5%), base: '
                      'median 26.1%, spraying -21.8% -- not met', out)
        self.assertIn('stage 2 criterion (median <= 5%, spraying sum >= -5%), '
                      'candidate: median 13.9%, spraying -44.3% -- not met', out)
        out.encode('ascii')

    def test_negative_control_the_boundary_row_decides(self):
        """A7 5.1921 on row 2 is 0.0001 ha beyond green: no row falls, PASS."""
        code, out, _err = run('--csv', self.v5_with(2, 'gps_ha_a7', '5.1921'))
        self.assertEqual(code, 0, out)
        self.assertIn('candidate: none\n', out)
        self.assertIn('VERDICT (roadmap 2.12, condition 4): PASS', out)

    def test_the_rows_match_the_owners_book(self):
        """Sums of the owner's fields, column H of «список работ для ручных
        замеров», and the one row he marked as no field work."""
        rows = {row['n']: row for row in compare.load(V5_SET, 'gps_ha_a7',
                                                      'gps_ha_edge')}
        self.assertEqual(len(rows), 12)
        self.assertEqual(rows[3]['manual'], 17.639)       # five fields
        self.assertEqual(rows[1]['manual'], 1.2)          # 1.069 + 0.131
        self.assertIsNone(rows[9]['manual'])
        self.assertEqual(rows[9]['note'], 'Полевые работы не проводились')


class Rule(Base):

    def test_the_green_boundary_is_inclusive(self):
        # 10 % of 3.0 is 0.3: exactly on the band both ways
        path = self.write([['1', 'P', '2026-09-01', 'M', '3.300', '2.700', '3.000', ''],
                           ['2', 'P', '2026-09-02', 'M', '1.000', '1.000', '1.000', '']])
        code, out, _err = run('--csv', path)
        self.assertEqual(code, 0, out)
        self.assertIn('candidate: none\n', out)
        # no spraying row: the stage 2 criterion is the median alone
        self.assertIn('base: median 5.0%, spraying - -- met', out)

    def test_the_stage2_spraying_sum_decides_when_the_median_holds(self):
        rows = [['2', 'P', '2026-09-02', 'M', '1.000', '1.000', '1.000', ''],
                ['3', 'P', '2026-09-03', 'M', '2.000', '2.000', '2.000', '']]
        short = self.write([['1', 'S', '2026-09-01', 'M', '0.920', '0.980', '1.000',
                             '']] + rows)
        code, out, _err = run('--csv', short)
        self.assertIn('base: median 0.0%, spraying -8.0% -- not met', out)
        self.assertIn('candidate: median 0.0%, spraying -2.0% -- met', out)

    def test_a_fall_from_green_into_undercount_fails(self):
        path = self.write([['1', 'S', '2026-09-01', 'M', '3.100', '2.600', '3.000', ''],
                           ['2', 'P', '2026-09-02', 'M', '5.000', '1.000', '1.000', '']])
        code, out, _err = run('--csv', path)
        self.assertEqual(code, 4, out)
        self.assertIn('candidate: n=1\n', out)

    def test_leaving_green_by_overcount_is_not_a_fall(self):
        """The condition names undercount: a candidate above the band is
        judged by the median, not by this test."""
        path = self.write([['1', 'S', '2026-09-01', 'M', '3.100', '3.500', '3.000', ''],
                           ['2', 'P', '2026-09-02', 'M', '9.000', '1.000', '1.000', '']])
        code, out, _err = run('--csv', path)
        self.assertIn('candidate: none\n', out)
        self.assertEqual(code, 0, out)

    def test_a_larger_median_fails(self):
        path = self.write([['1', 'P', '2026-09-01', 'M', '1.050', '1.200', '1.000', ''],
                           ['2', 'P', '2026-09-02', 'M', '2.100', '2.400', '2.000', ''],
                           ['3', 'P', '2026-09-03', 'M', '3.150', '3.600', '3.000', '']])
        code, out, _err = run('--csv', path)
        self.assertEqual(code, 4, out)
        self.assertIn('FAIL -- the median |deviation| grows', out)

    def test_a_note_without_a_number_is_out_of_the_count(self):
        path = self.write([['1', 'P', '2026-09-01', 'M', '1.000', '1.000', '1.000', ''],
                           ['2', 'O', '2026-09-02', 'M', '9.000', '0.100', '',
                            'Полевые работы не проводились']])
        code, out, _err = run('--csv', path)
        self.assertEqual(code, 0, out)
        self.assertIn('counted rows: 1;', out)
        self.assertIn('  2 O  2026-09-02 out of the count', out)


class Refusals(Base):

    def test_bad_input_is_code_2(self):
        missing_column = self.write([['1', 'P', '2026-09-01', 'M', '1.0', '1.0']],
                                    header=HEADER[:6])
        silent_blank = self.write([['1', 'P', '2026-09-01', 'M', '1.0', '1.0', '', '']],
                                  name='blank.csv')
        zero = self.write([['1', 'P', '2026-09-01', 'M', '1.0', '1.0', '0', '']],
                          name='zero.csv')
        only_notes = self.write([['1', 'P', '2026-09-01', 'M', '1.0', '1.0', '',
                                  'нет работы']], name='notes.csv')
        for argv, word in (
                (('--csv', os.path.join(self.scratch, 'none.csv')), 'not found'),
                (('--csv', missing_column), 'no column(s): manual_ha, manual_note'),
                (('--csv', silent_blank), 'neither a hand measurement nor a note'),
                (('--csv', zero), 'must be positive'),
                (('--csv', only_notes), 'no row with a hand measurement')):
            code, out, err = run(*argv)
            self.assertEqual(code, 2, (argv, out, err))
            self.assertIn(word, err, argv)
            self.assertEqual(out, '', argv)



class MinRows(Base):

    FAILING = [['1', 'S', '2026-09-01', 'M', '3.100', '2.600', '3.000', ''],
               ['2', 'P', '2026-09-02', 'M', '5.000', '1.000', '1.000', ''],
               ['3', 'O', '2026-09-03', 'M', '9.000', '0.100', '',
                'Полевые работы не проводились']]

    def test_fewer_counted_rows_than_asked_is_not_checked(self):
        path = self.write(self.FAILING)
        code, out, _err = run('--csv', path, '--min-rows', '3', '--condition',
                              'roadmap 2.13, condition 3')
        self.assertEqual(code, 3, out)
        self.assertIn('VERDICT (roadmap 2.13, condition 3): NOT CHECKED -- 2 counted '
                      'row(s), at least 3 are needed', out)
        # the comparison itself is still printed
        self.assertIn('candidate: n=1\n', out)
        # control: the same file judged without the minimum fails
        code, out, _err = run('--csv', path)
        self.assertEqual(code, 4, out)
        self.assertIn('VERDICT (roadmap 2.12, condition 4): FAIL', out)

    def test_enough_rows_are_judged_as_before(self):
        path = self.write(self.FAILING)
        code, out, _err = run('--csv', path, '--min-rows', '2')
        self.assertEqual(code, 4, out)
        self.assertIn('VERDICT (roadmap 2.12, condition 4): FAIL', out)
        passing = self.write([['1', 'P', '2026-09-01', 'M', '1.000', '1.000', '1.000',
                               ''],
                              ['2', 'P', '2026-09-02', 'M', '2.000', '2.000', '2.000',
                               '']], name='passing.csv')
        code, out, _err = run('--csv', passing, '--min-rows', '2')
        self.assertEqual(code, 0, out)

    def test_a_minimum_below_one_is_refused(self):
        code, out, err = run('--csv', self.write(self.FAILING), '--min-rows', '0')
        self.assertEqual(code, 2, out + err)
        self.assertIn('--min-rows must be at least 1', err)


if __name__ == '__main__':
    unittest.main()
