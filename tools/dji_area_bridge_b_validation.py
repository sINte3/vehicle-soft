# -*- coding: utf-8 -*-
"""tools/dji_area_bridge_b_validation.py -- исследование мостиков B в цепочках
A -> B -> C замороженного структурного экрана.

DJI-AREA-BRIDGE-B-VALIDATION-001. Один вопрос: надо ли вместе с доказанным
фантомом C корректировать ещё и запись-мостик B. Инструмент НИЧЕГО не
корректирует и правил не вводит -- он собирает доказательства.

Чего он не делает, и это проверяется тестами:

* не пишет в базу: файл открывается `mode=ro`, SHA-256 снимается до и после;
* не ходит в DJI и вообще в сеть;
* не копирует структурное правило: цепочки строит `dji_area.structural`,
  на тех же группах и в том же порядке, что и конвейер (`pipeline.load_flights`
  + `chronology_key`);
* не вводит порогов: исследовательская категория B -- функция УЖЕ посчитанной
  строки `dji_area_calculations` (статус резолвера, применение, канал).

[REASON]: мостик -- это ЛЮБАЯ запись цепочки между базой и целью, кроме
«mode 4 с шириной». Поэтому B бывает и ручным, и сам оказывается записью
mode 4 без ширины, то есть целью собственной цепочки. Такой B уже исправляется
как C; посчитать его ещё и как B значило бы задвоить коррекцию.

Категории ниже -- ИССЛЕДОВАТЕЛЬСКИЕ. Это не учётные классы
(`dji_area.accounting`), и имя PHANTOM_PROVEN для B намеренно не используется.

Запуск (база -- копия либо площадка с остановленной службой; только чтение):

  & "C:\\Program Files\\Python314\\python.exe" tools\\dji_area_bridge_b_validation.py --db instance\\transport.db --from 2026-09-01 --to 2026-09-18 --out C:\\VehicleSoft_Bridge_B

  --plan plan.json        -- сверить восстановленные цели C с кандидатами плана
  --capture-manifest      -- дополнительно записать bridge_b_v4_capture_ids.txt

Коды возврата: 0 выполнено; 1 ошибка командной строки или данных; 2 база не
найдена (файл НЕ создаётся); 3 восстановленный набор C не совпал с эталоном --
STOP, дальше считать нельзя. Вывод в консоль только ASCII.
"""

import argparse
import csv
import io
import json
import math
import os
import random
import sqlite3
import sys
from collections import Counter, defaultdict
from datetime import date, datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from dji_area import evidence as ev  # noqa: E402
from dji_area import pipeline as pl  # noqa: E402
from dji_area import resolver as rs  # noqa: E402
from dji_area import store  # noqa: E402
from dji_area import structural as st  # noqa: E402
from dji_area import v4 as v4mod  # noqa: E402
from tools.dji_area_holdout import (  # noqa: E402
    clopper_pearson_upper, file_sha256)

TOOL_VERSION = 'bridge-b-validation-1'
DEFAULT_SEED = 20260921
DJI_RECORD_URL = 'https://www.djiag.com/record/%d'

EXIT_OK = 0
EXIT_USAGE = 1
EXIT_NO_DATABASE = 2
EXIT_MISMATCH = 3

# ─── Исследовательские категории B ───────────────────────────────────────────
# Две первые -- структурные метки: они снимают запись с вопроса ДО разбора
# доказательств. Остальные пять заданы постановкой этапа.
B_IS_ITSELF_C = 'B_IS_ITSELF_C'
B_NO_AREA_CLAIMED = 'B_NO_AREA_CLAIMED'
B_ZERO_PROVEN = 'B_ZERO_PROVEN'
B_PARTIAL = 'B_PARTIAL'
B_REAL_WORK = 'B_REAL_WORK'
B_VISUAL_REVIEW_REQUIRED = 'B_VISUAL_REVIEW_REQUIRED'
B_EVIDENCE_CONFLICT = 'B_EVIDENCE_CONFLICT'
CATEGORIES = (B_IS_ITSELF_C, B_NO_AREA_CLAIMED, B_ZERO_PROVEN, B_PARTIAL,
              B_REAL_WORK, B_EVIDENCE_CONFLICT, B_VISUAL_REVIEW_REQUIRED)

_CORROBORATED = (rs.RAW_CORROBORATED, rs.RAW_CORROBORATED_QUALIFIED)

# Типы отрезков одного взлёта -- только для описательной модели переходов.
SEG_AUTO_WITH_WIDTH = 'A'       # mode 4 с шириной: обычная Auto-обработка
SEG_AUTO_NO_WIDTH = 'N'         # mode 4 без ширины: вид цели C
SEG_OTHER = 'M'                 # всё остальное, на практике ручной режим


class BridgeError(Exception):
    pass


# ─── База только на чтение ───────────────────────────────────────────────────

def open_readonly(db_path):
    """[REASON]: `mode=ro` -- отказ на уровне SQLite, а не обещание в
    докстринге. Любой INSERT/UPDATE на этом соединении падает."""
    uri = 'file:%s?mode=ro' % os.path.abspath(db_path).replace('\\', '/')
    con = sqlite3.connect(uri, uri=True, timeout=30)
    con.row_factory = sqlite3.Row
    return con


# ─── Цепочки A -> B1..Bn -> C ────────────────────────────────────────────────

def grouped_records(con, date_from, date_to):
    """{chronology_key: записи по времени} -- ровно как группирует конвейер."""
    items = pl.load_flights(con, date_from, date_to)
    by_key = defaultdict(list)
    for item in items:
        if item['chronology_key']:
            by_key[item['chronology_key']].append(item)
    for records in by_key.values():
        records.sort(key=lambda r: (r['start_ts'] or 0, r['flight_id']))
    return items, by_key


def reconstruct_chains(by_key):
    """Цепочки по ЗАМОРОЖЕННОМУ экрану. Мостиков может быть сколько угодно."""
    chains = []
    for key, records in by_key.items():
        for record, screen in zip(records, st.screen_all(records)):
            if not (screen['candidate'] and record['in_period']):
                continue
            chains.append({
                'chronology_key': key,
                'c_id': record['flight_id'],
                'a_id': screen['base_flight_id'],
                'b_ids': list(screen['bridge_flight_ids']),
                'boundary_gaps_s': list(screen['boundary_gaps_s']),
                'scalar_source_check': screen['scalar_source_check'],
                'rule_version': screen['rule_version'],
            })
    chains.sort(key=lambda c: c['c_id'])
    return chains


