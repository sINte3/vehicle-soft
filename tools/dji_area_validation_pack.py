# -*- coding: utf-8 -*-
"""tools/dji_area_validation_pack.py -- пакет для ручной сверки владельцем.

Из сохранённых расчётов (`dji_area_calculations` + `dji_field_attributions`
+ `dji_flight_evidence`) собирает Excel с двумя листами:

* «OWNER CHECK» -- ~20–30 репрезентативных записей периода: что открыть в
  SmartFarm и что посмотреть; колонки наблюдений владельца ПУСТЫ и
  заполняются им (никогда не из ожиданий);
* «TECHNICAL» -- машинно-читаемые ожидаемые статусы/числа тех же записей.

Выбор кейсов -- по классам (нормальные малые/большие, несколько бортов,
3 Gijduvon, manual, null width, flat, tiny-positive, width-present stale,
baseline unknown, V4 нет, outlier, RAW=0 с применением, TIER1/2/5,
изменённый полигон, cross-midnight, повтор скаляра). Класс без записей в
периоде отмечается как «нет в периоде», а не подменяется.

Запуск:
  & "C:\\Program Files\\Python314\\python.exe" tools\\dji_area_validation_pack.py --db instance\\transport.db --from 2026-08-01 --to 2026-08-31 --out C:\\path\\DJI_AREA_AUGUST_LIVE_VALIDATION_PACK.xlsx

Консоль -- ASCII. Координаты, тела источников, токены в файл не попадают.
"""

import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from dji_area import AREA_ALGORITHM_VERSION, FIELD_RESOLVER_VERSION, MODEL_VERSION  # noqa: E402
from dji_area import resolver as rs  # noqa: E402

UTC5 = timedelta(hours=5)

# Ключевые кейсы матрицы приёмки (если попадают в период) -- ставятся первыми.
PINNED = [673501214, 677850572, 675819746, 675819715, 688669423, 684409277,
          674061957, 687623234, 690480852, 694956303, 677057244, 685264927,
          692752823, 688669418, 687623232, 695027340, 677314979]

CLASS_RULES = [
    ('NORMAL_SMALL', lambda r: r['area_status'] in (rs.RAW_CORROBORATED, rs.RAW_CORROBORATED_QUALIFIED) and (r['raw_area_m2'] or 0) < 1500),
    ('NORMAL_LARGE', lambda r: r['area_status'] in (rs.RAW_CORROBORATED, rs.RAW_CORROBORATED_QUALIFIED) and (r['raw_area_m2'] or 0) > 20000),
    ('GIJDUVON_FLAGLESS', lambda r: r['hardware_id'] == '1581F5742255T0C1L061' and r['area_status'] in (rs.RAW_CORROBORATED, rs.RAW_CORROBORATED_QUALIFIED)),
    ('MANUAL', lambda r: r['manual_mode'] == 1 and r['area_status'] in (rs.RAW_CORROBORATED, rs.RAW_CORROBORATED_QUALIFIED)),
    ('NULL_WIDTH_REAL_WORK', lambda r: r['list_spray_width'] is None and r['area_status'] in (rs.RAW_CORROBORATED, rs.RAW_CORROBORATED_QUALIFIED)),
    ('STALE_FLAT', lambda r: r['area_status'] == rs.COUNTER_FLAT_RAW_OVERSTATED and r['structural_candidate'] == 1),
    ('STALE_TINY_POSITIVE', lambda r: r['area_status'] == rs.PARTIAL_RECORDED_OVERSTATEMENT),
    ('WIDTH_PRESENT_STALE', lambda r: r['area_status'] == rs.COUNTER_FLAT_RAW_OVERSTATED and r['list_spray_width'] is not None),
    ('BASELINE_UNKNOWN', lambda r: r['area_status'] == rs.BASELINE_UNKNOWN),
    ('V4_MISSING_SUSPECT', lambda r: r['area_status'] in (rs.UNKNOWN_SUSPECT, rs.OVERLAP_REVIEW)),
    ('V4_MISSING_ORDINARY', lambda r: r['area_status'] == rs.RAW_UNVERIFIED),
    ('RELATIONSHIP_OUTLIER', lambda r: r['area_status'] == rs.COUNTER_RELATIONSHIP_OUTLIER),
    ('RAW_ZERO_APPLICATION', lambda r: r['area_status'] == rs.APPLICATION_WITHOUT_MEASURED_AREA),
    ('RAW_ZERO_COUNTER', lambda r: r['area_status'] == rs.COUNTER_ZERO),
    ('CROSS_MIDNIGHT', lambda r: r['crosses_midnight']),
    ('FIELD_TIER1', lambda r: r['field_attribution_tier'] == 'TIER1_EXACT'),
    ('FIELD_TIER2', lambda r: r['field_attribution_tier'] == 'TIER2_STRONG'),
    ('FIELD_TIER3', lambda r: r['field_attribution_tier'] == 'TIER3_SUPPORTED'),
    ('FIELD_TIER4', lambda r: r['field_attribution_tier'] == 'TIER4_GEOMETRIC'),
    ('FIELD_TIER5', lambda r: r['field_attribution_tier'] == 'TIER5_UNKNOWN' and (r['raw_area_m2'] or 0) > 5000),
    ('HISTORICAL_POLYGON_CHANGED', lambda r: r['geometry_holder_land_uuid'] and r['linked_land_uuid'] and r['geometry_holder_land_uuid'] != r['linked_land_uuid']),
    ('ROUTE_QUARANTINED', lambda r: 'ROUTE_IDENTITY_ERROR' in (r['anomaly_flags'] or '')),
]

