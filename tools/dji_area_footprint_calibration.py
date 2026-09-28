# -*- coding: utf-8 -*-
"""tools/dji_area_footprint_calibration.py -- калибровка следа распыления.

DJI-AREA-FOOTPRINT-CALIBRATION-001. Вопрос один: отделяет ли след распыления
(путь во время применения x ширина полосы) записи с плоским счётчиком и
применением от доказанных настоящих обработок. Правило здесь НЕ вводится и
порог НЕ выбирается заранее: инструмент считает признаки и показывает
распределения и таблицу возможных границ с их ложными срабатываниями.

Только чтение. База открывается `mode=ro`, SHA-256 файла снимается до и после;
тела V4 читаются из хранилища рядом с базой (`instance/dji_sources`); к DJI
инструмент не обращается; пишет только файлы в `--out`.

Группы -- по текущим строкам расчёта (`AREA_ALGORITHM_VERSION`):

  A1  RAW_CORROBORATED: полное проверенное окно, прирост счётчика совпал с RAW;
  A2  RAW_CORROBORATED_QUALIFIED: прирост закодированного подокна совпал с RAW;
  B   REVIEW с причиной APPLICATION_WITH_FLAT_COUNTER (после правила
      DJI-AREA-RETAINED-FOOTPRINT-001 -- и его причина: группа та же).

Названные `--flight-id` помечаются в выводе (санити-проверка) и в обучение
ничего не вносят: правило не выбирается вовсе.

Признаки считаются на кадрах ПРИМЕНЕНИЯ (флаг распыления ИЛИ расход > 0), не
на всём маршруте. Шаг `k-1 -> k` входит в след, если в кадре `k-1` шло
применение и 0 < dt <= 1 с. Ширина шага -- ширина кадра `k-1` (поле 6), иначе
медиана ширины кадров применения записи, иначе медиана по записи, иначе
ширина из списка DJI; нет ни одной -- след не определён.

Запуск (корень репозитория; служба может работать -- база только читается):

  & "C:\\Program Files\\Python314\\python.exe" tools\\dji_area_footprint_calibration.py --db instance\\transport.db --out C:\\VehicleSoft_Footprint_Calibration --flight-id 679813767 --flight-id 693319955 --flight-id 698068932 --flight-id 687610350

ОЦЕНКА ПРАВИЛА (`--evaluate-rule`, DJI-AREA-RETAINED-FOOTPRINT-001). Вместо
распределений -- ответ, что сделает правило `dji_area.footprint` +
`resolver.retained_negligible_footprint` с текущими строками, ДО слияния и
без записи. Группа B -- текущие строки с причиной APPLICATION_WITH_FLAT_COUNTER
(или уже RETAINED_SCALAR_WITH_NEGLIGIBLE_FOOTPRINT). Для каждой записи B и
каждой контрольной: след правила (путь при применении x 12,0 м, отношение к
RAW), затем сухой прогон НАСТОЯЩЕГО конвейера (`pipeline.recalculate`,
`read_only=True`) по этим записям -- срабатывает ли правило, какой отпечаток
строка получила бы, не меняется ли RAW и billable. Контрольные записи
production встроены (`NAMED_EXPECTATIONS`, `A2_NEGATIVE_CONTROLS`),
`--flight-id` и `--negative-control` добавляют свои. Команду применения
инструмент не печатает: решение о ней -- после ревью.

  & "C:\\Program Files\\Python314\\python.exe" tools\\dji_area_footprint_calibration.py --db instance\\transport.db --out C:\\VehicleSoft_Retained_Footprint_Eval --evaluate-rule

Коды возврата: 0 -- выполнено; 1 -- ошибка аргументов или данных; 2 -- база не
найдена (файл НЕ создаётся); 3 -- файл базы изменился во время чтения;
4 -- (оценка правила) нарушен контроль: сработало на отрицательной контрольной
или вне группы B, изменился RAW, billable не пуст, переписалась бы строка, где
правило не сработало, либо запись не удалось оценить. Вывод в консоль только
ASCII.

ОТКАТ. Кода: удалить файл, его никто не импортирует. Данных: в базу не пишет.
"""

import argparse
import csv
import hashlib
import json
import math
import os
import sqlite3
import sys
from datetime import date

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import dji_area  # noqa: E402
from dji_area import accounting as acc  # noqa: E402
from dji_area import footprint as fp  # noqa: E402
from dji_area import pipeline as pl  # noqa: E402
from dji_area import resolver as rs  # noqa: E402
from dji_area import store  # noqa: E402
from dji_area import v4 as v4mod  # noqa: E402

EXIT_OK = 0
EXIT_USAGE = 1
EXIT_NO_DATABASE = 2
EXIT_DB_CHANGED = 3
EXIT_CONTROL_VIOLATED = 4

GROUP_A1 = 'A1'
GROUP_A2 = 'A2'
GROUP_B = 'B'
GROUPS = (GROUP_A1, GROUP_A2, GROUP_B)
# Группа B -- плоский счётчик при наблюдённом применении. До правила
# DJI-AREA-RETAINED-FOOTPRINT-001 у всех таких записей одна причина -- спор
# человеку; после его применения часть несёт уже причину правила. Группа та же.
B_REASONS = (acc.R_APPLICATION_WITH_FLAT_COUNTER,
             acc.R_RETAINED_NEGLIGIBLE_FOOTPRINT)