def segment_type(record):
    if record.get('mode_name') == st.TARGET_MODE:
        # [REASON]: предикат ширины берётся у замороженного модуля, а не
        # переписывается: иначе «ширина есть» здесь и в правиле разойдутся.
        return (SEG_AUTO_WITH_WIDTH if st._width_present(record)
                else SEG_AUTO_NO_WIDTH)
    return SEG_OTHER


def sortie_signatures(by_key):
    """Описательная модель: из каких отрезков состоят взлёты.

    Взлёт -- максимальная серия записей борта с зазором в пределах правила
    (`MIN_GAP_S..MAX_GAP_S`). Сигнатура -- строка типов отрезков, например
    `AMN`. Никакой классификации: только счёт того, что есть в данных.
    """
    signatures = Counter()
    for records in by_key.values():
        run = []
        for record in records:
            if not record['in_period'] or record['start_ts'] is None:
                if run:
                    signatures[''.join(run)] += 1
                    run = []
                continue
            if run:
                gap = record['start_ts'] - prev['end_ts'] \
                    if prev['end_ts'] is not None else None
                if gap is None or gap < st.MIN_GAP_S or gap > st.MAX_GAP_S:
                    signatures[''.join(run)] += 1
                    run = []
            run.append(segment_type(record))
            prev = record
        if run:
            signatures[''.join(run)] += 1
    return signatures


# ─── Сырые поля LIST (описательные, не входят в расчёт) ──────────────────────

class ListReader(object):
    """Сырые поля записи списка: расход, точка взлёта, время работы.

    [REASON]: это НЕ замена `evidence.select_list_record`. Тот отдаёт только
    скаляры расчёта. Здесь нужны описательные поля, которых расчёт не читает.
    Однозначность записи на странице проверяет всё тот же замороженный
    `select_list_record` -- свою проверку инструмент не изобретает.
    """

    def __init__(self, con, root):
        self.con = con
        self.root = root
        self._pages = {}

    def record(self, flight_id):
        rev = self.con.execute(
            "SELECT * FROM dji_source_revisions WHERE flight_id=? AND "
            "source_type='list' ORDER BY id DESC LIMIT 1",
            (int(flight_id),)).fetchone()
        if rev is None:
            return self._from_raw_json(flight_id)
        document = self._pages.get(rev['sha256'])
        if document is None:
            try:
                document = json.loads(
                    store.read_body(self.root, rev).decode('utf-8'))
            except (store.StoreError, ValueError):
                return None
            self._pages[rev['sha256']] = document
        try:
            ev.select_list_record(document, flight_id)
        except ValueError:
            return None
        rows = document.get('data')
        if isinstance(rows, list):
            for row in rows:
                if isinstance(row, dict) and _int(row.get('id')) == flight_id:
                    return row
            return None
        return document

    def _from_raw_json(self, flight_id):
        row = self.con.execute(
            'SELECT raw_json FROM drone_flights WHERE dji_flight_id=?',
            (int(flight_id),)).fetchone()
        if row is None or not row['raw_json']:
            return None
        try:
            return json.loads(row['raw_json'])
        except ValueError:
            return None


