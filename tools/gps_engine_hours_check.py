# -*- coding: utf-8 -*-
"""GPS-25 -- часы зажигания по правилу R против счётчика моточасов Wialon. Только чтение.

ЗАЧЕМ
Метод «время» сверки заявок считает часы с включённым зажиганием по точкам
(правило R, `gps/ignition.py`). Прежде чем этим часам поверит сверка, их
надо сравнить с независимым счётом. Wialon сам считает моточасы по тому же
датчику зажигания (у 615 объектов из 621 -- биты `cfl` 0x10 и 0x200,
GPS-23), а сборщик с GPS-25 каждым прогоном снимает этот счётчик (`cneh`,
часы) вместе с временем последнего сообщения объекта. Разность двух
соседних снимков -- моточасы Wialon за окно между ними.

ПРЕДРЕГИСТРАЦИЯ (трек GPS, §0.6, 19.10.2026 -- записана до прогона)
Окно -- (t1, t2] между соседними снимками объекта; W = (cneh2 − cneh1) ×
3600 с; O -- «включено» по правилу R на точках окна (пары с t1 <= a.t и
b.t <= t2); U -- «неизвестно».
  Вне счёта, каждое своим числом: у объекта нет битов 0x10 и 0x200
  (`bez_bitov`); W < 0 или W больше длины окна на минуту и более
  (`schetchik_pravili`); у точек окна нет ни одного значения `ign`
  (`net_zazhiganiya`).
  K1 -- из окон с W >= 1800 с не меньше 90 % с |O − W| <= max(0,15 × W, 1800 с);
  K3 -- из окон с W < 60 с не меньше 95 % с O < 1800 с;
  выборка: K1 -- не меньше 200 окон, K3 -- не меньше 50.
  Вердикт последней строкой: MET -- K1 и K3 выполнены; NOT MET -- не выполнен
  любой; NOT EVALUATED -- выборки мало.
  Справочно, не критерий: медиана (O − W) / W и доля окон с O <= W <= O + U.

ЧЕГО НЕ ДЕЛАЕТ
  НИЧЕГО НЕ ПИШЕТ: файл отметок и файлы месяцев открываются `mode=ro`.
  В Wialon не ходит: снимки уже лежат в файле отметок.
  Порог 300 с правила R не подбирает: правило, не прошедшее проверку,
  меняется новой предрегистрацией.
  Имён не печатает и в CSV не пишет: только номер объекта Wialon и числа.

КОДЫ ВЫХОДА
  0  CSV записан, вердикт напечатан (любой);
  2  плохой ввод: нет файла отметок или в нём нет снимков счётчика (сборщик
     без GPS-25), нет папки для CSV, CSV открыт в другой программе, дата
     не YYYY-MM-DD.

ТОЛЬКО STDLIB, системный Python.

Запуск (PowerShell, по одной команде на строку):
  & "C:\\Program Files\\Python314\\python.exe" C:\\gps-tools\\tools\\gps_engine_hours_check.py --dir C:\\transport-report\\instance --out C:\\gps-tools\\check\\engine_hours.csv
  $LASTEXITCODE
"""

import argparse
import csv
import os
import sqlite3
import statistics
import sys
from collections import Counter
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from gps import ignition                                           # noqa: E402
from gps_collector import config, storage                          # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

EXIT_OK = 0
EXIT_BAD_INPUT = 2

# Биты `cfl`: моточасы по датчику зажигания и автоматически по сообщениям.
CFL_IGNITION = 0x10
CFL_AUTO = 0x200

# Числа предрегистрации -- не править без новой записи в треке.
WORK_S = 1800
K1_SHARE = 0.90
K1_TOLERANCE = 0.15
K1_MIN = 200
IDLE_S = 60
K3_SHARE = 0.95
K3_MIN = 50
EDITED_SLACK_S = 60

COLUMNS = ('wialon_id', 'okno_s', 'okno_po', 'dlina_s', 'wialon_s',
           'nashe_vkl_s', 'neizvestno_s', 'vykl_s', 'raznica_s',
           'raznica_dolya', 'v_dopuske', 'klass')

IN_COUNT = 'v_schete'


def local(stamp):
    return datetime.fromtimestamp(int(stamp), config.TZ).strftime(
        '%Y-%m-%d %H:%M:%S')


