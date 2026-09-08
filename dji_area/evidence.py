# -*- coding: utf-8 -*-
"""dji_area/evidence.py -- чистые разборщики источников DJI в доказательства.

Одна функция на источник: list-запись, карточка вылета (JSON), маршрут
(protobuf), идентичность V4 по контексту захвата. Используются двумя
писателями -- приёмником Flask и инструментами на stdlib sqlite3 -- чтобы
оба клали в `dji_flight_evidence` одно и то же.

Правила, которые здесь держатся:

* борт -- только `hardware_id` карточки; при её отсутствии -- маршрут с
  ПРОВЕРЕННОЙ идентичностью; ник борт не определяет;
* embedded flight id маршрута обязан совпасть с ожидаемым, иначе маршрут
  в карантине (ROUTE_IDENTITY_ERROR) и не участвует ни в чём;
* поле №3 точки маршрута остаётся UNKNOWN_SEMANTICS: считается ТОЛЬКО число
  точек, где оно закодировано, как структурная диагностика;
* V4 не несёт flight id: идентичность -- путь захвата (`/airline_v4/<id>/`)
  плюс временное окно (проверяется в гейтах V4).

Ввода-вывода здесь нет.
"""

import hashlib
import json
import re
from datetime import datetime, timedelta

from drone_collector.route_decode import (RouteDecodeError,
                                          decode_route_response)

from dji_area import REPORT_UTC_OFFSET_HOURS

PROVIDER_ACCOUNT_DEFAULT = 'dji-smartfarm-main'

SOURCE_LIST = 'list'
SOURCE_CARD = 'card'
SOURCE_ROUTE = 'route'
SOURCE_V4 = 'v4'
SOURCE_AIRLINES = 'airlines'
SOURCE_LAND_PAGE = 'land_page'
SOURCE_LAND_GEOMETRY = 'land_geometry'
SOURCE_TYPES = (SOURCE_LIST, SOURCE_CARD, SOURCE_ROUTE, SOURCE_V4,
                SOURCE_AIRLINES, SOURCE_LAND_PAGE, SOURCE_LAND_GEOMETRY)
FLIGHT_SOURCE_TYPES = (SOURCE_LIST, SOURCE_CARD, SOURCE_ROUTE, SOURCE_V4,
                       SOURCE_AIRLINES)

ROUTE_OK = 'OK'
ROUTE_IDENTITY_ERROR = 'ROUTE_IDENTITY_ERROR'
ROUTE_DECODE_ERROR = 'DECODE_ERROR'
ROUTE_STATUS_NOT_OK = 'STATUS_NOT_OK'
ROUTE_EMPTY = 'EMPTY'

V4_URL_PATH_MATCH = 'URL_PATH_MATCH'
V4_DIRECTORY_ONLY = 'DIRECTORY_ONLY'
V4_MISMATCH = 'MISMATCH'
V4_UNKNOWN = 'UNKNOWN'
V4_ABSENT = 'ABSENT'

HW_SOURCE_CARD = 'card'
HW_SOURCE_ROUTE = 'route'

_AIRLINE_V4_PATH = re.compile(r'/airline_v4/(\d+)/')
# Маркеры секретов, которых не должно быть ни в одном сохранённом теле.
SECRET_MARKERS = ('OSSAccessKeyId=', 'Signature=', 'Expires=', 'signedURL',
                  'set-cookie', 'authorization:', 'bearer ')


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def canonical_record_bytes(record):
    """Канонический JSON list-записи (sort_keys, компактно, UTF-8)."""
    return json.dumps(record, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':')).encode('utf-8')


def contains_secret_marker(data):
    """True, если тело несёт маркер подписанного URL/креденшела."""
    if isinstance(data, (bytes, bytearray)):
        text = bytes(data).decode('utf-8', 'ignore')
    else:
        text = str(data)
    low = text.lower()
    return any(marker.lower() in low for marker in SECRET_MARKERS)


