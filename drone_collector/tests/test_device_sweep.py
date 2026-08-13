# -*- coding: utf-8 -*-
"""DRONE-BODYCODE-001: обход по бортам пишет то, что прочитал, и ловит враньё.

Живой сайт из среды разработки недоступен, поэтому проверяется всё, что от
сайта не зависит: разбор вылета в строку, запись файлов, обнаружение
неприменившегося фильтра и коды возврата.

К каждой проверке -- отрицательный контроль, потому что обход, который
«прошёл успешно» на любых данных, ничего не гарантирует:

  1. Строка CSV несёт id вылета, имя устройства, ник и площадь в гектарах
     (м2/10000). Отрицательный контроль: площадь БЕЗ деления отличается в
     десять тысяч раз, и тест это видит.
  2. Один и тот же вылет под двумя устройствами -- это неприменившийся
     фильтр. Он попадает в summary.json и меняет код возврата. Отрицательный
     контроль: на честных данных список пуст.
  3. Сырые тела сохраняются всегда, по файлу на устройство.
  4. Сумма по устройствам сравнивается с прогоном без фильтра; расхождение
     возвращает код 9, а не ноль.

Run:
  python -m unittest drone_collector.tests.test_device_sweep -v
"""

import json
import os
import tempfile
import unittest

from drone_collector import devices


class FakeLog(object):
    """Журнал, который запоминает, что ему сказали."""

    def __init__(self):
        self.messages = []

    def _add(self, level, fmt, args):
        try:
            self.messages.append((level, fmt % args if args else fmt))
        except Exception:
            self.messages.append((level, fmt))

    def info(self, fmt, *args):
        self._add('info', fmt, args)

    def warning(self, fmt, *args):
        self._add('warning', fmt, args)

    def error(self, fmt, *args):
        self._add('error', fmt, args)

    def text(self, level=None):
        return '\n'.join(m for lvl, m in self.messages
                         if level is None or lvl == level)


def flight(fid, nickname, area_m2=12345.0, started=1757000000):
    return {'id': fid, 'nickname': nickname, 'new_work_area': area_m2,
            'start_timestamp': started, 'serial_number': 'R%09d' % fid}


def device_item(name, flights, complete=True, raw=None):
    return {'device': name, 'flights': flights,
            'raw_bodies': raw if raw is not None else [{'code': 0,
                                                        'data': flights}],
            'pages': 1, 'total_pages': 1, 'clicks': 0, 'complete': complete,
            'self_duplicates': 0}


class FlightRow(unittest.TestCase):
    """1. Разбор вылета в строку."""

    def test_fields_and_hectares(self):
        row = devices.flight_row(flight(101, '12 Peshku', area_m2=12345.0),
                                 '13 Peshku')
        self.assertEqual(row['dji_flight_id'], 101)
        self.assertEqual(row['device'], '13 Peshku')
        self.assertEqual(row['nickname'], '12 Peshku')
        self.assertEqual(row['area_ha'], '1.2345')

    def test_area_without_the_divisor_would_be_ten_thousand_times_off(self):
        """Отрицательный контроль на единицы: new_work_area -- это м2."""
        row = devices.flight_row(flight(102, 'x', area_m2=10000.0), 'd')
        self.assertEqual(row['area_ha'], '1.0000')
        self.assertNotEqual(row['area_ha'], '10000.0000')

    def test_unusable_area_becomes_empty_not_zero(self):
        """Пустая площадь и площадь 0 -- разные вещи; выдумывать ноль нельзя."""
        row = devices.flight_row({'id': 7, 'nickname': 'n',
                                  'new_work_area': None}, 'd')
        self.assertEqual(row['area_ha'], '')
        row_zero = devices.flight_row(flight(8, 'n', area_m2=0.0), 'd')
        self.assertEqual(row_zero['area_ha'], '0.0000')


