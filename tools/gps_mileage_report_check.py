# -*- coding: utf-8 -*-
"""GPS-32 -- правило P (пробег) у грузовиков против отчёта Wialon «Сводка». Только чтение.

ЗАЧЕМ
Вердикт заявки «время» у грузовика -- по пробегу окна: от 3 км -- «работа
была» (решения владельца 09.10 и 19.10). Пробег считает правило P
(`gps/mileage.py`). Проверка GPS-28 сравнила его со столбцом «Пробег»
отчёта «Моточасы», а это пробег внутри интервалов моточасов: в сутки без
моточасов он 0, как бы машина ни ехала (разбор 20.10). Эталон этой
проверки -- «Пробег по всем сообщениям» отчёта «Сводка» (`unit_generic`).

ПРЕДРЕГИСТРАЦИЯ (трек GPS, §0.6, «Дополнение к предрегистрации», 22.10.2026)
  Объекты: грузовые (`yuk_transport`, не «не наша»). Период -- сутки UTC
  01.08--31.08.2026 и 01.10--21.10.2026 (сентябрь вскрыт разбором 20.10).
  Сутки в счёте -- если у объекта за них хотя бы две точки; нет строки
  отчёта -- W = 0. Наша сторона -- сообщения из Wialon в памяти, только с
  координатами (как у сборщика), окно -- сутки UTC; правило P без изменений.
    B1 -- из суток с W >= 3 км не меньше 90 % с O >= 3 км;
    B3 -- из суток с W < 0,5 км не меньше 95 % с O < 3 км;  выборки 200 и 50.
  Справочно: KM1 предрегистрации 19.10 (|O − W| <= max(15 %, 1 км) из суток
  с W >= 3 км) и «Пробег в поездках» (`mileage`) в CSV.
  Вердикт последней строкой: VERDICT TRUCK MILEAGE: MET / NOT MET /
  NOT EVALUATED.

ЧЕГО НЕ ДЕЛАЕТ
  НИЧЕГО НЕ ПИШЕТ В WIALON: шаблон отчёта передаётся в самом запросе и не
      сохраняется; результат отчёта освобождается `report/cleanup_result`,
      сообщения -- `messages/unload`.
  В базу не пишет: `transport.db` открывается `mode=ro` -- только чтобы
      знать, какие объекты грузовые.
  Пороги правила не подбирает.
  Имён не печатает и в CSV не пишет: номер объекта Wialon и числа.

ПРОГОН
Около 25 объектов, 52 суток, на объект -- сообщения двух периодов и два
отчёта, пауза 2 с: около получаса. CSV дописывается после каждого объекта;
прерванный прогон продолжается с --resume. Не во время ночного сбора в
01:00. Wialon хранит сообщения три месяца: август уйдёт около 01.11,
прогон -- не позже 31.10.2026.

КОДЫ ВЫХОДА
  0  готово, вердикт напечатан (любой);
  1  часть объектов не прочитана (перечислены); остальные в CSV -- можно
     продолжить с --resume;
  2  плохой ввод: нет базы, нет папки для CSV, --resume без CSV;
  3  Wialon: вход отклонён, или «Сводка» на пробных сутках (объект 923,
     15.10.2026) не дала пробега -- шаблон в запросе не принят; ничего не
     проверено, CSV не создан.

ТОЛЬКО STDLIB, системный Python.

Запуск (PowerShell, по одной команде на строку). Из C:\\transport-report --
там лежит wialon_token.txt:
  cd C:\\transport-report
  & "C:\\Program Files\\Python314\\python.exe" C:\\gps-tools\\tools\\gps_mileage_report_check.py --db C:\\transport-report\\instance\\transport.db --out C:\\gps-tools\\check\\mileage_report_check.csv *> C:\\gps-tools\\check\\mileage_report_check.log
  $LASTEXITCODE
"""

