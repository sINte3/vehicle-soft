# -*- coding: utf-8 -*-
"""GPS A7: «было / стало» предрегистрированного правила допуска. Только чтение.

ЗАЧЕМ
Правило «допуск при переполнении» (docs/GPS_PLAN_FAKT_VISION_ROADMAP.md,
2.11) записано 02.10.2026 до прогона на данных, вместе с условиями приёмки.
Этот инструмент и есть прогон: он считает каждые машино-сутки прежним
методом (`overflow_cap=False`, версия `adaptive-alpha-2026-08-12`) и
правилом (`overflow_cap=True`) на одних и тех же точках и печатает вердикт
по условиям, которые проверяет машина. С 07.10 правило -- действующий метод
(`overflow-cap-2026-10-07`); «было» здесь по-прежнему прежний метод, явно.
Условия, которые решает глаз владельца, он готовит: для условий 3 и 4
выгружает KML, где у каждых суток рядом лежат участки «было» и «стало»
поверх трека; для условия 1 печатает изменившиеся строки с названием контура.

ДВА РЕЖИМА
  --tracks ... --zones ...  наборы с ручными замерами (условие 1). Треки --
      CSV зондов с разделителем «;» и столбцами unit_id, date, time, lat,
      lon, speed (`verify_tracks.csv` -- 27.07, `verify2_tracks.csv` --
      12.08; файл без столбца даты тоже читается); зоны --
      `wialon_zones.json`. Набор 27.07 узнаётся по дате 2026-07-27, набор
      12.08 -- по своим 15 машино-суткам. Каждые машино-сутки считаются по
      суткам (`work_sites`, строка «day») и по каждому контуру, куда машина
      заехала хотя бы 10 точками в движении (`worked_area`, строка «zone»).
      Работа на земле без контура видна только в строке «day». Пятнадцать
      машино-суток 12.08 -- сами работы (`WORK_DAYS_1208`), поэтому их
      изменение -- FAIL без вопроса владельцу; прочие изменившиеся строки
      печатаются с номером и названием контура, и решает книга владельца.
      Оба набора обязательны: без любого из них условие не проверено.
  --db ... --dir ...  production (условия 2, 3, 5 и выгрузка для 3 и 4).
      Опубликованные машино-сутки периода (причина пуста), чьи точки ещё на
      диске. Контроль прогона: каждая строка базы, пересчитанная по точкам
      тем методом, который её записал (по `method_version`), обязана совпасть
      с базой; иначе прогон недействителен. Контроль судит строку по её же
      метке, поэтому непересчитанная строка проходит его как прежний метод;
      что окно пересчитано, говорит соседняя строка -- число строк по версиям
      метода. После пересчёта обе вместе подтверждают: все строки новой
      версии, и каждая из них ровно правило.

ВЕРДИКТЫ И КОД ВЫХОДА
  PASS / FAIL -- условие проверено; NOT CHECKED -- проверять было нечего
  (пустой ввод); RUN INVALID -- контроль прогона не сошёлся, чинить
  инструмент и повторять; NOT EVALUATED -- названные сутки не пересчитаны;
  OWNER CHECK -- нужен глаз владельца. Код выхода: 0 -- всё машинное PASS,
  4 -- есть FAIL, 3 -- FAIL нет, но есть невыполненная проверка или нужен
  владелец, 2 -- неверный ввод (тогда ничего не считалось), 1 -- инструмент
  упал (так Python завершает любое необработанное исключение; о правиле это
  ничего не говорит).

[REASON]: база и файлы точек открываются `mode=ro`, KML пишется только в файл,
указанный ключом. Решения здесь нет: включение правила в ночной расчёт --
отдельный шаг после решения владельца. Одна оговорка про «ничего не пишет»:
SQLite, открывая базу в режиме WAL только на чтение, может создать рядом
пустые служебные файлы `-wal` и `-shm`; данные не меняются.

Запуск -- из окружения расчёта (нужна геометрия), PowerShell, из C:\\gps-tools:

    & C:\\gps_venv\\Scripts\\python.exe -u tools\\gps_alpha_replay.py --tracks C:\\diag\\wialon\\verify_tracks.csv --tracks C:\\diag\\wialon\\verify2_tracks.csv --zones C:\\diag\\wialon\\wialon_zones.json

    & C:\\gps_venv\\Scripts\\python.exe -u tools\\gps_alpha_replay.py --db C:\\transport-report\\instance\\transport.db --dir C:\\transport-report\\instance --since 2026-09-01 --until 2026-09-30 --kml C:\\gps-tools\\check\\a7_replay.kml

Вывод -- только ASCII. `-u` -- чтобы строки хода прогона доходили до файла
сразу, а не по окончании.
"""

