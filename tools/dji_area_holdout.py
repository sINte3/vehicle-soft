# -*- coding: utf-8 -*-
"""tools/dji_area_holdout.py -- слепой holdout замороженного структурного правила.

DJI-AREA-SIMPLIFY-001. Протокол и критерии зафиксированы ДО раскрытия V4 в
`docs/DJI_AREA_SIMPLIFY_001_HOLDOUT_PREREG.md`; этот файл их только исполняет.

Шаги, между которыми лежит живой адресный сбор V4 (его делает владелец штатным
`drone_collector --sources --ids-file`, не этот инструмент):

  list-db -- необязательный: одноразовая СПИСОЧНАЯ база из дампов
             `drone_collector --from .. --to .. --dry-run`, чтобы план можно было
             составить там, где живёт сборщик, не дотрагиваясь ни до одной базы
             приложения. Хронология в ней ведётся по нику (ключ `NICK:<ник>`).
  plan    -- по СПИСОЧНЫМ данным периода: все кандидаты замороженного экрана,
             стратифицированный контроль NORMAL, предсказания и минимальный
             список вылетов для сбора V4. План хешируется; второй план в тот же
             каталог не пишется.
  report  -- после сбора: классификация теми же `structural.py` + `resolver.py`
             через `dji_area.pipeline` (сухой прогон), приёмка кандидатов,
             поиск пропусков правила в контроле, раздельные величины периода.
  fingerprint -- отпечаток замороженного кода: одно значение, которым ранбук
             доказывает, что на машине лежит проверенная ревизия.

Собственного классификатора здесь НЕТ: кандидата называет
`dji_area.structural.screen`, статус -- `dji_area.resolver.resolve_area`, учётный
класс -- `dji_area.accounting.classify`. База открывается только на чтение.

Запуск (служба площадки остановлена либо база -- копия):

  cd C:\\transport-report-staging
  & "C:\\Program Files\\Python314\\python.exe" tools\\dji_area_holdout.py plan --db instance\\transport.db --from 2026-09-01 --to auto --out C:\\VehicleSoft_Holdout\\plan
  & "C:\\Program Files\\Python314\\python.exe" tools\\dji_area_holdout.py report --db instance\\transport.db --plan C:\\VehicleSoft_Holdout\\plan\\plan.json --out C:\\VehicleSoft_Holdout\\report

Коды возврата: 0 -- план записан / приёмка PASS; 1 -- ошибка командной строки
или данных; 2 -- база не найдена (файл НЕ создаётся); 3 -- приёмка FAIL
(кандидаты либо контрольные ворота); 4 -- нарушена целостность (хеш плана,
замороженный код ВКЛЮЧАЯ ЭТОТ ФАЙЛ, база изменилась во время чтения);
5 -- приёмка INCONCLUSIVE (мало оцениваемых записей).
Вывод в консоль только ASCII.
"""

import argparse
import csv
import hashlib
import json
import math
import os
import random
import sqlite3
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import dji_area  # noqa: E402
from dji_area import accounting as acc  # noqa: E402
from dji_area import pipeline  # noqa: E402
from dji_area import resolver as rs  # noqa: E402
from dji_area import store  # noqa: E402
from dji_area import structural as st  # noqa: E402

DEFAULT_DB = os.path.join(ROOT, 'instance', 'transport.db')

EXIT_OK = 0
EXIT_USAGE = 1
EXIT_NO_DATABASE = 2
EXIT_ACCEPTANCE_FAIL = 3
EXIT_INTEGRITY = 4
EXIT_INCONCLUSIVE = 5

PROTOCOL = 'DJI-AREA-SIMPLIFY-001-HOLDOUT'
# Версия 2: этот файл вошёл в список замороженных (его правка между plan и
# report -- отказ, а не предупреждение), и контрольные ворота стали частью
# общего вердикта. План версии 1 в обороте не был -- живой holdout не
# начинался, -- но старый план обязан быть отвергнут, а не прочитан молча.
PROTOCOL_VERSION = 2

# ─── Пред-зарегистрированные константы (см. PREREG, разделы 5, 7, 8) ─────────
# Менять их после раскрытия V4 нельзя: они входят в хеш плана, и `report`
# сверяет план со своими константами.

DEFAULT_SEED = 20260918
CANDIDATE_DELTA_MAX_SHARE = 0.01     # попадание: прирост <= 1 % RAW
CANDIDATE_PASS_SHARE = 0.99          # >= 99 % оцениваемых -- попадания
MIN_EVALUABLE_SHARE = 0.90           # иначе INCONCLUSIVE
CONFIDENCE = 0.95

SHORT_MAX_DURATION_S = 120
SHORT_MIN_RATE_M2_S = 49.0           # 7 м/с при захвате 7 м
EQUAL_RECENT_MAX_LAG = 3
CONTIGUOUS_MAX_GAP_S = 120
CAP_SHORT_HIGH_RATE = 100
CAP_AUTO_NO_WIDTH = 100
CAP_EQUAL_RECENT = 150
N_CONTIGUOUS_AUTO = 60
N_RANDOM = 300
RANDOM_CELL_FLOOR = 6
SYSTEMATIC_MIN_SIGNATURE_MISSES = 3
SYSTEMATIC_MIN_RANDOM_MISSES = 2

S_SHORT = 'N3_SHORT_HIGH_RATE'
S_NO_WIDTH = 'N2_AUTO_NO_WIDTH'
S_EQUAL = 'N1_EQUAL_RECENT'
S_CONTIG = 'N4_CONTIGUOUS_AUTO'
S_RANDOM = 'N5_RANDOM'
STRATA_ORDER = (S_SHORT, S_NO_WIDTH, S_EQUAL, S_CONTIG, S_RANDOM)

PRED_RETAINED = 'RETAINED'
PRED_NORMAL = 'NORMAL'

TOOL_FILE = 'tools/dji_area_holdout.py'

# Файлы, чьё содержимое holdout обязан застать неизменным между plan и report.
#
# [REASON]: инструмент входит сюда НАРАВНЕ с правилом. Прежняя редакция считала
# его правку предупреждением -- «ошибка в печати отчёта не должна стоить
# повторного живого сбора». Это неверно: здесь же лежат `candidate_outcome`,
# `control_outcome` и сам вердикт, то есть подмена порога или знака сравнения
# между plan и report прошла бы с пометкой в углу отчёта. Стоимость повторного
# прогона -- не довод против целостности: отчёт можно перестроить из того же
# плана, вернув файл, а вот незамеченную подгонку вернуть нельзя.
FROZEN_FILES = (
    'dji_area/__init__.py', 'dji_area/structural.py', 'dji_area/resolver.py',
    'dji_area/v4.py', 'dji_area/pipeline.py', 'dji_area/evidence.py',
    'dji_area/store.py', 'dji_area/accounting.py', TOOL_FILE,
)
# [REASON]: тесты сюда НЕ входят намеренно. Они не участвуют ни в отборе, ни в
# оценке; заморозив их, отпечаток ломался бы от любой новой проверки и перестал
# бы что-либо значить.

CORROBORATED = (rs.RAW_CORROBORATED, rs.RAW_CORROBORATED_QUALIFIED)
OVERSTATED = (rs.COUNTER_FLAT_RAW_OVERSTATED, rs.PARTIAL_RECORDED_OVERSTATEMENT)


class HoldoutError(RuntimeError):
    """Отказ с кодом возврата."""

    def __init__(self, message, code=EXIT_USAGE):
        RuntimeError.__init__(self, message)
        self.code = code


# ─── Целостность ─────────────────────────────────────────────────────────────

