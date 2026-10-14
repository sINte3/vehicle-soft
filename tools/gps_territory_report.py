# -*- coding: utf-8 -*-
"""GPS-19 -- участки опубликованных суток на территориях подразделений. Только чтение.

ЗАЧЕМ
Владелец 14.10: тракторы весь день ездят по территориям подразделений --
хлопкозаводы, ПТМ, МТП, АЗС, -- и «участок на территории подразделения --
не гектары». Территории -- группы геозон ресурса Wialon 285: группа 16
«Агрокластер» (45 зон) и группа 3 «АЗСлар» (29 АЗС), список --
`docs/gps_territory_zones_2026-10-14.csv` (`zona_id,gruppa_id,tip`). Правило
записано до замера (дорожная карта, §2.14, «Правило территорий»): вычесть
территорию, а участок, который лежит на ней наполовину и больше, не считать
гектарами целиком; зона больше 50 га в правило не входит. Включать его или
нет, решают условия, записанные там же, по замерам с боевой базы. Инструмент
эти замеры делает: два крайних варианта по отдельности (гибрид выводится из
их столбцов) и всё, что нужно условиям; владелец запускает его на сервере и
присылает три CSV и журнал консоли. Сутки, которые
владелец назвал «трактор весь день на территории», сессия сверяет по CSV
суток: в нём и участки вне территорий, без них доля суток не видна.

ЧТО ДЕЛАЕТ
1. Территории: по каждой зоне списка -- контур из зеркала `field_contours`
   (`source='wialon'`, `external_id` -- id зоны, строка не выключена -- как
   у расчёта, `load_contours`). Контур читается и чинится той же функцией,
   что у расчёта (`gps.daily._utm_polygon_from_geojson`: UTM 41N,
   `repair_polygon`) -- зоны рисуют мышкой, и «бабочка» без починки роняет
   GEOS. Зона без пригодного контура (нет в зеркале, выключена, круг или
   линия) считается и называется по id. Зона больше 50 га в объединение
   территорий не входит (§2.14, п. 1): в CSV зон она с `v_pravile = net`,
   её участки не мерятся, в консоли она названа.
2. Участки: строки `gps_work_polygons` опубликованных суток окна
   (`gps_daily_aggregates.reason IS NULL`). Участок, чья рамка не задевает
   ни одной зоны (пространственный индекс STRtree), только считается.
3. По участку на территории (пересечение с объединением зон больше нуля):
     ga_na_territorii      площадь участка ∩ объединение зон, га;
     dolya                 она же, делённая на площадь САМОГО МНОГОУГОЛЬНИКА
                           участка (не на `area_ha` строки), 3 знака;
     ga_posle_vychitaniya  вариант А «вычесть»: участок минус объединение,
                           куски меньше `gps.area.MIN_WORK_AREA_HA` (0,3 га)
                           отброшены, остальное сложено;
     ga_posle_otbrosa      вариант Б «отбросить по доле»: 0 при dolya >= 0,5,
                           иначе `area_ha` строки;
     zony, tipy            до 5 задетых зон, от большего перекрытия к
                           меньшему, и их типы (группы -- в CSV зон);
     kontur_territoriya    da -- `contour_id` строки ведёт на зону списка;
   и объект: исключён ли (`gps.exclusion.excluded_units`), машина, госномер
   и категория по первой непомеченной (`skip` пуст или 0) строке
   сопоставления с машиной, ответ оператора.
4. Пишет три CSV (UTF-8 с BOM, разделитель `;`):
     --out        только участки на территории; по убыванию
                  ga_na_territorii, затем дата, объект, номер участка;
     --zones-out  строка на зону списка: есть ли контур, площадь зоны,
                  сколько участков её задевает, их гектары на ЭТОЙ зоне,
                  сколько объектов и дат; по убыванию ga_na_zone;
     --days-out   строка на каждые опубликованные машино-сутки окна -- и
                  без перекрытия, и без участков (тогда всё по нулям):
                  участков, ga_vsego (сумма `area_ha`), ga_na_territorii,
                  dolya_dnya (на территории / сумма площадей
                  многоугольников; 0, если перекрытия нет) и суммы суток
                  по обоим вариантам -- участок без перекрытия входит в
                  них своим `area_ha` целиком; по дате и объекту.
   Участок, контур которого не читается или на котором упал GEOS, в
   сутках не мерен: в обоих вариантах он остаётся своим `area_ha`, а в
   знаменатель dolya_dnya не входит (их число -- в консоли).
5. В консоль (ASCII, без имён) -- окно, сколько суток и участков
   просмотрено, участки на территории по корзинам доли, итоги трёх чисел
   (на территории, снимет А, снимет Б) -- всего, по исключённости, по
   категории машины, по ответу оператора, -- зоны без контура и самые
   большие зоны: «территория» в десятки гектаров может накрыть настоящее
   поле, и это должно быть видно до правила.

ДВА ЧИСЛА ГЕКТАРОВ -- НЕ ОДНО
`area_ha` -- площадь, которую записал расчёт; площадь многоугольника из
`polygon_geojson` -- та же величина, пересчитанная из контура, округлённого
до 6 знаков (~0,1 м). Доля и вариант А меряются по многоугольнику (вариант
А -- геометрия), вариант Б снимает `area_ha` целиком. Консоль печатает обе
суммы по участкам на территории: расхождение больше округления значит, что
контур прочитан не так, как его строил расчёт.

[REASON]: дыры в участке сохраняются. Участок -- объединение треугольников
Делоне, и в нём бывают дыры (необработанное пятно внутри поля). Расчётная
`_utm_polygon_from_geojson` берёт только внешний контур -- для зон это
верно, для участка нет: зона, лежащая в дыре, оказалась бы «на участке».

ЗОНЫ ПЕРЕКРЫВАЮТСЯ
Гектары участка на территории считаются по объединению зон -- место, где
две зоны лежат одна на другой, не считается дважды, и ga_na_territorii не
больше площади участка. Строка CSV зон меряет каждую зону саму по себе:
участок на месте перекрытия попадёт в обе строки, и сумма столбца
ga_na_zone может быть больше итога по территории.

ЧЕГО НЕ ДЕЛАЕТ
  НИЧЕГО НЕ ПИШЕТ В БАЗУ. `transport.db` открывается `file:...?mode=ro`:
      запись отвергает SQLite, а не обещание инструмента. Отсутствующая база
      не создаётся -- отказ с кодом 2. Оговорка та же, что у прогонов края:
      SQLite, открывая базу в режиме WAL на чтение, может создать рядом
      пустые служебные `-wal` и `-shm`; данные не меняются.
  Не решает, что такое территория и какое правило верное. Список зон --
      решение владельца, правило -- следующий шаг после замеров.
  Не трогает расчёт: ни `gps.daily`, ни `gps.area` не меняются, гектары на
      экране остаются прежними.
  Не печатает имён в консоль: имена машин -- только в CSV.

КОДЫ ВЫХОДА
  0 -- готово, все три CSV записаны;
  3 -- записано, но на части участков геометрия упала (GEOS); они названы
       в консоли номерами, в CSV участков не попали, а в CSV суток
       остались своим `area_ha` неизмеренными;
  2 -- неверный ввод, ничего не считалось и не записано: нет базы (она не
       создаётся), в базе нет нужной таблицы или столбца, кривой список зон
       (нет файла или столбца, id не число или повторён, список пуст),
       неверная дата, в пути к базе есть `#`, `?` или `%` (SQLite прочёл
       бы его как адрес и открыл бы другой файл на запись),
       --out / --zones-out / --days-out указывает на базу,
       её -wal/-shm, список зон, другой из трёх отчётов или любой файл
       SQLite, папки нет или файл не открывается на запись (открыт в
       Excel);
  1 -- инструмент упал (необработанное исключение, в том числе нарушенный
       инвариант: гектары на территории или после вычитания больше площади
       участка) -- CSV не записаны.

Запуск -- окружением расчёта (нужна геометрия), PowerShell, по одной
команде на строку:

  & C:\\gps_venv\\Scripts\\python.exe C:\\gps-tools\\tools\\gps_territory_report.py --db C:\\transport-report\\instance\\transport.db --zones C:\\gps-tools\\docs\\gps_territory_zones_2026-10-14.csv --out C:\\gps-tools\\check\\territory_sites.csv --zones-out C:\\gps-tools\\check\\territory_zones.csv --days-out C:\\gps-tools\\check\\territory_days.csv *> C:\\gps-tools\\check\\territory.log

  $LASTEXITCODE

Без --since / --until берутся все сутки базы. Вывод в консоль -- ASCII; часть
чисел условий (итог ответов «работа» по всем участкам) -- только в нём,
поэтому журнал консоли присылается вместе с CSV.
"""