import argparse
import csv
import os
import sys
from collections import defaultdict
from datetime import datetime
from xml.sax.saxutils import escape

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from gps.area import (ALPHA_M, ALPHA_SPACING_FACTOR, METHOD_VERSION,  # noqa: E402
                      PREVIOUS_METHOD_VERSION, SPACING_CAP_M, _moving_mask,
                      candidate_contours, pass_spacing,
                      pass_spacing_on_overflow, to_utm, work_sites,
                      worked_area)
from gps.daily import compute_day, load_contours                     # noqa: E402
from tools.gps_day_kml import (STYLES, _coordinates, _polygon_kml,   # noqa: E402
                               polygons_of, track_pieces)
from tools.gps_implausible_sites import kinds_of, read_day_readonly  # noqa: E402
from tools.gps_track_only_days import (console, open_readonly,      # noqa: E402
                                       unit_names)

ALPHA_CEILING_M = ALPHA_SPACING_FACTOR * SPACING_CAP_M      # 53.568
# [REASON]: шесть суток сентября с допуском 300-981 м, названные в условии 3
# предрегистрации (вывод `tools/gps_alpha_report.py` 02.10, строки -- в треке).
# Список закреплён в документе, поэтому и здесь закреплён, а не вычисляется
# заново; тест сверяет его с текстом раздела 2.11.
NAMED_DAYS = (('2026-09-26', 393), ('2026-09-26', 330), ('2026-09-26', 3188),
              ('2026-09-03', 411), ('2026-09-21', 10322), ('2026-09-05', 337))
# [REASON]: пятнадцать работ с ручным замером набора 12.08 -- это машино-сутки
# целиком (`tools/wialon_probe5_spraying.py`, UNIT_DAYS; тест сверяет списки).
# Все участки одних суток получают один шаг и одну альфу, поэтому сработавшее
# или изменившееся правило на таких сутках изменило саму работу, и по
# поправке условия 1 это отказ правилу, а не вопрос к книге владельца.
WORK_DAYS_1208 = (
    (1729, 'MTZ 261 EA', '2026-08-08'), (1729, 'MTZ 261 EA', '2026-08-07'),
    (3100, 'MTZ 25 GA 691 (575 HA)', '2026-08-07'),
    (1729, 'MTZ 261 EA', '2026-08-05'), (1729, 'MTZ 261 EA', '2026-08-01'),
    (1729, 'MTZ 261 EA', '2026-07-31'), (3453, 'MTZ 266 EA', '2026-07-20'),
    (1730, 'MTZ 873 GA', '2026-07-17'), (1730, 'MTZ 873 GA', '2026-07-16'),
    (6824, 'TD5 681 GA (187)', '2026-07-13'),
    (952, 'MTZ-80 X 540 GA', '2026-07-07'), (942, 'MTZ-80 X 514 GA', '2026-07-07'),
    (1730, 'MTZ 873 GA', '2026-07-07'), (1730, 'MTZ 873 GA', '2026-07-04'),
    (1730, 'MTZ 873 GA', '2026-07-01'))
# [REASON]: набор 27.07 -- треки одного дня проверки, 2026-07-27: 7
# тракторов, 17 440 точек (`tools/gps_area_method_repro.py`, раздел 2.2
# дорожной карты); 12.08 -- 27 174 точки (раздел 2.3). Зонды пишут столбец
# даты (`unit_id;date;time;...`), поэтому набор 27.07 узнаётся по дате, а не
# по отсутствию столбца: на этом допущении первая редакция инструмента
# 03.10 ложно сказала «набор 27.07 не прочитан». Файл без столбца даты тоже
# принимается -- тогда весь он один день. Числа точек печатаются рядом с
# записанными, чтобы личность набора была видна в выводе.
WORKS_0727, DAY_0727, TRACTORS_0727, POINTS_0727 = 17, '2026-07-27', 7, 17440
POINTS_1208 = 27174
REQUIRED_COLUMNS = ('unit_id', 'time', 'lat', 'lon', 'speed')
TOP_LOSSES = 10
MIN_MOVING_POINTS_IN_ZONE = 10
# [REASON]: допуск на сложение чисел с плавающей точкой при сравнении сумм
# гектаров и альф. Сами шаг и альфа сравниваются как есть, без допуска.
EPSILON = 1e-9
NEW_STYLE = ('new', '<LineStyle><color>ffff6600</color><width>2</width>'
                    '</LineStyle><PolyStyle><color>55ff6600</color></PolyStyle>')
PASS, FAIL, NOT_CHECKED, RUN_INVALID, NOT_EVALUATED, OWNER_CHECK = (
    'PASS', 'FAIL', 'NOT CHECKED', 'RUN INVALID', 'NOT EVALUATED', 'OWNER CHECK')


