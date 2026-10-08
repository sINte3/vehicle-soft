# -*- coding: utf-8 -*-
"""gps_routes.py -- GPS-3, экран «Факт по технике».

Карта маршрутов:
  GET  /gps/fact          -- сутки одной машины: измеренные показатели трека,
                             найденные участки работы, их полигоны и ответы
                             оператора «работа/проезд».
  POST /gps/fact/answer   -- ответ оператора по одному участку.

ЧТО ЭТОТ ЭКРАН ЧИТАЕТ И ЧТО ПИШЕТ
Читает две таблицы, которые заполняет gps/daily.py (GPS-2). Пишет ровно две
колонки: gps_work_polygons.operator_label и .decided_at. Ничего из
посчитанного экран не правит -- пересчёт суток всё равно заменит эти строки,
и правка молча исчезла бы.

С A2 (карта) экран ещё ЧИТАЕТ трек одной машины за одни сутки из помесячного
файла точек (`instance/gps_points_YYYYMM.db`) -- только `mode=ro`, запись
отвергает сам SQLite -- и контуры участков из `field_contours`. Решение G3 это
не нарушает: оно запрещает класть точки В `transport.db`, а здесь одна машина
за одни сутки читается на время запроса и никуда не копируется.

[REASON]: маршруты закрыты @module_required('wialon'), а не новым кодом
модуля. Это те же данные Wialon, что и существующий раздел /wialon, право на
них уже роздано, а новый код модуля потребовал бы миграции прав, строки в
админке и решения владельца о том, кому его выдавать -- ради экрана, который
показывает ровно то же, что уже разрешено видеть.

Ответ оператора -- обучающий набор для правила «работа/проезд» (раздел 2.8.1
дорожной карты), поэтому подпись `suggested_label` показывается КАК ПОДСКАЗКА
рядом с ответом, а не вместо него: если бы экран подставлял её в ответ,
набор перестал бы быть независимым от правила, которое на нём проверяют.
"""

import json
import math
import os
import sqlite3

from collections import Counter, defaultdict
from datetime import date as date_cls, datetime, timedelta

from flask import (Blueprint, abort, current_app, flash, g, redirect,
                   render_template, request, url_for)
from flask_login import current_user, login_required

import vs_map
from gps.exclusion import REASON_TRACK_ONLY
from gps.tolerance import (TOLERANCE_GA_OK, TOLERANCE_GA_WARN,  # noqa: F401
                           VERDICT_FAIL, VERDICT_OK, VERDICT_WARN,
                           verdict_for_ga)
# [REASON]: коллектор -- чистый stdlib (gps_collector/requirements.txt пуст
# намеренно), поэтому служба Flask берёт у него путь к файлам точек и их
# местные сутки, а не заводит вторую копию. Стрелка обратно не идёт никогда.
from gps_collector import storage as point_storage
from gps_collector.config import TZ as LOCAL_TZ, points_dir
from models import (
    db,
    CAT_PASSENGER,
    CAT_YUK_TRANSPORT,
    Equipment,
    FieldContour,
    GPS_DECISION_DISPUTED,
    GPS_DECISIONS,
    GPS_LABEL_PASSAGE,
    GPS_LABEL_WORK,
    GPS_OPERATOR_LABELS,
    GpsDailyAggregate,
    GpsSyncLog,
    GpsVerdict,
    GpsWorkPolygon,
    VialonMapping,
    WO_STATUS_CANCELLED,
    WorkOrder,
    module_required,
)

gps_bp = Blueprint('gps', __name__, url_prefix='/gps')


def _gps_t(uz_text, ru_text):
    # Route-level bilingual helper for strings built outside templates --
    # the same pattern as _drone_t (drones.py) and _spare_t (spare_parts.py).
    return ru_text if getattr(g, 'lang', 'uz') == 'ru' else uz_text

# Причины, по которым площадь не публикуется. Подписи двуязычные; ключ --
# то, что пишет gps/daily.py в колонку reason.
REASON_LABELS = {
    'redkaya_zapis': ('Редкая запись трека — площадь не публикуется',
                      'Трек сийрак ёзилган — майдон эълон қилинмайди'),
    'net_dvizheniya': ('Движения нет', 'Ҳаракат йўқ'),
    'net_tochek': ('Точек за сутки нет', 'Кун учун нуқталар йўқ'),
    # [REASON]: GPS-11 -- коллектор ещё не дошёл до конца суток (усечённый
    # ответ сервера при долгом простое). Площадь по неполным точкам не
    # публикуется; gps.daily --catch-up досчитает сутки, когда точки доедут.
    'sbor_nepolnyy': ('Сбор точек за эти сутки не завершён — площадь не публикуется',
                      'Бу кун учун нуқталар йиғиш тугалланмаган — майдон эълон қилинмайди'),
    # [REASON]: A1, решение владельца 28.09.2026 -- по спецтехнике след есть,
    # гектаров нет. Слово пишет gps/daily.py, берётся из gps/exclusion.py:
    # импорт стоит на stdlib-модуль, а не на расчёт с numpy.
    REASON_TRACK_ONLY: ('Спецтехника — гектары не считаются',
                        'Махсус техника — гектар ҳисобланмайди'),
}


