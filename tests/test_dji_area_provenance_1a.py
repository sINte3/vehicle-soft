# -*- coding: utf-8 -*-
"""DRONE-AREA-1A: скаляры списка читаются от неизменяемого источника (D1).

Смысл всей модели доказательств -- считать от захешированной ревизии.
``drone_flights.raw_json`` -- изменяемая колонка приложения: её переписывает
любой повторный приём списка, и она не покрыта ни одним SHA. До impl-3
``pipeline.load_flights`` разбирал именно её, хотя разобранные из ревизии
значения уже лежали в ``dji_flight_evidence``.

Каждая проверка здесь построена как РАСХОЖДЕНИЕ: `raw_json` и evidence несут
РАЗНЫЕ числа, поэтому тест способен различить верный и неверный код. Рядом --
отрицательный контроль на запасной путь, чтобы правка не превратилась в
«evidence всегда, даже когда её нет».

Stdlib sqlite3, без Flask и без приложения: пакет ``dji_area`` от Flask не
зависит, и этот набор на том стоит.
"""

import json
import os
import re
import sqlite3
import sys
import unittest
from datetime import date

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from dji_area import pipeline as pl  # noqa: E402

MIGRATION = os.path.join(REPO_ROOT, 'migrate_dji_area_evidence_001.py')

FLIGHT = 900101
HW = 'SYNTHETIC-HW-NOT-REAL'
START_TS = 1785526013

# Значения, различающиеся НАМЕРЕННО: слева -- то, что записано в изменяемой
# колонке, справа -- то, что разобрано из захешированной ревизии.
# ВСЕ шесть скаляров расходятся. Если совпадает хоть один, проверка по нему
# проходит при любой реализации и создаёт ложное покрытие.
RAW_JSON_AREA, EVIDENCE_AREA = 11111.0, 8386.0
RAW_JSON_WIDTH, EVIDENCE_WIDTH = 9.99, 6.35
RAW_JSON_MODE, EVIDENCE_MODE = 1, 4
RAW_JSON_MANUAL, EVIDENCE_MANUAL = True, 0
RAW_JSON_START, EVIDENCE_START = START_TS + 777, START_TS
RAW_JSON_END, EVIDENCE_END = START_TS + 999, START_TS + 300


def _evidence_ddl():
    """DDL таблицы улик берётся из САМОЙ миграции, а не переписывается сюда."""
    with open(MIGRATION, encoding='utf-8') as fh:
        text = fh.read()
    found = re.search(
        r'CREATE TABLE IF NOT EXISTS dji_flight_evidence \(.*?\n    \)',
        text, re.S)
    if not found:
        raise AssertionError('DDL dji_flight_evidence не найден в миграции')
    return found.group(0)


def build_db(with_list_revision, revision_without_values=False,
             partial=False):
    """База в памяти с одной записью. Ревизия списка есть или её нет."""
    con = sqlite3.connect(':memory:')
    con.row_factory = sqlite3.Row
    con.execute('CREATE TABLE drone_units (id INTEGER PRIMARY KEY, '
                'hardware_id TEXT)')
    con.execute('CREATE TABLE drone_flights (id INTEGER PRIMARY KEY, '
                'dji_flight_id BIGINT, started_at TEXT, finished_at TEXT, '
                'raw_json TEXT, drone_unit_id INTEGER, nickname_raw TEXT)')
    con.execute(_evidence_ddl())
    con.execute('INSERT INTO drone_units (id, hardware_id) VALUES (1, ?)', (HW,))
    con.execute(
        'INSERT INTO drone_flights (id, dji_flight_id, started_at, '
        'finished_at, raw_json, drone_unit_id, nickname_raw) '
        'VALUES (1, ?, ?, ?, ?, 1, ?)',
        (FLIGHT, '2026-08-18 00:53:00', '2026-08-18 00:58:00',
         json.dumps({'id': FLIGHT, 'hardware_id': HW,
                     'new_work_area': RAW_JSON_AREA,
                     'spray_width': RAW_JSON_WIDTH,
                     'mode_name': RAW_JSON_MODE,
                     'manual_mode': RAW_JSON_MANUAL,
                     'start_timestamp': RAW_JSON_START,
                     'end_timestamp': RAW_JSON_END,
                     'nickname': 'SYNTHETIC-NICK'}),
         'SYNTHETIC-NICK'))
    if revision_without_values:
        # Ревизия названа, но тело не прочиталось: store.py:297-303 оставляет
        # id и не заполняет ни один скаляр.
        con.execute(
            'INSERT INTO dji_flight_evidence (flight_id, provider_account_id, '
            'hardware_id, hardware_id_source, list_revision_id, updated_at) '
            'VALUES (?, ?, ?, ?, ?, ?)',
            (FLIGHT, 'acct', HW, 'card', 77, '2026-09-09 00:00:00'))
        con.commit()
        return con
    if partial:
        # [REASON]: ревизия дала ЧАСТЬ скаляров. `all(...)` отправил бы такую
        # строку на изменяемый raw_json, потеряв те значения, которые от
        # захешированного источника всё-таки пришли.
        con.execute(
            'INSERT INTO dji_flight_evidence (flight_id, provider_account_id, '
            'hardware_id, hardware_id_source, list_revision_id, '
            'list_raw_area_m2, list_spray_width, updated_at) '
            'VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
            (FLIGHT, 'acct', HW, 'card', 77, EVIDENCE_AREA, EVIDENCE_WIDTH,
             '2026-09-09 00:00:00'))
        con.commit()
        return con
    con.execute(
        'INSERT INTO dji_flight_evidence (flight_id, provider_account_id, '
        'hardware_id, hardware_id_source, list_revision_id, list_raw_area_m2, '
        'list_start_ts, list_end_ts, list_mode_name, list_manual_mode, '
        'list_spray_width, updated_at) '
        'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
        (FLIGHT, 'acct', HW, 'card', 77 if with_list_revision else None,
         EVIDENCE_AREA if with_list_revision else None,
         EVIDENCE_START if with_list_revision else None,
         EVIDENCE_END if with_list_revision else None,
         EVIDENCE_MODE if with_list_revision else None,
         EVIDENCE_MANUAL if with_list_revision else None,
         EVIDENCE_WIDTH if with_list_revision else None,
         '2026-09-09 00:00:00'))
    con.commit()
    return con