def alpha_of(spacing):
    return (max(ALPHA_M, ALPHA_SPACING_FACTOR * spacing)
            if spacing is not None else ALPHA_M)


def measure(track):
    """(шаг сегодня, шаг правила, альфа сегодня, альфа правила) по сырым числам.

    [REASON]: решение «сработало ли правило» и инварианты альфы берутся отсюда,
    а не из строк участков. Строки округлены (шаг до 0,001 м), а у суток без
    участков их нет вовсе -- тогда сработавшее правило выглядело бы
    несработавшим и его альфа не проверялась бы. Точки -- ровно те, что
    `work_sites` отдаёт оценке шага: по времени, в окне рабочей скорости.
    """
    track = sorted(track)
    xs, ys = to_utm([r[1] for r in track], [r[2] for r in track])
    mask = _moving_mask([r[3] for r in track])
    points = [(float(x), float(y)) for x, y, m in zip(xs, ys, mask) if m]
    today = pass_spacing(points)
    capped = pass_spacing_on_overflow(points)
    return today, capped, alpha_of(today), alpha_of(capped)


def verdict_code(verdicts):
    if FAIL in verdicts:
        return 4
    if any(v != PASS for v in verdicts):
        return 3
    return 0


# --- наборы с ручными замерами (условие 1) ------------------------------------

class BadInput(Exception):
    """Файл набора нельзя прочитать как набор -- код выхода 2."""


def load_csv_days(path):
    """((unit_id, дата или '') -> [(t, lon, lat, speed)] по времени, пропущено).

    [REASON]: выгрузка набора пишет пустую ячейку там, где Wialon не дал
    значения (`wialon_probe5_spraying.py`), и одна такая строка не должна
    обрывать прогон всех 32 работ. Строка без времени, координат или
    скорости пропускается -- обоими методами одинаково, сравнения она не
    меняет, -- но число пропущенных печатается, а не теряется молча. Файл
    без нужного столбца -- другое дело: это не набор, а неверный ввод, и
    пропуск всех его строк выдал бы непрочитанный набор за проверенный.
    """
    days, skipped = defaultdict(list), 0
    with open(path, encoding='utf-8-sig', newline='') as handle:
        reader = csv.DictReader(handle, delimiter=';')
        absent = [name for name in REQUIRED_COLUMNS
                  if name not in (reader.fieldnames or [])]
        if absent:
            raise BadInput('%s has no column(s) %s'
                           % (console(os.path.basename(path)), ', '.join(absent)))
        for row in reader:
            try:
                day = (row.get('date') or '').strip()
                hh, mm, ss = row['time'].strip().split(':')
                t = int(hh) * 3600 + int(mm) * 60 + int(ss)
                if day:
                    t += datetime.strptime(day, '%Y-%m-%d').toordinal() * 86400
                point = (t, float(row['lon']), float(row['lat']),
                         float(row['speed']))
                unit = int(row['unit_id'])
            except (AttributeError, KeyError, TypeError, ValueError):
                skipped += 1
                continue
            days[(unit, day)].append(point)
    return {key: sorted(track) for key, track in days.items()}, skipped


def site_list(sites):
    return [(s.contour_id, s.area_ha, s.alpha_used_m, s.pass_spacing_m)
            for s in sites]


def sites_key(sites):
    return [(site.area_ha, site.alpha_used_m, site.pass_spacing_m,
             None if site.polygon is None else site.polygon.wkb)
            for site in sites]


def compare_set(name, days, zones, zone_names):
    """Строки сравнения: по суткам и по каждому контуру с заездом."""
    rows = []
    for (unit, day), track in sorted(days.items()):
        today_s, cap_s, today_a, cap_a = measure(track)
        today, _ = work_sites(track, contours=zones, overflow_cap=False)
        capped, _ = work_sites(track, contours=zones, overflow_cap=True)
        rows.append({'set': name, 'unit': unit, 'day': day or '-',
                     'path': 'day', 'zone': None, 'zone_name': '',
                     'points': len(track),
                     'ha_today': sum(s.area_ha for s in today),
                     'ha_cap': sum(s.area_ha for s in capped),
                     'spacing': today_s, 'spacing_cap': cap_s,
                     'triggered': today_s is not None and today_s > SPACING_CAP_M,
                     'same': sites_key(today) == sites_key(capped),
                     'sites_today': site_list(today),
                     'sites_cap': site_list(capped)})
        for zone_id, _inside in candidate_contours(
                track, zones, min_moving_points=MIN_MOVING_POINTS_IN_ZONE):
            one = worked_area(track, zones[zone_id], zone_id, overflow_cap=False)
            two = worked_area(track, zones[zone_id], zone_id, overflow_cap=True)
            rows.append({'set': name, 'unit': unit, 'day': day or '-',
                         'path': 'zone', 'zone': zone_id,
                         'zone_name': zone_names.get(zone_id, ''),
                         'ha_today': one.area_ha, 'ha_cap': two.area_ha,
                         'spacing': one.pass_spacing_m,
                         'spacing_cap': two.pass_spacing_m,
                         'triggered': (one.pass_spacing_m is not None
                                       and one.pass_spacing_m > SPACING_CAP_M),
                         'same': ((one.area_ha, one.alpha_used_m,
                                   one.pass_spacing_m,
                                   None if one.polygon is None else one.polygon.wkb)
                                  == (two.area_ha, two.alpha_used_m,
                                      two.pass_spacing_m,
                                      None if two.polygon is None
                                      else two.polygon.wkb)),
                         'sites_today': [], 'sites_cap': []})
    return rows


