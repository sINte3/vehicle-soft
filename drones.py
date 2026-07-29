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

import io
import json

from datetime import datetime, timedelta

from flask import (Blueprint, current_app, flash, jsonify, redirect,
                   render_template, request, send_file, url_for, g)
from flask_login import current_user
from sqlalchemy import func
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
    """Ingest a batch of raw DJI flight payloads.

    Counter semantics (also stated on the DroneSyncLog model): every seen
    flight lands in exactly one of new / duplicates / errors, so

        seen = new + duplicates + errors

    `new` counts every row that entered the table -- it is the answer to
    "did data arrive". `unresolved` is a SUBSET of `new`, not a sibling
    bucket (unresolved <= new): rows stored with an unrecognised nickname
    and drone_unit_id NULL. A batch-level failure rolls everything back
    and reports new = duplicates = unresolved = 0, errors = seen.
    """
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
                # [REASON]: `new` counts every row that entered the table,
                # whether or not its nickname resolved -- it is the answer
                # to "did data arrive". `unresolved` is a SUBSET of `new`,
                # not a sibling bucket: an unattributed flight is still a
                # stored flight, and it becomes attributed later without
                # the log changing.
                new += 1
                if unit_id is None:
                    unresolved += 1
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
        # [REASON]: the rollback above discarded every row of this batch,
        # so the counters must say so. Recording the in-flight values here
        # would leave a log claiming N new rows that do not exist -- worse
        # than no log at all. records_seen stays: what was submitted is
        # still true.
        log.records_new = 0
        log.records_duplicate = 0
        log.records_unresolved = 0
        log.records_error = len(flights)
        log.status = 'error'
        log.error_text = ('batch rolled back, nothing was stored: %s' % exc)
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


# ─── Summary and Excel exports (DRONE-004) ────────────────────────────────────

# [REASON]: a silently truncated export reads as complete data on the other
# side of an e-mail; above the cap the route refuses with a clear message
# asking to narrow the period instead.
DRONE_FLIGHTS_XLSX_CAP = 50000


def _drone_filters_from_args(args, default_current_month):
    """Parse the shared filter set (dates, machine, region) from a query
    string, mirroring index(): the same _drone_parse_date and the same UTC+5
    day-boundary shift -- one convention, not two.

    When default_current_month is true and NEITHER date parameter is present
    in the query string at all, the current calendar month (in UTC+5) is
    preselected. Parameters that are present but empty mean "no bound" --
    that is how the operator asks for all time by clearing the inputs.
    """
    has_date_args = ('date_from' in args) or ('date_to' in args)
    date_from_s = (args.get('date_from') or '').strip()
    date_to_s = (args.get('date_to') or '').strip()
    date_from = _drone_parse_date(date_from_s)
    date_to = _drone_parse_date(date_to_s)
    if default_current_month and not has_date_args:
        today_local = (datetime.utcnow() + DRONE_DISPLAY_UTC_OFFSET).date()
        date_from = today_local.replace(day=1)
        if today_local.month == 12:
            date_to = today_local.replace(day=31)
        else:
            date_to = (today_local.replace(month=today_local.month + 1, day=1)
                       - timedelta(days=1))
        date_from_s = date_from.isoformat()
        date_to_s = date_to.isoformat()
    return {
        'date_from': date_from,
        'date_to': date_to,
        'date_from_s': date_from_s,
        'date_to_s': date_to_s,
        'unit_id': args.get('unit_id', type=int),
        'region': (args.get('region') or '').strip(),
    }


def _drone_flight_conditions(filters):
    """Filter conditions over DroneFlight for the parsed filter set."""
    conds = []
    if filters['date_from']:
        conds.append(DroneFlight.started_at >=
                     datetime.combine(filters['date_from'],
                                      datetime.min.time())
                     - DRONE_DISPLAY_UTC_OFFSET)
    if filters['date_to']:
        conds.append(DroneFlight.started_at <=
                     datetime.combine(filters['date_to'],
                                      datetime.max.time())
                     - DRONE_DISPLAY_UTC_OFFSET)
    if filters['unit_id']:
        conds.append(DroneFlight.drone_unit_id == filters['unit_id'])
    if filters['region']:
        conds.append(DroneFlight.region == filters['region'])
    return conds