def only_item(con):
    items = pl.load_flights(con, date(2026, 8, 18), date(2026, 8, 18))
    hit = [i for i in items if i['flight_id'] == FLIGHT]
    assert len(hit) == 1, 'ожидалась ровно одна запись, получено %d' % len(hit)
    return hit[0]


class ListScalarsComeFromTheHashedRevision(unittest.TestCase):

    def test_evidence_wins_over_mutable_raw_json_for_every_scalar(self):
        item = only_item(build_db(with_list_revision=True))
        self.assertEqual(item['list_value_source'], pl.LIST_FROM_REVISION)
        for key, from_revision, from_raw_json in (
                ('raw_area_m2', EVIDENCE_AREA, RAW_JSON_AREA),
                ('spray_width', EVIDENCE_WIDTH, RAW_JSON_WIDTH),
                ('mode_name', EVIDENCE_MODE, RAW_JSON_MODE),
                ('manual_mode', EVIDENCE_MANUAL, RAW_JSON_MANUAL),
                ('list_start_ts', EVIDENCE_START, RAW_JSON_START),
                ('list_end_ts', EVIDENCE_END, RAW_JSON_END)):
            self.assertEqual(item[key], from_revision, key)
            self.assertNotEqual(item[key], from_raw_json, key)

    def test_a_partially_parsed_revision_still_beats_raw_json(self):
        item = only_item(build_db(with_list_revision=True, partial=True))
        self.assertEqual(item['list_value_source'], pl.LIST_FROM_REVISION)
        # То, что ревизия дала, побеждает.
        self.assertEqual(item['raw_area_m2'], EVIDENCE_AREA)
        self.assertEqual(item['spray_width'], EVIDENCE_WIDTH)
        self.assertNotEqual(item['raw_area_m2'], RAW_JSON_AREA)
        # Чего не дала -- остаётся NULL, а не подставляется из raw_json.
        self.assertIsNone(item['mode_name'])
        self.assertIsNone(item['list_start_ts'])

    def test_a_named_revision_without_values_is_its_own_state(self):
        # НЕ «есть ревизия» и НЕ «нет ревизии». Иначе строка, у которой тело
        # не прочиталось, была бы помечена как посчитанная от захешированного
        # источника, не получив от него ни одного значения.
        item = only_item(build_db(with_list_revision=True,
                                  revision_without_values=True))
        self.assertEqual(item['list_value_source'], pl.LIST_REVISION_UNPARSED)
        self.assertIsNone(item['raw_area_m2'])
        self.assertIsNone(item['spray_width'])

    def test_control_without_a_revision_the_fallback_is_used_and_declared(self):
        # Отрицательный контроль: правка не имеет права выключить запасной
        # путь для записей, у которых ревизии списка действительно нет.
        item = only_item(build_db(with_list_revision=False))
        self.assertEqual(item['raw_area_m2'], RAW_JSON_AREA)
        self.assertEqual(item['spray_width'], RAW_JSON_WIDTH)
        self.assertEqual(item['list_value_source'],
                         pl.LIST_FROM_MUTABLE_RAW_JSON)

    def test_the_two_sources_are_distinguishable_at_all(self):
        # Без этого две проверки выше прошли бы и на совпадающих числах.
        self.assertNotEqual(RAW_JSON_AREA, EVIDENCE_AREA)
        self.assertNotEqual(RAW_JSON_WIDTH, EVIDENCE_WIDTH)


if __name__ == '__main__':
    unittest.main()
