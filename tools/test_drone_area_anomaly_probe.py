# -*- coding: utf-8 -*-
"""Тесты tools/drone_area_anomaly_probe.py. Без сети и без реальной базы.

    python tools/test_drone_area_anomaly_probe.py

Восемь обязательных проверок задания A2 §6 — каждая отдельным тестом, с
именем, называющим проверяемое свойство:

1. SHA-256 копии базы до и после анализа совпадает.
2. База открывается через read-only URI (и запись через это соединение
   отвергается самим SQLite).
3. В исходнике скрипта нет пишущих SQL-команд.
4. Отрицательный контроль: синтетика без аномалий даёт ноль кандидатов.
5. Положительный контроль: синтетика с повтором площади даёт кандидата.
6. NULL, отсутствие ключа, -1 и 0 для ширины различаются по отдельности.
7. Повторный запуск даёт тот же результат.
8. Ошибка разбора одной строки не прекращает анализ и попадает в отчёт.

Синтетическая база строится тем же DDL, что стоит в
migrate_drones_foundation_001.py, а не тем, что удобно тесту: проверка на
схеме, отличной от production, проверяет не то.
"""

import contextlib
import datetime
import io
import json
import os
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.drone_area_anomaly_probe import (  # noqa: E402
    DEFAULT_LOOKBACK, QUALITY_CODES, ProbeError, analyse, check_known_case,
    connect_read_only, main, sha256_of)

# Точная копия DDL из migrate_drones_foundation_001.py.
DDL_FLIGHTS = """
    CREATE TABLE drone_flights (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        dji_flight_id BIGINT NOT NULL UNIQUE,
        drone_unit_id INTEGER REFERENCES drone_units (id),
        nickname_raw  VARCHAR(100),
        serial_number VARCHAR(50),
        flyer_name    VARCHAR(100),
        team_name     VARCHAR(100),
        started_at    DATETIME NOT NULL,
        finished_at   DATETIME,
        work_seconds  INTEGER,
        area_ha       FLOAT NOT NULL DEFAULT 0,
        spray_liters  FLOAT,
        sow_kg        FLOAT,
        usage_type    INTEGER,
        mode_name     INTEGER,
        manual_mode   BOOLEAN,
        work_speed    FLOAT,
        spray_width   FLOAT,
        radar_height  FLOAT,
        lat           FLOAT,
        lng           FLOAT,
        location_text VARCHAR(500),
        region        VARCHAR(100),
        raw_json      TEXT NOT NULL,
        sync_log_id   INTEGER,
        ingested_at   DATETIME NOT NULL
    )
"""

DDL_UNITS = """
    CREATE TABLE drone_units (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        number      INTEGER NOT NULL UNIQUE,
        hardware_id VARCHAR(50)
    )
"""

BASE = datetime.datetime(2026, 6, 5, 14, 0, 0)


def flight(dji_id, minutes, area_m2, width=5.95, unit_id=1,
           nickname='8 GardenU', duration=500, raw_override=None,
           broken_json=False, area_ha_override=None, finished_delta=None):
    """Одна строка вылета в форме, пригодной для INSERT."""
    started = BASE + datetime.timedelta(minutes=minutes)
    finished = started + datetime.timedelta(
        seconds=duration if finished_delta is None else finished_delta)
    payload = {
        'id': dji_id,
        'nickname': nickname,
        'serial_number': 'R%010d' % dji_id,
        'new_work_area': area_m2,
        'start_timestamp': int(started.timestamp()),
        'end_timestamp': int(finished.timestamp()),
        'work_time_seconds': duration,
    }
    if width is not Ellipsis:
        payload['spray_width'] = width
    if raw_override is not None:
        payload.update(raw_override)
    raw = 'НЕ JSON {{{' if broken_json else json.dumps(payload,
                                                       ensure_ascii=False)
    area_ha = (area_m2 / 10000.0 if area_ha_override is None
               else area_ha_override)
    return (dji_id, unit_id, nickname, 'R%010d' % dji_id,
            started.strftime('%Y-%m-%d %H:%M:%S'),
            finished.strftime('%Y-%m-%d %H:%M:%S'),
            duration, area_ha,
            None if width is Ellipsis else width,
            raw, BASE.strftime('%Y-%m-%d %H:%M:%S'))


