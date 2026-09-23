# -*- coding: utf-8 -*-
"""DRONE-AREA-CONTROL-V2-MEGA, блок C -- общий фильтр «дата+время» модуля.

Юнит-тесты `drone_period.py`: только stdlib, без Flask и без базы. Модуль
импортируют и страницы, и выгрузки, поэтому семантика периода проверяется
здесь один раз, а на маршрутах -- в tests/test_drone_period_filters.py.

Что держится:

  * по умолчанию 00:00 и 23:59, то есть целые дни;
  * точные границы минуты: конец ПОЛУОТКРЫТЫЙ -- 23:59:41 остаётся, 00:00:00
    следующего дня уходит; начало включительное -- 00:00:00 остаётся,
    23:59:59 предыдущего дня уходит;
  * UTC+5 -> UTC: хранение в UTC, границы сдвигаются на пять часов;
  * интервал через полночь (20:00 первого дня .. 06:00 второго);
  * начало позже конца -- флаг и сообщение, а не исключение;
  * неверное время -- значение по умолчанию и предупреждение;
  * link_args / echo / filename_part -- «по умолчанию» не оставляет следа;
  * словарь без ключей времени (так их собирают тесты и старые вызовы)
    читается как «целые дни».

Отрицательный контроль (CLAUDE.md: проверка, дающая одинаковый результат
при верном и неверном коде, проверкой не является): закрытая граница
«<= 23:59:00» на тех же моментах теряет 23:59:41 -- тест показывает, что
данные способны различить две реализации.

Run:
  python -m unittest tests.test_drone_period -v
"""
import os
import re
import sys
import unittest
from datetime import date, datetime, time, timedelta

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

import drone_period as dp  # noqa: E402

LATIN = re.compile(r'[A-Za-z]')


def local(y, m, d, hh=0, mm=0, ss=0):
    """Местный момент UTC+5 -> момент хранения (UTC)."""
    return datetime(y, m, d, hh, mm, ss) - timedelta(hours=5)


def period(**kwargs):
    """parse() над обычным словарём -- так же, как над request.args."""
    default_window = kwargs.pop('default_window', None)
    with_time = kwargs.pop('with_time', True)
    return dp.parse(kwargs, default_window=default_window,
                    with_time=with_time)


class DefaultsTest(unittest.TestCase):

    def test_default_time_is_whole_day(self):
        f = period(date_from='2026-06-15', date_to='2026-06-15')
        self.assertEqual(f['time_from'], time(0, 0))
        self.assertEqual(f['time_to'], time(23, 59))
        self.assertEqual(f['time_from_s'], '00:00')
        self.assertEqual(f['time_to_s'], '23:59')
        self.assertTrue(f['time_is_default'])
        self.assertEqual(f['period_warnings'], [])
        self.assertFalse(f['period_inverted'])
        self.assertEqual(f['start_local'], datetime(2026, 6, 15, 0, 0))
        self.assertEqual(f['end_local'], datetime(2026, 6, 15, 23, 59))

    def test_empty_time_keys_are_the_default_not_an_error(self):
        f = period(date_from='2026-06-15', date_to='2026-06-15',
                   time_from='', time_to='  ')
        self.assertTrue(f['time_is_default'])
        self.assertEqual(f['period_warnings'], [])

    def test_absent_date_keys_take_the_default_window(self):
        window = (date(2026, 6, 1), date(2026, 6, 30))
        f = period(default_window=window)
        self.assertEqual((f['date_from'], f['date_to']), window)
        self.assertEqual((f['date_from_s'], f['date_to_s']),
                         ('2026-06-01', '2026-06-30'))
        self.assertFalse(f['has_date_args'])

    def test_present_but_empty_date_is_no_bound(self):
        """Пустой ключ -- осознанное «без границы», окно не подставляется."""
        f = period(date_from='', date_to='',
                   default_window=(date(2026, 6, 1), date(2026, 6, 30)))
        self.assertIsNone(f['date_from'])
        self.assertIsNone(f['date_to'])
        self.assertTrue(f['has_date_args'])
        self.assertEqual(dp.utc_bounds(f), (None, None))

    def test_malformed_date_is_no_bound_and_echoed_raw(self):
        f = period(date_from='15.06.2026', date_to='2026-06-15')
        self.assertIsNone(f['date_from'])
        self.assertEqual(f['date_from_s'], '15.06.2026')
        start, end = dp.utc_bounds(f)
        self.assertIsNone(start)
        self.assertEqual(end, datetime(2026, 6, 15, 19, 0))


