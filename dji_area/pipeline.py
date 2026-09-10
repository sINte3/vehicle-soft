# -*- coding: utf-8 -*-
"""dji_area/pipeline.py -- пересчёт учёта площади DJI за период.

Один проход: записи периода (плюс граничные дни для хронологии борта) ->
сводки V4 (кэш в `dji_v4_summaries`) -> структурный экран и пересечения по
борту -> качество канала применения по борту и месяцу -> резолвер площади
-> резолвер поля -> append-only запись. Без `--apply` ничего не пишется, но
сводка считается полностью -- по статусам, доступности V4, baseline, tiers,
нерешённым записям, пересечениям, применению без площади.

Идемпотентность: тот же вход даёт тот же `calculation_input_hash`, и строка
остаётся `unchanged`; иной вход добавляет строку и закрывает прежнюю.

Stdlib sqlite3 и чистые модули; Flask здесь нет.
"""

import json
from collections import defaultdict
from datetime import datetime, timedelta

from dji_area import (AREA_ALGORITHM_VERSION, FIELD_RESOLVER_VERSION,
                      REPORT_TIMEZONE, REPORT_UTC_OFFSET_HOURS)
from dji_area import evidence as ev
from dji_area import field as fld
from dji_area import resolver as rs
from dji_area import store
from dji_area import structural as st
from dji_area import v4 as v4mod
from dji_area.hashing import calculation_input_hash, field_input_hash

HW_SOURCE_UNIT_NICKNAME = 'unit_nickname'
BOUNDARY_DAYS = 1

# Откуда взяты скаляры списка (площадь, ширина, режим, границы интервала).
# Различие несёт доказательную силу и обязано входить в отпечаток входа.
LIST_FROM_REVISION = 'revision'
LIST_FROM_MUTABLE_RAW_JSON = 'drone_flights_raw_json'
# Ревизия списка названа, но ни одного значения из неё не пришло: тело не
# прочиталось или не разобралось. Не молчаливый ноль и не «источник надёжен».
LIST_REVISION_UNPARSED = 'revision_named_but_unparsed'


class PipelineError(RuntimeError):
    pass


# ─── Загрузка ────────────────────────────────────────────────────────────────

def _parse_dt(value):
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    text = str(value)
    for fmt in ('%Y-%m-%d %H:%M:%S.%f', '%Y-%m-%d %H:%M:%S', '%Y-%m-%dT%H:%M:%S.%f',
                '%Y-%m-%dT%H:%M:%S', '%Y-%m-%d'):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def _epoch(dt):
    if dt is None:
        return None
    return int((dt - datetime(1970, 1, 1)).total_seconds())


def report_day_of(dt):
    return (dt + timedelta(hours=REPORT_UTC_OFFSET_HOURS)).date()