PERCENTILES = (1, 5, 10, 25, 50, 75, 90, 95, 99)
M2_PER_HA = 10000.0

CSV_COLUMNS = (
    'flight_id', 'group', 'named', 'report_start_date', 'area_status',
    'raw_m2', 'validated_delta_m2', 'v4_sha256', 'application_frames',
    'application_bursts', 'application_duration_s', 'application_path_m',
    'application_displacement_m', 'width_m', 'width_source', 'width_min_m',
    'width_max_m', 'footprint_m2', 'footprint_ha', 'footprint_to_raw',
    'footprint_to_delta', 'footprint_min_width_to_raw',
    'footprint_max_width_to_raw', 'footprint_fleet_max_width_to_raw',
    'speed_max_mps', 'speed_median_mps', 'observed_share',
    'steps_counted', 'steps_skipped', 'quantity_delta', 'quantity_per_raw',
    'problem',
)


def file_sha256(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as handle:
        for block in iter(lambda: handle.read(1 << 20), b''):
            digest.update(block)
    return digest.hexdigest()


def connect_read_only(db_path):
    uri = 'file:%s?mode=ro' % os.path.abspath(db_path).replace(
        '\\', '/').replace('?', '%3f').replace('#', '%23')
    con = sqlite3.connect(uri, uri=True, timeout=30)
    con.row_factory = sqlite3.Row
    return con


def _median(values):
    if not values:
        return None
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2.0


def _positive_width(frame):
    width = frame.get('width')
    if width is None or width != width or width <= 0:
        return None
    return width


def _speed(frame):
    if 'vx' in frame and 'vy' in frame:
        return math.hypot(frame['vx'], frame['vy'])
    return None


def _is_application(frame):
    """Тот же признак, что `application_frames` сводки V4: флаг ИЛИ расход."""
    return (frame.get('spray_flag') or 0) > 0 or (frame.get('flow') or 0) > 0


def application_features(frames, list_width=None):
    """Признаки применения одной записи. Чистая функция."""
    n = len(frames)
    app = [_is_application(f) for f in frames]
    app_idx = [i for i, flag in enumerate(app) if flag]
    out = {'application_frames': len(app_idx), 'application_bursts': 0,
           'application_duration_s': 0.0, 'application_path_m': 0.0,
           'application_displacement_m': None, 'width_m': None,
           'width_source': None, 'width_min_m': None, 'width_max_m': None,
           'footprint_m2': None, 'footprint_min_width_m2': None,
           'footprint_max_width_m2': None, 'speed_max_mps': None,
           'speed_median_mps': None, 'observed_share': None,
           'steps_counted': 0, 'steps_skipped': 0, 'quantity_delta': None}
    quantities = [f['quantity'] for f in frames if 'quantity' in f]
    if quantities:
        out['quantity_delta'] = quantities[-1] - quantities[0]
    if not app_idx:
        out['footprint_m2'] = 0.0
        return out

    widths_app = [w for w in (_positive_width(frames[i]) for i in app_idx) if w]
    widths_all = [w for w in (_positive_width(f) for f in frames) if w]
    if widths_app:
        width, source = _median(widths_app), 'frames_application'
    elif widths_all:
        width, source = _median(widths_all), 'frames_record'
    elif list_width is not None and list_width > 0:
        width, source = float(list_width), 'list'
    else:
        width, source = None, None
    out['width_m'], out['width_source'] = width, source
    pool = widths_all or ([width] if width else [])
    out['width_min_m'] = min(pool) if pool else None
    out['width_max_m'] = max(pool) if pool else None

    bursts, prev = 0, None
    for i in app_idx:
        if prev is None or i != prev + 1:
            bursts += 1
        prev = i
    out['application_bursts'] = bursts

    path = duration = footprint = 0.0
    counted = skipped = 0
    footprint_defined = True
    for k in range(1, n):
        if not app[k - 1]:
            continue
        a, b = frames[k - 1], frames[k]
        if a.get('t') is None or b.get('t') is None:
            skipped += 1
            continue
        dt = (b['t'] - a['t']) / 1000.0
        if dt <= 0 or dt > v4mod.WINDOW_MAX_DT_S:
            skipped += 1
            continue
        distance = v4mod._distance_m(a, b)
        if distance is None:
            skipped += 1
            continue
        step_width = _positive_width(a) or width
        path += distance
        duration += dt
        counted += 1
        if step_width is None:
            footprint_defined = False
        else:
            footprint += distance * step_width
    out['application_path_m'] = path
    out['application_duration_s'] = duration
    out['steps_counted'] = counted
    out['steps_skipped'] = skipped
    out['footprint_m2'] = footprint if footprint_defined else None
    if out['width_min_m'] is not None:
        out['footprint_min_width_m2'] = path * out['width_min_m']
        out['footprint_max_width_m2'] = path * out['width_max_m']

    placed = [frames[i] for i in app_idx if 'lat' in frames[i]
              and 'lng' in frames[i]]
    if placed:
        out['application_displacement_m'] = v4mod._distance_m(placed[0],
                                                             placed[-1])

    speeds, observed = [], 0
    for i in app_idx:
        speed = _speed(frames[i])
        step_speed = None
        for k in (i, i + 1):
            if 0 < k < n and frames[k - 1].get('t') is not None \
                    and frames[k].get('t') is not None:
                dt = (frames[k]['t'] - frames[k - 1]['t']) / 1000.0
                d = v4mod._distance_m(frames[k - 1], frames[k])
                if d is not None and 0 < dt <= v4mod.WINDOW_MAX_DT_S:
                    step_speed = d / dt if step_speed is None \
                        else max(step_speed, d / dt)
        if speed is not None or step_speed is not None:
            observed += 1
        best = speed if speed is not None else step_speed
        if best is not None:
            speeds.append(best)
    out['observed_share'] = observed / float(len(app_idx))
    out['speed_max_mps'] = max(speeds) if speeds else None
    out['speed_median_mps'] = _median(speeds)
    return out


def _ratio(numerator, denominator):
    if numerator is None or not denominator:
        return None
    return numerator / float(denominator)


def percentile(values, p):
    """Ближайший ранг: значение, ниже или равно которому доля p записей."""
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, int(math.ceil(p / 100.0 * len(ordered))))
    return ordered[rank - 1]