STATUS_RU = {
    rs.RAW_CORROBORATED: 'Проверено',
    rs.RAW_CORROBORATED_QUALIFIED: 'Проверено частично',
    rs.COUNTER_FLAT_RAW_OVERSTATED: 'Повторная/перенесённая запись',
    rs.PARTIAL_RECORDED_OVERSTATEMENT: 'Частичная новая работа',
    rs.RAW_UNVERIFIED: 'Предварительно',
    rs.UNKNOWN_SUSPECT: 'Недостаточно данных (подозрение)',
    rs.APPLICATION_WITHOUT_MEASURED_AREA: 'Площадь не измерена',
    rs.COUNTER_ZERO: 'Ноль по счётчику',
    rs.ZERO_RECORDED_UNVERIFIED: 'Ноль без проверки',
    rs.CHANNEL_MISSING: 'Канал недоступен',
    rs.BASELINE_UNKNOWN: 'Начало не измерено',
    rs.COUNTER_RELATIONSHIP_OUTLIER: 'Требует проверки',
    rs.COUNTER_NONMONOTONE_REVIEW: 'Сброс счётчика',
    rs.OVERLAP_REVIEW: 'Пересечение записей',
}
TIER_RU = {'TIER1_EXACT': 'Поле подтверждено (геометрия по hash)',
           'TIER2_STRONG': 'Поле подтверждено (uuid, версия полигона не сохранена)',
           'TIER3_SUPPORTED': 'Поле предположительно (lineage)',
           'TIER4_GEOMETRIC': 'Поле предположительно (маршрут внутри текущего полигона)',
           'TIER5_UNKNOWN': 'Поле не определено'}

WHAT_TO_CHECK = {
    'NORMAL_SMALL': 'Открыть запись в SmartFarm: площадь в карточке; визуально -- один короткий заход.',
    'NORMAL_LARGE': 'Площадь в карточке; полный проход поля; литры заметны.',
    'GIJDUVON_FLAGLESS': 'Борт 3 Gijduvon: площадь есть, расход 0 -- так пишет сам борт; сверить площадь, не литры.',
    'MANUAL': 'Ручной режим: площадь и трек есть без контура; сверить площадь.',
    'NULL_WIDTH_REAL_WORK': 'Ширина не записана, но работа была: сверить площадь и трек.',
    'STALE_FLAT': 'Запись после возобновления: площадь равна предыдущей записи того же борта -- см. базу в TECHNICAL; трек короткий/пустой?',
    'STALE_TINY_POSITIVE': 'Как STALE_FLAT, но был небольшой реальный дозаход: виден ли короткий проход?',
    'WIDTH_PRESENT_STALE': 'Ширина записана, а счётчик не рос: площадь равна какой-то прежней записи? Трек?',
    'BASELINE_UNKNOWN': 'Первые кадры без счётчика: похоже ли на продолжение предыдущей записи? Не трактовать как 0 и как новую площадь.',
    'V4_MISSING_SUSPECT': 'У DJI нет телеметрии; запись в цепочке возобновления. Что показывает карта?',
    'V4_MISSING_ORDINARY': 'Обычная запись без телеметрии: площадь карточки как есть.',
    'RELATIONSHIP_OUTLIER': 'Счётчик и площадь расходятся на ~7 %: сверить площадь карточки и длину трека.',
    'RAW_ZERO_APPLICATION': 'Площадь 0, но насос работал: видно ли распыление/расход в карточке?',
    'RAW_ZERO_COUNTER': 'Площадь 0 и счётчик не рос при работающем насосе.',
    'CROSS_MIDNIGHT': 'Начало до полуночи (UTC+5): к какому дню DJI относит запись в списке?',
    'FIELD_TIER1': 'Поле в SmartFarm: имя и контур совпадают с указанным?',
    'FIELD_TIER2': 'Поле опознано по uuid, версия полигона не сохранена: тот ли контур сейчас?',
    'FIELD_TIER3': 'Поле выведено по родству ключей других вылетов: правдоподобно?',
    'FIELD_TIER4': 'Поле подобрано геометрически: правдоподобно?',
    'FIELD_TIER5': 'Поле не определено: под каким именем оно в SmartFarm (если есть)?',
    'HISTORICAL_POLYGON_CHANGED': 'Контур перерисован после вылета: старая версия (holder) отличается от текущей (linked).',
    'ROUTE_QUARANTINED': 'Маршрут в архиве оказался от другого вылета -- площадь по карточке; на карте SmartFarm свой трек?',
}


