# -*- coding: utf-8 -*-
"""drone_collector/geometry.py -- полные контуры полей DJI.

    python -m drone_collector.main --lands --with-geometry
    python -m drone_collector.main --lands --with-geometry --dry-run

ЧТО ЭТО ДОБАВЛЯЕТ К `--lands`

Обычный снимок справочника (`lands.py`) намеренно НЕ качает полигоны: они
лежат за `geometry.storage.signedURL` -- предварительно подписанной ссылкой со
сроком жизни в шесть часов, и 5 489 таких ссылок это 5 489 временных учётных
свидетельств. Здесь этот шаг делается, и делается по правилам §9 архитектуры.

ПОРЯДОК РАБОТЫ С ПОДПИСАННОЙ ССЫЛКОЙ

1. ссылка берётся из объекта справочника, УЖЕ находящегося в памяти;
2. по ней скачивается тело -- в память, не в файл;
3. считается md5 и сверяется с `contentMd5`, который назвал сам DJI;
4. считается наш sha256;
5. в очередь уходят геометрия и хеши -- **ссылка не уходит никуда**;
6. ссылка затирается в рабочем объекте немедленно.

Ссылка не попадает в базу, в лог, в отчёт, в фикстуру, в git, в API и в
интерфейс. Ни одно исключение этого модуля не несёт её текста: сообщения
чужих библиотек проходят через `scrub`, который вырезает из них любой URL.

ЧТО СОХРАНЯЕТСЯ

**Весь исходный `FeatureCollection` целиком, дословно.** Не выжимка из него.
Подтверждено на файле контура `P03335975` (`V2_FIELD_GEOMETRY_CONFIRMED`):
кроме самого `Polygon` с `funcType=PlantZone` там лежат `parameters.offset`
из 22 значений и пустой `MultiPoint` с `funcType=ReferencePoint`. Смысл
второго нам неизвестен, и именно поэтому он сохраняется: выбросить
непонятое -- значит решить за следующего читателя, что оно не нужно.

ВЕРСИИ

Версия контура -- это `contentMd5`. Та же версия второй раз не скачивается
вовсе (кеш) и не создаёт второй записи (дедупликация очереди). Новая версия
кладётся рядом со старой; старая никогда не переписывается.
"""

import hashlib
import json
import logging
import math
import re
import time

from drone_collector.outbox import (KIND_FIELD_GEOMETRY, find_secret_markers,
                                    utc_now_iso)

log = logging.getLogger(__name__)

# 1 гектар = 15 му ровно. Установлено на живых ответах DJI, записано в
# docs/tracks/drones.md §3 и используется приёмником `land_sync`.
MU_PER_HECTARE = 15.0

# Потолок одного файла геометрии.
#
# [REASON]: контур `P03335975` -- 22 вершины, единицы килобайт. Четыре
# мегабайта покрывают контур в тысячи вершин и не дают одному ответу съесть
# память процесса, который обходит пять с половиной тысяч контуров подряд.
MAX_GEOMETRY_BYTES = 4 * 1024 * 1024

# Три попытки: первая, затем 2 с и 4 с. Тот же порядок, что у `sender.py` и
# `routes.py`, чтобы в проекте не было трёх разных политик повтора.
RETRY_ATTEMPTS = 3
RETRY_BACKOFF_SECONDS = (2, 4)

# Пауза между скачиваниями. Пять с половиной тысяч файлов подряд -- это уже
# нагрузка на чужой сервис, и темп задаём мы, а не он.
DEFAULT_DOWNLOAD_PAUSE_SECONDS = 0.35

# Допуск сверки нашей площади с `totalArea` DJI, проценты.
#
# [REASON]: это ТЕХНИЧЕСКИЙ допуск проверки формата -- «мы прочитали файл
# правильно?», -- а не допуск коммерческого расчёта. Разбор в другой системе
# координат или с перепутанным порядком координат промахивается в разы, а не
# на проценты. На подтверждённом контуре расхождение вышло +0.22 %.
AREA_TOLERANCE_PERCENT = 1.0

EARTH_RADIUS_M = 6378137.0

GEOMETRY_COLLECTOR_VERSION = 'geometry-1'

