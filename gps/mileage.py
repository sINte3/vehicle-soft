# -*- coding: utf-8 -*-
"""Пробег по точкам -- правило P. Только stdlib.

Решение владельца 19.10.2026 по грузовикам: «Работала или нет техника,
проверяем по пробегу, это не спецтехника»; порог -- 3 км (решение 09.10).

ПРАВИЛО P -- ПРЕДРЕГИСТРИРОВАНО (трек GPS, §0.6, «Дополнение к
предрегистрации», 19.10.2026)
Сумма расстояний по дуге большого круга между соседними точками a -> b,
обе внутри промежутка [lo, hi]. Пара не считается, если:
  * скорость, которую сообщил трекер, у обеих точек равна 0 -- машина стоит;
  * расчётная скорость пары больше MAX_SPEED_KMH -- скачок координат.
Пауза между точками пару не отменяет: прямая -- нижняя оценка пути.
"""

import collections
import math

EARTH_RADIUS_KM = 6371.0

# [REASON]: выше любой скорости грузовика, ниже скачков координат. Число
# предрегистрации -- меняется только новой записью в треке.
MAX_SPEED_KMH = 150.0

Mileage = collections.namedtuple("Mileage", "km pairs counted standing jumps")


def distance_km(lon1, lat1, lon2, lat2):
    """Расстояние по дуге большого круга, км."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = phi2 - phi1
    dlmb = math.radians(lon2 - lon1)
    h = (math.sin(dphi / 2) ** 2
         + math.cos(phi1) * math.cos(phi2) * math.sin(dlmb / 2) ** 2)
    return 2 * EARTH_RADIUS_KM * math.asin(min(1.0, math.sqrt(h)))


def mileage(points, lo, hi, max_speed=MAX_SPEED_KMH):
    """Пробег по правилу P над парами внутри [lo, hi].

    `points` -- (t, lon, lat, speed) по возрастанию t; точки вне промежутка
    допустимы и пропускаются. Скорость None считается нулём: неизвестная
    скорость не делает стоящую машину едущей.
    """
    inside = [p for p in points if lo <= p[0] <= hi]
    km = 0.0
    counted = standing = jumps = 0
    for (t_a, lon_a, lat_a, s_a), (t_b, lon_b, lat_b, s_b) in zip(inside,
                                                                inside[1:]):
        if not (s_a or 0) and not (s_b or 0):
            # [REASON]: дрожание координат стоящего трекера -- метры на
            # каждую точку, за сутки это километры: ровно порог «работала».
            standing += 1
            continue
        step = distance_km(lon_a, lat_a, lon_b, lat_b)
        seconds = t_b - t_a
        if seconds <= 0 or step / seconds * 3600.0 > max_speed:
            jumps += 1
            continue
        km += step
        counted += 1
    return Mileage(km=km, pairs=max(len(inside) - 1, 0), counted=counted,
                   standing=standing, jumps=jumps)
