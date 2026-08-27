# -*- coding: utf-8 -*-
"""drone_collector/routes.py -- сбор геометрических маршрутов вылетов DJI.

    python -m drone_collector.main --routes --from 2026-06-01 --to 2026-06-30
    python -m drone_collector.main --routes --ids-file ids.txt --dry-run

ЧТО ЭТО СОБИРАЕТ И КАК ЭТО НАЗЫВАТЬ

Ответ несёт **геометрический маршрут** -- последовательность координат. Ни
времени точки, ни состояния насоса, ни распыления в нём нет (подтверждено на
961 точке выборки и на `data_type=simplified`). Поэтому ни одна строка этого
модуля не называет маршрут работой, обработкой или подтверждённым
опрыскиванием: собирается `route`, `route segment`, `кандидат покрытия`.
Ограничение доказано ТОЛЬКО для `simplified` -- не для всех источников DJI.

КАК ЗАПРАШИВАЕТСЯ

Тело запроса подтверждено на живом трафике 2026-08-26
(`V1_REQUEST_BODY_CONFIRMED`):

    POST .../api/web/v2/flight_datas/flight_records
    {"flight_record_ids": [...], "data_type": "simplified"}

Периода и идентификатора устройства в теле НЕТ -- запрашиваются именно
идентификаторы вылетов, поштучно. Отсюда и устройство этого модуля: ему на
вход даётся список уже известных `dji_flight_id`, а не окно дат.

**Запрос выполняет САМА СТРАНИЦА.** Транспорт (`PageRouteTransport`) просит
страницу выполнить `fetch` в её собственном контексте, поэтому подпись, если
сайт её ставит, ставит сайт. Подпись DJI здесь не воспроизводится и не
разбирается -- то же правило, по которому работает сбор вылетов и справочника.

**НЕПРОВЕРЕННОЕ ДОПУЩЕНИЕ, названное прямо:** ставит ли сайт подпись на
`fetch`, выполненный из его контекста, из контейнера разработки проверить
нельзя -- кабинет DJI оттуда недостижим. Если не ставит, DJI ответит своим
внутренним кодом, прогон завершится ненулевым кодом и НАЗОВЁТ этот исход
(`ROUTE_REQUEST_REFUSED`). Молчаливого «маршрутов не найдено» этот путь не
даёт: статус ответа проверяется до всего остального.

ПРО `data_type`

Значение берётся из списка НАБЛЮДАВШИХСЯ, а не подбирается. Перебор
недопустим: он ничего не доказывает, зато выглядит со стороны DJI как
сканирование чужого API. Новое значение попадает сюда только с ссылкой на
фактический код фронтенда или на реальный трафик.

СЧЁТЧИКИ ПРОГОНА

    requested = new + duplicates + missing + errors

Четыре ведра, а не три. `missing` -- идентификатор запросили, DJI маршрута не
вернул; это НЕ ошибка прогона и не должно в неё сваливаться, иначе первый же
вылет без маршрута сделает прогон «неуспешным» и скроет настоящие отказы.
"""

import hashlib
import json
import logging
import time

from drone_collector.outbox import (KIND_ROUTE, Outbox, OutboxError,
                                    SecretInEnvelope, find_secret_markers,
                                    utc_now_iso)

log = logging.getLogger(__name__)

# ─── Эндпоинт ────────────────────────────────────────────────────────────────

# Совпадение по ИМЕНИ эндпоинта, а не по версии API: смена v2 на v3 не должна
# превращать прогон в молчаливое «маршрутов не найдено». Тот же приём, что в
# tools/drone_route_probe.py.
ROUTE_ENDPOINT_MARKER = '/flight_datas/flight_records'

# Путь запроса целиком -- как его выдаёт кабинет. Используется транспортом;
# фильтр ответов смотрит только на маркер выше.
ROUTE_ENDPOINT_PATH = '/api/web/v2/flight_datas/flight_records'

# ─── data_type ───────────────────────────────────────────────────────────────

