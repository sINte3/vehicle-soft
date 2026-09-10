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
RAW_JSON_AREA, EVIDENCE_AREA = 11111.0, 8386.0
RAW_JSON_WIDTH, EVIDENCE_WIDTH = 9.99, 6.35


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


def build_db(with_list_revision):
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
                     'spray_width': RAW_JSON_WIDTH, 'mode_name': 4,
                     'manual_mode': False, 'start_timestamp': START_TS,
                     'end_timestamp': START_TS + 300,
                     'nickname': 'SYNTHETIC-NICK'}),
         'SYNTHETIC-NICK'))
    con.execute(
        'INSERT INTO dji_flight_evidence (flight_id, provider_account_id, '
        'hardware_id, hardware_id_source, list_revision_id, list_raw_area_m2, '
        'list_start_ts, list_end_ts, list_mode_name, list_manual_mode, '
        'list_spray_width, updated_at) '
        'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
        (FLIGHT, 'acct', HW, 'card', 77 if with_list_revision else None,
         EVIDENCE_AREA, START_TS, START_TS + 300, 4, 0, EVIDENCE_WIDTH,
         '2026-09-09 00:00:00'))
    con.commit()
    return con


def only_item(con):
    items = pl.load_flights(con, date(2026, 8, 18), date(2026, 8, 18))
    hit = [i for i in items if i['flight_id'] == FLIGHT]
    assert len(hit) == 1, 'ожидалась ровно одна запись, получено %d' % len(hit)
    return hit[0]


class ListScalarsComeFromTheHashedRevision(unittest.TestCase):

    def test_evidence_wins_over_mutable_raw_json(self):
        item = only_item(build_db(with_list_revision=True))
        self.assertEqual(item['raw_area_m2'], EVIDENCE_AREA)
        self.assertEqual(item['spray_width'], EVIDENCE_WIDTH)
        self.assertEqual(item['list_value_source'], pl.LIST_FROM_REVISION)
        # Ровно то значение, которое НЕ должно было победить.
        self.assertNotEqual(item['raw_area_m2'], RAW_JSON_AREA)
        self.assertNotEqual(item['spray_width'], RAW_JSON_WIDTH)

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
