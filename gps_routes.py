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

from datetime import date as date_cls, datetime

from flask import (Blueprint, abort, flash, g, redirect, render_template,
                   request, url_for)
from flask_login import login_required

from models import (
    db,
    Equipment,
    GPS_OPERATOR_LABELS,
    GpsDailyAggregate,
    GpsWorkPolygon,
    VialonMapping,
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
}


def _machine_names(wialon_ids):
    """wialon_id -> человеческое имя машины, где оно известно.

    [REASON]: связка ещё не заполнена (VialonMapping.wialon_id ставит только
    сопоставление PHASE1, ручной импорт его не трогает), поэтому имя может
    отсутствовать. Тогда показывается сам wialon_id: подставить сюда имя по
    похожести номера нельзя -- шесть номеров указывают на несколько объектов,
    и у одной машины их три.
    """
    if not wialon_ids:
        return {}
    rows = (db.session.query(VialonMapping.wialon_id, VialonMapping.vialon_name,
                             Equipment.name)
            .outerjoin(Equipment, VialonMapping.equipment_id == Equipment.id)
            .filter(VialonMapping.wialon_id.in_(list(wialon_ids))).all())
    names = {}
    for wialon_id, wialon_name, equipment_name in rows:
        names[wialon_id] = equipment_name or wialon_name
    return names


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


@gps_bp.route('/fact')
@module_required('wialon')
@login_required
def fact():
    days = [row[0] for row in db.session.query(GpsDailyAggregate.work_date)
            .distinct().order_by(GpsDailyAggregate.work_date.desc()).all()]
    day = None
    asked = (request.args.get('date') or '').strip()
    if asked:
        try:
            day = datetime.strptime(asked, '%Y-%m-%d').date()
        except ValueError:
            day = None
    if day is None:
        day = days[0] if days else date_cls.today()

    aggregates = (GpsDailyAggregate.query.filter_by(work_date=day)
                  .order_by(GpsDailyAggregate.wialon_id).all())
    names = _machine_names({a.wialon_id for a in aggregates})

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
    if aggregate is not None:
        sites = (GpsWorkPolygon.query
                 .filter_by(work_date=day, wialon_id=unit_id)
                 .order_by(GpsWorkPolygon.site_number).all())
    shapes, svg_width, svg_height = _svg_shapes(sites)

    return render_template(
        'gps/fact.html',
        days=days, day=day, aggregates=aggregates, aggregate=aggregate,
        unit_id=unit_id, names=names, sites=sites,
        shapes=shapes, svg_width=svg_width, svg_height=svg_height,
        reason_labels=REASON_LABELS,
        answered=sum(1 for s in sites if s.operator_label),
        total_ha=round(sum(s.area_ha or 0 for s in sites), 2),
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
