# -*- coding: utf-8 -*-
"""DJI-AREA-POLYGON-AUDIT-001: годен ли сохранённый контур поля как эталон.

Шаг 0 перед polygon closure. Closure собирается сравнивать объединение
внесения с площадью контура поля, то есть объявить контур ФИЗИЧЕСКОЙ
эталонной ссылкой. Прежде чем это делать, надо знать две вещи, которых
сейчас не знает никто:

1. целы ли сохранённые полигоны -- замкнуты ли кольца, нет ли
   самопересечений, извлекается ли вообще PlantZone;
2. какова ПОГРЕШНОСТЬ контура -- насколько площадь, посчитанная по его
   кольцам, расходится с площадью, которую DJI объявляет в том же узле
   каталога.

Без второго пункта любая полоса приёмки у closure (условные 97-103%) взята
с потолка: если сам контур врёт на два процента, полоса шириной в три
процента измеряет собственную линейку, а не ширину прохода.

[REASON]: почему этот аудит вообще нужен отдельно -- долг A21. Валидацию
геометрии (кольца, самопересечения, площадь) умеет `describe_geometry` из
`drone_collector/geometry.py`, но она живёт на пути `GeometryRun`, который
кладёт в очередь `KIND_FIELD_GEOMETRY`, и её НИКТО не дренажит. Путь,
который реально доезжает до хранилища (`download_snapshot_geometries`),
проверяет только размер, md5 и маркеры секретов, а приёмник
(`dji_area/store.py`) вызывает упрощённый `field.rings_from_geojson`: он
достаёт кольца, но не проверяет ни замкнутость, ни самопересечение, и
площадь не считает. Значит настоящий валидатор по сохранённым 6171
полигонам не проходил ни разу. Этот скрипт прогоняет его -- ЧИТАЯ, ничего
не записывая.

Что ОТВЕЧАЕТ этот аудит: пригоден ли контур как эталон и с какой точностью.
Что НЕ отвечает: ширину прохода, closure, классификацию внесения. Это
Шаги 1-3, и подменять их этим числом нельзя.

[REASON]: сравнение идёт со ВСЕМИ тремя объявленными числами
(`totalArea`, `workArea`, `totalArea - obstacleArea`), а не с одним
заранее выбранным. Какое из них соответствует сумме PlantZone -- это
предположение, и если проверять только его, то при несовпадении вывод
"полигон плохой" будет неотличим от вывода "мы сравнили не с тем полем".
Пусть распределение само покажет, за каким числом идёт геометрия.

Только чтение: база открывается в режиме `mode=ro` плюс `query_only`, ни
одного INSERT/UPDATE/DELETE в файле нет. Отката не требуется.

Запуск (PowerShell, на сервере):

    Set-Location C:\\transport-report
    & "C:\\Program Files\\Python314\\python.exe" tools\\dji_polygon_audit_001.py

Результат -- каталог `C:\\VehicleSoft_Audits\\DJI_POLYGON_AUDIT\\<штамп>` с
`POLYGON_AUDIT.csv`, `POLYGON_AUDIT_SUMMARY.json` и `VERDICT.txt`.
В консоль только ASCII; кириллица уходит в `VERDICT.txt` (UTF-8).
"""
import argparse
import csv
import io
import json
import math
import os
import sqlite3
import sys
from datetime import datetime

AUDIT_ID = 'DJI-AREA-POLYGON-AUDIT-001'
PARSER_NOTE = 'describe_geometry from drone_collector/geometry.py'

# 1 му = 2000/3 м2. Та же константа, что в tools/dji_area_block1b.py:77.
# [REASON]: не «примерно 666». Округление до 666.0 даёт систематический
# сдвиг 0.1%, а мы здесь как раз измеряем расхождение в единицах процента:
# ошибка линейки попала бы в результат измерения линейки.
MU_M2 = 2000.0 / 3.0

DEFAULT_DB = os.path.join('instance', 'transport.db')
DEFAULT_OUT = r'C:\VehicleSoft_Audits\DJI_POLYGON_AUDIT'

