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
  4. Контроль окна берётся из метаданных первой страницы: точное число, если
     сайт его сообщает, иначе границы по числу страниц. Отрицательный
     контроль: `total_pages` содержит слово total, но числом вылетов не
     является и им не становится.
  5. То, что сломалось на живом прогоне 2026-08-13, закреплено тестом:
     привязка селектора устройства к подписи, повтор обхода, длина дампа
     панели.

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


def save_all(out_dir, per_device, log):
    """Как это делает сам режим: борт за бортом, сразу на диск."""
    for item in per_device:
        devices.save_device(out_dir, item, log)
    return devices.build_summary(out_dir, log)


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
            csv_path, summary = save_all(out, per_device, log)
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
            _path, summary = save_all(out, per_device, log)
        self.assertEqual(len(summary['cross_device_duplicates']), 1)
        entry = summary['cross_device_duplicates'][0]
        # Сводка строится по тому, что ЛЕЖИТ НА ДИСКЕ, поэтому id -- строка:
        # так его прочитает и тот, кто откроет CSV следующим.
        self.assertEqual(str(entry['dji_flight_id']), '1')
        self.assertEqual({entry['first'], entry['second']},
                         {'12 Servis', '13 Peshku'})
        self.assertIn('more than one device', log.text('error'))

    def test_device_name_with_slashes_still_gets_a_raw_file(self):
        per_device = [device_item('4 Ғиждувон / test', [flight(9, 'n')])]
        log = FakeLog()
        with tempfile.TemporaryDirectory() as out:
            save_all(out, per_device, log)
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


class FakePage(object):
    """Минимальная страница: помнит, что у неё есть meta и url."""

    def __init__(self, meta, url):
        self._meta = meta
        self.url = url

    @property
    def meta(self):
        return self._meta

    @property
    def total_pages(self):
        return self._meta.get('total_pages')


class ControlBounds(unittest.TestCase):
    """Контроль окна берётся из метаданных, а не из полного обхода."""

    URL = ('https://www.djiag.com/api/web/v1/flight_records?'
           'filters%5Btimestamp_gteq%5D=1756666800000&'
           'filters%5Btimestamp_lteq%5D=1759258799999&page_size=50&page=1')

    def test_bounds_from_page_count(self):
        log = FakeLog()
        control = devices.control_bounds(
            [FakePage({'current_page': 1, 'total_pages': 114}, self.URL)], log)
        self.assertIsNone(control['exact'])
        self.assertEqual(control['low'], 5651)
        self.assertEqual(control['high'], 5700)
        # Сентябрь-2025 -- 5661 вылет: попадает в границы.
        self.assertTrue(control['low'] <= 5661 <= control['high'])

    def test_total_pages_is_never_mistaken_for_a_record_count(self):
        """Отрицательный контроль: total_pages содержит 'total', но это страницы."""
        log = FakeLog()
        control = devices.control_bounds(
            [FakePage({'total_pages': 114}, self.URL)], log)
        self.assertIsNone(control['exact'])
        self.assertNotEqual(control['exact'], 114)

    def test_explicit_total_is_used_when_the_site_sends_one(self):
        log = FakeLog()
        control = devices.control_bounds(
            [FakePage({'total_pages': 114, 'total_count': 5661}, self.URL)],
            log)
        self.assertEqual(control['exact'], 5661)

    def test_no_captures_gives_no_control(self):
        control = devices.control_bounds([], FakeLog())
        self.assertIsNone(control['exact'])
        self.assertIsNone(control['low'])


class LiveRunFixes(unittest.TestCase):
    """То, что сломалось на живом прогоне 2026-08-13, закреплено тестом."""

    def test_device_selector_is_pinned_to_its_label(self):
        # Team/Member -- тоже ant-select; без привязки к подписи селектор
        # взял бы то поле, которое окажется первым в DOM.
        self.assertIn('Device', devices.SELECTOR_DEVICE_SELECT)
        self.assertIn(':has(', devices.SELECTOR_DEVICE_SELECT)
        self.assertIn('ant-form-item', devices.SELECTOR_DEVICE_SELECT)

    def test_pagination_is_retried_more_than_once(self):
        self.assertGreater(devices.PAGINATION_ATTEMPTS, 1)
        self.assertGreaterEqual(devices.PAGINATION_RETRY_PAUSE_MS, 10000)

    def test_panel_dump_is_long_enough_to_reach_the_buttons(self):
        # 4000 символов обрывались на Team/Member: ни галочки, ни OK/Clear.
        self.assertGreaterEqual(devices.PANEL_DUMP_CHARS, 12000)


