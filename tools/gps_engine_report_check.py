# -*- coding: utf-8 -*-
"""GPS-28 -- правила R (часы) и P (пробег) против отчёта Wialon «Моточасы» за прошлые сутки. Только чтение.

ЗАЧЕМ
Метод «время» считает часы с включённым зажиганием правилом R
(`gps/ignition.py`), а пробег грузовика -- правилом P (`gps/mileage.py`).
Прежде чем сверка заявок поверит этим числам, они сравниваются с тем, что
Wialon считает сам: отчёт «Моточасы» (таблица `unit_engine_hours`) отдаёт
моточасы и пробег по суткам за любой день из трёх месяцев, что хранит
Wialon. Зонд GPS-27 показал формат отчёта и что сутки отчёта -- сутки
учётной записи, UTC.

ПРЕДРЕГИСТРАЦИЯ (трек GPS, §0.6, «Дополнение к предрегистрации», 19.10.2026)
  Объекты: считаемые план-фактом с датчиком зажигания и грузовые
  (`yuk_transport`). Период -- сутки 01.09--30.09.2026 (UTC). Сутки в счёте
  -- если у объекта за них хотя бы две точки; нет строки отчёта -- W = 0.
  Наша сторона -- сообщения из Wialon в памяти, только с координатами (как у
  сборщика), окно -- сутки UTC.
  Часы (объекты с датчиком, не исключённые):
    K1 -- из суток с W >= 1800 с не меньше 90 % с |O − W| <= max(0,15 × W, 1800 с);
    K3 -- из суток с W < 60 с не меньше 95 % с O < 1800 с;  выборки 200 и 50.
  Пробег (все объекты выборки):
    KM1 -- из суток с W >= 3 км не меньше 90 % с |O − W| <= max(0,15 × W, 1 км);
    KM3 -- из суток с W < 0,5 км не меньше 95 % с O < 3 км;  выборки 200 и 50.
  Два вердикта последними строками: часы и пробег, каждый MET / NOT MET /
  NOT EVALUATED.

ЧЕГО НЕ ДЕЛАЕТ
  НИЧЕГО НЕ ПИШЕТ В WIALON: шаблон отчёта передаётся в самом запросе и не
      сохраняется; результат отчёта освобождается `report/cleanup_result`,
      сообщения -- `messages/unload`.
  В базу не пишет: `transport.db` открывается `mode=ro` -- только чтобы
      знать, кого план-факт считает и кто грузовой.
  Пороги правил не подбирает.
  Имён не печатает и в CSV не пишет: номер объекта Wialon и числа.

ДОЛГИЙ ПРОГОН
Около 400 объектов, на объект -- несколько запросов сообщений и три запроса
отчёта, пауза 2 с: два-три часа. CSV дописывается после каждого объекта;
прерванный прогон продолжается с --resume: объекты, уже лежащие в CSV, не
спрашиваются, вердикт считается по всему файлу. Не во время ночного сбора
в 01:00.

КОДЫ ВЫХОДА
  0  готово, вердикты напечатаны (любые);
  1  часть объектов не прочитана (перечислены); остальные в CSV -- можно
     продолжить с --resume;
  2  плохой ввод: нет базы, нет папки для CSV, даты, --resume без CSV;
  3  Wialon: вход отклонён, или отчёт на пробных сутках (объект 923,
     15.10.2026) не дал таблицы с моточасами -- шаблон в запросе не принят;
     ничего не проверено, CSV не создан.

ТОЛЬКО STDLIB, системный Python.

Запуск (PowerShell, по одной команде на строку). Из C:\\transport-report --
там лежит wialon_token.txt:
  cd C:\\transport-report
  & "C:\\Program Files\\Python314\\python.exe" C:\\gps-tools\\tools\\gps_engine_report_check.py --db C:\\transport-report\\instance\\transport.db --out C:\\gps-tools\\check\\engine_report_check.csv *> C:\\gps-tools\\check\\engine_report_check.log
  $LASTEXITCODE
"""