# Значения `data_type`, НАБЛЮДАВШИЕСЯ в реальном трафике.
#
# [REASON]: список наблюдений, а не перечень догадок. `simplified` снят с
# живого запроса кабинета 2026-08-26 и записан в
# docs/DRONE_COVERAGE_001_A2_A3_REPORT.md как `V1_REQUEST_BODY_CONFIRMED`.
# Добавлять сюда значение можно, только предъявив код фронтенда или снимок
# трафика, где оно встретилось. Подбор запрещён: он ничего не доказывает и
# со стороны DJI неотличим от сканирования API.
OBSERVED_DATA_TYPES = ('simplified',)

DEFAULT_DATA_TYPE = 'simplified'

# ─── Пределы прогона ─────────────────────────────────────────────────────────

# Сколько идентификаторов уходит в один запрос.
#
# [REASON]: в подтверждённом теле их девять. Двадцать пять -- осторожный
# верх: он кратно меньше страницы списка вылетов (30) и заведомо в пределах
# того, что кабинет формирует сам. Большой пакет экономит запросы, но при
# отказе теряет весь пакет целиком и утяжеляет один ответ; предел сверху
# держит и то, и другое.
DEFAULT_ROUTE_BATCH_SIZE = 25
MAX_ROUTE_BATCH_SIZE = 100

# Потолок одного ответа. Маршрут на 107 точек -- единицы килобайт; 32 МБ
# покрывают самый крупный мыслимый пакет и не дают ответу-переростку съесть
# память процесса.
MAX_RESPONSE_BYTES = 32 * 1024 * 1024

# Три попытки: первая, затем паузы 2 с и 4 с. Тот же порядок, что у отправки
# в Vehicle Soft (`sender.py`), чтобы не заводить второй.
RETRY_ATTEMPTS = 3
RETRY_BACKOFF_SECONDS = (2, 4)

# Пауза между пакетами. Кабинет -- чужой сервис, и темп задаётся нами.
DEFAULT_BATCH_PAUSE_SECONDS = 1.0

# Версия правил разбора, попадающая в каждую запись очереди. Меняется, когда
# меняется СМЫСЛ разобранных полей.
ROUTE_COLLECTOR_VERSION = 'routes-1'

# Насколько подробно хранятся координаты. 1e-7 градуса -- около 1.1 см по
# широте, на два порядка точнее GPS дрона; округление ничего не теряет, зато
# исключает дрейф последнего разряда float при повторной сериализации.
COORDINATE_DECIMALS = 7

# Предел на неопознанное поле, сохраняемое шестнадцатеричной строкой.
#
# [REASON]: hex НЕПРОЗРАЧЕН для проверки на секреты -- маркер внутри него не
# найдётся. Поэтому длинное неопознанное поле не переписывается целиком: от
# него остаются длина и sha256. Короткое сохраняется полностью, потому что
# именно ради него ведётся `unknown`: следующий читатель должен увидеть, что
# поле есть, а не узнать, что оно было.
MAX_UNKNOWN_FIELD_BYTES = 512


class RouteRunError(Exception):
    """Прогон сбора маршрутов остановлен."""


class RouteRequestRefused(RouteRunError):
    """DJI отказал в запросе маршрутов. Код исхода -- `ROUTE_REQUEST_REFUSED`."""


# ─── Чистые функции: их проверяют тесты без браузера и без сети ──────────────

def is_route_url(url):
    """True для эндпоинта маршрутов, на любой версии API."""
    return bool(url) and ROUTE_ENDPOINT_MARKER in url


def assert_data_type(value):
    """Отказ, если `data_type` не из наблюдавшихся."""
    if value not in OBSERVED_DATA_TYPES:
        raise RouteRunError(
            'data_type %r has never been observed in real traffic; the only '
            'observed value(s) are %s. Add one only with the frontend code or '
            'a traffic capture that shows it -- never by trying values.'
            % (value, ', '.join(OBSERVED_DATA_TYPES)))
    return value