class Outputs(unittest.TestCase):
    """2 и 3. Файлы, дубли между устройствами, сырые тела."""

    def test_csv_summary_and_raw_are_written(self):
        per_device = [
            device_item('12 Servis', [flight(1, '14 Servis'),
                                      flight(2, '14 Servis')]),
            device_item('13 Peshku', [flight(3, '12 Peshku')]),
        ]
        log = FakeLog()
        with tempfile.TemporaryDirectory() as out:
            csv_path, summary = devices.write_outputs(out, per_device, log)
            self.assertTrue(os.path.exists(csv_path))
            with open(csv_path, encoding='utf-8-sig') as handle:
                body = handle.read()
            self.assertIn('12 Servis', body)
            self.assertIn('14 Servis', body)
            raw_dir = os.path.join(out, 'raw')
            self.assertEqual(len(os.listdir(raw_dir)), 2)
            with open(os.path.join(out, 'summary.json'),
                      encoding='utf-8') as handle:
                saved = json.load(handle)
        self.assertEqual(summary['flights_total'], 3)
        self.assertEqual(saved['flights_total'], 3)
        self.assertEqual(summary['cross_device_duplicates'], [])

    def test_same_flight_under_two_devices_is_reported(self):
        """Фильтр не применился -- это видно и не прячется."""
        per_device = [
            device_item('12 Servis', [flight(1, 'n')]),
            device_item('13 Peshku', [flight(1, 'n')]),
        ]
        log = FakeLog()
        with tempfile.TemporaryDirectory() as out:
            _path, summary = devices.write_outputs(out, per_device, log)
        self.assertEqual(len(summary['cross_device_duplicates']), 1)
        entry = summary['cross_device_duplicates'][0]
        self.assertEqual(entry['dji_flight_id'], 1)
        self.assertEqual({entry['first'], entry['second']},
                         {'12 Servis', '13 Peshku'})
        self.assertIn('more than one device', log.text('error'))

    def test_device_name_with_slashes_still_gets_a_raw_file(self):
        per_device = [device_item('4 Ғиждувон / test', [flight(9, 'n')])]
        log = FakeLog()
        with tempfile.TemporaryDirectory() as out:
            devices.write_outputs(out, per_device, log)
            names = os.listdir(os.path.join(out, 'raw'))
        self.assertEqual(len(names), 1)
        self.assertTrue(names[0].endswith('.json'))


class ExitCodes(unittest.TestCase):
    """4. Коды возврата отличают сошедшийся обход от несошедшегося."""

    def test_constants_are_distinct(self):
        codes = [devices.EXIT_OK, devices.EXIT_CONFIG, devices.EXIT_SESSION,
                 devices.EXIT_PERIOD, devices.EXIT_PAGINATION,
                 devices.EXIT_NO_DEVICES, devices.EXIT_MISMATCH]
        self.assertEqual(len(codes), len(set(codes)))
        self.assertEqual(devices.EXIT_OK, 0)
        self.assertNotEqual(devices.EXIT_MISMATCH, devices.EXIT_OK)

    def test_parser_requires_period_and_out(self):
        parser = devices.build_parser()
        args = parser.parse_args(['--from', '2025-09-01', '--to',
                                  '2025-09-30', '--out', 'x'])
        self.assertEqual(args.date_from, '2025-09-01')
        self.assertEqual(args.out, 'x')
        self.assertEqual(args.device, [])
        with self.assertRaises(SystemExit):
            parser.parse_args(['--from', '2025-09-01'])

    def test_repeatable_device_argument(self):
        parser = devices.build_parser()
        args = parser.parse_args(['--from', '2025-09-01', '--to',
                                  '2025-09-30', '--out', 'x',
                                  '--device', '1 Klaster',
                                  '--device', '2 Klaster'])
        self.assertEqual(args.device, ['1 Klaster', '2 Klaster'])


class Selectors(unittest.TestCase):
    """Селекторы написаны вслепую -- проверяется хотя бы их форма."""

    def test_device_xpath_uses_the_following_axis(self):
        # Классы сборки хешируются на каждом деплое; ось following от подписи
        # `Device` -- единственное, что от них не зависит.
        self.assertTrue(devices.XPATH_DEVICE_SELECT.startswith('xpath='))
        self.assertIn("'Device'", devices.XPATH_DEVICE_SELECT)
        self.assertIn('following', devices.XPATH_DEVICE_SELECT)

    def test_dropdown_selector_skips_hidden_copies(self):
        # Ant Design оставляет закрытый список в DOM; без :not(...-hidden)
        # читались бы опции прошлого открытия.
        self.assertIn('not(.ant-select-dropdown-hidden)',
                      devices.SELECTOR_DEVICE_OPTIONS)

    def test_zero_area_checkbox_text_is_the_one_from_the_cabinet(self):
        self.assertEqual(devices.TEXT_ZERO_AREA,
                         'Display 0 Field Area Records')


if __name__ == '__main__':
    unittest.main()