def _parse_date(value):
    try:
        return datetime.strptime((value or '').strip(), '%Y-%m-%d').date()
    except (TypeError, ValueError):
        return None


def _machine_names(wialon_ids):
    """wialon_id -> человеческое имя машины, где оно известно.

    [REASON]: связка ещё не заполнена (VialonMapping.wialon_id ставит только
    сопоставление PHASE1, ручной импорт его не трогает), поэтому имя может
    отсутствовать. Тогда показывается сам wialon_id: подставить сюда имя по
    похожести номера нельзя -- шесть номеров указывают на несколько объектов,
    и у одной машины их три.

    [REASON]: имя машины -- это `Equipment.name` ВМЕСТЕ с госномером. Одно
    `name` -- это модель («МТЗ-80.1»), и владелец 28.09 увидел в списке дюжину
    одинаковых строк, среди которых свою машину не найти. Формат «модель —
    номер» тот же, что в выборе техники наряда и отчёта по запчастям. Строка
    сопоставления без машины показывает имя объекта в Wialon -- в нём номер
    обычно уже есть.

    Несколько строк на один id (сменённые трекеры) дают одно имя, и всегда
    одно и то же: сначала строка с машиной, при равенстве -- меньший номер
    строки. Иначе имя в списке зависело бы от порядка, в котором база вернула
    строки.
    """
    if not wialon_ids:
        return {}
    rows = (db.session.query(VialonMapping.wialon_id, VialonMapping.vialon_name,
                             Equipment.name, Equipment.plate)
            .outerjoin(Equipment, VialonMapping.equipment_id == Equipment.id)
            .filter(VialonMapping.wialon_id.in_(list(wialon_ids)))
            .order_by(VialonMapping.id).all())
    names, from_machine = {}, set()
    for wialon_id, wialon_name, equipment_name, plate in rows:
        if equipment_name:
            if wialon_id in from_machine:
                continue
            plate = (plate or '').strip()
            names[wialon_id] = ('%s — %s' % (equipment_name, plate) if plate
                                else equipment_name)
            from_machine.add(wialon_id)
        elif wialon_name and wialon_id not in names:
            names[wialon_id] = wialon_name
    return names


def _by_machine_name(aggregates, names):
    """Строки суток в порядке имён: сначала названные, потом голые номера.

    [REASON]: список машин читает человек, который ищет СВОЮ машину. В порядке
    номеров объектов Wialon «МТЗ-80.1 — 80 248 HA» стоит между комбайном и
    погрузчиком, а соседние номера одной модели разбросаны по всему списку.
    """
    return sorted(aggregates, key=lambda a: (
        a.wialon_id not in names,
        (names.get(a.wialon_id) or '').casefold(),
        a.wialon_id))


# [REASON]: слаг категории «Йўловчи ташиш техникаси» (`CAT_PASSENGER`). Взят
# из `models.CATEGORIES` через импорт, в отличие от `gps/daily.py`, которому
# импортировать models нельзя. Одно и то же множество исключённых объектов
# считается здесь и там ДВУМЯ реализациями -- по-другому нельзя: расчёт живёт
# в своём venv с numpy, а служба Flask этот стек не тянет. Чтобы реализации не
# разъехались, их ответы закреплены общим тестом на одной и той же базе
# (tests/test_gps_fact_excluded.py).
NON_FIELD_CATEGORIES = frozenset({CAT_PASSENGER, CAT_YUK_TRANSPORT})


def _excluded_units():
    """wialon_id объектов, которых на план-факте быть не должно.

    То же правило, что в `gps.daily.excluded_units`:

    - строка сопоставления со `skip = 1` -- владелец сказал «не наша техника»;
    - машина в непольевой категории -- легковая остаётся нашей и в импорте
      моточасов, просто поля не пашет, поэтому категория, а не `skip`.

    Исключает ТОЛЬКО явное. `skip NULL` (колонку добавляли миграцией к уже
    существующим строкам) не исключает, объект без строки сопоставления -- тоже.
    Если у одного id есть и исключающая строка, и оставляющая, объект остаётся:
    противоречие разбирает человек, а не запрос.

    Строки НЕ удаляются и расчёт задним числом не переписывается: уже
    посчитанные сутки остаются в базе, просто не показываются.
    """
    marked, kept, non_field, field = set(), set(), set(), set()
    rows = (db.session.query(VialonMapping.wialon_id, VialonMapping.skip,
                             Equipment.category)
            .outerjoin(Equipment, VialonMapping.equipment_id == Equipment.id)
            .filter(VialonMapping.wialon_id.isnot(None)).all())
    for wialon_id, skip, category in rows:
        unit_id = int(wialon_id)
        (marked if skip else kept).add(unit_id)
        if category is None:
            continue
        (non_field if category in NON_FIELD_CATEGORIES else field).add(unit_id)
    return (marked - kept) | (non_field - field)