import argparse
import csv
import json
import os
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

import shapely                                                      # noqa: E402
from shapely.errors import GEOSException                            # noqa: E402
from shapely.geometry import shape as shapely_shape                 # noqa: E402
from shapely.ops import transform as shapely_transform              # noqa: E402

from gps.area import MIN_WORK_AREA_HA, repair_polygon, to_utm       # noqa: E402
from gps.daily import _utm_polygon_from_geojson                     # noqa: E402
from gps.exclusion import excluded_units                            # noqa: E402
from tools.gps_mark_not_ours import (BadInput, out_clash,           # noqa: E402
                                     out_writable)
from tools.gps_track_only_days import console, open_readonly        # noqa: E402

DB_PATH = os.path.join(REPO_ROOT, 'instance', 'transport.db')
SOURCE = 'wialon'

ZONE_COLUMNS = ('zona_id', 'gruppa_id', 'tip')
SITE_CSV_COLUMNS = ('work_date', 'wialon_id', 'site_number', 'machine', 'plate',
                    'category', 'isklyuchen', 'area_ha', 'dolya',
                    'ga_na_territorii', 'ga_posle_vychitaniya',
                    'ga_posle_otbrosa', 'zony', 'tipy', 'contour_id',
                    'kontur_territoriya', 'operator_label')
ZONE_CSV_COLUMNS = ('zona_id', 'gruppa_id', 'tip', 'geometriya', 'v_pravile',
                    'ploshchad_ga', 'uchastkov', 'ga_na_zone', 'ob_ektov',
                    'dney')
DAY_CSV_COLUMNS = ('work_date', 'wialon_id', 'machine', 'category', 'isklyuchen',
                   'uchastkov', 'ga_vsego', 'ga_na_territorii', 'dolya_dnya',
                   'ga_posle_vychitaniya', 'ga_posle_otbrosa')

