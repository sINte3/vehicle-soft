# -*- coding: utf-8 -*-
"""tools/dji_card_coverage_pilot.py -- пилот DRONE-CARD-COVERAGE-001, только чтение.

Пилот отвечает на вопрос владельца до решения о полном сборе карточек
вылетов DJI: сколько из вылетов «карточка не собрана» (NO_CARD) после
получения карточки становятся подтверждённой работой поля (EXACT или
IDENTIFIED), и во что превращаются остальные. Документ --
`docs/DRONE_CARD_COVERAGE_001.md`.

Этот инструмент НЕ собирает ничего и к DJI не обращается. Сбор -- штатный
`python -m drone_collector.main --sources --ids-file ... --send-sources`
(рабочая машина, отправка только на площадку); приём -- штатный
`/drones/api/source_sync` площадки; пересчёт -- штатный
`tools/dji_area_recalc.py --apply --flight-id ...`. Здесь -- только то, чего
у штатного пути нет: замороженная выборка до сбора и сверка после.

  plan            по копии базы: перепись периода, когорта NO_CARD,
                  контрольная когорта (карточка уже есть), стратифицированная
                  детерминированная выборка не больше 500 и канарейка -- её
                  первые 50. Манифест замораживается: второй запуск с тем же
                  входом даёт те же байты, с другим -- отказ.
  fingerprint     отпечатки, которые пилот обязан не изменить: RAW
                  (`drone_flights.area_ha`) всех вылетов, решения
                  администратора, прежние ревизии источников, текущие
                  привязки и расчёты вылетов периода.
  collector-stats разбор журнала сборщика: кто посещён, с каким итогом,
                  сколько времени, признаки отказов DJI (403/429/капча/сессия).
  measure         по базе площадки после приёма и пересчёта: состояние
                  каждого вылета манифеста до и после, конверсия в
                  подтверждённые с 95 % интервалом Уилсона, разрезы по борту
                  и неделе, сравнение с контрольной когортой, проекция на всю
                  когорту и ворота неизменности.

Принадлежность к полю и состояние -- `dji_area.field_view.classify` поверх
`dji_area.field_store`, те же функции, что у экранов и
`tools/dji_field_census.py`. Отдельной копии правил здесь нет, и `plan`
сверяет свои счётчики с переписью: расхождение -- код 5.

База открывается только `mode=ro`; production (`C:\\transport-report`)
инструмент не открывает вовсе. Вывод в консоль -- ASCII. Ни тел источников,
ни путей хранения, ни подписанных ссылок, ни токенов инструмент не читает.

Запуск (рабочий каталог -- корень репозитория):

  python tools\\dji_card_coverage_pilot.py plan --db D:\\...\\snapshot.db --out-dir D:\\...\\plan
  python tools\\dji_card_coverage_pilot.py fingerprint --db D:\\...\\snapshot.db --out D:\\...\\plan\\fingerprint_before.json
  python tools\\dji_card_coverage_pilot.py collector-stats --log C:\\...\\collector.log --ids C:\\...\\canary_ids.txt --out C:\\...\\collector_stats.json
  python tools\\dji_card_coverage_pilot.py measure --db C:\\transport-report-staging\\instance\\transport.db --plan-dir D:\\...\\plan --stage canary --out-dir D:\\...\\measure_canary

Коды возврата: 0 -- выполнено; 1 -- ошибка аргументов; 2 -- базы нет (файл
не создаётся); 3 -- нет таблиц слоя доказательств; 4 -- отказ (production,
превышение потолка, манифест уже заморожен иначе); 5 -- ворота не прошли
(счётчики разошлись с переписью, RAW или чужие строки изменились);
6 -- журнал сборщика говорит «остановиться» (отказы DJI).
"""

import argparse
import csv
import hashlib
import io
import json
import math
import os
import re
import sqlite3
import sys

from datetime import date, datetime, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from dji_area import AREA_ALGORITHM_VERSION, FIELD_RESOLVER_VERSION  # noqa: E402
from dji_area import field_store as fs  # noqa: E402
from dji_area import field_view as fv  # noqa: E402

PILOT_ID = 'DRONE-CARD-COVERAGE-001'
TOOL_VERSION = 'card-coverage-pilot-1'

# [REASON]: потолок пилота задан владельцем -- не больше 500 вылетов, меньше
# 10 % из ~6 000 без карточки. Он зашит, а не только аргумент: `--cap` может
# его уменьшить, но не превысить, иначе одна опечатка в блоке превратила бы
# пилот в исторический сбор, на который разрешения нет.
HARD_CAP = 500
DEFAULT_CANARY = 50
DEFAULT_FROM = '2026-09-01'
DEFAULT_TO = '2026-09-30'
LOCAL_OFFSET = timedelta(hours=5)

EXIT_OK = 0
EXIT_USAGE = 1
EXIT_NO_DATABASE = 2
EXIT_NO_TABLES = 3
EXIT_REFUSED = 4
EXIT_GATE = 5
EXIT_COLLECTOR_STOP = 6

# Имя каталога production-копии кода. Площадка (`transport-report-staging`)
# и папки копий (`transport-report-backups`) этим правилом не задеты.
PRODUCTION_FOLDER = 'transport-report'

MANIFEST_NAME = 'pilot_manifest.csv'
PLAN_NAME = 'plan.json'
COHORT_NAME = 'cohort_no_card.csv'
CONTROL_NAME = 'control_with_card.csv'
CANARY_IDS = 'canary_ids.txt'
PILOT_IDS = 'pilot_ids.txt'
FINGERPRINT_BEFORE = 'fingerprint_before.json'

FLIGHT_COLUMNS = ('flight_id', 'local_datetime', 'local_date', 'iso_week',
                  'stratum', 'unit_number', 'nickname', 'area_ha', 'state',
                  'reason', 'has_calc', 'has_card', 'selection_key')
MANIFEST_COLUMNS = ('manifest_order', 'flight_id', 'canary', 'stratum',
                    'local_date', 'iso_week', 'unit_number', 'area_ha',
                    'reason_selected', 'selection_key')
REASON_SELECTED = ('proportional allocation by stratum (largest remainder); '
                   'lowest sha256 selection key inside the stratum')

# Строки журнала сборщика (`drone_collector/logging_setup.py`, LOG_FORMAT):
# `2026-10-03 10:15:02,123 INFO drone_collector: Flight 715984635: V4 (...)`.
LOG_TIME = r'^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})(?:,\d+)?\s'
RE_FLIGHT_STATUS = re.compile(
    LOG_TIME + r'.*Flight (\d+): (V4|NO_V4_URL|NO_V4|V4_FAILED) \((.*)\)\s*$')