def _svg_shapes(sites):
    """Полигоны участков в координатах картинки 0..1000 по большей стороне.

    Возвращает (shapes, width, height); shapes -- список словарей с готовой
    строкой `points` для <polygon>. Пусто, если рисовать нечего.

    [REASON]: считается на сервере и уходит в шаблон готовыми числами. Рисовать
    в браузере значило бы разбирать GeoJSON скриптом на странице; проект
    запрещает внешние фронтенд-фреймворки, а свой разбор координат -- это тот
    же код, только без тестов.
    """
    rings = []
    for site in sites:
        try:
            geometry = json.loads(site.polygon_geojson or '')
        except (ValueError, TypeError):
            continue
        if not isinstance(geometry, dict):
            continue
        coordinates = geometry.get('coordinates') or []
        if geometry.get('type') == 'Polygon':
            outers = [coordinates[0]] if coordinates else []
        elif geometry.get('type') == 'MultiPolygon':
            outers = [part[0] for part in coordinates if part]
        else:
            continue
        for ring in outers:
            points = [(float(x), float(y)) for x, y in ring
                      if isinstance(x, (int, float)) and isinstance(y, (int, float))]
            if len(points) >= 3:
                rings.append((site, points))
    if not rings:
        return [], 0, 0

    xs = [x for _, points in rings for x, _ in points]
    ys = [y for _, points in rings for _, y in points]
    left, right, bottom, top = min(xs), max(xs), min(ys), max(ys)
    # [REASON]: градус долготы на широте Бухары примерно в 0,77 раза короче
    # градуса широты. Без этой поправки поле вытягивается по горизонтали и
    # человек не узнаёт на картинке своё поле.
    span_x = max((right - left) * 0.77, 1e-9)
    span_y = max(top - bottom, 1e-9)
    scale = 1000.0 / max(span_x, span_y)
    width = max(round(span_x * scale, 1), 1.0)
    height = max(round(span_y * scale, 1), 1.0)

    shapes = []
    for site, points in rings:
        pairs = ['%.1f,%.1f' % ((x - left) * 0.77 * scale,
                                height - (y - bottom) * scale)
                 for x, y in points]
        shapes.append({'site': site, 'points': ' '.join(pairs)})
    return shapes, width, height


# ── Трек суток для карты (A2) ────────────────────────────────────────────────

# [REASON]: те же пороги, что в gps/area.py (SPEED_MIN_KMH, MOTION_GAP_SECONDS):
# «в движении» -- от 1 км/ч, молчание дольше 5 минут на ходу -- разрыв, а не
# езда. Продублированы, потому что служба Flask не импортирует numpy, а
# gps/area.py импортирует его первой строкой; совпадение закреплено тестом,
# который читает gps/area.py как текст (tests/test_gps_fact_map.py).
MOTION_MIN_KMH = 1.0
MOTION_GAP_S = 300.0

# [REASON]: плотный трекер пишет до 50 000 точек в сутки (FMB 140, 04.09), и
# все они в странице -- мегабайт текста ради линии, которую на экране не
# различить. Точки ближе 3 м к предыдущей (стоянка, дрожание) не рисуются, а
# сверх 8 000 вершин берётся каждая k-я -- на масштабе поля это та же линия.
TRACK_MIN_STEP_M = 3.0
TRACK_MAX_VERTICES = 8000


def _day_points(unit_id, day, folder=None):
    """[(t, lon, lat, speed)] одного объекта за местные сутки. Только чтение.

    [REASON]: `mode=ro` -- запрет SQLite, а не обещание кода: экран не может
    ни дописать в файл коллектора, ни создать пустой вместо отсутствующего.
    Нет файла, файл занят или испорчен -- трека нет, а страница открывается:
    карта без трека лучше экрана с ошибкой вместо показателей суток.
    """
    folder = folder or current_app.config.get('GPS_POINTS_DIR') or points_dir()
    path = point_storage.points_path(folder, day.strftime('%Y%m'))
    if not os.path.exists(path):
        return []
    start = int(datetime(day.year, day.month, day.day,
                         tzinfo=LOCAL_TZ).timestamp())
    try:
        con = sqlite3.connect('file:%s?mode=ro' % path, uri=True, timeout=5)
    except sqlite3.Error:
        return []
    try:
        return [(int(t), float(lon), float(lat), float(speed))
                for t, lon, lat, speed in con.execute(
                    'SELECT t, lon, lat, speed FROM points '
                    'WHERE unit_id = ? AND t >= ? AND t < ? ORDER BY t',
                    (int(unit_id), start, start + 86400))]
    except sqlite3.Error:
        return []
    finally:
        con.close()