# Столбцы, которые инструмент читает. Нет хоть одного -- отказ с кодом 2,
# а не тихий ноль.
REQUIRED = {
    'gps_daily_aggregates': ('work_date', 'wialon_id', 'reason'),
    'gps_work_polygons': ('work_date', 'wialon_id', 'site_number', 'area_ha',
                          'polygon_geojson', 'contour_id', 'operator_label'),
    'field_contours': ('id', 'source', 'external_id', 'geometry_geojson',
                       'is_active'),
    'vialon_mappings': ('wialon_id', 'equipment_id', 'skip'),
    'equipment': ('id', 'name', 'plate', 'category'),
}

# [REASON]: порог из правила §2.14 (п. 2): участок, половина и больше
# которого на территории, -- не гектары. Вариант Б меряет его отдельно от
# вычитания, гибрид правила выводится из обоих столбцов.
DROP_SHARE = 0.5
SHARE_DIGITS = 3
HA_DIGITS = 4
ZONES_SHOWN = 5
LIST_SEPARATOR = ' | '
# [REASON]: зона в десятки гектаров -- уже не двор завода, а, может быть,
# поле рядом с ним. Такие зоны печатаются отдельно: правило, снимающее
# гектары по ним, сняло бы настоящую работу.
LARGE_ZONE_HA = 50.0
LARGEST_SHOWN = 5
FAILED_SHOWN = 10
# Корзины доли: (подпись, нижняя граница включительно). Первая корзина --
# всё, что больше нуля и меньше 0,1.
SHARE_BINS = (('(0,0.1)', 0.0), ('[0.1,0.5)', 0.1), ('[0.5,0.9)', 0.5),
              ('[0.9,1]', 0.9))
NO_MACHINE = '(no machine)'
NO_ANSWER = '(no answer)'
COUNTED = 'counted'
# Порядок категорий в консоли: эти две -- первыми, их разбивку постановка
# GPS-19 называет прямо (о МТЗ на территориях говорил владелец 14.10);
# остальные -- по алфавиту.
FIRST_CATEGORIES = ('mtz', 'combine')
EPS_HA = 1e-6


class InvariantBroken(RuntimeError):
    """Гектары участка не сошлись с его площадью -- ошибка инструмента."""


# --- ввод --------------------------------------------------------------------

def read_zones(path):
    """[(zona_id, gruppa_id, tip)] из списка зон. Кривое -- BadInput.

    [REASON]: список -- решение владельца, по нему меряются гектары, которые
    правило снимет. Повтор id или нечисло -- признак ошибки в списке, и
    молча его не чинят.
    """
    if not os.path.isfile(path):
        raise BadInput('zones list not found: %s' % path)
    name = os.path.basename(path)
    out, seen = [], set()
    try:
        with open(path, encoding='utf-8-sig', newline='') as handle:
            reader = csv.DictReader(handle)
            missing = [column for column in ZONE_COLUMNS
                       if column not in (reader.fieldnames or [])]
            if missing:
                raise BadInput('%s has no column(s) %s (comma-separated, '
                               'header zona_id,gruppa_id,tip)'
                               % (name, ', '.join(missing)))
            for line, row in enumerate(reader, 2):
                zone = (row.get('zona_id') or '').strip()
                group = (row.get('gruppa_id') or '').strip()
                if not zone.isdigit() or int(zone) <= 0:
                    raise BadInput('%s line %d: zona_id is not a positive number'
                                   % (name, line))
                if not group.isdigit() or int(group) <= 0:
                    raise BadInput('%s line %d: gruppa_id is not a positive '
                                   'number' % (name, line))
                if int(zone) in seen:
                    raise BadInput('%s line %d: zona_id %d is listed twice'
                                   % (name, line, int(zone)))
                seen.add(int(zone))
                out.append((int(zone), int(group), (row.get('tip') or '').strip()))
    except (UnicodeDecodeError, csv.Error) as problem:
        raise BadInput('%s is not a readable CSV (%s)'
                       % (name, problem.__class__.__name__))
    if not out:
        raise BadInput('%s lists no zones' % name)
    return out


def _date(option, value):
    try:
        return datetime.strptime(value, '%Y-%m-%d').strftime('%Y-%m-%d')
    except (TypeError, ValueError):
        raise BadInput('%s must look like YYYY-MM-DD' % option)


def schema_problem(con):
    """'' или чего в базе нет. Ничего не создаёт."""
    for table, columns in REQUIRED.items():
        present = {row[1] for row in con.execute('PRAGMA table_info(%s)' % table)}
        if not present:
            return 'table %s is missing' % table
        for column in columns:
            if column not in present:
                return 'column %s.%s is missing' % (table, column)
    return ''


# --- геометрия ---------------------------------------------------------------

def site_polygon(text):
    """Многоугольник участка в UTM 41N вместе с дырами; None -- не читается.

    [REASON]: проекция -- `gps.area.to_utm`, та же, в которой расчёт строил
    участок, и починка -- `repair_polygon`: контур, округлённый до 6 знаков,
    может коснуться сам себя, и GEOS на нём падает. Дыры не выбрасываются
    (см. докстринг модуля), поэтому здесь не `_utm_polygon_from_geojson`.
    """
    try:
        geometry = shapely_shape(json.loads(text))
    except (ValueError, TypeError, AttributeError, KeyError):
        return None
    if geometry.is_empty or geometry.geom_type not in ('Polygon', 'MultiPolygon'):
        return None
    utm = shapely_transform(to_utm, geometry)
    fixed = repair_polygon(utm)
    if fixed is None or fixed.area <= 0:
        return None
    return fixed


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