def build_db(path, rows):
    con = sqlite3.connect(path)
    con.execute(DDL_FLIGHTS)
    con.execute(DDL_UNITS)
    con.execute('INSERT INTO drone_units (id, number, hardware_id) '
                "VALUES (1, 8, 'FIXTURE0000000000000')")
    con.executemany(
        'INSERT INTO drone_flights (dji_flight_id, drone_unit_id, '
        ' nickname_raw, serial_number, started_at, finished_at, work_seconds, '
        ' area_ha, spray_width, raw_json, ingested_at) '
        'VALUES (?,?,?,?,?,?,?,?,?,?,?)', rows)
    con.commit()
    con.close()


class ProbeCase(unittest.TestCase):
    """Общая мастерская: собрать базу, прогнать анализ, вернуть результат."""

    def run_probe(self, rows):
        self.directory = tempfile.mkdtemp()
        path = os.path.join(self.directory, 'copy.db')
        build_db(path, rows)
        before = sha256_of(path)
        con = connect_read_only(path)
        try:
            result = analyse(con)
        finally:
            con.close()
        after = sha256_of(path)
        return path, result, before, after


# ─── 1 и 7: неизменность файла и повторяемость ───────────────────────────────

class TestReadOnlyGuarantee(ProbeCase):

    def test_1_sha256_before_and_after_match(self):
        _path, _result, before, after = self.run_probe([
            flight(1, 0, 14520.0), flight(2, 10, 14906.0)])
        self.assertEqual(before, after)

    def test_2_the_connection_really_is_read_only(self):
        """Не «мы открыли с mode=ro», а «запись через него отвергается».

        Проверка, которая только читает URI, дала бы одинаковый результат при
        верном и неверном коде. Здесь SQLite сам обязан отказать.
        """
        path, _result, _b, _a = self.run_probe([flight(1, 0, 14520.0)])
        con = connect_read_only(path)
        try:
            with self.assertRaises(sqlite3.OperationalError):
                con.execute('DELETE FROM drone_flights')
        finally:
            con.close()

    def test_2b_a_missing_database_is_refused_not_created(self):
        directory = tempfile.mkdtemp()
        missing = os.path.join(directory, 'nope.db')
        with self.assertRaises(ProbeError):
            connect_read_only(missing)
        self.assertFalse(os.path.exists(missing),
                         'the probe must never create a database')

    def test_7_a_second_run_gives_the_same_answer(self):
        rows = [flight(1, 0, 14520.0), flight(2, 10, 14520.0),
                flight(3, 20, 9193.0)]
        path, first, _b, _a = self.run_probe(rows)
        con = connect_read_only(path)
        try:
            second = analyse(con)
        finally:
            con.close()
        self.assertEqual(first['repeats']['count'], second['repeats']['count'])
        self.assertEqual(first['widths']['states'], second['widths']['states'])
        self.assertAlmostEqual(first['total_area_ha'], second['total_area_ha'])


class TestSourceHasNoWrites(unittest.TestCase):

    def test_3_no_writing_sql_in_the_probe_source(self):
        here = os.path.dirname(os.path.abspath(__file__))
        with open(os.path.join(here, 'drone_area_anomaly_probe.py'),
                  encoding='utf-8') as handle:
            source = handle.read()
        # Комментарии и докстринги упоминают запись словами; ищем SQL.
        for statement in ('INSERT INTO', 'UPDATE ', 'DELETE FROM',
                          'DROP TABLE', 'ALTER TABLE', 'CREATE TABLE',
                          'REPLACE INTO'):
            self.assertNotIn(statement, source,
                             'writing SQL found in the probe: %s' % statement)


# ─── 4 и 5: контроли на повтор площади ───────────────────────────────────────

