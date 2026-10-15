# -*- coding: utf-8 -*-
"""Правило территорий подразделений: участок на дворе -- не гектары.

ЗАЧЕМ
Трактор, который весь день работает на дворе хлопкозавода, пункта приёма или
заправки, оставляет в треке «участки» -- и ночной расчёт считает их
гектарами. Владелец (09.10 и 14.10): работа на территории подразделения --
не гектары поля. Правило и условие его включения записаны в дорожной карте
до замера (§2.14, «Правило территорий -- что записано до прогона GPS-19»);
замер -- GPS-19 `tools/gps_territory_report.py`.

ПРАВИЛО (§2.14, п. 2)
Для каждого куска суточной фигуры, который расчёт и так считает участком
(не меньше MIN_WORK_AREA_HA):
  - доля куска на территории >= DROP_SHARE (по куску ДО вычитания) --
    кусок не гектары целиком;
  - иначе из куска вычитается территория, и из остатков участками остаются
    те, что не меньше MIN_WORK_AREA_HA (как в A7: мелкое -- проезд).
Территория -- объединение зон списка `gps/data/territory_zones.csv` с
контуром из зеркала `field_contours`; зона больше LARGE_ZONE_HA в правило не
входит (п. 1), выключенная строка зеркала -- не контур (как у расчёта).

ВКЛЮЧЕНИЕ (§2.14, п. 4 и 6)
Выключено, пока TERRITORY_FROM -- None. Включает владелец; в выпуске с
включением сюда записывается дата: правило действует только на сутки с
датой не раньше неё. Ночной `--catch-up` пересчитывает сутки окна текущим
движком, и без этой даты правило задело бы прошлые сутки окна.

Результат с правилом несёт в `method_version` суффикс
TERRITORY_RULE_VERSION -- как правила края: сутки без правила и с ним
различимы и через год.

Только чтение базы: зоны берутся из `field_contours`, ничего не пишется.
"""

import csv
import json
import os
from datetime import datetime

import shapely
from shapely.geometry import shape as shapely_shape

# TERRITORY_RULE_VERSION -- метка результата с правилом (§2.14, п. 6) --
# живёт в gps.area рядом с метками правил края: её ставит work_sites.
from gps.area import (MIN_WORK_AREA_HA, TERRITORY_RULE_VERSION,  # noqa: F401
                      repair_polygon, to_utm)

# [REASON]: §2.14, п. 4 -- правило действует только на сутки с датой не
# раньше дня выпуска, а дата записывается в код вместе с включением. None --
# выключено: ни один день не считается с правилом. Включает владелец. Дата --
# строка YYYY-MM-DD, равная дню выпуска: раньше -- сутки между ней и выпуском
# опубликуются без правила, а любой проход `--catch-up` потом пересчитает
# их с правилом и поменяет гектары, которые владелец уже видел.
TERRITORY_FROM = None

# [REASON]: §2.14, п. 1 -- двор завода или пункта -- это единицы гектаров;
# зона в десятки гектаров накрывает поля, и правило её не берёт. Тот же
# порог, что у замера GPS-19: правило должно делать ровно то, что мерили.
LARGE_ZONE_HA = 50.0

# [REASON]: §2.14, п. 2 -- участок, лежащий на территории наполовину и
# больше (доля по участку до вычитания), не гектары целиком.
DROP_SHARE = 0.5
# [REASON]: доля сравнивается с порогом округлённой до трёх знаков -- так её
# сравнивал замер GPS-19 (столбец `dolya`), по которому владелец решал о
# включении. Участок, разрезанный краем зоны ровно пополам, не решается
# шумом плавающей точки (0,49999999 против 0,5).
SHARE_DIGITS = 3

# [REASON]: §2.14, п. 5 -- список зон меняется только по ответу владельца, и
# после этого GPS-19 прогоняется снова. Зеркало же обновляет контуры само
# (`tools/gps_mirror_contours.py` переписывает геометрию изменившихся зон),
# поэтому в списке движка у каждой зоны записана площадь контура, по
# которому её мерил GPS-19. Контур, который с тех пор стал другим (больше
# допуска), или зона, которую GPS-19 не мерил (без площади), в правило не
# входят, пока список не пересобран после нового замера.
PIN_TOLERANCE_HA = 0.01