def _drone_link_args(filters):
    """Query args for drill-down links and exports -- only the filters that
    are actually set, so cleared dates stay cleared in the target URL."""
    link = {}
    if filters['date_from_s']:
        link['date_from'] = filters['date_from_s']
    if filters['date_to_s']:
        link['date_to'] = filters['date_to_s']
    if filters['unit_id']:
        link['unit_id'] = filters['unit_id']
    if filters['region']:
        link['region'] = filters['region']
    return link


def _drone_share(area, total_area):
    if not total_area:
        return 0.0
    return round(area * 100.0 / total_area, 1)


def _drone_summary_data(conds):
    """Aggregate the filtered flights for the summary page and the exports.

    [REASON]: every breakdown must reconcile with the grand total, and
    unattributed flights must appear as their own visible line.
    drone_flights.drone_unit_id is nullable BY DESIGN -- a flight whose DJI
    nickname is not yet in the alias map is stored with NULL rather than
    rejected. A per-machine table that simply groups by machine silently
    omits those flights and their hectares; that is precisely how the
    previous system lost 2 036 flights and 2 082 hectares into an invisible
    bucket. So: NULL drone_unit_id gets an explicit line, NULL region gets
    an explicit line, every table carries a total row, and each table's
    total is compared against the grand total -- a mismatch is surfaced on
    the page (reconciled=False), never hidden.

    Aggregation happens in SQL (func.count / func.sum / group_by), never by
    looping over loaded rows: the table holds ~29 000 flights after the
    historical backfill and keeps growing.
    """
    totals_row = (db.session.query(
        func.count(DroneFlight.id),
        func.coalesce(func.sum(DroneFlight.area_ha), 0.0),
        func.coalesce(func.sum(DroneFlight.work_seconds), 0),
        func.coalesce(func.sum(DroneFlight.spray_liters), 0.0),
        func.coalesce(func.sum(DroneFlight.sow_kg), 0.0),
    ).filter(*conds).one())
    zero_area = (db.session.query(func.count(DroneFlight.id))
                 .filter(*conds).filter(DroneFlight.area_ha == 0).scalar())

    totals = {
        'flights': totals_row[0] or 0,
        'area_ha': float(totals_row[1] or 0.0),
        'hours': (totals_row[2] or 0) / 3600.0,
        'spray_liters': float(totals_row[3] or 0.0),
        'sow_kg': float(totals_row[4] or 0.0),
        'zero_area': zero_area or 0,
    }
    total_area = totals['area_ha']

    def reconciled(flights_sum, area_sum):
        return (flights_sum == totals['flights']
                and abs(area_sum - totals['area_ha']) < 0.005)

    # По машинам -- ordered by machine number, NULL as its own line.
    machine_groups = (db.session.query(
        DroneFlight.drone_unit_id,
        func.count(DroneFlight.id),
        func.coalesce(func.sum(DroneFlight.area_ha), 0.0),
    ).filter(*conds).group_by(DroneFlight.drone_unit_id).all())
    unit_numbers = {u.id: u.number for u in DroneUnit.query.all()}
    machine_rows = []
    machine_unattributed = None
    for unit_id, flights, area in machine_groups:
        area = float(area or 0.0)
        if unit_id is None:
            machine_unattributed = {'flights': flights, 'area': area,
                                    'share': _drone_share(area, total_area)}
        else:
            machine_rows.append({
                'unit_id': unit_id,
                'number': unit_numbers.get(unit_id),
                'flights': flights,
                'area': area,
                'share': _drone_share(area, total_area),
            })
    machine_rows.sort(key=lambda r: (r['number'] is None, r['number']))
    m_flights = (sum(r['flights'] for r in machine_rows)
                 + (machine_unattributed['flights']
                    if machine_unattributed else 0))
    m_area = (sum(r['area'] for r in machine_rows)
              + (machine_unattributed['area']
                 if machine_unattributed else 0.0))
    by_machine = {
        'rows': machine_rows,
        'unattributed': machine_unattributed,
        'total': {'flights': m_flights, 'area': m_area},
        'reconciled': reconciled(m_flights, m_area),
    }

    # По областям -- ordered by hectares descending, NULL as its own line.
    region_groups = (db.session.query(
        DroneFlight.region,
        func.count(DroneFlight.id),
        func.coalesce(func.sum(DroneFlight.area_ha), 0.0),
    ).filter(*conds).group_by(DroneFlight.region).all())
    region_rows = []
    region_undetermined = None
    for region, flights, area in region_groups:
        area = float(area or 0.0)
        if region is None:
            region_undetermined = {'flights': flights, 'area': area,
                                   'share': _drone_share(area, total_area)}
        else:
            region_rows.append({
                'region': region,
                'flights': flights,
                'area': area,
                'share': _drone_share(area, total_area),
            })
    region_rows.sort(key=lambda r: r['area'], reverse=True)
    r_flights = (sum(r['flights'] for r in region_rows)
                 + (region_undetermined['flights']
                    if region_undetermined else 0))
    r_area = (sum(r['area'] for r in region_rows)
              + (region_undetermined['area'] if region_undetermined else 0.0))
    by_region = {
        'rows': region_rows,
        'undetermined': region_undetermined,
        'total': {'flights': r_flights, 'area': r_area},
        'reconciled': reconciled(r_flights, r_area),
    }

    # По типам работ -- spraying and sowing separately; litres belong only
    # to the spraying row and kilograms only to the sowing row (different
    # substances in different units are NEVER added together); any other
    # usage_type value gets its own row instead of being folded into either.
    usage_groups = (db.session.query(
        DroneFlight.usage_type,
        func.count(DroneFlight.id),
        func.coalesce(func.sum(DroneFlight.area_ha), 0.0),
        func.coalesce(func.sum(DroneFlight.spray_liters), 0.0),
        func.coalesce(func.sum(DroneFlight.sow_kg), 0.0),
    ).filter(*conds).group_by(DroneFlight.usage_type).all())
    usage_labels = _drone_usage_labels()
    usage_rows = []
    for usage_type, flights, area, liters, kg in usage_groups:
        area = float(area or 0.0)
        label = usage_labels.get(usage_type)
        if label is None:
            label = (_drone_t('Номаълум тур', 'Неизвестный тип')
                     + ': ' + str(usage_type))
        usage_rows.append({
            'usage_type': usage_type,
            'label': label,
            'flights': flights,
            'area': area,
            'share': _drone_share(area, total_area),
            'spray_liters': float(liters or 0.0) if usage_type == 0 else None,
            'sow_kg': float(kg or 0.0) if usage_type == 1 else None,
        })
    usage_rows.sort(key=lambda r: (r['usage_type'] is None,
                                   r['usage_type']
                                   if r['usage_type'] is not None else 0))
    u_flights = sum(r['flights'] for r in usage_rows)
    u_area = sum(r['area'] for r in usage_rows)
    by_usage = {
        'rows': usage_rows,
        'total': {'flights': u_flights, 'area': u_area},
        'reconciled': reconciled(u_flights, u_area),
    }

    # По месяцам -- calendar months in the operator's timezone (UTC+5),
    # ascending. The shift must match the display shift exactly: a flight at
    # 19:30 UTC on the 31st belongs to the next month for the operator.
    month_expr = func.strftime(
        '%Y-%m', func.datetime(DroneFlight.started_at, '+5 hours'))
    month_groups = (db.session.query(
        month_expr,
        func.count(DroneFlight.id),
        func.coalesce(func.sum(DroneFlight.area_ha), 0.0),
    ).filter(*conds).group_by(month_expr).order_by(month_expr).all())
    month_rows = [{
        'month': month,
        'flights': flights,
        'area': float(area or 0.0),
        'share': _drone_share(float(area or 0.0), total_area),
    } for month, flights, area in month_groups]
    mo_flights = sum(r['flights'] for r in month_rows)
    mo_area = sum(r['area'] for r in month_rows)
    by_month = {
        'rows': month_rows,
        'total': {'flights': mo_flights, 'area': mo_area},
        'reconciled': reconciled(mo_flights, mo_area),
    }

    return {
        'totals': totals,
        'by_machine': by_machine,
        'by_region': by_region,
        'by_usage': by_usage,
        'by_month': by_month,
    }


@drones_bp.route('/summary')
@module_required('drones')
def summary():
    filters = _drone_filters_from_args(request.args,
                                       default_current_month=True)
    conds = _drone_flight_conditions(filters)
    data = _drone_summary_data(conds)

    units = DroneUnit.query.order_by(DroneUnit.number).all()
    regions = [row[0] for row in
               db.session.query(DroneFlight.region)
               .filter(DroneFlight.region.isnot(None))
               .distinct().order_by(DroneFlight.region).all()]

    return render_template(
        'drones/summary.html',
        data=data,
        filters=filters,
        link_args=_drone_link_args(filters),
        units=units,
        regions=regions,
    )
