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

from flask import (Blueprint, abort, current_app, flash, jsonify, redirect,
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
    DroneReattachRun,
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
        # [REASON]: labels are for display only. The <option> value and the
        # filter comparison stay the RAW stored string, so
        # DroneFlight.region == filters['region'] keeps working unchanged and
        # links already in circulation keep resolving.
        region_labels=_drone_region_label_map(regions),
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


# [REASON]: DRONE-007 -- stems of the stored region strings, i.e. the value
# lowercased with a trailing ' region' removed. DJI sends the region in Latin
# ('Bukhara Region') and it landed unchanged on an Uzbek Cyrillic screen and
# in Excel. This map is DISPLAY ONLY: nothing here is ever written to
# drone_flights.region, and every filter keeps comparing the raw stored value,
# so an existing bookmark or export link keeps working.
#
# Confirmed present in production data: bukhara, navoiy, qashqadaryo,
# samarqand, jizzakh, tashkent. The remaining Uzbek regions are listed too, so
# the first flight from a new region does not appear raw among translated
# neighbours. Uzbek is Cyrillic, as everywhere in this module.
DRONE_REGION_LABELS = {
    'bukhara':        ('Бухоро вилояти', 'Бухарская область'),
    'navoiy':         ('Навоий вилояти', 'Навоийская область'),
    'qashqadaryo':    ('Қашқадарё вилояти', 'Кашкадарьинская область'),
    'samarqand':      ('Самарқанд вилояти', 'Самаркандская область'),
    'jizzakh':        ('Жиззах вилояти', 'Джизакская область'),
    'tashkent':       ('Тошкент', 'Ташкент'),
    'andijan':        ('Андижон вилояти', 'Андижанская область'),
    'fergana':        ('Фарғона вилояти', 'Ферганская область'),
    'namangan':       ('Наманган вилояти', 'Наманганская область'),
    'sirdaryo':       ('Сирдарё вилояти', 'Сырдарьинская область'),
    'surxondaryo':    ('Сурхондарё вилояти', 'Сурхандарьинская область'),
    'xorazm':         ('Хоразм вилояти', 'Хорезмская область'),
    'karakalpakstan': ('Қорақалпоғистон Республикаси',
                       'Республика Каракалпакстан'),
}

DRONE_REGION_SUFFIX = ' region'


def _drone_region_label(region):
    """Display label for a stored region value, in the current language.

    [REASON]: an UNKNOWN region is returned VERBATIM, never blank and never a
    placeholder. A region that fell out of this map must still be readable and
    still be recognisable as the string the filter works on; rendering it as
    an empty cell would look exactly like the NULL-region case, which has its
    own explicit line and its own wording.
    """
    if not region:
        return region
    stem = region.strip().lower()
    if stem.endswith(DRONE_REGION_SUFFIX):
        stem = stem[:-len(DRONE_REGION_SUFFIX)].strip()
    labels = DRONE_REGION_LABELS.get(stem)
    if labels is None:
        return region
    return _drone_t(labels[0], labels[1])


def _drone_region_label_map(regions):
    """{raw value: label} for a list of raw region strings.

    Handed to the templates instead of registering a global Jinja filter --
    a global would have to be registered in app.py, which this change does
    not touch.
    """
    return {region: _drone_region_label(region) for region in regions}


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
    # [REASON]: DRONE-007 -- the units screen offers a per-alias disable
    # toggle. Without this filter the toggle would change nothing at ingest
    # and the UI would be lying about what it does. isnot(False), never
    # == True: in SQLite the column is BOOLEAN DEFAULT 1 and a legacy or
    # hand-inserted row can hold NULL, which == True would silently drop,
    # turning known spellings into unresolved flights. NULL therefore counts
    # as active, which is the behaviour that was there before. All twenty
    # rows seeded by migrate_drones_foundation_001.py carry is_active = 1, so
    # nothing changes on existing data.
    for nick in DroneNickname.query.filter(
            DroneNickname.is_active.isnot(False)).all():
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
    # [REASON]: counted for the batch-level warning below only. It is NOT a
    # sixth DroneSyncLog counter and NOT part of the JSON response: the log's
    # five counters carry a documented invariant and the response shape is
    # what the collector reads, so the signal channel here is the app log.
    new_without_region = 0
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
                region = _drone_region_from_location(flight.get('location'))

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
                    region=region,
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
                if region is None:
                    new_without_region += 1
            except Exception as exc:
                # [REASON]: a row that fails to parse increments errors and
                # is recorded, but never aborts the batch -- the rest of the
                # chunk must still land.
                errors += 1
                if len(error_lines) < DRONE_SYNC_MAX_ERRORS_LOGGED:
                    error_lines.append(str(exc))

        # [REASON]: DRONE-007 -- the region parser looks for the ENGLISH word
        # 'Region' in the reverse-geocoded address. If the DJI account
        # language ever changes, the token disappears, the column goes NULL
        # for every new row and nothing else about the run looks different:
        # the flights arrive, the counters are healthy, and the by-region
        # report quietly empties out. A whole batch with no region at all is
        # the shape of that failure, so it is said out loud. The threshold is
        # ALL of them, not some: a handful of addresses without the token is
        # ordinary, and a warning that fires on ordinary data stops being
        # read. The parsing rule itself is NOT changed -- it is verified at
        # 100 % coverage on 10 385 rows, and changing it on a guess would
        # corrupt a working column.
        if new > 0 and new_without_region == new:
            current_app.logger.warning(
                'DRONE INGEST: all %d newly stored flight(s) in this batch '
                'have region NULL. That is what a change of the DJI account '
                'language looks like -- the address no longer contains the '
                'token the region parser matches on. Check the SmartFarm '
                'account language; the parsing rule was not changed and is '
                'verified at 100%% coverage on 10385 rows. Sync log id %s.',
                new, log.id)

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


# ─── Alias management (DRONE-007) ─────────────────────────────────────────────
#
# [REASON]: DroneNickname was writable only by migration until now, so the
# forty historical 2025 spellings sitting in drone_flights.nickname_raw could
# not be mapped without a code change. Two POST routes -- add and update --
# cover the whole surface the owner needs: a new spelling, a rename, a move to
# another machine, and a disable.
#
# There is deliberately NO delete route. Deactivation is the reversible
# operation and it preserves the record that a spelling ever existed, which is
# what makes an old attribution explainable years later; deleting the row
# loses that permanently and gains nothing, because an inactive alias already
# stops resolving at ingest (_drone_nickname_maps filters on is_active).


def _drone_units_redirect():
    return redirect(url_for('drones.units'))


def _drone_nickname_ambiguous_with(normalized, drone_unit_id,
                                   exclude_id=None):
    """An ACTIVE alias normalising the same way but pointing elsewhere.

    [REASON]: `normalized` is deliberately not unique ('8 Garden U' and
    '8 GardenU' both normalise to '8gardenu'), and that is fine while every
    row of a normalized group points at one machine. When a group straddles
    two machines, _drone_resolve_unit() stops using the normalized fallback
    for the whole group and returns None rather than guessing -- so the save
    still happens, but the operator has to be told that resolution for these
    spellings is now exact-match only.
    """
    query = DroneNickname.query.filter(
        DroneNickname.normalized == normalized,
        DroneNickname.drone_unit_id != drone_unit_id,
        DroneNickname.is_active.isnot(False))
    if exclude_id is not None:
        query = query.filter(DroneNickname.id != exclude_id)
    return query.first()


def _drone_flash_ambiguity(other):
    unit = DroneUnit.query.get(other.drone_unit_id)
    number = unit.number if unit else other.drone_unit_id
    flash(_drone_t(
        'Сақланди, аммо огоҳлантириш: «%s» ёзилиши № %s машинага '
        'ишора қилади ва нормаллаштирилгандан кейин худди шундай бўлади. '
        'Бу ёзилишлар гуруҳи учун нормаллаштирилган мослик энди ноаниқ — '
        'аниқлаш фақат аниқ мослик бўйича ишлайди.',
        'Сохранено, но внимание: написание «%s» указывает на машину № %s и '
        'после нормализации совпадает с этим. Для этой группы написаний '
        'нормализованное сопоставление стало неоднозначным — распознавание '
        'будет работать только по точному совпадению.')
        % (other.nickname, number), 'warning')


def _drone_flash_duplicate(nickname):
    """Name the machine the existing spelling already points at, not a 500."""
    existing = DroneNickname.query.filter(
        DroneNickname.nickname == nickname).first()
    if existing is None:
        flash(_drone_t('«%s» ёзилиши сақланмади: у аллақачон мавжуд.',
                       'Написание «%s» не сохранено: оно уже существует.')
              % nickname, 'danger')
        return
    unit = DroneUnit.query.get(existing.drone_unit_id)
    number = unit.number if unit else existing.drone_unit_id
    flash(_drone_t(
        '«%s» ёзилиши аллақачон мавжуд ва № %s машинага бириктирилган. '
        'Ҳеч нарса ўзгартирилмади.',
        'Написание «%s» уже существует и привязано к машине № %s. '
        'Ничего не изменено.') % (nickname, number), 'danger')


@drones_bp.route('/units/nicknames/add', methods=['POST'])
@module_required('drones')
def units_nickname_add():
    """Add one raw DJI spelling and attach it to a machine."""
    # The admin-only decorators live inside create_app() and are not
    # importable from a blueprint; spare_parts.py and fuel_routes.py check the
    # flag inline for the same reason.
    if not current_user.can_edit:
        abort(403)

    nickname = (request.form.get('nickname') or '').strip()
    unit_id = request.form.get('drone_unit_id', type=int)

    if not nickname:
        flash(_drone_t('Ник бўш бўлиши мумкин эмас.',
                       'Ник не может быть пустым.'), 'warning')
        return _drone_units_redirect()

    unit = DroneUnit.query.get(unit_id) if unit_id else None
    if unit is None:
        flash(_drone_t('Машина танланмаган ёки топилмади.',
                       'Машина не выбрана или не найдена.'), 'warning')
        return _drone_units_redirect()

    # [REASON]: normalized is recomputed here with the ingest's own helper on
    # every write, so the column can never drift from the rule the resolution
    # uses -- the migration seeds it with the identical rule.
    normalized = _drone_normalize_nickname(nickname)
    ambiguous = _drone_nickname_ambiguous_with(normalized, unit.id)

    row = DroneNickname(drone_unit_id=unit.id, nickname=nickname,
                        normalized=normalized, is_active=True,
                        created_at=datetime.utcnow())
    db.session.add(row)
    try:
        db.session.commit()
    except IntegrityError:
        # nickname is UNIQUE in the database; a readable message beats a 500.
        db.session.rollback()
        _drone_flash_duplicate(nickname)
        return _drone_units_redirect()

    flash(_drone_t('«%s» ники № %s машинага қўшилди.',
                   'Ник «%s» добавлен машине № %s.')
          % (nickname, unit.number), 'success')
    if ambiguous is not None:
        _drone_flash_ambiguity(ambiguous)
    return _drone_units_redirect()


@drones_bp.route('/units/nicknames/<int:nick_id>/update', methods=['POST'])
@module_required('drones')
def units_nickname_update(nick_id):
    """Rename a spelling, move it to another machine, activate or disable it.

    One route covers all three edits of a row: they are one form on the
    screen, and splitting them would make "rename and re-attach in one go"
    two round trips that can half-fail.
    """
    if not current_user.can_edit:
        abort(403)

    row = DroneNickname.query.get(nick_id)
    if row is None:
        flash(_drone_t('Ник топилмади.', 'Ник не найден.'), 'warning')
        return _drone_units_redirect()

    nickname = (request.form.get('nickname') or '').strip()
    unit_id = request.form.get('drone_unit_id', type=int)
    # An unchecked checkbox is simply absent from the form body.
    is_active = request.form.get('is_active') is not None

    if not nickname:
        flash(_drone_t('Ник бўш бўлиши мумкин эмас.',
                       'Ник не может быть пустым.'), 'warning')
        return _drone_units_redirect()

    unit = DroneUnit.query.get(unit_id) if unit_id else None
    if unit is None:
        flash(_drone_t('Машина танланмаган ёки топилмади.',
                       'Машина не выбрана или не найдена.'), 'warning')
        return _drone_units_redirect()

    normalized = _drone_normalize_nickname(nickname)
    # Only an alias that will BE active can make a group ambiguous, and the
    # row being edited is excluded from its own check.
    ambiguous = None
    if is_active:
        ambiguous = _drone_nickname_ambiguous_with(normalized, unit.id,
                                                   exclude_id=row.id)

    row.nickname = nickname
    row.normalized = normalized
    row.drone_unit_id = unit.id
    row.is_active = is_active
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        _drone_flash_duplicate(nickname)
        return _drone_units_redirect()

    if is_active:
        flash(_drone_t('«%s» ники янгиланди: № %s машина, фаол.',
                       'Ник «%s» обновлён: машина № %s, активен.')
              % (nickname, unit.number), 'success')
    else:
        # [REASON]: say plainly what a disabled alias does, because it is the
        # only setting on this screen that changes how new data is ingested:
        # _drone_nickname_maps() skips it, so flights carrying this spelling
        # are stored unattributed from now on. Nothing already stored moves.
        flash(_drone_t(
            '«%s» ники ўчирилди: у энди янги парвозларда танилмайди, '
            'аллақачон сақланган парвозлар ўзгармайди.',
            'Ник «%s» отключён: он больше не распознаётся при загрузке новых '
            'вылетов, уже сохранённые вылеты не меняются.')
            % nickname, 'success')
    if ambiguous is not None:
        _drone_flash_ambiguity(ambiguous)
    return _drone_units_redirect()


# ─── Re-attribution pass (DRONE-007) ──────────────────────────────────────────
#
# [REASON]: THE INVARIANT OF THIS WHOLE SECTION -- a flight whose
# drone_unit_id IS NOT NULL is never modified by any route here. Re-attribution
# only fills blanks. Correcting an attribution that already points at a machine
# is a different problem with a different risk profile (it silently moves
# hectares between machines in reports that have already been sent) and is not
# in this change. Every write below therefore carries
# DroneFlight.drone_unit_id.is_(None) IN THE SQL WHERE CLAUSE, never a filter
# applied in Python after loading, so the guarantee does not depend on the
# rows the request happened to read.

# [REASON]: SQLite caps the number of bound parameters in one statement
# (999 before 3.32, 32766 after). The worst case here is 5 977 ids in one
# undo, which lands on the wrong side of that line on an older SQLite and
# fails the whole statement. Chunking is unconditional so behaviour does not
# depend on the library version that happens to be linked into the Python on
# the server.
DRONE_ID_CHUNK = 400


def _drone_id_chunks(ids):
    for start in range(0, len(ids), DRONE_ID_CHUNK):
        yield ids[start:start + DRONE_ID_CHUNK]


def _drone_unattributed_groups():
    """Unattributed flights grouped by raw spelling, with their resolution.

    Returns (resolvable, unresolvable, totals). Reads only -- this is shared
    by the preview and by apply, and apply recomputes it inside its own
    request rather than trusting anything the client posted back.
    """
    rows = (db.session.query(
        DroneFlight.nickname_raw,
        func.count(DroneFlight.id),
        func.coalesce(func.sum(DroneFlight.area_ha), 0.0))
        .filter(DroneFlight.drone_unit_id.is_(None))
        .group_by(DroneFlight.nickname_raw).all())

    exact, normalized = _drone_nickname_maps()
    unit_numbers = {u.id: u.number for u in DroneUnit.query.all()}

    resolvable, unresolvable = [], []
    for nickname, flights, area in rows:
        unit_id = _drone_resolve_unit(nickname, exact, normalized)
        entry = {
            'nickname': nickname,
            'flights': flights,
            'area': float(area or 0.0),
            'drone_unit_id': unit_id,
            'unit_number': unit_numbers.get(unit_id),
        }
        (resolvable if unit_id is not None else unresolvable).append(entry)

    resolvable.sort(key=lambda r: (-r['flights'], r['nickname'] or ''))
    unresolvable.sort(key=lambda r: (-r['flights'], r['nickname'] or ''))
    totals = {
        'resolvable_flights': sum(r['flights'] for r in resolvable),
        'resolvable_area': sum(r['area'] for r in resolvable),
        'unresolvable_flights': sum(r['flights'] for r in unresolvable),
        'unresolvable_area': sum(r['area'] for r in unresolvable),
    }
    return resolvable, unresolvable, totals


def _drone_run_detail(run):
    """detail_json parsed, never raising on a malformed or truncated row."""
    try:
        detail = json.loads(run.detail_json or '{}')
    except (TypeError, ValueError):
        return {'by_nickname': {}, 'flight_ids': []}
    if not isinstance(detail, dict):
        return {'by_nickname': {}, 'flight_ids': []}
    by_nickname = detail.get('by_nickname')
    flight_ids = detail.get('flight_ids')
    return {
        'by_nickname': by_nickname if isinstance(by_nickname, dict) else {},
        'flight_ids': [i for i in flight_ids
                       if isinstance(i, int)] if isinstance(flight_ids,
                                                            list) else [],
    }


@drones_bp.route('/units/reattach')
@module_required('drones')
def reattach():
    """Preview of the pass. WRITES NOTHING."""
    if not current_user.can_edit:
        abort(403)

    resolvable, unresolvable, totals = _drone_unattributed_groups()
    runs = (DroneReattachRun.query
            .options(joinedload(DroneReattachRun.performer),
                     joinedload(DroneReattachRun.undoer))
            .order_by(DroneReattachRun.id.desc())
            .limit(50).all())
    run_rows = []
    for run in runs:
        detail = _drone_run_detail(run)
        run_rows.append({
            'id': run.id,
            'performed_at': run.performed_at,
            'performed_by': (run.performer.full_name or
                             run.performer.username) if run.performer else None,
            'rows_matched': run.rows_matched,
            'rows_updated': run.rows_updated,
            'undone_at': run.undone_at,
            'undone_by': (run.undoer.full_name or run.undoer.username)
                         if run.undoer else None,
            'nicknames': sorted(detail['by_nickname'].keys()),
        })

    return render_template(
        'drones/reattach.html',
        resolvable=resolvable,
        unresolvable=unresolvable,
        totals=totals,
        runs=run_rows,
        fmt_dt=_drone_fmt_dt,
    )


@drones_bp.route('/units/reattach/apply', methods=['POST'])
@module_required('drones')
def reattach_apply():
    """Fill drone_unit_id on unattributed flights whose spelling now resolves.

    The grouping is recomputed HERE. Nothing the client posted about which
    rows to change is read, because a form field naming rows to update is a
    form field an attacker can rewrite.
    """
    if not current_user.can_edit:
        abort(403)

    resolvable, _unresolvable, totals = _drone_unattributed_groups()
    if not resolvable:
        # [REASON]: a run row recording zero changes is noise in an audit
        # ledger -- it makes "what has been done to this data" longer to read
        # without adding a fact. Nothing resolvable means nothing happened.
        flash(_drone_t(
            'Қайта бириктириш учун ҳеч нарса йўқ: бириктирилмаган '
            'парвозларнинг ҳеч бир ёзилиши жорий никлар харитасида '
            'танилмади.',
            'Переназначать нечего: ни одно написание среди непривязанных '
            'вылетов не распознаётся текущей картой ников.'), 'info')
        return redirect(url_for('drones.reattach'))

    by_nickname = {}
    flight_ids = []
    rows_updated = 0
    try:
        for group in resolvable:
            nickname = group['nickname']
            unit_id = group['drone_unit_id']
            # The id list and the UPDATE carry the SAME conditions, and
            # drone_unit_id IS NULL is one of them on both sides.
            conds = (DroneFlight.drone_unit_id.is_(None),
                     DroneFlight.nickname_raw == nickname)
            ids = [row[0] for row in
                   db.session.query(DroneFlight.id).filter(*conds).all()]
            if not ids:
                continue
            updated = (db.session.query(DroneFlight).filter(*conds)
                       .update({DroneFlight.drone_unit_id: unit_id},
                               synchronize_session=False))
            rows_updated += updated or 0
            flight_ids.extend(ids)
            by_nickname[nickname] = {
                'drone_unit_id': unit_id,
                'unit_number': group['unit_number'],
                'count': len(ids),
            }

        run = DroneReattachRun(
            performed_by=current_user.id,
            performed_at=datetime.utcnow(),
            rows_matched=totals['resolvable_flights'],
            rows_updated=rows_updated,
            detail_json=json.dumps({'by_nickname': by_nickname,
                                    'flight_ids': flight_ids},
                                   ensure_ascii=False),
        )
        db.session.add(run)
        db.session.commit()
    except Exception as exc:
        # One transaction: either the whole pass and its ledger row land, or
        # neither does. A ledger row describing rows that were rolled back
        # would be worse than no ledger at all.
        db.session.rollback()
        current_app.logger.exception('Drone re-attribution failed')
        flash(_drone_t('Қайта бириктириш бажарилмади, ҳеч нарса '
                       'ўзгартирилмади: %s',
                       'Переназначение не выполнено, ничего не изменено: %s')
              % exc, 'danger')
        return redirect(url_for('drones.reattach'))

    flash(_drone_t(
        'Қайта бириктирилди: %d та ёзилиш, %d та парвоз машиналарга '
        'бириктирилди. Прогон №%d — уни бекор қилиш мумкин.',
        'Переназначено: %d написаний, %d вылетов привязано к машинам. '
        'Прогон №%d — его можно откатить.')
        % (len(by_nickname), rows_updated, run.id), 'success')
    return redirect(url_for('drones.reattach'))


@drones_bp.route('/units/reattach/<int:run_id>/undo', methods=['POST'])
@module_required('drones')
def reattach_undo(run_id):
    """Put the blanks back for exactly the rows this run filled."""
    if not current_user.can_edit:
        abort(403)

    run = DroneReattachRun.query.get(run_id)
    if run is None:
        flash(_drone_t('Прогон топилмади.', 'Прогон не найден.'), 'warning')
        return redirect(url_for('drones.reattach'))
    if run.undone_at is not None:
        flash(_drone_t('Прогон №%d аллақачон бекор қилинган (%s). Ҳеч нарса '
                       'ўзгартирилмади.',
                       'Прогон №%d уже откачен (%s). Ничего не изменено.')
              % (run.id, _drone_fmt_dt(run.undone_at)), 'warning')
        return redirect(url_for('drones.reattach'))

    detail = _drone_run_detail(run)
    recorded_ids = detail['flight_ids']
    expected_unit = {}
    for nickname, info in detail['by_nickname'].items():
        if isinstance(info, dict) and info.get('drone_unit_id') is not None:
            expected_unit[nickname] = info['drone_unit_id']

    reverted = 0
    try:
        # [REASON]: only rows that STILL point at the machine this run
        # recorded for their spelling are reverted. A row re-attributed since
        # -- by a later pass after this one was undone and redone, say -- is
        # skipped rather than blanked, because undoing this run must not undo
        # somebody else's work. The id restriction is equally load-bearing: it
        # separates the rows this run filled from flights of the same spelling
        # that arrived afterwards and were attributed at ingest, which this
        # run never touched and must not blank.
        for chunk in _drone_id_chunks(recorded_ids):
            rows = (db.session.query(DroneFlight.id, DroneFlight.nickname_raw,
                                     DroneFlight.drone_unit_id)
                    .filter(DroneFlight.id.in_(chunk)).all())
            by_unit = {}
            for flight_id, nickname_raw, unit_id in rows:
                if (unit_id is not None
                        and expected_unit.get(nickname_raw) == unit_id):
                    by_unit.setdefault(unit_id, []).append(flight_id)
            # The machine condition is repeated in the UPDATE itself, so the
            # guarantee rests on SQL and not only on the read above.
            for unit_id, ids in by_unit.items():
                reverted += (db.session.query(DroneFlight).filter(
                    DroneFlight.id.in_(ids),
                    DroneFlight.drone_unit_id == unit_id)
                    .update({DroneFlight.drone_unit_id: None},
                            synchronize_session=False) or 0)
        run.undone_at = datetime.utcnow()
        run.undone_by = current_user.id
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        current_app.logger.exception('Drone re-attribution undo failed')
        flash(_drone_t('Бекор қилиш бажарилмади, ҳеч нарса ўзгартирилмади: %s',
                       'Откат не выполнен, ничего не изменено: %s')
              % exc, 'danger')
        return redirect(url_for('drones.reattach'))

    skipped = len(recorded_ids) - reverted
    flash(_drone_t(
        'Прогон №%d бекор қилинди: %d та парвоз яна бириктирилмаган ҳолатга '
        'қайтарилди, %d таси ўтказиб юборилди (улар ўшандан бери '
        'ўзгартирилган).',
        'Прогон №%d откачен: %d вылетов возвращено в непривязанное '
        'состояние, %d пропущено (они были изменены с тех пор).')
        % (run.id, reverted, skipped), 'success')
    return redirect(url_for('drones.reattach'))


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
        # [REASON]: the flag travels with the parsed filters so a caller can
        # tell "no date filter was ever specified" from "the date filter was
        # explicitly cleared". Both look like an empty date_from_s, but only
        # the second one must survive into an export link -- see
        # _drone_link_args.
        'has_date_args': has_date_args,
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
    """Query args for drill-down links and exports.

    A set date is passed through. A date that was explicitly cleared is passed
    through as an EMPTY value rather than dropped: _drone_filters_from_args
    decides whether to apply its current-month default by the PRESENCE of the
    key, so dropping a cleared date makes the target silently fall back to the
    current month while the page that produced the link shows all time.
    """
    link = {}
    if filters['date_from_s']:
        link['date_from'] = filters['date_from_s']
    elif filters.get('has_date_args'):
        link['date_from'] = ''
    if filters['date_to_s']:
        link['date_to'] = filters['date_to_s']
    elif filters.get('has_date_args'):
        link['date_to'] = ''
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
                # The label rides alongside the raw value, never replaces it:
                # the drill-down link and the Excel row both need the raw
                # string to keep filtering, and both need the label to be
                # readable. Computed here so the page and summary_xlsx use
                # one helper rather than two.
                'label': _drone_region_label(region),
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

    # По месяцам -- calendar months in the operator's timezone, ascending.
    # The shift must match the display shift exactly: a flight at 19:30 UTC
    # on the 31st belongs to the next month for the operator.
    # [REASON]: the modifier is derived from DRONE_DISPLAY_UTC_OFFSET, never
    # written as a literal. Eight other sites in this module already derive
    # from the constant; a ninth that hardcodes it would silently keep
    # grouping at the old offset if the constant ever changes, and the month
    # table would stop reconciling with the header cards while still looking
    # plausible. Minutes, not hours, so a half-hour timezone stays correct.
    _offset_minutes = int(DRONE_DISPLAY_UTC_OFFSET.total_seconds() // 60)
    month_expr = func.strftime(
        '%Y-%m',
        func.datetime(DroneFlight.started_at,
                      '%+d minutes' % _offset_minutes))
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
        region_labels=_drone_region_label_map(regions),
    )


def _drone_xlsx_safe(value):
    """Neutralize spreadsheet formula injection in a data-driven string.

    [REASON]: same defense as _xlsx_safe in spare_parts.py (SP-F-004) --
    Excel/LibreOffice execute a cell starting with = + - @ as a formula.
    Nicknames and reverse-geocoded addresses come from the DJI cloud, i.e.
    outside this system; the fix is one helper and it also stops Excel from
    mangling ordinary values. Applied only to data-driven strings; fixed
    labels and numeric columns are left untouched.
    """
    if isinstance(value, str):
        stripped = value.lstrip()
        if stripped and stripped[0] in ('=', '+', '-', '@'):
            return "'" + value
    return value


def _drone_xlsx_styler():
    """Shared openpyxl styling for the drones workbooks.

    Modeled on _spare_report_styler (spare_parts.py): header fill and bold
    font, thin borders, frozen header row, auto column widths. Local on
    purpose -- excel_export.py is the eight-sheet daily activity machinery
    and is not a fit here.
    """
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    from openpyxl.utils import get_column_letter
    from types import SimpleNamespace

    header_fill = PatternFill('solid', fgColor='D9EAD3')
    header_font = Font(bold=True)
    total_font = Font(bold=True)
    thin = Side(style='thin', color='D9D9D9')
    border = Border(left=thin, right=thin, top=thin, bottom=thin)

    def style_table(ws, num_formats=None, bold_rows=(), datetime_format=None):
        """num_formats: {column index: number format} for numeric cells;
        datetime_format: {column index: format} for datetime cells."""
        ws.freeze_panes = 'A2'
        ws.sheet_view.showGridLines = False
        num_formats = num_formats or {}
        datetime_format = datetime_format or {}
        for cell in ws[1]:
            cell.font = header_font
            cell.fill = header_fill
            cell.alignment = Alignment(horizontal='center', vertical='center',
                                       wrap_text=True)
        for row in ws.iter_rows(min_row=2, max_row=ws.max_row,
                                max_col=ws.max_column):
            for cell in row:
                if (cell.column in num_formats
                        and isinstance(cell.value, (int, float))
                        and not isinstance(cell.value, bool)):
                    cell.number_format = num_formats[cell.column]
                elif (cell.column in datetime_format
                        and isinstance(cell.value, datetime)):
                    cell.number_format = datetime_format[cell.column]
        for row in ws.iter_rows(min_row=1, max_row=ws.max_row,
                                max_col=ws.max_column):
            for cell in row:
                cell.border = border
        for row_idx in bold_rows:
            for cell in ws[row_idx]:
                cell.font = total_font
        for col in range(1, ws.max_column + 1):
            letter = get_column_letter(col)
            width = 10
            for cell in ws[letter]:
                val = '' if cell.value is None else str(cell.value)
                width = max(width, min(len(val) + 2, 42))
            ws.column_dimensions[letter].width = width

    return SimpleNamespace(style_table=style_table, total_font=total_font)


def _drone_xlsx_response(wb, base_name, filters):
    buffer = io.BytesIO()
    wb.save(buffer)
    buffer.seek(0)
    if filters['date_from_s'] or filters['date_to_s']:
        fname = '%s_%s_%s.xlsx' % (base_name,
                                   filters['date_from_s'] or 'all',
                                   filters['date_to_s'] or 'all')
    else:
        fname = '%s_all.xlsx' % base_name
    return send_file(
        buffer,
        as_attachment=True,
        download_name=fname,
        mimetype=('application/vnd.openxmlformats-officedocument'
                  '.spreadsheetml.sheet'),
    )


@drones_bp.route('/summary.xlsx')
@module_required('drones')
def summary_xlsx():
    """Five-sheet workbook of the summary page, same filters, same order."""
    from openpyxl import Workbook

    filters = _drone_filters_from_args(request.args,
                                       default_current_month=True)
    data = _drone_summary_data(_drone_flight_conditions(filters))
    st = _drone_xlsx_styler()

    label_total = _drone_t('Жами', 'Итого')
    label_unattr = _drone_t('Аниқланмаган', 'Не распознано')
    label_noregion = _drone_t('Вилоят аниқланмаган', 'Область не определена')
    head_flights = _drone_t('Парвозлар', 'Вылеты')
    head_area = _drone_t('Гектар', 'Гектары')
    head_share = _drone_t('Улуш, %', 'Доля, %')
    unbounded = _drone_t('чекланмаган', 'не ограничен')

    wb = Workbook()

    # 1. Сводка / Жамланма
    ws = wb.active
    ws.title = _drone_t('Жамланма', 'Сводка')
    ws.append([_drone_t('Кўрсаткич', 'Показатель'),
               _drone_t('Қиймат', 'Значение')])
    summary_rows = [
        (_drone_t('Давр: бошланиши', 'Период: с'),
         filters['date_from_s'] or unbounded, None),
        (_drone_t('Давр: охири', 'Период: по'),
         filters['date_to_s'] or unbounded, None),
        (_drone_t('Парвозлар', 'Вылетов'), data['totals']['flights'], None),
        (_drone_t('Гектар', 'Гектаров'), data['totals']['area_ha'], '0.00'),
        (_drone_t('Ҳавода соат', 'Часов в воздухе'),
         data['totals']['hours'], '0.00'),
        (_drone_t('Литр эритма', 'Литров раствора'),
         data['totals']['spray_liters'], '0.00'),
        (_drone_t('Кг уруғ', 'Кг посева'), data['totals']['sow_kg'], '0.000'),
        (_drone_t('Ноль майдонли парвозлар', 'Вылетов с нулевой площадью'),
         data['totals']['zero_area'], None),
    ]
    for label, value, fmt in summary_rows:
        ws.append([label, value])
        if fmt:
            ws.cell(row=ws.max_row, column=2).number_format = fmt
    st.style_table(ws)

    # 2. По машинам / Машиналар бўйича
    ws = wb.create_sheet(_drone_t('Машиналар бўйича', 'По машинам'))
    ws.append([_drone_t('Машина (№)', 'Машина (№)'), head_flights,
               head_area, head_share])
    for r in data['by_machine']['rows']:
        ws.append([r['number'], r['flights'], r['area'], r['share']])
    if data['by_machine']['unattributed']:
        u = data['by_machine']['unattributed']
        ws.append([label_unattr, u['flights'], u['area'], u['share']])
    ws.append([label_total, data['by_machine']['total']['flights'],
               data['by_machine']['total']['area'], 100.0])
    st.style_table(ws, num_formats={3: '0.00', 4: '0.0'},
                   bold_rows=(ws.max_row,))

    # 3. По областям / Вилоятлар бўйича
    ws = wb.create_sheet(_drone_t('Вилоятлар бўйича', 'По областям'))
    ws.append([_drone_t('Вилоят', 'Область'), head_flights,
               head_area, head_share])
    for r in data['by_region']['rows']:
        # The same label helper as the screen -- an export that disagrees with
        # the page it was taken from is a support ticket waiting to happen.
        # _drone_xlsx_safe still wraps it: an unknown region passes through as
        # its raw string, which came from outside this system.
        ws.append([_drone_xlsx_safe(r['label']), r['flights'], r['area'],
                   r['share']])
    if data['by_region']['undetermined']:
        u = data['by_region']['undetermined']
        ws.append([label_noregion, u['flights'], u['area'], u['share']])
    ws.append([label_total, data['by_region']['total']['flights'],
               data['by_region']['total']['area'], 100.0])
    st.style_table(ws, num_formats={3: '0.00', 4: '0.0'},
                   bold_rows=(ws.max_row,))

    # 4. По типам работ / Иш турлари бўйича
    ws = wb.create_sheet(_drone_t('Иш турлари бўйича', 'По типам работ'))
    ws.append([_drone_t('Иш тури', 'Тип работы'), head_flights, head_area,
               head_share, _drone_t('Литр', 'Литров'),
               _drone_t('Килограмм', 'Килограммов')])
    for r in data['by_usage']['rows']:
        ws.append([r['label'], r['flights'], r['area'], r['share'],
                   r['spray_liters'], r['sow_kg']])
    ws.append([label_total, data['by_usage']['total']['flights'],
               data['by_usage']['total']['area'], 100.0, None, None])
    st.style_table(ws, num_formats={3: '0.00', 4: '0.0', 5: '0.00',
                                    6: '0.000'},
                   bold_rows=(ws.max_row,))

    # 5. По месяцам / Ойлар бўйича
    ws = wb.create_sheet(_drone_t('Ойлар бўйича', 'По месяцам'))
    ws.append([_drone_t('Ой', 'Месяц'), head_flights, head_area, head_share])
    for r in data['by_month']['rows']:
        ws.append([r['month'], r['flights'], r['area'], r['share']])
    ws.append([label_total, data['by_month']['total']['flights'],
               data['by_month']['total']['area'], 100.0])
    st.style_table(ws, num_formats={3: '0.00', 4: '0.0'},
                   bold_rows=(ws.max_row,))

    return _drone_xlsx_response(wb, 'drones_summary', filters)


@drones_bp.route('/flights.xlsx')
@module_required('drones')
def flights_xlsx():
    """Flat one-sheet export of the flight list, honouring its filters."""
    from openpyxl import Workbook

    filters = _drone_filters_from_args(request.args,
                                       default_current_month=False)
    conds = _drone_flight_conditions(filters)

    total = (db.session.query(func.count(DroneFlight.id))
             .filter(*conds).scalar() or 0)
    if total > DRONE_FLIGHTS_XLSX_CAP:
        # [REASON]: a silently truncated file reads as complete data on the
        # other side of an e-mail -- refuse with a clear message instead.
        flash(_drone_t(
            'Экспорт жуда катта: %d та парвоз, чегара — %d. '
            'Даврни торайтиринг.',
            'Экспорт слишком велик: %d вылетов при пределе %d. '
            'Сузьте период.') % (total, DRONE_FLIGHTS_XLSX_CAP), 'warning')
        return redirect(url_for('drones.index', **_drone_link_args(filters)))

    flights = (DroneFlight.query.filter(*conds)
               .options(joinedload(DroneFlight.drone_unit))
               .order_by(DroneFlight.started_at.desc())
               .all())
    usage_labels = _drone_usage_labels()

    wb = Workbook()
    ws = wb.active
    ws.title = _drone_t('Парвозлар', 'Вылеты')
    ws.append([
        _drone_t('Сана ва вақт (Тошкент)', 'Дата и время (UTC+5)'),
        _drone_t('Машина (№)', 'Машина (№)'),
        _drone_t('Ник (DJI)', 'Ник (DJI)'),
        _drone_t('Вилоят', 'Область'),
        _drone_t('Манзил', 'Адрес'),
        _drone_t('Гектар', 'Гектары'),
        _drone_t('Дақиқа', 'Минуты'),
        _drone_t('Литр', 'Литры'),
        _drone_t('Килограмм', 'Килограммы'),
        _drone_t('Иш тури', 'Тип работы'),
        'DJI id',
    ])
    for f in flights:
        usage_label = usage_labels.get(f.usage_type)
        if usage_label is None and f.usage_type is not None:
            usage_label = str(f.usage_type)
        ws.append([
            (f.started_at + DRONE_DISPLAY_UTC_OFFSET)
            if f.started_at else None,
            f.drone_unit.number if f.drone_unit else None,
            _drone_xlsx_safe(f.nickname_raw),
            _drone_xlsx_safe(_drone_region_label(f.region)),
            _drone_xlsx_safe(f.location_text),
            f.area_ha,
            (f.work_seconds / 60.0) if f.work_seconds is not None else None,
            f.spray_liters,
            f.sow_kg,
            usage_label,
            f.dji_flight_id,
        ])
    st = _drone_xlsx_styler()
    st.style_table(ws,
                   num_formats={6: '0.00', 7: '0.0', 8: '0.00', 9: '0.000'},
                   datetime_format={1: 'DD.MM.YYYY HH:MM'})
    return _drone_xlsx_response(wb, 'drones_flights', filters)
