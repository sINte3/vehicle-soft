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
    """Сводка ТОЙ ЖЕ формы, что пишет `dji_area_recalc.py --json`.

    [REASON]: прежняя фикстура клала список `flights`, которого в настоящем
    файле нет: `dji_area_recalc.py:169` делает `summary.pop('flights', [])`
    ДО `json.dump(summary, ...)`, а `pipeline.py:537` вдобавок наполняет этот
    список только при `collect_rows`, то есть лишь когда передан `--rows`.
    Фикстура, не совпадающая с настоящим файлом, проверяла ворота на входе,
    который никогда не встречается.
    """
    return {'flights_in_period': n_flights,
            'flights_loaded': n_flights + 4,
            'calc_writes': calc, 'field_writes': field}


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
        res = self.run_gate({'flights_in_period': 1})
        self.assertEqual(res.returncode, 1)
        self.assertIn('calc_writes is missing', res.stdout)

    def test_the_real_apply2_shape_without_unchanged_stops_the_run(self):
        # Ровно тот файл, который приедет с сервера: ключа `flights` НЕТ,
        # период непустой, `unchanged` отсутствует. Пока ворота считали
        # записи по `flights`, n_flights всегда выходил 0 и этот вход
        # проходил насквозь -- заявленный контроль был мёртвым.
        res = self.run_gate({'flights_in_period': 226, 'flights_loaded': 230,
                             'calc_writes': {'new': 226},
                             'field_writes': {'new': 226}})
        self.assertEqual(res.returncode, 1, res.stdout)
        self.assertIn('calc_writes has no unchanged while 226', res.stdout)
        self.assertIn('field_writes has no unchanged while 226', res.stdout)

    def test_the_real_apply2_shape_with_empty_writes_stops_the_run(self):
        # Тот же вход, но writes пустые: не идемпотентность, а отсутствие
        # проверки. По коду возврата 0 их не различить.
        res = self.run_gate({'flights_in_period': 226, 'flights_loaded': 230,
                             'calc_writes': {}, 'field_writes': {}})
        self.assertEqual(res.returncode, 1, res.stdout)
        self.assertIn('has no unchanged while 226', res.stdout)

    def test_a_count_that_is_not_a_whole_number_stops_the_run(self):
        for bad in ('226', 12.5, True, None, [226], -1):
            res = self.run_gate({'flights_in_period': bad,
                                 'calc_writes': {'unchanged': 1},
                                 'field_writes': {'unchanged': 1}})
            self.assertEqual(res.returncode, 1,
                             'flights_in_period=%r passed' % (bad,))
            self.assertIn('flights_in_period', res.stdout)

    def test_a_missing_count_stops_the_run(self):
        # Сводка без этого поля -- не сводка пересчёта. Считать её пустым
        # периодом значило бы пропустить чужой файл молча.
        res = self.run_gate({'calc_writes': {'unchanged': 226},
                             'field_writes': {'unchanged': 226}})
        self.assertEqual(res.returncode, 1, res.stdout)
        self.assertIn('flights_in_period is missing', res.stdout)

    def test_control_an_empty_period_needs_no_unchanged(self):
        # Ноль записей в периоде -- законный случай, и он НЕ должен
        # останавливать прогон: проверять нечего.
        res = self.run_gate(summary({}, {}, n_flights=0))
        self.assertEqual(res.returncode, 0, res.stdout)


class TheSeamWithTheRealRecalcHolds(Base):
    """Ворота читают поле, которое настоящий инструмент правда пишет.

    Без этого весь набор проверял бы ворота на выдуманной форме входа --
    ровно та ошибка, из-за которой контроль и оказался мёртвым.
    """

    def real_summary(self, n_flights):
        """Сводка pipeline, проведённая через сериализацию recalc."""
        from dji_area import pipeline
        import importlib.util
        spec = importlib.util.spec_from_file_location('_gate', GATE)
        gate = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(gate)
        summary = pipeline._empty_summary()
        # [REASON]: имя поля берётся из САМИХ ворот и проверяется на наличие
        # ДО подстановки. Иначе тест сам создаёт ключ, которого в pipeline
        # может уже не быть, и переименование поля пройдёт незамеченным --
        # ровно та ошибка, из-за которой контроль и оказался мёртвым.
        self.assertIn(gate.COUNT_KEY, summary,
                      'pipeline no longer publishes %r' % gate.COUNT_KEY)
        summary[gate.COUNT_KEY] = n_flights
        summary['flights_loaded'] = n_flights + 4
        for _ in range(n_flights):
            summary['calc_writes']['new'] += 1
            summary['field_writes']['new'] += 1
        plain = pipeline._plain(summary)
        # Ровно то, что делает tools/dji_area_recalc.py:169 перед json.dump.
        plain.pop('flights', [])
        return json.loads(json.dumps(plain, default=str))

    def test_the_serialized_summary_has_no_flights_but_keeps_the_count(self):
        real = self.real_summary(226)
        self.assertNotIn('flights', real)
        self.assertEqual(real['flights_in_period'], 226)

    def test_a_real_second_apply_that_wrote_rows_is_refused(self):
        res = self.run_gate(self.real_summary(226))
        self.assertEqual(res.returncode, 1, res.stdout)
        self.assertIn('calc_writes.new = 226', res.stdout)

    def test_a_real_second_apply_that_wrote_nothing_is_confirmed(self):
        real = self.real_summary(226)
        real['calc_writes'] = {'unchanged': 226}
        real['field_writes'] = {'unchanged': 226}
        res = self.run_gate(real)
        self.assertEqual(res.returncode, 0, res.stdout)
        self.assertIn('IDEMPOTENCE CONFIRMED', res.stdout)

    def test_a_real_summary_whose_writes_never_happened_is_refused(self):
        # Худший случай мёртвого контроля: пересчёт не написал НИЧЕГО, а
        # ворота печатали CONFIRMED.
        real = self.real_summary(226)
        real['calc_writes'] = {}
        real['field_writes'] = {}
        res = self.run_gate(real)
        self.assertEqual(res.returncode, 1, res.stdout)
        self.assertIn('has no unchanged while 226', res.stdout)


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