def distribution(values):
    values = [v for v in values if v is not None]
    out = {'n': len(values)}
    if values:
        out['min'] = min(values)
        out['max'] = max(values)
        for p in PERCENTILES:
            out['P%d' % p] = percentile(values, p)
    return out


def load_targets(con, named):
    rows = [dict(r) for r in con.execute(
        'SELECT * FROM dji_area_calculations WHERE superseded_at IS NULL AND '
        'area_algorithm_version = ? ORDER BY flight_id',
        (dji_area.AREA_ALGORITHM_VERSION,))]
    targets = []
    for row in rows:
        group = None
        if row['area_status'] == rs.RAW_CORROBORATED:
            group = GROUP_A1
        elif row['area_status'] == rs.RAW_CORROBORATED_QUALIFIED:
            group = GROUP_A2
        elif acc.classify(row)['reason'] in B_REASONS:
            group = GROUP_B
        if group is not None or int(row['flight_id']) in named:
            targets.append((group, row))
    return len(rows), targets


def analyse(db_path, named=()):
    named = {int(f) for f in named}
    root = store.source_root(os.path.abspath(db_path))
    con = connect_read_only(db_path)
    items = []
    try:
        current, targets = load_targets(con, named)
        for group, row in targets:
            fid = int(row['flight_id'])
            ev = con.execute(
                'SELECT list_spray_width, card_spray_width FROM '
                'dji_flight_evidence WHERE flight_id=?', (fid,)).fetchone()
            list_width = None
            if ev is not None:
                list_width = ev['list_spray_width'] or ev['card_spray_width']
            item = {'flight_id': fid, 'group': group, 'named': fid in named,
                    'report_start_date': str(row['report_start_date'])[:10],
                    'area_status': row['area_status'],
                    'raw_m2': row['raw_area_m2'],
                    'validated_delta_m2': row['controller_delta_area_m2'],
                    'v4_sha256': None, 'problem': None}
            rev = (store.revision_by_id(con, row['v4_revision_id'])
                   if row['v4_revision_id'] else None)
            if rev is None:
                item['problem'] = 'NO_V4_REVISION'
                items.append(item)
                continue
            item['v4_sha256'] = rev['sha256']
            try:
                frames = v4mod.decode_v4(store.read_body(root, rev)).frames
            except (store.StoreError, OSError, v4mod.V4DecodeError) as exc:
                item['problem'] = 'V4_UNREADABLE_%s' % type(exc).__name__
                items.append(item)
                continue
            item.update(application_features(frames, list_width))
            items.append(item)
    finally:
        con.close()

    widths = [i['width_max_m'] for i in items if i.get('width_max_m')]
    fleet_max = max(widths) if widths else None
    for item in items:
        raw = item.get('raw_m2')
        fp = item.get('footprint_m2')
        item['footprint_ha'] = fp / M2_PER_HA if fp is not None else None
        item['footprint_to_raw'] = _ratio(fp, raw)
        item['footprint_to_delta'] = _ratio(fp, item.get('validated_delta_m2'))
        item['footprint_min_width_to_raw'] = _ratio(
            item.get('footprint_min_width_m2'), raw)
        item['footprint_max_width_to_raw'] = _ratio(
            item.get('footprint_max_width_m2'), raw)
        path = item.get('application_path_m')
        item['footprint_fleet_max_width_to_raw'] = (
            _ratio(path * fleet_max, raw)
            if path is not None and fleet_max else None)
        item['quantity_per_raw'] = _ratio(item.get('quantity_delta'), raw)
    return current, items, fleet_max


