# -*- coding: utf-8 -*-
"""drone_collector/route_decode.py -- decode the binary flight-route response
of DJI SmartFarm.

DISCOVERY CODE. Nothing here talks to the network, to a browser or to a
database, and nothing here is wired into the production collector. It exists
so that the finding recorded in docs/DRONE_COVERAGE_001_DISCOVERY.md can be
re-derived from a saved response instead of being believed.

The response
------------
The map view of Task History issues

    POST https://kr-ag2-api.dji.com/api/web/v2/flight_datas/flight_records

and gets back `application/octet-stream`. The body is protobuf WIRE FORMAT.
No .proto schema was found and none is assumed: this module walks the wire
format, which is self-describing as to field number and wire type, and gives
a NAME to a field only where that name was proved by cross-checking the same
flight against the JSON endpoints of the same cabinet.

What "proved" means here, precisely. The sample was one day of one machine --
2026-06-05, `8 GardenU`, nine flights. For each of the nine, the route record
was compared field by field against the flight's own card
(`/api/web/v1/flight_records/{id}`) and its row of the list
(`/api/web/v1/flight_records?...`). Every name below matched on 9 of 9. The
fields that did NOT match anything are kept, unnamed, in `RouteRecord.unknown`
-- they are not guessed at, and they are not dropped either.

What the payload does NOT contain, and this is the load-bearing negative
result: a point is EXACTLY two doubles, latitude and longitude. All 961 points
of the sample parsed as `{1: fixed64, 2: fixed64}` and nothing else. There is
no per-point timestamp, no altitude, no speed, no heading, and -- decisively
for the money question -- no pump or spray state. Any per-point claim beyond
position is therefore unavailable from this source, not merely unimplemented.

Strictness
----------
The decoder refuses rather than improvises. A truncated varint, an unknown
wire type, a group (wire types 3 and 4, deprecated), a length that runs past
the end of its buffer, a point that is not exactly two fixed64 fields -- each
raises RouteDecodeError. [REASON]: this feeds a number that is meant to be
compared with money. A decoder that returns "something" from a body it does
not understand would put a plausible wrong figure in front of the owner, and
that is the one outcome the whole task exists to prevent.
"""

import struct

# Version of the field mapping below. Bumped when the MEANING of a decoded
# field changes -- a new name proved, a name withdrawn -- not when a comment
# is edited. It travels with every route the collector stores, so a figure
# computed later can be traced back to the rules it was decoded by.
DECODER_VERSION = 'route-decode-1'

# ─── The envelope ────────────────────────────────────────────────────────────

# Top level: 1 = status, 2 = message, 3 = payload.
#
# [REASON]: `status` here is DJI's in-body status, and the same warning applies
# as everywhere else in this integration -- HTTP 200 says nothing. Observed
# success is status 200 together with message "Success." (with the full stop;
# the JSON endpoints spell theirs "OK", so the two envelopes are NOT the same
# envelope and must not share a constant).
FIELD_STATUS = 1
FIELD_MESSAGE = 2
FIELD_PAYLOAD = 3

STATUS_OK = 200

# Payload: field 1, repeated -- one entry per flight.
FIELD_ROUTE = 1

# Inside one route record. Only the proved ones are named.
FIELD_POINT = 1           # repeated, {1: double lat, 2: double lng}
FIELD_FLIGHT_ID = 2       # == the flight's `id` in the JSON endpoints
FIELD_WORK_AREA = 3       # float32, == `new_work_area`, square metres
FIELD_DURATION_MS = 4     # == (end_timestamp - start_timestamp) * 1000
FIELD_TAKEOFF = 7         # {1: double lat, 2: double lng}, == list `lat`/`lng`
FIELD_FLYER = 8           # {1: id, 2: name}, name == `flyer_name`
FIELD_DEVICE = 9          # {1: id, 2: nickname, 3: hardware_id}
FIELD_TEAM = 10           # {1: id, 2: name}, name == `team_name`
FIELD_MODE_NAME = 11      # == `mode_name` (4 automatic, 1 manual, in sample)
FIELD_MISSION_UUID = 14   # see RouteRecord.mission_uuid -- NOT proved
FIELD_LOCATION = 18       # == `location`
FIELD_START_MS = 22       # == start_timestamp * 1000, but to the millisecond
FIELD_END_MS = 23         # == end_timestamp * 1000, but to the millisecond
FIELD_DRONE_TYPE = 24     # == `drone_type`
FIELD_APP_VERSION = 25    # == `app_version`
FIELD_SPRAY_WIDTH = 26    # float32, == `spray_width`, metres. See below.