# funcType, которые считаются полем. Ровно тот же набор, что у приёмника в
# `dji_area/field.py::rings_from_geojson`: None трактуется как PlantZone.
PLANT_FUNC_TYPES = (None, 'PlantZone')
OBSTACLE_FUNC_TYPES = ('ObstacleZone',)

REQUIRED_TABLES = ('dji_land_geometries', 'dji_land_revisions')


# ─── откуда импортировать валидатор ─────────────────────────────────────────

def repo_root(explicit=None):
    """Корень рабочей копии, из которой берётся `drone_collector.geometry`.

    [REASON]: Python кладёт в `sys.path[0]` каталог СКРИПТА, а не рабочий
    каталог. Пока файл лежит в `<repo>/tools/`, это одно и то же по сути --
    корень на уровень выше. Но аудит делается ОДИН раз и его естественно
    унести в отдельный каталог, чтобы не трогать рабочую копию прода: там
    `import drone_collector` падает, хотя `Set-Location C:\transport-report`
    и выглядит достаточным. Поэтому корень ищется явно, а не предполагается.
    """
    if explicit:
        return os.path.abspath(explicit)
    here = os.path.dirname(os.path.abspath(__file__))
    beside = os.path.dirname(here)
    if os.path.isdir(os.path.join(beside, 'drone_collector')):
        return beside
    return os.path.abspath(os.getcwd())


def ensure_validator(root):
    """Положить корень в sys.path и убедиться, что валидатор импортируется."""
    if root not in sys.path:
        sys.path.insert(0, root)
    try:
        from drone_collector.geometry import describe_geometry     # noqa: F401
    except ImportError as exc:
        print('ERROR: cannot import drone_collector.geometry from %s (%s).'
              % (root, exc))
        print('Point --repo at the worktree that holds drone_collector, '
              'for example: --repo C:\\transport-report')
        sys.exit(2)


# ─── чтение базы ─────────────────────────────────────────────────────────────

def connect_read_only(path):
    """Соединение, которое физически не может писать.

    [REASON]: `sqlite3.connect(path)` СОЗДАЁТ пустой файл, если базы нет, и
    тогда аудит бодро отчитается о нуле полигонов на пустышке, которую сам
    же и завёл. URI с `mode=ro` на отсутствующем файле честно падает, а
    `query_only` снимает последнюю возможность записи, если в коде однажды
    появится INSERT.
    """
    if not os.path.exists(path):
        print('ERROR: database not found at %s - refusing to run.' % path)
        sys.exit(2)
    uri = 'file:%s?mode=ro' % path.replace('?', '%3f').replace('#', '%23')
    con = sqlite3.connect(uri, uri=True)
    con.row_factory = sqlite3.Row
    con.execute('PRAGMA query_only = ON')
    return con