def summarise(items, fleet_max):
    def members(group, need_application):
        out = [i for i in items if i['group'] == group and not i['problem']]
        if need_application:
            out = [i for i in out if (i.get('application_frames') or 0) > 0]
        return out

    report = {'fleet_max_width_m': fleet_max, 'groups': {}}
    for group in GROUPS:
        every = [i for i in items if i['group'] == group]
        usable = members(group, False)
        applied = members(group, True)
        report['groups'][group] = {
            'records': len(every),
            'without_v4_or_unreadable': len(every) - len(usable),
            'without_application_frames': len(usable) - len(applied),
            'with_application': len(applied),
            'footprint_undefined_no_width': sum(
                1 for i in applied if i.get('footprint_m2') is None),
            'footprint_to_raw': distribution(
                [i['footprint_to_raw'] for i in applied]),
            'footprint_ha': distribution([i['footprint_ha'] for i in applied]),
            'footprint_to_delta': distribution(
                [i['footprint_to_delta'] for i in applied]),
            'footprint_min_width_to_raw': distribution(
                [i['footprint_min_width_to_raw'] for i in applied]),
            'footprint_fleet_max_width_to_raw': distribution(
                [i['footprint_fleet_max_width_to_raw'] for i in applied]),
            'observed_share': distribution(
                [i['observed_share'] for i in applied]),
            'quantity_per_raw': distribution(
                [i['quantity_per_raw'] for i in applied]),
        }
    positives = [i for i in items if i['group'] in (GROUP_A1, GROUP_A2)
                 and not i['problem'] and (i.get('application_frames') or 0)]
    suspects = [i for i in items if i['group'] == GROUP_B and not i['problem']]
    cuts = sorted({round(i['footprint_to_raw'], 4) for i in suspects
                   if i.get('footprint_to_raw') is not None})

    def at_or_below(records, key, cut):
        return [i for i in records if i.get(key) is not None
                and round(i[key], 4) <= cut]

    table = []
    for cut in cuts:
        # «Ниже или равно cut»: граница проходит ровно по записи группы B.
        # Консервативно с обеих сторон: реальные обработки -- с САМОЙ УЗКОЙ
        # шириной записи (их след наименьший), подозрительные -- ещё и с самой
        # широкой шириной парка (их след наибольший).
        false_pos = sorted(at_or_below(positives,
                                       'footprint_min_width_to_raw', cut),
                           key=lambda i: i['footprint_min_width_to_raw'])
        flagged = at_or_below(suspects, 'footprint_to_raw', cut)
        flagged_wide = at_or_below(suspects,
                                   'footprint_fleet_max_width_to_raw', cut)
        table.append({
            'cut_footprint_to_raw': cut,
            'b_flagged': len(flagged),
            'b_flagged_raw_ha': sum(i['raw_m2'] or 0 for i in flagged)
            / M2_PER_HA,
            'b_flagged_with_fleet_max_width': len(flagged_wide),
            'named_flagged': sorted(i['flight_id'] for i in flagged
                                    if i['named']),
            'a_false_positives': len(at_or_below(positives,
                                                 'footprint_to_raw', cut)),
            'a_false_positives_min_width': len(false_pos),
            'a_false_positive_ids': [(i['flight_id'], i['raw_m2'])
                                     for i in false_pos[:10]],
        })
    report['candidate_cuts'] = table
    lowest = sorted((i for i in positives
                     if i.get('footprint_min_width_to_raw') is not None),
                    key=lambda i: i['footprint_min_width_to_raw'])[:15]
    report['lowest_positive_controls'] = [
        {k: i.get(k) for k in ('flight_id', 'group', 'raw_m2',
                               'validated_delta_m2', 'application_frames',
                               'application_path_m', 'width_m',
                               'width_source', 'footprint_to_raw',
                               'footprint_min_width_to_raw', 'observed_share',
                               'steps_skipped')} for i in lowest]
    return report


def write_outputs(out_dir, items, report):
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, 'footprint_calibration.csv'), 'w',
              newline='', encoding='utf-8-sig') as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_COLUMNS,
                                extrasaction='ignore')
        writer.writeheader()
        for item in sorted(items, key=lambda i: (str(i['group']),
                                                 i['flight_id'])):
            writer.writerow({k: item.get(k) for k in CSV_COLUMNS})
    with open(os.path.join(out_dir, 'footprint_calibration.json'), 'w',
              encoding='utf-8') as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2,
                  sort_keys=True, default=str)


def _fmt(value, spec='%.4f'):
    return '-' if value is None else spec % value


def print_report(current, items, report):
    print('DJI-AREA-FOOTPRINT-CALIBRATION-001 (read-only)')
    print('  algorithm        : %s' % dji_area.AREA_ALGORITHM_VERSION)
    print('  current rows     : %d' % current)
    print('  fleet max width  : %s m' % _fmt(report['fleet_max_width_m'],
                                            '%.2f'))
    for group in GROUPS:
        g = report['groups'][group]
        print('  group %-2s: records %d, with application %d, without '
              'application %d, no V4/unreadable %d, no width %d'
              % (group, g['records'], g['with_application'],
                 g['without_application_frames'],
                 g['without_v4_or_unreadable'],
                 g['footprint_undefined_no_width']))
        d = g['footprint_to_raw']
        if d['n']:
            print('     footprint/RAW  min %s P1 %s P5 %s P10 %s P25 %s P50 %s '
                  'P75 %s P90 %s P95 %s P99 %s max %s' % tuple(
                      _fmt(d.get(k)) for k in ('min', 'P1', 'P5', 'P10',
                                               'P25', 'P50', 'P75', 'P90',
                                               'P95', 'P99', 'max')))
    print('  candidate cuts (ratio <= cut; A counted with its own and with the '
          'NARROWEST width, B also with the fleet max width):')
    for row in report['candidate_cuts']:
        print('     cut %.4f  B %2d (%.4f ha, fleet-max width %2d)  A false '
              'positives %d (narrowest width %d)  named %s' % (
                  row['cut_footprint_to_raw'], row['b_flagged'],
                  row['b_flagged_raw_ha'],
                  row['b_flagged_with_fleet_max_width'],
                  row['a_false_positives'],
                  row['a_false_positives_min_width'],
                  ','.join(str(f) for f in row['named_flagged']) or '-'))
    print('  named flights:')
    for item in sorted((i for i in items if i['named']),
                       key=lambda i: i['flight_id']):
        print('     %d group %s raw %s ha footprint %s ha ratio %s path %s m '
              'width %s' % (item['flight_id'], item['group'],
                            _fmt((item['raw_m2'] or 0) / M2_PER_HA),
                            _fmt(item.get('footprint_ha')),
                            _fmt(item.get('footprint_to_raw')),
                            _fmt(item.get('application_path_m'), '%.1f'),
                            _fmt(item.get('width_m'), '%.2f')))


