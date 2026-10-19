# -*- coding: utf-8 -*-
"""Метод «время» -- часы с включённым зажиганием по точкам. Только stdlib.

Решение владельца 09.10.2026: моточас -- время с включённым зажиганием по
трекеру. Сборщик кладёт рядом с точкой сырое значение датчика зажигания
(`ign`, GPS-24); здесь -- правило, по которому из этих значений получаются
часы.

ПРАВИЛО R -- ПРЕДРЕГИСТРИРОВАНО (трек GPS, §0.6, 19.10.2026)
Для каждой пары соседних точек a -> b, Δ = b.t − a.t:
  * `ign` у a нет (None)            -- Δ в «неизвестно»;
  * `ign` у a равен 0               -- «выключено»;
  * `ign` у a не 0 и Δ <= MAX_GAP_S -- «включено»;
  * `ign` у a не 0 и Δ >  MAX_GAP_S -- «неизвестно».
Пара считается, только если обе точки внутри промежутка [lo, hi].

Числа предрегистрации не подгоняются: правило, не прошедшее проверку против
счётчика Wialon, меняется новой предрегистрацией, а не правкой константы.
"""

import collections

# [REASON]: шаг записи трекеров на фикстурах репозитория -- у работающей
# машины до 30 с, редко 60 с, и в движении, и на стоянке с работающим
# двигателем; паузы в минуты и часы бывают только на стоянке (сон трекера).
# Пара длиннее пяти минут при включённом зажигании -- дыра в данных, а не
# часы: она идёт в «неизвестно», и вердикт без неё не выносится.
MAX_GAP_S = 300

Split = collections.namedtuple(
    "Split", "on_s off_s unknown_s pairs points with_ign")


def split(points, lo, hi, max_gap=MAX_GAP_S):
    """Seconds on / off / unknown over the pairs inside [lo, hi].

    `points` are (t, ign) in ascending t, as storage.read_ignition gives
    them; points outside [lo, hi] are allowed and skipped.
    """
    inside = [(int(t), ign) for t, ign in points if lo <= t <= hi]
    on = off = unknown = 0
    for (t_a, ign_a), (t_b, _ign_b) in zip(inside, inside[1:]):
        delta = t_b - t_a
        if delta <= 0:
            continue
        if ign_a is None:
            unknown += delta
        elif ign_a == 0:
            off += delta
        elif delta <= max_gap:
            on += delta
        else:
            unknown += delta
    return Split(on_s=on, off_s=off, unknown_s=unknown,
                 pairs=max(len(inside) - 1, 0), points=len(inside),
                 with_ign=sum(1 for _t, ign in inside if ign is not None))