def require_tables(con):
    have = {row[0] for row in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    missing = [name for name in REQUIRED_TABLES if name not in have]
    if missing:
        print('ERROR: this database has no %s - wrong database?'
              % ', '.join(missing))
        sys.exit(2)


def current_revisions(con):
    """Последняя ревизия каждой земли: {land_uuid: row}.

    [REASON]: заголовочные числа считаются по ТЕКУЩИМ ревизиям, потому что
    именно их увидит closure. Историческая ревизия с битым полигоном -- тоже
    находка, но она не действие на сегодня, и смешивать их в одну цифру
    значит получить тревогу, которая горит всегда.

    [REASON]: JOIN по MAX(id), а не «плоский SELECT, последний побеждает в
    словаре». Проверено: на SQLite обе формы дают один результат, потому что
    скан без WHERE идёт в порядке rowid, и мутация «убрать JOIN» набор НЕ
    роняет -- она эквивалентна. JOIN оставлен не ради поведения, а потому
    что порядок скана ничем не обещан: он деталь реализации, а не контракт.
    Тест на это не ставится: проверка, дающая одинаковый результат при
    верном и неверном коде, проверкой не является.
    """
    rows = {}
    for row in con.execute(
            'SELECT r.* FROM dji_land_revisions AS r '
            'JOIN (SELECT land_uuid, MAX(id) AS last_id '
            '        FROM dji_land_revisions GROUP BY land_uuid) AS cur '
            '  ON cur.last_id = r.id'):
        rows[row['land_uuid']] = row
    return rows


def geometry_bodies(con):
    """Итератор по телам полигонов. Курсором, не fetchall.

    [REASON]: 6171 тело по десятки килобайт -- это сотни мегабайт, если
    прочитать список целиком. На сервере с работающей службой это лишнее.
    """
    cur = con.execute(
        'SELECT content_md5, size_bytes, md5_verified, parse_status, '
        '       ring_count, body_blob '
        '  FROM dji_land_geometries ORDER BY id')
    while True:
        row = cur.fetchone()
        if row is None:
            return
        yield row


# ─── разбор одного полигона ──────────────────────────────────────────────────

def describe_body(blob):
    """(описание, причины, ошибка). Ошибка -- это про чтение, не про геометрию."""
    from drone_collector.geometry import describe_geometry

    try:
        text = bytes(blob).decode('utf-8')
    except Exception as exc:                                   # noqa: BLE001
        return None, [], 'undecodable: %s' % type(exc).__name__
    try:
        document = json.loads(text)
    except Exception as exc:                                   # noqa: BLE001
        return None, [], 'not json: %s' % type(exc).__name__
    try:
        description, reasons = describe_geometry(document)
    except Exception as exc:                                   # noqa: BLE001
        return None, [], 'describe failed: %s' % type(exc).__name__
    return description, list(reasons or ()), None


def split_areas(description):
    """(площадь поля м2, площадь препятствий м2, прочее м2, типы).

    `describe_geometry` суммирует ВСЕ фигуры, включая ObstacleZone, поэтому
    его `area_ha` для сверки с `totalArea` не годится: разложение по
    funcType делается здесь.
    """
    plant = obstacle = other = 0.0
    types = []
    for shape in (description.get('shapes') or ()):
        area_m2 = float(shape.get('area_ha') or 0.0) * 10000.0
        func = shape.get('func_type')
        types.append(func)
        if func in PLANT_FUNC_TYPES:
            plant += area_m2
        elif func in OBSTACLE_FUNC_TYPES:
            obstacle += area_m2
        else:
            other += area_m2
    return plant, obstacle, other, types


# ─── объявленные числа ───────────────────────────────────────────────────────

def declared_m2(revision):
    """(total, work, obstacle) в м2 и статус единицы измерения.

    [REASON]: единица НЕ угадывается. Колонка `area_unit` по умолчанию
    'mu', но если там окажется что-то другое, пересчёт по му даст ошибку в
    667 раз и при этом вполне убедительные числа. Неизвестная единица -- это
    UNIT_UNKNOWN и строка выпадает из статистики, а не тихо участвует в ней.
    """
    unit = (revision['area_unit'] or '').strip().lower()
    if unit in ('mu', ''):
        factor = MU_M2
    elif unit in ('m2', 'sqm', 'm^2'):
        factor = 1.0
    elif unit == 'ha':
        factor = 10000.0
    else:
        return None, None, None, 'UNIT_UNKNOWN:%s' % unit

    def scale(value):
        return None if value is None else float(value) * factor

    return (scale(revision['total_area_raw']),
            scale(revision['work_area_raw']),
            scale(revision['obstacle_area_raw']), 'OK')


def relative_pct(computed, declared):
    """(computed - declared) / declared * 100, либо None."""
    if computed is None or declared is None or declared <= 0:
        return None
    if not math.isfinite(computed):
        return None
    return (computed - declared) / declared * 100.0


# ─── статистика ──────────────────────────────────────────────────────────────

def percentile(values, share):
    """Перцентиль по ближайшему рангу. Пустой список -> None."""
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    index = int(round(share * (len(ordered) - 1)))
    return ordered[max(0, min(index, len(ordered) - 1))]


def spread(values):
    """Сводка по знаковым расхождениям в процентах."""
    absolute = [abs(v) for v in values]
    return {
        'n': len(values),
        'median_signed_pct': percentile(values, 0.5),
        'median_abs_pct': percentile(absolute, 0.5),
        'p90_abs_pct': percentile(absolute, 0.9),
        'p99_abs_pct': percentile(absolute, 0.99),
        'max_abs_pct': max(absolute) if absolute else None,
        'within_1pct': sum(1 for v in absolute if v <= 1.0),
        'within_2pct': sum(1 for v in absolute if v <= 2.0),
        'within_5pct': sum(1 for v in absolute if v <= 5.0),
    }


# ─── аудит ───────────────────────────────────────────────────────────────────

CSV_COLUMNS = (
    'land_uuid', 'content_md5', 'revision_id', 'scope', 'name',
    'size_bytes', 'md5_verified', 'stored_parse_status', 'stored_ring_count',
    'geometry_status', 'shape_count', 'func_types',
    'plant_area_m2', 'obstacle_area_m2', 'other_area_m2',
    'declared_total_m2', 'declared_work_m2', 'declared_obstacle_m2',
    'unit_status',
    'pct_vs_total', 'pct_vs_work', 'pct_vs_total_minus_obstacle',
    'reasons',
)


def audit(con):
    """Возвращает (строки, сводка). Ничего не пишет в базу."""
    revisions = current_revisions(con)
    by_md5 = {}
    for row in revisions.values():
        md5 = (row['geometry_md5'] or '').lower()
        if md5:
            by_md5.setdefault(md5, []).append(row)

    total_lands = len(revisions)
    lands_without_md5 = sum(
        1 for row in revisions.values() if not (row['geometry_md5'] or ''))

    rows = []
    counters = {
        'geometries_in_store': 0,
        'geometries_unreadable': 0,
        'geometries_rejected_by_validator': 0,
        'geometries_ok': 0,
        'geometries_not_referenced_by_current': 0,
        'current_lands': total_lands,
        'current_lands_without_geometry_md5': lands_without_md5,
        'current_lands_with_body': 0,
        'md5_not_verified': 0,
        'no_plant_zone': 0,
    }
    # [REASON]: причины считаются ДВАЖДЫ и раздельно. Одно тело может нести
    # несколько зон, и у каждой своё кольцо, поэтому число причин больше
    # числа отвергнутых тел -- на живом каталоге 2543 кольца на 786 тел.
    # Первая редакция печатала число причин под подписью «сколько тел», и в
    # отчёте рядом стояли 786 и 2543 как будто про одно и то же.
    reason_bodies = {}
    reason_rings = {}
    read_errors = {}
    samples = {'pct_vs_total': [], 'pct_vs_work': [],
               'pct_vs_total_minus_obstacle': []}

    for body in geometry_bodies(con):
        counters['geometries_in_store'] += 1
        md5 = (body['content_md5'] or '').lower()
        holders = by_md5.get(md5) or []
        if not holders:
            counters['geometries_not_referenced_by_current'] += 1
        if not body['md5_verified']:
            counters['md5_not_verified'] += 1

        description, reasons, error = describe_body(body['body_blob'])
        if error is not None:
            counters['geometries_unreadable'] += 1
            read_errors[error] = read_errors.get(error, 0) + 1
            status = 'UNREADABLE'
            plant = obstacle = other = None
            types = []
        else:
            plant, obstacle, other, types = split_areas(description)
            for reason in reasons:
                key = _reason_key(reason)
                reason_rings[key] = reason_rings.get(key, 0) + 1
            for key in {_reason_key(reason) for reason in reasons}:
                reason_bodies[key] = reason_bodies.get(key, 0) + 1
            if reasons:
                counters['geometries_rejected_by_validator'] += 1
                status = 'REJECTED'
            elif plant <= 0:
                counters['no_plant_zone'] += 1
                status = 'NO_PLANT_ZONE'
            else:
                counters['geometries_ok'] += 1
                status = 'OK'

        # Одна строка на КАЖДУЮ текущую землю, которая ссылается на это тело:
        # объявленные числа принадлежат земле, а не полигону, и один полигон
        # может обслуживать несколько земель.
        targets = holders or [None]
        for revision in targets:
            row = _row_for(body, revision, status, description, plant,
                           obstacle, other, types, reasons, error)
            rows.append(row)
            if revision is not None:
                counters['current_lands_with_body'] += 1
            if status != 'OK' or revision is None:
                continue
            if row['unit_status'] != 'OK':
                continue
            for key in samples:
                value = row[key]
                if value is not None:
                    samples[key].append(value)

    summary = {
        'audit_id': AUDIT_ID,
        'parser': PARSER_NOTE,
        'generated_at_utc': datetime.utcnow().isoformat(timespec='seconds'),
        'mu_m2': MU_M2,
        'counters': counters,
        'validator_reasons_bodies': dict(sorted(reason_bodies.items(),
                                                key=lambda kv: -kv[1])),
        'validator_reasons_rings': dict(sorted(reason_rings.items(),
                                               key=lambda kv: -kv[1])),
        'read_errors': dict(sorted(read_errors.items(),
                                   key=lambda kv: -kv[1])),
        'agreement': {key: spread(values) for key, values in samples.items()},
    }
    summary['best_match'] = _best_match(summary['agreement'])
    return rows, summary


def _reason_key(reason):
    """Причина без номера фигуры: 'shape 3 outer ring: ...' -> 'outer ring: ...'."""
    text = str(reason)
    for marker in (' outer ring: ', ' hole '):
        position = text.find(marker)
        if position != -1:
            return text[position:].strip()
    return text


def _row_for(body, revision, status, description, plant, obstacle, other,
             types, reasons, error):
    declared_total = declared_work = declared_obstacle = None
    unit_status = 'NO_CURRENT_REVISION'
    if revision is not None:
        declared_total, declared_work, declared_obstacle, unit_status = (
            declared_m2(revision))
    total_minus_obstacle = (
        None if (declared_total is None or declared_obstacle is None)
        else declared_total - declared_obstacle)
    return {
        'land_uuid': (revision['land_uuid'] if revision is not None else ''),
        'content_md5': body['content_md5'],
        'revision_id': (revision['id'] if revision is not None else ''),
        'scope': ('CURRENT_REVISION' if revision is not None
                  else 'BODY_WITHOUT_CURRENT_LAND'),
        'name': (revision['name'] if revision is not None else ''),
        'size_bytes': body['size_bytes'],
        'md5_verified': int(bool(body['md5_verified'])),
        'stored_parse_status': body['parse_status'],
        'stored_ring_count': body['ring_count'],
        'geometry_status': status,
        'shape_count': (len(description.get('shapes') or ())
                        if description else 0),
        'func_types': '|'.join(sorted({str(t) for t in types})),
        'plant_area_m2': _round(plant),
        'obstacle_area_m2': _round(obstacle),
        'other_area_m2': _round(other),
        'declared_total_m2': _round(declared_total),
        'declared_work_m2': _round(declared_work),
        'declared_obstacle_m2': _round(declared_obstacle),
        'unit_status': unit_status,
        'pct_vs_total': _round(relative_pct(plant, declared_total), 4),
        'pct_vs_work': _round(relative_pct(plant, declared_work), 4),
        'pct_vs_total_minus_obstacle': _round(
            relative_pct(plant, total_minus_obstacle), 4),
        'reasons': ' | '.join(str(r) for r in reasons) or (error or ''),
    }


def _round(value, digits=2):
    return None if value is None else round(float(value), digits)


def _best_match(agreement):
    """За каким объявленным числом геометрия идёт ближе всего.

    [REASON]: вывод делается по МЕДИАНЕ модуля, а не по знаковой средней.
    Знаковая средняя гасит симметричный разброс и объявила бы согласие там,
    где половина полей врёт в плюс, а половина в минус.
    """
    best, best_value = None, None
    for key, stats in agreement.items():
        value = stats.get('median_abs_pct')
        if stats.get('n', 0) < 1 or value is None:
            continue
        if best_value is None or value < best_value:
            best, best_value = key, value
    return {'field': best, 'median_abs_pct': best_value}


# ─── вывод ───────────────────────────────────────────────────────────────────

def write_csv(path, rows):
    with io.open(path, 'w', encoding='utf-8', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=list(CSV_COLUMNS))
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key) for key in CSV_COLUMNS})