def normalise_ids(values):
    """Целые идентификаторы вылетов без повторов, в устойчивом порядке.

    Порядок -- по возрастанию, а не в порядке поступления: пакеты обязаны
    быть воспроизводимыми между прогонами, иначе `--dry-run` показывает одно,
    а настоящий прогон делает другое.
    """
    seen = set()
    for value in values or ():
        if isinstance(value, bool):
            raise RouteRunError('flight id %r is a boolean' % (value,))
        if isinstance(value, float):
            # [REASON]: `int(1.5)` молча даёт 1. Идентификатор вылета,
            # усечённый на приёме, указал бы на ЧУЖОЙ вылет -- и маршрут лёг
            # бы в базу под чужим номером, ничем себя не выдав. Дробное
            # значение отвергается, а не округляется.
            raise RouteRunError('flight id %r is a float, not a whole number'
                                % (value,))
        try:
            number = int(value)
        except (TypeError, ValueError):
            raise RouteRunError('flight id %r is not a whole number' % (value,))
        if number <= 0:
            raise RouteRunError('flight id %r is not positive' % (value,))
        seen.add(number)
    return sorted(seen)


def build_request_body(flight_ids, data_type=DEFAULT_DATA_TYPE):
    """Тело POST-запроса -- ровно той формы, что снята с живого трафика."""
    assert_data_type(data_type)
    ids = normalise_ids(flight_ids)
    if not ids:
        raise RouteRunError('a route request needs at least one flight id')
    return {'flight_record_ids': ids, 'data_type': data_type}


def parse_request_body(text):
    """(идентификаторы, data_type) из сохранённого тела запроса.

    Принимает и «голое» тело, и безопасный конверт DevTools с `bodyText` --
    ту же пару форм, что понимает `tools/drone_route_probe.py`.
    """
    try:
        document = json.loads(text)
    except (TypeError, ValueError) as exc:
        raise RouteRunError('the request body is not JSON (%s)'
                            % type(exc).__name__)
    if (isinstance(document, dict) and isinstance(document.get('bodyText'), str)
            and 'flight_record_ids' not in document):
        return parse_request_body(document['bodyText'])
    if not isinstance(document, dict):
        raise RouteRunError('the request body is not an object')
    ids = normalise_ids(document.get('flight_record_ids') or ())
    data_type = document.get('data_type')
    return ids, data_type


def chunk_ids(flight_ids, batch_size):
    """Пакеты идентификаторов. Размер зажимается потолком, как в sender.chunk."""
    size = min(int(batch_size), MAX_ROUTE_BATCH_SIZE)
    if size < 1:
        size = 1
    ids = list(flight_ids)
    return [ids[i:i + size] for i in range(0, len(ids), size)]


class Reconciliation(object):
    """Сверка запрошенных идентификаторов с вернувшимися."""

    __slots__ = ('requested', 'returned', 'matched', 'missing', 'unexpected')

    def __init__(self, requested, returned):
        request_set = set(requested)
        return_list = [value for value in returned if value is not None]
        return_set = set(return_list)
        self.requested = sorted(request_set)
        self.returned = sorted(return_set)
        self.matched = sorted(request_set & return_set)
        self.missing = sorted(request_set - return_set)
        self.unexpected = sorted(return_set - request_set)

    @property
    def is_trustworthy(self):
        """False, когда ответ несёт то, чего не просили.

        [REASON]: лишний идентификатор означает, что связка «запрос-ответ»
        порвалась -- поймали чужой ответ, ответ на другой пакет или ответ
        параллельного запроса самой страницы. Принять такой пакет значит
        положить в базу маршруты, про которые неизвестно, чьи они. Пакет
        целиком считается ошибкой, а прогон идёт дальше -- ровно так же, как
        приём вылетов отвергает частично повреждённый пакет целиком.
        """
        return not self.unexpected

    def describe(self):
        return ('requested=%d returned=%d matched=%d missing=%d unexpected=%d'
                % (len(self.requested), len(self.returned), len(self.matched),
                   len(self.missing), len(self.unexpected)))