# ─── DJI-AREA-RETAINED-FOOTPRINT-001: оценка правила до слияния ─────────────

# Названные записи production: три администратор признал фантомами
# (CONFIRM_FULL_PHANTOM); 687610350 -- плоский счётчик при заметном
# распылении и без структурного совпадения, обязан остаться человеку.
NAMED_EXPECTATIONS = {679813767: 'candidate', 693319955: 'candidate',
                      698068932: 'candidate', 687610350: 'review'}
# Настоящие работы A2 production, чей след ниже порога (до 0,008 RAW):
# кадры применения там потеряли большую часть распыления. Правило не имеет
# права сделать их нулём -- их держат плоский счётчик и структурный экран.
A2_NEGATIVE_CONTROLS = (668794098, 669810485, 685273720, 706410605)

RULE_CSV_COLUMNS = (
    'flight_id', 'group', 'report_start_date', 'stored_class',
    'stored_reason', 'raw_m2', 'problem', 'application_frames',
    'steps_needed', 'steps_unobserved', 'application_path_m',
    'conservative_footprint_m2', 'conservative_footprint_to_raw',
    'width_over_envelope', 'width_max_m', 'passes_footprint',
    'structural_candidate', 'scalar_source_check', 'structural_match',
    'candidate_base_flight_id', 'resolved', 'fires', 'dry_class',
    'dry_reason', 'accepted_after_m2', 'would_write', 'raw_changed',
    'dry_billable_m2', 'expectation', 'negative_control',
)


ORACLE_PATH = os.path.join(ROOT, 'docs', 'DJI_AREA_SEPTEMBER_2026_ORACLE.json')


def _report_day(row):
    return date.fromisoformat(str(row['report_start_date'])[:10])


def oracle_period(path=ORACLE_PATH):
    """Период сентябрьского оракула приёмки или None.

    [REASON]: блок A ранбука принимает ревизию, только если её сухой пересчёт
    сентября не хочет переписать ни одной строки, а четыре записи с плоским
    счётчиком там остаются на проверке. Кандидат правила внутри этого периода
    -- заранее известная остановка развёртывания: оракул придётся пересмотреть
    решением владельца. Оценка обязана назвать такие записи до слияния.
    """
    try:
        with open(path, encoding='utf-8') as handle:
            period = json.load(handle)['period']
        return date.fromisoformat(period[0]), date.fromisoformat(period[1])
    except (OSError, ValueError, KeyError, IndexError, TypeError):
        return None


def rule_footprint(con, root, row):
    """След правила по телу V4 строки расчёта: (след | None, проблема)."""
    rev = (store.revision_by_id(con, row['v4_revision_id'])
           if row.get('v4_revision_id') else None)
    if rev is None:
        return None, 'NO_V4_REVISION'
    try:
        frames = v4mod.decode_v4(store.read_body(root, rev)).frames
    except (store.StoreError, OSError, v4mod.V4DecodeError) as exc:
        return None, 'V4_UNREADABLE_%s' % type(exc).__name__
    return fp.application_footprint(frames), None


def footprint_passes(footprint, raw):
    """Только след, без экрана: наблюдаем, ширина в огибающей, <= 0,027 RAW."""
    return (footprint is not None and footprint['steps_unobserved'] == 0
            and footprint['width_over_envelope'] is False
            and fp.negligible_footprint(
                footprint['conservative_footprint_m2'], raw))