def _spacing(value):
    return '-' if value is None else '%.2f' % value


def _dates(days):
    days = sorted(set(days))
    if len(days) <= 3:
        return ', '.join(days) or 'none'
    return '%d dates %s..%s' % (len(days), days[0], days[-1])


def _counts(rows, key):
    return ('%d day row(s), %d zone row(s)'
            % (sum(1 for r in rows if r['path'] == 'day' and key(r)),
               sum(1 for r in rows if r['path'] == 'zone' and key(r))))


def report_sets(sets, zone_names, out=print):
    """Условие 1 в редакции поправки 02.10. Возвращает вердикт.

    `sets` -- [(имя файла, строки compare_set)] по каждому --tracks.
    """
    rows = [row for _name, mine in sets for row in mine]
    work_days = {(unit, day): name for unit, name, day in WORK_DAYS_1208}
    gaps = []
    for name, mine in sets:
        day_rows = [r for r in mine if r['path'] == 'day']
        out('%s: machine-days %d (day rows), contours entered %d (zone rows), '
            'points %d, dates %s; triggered: %s; changed: %s'
            % (console(name), len(day_rows),
               sum(1 for r in mine if r['path'] == 'zone'),
               sum(r.get('points', 0) for r in day_rows),
               _dates(r['day'] for r in day_rows),
               _counts(mine, lambda r: r['triggered']),
               _counts(mine, lambda r: not r['same'])))
        if not day_rows:
            gaps.append('%s gave no machine-day' % console(name))
        elif not any(r['path'] == 'zone' for r in mine):
            gaps.append('%s entered no contour: its per-contour half was not '
                        'compared' % console(name))
    day_rows = [r for r in rows if r['path'] == 'day']
    read = {(r['unit'], r['day']) for r in day_rows}
    absent = [(unit, day) for unit, _name, day in WORK_DAYS_1208
              if (unit, day) not in read]
    if absent:
        gaps.append('%d of the %d works of 12.08 were not read: %s'
                    % (len(absent), len(WORK_DAYS_1208),
                       ', '.join('%d %s' % pair for pair in absent)))
    else:
        out('12.08 set: all %d works read, %d points (recorded: %d)'
            % (len(WORK_DAYS_1208),
               sum(r.get('points', 0) for r in day_rows
                   if (r['unit'], r['day']) in work_days), POINTS_1208))
    early = [r for r in day_rows if r['day'] in (DAY_0727, '-')]
    if early:
        out('27.07 set: %d machine-days, %d points (recorded: %d tractors, %d '
            'points)' % (len(early), sum(r.get('points', 0) for r in early),
                         TRACTORS_0727, POINTS_0727))
    else:
        gaps.append('the 27.07 set (%d works: tracks of %s, %d tractors, %d '
                    'points) was not read; dates seen: %s'
                    % (WORKS_0727, DAY_0727, TRACTORS_0727, POINTS_0727,
                       _dates(r['day'] for r in day_rows)))
    out('Works on ground without a contour appear only in day rows (zone -), '
        'by unit and day.')
    changed = [row for row in rows if not row['same'] or row['triggered']]
    failed = [row for row in changed if row['path'] == 'day'
              and (row['unit'], row['day']) in work_days]
    for row in changed:
        out('  %-4s unit %-6d day %-10s zone %-6s spacing %s -> %s  ha %.4f -> '
            '%.4f  %s  %s'
            % (row['path'], row['unit'], row['day'],
               '-' if row['zone'] is None else row['zone'],
               _spacing(row['spacing']), _spacing(row['spacing_cap']),
               row['ha_today'], row['ha_cap'],
               'same' if row['same'] else 'CHANGED',
               console(row['zone_name']
                       or work_days.get((row['unit'], row['day']), ''))))
        for label, sites in (('was', row['sites_today']), ('now', row['sites_cap'])):
            for contour_id, area, alpha, spacing in sites:
                out('       %s: site %.4f ha  alpha %.2f  spacing %s  zone %s  %s'
                    % (label, area, alpha, _spacing(spacing),
                       '-' if contour_id is None else contour_id,
                       console(zone_names.get(contour_id, ''))))
    for gap in gaps:
        out('  not checked: %s' % gap)
    if failed:
        out('CONDITION 1: %s -- %d of the 15 works of 12.08 changed: each is a '
            'whole machine-day, and the rule changed its step and alpha'
            % (FAIL, len(failed)))
        return FAIL
    if gaps:
        out('CONDITION 1: %s -- see the lines above; both sets must be compared '
            'in full' % NOT_CHECKED)
        return NOT_CHECKED
    if not changed:
        out('CONDITION 1 (no row of the two sets changed or triggered, so none of '
            'the 32 works did): PASS')
        return PASS
    out('CONDITION 1: %s -- %d row(s) above changed or triggered. A DAY row: the '
        'rule changed the step and alpha of EVERY site of that machine-day, so it '
        'is rejected if the machine did any of the 32 works that day. A ZONE row: '
        'it is rejected if that contour is one of the works (compare zone numbers '
        'and names with the workbook). Rows that are neither go to the report.'
        % (OWNER_CHECK, len(changed)))
    return OWNER_CHECK