class TestRepeatDetection(ProbeCase):

    def test_4_negative_control_no_anomalies_means_zero_candidates(self):
        """Все площади разные -- кандидатов быть не должно ни одного."""
        _p, result, _b, _a = self.run_probe([
            flight(1, 0, 14520.0), flight(2, 10, 14906.0),
            flight(3, 20, 14713.0), flight(4, 30, 15366.0)])
        self.assertEqual(result['repeats']['count'], 0)
        self.assertEqual(result['repeats']['area_ha'], 0.0)

    def test_5_positive_control_a_repeat_is_found(self):
        """Тот же тест на данных, где повтор есть.

        Без этой пары предыдущий тест ничего не значит: ноль кандидатов
        выдал бы и сломанный детектор.
        """
        _p, result, _b, _a = self.run_probe([
            flight(1, 0, 14520.0), flight(2, 10, 5940.0),
            flight(3, 20, 5940.0)])
        self.assertEqual(result['repeats']['count'], 1)
        candidate = result['repeats']['candidates'][0]
        self.assertEqual(candidate['dji_flight_id'], 3)
        self.assertEqual(candidate['previous_dji_flight_id'], 2)
        self.assertAlmostEqual(candidate['area_ha'], 0.594)

    def test_5b_the_known_case_is_recognised_at_lag_two(self):
        """Настоящая форма случая 622715275 из DISCOVERY §6.2.

        Порядок вылетов 05.06.2026 у машины №8 именно такой: между двумя
        вылетами по 5940.0000297 стоит 622715274 на 3293.3333 в ручном
        режиме. Значит 622715275 повторяет площадь вылета ЧЕРЕЗ ОДИН, и
        правило «равна предыдущему» его НЕ ловит -- это и проверяется здесь
        вместе со следующим тестом.
        """
        _p, result, _b, _a = self.run_probe([
            flight(622715273, 0, 5940.000029700001, width=5.91, duration=362),
            flight(622715274, 10, 3293.3333498, width=Ellipsis, duration=90),
            flight(622715275, 20, 5940.000029700001, width=Ellipsis,
                   duration=40)])
        known = check_known_case(result)
        self.assertTrue(known['found_as_candidate'],
                        'the known case must be found with the default window')
        self.assertEqual(known['detail']['lag'], 2)
        self.assertTrue(known['detail']['short_flight'])

    def test_5b2_a_one_step_window_would_miss_the_known_case(self):
        """Отрицательный контроль к предыдущему тесту.

        Он же -- доказательство, что расхождение с формулировкой задания
        реально, а не придумано: с окном в один шаг кандидатов ноль.
        """
        directory = tempfile.mkdtemp()
        path = os.path.join(directory, 'copy.db')
        build_db(path, [
            flight(622715273, 0, 5940.000029700001, width=5.91, duration=362),
            flight(622715274, 10, 3293.3333498, width=Ellipsis, duration=90),
            flight(622715275, 20, 5940.000029700001, width=Ellipsis,
                   duration=40)])
        con = connect_read_only(path)
        try:
            narrow = analyse(con, lookback=1)
        finally:
            con.close()
        self.assertEqual(narrow['repeats']['count'], 0)
        self.assertFalse(check_known_case(narrow)['found_as_candidate'])

    def test_5f_lag_one_and_lag_two_are_counted_apart(self):
        _p, result, _b, _a = self.run_probe([
            flight(1, 0, 5940.0), flight(2, 10, 5940.0),
            flight(3, 20, 1000.0), flight(4, 30, 1000.0),
            flight(5, 40, 7000.0), flight(6, 50, 3000.0),
            flight(7, 60, 7000.0)])
        repeats = result['repeats']
        self.assertEqual(repeats['lag1_count'], 2)
        self.assertEqual(repeats['lag2plus_count'], 1)
        self.assertEqual(repeats['count'], 3)

    def test_5c_a_zero_area_repeat_is_not_a_candidate(self):
        """Нулевая площадь совпадает у соседей сплошь и рядом."""
        _p, result, _b, _a = self.run_probe([
            flight(1, 0, 0.0), flight(2, 10, 0.0)])
        self.assertEqual(result['repeats']['count'], 0)

    def test_5d_a_repeat_across_different_machines_is_not_a_candidate(self):
        """Сравнение идёт внутри одной машины, а не по всей базе."""
        _p, result, _b, _a = self.run_probe([
            flight(1, 0, 5940.0, unit_id=1, nickname='8 GardenU'),
            flight(2, 10, 5940.0, unit_id=2, nickname='9 Garden')])
        self.assertEqual(result['repeats']['count'], 0)

    def test_5e_a_run_of_three_is_counted_as_a_run(self):
        _p, result, _b, _a = self.run_probe([
            flight(1, 0, 5940.0), flight(2, 10, 5940.0),
            flight(3, 20, 5940.0)])
        self.assertEqual(result['repeats']['count'], 2)
        self.assertEqual(result['repeats']['runs_3plus_count'], 1)