def _metres(a, b):
    """Расстояние между (lon, lat) в метрах -- равнопромежуточное приближение.

    На расстояниях в метры и десятки метров ошибка ничтожна; точнее здесь не
    нужно: это решает, рисовать ли точку, а не сколько гектаров.
    """
    lat = math.radians((a[1] + b[1]) / 2.0)
    dx = (b[0] - a[0]) * 111320.0 * math.cos(lat)
    dy = (b[1] - a[1]) * 110540.0
    return math.hypot(dx, dy)


def track_segments(points, min_step_m=TRACK_MIN_STEP_M,
                   max_vertices=TRACK_MAX_VERTICES):
    """Трек для карты: куски [[lat, lon], ...], разрезанные по молчанию.

    [REASON]: разрез -- где между двумя СОСЕДНИМИ сообщениями больше 5 минут.
    Одна линия через разрыв нарисовала бы прямую поперёк поля, по которой
    машина не ездила, и на разметке «работа/проезд» человек принял бы её за
    проход.
    """
    segments, current, last, previous_t = [], [], None, None
    for t, lon, lat, _speed in points:
        if not (-180.0 <= lon <= 180.0 and -90.0 <= lat <= 90.0):
            continue
        if previous_t is not None and t - previous_t > MOTION_GAP_S:
            if len(current) > 1:
                segments.append(current)
            current, last = [], None
        previous_t = t
        if last is not None and _metres(last, (lon, lat)) < min_step_m:
            continue
        current.append([round(lat, 5), round(lon, 5)])
        last = (lon, lat)
    if len(current) > 1:
        segments.append(current)
    total = sum(len(segment) for segment in segments)
    if total > max_vertices:
        step = int(math.ceil(total / float(max_vertices)))
        segments = [segment[::step] + ([segment[-1]]
                                       if (len(segment) - 1) % step else [])
                    for segment in segments]
    return segments


def track_summary(points):
    """Время в движении и первое/последнее движение за сутки.

    [REASON]: «след» спецтехники -- это точки, километры, ВРЕМЯ и качество
    (прочтение, подтверждённое владельцем 28.09). Время в агрегате не лежит,
    а точки для карты уже прочитаны: сумма промежутков между соседними
    сообщениями, где машина ехала (от 1 км/ч в начале промежутка), без
    разрывов длиннее 5 минут -- их расчёт считает потерянным временем, а не
    ездой. Те же определения, что у показателей качества в gps/area.py.
    """
    moving = 0.0
    first = last = None
    for (t0, _lon0, _lat0, v0), (t1, _lon1, _lat1, _v1) in zip(points,
                                                              points[1:]):
        if v0 >= MOTION_MIN_KMH and t1 - t0 <= MOTION_GAP_S:
            moving += t1 - t0
    for t, _lon, _lat, speed in points:
        if speed >= MOTION_MIN_KMH:
            first = t if first is None else first
            last = t
    return {'moving_s': moving,
            'first_move': (datetime.fromtimestamp(first, LOCAL_TZ)
                           if first is not None else None),
            'last_move': (datetime.fromtimestamp(last, LOCAL_TZ)
                          if last is not None else None)}


def _geometry(text):
    try:
        geometry = json.loads(text or '')
    except (TypeError, ValueError):
        return None
    if not isinstance(geometry, dict) or geometry.get('type') not in (
            'Polygon', 'MultiPolygon'):
        return None
    return geometry


SITE_TONES = {GPS_LABEL_WORK: 'success', GPS_LABEL_PASSAGE: 'danger'}