def read_snapshots(path, since=None):
    """{unit_id: [(last_t, cneh, cfl)]} by time; None if there is no table."""
    con = sqlite3.connect('file:%s?mode=ro' % path, uri=True)
    try:
        present = con.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' "
            "AND name = 'collector_counters'").fetchone()
        if not present:
            return None
        found = {}
        for unit_id, last_t, cneh, cfl in con.execute(
                'SELECT unit_id, last_t, cneh, cfl FROM collector_counters '
                'ORDER BY unit_id, last_t'):
            found.setdefault(int(unit_id), []).append(
                (int(last_t), float(cneh), None if cfl is None else int(cfl)))
    finally:
        con.close()
    if since is not None:
        # [REASON]: окно, начатое до --since, отбрасывается целиком, а не
        # обрезается: обрезанное окно уже не равно разности двух снимков.
        found = {unit: [s for s in shots if s[0] >= since]
                 for unit, shots in found.items()}
    return found


def windows(folder, snapshots):
    """One dict per window between two neighbouring snapshots of an object."""
    out = []
    for unit_id in sorted(snapshots):
        shots = snapshots[unit_id]
        for (t1, cneh1, cfl1), (t2, cneh2, cfl2) in zip(shots, shots[1:]):
            row = {'wialon_id': unit_id, 't1': t1, 't2': t2,
                   'length': t2 - t1, 'wialon': (cneh2 - cneh1) * 3600.0,
                   'on': None, 'unknown': None, 'off': None}
            bits = CFL_IGNITION | CFL_AUTO
            if cfl1 is None or cfl2 is None or (cfl1 & bits) != bits \
                    or (cfl2 & bits) != bits:
                row['class'] = 'bez_bitov'
            elif row['wialon'] < 0 or row['wialon'] > row['length'] + EDITED_SLACK_S:
                row['class'] = 'schetchik_pravili'
            else:
                parts = ignition.split(
                    storage.read_ignition(folder, unit_id, t1, t2), t1, t2)
                row.update(on=parts.on_s, unknown=parts.unknown_s,
                           off=parts.off_s)
                row['class'] = IN_COUNT if parts.with_ign else 'net_zazhiganiya'
            out.append(row)
    return out


def in_tolerance(row):
    return abs(row['on'] - row['wialon']) <= max(K1_TOLERANCE * row['wialon'],
                                                 WORK_S)


def judge(rows):
    """K1, K3 and the verdict over the windows in the count."""
    counted = [r for r in rows if r['class'] == IN_COUNT]
    work = [r for r in counted if r['wialon'] >= WORK_S]
    idle = [r for r in counted if r['wialon'] < IDLE_S]
    k1_hit = sum(1 for r in work if in_tolerance(r))
    k3_hit = sum(1 for r in idle if r['on'] < WORK_S)
    k1 = None if len(work) < K1_MIN else k1_hit / len(work) >= K1_SHARE
    k3 = None if len(idle) < K3_MIN else k3_hit / len(idle) >= K3_SHARE
    if k1 is False or k3 is False:
        verdict = 'NOT MET'
    elif k1 is None or k3 is None:
        verdict = 'NOT EVALUATED'
    else:
        verdict = 'MET'
    relative = [(r['on'] - r['wialon']) / r['wialon'] for r in work]
    bracketed = sum(1 for r in counted
                    if r['on'] <= r['wialon'] <= r['on'] + r['unknown'])
    return {'work': len(work), 'k1_hit': k1_hit, 'k1': k1,
            'idle': len(idle), 'k3_hit': k3_hit, 'k3': k3,
            'verdict': verdict, 'counted': len(counted),
            'median_relative': statistics.median(relative) if relative else None,
            'bracketed': bracketed}


def csv_rows(rows):
    for r in rows:
        counted = r['class'] == IN_COUNT
        diff = r['on'] - r['wialon'] if counted else None
        yield {'wialon_id': r['wialon_id'], 'okno_s': local(r['t1']),
               'okno_po': local(r['t2']), 'dlina_s': r['length'],
               'wialon_s': round(r['wialon'], 1),
               'nashe_vkl_s': '' if r['on'] is None else r['on'],
               'neizvestno_s': '' if r['unknown'] is None else r['unknown'],
               'vykl_s': '' if r['off'] is None else r['off'],
               'raznica_s': '' if diff is None else round(diff, 1),
               'raznica_dolya': (round(diff / r['wialon'], 4)
                                 if counted and r['wialon'] > 0 else ''),
               'v_dopuske': (('da' if in_tolerance(r) else 'net')
                             if counted and r['wialon'] >= WORK_S else ''),
               'klass': r['class']}


