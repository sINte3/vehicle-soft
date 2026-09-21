# -*- coding: utf-8 -*-
"""dji_area/capture_manifest.py -- кому из свежих вылетов нужен адресный V4.

DJI-AREA-PRODUCTIONIZATION-001. Ежедневный сбор источников по всему парку --
около 270 посещений кабинета и 50 минут. Нужен он немногим: кандидатам
замороженного структурного экрана (без V4 их нельзя ни исправить, ни
оправдать) и малой контрольной выборке обычных записей.

Модуль ТОЛЬКО читает базу и к DJI не обращается. Flask здесь нет: тот же код
зовут и endpoint `/drones/api/area_capture_manifest`, и тесты без приложения.

ЧТО ВХОДИТ В МАНИФЕСТ

* **Все** кандидаты экрана, у которых V4 ещё нет. Кандидата считает
  `dji_area.structural` на тех же группах, что и конвейер; своей копии правила
  здесь нет.
* Контрольная выборка. [REASON]: замороженное правило знает один класс
  аномалии. Запись `701707553` оно пропустило, а контрольная V4-выборка
  сентябрьского holdout её поймала. Без постоянного контроля новый класс
  останется невидимым навсегда -- правило ради recall НЕ расширяется.

ЧЕГО В МАНИФЕСТЕ НЕТ

* Записи с уже захваченным V4: повторный захват даст те же байты.
* Записи, у которых DJI сам не хранит V4 (`NO_V4_URL_AT_SOURCE`). Это
  отдельное ВИДИМОЕ состояние, а не повод ходить в кабинет каждый день.

КАНДИДАТ ВАЖНЕЕ КОНТРОЛЯ. Предел числа идентификаторов режет только контроль;
кандидат из-за предела не теряется никогда, и ответ говорит об этом прямо
(`over_cap`).
"""

import hashlib
from collections import defaultdict
from datetime import timedelta

from dji_area import pipeline as pl
from dji_area import structural as st

MANIFEST_VERSION = 'area-capture-manifest-1'

DEFAULT_WINDOW_DAYS = 3
MAX_WINDOW_DAYS = 7
DEFAULT_MAX_IDS = 50
HARD_MAX_IDS = 400

REASON_CANDIDATE = 'STRUCTURAL_CANDIDATE'
REASON_CONTROL = 'CONTROL'

CONTROL_RANDOM = 'RANDOM'
CONTROL_RISK_EQUAL_SHORT = 'RISK_EQUAL_RECENT_SHORT'
CONTROL_RISK_EQUAL = 'RISK_EQUAL_RECENT'
CONTROL_RISK_AUTO_NO_WIDTH = 'RISK_AUTO_NO_WIDTH'
CONTROL_RISK_SHORT = 'RISK_SHORT_HIGH_RATE'
# Порядок -- приоритет внутри дня: сначала то, что ближе всего к сигнатуре
# известного пропуска правила.
RISK_PRIORITY = (CONTROL_RISK_EQUAL_SHORT, CONTROL_RISK_EQUAL,
                 CONTROL_RISK_AUTO_NO_WIDTH, CONTROL_RISK_SHORT)

V4_PRESENT = 'V4_PRESENT'
V4_NEEDED = 'V4_NEEDED'
V4_ABSENT_AT_SOURCE = 'NO_V4_AT_SOURCE'
_NO_V4_AT_SOURCE_REASON = 'NO_V4_URL_AT_SOURCE'

# [REASON]: это параметры ОТБОРА КОНТРОЛЯ, а не правило выявления. Они ничего
# не классифицируют и ни одной площади не меняют -- только решают, каким
# обычным записям достанется V4 для проверки. Значения взяты из
# пред-регистрации сентябрьского holdout, где именно эти признаки поймали
# пропуск правила: короткая запись с большой площадью и RAW, повторяющий одну
# из трёх предыдущих записей борта.
RISK_SHORT_MAX_DURATION_S = 120
RISK_SHORT_MIN_RATE_M2_S = 49.0
RISK_EQUAL_MAX_LAG = 3

CONTROLS_RISK_PER_DAY = 3
CONTROLS_RANDOM_PER_DAY = 6


class ManifestError(ValueError):
    pass