class BoundaryTest(unittest.TestCase):
    """Точные границы минуты на одном местном дне 2026-06-15."""

    def setUp(self):
        self.f = period(date_from='2026-06-15', date_to='2026-06-15')

    def test_end_is_half_open_and_keeps_the_seconds_of_2359(self):
        self.assertTrue(dp.contains_utc(self.f, local(2026, 6, 15, 23, 59, 41)))
        self.assertTrue(dp.contains_utc(self.f, local(2026, 6, 15, 23, 59, 59)))
        self.assertFalse(dp.contains_utc(self.f, local(2026, 6, 16, 0, 0, 0)))

    def test_start_is_inclusive_and_drops_the_previous_day(self):
        self.assertTrue(dp.contains_utc(self.f, local(2026, 6, 15, 0, 0, 0)))
        self.assertFalse(dp.contains_utc(self.f, local(2026, 6, 14, 23, 59, 59)))

    def test_minute_precise_end_keeps_its_last_minute(self):
        f = period(date_from='2026-06-15', date_to='2026-06-15',
                   time_from='08:00', time_to='10:30')
        self.assertTrue(dp.contains_utc(f, local(2026, 6, 15, 8, 0, 0)))
        self.assertFalse(dp.contains_utc(f, local(2026, 6, 15, 7, 59, 59)))
        self.assertTrue(dp.contains_utc(f, local(2026, 6, 15, 10, 30, 41)))
        self.assertFalse(dp.contains_utc(f, local(2026, 6, 15, 10, 31, 0)))

    def test_negative_control_a_closed_minute_bound_loses_2359_41(self):
        """Данные различают полуоткрытую и закрытую границу.

        Закрытое «<= 23:59:00» -- очевидная неверная реализация «по 23:59».
        На том же моменте она даёт другой ответ; если бы не давала, тесты
        выше ничего бы не доказывали.
        """
        moment = local(2026, 6, 15, 23, 59, 41)
        closed_end = dp.to_utc(self.f['end_local'])       # 23:59:00 local
        self.assertFalse(moment <= closed_end)            # неверная: теряет
        self.assertTrue(dp.contains_utc(self.f, moment))  # верная: держит

    def test_none_is_never_inside(self):
        self.assertFalse(dp.contains_utc(self.f, None))


class TimezoneTest(unittest.TestCase):

    def test_utc_plus_5_to_utc(self):
        f = period(date_from='2026-06-15', date_to='2026-06-15',
                   time_from='02:00', time_to='04:59')
        start, end_excl = dp.utc_bounds(f)
        # 02:00 местного -- это 21:00 UTC ПРЕДЫДУЩЕГО дня.
        self.assertEqual(start, datetime(2026, 6, 14, 21, 0))
        # 04:59 + 1 минута = 05:00 местного = 00:00 UTC того же дня.
        self.assertEqual(end_excl, datetime(2026, 6, 15, 0, 0))
        self.assertEqual(f['utc_start'], start)
        self.assertEqual(f['utc_end_excl'], end_excl)

    def test_whole_month_bounds(self):
        f = period(date_from='2026-06-01', date_to='2026-06-30')
        self.assertEqual(dp.utc_bounds(f), (datetime(2026, 5, 31, 19, 0),
                                            datetime(2026, 6, 30, 19, 0)))

    def test_to_local_inverts_to_utc(self):
        moment = datetime(2026, 6, 15, 13, 45, 12)
        self.assertEqual(dp.to_local(dp.to_utc(moment)), moment)
        self.assertIsNone(dp.to_utc(None))
        self.assertIsNone(dp.to_local(None))

    def test_sql_text_is_19_characters_without_fraction(self):
        text = dp.sql_text(datetime(2026, 6, 14, 19, 0, 0, 123456))
        self.assertEqual(text, '2026-06-14 19:00:00')
        self.assertEqual(len(text), 19)
        self.assertIsNone(dp.sql_text(None))