# --- production (условия 2, 3, 5) ------------------------------------------------

def published_days(con, since, until):
    query = ('SELECT work_date, wialon_id, method_version FROM gps_daily_aggregates '
             'WHERE reason IS NULL AND work_date >= ?')
    args = [since]
    if until is not None:
        query += ' AND work_date <= ?'
        args.append(until)
    return [(str(day), int(unit), version) for day, unit, version
            in con.execute(query + ' ORDER BY work_date, wialon_id', args)]


def stored_sites(con, day, unit):
    return [(round(float(area), 4),
             None if alpha is None else round(float(alpha), 3),
             None if spacing is None else round(float(spacing), 3))
            for area, alpha, spacing in con.execute(
                'SELECT area_ha, alpha_used_m, pass_spacing_m '
                'FROM gps_work_polygons WHERE work_date = ? AND wialon_id = ? '
                'ORDER BY area_ha DESC, site_number', (day, unit))]


def row_key(rows):
    return sorted(((row['area_ha'], row['alpha_used_m'], row['pass_spacing_m'])
                   for row in rows), key=lambda item: -item[0])


def full_key(rows):
    return sorted(((row['area_ha'], row['alpha_used_m'], row['pass_spacing_m'],
                    row['polygon_geojson']) for row in rows),
                  key=lambda item: (-item[0], item[3]))


def replay_day(points, contours):
    """(участки прежнего метода, участки правила) одних суток."""
    today = compute_day(points, contours=contours, overflow_cap=False)
    capped = compute_day(points, contours=contours, overflow_cap=True)
    return today.sites, capped.sites


def judge_day(day, unit, version, stored, today, capped, measures):
    """Одна строка прогона и её нарушения.

    `version` -- `method_version` строки в базе; `measures` -- (шаг сегодня,
    шаг правила, альфа сегодня, альфа правила) по сырым числам (`measure`).
    """
    spacing, spacing_cap, alpha_today, alpha_cap = measures
    ha_today = sum(row['area_ha'] for row in today)
    ha_cap = sum(row['area_ha'] for row in capped)
    triggered = spacing is not None and spacing > SPACING_CAP_M
    violations = []
    if ha_cap > ha_today + EPSILON:
        violations.append('more hectares than today')
    if alpha_cap > alpha_today + EPSILON:
        violations.append('wider alpha than today')
    if alpha_cap > ALPHA_CEILING_M + EPSILON:
        violations.append('alpha above %.3f' % ALPHA_CEILING_M)
    if not triggered and (spacing_cap != spacing
                          or full_key(today) != full_key(capped)):
        violations.append('changed below the cap')
    # [REASON]: строку сверяют с пересчётом тем методом, который её записал.
    # До 07.10 в базе лежал прежний метод, после пересчёта окна -- правило;
    # строка иной версии (старше прежнего метода) не судится вовсе.
    written_by = {PREVIOUS_METHOD_VERSION: today, METHOD_VERSION: capped}
    control = None
    if version in written_by:
        control = (row_key(written_by[version])
                   == sorted(stored, key=lambda item: -item[0]))
    # [REASON]: `measure` повторяет отбор точек движка, а не вызывает его;
    # если повтор разошёлся с тем, что движок записал в строки, вердикт
    # опирался бы на чужие числа. Такое расхождение -- поломка прогона.
    consistent = (_rows_carry(today, spacing, alpha_today)
                  and _rows_carry(capped, spacing_cap, alpha_cap))
    return {'day': day, 'unit': unit, 'ha_today': ha_today, 'ha_cap': ha_cap,
            'spacing': spacing, 'spacing_cap': spacing_cap,
            'alpha_today': alpha_today, 'alpha_cap': alpha_cap,
            'triggered': triggered, 'violations': violations,
            'control': control, 'consistent': consistent,
            'version': version, 'today': today, 'capped': capped}