def load_flights(con, date_from, date_to, flight_ids=None):
    """Записи периода [date_from, date_to] по дню начала (UTC+5) с граничными
    днями. `flight_ids` ограничивает набор ЦЕЛЕВЫХ записей; соседи по борту
    всё равно загружаются."""
    lo = date_from - timedelta(days=BOUNDARY_DAYS)
    hi = date_to + timedelta(days=BOUNDARY_DAYS)
    # started_at хранится в UTC; день отчёта -- +5 часов.
    lo_utc = datetime.combine(lo, datetime.min.time()) - timedelta(
        hours=REPORT_UTC_OFFSET_HOURS)
    hi_utc = datetime.combine(hi + timedelta(days=1), datetime.min.time()) \
        - timedelta(hours=REPORT_UTC_OFFSET_HOURS)
    rows = con.execute(
        'SELECT f.id AS drone_flight_id, f.dji_flight_id AS flight_id, '
        'f.started_at, f.finished_at, f.raw_json, f.drone_unit_id, '
        'f.nickname_raw, u.hardware_id AS unit_hardware_id, '
        'e.hardware_id AS ev_hardware_id, e.hardware_id_source, '
        'e.list_revision_id, e.card_revision_id, e.route_revision_id, '
        'e.v4_revision_id, e.card_raw_area_m2, e.route_area_m2, '
        'e.card_geometry_md5, e.card_manual_mode, e.route_identity_status, '
        'e.v4_identity_status, e.v4_absent_reason, e.card_start_ts, '
        'e.card_end_ts, e.list_raw_area_m2, e.list_start_ts, e.list_end_ts, '
        'e.list_mode_name, e.list_manual_mode, e.list_spray_width '
        'FROM drone_flights f '
        'LEFT JOIN drone_units u ON u.id = f.drone_unit_id '
        'LEFT JOIN dji_flight_evidence e ON e.flight_id = f.dji_flight_id '
        'WHERE f.started_at >= ? AND f.started_at < ? '
        'ORDER BY f.started_at, f.dji_flight_id',
        (lo_utc.strftime('%Y-%m-%d %H:%M:%S'),
         hi_utc.strftime('%Y-%m-%d %H:%M:%S'))).fetchall()
    wanted = set(int(x) for x in flight_ids) if flight_ids else None
    out = []
    for r in rows:
        started = _parse_dt(r['started_at'])
        if started is None:
            continue
        day = report_day_of(started)
        # [REASON]: смысл всей модели -- считать от НЕИЗМЕНЯЕМОГО источника.
        # `drone_flights.raw_json` -- изменяемая колонка приложения: её
        # переписывает любой повторный приём списка, и она не покрыта ни одним
        # SHA. Значения списка, разобранные из захешированной ревизии, уже
        # лежат в `dji_flight_evidence` (store.build_evidence_row), поэтому
        # берём их оттуда. Разбор raw_json остаётся ТОЛЬКО как объявленный
        # запасной путь для записей без list-ревизии, и он помечается.
        # [REASON]: наличия `list_revision_id` НЕДОСТАТОЧНО. store.py:297-303
        # ставит его ДО разбора и оставляет, когда тело ревизии не читается
        # (StoreError на пропавшем файле или несовпавшем SHA) или не
        # разбирается (ValueError): скаляры при этом остаются NULL. Ветка «по
        # одному только id» пометила бы такую строку как посчитанную от
        # захешированного источника, хотя от него не пришло ни одного
        # значения, и NULL молча стал бы отсутствующей площадью.
        have_revision_values = bool(r['list_revision_id']) and any(
            r[col] is not None for col in
            ('list_raw_area_m2', 'list_start_ts', 'list_end_ts',
             'list_mode_name', 'list_manual_mode', 'list_spray_width'))
        if have_revision_values:
            lst = {'mode_name': r['list_mode_name'],
                   'manual_mode': r['list_manual_mode'],
                   'spray_width': r['list_spray_width'],
                   'raw_area_m2': r['list_raw_area_m2'],
                   'start_ts': r['list_start_ts'],
                   'end_ts': r['list_end_ts']}
            list_value_source = LIST_FROM_REVISION
        elif r['list_revision_id']:
            # Ревизия названа, но значений из неё нет. Это НЕ «нет ревизии» и
            # НЕ «есть ревизия»: третье состояние, и оно обязано отличаться в
            # отпечатке, иначе деградированная строка останется навсегда.
            lst = None
            list_value_source = LIST_REVISION_UNPARSED
        else:
            try:
                raw = json.loads(r['raw_json']) if r['raw_json'] else {}
            except ValueError:
                raw = {}
            try:
                lst = ev.parse_list_record(raw) if raw else None
            except ValueError:
                lst = None
            list_value_source = LIST_FROM_MUTABLE_RAW_JSON if lst else None
        hardware, hw_source = r['ev_hardware_id'], r['hardware_id_source']
        if not hardware and r['unit_hardware_id']:
            # [REASON]: у записей без карточки борт неизвестен из источника;
            # ник -> машина -> паспортный hardware_id даёт хронологию, но
            # помечается как выведенный, а не прочитанный.
            hardware, hw_source = r['unit_hardware_id'], HW_SOURCE_UNIT_NICKNAME
        item = {
            'flight_id': int(r['flight_id']),
            'drone_flight_id': r['drone_flight_id'],
            'started_at': started,
            'finished_at': _parse_dt(r['finished_at']),
            'start_ts': _epoch(started),
            'end_ts': _epoch(_parse_dt(r['finished_at'])),
            'report_day': day,
            'in_period': (date_from <= day <= date_to)
            and (wanted is None or int(r['flight_id']) in wanted),
            'hardware_id': hardware,
            'hardware_id_source': hw_source,
            'mode_name': lst['mode_name'] if lst else None,
            'manual_mode': lst['manual_mode'] if lst else None,
            'spray_width': lst['spray_width'] if lst else None,
            'raw_area_m2': lst['raw_area_m2'] if lst else None,
            'raw_area_source': 'list' if lst and lst['raw_area_m2'] is not None
            else None,
            'list_value_source': list_value_source,
            'list_start_ts': lst['start_ts'] if lst else None,
            'list_end_ts': lst['end_ts'] if lst else None,
            'list_revision_id': r['list_revision_id'],
            'card_revision_id': r['card_revision_id'],
            'route_revision_id': r['route_revision_id'],
            'v4_revision_id': r['v4_revision_id'],
            'card_raw_area_m2': r['card_raw_area_m2'],
            'route_area_m2': r['route_area_m2'],
            'card_geometry_md5': r['card_geometry_md5'],
            'card_manual_mode': r['card_manual_mode'],
            'route_identity_status': r['route_identity_status'],
            'v4_identity_status': r['v4_identity_status'],
            'v4_absent_reason': r['v4_absent_reason'],
        }
        if item['raw_area_m2'] is None and r['card_raw_area_m2'] is not None:
            item['raw_area_m2'] = r['card_raw_area_m2']
            item['raw_area_source'] = 'card'
        if item['start_ts'] is None and lst:
            item['start_ts'] = lst['start_ts']
        if item['end_ts'] is None and lst:
            item['end_ts'] = lst['end_ts']
        out.append(item)
    return out