SOURCE = 'wialon'
ZONES_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data',
                          'territory_zones.csv')


class BadZoneList(ValueError):
    """Список зон не читается -- правило без списка не применяется."""


def _date(value):
    return datetime.strptime(value, '%Y-%m-%d').date()


def rule_applies(day, start=None):
    """Действует ли правило на сутки `day` (YYYY-MM-DD).

    `start` -- дата включения; None -- взять TERRITORY_FROM.

    [REASON]: даты разбираются, а не сравниваются строками: «2026-10-5»
    против «2026-10-16» как строка больше, и сутки до выпуска получили бы
    правило. Неверная дата -- ValueError, а не тихое «выключено».
    """
    start = TERRITORY_FROM if start is None else start
    if not start:
        return False
    return _date(day) >= _date(start)


def read_zone_list(path=None):
    """{id зоны Wialon: площадь контура при замере, га, или None} из списка.

    Столбцы `zona_id` и `ploshchad_ga` (CSV через запятую); пустая площадь --
    зону GPS-19 не мерил (контура не было). `path` None -- список движка
    ZONES_PATH (читается при вызове, а не при импорте: тест и инструмент
    могут подставить свой).

    [REASON]: список -- решение владельца. Повтор id, нечисло или пустой
    список -- ошибка в списке, и молча её не чинят: правило с неверным
    списком снимало бы гектары не там.
    """
    path = path or ZONES_PATH
    if not os.path.isfile(path):
        raise BadZoneList('territory zone list not found: %s' % path)
    zones = {}
    try:
        with open(path, encoding='utf-8-sig', newline='') as handle:
            reader = csv.DictReader(handle)
            for column in ('zona_id', 'ploshchad_ga'):
                if column not in (reader.fieldnames or []):
                    raise BadZoneList('%s has no %s column' % (path, column))
            for line, row in enumerate(reader, 2):
                value = (row.get('zona_id') or '').strip()
                if not value.isdigit() or int(value) <= 0:
                    raise BadZoneList('%s line %d: zona_id is not a positive '
                                      'number' % (path, line))
                if int(value) in zones:
                    raise BadZoneList('%s line %d: zona_id %s is listed twice'
                                      % (path, line, value))
                area = (row.get('ploshchad_ga') or '').strip()
                try:
                    pinned = float(area) if area else None
                except ValueError:
                    raise BadZoneList('%s line %d: ploshchad_ga is not a number'
                                      % (path, line))
                if pinned is not None and not pinned > 0:
                    raise BadZoneList('%s line %d: ploshchad_ga must be above 0'
                                      % (path, line))
                zones[int(value)] = pinned
    except (UnicodeDecodeError, csv.Error) as problem:
        raise BadZoneList('%s is not a readable CSV (%s)'
                          % (path, problem.__class__.__name__))
    if not zones:
        raise BadZoneList('%s lists no zones' % path)
    return zones


def zone_polygon(text):
    """Контур зоны из GeoJSON зеркала в UTM 41N, починенный; None -- не годится.

    [REASON]: та же обработка, что у контуров расчёта (`load_contours`) и у
    замера GPS-19: внешнее кольцо, проекция `to_utm`, `repair_polygon`.
    Зоны Wialon рисуют мышкой, самопересечение там обычное дело, и GEOS на
    таком контуре падает вместо пустого пересечения.
    """
    try:
        polygon = shapely_shape(json.loads(text))
    except (ValueError, TypeError, AttributeError, KeyError):
        return None
    if polygon.is_empty or polygon.geom_type != 'Polygon':
        return None
    xs, ys = to_utm(*polygon.exterior.coords.xy)
    fixed = repair_polygon(shapely.Polygon(zip(xs, ys)))
    if fixed is None or fixed.area <= 0:
        return None
    return fixed