def map_layers(points, sites, contours, is_ru):
    """Слои карты суток снизу вверх: контуры полей, трек, участки.

    Подписи готовятся здесь, на языке интерфейса: vs-map.js вставляет их
    текстом и сам ничего не переводит.
    """
    layers = []
    group_contours = 'Контуры полей' if is_ru else 'Дала контурлари'
    group_track = 'Трек' if is_ru else 'Трек'
    group_sites = 'Участки' if is_ru else 'Участкалар'
    for contour in contours:
        geometry = _geometry(contour.geometry_geojson)
        if geometry is not None:
            layers.append({'kind': 'outline', 'group': group_contours,
                           'title': contour.name, 'geojson': geometry})
    segments = track_segments(points)
    if segments:
        layers.append({'kind': 'track', 'group': group_track,
                       'title': 'Трек за сутки' if is_ru else 'Кунлик трек',
                       'segments': segments})
    answers = {GPS_LABEL_WORK: 'работа' if is_ru else 'иш',
               GPS_LABEL_PASSAGE: 'проезд' if is_ru else 'ўтиш'}
    for site in sites:
        geometry = _geometry(site.polygon_geojson)
        if geometry is None:
            continue
        parts = ['№%d' % site.site_number,
                 '%.2f %s' % (site.area_ha or 0, 'га'),
                 '%d %s' % (round(site.minutes or 0),
                            'мин' if is_ru else 'дақ')]
        if site.operator_label in answers:
            parts.append(answers[site.operator_label])
        layers.append({'kind': 'area', 'group': group_sites,
                       'label': str(site.site_number),
                       'title': ' · '.join(parts),
                       'tone': SITE_TONES.get(site.operator_label, 'primary'),
                       'geojson': geometry})
    return layers


# ── Сверка наряда: план против факта GPS ─────────────────────────────────────

# [REASON]: допуски владельца (вопрос В-2) и светофор живут в
# `gps/tolerance.py`: той же функцией судит объём заявок сверка agro-work, а
# её ядро на stdlib этот модуль импортировать не может (Flask). Имена ниже
# -- те же объекты, не копии.
UNIT_GA = 'ga'

VERDICT_NO_DATA = 'no_data'

# Почему сверить нельзя. Это не отказ, а названная причина: «мы не смогли» и
# «расхождение ноль» — разные вещи, и очередь исключений живёт именно ими.
NO_DATA_REASONS = {
    'edinica_ne_meryaetsya': (
        'Единица измерения не меряется по GPS',
        'Ўлчов бирлиги GPS орқали ўлчанмайди'),
    'net_svyazi_s_wialon': (
        'Техника не сопоставлена с объектом Wialon',
        'Техника Wialon объекти билан боғланмаган'),
    'neskolko_obektov': (
        'Техника сопоставлена с несколькими объектами Wialon',
        'Техника бир нечта Wialon объекти билан боғланган'),
    'net_rascheta': (
        'За эти сутки расчёта нет',
        'Бу кун учун ҳисоб йўқ'),
    'ploshchad_ne_publikovalas': (
        'Площадь за эти сутки не публиковалась',
        'Бу кун учун майдон эълон қилинмаган'),
    'net_obyoma': (
        'В наряде нет объёма, с чем сверять',
        'Нарядда ҳажм йўқ, солиштиришга нарса йўқ'),
}


def fact_from_sites(sites):
    """Гектары за сутки и то, что о них ещё не сказано.

    [REASON]: участок, названный человеком проездом, из факта вычитается —
    ответ человека это факт. Участок БЕЗ ответа в факт входит: он уже прошёл
    подтверждённый владельцем порог 0,3 га (раздел 2.6 дорожной карты).
    Предрегистрированное правило «≥ 1,3 га ИЛИ ≥ 25 мин» в арифметику НЕ
    берётся ни при каких условиях: оно ещё не принято (раздел 2.8.1), и
    считать по нему деньги значило бы ввести его в обход собственного
    протокола приёмки. Поэтому рядом с цифрой всегда стоит, сколько участков
    и гектаров ещё без ответа — на столько цифра может уменьшиться.
    """
    counted = [s for s in sites if s.operator_label != GPS_LABEL_PASSAGE]
    unanswered = [s for s in sites if not s.operator_label]
    return (round(sum(s.area_ha or 0 for s in counted), 3),
            len(unanswered),
            round(sum(s.area_ha or 0 for s in unanswered), 3))