# ─── V4 ──────────────────────────────────────────────────────────────────────

V4_BODY_UNREADABLE = 'V4_BODY_UNREADABLE'
V4_BODY_UNDECODABLE = 'V4_BODY_UNDECODABLE'


def ensure_v4_summary(con, root, item, apply, cache):
    """Сводка V4 записи: из кэша БД, иначе декодировать ревизию.

    Возвращает (summary_dict, summary_row_id, failure), где ``failure`` --
    None либо причина, по которой имеющаяся ревизия V4 не превратилась в
    сводку.

    [REASON]: «ревизии V4 нет» и «ревизия есть, но её тело сейчас не читается»
    -- разные входы, и раньше они давали одинаковый результат: RAW_UNVERIFIED
    с полным RAW как provisional. Хуже того, отпечаток входа собирался из SHA
    ревизий и потому у обоих совпадал: восстановив файловое хранилище и
    запустив пересчёт снова, оператор получал `unchanged` и НАВСЕГДА оставался
    с деградированной строкой, будучи уверен, что пересчёт прошёл. Причина
    возвращается наверх и входит в отпечаток -- тогда возврат файла меняет
    вход и строка переписывается.
    """
    rev_id = item.get('v4_revision_id')
    if not rev_id:
        return None, None, None
    if rev_id in cache:
        return cache[rev_id]
    row = store.v4_summary_for_revision(con, rev_id)
    if row is not None and row['parser_version'] == v4mod.V4_PARSER_VERSION:
        summary = json.loads(row['summary_json'])
        cache[rev_id] = (summary, row['id'], None)
        return cache[rev_id]
    rev = store.revision_by_id(con, rev_id)
    if rev is None:
        return None, None, V4_BODY_UNREADABLE
    try:
        body = store.read_body(root, rev)
    except (store.StoreError, OSError):
        cache[rev_id] = (None, None, V4_BODY_UNREADABLE)
        return cache[rev_id]
    try:
        decoded = v4mod.decode_v4(body)
    except v4mod.V4DecodeError:
        cache[rev_id] = (None, None, V4_BODY_UNDECODABLE)
        return cache[rev_id]
    start_ts = item.get('list_start_ts') or item.get('start_ts')
    end_ts = item.get('list_end_ts') or item.get('end_ts')
    summary = v4mod.summarize_v4(decoded, start_ts, end_ts)
    identity_ok = item.get('v4_identity_status') != ev.V4_MISMATCH
    wq, baseline, reasons = v4mod.evaluate_window(summary, identity_ok)
    summary_id = None
    if apply:
        _state, summary_id = store.upsert_v4_summary(
            con, item['flight_id'], rev_id, summary, wq, baseline, reasons)
    cache[rev_id] = (summary, summary_id, None)
    return cache[rev_id]


def revision_sha(con, revision_id, cache):
    if not revision_id:
        return None
    if revision_id in cache:
        return cache[revision_id]
    row = con.execute('SELECT sha256 FROM dji_source_revisions WHERE id=?',
                      (revision_id,)).fetchone()
    cache[revision_id] = row['sha256'] if row else None
    return cache[revision_id]


