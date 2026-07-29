# -*- coding: utf-8 -*-
"""drones.py -- drones module blueprint.

Route map:
  GET  /drones/                 -- flight list: server-side pagination
                                   (50/page), filters by date range, machine
                                   and region. Correct on an empty table.
  GET  /drones/units            -- the 15 machines with their nickname
                                   aliases grouped.
  POST /drones/api/flight_sync  -- ingest endpoint for raw DJI flight
                                   payloads (DRONE-002). Token-authenticated
                                   via DRONE_API_TOKEN (deny-by-default),
                                   CSRF-exempt in app.py:is_csrf_exempt().

Browser routes are decorated with @module_required('drones'): the admin-UI
permission toggles are enforced at the route, not only at the sidebar link.
The ingest endpoint is a machine API authenticated by token in the request
body (same convention as the Topaz fuel sync), not by a browser session.

Out of scope here: the Playwright collector (DRONE-003), reporting screens
and Excel (DRONE-004).
"""

import json

from datetime import datetime, timedelta

from flask import Blueprint, current_app, jsonify, render_template, request, g
from flask_login import current_user
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import joinedload

from ingest_common import verify_api_token, extract_token
from models import (
    db,
    DroneUnit,
    DroneNickname,
    DroneFlight,
    DroneSyncLog,
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


# ─── Ingest endpoint (DRONE-002) ──────────────────────────────────────────────

# [REASON]: the collector chunks its uploads; a hard cap keeps one request
# from ballooning memory and the SQLite write transaction. 1000 > the
# collector's default chunk of 500 with headroom.
DRONE_SYNC_MAX_BATCH = 1000

DRONE_SYNC_KINDS = ('backfill', 'incremental', 'replay')

# Error text on the sync log keeps at most this many per-row messages.
DRONE_SYNC_MAX_ERRORS_LOGGED = 50


def _drone_normalize_nickname(nickname):
    """Lowercase, ALL whitespace removed -- must match the migration seed."""
    return ''.join(nickname.lower().split())


def _drone_region_from_location(location):
    """Region from the reverse-geocoded address, rule verified on 10 385 rows.

    [REASON]: split on commas and take the LAST segment containing the word
    'Region'; when none does, use 'Tashkent' if a segment equals 'Tashkent';
    otherwise NULL. Naive 'second-to-last segment' splitting is wrong: in
    roughly a third of the rows that segment is a postal code. Measured
    coverage on the sample is 100 % (10 381 by the Region rule + 4 by the
    Tashkent fallback). See docs/DRONES_DOMAIN.md section 4.
    """
    if not location or not isinstance(location, str):
        return None
    segments = [seg.strip() for seg in location.split(',')]
    for seg in reversed(segments):
        if 'Region' in seg:
            return seg
    for seg in segments:
        if seg == 'Tashkent':
            return 'Tashkent'
    return None


def _drone_num(value):
    """float(value) or None -- missing and non-numeric both become None."""
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _drone_int(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _drone_utc(value):
    """Unix seconds -> naive UTC datetime, None when missing or non-numeric."""
    ts = _drone_num(value)
    if ts is None:
        return None
    try:
        return datetime.utcfromtimestamp(ts)
    except (OverflowError, OSError, ValueError):
        return None


def _drone_text(value, limit):
    if value is None:
        return None
    text = str(value)
    return text[:limit] if text else None


def _drone_nickname_maps():
    """Build the two-step resolution maps in one query.

    Resolution order (drones track): exact match on nickname; on a miss,
    match on normalized; if the normalized match points at more than one
    drone_unit_id, the nickname is UNRESOLVED rather than guessed.
    """
    exact = {}
    normalized = {}
    for nick in DroneNickname.query.all():
        exact[nick.nickname] = nick.drone_unit_id
        normalized.setdefault(nick.normalized, set()).add(nick.drone_unit_id)
    return exact, normalized


def _drone_resolve_unit(nickname, exact, normalized):
    if not nickname:
        return None
    unit_id = exact.get(nickname)
    if unit_id is not None:
        return unit_id
    candidates = normalized.get(_drone_normalize_nickname(nickname))
    if candidates and len(candidates) == 1:
        return next(iter(candidates))
    return None


@drones_bp.route('/api/flight_sync', methods=['POST'])
def api_flight_sync():
    payload = request.get_json(force=True, silent=True)
    token = extract_token(payload)
    # [REASON]: a missing DRONE_API_TOKEN must DENY, never accept --
    # verify_api_token already implements that contract; no fallback here.
    if not verify_api_token(token, current_app.config.get('DRONE_API_TOKEN')):
        return jsonify(error='unauthorized'), 401

    flights = payload.get('flights')
    if not isinstance(flights, list):
        return jsonify(error='flights must be a list'), 400
    if len(flights) > DRONE_SYNC_MAX_BATCH:
        return jsonify(error='batch too large: %d flights, the cap is %d '
                             'per request -- send chunks'
                             % (len(flights), DRONE_SYNC_MAX_BATCH)), 413
    kind = payload.get('kind')
    if kind not in DRONE_SYNC_KINDS:
        return jsonify(error='kind must be one of %s'
                             % ', '.join(DRONE_SYNC_KINDS)), 400

    log = DroneSyncLog(
        kind=kind,
        period_from=_drone_parse_date(payload.get('period_from')),
        period_to=_drone_parse_date(payload.get('period_to')),
        started_at=datetime.utcnow(),
        status='running',
        records_seen=len(flights),
        source_ip=request.remote_addr,
    )
    db.session.add(log)
    # Commit the running row first: a crash mid-batch must leave a visible
    # 'running' log, not nothing.
    db.session.commit()

    new = duplicates = unresolved = errors = 0
    error_lines = []

    try:
        exact, normalized = _drone_nickname_maps()

        # [REASON]: dedup on dji_flight_id -- the existing ids for the whole
        # batch are read in ONE query; the per-row IntegrityError guard below
        # stays as well, because two collector runs may overlap.
        candidate_ids = []
        for flight in flights:
            if isinstance(flight, dict):
                fid = _drone_int(flight.get('id'))
                if fid is not None:
                    candidate_ids.append(fid)
        existing = set()
        if candidate_ids:
            rows = (db.session.query(DroneFlight.dji_flight_id)
                    .filter(DroneFlight.dji_flight_id.in_(candidate_ids))
                    .all())
            existing = {row[0] for row in rows}

        for position, flight in enumerate(flights):
            try:
                if not isinstance(flight, dict):
                    raise ValueError('flight #%d is not an object' % position)
                fid = _drone_int(flight.get('id'))
                if fid is None:
                    raise ValueError('flight #%d has no numeric id'
                                     % position)
                if fid in existing:
                    duplicates += 1
                    continue
                started_at = _drone_utc(flight.get('start_timestamp'))
                if started_at is None:
                    raise ValueError('flight id=%d has no valid '
                                     'start_timestamp' % fid)

                nickname = _drone_text(flight.get('nickname'), 100)
                unit_id = _drone_resolve_unit(nickname, exact, normalized)

                area = _drone_num(flight.get('new_work_area'))
                spray = _drone_num(flight.get('spray_usage'))
                sow = _drone_num(flight.get('sow_usage'))
                manual = flight.get('manual_mode')

                row = DroneFlight(
                    dji_flight_id=fid,
                    # [REASON]: an unknown nickname never rejects a flight --
                    # a hardcoded nickname dictionary cost the previous
                    # system 2 036 flights, and a rejected flight is
                    # unrecoverable once the DJI history window rolls past
                    # it. Unresolved rows are stored with drone_unit_id NULL
                    # and nickname_raw filled, and can be re-attributed after
                    # the alias map learns the spelling.
                    drone_unit_id=unit_id,
                    nickname_raw=nickname,
                    serial_number=_drone_text(flight.get('serial_number'), 50),
                    flyer_name=_drone_text(flight.get('flyer_name'), 100),
                    team_name=_drone_text(flight.get('team_name'), 100),
                    started_at=started_at,
                    finished_at=_drone_utc(flight.get('end_timestamp')),
                    work_seconds=_drone_int(flight.get('work_time_seconds')),
                    # Units, verified on the sample: new_work_area is m2,
                    # spray_usage is ml, sow_usage is GRAMS.
                    area_ha=(area / 10000.0) if area is not None else 0,
                    spray_liters=(spray / 1000.0) if spray is not None else None,
                    sow_kg=(sow / 1000.0) if sow is not None else None,
                    usage_type=_drone_int(flight.get('usage_type')),
                    mode_name=_drone_int(flight.get('mode_name')),
                    manual_mode=bool(manual) if manual is not None else None,
                    work_speed=_drone_num(flight.get('work_speed')),
                    spray_width=_drone_num(flight.get('spray_width')),
                    radar_height=_drone_num(flight.get('radar_height')),
                    lat=_drone_num(flight.get('lat')),
                    lng=_drone_num(flight.get('lng')),
                    location_text=_drone_text(flight.get('location'), 500),
                    # [REASON]: region is computed at ingest and stored, so
                    # reports never re-parse tens of thousands of address
                    # strings; location_text keeps the source verbatim.
                    region=_drone_region_from_location(flight.get('location')),
                    raw_json=json.dumps(flight, ensure_ascii=False),
                    sync_log_id=log.id,
                    ingested_at=datetime.utcnow(),
                )
                try:
                    with db.session.begin_nested():
                        db.session.add(row)
                except IntegrityError:
                    # Same dji_flight_id landed from an overlapping run (or
                    # twice in one batch) between the dedup read and here.
                    duplicates += 1
                    continue
                existing.add(fid)
                if unit_id is None:
                    unresolved += 1
                else:
                    new += 1
            except Exception as exc:
                # [REASON]: a row that fails to parse increments errors and
                # is recorded, but never aborts the batch -- the rest of the
                # chunk must still land.
                errors += 1
                if len(error_lines) < DRONE_SYNC_MAX_ERRORS_LOGGED:
                    error_lines.append(str(exc))

        log.records_new = new
        log.records_duplicate = duplicates
        log.records_unresolved = unresolved
        log.records_error = errors
        log.error_text = '\n'.join(error_lines) if error_lines else None
        log.status = 'ok'
        log.finished_at = datetime.utcnow()
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        log.records_new = new
        log.records_duplicate = duplicates
        log.records_unresolved = unresolved
        log.records_error = errors
        log.status = 'error'
        log.error_text = str(exc)
        log.finished_at = datetime.utcnow()
        db.session.commit()
        return jsonify(status='error', log_id=log.id, error=str(exc)), 500

    return jsonify(status='ok', log_id=log.id, seen=len(flights), new=new,
                   duplicates=duplicates, unresolved=unresolved,
                   errors=errors)


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