class Territory:
    """Объединение зон правила и сам разрез куска.

    `zones` -- {zona_id: многоугольник UTM} зон, вошедших в правило;
    `left_out` -- {zona_id: причина} зон списка, которые в правило не вошли
    (нет в зеркале, нет пригодного контура, больше LARGE_ZONE_HA).
    """

    def __init__(self, zones, left_out=None):
        if not zones:
            raise ValueError('a territory needs at least one zone')
        self.zones = dict(zones)
        self.left_out = dict(left_out or {})
        self.union = shapely.union_all(list(self.zones.values()))
        shapely.prepare(self.union)

    def cut(self, piece, min_area_ha=MIN_WORK_AREA_HA):
        """(доля куска на территории, [куски, что остаются участками]).

        Доля считается по куску до вычитания. Доля >= DROP_SHARE -- кусок
        уходит целиком; иначе территория вычитается, и остаются куски не
        меньше `min_area_ha`. Кусок, не задевший территории, возвращается
        как есть -- тем же объектом, без пересборки: его площадь --
        измерение.
        """
        if piece is None or piece.is_empty or piece.area <= 0:
            return 0.0, []
        if not shapely.intersects(self.union, piece):
            return 0.0, [piece]
        on = piece.intersection(self.union).area
        if on <= 0:
            return 0.0, [piece]
        share = round(on / piece.area, SHARE_DIGITS)
        if share >= DROP_SHARE:
            return share, []
        left = piece.difference(self.union)
        kept = [part for part in polygon_parts(left)
                if part.area / 10000.0 >= min_area_ha]
        return share, kept


def polygon_parts(geometry):
    """Многоугольные куски геометрии; линии и точки площади не несут."""
    if geometry is None or geometry.is_empty:
        return []
    if geometry.geom_type == 'Polygon':
        return [geometry]
    parts = []
    for part in getattr(geometry, 'geoms', []):
        parts.extend(polygon_parts(part))
    return parts


def load_territory(con, zones, log=None):
    """Territory по зонам списка из `field_contours`; None -- ни одной зоны.

    `zones` -- {id зоны: площадь при замере или None}, как read_zone_list.
    Строка зеркала берётся, как у расчёта и у GPS-19: `source = 'wialon'`,
    `external_id` -- id зоны, выключенная строка (`is_active = 0`) -- не
    контур. В правило не входят и называются в журнале: зона без контура,
    зона больше LARGE_ZONE_HA, зона без площади замера и зона, чей контур с
    замера изменился больше чем на PIN_TOLERANCE_HA.

    [REASON]: None, а не пустая территория: правило, включённое при пустом
    зеркале, не сняло бы ничего, а сутки несли бы метку правила. Вызывающий
    тогда считает сутки без правила и честно без метки, а журнал говорит
    почему.
    """
    pins = {int(zone): area for zone, area in dict(zones).items()}
    zone_ids = list(pins)
    if not zone_ids:
        return None
    marks = ','.join('?' * len(zone_ids))
    rows = {}
    for external, geometry in con.execute(
            'SELECT external_id, geometry_geojson FROM field_contours '
            'WHERE source = ? AND (is_active IS NULL OR is_active = 1) '
            'AND external_id IN (%s) ORDER BY id' % marks,
            [SOURCE] + [str(zone) for zone in zone_ids]):
        rows[str(external)] = geometry
    kept, left_out = {}, {}
    for zone in zone_ids:
        geometry = rows.get(str(zone))
        polygon = zone_polygon(geometry) if geometry else None
        if polygon is None:
            left_out[zone] = 'no outline'
        elif polygon.area / 10000.0 > LARGE_ZONE_HA:
            left_out[zone] = 'larger than %g ha' % LARGE_ZONE_HA
        elif pins[zone] is None:
            left_out[zone] = 'not measured by GPS-19'
        elif abs(polygon.area / 10000.0 - pins[zone]) > PIN_TOLERANCE_HA:
            left_out[zone] = ('outline changed since GPS-19: %.4f ha, measured '
                              '%.4f ha' % (polygon.area / 10000.0, pins[zone]))
        else:
            kept[zone] = polygon
    if log and left_out:
        log('  territory: %d zone(s) left out of the rule: %s'
            % (len(left_out), ', '.join('%s (%s)' % (zone, why)
                                        for zone, why in sorted(left_out.items()))))
    if not kept:
        if log:
            log('  territory: no listed zone has a usable outline -- the rule '
                'is NOT applied')
        return None
    return Territory(kept, left_out)