# Статусы обработки одного контура. Ошибка ВСЕГДА имеет имя: «не получилось»
# без кода причины не даёт понять, чинить сеть, формат или права.
STATUS_OK = 'OK'
STATUS_UNCHANGED = 'SKIPPED_UNCHANGED'
STATUS_NO_GEOMETRY = 'NO_GEOMETRY'
STATUS_DOWNLOAD_FAILED = 'DOWNLOAD_FAILED'
STATUS_TOO_LARGE = 'TOO_LARGE'
STATUS_MD5_MISMATCH = 'MD5_MISMATCH'
STATUS_UNPARSEABLE = 'UNPARSEABLE'
STATUS_INVALID_GEOMETRY = 'INVALID_GEOMETRY'
STATUS_SECRET_IN_PAYLOAD = 'SECRET_IN_PAYLOAD'
STATUS_AREA_MISMATCH = 'AREA_MISMATCH'

STATUSES = (STATUS_OK, STATUS_UNCHANGED, STATUS_NO_GEOMETRY,
            STATUS_DOWNLOAD_FAILED, STATUS_TOO_LARGE, STATUS_MD5_MISMATCH,
            STATUS_UNPARSEABLE, STATUS_INVALID_GEOMETRY,
            STATUS_SECRET_IN_PAYLOAD, STATUS_AREA_MISMATCH)

# Всё, что похоже на ссылку. Вырезается из любого текста, который мы не
# писали сами.
_URL = re.compile(r'\b[a-zA-Z][a-zA-Z0-9+.-]*://\S+')

URL_PLACEHOLDER = '<url-removed>'


class GeometryError(Exception):
    """Контур обработать не удалось. Текст НИКОГДА не несёт ссылки."""


def scrub(text):
    """Текст без ссылок и без маркеров секретов.

    [REASON]: сообщение чужой библиотеки -- самый вероятный путь, которым
    подписанная ссылка попадает в лог. `requests` и Playwright охотно
    вставляют URL в текст исключения, а лог прогона живёт на диске службы
    месяцами. Поэтому чужой текст не пересказывается, а чистится.
    """
    cleaned = _URL.sub(URL_PLACEHOLDER, str(text))
    if find_secret_markers(cleaned):
        return ('the message carried something that must not be logged; it '
                'was dropped entirely')
    return cleaned


# ─── Разбор GeoJSON ──────────────────────────────────────────────────────────
#
# [REASON]: разбор написан здесь, а не взят из `tools/drone_field_geometry_probe.py`.
# collector -- отдельный процесс со своим venv и своим requirements.txt, и
# устав прямо запрещает тащить в него зависимости приложения. Формула площади
# кольца совпадает с формулой валидатора намеренно: они обязаны давать одно и
# то же число, и это проверяется тестом на одном и том же контуре.

def ring_area_m2(ring):
    """Площадь кольца на сфере, м2. Знак говорит о направлении обхода."""
    if len(ring) < 3:
        return 0.0
    total = 0.0
    count = len(ring)
    for index in range(count):
        lon1, lat1 = ring[index][0], ring[index][1]
        lon2, lat2 = ring[(index + 1) % count][0], ring[(index + 1) % count][1]
        total += (math.radians(lon2 - lon1)
                  * (2 + math.sin(math.radians(lat1))
                     + math.sin(math.radians(lat2))))
    return total * EARTH_RADIUS_M * EARTH_RADIUS_M / 2.0


def _finite(value):
    return (not isinstance(value, bool) and isinstance(value, (int, float))
            and math.isfinite(value))


def _valid_ring(ring):
    """(годно, причина). Кольцо должно быть замкнутым и в градусах WGS84."""
    if not isinstance(ring, list) or len(ring) < 4:
        return False, 'ring has fewer than 4 positions'
    for index, position in enumerate(ring):
        if not isinstance(position, (list, tuple)) or len(position) < 2:
            return False, 'position %d is not a coordinate pair' % index
        lon, lat = position[0], position[1]
        if not _finite(lon) or not _finite(lat):
            return False, 'position %d is not finite' % index
        if not -180.0 <= lon <= 180.0:
            return False, 'position %d: longitude out of range' % index
        if not -90.0 <= lat <= 90.0:
            return False, 'position %d: latitude out of range' % index
    if list(ring[0][:2]) != list(ring[-1][:2]):
        return False, 'ring is not closed'
    if len({(p[0], p[1]) for p in ring}) < 3:
        return False, 'ring has fewer than 3 distinct vertices'
    return True, ''