def reconcile_orders(orders, wialon_by_equipment, aggregates, sites_by_key):
    """По наряду на строку: план, факт, расхождение, вердикт или причина.

    Ничего не читает сама — всё передаётся готовыми словарями, чтобы список
    нарядов стоил трёх запросов, а не трёх на каждую строку.
    """
    rows = []
    for order in orders:
        day = order.actual_date or order.planned_date
        base = (order.actual_quantity if order.actual_quantity is not None
                else order.planned_quantity)
        row = {'order': order, 'day': day, 'base': base, 'fact': None,
               'deviation': None, 'share': None, 'verdict': VERDICT_NO_DATA,
               'reason': None, 'unanswered': 0, 'unanswered_ha': 0.0,
               'wialon_id': None}

        if (order.unit or '').strip().lower() != UNIT_GA:
            row['reason'] = 'edinica_ne_meryaetsya'
            rows.append(row)
            continue
        if base is None:
            row['reason'] = 'net_obyoma'
            rows.append(row)
            continue

        wialon_ids = wialon_by_equipment.get(order.equipment_id) or []
        if not wialon_ids:
            row['reason'] = 'net_svyazi_s_wialon'
            rows.append(row)
            continue
        if len(wialon_ids) > 1:
            # [REASON]: шесть номеров указывают на несколько объектов Wialon, а
            # у одной машины их три — история замены трекеров. Сложить их
            # значит посчитать одну работу дважды; выбрать один — угадать.
            # Сумма в счёте не угадывается.
            row['reason'] = 'neskolko_obektov'
            rows.append(row)
            continue

        wialon_id = wialon_ids[0]
        row['wialon_id'] = wialon_id
        aggregate = aggregates.get((wialon_id, day))
        if aggregate is None:
            row['reason'] = 'net_rascheta'
            rows.append(row)
            continue
        if aggregate.reason:
            row['reason'] = 'ploshchad_ne_publikovalas'
            row['aggregate_reason'] = aggregate.reason
            rows.append(row)
            continue

        fact, unanswered, unanswered_ha = fact_from_sites(
            sites_by_key.get((wialon_id, day), []))
        verdict, deviation, share = verdict_for_ga(base, fact)
        row.update({'fact': fact, 'deviation': round(deviation, 3),
                    'share': share, 'verdict': verdict,
                    'unanswered': unanswered, 'unanswered_ha': unanswered_ha})
        rows.append(row)
    return rows


def _wialon_by_equipment(equipment_ids):
    """equipment_id -> [wialon_id, ...] по сопоставлению, без пропущенных."""
    out = defaultdict(list)
    if not equipment_ids:
        return out
    rows = (db.session.query(VialonMapping.equipment_id, VialonMapping.wialon_id)
            .filter(VialonMapping.equipment_id.in_(list(equipment_ids)),
                    VialonMapping.wialon_id.isnot(None),
                    VialonMapping.skip.is_(False)).all())
    for equipment_id, wialon_id in rows:
        if wialon_id not in out[equipment_id]:
            out[equipment_id].append(wialon_id)
    return out


def _latest_reviews(order_ids):
    """work_order_id -> последняя запись разбора.

    [REASON]: журнал только пополняется, поэтому «текущее состояние» — это
    последняя строка, а не отдельное поле, которое пришлось бы держать в
    согласии с историей. Одним запросом, а не одним на наряд.
    """
    if not order_ids:
        return {}
    latest = {}
    for row in (GpsVerdict.query
                .filter(GpsVerdict.work_order_id.in_(list(order_ids)))
                .order_by(GpsVerdict.reviewed_at, GpsVerdict.id)):
        latest[row.work_order_id] = row
    return latest


@gps_bp.route('/sync')
@module_required('wialon')
@login_required
def sync():
    """Приём точек: что сделал каждый ночной прогон коллектора.

    [REASON]: без этого экрана «коллектор отработал и ничего нового не нашёл»
    неотличимо от «коллектор не запускался», а точки лежат вне transport.db и
    посмотреть на них из приложения нельзя вовсе.
    """
    runs = (GpsSyncLog.query.order_by(GpsSyncLog.started_at.desc())
            .limit(60).all())
    return render_template('gps/sync.html', runs=runs,
                           broken=[r for r in runs if not r.balances])


@gps_bp.route('/orders/review', methods=['POST'])
@module_required('wialon')
@login_required
def orders_review():
    """Записать разбор. Ни одно число при этом не меняется."""
    order = WorkOrder.query.get_or_404(request.form.get('order_id', type=int))
    if not current_user.is_admin and not current_user.can_access_org(
            order.organization_id):
        abort(403)
    decision = (request.form.get('decision') or '').strip()
    if decision not in GPS_DECISIONS:
        abort(400)
    comment = (request.form.get('comment') or '').strip()
    if decision == GPS_DECISION_DISPUTED and not comment:
        # [REASON]: «оспорен» без объяснения не годится ни для чего. Ради
        # объяснений журнал и ведётся: по ним раз в сезон пересматриваются
        # допуски, и «не согласен» без причины в такой разбор не входит.
        flash(_gps_t('Изоҳсиз эътироз қабул қилинмайди.',
                     'Возражение без объяснения не принимается.'), 'error')
        return redirect(request.referrer or url_for('gps.orders'))

    # Числа берутся снимком на момент разбора, а не ссылкой на пересчитываемое.
    day = order.actual_date or order.planned_date
    wialon_by_equipment = _wialon_by_equipment({order.equipment_id})
    aggregates, sites_by_key = {}, defaultdict(list)
    for wialon_id in wialon_by_equipment.get(order.equipment_id) or []:
        aggregate = GpsDailyAggregate.query.filter_by(
            wialon_id=wialon_id, work_date=day).first()
        if aggregate is not None:
            aggregates[(wialon_id, day)] = aggregate
        for site in GpsWorkPolygon.query.filter_by(wialon_id=wialon_id,
                                                   work_date=day):
            sites_by_key[(wialon_id, day)].append(site)
    row = reconcile_orders([order], wialon_by_equipment, aggregates,
                           sites_by_key)[0]

    db.session.add(GpsVerdict(
        work_order_id=order.id, work_date=day, wialon_id=row['wialon_id'],
        unit=order.unit or '', base_quantity=row['base'], fact_ha=row['fact'],
        deviation=row['deviation'], verdict=row['verdict'],
        no_data_reason=row['reason'], decision=decision, comment=comment,
        reviewed_by=current_user.id, reviewed_at=datetime.utcnow()))
    db.session.commit()
    flash(_gps_t('Разбор ёзиб олинди.', 'Разбор записан.'), 'success')
    return redirect(request.referrer or url_for('gps.orders'))