def _int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _num(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


# ─── Геометрия по кадрам V4 (только описательные величины) ───────────────────

def _distance_m(a, b):
    lat = math.radians((a[0] + b[0]) / 2.0)
    dy = (b[0] - a[0]) * 111320.0
    dx = (b[1] - a[1]) * 111320.0 * math.cos(lat)
    return math.hypot(dx, dy)


def v4_geometry(con, root, flight_id):
    """Длина пути, путь с включённым насосом, прямолинейность, концы.

    [REASON]: это описание, а не эвристика «возврат или обработка». Ни одна
    категория B от этих чисел не зависит.
    """
    rev = con.execute(
        "SELECT * FROM dji_source_revisions WHERE flight_id=? AND "
        "source_type='v4' ORDER BY id DESC LIMIT 1",
        (int(flight_id),)).fetchone()
    if rev is None:
        return None
    try:
        decoded = v4mod.decode_v4(store.read_body(root, rev))
    except (store.StoreError, v4mod.V4DecodeError, ValueError):
        return None
    points = [(f['lat'], f['lng'],
               (f.get('spray_flag') or 0) > 0 or (f.get('flow') or 0) > 0)
              for f in decoded.frames if 'lat' in f and 'lng' in f]
    if len(points) < 2:
        return None
    total = pump_on = 0.0
    for p, q in zip(points, points[1:]):
        step = _distance_m(p, q)
        total += step
        if q[2]:
            pump_on += step
    net = _distance_m(points[0], points[-1])
    return {
        'path_m': round(total, 1),
        'pump_on_path_m': round(pump_on, 1),
        'pump_on_share': round(pump_on / total, 4) if total > 0 else None,
        'straightness': round(net / total, 4) if total > 0 else None,
        'first': points[0][:2],
        'last': points[-1][:2],
    }


# ─── Профиль B ───────────────────────────────────────────────────────────────

def load_calculations(con):
    rows = {}
    for row in con.execute('SELECT * FROM dji_area_calculations '
                           'WHERE superseded_at IS NULL'):
        rows[int(row['flight_id'])] = dict(row)
    return rows


def load_v4_summaries(con):
    rows = {}
    for row in con.execute('SELECT * FROM dji_v4_summaries ORDER BY id'):
        rows[int(row['flight_id'])] = dict(row)
    return rows


def load_evidence(con):
    rows = {}
    for row in con.execute('SELECT * FROM dji_flight_evidence'):
        rows[int(row['flight_id'])] = dict(row)
    return rows


def load_nicknames(con):
    return {int(r['dji_flight_id']): r['nickname_raw'] for r in con.execute(
        'SELECT dji_flight_id, nickname_raw FROM drone_flights')}


def research_category(profile):
    """(категория, причина). Чистая функция профиля; порогов не вводит."""
    if profile['is_itself_candidate']:
        return B_IS_ITSELF_C, ('mode 4 without width: a frozen-rule target of '
                               'its own chain, corrected as C and not as B')
    raw = profile['raw_area_m2']
    if raw is None:
        return B_VISUAL_REVIEW_REQUIRED, 'RAW area unknown'
    if raw == 0:
        return B_NO_AREA_CLAIMED, 'RAW area is zero: nothing to correct'
    if not profile['v4_present']:
        return B_VISUAL_REVIEW_REQUIRED, 'V4_NOT_CAPTURED'
    status = profile['area_status']
    activity = profile['application_activity']
    channel = profile['application_channel_quality']
    if status == rs.COUNTER_FLAT_RAW_OVERSTATED:
        if activity == rs.ACT_NOT_OBSERVED:
            return B_ZERO_PROVEN, ('validated counter is exactly flat and no '
                                   'application was observed')
        if activity == rs.ACT_PRESENT:
            return B_EVIDENCE_CONFLICT, ('counter is flat but application '
                                         'was observed')
        return B_VISUAL_REVIEW_REQUIRED, ('counter is flat, application '
                                          'channel cannot say (%s)' % channel)
    if status == rs.PARTIAL_RECORDED_OVERSTATEMENT:
        return B_PARTIAL, ('validated counter grew by less than the recorded '
                           'area')
    if status in _CORROBORATED:
        if activity == rs.ACT_PRESENT:
            return B_REAL_WORK, ('validated counter grew by the recorded area '
                                 'and application was observed')
        if activity == rs.ACT_NOT_OBSERVED:
            return B_EVIDENCE_CONFLICT, (
                'validated counter grew by the recorded area, yet no '
                'application was observed on an informative channel')
        return B_VISUAL_REVIEW_REQUIRED, (
            'counter corroborates the recorded area, application channel '
            'cannot say (%s)' % channel)
    return (B_VISUAL_REVIEW_REQUIRED,
            'resolver status %s needs a person' % status)


def build_profiles(con, root, items, chains, with_geometry=True):
    by_id = {item['flight_id']: item for item in items}
    calcs = load_calculations(con)
    summaries = load_v4_summaries(con)
    evidence = load_evidence(con)
    nicknames = load_nicknames(con)
    reader = ListReader(con, root)
    targets = {chain['c_id'] for chain in chains}
    shared = Counter(b for chain in chains for b in chain['b_ids'])
    geometry_cache = {}

    def geometry(flight_id):
        if not with_geometry:
            return None
        if flight_id not in geometry_cache:
            geometry_cache[flight_id] = (
                v4_geometry(con, root, flight_id)
                if flight_id in summaries else None)
        return geometry_cache[flight_id]

    def usage(flight_id):
        record = reader.record(flight_id) or {}
        return _int(record.get('spray_usage')), record

    slots = []
    for chain in chains:
        a_item = by_id.get(chain['a_id']) or {}
        c_item = by_id.get(chain['c_id']) or {}
        a_usage, a_raw = usage(chain['a_id'])
        c_usage, c_raw = usage(chain['c_id'])
        # [REASON]: мостик, который сам цель C (mode 4 без ширины), несёт
        # НАКОПИТЕЛЬНЫЙ расход взлёта, как и всякая запись этого вида. Сложить
        # его с собственными расходами A и ручных мостиков значило бы учесть
        # начало взлёта дважды.
        b_usages = [usage(b)[0] for b in chain['b_ids'] if b not in targets]
        known = [u for u in [a_usage] + b_usages if u is not None]
        chain['a_raw_m2'] = a_item.get('raw_area_m2')
        chain['c_raw_m2'] = c_item.get('raw_area_m2')
        chain['nickname'] = nicknames.get(chain['c_id'])
        chain['report_day'] = c_item.get('report_day')
        chain['a_spray_usage'] = a_usage
        chain['c_spray_usage'] = c_usage
        chain['b_spray_usage_sum'] = sum(u for u in b_usages if u is not None)
        # Сколько вылито за сам C сверх накопленного за A и B.
        complete = len(known) == 1 + len(b_usages)
        chain['c_usage_residual'] = (c_usage - sum(known)) \
            if c_usage is not None and complete else None
        chain['same_takeoff_point'] = _same_point(
            [a_raw, c_raw] + [usage(b)[1] for b in chain['b_ids']])
        a_area = _num(chain['a_raw_m2'])
        chain['a_litres_per_ha'] = round(
            a_usage / 1000.0 / (a_area / 10000.0), 2) \
            if a_usage and a_area else None
        chain['signature'] = ''.join(
            segment_type(by_id[f]) for f in
            [chain['a_id']] + chain['b_ids'] + [chain['c_id']] if f in by_id)
        c_geo = geometry(chain['c_id'])
        chain['v4_dominant_mode'] = {
            'A': _dominant_mode(summaries.get(chain['a_id'])),
            'C': _dominant_mode(summaries.get(chain['c_id']))}

        for position, b_id in enumerate(chain['b_ids'], 1):
            item = by_id.get(b_id) or {}
            calc = calcs.get(b_id) or {}
            summ = summaries.get(b_id)
            evid = evidence.get(b_id) or {}
            b_usage, b_raw_rec = usage(b_id)
            start, end = item.get('start_ts'), item.get('end_ts')
            raw = _num(item.get('raw_area_m2'))
            b_geo = geometry(b_id)
            following = (chain['b_ids'][position]
                         if position < len(chain['b_ids']) else chain['c_id'])
            next_geo = geometry(following)
            profile = {
                'chain_c_id': chain['c_id'],
                'a_id': chain['a_id'],
                'b_id': b_id,
                'position': 'B%d' % position,
                'bridges_in_chain': len(chain['b_ids']),
                'chains_sharing_b': shared[b_id],
                'is_itself_candidate': b_id in targets,
                'chronology_key': chain['chronology_key'],
                'nickname': nicknames.get(b_id),
                'report_day': item.get('report_day'),
                'start_ts': start,
                'end_ts': end,
                'duration_s': (end - start)
                if start is not None and end is not None else None,
                'gap_before_s': chain['boundary_gaps_s'][position - 1]
                if position - 1 < len(chain['boundary_gaps_s']) else None,
                'gap_after_s': chain['boundary_gaps_s'][position]
                if position < len(chain['boundary_gaps_s']) else None,
                'mode_name': item.get('mode_name'),
                'manual_mode': item.get('manual_mode'),
                'spray_width': item.get('spray_width'),
                'raw_area_m2': raw,
                'a_raw_m2': _num(chain['a_raw_m2']),
                'c_raw_m2': _num(chain['c_raw_m2']),
                'list_value_source': item.get('list_value_source'),
                'list_spray_usage_ml': b_usage,
                'list_work_time_s': _int(b_raw_rec.get('work_time_seconds')),
                'list_serial_number': b_raw_rec.get('serial_number'),
                'has_list': bool(evid.get('list_revision_id')),
                'has_card': bool(evid.get('card_revision_id')),
                'has_route': bool(evid.get('route_revision_id')),
                'has_airlines': bool(evid.get('airlines_revision_id')),
                'v4_present': summ is not None,
                'area_status': calc.get('area_status'),
                'aggregation_eligibility': calc.get('aggregation_eligibility'),
                'counter_baseline_status': calc.get('counter_baseline_status'),
                'counter_window_quality': calc.get('counter_window_quality'),
                'controller_delta_area_m2':
                    _num(calc.get('controller_delta_area_m2')),
                'corrected_recorded_area_m2':
                    _num(calc.get('corrected_recorded_area_m2')),
                'application_activity': calc.get('application_activity'),
                'application_channel_quality':
                    calc.get('application_channel_quality'),
                'application_evidence_kind':
                    calc.get('application_evidence_kind'),
                'anomaly_flags': json.loads(
                    calc.get('anomaly_flags_json') or '[]'),
                'v4_application_frames':
                    summ.get('application_frames') if summ else None,
                'v4_flow_positive_frames':
                    summ.get('flow_positive_frames') if summ else None,
                'v4_quantity_delta_ml':
                    _num(summ.get('quantity_delta')) if summ else None,
                'v4_counter_first_m2': v4mod.native_to_m2(
                    summ['counter_first_encoded_native'])
                if summ and summ.get('counter_first_encoded_native')
                is not None else None,
                'v4_counter_last_m2': v4mod.native_to_m2(
                    summ['counter_last_encoded_native'])
                if summ and summ.get('counter_last_encoded_native')
                is not None else None,
                'v4_dominant_mode': _dominant_mode(summ),
                'v4_path_m': b_geo['path_m'] if b_geo else None,
                'v4_pump_on_share': b_geo['pump_on_share'] if b_geo else None,
                'v4_straightness': b_geo['straightness'] if b_geo else None,
                'join_to_next_m': round(_distance_m(
                    b_geo['last'], next_geo['first']), 2)
                if b_geo and next_geo else None,
            }
            profile['ratio_b_to_a'] = _ratio(raw, profile['a_raw_m2'])
            profile['ratio_b_to_c'] = _ratio(raw, profile['c_raw_m2'])
            profile['equals_a'] = (raw is not None
                                   and raw == profile['a_raw_m2'])
            profile['equals_c'] = (raw is not None
                                   and raw == profile['c_raw_m2'])
            profile['litres_per_ha'] = round(
                b_usage / 1000.0 / (raw / 10000.0), 2) \
                if b_usage and raw else None
            category, reason = research_category(profile)
            profile['category'] = category
            profile['category_reason'] = reason
            slots.append(profile)
        chain['c_v4_pump_on_share'] = c_geo['pump_on_share'] if c_geo else None
        chain['c_v4_path_m'] = c_geo['path_m'] if c_geo else None
    return slots


def _dominant_mode(summary):
    """Преобладающий рабочий режим в кадрах V4; 'None' -- поле отсутствует."""
    if not summary:
        return None
    try:
        histogram = json.loads(summary.get('summary_json') or '{}').get(
            'mode_histogram') or {}
    except ValueError:
        return None
    if not histogram:
        return None
    return max(sorted(histogram.items()), key=lambda kv: kv[1])[0]


def _ratio(a, b):
    if a is None or not b:
        return None
    return round(a / b, 4)


def _same_point(records):
    points = {(r.get('lat'), r.get('lng')) for r in records if r}
    if len([r for r in records if r]) != len(records):
        return None
    if any(p[0] is None or p[1] is None for p in points):
        return None
    return len(points) == 1


def unique_bridges(slots):
    """Один профиль на B: общий для двух цепочек мостик считается ОДИН раз."""
    seen = {}
    for slot in slots:
        seen.setdefault(slot['b_id'], slot)
    return [seen[b] for b in sorted(seen)]


# ─── Сводка ──────────────────────────────────────────────────────────────────

def _ha(m2):
    return round((m2 or 0.0) / 10000.0, 4)


def _percentile(values, share):
    values = sorted(v for v in values if v is not None)
    if not values:
        return None
    index = min(len(values) - 1, int(round(share * (len(values) - 1))))
    return values[index]


def _distribution(values):
    values = [v for v in values if v is not None]
    return {'n': len(values), 'min': min(values) if values else None,
            'p50': _percentile(values, 0.5), 'p90': _percentile(values, 0.9),
            'max': max(values) if values else None}


def summarize(chains, slots, signatures, takeoff_control):
    bridges = unique_bridges(slots)
    manual = [b for b in bridges if not b['is_itself_candidate']]
    with_v4 = [b for b in manual if b['v4_present']]
    by_category = defaultdict(list)
    for bridge in bridges:
        by_category[bridge['category']].append(bridge)

    def area(rows):
        return _ha(sum(r['raw_area_m2'] or 0.0 for r in rows))

    confirmed = 0.0
    for bridge in by_category[B_ZERO_PROVEN] + by_category[B_PARTIAL]:
        delta = bridge['controller_delta_area_m2'] or 0.0
        confirmed += max(0.0, (bridge['raw_area_m2'] or 0.0) - delta)

    review = by_category[B_VISUAL_REVIEW_REQUIRED]
    review_with_usage = [b for b in review
                         if (b['list_spray_usage_ml'] or 0) > 0]
    review_without_usage = [b for b in review
                            if not (b['list_spray_usage_ml'] or 0) > 0]
    flat = [b for b in with_v4
            if b['area_status'] == rs.COUNTER_FLAT_RAW_OVERSTATED]
    ratios = sorted(b['list_spray_usage_ml'] / b['v4_quantity_delta_ml']
                    for b in with_v4
                    if b['list_spray_usage_ml'] and b['v4_quantity_delta_ml'])

    return {
        'tool_version': TOOL_VERSION,
        'structural_rule_version':
            chains[0]['rule_version'] if chains else None,
        'chains': len(chains),
        'bridges_per_chain': dict(sorted(Counter(
            len(c['b_ids']) for c in chains).items())),
        'bridge_slots': len(slots),
        'unique_bridges': len(bridges),
        'bridges_shared_by_several_chains': sum(
            1 for b in bridges if b['chains_sharing_b'] > 1),
        'bridges_that_are_themselves_c': len(by_category[B_IS_ITSELF_C]),
        'scalar_source_check_true': sum(
            1 for c in chains if c['scalar_source_check'] is True),
        'same_takeoff_point_chains': sum(
            1 for c in chains if c['same_takeoff_point'] is True),
        'c_usage_equals_a_plus_b': sum(
            1 for c in chains if c['c_usage_residual'] == 0),
        'c_usage_residual_ml': _distribution(
            [c['c_usage_residual'] for c in chains]),
        'by_drone': dict(sorted(Counter(
            c['nickname'] or 'UNKNOWN' for c in chains).items())),
        'bridge_mode_name': dict(Counter(
            str(b['mode_name']) for b in bridges)),
        'bridge_manual_mode': dict(Counter(
            str(b['manual_mode']) for b in bridges)),
        'bridge_width_present': sum(
            1 for b in bridges if _num(b['spray_width'])),
        'bridge_raw_zero': sum(1 for b in bridges if not b['raw_area_m2']),
        'bridge_raw_positive': sum(1 for b in bridges if b['raw_area_m2']),
        'bridge_raw_total_ha': area(bridges),
        'bridge_raw_ha_by_drone': {
            name: area([b for b in bridges if (b['nickname'] or 'UNKNOWN')
                        == name])
            for name in sorted({b['nickname'] or 'UNKNOWN' for b in bridges})},
        'bridge_raw_m2': _distribution([b['raw_area_m2'] for b in bridges]),
        'bridge_duration_s': _distribution([b['duration_s'] for b in bridges]),
        'gap_a_to_b_s': dict(Counter(
            str(b['gap_before_s']) for b in slots if b['position'] == 'B1')),
        'gap_b_to_next_s': dict(Counter(str(b['gap_after_s']) for b in slots)),
        'list_spray_usage_positive': sum(
            1 for b in manual if (b['list_spray_usage_ml'] or 0) > 0),
        'manual_bridges': len(manual),
        'manual_litres_per_ha': _distribution(
            [b['litres_per_ha'] for b in manual]),
        'base_a_litres_per_ha': _distribution(
            [c['a_litres_per_ha'] for c in chains]),
        'v4_dominant_mode_by_role': {
            'A': dict(Counter(c['v4_dominant_mode']['A'] for c in chains
                              if c['v4_dominant_mode']['A'] is not None)),
            'B': dict(Counter(b['v4_dominant_mode'] for b in manual
                              if b['v4_dominant_mode'] is not None)),
            'C': dict(Counter(c['v4_dominant_mode']['C'] for c in chains
                              if c['v4_dominant_mode']['C'] is not None))},
        'v4_coverage': {
            'manual_with_v4': len(with_v4),
            'manual_without_v4': len(manual) - len(with_v4),
            'counter_flat_among_v4': len(flat),
            'flat_share_upper_95': round(clopper_pearson_upper(
                len(flat), len(with_v4)), 4) if with_v4 else None,
            'application': dict(Counter(
                b['application_activity'] for b in with_v4)),
            'area_status': dict(Counter(b['area_status'] for b in with_v4)),
            'pump_on_share': _distribution(
                [b['v4_pump_on_share'] for b in with_v4]),
            'straightness': _distribution(
                [b['v4_straightness'] for b in with_v4]),
            'join_b_end_to_next_start_m': _distribution(
                [b['join_to_next_m'] for b in with_v4]),
            'list_usage_over_v4_quantity': {
                'n': len(ratios),
                'min': round(ratios[0], 4) if ratios else None,
                'p50': round(ratios[len(ratios) // 2], 4) if ratios else None,
                'max': round(ratios[-1], 4) if ratios else None},
        },
        'c_pump_on_share': _distribution(
            [c['c_v4_pump_on_share'] for c in chains]),
        'categories': {name: {'bridges': len(by_category[name]),
                              'raw_ha': area(by_category[name])}
                       for name in CATEGORIES},
        'B_EVIDENCE_CONFIRMED_OVERSTATEMENT_ha': _ha(confirmed),
        'B_POTENTIAL_OVERSTATEMENT_ha': {
            'evidence_conflict': area(by_category[B_EVIDENCE_CONFLICT]),
            'no_v4_and_no_list_usage': area(review_without_usage),
            'no_v4_but_list_usage_positive': area(review_with_usage),
        },
        'review_without_any_application_evidence': sorted(
            b['b_id'] for b in review_without_usage),
        'sortie_signatures': dict(signatures.most_common(12)),
        'takeoff_point_control': takeoff_control,
    }


def takeoff_point_control(by_key, reader):
    """Контроль признака «одна точка взлёта»: он обязан различать.

    [REASON]: совпадение `lat/lng` у A, B и C что-то значит, только если у
    записей РАЗНЫХ взлётов оно не совпадает. Иначе признак одинаков в обоих
    случаях и признаком не является.
    """
    near = Counter()
    far = Counter()
    for records in by_key.values():
        for prev, nxt in zip(records, records[1:]):
            if not (prev['in_period'] and nxt['in_period']):
                continue
            if prev['end_ts'] is None or nxt['start_ts'] is None:
                continue
            gap = nxt['start_ts'] - prev['end_ts']
            if st.MIN_GAP_S <= gap <= st.MAX_GAP_S:
                bucket = near
            elif gap > 60:
                bucket = far
            else:
                continue
            same = _same_point([reader.record(prev['flight_id']),
                                reader.record(nxt['flight_id'])])
            if same is not None:
                bucket['same' if same else 'different'] += 1
    return {'neighbours_gap_within_rule': dict(near),
            'neighbours_gap_over_60s': dict(far)}


# ─── Выборка для человека ────────────────────────────────────────────────────

def select_sample(slots, seed=DEFAULT_SEED, typical=15):
    """Все необычные B плюс типичные, по всем бортам. Детерминированно."""
    bridges = unique_bridges(slots)
    rng = random.Random(seed)
    chosen = {}

    def take(bridge, why):
        chosen.setdefault(bridge['b_id'], (bridge, why))

    for bridge in bridges:
        if bridge['category'] in (B_EVIDENCE_CONFLICT, B_PARTIAL,
                                  B_ZERO_PROVEN, B_IS_ITSELF_C):
            take(bridge, bridge['category'])
        elif bridge['bridges_in_chain'] > 1:
            take(bridge, 'MULTI_BRIDGE_CHAIN')
        elif (bridge['category'] == B_VISUAL_REVIEW_REQUIRED
              and not (bridge['list_spray_usage_ml'] or 0) > 0):
            # [REASON]: площадь записана, V4 нет и расхода в LIST нет -- это и
            # есть весь остаток неопределённости. Таких записей единицы, и
            # показать человеку половину значило бы оставить вопрос открытым.
            take(bridge, 'NO_V4_AND_NO_LIST_USAGE')
    for key, why in (('raw_area_m2', 'LARGEST_RAW_AREA'),
                     ('duration_s', 'LONGEST_DURATION')):
        ranked = sorted((b for b in bridges if b[key] is not None),
                        key=lambda b: -b[key])
        for bridge in ranked[:3]:
            take(bridge, why)

    pools = (
        (B_REAL_WORK, 'TYPICAL_V4_REAL_WORK', lambda b: True),
        (B_VISUAL_REVIEW_REQUIRED, 'TYPICAL_NO_V4_LIST_USAGE_POSITIVE',
         lambda b: (b['list_spray_usage_ml'] or 0) > 0),
        (B_NO_AREA_CLAIMED, 'TYPICAL_NO_AREA_CLAIMED', lambda b: True),
    )
    per_pool = typical // len(pools)
    for category, why, keep in pools:
        pool = [b for b in bridges if b['category'] == category and keep(b)
                and b['b_id'] not in chosen]
        by_drone = defaultdict(list)
        for bridge in pool:
            by_drone[bridge['nickname'] or 'UNKNOWN'].append(bridge)
        picked = 0
        # По кругу по бортам: так в выборку попадают ВСЕ машины, а не те,
        # у которых записей больше.
        while picked < per_pool and any(by_drone.values()):
            for name in sorted(by_drone):
                if picked >= per_pool:
                    break
                if by_drone[name]:
                    bridge = by_drone[name].pop(
                        rng.randrange(len(by_drone[name])))
                    take(bridge, why)
                    picked += 1
    # [REASON]: выборка обязана покрывать ВСЕ борта. Необычные записи могут
    # целиком прийтись на несколько машин, и тогда борт без необычных выпал
    # бы из проверки человеком вовсе.
    covered = {(b['nickname'] or 'UNKNOWN') for b, _why in chosen.values()}
    for name in sorted({(b['nickname'] or 'UNKNOWN') for b in bridges}
                       - covered):
        pool = sorted((b for b in bridges
                       if (b['nickname'] or 'UNKNOWN') == name),
                      key=lambda b: b['b_id'])
        take(pool[rng.randrange(len(pool))], 'DRONE_COVERAGE')
    return [chosen[b] for b in sorted(chosen)]


# ─── Вывод ───────────────────────────────────────────────────────────────────

PROFILE_COLUMNS = (
    'chain_c_id', 'a_id', 'b_id', 'position', 'bridges_in_chain',
    'chains_sharing_b', 'nickname', 'report_day', 'duration_s',
    'gap_before_s', 'gap_after_s', 'mode_name', 'manual_mode', 'spray_width',
    'raw_area_m2', 'a_raw_m2', 'c_raw_m2', 'ratio_b_to_a', 'ratio_b_to_c',
    'equals_a', 'equals_c', 'list_spray_usage_ml', 'litres_per_ha',
    'list_work_time_s', 'list_serial_number', 'has_list', 'has_card',
    'has_route', 'has_airlines', 'v4_present', 'counter_baseline_status',
    'counter_window_quality', 'controller_delta_area_m2',
    'corrected_recorded_area_m2', 'area_status', 'aggregation_eligibility',
    'application_activity', 'application_channel_quality',
    'application_evidence_kind', 'v4_application_frames',
    'v4_flow_positive_frames', 'v4_quantity_delta_ml', 'v4_counter_first_m2',
    'v4_counter_last_m2', 'v4_path_m', 'v4_pump_on_share', 'v4_straightness',
    'join_to_next_m', 'category', 'category_reason')


def write_csv(path, columns, rows):
    with io.open(path, 'w', encoding='utf-8-sig', newline='') as fh:
        writer = csv.writer(fh)
        writer.writerow(columns)
        for row in rows:
            writer.writerow([_cell(row.get(c)) for c in columns])


def _cell(value):
    if isinstance(value, (list, tuple)):
        return '; '.join(str(v) for v in value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return value


def write_xlsx(path, summary, chains, slots, sample):
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter
    from openpyxl.worksheet.datavalidation import DataValidation

    head_font = Font(bold=True, color='FFFFFF')
    head_fill = PatternFill('solid', fgColor='1F4E78')
    link_font = Font(color='0563C1', underline='single')

    def header(sheet, titles, widths=None):
        sheet.append(titles)
        for index, cell in enumerate(sheet[sheet.max_row], 1):
            cell.font = head_font
            cell.fill = head_fill
            cell.alignment = Alignment(wrap_text=True, vertical='center')
            sheet.column_dimensions[get_column_letter(index)].width = (
                widths[index - 1] if widths else 16)
        # [REASON]: именно строкой. `sheet.cell(row=2, ...)` СОЗДАЁТ пустую
        # ячейку, и первая строка данных уезжает на третью.
        sheet.freeze_panes = 'A2'

    def link(cell, flight_id, label):
        if flight_id is None:
            return
        cell.value = label
        cell.hyperlink = DJI_RECORD_URL % int(flight_id)
        cell.font = link_font

    book = Workbook()

    # 1. Сводка
    sheet = book.active
    sheet.title = 'Сводка'
    header(sheet, ['Показатель', 'Значение', 'Пояснение'], [52, 26, 90])
    cov = summary['v4_coverage']
    pot = summary['B_POTENTIAL_OVERSTATEMENT_ha']
    rows = [
        ('Цепочек A-B-C', summary['chains'], 'цели C замороженного экрана'),
        ('Мостиков на цепочку', json.dumps(summary['bridges_per_chain']),
         'число мостиков : число цепочек'),
        ('Уникальных B', summary['unique_bridges'],
         'общий для двух цепочек мостик считается один раз'),
        ('B, которые сами являются целью C',
         summary['bridges_that_are_themselves_c'],
         'mode 4 без ширины; уже исправляются как C'),
        ('C повторяет площадь A', '%d из %d' % (
            summary['scalar_source_check_true'], summary['chains']), ''),
        ('A, B и C -- одна точка взлёта', '%d из %d' % (
            summary['same_takeoff_point_chains'], summary['chains']),
         'lat/lng записи LIST совпадают до последнего знака'),
        ('Расход C = A + сумма B (точно)', '%d из %d' % (
            summary['c_usage_equals_a_plus_b'], summary['chains']),
         'у C расход накопительный, у B -- собственный'),
        ('Режим B (mode_name)', json.dumps(summary['bridge_mode_name']), ''),
        ('B с шириной', summary['bridge_width_present'], ''),
        ('B с RAW > 0 / RAW = 0', '%d / %d' % (
            summary['bridge_raw_positive'], summary['bridge_raw_zero']), ''),
        ('RAW всех B, га', summary['bridge_raw_total_ha'], ''),
        ('Ручных B с расходом LIST > 0', '%d из %d' % (
            summary['list_spray_usage_positive'], summary['manual_bridges']),
         'spray_usage записи списка'),
        ('Ручных B с V4 / без V4', '%d / %d' % (
            cov['manual_with_v4'], cov['manual_without_v4']), ''),
        ('Из них с плоским счётчиком', cov['counter_flat_among_v4'],
         'верхняя граница доли 95 %%: %s' % cov['flat_share_upper_95']),
        ('Применение у B с V4', json.dumps(cov['application']), ''),
        ('Доля пути B с включённым насосом, медиана',
         cov['pump_on_share']['p50'], 'по кадрам V4'),
        ('Доля пути C с включённым насосом, медиана',
         summary['c_pump_on_share']['p50'], 'по кадрам V4'),
        ('Стык конца B и начала следующей записи, м (макс.)',
         cov['join_b_end_to_next_start_m']['max'], 'по кадрам V4'),
    ]
    for name in CATEGORIES:
        item = summary['categories'][name]
        rows.append(('Категория %s' % name, item['bridges'],
                     'RAW %.4f га' % item['raw_ha']))
    rows += [
        ('B_EVIDENCE_CONFIRMED_OVERSTATEMENT, га',
         summary['B_EVIDENCE_CONFIRMED_OVERSTATEMENT_ha'],
         'только ZERO_PROVEN и PARTIAL; к C НЕ прибавляется'),
        ('Потенциал: конфликт доказательств, га',
         pot['evidence_conflict'], ''),
        ('Потенциал: нет V4 и нет расхода LIST, га',
         pot['no_v4_and_no_list_usage'], ''),
        ('Нет V4, но расход LIST > 0, га',
         pot['no_v4_but_list_usage_positive'],
         'расход жидкости записан -- признак обработки'),
    ]
    for row in rows:
        sheet.append(list(row))

    # 2. Цепочки_ABC
    sheet = book.create_sheet('Цепочки_ABC')
    header(sheet, ['№', 'Дрон', 'Дата', 'A Flight ID', 'A ссылка', 'A RAW га',
                   'B IDs', 'C Flight ID', 'C ссылка', 'C RAW га',
                   'Мостиков', 'Сигнатура', 'Одна точка взлёта',
                   'Расход C - (A+B), мл'],
           [6, 16, 12, 13, 12, 10, 26, 13, 12, 10, 10, 11, 14, 16])
    for number, chain in enumerate(chains, 1):
        sheet.append([number, chain['nickname'], _cell(chain['report_day']),
                      chain['a_id'], None, _ha(chain['a_raw_m2']),
                      '; '.join(str(b) for b in chain['b_ids']),
                      chain['c_id'], None, _ha(chain['c_raw_m2']),
                      len(chain['b_ids']), chain['signature'],
                      {True: 'да', False: 'НЕТ'}.get(
                          chain['same_takeoff_point'], '?'),
                      chain['c_usage_residual']])
        link(sheet.cell(row=sheet.max_row, column=5), chain['a_id'],
             'ОТКРЫТЬ A')
        link(sheet.cell(row=sheet.max_row, column=9), chain['c_id'],
             'ОТКРЫТЬ C')
    sheet.auto_filter.ref = sheet.dimensions

    # 3-5. Листы с мостиками
    titles = ['№', 'Дрон', 'Дата', 'Chain C ID', 'A ID', 'B ID', 'C ID',
              'Позиция', 'mode', 'manual_mode', 'width', 'Длит., с',
              'RAW B га', 'RAW A га', 'RAW C га', 'Расход LIST, мл', 'л/га',
              'V4', 'Прирост счётчика, м2', 'Применение', 'Канал',
              'Насос вкл., доля пути', 'Категория', 'Причина',
              'ОТКРЫТЬ A', 'ОТКРЫТЬ B', 'ОТКРЫТЬ C',
              'Визуальный вердикт владельца', 'Комментарий владельца']
    widths = [6, 16, 12, 13, 13, 13, 13, 9, 7, 12, 8, 9, 10, 10, 10, 14, 8, 6,
              16, 16, 14, 14, 28, 60, 12, 12, 12, 26, 40]

    def bridge_sheet(title, rows, extra=None):
        sheet = book.create_sheet(title)
        header(sheet, titles + ([extra] if extra else []),
               widths + ([34] if extra else []))
        verdict = DataValidation(
            type='list', allow_blank=True,
            formula1='"ФАНТОМ,РЕАЛЬНАЯ ОБРАБОТКА,НЕЯСНО"')
        sheet.add_data_validation(verdict)
        for number, item in enumerate(rows, 1):
            slot, why = item if isinstance(item, tuple) else (item, None)
            sheet.append([
                number, slot['nickname'], _cell(slot['report_day']),
                slot['chain_c_id'], slot['a_id'], slot['b_id'],
                slot['chain_c_id'], slot['position'], slot['mode_name'],
                slot['manual_mode'], slot['spray_width'], slot['duration_s'],
                _ha(slot['raw_area_m2']), _ha(slot['a_raw_m2']),
                _ha(slot['c_raw_m2']), slot['list_spray_usage_ml'],
                slot['litres_per_ha'], 'да' if slot['v4_present'] else 'нет',
                slot['controller_delta_area_m2'],
                slot['application_activity'],
                slot['application_channel_quality'],
                slot['v4_pump_on_share'], slot['category'],
                slot['category_reason'], None, None, None, None, None]
                + ([why] if extra else []))
            row = sheet.max_row
            link(sheet.cell(row=row, column=25), slot['a_id'], 'ОТКРЫТЬ A')
            link(sheet.cell(row=row, column=26), slot['b_id'], 'ОТКРЫТЬ B')
            link(sheet.cell(row=row, column=27), slot['chain_c_id'],
                 'ОТКРЫТЬ C')
            verdict.add(sheet.cell(row=row, column=28))
        sheet.auto_filter.ref = sheet.dimensions

    bridge_sheet('Bridge_B_проверка', slots)
    bridge_sheet('B_для_выборочной_проверки', sample,
                 extra='Почему в выборке')
    unusual = [s for s in slots
               if s['category'] in (B_PARTIAL, B_REAL_WORK,
                                    B_EVIDENCE_CONFLICT, B_ZERO_PROVEN,
                                    B_IS_ITSELF_C)
               or s['bridges_in_chain'] > 1]
    bridge_sheet('Необычные_B', unusual)
    book.save(path)


# ─── CLI ─────────────────────────────────────────────────────────────────────

def build_parser():
    parser = argparse.ArgumentParser(
        description='Read-only study of bridge records B in A-B-C chains.')
    parser.add_argument('--db', dest='db_path', required=True)
    parser.add_argument('--from', dest='date_from', required=True)
    parser.add_argument('--to', dest='date_to', required=True)
    parser.add_argument('--out', dest='out_dir', required=True)
    parser.add_argument('--plan', dest='plan_path',
                        help='locked holdout plan.json: its candidates are '
                             'the reference C set')
    parser.add_argument('--seed', type=int, default=DEFAULT_SEED)
    parser.add_argument('--no-geometry', action='store_true',
                        help='skip V4 frame geometry (faster)')
    parser.add_argument('--capture-manifest', action='store_true',
                        help='also write bridge_b_v4_capture_ids.txt')
    return parser


def plan_candidates(plan_path):
    with io.open(plan_path, encoding='utf-8') as fh:
        document = json.load(fh)
    locked = document.get('locked') or {}
    found = set()
    for entry in locked.get('candidates') or []:
        found.add(int(entry['flight_id'] if isinstance(entry, dict)
                      else entry))
    return found


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        date_from = date.fromisoformat(args.date_from)
        date_to = date.fromisoformat(args.date_to)
    except ValueError as exc:
        print('ERROR: %s' % exc)
        return EXIT_USAGE
    if date_from > date_to:
        print('ERROR: --from is after --to')
        return EXIT_USAGE
    if not os.path.exists(args.db_path):
        # [REASON]: sqlite3.connect создал бы пустой файл и отчитался нулём.
        print('ERROR: database not found at %s - refusing to run.'
              % args.db_path)
        return EXIT_NO_DATABASE

    before = file_sha256(args.db_path)
    con = open_readonly(args.db_path)
    root = store.source_root(os.path.abspath(args.db_path))
    try:
        store.require_tables(con)
        items, by_key = grouped_records(con, date_from, date_to)
        chains = reconstruct_chains(by_key)
        print('DJI AREA BRIDGE B VALIDATION (read-only)')
        print('  period            : %s .. %s' % (date_from, date_to))
        print('  chains A-B-C      : %d' % len(chains))

        reconstructed = {c['c_id'] for c in chains}
        stored = {int(r[0]) for r in con.execute(
            'SELECT flight_id FROM dji_area_calculations WHERE superseded_at '
            'IS NULL AND structural_candidate=1 AND report_start_date '
            'BETWEEN ? AND ?', (date_from.isoformat(), date_to.isoformat()))}
        references = [('stored calculation rows', stored)]
        if args.plan_path:
            references.append(('locked holdout plan',
                               plan_candidates(args.plan_path)))
        for name, reference in references:
            if reference != reconstructed:
                print('STOP: reconstructed C set differs from %s' % name)
                print('  only reconstructed: %s'
                      % sorted(reconstructed - reference)[:20])
                print('  only reference    : %s'
                      % sorted(reference - reconstructed)[:20])
                return EXIT_MISMATCH
            print('  C set == %-24s: yes (%d)' % (name, len(reference)))

        slots = build_profiles(con, root, items, chains,
                               with_geometry=not args.no_geometry)
        signatures = sortie_signatures(by_key)
        control = takeoff_point_control(by_key, ListReader(con, root))
        summary = summarize(chains, slots, signatures, control)
        sample = select_sample(slots, seed=args.seed)
    except (store.StoreError, BridgeError, ValueError) as exc:
        print('ERROR: %s' % exc)
        return EXIT_USAGE
    finally:
        con.close()

    after = file_sha256(args.db_path)
    summary['db_sha256_before'] = before
    summary['db_sha256_after'] = after
    summary['period'] = [date_from.isoformat(), date_to.isoformat()]
    summary['sample_seed'] = args.seed
    summary['sample_size'] = len(sample)
    if before != after:
        print('ERROR: the database changed while it was being read')
        return EXIT_USAGE

    os.makedirs(args.out_dir, exist_ok=True)
    write_csv(os.path.join(args.out_dir, 'bridge_b_profile.csv'),
              PROFILE_COLUMNS, slots)
    write_csv(os.path.join(args.out_dir, 'bridge_b_chains.csv'),
              ('c_id', 'a_id', 'b_ids', 'nickname', 'report_day', 'a_raw_m2',
               'c_raw_m2', 'boundary_gaps_s', 'signature',
               'scalar_source_check', 'same_takeoff_point', 'a_spray_usage',
               'b_spray_usage_sum', 'c_spray_usage', 'c_usage_residual',
               'c_v4_path_m', 'c_v4_pump_on_share'), chains)
    with io.open(os.path.join(args.out_dir, 'bridge_b_summary.json'), 'w',
                 encoding='utf-8') as fh:
        json.dump(summary, fh, ensure_ascii=False, indent=2, sort_keys=True,
                  default=_cell)
    write_xlsx(os.path.join(args.out_dir,
                            'DJI_SEPTEMBER_BRIDGE_B_VERIFICATION.xlsx'),
               summary, chains, slots, sample)
    if args.capture_manifest:
        with io.open(os.path.join(args.out_dir, 'bridge_b_v4_capture_ids.txt'),
                     'w', encoding='utf-8') as fh:
            fh.write('# bridges B with recorded area, no V4 and no LIST spray '
                     'usage\n')
            for flight_id in summary['review_without_any_application_evidence']:
                fh.write('%d\n' % flight_id)

    print('  bridges per chain : %s' % summary['bridges_per_chain'])
    print('  unique bridges    : %d (slots %d)' % (summary['unique_bridges'],
                                                   summary['bridge_slots']))
    print('  bridge RAW total  : %.4f ha' % summary['bridge_raw_total_ha'])
    print('  manual with V4    : %d of %d' % (
        summary['v4_coverage']['manual_with_v4'], summary['manual_bridges']))
    for name in CATEGORIES:
        item = summary['categories'][name]
        print('  %-26s: %3d  %.4f ha' % (name, item['bridges'],
                                         item['raw_ha']))
    print('  confirmed overstatement in B: %.4f ha'
          % summary['B_EVIDENCE_CONFIRMED_OVERSTATEMENT_ha'])
    print('  database sha256 unchanged: yes')
    print('  out               : %s' % args.out_dir)
    return EXIT_OK


if __name__ == '__main__':
    sys.exit(main())