def route_body(record, data_type, decoder_version=None):
    """Разобранный маршрут в виде, пригодном для очереди и для приёмника.

    Ширина, которой DJI не записал, остаётся `None`. Подстановка запрещена
    решением владельца 2026-08-25: ни медианой, ни паспортом машины, ни
    соседним значением того же дня.
    """
    points = [[round(lat, COORDINATE_DECIMALS), round(lng, COORDINATE_DECIMALS)]
              for lat, lng in record.points]
    takeoff = None
    if record.takeoff is not None:
        takeoff = [round(record.takeoff[0], COORDINATE_DECIMALS),
                   round(record.takeoff[1], COORDINATE_DECIMALS)]
    return {
        'dji_flight_id': record.flight_id,
        'data_type': data_type,
        'collector_version': ROUTE_COLLECTOR_VERSION,
        'decoder_version': decoder_version,
        'points': points,
        'point_count': len(points),
        'takeoff': takeoff,
        # Площадь DJI из маршрута -- float32, только для сверки личности
        # записи. В отчёты идёт значение из JSON-тракта, не это.
        'dji_area_m2': record.work_area_m2,
        # [REASON]: `spray_width_known` отсекает и -1, и ноль. Ноль как радиус
        # буфера ничем не лучше отрицательного: он даёт уверенные 0.00 га
        # вместо честного «ширина неизвестна».
        'spray_width_m': (record.spray_width_m
                          if record.spray_width_known else None),
        'spray_width_recorded': bool(record.spray_width_known),
        'hardware_id': record.hardware_id,
        'device_id': record.device_id,
        'nickname': record.nickname,
        'flyer_id': record.flyer_id,
        'flyer_name': record.flyer_name,
        'team_id': record.team_id,
        'team_name': record.team_name,
        'mode_name': record.mode_name,
        'mission_uuid': record.mission_uuid,
        'location': record.location,
        'start_ms': record.start_ms,
        'end_ms': record.end_ms,
        'duration_ms': record.duration_ms,
        'drone_type': record.drone_type,
        'app_version': record.app_version,
        'unknown_fields': [_unknown_field(number, wire, value)
                           for number, wire, value in record.unknown],
    }


def _unknown_field(number, wire, value):
    """Неопознанное поле protobuf -- сохранённым, но не бесконтрольно."""
    raw = bytes(value) if isinstance(value, (bytes, bytearray)) else None
    entry = {'field': number, 'wire': wire}
    if raw is None:
        entry['varint'] = value
        return entry
    entry['bytes'] = len(raw)
    entry['sha256'] = hashlib.sha256(raw).hexdigest()
    if len(raw) <= MAX_UNKNOWN_FIELD_BYTES:
        entry['hex'] = raw.hex()
    else:
        entry['truncated'] = True
    return entry


def content_sha256(body):
    """Хеш канонизированного тела маршрута -- ключ идемпотентности записи."""
    text = json.dumps(body, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':'))
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


class RouteRunResult(object):
    """Счётчики одного прогона сбора маршрутов.

    Инвариант: `requested = new + duplicates + missing + errors`.
    """

    __slots__ = ('requested', 'batches', 'responses', 'new', 'duplicates',
                 'missing', 'errors', 'points', 'without_width', 'unlinked',
                 'quarantined', 'refusals', 'data_types')

    def __init__(self):
        self.requested = 0
        self.batches = 0
        self.responses = 0
        self.new = 0
        self.duplicates = 0
        self.missing = 0
        self.errors = 0
        self.points = 0
        self.without_width = 0
        self.unlinked = 0
        self.quarantined = 0
        self.refusals = 0
        self.data_types = {}

    @property
    def invariant_holds(self):
        return (self.requested
                == self.new + self.duplicates + self.missing + self.errors)

    def as_dict(self):
        return {name: getattr(self, name) for name in self.__slots__}

    def __repr__(self):
        return 'RouteRunResult(%s)' % self.as_dict()


class RouteFetch(object):
    """Один выполненный запрос: что просили и что пришло."""

    __slots__ = ('requested_ids', 'data_type', 'raw', 'endpoint')

    def __init__(self, requested_ids, data_type, raw, endpoint=None):
        self.requested_ids = list(requested_ids)
        self.data_type = data_type
        self.raw = raw
        self.endpoint = endpoint or ROUTE_ENDPOINT_PATH

    @property
    def response_sha256(self):
        return hashlib.sha256(bytes(self.raw or b'')).hexdigest()


# ─── Прогон ──────────────────────────────────────────────────────────────────