RE_PAGE_ERROR = re.compile(
    LOG_TIME + r'.*Flight (\d+): the record page did not open \((.*)\)')
RE_SUMMARY = re.compile(r'RUN SUMMARY (.*)$')
# [REASON]: признаки, на которых пилот ОСТАНАВЛИВАЕТСЯ, а не обходит защиту:
# истёкшая сессия, отказ в доступе, ограничение частоты, проверка «вы не
# робот». Сборщик сам останавливается после трёх неоткрывшихся страниц
# подряд; эти строки -- то, что владелец должен увидеть в итоге.
STOP_MARKERS = (
    ('SESSION', re.compile(r'no longer signed in|SessionExpired|session (is )?'
                           r'(missing|expired)', re.I)),
    ('HTTP_429', re.compile(r'\b429\b|too many requests|rate.?limit', re.I)),
    ('HTTP_403', re.compile(r'\bHTTP 403\b|\b403 Forbidden\b|forbidden', re.I)),
    ('CAPTCHA', re.compile(r'captcha|verify you are human|challenge', re.I)),
    ('BROWSER_DEAD', re.compile(r'browser is not usable', re.I)),
)


class UsageError(Exception):
    pass


class Refused(Exception):
    pass


class GateFailed(Exception):
    pass


# ─── Общие помощники ─────────────────────────────────────────────────────────

def is_production_path(path):
    """True, если путь лежит в каталоге production-копии кода.

    [REASON]: пилот не трогает production ни одной операцией, даже чтением
    -- базой пилота служит online-копия. Сравнивается имя КАЖДОЙ части пути
    целиком, без регистра: `transport-report-staging` и
    `transport-report-backups` -- не production.
    """
    parts = [p.lower() for p in re.split(r'[\\/]+', os.path.abspath(path)) if p]
    return PRODUCTION_FOLDER in parts


def open_read_only(path):
    if is_production_path(path):
        raise Refused('%s is inside the production folder -- the pilot reads '
                      'an online copy, never production itself' % path)
    if not os.path.isfile(path):
        raise FileNotFoundError(path)
    uri = 'file:%s?mode=ro' % os.path.abspath(path).replace(
        '\\', '/').replace('?', '%3f').replace('#', '%23')
    con = sqlite3.connect(uri, uri=True, timeout=30)
    con.row_factory = sqlite3.Row
    return con


def parse_day(text, name):
    try:
        return datetime.strptime(text, '%Y-%m-%d').date()
    except (TypeError, ValueError):
        raise UsageError('%s must be YYYY-MM-DD, got %r' % (name, text))


def utc_bounds(date_from, date_to):
    """[from 00:00, to+1 00:00) по UTC+5 -> UTC, как в переписи."""
    start = datetime.combine(date_from, datetime.min.time()) - LOCAL_OFFSET
    end = (datetime.combine(date_to + timedelta(days=1), datetime.min.time())
           - LOCAL_OFFSET)
    return start, end


def parse_started(text):
    return datetime.strptime(str(text)[:19], '%Y-%m-%d %H:%M:%S')


def selection_key(flight_id):
    """Устойчивый ключ отбора: sha256 от имени пилота и номера вылета.

    [REASON]: не `random` и не порядок строк в базе -- ключ воспроизводим на
    любой машине и не зависит ни от того, когда вылет попал в базу, ни от
    его площади, ни от чьего-либо выбора.
    """
    return hashlib.sha256(('%s|%d' % (PILOT_ID, int(flight_id)))
                          .encode('ascii')).hexdigest()


def stratum_of(unit_number, nickname, iso_week):
    if unit_number is not None:
        board = 'U%03d' % int(unit_number)
    elif nickname:
        # [REASON]: ник без машины -- кириллица; ASCII-замена склеила бы
        # разные ники в один слой «N:????». Короткий хэш ника различает их.
        board = 'N:%s' % hashlib.sha256(
            str(nickname).encode('utf-8')).hexdigest()[:10]
    else:
        board = 'UNKNOWN'
    return '%s|W%02d' % (board, iso_week)


def ascii_safe(text):
    return str(text).encode('ascii', 'replace').decode('ascii')


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as handle:
        for block in iter(lambda: handle.read(1 << 20), b''):
            digest.update(block)
    return digest.hexdigest()


def write_atomic(path, data):
    tmp = path + '.tmp'
    with open(tmp, 'wb') as handle:
        handle.write(data)
    os.replace(tmp, path)


def csv_bytes(columns, rows):
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator='\n')
    writer.writerow(columns)
    for row in rows:
        writer.writerow([row.get(c, '') if row.get(c) is not None else ''
                         for c in columns])
    return buf.getvalue().encode('utf-8')


def json_bytes(value):
    return (json.dumps(value, ensure_ascii=True, indent=1, sort_keys=True)
            + '\n').encode('ascii')


def ids_bytes(title, ids):
    lines = ['# %s -- %s' % (PILOT_ID, title),
             '# one DJI flight id per line; read by drone_collector --ids-file']
    lines.extend(str(int(i)) for i in ids)
    return ('\n'.join(lines) + '\n').encode('ascii')


def read_ids(path):
    out = []
    with open(path, encoding='utf-8-sig') as handle:
        for line in handle:
            text = line.split('#', 1)[0].strip()
            if text:
                out.append(int(text))
    return out


# ─── Состояние вылетов периода ───────────────────────────────────────────────

def period_flights(con, date_from, date_to):
    """Вылеты периода с состоянием привязки -- те же функции, что у экранов."""
    utc_start, utc_end = utc_bounds(date_from, date_to)
    # [REASON]: граница периода -- `field_store._period`, та же, что у
    # переписи: строка времени SQLAlchemy длиннее stdlib-строки, и своя
    # граница здесь разошлась бы с переписью на вылетах одной секунды.
    period_sql, params = fs._period('f.started_at', utc_start, utc_end)
    rows = con.execute(
        'SELECT f.dji_flight_id AS flight_id, f.started_at, f.nickname_raw, '
        'f.area_ha, u.number AS unit_number FROM drone_flights f '
        'LEFT JOIN drone_units u ON u.id = f.drone_unit_id WHERE 1 = 1'
        + period_sql + ' ORDER BY f.dji_flight_id', params).fetchall()
    ids = [int(r['flight_id']) for r in rows]
    attrs = fs.current_attributions(con, ids)
    evidence = fs.evidence_for(con, ids)
    calcs = current_calculations(con, ids)
    out = []
    for r in rows:
        fid = int(r['flight_id'])
        attr, ev = attrs.get(fid), evidence.get(fid)
        state, reason = fv.classify(attr, ev)
        local = parse_started(r['started_at']) + LOCAL_OFFSET
        week = local.date().isocalendar()[1]
        out.append({
            'flight_id': fid,
            'local_datetime': local.strftime('%Y-%m-%d %H:%M'),
            'local_date': local.strftime('%Y-%m-%d'),
            'iso_week': 'W%02d' % week,
            'stratum': stratum_of(r['unit_number'], r['nickname_raw'], week),
            'unit_number': r['unit_number'],
            'nickname': r['nickname_raw'] or '',
            'area_ha': r['area_ha'],
            'state': state,
            'reason': reason or '',
            'has_calc': 1 if fid in calcs else 0,
            'has_card': 1 if ev and ev.get('card_revision_id') else 0,
            'selection_key': selection_key(fid),
            '_attr': attr, '_evidence': ev, '_calc_id': calcs.get(fid),
        })
    return out, (utc_start, utc_end)