# Sub-fields of a point and of the {id, name} records.
SUB_LAT = 1
SUB_LNG = 2
SUB_ID = 1
SUB_NAME = 2
SUB_HARDWARE_ID = 3

# DJI's "not recorded" marker for spray_width, observed as exactly -1.0 on the
# two flights of the sample whose JSON `spray_width` is null.
#
# [REASON]: -1 must never reach a buffer radius. Halved it would be -0.5 m, and
# shapely buffers a negative distance happily -- it erodes instead of dilating
# and returns an EMPTY geometry for a line. The coverage of such a flight would
# come out as a clean, confident 0.00 ha rather than as "width unknown".
SPRAY_WIDTH_ABSENT = -1.0


class RouteDecodeError(ValueError):
    """The body is not a route response we understand. Never raised for data
    that is merely unexpected in VALUE -- only for structure we cannot read."""


# ─── Wire format ─────────────────────────────────────────────────────────────

WIRE_VARINT = 0
WIRE_FIXED64 = 1
WIRE_BYTES = 2
WIRE_FIXED32 = 5

_MAX_VARINT_SHIFT = 63


def _read_varint(buf, pos, end):
    result = 0
    shift = 0
    while True:
        if pos >= end:
            raise RouteDecodeError('varint runs past the end of the buffer')
        byte = buf[pos]
        pos += 1
        result |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return result, pos
        shift += 7
        if shift > _MAX_VARINT_SHIFT:
            raise RouteDecodeError('varint longer than 64 bits')


def walk(buf, pos=0, end=None):
    """[(field_number, wire_type, value)] for one protobuf message.

    value is an int for varints, and bytes for every other wire type. The
    caller decides what the bytes mean; this function never guesses.
    """
    if end is None:
        end = len(buf)
    fields = []
    while pos < end:
        key, pos = _read_varint(buf, pos, end)
        number, wire = key >> 3, key & 7
        if number == 0:
            raise RouteDecodeError('field number 0 is not legal')
        if wire == WIRE_VARINT:
            value, pos = _read_varint(buf, pos, end)
        elif wire == WIRE_FIXED64:
            if pos + 8 > end:
                raise RouteDecodeError('fixed64 runs past the end of the buffer')
            value, pos = buf[pos:pos + 8], pos + 8
        elif wire == WIRE_BYTES:
            length, pos = _read_varint(buf, pos, end)
            if pos + length > end:
                raise RouteDecodeError(
                    'length-delimited field %d runs past the end of the buffer'
                    % number)
            value, pos = buf[pos:pos + length], pos + length
        elif wire == WIRE_FIXED32:
            if pos + 4 > end:
                raise RouteDecodeError('fixed32 runs past the end of the buffer')
            value, pos = buf[pos:pos + 4], pos + 4
        else:
            # 3 and 4 are the deprecated group markers; 6 and 7 do not exist.
            raise RouteDecodeError('unsupported wire type %d on field %d'
                                   % (wire, number))
        fields.append((number, wire, value))
    return fields


def _double(raw):
    return struct.unpack('<d', raw)[0]


def _float(raw):
    return struct.unpack('<f', raw)[0]


def _text(raw):
    try:
        return raw.decode('utf-8')
    except UnicodeDecodeError:
        raise RouteDecodeError('field is not valid UTF-8')


def _one(fields, number):
    """The single value of `number`, or None. Repeated -> RouteDecodeError."""
    found = [value for num, _wire, value in fields if num == number]
    if not found:
        return None
    if len(found) > 1:
        raise RouteDecodeError('field %d appears %d times, expected at most one'
                               % (number, len(found)))
    return found[0]