def extract_shapes(document):
    """[(тип, внешнее кольцо, [дыры], properties)] из GeoJSON.

    Поддержаны `Polygon` и `MultiPolygon`, в `Feature`, в `FeatureCollection`
    и голой геометрией. `MultiPoint` (`ReferencePoint` из подтверждённого
    файла) полигоном не считается -- и не выбрасывается: он остаётся в
    дословно сохранённом документе.
    """
    shapes = []

    def take(geometry, properties):
        if not isinstance(geometry, dict):
            return
        kind = geometry.get('type')
        coordinates = geometry.get('coordinates')
        if kind == 'Polygon' and isinstance(coordinates, list) and coordinates:
            shapes.append(('Polygon', coordinates[0], list(coordinates[1:]),
                           properties))
        elif kind == 'MultiPolygon' and isinstance(coordinates, list):
            for polygon in coordinates:
                if isinstance(polygon, list) and polygon:
                    shapes.append(('MultiPolygon', polygon[0],
                                   list(polygon[1:]), properties))
        elif kind == 'GeometryCollection':
            for child in geometry.get('geometries') or ():
                take(child, properties)

    if not isinstance(document, dict):
        return shapes
    if document.get('type') == 'FeatureCollection':
        for feature in document.get('features') or ():
            if isinstance(feature, dict):
                take(feature.get('geometry'), feature.get('properties') or {})
    elif document.get('type') == 'Feature':
        take(document.get('geometry'), document.get('properties') or {})
    else:
        take(document, {})
    return shapes


def describe_geometry(document):
    """Сводка по документу: площадь, кольца, функции, смещения.

    Возвращает (описание, список причин отказа). Пустой список причин --
    геометрия годна.
    """
    shapes = extract_shapes(document)
    reasons = []
    if not shapes:
        reasons.append('no Polygon or MultiPolygon in the document')
        return {'shapes': [], 'area_ha': None, 'func_types': [],
                'offsets': [], 'reference_points': 0,
                'other_geometry_types': _other_types(document)}, reasons

    described = []
    total_m2 = 0.0
    for index, (kind, outer, holes, properties) in enumerate(shapes):
        ok, why = _valid_ring(outer)
        if not ok:
            reasons.append('shape %d outer ring: %s' % (index, why))
            continue
        hole_area = 0.0
        for hole_index, hole in enumerate(holes):
            hole_ok, hole_why = _valid_ring(hole)
            if not hole_ok:
                reasons.append('shape %d hole %d: %s'
                               % (index, hole_index, hole_why))
                continue
            hole_area += abs(ring_area_m2(hole))
        area = abs(ring_area_m2(outer)) - hole_area
        total_m2 += area
        described.append({
            'type': kind,
            'positions': len(outer),
            'distinct_vertices': len({(p[0], p[1]) for p in outer}),
            'holes': len(holes),
            'area_ha': area / 10000.0,
            'func_type': (properties or {}).get('funcType'),
            'offset_count': _offset_count(properties),
        })

    if not described:
        return {'shapes': [], 'area_ha': None, 'func_types': [],
                'offsets': [], 'reference_points': 0,
                'other_geometry_types': _other_types(document)}, reasons
    if total_m2 <= 0 or not math.isfinite(total_m2):
        reasons.append('the total area is zero, negative or not finite')

    return {
        'shapes': described,
        'area_ha': total_m2 / 10000.0,
        'func_types': sorted({shape['func_type'] for shape in described
                              if shape['func_type']}),
        'offsets': _all_offsets(document),
        'reference_points': _reference_point_count(document),
        'other_geometry_types': _other_types(document),
    }, reasons


def _offset_count(properties):
    parameters = (properties or {}).get('parameters')
    if isinstance(parameters, dict) and isinstance(parameters.get('offset'),
                                                   list):
        return len(parameters['offset'])
    return None


def _all_offsets(document):
    """Все наборы `parameters.offset`, как есть. Числа не усредняются."""
    found = []
    for feature in _features(document):
        parameters = (feature.get('properties') or {}).get('parameters')
        if isinstance(parameters, dict) and isinstance(
                parameters.get('offset'), list):
            found.append(parameters['offset'])
    return found


def _reference_point_count(document):
    """Сколько `funcType=ReferencePoint` в документе. Пустой -- тоже считается.

    [REASON]: в подтверждённом файле `ReferencePoint` пустой -- `MultiPoint`
    без координат. Считать «пустой значит нет» нельзя: сам факт присутствия
    признака -- это наблюдение, и оно должно доехать до следующего читателя.
    """
    count = 0
    for feature in _features(document):
        if (feature.get('properties') or {}).get('funcType') == 'ReferencePoint':
            count += 1
    return count