def _rows_carry(rows, spacing, alpha):
    expected = (None if spacing is None else round(spacing, 3), round(alpha, 3))
    return all((row['pass_spacing_m'], row['alpha_used_m']) == expected
               for row in rows)


def site_placemarks(rows, style, label):
    parts = []
    for number, row in enumerate(rows, 1):
        shapes = [_polygon_kml(rings) for rings in polygons_of(row['polygon_geojson'])]
        shapes = [shape for shape in shapes if shape]
        if not shapes:
            continue
        geometry = (shapes[0] if len(shapes) == 1 else
                    '<MultiGeometry>%s</MultiGeometry>' % ''.join(shapes))
        parts.append('<Placemark><name>%s</name><styleUrl>#%s</styleUrl>%s'
                     '</Placemark>' % (escape('%s %d — %.2f га'
                                              % (label, number, row['area_ha'])),
                                       style, geometry))
    return ''.join(parts)


def kml_folder(result, points, name):
    day = datetime.strptime(result['day'], '%Y-%m-%d').strftime('%d.%m.%Y')
    title = ('%s · %s · было / эди %.2f га → стало / бўлди %.2f га'
             % (name or result['unit'], day, result['ha_today'],
                result['ha_cap']))
    parts = ['<Folder><name>%s</name>' % escape(title),
             site_placemarks(result['today'], 'site',
                             'Было / эди: участок / участка'),
             site_placemarks(result['capped'], 'new',
                             'Стало / бўлди: участок / участка')]
    pieces = track_pieces(points or [])
    for work, style, label in (
            (True, 'work', 'Трек в работе, 1–15 км/ч / иш тезлигидаги трек'),
            (False, 'move', 'Трек вне работы / ишдан ташқари трек')):
        lines = ''.join('<LineString><tessellate>1</tessellate><coordinates>%s'
                        '</coordinates></LineString>' % _coordinates(line)
                        for kind, line in pieces if kind is work)
        if lines:
            parts.append('<Placemark><name>%s</name><styleUrl>#%s</styleUrl>'
                         '<MultiGeometry>%s</MultiGeometry></Placemark>'
                         % (escape(label), style, lines))
    parts.append('</Folder>')
    return ''.join(parts)


def kml_document(folders):
    styles = ''.join('<Style id="%s">%s</Style>' % (key, body)
                     for key, body in tuple(STYLES) + (NEW_STYLE,))
    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<kml xmlns="http://www.opengis.net/kml/2.2"><Document>'
            '<name>%s</name>%s%s</Document></kml>\n'
            % (escape('GPS A7: было и стало / эди ва бўлди'), styles,
               ''.join(folders)))


def replay_production(db, folder, since, until, kml_path, out=print,
                      progress=None):
    """Прогон production. Возвращает список вердиктов машинных условий."""
    con = open_readonly(db)
    try:
        days = published_days(con, since, until)
        contours = load_contours(con)
        results, missing = [], 0
        for index, (day, unit, version) in enumerate(days, 1):
            points = read_day_readonly(folder, unit, day)
            if not points:
                missing += 1
                continue
            today, capped = replay_day(points, contours or None)
            results.append(judge_day(day, unit, version,
                                     stored_sites(con, day, unit), today,
                                     capped, measure([r[:4] for r in points])))
            if progress and index % 200 == 0:
                progress('  %d of %d machine-days' % (index, len(days)))
        units = sorted({row['unit'] for row in results})
        kinds = kinds_of(con, units)
        names = unit_names(con, units)
    finally:
        con.close()
    verdicts = report_production(results, missing, kinds, names, out)
    if kml_path:
        picked = pick_for_review(results, kinds)
        folders = []
        for result in picked:
            points = read_day_readonly(folder, result['unit'], result['day'])
            folders.append(kml_folder(result, points, names.get(result['unit'])))
        with open(kml_path, 'w', encoding='utf-8') as handle:
            handle.write(kml_document(folders))
        out('KML for conditions 3 and 4 (%d machine-days): %s'
            % (len(picked), console(kml_path)))
    return verdicts


def counted(results, kinds):
    """Сутки, чьи гектары идут в план-факт.

    [REASON]: условия 4 и 5 говорят о план-факте, а объекты, исключённые
    владельцем («не наша», непольевая категория), экран скрывает и в
    план-факт не считает. Их строки в базе остаются, поэтому они проходят
    контроль и инварианты условия 2 вместе со всеми, но в списке потерь и в
    сумме план-факта их нет.
    """
    return [row for row in results if kinds.get(row['unit']) != 'excluded']