def load_zones(con, listed):
    """Территории: [{zona_id, gruppa_id, tip, polygon|None, area_ha, row_id}].

    `row_id` -- номер строки `field_contours` зоны: по нему `contour_id`
    участка узнаётся как территория. Контур без пригодной геометрии
    (NULL у круга и линии, не многоугольник, не чинится) -- `polygon` None.
    """
    marks = ','.join('?' * len(listed))
    rows = {}
    for row_id, external, geometry in con.execute(
            'SELECT id, external_id, geometry_geojson FROM field_contours '
            'WHERE source = ? AND (is_active IS NULL OR is_active = 1) '
            'AND external_id IN (%s)' % marks,
            [SOURCE] + [str(zone) for zone, _group, _tip in listed]):
        rows[str(external)] = (row_id, geometry)
    zones = []
    for zone, group, tip in listed:
        row_id, geometry = rows.get(str(zone), (None, None))
        polygon = _utm_polygon_from_geojson(geometry) if geometry else None
        if polygon is not None and polygon.area <= 0:
            polygon = None
        area_ha = polygon.area / 10000.0 if polygon is not None else None
        zones.append({'zona_id': zone, 'gruppa_id': group, 'tip': tip,
                      'polygon': polygon, 'area_ha': area_ha,
                      # [REASON]: §2.14, п. 1: двор завода или пункта -- это
                      # единицы гектаров; зона в десятки гектаров накрывает
                      # поля, и правило её не берёт.
                      'in_rule': area_ha is not None and area_ha <= LARGE_ZONE_HA,
                      'row_id': row_id,
                      'sites': 0, 'ha': 0.0, 'units': set(), 'days': set()})
    return zones


def measure_site(site, territories, tree):
    """Участок против территорий. None -- рамка не задела ни одной зоны.

    `territories` -- многоугольники зон с контуром, `tree` -- STRtree по ним.
    Возвращает {geom_ha, on_ha, kept_a, overlaps: [(индекс в territories,
    га)]}; `overlaps` пуст, если рамка задела зону, а пересечения нет.
    """
    hits = [int(index) for index in tree.query(site)]
    if not hits:
        return None
    geom_ha = site.area / 10000.0
    overlaps = []
    for index in hits:
        area = site.intersection(territories[index]).area
        if area > 0:
            overlaps.append((index, area / 10000.0))
    if not overlaps:
        return {'geom_ha': geom_ha, 'on_ha': 0.0, 'kept_a': geom_ha,
                'overlaps': []}
    # [REASON]: гектары на территории -- по ОБЪЕДИНЕНИЮ задетых зон, а не
    # сумма перекрытий: у двух наложенных зон место наложения иначе
    # считалось бы дважды, и «на территории» вышло бы больше участка.
    territory = shapely.union_all([territories[index] for index, _ha in overlaps])
    on_ha = site.intersection(territory).area / 10000.0
    # [REASON]: вариант А повторяет «вычесть до нарезки»: расчёт режет форму
    # суток на куски и выбрасывает куски меньше MIN_WORK_AREA_HA как проезд.
    # Вычесть территорию из сохранённого участка и выбросить мелкие остатки --
    # то же самое для каждого участка по отдельности (участки суток не
    # пересекаются).
    kept_a = sum(piece.area for piece in polygon_parts(site.difference(territory))
                 if piece.area / 10000.0 >= MIN_WORK_AREA_HA) / 10000.0
    overlaps.sort(key=lambda item: -item[1])
    return {'geom_ha': geom_ha, 'on_ha': on_ha, 'kept_a': kept_a,
            'overlaps': overlaps}


def check_invariants(key, measured):
    """0 <= после вычитания <= площадь; на территории <= площадь участка."""
    geom = measured['geom_ha']
    if not (0.0 <= measured['kept_a'] <= geom + EPS_HA):
        raise InvariantBroken('%s: %.6f ha after subtraction, site %.6f ha'
                              % (key, measured['kept_a'], geom))
    if not (0.0 <= measured['on_ha'] <= geom + EPS_HA):
        raise InvariantBroken('%s: %.6f ha on territory, site %.6f ha'
                              % (key, measured['on_ha'], geom))
    if measured['kept_a'] > geom - measured['on_ha'] + EPS_HA:
        raise InvariantBroken('%s: kept %.6f ha is more than the site minus '
                              'the territory' % (key, measured['kept_a']))


# --- база --------------------------------------------------------------------

def machines(con):
    """wialon_id -> (машина, госномер, категория) по первой непомеченной строке.

    [REASON]: строка «не наша» (`skip = 1`) машину объекту не даёт -- так её
    читает правило исключения; объект без такой строки -- «(no machine)».
    Несколько строк с машиной -- берётся первая по номеру строки, как у
    экрана «Факт по технике».
    """
    out = {}
    for wialon_id, name, plate, category in con.execute(
            'SELECT m.wialon_id, e.name, e.plate, e.category '
            'FROM vialon_mappings m JOIN equipment e ON e.id = m.equipment_id '
            'WHERE m.wialon_id IS NOT NULL AND (m.skip IS NULL OR m.skip = 0) '
            'ORDER BY m.id'):
        out.setdefault(int(wialon_id), (name or '', (plate or '').strip(),
                                        category or ''))
    return out


def _window(since, until):
    clause, args = '', []
    if since:
        clause += ' AND {0}.work_date >= ?'
        args.append(since)
    if until:
        clause += ' AND {0}.work_date <= ?'
        args.append(until)
    return clause, args