def _features(document):
    if not isinstance(document, dict):
        return []
    if document.get('type') == 'FeatureCollection':
        return [f for f in (document.get('features') or ())
                if isinstance(f, dict)]
    if document.get('type') == 'Feature':
        return [document]
    return []


def _other_types(document):
    """Типы геометрий, не являющиеся полигонами. Наблюдение, а не ошибка."""
    kinds = set()
    for feature in _features(document):
        geometry = feature.get('geometry')
        if isinstance(geometry, dict):
            kind = geometry.get('type')
            if kind and kind not in ('Polygon', 'MultiPolygon'):
                kinds.add(kind)
    return sorted(kinds)


# ─── Один контур ─────────────────────────────────────────────────────────────

class ContourSource(object):
    """Что справочник знает о контуре ДО скачивания.

    [REASON]: ссылка живёт в поле `_signed_url`, которое `__repr__` не
    печатает, а `describe()` не возвращает. Объект такого рода рано или
    поздно попадёт в `log.debug('%s', obj)`, и это должно быть безопасно.
    """

    # [REASON]: поле называется `field_serial`, а НЕ `serial_number`. В этом
    # пакете `serial_number` уже занят: так называется поле строки вылета, и
    # оно уникально на вылет, а не на машину -- 10 385 записей это показали, и
    # на это стоит структурный тест. Серийник контура (`P03335975`) -- совсем
    # другая величина, и два разных смысла под одним именем в одном пакете
    # рано или поздно сойдутся в чьей-нибудь голове в один.
    __slots__ = ('uuid', 'field_serial', 'name', 'content_md5',
                 'total_area_mu', '_signed_url')

    def __init__(self, uuid, signed_url=None, content_md5=None,
                 field_serial=None, name=None, total_area_mu=None):
        self.uuid = uuid
        self.field_serial = field_serial
        self.name = name
        self.content_md5 = content_md5
        self.total_area_mu = total_area_mu
        self._signed_url = signed_url

    @property
    def has_link(self):
        return bool(self._signed_url)

    def take_link(self):
        """Отдать ссылку ОДИН раз и немедленно забыть её.

        [REASON]: затирание -- не украшение. Объект справочника живёт всё
        время прогона по пяти с половиной тысячам контуров; ссылка, оставшаяся
        в нём, доживёт до дампа памяти, до `repr` в трейсбеке и до отладочной
        печати, которую кто-нибудь добавит через полгода.
        """
        link = self._signed_url
        self._signed_url = None
        return link

    @property
    def area_ha_dji(self):
        if self.total_area_mu is None:
            return None
        try:
            return float(self.total_area_mu) / MU_PER_HECTARE
        except (TypeError, ValueError):
            return None

    def describe(self):
        """Безопасное описание: всё, кроме ссылки."""
        return {'uuid': self.uuid, 'field_serial': self.field_serial,
                'name': self.name, 'content_md5': self.content_md5,
                'total_area_mu': self.total_area_mu}

    def __repr__(self):
        return '<ContourSource %s md5=%s>' % (self.uuid, self.content_md5)


def contour_from_node(node):
    """ContourSource из узла справочника `lands`, или None."""
    if not isinstance(node, dict):
        return None
    uuid = node.get('uuid')
    if not uuid:
        return None
    storage = ((node.get('geometry') or {}).get('storage')
               if isinstance(node.get('geometry'), dict) else None)
    storage = storage if isinstance(storage, dict) else {}
    return ContourSource(
        uuid=uuid,
        signed_url=storage.get('signedURL'),
        content_md5=storage.get('contentMd5'),
        field_serial=node.get('serialNumber'),
        name=node.get('name'),
        total_area_mu=node.get('totalArea'),
    )


class ContourOutcome(object):
    """Результат обработки одного контура."""

    __slots__ = ('uuid', 'status', 'detail', 'content_md5', 'sha256',
                 'area_ha_computed', 'area_ha_dji', 'queued')

    def __init__(self, uuid, status, detail=None, content_md5=None,
                 sha256=None, area_ha_computed=None, area_ha_dji=None,
                 queued=False):
        self.uuid = uuid
        self.status = status
        self.detail = detail
        self.content_md5 = content_md5
        self.sha256 = sha256
        self.area_ha_computed = area_ha_computed
        self.area_ha_dji = area_ha_dji
        self.queued = queued

    def as_dict(self):
        return {name: getattr(self, name) for name in self.__slots__}

    def __repr__(self):
        return '<ContourOutcome %s %s>' % (self.uuid, self.status)