class RouteRun(object):
    """Сбор маршрутов для списка известных `dji_flight_id`.

        run = RouteRun(outbox, fetch_fn, log=log)
        result = run.collect([622715275, 622715274])

    `fetch_fn(flight_ids, data_type) -> RouteFetch` -- единственная точка,
    которой нужен браузер. Всё остальное чистое и проверяется фикстурами.
    """

    def __init__(self, outbox, fetch_fn, logger=None,
                 batch_size=DEFAULT_ROUTE_BATCH_SIZE,
                 data_type=DEFAULT_DATA_TYPE, sleep_fn=None,
                 batch_pause_s=DEFAULT_BATCH_PAUSE_SECONDS,
                 quarantine_dir=None, dry_run=False):
        self.outbox = outbox
        self.fetch_fn = fetch_fn
        self.log = logger or log
        self.batch_size = batch_size
        self.data_type = assert_data_type(data_type)
        self.sleep = sleep_fn or time.sleep
        self.batch_pause_s = batch_pause_s
        self.quarantine_dir = quarantine_dir
        self.dry_run = dry_run
        self.prepared_bodies = []

    # -- возобновление --------------------------------------------------------

    def already_queued(self, flight_ids):
        """Идентификаторы, для которых запись уже лежит в очереди.

        [REASON]: возобновление опирается на СОДЕРЖИМОЕ очереди, а не на
        отдельный файл прогресса. Файл прогресса и очередь расходятся при
        первом же обрыве между записью одного и другого, и тогда возобновление
        либо теряет собранное, либо просит заново уже собранное.
        """
        if self.outbox is None:
            return set()
        found = set()
        wanted = set(normalise_ids(flight_ids))
        for path in self.outbox.records(KIND_ROUTE):
            try:
                envelope = self.outbox.read(path)
            except OutboxError:
                continue
            try:
                identity = int(envelope.get('identity'))
            except (TypeError, ValueError):
                continue
            if identity in wanted:
                found.add(identity)
        return found

    # -- сам прогон -----------------------------------------------------------

    def collect(self, flight_ids, resume=True):
        """Собрать маршруты. Возвращает RouteRunResult."""
        result = RouteRunResult()
        wanted = normalise_ids(flight_ids)
        if not wanted:
            self.log.info('Nothing to collect: zero flight ids given.')
            return result

        skipped = self.already_queued(wanted) if resume else set()
        if skipped:
            self.log.info('Resuming: %d of %d route(s) are already in the '
                          'outbox and will not be requested again.',
                          len(skipped), len(wanted))
        todo = [value for value in wanted if value not in skipped]

        result.requested = len(wanted)
        result.duplicates += len(skipped)

        batches = chunk_ids(todo, self.batch_size)
        self.log.info('Collecting %d route(s) in %d batch(es) of at most %d, '
                      'data_type=%s%s', len(todo), len(batches),
                      min(self.batch_size, MAX_ROUTE_BATCH_SIZE),
                      self.data_type, ' (DRY RUN)' if self.dry_run else '')

        try:
            for index, batch in enumerate(batches, start=1):
                self._collect_batch(batch, index, len(batches), result)
                if index < len(batches) and self.batch_pause_s:
                    self.sleep(self.batch_pause_s)
        except RouteRequestRefused:
            # [REASON]: отказ кабинета останавливает прогон целиком -- у
            # следующего пакета нет причин пройти. Идентификаторы, до которых
            # прогон не дошёл, записываются ошибками, а не теряются: иначе
            # сводка прогона объявила бы их собранными, просто не назвав.
            self._account_for_abort(result)
            self._finish(result)
            raise

        self._finish(result)
        return result

    def _account_for_abort(self, result):
        """Свести счётчики прерванного прогона: неопрошенное -- ошибки."""
        accounted = (result.new + result.duplicates + result.missing
                     + result.errors)
        if result.requested > accounted:
            result.errors += result.requested - accounted

    def _collect_batch(self, batch, index, total, result):
        result.batches += 1
        try:
            fetch = self._fetch_with_retries(batch, index, total)
        except RouteRequestRefused:
            result.refusals += 1
            result.errors += len(batch)
            raise
        except RouteRunError as exc:
            self.log.error('Batch %d/%d failed: %s', index, total, exc)
            result.errors += len(batch)
            return

        result.responses += 1
        decoded = self._decode(fetch, index, total, result, batch)
        if decoded is None:
            return

        reconciliation = Reconciliation(batch, decoded.flight_ids)
        self.log.info('Batch %d/%d: %s', index, total,
                      reconciliation.describe())
        if not reconciliation.is_trustworthy:
            # Идентификаторы лишних маршрутов НЕ печатаются: это чужие данные,
            # и в журнале им делать нечего. Печатается их число.
            self.log.error('Batch %d/%d carried %d route(s) that were NOT '
                           'requested; the pairing between the request and '
                           'this response cannot be trusted, so the whole '
                           'batch is rejected.', index, total,
                           len(reconciliation.unexpected))
            result.errors += len(batch)
            return

        result.missing += len(reconciliation.missing)
        if reconciliation.missing:
            self.log.warning('Batch %d/%d: DJI returned no route for %d of the '
                             'requested flight(s). Not an error -- recorded as '
                             'missing.', index, total,
                             len(reconciliation.missing))

        # [REASON]: имя `requested`, а не `observed`. Ответ маршрутов НЕ несёт
        # своего `data_type` -- в конверте только `status`, `message` и сами
        # маршруты, -- поэтому единственное, что здесь известно, это чем мы
        # спросили. Назвать это наблюдением значило бы записать в улику, будто
        # DJI подтвердил тип, и следующий читатель прочёл бы подтверждение там,
        # где его нет.
        requested = fetch.data_type or self.data_type
        result.data_types[requested] = result.data_types.get(requested, 0) + 1

        for record in decoded.routes:
            self._store(record, fetch, requested, result)

    def _fetch_with_retries(self, batch, index, total):
        """Один пакет, до RETRY_ATTEMPTS попыток. Возвращает RouteFetch."""
        last_error = None
        for attempt in range(1, RETRY_ATTEMPTS + 1):
            try:
                fetch = self.fetch_fn(list(batch), self.data_type)
            except RouteRequestRefused:
                # Отказ в доступе повторять бессмысленно: он не пройдёт и на
                # третий раз, а прогон должен назвать причину, а не утонуть в
                # трёх одинаковых неудачах.
                raise
            except Exception as exc:
                last_error = '%s: %s' % (type(exc).__name__, exc)
                self.log.warning('Batch %d/%d attempt %d/%d failed (%s)',
                                 index, total, attempt, RETRY_ATTEMPTS,
                                 last_error)
            else:
                raw = bytes(fetch.raw or b'')
                if not raw:
                    last_error = 'the response body was empty'
                    self.log.warning('Batch %d/%d attempt %d/%d: %s', index,
                                     total, attempt, RETRY_ATTEMPTS, last_error)
                elif len(raw) > MAX_RESPONSE_BYTES:
                    raise RouteRunError(
                        'batch %d/%d answered %d bytes, the cap is %d; nothing '
                        'was decoded' % (index, total, len(raw),
                                         MAX_RESPONSE_BYTES))
                else:
                    return fetch
            if attempt < RETRY_ATTEMPTS:
                delay = RETRY_BACKOFF_SECONDS[
                    min(attempt - 1, len(RETRY_BACKOFF_SECONDS) - 1)]
                self.log.info('Retrying batch %d/%d in %s s', index, total,
                              delay)
                self.sleep(delay)
        raise RouteRunError('batch %d/%d failed after %d attempt(s) (%s)'
                            % (index, total, RETRY_ATTEMPTS, last_error))

    def _decode(self, fetch, index, total, result, batch):
        """Разобрать ответ. None -- ответ ушёл в карантин, пакет пропущен."""
        from drone_collector.route_decode import (RouteDecodeError,
                                                  decode_route_response)
        try:
            decoded = decode_route_response(fetch.raw)
        except RouteDecodeError as exc:
            self.log.error('Batch %d/%d did not decode (%s); the body is '
                           'quarantined by hash and the run continues.',
                           index, total, exc)
            self._quarantine(fetch, str(exc))
            result.quarantined += 1
            result.errors += len(batch)
            return None
        if not decoded.is_ok:
            # Сообщение DJI печатается: это его собственный текст об отказе,
            # без наших данных и без подписи.
            self.log.error('Batch %d/%d refused by DJI: status=%s message=%r',
                           index, total, decoded.status, decoded.message)
            result.errors += len(batch)
            result.refusals += 1
            raise RouteRequestRefused(
                'DJI answered status %s (%r) -- the cabinet did not serve the '
                'routes. Nothing was collected for this batch.'
                % (decoded.status, decoded.message))
        return decoded

    def _store(self, record, fetch, requested_data_type, result):
        """Положить один маршрут в очередь (или пересчитать для dry-run)."""
        from drone_collector.route_decode import DECODER_VERSION

        body = route_body(record, requested_data_type,
                          decoder_version=DECODER_VERSION)
        result.points += body['point_count']
        if body['spray_width_m'] is None:
            result.without_width += 1
        if record.flight_id is None:
            # [REASON]: маршрут без поля 2 нельзя связать с вылетом. Он не
            # отбрасывается -- по тому же правилу, по которому незнакомый ник
            # не отклоняет вылет, -- но и в очередь по идентификатору не
            # ложится: идентификатора нет.
            result.unlinked += 1
            self.log.warning('A route arrived without a flight id; it cannot '
                             'be linked and is not queued.')
            return

        diagnostics = {
            'endpoint': fetch.endpoint,
            'response_sha256': fetch.response_sha256,
            'requested_count': len(fetch.requested_ids),
            'requested_data_type': requested_data_type,
            'collected_at': utc_now_iso(),
        }
        if self.dry_run:
            self.prepared_bodies.append({'body': body,
                                         'diagnostics': diagnostics})
            result.new += 1
            return

        try:
            _, duplicate = self.outbox.enqueue(
                KIND_ROUTE, str(record.flight_id), body, content_sha256(body),
                diagnostics=diagnostics)
        except OutboxError as exc:
            # [REASON]: очередь отказывает по своим правилам -- маркер секрета
            # в теле, запись сверх потолка. Это отказ ОДНОГО маршрута, и он
            # обязан остаться одним: без этого он уходил наружу мимо всех
            # `except` прогона, обрывал оставшиеся пакеты и приходил к
            # оператору голым трейсбеком с кодом 1. Текст исключения очереди
            # называет маркеры, но не значения, поэтому его можно печатать.
            self.log.error('Flight %s was not queued: %s',
                           record.flight_id, exc)
            result.errors += 1
            return
        if duplicate:
            result.duplicates += 1
        else:
            result.new += 1

    def _quarantine(self, fetch, reason):
        """Сохранить неразобранный ответ по хешу -- или только его описание.

        [REASON]: тело, которое мы НЕ разобрали, мы и не понимаем. Прежде чем
        класть его на диск, оно проверяется на маркеры секретов; при находке
        сохраняется только описание -- размер, хеш, имена маркеров. Записать
        непонятое тело целиком «на всякий случай» -- ровно тот способ, которым
        подписи и токены переживают прогон.
        """
        if not self.quarantine_dir:
            return None
        from pathlib import Path
        directory = Path(self.quarantine_dir)
        directory.mkdir(parents=True, exist_ok=True)
        raw = bytes(fetch.raw or b'')
        digest = fetch.response_sha256
        markers = find_secret_markers(raw.decode('latin-1'))
        note = {
            'response_sha256': digest,
            'bytes': len(raw),
            'endpoint': fetch.endpoint,
            'requested_count': len(fetch.requested_ids),
            'reason': reason,
            'quarantined_at': utc_now_iso(),
            'secret_markers': markers,
            'body_written': not markers,
        }
        with (directory / ('%s.json' % digest)).open('w',
                                                     encoding='utf-8') as handle:
            json.dump(note, handle, ensure_ascii=False, indent=1)
        if not markers:
            (directory / ('%s.bin' % digest)).write_bytes(raw)
        else:
            self.log.error('The undecodable body carries %s; only its '
                           'description was written, not the body itself.',
                           ', '.join(markers))
        return digest

    def _finish(self, result):
        if not result.invariant_holds:
            # [REASON]: расхождение счётчиков означает, что какой-то путь
            # прогона не учтён. Оно не молчит: несведённые счётчики -- это
            # ровно тот дефект, из-за которого журнал приёма вылетов однажды
            # показывал больше принятого, чем присланного.
            self.log.error('COUNTER MISMATCH: requested=%d but new+duplicates+'
                           'missing+errors=%d. The run summary cannot be '
                           'trusted.', result.requested,
                           result.new + result.duplicates + result.missing
                           + result.errors)
        self.log.info('Route run: %s', result.as_dict())


