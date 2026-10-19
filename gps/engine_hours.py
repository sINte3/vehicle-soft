# -*- coding: utf-8 -*-
"""GPS-26 -- суточные часы зажигания: правило R по суткам в gps_daily_engine. Только stdlib.

Метод «время» сверки заявок (решение владельца 09.10.2026: моточас -- время
с включённым зажиганием по трекеру) берёт часы машины за сутки отсюда. По
каждому объекту и местным суткам -- одна строка таблицы `gps_daily_engine`
(миграция GPS_ENGINE_001): секунды «включено», «выключено» и «неизвестно» по
правилу R (`gps/ignition.py`, предрегистрация в треке GPS 19.10.2026), число
точек суток и сколько из них несли зажигание.

ЧТО ПЕРЕСЧИТЫВАЕТСЯ
Сутки окна -- по умолчанию 30 последних, до вчерашних включительно; или
--from / --to. По каждым суткам одним запросом читается, сколько у каждого
объекта точек и сколько из них с зажиганием. Объект-сутки считаются заново,
если строки нет, если число точек или точек с зажиганием на диске не то,
с которым строка посчитана (поздний сбор, дозаполнение зажигания), или если
версия правила другая. Остальные не трогаются.

[REASON]: сутки, точек которых на диске нет, не пересчитываются никогда --
строка остаётся, как посчитана. Ретенция удаляет файл месяца, а часы, по
которым уже вынесена сверка, должны остаться: тот же урок, что `sbor_nepolnyy`
у ночного расчёта площади (трек GPS, §0.6).

[REASON]: считаются все объекты с точками, без исключений план-факта. Правило
исключений -- про гектары («не наша», «непольевая»); часы нужны и
спецтехнике, и грузовикам с почасовой оплатой, а что из них сверять, решает
сверка.

ЧЕГО НЕ ДЕЛАЕТ
  Файлы точек только читает (`mode=ro`). В `transport.db` пишет одну таблицу
  `gps_daily_engine`; гектары, участки и ответы операторов не трогает.
  Строку не удаляет никогда.

КОДЫ ВЫХОДА
  0  готово;
  2  плохой ввод: нет базы, нет таблицы (миграция GPS_ENGINE_001 не
     применена), даты не YYYY-MM-DD или --from позже --to;
  5  часть объектов-суток не посчиталась (перечислены); остальные записаны.

Запуск (PowerShell; ночью -- третьей строкой gps_daily.bat):
  C:\\gps_venv\\Scripts\\python.exe -m gps.engine_hours >> gps_daily.log 2>&1
После дозаполнения зажигания -- весь период на диске:
  C:\\gps_venv\\Scripts\\python.exe -m gps.engine_hours --from 2026-08-01 --to 2026-10-19
Консоль -- ASCII.
"""

import argparse
import collections
import os
import sqlite3
import sys
from datetime import datetime, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from gps import ignition                                           # noqa: E402
from gps_collector import storage                                  # noqa: E402
from gps_collector.config import TZ, points_dir                    # noqa: E402

DB_PATH = os.path.join(ROOT, 'instance', 'transport.db')
TABLE = 'gps_daily_engine'

# [REASON]: версия -- имя правила, его порог и дата предрегистрации. Другая
# версия в строке -- повод пересчитать сутки: смена правила (новой
# предрегистрацией) обязана дойти до всех суток окна, а не только до новых.
METHOD_VERSION = 'ignition-R%d-2026-10-19' % ignition.MAX_GAP_S
WINDOW_DAYS = 30

EXIT_OK = 0
EXIT_BAD_INPUT = 2
EXIT_PARTIAL = 5

Summary = collections.namedtuple(
    'Summary', 'days objects recomputed unchanged with_ignition failed')


def day_bounds(day):
    """[lo, hi] of a local day, both inclusive: pairs inside the day only."""
    lo = int(datetime.strptime(day, '%Y-%m-%d').replace(tzinfo=TZ).timestamp())
    return lo, lo + 86399


def stored_counts(con, day):
    """{unit_id: (points_total, points_with_ign, method_version)} of a day."""
    return {int(unit): (int(n), int(n_ign), version)
            for unit, n, n_ign, version in con.execute(
                'SELECT wialon_id, points_total, points_with_ign, '
                'method_version FROM %s WHERE work_date = ?' % TABLE, (day,))}


def stale_units(on_disk, stored):
    """Objects of the day whose row is missing or no longer matches the disk."""
    out = []
    for unit_id, (n, n_ign) in sorted(on_disk.items()):
        row = stored.get(unit_id)
        if row is None or row != (n, n_ign, METHOD_VERSION):
            out.append(unit_id)
    return out


def compute(folder, unit_id, day):
    lo, hi = day_bounds(day)
    return ignition.split(storage.read_ignition(folder, unit_id, lo, hi), lo, hi)