def _point(raw):
    """{1: double, 2: double} -> (lat, lng). Anything else is an error.

    [REASON]: this is the check that carries the negative result of the whole
    discovery. If DJI ever adds a third field to a point -- a timestamp, a pump
    flag -- this raises instead of silently ignoring it, and the finding
    "position only" gets re-opened deliberately rather than going stale.
    """
    fields = walk(raw)
    if len(fields) != 2:
        raise RouteDecodeError('point has %d fields, expected exactly 2'
                               % len(fields))
    by_number = {}
    for number, wire, value in fields:
        if wire != WIRE_FIXED64:
            raise RouteDecodeError(
                'point field %d has wire type %d, expected fixed64'
                % (number, wire))
        by_number[number] = value
    if set(by_number) != {SUB_LAT, SUB_LNG}:
        raise RouteDecodeError('point carries fields %s, expected {1, 2}'
                               % sorted(by_number))
    return _double(by_number[SUB_LAT]), _double(by_number[SUB_LNG])


def _named(raw):
    """{1: id, 2: name, [3: hardware_id]} -> (id, name, hardware_id)."""
    fields = walk(raw)
    ident = _one(fields, SUB_ID)
    name = _one(fields, SUB_NAME)
    hardware = _one(fields, SUB_HARDWARE_ID)
    return (ident if isinstance(ident, int) else None,
            _text(name) if isinstance(name, bytes) else None,
            _text(hardware) if isinstance(hardware, bytes) else None)


# ─── The decoded shapes ──────────────────────────────────────────────────────

class RouteRecord(object):
    """One flight's route, as far as the wire format proves it.

    Every attribute may be None: the sample already contains two flights that
    carry no spray width and no mission uuid at all, and a decoder that
    substituted a default there would be inventing the very numbers this task
    forbids inventing.
    """

    __slots__ = ('flight_id', 'points', 'work_area_m2', 'duration_ms',
                 'takeoff', 'flyer_id', 'flyer_name', 'device_id', 'nickname',
                 'hardware_id', 'team_id', 'team_name', 'mode_name',
                 'mission_uuid', 'location', 'start_ms', 'end_ms',
                 'drone_type', 'app_version', 'spray_width_m', 'unknown')

    def __init__(self, **kwargs):
        for name in self.__slots__:
            setattr(self, name, kwargs.get(name))
        if self.points is None:
            self.points = []
        if self.unknown is None:
            self.unknown = []

    @property
    def spray_width_known(self):
        """False when DJI recorded no width for this flight.

        The observed marker is exactly -1.0; anything at or below zero is
        treated the same way, because a width of zero is no more usable as a
        buffer radius than a negative one.
        """
        return self.spray_width_m is not None and self.spray_width_m > 0

    @property
    def area_ha(self):
        """DJI's own area for this flight in hectares, or None.

        Note the precision: field 3 is a float32, so it agrees with the JSON
        `new_work_area` only to about seven significant digits. It is a
        cross-check of identity, NOT a replacement for the JSON value, and the
        stored figure must keep coming from the JSON path.
        """
        if self.work_area_m2 is None:
            return None
        return self.work_area_m2 / 10000.0

    def __repr__(self):
        return '<RouteRecord flight=%s points=%d area=%s>' % (
            self.flight_id, len(self.points), self.work_area_m2)


class RouteResponse(object):
    """The whole decoded body: envelope plus one RouteRecord per flight."""

    __slots__ = ('status', 'message', 'routes')

    def __init__(self, status, message, routes):
        self.status = status
        self.message = message
        self.routes = routes

    @property
    def is_ok(self):
        return self.status == STATUS_OK

    @property
    def flight_ids(self):
        return [route.flight_id for route in self.routes]

    @property
    def point_count(self):
        return sum(len(route.points) for route in self.routes)

    def __repr__(self):
        return '<RouteResponse status=%s routes=%d points=%d>' % (
            self.status, len(self.routes), self.point_count)