def top_losses(results, kinds):
    named = set(NAMED_DAYS)
    rest = [row for row in counted(results, kinds)
            if row['triggered'] and (row['day'], row['unit']) not in named
            and row['ha_today'] - row['ha_cap'] > EPSILON]
    rest.sort(key=lambda row: (-(row['ha_today'] - row['ha_cap']), row['day'],
                               row['unit']))
    return rest[:TOP_LOSSES]


def pick_for_review(results, kinds):
    by_key = {(row['day'], row['unit']): row for row in results}
    named = [by_key[key] for key in NAMED_DAYS if key in by_key]
    return named + top_losses(results, kinds)


def condition_2(results, out):
    checked = [row for row in results if row['control'] is not None]
    mismatched = [row for row in checked if not row['control']]
    out('control, recomputed by the method that wrote the row == stored: %d of '
        '%d same, %d different, %d not checked (another method version)'
        % (len(checked) - len(mismatched), len(checked), len(mismatched),
           len(results) - len(checked)))
    versions = defaultdict(int)
    for row in results:
        versions[row.get('version')] += 1
    out('rows by method version: %s'
        % ', '.join('%s %d' % (name, count) for name, count
                    in sorted(versions.items(), key=lambda item: str(item[0]))))
    for row in mismatched[:20]:
        out('  control mismatch %s %d' % (row['day'], row['unit']))
    for row in [row for row in results if not row['consistent']][:20]:
        out('  measure mismatch %s %d: the replay measured another spacing '
            'than the engine wrote' % (row['day'], row['unit']))
    inconsistent = sum(1 for row in results if not row['consistent'])
    violations = [row for row in results if row['violations']]
    for row in violations[:20]:
        out('  VIOLATION %s %d: %s' % (row['day'], row['unit'],
                                       '; '.join(row['violations'])))
    # [REASON]: нарушение инварианта считается по одному повтору точек, база
    # в нём не участвует. Поэтому несошедшийся контроль на других сутках его
    # не отменяет: правило отвергается, а контроль чинится отдельно. Не
    # доверяются только сутки, где повтор разошёлся с самим движком.
    standing = [row for row in violations if row['consistent']]
    if not results:
        verdict, note = NOT_CHECKED, 'no machine-day was replayed'
    elif standing:
        verdict, note = FAIL, ('%d of %d machine-days break an invariant'
                               % (len(standing), len(results)))
    elif mismatched or inconsistent:
        verdict, note = RUN_INVALID, ('the control failed on %d machine-day(s), '
                                      'the measure on %d: fix the replay and '
                                      'run it again'
                                      % (len(mismatched), inconsistent))
    else:
        verdict, note = PASS, ('no invariant broken on %d machine-days'
                               % len(results))
    out('CONDITION 2 (invariants on every machine-day): %s -- %s'
        % (verdict, note))
    return verdict


def condition_3(results, names, out):
    out('condition 3, the six named machine-days:')
    by_key = {(row['day'], row['unit']): row for row in results}
    missing, failed, untrusted = [], [], []
    for day, unit in NAMED_DAYS:
        row = by_key.get((day, unit))
        if row is None:
            missing.append((day, unit))
            out('  %s %6d  not replayed (no published row or no points)'
                % (day, unit))
            continue
        # [REASON]: названные сутки выбраны по тому, что лежит в базе (допуск
        # 300-981 м). Если повтор не воспроизвёл базу или разошёлся с
        # движком, пересчитаны другие сутки, чем те, что названы, и судить
        # по ним правило нельзя -- ни в плюс, ни в минус.
        trusted = row['control'] is not False and row['consistent']
        fell = row['triggered'] and row['ha_cap'] < row['ha_today'] - EPSILON
        if not trusted:
            untrusted.append((day, unit))
        elif not fell:
            failed.append((day, unit))
        out('  %s %6d  triggered %-3s  alpha %.1f -> %.1f  ha %.2f -> %.2f  %s  %s'
            % (day, unit, 'yes' if row['triggered'] else 'NO',
               row['alpha_today'], row['alpha_cap'], row['ha_today'],
               row['ha_cap'],
               ('UNTRUSTED, the replay does not reproduce this day' if not trusted
                else 'fell' if fell else 'DID NOT FALL'),
               console(names.get(unit) or '')))
    if failed:
        verdict = FAIL
    elif untrusted:
        verdict = RUN_INVALID
    elif missing:
        verdict = NOT_EVALUATED
    else:
        verdict = PASS
    out('CONDITION 3, machine part (all six triggered, hectares strictly fall): %s'
        % verdict)
    out('CONDITION 3, owner part: %s -- look at the six folders of the KML; no '
        'remaining blue site may cover ground between different roads'
        % OWNER_CHECK)
    return verdict