def current_calculations(con, flight_ids):
    out = {}
    for chunk in fs._chunks(list(flight_ids)):
        rows = con.execute(
            'SELECT id, flight_id FROM dji_area_calculations WHERE '
            'superseded_at IS NULL AND area_algorithm_version = ? AND '
            'flight_id IN (%s) ORDER BY id' % ','.join('?' * len(chunk)),
            [AREA_ALGORITHM_VERSION] + chunk).fetchall()
        for row in rows:
            out[int(row['flight_id'])] = int(row['id'])
    return out


def bucket_of(flights):
    bucket = fv.empty_census()
    for f in flights:
        fv.census_add(bucket, f['_attr'], f['_evidence'], bool(f['has_calc']))
    return bucket


def distribution(flights):
    """{состояние или причина: число} -- для отчётов и сравнения."""
    out = {}
    for f in flights:
        key = f['reason'] if f['state'] == fv.STATE_UNRESOLVED else f['state']
        out[key] = out.get(key, 0) + 1
    return dict(sorted(out.items()))


def confirmed_count(flights):
    return sum(1 for f in flights if f['state'] in fv.CONFIRMED_STATES)


# ─── Выборка ─────────────────────────────────────────────────────────────────

def allocate(strata_sizes, total):
    """Пропорциональное размещение методом наибольшего остатка.

    Ни один слой не получает больше, чем в нём есть; маленький слой не
    раздувается. Ничьи остатков решает имя слоя -- детерминированно.
    """
    population = sum(strata_sizes.values())
    total = min(total, population)
    if total <= 0:
        return dict.fromkeys(strata_sizes, 0)
    quota = {h: total * n / float(population) for h, n in strata_sizes.items()}
    alloc = {h: min(int(math.floor(q)), strata_sizes[h])
             for h, q in quota.items()}
    left = total - sum(alloc.values())
    order = sorted(strata_sizes, key=lambda h: (-(quota[h] - math.floor(quota[h])), h))
    while left > 0:
        moved = False
        for h in order:
            if left <= 0:
                break
            if alloc[h] < strata_sizes[h]:
                alloc[h] += 1
                left -= 1
                moved = True
        if not moved:
            break
    return alloc


def build_sample(cohort, cap, canary):
    """(манифест по порядку, размещение по слоям).

    Внутри слоя -- наименьшие ключи отбора. Порядок манифеста --
    по доле ранга внутри слоя, `(ранг + 0,5) / n_слоя`, затем по ключу:
    любой префикс манифеста (канарейка -- первые 50) сам распределён по
    слоям пропорционально, а не набран из первых бортов алфавита.
    """
    by_stratum = {}
    for f in cohort:
        by_stratum.setdefault(f['stratum'], []).append(f)
    sizes = {h: len(v) for h, v in by_stratum.items()}
    alloc = allocate(sizes, cap)
    chosen = []
    for h, items in by_stratum.items():
        items.sort(key=lambda f: f['selection_key'])
        n = alloc[h]
        for rank, f in enumerate(items[:n]):
            chosen.append(((rank + 0.5) / n, f['selection_key'], f))
    chosen.sort(key=lambda t: (t[0], t[1]))
    manifest = []
    for order, (_pos, _key, f) in enumerate(chosen, start=1):
        manifest.append({
            'manifest_order': order, 'flight_id': f['flight_id'],
            'canary': 1 if order <= canary else 0, 'stratum': f['stratum'],
            'local_date': f['local_date'], 'iso_week': f['iso_week'],
            'unit_number': f['unit_number'], 'area_ha': f['area_ha'],
            'reason_selected': REASON_SELECTED,
            'selection_key': f['selection_key']})
    return manifest, alloc


def reweighted_control(control, cohort_sizes):
    """Распределение контрольной когорты, перевзвешенное на слои NO_CARD.

    [REASON]: карточки сентября собирались не случайно -- суточный манифест
    берёт кандидатов экрана площади плюс малую контрольную выборку. Сырая
    доля подтверждённых среди уже собранных карточек поэтому не обязана
    представлять NO_CARD. Перевзвешивание по слоям убирает разницу в бортах
    и неделях, но не отбор по аномалии -- это сказано в отчёте.
    """
    by_stratum = {}
    for f in control:
        by_stratum.setdefault(f['stratum'], []).append(f)
    covered = {h: n for h, n in cohort_sizes.items() if h in by_stratum}
    total = float(sum(covered.values()))
    weights = {}
    for h, n in covered.items():
        items = by_stratum[h]
        for key, count in distribution(items).items():
            weights[key] = weights.get(key, 0.0) + (n / total) * count / len(items)
    return {'cohort_flights_in_covered_strata': int(total),
            'cohort_flights_total': int(sum(cohort_sizes.values())),
            'shares': {k: round(v, 4) for k, v in sorted(weights.items())}}


# ─── plan ────────────────────────────────────────────────────────────────────

