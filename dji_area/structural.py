# -*- coding: utf-8 -*-
"""dji_area/structural.py -- замороженный структурный экран retained-state.

Диагностика, а НЕ универсальный классификатор дублей. Правило заморожено
исследованием (B1.5, версия ``structural-retained-screen-frozen-1``) и
расширять его ради recall запрещено: gap 2 с, 31 с, 78 с, 110 с -- известные
false-negative пути, они проверяются через V4, а не через искажение правила.

Правило, дословно:

1. один и тот же борт (``hardware_id``), записи в хронологическом порядке
   (по ``start_ts``), с граничными днями -- полночь цепочку не рвёт;
2. target -- запись ``mode_name == 4`` с отсутствующей шириной;
3. обратный проход по цепочке записей, у которых зазор
   ``start(next) - end(prev)`` лежит в ``[0, 1]`` с;
4. база -- ближайшая предыдущая запись ``mode_name == 4`` с ПРИСУТСТВУЮЩЕЙ
   шириной; между базой и target обязан быть хотя бы один промежуточный
   record (bridge);
5. база выбирается ДО чтения площади target (area-blind); только после
   выбора выполняется ``scalar_source_check`` -- точное равенство
   ``raw_area`` target и базы.

Кандидат означает «возможный источник retained-скаляра», не «нулевая
работа»: retained-запись может нести небольшой настоящий прирост (13
августовских случаев, 193.336 м²). Решение о площади принимает только
проверенный интервал счётчика (``resolver.py``).

Здесь нет ввода-вывода, Flask и базы.
"""

from dji_area import STRUCTURAL_RULE_VERSION

TARGET_MODE = 4
MAX_GAP_S = 1.0
MIN_GAP_S = 0.0


def _width_present(record):
    width = record.get('spray_width')
    if width is None or isinstance(width, bool):
        return False
    try:
        return float(width) > 0
    except (TypeError, ValueError):
        return False


def _gap(prev, nxt):
    """Зазор между концом prev и началом nxt, с. None если время неизвестно."""
    a = prev.get('end_ts')
    b = nxt.get('start_ts')
    if a is None or b is None:
        return None
    return float(b) - float(a)


def not_applicable(reason):
    return {
        'applicable': False,
        'candidate': False,
        'reason': reason,
        'base_flight_id': None,
        'bridge_flight_ids': [],
        'boundary_gaps_s': [],
        'scalar_source_check': None,
        'rule_version': STRUCTURAL_RULE_VERSION,
    }


def screen(records, target_index):
    """Экран для ``records[target_index]``.

    ``records`` -- записи ОДНОГО борта, отсортированные по ``start_ts``:
    словари с ``flight_id``, ``start_ts``, ``end_ts`` (unix-секунды),
    ``mode_name``, ``spray_width``, ``raw_area_m2``.

    Возвращает словарь: ``applicable``, ``candidate``, ``reason``,
    ``base_flight_id``, ``bridge_flight_ids`` (от базы к target, без базы и
    target), ``boundary_gaps_s`` (зазоры между соседями от базы к target),
    ``scalar_source_check`` (True/False; None когда кандидата нет).
    """
    target = records[target_index]
    if target.get('mode_name') != TARGET_MODE:
        return not_applicable('TARGET_NOT_MODE4')
    if _width_present(target):
        return not_applicable('TARGET_WIDTH_PRESENT')

    # Обратный проход: цепочка записей с зазором 0..1 с.
    chain = []          # записи от ближайшей к дальней
    gaps = []           # зазор между chain[i] и следующей к target
    i = target_index
    while i - 1 >= 0:
        prev = records[i - 1]
        gap = _gap(prev, records[i])
        if gap is None or gap < MIN_GAP_S or gap > MAX_GAP_S:
            break
        chain.append(prev)
        gaps.append(gap)
        i -= 1

    base = None
    base_pos = None
    for pos, rec in enumerate(chain):
        if rec.get('mode_name') == TARGET_MODE and _width_present(rec):
            base = rec
            base_pos = pos
            break

    result = {
        'applicable': True,
        'candidate': False,
        'reason': None,
        'base_flight_id': None,
        'bridge_flight_ids': [],
        'boundary_gaps_s': [],
        'scalar_source_check': None,
        'rule_version': STRUCTURAL_RULE_VERSION,
    }
    if base is None:
        result['reason'] = ('NO_CHAIN' if not chain
                            else 'NO_WIDTH_PRESENT_MODE4_BASE_IN_CHAIN')
        return result
    if base_pos == 0:
        # База непосредственно перед target: bridge отсутствует.
        result['reason'] = 'NO_BRIDGE_RECORD'
        result['base_flight_id'] = base.get('flight_id')
        return result

    bridges = list(reversed(chain[:base_pos]))
    boundary_gaps = list(reversed(gaps[:base_pos + 1]))
    result['candidate'] = True
    result['reason'] = 'CANDIDATE'
    result['base_flight_id'] = base.get('flight_id')
    result['bridge_flight_ids'] = [b.get('flight_id') for b in bridges]
    result['boundary_gaps_s'] = [round(g, 3) for g in boundary_gaps]

    # Только ПОСЛЕ выбора базы -- сравнение скаляров.
    target_raw = target.get('raw_area_m2')
    base_raw = base.get('raw_area_m2')
    if target_raw is None or base_raw is None:
        result['scalar_source_check'] = None
    else:
        result['scalar_source_check'] = (float(target_raw) == float(base_raw))
    return result


def screen_all(records):
    """Экран для каждой записи борта. ``records`` отсортированы по start_ts."""
    return [screen(records, i) for i in range(len(records))]


# ─── Пересечение интервалов ──────────────────────────────────────────────────

def overlap_groups(records):
    """Группы записей одного борта с пересекающимися интервалами.

    Пересечение -- строго ``start(next) < end(prev)``; касание (gap 0) --
    не пересечение (цепочка resume у frozen screen имеет gap 0). Возвращает
    {flight_id: group_key} только для записей в конфликте.
    """
    ordered = sorted(
        (r for r in records if r.get('start_ts') is not None),
        key=lambda r: (float(r['start_ts']), str(r.get('flight_id'))))
    groups = {}
    current = []
    current_end = None
    for rec in ordered:
        start = float(rec['start_ts'])
        end = rec.get('end_ts')
        end = float(end) if end is not None else start
        if current and current_end is not None and start < current_end:
            current.append(rec)
            current_end = max(current_end, end)
        else:
            if len(current) > 1:
                key = 'overlap:%s' % current[0].get('flight_id')
                for member in current:
                    groups[member.get('flight_id')] = key
            current = [rec]
            current_end = end
    if len(current) > 1:
        key = 'overlap:%s' % current[0].get('flight_id')
        for member in current:
            groups[member.get('flight_id')] = key
    return groups