# ─── Decoding ────────────────────────────────────────────────────────────────

def decode_route_record(raw):
    """One route record -> RouteRecord."""
    points = []
    unknown = []
    named = {}
    for number, wire, value in walk(raw):
        if number == FIELD_POINT and wire == WIRE_BYTES:
            points.append(_point(value))
            continue
        if number in named:
            raise RouteDecodeError('field %d appears more than once in a route'
                                   % number)
        named[number] = (wire, value)

    def take(number, wanted_wire):
        entry = named.pop(number, None)
        if entry is None:
            return None
        wire, value = entry
        if wire != wanted_wire:
            raise RouteDecodeError(
                'field %d has wire type %d, expected %d'
                % (number, wire, wanted_wire))
        return value

    flight_id = take(FIELD_FLIGHT_ID, WIRE_VARINT)
    area_raw = take(FIELD_WORK_AREA, WIRE_FIXED32)
    duration = take(FIELD_DURATION_MS, WIRE_VARINT)
    takeoff_raw = take(FIELD_TAKEOFF, WIRE_BYTES)
    flyer_raw = take(FIELD_FLYER, WIRE_BYTES)
    device_raw = take(FIELD_DEVICE, WIRE_BYTES)
    team_raw = take(FIELD_TEAM, WIRE_BYTES)
    mode_name = take(FIELD_MODE_NAME, WIRE_VARINT)
    mission_raw = take(FIELD_MISSION_UUID, WIRE_BYTES)
    location_raw = take(FIELD_LOCATION, WIRE_BYTES)
    start_ms = take(FIELD_START_MS, WIRE_VARINT)
    end_ms = take(FIELD_END_MS, WIRE_VARINT)
    type_raw = take(FIELD_DRONE_TYPE, WIRE_BYTES)
    version_raw = take(FIELD_APP_VERSION, WIRE_BYTES)
    width_raw = take(FIELD_SPRAY_WIDTH, WIRE_FIXED32)

    # Whatever is left is real data we have not identified. It is reported,
    # not discarded: the next reader needs to see that it exists.
    for number, (wire, value) in sorted(named.items()):
        unknown.append((number, wire, value))

    flyer_id, flyer_name, _ = _named(flyer_raw) if flyer_raw else (None, None, None)
    device_id, nickname, hardware_id = (_named(device_raw) if device_raw
                                        else (None, None, None))
    team_id, team_name, _ = _named(team_raw) if team_raw else (None, None, None)

    return RouteRecord(
        flight_id=flight_id,
        points=points,
        work_area_m2=_float(area_raw) if area_raw else None,
        duration_ms=duration,
        takeoff=_point(takeoff_raw) if takeoff_raw else None,
        flyer_id=flyer_id, flyer_name=flyer_name,
        device_id=device_id, nickname=nickname, hardware_id=hardware_id,
        team_id=team_id, team_name=team_name,
        mode_name=mode_name,
        mission_uuid=_text(mission_raw) if mission_raw else None,
        location=_text(location_raw) if location_raw else None,
        start_ms=start_ms, end_ms=end_ms,
        drone_type=_text(type_raw) if type_raw else None,
        app_version=_text(version_raw) if version_raw else None,
        spray_width_m=_float(width_raw) if width_raw else None,
        unknown=unknown,
    )


def decode_route_response(raw):
    """The raw response body -> RouteResponse.

    Raises RouteDecodeError on anything structurally unreadable. Does NOT
    raise on a non-OK status: the caller has to see the status DJI sent, and
    an error envelope carries no routes anyway.
    """
    if not isinstance(raw, (bytes, bytearray)):
        raise RouteDecodeError('expected bytes, got %s' % type(raw).__name__)
    if not raw:
        raise RouteDecodeError('empty body')

    status = message = None
    payload = None
    for number, wire, value in walk(bytes(raw)):
        if number == FIELD_STATUS and wire == WIRE_VARINT:
            status = value
        elif number == FIELD_MESSAGE and wire == WIRE_BYTES:
            message = _text(value)
        elif number == FIELD_PAYLOAD and wire == WIRE_BYTES:
            if payload is not None:
                raise RouteDecodeError('more than one payload field')
            payload = value

    if status is None:
        raise RouteDecodeError('no status field -- not a route response')

    routes = []
    if payload is not None:
        for number, wire, value in walk(payload):
            if number != FIELD_ROUTE or wire != WIRE_BYTES:
                raise RouteDecodeError(
                    'unexpected field %d (wire %d) in the payload'
                    % (number, wire))
            routes.append(decode_route_record(value))

    return RouteResponse(status, message, routes)