def evaluate_rule(db_path, named=(), negatives=()):
    """Что сделает правило с текущими строками. Только чтение."""
    expectations = dict(NAMED_EXPECTATIONS)
    for fid in named:
        expectations.setdefault(int(fid), 'report')
    negatives = sorted(set(A2_NEGATIVE_CONTROLS) | {int(f) for f in negatives})
    must_not_fire = set(negatives) | {fid for fid, e in expectations.items()
                                      if e == 'review'}

    root = store.source_root(os.path.abspath(db_path))
    con = connect_read_only(db_path)
    try:
        rows = {int(r['flight_id']): dict(r) for r in con.execute(
            'SELECT * FROM dji_area_calculations WHERE superseded_at IS NULL '
            'AND area_algorithm_version = ? ORDER BY id',
            (dji_area.AREA_ALGORITHM_VERSION,))}
        stored = {fid: acc.classify(row) for fid, row in rows.items()}
        b_ids = {fid for fid, c in stored.items() if c['reason'] in B_REASONS}
        wanted = sorted(b_ids | ((set(expectations) | set(negatives))
                                 & set(rows)))
        billable_table = con.execute(
            'SELECT COUNT(*) FROM dji_area_calculations WHERE '
            'billable_area_m2 IS NOT NULL').fetchone()[0]
        footprints = {fid: rule_footprint(con, root, rows[fid])
                      for fid in wanted}
    finally:
        con.close()

    lines, calc_writes = {}, {}
    if wanted:
        days = [_report_day(rows[fid]) for fid in wanted]
        dry = pl.recalculate(db_path, min(days), max(days),
                             flight_ids=wanted, read_only=True,
                             collect_rows=True)
        lines = {int(line['flight_id']): line for line in dry['flights']}
        calc_writes = dict(dry['calc_writes'])

    records = []
    for fid in wanted:
        row, line = rows[fid], lines.get(fid)
        footprint, problem = footprints[fid]
        raw = row['raw_area_m2']
        f = footprint or {}
        conservative = f.get('conservative_footprint_m2')
        after = acc.classify(line) if line is not None else None
        source = line if line is not None else row
        if fid in b_ids:
            group = GROUP_B
        elif row['area_status'] == rs.RAW_CORROBORATED_QUALIFIED:
            group = GROUP_A2
        elif row['area_status'] == rs.RAW_CORROBORATED:
            group = GROUP_A1
        else:
            group = 'other'
        records.append({
            'flight_id': fid, 'group': group,
            'report_start_date': str(row['report_start_date'])[:10],
            'stored_class': stored[fid]['accounting_class'],
            'stored_reason': stored[fid]['reason'],
            'raw_m2': raw, 'problem': problem,
            'application_frames': f.get('application_frames'),
            'steps_needed': f.get('steps_needed'),
            'steps_unobserved': f.get('steps_unobserved'),
            'application_path_m': f.get('application_path_m'),
            'conservative_footprint_m2': conservative,
            'conservative_footprint_to_raw': _ratio(conservative, raw),
            'width_over_envelope': f.get('width_over_envelope'),
            'width_max_m': f.get('width_max_m'),
            'passes_footprint': footprint_passes(footprint, raw),
            'structural_candidate': bool(source.get('structural_candidate')),
            'scalar_source_check': source.get('scalar_source_check') in (
                True, 1),
            'structural_match': acc.is_structural_match(source),
            'candidate_base_flight_id': source.get('candidate_base_flight_id'),
            'resolved': line is not None,
            'fires': line is not None and rs.F_RETAINED_NEGLIGIBLE_FOOTPRINT
            in line['anomaly_flags'],
            'dry_class': after['accounting_class'] if after else None,
            'dry_reason': after['reason'] if after else None,
            'accepted_after_m2': after['accounted_area_m2'] if after else None,
            'would_write': line is not None and line['calculation_input_hash']
            != row['calculation_input_hash'],
            'raw_changed': line is not None and line['raw_area_m2'] != raw,
            'dry_billable_m2': line['billable_area_m2'] if line else None,
            'expectation': expectations.get(fid),
            'negative_control': fid in negatives,
        })

    by_id = {r['flight_id']: r for r in records}
    b = [r for r in records if r['group'] == GROUP_B]
    passing = [r for r in b if r['passes_footprint']]
    candidates = [r for r in b if r['fires']]
    violations = []
    for r in records:
        fid = r['flight_id']
        if not r['resolved']:
            violations.append('NOT_RESOLVED:%d' % fid)
        if r['fires'] and fid in must_not_fire:
            violations.append('NEGATIVE_CONTROL_FIRED:%d' % fid)
        if r['negative_control'] and r['dry_class'] == acc.PHANTOM_PROVEN \
                and r['stored_class'] != acc.PHANTOM_PROVEN:
            violations.append('NEGATIVE_CONTROL_BECOMES_PHANTOM:%d' % fid)
        if r['fires'] and r['group'] != GROUP_B:
            violations.append('FIRED_OUTSIDE_B:%d' % fid)
        if r['fires'] and not (r['passes_footprint'] and r['structural_match']):
            violations.append('FIRED_WITHOUT_INDEPENDENT_SUPPORT:%d' % fid)
        if r['raw_changed']:
            violations.append('RAW_CHANGED:%d' % fid)
        if r['dry_billable_m2'] is not None:
            violations.append('BILLABLE_NOT_NULL:%d' % fid)
        # [REASON]: контракт версий -- строка, где правило не сработало,
        # обязана сохранить отпечаток: иначе применение перепишет её и сделает
        # «устаревшими» решения администратора по ней.
        if r['would_write'] and not r['fires']:
            violations.append('UNEXPECTED_REWRITE:%d' % fid)
        if r['fires'] and not r['would_write'] \
                and r['stored_reason'] != acc.R_RETAINED_NEGLIGIBLE_FOOTPRINT:
            violations.append('FIRED_NOT_REWRITTEN:%d' % fid)
    if billable_table:
        violations.append('BILLABLE_NOT_NULL_IN_TABLE:%d' % billable_table)
    review_before = sum(1 for c in stored.values()
                        if c['accounting_class'] == acc.REVIEW)
    leaving_review = sum(1 for r in records if r['stored_class'] == acc.REVIEW
                         and r['dry_class'] is not None
                         and r['dry_class'] != acc.REVIEW)
    period = oracle_period()
    in_oracle = sorted(
        r['flight_id'] for r in candidates if period is not None
        and period[0] <= date.fromisoformat(r['report_start_date'])
        <= period[1])
    return {
        'oracle_period': [d.isoformat() for d in period] if period else None,
        'oracle_period_candidates': in_oracle,
        'rule': rs.retained_footprint_rule_snapshot(),
        'algorithm': dji_area.AREA_ALGORITHM_VERSION,
        'current_rows': len(rows),
        'b_total': len(b),
        'b_footprint_unreadable': sorted(r['flight_id'] for r in b
                                         if r['problem']),
        'b_steps_unobserved': sorted(r['flight_id'] for r in b
                                     if (r['steps_unobserved'] or 0) > 0),
        'b_width_over_envelope': sorted(r['flight_id'] for r in b
                                        if r['width_over_envelope']),
        'b_passes_footprint': len(passing),
        'b_passes_footprint_structural_match': sum(
            1 for r in passing if r['structural_match']),
        'below_cut_structural_mismatch': sorted(
            r['flight_id'] for r in passing if not r['structural_match']),
        'held_back_despite_both': sorted(
            r['flight_id'] for r in passing
            if r['structural_match'] and not r['fires']),
        'final_candidates': sorted(r['flight_id'] for r in candidates),
        'final_candidates_raw_ha': sum(r['raw_m2'] or 0.0
                                       for r in candidates) / M2_PER_HA,
        'remaining_review_b': sorted(r['flight_id'] for r in b
                                     if not r['fires']),
        'review_total_before': review_before,
        'review_total_after': review_before - leaving_review,
        'named': [by_id.get(fid, {'flight_id': fid, 'absent': True})
                  for fid in sorted(expectations)],
        'negative_controls': [by_id.get(fid, {'flight_id': fid,
                                              'absent': True})
                              for fid in negatives],
        'dry_run_calc_writes': calc_writes,
        'billable_non_null_table': billable_table,
        'billable_non_null_dry_run': sum(
            1 for r in records if r['dry_billable_m2'] is not None),
        'raw_changed': sum(1 for r in records if r['raw_changed']),
        'violations': violations,
        'records': records,
    }