@gps_bp.route('/orders')
@module_required('wialon')
@login_required
def orders():
    """Сверка нарядов: план оператора против факта, посчитанного по треку."""
    today = date_cls.today()
    date_to = _parse_date(request.args.get('to')) or today
    date_from = _parse_date(request.args.get('from')) or (date_to - timedelta(days=13))
    only_problems = request.args.get('problems') == '1'
    only_unreviewed = request.args.get('unreviewed') == '1'

    query = (WorkOrder.query
             .filter(WorkOrder.status != WO_STATUS_CANCELLED)
             .filter(db.or_(
                 db.and_(WorkOrder.actual_date.isnot(None),
                         WorkOrder.actual_date >= date_from,
                         WorkOrder.actual_date <= date_to),
                 db.and_(WorkOrder.actual_date.is_(None),
                         WorkOrder.planned_date >= date_from,
                         WorkOrder.planned_date <= date_to))))
    if not current_user.is_admin:
        # [REASON]: тот же охват организаций, что и везде в приложении.
        # Сверка не имеет права показать наряд организации, которую человеку
        # видеть не положено.
        query = query.filter(
            WorkOrder.organization_id.in_(current_user.get_org_ids() or [0]))
    orders_list = query.order_by(WorkOrder.planned_date.desc(),
                                 WorkOrder.number.desc()).limit(500).all()

    wialon_by_equipment = _wialon_by_equipment(
        {o.equipment_id for o in orders_list})
    keys = set()
    for order in orders_list:
        day = order.actual_date or order.planned_date
        for wialon_id in wialon_by_equipment.get(order.equipment_id) or []:
            keys.add((wialon_id, day))

    aggregates, sites_by_key = {}, defaultdict(list)
    if keys:
        wanted_units = {wialon_id for wialon_id, _ in keys}
        wanted_days = {day for _, day in keys}
        for row in (GpsDailyAggregate.query
                    .filter(GpsDailyAggregate.wialon_id.in_(wanted_units),
                            GpsDailyAggregate.work_date.in_(wanted_days))):
            aggregates[(row.wialon_id, row.work_date)] = row
        for row in (GpsWorkPolygon.query
                    .filter(GpsWorkPolygon.wialon_id.in_(wanted_units),
                            GpsWorkPolygon.work_date.in_(wanted_days))):
            sites_by_key[(row.wialon_id, row.work_date)].append(row)

    rows = reconcile_orders(orders_list, wialon_by_equipment, aggregates,
                            sites_by_key)
    reviews = _latest_reviews([o.id for o in orders_list])
    for row in rows:
        row['review'] = reviews.get(row['order'].id)
    counters = Counter(row['verdict'] for row in rows)
    # [REASON]: считается ДО сужения показа. «Сколько всего требует внимания»
    # и «сколько сейчас на экране» -- разные числа, и первое не должно
    # меняться от того, какие галочки нажаты.
    needs_attention = [row for row in rows if row['verdict'] != VERDICT_OK]
    unreviewed = sum(1 for row in needs_attention if row['review'] is None)
    if only_problems:
        rows = needs_attention
    if only_unreviewed:
        rows = [row for row in rows
                if row['verdict'] != VERDICT_OK and row['review'] is None]

    return render_template(
        'gps/orders.html', rows=rows, counters=counters,
        date_from=date_from, date_to=date_to, only_problems=only_problems,
        only_unreviewed=only_unreviewed, unreviewed=unreviewed,
        no_data_reasons=NO_DATA_REASONS, reason_labels=REASON_LABELS,
        total=len(orders_list))