def published_days(con, since, until):
    """(машино-суток, дат, первая, последняя) опубликованных суток окна."""
    clause, args = _window(since, until)
    count, dates, first, last = con.execute(
        'SELECT COUNT(*), COUNT(DISTINCT a.work_date), MIN(a.work_date), '
        'MAX(a.work_date) FROM gps_daily_aggregates a WHERE a.reason IS NULL'
        + clause.format('a'), args).fetchone()
    return int(count or 0), int(dates or 0), first, last


def published_keys(con, since, until):
    """(дата, объект) всех опубликованных суток окна."""
    clause, args = _window(since, until)
    return con.execute(
        'SELECT a.work_date, a.wialon_id FROM gps_daily_aggregates a '
        'WHERE a.reason IS NULL' + clause.format('a'), args).fetchall()


def published_sites(con, since, until):
    """Участки опубликованных суток окна -- по одному, без загрузки всех сразу.

    [REASON]: контур участка -- килобайты текста, участков за сезон -- сотни
    тысяч; список целиком занял бы гигабайты памяти сервера.
    """
    clause, args = _window(since, until)
    return con.execute(
        'SELECT p.work_date, p.wialon_id, p.site_number, p.area_ha, '
        'p.polygon_geojson, p.contour_id, p.operator_label '
        'FROM gps_work_polygons p JOIN gps_daily_aggregates a '
        'ON a.work_date = p.work_date AND a.wialon_id = p.wialon_id '
        'WHERE a.reason IS NULL' + clause.format('p')
        + ' ORDER BY p.work_date, p.wialon_id, p.site_number', args)


# --- расчёт ------------------------------------------------------------------

def scan(con, listed, since, until):
    """Всё, что пишется в CSV и в консоль. Ничего не пишет."""
    zones = load_zones(con, listed)
    usable = [zone for zone in zones if zone['in_rule']]
    territories = [zone['polygon'] for zone in usable]
    tree = shapely.STRtree(territories) if territories else None
    territory_rows = {zone['row_id'] for zone in zones
                      if zone['row_id'] is not None}
    excluded = excluded_units(con)
    machine_of = machines(con)
    days = published_days(con, since, until)
    totals = {'sites': 0, 'stored_ha': 0.0, 'far': 0, 'near': 0,
              'unreadable': 0, 'unreadable_ha': 0.0,
              'answered': defaultdict(lambda: [0, 0.0])}
    rows, failed = [], []
    by_day = {}
    for day, unit, number, area, text, contour_id, label in \
            published_sites(con, since, until):
        unit = int(unit)
        stored = float(area or 0.0)
        totals['sites'] += 1
        totals['stored_ha'] += stored
        # [REASON]: условие включения правила (§2.14) меряет, какую долю
        # площади участков с ответом «работа» оно снимет; знаменатель --
        # ВСЕ такие участки окна, а не только задевшие территорию.
        if label:
            totals['answered'][label][0] += 1
            totals['answered'][label][1] += stored
        today = by_day.get((str(day), unit))
        if today is None:
            today = by_day[(str(day), unit)] = {
                'work_date': str(day), 'wialon_id': unit, 'sites': 0,
                'stored': 0.0, 'geom': 0.0, 'on': 0.0, 'kept_a': 0.0,
                'kept_b': 0.0}
        today['sites'] += 1
        today['stored'] += stored
        polygon = site_polygon(text) if text else None
        if polygon is None:
            totals['unreadable'] += 1
            totals['unreadable_ha'] += stored
            keep_whole(today, stored, None)
            continue
        if tree is None:
            totals['far'] += 1
            keep_whole(today, stored, polygon.area / 10000.0)
            continue
        # [REASON]: исключение GEOS на одном участке не должно стоить
        # владельцу всего прогона. Такой участок назван номером, код выхода
        # 3: записано не всё.
        try:
            measured = measure_site(polygon, territories, tree)
        except GEOSException as problem:
            failed.append((day, unit, number, str(problem)))
            keep_whole(today, stored, None)
            continue
        if measured is None:
            totals['far'] += 1
            keep_whole(today, stored, polygon.area / 10000.0)
            continue
        if not measured['overlaps']:
            totals['near'] += 1
            keep_whole(today, stored, measured['geom_ha'])
            continue
        check_invariants('%s unit %d site %s' % (day, unit, number), measured)
        share = round(measured['on_ha'] / measured['geom_ha'], SHARE_DIGITS)
        # [REASON]: порог варианта Б сравнивается с долей так, как она записана
        # в CSV: решение можно пересчитать по самой строке, а участок,
        # разрезанный краем зоны ровно пополам, не решается шумом
        # плавающей точки (0,49999999 против 0,5).
        kept_b = 0.0 if share >= DROP_SHARE else stored
        today['geom'] += measured['geom_ha']
        today['on'] += measured['on_ha']
        today['kept_a'] += measured['kept_a']
        today['kept_b'] += kept_b
        touched = [usable[index] for index, _ha in measured['overlaps']]
        for zone, (_index, ha) in zip(touched, measured['overlaps']):
            zone['sites'] += 1
            zone['ha'] += ha
            zone['units'].add(unit)
            zone['days'].add(str(day))
        name, plate, category = machine_of.get(unit, ('', '', ''))
        rows.append({
            'work_date': str(day), 'wialon_id': unit, 'site_number': number,
            'machine': name, 'plate': plate, 'category': category,
            'isklyuchen': excluded.get(unit, ''), 'area_ha': stored,
            'geom_ha': measured['geom_ha'], 'dolya': share,
            'ga_na_territorii': measured['on_ha'],
            'ga_posle_vychitaniya': measured['kept_a'],
            'ga_posle_otbrosa': kept_b,
            'zony': LIST_SEPARATOR.join(str(zone['zona_id'])
                                        for zone in touched[:ZONES_SHOWN]),
            'tipy': LIST_SEPARATOR.join(zone['tip']
                                        for zone in touched[:ZONES_SHOWN]),
            'contour_id': '' if contour_id is None else contour_id,
            'kontur_territoriya': 'da' if contour_id in territory_rows else 'net',
            'operator_label': label or ''})
    # [REASON]: порядок -- по гектарам так, как они записаны в CSV: у двух
    # участков по 1 га шум плавающей точки (0,99999999 и 1,00000001) иначе
    # решал бы порядок вместо даты и объекта.
    rows.sort(key=lambda row: (-round(row['ga_na_territorii'], HA_DIGITS),
                               row['work_date'], row['wialon_id'],
                               row['site_number']))
    # [REASON]: сутки наборов Z/ZA сверяются по CSV суток; опубликованные
    # сутки без участков -- тоже ответ (0 га), и отсутствие строки не должно
    # значить ни «нет суток», ни «нет участков».
    for day, unit in published_keys(con, since, until):
        by_day.setdefault((str(day), int(unit)), {
            'work_date': str(day), 'wialon_id': int(unit), 'sites': 0,
            'stored': 0.0, 'geom': 0.0, 'on': 0.0, 'kept_a': 0.0,
            'kept_b': 0.0})
    return {'zones': zones, 'rows': rows, 'failed': failed, 'days': days,
            'totals': totals,
            'day_rows': day_rows(by_day, machine_of, excluded)}