def write(con, day, unit_id, parts, computed_at):
    con.execute(
        'INSERT INTO %s (work_date, wialon_id, on_seconds, off_seconds, '
        'unknown_seconds, points_total, points_with_ign, method_version, '
        'computed_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) '
        'ON CONFLICT(work_date, wialon_id) DO UPDATE SET '
        'on_seconds = excluded.on_seconds, off_seconds = excluded.off_seconds, '
        'unknown_seconds = excluded.unknown_seconds, '
        'points_total = excluded.points_total, '
        'points_with_ign = excluded.points_with_ign, '
        'method_version = excluded.method_version, '
        'computed_at = excluded.computed_at' % TABLE,
        (day, int(unit_id), parts.on_s, parts.off_s, parts.unknown_s,
         parts.points, parts.with_ign, METHOD_VERSION, computed_at))


def days_between(first, last):
    current = datetime.strptime(first, '%Y-%m-%d')
    end = datetime.strptime(last, '%Y-%m-%d')
    out = []
    while current <= end:
        out.append(current.strftime('%Y-%m-%d'))
        current += timedelta(days=1)
    return out


def run_days(folder, db_path, days, log=print):
    """Recompute what is stale on the given days. Returns (Summary, failures)."""
    objects = recomputed = unchanged = with_ignition = 0
    failures = []
    computed_at = datetime.now(TZ).strftime('%Y-%m-%d %H:%M:%S')
    con = sqlite3.connect(db_path, timeout=30)
    try:
        for day in days:
            on_disk = storage.day_unit_counts(folder, day)
            if not on_disk:
                continue
            stale = stale_units(on_disk, stored_counts(con, day))
            objects += len(on_disk)
            unchanged += len(on_disk) - len(stale)
            done = 0
            for unit_id in stale:
                try:
                    parts = compute(folder, unit_id, day)
                    write(con, day, unit_id, parts, computed_at)
                except Exception as problem:                       # noqa: BLE001
                    # [REASON]: один сломанный объект-сутки не уносит прогон --
                    # тот же охранник, что у ночного расчёта площади (GPS-13).
                    failures.append((day, unit_id, type(problem).__name__))
                    continue
                done += 1
                with_ignition += parts.with_ign > 0
            con.commit()
            recomputed += done
            if stale:
                log('%s: objects %d, recomputed %d' % (day, len(on_disk), done))
    finally:
        con.close()
    return Summary(days=len(days), objects=objects, recomputed=recomputed,
                   unchanged=unchanged, with_ignition=with_ignition,
                   failed=len(failures)), failures


def has_table(db_path):
    con = sqlite3.connect('file:%s?mode=ro' % db_path, uri=True)
    try:
        return con.execute("SELECT name FROM sqlite_master WHERE type='table' "
                           "AND name=?", (TABLE,)).fetchone() is not None
    finally:
        con.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--from', dest='day_from', default=None,
                        help='first local day, YYYY-MM-DD')
    parser.add_argument('--to', dest='day_to', default=None,
                        help='last local day, YYYY-MM-DD (default: yesterday)')
    parser.add_argument('--dir', default=None,
                        help='where the point files live (default: instance/)')
    parser.add_argument('--db', default=None, help='path to transport.db')
    args = parser.parse_args(argv)

    yesterday = (datetime.now(TZ) - timedelta(days=1)).strftime('%Y-%m-%d')
    last = args.day_to or yesterday
    try:
        first = args.day_from or (datetime.strptime(last, '%Y-%m-%d')
                                  - timedelta(days=WINDOW_DAYS - 1)
                                  ).strftime('%Y-%m-%d')
        days = days_between(first, last)
    except ValueError:
        sys.stderr.write('ERROR: dates are YYYY-MM-DD\n')
        return EXIT_BAD_INPUT
    if not days:
        sys.stderr.write('ERROR: --from is after --to\n')
        return EXIT_BAD_INPUT
    db_path = args.db or DB_PATH
    if not os.path.exists(db_path):
        sys.stderr.write('ERROR: database not found at %s\n' % db_path)
        return EXIT_BAD_INPUT
    if not has_table(db_path):
        sys.stderr.write('ERROR: no table %s -- migration GPS_ENGINE_001 is '
                         'not applied\n' % TABLE)
        return EXIT_BAD_INPUT
    folder = args.dir or points_dir()

    print('engine hours %s -> %s (%d days), rule %s'
          % (days[0], days[-1], len(days), METHOD_VERSION))
    summary, failures = run_days(folder, db_path, days)
    print('object-days with points: %d | recomputed: %d | unchanged: %d | '
          'recomputed with ignition data: %d | failed: %d'
          % (summary.objects, summary.recomputed, summary.unchanged,
             summary.with_ignition, summary.failed))
    for day, unit_id, kind in failures:
        print('  FAILED %s %s: %s' % (day, unit_id, kind))
    return EXIT_PARTIAL if failures else EXIT_OK


if __name__ == '__main__':
    sys.exit(main())