def write_rule_outputs(out_dir, evaluation):
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, 'retained_footprint_evaluation.csv'), 'w',
              newline='', encoding='utf-8-sig') as handle:
        writer = csv.DictWriter(handle, fieldnames=RULE_CSV_COLUMNS,
                                extrasaction='ignore')
        writer.writeheader()
        for record in sorted(evaluation['records'],
                             key=lambda r: (r['group'], r['flight_id'])):
            writer.writerow({k: record.get(k) for k in RULE_CSV_COLUMNS})
    with open(os.path.join(out_dir, 'retained_footprint_evaluation.json'), 'w',
              encoding='utf-8') as handle:
        json.dump(evaluation, handle, ensure_ascii=False, indent=2,
                  sort_keys=True, default=str)


def _ids(values):
    return ', '.join(str(v) for v in values) or '-'


def _yes(value):
    return 'yes' if value else 'no'


def print_rule_report(ev):
    rule = ev['rule']
    print('DJI-AREA-RETAINED-FOOTPRINT-001 rule evaluation (read-only dry run)')
    print('  algorithm        : %s' % ev['algorithm'])
    print('  rule             : %s' % rule['rule_version'])
    print('  formula          : application path x %.1f m / RAW <= %.3f, '
          'every step 0 < dt <= %.1f s, no observed width > %.1f m'
          % (rule['width_envelope_m'], rule['footprint_to_raw_max'],
             rule['step_max_dt_s'], rule['width_envelope_m']))
    print('  current rows     : %d' % ev['current_rows'])
    print('  B total          : %d' % ev['b_total'])
    print('  B footprint unreadable %d, unobserved steps %d, width over '
          'envelope %d' % (len(ev['b_footprint_unreadable']),
                           len(ev['b_steps_unobserved']),
                           len(ev['b_width_over_envelope'])))
    print('  B footprint <= %.3f at %.1f m : %d'
          % (rule['footprint_to_raw_max'], rule['width_envelope_m'],
             ev['b_passes_footprint']))
    print('    with structural scalar match : %d'
          % ev['b_passes_footprint_structural_match'])
    print('    below cut, structural mismatch: %d  [%s]'
          % (len(ev['below_cut_structural_mismatch']),
             _ids(ev['below_cut_structural_mismatch'])))
    if ev['held_back_despite_both']:
        print('    both, but held back by another condition: [%s]'
              % _ids(ev['held_back_despite_both']))
    print('  FINAL CANDIDATES (dry run fires the rule): %d, RAW %.4f ha'
          % (len(ev['final_candidates']), ev['final_candidates_raw_ha']))
    print('    [%s]' % _ids(ev['final_candidates']))
    if ev['oracle_period']:
        print('    inside the September oracle period %s..%s: %d  [%s]'
              % (ev['oracle_period'][0], ev['oracle_period'][1],
                 len(ev['oracle_period_candidates']),
                 _ids(ev['oracle_period_candidates'])))
    print('  remaining REVIEW in B : %d  [%s]'
          % (len(ev['remaining_review_b']), _ids(ev['remaining_review_b'])))
    print('  REVIEW total          : before %d, after %d'
          % (ev['review_total_before'], ev['review_total_after']))
    print('  named controls:')
    for r in ev['named']:
        if r.get('absent'):
            print('     %d absent from current rows' % r['flight_id'])
            continue
        print('     %d group %s raw %.4f ha ratio %s structural %s/%s fires %s '
              '-> %s %s (expected %s)'
              % (r['flight_id'], r['group'], (r['raw_m2'] or 0) / M2_PER_HA,
                 _fmt(r['conservative_footprint_to_raw']),
                 _yes(r['structural_candidate']),
                 _yes(r['scalar_source_check']), _yes(r['fires']),
                 r['dry_class'], r['dry_reason'], r['expectation']))
    print('  negative controls (must never become phantom):')
    for r in ev['negative_controls']:
        if r.get('absent'):
            print('     %d absent from current rows' % r['flight_id'])
            continue
        print('     %d group %s raw %.4f ha ratio %s passes footprint alone %s '
              'fires %s -> %s'
              % (r['flight_id'], r['group'], (r['raw_m2'] or 0) / M2_PER_HA,
                 _fmt(r['conservative_footprint_to_raw']),
                 _yes(r['passes_footprint']), _yes(r['fires']),
                 r['dry_class']))
    writes = ev['dry_run_calc_writes']
    print('  dry run calc writes   : %s' % (', '.join(
        '%s %d' % (k, writes[k]) for k in sorted(writes)) or '-'))
    print('  RAW changed           : %d' % ev['raw_changed'])
    print('  billable non-null     : table %d, dry run %d'
          % (ev['billable_non_null_table'], ev['billable_non_null_dry_run']))
    for violation in ev['violations']:
        print('  VIOLATION %s' % violation)
    print('  no apply command is printed: applying is a separate decision')