class ControlKeyChoice(unittest.TestCase):
    """Живой прогон 2026-08-13: meta несёт и `count`, и `total_count`."""

    URL = ('https://www.djiag.com/api/web/v1/flight_records?'
           'page_size=50&page=1')
    # Ровно то, что прислал сайт: count -- строки страницы, total_count -- окно.
    LIVE_META = {'count': 50, 'current_page': 1, 'total_count': 5661,
                 'total_pages': 114}

    def test_page_count_is_not_mistaken_for_the_window(self):
        """Отрицательный контроль: по алфавиту `count` первый, но он не окно."""
        control = devices.control_bounds(
            [FakePage(self.LIVE_META, self.URL)], FakeLog())
        self.assertEqual(control['exact'], 5661)
        self.assertNotEqual(control['exact'], 50)

    def test_a_total_contradicting_the_page_count_is_refused(self):
        """Кандидат обязан согласоваться с числом страниц."""
        log = FakeLog()
        control = devices.control_bounds(
            [FakePage({'total_count': 50, 'total_pages': 114}, self.URL)], log)
        self.assertIsNone(control['exact'])
        self.assertIn('contradicts the page count', log.text('warning'))
        # Границы остаются -- проверка по ним всё ещё возможна.
        self.assertEqual(control['low'], 5651)

    def test_count_alone_is_never_used(self):
        control = devices.control_bounds(
            [FakePage({'count': 50, 'total_pages': 114}, self.URL)], FakeLog())
        self.assertIsNone(control['exact'])
        self.assertEqual(control['low'], 5651)

    def test_preferred_keys_exclude_count(self):
        self.assertNotIn('count', devices.TOTAL_KEYS_PREFERRED)
        self.assertEqual(devices.TOTAL_KEYS_PREFERRED[0], 'total_count')


class Resilience(unittest.TestCase):
    """Один сорвавшийся борт не отменяет остальные четырнадцать."""

    def test_device_retry_is_configured(self):
        self.assertGreater(devices.DEVICE_ATTEMPTS, 1)
        self.assertGreaterEqual(devices.DEVICE_RETRY_PAUSE_MS, 10000)

    def test_collector_exposes_recovery(self):
        for name in ('recover', 'collect_one_device_resilient',
                     'paginate_with_retry', 'filter_is_open'):
            self.assertTrue(hasattr(devices.DeviceSweepCollector, name),
                            'нет метода %s' % name)