def _num(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _int(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _text(value, limit):
    if value is None:
        return None
    text = str(value)
    return text[:limit] if text else None


def report_start_date(start_ts):
    """День отчёта: дата НАЧАЛА записи в UTC+5, ровно один раз."""
    if start_ts is None:
        return None
    return (datetime.utcfromtimestamp(float(start_ts))
            + timedelta(hours=REPORT_UTC_OFFSET_HOURS)).date()


def utc_datetime(ts):
    if ts is None:
        return None
    try:
        return datetime.utcfromtimestamp(float(ts))
    except (OverflowError, OSError, ValueError):
        return None


# ─── List ────────────────────────────────────────────────────────────────────

def parse_list_record(record):
    """Поля list-записи, нужные резолверу. Ничего не нормализуется по смыслу."""
    if not isinstance(record, dict):
        raise ValueError('list record is not an object')
    flight_id = _int(record.get('id'))
    if flight_id is None:
        raise ValueError('list record has no numeric id')
    manual = record.get('manual_mode')
    return {
        'flight_id': flight_id,
        'raw_area_m2': _num(record.get('new_work_area')),
        'start_ts': _int(record.get('start_timestamp')),
        'end_ts': _int(record.get('end_timestamp')),
        'mode_name': _int(record.get('mode_name')),
        'manual_mode': bool(manual) if manual is not None else None,
        'spray_width': _num(record.get('spray_width')),
        'nickname': _text(record.get('nickname'), 100),
        'serial_number': _text(record.get('serial_number'), 50),
        'work_seconds': _int(record.get('work_time_seconds')),
        'create_date': _int(record.get('create_date')),
        'lat': _num(record.get('lat')),
        'lng': _num(record.get('lng')),
    }


# ─── Card ────────────────────────────────────────────────────────────────────

def parse_card_body(body):
    """Карточка вылета (JSON /flight_records/<id>) -> поля.

    Отказ ValueError на не-JSON, `code != 0` или отсутствие `data.id`.
    `new_work_area` карточки -- float с исходной точностью; хранится
    отдельно от целого list-значения, не смешивается.
    """
    if isinstance(body, (bytes, bytearray)):
        text = bytes(body).decode('utf-8')
    else:
        text = body
    try:
        doc = json.loads(text)
    except ValueError:
        raise ValueError('card body is not JSON')
    if not isinstance(doc, dict):
        raise ValueError('card body is not an object')
    code = doc.get('code')
    data = doc.get('data')
    if code != 0 or not isinstance(data, dict):
        raise ValueError('card body is not a success envelope (code=%r)'
                         % (code,))
    flight_id = _int(data.get('id'))
    if flight_id is None:
        raise ValueError('card has no numeric data.id')
    manual = data.get('manual_mode')
    return {
        'flight_id': flight_id,
        'api_code': code,
        'hardware_id': _text(data.get('hardware_id'), 50),
        'raw_area_m2': _num(data.get('new_work_area')),
        'geometry_md5': _text(data.get('geometry_md5'), 80),
        'mode_name': _int(data.get('mode_name')),
        'manual_mode': bool(manual) if manual is not None else None,
        'spray_width': _num(data.get('spray_width')),
        'start_ts': _int(data.get('start_timestamp')),
        'end_ts': _int(data.get('end_timestamp')),
        'app_version': _text(data.get('app_version'), 20),
        'drone_type': _text(data.get('drone_type'), 20),
        'create_date': _int(data.get('create_date')),
        'nickname': _text(data.get('nickname'), 100),
        'detail_present': data.get('detail_present'),
        'first_usage': _int(data.get('first_usage')),
        'last_usage': _int(data.get('last_usage')),
    }


# ─── Route ───────────────────────────────────────────────────────────────────

def parse_route_body(body, expected_flight_id):
    """Маршрут (protobuf POST flight_records) -> поля + статус идентичности.

    Никогда не бросает на данных: любой отказ декодера -- статус
    DECODE_ERROR. Один файл -- одна запись; при нескольких берётся та, чей
    embedded id совпал, иначе первая и ROUTE_IDENTITY_ERROR.
    """
    out = {
        'identity_status': None,
        'embedded_flight_id': None,
        'hardware_id': None,
        'area_m2': None,
        'spray_width': None,
        'mode_name': None,
        'point_count': None,
        'points_with_field3': None,
        'start_ms': None,
        'end_ms': None,
        'session_no': None,
        'points': None,
        'api_status': None,
        'records': 0,
    }
    try:
        response = decode_route_response(bytes(body))
    except (RouteDecodeError, ValueError, TypeError) as exc:
        out['identity_status'] = ROUTE_DECODE_ERROR
        out['decode_error'] = type(exc).__name__
        return out
    out['api_status'] = response.status
    if not response.is_ok:
        out['identity_status'] = ROUTE_STATUS_NOT_OK
        return out
    records = list(response.routes)
    out['records'] = len(records)
    if not records:
        out['identity_status'] = ROUTE_EMPTY
        return out
    expected = _int(expected_flight_id)
    chosen = None
    for record in records:
        if record.flight_id is not None and record.flight_id == expected:
            chosen = record
            break
    if chosen is None:
        chosen = records[0]
        out['identity_status'] = ROUTE_IDENTITY_ERROR
    else:
        out['identity_status'] = ROUTE_OK
    out['embedded_flight_id'] = chosen.flight_id
    out['hardware_id'] = _text(chosen.hardware_id, 50)
    out['area_m2'] = _num(chosen.work_area_m2)
    width = chosen.spray_width_m if chosen.spray_width_known else None
    out['spray_width'] = width
    out['mode_name'] = _int(chosen.mode_name)
    out['point_count'] = len(chosen.points or ())
    # PointShape.fields -- кортежи (number, wire, count); значение поля №3
    # здесь не читается и не интерпретируется (UNKNOWN_SEMANTICS).
    out['points_with_field3'] = sum(
        1 for shape in (chosen.point_shapes or ())
        if any(field[0] == 3 for field in shape.fields))
    out['start_ms'] = _int(chosen.start_ms)
    out['end_ms'] = _int(chosen.end_ms)
    out['session_no'] = _text(chosen.mission_uuid, 100)
    out['points'] = [(float(p[0]), float(p[1])) for p in (chosen.points or ())]
    return out


# ─── V4 identity ─────────────────────────────────────────────────────────────

def v4_identity(request_context, expected_flight_id):
    """(status, identity_ok) по контексту захвата V4.

    Контекст -- словарь приёмника/импорта: `path` (путь URL без подписи)
    и/или `association` ('url_path' | 'directory'). Совпадение id в пути --
    URL_PATH_MATCH; иной id в пути -- MISMATCH; только имя каталога --
    DIRECTORY_ONLY (identity_ok=True с ограничением: filename не proof, но
    таймстемпы проверит гейт окна); ничего -- UNKNOWN.
    """
    expected = _int(expected_flight_id)
    context = request_context or {}
    path = context.get('path') or context.get('url_path') or ''
    match = _AIRLINE_V4_PATH.search(str(path))
    if match:
        found = int(match.group(1))
        if expected is not None and found == expected:
            return V4_URL_PATH_MATCH, True
        return V4_MISMATCH, False
    if context.get('association') == 'directory':
        return V4_DIRECTORY_ONLY, True
    return V4_UNKNOWN, True


# ─── Hardware ────────────────────────────────────────────────────────────────

def hardware_for(card, route):
    """(hardware_id, source): карточка, иначе маршрут с OK-идентичностью."""
    if card and card.get('hardware_id'):
        return card['hardware_id'], HW_SOURCE_CARD
    if route and route.get('identity_status') == ROUTE_OK \
            and route.get('hardware_id'):
        return route['hardware_id'], HW_SOURCE_ROUTE
    return None, None


# ─── Land ────────────────────────────────────────────────────────────────────

_LAND_VOLATILE_KEYS = ('signedURL',)


def strip_volatile(value):
    """Рекурсивно убрать signedURL; uuid и contentMd5 остаются."""
    if isinstance(value, dict):
        return {k: strip_volatile(v) for k, v in value.items()
                if k not in _LAND_VOLATILE_KEYS}
    if isinstance(value, list):
        return [strip_volatile(v) for v in value]
    return value


def _land_utc(value):
    if not value or not isinstance(value, str):
        return None
    text = value.strip()
    if text.endswith('Z'):
        text = text[:-1] + '+00:00'
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed
    return (parsed - parsed.utcoffset()).replace(tzinfo=None)


def parse_land_node(node):
    """GraphQL land node -> поля ревизии + канонический raw_json/sha256."""
    if not isinstance(node, dict):
        raise ValueError('land node is not an object')
    uuid = _text(node.get('uuid'), 40)
    if not uuid:
        raise ValueError('land node has no uuid')
    stripped = strip_volatile(node)
    raw_json = json.dumps(stripped, ensure_ascii=False, sort_keys=True,
                          separators=(',', ':'))
    geometry = (node.get('geometry') or {}).get('storage') or {}
    position = node.get('position') or {}
    return {
        'land_uuid': uuid,
        'external_id': _text(node.get('externalId'), 120),
        'serial_number': _text(node.get('serialNumber'), 50),
        'name': _text(node.get('name'), 300),
        'total_area_raw': _num(node.get('totalArea')),
        'work_area_raw': _num(node.get('workArea')),
        'obstacle_area_raw': _num(node.get('totalObstacleArea')),
        'area_unit': 'mu',
        'geometry_md5': _text(geometry.get('contentMd5'), 32),
        'geometry_storage_uuid': _text(geometry.get('uuid'), 40),
        'land_type': _text(node.get('landType'), 40),
        'created_at_source': _land_utc(node.get('createdAt')),
        'updated_at_source': _land_utc(node.get('updatedAt')),
        'center_lat': _num(position.get('lat')),
        'center_lng': _num(position.get('lng')),
        'raw_json': raw_json,
        'raw_sha256': hashlib.sha256(raw_json.encode('utf-8')).hexdigest(),
    }


def geometry_md5_of(body):
    return hashlib.md5(bytes(body)).hexdigest()
