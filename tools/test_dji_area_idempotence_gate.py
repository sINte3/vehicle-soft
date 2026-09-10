# -*- coding: utf-8 -*-
"""Самотест ворот идемпотентности.

Ворота обязаны РАЗЛИЧАТЬ два случая, а не всегда пропускать. Поэтому рядом с
каждым положительным контролем стоит отрицательный: сводка, которая обязана
остановить прогон.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

GATE = os.path.join(ROOT, 'tools', 'dji_area_idempotence_gate.py')


def summary(calc, field, n_flights=226):
    return {'calc_writes': calc, 'field_writes': field,
            'flights': [{'flight_id': i} for i in range(n_flights)]}


class Base(unittest.TestCase):

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix='idem-')

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def run_gate(self, payload, name='apply2.json'):
        path = os.path.join(self.dir, name)
        if payload is not None:
            with open(path, 'w', encoding='utf-8') as fh:
                json.dump(payload, fh)
        return subprocess.run([sys.executable, GATE, '--summary', path],
                              capture_output=True, text=True)


class ItPassesOnlyOnARealSecondApply(Base):

    def test_all_unchanged_is_confirmed(self):
        res = self.run_gate(summary({'unchanged': 226}, {'unchanged': 226}))
        self.assertEqual(res.returncode, 0, res.stdout)
        self.assertIn('IDEMPOTENCE CONFIRMED', res.stdout)


class ItStopsTheRun(Base):

    def test_a_new_calculation_row_stops_the_run(self):
        res = self.run_gate(summary({'unchanged': 225, 'new': 1},
                                    {'unchanged': 226}))
        self.assertEqual(res.returncode, 1)
        self.assertIn('calc_writes.new = 1', res.stdout)

    def test_a_reactivated_calculation_row_stops_the_run(self):
        res = self.run_gate(summary({'unchanged': 225, 'reactivated': 1},
                                    {'unchanged': 226}))
        self.assertEqual(res.returncode, 1)
        self.assertIn('calc_writes.reactivated = 1', res.stdout)

    def test_a_new_field_row_stops_the_run(self):
        res = self.run_gate(summary({'unchanged': 226},
                                    {'unchanged': 225, 'new': 1}))
        self.assertEqual(res.returncode, 1)
        self.assertIn('field_writes.new = 1', res.stdout)

    def test_no_unchanged_at_all_stops_the_run(self):
        # Пустые writes при наличии записей -- это не идемпотентность, а
        # отсутствие проверки. По коду возврата 0 их не различить.
        res = self.run_gate(summary({}, {}))
        self.assertEqual(res.returncode, 1)
        self.assertIn('has no unchanged while 226 flight(s)', res.stdout)

    def test_zero_unchanged_stops_the_run(self):
        res = self.run_gate(summary({'unchanged': 0}, {'unchanged': 0}))
        self.assertEqual(res.returncode, 1)

    def test_a_missing_writes_section_stops_the_run(self):
        res = self.run_gate({'flights': [{'flight_id': 1}]})
        self.assertEqual(res.returncode, 1)
        self.assertIn('calc_writes is missing', res.stdout)

    def test_control_an_empty_period_needs_no_unchanged(self):
        # Ноль записей в периоде -- законный случай, и он НЕ должен
        # останавливать прогон: проверять нечего.
        res = self.run_gate(summary({}, {}, n_flights=0))
        self.assertEqual(res.returncode, 0, res.stdout)


class ItRefusesUnreadableInput(Base):

    def test_a_missing_file_is_code_2(self):
        res = self.run_gate(None)
        self.assertEqual(res.returncode, 2)
        self.assertIn('summary not found', res.stdout)

    def test_broken_json_is_code_2_not_a_silent_pass(self):
        path = os.path.join(self.dir, 'apply2.json')
        with open(path, 'w', encoding='utf-8') as fh:
            fh.write('{not json')
        res = subprocess.run([sys.executable, GATE, '--summary', path],
                             capture_output=True, text=True)
        self.assertEqual(res.returncode, 2)


if __name__ == '__main__':
    unittest.main()
