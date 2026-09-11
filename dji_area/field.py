# -*- coding: utf-8 -*-
"""dji_area/field.py -- резолвер поля / исторического контура (TIER1–TIER5).

Независим от резолвера площади: низкий tier поля НИКОГДА не меняет
площадь, а корректная площадь с неизвестным полем остаётся видимой в
корзине «поле не определено».

Ключ ``geometry_md5`` карточки вылета -- либо plain MD5 (32 hex), либо
``UUID__MD5``. Установлено исследованием (historical_contours_202608):
md5 -- это md5 байтов GeoJSON геометрии, локальной для пульта в момент
взлёта; uuid -- запись land в облаке, к которой поле было привязано.
Записи каталога неизменяемы: правка -- новый uuid, повторная синхронизация
без изменений -- delete+create с тем же externalId/contentMd5.

Tiers:

* TIER1_EXACT      -- байты геометрии с этим md5 сохранены и проверены
                      (md5(bytes) == contentMd5). Composite-префикс
                      (linked land) и держатель байтов (holder) -- разные
                      понятия и хранятся раздельно.
* TIER2_STRONG     -- uuid composite-ключа найден в каталоге, но байтов
                      геометрии с нужным md5 нет. Identity сильная, история
                      полигона недоступна; текущий полигон -- только справка.
* TIER3_SUPPORTED  -- явная lineage uuid через другие точно разрешённые
                      записи; противоречие -> TIER5.
* TIER4_GEOMETRIC  -- optional candidate: >= 80 % точек маршрута с проверенной
                      идентичностью внутри ОДНОГО текущего полигона.
* TIER5_UNKNOWN    -- всё остальное: keyless, manual, конфликт.

Здесь нет ввода-вывода, Flask и базы: каталог передаётся объектом с
чистыми методами (см. ``FieldCatalog``).
"""

import math
import re

from dji_area import FIELD_RESOLVER_VERSION