# ─── Пересчёт ────────────────────────────────────────────────────────────────

def _month_key(day):
    return day.strftime('%Y-%m')


def _full_months(date_from, date_to):
    """Полные календарные месяцы, накрывающие [date_from, date_to]."""
    lo = date_from.replace(day=1)
    if date_to.month == 12:
        hi = date_to.replace(year=date_to.year + 1, month=1, day=1)
    else:
        hi = date_to.replace(month=date_to.month + 1, day=1)
    return lo, hi - timedelta(days=1)


def _empty_summary():
    return {
        'flights_in_period': 0,
        'flights_loaded': 0,
        'status_counts': defaultdict(int),
        'evidence_status_counts': defaultdict(int),
        'v4_availability': defaultdict(int),
        'baseline_counts': defaultdict(int),
        'window_quality_counts': defaultdict(int),
        'tier_counts': defaultdict(int),
        'eligibility_counts': defaultdict(int),
        'hardware_source_counts': defaultdict(int),
        'route_identity_counts': defaultdict(int),
        'application_activity_counts': defaultdict(int),
        'unresolved_records': 0,
        'unresolved_raw_exposure_m2': 0.0,
        'overlap_records': 0,
        'application_without_area_records': 0,
        'unreliable_channel_records': 0,
        'structural_candidates': 0,
        'structural_scalar_matches': 0,
        'raw_sum_m2': 0.0,
        'raw_missing_records': 0,
        'certified_sum_m2': 0.0,
        'certified_records': 0,
        'provisional_sum_m2': 0.0,
        'provisional_records': 0,
        'controller_delta_sum_m2': 0.0,
        'calc_writes': defaultdict(int),
        'field_writes': defaultdict(int),
        'v4_summaries_decoded': 0,
        'by_status_raw_m2': defaultdict(float),
        'flights': [],
    }


def recalculate(db_path, date_from, date_to, apply=False, flight_ids=None,
                with_geometric=False, batch_size=500, now=None,
                collect_rows=False, progress=None):
    """Пересчёт периода. Возвращает сводку (словарь с обычными типами)."""
    con = store.connect(db_path)
    root = store.source_root(db_path)
    try:
        store.require_tables(con)
        return _recalculate(con, root, date_from, date_to, apply, flight_ids,
                            with_geometric, batch_size, now, collect_rows,
                            progress)
    finally:
        con.close()