@gps_bp.route('/fact')
@module_required('wialon')
@login_required
def fact():
    skipped = _excluded_units()
    day_query = db.session.query(GpsDailyAggregate.work_date)
    aggregate_query = GpsDailyAggregate.query
    if skipped:
        ids = list(skipped)
        day_query = day_query.filter(~GpsDailyAggregate.wialon_id.in_(ids))
        aggregate_query = aggregate_query.filter(
            ~GpsDailyAggregate.wialon_id.in_(ids))
    days = [row[0] for row in day_query.distinct()
            .order_by(GpsDailyAggregate.work_date.desc()).all()]
    day = _parse_date(request.args.get('date'))
    if day is None:
        day = days[0] if days else date_cls.today()

    # [REASON]: сутки фильтруются тоже, а не только список машин. День, в
    # котором остались одни легковые, иначе стоял бы в выборе даты и открывался
    # пустым -- человек искал бы пропавшую машину там, где её не было.
    aggregates = (aggregate_query.filter_by(work_date=day)
                  .order_by(GpsDailyAggregate.wialon_id).all())
    names = _machine_names({a.wialon_id for a in aggregates})
    aggregates = _by_machine_name(aggregates, names)

    unit_id = None
    asked_unit = (request.args.get('unit') or '').strip()
    if asked_unit.isdigit():
        unit_id = int(asked_unit)
    if unit_id is None or all(a.wialon_id != unit_id for a in aggregates):
        # [REASON]: по умолчанию открывается машина, у которой в этот день
        # ЕСТЬ что показать. Открывать первую по номеру значило бы в половине
        # случаев встречать человека пустым экраном при непустых сутках.
        published = [a for a in aggregates if a.reason is None]
        unit_id = (published or aggregates)[0].wialon_id if aggregates else None

    aggregate = next((a for a in aggregates if a.wialon_id == unit_id), None)
    sites = []
    # [REASON]: у спецтехники участков нет по правилу, а не по случаю. Полигоны,
    # оставшиеся от расчёта до решения 28.09, расчёт намеренно не удаляет
    # (gps/daily.py, write_track_only: на них могут быть ответы оператора), и
    # показать их значило бы вернуть на экран те самые гектары, которые
    # владелец велел не считать.
    if aggregate is not None and aggregate.reason != REASON_TRACK_ONLY:
        sites = (GpsWorkPolygon.query
                 .filter_by(work_date=day, wialon_id=unit_id)
                 .order_by(GpsWorkPolygon.site_number).all())
    shapes, svg_width, svg_height = _svg_shapes(sites)

    # ── A2: карта суток -- трек, участки, контуры их полей ──
    is_ru = getattr(g, 'lang', 'uz') == 'ru'
    points = _day_points(unit_id, day) if aggregate is not None else []
    contour_ids = sorted({s.contour_id for s in sites if s.contour_id})
    contours = (FieldContour.query.filter(FieldContour.id.in_(contour_ids))
                .order_by(FieldContour.id).all() if contour_ids else [])
    layers = map_layers(points, sites, contours, is_ru)
    key_file = current_app.config.get('MAP_ESRI_KEY_FILE')
    instance_file = current_app.config.get('MAP_COPERNICUS_INSTANCE_FILE')
    map_data = ({'base': vs_map.base_layers(is_ru, key_file, instance_file,
                                            day=day),
                 'layers': layers} if layers else None)

    return render_template(
        'gps/fact.html',
        days=days, day=day, aggregates=aggregates, aggregate=aggregate,
        unit_id=unit_id, names=names, sites=sites,
        shapes=shapes, svg_width=svg_width, svg_height=svg_height,
        reason_labels=REASON_LABELS,
        answered=sum(1 for s in sites if s.operator_label),
        total_ha=round(sum(s.area_ha or 0 for s in sites), 2),
        map_data=map_data,
        has_track=any(layer['kind'] == 'track' for layer in layers),
        satellite=vs_map.satellite_configured(key_file, instance_file),
        sharp_on=bool(vs_map.esri_key(key_file)),
        fresh_on=bool(vs_map.copernicus_instance(instance_file)[0]),
        summary=track_summary(points) if points else None,
    )


@gps_bp.route('/fact/answer', methods=['POST'])
@module_required('wialon')
@login_required
def fact_answer():
    site = GpsWorkPolygon.query.get_or_404(request.form.get('site_id', type=int))
    label = (request.form.get('label') or '').strip()
    if label not in GPS_OPERATOR_LABELS and label != '':
        abort(400)
    # [REASON]: пустая метка -- это снятие ответа, а не ответ «ничего». Человек
    # ошибся кнопкой, и вернуть участок в «без ответа» он обязан иметь право:
    # обучающий набор с ответом, который никто не хотел давать, хуже набора
    # поменьше.
    site.operator_label = label or None
    site.decided_at = datetime.utcnow() if label else None
    db.session.commit()
    flash(_gps_t('Жавоб сақланди.', 'Ответ сохранён.') if label
          else _gps_t('Жавоб олиб ташланди.', 'Ответ снят.'), 'success')
    return redirect(url_for('gps.fact', date=site.work_date.isoformat(),
                            unit=site.wialon_id))