class CrossDayTest(unittest.TestCase):
    """20:00 первого дня .. 06:00 второго -- ночная смена через полночь."""

    def setUp(self):
        self.f = period(date_from='2026-06-15', date_to='2026-06-16',
                        time_from='20:00', time_to='06:00')

    def test_not_inverted(self):
        self.assertFalse(self.f['period_inverted'])
        self.assertEqual(dp.messages(self.f, 'ru'), [])

    def test_bounds(self):
        self.assertEqual(dp.utc_bounds(self.f),
                         (datetime(2026, 6, 15, 15, 0),
                          datetime(2026, 6, 16, 1, 1)))

    def test_membership_across_midnight(self):
        inside = (local(2026, 6, 15, 20, 0, 0), local(2026, 6, 15, 23, 59, 41),
                  local(2026, 6, 16, 0, 0, 0), local(2026, 6, 16, 5, 45),
                  local(2026, 6, 16, 6, 0, 59))
        outside = (local(2026, 6, 15, 19, 59, 59), local(2026, 6, 16, 6, 1, 0),
                   local(2026, 6, 15, 12, 0))
        for moment in inside:
            self.assertTrue(dp.contains_utc(self.f, moment), moment)
        for moment in outside:
            self.assertFalse(dp.contains_utc(self.f, moment), moment)


class InvertedTest(unittest.TestCase):

    def test_dates_inverted(self):
        f = period(date_from='2026-06-16', date_to='2026-06-15')
        self.assertTrue(f['period_inverted'])
        self.assertFalse(dp.contains_utc(f, local(2026, 6, 15, 12, 0)))
        self.assertFalse(dp.contains_utc(f, local(2026, 6, 16, 12, 0)))

    def test_times_inverted_on_one_day(self):
        f = period(date_from='2026-06-15', date_to='2026-06-15',
                   time_from='10:00', time_to='09:59')
        self.assertTrue(f['period_inverted'])

    def test_one_minute_window_is_not_inverted(self):
        """10:00 .. 10:00 -- это минута [10:00, 10:01), а не пустота."""
        f = period(date_from='2026-06-15', date_to='2026-06-15',
                   time_from='10:00', time_to='10:00')
        self.assertFalse(f['period_inverted'])
        self.assertTrue(dp.contains_utc(f, local(2026, 6, 15, 10, 0, 59)))
        self.assertFalse(dp.contains_utc(f, local(2026, 6, 15, 10, 1, 0)))

    def test_message_in_both_languages(self):
        f = period(date_from='2026-06-16', date_to='2026-06-15')
        ru = dp.messages(f, 'ru')
        uz = dp.messages(f, 'uz')
        self.assertEqual(len(ru), 1)
        self.assertEqual(len(uz), 1)
        self.assertIn('Начало периода позже его конца', ru[0])
        self.assertIn('Давр бошланиши тугашидан кейин', uz[0])

    def test_open_ended_period_is_never_inverted(self):
        self.assertFalse(period(date_from='2026-06-16')['period_inverted'])
        self.assertFalse(period(date_to='2026-06-15')['period_inverted'])


class InvalidTimeTest(unittest.TestCase):

    def test_invalid_time_falls_back_to_default_with_a_warning(self):
        for bad in ('25:00', '12:60', 'abc', '7:05', '12-30', '24:00'):
            f = period(date_from='2026-06-15', date_to='2026-06-15',
                       time_from=bad, time_to=bad)
            self.assertEqual(f['time_from'], time(0, 0), bad)
            self.assertEqual(f['time_to'], time(23, 59), bad)
            self.assertEqual(f['period_warnings'], ['time_from', 'time_to'],
                             bad)
            ru = dp.messages(f, 'ru')
            self.assertEqual(len(ru), 2, bad)
            self.assertIn('00:00', ru[0])
            self.assertIn('23:59', ru[1])

    def test_seconds_are_accepted_and_dropped(self):
        f = period(date_from='2026-06-15', date_to='2026-06-15',
                   time_from='07:05:30')
        self.assertEqual(f['time_from'], time(7, 5))
        self.assertEqual(f['period_warnings'], [])

    def test_uzbek_messages_are_cyrillic(self):
        """CLAUDE.md: узбекский только кириллицей."""
        for key, (ru, uz) in dp.MESSAGES.items():
            self.assertIsNone(LATIN.search(uz), key)
            self.assertNotEqual(ru, uz, key)

    def test_with_time_false_ignores_time_entirely(self):
        f = period(date_from='2026-06-15', date_to='2026-06-15',
                   time_from='25:00', time_to='10:00', with_time=False)
        self.assertTrue(f['time_is_default'])
        self.assertEqual(f['period_warnings'], [])
        self.assertEqual(dp.link_args(f), {})
        self.assertEqual(dp.filename_part(f), '')