def _recalculate(con, root, date_from, date_to, apply, flight_ids,
                 with_geometric, batch_size, now, collect_rows, progress):
    now = now or store.utcnow()
    summary = _empty_summary()
    items = load_flights(con, date_from, date_to, flight_ids)
    summary['flights_loaded'] = len(items)
    targets = [i for i in items if i['in_period']]
    summary['flights_in_period'] = len(targets)

    # ── Хронология по борту ──────────────────────────────────────────────
    by_hw = defaultdict(list)
    for item in items:
        if item['hardware_id']:
            by_hw[item['hardware_id']].append(item)
    for hw, records in by_hw.items():
        records.sort(key=lambda r: (r['start_ts'] or 0, r['flight_id']))
    structural_by_flight = {}
    overlap_by_flight = {}
    neighbours_by_flight = {}
    for hw, records in by_hw.items():
        screens = st.screen_all(records)
        groups = st.overlap_groups(records)
        for idx, rec in enumerate(records):
            structural_by_flight[rec['flight_id']] = screens[idx]
            overlap_by_flight[rec['flight_id']] = groups.get(rec['flight_id'])
            # Соседи, влияющие на решение: цепочка экрана и группа пересечения.
            neigh = set()
            scr = screens[idx]
            if scr.get('base_flight_id'):
                neigh.add(scr['base_flight_id'])
            neigh.update(scr.get('bridge_flight_ids') or [])
            group = groups.get(rec['flight_id'])
            if group:
                neigh.update(f for f, g in groups.items() if g == group)
            neigh.discard(rec['flight_id'])
            neighbours_by_flight[rec['flight_id']] = sorted(neigh)

    # ── Сводки V4 и качество канала по (борт, месяц) ─────────────────────
    if apply:
        store.begin_immediate(con)
    v4_cache = {}
    sha_cache = {}
    channel_evidence = defaultdict(bool)
    v4_failures = {}
    v4_by_flight = {}
    # [REASON]: ревизия способности канала названа `app-channel-hw-month-1` --
    # ключ (борт, МЕСЯЦ). Пока свидетельство собиралось только по загруженным
    # записям, оно зависело от дат в командной строке: пересчёт одного дня не
    # видел вылета того же борта 20-го числа, и запись, у которой месячный
    # прогон дал NOT_OBSERVED / application_without_area=False, получала
    # UNKNOWN / NULL, вытесняя верную строку. Чередование дневного и месячного
    # прогонов качало одну и ту же запись бесконечно. Свидетельство считается
    # по ПОЛНЫМ календарным месяцам, которых касается набор: результат зависит
    # от данных, а не от аргументов.
    channel_items = items
    ch_from, ch_to = _full_months(date_from, date_to)
    if (ch_from, ch_to) != (date_from, date_to):
        channel_items = load_flights(con, ch_from, ch_to)
    # Граничные дни соседних месяцев загружаются вместе с месяцем, но своим
    # месяцам свидетельства не дают: их месяц загружен НЕ целиком.
    months_wanted = set()
    probe = ch_from
    while probe <= ch_to:
        months_wanted.add(_month_key(probe))
        probe = (probe.replace(day=28) + timedelta(days=4)).replace(day=1)
    for n, item in enumerate(channel_items):
        summ, summ_id, failure = ensure_v4_summary(con, root, item, apply,
                                                   v4_cache)
        v4_by_flight[item['flight_id']] = (summ, summ_id)
        v4_failures[item['flight_id']] = failure
        if summ is not None:
            summary['v4_summaries_decoded'] += 1
            month = _month_key(item['report_day'])
            # Файл с чужой идентичностью не свидетельствует и о канале борта:
            # он вообще не о нём.
            if ((summ.get('application_frames') or 0) > 0
                    and item['hardware_id'] and month in months_wanted
                    and item.get('v4_identity_status') != ev.V4_MISMATCH):
                channel_evidence[(item['hardware_id'], month)] = True
        if progress and n % 200 == 0:
            progress('v4 %d/%d' % (n, len(channel_items)))

    catalog = store.SqliteCatalog(con)
    catalog_sha = catalog.catalog_state_sha()
    polygons = catalog.current_polygons() if with_geometric else None

    pending = 0
    for n, item in enumerate(targets):
        fid = item['flight_id']
        summ, summ_id = v4_by_flight.get(fid, (None, None))
        hw = item['hardware_id']
        month = _month_key(item['report_day'])
        channel = rs.channel_quality_for(hw, channel_evidence.get((hw, month),
                                                                 False))
        structural = structural_by_flight.get(fid) or {}
        overlap_group = overlap_by_flight.get(fid)
        overlap = {'group_id': overlap_group, 'conflict': bool(overlap_group)}
        route = {'identity_status': item.get('route_identity_status')}
        v4_evidence = None
        if summ is not None:
            v4_evidence = {'summary': summ,
                           'identity_ok': item.get('v4_identity_status')
                           != ev.V4_MISMATCH}
        decision = rs.resolve_area({
            'raw_area_m2': item['raw_area_m2'],
            'raw_area_source': item['raw_area_source'],
            'v4': v4_evidence, 'route': route, 'structural': structural,
            'overlap': overlap, 'channel_quality': channel,
            'hardware_id': hw,
        })
        if item.get('v4_identity_status') in (ev.V4_UNKNOWN,
                                               ev.V4_DIRECTORY_ONLY) \
                and summ is not None:
            decision.anomaly_flags.append('V4_IDENTITY_' + item['v4_identity_status'])
        v4_failure = v4_failures.get(fid)
        if v4_failure:
            decision.anomaly_flags.append(v4_failure)
        if item['hardware_id_source'] == HW_SOURCE_UNIT_NICKNAME:
            decision.anomaly_flags.append('HARDWARE_FROM_NICKNAME')
        if item.get('list_value_source') == LIST_FROM_MUTABLE_RAW_JSON:
            decision.anomaly_flags.append('LIST_FROM_MUTABLE_RAW_JSON')
        elif item.get('list_value_source') == LIST_REVISION_UNPARSED:
            decision.anomaly_flags.append('LIST_REVISION_UNPARSED')
        if item['v4_absent_reason'] == 'NO_V4_URL_AT_SOURCE' and summ is None:
            decision.anomaly_flags.append('NO_V4_AT_SOURCE')

        neighbours = [(nid, revision_sha(con, _neighbour_revision(items, nid),
                                         sha_cache))
                      for nid in neighbours_by_flight.get(fid, [])]
        input_hash = calculation_input_hash(
            {'list': revision_sha(con, item['list_revision_id'], sha_cache)
             or _list_fallback_sha(item),
             'card': revision_sha(con, item['card_revision_id'], sha_cache),
             'route': revision_sha(con, item['route_revision_id'], sha_cache),
             'v4': revision_sha(con, item['v4_revision_id'], sha_cache)},
            neighbours, channel_evidence.get((hw, month), False),
            extra={'hardware_id': hw, 'hardware_id_source':
                   item['hardware_id_source'],
                   'route_identity': item.get('route_identity_status'),
                   'v4_identity': item.get('v4_identity_status'),
                   # [REASON]: различить ревизию и raw_json отпечаток умел и
                   # без этого поля -- у них разный `sources['list']`. Поле
                   # нужно для ТРЕТЬЕГО состояния: ревизия названа, но её
                   # значения не пришли. Там `sources['list']` совпадает со
                   # здоровым случаем, и без явной пометки деградированная
                   # строка и здоровая дали бы один отпечаток, а пересчёт
                   # после починки тела ответил бы `unchanged`.
                   'list_value_source': item.get('list_value_source'),
                   # [REASON]: без этого «тело V4 не прочиталось» и «тела V4
                   # нет» дают ОДИН отпечаток, и после возврата файлового
                   # хранилища пересчёт отвечает `unchanged`, оставляя
                   # деградированную строку навсегда.
                   'v4_failure': v4_failure})

        calc_row = _calc_row(item, decision, input_hash, summ_id, now)

        # ── Поле ─────────────────────────────────────────────────────────
        route_points = None
        route_ok = item.get('route_identity_status') == ev.ROUTE_OK
        if with_geometric and route_ok and item['route_revision_id']:
            rev = store.revision_by_id(con, item['route_revision_id'])
            if rev is not None:
                try:
                    parsed = ev.parse_route_body(store.read_body(root, rev), fid)
                    route_points = parsed.get('points')
                except store.StoreError:
                    route_points = None
        field = fld.resolve_field(
            item.get('card_geometry_md5'), catalog, flight_id=fid,
            manual_mode=(item.get('card_manual_mode')
                         if item.get('card_manual_mode') is not None
                         else item.get('manual_mode')),
            route_points=route_points, route_identity_ok=route_ok,
            current_polygons=polygons)
        field_hash = field_input_hash(
            item.get('card_geometry_md5'), catalog_sha,
            route_sha=revision_sha(con, item['route_revision_id'], sha_cache)
            if with_geometric else None,
            lineage_keys=field.get('field_lineage_evidence_ids'))
        field_row = _field_row(fid, field, field_hash)

        if apply:
            state, _cid = store.insert_calculation(con, calc_row, now)
            summary['calc_writes'][state] += 1
            fstate, _fid = store.insert_field_attribution(con, field_row, now)
            summary['field_writes'][fstate] += 1
            pending += 1
            if pending >= batch_size:
                con.execute('COMMIT')
                store.begin_immediate(con)
                pending = 0
        else:
            existing = store.current_calculation(con, fid)
            summary['calc_writes'][
                'unchanged' if existing is not None
                and existing['calculation_input_hash'] == input_hash
                else 'would_write'] += 1

        _accumulate(summary, item, decision, field, calc_row)
        if collect_rows:
            summary['flights'].append(_flight_line(item, decision, field))
        if progress and n % 500 == 0:
            progress('resolve %d/%d' % (n, len(targets)))
    if apply:
        con.execute('COMMIT')
    return _plain(summary)