# ─── Geometry helpers ────────────────────────────────────────────────────────
#
# Deliberately stdlib-only. The real coverage computation belongs in a package
# with shapely and pyproj (see docs/DRONE_COVERAGE_001_ARCHITECTURE.md); what
# is here is only what the discovery needed in order to state a number, and it
# is exact enough for that: an equirectangular projection about the track's own
# centre is accurate to better than a metre over the few hundred metres a
# single flight spans.

EARTH_RADIUS_M = 6378137.0


def path_length_m(points, origin=None):
    """Length of a lat/lng polyline in metres, 0.0 for fewer than two points."""
    import math
    if len(points) < 2:
        return 0.0
    if origin is None:
        origin = (sum(p[0] for p in points) / len(points),
                  sum(p[1] for p in points) / len(points))
    lat0, lng0 = origin
    scale = math.cos(math.radians(lat0))
    def xy(point):
        return (math.radians(point[1] - lng0) * EARTH_RADIUS_M * scale,
                math.radians(point[0] - lat0) * EARTH_RADIUS_M)
    flat = [xy(p) for p in points]
    return sum(math.dist(flat[i], flat[i + 1]) for i in range(len(flat) - 1))


def implied_work_length_m(record):
    """DJI's own area divided by its own width: how much path DJI's number
    needs, in metres. None when either input is missing.

    Why this is worth computing. There is a WORKING HYPOTHESIS, derived from
    analysing liquid consumption in docs/DRONES_AREA_DISPUTE.md, that
    `new_work_area` is the path flown with the pump running multiplied by the
    set swath. It is a hypothesis and not an established fact: it rests on
    litres-per-hectare staying constant across flights of very different size,
    and nothing in this payload -- or in any DJI payload we have seen --
    reports the pump at all.

    IF the hypothesis holds, this length cannot exceed the length of the
    flight's own route: the pump cannot run longer than the drone flew. That
    makes the comparison a one-sided CONSISTENCY CHECK which the data is able
    to fail. On the nine flights of 2026-06-05 nothing failed it -- seven came
    out between 0.336 and 0.930, and the remaining two could not be checked at
    all because DJI recorded no swath for them (see `route_exceeds_path`,
    which answers None there and never False).

    What a pass does NOT mean. The check is one-sided, so any hypothesis
    yielding an area no larger than length x swath passes it too. Agreement is
    a point in the hypothesis' favour; it is not proof of it, and no number
    derived through the hypothesis may be reported as an observation.
    """
    if record.work_area_m2 is None or not record.spray_width_known:
        return None
    return record.work_area_m2 / record.spray_width_m


def route_exceeds_path(record, tolerance=1.0):
    """True when DJI's area needs more path than the route contains.

    Conditional by construction: what it tests is consistency with the working
    hypothesis described on `implied_work_length_m`, not with a measured fact.
    True therefore means "DJI's area cannot be reconciled with this route under
    that hypothesis" -- a reason to look, never a verdict on its own.

    `tolerance` is a plain ratio, not a percentage: 1.0 means "flag only a
    real excess". Returns None when the test cannot be run at all (no width,
    no area or fewer than two points) -- which is NOT the same answer as False
    and must not be collapsed into it.
    """
    implied = implied_work_length_m(record)
    if implied is None or len(record.points) < 2:
        return None
    actual = path_length_m(record.points)
    if actual <= 0:
        return None
    return implied > actual * tolerance
