# -*- coding: utf-8 -*-
"""Машина agro-work -> наша строка техники. Только stdlib, ничего не пишет.

ПРАВИЛО ОДНО: госномер после свёртки `plate_norm.normalize_plate` совпал с
госномером РОВНО ОДНОЙ нашей машины. Это один и тот же номер в двух местах, а
не догадка. Всё остальное -- решение человека:

  manual     -- связка владельца (ответ на вопрос 7, 28.09). Всегда
                побеждает автоматику и никогда ею не перезаписывается;
  auto       -- номер совпал ровно с одной нашей машиной;
  ambiguous  -- номер совпал с несколькими нашими машинами, либо на одну
                нашу машину пришлись две машины agro-work;
  none       -- совпадения нет.

[REASON]: номер без кода региона -- ПОДСКАЗКА в CSV, а не связка. В реестре
agro-work код региона есть всегда (B0), в нашем справочнике -- не везде, и
«609 EA» у нас может оказаться и «80 609 EA», и «25 609 EA»: кластер работает
в двух областях. Угадать область значит однажды отдать трек одной машины
другой -- и сверка молча посчитает чужую работу.
"""

from collections import defaultdict

from plate_norm import normalize_plate

MANUAL = 'manual'
AUTO = 'auto'
AMBIGUOUS = 'ambiguous'
NONE = 'none'

MAX_CANDIDATES = 5


def _suffix_keys(norm):
    """Номер без двух ведущих цифр кода региона, если они есть."""
    if len(norm) > 4 and norm[:2].isdigit():
        return norm[2:]
    return None


def resolve(transports, equipment, manual):
    """transport_id -> {'equipment_id', 'status', 'candidates'}.

    transports -- итерируемое словарей с `id` и `plate_norm`;
    equipment  -- итерируемое словарей с `id` и `plate`;
    manual     -- {agro transport id: equipment id}, живые связки владельца.
    """
    by_norm = defaultdict(list)
    by_suffix = defaultdict(list)
    for row in equipment:
        key = normalize_plate(row.get('plate'))
        if not key:
            continue
        by_norm[key].append(row['id'])
        suffix = _suffix_keys(key)
        if suffix:
            by_suffix[suffix].append(row['id'])

    out = {}
    for row in transports:
        transport_id = row['id']
        norm = row.get('plate_norm') or ''
        exact = list(by_norm.get(norm, [])) if norm else []
        if transport_id in manual:
            out[transport_id] = {'equipment_id': manual[transport_id],
                                 'status': MANUAL, 'candidates': exact}
            continue
        if len(exact) == 1:
            out[transport_id] = {'equipment_id': exact[0], 'status': AUTO,
                                 'candidates': exact}
            continue
        if len(exact) > 1:
            out[transport_id] = {'equipment_id': None, 'status': AMBIGUOUS,
                                 'candidates': exact[:MAX_CANDIDATES]}
            continue
        hints = []
        suffix = _suffix_keys(norm) if norm else None
        if suffix:
            hints.extend(by_norm.get(suffix, []))
            hints.extend(by_suffix.get(suffix, []))
        if norm:
            hints.extend(by_suffix.get(norm, []))
        seen, ordered = set(), []
        for equipment_id in hints:
            if equipment_id not in seen:
                seen.add(equipment_id)
                ordered.append(equipment_id)
        out[transport_id] = {'equipment_id': None, 'status': NONE,
                             'candidates': ordered[:MAX_CANDIDATES]}

    # [REASON]: одна наша машина -- не больше одной машины agro-work. Две
    # машины их реестра на одном нашем треке значат, что одна связка неверна,
    # и сверка в обратную сторону отдала бы работу не той заявке. Связку
    # владельца это не отменяет -- его слово остаётся, но автоматика на эту
    # машину не садится.
    claimed = defaultdict(list)
    for transport_id, item in out.items():
        if item['equipment_id'] is not None:
            claimed[item['equipment_id']].append(transport_id)
    for equipment_id, owners in claimed.items():
        if len(owners) < 2:
            continue
        for transport_id in owners:
            item = out[transport_id]
            if item['status'] == AUTO:
                item['status'] = AMBIGUOUS
                item['candidates'] = [equipment_id]
                item['equipment_id'] = None
    return out
