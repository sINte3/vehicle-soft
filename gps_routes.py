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

from collections import Counter, defaultdict
from datetime import date as date_cls, datetime, timedelta

from flask import (Blueprint, abort, flash, g, redirect, render_template,
                   request, url_for)
from flask_login import current_user, login_required

from models import (
    db,
    Equipment,
    GPS_LABEL_PASSAGE,
    GPS_OPERATOR_LABELS,
    GpsDailyAggregate,
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


# ── Сверка наряда: план против факта GPS ─────────────────────────────────────

# [REASON]: допуски заданы владельцем (вопрос В-2, закрыт 2026-08-12) и здесь
# только записаны, не выведены. Абсолютная добавка обязательна и не является
# украшением: ручной ввод округляется сеткой 0,5 га, и на поле 2 га одно
# округление даёт ±12% — процентный допуск в одиночку залил бы очередь
# ложными «жёлтыми».
UNIT_GA = 'ga'
TOLERANCE_GA_OK = (0.10, 0.3)      # доля, абсолютная добавка в га
TOLERANCE_GA_WARN = (0.20, 0.5)

VERDICT_OK = 'ok'
VERDICT_WARN = 'warn'
VERDICT_FAIL = 'fail'
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


def verdict_for_ga(base, fact):
    """Светофор по гектарам. Возвращает (вердикт, расхождение, доля).

    Сравнивается с тем объёмом, который закрыл человек, а не с планом, если
    закрытый есть: сверяется факт против того, за что выставят счёт.
    """
    deviation = fact - base
    # [REASON]: округление ДО сравнения, иначе граница решается ошибкой
    # представления. 2,0 - 1,7 в двоичной плавающей точке даёт
    # 0.30000000000000004, и расхождение ровно в допуск (0,3 га) уезжало в
    # «жёлтое». Шесть знаков -- это 0,01 квадратного метра, на четыре порядка
    # мельче любого реального разрешения метода, и граница ведёт себя так, как
    # написано в допуске: «не больше» значит не больше.
    size = round(abs(deviation), 6)
    share = (deviation / base) if base else None
    if size <= round(max(TOLERANCE_GA_OK[0] * abs(base), TOLERANCE_GA_OK[1]), 6):
        return VERDICT_OK, deviation, share
    if size <= round(max(TOLERANCE_GA_WARN[0] * abs(base), TOLERANCE_GA_WARN[1]), 6):
        return VERDICT_WARN, deviation, share
    return VERDICT_FAIL, deviation, share


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


@gps_bp.route('/orders')
@module_required('wialon')
@login_required
def orders():
    """Сверка нарядов: план оператора против факта, посчитанного по треку."""
    today = date_cls.today()
    date_to = _parse_date(request.args.get('to')) or today
    date_from = _parse_date(request.args.get('from')) or (date_to - timedelta(days=13))
    only_problems = request.args.get('problems') == '1'

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
    counters = Counter(row['verdict'] for row in rows)
    if only_problems:
        rows = [row for row in rows if row['verdict'] != VERDICT_OK]

    return render_template(
        'gps/orders.html', rows=rows, counters=counters,
        date_from=date_from, date_to=date_to, only_problems=only_problems,
        no_data_reasons=NO_DATA_REASONS, reason_labels=REASON_LABELS,
        total=len(orders_list))


@gps_bp.route('/fact')
@module_required('wialon')
@login_required
def fact():
    days = [row[0] for row in db.session.query(GpsDailyAggregate.work_date)
            .distinct().order_by(GpsDailyAggregate.work_date.desc()).all()]
    day = _parse_date(request.args.get('date'))
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