def run_plan(args, out):
    date_from = parse_day(args.date_from, '--from')
    date_to = parse_day(args.date_to, '--to')
    if args.cap < 1 or args.cap > HARD_CAP:
        raise Refused('--cap must be 1..%d, got %d -- the pilot never asks for '
                      'more than %d flights' % (HARD_CAP, args.cap, HARD_CAP))
    if args.canary is None:
        args.canary = min(DEFAULT_CANARY, args.cap)
    if args.canary < 1 or args.canary > args.cap:
        raise UsageError('--canary must be 1..--cap, got %d' % args.canary)
    con = open_read_only(args.db)
    try:
        if not fs.tables_present(con):
            raise LookupError(', '.join(fs.missing_tables(con)))
        flights, (utc_start, utc_end) = period_flights(con, date_from, date_to)
        census_total, _months = fs.flight_census(con, utc_start, utc_end)
    finally:
        con.close()

    mine = bucket_of(flights)
    if (mine['states'] != census_total['states']
            or mine['reasons'] != census_total['reasons']
            or mine['flights'] != census_total['flights']):
        raise GateFailed('per-flight classification differs from the census: '
                         'states %s vs %s, reasons %s vs %s'
                         % (mine['states'], census_total['states'],
                            mine['reasons'], census_total['reasons']))

    cohort = [f for f in flights if f['reason'] == fv.REASON_NO_CARD]
    control = [f for f in flights if f['has_card']]
    manifest, alloc = build_sample(cohort, args.cap, args.canary)
    selected = {m['flight_id']: m['manifest_order'] for m in manifest}
    for f in cohort:
        f['selected'] = 1 if f['flight_id'] in selected else 0
        f['manifest_order'] = selected.get(f['flight_id'], '')
    cohort_sizes = {}
    for f in cohort:
        cohort_sizes[f['stratum']] = cohort_sizes.get(f['stratum'], 0) + 1

    files = {
        COHORT_NAME: csv_bytes(FLIGHT_COLUMNS + ('selected', 'manifest_order'),
                               cohort),
        CONTROL_NAME: csv_bytes(FLIGHT_COLUMNS, control),
        MANIFEST_NAME: csv_bytes(MANIFEST_COLUMNS, manifest),
        CANARY_IDS: ids_bytes('canary: first %d of the frozen manifest'
                              % min(args.canary, len(manifest)),
                              [m['flight_id'] for m in manifest
                               if m['canary']]),
        PILOT_IDS: ids_bytes('pilot: the whole frozen manifest (%d)'
                             % len(manifest),
                             [m['flight_id'] for m in manifest]),
    }
    manifest_sha = hashlib.sha256(files[MANIFEST_NAME]).hexdigest()
    plan = {
        'pilot_id': PILOT_ID, 'tool_version': TOOL_VERSION,
        'field_resolver_version': FIELD_RESOLVER_VERSION,
        'area_algorithm_version': AREA_ALGORITHM_VERSION,
        'period': {'from': str(date_from), 'to': str(date_to),
                   'timezone': 'UTC+5'},
        'database': {'name': os.path.basename(args.db),
                     'bytes': os.path.getsize(args.db),
                     'sha256': sha256_file(args.db)},
        'census': {'flights': census_total['flights'],
                   'with_calculation': census_total['with_calculation'],
                   'with_attribution': census_total['with_attribution'],
                   'states': census_total['states'],
                   'reasons': census_total['reasons'],
                   'awaiting_recalculation':
                       census_total['awaiting_recalculation']},
        'cohort': {'no_card': len(cohort),
                   'no_card_with_calculation':
                       sum(1 for f in cohort if f['has_calc']),
                   'strata': len(cohort_sizes),
                   'strata_sizes': dict(sorted(cohort_sizes.items())),
                   'allocation': dict(sorted((h, n) for h, n in alloc.items()
                                             if n))},
        'control': {'with_card': len(control),
                    'distribution': distribution(control),
                    'confirmed': confirmed_count(control),
                    'reweighted_to_no_card_strata':
                        reweighted_control(control, cohort_sizes)},
        'sample': {'cap': args.cap, 'size': len(manifest),
                   'canary': sum(m['canary'] for m in manifest),
                   'selection': REASON_SELECTED,
                   'manifest_sha256': manifest_sha,
                   'canary_ids_sha256':
                       hashlib.sha256(files[CANARY_IDS]).hexdigest(),
                   'pilot_ids_sha256':
                       hashlib.sha256(files[PILOT_IDS]).hexdigest()},
    }
    files[PLAN_NAME] = json_bytes(plan)

    os.makedirs(args.out_dir, exist_ok=True)
    existing = os.path.join(args.out_dir, MANIFEST_NAME)
    if os.path.exists(existing):
        # [REASON]: манифест замораживается до сбора и после результатов не
        # меняется. Повтор с тем же входом -- те же байты, и это не отказ;
        # другой вход (другая база, период, потолок) -- отказ: заменить
        # неудобные вылеты удобными задним числом нельзя.
        with open(existing, 'rb') as handle:
            if handle.read() != files[MANIFEST_NAME]:
                raise Refused('%s already holds a DIFFERENT frozen manifest -- '
                              'it is never replaced; use a new --out-dir only '
                              'for a new, separately agreed pilot' % existing)
        out.write('MANIFEST ALREADY FROZEN: identical, nothing rewritten\n')
    else:
        for name, data in files.items():
            write_atomic(os.path.join(args.out_dir, name), data)
    for line in plan_lines(plan):
        out.write(line + '\n')
    return EXIT_OK


def _pct(part, whole):
    return '%5.1f%%' % (100.0 * part / whole) if whole else '    -'


def plan_lines(plan):
    c = plan['census']
    n = c['flights']
    s, r = c['states'], c['reasons']
    confirmed = s[fv.STATE_EXACT] + s[fv.STATE_IDENTIFIED]
    lines = [
        'PILOT                %s (%s)' % (PILOT_ID, plan['tool_version']),
        'PERIOD (UTC+5)       %s .. %s' % (plan['period']['from'],
                                          plan['period']['to']),
        'DATABASE             %s, %d bytes, sha256 %s'
        % (plan['database']['name'], plan['database']['bytes'],
           plan['database']['sha256']),
        'FLIGHTS              %d' % n,
        'WITH CALCULATION     %d' % c['with_calculation'],
        'WITH ATTRIBUTION     %d' % c['with_attribution'],
        'CONFIRMED            %d %s  (EXACT %d, IDENTIFIED %d)'
        % (confirmed, _pct(confirmed, n), s[fv.STATE_EXACT],
           s[fv.STATE_IDENTIFIED]),
        'PROBABLE / CANDIDATE %d / %d' % (s[fv.STATE_PROBABLE],
                                          s[fv.STATE_CANDIDATE]),
        'AMBIGUOUS            %d' % s[fv.STATE_AMBIGUOUS],
        'UNRESOLVED           %d' % s[fv.STATE_UNRESOLVED],
        '  NO_CARD            %d %s' % (r[fv.REASON_NO_CARD],
                                        _pct(r[fv.REASON_NO_CARD], n)),
        '  NO_KEY             %d' % r[fv.REASON_NO_KEY],
        '  KEY_AFTER_RESOL.   %d' % r[fv.REASON_KEY_AFTER_RESOLUTION],
        '  NOT_IN_CATALOG     %d' % r[fv.REASON_NOT_IN_CATALOG],
        '  UNPARSED / OTHER   %d / %d' % (r[fv.REASON_UNPARSED],
                                          r[fv.REASON_OTHER]),
        'NOT_RESOLVED         %d' % s[fv.STATE_NOT_RESOLVED],
        'IDENTIFIED AWAITING RECALC %d' % c['awaiting_recalculation'],
        'COHORT NO_CARD       %d (with calculation %d) in %d strata'
        % (plan['cohort']['no_card'],
           plan['cohort']['no_card_with_calculation'],
           plan['cohort']['strata']),
        'CONTROL WITH CARD    %d, confirmed %d %s'
        % (plan['control']['with_card'], plan['control']['confirmed'],
           _pct(plan['control']['confirmed'], plan['control']['with_card'])),
        'CONTROL DISTRIBUTION %s' % json.dumps(plan['control']['distribution'],
                                               sort_keys=True),
        'CONTROL REWEIGHTED   %s' % json.dumps(
            plan['control']['reweighted_to_no_card_strata']['shares'],
            sort_keys=True),
        'SAMPLE               %d of cap %d, canary %d'
        % (plan['sample']['size'], plan['sample']['cap'],
           plan['sample']['canary']),
        'MANIFEST SHA256      %s' % plan['sample']['manifest_sha256'],
        'CANARY IDS SHA256    %s' % plan['sample']['canary_ids_sha256'],
        'PILOT IDS SHA256     %s' % plan['sample']['pilot_ids_sha256'],
    ]
    return [ascii_safe(line) for line in lines]