class GeometryRunResult(object):
    """Счётчики прогона геометрии.

    Инвариант: `seen = sum(by_status.values())`. Каждый контур получает ровно
    один статус -- «обработан молча» состояния нет.
    """

    __slots__ = ('seen', 'downloaded', 'queued', 'duplicates', 'bytes',
                 'by_status')

    def __init__(self):
        self.seen = 0
        self.downloaded = 0
        self.queued = 0
        self.duplicates = 0
        self.bytes = 0
        self.by_status = {}

    def note(self, outcome):
        self.seen += 1
        self.by_status[outcome.status] = self.by_status.get(outcome.status,
                                                            0) + 1
        if outcome.queued:
            self.queued += 1

    @property
    def invariant_holds(self):
        return self.seen == sum(self.by_status.values())

    def as_dict(self):
        return {'seen': self.seen, 'downloaded': self.downloaded,
                'queued': self.queued, 'duplicates': self.duplicates,
                'bytes': self.bytes, 'by_status': dict(self.by_status)}

    def __repr__(self):
        return 'GeometryRunResult(%s)' % self.as_dict()


class GeometryRun(object):
    """Скачивание полных контуров по справочнику, уже лежащему в памяти.

        run = GeometryRun(outbox, download_fn)
        result = run.collect(nodes)

    `download_fn(url) -> bytes` -- единственная точка, которой нужна сеть.
    """

    def __init__(self, outbox, download_fn, logger=None, sleep_fn=None,
                 pause_s=DEFAULT_DOWNLOAD_PAUSE_SECONDS, dry_run=False,
                 area_tolerance_percent=AREA_TOLERANCE_PERCENT):
        self.outbox = outbox
        self.download_fn = download_fn
        self.log = logger or log
        self.sleep = sleep_fn or time.sleep
        self.pause_s = pause_s
        self.dry_run = dry_run
        self.area_tolerance_percent = area_tolerance_percent
        self.prepared_bodies = []
        self._known_versions = None

    # -- кеш версий -----------------------------------------------------------

    def known_versions(self):
        """{(uuid, content_md5)} -- версии, уже лежащие в очереди.

        [REASON]: кеш строится по СОДЕРЖИМОМУ очереди, а не по отдельному
        файлу состояния. Отдельный файл расходится с очередью при первом же
        обрыве, и тогда кеш либо просит заново собранное, либо молча
        пропускает несобранное.
        """
        if self._known_versions is not None:
            return self._known_versions
        found = set()
        if self.outbox is not None:
            from drone_collector.outbox import OutboxError
            for path in self.outbox.records(KIND_FIELD_GEOMETRY):
                try:
                    envelope = self.outbox.read(path)
                except OutboxError:
                    continue
                body = envelope.get('body') or {}
                found.add((envelope.get('identity'), body.get('content_md5')))
        self._known_versions = found
        return found

    # -- прогон ---------------------------------------------------------------

    def collect(self, nodes):
        """Обойти узлы справочника. Возвращает GeometryRunResult."""
        result = GeometryRunResult()
        sources = [contour_from_node(node) for node in nodes or ()]
        sources = [source for source in sources if source is not None]
        self.log.info('Field geometry: %d contour(s) to consider%s',
                      len(sources), ' (DRY RUN)' if self.dry_run else '')

        for index, source in enumerate(sources, start=1):
            outcome = self._one(source, result)
            result.note(outcome)
            if outcome.status != STATUS_OK:
                self.log.warning('Contour %s (%s): %s%s', source.uuid,
                                 source.field_serial or '-', outcome.status,
                                 ' -- %s' % outcome.detail
                                 if outcome.detail else '')
            if (index < len(sources) and self.pause_s
                    and outcome.status not in (STATUS_UNCHANGED,
                                               STATUS_NO_GEOMETRY)):
                self.sleep(self.pause_s)

        if not result.invariant_holds:
            self.log.error('COUNTER MISMATCH: seen=%d but the statuses sum to '
                           '%d.', result.seen, sum(result.by_status.values()))
        self.log.info('Field geometry run: %s', result.as_dict())
        return result

    def _one(self, source, result):
        """Обработать один контур. Ссылка не покидает этот метод."""
        if not source.has_link or not source.content_md5:
            return ContourOutcome(source.uuid, STATUS_NO_GEOMETRY,
                                  'the directory carries no geometry for it')

        if (source.uuid, source.content_md5) in self.known_versions():
            result.duplicates += 1
            return ContourOutcome(source.uuid, STATUS_UNCHANGED,
                                  content_md5=source.content_md5)

        link = source.take_link()
        try:
            blob = self._download_with_retries(source, link)
        except GeometryError as exc:
            return ContourOutcome(source.uuid, STATUS_DOWNLOAD_FAILED,
                                  scrub(exc), content_md5=source.content_md5)
        finally:
            # Ссылка живёт только в локальной переменной и умирает здесь.
            link = None

        if len(blob) > MAX_GEOMETRY_BYTES:
            return ContourOutcome(source.uuid, STATUS_TOO_LARGE,
                                  '%d bytes, the cap is %d'
                                  % (len(blob), MAX_GEOMETRY_BYTES),
                                  content_md5=source.content_md5)

        result.downloaded += 1
        result.bytes += len(blob)

        actual_md5 = hashlib.md5(blob).hexdigest()
        if actual_md5.lower() != str(source.content_md5).lower():
            # [REASON]: расхождение md5 -- это НЕ «скачалось криво». Это либо
            # другой объект, либо обрезанное тело; в обоих случаях полигон,
            # положенный в базу, будет полигоном чужого поля. Отказ, а не
            # предупреждение.
            return ContourOutcome(source.uuid, STATUS_MD5_MISMATCH,
                                  'DJI named a different content md5',
                                  content_md5=source.content_md5,
                                  sha256=hashlib.sha256(blob).hexdigest())

        digest = hashlib.sha256(blob).hexdigest()

        markers = find_secret_markers(blob.decode('utf-8', errors='replace'))
        if markers:
            # Файл геометрии с подписью внутри нельзя ни сохранить, ни
            # приложить к отчёту. То же правило, что у валидатора A3.
            return ContourOutcome(source.uuid, STATUS_SECRET_IN_PAYLOAD,
                                  'the payload carries %s' % ', '.join(markers),
                                  content_md5=source.content_md5, sha256=digest)

        try:
            document = json.loads(blob.decode('utf-8'))
        except (UnicodeDecodeError, ValueError) as exc:
            return ContourOutcome(source.uuid, STATUS_UNPARSEABLE,
                                  'not readable GeoJSON (%s)'
                                  % type(exc).__name__,
                                  content_md5=source.content_md5, sha256=digest)

        described, reasons = describe_geometry(document)
        if reasons:
            return ContourOutcome(source.uuid, STATUS_INVALID_GEOMETRY,
                                  '; '.join(reasons[:3]),
                                  content_md5=source.content_md5, sha256=digest)

        area_ha = described['area_ha']
        area_dji = source.area_ha_dji
        difference = _percent_difference(area_ha, area_dji)
        if (difference is not None
                and abs(difference) > self.area_tolerance_percent):
            # [REASON]: расхождение площадей больше технического допуска
            # означает, что файл прочитан НЕ ТАК -- другая система координат,
            # перепутанный порядок координат, не то кольцо. Это контроль
            # формата, а не контроль учёта: к тому, какое расхождение
            # приемлемо в гектарах, он отношения не имеет.
            return ContourOutcome(source.uuid, STATUS_AREA_MISMATCH,
                                  'our %.4f ha vs DJI %.4f ha (%.2f%%)'
                                  % (area_ha, area_dji, difference),
                                  content_md5=source.content_md5,
                                  sha256=digest, area_ha_computed=area_ha,
                                  area_ha_dji=area_dji)

        body = {
            'external_id': source.uuid,
            'field_serial': source.field_serial,
            'name': source.name,
            'content_md5': source.content_md5,
            'sha256': digest,
            'source_format': 'GEOJSON',
            'bytes': len(blob),
            'collector_version': GEOMETRY_COLLECTOR_VERSION,
            'area_ha_computed': area_ha,
            'area_ha_dji': area_dji,
            'area_difference_percent': difference,
            'summary': described,
            # Дословный документ. Не выжимка: `funcType`, `parameters.offset`,
            # `ReferencePoint` и любые неизвестные properties живут здесь.
            'geometry_geojson': document,
        }
        diagnostics = {
            'downloaded_at': utc_now_iso(),
            'func_types': described['func_types'],
            'reference_points': described['reference_points'],
            'other_geometry_types': described['other_geometry_types'],
            'offset_sets': len(described['offsets']),
        }

        if self.dry_run:
            self.prepared_bodies.append({'body': body,
                                         'diagnostics': diagnostics})
            return ContourOutcome(source.uuid, STATUS_OK,
                                  content_md5=source.content_md5, sha256=digest,
                                  area_ha_computed=area_ha,
                                  area_ha_dji=area_dji, queued=True)

        _path, duplicate = self.outbox.enqueue(
            KIND_FIELD_GEOMETRY, source.uuid, body, digest,
            diagnostics=diagnostics)
        if duplicate:
            result.duplicates += 1
            return ContourOutcome(source.uuid, STATUS_UNCHANGED,
                                  content_md5=source.content_md5, sha256=digest,
                                  area_ha_computed=area_ha,
                                  area_ha_dji=area_dji)
        return ContourOutcome(source.uuid, STATUS_OK,
                              content_md5=source.content_md5, sha256=digest,
                              area_ha_computed=area_ha, area_ha_dji=area_dji,
                              queued=True)

    def _download_with_retries(self, source, link):
        """Скачать тело. Ни одно сообщение отсюда не несёт ссылки."""
        last_error = None
        for attempt in range(1, RETRY_ATTEMPTS + 1):
            try:
                blob = self.download_fn(link)
            except Exception as exc:
                last_error = '%s: %s' % (type(exc).__name__, scrub(exc))
                self.log.warning('Contour %s attempt %d/%d failed (%s)',
                                 source.uuid, attempt, RETRY_ATTEMPTS,
                                 last_error)
            else:
                if isinstance(blob, (bytes, bytearray)) and blob:
                    return bytes(blob)
                last_error = 'the download returned an empty body'
                self.log.warning('Contour %s attempt %d/%d: %s', source.uuid,
                                 attempt, RETRY_ATTEMPTS, last_error)
            if attempt < RETRY_ATTEMPTS:
                delay = RETRY_BACKOFF_SECONDS[
                    min(attempt - 1, len(RETRY_BACKOFF_SECONDS) - 1)]
                self.sleep(delay)
        raise GeometryError('failed after %d attempt(s) (%s)'
                            % (RETRY_ATTEMPTS, last_error))