# ─── 6: четыре состояния ширины по отдельности ───────────────────────────────

class TestWidthStates(ProbeCase):

    def test_6_null_missing_minus_one_and_zero_are_told_apart(self):
        _p, result, _b, _a = self.run_probe([
            flight(1, 0, 14520.0, width=5.95),      # PRESENT
            flight(2, 10, 14520.0, width=None),     # JSON_NULL
            flight(3, 20, 14520.0, width=Ellipsis),  # MISSING_KEY
            flight(4, 30, 14520.0, width=-1.0),     # MINUS_ONE
            flight(5, 40, 14520.0, width=0.0),      # ZERO
        ])
        states = result['widths']['states']
        self.assertEqual(states.get('PRESENT'), 1)
        self.assertEqual(states.get('JSON_NULL'), 1)
        self.assertEqual(states.get('MISSING_KEY'), 1)
        self.assertEqual(states.get('MINUS_ONE'), 1)
        self.assertEqual(states.get('ZERO'), 1)

    def test_6b_only_the_present_state_counts_as_usable(self):
        _p, result, _b, _a = self.run_probe([
            flight(1, 0, 10000.0, width=6.0),
            flight(2, 10, 10000.0, width=-1.0),
            flight(3, 20, 10000.0, width=0.0),
        ])
        self.assertEqual(result['width_usable_flights'], 1)
        self.assertAlmostEqual(result['width_usable_share'], 1 / 3.0)
        self.assertAlmostEqual(result['width_usable_area_ha'], 1.0)

    def test_6c_no_width_is_never_substituted(self):
        """Отрицательный контроль на запрет подстановки.

        Гектары вылета без ширины обязаны остаться в «недоступных», а не
        перетечь в пригодные из-за соседнего значения того же дня.
        """
        _p, result, _b, _a = self.run_probe([
            flight(1, 0, 10000.0, width=5.95),
            flight(2, 10, 20000.0, width=-1.0),
        ])
        self.assertAlmostEqual(result['width_usable_area_ha'], 1.0)
        self.assertAlmostEqual(
            result['total_area_ha'] - result['width_usable_area_ha'], 2.0)


# ─── 8: битая строка не роняет анализ ────────────────────────────────────────

class TestBrokenRowSurvives(ProbeCase):

    def test_8_a_broken_raw_json_is_counted_not_fatal(self):
        _p, result, _b, _a = self.run_probe([
            flight(1, 0, 14520.0),
            flight(2, 10, 14906.0, broken_json=True),
            flight(3, 20, 14713.0)])
        self.assertEqual(result['flights_total'], 3)
        self.assertEqual(result['quality']['issues'].get('raw_json не разобрался'), 1)

    def test_8b_the_area_of_a_broken_row_falls_back_to_the_column(self):
        _p, result, _b, _a = self.run_probe([
            flight(1, 0, 14520.0, broken_json=True)])
        self.assertAlmostEqual(result['total_area_ha'], 1.452, places=3)

    def test_8c_a_column_json_area_mismatch_is_reported(self):
        _p, result, _b, _a = self.run_probe([
            flight(1, 0, 14520.0, area_ha_override=9.99)])
        self.assertEqual(
            result['quality']['issues'].get(
                'площадь в колонке и в raw_json расходятся'), 1)

    def test_8d_a_clean_database_reports_no_quality_issues(self):
        """Отрицательный контроль к трём тестам выше."""
        _p, result, _b, _a = self.run_probe([
            flight(1, 0, 14520.0), flight(2, 10, 14906.0)])
        self.assertEqual(result['quality']['issues'], {})


# ─── Схема и идентификатор борта ─────────────────────────────────────────────