import argparse
import csv
import json
import os
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from gps import mileage                                            # noqa: E402
from gps_collector import config                                   # noqa: E402
from gps_collector.wialon import (Client, err_code, err_reason,   # noqa: E402
                                  login_failure)
from tools.gps_engine_report_check import (                       # noqa: E402
    DB_PATH, PROBE_FROM, PROBE_TO, PROBE_UNIT, REPORT_RESOURCE, TRUCK,
    NoTable, ReportRefused, day_of, day_start, load_points, open_readonly,
    parse_km)

EXIT_OK = 0
EXIT_PARTIAL = 1
EXIT_BAD_INPUT = 2
EXIT_WIALON = 3

# [REASON]: числа -- предрегистрация 22.10.2026 (§0.6). Сентябрь вскрыт
# разбором 20.10 и в проверку не входит; август и октябрь до 21.10 разбор
# не видел.
PERIODS = (('2026-08-01', '2026-08-31'), ('2026-10-01', '2026-10-21'))
WORK_KM, IDLE_KM = 3.0, 0.5
SHARE_1, SHARE_3, MIN_1, MIN_3 = 0.90, 0.95, 200, 50
KM_TOLERANCE, KM_FLOOR = 0.15, 1.0                    # KM1, справочно

COLUMNS = ('wialon_id', 'den_utc', 'tochek', 'nashe_km', 'wialon_km',
           'wialon_km_v_poezdkah')

TABLE = 'unit_generic'
# [REASON]: эталон -- «Пробег по всем сообщениям» (`mileage_all`), а не
# «Пробег в поездках» (`mileage`): поездки Wialon режет своим детектором, и
# пробег вне поездки в них не попал бы -- та же ошибка, что у пробега внутри
# моточасов 20.10. «В поездках» идёт в CSV справочно.
REPORT_COLUMNS = ('mileage_all', 'mileage')


def _json(value):
    return json.dumps(value, separators=(',', ':'))


# [REASON]: шаблон в самом запросе -- по образцу шаблона 23 ресурса 285
# (сводка с группировкой по суткам, флаги 4369, тот же порядок фильтров; зонд
# GPS-27, 19.10), без его масок датчиков топлива и моточасов: пробегу они не
# нужны. Сохранённые шаблоны принадлежат людям и меняются без нас. Столбцы,
# подписи и параметры -- JSON-списки в строке, как у сервера: столбцы через
# запятую сервер 20.10 не разобрал (GPS-28).
REPORT_TEMPLATE = {
    'id': 0, 'n': 'gps-32 check', 'ct': 'avl_unit',
    'p': _json({'bind': {'avl_unit': []}, 'descr': ''}),
    'tbl': [{'n': TABLE, 'l': 'Summary',
             'c': _json(list(REPORT_COLUMNS)),
             'cl': _json(['Mileage in all messages', 'Mileage in trips']),
             'cp': _json([{} for _ in REPORT_COLUMNS]),
             's': '', 'sl': '',
             'filter_order': ['base_eh_sensor', 'sensor_name'],
             'p': _json({'grouping': _json({'type': 'day'}),
                         'custom_interval': {'type': 0}}),
             'sch': {'f1': 0, 'f2': 0, 't1': 0, 't2': 0, 'm': 0, 'y': 0,
                     'w': 0, 'fl': 0},
             'f': 4369}]}

# Пробег объекта 923 за 15.10.2026 внутри моточасов -- 104,05 км (шаблон 2
# сервера, зонд GPS-27): пробег по всем сообщениям не меньше.
PROBE_KNOWN_KM = 104.05