def keep_whole(today, stored, geom_ha):
    """Участок без перекрытия или неизмеренный -- в сутки целиком.

    [REASON]: правило снимает только то, что лежит на территории; участок
    без перекрытия остаётся в обоих вариантах своим `area_ha`. Неизмеренный
    (контур не читается, упал GEOS) тоже ничего не теряет -- снять можно
    только измеренное, -- и в знаменатель доли суток не входит
    (`geom_ha` None).
    """
    if geom_ha is not None:
        today['geom'] += geom_ha
    today['kept_a'] += stored
    today['kept_b'] += stored


def day_rows(by_day, machine_of, excluded):
    """Строки CSV суток по дате и объекту; доля 0, если перекрытия нет."""
    out = []
    for key in sorted(by_day):
        today = by_day[key]
        unit = today['wialon_id']
        name, _plate, category = machine_of.get(unit, ('', '', ''))
        on = today['on']
        out.append({
            'work_date': today['work_date'], 'wialon_id': unit,
            'machine': name, 'category': category,
            'isklyuchen': excluded.get(unit, ''), 'uchastkov': today['sites'],
            'ga_vsego': today['stored'], 'ga_na_territorii': on,
            'dolya_dnya': (round(on / today['geom'], SHARE_DIGITS)
                           if on > 0 and today['geom'] > 0 else 0.0),
            'ga_posle_vychitaniya': today['kept_a'],
            'ga_posle_otbrosa': today['kept_b']})
    return out


# --- вывод -------------------------------------------------------------------

def removed_a(row):
    """Сколько снимет вариант А: многоугольник участка минус оставленное."""
    return row['geom_ha'] - row['ga_posle_vychitaniya']


def removed_b(row):
    return row['area_ha'] - row['ga_posle_otbrosa']


def write_sites(path, rows):
    with open(path, 'w', encoding='utf-8-sig', newline='') as handle:
        writer = csv.writer(handle, delimiter=';')
        writer.writerow(SITE_CSV_COLUMNS)
        for row in rows:
            row = dict(row, area_ha=round(row['area_ha'], HA_DIGITS),
                       ga_na_territorii=round(row['ga_na_territorii'], HA_DIGITS),
                       ga_posle_vychitaniya=round(row['ga_posle_vychitaniya'],
                                                  HA_DIGITS),
                       ga_posle_otbrosa=round(row['ga_posle_otbrosa'], HA_DIGITS))
            writer.writerow([row[column] for column in SITE_CSV_COLUMNS])


def write_zones(path, zones):
    # по гектарам, как они записаны; равные -- в порядке списка владельца
    ordered = sorted(zones, key=lambda zone: -round(zone['ha'], HA_DIGITS))
    with open(path, 'w', encoding='utf-8-sig', newline='') as handle:
        writer = csv.writer(handle, delimiter=';')
        writer.writerow(ZONE_CSV_COLUMNS)
        for zone in ordered:
            writer.writerow([
                zone['zona_id'], zone['gruppa_id'], zone['tip'],
                'da' if zone['polygon'] is not None else 'net',
                'da' if zone['in_rule'] else 'net',
                '' if zone['area_ha'] is None else round(zone['area_ha'], HA_DIGITS),
                zone['sites'], round(zone['ha'], HA_DIGITS), len(zone['units']),
                len(zone['days'])])