def write_json(path, summary):
    with io.open(path, 'w', encoding='utf-8') as handle:
        handle.write(json.dumps(summary, ensure_ascii=False, indent=2,
                                sort_keys=False))


def verdict_lines(summary):
    """Кириллический разбор для VERDICT.txt. В консоль это не идёт."""
    counters = summary['counters']
    agreement = summary['agreement']
    best = summary['best_match']
    out = []
    add = out.append
    add('%s -- пригоден ли контур поля как эталон' % AUDIT_ID)
    add('Сформировано (UTC): %s' % summary['generated_at_utc'])
    add('Валидатор: %s' % PARSER_NOTE)
    add('')
    add('ЦЕЛОСТНОСТЬ ПОЛИГОНОВ')
    add('  тел в хранилище ............................ %d'
        % counters['geometries_in_store'])
    add('  не читаются (не UTF-8 / не JSON) ........... %d'
        % counters['geometries_unreadable'])
    add('  отвергнуты валидатором ..................... %d'
        % counters['geometries_rejected_by_validator'])
    add('  без PlantZone (поля в документе нет) ....... %d'
        % counters['no_plant_zone'])
    add('  годны ...................................... %d'
        % counters['geometries_ok'])
    add('  md5 не подтверждён ......................... %d'
        % counters['md5_not_verified'])
    add('  не нужны ни одной текущей земле ............ %d'
        % counters['geometries_not_referenced_by_current'])
    add('')
    add('ТЕКУЩИЕ ЗЕМЛИ')
    add('  всего ...................................... %d'
        % counters['current_lands'])
    add('  без ссылки на полигон ...................... %d'
        % counters['current_lands_without_geometry_md5'])
    add('  с телом полигона ........................... %d'
        % counters['current_lands_with_body'])
    if summary['validator_reasons_bodies']:
        add('')
        add('ПОЧЕМУ ОТВЕРГНУТЫ (тел / колец -- одно тело может нести')
        add('несколько зон, и у каждой своё кольцо)')
        for reason, bodies in summary['validator_reasons_bodies'].items():
            rings = (summary['validator_reasons_rings'] or {}).get(reason, 0)
            add('  %-52s %5d / %5d' % (reason[:52], bodies, rings))
    if summary['read_errors']:
        add('')
        add('ОШИБКИ ЧТЕНИЯ')
        for error, count in summary['read_errors'].items():
            add('  %-60s %d' % (error[:60], count))
    add('')
    add('ТОЧНОСТЬ КОНТУРА -- расхождение площади колец с объявленной DJI')
    add('  Знак: плюс означает, что по кольцам площадь БОЛЬШЕ объявленной.')
    for key in ('pct_vs_total', 'pct_vs_work',
                'pct_vs_total_minus_obstacle'):
        stats = agreement.get(key) or {}
        add('')
        add('  %s (n=%s)' % (key, stats.get('n')))
        if not stats.get('n'):
            add('    сравнивать нечего')
            continue
        add('    медиана знаковая ....... %s %%'
            % _fmt(stats.get('median_signed_pct')))
        add('    медиана модуля ......... %s %%'
            % _fmt(stats.get('median_abs_pct')))
        add('    p90 модуля ............. %s %%'
            % _fmt(stats.get('p90_abs_pct')))
        add('    p99 модуля ............. %s %%'
            % _fmt(stats.get('p99_abs_pct')))
        add('    максимум модуля ........ %s %%'
            % _fmt(stats.get('max_abs_pct')))
        add('    в пределах 1 %% ......... %d из %d'
            % (stats.get('within_1pct') or 0, stats.get('n')))
        add('    в пределах 2 %% ......... %d из %d'
            % (stats.get('within_2pct') or 0, stats.get('n')))
        add('    в пределах 5 %% ......... %d из %d'
            % (stats.get('within_5pct') or 0, stats.get('n')))
    add('')
    add('КАКОМУ ОБЪЯВЛЕННОМУ ЧИСЛУ СООТВЕТСТВУЕТ СУММА PlantZone')
    add('  ближе всего: %s (медиана модуля %s %%)'
        % (best.get('field'), _fmt(best.get('median_abs_pct'))))
    add('  [REASON] сравнение шло со всеми тремя нарочно: если проверять')
    add('  только одно заранее выбранное, то "полигон плохой" и "мы сверили')
    add('  не с тем полем" снаружи выглядят одинаково.')
    add('')
    add('КАК ЭТО ЧИТАТЬ ДЛЯ POLYGON CLOSURE')
    add('  Медиана модуля по лучшему полю -- это НИЖНЯЯ граница того, что')
    add('  closure вообще способен различить. Полосу приёмки уже этой')
    add('  величины назначать нельзя: она будет измерять линейку, а не')
    add('  ширину прохода. Отвергнутые и непрочитанные полигоны в closure')
    add('  не должны участвовать вовсе: их площадь не определена.')
    add('')
    add('ЧЕГО ЭТОТ АУДИТ НЕ ДОКАЗЫВАЕТ')
    add('  Он не проверяет ширину прохода и не заменяет closure.')
    add('  Согласие площади колец с объявленной DJI означает лишь')
    add('  внутреннюю непротиворечивость каталога: оба числа приходят от')
    add('  DJI. Независимой геодезической проверки контура здесь нет, и')
    add('  если контур нарисован по снимку, обе величины могут быть')
    add('  согласованно смещены. Это названо прямо, а не подразумевается.')
    return out