class Resume(unittest.TestCase):
    """6. Прогон продолжаемый: снятое остаётся на диске и не переснимается."""

    def test_each_device_lands_on_disk_immediately(self):
        log = FakeLog()
        with tempfile.TemporaryDirectory() as out:
            devices.save_device(out, device_item('2 Klaster',
                                                 [flight(1, 'Klaster№1')]), log)
            # Второй борт ещё не снят, но первый уже на диске.
            self.assertTrue(os.path.exists(devices.device_csv_path(out)))
            progress = devices.load_progress(out)
            self.assertEqual(list(progress), ['2 Klaster'])
            devices.save_device(out, device_item('6 Shofirko',
                                                 [flight(2, 'Shofirko№4'),
                                                  flight(3, 'Shofirko№4')]), log)
            _path, summary = devices.build_summary(out, log)
        self.assertEqual(summary['flights_total'], 3)
        self.assertEqual(len(summary['devices']), 2)

    def test_resweeping_a_device_replaces_it_instead_of_doubling(self):
        """Отрицательный контроль: повтор борта не удваивает его вылеты."""
        log = FakeLog()
        with tempfile.TemporaryDirectory() as out:
            devices.save_device(out, device_item('2 Klaster',
                                                 [flight(1, 'n')]), log)
            devices.save_device(out, device_item('2 Klaster',
                                                 [flight(1, 'n'),
                                                  flight(2, 'n')]), log)
            _path, summary = devices.build_summary(out, log)
        self.assertEqual(summary['flights_total'], 2)
        self.assertNotEqual(summary['flights_total'], 3)

    def test_progress_survives_a_crash_between_devices(self):
        """Снятое прошлым прогоном читается следующим -- это и есть resume."""
        log = FakeLog()
        with tempfile.TemporaryDirectory() as out:
            devices.save_device(out, device_item('2 Klaster',
                                                 [flight(1, 'n')]), log)
            # «Новый процесс»: ничего в памяти нет, читаем с диска.
            done = devices.load_progress(out)
            self.assertIn('2 Klaster', done)
            self.assertEqual(done['2 Klaster']['flights'], 1)

    def test_missing_progress_file_is_not_an_error(self):
        with tempfile.TemporaryDirectory() as out:
            self.assertEqual(devices.load_progress(out), {})

    def test_summary_counts_previous_runs_too(self):
        log = FakeLog()
        with tempfile.TemporaryDirectory() as out:
            devices.save_device(out, device_item('a', [flight(1, 'n')]), log)
            _p, first = devices.build_summary(out, log)
            devices.save_device(out, device_item('b', [flight(2, 'n')]), log)
            _p, second = devices.build_summary(out, log)
        self.assertEqual(first['flights_total'], 1)
        self.assertEqual(second['flights_total'], 2)

    def test_start_retry_is_configured(self):
        self.assertGreater(devices.START_ATTEMPTS, 1)
        self.assertGreaterEqual(devices.START_RETRY_PAUSE_MS, 30000)
        self.assertGreater(devices.DEVICE_GAP_MS, 0)

    def test_restart_flag_exists(self):
        parser = devices.build_parser()
        args = parser.parse_args(['--from', '2025-09-01', '--to', '2025-09-30',
                                  '--out', 'x'])
        self.assertFalse(args.restart)
        args = parser.parse_args(['--from', '2025-09-01', '--to', '2025-09-30',
                                  '--out', 'x', '--restart'])
        self.assertTrue(args.restart)


class ClockSkew(unittest.TestCase):
    """7. `code-408` -- «плохая отметка времени», и её можно ИЗМЕРИТЬ."""

    import datetime as _dt

    NOW = _dt.datetime(2026, 8, 14, 6, 0, 0, tzinfo=_dt.timezone.utc)

    def test_clock_in_agreement(self):
        skew = devices.clock_skew_seconds('Fri, 14 Aug 2026 06:00:00 GMT',
                                          self.NOW)
        self.assertEqual(skew, 0.0)

    def test_machine_ahead_is_positive(self):
        skew = devices.clock_skew_seconds('Fri, 14 Aug 2026 05:58:00 GMT',
                                          self.NOW)
        self.assertEqual(skew, 120.0)
        self.assertGreater(abs(skew), devices.CLOCK_SKEW_WARN_SECONDS)

    def test_machine_behind_is_negative(self):
        skew = devices.clock_skew_seconds('Fri, 14 Aug 2026 06:05:00 GMT',
                                          self.NOW)
        self.assertEqual(skew, -300.0)

    def test_unparsable_header_is_not_a_zero_skew(self):
        """Отрицательный контроль: «не разобрано» и «совпало» -- разные вещи.

        Вернуть 0 на мусорный заголовок значило бы доложить, что часы в
        порядке, ровно тогда, когда о них ничего не известно.
        """
        for bad in (None, '', 'не дата', 'Fri, 32 Aug 2026 06:00:00 GMT'):
            self.assertIsNone(devices.clock_skew_seconds(bad, self.NOW),
                              'на %r ожидался None' % (bad,))

    def test_header_without_timezone_is_treated_as_utc(self):
        skew = devices.clock_skew_seconds('14 Aug 2026 06:00:00', self.NOW)
        self.assertEqual(skew, 0.0)

    def test_threshold_is_tight_enough_to_matter(self):
        self.assertLessEqual(devices.CLOCK_SKEW_WARN_SECONDS, 60)
