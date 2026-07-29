# -*- coding: utf-8 -*-
"""drones.py -- DRONE-001: drones module blueprint (read-only foundation).

Route map in this increment:
  GET /drones/       -- flight list: server-side pagination (50/page),
                        filters by date range, machine and region. The list
                        is correct on an empty table and says so in both
                        languages.
  GET /drones/units  -- the 15 machines with their nickname aliases grouped.

Every route is decorated with @module_required('drones'): the admin-UI
permission toggles are enforced at the route, not only at the sidebar link.

Out of scope here: the ingest endpoint (DRONE-002 adds it to this
blueprint), the Playwright collector (DRONE-003), reporting screens and
Excel (DRONE-004).
"""

from datetime import datetime, timedelta

from flask import Blueprint, render_template, request, g
from flask_login import current_user
from sqlalchemy.orm import joinedload

from models import (
    db,
    DroneUnit,
    DroneNickname,
    DroneFlight,
    module_required,
)

drones_bp = Blueprint('drones', __name__, url_prefix='/drones')

# [REASON]: 150 flights a day in season, peak 476 (drones track) --
# server-side pagination is mandatory from the first line; 50 matches the
# SmartFarm page size the operators already know.
DRONE_PAGE_SIZE = 50

# [REASON]: flight timestamps are stored in UTC (unix seconds in the DJI
# payload); the business reads them in UTC+5. The offset is applied at
# render time only -- storage and filtering stay consistent in the DB.
DRONE_DISPLAY_UTC_OFFSET = timedelta(hours=5)


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _drone_lang():
    lang = getattr(g, 'lang', None)
    if not lang and getattr(current_user, 'is_authenticated', False):
        lang = getattr(current_user, 'language', None)
    return 'ru' if lang == 'ru' else 'uz'


def _drone_t(uz_text, ru_text):
    # Same pattern as _spare_t (spare_parts.py): route-level bilingual helper
    # for strings that are built outside templates.
    return ru_text if _drone_lang() == 'ru' else uz_text


def _drone_parse_date(value):
    if not value:
        return None
    try:
        return datetime.strptime(value.strip(), '%Y-%m-%d').date()
    except (TypeError, ValueError, AttributeError):
        return None


def _drone_fmt_dt(dt):
    """UTC-stored datetime -> display string in UTC+5, em dash when empty."""
    if not dt:
        return '—'
    return (dt + DRONE_DISPLAY_UTC_OFFSET).strftime('%d.%m.%Y %H:%M')


def _drone_usage_labels():
    return {
        0: _drone_t('Пуркаш', 'Опрыскивание'),
        1: _drone_t('Экиш', 'Сев'),
    }


# ─── Routes (read-only) ───────────────────────────────────────────────────────

@drones_bp.route('/')
@module_required('drones')
def index():
    page = request.args.get('page', 1, type=int) or 1
    if page < 1:
        page = 1
    date_from_s = (request.args.get('date_from') or '').strip()
    date_to_s = (request.args.get('date_to') or '').strip()
    unit_id = request.args.get('unit_id', type=int)
    region = (request.args.get('region') or '').strip()

    date_from = _drone_parse_date(date_from_s)
    date_to = _drone_parse_date(date_to_s)

    q = DroneFlight.query
    # [REASON]: the operator picks dates in local time (UTC+5) while
    # started_at is stored in UTC, so the day boundaries are shifted by the
    # display offset -- a flight at 02:00 local on the 20th (21:00 UTC on
    # the 19th) belongs to the 20th for the person filtering.
    if date_from:
        q = q.filter(DroneFlight.started_at >=
                     datetime.combine(date_from, datetime.min.time())
                     - DRONE_DISPLAY_UTC_OFFSET)
    if date_to:
        q = q.filter(DroneFlight.started_at <=
                     datetime.combine(date_to, datetime.max.time())
                     - DRONE_DISPLAY_UTC_OFFSET)
    if unit_id:
        q = q.filter(DroneFlight.drone_unit_id == unit_id)
    if region:
        q = q.filter(DroneFlight.region == region)

    total = q.count()
    pages = max(1, (total + DRONE_PAGE_SIZE - 1) // DRONE_PAGE_SIZE)
    if page > pages:
        page = pages
    flights = (q.options(joinedload(DroneFlight.drone_unit))
                .order_by(DroneFlight.started_at.desc())
                .offset((page - 1) * DRONE_PAGE_SIZE)
                .limit(DRONE_PAGE_SIZE)
                .all())

    units = DroneUnit.query.order_by(DroneUnit.number).all()
    regions = [row[0] for row in
               db.session.query(DroneFlight.region)
               .filter(DroneFlight.region.isnot(None))
               .distinct().order_by(DroneFlight.region).all()]

    # Filter args echoed into pagination links, only the ones actually set.
    filter_args = {}
    if date_from_s:
        filter_args['date_from'] = date_from_s
    if date_to_s:
        filter_args['date_to'] = date_to_s
    if unit_id:
        filter_args['unit_id'] = unit_id
    if region:
        filter_args['region'] = region

    return render_template(
        'drones/list.html',
        flights=flights,
        total=total,
        page=page,
        pages=pages,
        units=units,
        regions=regions,
        filters={'date_from': date_from_s, 'date_to': date_to_s,
                 'unit_id': unit_id, 'region': region},
        filter_args=filter_args,
        fmt_dt=_drone_fmt_dt,
        usage_labels=_drone_usage_labels(),
    )


@drones_bp.route('/units')
@module_required('drones')
def units():
    unit_rows = (DroneUnit.query
                 .options(joinedload(DroneUnit.organization))
                 .order_by(DroneUnit.number).all())
    # [REASON]: one query for all aliases, grouped in Python -- no per-unit
    # N+1 through the lazy='dynamic' relationship.
    nicknames_by_unit = {}
    for nick in DroneNickname.query.order_by(DroneNickname.id).all():
        nicknames_by_unit.setdefault(nick.drone_unit_id, []).append(nick)

    return render_template(
        'drones/units.html',
        units=unit_rows,
        nicknames_by_unit=nicknames_by_unit,
    )