def resolve_window(today, date_from=None, date_to=None,
                   window_days=DEFAULT_WINDOW_DAYS):
    """(date_from, date_to) в отчётных днях UTC+5, с ограничением длины.

    [REASON]: три дня по умолчанию, а не один. Цепочка может пересечь полночь,
    доказательство приходит с задержкой, и завтрашний прогон обязан лечить
    вчерашнюю запись. Верхний предел нужен, чтобы ответ не рос вместе с базой:
    манифест на год -- это выгрузка хранилища, а не список на сегодня.
    """
    if date_to is None:
        date_to = today
    if date_from is None:
        date_from = date_to - timedelta(days=int(window_days) - 1)
    if date_from > date_to:
        raise ManifestError('date_from is after date_to')
    if (date_to - date_from).days + 1 > MAX_WINDOW_DAYS:
        raise ManifestError('the window is %d day(s), the cap is %d'
                            % ((date_to - date_from).days + 1,
                               MAX_WINDOW_DAYS))
    return date_from, date_to


def v4_state(item):
    if item.get('v4_revision_id'):
        return V4_PRESENT
    if item.get('v4_absent_reason') == _NO_V4_AT_SOURCE_REASON:
        return V4_ABSENT_AT_SOURCE
    return V4_NEEDED


def _evidence(item):
    return {'list': bool(item.get('list_revision_id')),
            'card': bool(item.get('card_revision_id')),
            'route': bool(item.get('route_revision_id')),
            'v4': bool(item.get('v4_revision_id'))}


def _score(salt, day, flight_id):
    """Детерминированный «жребий» записи на её отчётный день.

    [REASON]: не `random`. Завтрашний прогон обязан выбрать на ВЧЕРАШНИЙ день
    тот же контроль, что и вчера: иначе каждый запуск добирал бы новую порцию
    записей того же дня, и захват полз бы без конца.
    """
    text = '%s|%s|%d' % (salt, day.isoformat(), int(flight_id))
    return hashlib.sha256(text.encode('ascii')).hexdigest()


def _risk_kind(record, previous):
    """Вид риска обычной записи либо None. Только то, что видно в списке."""
    raw = record.get('raw_area_m2') or 0.0
    start, end = record.get('start_ts'), record.get('end_ts')
    duration = (end - start) if start is not None and end is not None else None
    short = (duration is not None and 0 < duration <= RISK_SHORT_MAX_DURATION_S
             and raw / duration >= RISK_SHORT_MIN_RATE_M2_S)
    equal = bool(raw) and any(prev.get('raw_area_m2') == raw
                              for prev in previous[-RISK_EQUAL_MAX_LAG:])
    if equal and short:
        return CONTROL_RISK_EQUAL_SHORT
    if equal:
        return CONTROL_RISK_EQUAL
    if record.get('mode_name') == st.TARGET_MODE \
            and not st._width_present(record):
        return CONTROL_RISK_AUTO_NO_WIDTH
    if short:
        return CONTROL_RISK_SHORT
    return None


def _pick_controls(pool, salt, risk_per_day, random_per_day):
    """Контроль одного дня: сначала риск, затем случайные с РАЗНЫХ бортов."""
    day = pool[0]['item']['report_day']
    for entry in pool:
        entry['score'] = _score(salt, day, entry['item']['flight_id'])
    chosen = []
    taken = set()

    risky = sorted((e for e in pool if e['risk']),
                   key=lambda e: (RISK_PRIORITY.index(e['risk']), e['score']))
    for entry in risky[:risk_per_day]:
        chosen.append((entry, entry['risk']))
        taken.add(entry['item']['flight_id'])

    # [REASON]: по одной записи с борта, по кругу. Иначе контроль целиком
    # достался бы машине, которая налетала больше всех, а остальные не
    # проверялись бы никогда.
    by_drone = defaultdict(list)
    for entry in sorted(pool, key=lambda e: e['score']):
        if entry['item']['flight_id'] not in taken:
            by_drone[entry['item']['chronology_key']].append(entry)
    order = sorted(by_drone, key=lambda key: by_drone[key][0]['score'])
    picked = 0
    while picked < random_per_day and any(by_drone[key] for key in order):
        for key in order:
            if picked >= random_per_day:
                break
            if by_drone[key]:
                chosen.append((by_drone[key].pop(0), CONTROL_RANDOM))
                picked += 1
    return chosen