# ─── Транспорт через страницу ────────────────────────────────────────────────

# Скрипт выполняется В КОНТЕКСТЕ СТРАНИЦЫ, поэтому запрос отправляет сайт со
# своими перехватчиками и своей подписью. Ответ возвращается массивом байтов:
# тело `octet-stream`, и любая попытка получить его строкой испортит данные.
PAGE_FETCH_JS = """
async ([path, body]) => {
  const response = await fetch(path, {
    method: 'POST',
    credentials: 'include',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(body)
  });
  const buffer = await response.arrayBuffer();
  return {status: response.status, bytes: Array.from(new Uint8Array(buffer))};
}
"""


class PageRouteTransport(object):
    """Запрос маршрутов руками самой страницы.

    Тонкая обёртка вокруг Playwright: из контейнера разработки кабинет DJI
    недостижим, поэтому здесь нет ничего, кроме вызова и проверки формы
    ответа. Всё, что можно проверить фикстурой, живёт выше по файлу.
    """

    def __init__(self, page, api_origin, logger=None):
        self.page = page
        self.api_origin = (api_origin or '').rstrip('/')
        self.log = logger or log

    @property
    def endpoint(self):
        return self.api_origin + ROUTE_ENDPOINT_PATH

    def __call__(self, flight_ids, data_type):
        body = build_request_body(flight_ids, data_type)
        result = self.page.evaluate(PAGE_FETCH_JS, [self.endpoint, body])
        if not isinstance(result, dict):
            raise RouteRunError('the page returned %s, not a response object'
                                % type(result).__name__)
        status = result.get('status')
        payload = result.get('bytes')
        if status in (401, 403):
            raise RouteRequestRefused(
                'the cabinet answered HTTP %s to the route request. Either the '
                'session has expired, or the request made from the page is not '
                'signed the way the cabinet expects. Nothing was collected.'
                % status)
        if not isinstance(payload, list):
            raise RouteRunError('the page returned no body for the route '
                                'request (HTTP %s)' % status)
        return RouteFetch(flight_ids, data_type, bytes(bytearray(payload)),
                          endpoint=ROUTE_ENDPOINT_PATH)