def _fmt(value):
    return 'н/д' if value is None else ('%.4f' % value)


def console_lines(summary):
    """ASCII-сводка. Никакой кириллицы: консоль сервера её ломает."""
    counters = summary['counters']
    best = summary['best_match']
    stats = (summary['agreement'].get(best.get('field')) or {}
             if best.get('field') else {})
    out = ['=== %s ===' % AUDIT_ID,
           'bodies=%d ok=%d rejected=%d unreadable=%d no_plant=%d'
           % (counters['geometries_in_store'], counters['geometries_ok'],
              counters['geometries_rejected_by_validator'],
              counters['geometries_unreadable'], counters['no_plant_zone']),
           'current_lands=%d with_body=%d without_md5=%d'
           % (counters['current_lands'], counters['current_lands_with_body'],
              counters['current_lands_without_geometry_md5'])]
    if best.get('field'):
        out.append('best_match=%s n=%s median_abs=%.4f%% p90_abs=%s%%'
                   % (best['field'], stats.get('n'),
                      best.get('median_abs_pct') or 0.0,
                      _fmt(stats.get('p90_abs_pct'))))
        out.append('within_2pct=%d/%d'
                   % (stats.get('within_2pct') or 0, stats.get('n') or 0))
    else:
        out.append('best_match=NONE - nothing could be compared')
    return out