def _percent_difference(ours, theirs):
    if ours is None or theirs is None:
        return None
    if not _finite(ours) or not _finite(theirs) or theirs == 0:
        return None
    return (ours - theirs) / theirs * 100.0


# ─── Транспорт ───────────────────────────────────────────────────────────────

class ContextGeometryDownloader(object):
    """Скачивание через контекст браузера, в котором открыт кабинет.

    Тонкая обёртка: из контейнера разработки хранилище DJI недостижимо, и
    проверять здесь нечего, кроме формы ответа. Ссылка передаётся аргументом,
    нигде не сохраняется и не логируется.
    """

    def __init__(self, context, logger=None, timeout_ms=60000):
        self.context = context
        self.log = logger or log
        self.timeout_ms = timeout_ms

    def __call__(self, url):
        try:
            response = self.context.request.get(url, timeout=self.timeout_ms)
        except Exception as exc:
            raise GeometryError(scrub(exc))
        status = getattr(response, 'status', None)
        if status is not None and not 200 <= int(status) < 300:
            # Статус печатается, ссылка -- нет. 403 здесь почти всегда значит
            # «подпись истекла», и это надо видеть в журнале.
            raise GeometryError('the storage answered HTTP %s' % status)
        try:
            return response.body()
        except Exception as exc:
            raise GeometryError(scrub(exc))


def write_dry_run(result, prepared, out_dir):
    """Что было бы поставлено в очередь. Ни одной записи не создаётся."""
    from pathlib import Path
    target = Path(out_dir) / 'field_geometry_dry_run.json'
    target.parent.mkdir(parents=True, exist_ok=True)
    document = {
        'dry_run': True,
        'nothing_was_queued': True,
        'counters': result.as_dict(),
        'contours': prepared,
    }
    with target.open('w', encoding='utf-8') as handle:
        json.dump(document, handle, ensure_ascii=False, indent=2)
    return target