def write_days(path, rows):
    with open(path, 'w', encoding='utf-8-sig', newline='') as handle:
        writer = csv.writer(handle, delimiter=';')
        writer.writerow(DAY_CSV_COLUMNS)
        for row in rows:
            row = dict(row, **{column: round(row[column], HA_DIGITS) for column in
                               ('ga_vsego', 'ga_na_territorii',
                                'ga_posle_vychitaniya', 'ga_posle_otbrosa')})
            writer.writerow([row[column] for column in DAY_CSV_COLUMNS])


def _sum_line(label, rows, out):
    out('  %-22s %6d %11.2f %11.2f %11.2f %11.2f'
        % (label, len(rows), sum(row['area_ha'] for row in rows),
           sum(row['ga_na_territorii'] for row in rows),
           sum(removed_a(row) for row in rows),
           sum(removed_b(row) for row in rows)))


def _split(title, rows, key, order, out):
    out('')
    out('%s:' % title)
    out('  %-22s %6s %11s %11s %11s %11s'
        % ('', 'sites', 'stored_ha', 'on_terr_ha', 'minus_A_ha', 'minus_B_ha'))
    groups = defaultdict(list)
    for row in rows:
        groups[key(row)].append(row)
    for label in order(groups):
        _sum_line(console(label), groups[label], out)


def _category_order(groups):
    first = [label for label in FIRST_CATEGORIES if label in groups]
    rest = sorted(label for label in groups
                  if label not in FIRST_CATEGORIES and label != NO_MACHINE)
    return first + rest + ([NO_MACHINE] if NO_MACHINE in groups else [])


def report(result, since, until, out=print):
    zones, rows, totals = result['zones'], result['rows'], result['totals']
    count, dates, first, last = result['days']
    out('window asked       : %s .. %s' % (since or '(first day)',
                                           until or '(last day)'))
    if count:
        out('window covered     : %s .. %s (%d dates with a published day)'
            % (first, last, dates))
    else:
        out('window covered     : (no published day in the window)')
    out('published days     : %d machine-days' % count)
    out('  with a site      : %d, of them with a site on territory: %d'
        % (sum(1 for row in result['day_rows'] if row['uchastkov']),
           sum(1 for row in result['day_rows'] if row['ga_na_territorii'] > 0)))
    out('sites scanned      : %d, %.2f ha stored'
        % (totals['sites'], totals['stored_ha']))
    out('  far from any zone: %d (bounding box misses every zone)' % totals['far'])
    out('  near, no overlap : %d (bounding box meets a zone, the site does not)'
        % totals['near'])
    out('  on territory     : %d (listed below)' % len(rows))
    out('  outline unreadable: %d, %.2f ha (not measured)'
        % (totals['unreadable'], totals['unreadable_ha']))
    out('  geometry failed  : %d (not measured, see below)' % len(result['failed']))
    for label in sorted(totals['answered']):
        count_answered, ha_answered = totals['answered'][label]
        out('  answered %-9s: %d, %.2f ha (all scanned sites with this answer)'
            % (console(label), count_answered, ha_answered))

    with_geometry = [zone for zone in zones if zone['polygon'] is not None]
    without = [zone['zona_id'] for zone in zones if zone['polygon'] is None]
    out('')
    out('territory zones    : %d listed, %d with a usable outline, %d without'
        % (len(zones), len(with_geometry), len(without)))
    if without:
        out('  without outline  : %s' % ' '.join(str(zone) for zone in without))
    if not with_geometry:
        out('WARNING: no listed zone has an outline - nothing could be measured')
    else:
        union = shapely.union_all([zone['polygon'] for zone in with_geometry])
        total = sum(zone['area_ha'] for zone in with_geometry)
        out('  area             : %.2f ha as a union, %.2f ha summed by zone '
            '(%.2f ha where zones overlap)' % (union.area / 10000.0, total,
                                                total - union.area / 10000.0))
        largest = sorted(with_geometry,
                         key=lambda zone: -round(zone['area_ha'], HA_DIGITS))
        out('  largest zones    : %s' % ', '.join(
            '%d %s %.2f ha' % (zone['zona_id'], console(zone['tip']) or '-',
                               zone['area_ha'])
            for zone in largest[:LARGEST_SHOWN]))
        large = [zone['zona_id'] for zone in largest
                 if zone['area_ha'] > LARGE_ZONE_HA]
        out('  larger than %.0f ha: %s%s' % (
            LARGE_ZONE_HA, ' '.join(str(z) for z in large) or 'none',
            ' -- NOT in the rule and not measured (2.14, p. 1)' if large else ''))
        out('  in the rule      : %d zone(s)'
            % sum(1 for zone in zones if zone['in_rule']))

    out('')
    on_ha = sum(row['ga_na_territorii'] for row in rows)
    out('sites on territory : %d, %.2f ha stored, %.2f ha of it on territory'
        % (len(rows), sum(row['area_ha'] for row in rows), on_ha))
    out('  control          : %.4f ha stored vs %.4f ha by their outlines'
        % (sum(row['area_ha'] for row in rows), sum(row['geom_ha'] for row in rows)))
    out('  by share of the site on territory:')
    out('  %-10s %6s %11s %11s' % ('share', 'sites', 'stored_ha', 'on_terr_ha'))
    for position, (label, lower) in enumerate(SHARE_BINS):
        upper = SHARE_BINS[position + 1][1] if position + 1 < len(SHARE_BINS) \
            else None
        picked = [row for row in rows if row['dolya'] >= lower
                  and (upper is None or row['dolya'] < upper)]
        out('  %-10s %6d %11.2f %11.2f'
            % (label, len(picked), sum(row['area_ha'] for row in picked),
               sum(row['ga_na_territorii'] for row in picked)))
    out('')
    out('totals             : %.2f ha on territory; variant A (subtract, drop '
        'pieces < %.1f ha) removes %.2f ha; variant B (drop sites with share '
        '>= %.1f) removes %.2f ha'
        % (on_ha, MIN_WORK_AREA_HA, sum(removed_a(row) for row in rows),
           DROP_SHARE, sum(removed_b(row) for row in rows)))
    _split('by exclusion (counted / ne_nasha / ne_polevaya)', rows,
           lambda row: row['isklyuchen'] or COUNTED,
           lambda groups: sorted(groups, key=lambda label: (label != COUNTED,
                                                            label)), out)
    _split('by machine category', rows,
           lambda row: row['category'] or NO_MACHINE, _category_order, out)
    _split("by operator's answer", rows,
           lambda row: row['operator_label'] or NO_ANSWER,
           lambda groups: sorted(groups, key=lambda label: (label == NO_ANSWER,
                                                            label)), out)
    if result['failed']:
        out('')
        out('geometry failed on %d site(s):' % len(result['failed']))
        for day, unit, number, problem in result['failed'][:FAILED_SHOWN]:
            out('  %s unit %d site %s: %s' % (day, unit, number,
                                              console(problem)[:120]))
        if len(result['failed']) > FAILED_SHOWN:
            out('  ... and %d more' % (len(result['failed']) - FAILED_SHOWN))