def build_manifest(con, date_from, date_to, max_ids=DEFAULT_MAX_IDS,
                   salt=MANIFEST_VERSION,
                   risk_per_day=CONTROLS_RISK_PER_DAY,
                   random_per_day=CONTROLS_RANDOM_PER_DAY):
    """Манифест адресного захвата за отчётные дни [date_from, date_to].

    ``con`` -- соединение с базой (достаточно прав на чтение). Возвращает
    словарь с обычными типами, готовый к `jsonify`.
    """
    max_ids = max(0, min(int(max_ids), HARD_MAX_IDS))
    items = pl.load_flights(con, date_from, date_to)
    by_key = defaultdict(list)
    for item in items:
        if item['chronology_key']:
            by_key[item['chronology_key']].append(item)

    nicknames = _nicknames(con, [i['flight_id'] for i in items
                                 if i['in_period']])
    candidates = []
    pools = defaultdict(list)
    for key, records in by_key.items():
        records.sort(key=lambda r: (r['start_ts'] or 0, r['flight_id']))
        screens = st.screen_all(records)
        for index, (record, screen) in enumerate(zip(records, screens)):
            if not record['in_period']:
                continue
            if screen['candidate']:
                candidates.append((record, screen))
            elif (record.get('raw_area_m2') or 0) > 0:
                # Контроль берётся из ВСЕЙ популяции дня, а фильтр «V4 уже
                # есть» применяется ПОСЛЕ выбора: так набор дня не ползёт.
                pools[record['report_day']].append({
                    'item': record,
                    'risk': _risk_kind(record, records[:index])})

    def entry(record, reason, screen=None, control_kind=None):
        out = {
            'flight_id': record['flight_id'],
            'reason': reason,
            'report_day': record['report_day'].isoformat(),
            'chronology_key': record['chronology_key'],
            'drone': nicknames.get(record['flight_id']),
            'v4_state': v4_state(record),
            'evidence': _evidence(record),
        }
        if screen is not None:
            out['base_flight_id'] = screen['base_flight_id']
            out['bridge_flight_ids'] = list(screen['bridge_flight_ids'])
        if control_kind is not None:
            out['control_kind'] = control_kind
        return out

    candidate_entries = [entry(r, REASON_CANDIDATE, screen=s)
                         for r, s in sorted(candidates,
                                            key=lambda p: p[0]['flight_id'])]
    control_entries = []
    for day in sorted(pools):
        for picked, kind in _pick_controls(pools[day], salt, risk_per_day,
                                           random_per_day):
            control_entries.append(entry(picked['item'], REASON_CONTROL,
                                         control_kind=kind))

    need_candidates = [e for e in candidate_entries
                       if e['v4_state'] == V4_NEEDED]
    need_controls = [e for e in control_entries if e['v4_state'] == V4_NEEDED]
    room = max(0, max_ids - len(need_candidates))
    sent_controls = need_controls[:room]
    capture = need_candidates + sent_controls

    return {
        'manifest_version': MANIFEST_VERSION,
        'structural_rule_version': st.STRUCTURAL_RULE_VERSION,
        'date_from': date_from.isoformat(),
        'date_to': date_to.isoformat(),
        'max_ids': max_ids,
        # Кандидатов больше предела: все они в ответе, контроль не поместился.
        'over_cap': len(need_candidates) > max_ids,
        'counts': {
            'flights_in_window': sum(1 for i in items if i['in_period']),
            'candidates_total': len(candidate_entries),
            'candidates_need_capture': len(need_candidates),
            'candidates_with_v4': sum(1 for e in candidate_entries
                                      if e['v4_state'] == V4_PRESENT),
            'candidates_no_v4_at_source': sum(
                1 for e in candidate_entries
                if e['v4_state'] == V4_ABSENT_AT_SOURCE),
            'controls_selected': len(control_entries),
            'controls_need_capture': len(need_controls),
            'controls_dropped_by_cap': len(need_controls) - len(sent_controls),
            'capture_total': len(capture),
        },
        'capture': capture,
        # Видимое состояние, а не список на посещение: DJI сам не хранит V4.
        'no_v4_at_source': [e for e in candidate_entries
                            if e['v4_state'] == V4_ABSENT_AT_SOURCE],
    }


def _nicknames(con, flight_ids):
    out = {}
    ids = sorted(set(flight_ids))
    for start in range(0, len(ids), 500):
        chunk = ids[start:start + 500]
        for row in con.execute(
                'SELECT dji_flight_id, nickname_raw FROM drone_flights WHERE '
                'dji_flight_id IN (%s)' % ','.join('?' * len(chunk)), chunk):
            out[int(row[0])] = row[1]
    return out