def load_rows(con, date_from, date_to):
    rows = con.execute(
        'SELECT c.*, e.list_spray_width, e.list_manual_mode AS manual_mode, '
        'e.list_nickname, f.field_attribution_tier, f.field_name_at_snapshot, '
        'f.linked_land_uuid, f.geometry_holder_land_uuid, f.field_attribution_method '
        'FROM dji_area_calculations c '
        'LEFT JOIN dji_flight_evidence e ON e.flight_id = c.flight_id '
        'LEFT JOIN dji_field_attributions f ON f.flight_id = c.flight_id '
        '  AND f.superseded_at IS NULL AND f.field_resolver_version = ? '
        'WHERE c.superseded_at IS NULL AND c.area_algorithm_version = ? '
        'AND c.report_start_date BETWEEN ? AND ? ORDER BY c.start_at_utc',
        (FIELD_RESOLVER_VERSION, AREA_ALGORITHM_VERSION, date_from, date_to)
    ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        start = datetime.fromisoformat(d['start_at_utc'])
        end = datetime.fromisoformat(d['end_at_utc']) if d['end_at_utc'] else None
        d['start_local'] = start + UTC5
        d['end_local'] = (end + UTC5) if end else None
        d['crosses_midnight'] = bool(end) and (start + UTC5).date() != (end + UTC5).date()
        d['anomaly_flags'] = ' '.join(json.loads(d['anomaly_flags_json'] or '[]'))
        out.append(d)
    return out


def select_cases(rows, limit=30):
    by_id = {r['flight_id']: r for r in rows}
    chosen = []
    used = set()
    classes = {}
    for fid in PINNED:
        r = by_id.get(fid)
        if r is None:
            continue
        cls = next((name for name, rule in CLASS_RULES if rule(r)), 'PINNED')
        chosen.append((cls, r))
        used.add(fid)
    missing_classes = []
    for name, rule in CLASS_RULES:
        if any(c == name for c, _ in chosen):
            continue
        # prefer different aircraft than already chosen
        aircraft_used = {r['hardware_id'] for _c, r in chosen}
        cands = [r for r in rows if r['flight_id'] not in used and rule(r)]
        cands.sort(key=lambda r: (r['hardware_id'] in aircraft_used, r['flight_id']))
        if cands:
            chosen.append((name, cands[0]))
            used.add(cands[0]['flight_id'])
        else:
            missing_classes.append(name)
    return chosen[:limit], missing_classes


def build(db_path, date_from, date_to, out_path):
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill
    con = sqlite3.connect('file:%s?mode=ro' % db_path.replace(os.sep, '/'),
                          uri=True)
    con.row_factory = sqlite3.Row
    try:
        rows = load_rows(con, date_from, date_to)
    finally:
        con.close()
    chosen, missing = select_cases(rows)
    wb = Workbook()
    ws = wb.active
    ws.title = 'OWNER CHECK'
    header = ['CASE_ID', 'CLASS', 'DATE (UTC+5)', 'TIME (UTC+5)', 'DRONE (nickname)',
              'HARDWARE_ID', 'FLIGHT_ID', 'DJI RAW AREA, ha',
              'VEHICLE SOFT TECHNICAL AREA, ha', 'VEHICLE SOFT STATUS',
              'FIELD (name at snapshot)', 'FIELD TIER', 'WHAT TO OPEN / CHECK',
              'OWNER_OBSERVED_DJI_VALUE', 'OWNER_VISUAL_WORK',
              'OWNER_FIELD_OBSERVATION', 'OWNER_COMMENT', 'CHECKED']
    ws.append(header)
    for i, (cls, r) in enumerate(chosen, 1):
        corrected = r['corrected_recorded_area_m2']
        ws.append([
            'C%03d' % i, cls, r['start_local'].strftime('%d.%m.%Y'),
            r['start_local'].strftime('%H:%M'), r['list_nickname'] or '',
            r['hardware_id'] or '', r['flight_id'],
            round((r['raw_area_m2'] or 0) / 10000.0, 4) if r['raw_area_m2'] is not None else None,
            round(corrected / 10000.0, 4) if corrected is not None else 'Недостаточно данных',
            STATUS_RU.get(r['area_status'], r['area_status']),
            r['field_name_at_snapshot'] or 'Поле не определено',
            TIER_RU.get(r['field_attribution_tier'], r['field_attribution_tier'] or ''),
            WHAT_TO_CHECK.get(cls, ''),
            None, None, None, None, None,
        ])
    bold = Font(bold=True)
    fill = PatternFill('solid', fgColor='D9EAD3')
    for cell in ws[1]:
        cell.font = bold
        cell.fill = fill
    ws.freeze_panes = 'A2'
    for col in ws.columns:
        width = max(len(str(c.value)) if c.value is not None else 0 for c in col)
        ws.column_dimensions[col[0].column_letter].width = min(max(12, width + 2), 60)

    tech = wb.create_sheet('TECHNICAL')
    tcols = ['CASE_ID', 'flight_id', 'report_start_date', 'hardware_id',
             'raw_area_m2', 'card_area_m2', 'corrected_recorded_area_m2',
             'controller_delta_area_m2', 'counter_observed_delta_m2',
             'counter_zero_default_delta_m2', 'area_status', 'evidence_status',
             'area_method', 'area_confidence', 'counter_baseline_status',
             'counter_window_quality', 'anomaly_flags', 'application_activity',
             'application_evidence_kind', 'application_without_area',
             'structural_candidate', 'candidate_base_flight_id',
             'scalar_source_check', 'overlap_group_id', 'aggregation_eligibility',
             'field_attribution_tier', 'field_attribution_method',
             'field_name_at_snapshot', 'linked_land_uuid',
             'geometry_holder_land_uuid', 'area_algorithm_version',
             'calculation_input_hash']
    tech.append(tcols)
    for i, (cls, r) in enumerate(chosen, 1):
        tech.append(['C%03d' % i] + [r.get(c) for c in tcols[1:]])
    for cell in tech[1]:
        cell.font = bold
        cell.fill = fill
    tech.freeze_panes = 'A2'

    meta = wb.create_sheet('META')
    meta.append(['model_version', MODEL_VERSION])
    meta.append(['area_algorithm_version', AREA_ALGORITHM_VERSION])
    meta.append(['field_resolver_version', FIELD_RESOLVER_VERSION])
    meta.append(['period', '%s..%s' % (date_from, date_to)])
    meta.append(['records in period', len(rows)])
    meta.append(['cases selected', len(chosen)])
    meta.append(['classes absent in period', ', '.join(missing) or '-'])
    meta.append(['generated_at_utc', datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')])
    meta.append(['note', 'OWNER_* columns are EMPTY by design: expectations are not observations.'])
    wb.save(out_path)
    return len(chosen), missing, len(rows)


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--db', required=True)
    parser.add_argument('--from', dest='date_from', required=True)
    parser.add_argument('--to', dest='date_to', required=True)
    parser.add_argument('--out', required=True)
    args = parser.parse_args(argv)
    if not os.path.exists(args.db):
        print('ERROR: database not found at %s' % args.db)
        return 2
    n, missing, total = build(args.db, args.date_from, args.date_to, args.out)
    print('records in period: %d; cases: %d; absent classes: %s'
          % (total, n, ', '.join(missing) or '-'))
    print('written: %s' % args.out)
    return 0


if __name__ == '__main__':
    sys.exit(main())