def _neighbour_revision(items, flight_id):
    for item in items:
        if item['flight_id'] == flight_id:
            return item.get('card_revision_id') or item.get('list_revision_id')
    return None


def _list_fallback_sha(item):
    """Без list-ревизии вход описывает сама запись list (raw_json)."""
    payload = {k: item.get(k) for k in ('flight_id', 'raw_area_m2',
                                        'start_ts', 'end_ts', 'mode_name',
                                        'spray_width', 'manual_mode')}
    return 'list-record:' + ev.sha256_bytes(ev.canonical_record_bytes(payload))


def _calc_row(item, decision, input_hash, summary_id, now):
    return {
        'flight_id': item['flight_id'],
        'provider_account_id': ev.PROVIDER_ACCOUNT_DEFAULT,
        'drone_flight_id': item['drone_flight_id'],
        'hardware_id': item['hardware_id'],
        'hardware_id_source': item['hardware_id_source'],
        'area_algorithm_version': AREA_ALGORITHM_VERSION,
        'calculation_input_hash': input_hash,
        'start_at_utc': store.iso(item['started_at']),
        'end_at_utc': store.iso(item['finished_at']),
        'report_timezone': REPORT_TIMEZONE,
        'report_start_date': item['report_day'].isoformat(),
        'raw_area_m2': decision.raw_area_m2,
        'raw_area_source': decision.raw_area_source,
        'card_area_m2': item.get('card_raw_area_m2'),
        'route_area_m2': item.get('route_area_m2'),
        'counter_observed_delta_m2': decision.counter_observed_delta_m2,
        'counter_zero_default_delta_m2': decision.counter_zero_default_delta_m2,
        'controller_delta_area_m2': decision.controller_delta_area_m2,
        'counter_baseline_status': decision.counter_baseline_status,
        'counter_window_quality': decision.counter_window_quality,
        'window_reasons_json': json.dumps(decision.window_reasons),
        'corrected_recorded_area_m2': decision.corrected_recorded_area_m2,
        'area_status': decision.area_status,
        'evidence_status': decision.evidence_status,
        'area_method': decision.area_method,
        'area_confidence': decision.area_confidence,
        'anomaly_flags_json': json.dumps(decision.anomaly_flags),
        'application_activity': decision.application_activity,
        'application_channel_quality': decision.application_channel_quality,
        'application_evidence_kind': decision.application_evidence_kind,
        'application_without_area': decision.application_without_area,
        'structural_candidate': decision.structural_candidate,
        'structural_rule_version': decision.structural_rule_version,
        'candidate_base_flight_id': decision.candidate_base_flight_id,
        'bridge_flight_ids_json': json.dumps(decision.bridge_flight_ids),
        'boundary_gaps_json': json.dumps(decision.boundary_gaps_s),
        'scalar_source_check': decision.scalar_source_check,
        'overlap_group_id': decision.overlap_group_id,
        'aggregation_eligibility': decision.aggregation_eligibility,
        'v4_summary_id': summary_id,
        'list_revision_id': item.get('list_revision_id'),
        'card_revision_id': item.get('card_revision_id'),
        'route_revision_id': item.get('route_revision_id'),
        'v4_revision_id': item.get('v4_revision_id'),
        'unique_coverage_estimate_m2': None,
        'coverage_domain_id': None,
        'coverage_method_version': None,
        'customer_id': None,
        'customer_mapping_id': None,
        'customer_mapping_version': None,
        'billable_area_m2': None,
        'billing_policy_version': None,
        'billing_approval_id': None,
    }