class TestSchemaAndIdentity(ProbeCase):

    def test_a_missing_required_column_stops_the_run(self):
        directory = tempfile.mkdtemp()
        path = os.path.join(directory, 'thin.db')
        con = sqlite3.connect(path)
        con.execute('CREATE TABLE drone_flights (id INTEGER PRIMARY KEY, '
                    'dji_flight_id BIGINT)')
        con.commit()
        con.close()
        con = connect_read_only(path)
        try:
            with self.assertRaises(ProbeError):
                analyse(con)
        finally:
            con.close()

    def test_serial_number_is_measured_as_a_flight_id(self):
        """Факт трека перепроверяется на данных, а не пересказывается."""
        _p, result, _b, _a = self.run_probe([
            flight(1, 0, 14520.0), flight(2, 10, 14906.0),
            flight(3, 20, 14713.0)])
        identity = result['identity']
        self.assertEqual(identity['distinct_serial_number'],
                         identity['flights_total'])

    def test_hardware_id_is_absent_from_the_flight_payload(self):
        """Ключевое ограничение группировки: борта на строке вылета нет."""
        _p, result, _b, _a = self.run_probe([flight(1, 0, 14520.0)])
        self.assertEqual(result['identity']['hardware_id_in_raw_json'], 0)
        self.assertFalse(result['identity']['hardware_id_on_flight_row'])

    def test_unresolved_flights_are_not_lumped_together(self):
        """Вылеты без машины группируются по нику, а не в одну кучу."""
        _p, result, _b, _a = self.run_probe([
            flight(1, 0, 5940.0, unit_id=None, nickname='неизвестный A'),
            flight(2, 10, 5940.0, unit_id=None, nickname='неизвестный Б')])
        self.assertEqual(result['repeats']['count'], 0,
                         'two different unknown spellings are not one machine')
        self.assertEqual(result['identity']['drone_unit_id_null'], 2)


# ─── Отчёт целиком ───────────────────────────────────────────────────────────

class TestReportIsWritable(ProbeCase):
    """Прогон main() до файла.

    [REASON]: владелец запустит это один раз на копии базы. Падение в
    построении xlsx обнаружилось бы у него и стоило бы круга переписки, а
    здесь ловится бесплатно. Тест проверяет и то, что каждое состояние
    ширины и хотя бы один кандидат доходят до листов.
    """

    def test_main_writes_all_seven_sheets(self):
        from openpyxl import load_workbook
        directory = tempfile.mkdtemp()
        path = os.path.join(directory, 'copy.db')
        build_db(path, [
            flight(622715273, 0, 5940.000029700001, width=5.91, duration=362),
            flight(622715274, 10, 3293.3333498, width=Ellipsis, duration=90),
            flight(622715275, 20, 5940.000029700001, width=Ellipsis,
                   duration=40),
            flight(700000001, 60, 14520.0, width=5.95),
            flight(700000002, 80, 9193.0, width=0.0),
            flight(700000003, 100, 12000.0, width=None),
            flight(700000004, 120, 11000.0, broken_json=True),
            flight(700000005, 140, 1000.0, unit_id=None, nickname='неизвестный'),
        ])
        out = os.path.join(directory, 'A2.xlsx')
        before = sha256_of(path)
        with contextlib.redirect_stdout(io.StringIO()):
            code = main(['--db', path, '--out', out])
        self.assertEqual(code, 0)
        self.assertEqual(sha256_of(path), before)
        book = load_workbook(out)
        self.assertEqual(book.sheetnames,
                         ['Сводка', 'По месяцам', 'По дронам',
                          'Повторы площади', 'Нет ширины', 'Качество данных',
                          'Методика'])
        # Кандидат дошёл до листа: шапка плюс минимум одна строка.
        self.assertGreater(book['Повторы площади'].max_row, 1)
        self.assertGreater(book['Нет ширины'].max_row, 1)
        self.assertGreater(book['Качество данных'].max_row, 1)

    def test_main_refuses_a_missing_database(self):
        directory = tempfile.mkdtemp()
        missing = os.path.join(directory, 'nope.db')
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(['--db', missing]), 2)
        self.assertFalse(os.path.exists(missing))


class TestQualityCodesAreComplete(ProbeCase):

    def test_every_issue_kind_has_an_ascii_code(self):
        """Иначе консоль напечатает OTHER и проблема потеряет имя."""
        _p, result, _b, _a = self.run_probe([
            flight(1, 0, 14520.0),
            flight(2, 10, 14906.0, broken_json=True),
            flight(3, 20, 14713.0, area_ha_override=9.99),
            flight(4, 30, 1000.0, unit_id=None, nickname='X'),
            flight(5, 40, -5.0),
            flight(6, 50, 1000.0, duration=0, finished_delta=0),
        ])
        self.assertTrue(result['quality']['issues'])
        for kind in result['quality']['issues']:
            self.assertIn(kind, QUALITY_CODES,
                          'issue kind without an ASCII code: %s' % kind)


if __name__ == '__main__':
    unittest.main()
