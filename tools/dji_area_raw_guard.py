# -*- coding: utf-8 -*-
"""tools/dji_area_raw_guard.py -- доказать, что цикл не тронул RAW DJI.

DJI-AREA-PRODUCTIONIZATION-001. Главный инвариант слоя площади:
`drone_flights.area_ha` -- неизменяемый RAW DJI, а `billable_area_m2` остаётся
NULL. На синтетике это держат тесты. Этот инструмент доказывает то же на
НАСТОЯЩЕЙ базе вокруг настоящего прогона: снимок до, сверка после.

  --save FILE     записать снимок: `dji_flight_id -> area_ha` всех вылетов;
  --compare FILE  сверить базу со снимком.

[REASON]: сверяются только вылеты, которые были в снимке. Ежедневный цикл
законно ДОБАВЛЯЕТ вылеты (шаг FLIGHTS), поэтому хеш всей таблицы изменился бы
и при верной работе -- такая проверка давала бы один ответ на верный и на
неверный код. Новые идентификаторы считаются и печатаются, но нарушением не
являются; нарушение -- изменившаяся или пропавшая площадь уже известного вылета
и любая ненулевая `billable_area_m2`.

[REASON]: значения сравниваются как ТЕКСТ, которым SQLite отдаёт число
(`repr`), а не с допуском. RAW не пересчитывается никем, значит обязан совпасть
до последнего бита; допуск спрятал бы ровно ту правку, которую ищем.

Только чтение: база открывается `mode=ro`. К DJI инструмент не обращается.

Запуск (рабочий каталог -- корень репозитория):

  & "C:\\Program Files\\Python314\\python.exe" tools\\dji_area_raw_guard.py --db instance\\transport.db --save C:\\VehicleSoft_Area_Staging\\raw_before.json
  & "C:\\Program Files\\Python314\\python.exe" tools\\dji_area_raw_guard.py --db instance\\transport.db --compare C:\\VehicleSoft_Area_Staging\\raw_before.json

Коды возврата: 0 -- снимок записан / RAW не тронут; 1 -- ошибка аргументов или
снимка; 2 -- база не найдена (файл НЕ создаётся); 3 -- RAW изменён либо
`billable_area_m2` не NULL. Вывод в консоль только ASCII.

Откат кода: удалить файл, его никто не импортирует. Данных не пишет вовсе.
"""

import argparse
import io
import json
import os
import sqlite3
import sys

GUARD_ID = 'DJI_AREA_RAW_GUARD_001'

EXIT_OK = 0
EXIT_USAGE = 1
EXIT_NO_DATABASE = 2
EXIT_VIOLATED = 3

SHOW_AT_MOST = 10


def connect_read_only(path):
    return sqlite3.connect('file:%s?mode=ro' % os.path.abspath(path).replace(
        '\\', '/'), uri=True, timeout=30)


def _has_table(con, name):
    return con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND "
                       "name=?", (name,)).fetchone() is not None


def read_state(con):
    """(`{dji_flight_id: repr(area_ha)}`, число ненулевых billable)."""
    raw = {}
    for flight_id, area in con.execute(
            'SELECT dji_flight_id, area_ha FROM drone_flights '
            'WHERE dji_flight_id IS NOT NULL'):
        raw[str(flight_id)] = repr(area)
    billable = 0
    if _has_table(con, 'dji_area_calculations'):
        billable = con.execute(
            'SELECT COUNT(*) FROM dji_area_calculations '
            'WHERE billable_area_m2 IS NOT NULL').fetchone()[0]
    return raw, int(billable)


def compare(snapshot, current):
    """Что случилось с вылетами снимка. Новые вылеты -- не нарушение."""
    changed = sorted(fid for fid, area in snapshot.items()
                     if fid in current and current[fid] != area)
    missing = sorted(fid for fid in snapshot if fid not in current)
    new = sum(1 for fid in current if fid not in snapshot)
    return {'kept': len(snapshot) - len(changed) - len(missing),
            'changed': changed, 'missing': missing, 'new': new}


def main(argv=None):
    parser = argparse.ArgumentParser(
        description='Prove that the DJI RAW area was not rewritten.')
    parser.add_argument('--db', dest='db_path', required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--save', dest='save_path', metavar='FILE')
    mode.add_argument('--compare', dest='compare_path', metavar='FILE')
    args = parser.parse_args(argv)

    if not os.path.exists(args.db_path):
        # [REASON]: sqlite3.connect создал бы пустой файл, и снимок «ноль
        # вылетов» потом совпал бы с чем угодно.
        print('ERROR: database not found at %s - refusing to run.'
              % args.db_path)
        return EXIT_NO_DATABASE

    snapshot = None
    if args.compare_path:
        try:
            with io.open(args.compare_path, encoding='utf-8') as handle:
                document = json.load(handle)
            snapshot = document['area_ha']
            if document.get('guard') != GUARD_ID or not isinstance(snapshot,
                                                                   dict):
                raise ValueError('not a %s snapshot' % GUARD_ID)
        except (IOError, OSError, ValueError, KeyError, TypeError) as exc:
            print('ERROR: cannot read the snapshot: %s' % exc)
            return EXIT_USAGE

    try:
        con = connect_read_only(args.db_path)
        try:
            current, billable = read_state(con)
        finally:
            con.close()
    except sqlite3.Error as exc:
        print('ERROR: %s' % exc)
        return EXIT_USAGE

    print(GUARD_ID)
    if args.save_path:
        if not current:
            # Пустой снимок совпадёт с любой базой: это не проверка.
            print('ERROR: the database holds no flight - nothing to guard.')
            return EXIT_USAGE
        folder = os.path.dirname(os.path.abspath(args.save_path))
        os.makedirs(folder, exist_ok=True)
        with io.open(args.save_path, 'w', encoding='utf-8') as handle:
            json.dump({'guard': GUARD_ID, 'area_ha': current,
                       'billable_not_null': billable}, handle,
                      sort_keys=True)
        print('  SNAPSHOT SAVED     : %d flight(s)' % len(current))
        print('  billable not NULL  : %d' % billable)
        return EXIT_OK

    result = compare(snapshot, current)
    print('  flights in snapshot: %d' % len(snapshot))
    print('  RAW kept           : %d' % result['kept'])
    print('  RAW CHANGED        : %d' % len(result['changed']))
    print('  flights missing    : %d' % len(result['missing']))
    print('  new flights        : %d (allowed: the cycle adds flights)'
          % result['new'])
    print('  billable not NULL  : %d' % billable)
    for fid in result['changed'][:SHOW_AT_MOST]:
        print('    changed %s: %s -> %s' % (fid, snapshot[fid], current[fid]))
    for fid in result['missing'][:SHOW_AT_MOST]:
        print('    missing %s' % fid)
    violated = bool(result['changed'] or result['missing'] or billable)
    print('  VERDICT: %s' % ('RAW WAS TOUCHED' if violated
                             else 'RAW UNTOUCHED'))
    return EXIT_VIOLATED if violated else EXIT_OK


if __name__ == '__main__':
    sys.exit(main())