def lf_sha256(path):
    """SHA-256 содержимого с переводами строк, приведёнными к LF.

    [REASON]: рабочая копия Windows несёт CRLF (`core.autocrlf`), блоб git и
    контейнер CI -- LF. Хеш сырых байтов различался бы между машиной, где план
    составлен, и машиной, где он проверяется, и замок срабатывал бы на каждом
    честном прогоне.
    """
    with open(path, 'rb') as fh:
        data = fh.read()
    return hashlib.sha256(data.replace(b'\r\n', b'\n')).hexdigest()


def file_sha256(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def frozen_state():
    return {
        'versions': {
            'model': dji_area.MODEL_VERSION,
            'area_algorithm': dji_area.AREA_ALGORITHM_VERSION,
            'structural_rule': dji_area.STRUCTURAL_RULE_VERSION,
            'v4_parser': dji_area.V4_PARSER_VERSION,
            'channel_capability': dji_area.CHANNEL_CAPABILITY_REVISION,
            'accounting_classes': acc.ACCOUNTING_CLASSES_VERSION,
        },
        'files': {rel: lf_sha256(os.path.join(ROOT, rel.replace('/', os.sep)))
                  for rel in FROZEN_FILES},
    }


def acceptance_constants():
    return {
        'candidate_delta_max_share': CANDIDATE_DELTA_MAX_SHARE,
        'candidate_pass_share': CANDIDATE_PASS_SHARE,
        'min_evaluable_share': MIN_EVALUABLE_SHARE,
        'confidence': CONFIDENCE,
        'systematic_min_signature_misses': SYSTEMATIC_MIN_SIGNATURE_MISSES,
        'systematic_min_random_misses': SYSTEMATIC_MIN_RANDOM_MISSES,
    }


def strata_constants(random_n, contiguous_n):
    return {
        'order': list(STRATA_ORDER),
        'short_max_duration_s': SHORT_MAX_DURATION_S,
        'short_min_rate_m2_s': SHORT_MIN_RATE_M2_S,
        'equal_recent_max_lag': EQUAL_RECENT_MAX_LAG,
        'contiguous_max_gap_s': CONTIGUOUS_MAX_GAP_S,
        'cap_short_high_rate': CAP_SHORT_HIGH_RATE,
        'cap_auto_no_width': CAP_AUTO_NO_WIDTH,
        'cap_equal_recent': CAP_EQUAL_RECENT,
        'n_contiguous_auto': contiguous_n,
        'n_random': random_n,
        'random_cell_floor': RANDOM_CELL_FLOOR,
    }


def code_fingerprint():
    """Одно значение вместо девяти хешей: SHA-256 канонического JSON
    ``frozen_state()``.

    [REASON]: ранбук обязан доказать, что на машине лежит ПРОВЕРЕННАЯ ревизия,
    до первого обращения к кабинету DJI. Точный SHA коммита внутрь самого этого
    коммита не положить, а отпечаток кода в круг не попадает: ранбук в
    ``FROZEN_FILES`` не входит, поэтому вписывание отпечатка в ранбук его не
    меняет.
    """
    return hashlib.sha256(canonical(frozen_state())).hexdigest()


def canonical(obj):
    return json.dumps(obj, sort_keys=True, ensure_ascii=True,
                      separators=(',', ':')).encode('ascii')


def locked_sha(locked):
    return hashlib.sha256(canonical(locked)).hexdigest()


# ─── База: только чтение ─────────────────────────────────────────────────────

def open_readonly(db_path):
    if not os.path.exists(db_path):
        # [REASON]: sqlite3.connect создал бы пустой файл и отчитался нулями.
        raise HoldoutError('database not found at %s - refusing to run'
                           % db_path, EXIT_NO_DATABASE)
    uri = 'file:' + os.path.abspath(db_path).replace('\\', '/') + '?mode=ro'
    con = sqlite3.connect(uri, uri=True, timeout=30, isolation_level=None)
    con.row_factory = sqlite3.Row
    return con


def resolve_period(con, date_from, to_arg):
    """`--to auto` -- последний ПОЛНЫЙ день отчёта (UTC+5), что есть в базе."""
    if to_arg != 'auto':
        date_to = date.fromisoformat(to_arg)
    else:
        row = con.execute('SELECT MAX(started_at) AS m FROM drone_flights'
                          ).fetchone()
        last = pipeline._parse_dt(row['m']) if row and row['m'] else None
        if last is None:
            raise HoldoutError('drone_flights is empty - nothing to plan')
        # [REASON]: последний день в базе почти наверняка неполон -- сборщик
        # приходит утром, а вылет попадает в кабинет с задержкой. Неполный день
        # дал бы цепочки, оборванные не полётом, а моментом выгрузки.
        date_to = pipeline.report_day_of(last) - timedelta(days=1)
    if date_to < date_from:
        raise HoldoutError(
            'no complete report day in [%s, ...]: the database ends at %s'
            % (date_from.isoformat(), date_to.isoformat()))
    return date_to


def run_pipeline(con, db_path, date_from, date_to, progress=None):
    """Сухой прогон существующего конвейера: (строки решений, записи списка).

    [REASON]: вызывается `_recalculate`, а не публичный `recalculate`: тот сам
    открывает базу на запись-чтение, а holdout обязан доказуемо ничего не
    писать -- соединение здесь открыто с `mode=ro`, и любая попытка записи
    упала бы, а не прошла тихо. `apply=False` при этом всё равно не пишет.
    """
    store.require_tables(con)
    root = store.source_root(os.path.abspath(db_path))
    summary = pipeline._recalculate(
        con, root, date_from, date_to, False, None, False, 500, None, True,
        progress)
    rows = summary.pop('flights', [])
    items = [i for i in pipeline.load_flights(con, date_from, date_to)]
    return summary, rows, items


def nicknames_by_flight(con, flight_ids):
    out = {}
    ids = list(flight_ids)
    for pos in range(0, len(ids), 500):
        chunk = ids[pos:pos + 500]
        marks = ','.join('?' * len(chunk))
        for r in con.execute('SELECT dji_flight_id, nickname_raw FROM '
                             'drone_flights WHERE dji_flight_id IN (%s)'
                             % marks, chunk):
            out[int(r['dji_flight_id'])] = r['nickname_raw']
    return out


# ─── Метаданные списка для страт ─────────────────────────────────────────────

def list_features(items):
    """{flight_id: признаки} по хронологии борта, с граничными днями.

    Только то, что видно в списке: длительность, зазор до предыдущей записи
    борта, повтор RAW среди трёх предыдущих, режим, наличие ширины.
    """
    by_hw = defaultdict(list)
    for item in items:
        by_hw[item.get('hardware_id') or None].append(item)
    out = {}
    for hw, records in by_hw.items():
        records.sort(key=lambda r: (r['start_ts'] or 0, r['flight_id']))
        for idx, rec in enumerate(records):
            raw = rec.get('raw_area_m2')
            start, end = rec.get('start_ts'), rec.get('end_ts')
            duration = (end - start) if start is not None and end is not None \
                else None
            gap = None
            lag = None
            # Без борта хронологии нет: соседи по списку -- чужие машины.
            if hw is not None:
                if idx > 0 and start is not None \
                        and records[idx - 1].get('end_ts') is not None:
                    gap = start - records[idx - 1]['end_ts']
                if raw:
                    for back in range(1, EQUAL_RECENT_MAX_LAG + 1):
                        if idx - back < 0:
                            break
                        if records[idx - back].get('raw_area_m2') == raw:
                            lag = back
                            break
            out[rec['flight_id']] = {
                'duration_s': duration,
                'gap_prev_s': gap,
                'equal_recent_lag': lag,
                'mode_name': rec.get('mode_name'),
                # [REASON]: то же определение «ширина есть», что у замороженного
                # экрана. Своё определение разошлось бы с ним на bool/0/строке,
                # и страта «авто без ширины» перестала бы быть дополнением к
                # кандидатам.
                'width_present': st._width_present(rec),
            }
    return out


def stratum_of(item, feat):
    """Первая подходящая страта N3, N2, N1, N4; иначе N5."""
    raw = item.get('raw_area_m2') or 0.0
    dur = feat['duration_s']
    if dur is not None and 0 < dur <= SHORT_MAX_DURATION_S \
            and raw / dur >= SHORT_MIN_RATE_M2_S:
        return S_SHORT
    if feat['mode_name'] == st.TARGET_MODE and not feat['width_present']:
        return S_NO_WIDTH
    if feat['equal_recent_lag'] is not None:
        return S_EQUAL
    gap = feat['gap_prev_s']
    if feat['mode_name'] == st.TARGET_MODE and feat['width_present'] \
            and gap is not None and 0 <= gap <= CONTIGUOUS_MAX_GAP_S:
        return S_CONTIG
    return S_RANDOM


def _take(rng, ids, limit):
    ids = sorted(ids)
    if limit is None or len(ids) <= limit:
        return ids
    return sorted(rng.sample(ids, limit))


def allocate_random(cells, target, floor):
    """Сколько брать из каждого слоя: пол, затем пропорционально остаток.

    Метод наибольшего остатка; детерминирован порядком ключей.
    """
    keys = sorted(cells)
    if target <= 0:
        return {k: 0 for k in keys}
    take = {k: min(floor, len(cells[k])) for k in keys}
    # Полы вместе больше цели (много мелких слоёв при малой выборке): срезаем с
    # конца списка ключей, чтобы итог не превысил заявленный объём.
    for k in reversed(keys):
        while sum(take.values()) > target and take[k] > 0:
            take[k] -= 1
    left = target - sum(take.values())
    room = {k: len(cells[k]) - take[k] for k in keys}
    while left > 0 and any(room.values()):
        total_room = sum(room.values())
        shares = {k: left * room[k] / float(total_room) for k in keys}
        given = 0
        for k in keys:
            add = min(room[k], int(math.floor(shares[k])))
            take[k] += add
            room[k] -= add
            given += add
        left -= given
        if given == 0:
            # Остатки меньше единицы: по одному, начиная с наибольшего остатка.
            for k in sorted(keys, key=lambda x: (-(shares[x] % 1), x)):
                if left <= 0:
                    break
                if room[k] > 0:
                    take[k] += 1
                    room[k] -= 1
                    left -= 1
    return take


def build_plan(rows, items, date_from, date_to, seed, random_n, contiguous_n):
    items_by_id = {i['flight_id']: i for i in items}
    feats = list_features(items)
    in_period = [r for r in rows if items_by_id.get(r['flight_id'], {})
                 .get('in_period')]

    candidates = []
    pool = defaultdict(list)
    no_hardware = 0
    for row in sorted(in_period, key=lambda r: r['flight_id']):
        fid = row['flight_id']
        item = items_by_id[fid]
        feat = feats[fid]
        if not item.get('hardware_id'):
            no_hardware += 1
        if row.get('structural_candidate'):
            candidates.append({
                'flight_id': fid,
                'hardware_id': item.get('hardware_id'),
                'report_day': row['report_day'],
                'raw_area_m2': row.get('raw_area_m2'),
                'scalar_source_check': bool(row.get('scalar_source_check')),
                'base_flight_id': row.get('candidate_base_flight_id'),
                'duration_s': feat['duration_s'],
                'v4_present_at_plan': bool(item.get('v4_revision_id')),
                'prediction': PRED_RETAINED,
            })
            continue
        if not (row.get('raw_area_m2') or 0) > 0:
            continue
        pool[stratum_of(item, feat)].append(fid)

    rng = random.Random(seed)
    chosen = {}
    chosen[S_SHORT] = _take(rng, pool[S_SHORT], CAP_SHORT_HIGH_RATE)
    chosen[S_NO_WIDTH] = _take(rng, pool[S_NO_WIDTH], CAP_AUTO_NO_WIDTH)
    chosen[S_EQUAL] = _take(rng, pool[S_EQUAL], CAP_EQUAL_RECENT)
    chosen[S_CONTIG] = _take(rng, pool[S_CONTIG], contiguous_n)
    cells = defaultdict(list)
    for fid in pool[S_RANDOM]:
        item = items_by_id[fid]
        kind = 'AUTO' if item.get('mode_name') == st.TARGET_MODE else 'MANUAL'
        cells['%s|%s' % (item.get('hardware_id') or 'NO_HARDWARE', kind)
              ].append(fid)
    allocation = allocate_random(cells, random_n, RANDOM_CELL_FLOOR)
    picked = []
    for key in sorted(cells):
        picked.extend(_take(rng, cells[key], allocation[key]))
    chosen[S_RANDOM] = sorted(picked)

    controls = []
    for stratum in STRATA_ORDER:
        for fid in chosen[stratum]:
            item, feat = items_by_id[fid], feats[fid]
            controls.append({
                'flight_id': fid,
                'hardware_id': item.get('hardware_id'),
                'report_day': item['report_day'].isoformat(),
                'raw_area_m2': item.get('raw_area_m2'),
                'stratum': stratum,
                'mode_name': feat['mode_name'],
                'width_present': feat['width_present'],
                'duration_s': feat['duration_s'],
                'gap_prev_s': feat['gap_prev_s'],
                'equal_recent_lag': feat['equal_recent_lag'],
                'v4_present_at_plan': bool(item.get('v4_revision_id')),
                'prediction': PRED_NORMAL,
            })

    selected = [c['flight_id'] for c in candidates] + \
        [c['flight_id'] for c in controls]
    capture = sorted(fid for fid in selected
                     if not items_by_id[fid].get('v4_revision_id'))
    return {
        'protocol': PROTOCOL,
        'protocol_version': PROTOCOL_VERSION,
        'period': {'from': date_from.isoformat(), 'to': date_to.isoformat()},
        'seed': seed,
        'frozen': frozen_state(),
        'acceptance': acceptance_constants(),
        'strata': strata_constants(random_n, contiguous_n),
        'population': {
            'flights_in_period': len(in_period),
            'flights_without_hardware': no_hardware,
            'candidates': len(candidates),
            'candidates_scalar_match': sum(
                1 for c in candidates if c['scalar_source_check']),
            'control_pool': {s: len(pool[s]) for s in STRATA_ORDER},
            'control_selected': {s: len(chosen[s]) for s in STRATA_ORDER},
            'random_cells': {k: {'pool': len(cells[k]),
                                 'selected': allocation[k]}
                             for k in sorted(cells)},
        },
        'candidates': candidates,
        'controls': controls,
        'capture_ids': capture,
    }


# ─── list-db: одноразовая списочная база ─────────────────────────────────────

APP_DB_BASENAME = 'transport.db'
NICK_KEY_PREFIX = 'NICK:'


def _utc_text(epoch_s):
    return (datetime(1970, 1, 1) + timedelta(seconds=int(epoch_s))).strftime(
        '%Y-%m-%d %H:%M:%S')


def cmd_list_db(args):
    """Минимальная база из дампов списка: ровно то, что читает `pipeline`.

    [REASON]: замороженный экран исполняет `dji_area.pipeline`, а ему нужна
    база. Строить ради этого приложение нельзя -- `from app import app` зовёт
    `db.create_all()` и превращает читателя в писателя. Здесь только stdlib
    `sqlite3`, две таблицы с колонками, которые конвейер действительно читает,
    и восемь таблиц `dji_*`, чей DDL берётся из САМОЙ миграции.
    """
    if os.path.basename(args.db_path).lower() == APP_DB_BASENAME:
        # [REASON]: одноразовая база не имеет права носить имя базы приложения:
        # одна опечатка в пути -- и инструмент создал бы `instance/transport.db`
        # там, где приложение потом примет его за свою.
        raise HoldoutError('refusing the application database name %s for a '
                           'disposable list database' % APP_DB_BASENAME)
    if os.path.exists(args.db_path):
        raise HoldoutError('%s already exists - refusing to overwrite'
                           % args.db_path)
    import migrate_dji_area_evidence_001 as evidence_migration

    records, inputs = {}, []
    for path in args.list_json:
        if not os.path.exists(path):
            raise HoldoutError('list dump not found at %s' % path)
        with open(path, encoding='utf-8-sig') as fh:
            document = json.load(fh)
        flights = document.get('flights') if isinstance(document, dict) else None
        if not isinstance(flights, list):
            raise HoldoutError('%s is not a collector list dump: no "flights" '
                               'array' % path)
        fresh = 0
        for record in flights:
            try:
                fid = int(record['id'])
                int(record['start_timestamp'])
                int(record['end_timestamp'])
            except (KeyError, TypeError, ValueError):
                raise HoldoutError('%s: a flight record without numeric id / '
                                   'start_timestamp / end_timestamp' % path)
            if fid not in records:
                records[fid] = record
                fresh += 1
        inputs.append({'path': os.path.abspath(path),
                       'sha256': file_sha256(path),
                       'flights': len(flights), 'new': fresh})
    if not records:
        raise HoldoutError('the list dumps hold no flight at all')

    nicknames = sorted({(r.get('nickname') or '').strip()
                        for r in records.values()} - {''})
    unit_id = {nick: pos for pos, nick in enumerate(nicknames, start=1)}
    os.makedirs(os.path.dirname(os.path.abspath(args.db_path)), exist_ok=True)
    con = sqlite3.connect(args.db_path)
    try:
        con.execute('CREATE TABLE drone_units (id INTEGER PRIMARY KEY, '
                    'hardware_id TEXT)')
        con.execute('CREATE TABLE drone_flights (id INTEGER PRIMARY KEY, '
                    'dji_flight_id BIGINT UNIQUE, started_at TEXT, '
                    'finished_at TEXT, raw_json TEXT, drone_unit_id INTEGER, '
                    'nickname_raw TEXT)')
        for _name, ddl in evidence_migration.TABLES:
            con.execute(ddl)
        for nick, uid in unit_id.items():
            con.execute('INSERT INTO drone_units (id, hardware_id) VALUES (?,?)',
                        (uid, NICK_KEY_PREFIX + nick))
        for fid in sorted(records):
            record = records[fid]
            nick = (record.get('nickname') or '').strip()
            con.execute(
                'INSERT INTO drone_flights (dji_flight_id, started_at, '
                'finished_at, raw_json, drone_unit_id, nickname_raw) '
                'VALUES (?,?,?,?,?,?)',
                (fid, _utc_text(record['start_timestamp']),
                 _utc_text(record['end_timestamp']),
                 json.dumps(record, ensure_ascii=False, sort_keys=True),
                 unit_id.get(nick), nick or None))
        con.commit()
    finally:
        con.close()

    starts = [int(r['start_timestamp']) for r in records.values()]
    print('DJI AREA HOLDOUT LIST DATABASE')
    for item in inputs:
        print('  input             : %s (%d flights, %d new) sha256 %s'
              % (item['path'], item['flights'], item['new'], item['sha256']))
    print('  flights           : %d' % len(records))
    print('  aircraft by nick  : %d (without nickname: %d)'
          % (len(nicknames), sum(1 for r in records.values()
                                 if not (r.get('nickname') or '').strip())))
    print('  first / last start: %s .. %s UTC' % (_utc_text(min(starts)),
                                                  _utc_text(max(starts))))
    print('  database          : %s' % args.db_path)
    print('This is a disposable LIST-only database. It is not an application '
          'database.')
    return EXIT_OK


# ─── plan ────────────────────────────────────────────────────────────────────

def cmd_plan(args):
    date_from = date.fromisoformat(args.date_from)
    plan_path = os.path.join(args.out, 'plan.json')
    if os.path.exists(plan_path):
        # [REASON]: пред-регистрация, которую можно молча перегенерировать,
        # ничего не регистрирует. Другой посев или другой период -- это другой
        # план в другом каталоге, и оба остаются на диске.
        raise HoldoutError('a plan already exists at %s - refusing to '
                           'overwrite a pre-registration' % plan_path)
    before = None if args.skip_db_hash else file_sha256(args.db_path) \
        if os.path.exists(args.db_path) else None
    con = open_readonly(args.db_path)
    try:
        date_to = resolve_period(con, date_from, args.date_to)
        _summary, rows, items = run_pipeline(con, args.db_path, date_from,
                                             date_to, _progress(args))
    finally:
        con.close()
    after = None if args.skip_db_hash else file_sha256(args.db_path)
    if before != after:
        raise HoldoutError('the database changed while it was being read '
                           '(is the service running?)', EXIT_INTEGRITY)

    locked = build_plan(rows, items, date_from, date_to, args.seed,
                        args.random_n, args.contiguous_n)
    document = {'locked': locked, 'locked_sha256': locked_sha(locked),
                'tool_sha256': lf_sha256(os.path.join(
                    ROOT, TOOL_FILE.replace('/', os.sep))),
                'created_at_utc': datetime.now(timezone.utc).strftime(
                    '%Y-%m-%dT%H:%M:%SZ'),
                'db_sha256_at_plan': after}
    os.makedirs(args.out, exist_ok=True)
    with open(plan_path, 'w', encoding='utf-8', newline='\n') as fh:
        json.dump(document, fh, ensure_ascii=True, indent=1, sort_keys=True)
    ids_path = os.path.join(args.out, 'capture_ids.txt')
    with open(ids_path, 'w', encoding='ascii', newline='\n') as fh:
        fh.write('# DJI-AREA-SIMPLIFY-001 holdout: minimal targeted V4 capture\n')
        fh.write('# plan sha256 %s\n' % document['locked_sha256'])
        fh.write('# period %s..%s ; %d flight id(s)\n'
                 % (locked['period']['from'], locked['period']['to'],
                    len(locked['capture_ids'])))
        for fid in locked['capture_ids']:
            fh.write('%d\n' % fid)

    pop = locked['population']
    print('DJI AREA HOLDOUT PLAN')
    print('  period            : %s .. %s' % (locked['period']['from'],
                                              locked['period']['to']))
    print('  algorithm         : %s' % dji_area.AREA_ALGORITHM_VERSION)
    print('  structural rule   : %s' % dji_area.STRUCTURAL_RULE_VERSION)
    print('  flights in period : %d (without hardware id: %d)'
          % (pop['flights_in_period'], pop['flights_without_hardware']))
    print('  candidates        : %d (scalar match %d)'
          % (pop['candidates'], pop['candidates_scalar_match']))
    for s in STRATA_ORDER:
        print('  control %-20s: %d of %d' % (s, pop['control_selected'][s],
                                             pop['control_pool'][s]))
    print('  selected total    : %d' % (len(locked['candidates'])
                                        + len(locked['controls'])))
    print('  V4 to capture     : %d (already present: %d)'
          % (len(locked['capture_ids']),
             len(locked['candidates']) + len(locked['controls'])
             - len(locked['capture_ids'])))
    print('  plan              : %s' % plan_path)
    print('  capture ids       : %s' % ids_path)
    print('  PLAN SHA256       : %s' % document['locked_sha256'])
    print('Nothing was written to the database.')
    return EXIT_OK


# ─── Статистика ──────────────────────────────────────────────────────────────

def _log_binom_tail_ge(k, n, p):
    """log P(X >= k), X ~ Bin(n, p); точная сумма в логарифмах."""
    if k <= 0:
        return 0.0
    if p <= 0.0:
        return float('-inf')
    if p >= 1.0:
        return 0.0
    terms = []
    lp, lq = math.log(p), math.log1p(-p)
    for i in range(k, n + 1):
        terms.append(math.lgamma(n + 1) - math.lgamma(i + 1)
                     - math.lgamma(n - i + 1) + i * lp + (n - i) * lq)
    top = max(terms)
    return top + math.log(sum(math.exp(t - top) for t in terms))


def clopper_pearson_lower(k, n, confidence=CONFIDENCE):
    """Односторонняя нижняя граница доли успехов (точная)."""
    if n == 0 or k == 0:
        return 0.0
    target = math.log(1.0 - confidence)
    lo, hi = 0.0, 1.0
    for _ in range(80):
        mid = (lo + hi) / 2.0
        if _log_binom_tail_ge(k, n, mid) < target:
            lo = mid
        else:
            hi = mid
    return lo


def clopper_pearson_upper(k, n, confidence=CONFIDENCE):
    """Односторонняя верхняя граница доли (через симметрию)."""
    if n == 0:
        return 1.0
    return 1.0 - clopper_pearson_lower(n - k, n, confidence)


# ─── report ──────────────────────────────────────────────────────────────────

def load_plan(path):
    if not os.path.exists(path):
        raise HoldoutError('plan not found at %s' % path)
    with open(path, encoding='utf-8') as fh:
        document = json.load(fh)
    locked = document.get('locked')
    if not isinstance(locked, dict) \
            or locked_sha(locked) != document.get('locked_sha256'):
        raise HoldoutError('the plan does not match its own sha256 - it was '
                           'edited after it was locked', EXIT_INTEGRITY)
    if locked.get('protocol') != PROTOCOL \
            or locked.get('protocol_version') != PROTOCOL_VERSION:
        raise HoldoutError('the plan belongs to another protocol version',
                           EXIT_INTEGRITY)
    return document


def check_frozen(locked):
    """Расхождения замороженного кода и констант с планом (список строк)."""
    problems = []
    now = frozen_state()
    for name, value in sorted(locked['frozen']['versions'].items()):
        if now['versions'].get(name) != value:
            problems.append('version %s: plan %s, now %s'
                            % (name, value, now['versions'].get(name)))
    for rel, sha in sorted(locked['frozen']['files'].items()):
        if now['files'].get(rel) != sha:
            problems.append('file %s changed since the plan was locked' % rel)
    if locked.get('acceptance') != acceptance_constants():
        problems.append('acceptance constants differ from the plan')
    expect = strata_constants(locked['strata']['n_random'],
                              locked['strata']['n_contiguous_auto'])
    if locked.get('strata') != expect:
        problems.append('strata constants differ from the plan')
    return problems


def candidate_outcome(row):
    """HIT / MISS / REFUTED / NOT_EVALUABLE и причина -- PREREG, раздел 7."""
    if row is None:
        return 'NOT_EVALUABLE', 'RECORD_NOT_IN_DATABASE'
    status = row.get('area_status')
    if row.get('evidence_status') and status == rs.OVERLAP_REVIEW:
        # Пересечение интервалов перекрывает статус; решение по счётчику
        # сохранено в evidence_status, и оценивается именно оно.
        status = row['evidence_status']
    if status in CORROBORATED:
        return 'REFUTED', status
    delta = row.get('controller_delta_area_m2')
    raw = row.get('raw_area_m2')
    if delta is None or not raw:
        return 'NOT_EVALUABLE', (row.get('counter_window_quality')
                                 or 'NO_USABLE_V4')
    if delta <= CANDIDATE_DELTA_MAX_SHARE * raw:
        return 'HIT', status
    return 'MISS', status


def control_outcome(row):
    """CORROBORATED / RULE_MISS / OTHER / NOT_EVALUABLE -- PREREG, раздел 8."""
    if row is None:
        return 'NOT_EVALUABLE', 'RECORD_NOT_IN_DATABASE'
    status = row.get('area_status')
    if row.get('evidence_status') and status == rs.OVERLAP_REVIEW:
        status = row['evidence_status']
    if status in CORROBORATED:
        return 'CORROBORATED', status
    if status in OVERSTATED:
        return 'RULE_MISS', status
    if status in (rs.COUNTER_RELATIONSHIP_OUTLIER,
                  rs.COUNTER_NONMONOTONE_REVIEW):
        return 'OTHER', status
    return 'NOT_EVALUABLE', status or 'NO_STATUS'


def gap_bucket(gap):
    if gap is None:
        return 'NO_PREV'
    if gap < 0:
        return 'OVERLAP'
    if gap <= 1:
        return '0-1s'
    if gap <= 120:
        return '2-120s'
    return '>120s'


def signature(control):
    return '|'.join((
        'mode=%s' % control.get('mode_name'),
        'width=%s' % ('Y' if control.get('width_present') else 'N'),
        'gap=%s' % gap_bucket(control.get('gap_prev_s')),
        'equal=%s' % ('Y' if control.get('equal_recent_lag') else 'N')))


def control_gate_of(random_misses, miss_signatures):
    """Ворота контроля: (состояние, причина, подробность).

    Пред-регистрация, раздел 8 с дополнением от 19.09.2026: приёмка КАНДИДАТОВ
    не меняется, но общий holdout не может быть PASS при систематическом
    пропуске правила в контроле. Пороги те же, что и прежде считали
    ``systematic_miss_suspected``; изменилось только последствие.
    """
    if random_misses >= SYSTEMATIC_MIN_RANDOM_MISSES:
        return 'FAIL', 'SYSTEMATIC_MISS_IN_RANDOM_STRATUM', (
            '%d rule miss(es) in %s, threshold %d'
            % (random_misses, S_RANDOM, SYSTEMATIC_MIN_RANDOM_MISSES))
    repeated = sorted((sig, n) for sig, n in miss_signatures.items()
                      if n >= SYSTEMATIC_MIN_SIGNATURE_MISSES)
    if repeated:
        return 'FAIL', 'SYSTEMATIC_MISS_BY_SIGNATURE', '; '.join(
            '%s x%d' % (sig, n) for sig, n in repeated)
    return 'PASS', 'NO_SYSTEMATIC_MISS', None


def build_report(document, rows, nick_by_flight, frozen_problems):
    locked = document['locked']
    by_id = {r['flight_id']: r for r in rows}
    planned_candidates = {c['flight_id'] for c in locked['candidates']}

    # ── Кандидаты ────────────────────────────────────────────────────────
    cand_lines, tally = [], Counter()
    for c in locked['candidates']:
        row = by_id.get(c['flight_id'])
        if not c['scalar_source_check']:
            outcome, why = 'NOT_SCORED', 'SCALAR_MISMATCH_AT_PLAN'
        else:
            outcome, why = candidate_outcome(row)
        tally[outcome] += 1
        decision = acc.classify(row) if row else None
        cand_lines.append(dict(c, outcome=outcome, outcome_reason=why,
                               area_status=row and row.get('area_status'),
                               controller_delta_area_m2=row and row.get(
                                   'controller_delta_area_m2'),
                               accounting_class=decision and decision[
                                   'accounting_class'],
                               accounting_reason=decision and decision[
                                   'reason']))
    scored = sum(tally[k] for k in ('HIT', 'MISS', 'REFUTED', 'NOT_EVALUABLE'))
    evaluable = tally['HIT'] + tally['MISS'] + tally['REFUTED']
    hit_share = tally['HIT'] / float(evaluable) if evaluable else None
    evaluable_share = evaluable / float(scored) if scored else None
    if scored == 0:
        cand_verdict, cand_reason = 'INCONCLUSIVE', 'NO_SCORED_CANDIDATES'
    elif tally['REFUTED'] > 0:
        cand_verdict, cand_reason = 'FAIL', 'CANDIDATE_REFUTED_BY_COUNTER'
    elif evaluable_share < MIN_EVALUABLE_SHARE:
        cand_verdict, cand_reason = ('INCONCLUSIVE',
                                     'EVALUABLE_SHARE_BELOW_MINIMUM')
    elif hit_share >= CANDIDATE_PASS_SHARE:
        cand_verdict, cand_reason = 'PASS', 'BOTH_CRITERIA_MET'
    else:
        cand_verdict, cand_reason = 'FAIL', 'HIT_SHARE_BELOW_THRESHOLD'

    # ── Контроль ─────────────────────────────────────────────────────────
    ctrl_lines = []
    by_stratum = defaultdict(Counter)
    miss_signatures = Counter()
    for c in locked['controls']:
        row = by_id.get(c['flight_id'])
        outcome, why = control_outcome(row)
        by_stratum[c['stratum']][outcome] += 1
        if outcome == 'RULE_MISS':
            miss_signatures[signature(c)] += 1
        ctrl_lines.append(dict(c, outcome=outcome, outcome_reason=why,
                               area_status=row and row.get('area_status'),
                               controller_delta_area_m2=row and row.get(
                                   'controller_delta_area_m2'),
                               counter_observed_delta_m2=row and row.get(
                                   'counter_observed_delta_m2'),
                               signature=signature(c)))
    random_tally = by_stratum[S_RANDOM]
    random_evaluable = (random_tally['CORROBORATED'] + random_tally['RULE_MISS']
                        + random_tally['OTHER'])
    total_misses = sum(t['RULE_MISS'] for t in by_stratum.values())
    gate, gate_reason, gate_detail = control_gate_of(
        random_tally['RULE_MISS'], miss_signatures)
    systematic = gate == 'FAIL'

    # ── Общий вердикт ────────────────────────────────────────────────────
    # [REASON]: порядок намеренный. Целостность важнее любого числа; отказ по
    # кандидатам и отказ по воротам оба дают FAIL, и оба обязаны перебивать
    # INCONCLUSIVE -- «мало данных» не смеет прятать доказанную находку.
    if frozen_problems:
        verdict, verdict_reason = 'INVALID', 'FROZEN_CODE_OR_CONSTANTS_CHANGED'
    elif cand_verdict == 'FAIL':
        verdict, verdict_reason = 'FAIL', cand_reason
    elif gate == 'FAIL':
        verdict, verdict_reason = 'FAIL', gate_reason
    elif cand_verdict == 'INCONCLUSIVE':
        verdict, verdict_reason = 'INCONCLUSIVE', cand_reason
    else:
        verdict, verdict_reason = 'PASS', 'CANDIDATES_AND_CONTROL_GATE_PASSED'

    # ── Раздельные величины периода ──────────────────────────────────────
    lo, hi = locked['period']['from'], locked['period']['to']
    period_rows = [r for r in rows if lo <= r['report_day'] <= hi]
    summary = acc.summarize(period_rows, key_fn=lambda r: r.get('hardware_id')
                            or 'NO_HARDWARE')
    nick_by_hw = defaultdict(Counter)
    for r in period_rows:
        nick = nick_by_flight.get(r['flight_id'])
        if nick:
            nick_by_hw[r.get('hardware_id') or 'NO_HARDWARE'][nick] += 1
    unplanned = sorted(r['flight_id'] for r in period_rows
                       if r.get('structural_candidate')
                       and r['flight_id'] not in planned_candidates)
    # [REASON]: план мог быть составлен на списочной базе с хронологией по
    # нику, а отчёт идёт по базе с паспортным hardware_id. Экран один и тот же,
    # но если группировка записей разошлась, кандидат плана перестанет быть
    # кандидатом здесь -- и это обязано быть видно, а не раствориться в итоге.
    not_flagged_now = sorted(
        c['flight_id'] for c in locked['candidates']
        if c['flight_id'] in by_id
        and not by_id[c['flight_id']].get('structural_candidate'))

    return {
        'protocol': PROTOCOL,
        'plan_sha256': document['locked_sha256'],
        'period': locked['period'],
        'frozen_problems': frozen_problems,
        'verdict': verdict,
        'verdict_reason': verdict_reason,
        'candidate_verdict': cand_verdict,
        'candidate_verdict_reason': cand_reason,
        'control_gate': gate,
        'control_gate_reason': gate_reason,
        'control_gate_detail': gate_detail,
        'candidates': {
            'planned': len(locked['candidates']),
            'scored': scored,
            'not_scored_scalar_mismatch': tally['NOT_SCORED'],
            'hit': tally['HIT'], 'miss': tally['MISS'],
            'refuted': tally['REFUTED'],
            'not_evaluable': tally['NOT_EVALUABLE'],
            'evaluable': evaluable,
            'evaluable_share': evaluable_share,
            'hit_share_of_evaluable': hit_share,
            'hit_share_lower_bound_95': clopper_pearson_lower(
                tally['HIT'], evaluable) if evaluable else None,
            'v4_present_at_plan': sum(1 for c in locked['candidates']
                                      if c['v4_present_at_plan']),
            'unplanned_candidates_now': unplanned,
            'planned_but_not_flagged_now': not_flagged_now,
        },
        'controls': {
            'planned': len(locked['controls']),
            'by_stratum': {s: dict(by_stratum[s]) for s in STRATA_ORDER},
            'rule_misses': total_misses,
            'rule_miss_signatures': dict(miss_signatures),
            'random_evaluable': random_evaluable,
            'random_rule_misses': random_tally['RULE_MISS'],
            'random_miss_prevalence_upper_95': clopper_pearson_upper(
                random_tally['RULE_MISS'], random_evaluable)
            if random_evaluable else None,
            'systematic_miss_suspected': systematic,
            'gate': gate,
            'gate_reason': gate_reason,
            'gate_detail': gate_detail,
        },
        'period_totals': summary['total'],
        'by_aircraft': {hw: dict(bucket, nickname=(
            nick_by_hw[hw].most_common(1)[0][0] if nick_by_hw[hw] else None))
            for hw, bucket in summary['by_key'].items()},
        'candidate_records': cand_lines,
        'control_records': ctrl_lines,
    }


def _ha(m2):
    return '%.4f' % ((m2 or 0.0) / 10000.0)


def _pct(value):
    return 'n/a' if value is None else '%.2f %%' % (100.0 * value)


def render_markdown(report):
    t = report['period_totals']
    c = report['candidates']
    k = report['controls']
    cls, raw = t['class_records'], t['class_raw_m2']
    out = []
    out.append('# DJI-AREA-SIMPLIFY-001 -- holdout %s .. %s'
               % (report['period']['from'], report['period']['to']))
    out.append('')
    out.append('Plan SHA-256: `%s`. Verdict: **%s** (%s).'
               % (report['plan_sha256'], report['verdict'],
                  report['verdict_reason']))
    out.append('')
    out.append('Candidate verdict: **%s** (%s). Control gate: **%s** (%s).'
               % (report['candidate_verdict'],
                  report['candidate_verdict_reason'], report['control_gate'],
                  report['control_gate_reason']))
    if report['control_gate_detail']:
        out.append('')
        out.append('Control gate detail: %s.' % report['control_gate_detail'])
    if report['frozen_problems']:
        out.append('')
        out.append('**INVALID: frozen code or constants changed since the '
                   'plan was locked.**')
        out.extend('- %s' % p for p in report['frozen_problems'])
    out.append('')
    out.append('## Candidates (retained branch)')
    out.append('')
    out.append('| planned | scored | evaluable | HIT | MISS | REFUTED | '
               'NOT_EVALUABLE | hit share | lower 95 % | evaluable share |')
    out.append('|---|---|---|---|---|---|---|---|---|---|')
    out.append('| %d | %d | %d | %d | %d | %d | %d | %s | %s | %s |' % (
        c['planned'], c['scored'], c['evaluable'], c['hit'], c['miss'],
        c['refuted'], c['not_evaluable'], _pct(c['hit_share_of_evaluable']),
        _pct(c['hit_share_lower_bound_95']), _pct(c['evaluable_share'])))
    out.append('')
    out.append('Acceptance: hit share >= %s of evaluable, no REFUTED, evaluable '
               'share >= %s. Candidates with V4 already present at plan time: '
               '%d. Candidates that appeared after the plan (not scored): %d. '
               'Planned candidates this database does not flag: %d.'
               % (_pct(CANDIDATE_PASS_SHARE), _pct(MIN_EVALUABLE_SHARE),
                  c['v4_present_at_plan'], len(c['unplanned_candidates_now']),
                  len(c['planned_but_not_flagged_now'])))
    out.append('')
    out.append('## NORMAL control (rule misses)')
    out.append('')
    out.append('| stratum | CORROBORATED | RULE_MISS | OTHER | NOT_EVALUABLE |')
    out.append('|---|---|---|---|---|')
    for s in STRATA_ORDER:
        b = k['by_stratum'].get(s, {})
        out.append('| %s | %d | %d | %d | %d |' % (
            s, b.get('CORROBORATED', 0), b.get('RULE_MISS', 0),
            b.get('OTHER', 0), b.get('NOT_EVALUABLE', 0)))
    out.append('')
    out.append('Rule misses: %d. Random stratum: %d of %d evaluable, upper 95 %% '
               'bound on prevalence %s. Control gate: %s (%s). Thresholds: >= %d '
               'rule miss(es) in %s, or >= %d of one signature in any stratum, '
               'fail the holdout; single non-systematic findings stay visible '
               'and do not.'
               % (k['rule_misses'], k['random_rule_misses'],
                  k['random_evaluable'],
                  _pct(k['random_miss_prevalence_upper_95']), k['gate'],
                  k['gate_reason'], SYSTEMATIC_MIN_RANDOM_MISSES, S_RANDOM,
                  SYSTEMATIC_MIN_SIGNATURE_MISSES))
    for sig, n in sorted(k['rule_miss_signatures'].items()):
        out.append('- `%s`: %d' % (sig, n))
    out.append('')
    out.append('## Period quantities (kept separate, ha)')
    out.append('')
    out.append('| quantity | records | ha |')
    out.append('|---|---|---|')
    out.append('| DJI RAW | %d | %s |' % (t['records'], _ha(t['raw_sum_m2'])))
    out.append('| NORMAL | %d | %s |' % (cls[acc.NORMAL], _ha(raw[acc.NORMAL])))
    out.append('| PHANTOM_PROVEN, RAW exposure | %d | %s |'
               % (cls[acc.PHANTOM_PROVEN], _ha(raw[acc.PHANTOM_PROVEN])))
    out.append('| -- validated real delta inside them | | %s |'
               % _ha(t['proven_validated_delta_m2']))
    out.append('| -- confirmed overstatement | | %s |'
               % _ha(t['confirmed_overstatement_m2']))
    out.append('| PHANTOM_STRUCTURAL, unresolved exposure (NOT zeroed) | %d | %s |'
               % (cls[acc.PHANTOM_STRUCTURAL], _ha(raw[acc.PHANTOM_STRUCTURAL])))
    out.append('| REVIEW exposure (no automatic correction) | %d | %s |'
               % (cls[acc.REVIEW], _ha(raw[acc.REVIEW])))
    out.append('| certified subtotal (evidence layer) | %d | %s |'
               % (t['certified_records'], _ha(t['certified_sum_m2'])))
    out.append('| RAW minus confirmed overstatement (reference, not a total) | '
               '| %s |' % _ha(t['raw_minus_confirmed_overstatement_m2']))
    out.append('')
    out.append('Partition RAW = NORMAL + PROVEN + STRUCTURAL + REVIEW holds: %s.'
               % ('yes' if t['partition_holds'] else 'NO'))
    out.append('')
    out.append('## By aircraft (ha)')
    out.append('')
    out.append('| aircraft | records | RAW | PROVEN n | confirmed overstatement '
               '| share of RAW | STRUCTURAL n | STRUCTURAL ha | REVIEW n | '
               'REVIEW ha |')
    out.append('|---|---|---|---|---|---|---|---|---|---|')
    for hw, b in sorted(report['by_aircraft'].items(),
                        key=lambda kv: -kv[1]['raw_sum_m2']):
        share = (b['confirmed_overstatement_m2'] / b['raw_sum_m2']
                 if b['raw_sum_m2'] else None)
        out.append('| %s (%s) | %d | %s | %d | %s | %s | %d | %s | %d | %s |' % (
            b.get('nickname') or '?', hw, b['records'], _ha(b['raw_sum_m2']),
            b['class_records'][acc.PHANTOM_PROVEN],
            _ha(b['confirmed_overstatement_m2']), _pct(share),
            b['class_records'][acc.PHANTOM_STRUCTURAL],
            _ha(b['class_raw_m2'][acc.PHANTOM_STRUCTURAL]),
            b['class_records'][acc.REVIEW], _ha(b['class_raw_m2'][acc.REVIEW])))
    out.append('')
    return '\n'.join(out)


def write_csv(path, lines, columns):
    with open(path, 'w', encoding='utf-8', newline='') as fh:
        writer = csv.writer(fh)
        writer.writerow(columns)
        for line in lines:
            writer.writerow([line.get(col) for col in columns])


def cmd_report(args):
    document = load_plan(args.plan_path)
    locked = document['locked']
    frozen_problems = check_frozen(locked)
    if os.path.exists(args.out) and os.listdir(args.out):
        raise HoldoutError('output directory %s is not empty - refusing to mix '
                           'two reports' % args.out)
    date_from = date.fromisoformat(locked['period']['from'])
    date_to = date.fromisoformat(locked['period']['to'])
    before = None if args.skip_db_hash else file_sha256(args.db_path) \
        if os.path.exists(args.db_path) else None
    con = open_readonly(args.db_path)
    try:
        _summary, rows, _items = run_pipeline(con, args.db_path, date_from,
                                              date_to, _progress(args))
        nicks = nicknames_by_flight(con, [r['flight_id'] for r in rows])
    finally:
        con.close()
    after = None if args.skip_db_hash else file_sha256(args.db_path)
    if before != after:
        raise HoldoutError('the database changed while it was being read '
                           '(is the service running?)', EXIT_INTEGRITY)

    report = build_report(document, rows, nicks, frozen_problems)
    os.makedirs(args.out, exist_ok=True)
    with open(os.path.join(args.out, 'holdout_report.json'), 'w',
              encoding='utf-8', newline='\n') as fh:
        json.dump(report, fh, ensure_ascii=False, indent=1, default=str)
    with open(os.path.join(args.out, 'holdout_report.md'), 'w',
              encoding='utf-8', newline='\n') as fh:
        fh.write(render_markdown(report))
    write_csv(os.path.join(args.out, 'holdout_candidates.csv'),
              report['candidate_records'],
              ['flight_id', 'hardware_id', 'report_day', 'raw_area_m2',
               'scalar_source_check', 'base_flight_id', 'duration_s',
               'v4_present_at_plan', 'prediction', 'outcome', 'outcome_reason',
               'area_status', 'controller_delta_area_m2', 'accounting_class',
               'accounting_reason'])
    write_csv(os.path.join(args.out, 'holdout_controls.csv'),
              report['control_records'],
              ['flight_id', 'hardware_id', 'report_day', 'raw_area_m2',
               'stratum', 'mode_name', 'width_present', 'duration_s',
               'gap_prev_s', 'equal_recent_lag', 'v4_present_at_plan',
               'prediction', 'outcome', 'outcome_reason', 'area_status',
               'controller_delta_area_m2', 'counter_observed_delta_m2',
               'signature'])

    c, k, t = report['candidates'], report['controls'], report['period_totals']
    print('DJI AREA HOLDOUT REPORT')
    print('  plan sha256       : %s' % report['plan_sha256'])
    print('  period            : %s .. %s' % (report['period']['from'],
                                              report['period']['to']))
    print('  candidates        : planned %d, scored %d, evaluable %d'
          % (c['planned'], c['scored'], c['evaluable']))
    print('  outcomes          : HIT %d, MISS %d, REFUTED %d, NOT_EVALUABLE %d'
          % (c['hit'], c['miss'], c['refuted'], c['not_evaluable']))
    print('  plan vs database  : %d unplanned candidate(s) now, %d planned '
          'candidate(s) not flagged now'
          % (len(c['unplanned_candidates_now']),
             len(c['planned_but_not_flagged_now'])))
    print('  hit share         : %s (lower 95%%: %s), evaluable share %s'
          % (_pct(c['hit_share_of_evaluable']).replace(' %', '%'),
             _pct(c['hit_share_lower_bound_95']).replace(' %', '%'),
             _pct(c['evaluable_share']).replace(' %', '%')))
    print('  control misses    : %d (random stratum %d of %d evaluable)'
          % (k['rule_misses'], k['random_rule_misses'], k['random_evaluable']))
    print('  candidate verdict : %s (%s)' % (report['candidate_verdict'],
                                             report['candidate_verdict_reason']))
    print('  control gate      : %s (%s)%s'
          % (report['control_gate'], report['control_gate_reason'],
             '' if not report['control_gate_detail']
             else ' -- ' + report['control_gate_detail']))
    print('  RAW ha            : %s over %d records'
          % (_ha(t['raw_sum_m2']), t['records']))
    print('  PROVEN            : %d records, exposure %s ha, validated delta '
          '%s ha, confirmed overstatement %s ha'
          % (t['class_records'][acc.PHANTOM_PROVEN],
             _ha(t['class_raw_m2'][acc.PHANTOM_PROVEN]),
             _ha(t['proven_validated_delta_m2']),
             _ha(t['confirmed_overstatement_m2'])))
    print('  STRUCTURAL        : %d records, unresolved exposure %s ha (not '
          'zeroed)' % (t['class_records'][acc.PHANTOM_STRUCTURAL],
                       _ha(t['class_raw_m2'][acc.PHANTOM_STRUCTURAL])))
    print('  REVIEW            : %d records, exposure %s ha'
          % (t['class_records'][acc.REVIEW], _ha(t['class_raw_m2'][acc.REVIEW])))
    print('  partition holds   : %s' % ('yes' if t['partition_holds'] else 'NO'))
    for problem in frozen_problems:
        print('  FROZEN PROBLEM    : %s' % problem)
    print('  VERDICT           : %s (%s)' % (report['verdict'],
                                             report['verdict_reason']))
    print('  report            : %s' % args.out)
    print('Nothing was written to the database.')
    if frozen_problems:
        return EXIT_INTEGRITY
    return {'PASS': EXIT_OK, 'FAIL': EXIT_ACCEPTANCE_FAIL}.get(
        report['verdict'], EXIT_INCONCLUSIVE)


# ─── CLI ─────────────────────────────────────────────────────────────────────

def _progress(args):
    if getattr(args, 'quiet', False):
        return None

    def progress(text):
        print('  ... %s' % text)
    return progress


def build_parser():
    parser = argparse.ArgumentParser(
        prog='dji_area_holdout.py',
        description='Blind holdout of the frozen structural rule (%s, %s). '
                    'Reads the database, never writes it.'
                    % (dji_area.STRUCTURAL_RULE_VERSION,
                       dji_area.AREA_ALGORITHM_VERSION))
    sub = parser.add_subparsers(dest='command')
    list_db = sub.add_parser('list-db', help='build a disposable LIST-only '
                                             'database from collector '
                                             '--dry-run dumps')
    list_db.add_argument('--list-json', action='append', required=True,
                         metavar='PATH', help='out/flights_<from>_<to>.json; '
                                              'may be given more than once')
    list_db.add_argument('--db', dest='db_path', required=True, metavar='PATH')
    plan = sub.add_parser('plan', help='lock candidates, controls, predictions '
                                       'and the minimal V4 capture list')
    plan.add_argument('--db', dest='db_path', default=DEFAULT_DB, metavar='PATH')
    plan.add_argument('--from', dest='date_from', required=True,
                      metavar='YYYY-MM-DD')
    plan.add_argument('--to', dest='date_to', default='auto',
                      metavar='YYYY-MM-DD|auto',
                      help='last report day; "auto" = last complete day in '
                           'the database')
    plan.add_argument('--out', required=True, metavar='DIR')
    plan.add_argument('--seed', type=int, default=DEFAULT_SEED)
    plan.add_argument('--random-n', type=int, default=N_RANDOM)
    plan.add_argument('--contiguous-n', type=int, default=N_CONTIGUOUS_AUTO)
    plan.add_argument('--skip-db-hash', action='store_true',
                      help='do not hash the database before and after reading')
    plan.add_argument('--quiet', action='store_true')
    report = sub.add_parser('report', help='score the locked plan against the '
                                           'captured evidence')
    report.add_argument('--db', dest='db_path', default=DEFAULT_DB,
                        metavar='PATH')
    report.add_argument('--plan', dest='plan_path', required=True,
                        metavar='PATH')
    report.add_argument('--out', required=True, metavar='DIR')
    report.add_argument('--skip-db-hash', action='store_true')
    report.add_argument('--quiet', action='store_true')
    sub.add_parser('fingerprint', help='print the frozen-code fingerprint and '
                                       'the hash of every frozen file')
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_usage()
        return EXIT_USAGE
    try:
        if args.command == 'fingerprint':
            state = frozen_state()
            print('DJI AREA HOLDOUT CODE FINGERPRINT')
            for name, value in sorted(state['versions'].items()):
                print('  %-18s: %s' % (name, value))
            for rel, sha in sorted(state['files'].items()):
                print('  %-40s %s' % (rel, sha))
            print('  CODE FINGERPRINT  : %s' % code_fingerprint())
            return EXIT_OK
        if args.command == 'list-db':
            return cmd_list_db(args)
        if args.command == 'plan':
            if args.random_n < 0 or args.contiguous_n < 0:
                raise HoldoutError('sample sizes must not be negative')
            return cmd_plan(args)
        return cmd_report(args)
    except HoldoutError as exc:
        print('ERROR: %s' % exc)
        return exc.code
    except (ValueError, OSError, store.StoreError, pipeline.PipelineError,
            sqlite3.Error) as exc:
        print('ERROR: %s' % exc)
        return EXIT_USAGE


if __name__ == '__main__':
    sys.exit(main())