def share(hit, total):
    return '%.1f %%' % (100.0 * hit / total) if total else '-'


def report(rows, result, log=print):
    classes = Counter(r['class'] for r in rows)
    log('windows: %d | in the count: %d | no cfl bits: %d | counter edited: %d '
        '| no ignition values: %d'
        % (len(rows), classes[IN_COUNT], classes['bez_bitov'],
           classes['schetchik_pravili'], classes['net_zazhiganiya']))
    log('K1  windows with W >= %d s: %d (need %d), within max(%d %%, %d s): '
        '%d = %s (need %d %%)'
        % (WORK_S, result['work'], K1_MIN, int(K1_TOLERANCE * 100), WORK_S,
           result['k1_hit'], share(result['k1_hit'], result['work']),
           int(K1_SHARE * 100)))
    log('K3  windows with W < %d s: %d (need %d), ours under %d s: %d = %s '
        '(need %d %%)'
        % (IDLE_S, result['idle'], K3_MIN, WORK_S, result['k3_hit'],
           share(result['k3_hit'], result['idle']), int(K3_SHARE * 100)))
    median = result['median_relative']
    log('for reference: median (O - W) / W = %s | O <= W <= O + U in %d of %d'
        % ('-' if median is None else '%+.1f %%' % (100 * median),
           result['bracketed'], result['counted']))
    log('VERDICT: %s' % result['verdict'])


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--dir', default=os.path.join(ROOT, 'instance'),
                        help='folder of the point files and the state file')
    parser.add_argument('--out', required=True, help='CSV to write')
    parser.add_argument('--since', default=None,
                        help='only windows that start on or after this local '
                             'day, YYYY-MM-DD')
    args = parser.parse_args(argv)

    since = None
    if args.since:
        try:
            since = int(datetime.strptime(args.since, '%Y-%m-%d')
                        .replace(tzinfo=config.TZ).timestamp())
        except ValueError:
            sys.stderr.write('ERROR: --since is YYYY-MM-DD\n')
            return EXIT_BAD_INPUT
    state = storage.state_path(args.dir)
    if not os.path.isfile(state):
        sys.stderr.write('ERROR: no state file %s\n' % config.ascii_only(state))
        return EXIT_BAD_INPUT
    out_dir = os.path.dirname(os.path.abspath(args.out))
    if not os.path.isdir(out_dir) or os.path.isdir(args.out):
        sys.stderr.write('ERROR: cannot write %s\n' % config.ascii_only(args.out))
        return EXIT_BAD_INPUT
    snapshots = read_snapshots(state, since)
    if snapshots is None:
        sys.stderr.write('ERROR: no engine-hour counter snapshots in the state '
                         'file -- the collector has not run with GPS-25 yet\n')
        return EXIT_BAD_INPUT
    try:
        # [REASON]: файл, открытый в Excel, -- отказ до расчёта, а не после
        # него: иначе пять минут чтения точек кончились бы ошибкой записи.
        handle = open(args.out, 'w', encoding='utf-8-sig', newline='')
    except OSError:
        sys.stderr.write('ERROR: %s is open in another program\n'
                         % config.ascii_only(args.out))
        return EXIT_BAD_INPUT
    with handle:
        rows = windows(args.dir, snapshots)
        writer = csv.DictWriter(handle, fieldnames=COLUMNS, delimiter=';')
        writer.writeheader()
        writer.writerows(csv_rows(rows))
    result = judge(rows)
    print('objects with snapshots: %d | csv: %s'
          % (len(snapshots), config.ascii_only(args.out)))
    print('READ ONLY: nothing was written to the point files, the state file '
          'or Wialon')
    # Вердикт -- последней строкой (предрегистрация).
    report(rows, result)
    return EXIT_OK


if __name__ == '__main__':
    sys.exit(main())