def main(argv=None):
    parser = argparse.ArgumentParser(
        description='Read-only audit: is the stored field contour usable as '
                    'a reference for polygon closure?')
    parser.add_argument('--db', default=DEFAULT_DB,
                        help='path to transport.db (default: %s)' % DEFAULT_DB)
    parser.add_argument('--out-dir', dest='out_dir', default=DEFAULT_OUT,
                        help='where to put the run directory')
    parser.add_argument('--repo', default=None,
                        help='worktree that holds drone_collector; needed '
                             'only when this script lives outside it')
    args = parser.parse_args(argv)

    ensure_validator(repo_root(args.repo))
    con = connect_read_only(args.db)
    try:
        require_tables(con)
        rows, summary = audit(con)
    finally:
        con.close()

    stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    run_dir = os.path.join(args.out_dir, stamp)
    os.makedirs(run_dir, exist_ok=True)
    csv_path = os.path.join(run_dir, 'POLYGON_AUDIT.csv')
    json_path = os.path.join(run_dir, 'POLYGON_AUDIT_SUMMARY.json')
    verdict_path = os.path.join(run_dir, 'VERDICT.txt')
    write_csv(csv_path, rows)
    write_json(json_path, summary)
    with io.open(verdict_path, 'w', encoding='utf-8') as handle:
        handle.write('\n'.join(verdict_lines(summary)) + '\n')

    for line in console_lines(summary):
        print(line)
    print('RUN_DIR=%s' % run_dir)
    print('ROWS=%d' % len(rows))
    print('POLYGON_AUDIT=PASS')
    return 0


if __name__ == '__main__':
    sys.exit(main())