class LinkEchoFilenameTest(unittest.TestCase):

    def test_default_time_leaves_no_trace(self):
        f = period(date_from='2026-06-15', date_to='2026-06-16')
        self.assertEqual(dp.link_args(f), {})
        self.assertEqual(dp.filename_part(f), '')
        self.assertEqual(dp.echo(f), ('2026-06-15 00:00', '2026-06-16 23:59'))

    def test_non_default_time_travels(self):
        f = period(date_from='2026-06-15', date_to='2026-06-16',
                   time_from='20:00', time_to='06:00')
        self.assertEqual(dp.link_args(f),
                         {'time_from': '20:00', 'time_to': '06:00'})
        self.assertEqual(dp.filename_part(f), '_2000-0600')
        self.assertEqual(dp.echo(f), ('2026-06-15 20:00', '2026-06-16 06:00'))

    def test_only_the_changed_side_travels(self):
        f = period(date_from='2026-06-15', date_to='2026-06-15',
                   time_to='18:00')
        self.assertEqual(dp.link_args(f), {'time_to': '18:00'})
        self.assertEqual(dp.filename_part(f), '_0000-1800')

    def test_a_time_without_its_date_is_not_applied_and_said(self):
        f = period(date_from='', date_to='', time_to='10:00')
        self.assertIn(dp.WARN_TIME_TO_UNANCHORED, f['period_warnings'])
        self.assertEqual(dp.filename_part(f), '')
        self.assertIn('у конца периода нет даты', ' '.join(
            dp.messages(f, 'ru')))
        g = period(date_from='', date_to='2026-06-15', time_from='08:00')
        self.assertEqual(g['period_warnings'],
                         [dp.WARN_TIME_FROM_UNANCHORED])
        self.assertEqual(dp.filename_part(g), '')
        # Отрицательный контроль: с датой время применяется молча.
        h = period(date_from='2026-06-15', date_to='2026-06-15',
                   time_to='10:00')
        self.assertEqual(h['period_warnings'], [])
        self.assertEqual(dp.filename_part(h), '_0000-1000')

    def test_no_date_echoes_empty(self):
        self.assertEqual(dp.echo(period()), ('', ''))

    def test_label(self):
        self.assertEqual(dp.label(period(), 'ru'), 'за всё время')
        self.assertEqual(dp.label(period(), 'uz'), 'бутун давр учун')
        f = period(date_from='2026-06-15', date_to='2026-06-16',
                   time_from='20:00', time_to='06:00')
        self.assertEqual(dp.label(f, 'ru'), '15.06.2026 20:00 — 16.06.2026 06:00')


class PlainDictTest(unittest.TestCase):
    """Словарь без ключей времени -- как его собирают тесты и старые вызовы."""

    PLAIN = {'date_from': date(2026, 6, 1), 'date_to': date(2026, 6, 30),
             'unit_id': None, 'region': ''}

    def test_utc_bounds_of_a_plain_dict_are_whole_days(self):
        self.assertEqual(dp.utc_bounds(self.PLAIN),
                         (datetime(2026, 5, 31, 19, 0),
                          datetime(2026, 6, 30, 19, 0)))

    def test_plain_dict_has_no_time_trace(self):
        self.assertEqual(dp.link_args(self.PLAIN), {})
        self.assertEqual(dp.filename_part(self.PLAIN), '')
        self.assertEqual(dp.messages(self.PLAIN, 'ru'), [])

    def test_plain_dict_equals_the_parsed_whole_day_period(self):
        parsed = period(date_from='2026-06-01', date_to='2026-06-30')
        self.assertEqual(dp.utc_bounds(parsed), dp.utc_bounds(self.PLAIN))

    def test_plain_dict_without_dates_is_unbounded(self):
        self.assertEqual(dp.utc_bounds({'date_from': None, 'date_to': None}),
                         (None, None))
        self.assertEqual(dp.utc_bounds({}), (None, None))


if __name__ == '__main__':
    unittest.main()