import argparse
import csv
import json
import os
import re
import sqlite3
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from gps import ignition, mileage                                  # noqa: E402
from gps.exclusion import excluded_units                           # noqa: E402
from gps_collector import config                                   # noqa: E402
from gps_collector.main import _load_with_backoff                  # noqa: E402
from gps_collector.wialon import (Client, err_code, err_reason,   # noqa: E402
                                  login_failure, positions)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(ROOT, 'instance', 'transport.db')

EXIT_OK = 0
EXIT_PARTIAL = 1
EXIT_BAD_INPUT = 2
EXIT_WIALON = 3

UTC = timezone.utc
TRUCK = 'yuk_transport'
REPORT_RESOURCE = config.ZONE_RESOURCE_ID      # 285, ресурс «Бухоро агрокластер»

# Числа предрегистрации -- не править без новой записи в треке.
DEFAULT_FROM = '2026-09-01'
DEFAULT_TO = '2026-09-30'
WORK_S, IDLE_S, H_TOLERANCE = 1800, 60, 0.15
WORK_KM, IDLE_KM, KM_TOLERANCE, KM_FLOOR = 3.0, 0.5, 0.15, 1.0
SHARE_1, SHARE_3, MIN_1, MIN_3 = 0.90, 0.95, 200, 50

COLUMNS = ('wialon_id', 'den_utc', 'tochek', 'nashe_vkl_s', 'nashe_neizv_s',
           'wialon_motochasy_s', 'nashe_km', 'wialon_km', 'chasy_v_proverke',
           'probeg_v_proverke')

# [REASON]: шаблон в самом запросе -- по образцу шаблона 3 ресурса 285, который
# зонд GPS-27 прочитал 19.10 (таблица `unit_engine_hours`, группировка по
# суткам, флаги таблицы 4369), плюс столбец пробега. Сохранённые шаблоны
# принадлежат людям и меняются без нас; свой -- нет. Столбцы, их подписи и
# параметры -- JSON-списки в строке, как в шаблонах самого сервера: 20.10
# столбцы, переданные строкой через запятую, сервер не разобрал и ответил
# отчётом без единой таблицы.
REPORT_COLUMNS = ('duration', 'mileage')


def _json(value):
    return json.dumps(value, separators=(',', ':'))


REPORT_TEMPLATE = {
    'id': 0, 'n': 'gps-28 check', 'ct': 'avl_unit',
    'p': _json({'bind': {'avl_unit': []}, 'descr': ''}),
    'tbl': [{'n': 'unit_engine_hours', 'l': 'Engine hours',
             'c': _json(list(REPORT_COLUMNS)),
             'cl': _json(['Engine hours', 'Mileage']),
             'cp': _json([{} for _ in REPORT_COLUMNS]),
             's': '', 'sl': '',
             'filter_order': ['base_eh_sensor', 'duration', 'mileage',
                              'engine_hours', 'speed', 'trips', 'stops',
                              'parkings', 'sensors', 'sensor_name', 'driver',
                              'trailer', 'fillings', 'thefts', 'geozones_ex'],
             'p': _json({'grouping': _json({'type': 'day'})}),
             'sch': {'f1': 0, 'f2': 0, 't1': 0, 't2': 0, 'm': 0, 'y': 0,
                     'w': 0, 'fl': 0},
             'f': 4369}]}

# [REASON]: отчёт без таблицы не отличает «шаблон не принят» от «у объекта
# не было моточасов», а 20.10 на этом различии прогон и встал. Поэтому до
# долгого прогона шаблон пробуется на сутках с известным ответом: объект 923,
# местные сутки 15.10.2026 -- собственный шаблон 2 сервера дал на них 4:55:44
# и 104,05 км (зонд GPS-27, 19.10). Wialon хранит сообщения три месяца:
# сутки доступны до середины января 2027.
PROBE_UNIT = 923
PROBE_FROM, PROBE_TO = 1792004400, 1792090799
PROBE_KNOWN = '4:55:44, 104.05 km'