def build_parser():
    parser = argparse.ArgumentParser(
        prog='dji_area_footprint_calibration.py',
        description='Read-only calibration of the application footprint '
                    'against proven real work and flat-counter records.')
    parser.add_argument('--db', dest='db_path',
                        default=os.path.join(ROOT, 'instance', 'transport.db'))
    parser.add_argument('--out', dest='out_dir')
    parser.add_argument('--flight-id', dest='flight_ids', action='append',
                        type=int, default=[], metavar='ID')
    parser.add_argument('--evaluate-rule', action='store_true',
                        help='evaluate the retained-footprint rule on the '
                             'current rows (dry run, read-only)')
    parser.add_argument('--negative-control', dest='negative_controls',
                        action='append', type=int, default=[], metavar='ID',
                        help='a record the rule must never turn into a '
                             'phantom (added to the built-in A2 controls)')
    return parser


def run_rule_evaluation(args):
    before = file_sha256(args.db_path)
    try:
        evaluation = evaluate_rule(args.db_path, args.flight_ids,
                                   args.negative_controls)
    except (sqlite3.Error, store.StoreError, pl.PipelineError) as exc:
        print('evaluation failed: %s: %s' % (
            type(exc).__name__, str(exc).encode('ascii', 'replace').decode()))
        return EXIT_USAGE
    after = file_sha256(args.db_path)
    evaluation['database_sha256_before'] = before
    evaluation['database_sha256_after'] = after
    if args.out_dir:
        write_rule_outputs(os.path.abspath(args.out_dir), evaluation)
    print_rule_report(evaluation)
    if before != after:
        print('  DATABASE CHANGED DURING A READ-ONLY RUN')
        return EXIT_DB_CHANGED
    print('  database sha256 unchanged: yes')
    if evaluation['violations']:
        return EXIT_CONTROL_VIOLATED
    return EXIT_OK


def main(argv=None):
    args = build_parser().parse_args(argv)
    if not os.path.exists(args.db_path):
        print('database not found at %s - refusing to run'
              % args.db_path.encode('ascii', 'replace').decode())
        return EXIT_NO_DATABASE
    if args.evaluate_rule:
        return run_rule_evaluation(args)
    before = file_sha256(args.db_path)
    try:
        current, items, fleet_max = analyse(args.db_path, args.flight_ids)
    except (sqlite3.Error, store.StoreError) as exc:
        print('analysis failed: %s: %s' % (
            type(exc).__name__, str(exc).encode('ascii', 'replace').decode()))
        return EXIT_USAGE
    report = summarise(items, fleet_max)
    after = file_sha256(args.db_path)
    report['database_sha256_before'] = before
    report['database_sha256_after'] = after
    if args.out_dir:
        write_outputs(os.path.abspath(args.out_dir), items, report)
    print_report(current, items, report)
    if before != after:
        print('  DATABASE CHANGED DURING A READ-ONLY RUN')
        return EXIT_DB_CHANGED
    print('  database sha256 unchanged: yes')
    return EXIT_OK


if __name__ == '__main__':
    sys.exit(main())