def report_production(results, missing, kinds, names, out=print):
    mine = counted(results, kinds)
    out('machine-days replayed: %d (points gone from disk: %d); counted %d, '
        'excluded objects %d' % (len(results), missing, len(mine),
                                 len(results) - len(mine)))
    verdicts = [condition_2(results, out)]
    out('')
    verdicts.append(condition_3(results, names, out))
    out('')
    out('condition 4: %s -- the %d other triggered machine-days with the largest '
        'loss, in the KML:' % (OWNER_CHECK, TOP_LOSSES))
    for row in top_losses(results, kinds):
        out('  %s %6d  alpha %.1f -> %.1f  ha %.2f -> %.2f  %s'
            % (row['day'], row['unit'], row['alpha_today'], row['alpha_cap'],
               row['ha_today'], row['ha_cap'],
               console(names.get(row['unit']) or '')))
    out('')
    triggered = [row for row in mine if row['triggered']]
    untouched = [row for row in mine if not row['triggered']
                 and row['alpha_today'] > SPACING_CAP_M]
    ha_today = sum(row['ha_today'] for row in mine)
    ha_cap = sum(row['ha_cap'] for row in mine)
    out('condition 5 (report, no verdict):')
    out('  machine-days triggered: %d of %d counted' % (len(triggered), len(mine)))
    out('  hectares on untouched machine-days with alpha %.2f-%.3f m: %.2f '
        '(%d machine-days)' % (SPACING_CAP_M, ALPHA_CEILING_M,
                               sum(row['ha_today'] for row in untouched),
                               len(untouched)))
    out('  plan-fact of the period, counted objects: %.2f ha by the previous '
        'method -> %.2f ha by the rule (%+.2f)'
        % (ha_today, ha_cap, ha_cap - ha_today))
    return verdicts


def _day(option, value):
    try:
        return datetime.strptime(value, '%Y-%m-%d').strftime('%Y-%m-%d')
    except ValueError:
        sys.stderr.write('ERROR: %s must look like YYYY-MM-DD\n' % option)
        return None


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--tracks', action='append', default=[],
                        help='hand-measured set tracks csv, may repeat')
    parser.add_argument('--zones', help='wialon_zones.json for the sets')
    parser.add_argument('--db', help='path to transport.db (production mode)')
    parser.add_argument('--dir', help='folder with gps_points_YYYYMM.db files')
    parser.add_argument('--since', help='first date, YYYY-MM-DD')
    parser.add_argument('--until', help='last date, YYYY-MM-DD')
    parser.add_argument('--kml', help='KML file for the owner (production mode)')
    args = parser.parse_args(argv)
    if bool(args.tracks) == bool(args.db):
        sys.stderr.write('ERROR: give either --tracks/--zones or --db/--dir\n')
        return 2
    if args.tracks:
        missing = [path for path in args.tracks + [args.zones or '']
                   if not os.path.isfile(path)]
        if missing:
            sys.stderr.write('ERROR: not found: %s\n'
                             % ', '.join(console(m) for m in missing))
            return 2
        from tools.gps_area_method_repro import load_zones
        loaded = load_zones(args.zones)
        zones = {zone_id: polygon for zone_id, (_name, polygon) in loaded.items()}
        zone_names = {zone_id: name for zone_id, (name, _polygon) in loaded.items()}
        loaded_sets = []
        try:
            for path in args.tracks:
                loaded_sets.append((os.path.basename(path), load_csv_days(path)))
        except BadInput as error:
            sys.stderr.write('ERROR: %s\n' % error)
            return 2
        sets = []
        for name, (days, skipped) in loaded_sets:
            print('%s: unreadable rows skipped %d' % (console(name), skipped))
            sets.append((name, compare_set(name, days, zones, zone_names)))
        verdict = report_sets(sets, zone_names)
        print('nothing was written: the tracks and zones were only read')
        return verdict_code([verdict])
    since = _day('--since', args.since or '')
    until = None if args.until is None else _day('--until', args.until)
    if since is None or (args.until is not None and until is None):
        return 2
    if not os.path.isfile(args.db) or not os.path.isdir(args.dir or ''):
        sys.stderr.write('ERROR: no database or no points folder\n')
        return 2
    if args.kml and not os.path.isdir(os.path.dirname(os.path.abspath(args.kml))):
        sys.stderr.write('ERROR: the folder for --kml does not exist\n')
        return 2
    verdicts = replay_production(args.db, args.dir, since, until, args.kml,
                                 progress=lambda line: print(line, flush=True))
    print('nothing was written to the database or the point files: they were '
          'opened mode=ro')
    return verdict_code(verdicts)


if __name__ == '__main__':
    sys.exit(main())