DURATION = re.compile(r'(?:(\d+)\s*\D+?\s+)?(\d+):(\d{2}):(\d{2})\s*$')
NUMBER = re.compile(r'-?\d+(?:[.,]\d+)?')


class ReportRefused(Exception):
    """The report request failed: an error code or rows that did not come."""


class NoTable(ReportRefused):
    """The report came back without an engine-hours table."""


def day_of(stamp):
    return datetime.fromtimestamp(int(stamp), UTC).strftime('%Y-%m-%d')


def day_start(day):
    return int(datetime.strptime(day, '%Y-%m-%d').replace(tzinfo=UTC).timestamp())


def cell_text(value):
    if isinstance(value, dict):
        return str(value.get('t', ''))
    return '' if value is None else str(value)


def parse_duration(value):
    """Seconds of a report duration cell ('4:55:44', '1 d 02:00:00'), or None."""
    match = DURATION.search(cell_text(value).strip())
    if not match:
        return None
    days, hours, minutes, seconds = match.groups()
    return (int(days or 0) * 86400 + int(hours) * 3600 + int(minutes) * 60
            + int(seconds))


def parse_km(value):
    """Kilometres of a report mileage cell ('104.05 km'), or None."""
    match = NUMBER.search(cell_text(value))
    return None if not match else float(match.group(0).replace(',', '.'))


def report_days(client, unit_id, t_from, t_to):
    """{UTC day: (seconds, km)} from Wialon's engine-hours report."""
    try:
        answer = client.call('report/exec_report', {
            'reportResourceId': REPORT_RESOURCE, 'reportTemplateId': 0,
            'reportTemplate': REPORT_TEMPLATE, 'reportObjectId': int(unit_id),
            'reportObjectSecId': 0,
            'interval': {'from': int(t_from), 'to': int(t_to), 'flags': 0}})
        code = err_code(answer)
        if code is not None:
            raise ReportRefused('error %s %s' % (code, err_reason(answer) or ''))
        tables = (answer.get('reportResult') or {}).get('tables') or []
        index = next((i for i, t in enumerate(tables)
                      if t.get('name') == 'unit_engine_hours'), None)
        if index is None:
            raise NoTable('no unit_engine_hours table in the answer')
        count = int(tables[index].get('rows') or 0)
        found = {}
        if count == 0:
            return found
        rows = client.call('report/get_result_rows',
                           {'tableIndex': index, 'indexFrom': 0,
                            'indexTo': count})
        if not isinstance(rows, list):
            raise ReportRefused('rows did not come back as a list')
        for row in rows:
            cells = row.get('c') or []
            if row.get('t1') is None or len(cells) < 2 + len(REPORT_COLUMNS):
                continue
            # [REASON]: первые два столбца результата -- номер и группировка,
            # дальше -- столбцы шаблона в его порядке (зонд GPS-27). Сутки --
            # по началу первого интервала строки, во времени UTC: в нём
            # Wialon и группирует.
            seconds = parse_duration(cells[2])
            km = parse_km(cells[3])
            day = day_of(row['t1'])
            old = found.get(day, (0, 0.0))
            found[day] = (old[0] + (seconds or 0), old[1] + (km or 0.0))
        return found
    finally:
        try:
            client.call('report/cleanup_result', {})
        except Exception:                                          # noqa: BLE001
            pass


def template_works(client):
    """(seconds, km) of the probe day, or ReportRefused: the template must give
    a table with engine hours on a day that had them."""
    found = report_days(client, PROBE_UNIT, PROBE_FROM, PROBE_TO)
    seconds = sum(value[0] for value in found.values())
    if seconds <= 0:
        raise ReportRefused('the table has no engine hours')
    return seconds, sum(value[1] for value in found.values())