def read_ids_file(path):
    """Идентификаторы вылетов из файла: по одному в строке, `#` -- коммент.

    [REASON]: список известных `dji_flight_id` берётся из базы Vehicle Soft, а
    collector к базе не ходит вовсе -- у него нет ни доступа, ни права его
    иметь. Файл -- граница между двумя процессами, и она видима: владелец
    видит, что именно будет запрошено, до того как это запросят.
    """
    values = []
    with open(path, encoding='utf-8-sig') as handle:
        for number, line in enumerate(handle, start=1):
            text = line.split('#', 1)[0].strip()
            if not text:
                continue
            try:
                values.append(int(text))
            except ValueError:
                raise RouteRunError('%s line %d: %r is not a flight id'
                                    % (path, number, text[:40]))
    return normalise_ids(values)


def write_dry_run(result, prepared, out_dir):
    """Что было бы поставлено в очередь. Ни одной записи не создаётся.

    [REASON]: текст проверяется на маркеры секретов ПЕРЕД записью -- ровно
    так же, как это делает `Outbox._serialize`. Без этого сухой прогон был
    единственным путём, которым тело попадало на диск, минуя проверку: очередь
    отказалась бы, а этот файл ложился молча. Он же и открывается первым --
    порядок первого живого прогона велит смотреть в него до настоящего сбора.
    """
    from pathlib import Path
    target = Path(out_dir) / 'routes_dry_run.json'
    document = {
        'dry_run': True,
        'nothing_was_queued': True,
        'counters': result.as_dict(),
        'routes': prepared,
    }
    text = json.dumps(document, ensure_ascii=False, indent=2)
    found = find_secret_markers(text)
    if found:
        raise SecretInEnvelope(
            'the dry-run report would carry %s; nothing was written'
            % ', '.join(found))
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open('w', encoding='utf-8') as handle:
        handle.write(text)
    return target