def _field_row(flight_id, field, field_hash):
    return {
        'flight_id': flight_id,
        'field_resolver_version': FIELD_RESOLVER_VERSION,
        'field_input_hash': field_hash,
        'geometry_key_raw': field.get('geometry_key_raw'),
        'geometry_key_format': field.get('geometry_key_format'),
        'geometry_md5': field.get('geometry_md5'),
        'linked_land_uuid': field.get('linked_land_uuid'),
        'geometry_holder_land_uuid': field.get('geometry_holder_land_uuid'),
        'geometry_object_id': field.get('geometry_object_id'),
        'historical_geometry_available': field.get(
            'historical_geometry_available'),
        'historical_geometry_sha256': field.get('historical_geometry_sha256'),
        'field_attribution_tier': field.get('field_attribution_tier'),
        'field_attribution_method': field.get('field_attribution_method'),
        'field_confidence': field.get('field_confidence'),
        'field_land_uuid': field.get('field_land_uuid'),
        'field_name_at_snapshot': field.get('field_name_at_snapshot'),
        'field_serial_number': field.get('field_serial_number'),
        'land_snapshot_id': field.get('land_snapshot_id'),
        'land_revision_id': field.get('land_revision_id'),
        'field_lineage_evidence_json': json.dumps(
            field.get('field_lineage_evidence_ids') or []),
        'holder_count': field.get('holder_count'),
        'candidate_count': field.get('candidate_count'),
        'warnings_json': json.dumps(field.get('warnings') or []),
        'tier4_inside_share': field.get('tier4_inside_share'),
        'tier4_heuristic_version': field.get('tier4_heuristic_version'),
    }