def clock(seconds):
    return '%d:%02d:%02d' % (seconds // 3600, seconds % 3600 // 60, seconds % 60)


def load_points(client, unit_id, ignition_param, t_from, t_to, log):
    """{t: (lon, lat, speed, ign)} -- one point per second, as the collector
    keeps them (the first message of a second wins; a later one fills a
    missing ignition). None if the object could not be read."""
    points = {}
    piece_from = t_from
    while piece_from < t_to:
        messages = _load_with_backoff(client, unit_id, piece_from, t_to, log)
        if messages is None:
            return None
        for t, lon, lat, speed, _c, _s, ign in positions(messages, ignition_param):
            if t not in points:
                points[t] = (lon, lat, speed, ign)
            elif points[t][3] is None and ign is not None:
                points[t] = points[t][:3] + (ign,)
        if len(messages) < config.MAX_MESSAGES_PER_INTERVAL:
            break
        reached = max(int(m['t']) for m in messages if m.get('t') is not None)
        piece_from = max(reached, piece_from + 1)
    return points


def unit_rows(unit_id, has_ignition, counted_hours, points, report, days):
    """CSV rows of one object: one per UTC day with at least two points."""
    by_day = {}
    for t in sorted(points):
        by_day.setdefault(day_of(t), []).append(t)
    rows = []
    for day in days:
        stamps = by_day.get(day, [])
        if len(stamps) < 2:
            continue
        lo = day_start(day)
        hi = lo + 86399
        track = [(t, points[t][0], points[t][1], points[t][2]) for t in stamps]
        km = mileage.mileage(track, lo, hi).km
        on = unknown = ''
        if has_ignition:
            split = ignition.split([(t, points[t][3]) for t in stamps], lo, hi)
            on, unknown = split.on_s, split.unknown_s
        w_seconds, w_km = report.get(day, (0, 0.0))
        rows.append({'wialon_id': unit_id, 'den_utc': day, 'tochek': len(stamps),
                     'nashe_vkl_s': on, 'nashe_neizv_s': unknown,
                     'wialon_motochasy_s': w_seconds,
                     'nashe_km': round(km, 3), 'wialon_km': round(w_km, 3),
                     'chasy_v_proverke': 'da' if counted_hours else 'net',
                     'probeg_v_proverke': 'da'})
    return rows


def judge(rows):
    """Both verdicts over the CSV rows. Returns a dict of numbers."""
    hours = [r for r in rows if r['chasy_v_proverke'] == 'da'
             and r['nashe_vkl_s'] not in ('', None)]
    work = [r for r in hours if float(r['wialon_motochasy_s']) >= WORK_S]
    idle = [r for r in hours if float(r['wialon_motochasy_s']) < IDLE_S]
    k1 = sum(1 for r in work if abs(float(r['nashe_vkl_s'])
                                    - float(r['wialon_motochasy_s']))
             <= max(H_TOLERANCE * float(r['wialon_motochasy_s']), WORK_S))
    k3 = sum(1 for r in idle if float(r['nashe_vkl_s']) < WORK_S)
    km = [r for r in rows if r['probeg_v_proverke'] == 'da']
    km_work = [r for r in km if float(r['wialon_km']) >= WORK_KM]
    km_idle = [r for r in km if float(r['wialon_km']) < IDLE_KM]
    km1 = sum(1 for r in km_work
              if abs(float(r['nashe_km']) - float(r['wialon_km']))
              <= max(KM_TOLERANCE * float(r['wialon_km']), KM_FLOOR))
    km3 = sum(1 for r in km_idle if float(r['nashe_km']) < WORK_KM)
    return {'hours': verdict(k1, len(work), k3, len(idle)),
            'mileage': verdict(km1, len(km_work), km3, len(km_idle)),
            'k1': (k1, len(work)), 'k3': (k3, len(idle)),
            'km1': (km1, len(km_work)), 'km3': (km3, len(km_idle)),
            'hour_days': len(hours), 'km_days': len(km)}


def verdict(hit1, n1, hit3, n3):
    first = None if n1 < MIN_1 else hit1 / n1 >= SHARE_1
    third = None if n3 < MIN_3 else hit3 / n3 >= SHARE_3
    if first is False or third is False:
        return 'NOT MET'
    if first is None or third is None:
        return 'NOT EVALUATED'
    return 'MET'


def share(pair):
    hit, total = pair
    return '%d of %d = %s' % (hit, total,
                              '%.1f %%' % (100.0 * hit / total) if total else '-')


def report_lines(result):
    return [
        'hours (objects counted by plan-fact with an ignition sensor): days %d'
        % result['hour_days'],
        'K1  W >= %d s, within max(15 %%, %d s): %s (need 90 %%, %d days)'
        % (WORK_S, WORK_S, share(result['k1']), MIN_1),
        'K3  W < %d s, ours under %d s: %s (need 95 %%, %d days)'
        % (IDLE_S, WORK_S, share(result['k3']), MIN_3),
        'mileage (the same objects and trucks): days %d' % result['km_days'],
        'KM1 W >= 3 km, within max(15 %%, 1 km): %s (need 90 %%, %d days)'
        % (share(result['km1']), MIN_1),
        'KM3 W < 0.5 km, ours under 3 km: %s (need 95 %%, %d days)'
        % (share(result['km3']), MIN_3),
        'VERDICT HOURS: %s' % result['hours'],
        'VERDICT MILEAGE: %s' % result['mileage'],
    ]


def open_readonly(path):
    return sqlite3.connect('file:%s?mode=ro' % path.replace('\\', '/'), uri=True)


def sample(con, units):
    """[(unit_id, ignition param, counted for hours)] of the preregistered set."""
    excluded = excluded_units(con)
    trucks = {int(r[0]) for r in con.execute(
        'SELECT m.wialon_id FROM vialon_mappings m JOIN equipment e ON '
        'e.id = m.equipment_id WHERE m.wialon_id IS NOT NULL AND e.category = ? '
        'AND (m.skip IS NULL OR m.skip = 0)', (TRUCK,))}
    out = []
    for unit in units:
        unit_id, param = unit['id'], unit.get('ignition')
        counted = unit_id not in excluded and bool(param)
        if counted or unit_id in trucks:
            out.append((unit_id, param, counted))
    return out


def read_done(path):
    with open(path, encoding='utf-8-sig', newline='') as handle:
        rows = list(csv.DictReader(handle, delimiter=';'))
    return rows, {int(r['wialon_id']) for r in rows}


def main(argv=None, client_factory=Client):
    parser = argparse.ArgumentParser(
        description=config.ascii_only(__doc__.splitlines()[0]))
    parser.add_argument('--db', default=DB_PATH)
    parser.add_argument('--out', required=True, help='CSV to write')
    parser.add_argument('--from', dest='day_from', default=DEFAULT_FROM)
    parser.add_argument('--to', dest='day_to', default=DEFAULT_TO)
    parser.add_argument('--resume', action='store_true',
                        help='keep the CSV, skip the objects already in it')
    parser.add_argument('--only', action='append', default=[],
                        help='wialon id to check; may repeat')
    parser.add_argument('--pause', type=float, default=config.PAUSE_S)
    args = parser.parse_args(argv)

    try:
        first, last = day_start(args.day_from), day_start(args.day_to)
    except ValueError:
        sys.stderr.write('ERROR: dates are YYYY-MM-DD\n')
        return EXIT_BAD_INPUT
    if last < first:
        sys.stderr.write('ERROR: --from is after --to\n')
        return EXIT_BAD_INPUT
    days = [day_of(first + 86400 * i) for i in range((last - first) // 86400 + 1)]
    t_from, t_to = first, last + 86399
    if not os.path.isfile(args.db):
        sys.stderr.write('ERROR: database not found at %s\n'
                         % config.ascii_only(args.db))
        return EXIT_BAD_INPUT
    if not os.path.isdir(os.path.dirname(os.path.abspath(args.out))) \
            or os.path.isdir(args.out):
        sys.stderr.write('ERROR: cannot write %s\n' % config.ascii_only(args.out))
        return EXIT_BAD_INPUT
    done_rows, done = [], set()
    if args.resume:
        if not os.path.isfile(args.out):
            sys.stderr.write('ERROR: --resume, but there is no %s\n'
                             % config.ascii_only(args.out))
            return EXIT_BAD_INPUT
        done_rows, done = read_done(args.out)

    client = client_factory(pause=args.pause)
    try:
        client.login(config.read_token())
    except Exception as problem:                                   # noqa: BLE001
        sys.stderr.write('\nERROR: ne udalos voyti na %s (%s)\n'
                         % (config.BASE_URL, login_failure(problem)))
        return EXIT_WIALON
    failed, no_table = [], []
    rows_all = list(done_rows)
    try:
        try:
            probe = template_works(client)
        except ReportRefused as problem:
            print('FAILED: the engine-hours report gave no table with engine '
                  'hours on the check object %d, 2026-10-15 (%s) - the template '
                  'in the request was not accepted; nothing was checked'
                  % (PROBE_UNIT, config.ascii_only(str(problem))))
            return EXIT_WIALON
        print('template check on object %d, 2026-10-15: engine hours %s, '
              'mileage %.2f km (the server\'s own template: %s)'
              % (PROBE_UNIT, clock(probe[0]), probe[1], PROBE_KNOWN))
        units = client.list_units()
        con = open_readonly(args.db)
        try:
            chosen = sample(con, units)
        finally:
            con.close()
        wanted = {int(u) for u in args.only} if args.only else None
        todo = [c for c in chosen if c[0] not in done
                and (wanted is None or c[0] in wanted)]
        print('period %s .. %s (UTC days) | objects in the sample %d | already '
              'in the csv %d | to do %d'
              % (days[0], days[-1], len(chosen), len(done), len(todo)))
        mode = 'a' if args.resume else 'w'
        with open(args.out, mode, encoding='utf-8-sig' if mode == 'w' else 'utf-8',
                  newline='') as handle:
            writer = csv.DictWriter(handle, fieldnames=COLUMNS, delimiter=';')
            if mode == 'w':
                writer.writeheader()
            for number, (unit_id, param, counted) in enumerate(todo, 1):
                points = load_points(client, unit_id, param, t_from, t_to, print)
                if points is None:
                    failed.append(unit_id)
                    continue
                try:
                    report = report_days(client, unit_id, t_from, t_to)
                except NoTable:
                    # [REASON]: шаблон проверен на сутках с известным
                    # ответом, значит, отчёт без таблицы -- сутки без
                    # моточасов: по предрегистрации нет строки -- W = 0.
                    # Число таких объектов печатается.
                    report = {}
                    no_table.append(unit_id)
                except ReportRefused:
                    failed.append(unit_id)
                    continue
                rows = unit_rows(unit_id, bool(param), counted, points, report,
                                 days)
                writer.writerows(rows)
                handle.flush()
                rows_all.extend({k: str(v) for k, v in r.items()} for r in rows)
                print('  [%d/%d] %s days %d' % (number, len(todo), unit_id,
                                                len(rows)))
    finally:
        client.logout()
    print('READ ONLY: nothing was written to Wialon or to the database')
    if no_table:
        print('objects whose report had no table, W = 0 on their days: %d'
              % len(no_table))
    if failed:
        print('objects not read (run again with --resume): %s'
              % ', '.join(str(u) for u in failed))
    for line in report_lines(judge(rows_all)):
        print(line)
    return EXIT_PARTIAL if failed else EXIT_OK


if __name__ == '__main__':
    sys.exit(main())