# --- запуск ------------------------------------------------------------------

def _refuse(message):
    sys.stderr.write('ERROR: %s\n' % message)
    return 2


def check_outputs(args):
    """Ничего не пишет; BadInput -- в какой отчёт писать нельзя и почему."""
    protected = (args.db, args.db + '-wal', args.db + '-shm', args.zones)
    outputs = (('--out', args.out), ('--zones-out', args.zones_out),
               ('--days-out', args.days_out))
    for option, path in outputs:
        folder = os.path.dirname(os.path.abspath(path))
        if not os.path.isdir(folder):
            raise BadInput('folder for %s does not exist: %s' % (option, folder))
        # [REASON]: три отчёта попарно разные: второй, записанный поверх
        # первого, молча оставил бы владельцу один файл вместо двух.
        others = tuple(other for name, other in outputs if name != option)
        clash = out_clash(path, protected + others)
        if clash:
            raise BadInput('%s points at %s - refusing to overwrite it'
                           % (option, clash))
    for option, path in outputs:
        why = out_writable(path)
        if why:
            raise BadInput('cannot write %s %s (%s) - nothing was written'
                           % (option, path, why))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--db', default=DB_PATH, help='path to transport.db')
    parser.add_argument('--zones', required=True,
                        help='CSV of territory zones: zona_id,gruppa_id,tip')
    parser.add_argument('--since', help='first day, YYYY-MM-DD (default: all)')
    parser.add_argument('--until', help='last day, YYYY-MM-DD (default: all)')
    parser.add_argument('--out', default='gps_territory_sites.csv',
                        help='CSV of the sites on territory')
    parser.add_argument('--zones-out', default='gps_territory_zones.csv',
                        help='CSV with a row per listed zone')
    parser.add_argument('--days-out', default='gps_territory_days.csv',
                        help='CSV with a row per published machine-day')
    args = parser.parse_args(argv)

    try:
        since = _date('--since', args.since) if args.since else None
        until = _date('--until', args.until) if args.until else None
        if since and until and until < since:
            raise BadInput('--until is before --since')
        # [REASON]: проверка до любого открытия: `sqlite3.connect` на
        # отсутствующий путь создал бы пустую базу, и следующий прогон
        # расчёта нашёл бы её вместо боевой.
        if not os.path.isfile(args.db):
            raise BadInput('database not found at %s - refusing to run' % args.db)
        # [REASON]: база открывается адресом `file:...?mode=ro`; `#`, `?` и `%`
        # в пути SQLite читает как часть адреса -- режим «только чтение»
        # отпал бы, и рядом создался бы новый файл базы.
        if any(mark in args.db for mark in '#?%'):
            raise BadInput('the database path contains #, ? or %% - move the '
                           'database or use another path: %s' % args.db)
        listed = read_zones(args.zones)
        check_outputs(args)
    except BadInput as problem:
        return _refuse(str(problem))

    con = open_readonly(args.db)
    try:
        try:
            problem = schema_problem(con)
        except sqlite3.DatabaseError as error:
            problem = '%s is not a readable database (%s)' % (args.db, error)
        if problem:
            return _refuse(problem)
        result = scan(con, listed, since, until)
    except InvariantBroken as problem:
        print('FAILED: invariant broken (%s) - a bug in the tool, nothing was '
              'written' % console(problem))
        return 1
    finally:
        con.close()

    report(result, since, until)
    write_sites(args.out, result['rows'])
    write_zones(args.zones_out, result['zones'])
    write_days(args.days_out, result['day_rows'])
    print('')
    print('sites CSV (%d rows): %s' % (len(result['rows']), console(args.out)))
    print('zones CSV (%d rows): %s' % (len(result['zones']),
                                       console(args.zones_out)))
    print('days CSV (%d rows): %s' % (len(result['day_rows']),
                                      console(args.days_out)))
    print('nothing was written to the database: it was opened mode=ro')
    return 3 if result['failed'] else 0


if __name__ == '__main__':
    sys.exit(main())