def _accumulate(summary, item, decision, field, calc_row):
    s = summary
    s['status_counts'][decision.area_status] += 1
    s['evidence_status_counts'][decision.evidence_status or 'NONE'] += 1
    s['v4_availability']['present' if item.get('v4_revision_id') else
                         (item.get('v4_absent_reason') or 'absent')] += 1
    s['baseline_counts'][decision.counter_baseline_status or 'NONE'] += 1
    s['window_quality_counts'][decision.counter_window_quality or 'NONE'] += 1
    s['tier_counts'][field.get('field_attribution_tier')] += 1
    s['eligibility_counts'][decision.aggregation_eligibility] += 1
    s['hardware_source_counts'][item.get('hardware_id_source') or 'NONE'] += 1
    s['route_identity_counts'][item.get('route_identity_status') or 'ABSENT'] += 1
    s['application_activity_counts'][decision.application_activity] += 1
    raw = decision.raw_area_m2
    if raw is None:
        s['raw_missing_records'] += 1
    else:
        s['raw_sum_m2'] += raw
        s['by_status_raw_m2'][decision.area_status] += raw
    elig = decision.aggregation_eligibility
    corrected = decision.corrected_recorded_area_m2
    if elig == rs.AGG_CERTIFIED and corrected is not None:
        s['certified_sum_m2'] += corrected
        s['certified_records'] += 1
        if decision.controller_delta_area_m2 is not None:
            s['controller_delta_sum_m2'] += decision.controller_delta_area_m2
    elif elig == rs.AGG_PROVISIONAL and corrected is not None:
        s['provisional_sum_m2'] += corrected
        s['provisional_records'] += 1
    elif elig == rs.AGG_EXCLUDED_OVERLAP:
        s['overlap_records'] += 1
    else:
        s['unresolved_records'] += 1
        if raw is not None:
            s['unresolved_raw_exposure_m2'] += raw
    if decision.application_without_area is True:
        s['application_without_area_records'] += 1
    if decision.application_channel_quality == rs.CH_UNRELIABLE:
        s['unreliable_channel_records'] += 1
    if decision.structural_candidate:
        s['structural_candidates'] += 1
        if decision.scalar_source_check:
            s['structural_scalar_matches'] += 1


def _flight_line(item, decision, field):
    return {
        'flight_id': item['flight_id'],
        'report_day': item['report_day'].isoformat(),
        'hardware_id': item['hardware_id'],
        'raw_area_m2': decision.raw_area_m2,
        'corrected_recorded_area_m2': decision.corrected_recorded_area_m2,
        'controller_delta_area_m2': decision.controller_delta_area_m2,
        'counter_observed_delta_m2': decision.counter_observed_delta_m2,
        'counter_zero_default_delta_m2': decision.counter_zero_default_delta_m2,
        'area_status': decision.area_status,
        'evidence_status': decision.evidence_status,
        'area_method': decision.area_method,
        'area_confidence': decision.area_confidence,
        'counter_baseline_status': decision.counter_baseline_status,
        'counter_window_quality': decision.counter_window_quality,
        'anomaly_flags': list(decision.anomaly_flags),
        'application_activity': decision.application_activity,
        'application_evidence_kind': decision.application_evidence_kind,
        'application_without_area': decision.application_without_area,
        'structural_candidate': decision.structural_candidate,
        'candidate_base_flight_id': decision.candidate_base_flight_id,
        'scalar_source_check': decision.scalar_source_check,
        'overlap_group_id': decision.overlap_group_id,
        'aggregation_eligibility': decision.aggregation_eligibility,
        'field_attribution_tier': field.get('field_attribution_tier'),
        'field_attribution_method': field.get('field_attribution_method'),
        'field_name_at_snapshot': field.get('field_name_at_snapshot'),
    }


def _plain(value):
    if isinstance(value, defaultdict):
        return {str(k): _plain(v) for k, v in sorted(value.items(),
                                                     key=lambda kv: str(kv[0]))}
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_plain(v) for v in value]
    return value