# ─── fingerprint ─────────────────────────────────────────────────────────────

def _digest_rows(rows):
    digest = hashlib.sha256()
    count = 0
    for row in rows:
        digest.update(('|'.join('' if v is None else repr(v) for v in row)
                       + '\n').encode('utf-8'))
        count += 1
    return count, digest.hexdigest()


def fingerprint(con, date_from, date_to, source_upto=None):
    """Отпечатки того, что пилот обязан не изменить.

    ``source_upto`` -- сверять ревизии источников только до этого id
    включительно: новые ревизии пилота дописываются после, прежние
    обязаны остаться байт в байт.
    """
    raw_n, raw_sha = _digest_rows(con.execute(
        'SELECT dji_flight_id, area_ha FROM drone_flights '
        'ORDER BY dji_flight_id'))
    names = {r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    if 'drone_area_decisions' in names:
        dec_n, dec_sha = _digest_rows(con.execute(
            'SELECT * FROM drone_area_decisions ORDER BY id'))
    else:
        dec_n, dec_sha = 0, None
    max_rev = con.execute(
        'SELECT COALESCE(MAX(id), 0) FROM dji_source_revisions').fetchone()[0]
    upto = max_rev if source_upto is None else int(source_upto)
    src_n, src_sha = _digest_rows(con.execute(
        'SELECT id, provider_account_id, flight_id, source_type, sha256, '
        'size_bytes, captured_at_utc, capture_run_id, is_evidence_import '
        'FROM dji_source_revisions WHERE id <= ? ORDER BY id', (upto,)))
    flights, _bounds = period_flights(con, date_from, date_to)
    current = {str(f['flight_id']): [
        (f['_attr'] or {}).get('id'), f['_calc_id'],
        (f['_evidence'] or {}).get('card_revision_id')] for f in flights}
    migrations = None
    if 'schema_migrations' in names:
        migrations = con.execute(
            'SELECT COUNT(*) FROM schema_migrations').fetchone()[0]
    return {
        'pilot_id': PILOT_ID, 'tool_version': TOOL_VERSION,
        'period': {'from': str(date_from), 'to': str(date_to)},
        'raw_area_ha': {'rows': raw_n, 'sha256': raw_sha},
        'drone_area_decisions': {'rows': dec_n, 'sha256': dec_sha},
        'source_revisions': {'max_id': upto, 'rows': src_n,
                             'sha256': src_sha,
                             'max_id_now': max_rev},
        'schema_migrations': migrations,
        'period_current': current,
    }


def run_fingerprint(args, out):
    date_from = parse_day(args.date_from, '--from')
    date_to = parse_day(args.date_to, '--to')
    con = open_read_only(args.db)
    try:
        if not fs.tables_present(con):
            raise LookupError(', '.join(fs.missing_tables(con)))
        result = fingerprint(con, date_from, date_to)
    finally:
        con.close()
    write_atomic(args.out, json_bytes(result))
    out.write('RAW AREA_HA          rows %d sha256 %s\n'
              % (result['raw_area_ha']['rows'], result['raw_area_ha']['sha256']))
    out.write('AREA DECISIONS       rows %d sha256 %s\n'
              % (result['drone_area_decisions']['rows'],
                 result['drone_area_decisions']['sha256']))
    out.write('SOURCE REVISIONS     rows %d max id %d sha256 %s\n'
              % (result['source_revisions']['rows'],
                 result['source_revisions']['max_id'],
                 result['source_revisions']['sha256']))
    out.write('SCHEMA MIGRATIONS    %s\n' % result['schema_migrations'])
    out.write('PERIOD FLIGHTS       %d\n' % len(result['period_current']))
    out.write('FINGERPRINT FILE     %s\n' % ascii_safe(args.out))
    return EXIT_OK


# ─── collector-stats ─────────────────────────────────────────────────────────

def collector_stats(lines, wanted_ids=None):
    """Посещения и отказы по журналу сборщика; ничего, кроме id и статусов.

    Время посещения -- разница между итоговыми строками соседних вылетов
    одного прогона (сборщик пишет итог каждого вылета по порядку), то есть
    вместе с паузой между вылетами. Первый вылет прогона не меряется.
    """
    wanted = set(int(i) for i in wanted_ids) if wanted_ids else None
    visits = {}
    order = []
    markers = {}
    summaries = []
    previous = None
    durations = []
    for line in lines:
        m = RE_FLIGHT_STATUS.search(line)
        p = RE_PAGE_ERROR.search(line) if not m else None
        if m or p:
            stamp = datetime.strptime((m or p).group(1), '%Y-%m-%d %H:%M:%S')
            fid = int((m or p).group(2))
            if m:
                status = m.group(3)
                items = [x.strip() for x in m.group(4).split(',') if x.strip()]
            else:
                status, items = 'PAGE_ERROR', []
            if previous is not None and stamp >= previous:
                durations.append((stamp - previous).total_seconds())
            previous = stamp
            if wanted is None or fid in wanted:
                visits[fid] = {'status': status,
                               'card': 'card' in items,
                               'items': sorted(items)}
                order.append(fid)
            continue
        s = RE_SUMMARY.search(line)
        if s:
            summaries.append(dict(kv.split('=', 1) for kv in s.group(1).split()
                                  if '=' in kv))
            previous = None
            continue
        for name, rx in STOP_MARKERS:
            if rx.search(line):
                markers[name] = markers.get(name, 0) + 1
    statuses = {}
    for v in visits.values():
        statuses[v['status']] = statuses.get(v['status'], 0) + 1
    durations.sort()
    median = None
    if durations:
        mid = len(durations) // 2
        median = (durations[mid] if len(durations) % 2
                  else (durations[mid - 1] + durations[mid]) / 2.0)
    not_visited = sorted(wanted - set(visits)) if wanted is not None else []
    stop = [name for name in ('SESSION', 'HTTP_429', 'HTTP_403', 'CAPTCHA',
                              'BROWSER_DEAD') if markers.get(name)]
    return {
        'wanted': len(wanted) if wanted is not None else None,
        'visited': len(visits),
        'not_visited': not_visited,
        'card_captured': sum(1 for v in visits.values() if v['card']),
        'statuses': dict(sorted(statuses.items())),
        'page_errors': statuses.get('PAGE_ERROR', 0),
        'stop_markers': dict(sorted(markers.items())),
        'stop': stop,
        'seconds_per_visit_median': median,
        'seconds_measured': sum(durations),
        'visits_measured': len(durations),
        'summaries': summaries,
        'per_flight': {str(k): v for k, v in sorted(visits.items())},
    }


def run_collector_stats(args, out):
    wanted = read_ids(args.ids) if args.ids else None
    lines = []
    for path in args.log:
        with open(path, encoding='utf-8', errors='replace') as handle:
            lines.extend(handle.read().splitlines())
    stats = collector_stats(lines, wanted)
    if args.out:
        write_atomic(args.out, json_bytes(stats))
    out.write('WANTED               %s\n' % stats['wanted'])
    out.write('VISITED              %d\n' % stats['visited'])
    out.write('NOT VISITED          %d\n' % len(stats['not_visited']))
    out.write('CARD CAPTURED        %d\n' % stats['card_captured'])
    out.write('STATUSES             %s\n' % json.dumps(stats['statuses'],
                                                       sort_keys=True))
    out.write('PAGE ERRORS          %d\n' % stats['page_errors'])
    out.write('SECONDS PER VISIT    median %s over %d visits\n'
              % ('-' if stats['seconds_per_visit_median'] is None
                 else '%.1f' % stats['seconds_per_visit_median'],
                 stats['visits_measured']))
    out.write('STOP MARKERS         %s\n' % json.dumps(stats['stop_markers'],
                                                       sort_keys=True))
    if stats['stop']:
        out.write('COLLECTOR VERDICT    STOP (%s) -- do not continue the pilot\n'
                  % ', '.join(stats['stop']))
        return EXIT_COLLECTOR_STOP
    out.write('COLLECTOR VERDICT    no stop marker\n')
    return EXIT_OK


# ─── measure ─────────────────────────────────────────────────────────────────

def wilson(successes, n, z=1.96):
    """95 % интервал Уилсона для доли; (None, None) при n = 0."""
    if n <= 0:
        return None, None
    p = successes / float(n)
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return max(0.0, centre - half), min(1.0, centre + half)


def post_stratified(rows, strata_sizes):
    """Постстратифицированная доля подтверждённых среди получивших карточку.

    Вес слоя -- его доля в КОГОРТЕ NO_CARD (N_h / N по слоям, где карточка
    получена хотя бы у одного вылета); дисперсия -- сумма W_h^2 p_h(1-p_h)/n_h.
    """
    by = {}
    for r in rows:
        if r['fetched']:
            by.setdefault(r['stratum'], []).append(r)
    covered = {h: strata_sizes.get(h, 0) for h in by}
    total = float(sum(covered.values()))
    if not total:
        return None
    est = var = 0.0
    for h, items in by.items():
        w = covered[h] / total
        p = sum(1 for r in items if r['confirmed_after']) / float(len(items))
        est += w * p
        var += w * w * p * (1 - p) / len(items)
    half = 1.96 * math.sqrt(var)
    return {'estimate': est, 'low': max(0.0, est - half),
            'high': min(1.0, est + half),
            'cohort_share_covered': total / float(sum(strata_sizes.values()))}


def read_manifest(path):
    with open(path, encoding='utf-8') as handle:
        return list(csv.DictReader(handle))


def breakdown(rows, key):
    out = {}
    for r in rows:
        b = out.setdefault(r[key] if r[key] not in (None, '') else '-', {
            'attempted': 0, 'fetched': 0, 'confirmed': 0, 'no_key': 0,
            'not_in_catalog': 0})
        b['attempted'] += 1
        b['fetched'] += 1 if r['fetched'] else 0
        b['confirmed'] += 1 if r['confirmed_after'] else 0
        b['no_key'] += 1 if r['after'] == fv.REASON_NO_KEY else 0
        b['not_in_catalog'] += (1 if r['after'] == fv.REASON_NOT_IN_CATALOG
                                else 0)
    return dict(sorted(out.items()))


def pick_cases(rows, flights_by_id, con):
    """Случаи для проверки глазами: по два каждого исхода и отказ сбора."""
    def first(pred, n):
        hits = [r for r in rows if pred(r)]
        hits.sort(key=lambda r: r['selection_key'])
        return [r['flight_id'] for r in hits[:n]]
    cases = {
        'new_exact': first(lambda r: r['after'] == fv.STATE_EXACT, 2),
        'new_identified': first(lambda r: r['after'] == fv.STATE_IDENTIFIED, 2),
        'no_key': first(lambda r: r['after'] == fv.REASON_NO_KEY, 2),
        'not_in_catalog': first(
            lambda r: r['after'] == fv.REASON_NOT_IN_CATALOG, 2),
        'fetch_failure': first(lambda r: not r['fetched'], 1),
    }
    shared = []
    md5s = {}
    for r in rows:
        attr = (flights_by_id.get(r['flight_id']) or {}).get('_attr') or {}
        if r['confirmed_after'] and attr.get('geometry_md5'):
            md5s.setdefault(attr['geometry_md5'], []).append(r)
    if md5s:
        holders = {}
        keys = sorted(md5s)
        for start in range(0, len(keys), fs.CHUNK):
            chunk = keys[start:start + fs.CHUNK]
            for row in con.execute(
                    'SELECT geometry_md5, COUNT(DISTINCT land_uuid) AS n FROM '
                    'dji_land_revisions WHERE geometry_md5 IN (%s) GROUP BY '
                    'geometry_md5' % ','.join('?' * len(chunk)), chunk):
                holders[row['geometry_md5']] = row['n']
        for md5, items in sorted(md5s.items()):
            if holders.get(md5, 0) > 1:
                items.sort(key=lambda r: r['selection_key'])
                shared.append(items[0]['flight_id'])
    cases['shared_md5'] = shared[:1]
    return cases


def measure(con, plan, manifest, stage):
    date_from = parse_day(plan['period']['from'], 'plan period')
    date_to = parse_day(plan['period']['to'], 'plan period')
    flights, _bounds = period_flights(con, date_from, date_to)
    by_id = {f['flight_id']: f for f in flights}
    if stage == 'canary':
        manifest = [m for m in manifest if m['canary'] == '1']
    rows = []
    for m in manifest:
        fid = int(m['flight_id'])
        f = by_id.get(fid)
        after_state = f['state'] if f else 'MISSING'
        after = (f['reason'] if f and f['state'] == fv.STATE_UNRESOLVED
                 else after_state)
        rows.append({
            'manifest_order': int(m['manifest_order']), 'flight_id': fid,
            'stratum': m['stratum'], 'iso_week': m['iso_week'],
            'unit_number': m['unit_number'],
            'selection_key': m['selection_key'],
            'before': fv.REASON_NO_CARD,
            'fetched': bool(f and f['has_card']),
            'after': after,
            'confirmed_after': bool(f and f['state'] in fv.CONFIRMED_STATES),
            'has_calc_after': bool(f and f['has_calc']),
        })
    return rows, by_id


def gates(before, after, manifest_ids):
    """Ворота неизменности: (имя, прошли ли, подробность)."""
    out = []
    out.append(('RAW area_ha of every flight unchanged',
                before['raw_area_ha'] == after['raw_area_ha'],
                '%s -> %s' % (before['raw_area_ha']['sha256'][:16],
                              after['raw_area_ha']['sha256'][:16])))
    out.append(('drone_area_decisions unchanged',
                before['drone_area_decisions'] == after['drone_area_decisions'],
                'rows %s -> %s' % (before['drone_area_decisions']['rows'],
                                   after['drone_area_decisions']['rows'])))
    src_b, src_a = before['source_revisions'], after['source_revisions']
    out.append(('earlier source revisions byte-identical',
                (src_b['rows'], src_b['sha256'])
                == (src_a['rows'], src_a['sha256']),
                'rows %s, up to id %s' % (src_a['rows'], src_b['max_id'])))
    out.append(('schema migrations unchanged',
                before['schema_migrations'] == after['schema_migrations'],
                '%s -> %s' % (before['schema_migrations'],
                              after['schema_migrations'])))
    wanted = set(str(i) for i in manifest_ids)
    changed = sorted(k for k, v in before['period_current'].items()
                     if k not in wanted
                     and after['period_current'].get(k) != v)
    missing = sorted(k for k in before['period_current']
                     if k not in after['period_current'])
    out.append(('flights outside the manifest untouched '
                '(attribution, calculation, card)',
                not changed and not missing,
                'changed %d%s' % (len(changed), (' e.g. %s' % ', '.join(
                    changed[:5])) if changed else '')))
    return out


def run_measure(args, out):
    plan_path = os.path.join(args.plan_dir, PLAN_NAME)
    manifest_path = os.path.join(args.plan_dir, MANIFEST_NAME)
    before_path = args.before or os.path.join(args.plan_dir, FINGERPRINT_BEFORE)
    with open(plan_path, encoding='ascii') as handle:
        plan = json.load(handle)
    with open(manifest_path, 'rb') as handle:
        manifest_sha = hashlib.sha256(handle.read()).hexdigest()
    if manifest_sha != plan['sample']['manifest_sha256']:
        raise GateFailed('the manifest on disk is not the frozen one (sha256 %s, '
                         'plan says %s)' % (manifest_sha,
                                            plan['sample']['manifest_sha256']))
    manifest = read_manifest(manifest_path)
    with open(before_path, encoding='ascii') as handle:
        before = json.load(handle)
    date_from = parse_day(plan['period']['from'], 'plan period')
    date_to = parse_day(plan['period']['to'], 'plan period')
    con = open_read_only(args.db)
    try:
        if not fs.tables_present(con):
            raise LookupError(', '.join(fs.missing_tables(con)))
        rows, by_id = measure(con, plan, manifest, args.stage)
        after_fp = fingerprint(con, date_from, date_to,
                               source_upto=before['source_revisions']['max_id'])
        cases = pick_cases(rows, by_id, con)
        census_after, _m = fs.flight_census(con, *utc_bounds(date_from, date_to))
    finally:
        con.close()

    stats = None
    if args.collector_stats:
        with open(args.collector_stats, encoding='ascii') as handle:
            stats = json.load(handle)

    attempted = len(rows)
    if stats is not None:
        visited = set(int(k) for k in stats.get('per_flight', {}))
        attempted = sum(1 for r in rows if r['flight_id'] in visited)
    fetched = sum(1 for r in rows if r['fetched'])
    confirmed = sum(1 for r in rows if r['confirmed_after'])
    outcomes = {}
    for r in rows:
        if r['fetched']:
            outcomes[r['after']] = outcomes.get(r['after'], 0) + 1
    lo_f, hi_f = wilson(confirmed, fetched)
    lo_a, hi_a = wilson(confirmed, len(rows))
    ps = post_stratified(rows, plan['cohort']['strata_sizes'])
    no_card_total = plan['cohort']['no_card']
    flights_total = plan['census']['flights']
    confirmed_before = (plan['census']['states'][fv.STATE_EXACT]
                        + plan['census']['states'][fv.STATE_IDENTIFIED])

    def project(rate):
        if rate is None:
            return None
        add = rate * no_card_total
        return {'additional_confirmed': int(round(add)),
                'coverage_pct': round(100.0 * (confirmed_before + add)
                                      / flights_total, 1)}

    rate_attempt = confirmed / float(len(rows)) if rows else None
    # [REASON]: меняться вправе только вылеты ЭТОГО этапа: после канарейки
    # остальные 450 вылетов манифеста обязаны быть такими же, как до сбора.
    gate_rows = gates(before, after_fp, [r['flight_id'] for r in rows])
    result = {
        'pilot_id': PILOT_ID, 'tool_version': TOOL_VERSION,
        'stage': args.stage, 'manifest_sha256': manifest_sha,
        'manifest_flights_in_stage': len(rows),
        'attempted': attempted, 'fetched': fetched,
        'fetch_failed': len(rows) - fetched,
        'confirmed': confirmed,
        'outcomes_among_fetched': dict(sorted(outcomes.items())),
        'conversion_among_fetched': {
            'rate': confirmed / float(fetched) if fetched else None,
            'wilson95': [lo_f, hi_f]},
        'conversion_among_manifest': {
            'rate': rate_attempt, 'wilson95': [lo_a, hi_a]},
        'post_stratified_among_fetched': ps,
        'projection_on_no_card_cohort': {
            'no_card_flights': no_card_total,
            'confirmed_before': confirmed_before,
            'flights_total': flights_total,
            'at_manifest_rate': project(rate_attempt),
            'at_manifest_wilson_low': project(lo_a),
            'at_manifest_wilson_high': project(hi_a)},
        'by_unit': breakdown(rows, 'unit_number'),
        'by_week': breakdown(rows, 'iso_week'),
        'control_with_card': plan['control'],
        'census_after': {'states': census_after['states'],
                         'reasons': census_after['reasons'],
                         'awaiting_recalculation':
                             census_after['awaiting_recalculation']},
        'cases': cases,
        'gates': [{'gate': g, 'pass': ok, 'detail': d}
                  for g, ok, d in gate_rows],
        'collector': stats and {k: stats[k] for k in (
            'visited', 'card_captured', 'statuses', 'page_errors',
            'stop_markers', 'stop', 'seconds_per_visit_median')},
    }
    os.makedirs(args.out_dir, exist_ok=True)
    write_atomic(os.path.join(args.out_dir, 'measure_%s.json' % args.stage),
                 json_bytes(result))
    write_atomic(os.path.join(args.out_dir, 'results_%s.csv' % args.stage),
                 csv_bytes(('manifest_order', 'flight_id', 'stratum',
                            'iso_week', 'unit_number', 'before', 'fetched',
                            'after', 'confirmed_after', 'has_calc_after'),
                           rows))
    for line in measure_lines(result):
        out.write(line + '\n')
    if not all(ok for _g, ok, _d in gate_rows):
        return EXIT_GATE
    return EXIT_OK


def _rate(value):
    return '-' if value is None else '%.1f%%' % (100.0 * value)


def measure_lines(res):
    lines = [
        'STAGE                %s' % res['stage'],
        'MANIFEST SHA256      %s' % res['manifest_sha256'],
        'IN STAGE             %d' % res['manifest_flights_in_stage'],
        'ATTEMPTED            %d' % res['attempted'],
        'FETCHED (card now)   %d' % res['fetched'],
        'FETCH FAILED         %d' % res['fetch_failed'],
        'CONFIRMED NOW        %d' % res['confirmed'],
        'OUTCOMES (fetched)   %s' % json.dumps(res['outcomes_among_fetched'],
                                              sort_keys=True),
        'CONVERSION / FETCHED %s  95%% Wilson %s .. %s' % (
            _rate(res['conversion_among_fetched']['rate']),
            _rate(res['conversion_among_fetched']['wilson95'][0]),
            _rate(res['conversion_among_fetched']['wilson95'][1])),
        'CONVERSION / MANIF.  %s  95%% Wilson %s .. %s' % (
            _rate(res['conversion_among_manifest']['rate']),
            _rate(res['conversion_among_manifest']['wilson95'][0]),
            _rate(res['conversion_among_manifest']['wilson95'][1])),
    ]
    ps = res['post_stratified_among_fetched']
    if ps:
        lines.append('POST-STRATIFIED      %s  (%s .. %s), strata cover %s of '
                     'the cohort' % (_rate(ps['estimate']), _rate(ps['low']),
                                     _rate(ps['high']),
                                     _rate(ps['cohort_share_covered'])))
    pr = res['projection_on_no_card_cohort']
    for label, key in (('PROJECTION (rate)', 'at_manifest_rate'),
                       ('PROJECTION (low)', 'at_manifest_wilson_low'),
                       ('PROJECTION (high)', 'at_manifest_wilson_high')):
        value = pr[key]
        if value:
            lines.append('%-20s +%d confirmed of %d NO_CARD -> %.1f%% of %d '
                         'flights' % (label, value['additional_confirmed'],
                                      pr['no_card_flights'],
                                      value['coverage_pct'],
                                      pr['flights_total']))
    lines.append('CASES                %s' % json.dumps(res['cases'],
                                                     sort_keys=True))
    for g in res['gates']:
        lines.append('GATE %-4s %s -- %s' % ('PASS' if g['pass'] else 'FAIL',
                                            g['gate'], g['detail']))
    return [ascii_safe(line) for line in lines]


# ─── Командная строка ────────────────────────────────────────────────────────

class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise UsageError(message)


def build_parser():
    parser = _Parser(prog='dji_card_coverage_pilot.py',
                     description='%s: frozen sample, fingerprints and '
                                 'measurement. Reads only; never contacts '
                                 'DJI.' % PILOT_ID)
    sub = parser.add_subparsers(dest='command')
    p = sub.add_parser('plan')
    p.add_argument('--db', required=True)
    p.add_argument('--out-dir', required=True)
    p.add_argument('--from', dest='date_from', default=DEFAULT_FROM)
    p.add_argument('--to', dest='date_to', default=DEFAULT_TO)
    p.add_argument('--cap', type=int, default=HARD_CAP)
    p.add_argument('--canary', type=int, default=None,
                   help='first N of the manifest (default %d, never more '
                        'than --cap)' % DEFAULT_CANARY)
    f = sub.add_parser('fingerprint')
    f.add_argument('--db', required=True)
    f.add_argument('--out', required=True)
    f.add_argument('--from', dest='date_from', default=DEFAULT_FROM)
    f.add_argument('--to', dest='date_to', default=DEFAULT_TO)
    c = sub.add_parser('collector-stats')
    c.add_argument('--log', required=True, action='append')
    c.add_argument('--ids')
    c.add_argument('--out')
    m = sub.add_parser('measure')
    m.add_argument('--db', required=True)
    m.add_argument('--plan-dir', required=True)
    m.add_argument('--stage', choices=('canary', 'pilot'), required=True)
    m.add_argument('--out-dir', required=True)
    m.add_argument('--before')
    m.add_argument('--collector-stats')
    return parser


def main(argv=None, out=None):
    out = out or sys.stdout
    try:
        args = build_parser().parse_args(argv)
        if not args.command:
            raise UsageError('a command is required: plan, fingerprint, '
                             'collector-stats or measure')
        handler = {'plan': run_plan, 'fingerprint': run_fingerprint,
                   'collector-stats': run_collector_stats,
                   'measure': run_measure}[args.command]
        return handler(args, out)
    except UsageError as exc:
        out.write('USAGE ERROR: %s\n' % ascii_safe(exc))
        return EXIT_USAGE
    except Refused as exc:
        out.write('REFUSED: %s\n' % ascii_safe(exc))
        return EXIT_REFUSED
    except FileNotFoundError as exc:
        out.write('NOT FOUND: %s (no file was created)\n' % ascii_safe(exc))
        return EXIT_NO_DATABASE
    except LookupError as exc:
        out.write('MISSING TABLES: %s\n' % ascii_safe(exc))
        return EXIT_NO_TABLES
    except GateFailed as exc:
        out.write('GATE FAILED: %s\n' % ascii_safe(exc))
        return EXIT_GATE


if __name__ == '__main__':
    sys.exit(main())