def report_days(client, unit_id, t_from, t_to):
    """{UTC day: (km in all messages, km in trips)} from the summary report."""
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
                      if t.get('name') == TABLE), None)
        if index is None:
            raise NoTable('no %s table in the answer' % TABLE)
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
            # дальше -- столбцы шаблона в его порядке (так у «Моточасов»,
            # GPS-27/28). Сутки -- по началу интервала строки, во времени
            # UTC: в нём Wialon и группирует.
            km_all = parse_km(cells[2])
            km_trips = parse_km(cells[3])
            day = day_of(row['t1'])
            old = found.get(day, (0.0, 0.0))
            found[day] = (old[0] + (km_all or 0.0), old[1] + (km_trips or 0.0))
        return found
    finally:
        try:
            client.call('report/cleanup_result', {})
        except Exception:                                          # noqa: BLE001
            pass


def template_works(client):
    """(km in all messages, km in trips) of the probe day, or ReportRefused:
    the template must give mileage on a day the object drove 104 km."""
    found = report_days(client, PROBE_UNIT, PROBE_FROM, PROBE_TO)
    km_all = sum(value[0] for value in found.values())
    if km_all <= 0:
        raise ReportRefused('the summary has no mileage')
    return km_all, sum(value[1] for value in found.values())


def period_days(first, last):
    lo, hi = day_start(first), day_start(last)
    return [day_of(lo + 86400 * i) for i in range((hi - lo) // 86400 + 1)]


def unit_rows(unit_id, points, report, days):
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
        track = [(t, points[t][0], points[t][1], points[t][2]) for t in stamps]
        km = mileage.mileage(track, lo, lo + 86399).km
        w_all, w_trips = report.get(day, (0.0, 0.0))
        rows.append({'wialon_id': unit_id, 'den_utc': day, 'tochek': len(stamps),
                     'nashe_km': round(km, 3), 'wialon_km': round(w_all, 3),
                     'wialon_km_v_poezdkah': round(w_trips, 3)})
    return rows


def judge(rows):
    """The verdict over the CSV rows. Returns a dict of numbers."""
    work = [r for r in rows if float(r['wialon_km']) >= WORK_KM]
    idle = [r for r in rows if float(r['wialon_km']) < IDLE_KM]
    b1 = sum(1 for r in work if float(r['nashe_km']) >= WORK_KM)
    b3 = sum(1 for r in idle if float(r['nashe_km']) < WORK_KM)
    km1 = sum(1 for r in work
              if abs(float(r['nashe_km']) - float(r['wialon_km']))
              <= max(KM_TOLERANCE * float(r['wialon_km']), KM_FLOOR))
    return {'verdict': verdict(b1, len(work), b3, len(idle)),
            'b1': (b1, len(work)), 'b3': (b3, len(idle)),
            'km1': (km1, len(work)), 'days': len(rows),
            'units': len({r['wialon_id'] for r in rows})}


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
        'trucks %d | truck-days %d' % (result['units'], result['days']),
        'B1  W >= 3 km, ours >= 3 km: %s (need 90 %%, %d days)'
        % (share(result['b1']), MIN_1),
        'B3  W < 0.5 km, ours under 3 km: %s (need 95 %%, %d days)'
        % (share(result['b3']), MIN_3),
        'KM1 (reference) W >= 3 km, within max(15 %%, 1 km): %s'
        % share(result['km1']),
        'VERDICT TRUCK MILEAGE: %s' % result['verdict'],
    ]


def trucks(con, units):
    """Wialon ids of the trucks, in the order of the fleet list."""
    found = {int(r[0]) for r in con.execute(
        'SELECT m.wialon_id FROM vialon_mappings m JOIN equipment e ON '
        'e.id = m.equipment_id WHERE m.wialon_id IS NOT NULL AND e.category = ? '
        'AND (m.skip IS NULL OR m.skip = 0)', (TRUCK,))}
    return [unit['id'] for unit in units if unit['id'] in found]


def read_done(path):
    with open(path, encoding='utf-8-sig', newline='') as handle:
        rows = list(csv.DictReader(handle, delimiter=';'))
    return rows, {int(r['wialon_id']) for r in rows}


def main(argv=None, client_factory=Client):
    parser = argparse.ArgumentParser(
        description=config.ascii_only(__doc__.splitlines()[0]))
    parser.add_argument('--db', default=DB_PATH)
    parser.add_argument('--out', required=True, help='CSV to write')
    parser.add_argument('--resume', action='store_true',
                        help='keep the CSV, skip the objects already in it')
    parser.add_argument('--only', action='append', default=[],
                        help='wialon id to check; may repeat')
    parser.add_argument('--pause', type=float, default=config.PAUSE_S)
    args = parser.parse_args(argv)

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
            print('FAILED: the summary report gave no mileage on the check '
                  'object %d, 2026-10-15 (%s) - the template in the request '
                  'was not accepted; nothing was checked'
                  % (PROBE_UNIT, config.ascii_only(str(problem))))
            return EXIT_WIALON
        print('template check on object %d, 2026-10-15: mileage in all messages '
              '%.2f km, in trips %.2f km (inside engine hours, the server\'s '
              'own template: %.2f km)' % (PROBE_UNIT, probe[0], probe[1],
                                          PROBE_KNOWN_KM))
        units = client.list_units()
        con = open_readonly(args.db)
        try:
            chosen = trucks(con, units)
        finally:
            con.close()
        flags = Counter(unit.get('cfl') for unit in units if unit['id'] in chosen)
        print('cfl of the trucks (how Wialon counts): %s' % ', '.join(
            '%s x%d' % (value, n) for value, n in sorted(
                flags.items(), key=lambda item: (item[0] is None, item[0] or 0))))
        wanted = {int(u) for u in args.only} if args.only else None
        todo = [u for u in chosen if u not in done
                and (wanted is None or u in wanted)]
        print('periods %s (UTC days) | trucks %d | already in the csv %d | '
              'to do %d' % (', '.join('%s .. %s' % p for p in PERIODS),
                            len(chosen), len(done), len(todo)))
        mode = 'a' if args.resume else 'w'
        with open(args.out, mode, encoding='utf-8-sig' if mode == 'w' else 'utf-8',
                  newline='') as handle:
            writer = csv.DictWriter(handle, fieldnames=COLUMNS, delimiter=';')
            if mode == 'w':
                writer.writeheader()
            for number, unit_id in enumerate(todo, 1):
                rows = []
                for first, last in PERIODS:
                    t_from, t_to = day_start(first), day_start(last) + 86399
                    points = load_points(client, unit_id, None, t_from, t_to,
                                         print)
                    if points is None:
                        rows = None
                        break
                    try:
                        report = report_days(client, unit_id, t_from, t_to)
                    except NoTable:
                        # [REASON]: шаблон проверен на сутках с известным
                        # пробегом, значит, отчёт без таблицы -- период без
                        # сообщений: по предрегистрации нет строки -- W = 0.
                        report = {}
                        if unit_id not in no_table:
                            no_table.append(unit_id)
                    except ReportRefused:
                        rows = None
                        break
                    rows.extend(unit_rows(unit_id, points, report,
                                          period_days(first, last)))
                if rows is None:
                    # [REASON]: объект пишется в CSV целиком или никак:
                    # --resume пропускает объекты, уже лежащие в файле, и
                    # половина объекта осталась бы без второго периода.
                    failed.append(unit_id)
                    continue
                writer.writerows(rows)
                handle.flush()
                rows_all.extend({k: str(v) for k, v in r.items()} for r in rows)
                print('  [%d/%d] %s days %d' % (number, len(todo), unit_id,
                                                len(rows)))
    finally:
        client.logout()
    print('READ ONLY: nothing was written to Wialon or to the database')
    if no_table:
        print('trucks whose report had no table in a period, W = 0 on its days: '
              '%d' % len(no_table))
    if failed:
        print('objects not read (run again with --resume): %s'
              % ', '.join(str(u) for u in failed))
    for line in report_lines(judge(rows_all)):
        print(line)
    return EXIT_PARTIAL if failed else EXIT_OK


if __name__ == '__main__':
    sys.exit(main())