HEX32 = re.compile(r'^[0-9a-f]{32}$')
UUID_RE = re.compile(
    r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$')

KEY_PLAIN_MD5 = 'PLAIN_MD5'
KEY_COMPOSITE = 'COMPOSITE_UUID_MD5'
KEY_NONE = 'NONE'
KEY_UNRECOGNIZED = 'UNRECOGNIZED'

TIER1_EXACT = 'TIER1_EXACT'
TIER2_STRONG = 'TIER2_STRONG'
TIER3_SUPPORTED = 'TIER3_SUPPORTED'
TIER4_GEOMETRIC = 'TIER4_GEOMETRIC'
TIER5_UNKNOWN = 'TIER5_UNKNOWN'
TIERS = (TIER1_EXACT, TIER2_STRONG, TIER3_SUPPORTED, TIER4_GEOMETRIC,
         TIER5_UNKNOWN)

CONF_HIGH = 'HIGH'
CONF_MEDIUM = 'MEDIUM'
CONF_LOW = 'LOW'
CONF_UNKNOWN = 'UNKNOWN'

# Внешний reference threshold TIER4; отдельная версия эвристики.
TIER4_MIN_INSIDE_SHARE = 0.80
TIER4_HEURISTIC_VERSION = 'route-inside-current-polygon-80pct-1'


def parse_geometry_key(raw):
    """(format, uuid, md5). Никаких подстрок произвольной длины."""
    if raw is None:
        return KEY_NONE, None, None
    text = str(raw).strip().lower()
    if not text:
        return KEY_NONE, None, None
    if HEX32.match(text):
        return KEY_PLAIN_MD5, None, text
    if '__' in text:
        uuid, md5 = text.split('__', 1)
        if UUID_RE.match(uuid) and HEX32.match(md5):
            return KEY_COMPOSITE, uuid, md5
    return KEY_UNRECOGNIZED, None, None


class FieldCatalog(object):
    """Интерфейс каталога для резолвера. Реализуется поверх БД или dict.

    * ``geometry_by_md5(md5)`` -> None | {'geometry_object_id', 'sha256',
      'holder_land_uuids': [...], 'verified': bool}
    * ``land_revisions(uuid)`` -> [] | [{'land_uuid', 'name', 'serial_number',
      'geometry_md5', 'snapshot_id', 'land_revision_id', 'created_at_source'}]
      -- все известные revisions этого uuid, любые snapshots
    * ``lineage_uuids_for_md5(md5, exclude_flight_id)`` -> {uuid: [flight ids]}
      -- composite-ключи ДРУГИХ вылетов с тем же md5
    * ``lineage_holders_for_uuid(uuid, exclude_flight_id)`` -> {holder_uuid:
      [flight ids]} -- holders, к которым ДРУГИЕ composite-вылеты с тем же
      uuid разрешились точно (TIER1)
    * ``holders_for_md5(md5)`` -> [land uuids], чьи metadata-ревизии несут
      этот contentMd5 (идентичность геометрии по каталогу без байтов)
    """

    def geometry_by_md5(self, md5):  # pragma: no cover - interface
        raise NotImplementedError

    def land_revisions(self, uuid):  # pragma: no cover - interface
        raise NotImplementedError

    def lineage_uuids_for_md5(self, md5, exclude_flight_id=None):  # pragma: no cover
        raise NotImplementedError

    def lineage_holders_for_uuid(self, uuid, exclude_flight_id=None):  # pragma: no cover
        raise NotImplementedError

    def holders_for_md5(self, md5):  # pragma: no cover - interface
        raise NotImplementedError


class DictCatalog(FieldCatalog):
    """Каталог в памяти -- для тестов и для инструмента пересчёта."""

    def __init__(self, geometries=None, lands=None, flight_keys=None):
        # geometries: {md5: {...}}; lands: {uuid: [revisions]};
        # flight_keys: {flight_id: geometry_key_raw} для lineage.
        self.geometries = geometries or {}
        self.lands = lands or {}
        self.flight_keys = flight_keys or {}

    def geometry_by_md5(self, md5):
        return self.geometries.get(md5)

    def land_revisions(self, uuid):
        return list(self.lands.get(uuid) or [])

    def holders_for_md5(self, md5):
        return sorted(uuid for uuid, revisions in self.lands.items()
                      if any(r.get('geometry_md5') == md5 for r in revisions))

    def lineage_uuids_for_md5(self, md5, exclude_flight_id=None):
        out = {}
        for fid, key in self.flight_keys.items():
            if fid == exclude_flight_id:
                continue
            fmt, uuid, key_md5 = parse_geometry_key(key)
            if fmt == KEY_COMPOSITE and key_md5 == md5:
                out.setdefault(uuid, []).append(fid)
        return out

    def lineage_holders_for_uuid(self, uuid, exclude_flight_id=None):
        out = {}
        for fid, key in self.flight_keys.items():
            if fid == exclude_flight_id:
                continue
            fmt, key_uuid, key_md5 = parse_geometry_key(key)
            if fmt != KEY_COMPOSITE or key_uuid != uuid:
                continue
            geometry = self.geometries.get(key_md5)
            if not geometry or not geometry.get('verified'):
                continue
            for holder in geometry.get('holder_land_uuids') or []:
                out.setdefault(holder, []).append(fid)
        return out


def _latest_revision(revisions):
    """Последняя известная revision uuid: по snapshot, затем по id."""
    if not revisions:
        return None
    return sorted(revisions,
                  key=lambda r: (r.get('snapshot_id') or 0,
                                 r.get('land_revision_id') or 0))[-1]


def _result(**kwargs):
    base = {
        'field_resolver_version': FIELD_RESOLVER_VERSION,
        'geometry_key_raw': None,
        'geometry_key_format': KEY_NONE,
        'geometry_md5': None,
        'linked_land_uuid': None,
        'geometry_holder_land_uuid': None,
        'geometry_object_id': None,
        'historical_geometry_available': False,
        'historical_geometry_sha256': None,
        'field_attribution_tier': TIER5_UNKNOWN,
        'field_attribution_method': None,
        'field_confidence': CONF_UNKNOWN,
        'field_land_uuid': None,
        'field_name_at_snapshot': None,
        'field_serial_number': None,
        'land_snapshot_id': None,
        'land_revision_id': None,
        'field_lineage_evidence_ids': [],
        'holder_count': 0,
        'candidate_count': 0,
        'warnings': [],
    }
    base.update(kwargs)
    return base


def resolve_field(geometry_key_raw, catalog, flight_id=None, manual_mode=None,
                  route_points=None, route_identity_ok=False,
                  current_polygons=None):
    """Резолвер поля. Чистая функция.

    ``route_points`` / ``route_identity_ok`` / ``current_polygons`` нужны
    только для optional TIER4: полигоны -- [{'land_uuid','name','rings':
    [[(lat,lng),...]], 'snapshot_id'}]; без них TIER4 не пробуется.
    """
    fmt, uuid, md5 = parse_geometry_key(geometry_key_raw)
    res = _result(geometry_key_raw=geometry_key_raw, geometry_key_format=fmt,
                  geometry_md5=md5, linked_land_uuid=uuid)

    if fmt == KEY_UNRECOGNIZED:
        res['field_attribution_method'] = 'UNPARSED_KEY'
        res['warnings'].append('GEOMETRY_KEY_UNRECOGNIZED')
        return res

    if fmt == KEY_NONE:
        res['field_attribution_method'] = ('MANUAL_NO_KEY' if manual_mode
                                           else 'AUTO_NO_KEY')
        return _try_tier4(res, route_points, route_identity_ok,
                          current_polygons)

    # ── TIER1: байты геометрии по hash ───────────────────────────────────
    geometry = catalog.geometry_by_md5(md5)
    linked_revisions = catalog.land_revisions(uuid) if uuid else []
    if geometry and geometry.get('verified'):
        holders = list(geometry.get('holder_land_uuids') or [])
        res['geometry_object_id'] = geometry.get('geometry_object_id')
        res['historical_geometry_available'] = True
        res['historical_geometry_sha256'] = geometry.get('sha256')
        res['holder_count'] = len(holders)
        res['field_attribution_tier'] = TIER1_EXACT
        res['field_confidence'] = CONF_HIGH
        # [REASON]: держатель байтов и привязанный land -- разные записи:
        # после правки поля старый uuid остаётся в ключе, а байты нужной
        # версии хранит более поздняя запись. Оба сохраняются.
        holder = None
        if uuid and uuid in holders:
            holder = uuid
        elif holders:
            holder = sorted(holders)[0]
            if len(holders) > 1:
                res['warnings'].append('MULTIPLE_GEOMETRY_HOLDERS')
        res['geometry_holder_land_uuid'] = holder
        if uuid:
            if linked_revisions:
                res['field_attribution_method'] = (
                    'COMPOSITE_UUID_CURRENT_GEOMETRY' if holder == uuid
                    else 'COMPOSITE_UUID_GEOMETRY_VIA_TWIN')
                _apply_identity(res, uuid, linked_revisions)
            else:
                res['field_attribution_method'] = (
                    'COMPOSITE_UUID_DELETED_GEOMETRY_VIA_TWIN')
                res['warnings'].append('LINKED_LAND_UUID_NOT_IN_CATALOG')
                if holder:
                    _apply_identity(res, holder,
                                    catalog.land_revisions(holder))
        else:
            res['field_attribution_method'] = 'PLAIN_MD5_GEOMETRY_OBJECT'
            if holder:
                _apply_identity(res, holder, catalog.land_revisions(holder))
        if uuid and holder and holder != uuid:
            res['warnings'].append('HOLDER_UUID_NE_LINKED_UUID')
        return res

    # ── TIER2: identity сильная, байты нужной версии не сохранены ────────
    catalog_holders = list(catalog.holders_for_md5(md5) or [])
    if uuid and linked_revisions:
        res['field_attribution_tier'] = TIER2_STRONG
        # [REASON]: md5 ключа совпал с contentMd5 записи каталога -- объект
        # геометрии опознан по metadata, но байты не сохранены; это не
        # TIER1 (bytes не проверены) и не «поле неизвестно».
        res['field_attribution_method'] = (
            'COMPOSITE_UUID_CATALOG_MD5_BYTES_UNAVAILABLE'
            if catalog_holders else 'COMPOSITE_UUID_ONLY')
        res['field_confidence'] = CONF_HIGH
        res['warnings'].append('HISTORICAL_GEOMETRY_UNAVAILABLE')
        res['holder_count'] = len(catalog_holders)
        if catalog_holders:
            res['geometry_holder_land_uuid'] = (
                uuid if uuid in catalog_holders else sorted(catalog_holders)[0])
        _apply_identity(res, uuid, linked_revisions)
        return res
    if not uuid and catalog_holders:
        holder = sorted(catalog_holders)[0]
        res['field_attribution_tier'] = TIER2_STRONG
        res['field_attribution_method'] = 'PLAIN_MD5_CATALOG_MATCH_BYTES_UNAVAILABLE'
        res['field_confidence'] = CONF_HIGH
        res['warnings'].append('HISTORICAL_GEOMETRY_UNAVAILABLE')
        res['holder_count'] = len(catalog_holders)
        if len(catalog_holders) > 1:
            res['warnings'].append('MULTIPLE_GEOMETRY_HOLDERS')
        res['geometry_holder_land_uuid'] = holder
        _apply_identity(res, holder, catalog.land_revisions(holder))
        return res

    # ── TIER3: lineage через другие точные записи ────────────────────────
    if uuid:
        holders = catalog.lineage_holders_for_uuid(uuid, flight_id)
        if holders:
            if len(holders) > 1:
                res['field_attribution_method'] = (
                    'COMPOSITE_UUID_LINEAGE_CONFLICT')
                res['warnings'].append('LINEAGE_CONFLICT')
                res['candidate_count'] = len(holders)
                return res
            holder, evidence = next(iter(holders.items()))
            res['field_attribution_tier'] = TIER3_SUPPORTED
            res['field_attribution_method'] = (
                'COMPOSITE_UUID_DELETED_LINEAGE_VIA_OTHER_FLIGHTS')
            res['field_confidence'] = CONF_MEDIUM
            res['field_lineage_evidence_ids'] = sorted(evidence)
            res['geometry_holder_land_uuid'] = holder
            _apply_identity(res, holder, catalog.land_revisions(holder))
            return res
        res['field_attribution_method'] = 'COMPOSITE_UUID_AND_MD5_NOT_IN_CATALOG'
    else:
        lineage = catalog.lineage_uuids_for_md5(md5, flight_id)
        if lineage:
            if len(lineage) > 1:
                res['field_attribution_method'] = 'PLAIN_MD5_LINEAGE_CONFLICT'
                res['warnings'].append('LINEAGE_CONFLICT')
                res['candidate_count'] = len(lineage)
                return res
            lineage_uuid, evidence = next(iter(lineage.items()))
            revisions = catalog.land_revisions(lineage_uuid)
            if revisions:
                res['field_attribution_tier'] = TIER3_SUPPORTED
                res['field_attribution_method'] = (
                    'PLAIN_MD5_LINKED_VIA_OTHER_FLIGHTS_COMPOSITE_KEY')
                res['field_confidence'] = CONF_MEDIUM
                res['field_lineage_evidence_ids'] = sorted(evidence)
                _apply_identity(res, lineage_uuid, revisions)
                return res
        res['field_attribution_method'] = 'PLAIN_MD5_NOT_IN_CATALOG'

    return _try_tier4(res, route_points, route_identity_ok, current_polygons)


def _apply_identity(res, land_uuid, revisions):
    rev = _latest_revision(revisions)
    res['field_land_uuid'] = land_uuid
    if rev is None:
        return
    res['field_name_at_snapshot'] = rev.get('name')
    res['field_serial_number'] = rev.get('serial_number')
    res['land_snapshot_id'] = rev.get('snapshot_id')
    res['land_revision_id'] = rev.get('land_revision_id')


# ─── TIER4: геометрический кандидат ─────────────────────────────────────────

def point_in_ring(lat, lng, ring):
    """Ray casting, even-odd. ``ring`` -- [(lat, lng), ...]."""
    inside = False
    n = len(ring)
    if n < 3:
        return False
    j = n - 1
    for i in range(n):
        yi, xi = ring[i]
        yj, xj = ring[j]
        if (yi > lat) != (yj > lat):
            x_cross = (xj - xi) * (lat - yi) / (yj - yi) + xi
            if lng < x_cross:
                inside = not inside
        j = i
    return inside


def containment_share(points, rings):
    if not points:
        return 0.0
    inside = 0
    for lat, lng in points:
        if any(point_in_ring(lat, lng, ring) for ring in rings):
            inside += 1
    return inside / float(len(points))


def _try_tier4(res, route_points, route_identity_ok, current_polygons):
    if not route_points or not route_identity_ok or not current_polygons:
        return res
    usable = [(float(p[0]), float(p[1])) for p in route_points
              if isinstance(p, (list, tuple)) and len(p) >= 2]
    if len(usable) < 2:
        return res
    lat0 = sum(p[0] for p in usable) / len(usable)
    lng0 = sum(p[1] for p in usable) / len(usable)
    candidates = []
    for poly in current_polygons:
        rings = poly.get('rings') or []
        if not rings:
            continue
        # Грубый предфильтр по центру полигона, как в исследовании.
        center = poly.get('center')
        if center is not None:
            if abs(center[0] - lat0) > 0.02 or abs(center[1] - lng0) > 0.03:
                continue
        share = containment_share(usable, rings)
        if share >= TIER4_MIN_INSIDE_SHARE:
            candidates.append((share, poly))
    res['candidate_count'] = len(candidates)
    if len(candidates) != 1:
        if len(candidates) > 1:
            res['warnings'].append('TIER4_AMBIGUOUS_CANDIDATES')
        return res
    share, poly = candidates[0]
    res['field_attribution_tier'] = TIER4_GEOMETRIC
    res['field_attribution_method'] = 'ROUTE_INSIDE_CURRENT_POLYGON_CANDIDATE'
    res['field_confidence'] = CONF_LOW
    res['field_land_uuid'] = poly.get('land_uuid')
    res['field_name_at_snapshot'] = poly.get('name')
    res['land_snapshot_id'] = poly.get('snapshot_id')
    res['land_revision_id'] = poly.get('land_revision_id')
    res['warnings'].append('TIER4_CANDIDATE_ONLY_%d_PCT' % int(math.floor(
        share * 100)))
    res['tier4_inside_share'] = round(share, 4)
    res['tier4_heuristic_version'] = TIER4_HEURISTIC_VERSION
    return res


def rings_from_geojson(document):
    """Кольца PlantZone из FeatureCollection DJI -> [[(lat, lng), ...]].

    Координаты DJI -- [lng, lat, 0]; здесь переворачиваются в (lat, lng).
    Препятствия (ObstacleZone) и ReferencePoint не входят.
    """
    rings = []
    if not isinstance(document, dict):
        return rings
    features = document.get('features')
    if not isinstance(features, list):
        return rings
    for feature in features:
        if not isinstance(feature, dict):
            continue
        # [REASON]: `properties` и `geometry` берутся из сетевого документа и
        # вовсе не обязаны быть объектами: строка на их месте давала
        # AttributeError, которого не ждал ни один вызывающий.
        props = feature.get('properties')
        if not isinstance(props, dict):
            props = {}
        if props.get('funcType') not in (None, 'PlantZone'):
            continue
        geometry = feature.get('geometry')
        if not isinstance(geometry, dict):
            continue
        gtype = geometry.get('type')
        coords = geometry.get('coordinates')
        polygons = []
        if gtype == 'Polygon' and isinstance(coords, list):
            polygons = [coords]
        elif gtype == 'MultiPolygon' and isinstance(coords, list):
            polygons = coords
        for polygon in polygons:
            # [REASON]: документ приходит из сети и разбирается ДО того, как
            # его увидит гвардия md5 (клиент вправе не прислать content_md5).
            # `polygon[0]` на словаре давал KeyError, `float(None)` -- TypeError:
            # ни того ни другого не ловил ни приёмник, ни импортёр, и один
            # кривой полигон откатывал пачку до тысячи полей целиком. Разбор
            # обязан отвечать пропуском либо ValueError -- их ждут оба
            # вызывающих.
            if not isinstance(polygon, (list, tuple)) or not polygon:
                continue
            outer = polygon[0]
            if not isinstance(outer, (list, tuple)):
                continue
            ring = []
            for pt in outer:
                if isinstance(pt, (list, tuple)) and len(pt) >= 2:
                    try:
                        ring.append((float(pt[1]), float(pt[0])))
                    except (TypeError, ValueError):
                        ring = []
                        break
            if len(ring) >= 3:
                rings.append(ring)
    return rings
