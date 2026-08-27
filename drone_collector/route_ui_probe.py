# -*- coding: utf-8 -*-
"""drone_collector/route_ui_probe.py -- наблюдение за ШТАТНЫМ запросом
маршрутов, который кабинет DJI делает сам.

    python -m drone_collector.main --route-ui-probe

ЗАЧЕМ

Живой прогон 2026-08-27 опроверг нативный `fetch`: в одной сессии и в одну
минуту штатные запросы страницы принесли 168 вылетов, а наш собственный
`fetch` получил на все 19 пакетов `code 408` «недействительное время запроса».
Согласованный timestamp и `Signature` ставит внутренний перехватчик клиента
DJI; нативный `fetch` проходит мимо него.

Воспроизводить подпись запрещено -- уставом трека и здравым смыслом. Остаётся
единственный честный путь: **дать кабинету сделать запрос самому и посмотреть
на него**. Ровно так уже работает сбор вылетов (`browser.py`) и справочника
(`lands.py`): сайт спрашивает, мы слушаем.

Чего этот модуль НЕ делает: он не умеет заставить интерфейс сделать запрос.
Доказуемого способа переключить Task History в режим карты в репозитории нет --
есть только `SELECTOR_LIST_VIEW`, помеченный в `browser.py` как best-effort и
применяемый лишь когда список не пришёл. Придумывать селектор по догадке этот
модуль не станет: он открывает окно, просит человека сделать несколько шагов
руками и слушает то, что при этом полетит.

ПРО ПРЕДЕЛ РАЗМЕРА ОТВЕТА

`MAX_PROCESSED_RESPONSE_BYTES` -- это предел ОБРАБОТКИ, а не чтения.
Playwright отдаёт тело только целиком, и наблюдатель, который именно
наблюдает, остановить его до этого не может. Что предел действительно даёт:
превысившее тело не хешируется, не декодируется, не классифицируется и не
сохраняется, а в наблюдении остаются его фактический размер и имя исхода.
Если ответ назвал `Content-Length` больше предела, тело не запрашивается
вовсе -- но само это число фактическим размером не считается: его ставит
отправитель, при chunked его нет, при сжатии оно про сжатое тело.

ЧТО ОСТАЁТСЯ НА ДИСКЕ

Только безопасное описание. Ни одного заголовка со значением, ни одной cookie,
ни подписи, ни `request_id`, ни самого тела ответа. Имена заголовков и ДЛИНЫ
значений чувствительных заголовков -- да: по ним видно, что подпись вообще
есть и какого она порядка, и по ним нельзя её воспроизвести.

ЧЕГО ЭТОТ МОДУЛЬ НЕ ДЕЛАЕТ НИКОГДА

Не инициирует POST к эндпоинту маршрутов -- в этом весь смысл режима: запрос
должен сделать сам кабинет. Не ставит ничего в очередь. Не обращается к
Vehicle Soft. Не пишет HAR.

Сказать «не делает ни одного запроса» было бы неправдой: чтобы человек мог
открыть Task History, кабинет надо открыть, а это навигация. Гарантия ровно
та, что записана выше, и не шире.
"""

import hashlib
import json
import logging
import re

from urllib.parse import urlsplit

log = logging.getLogger(__name__)

# Эндпоинты, которые слушаются. Совпадение по имени, а не по версии API.
ROUTE_ENDPOINT_MARKER = '/flight_datas/flight_records'
ROUTE_ENDPOINT_PATH = '/api/web/v2/flight_datas/flight_records'
IDS_ENDPOINT_MARKER = 'only_all_ids'

# Заголовки, похожие на ПОДПИСЬ запроса.
#
# [REASON]: разделено с удостоверениями намеренно. Раньше один список отвечал
# на оба вопроса, и `Cookie` — обычное удостоверение сессии, которое есть у
# любого запроса, — поднимал флаг «запрос подписан». Вопрос, ради которого
# этот probe существует, звучит иначе: несёт ли ШТАТНЫЙ запрос ту подпись,
# которой не было у нашего `fetch`. Ответ «да, потому что есть cookie» на него
# не отвечает.
SIGNATURE_HEADER_PARTS = ('signature', 'sign', 'x-amz-signature')

# Заголовки, похожие на УДОСТОВЕРЕНИЕ сессии.
CREDENTIAL_HEADER_PARTS = (
    'authorization', 'cookie', 'token', 'secret', 'key', 'session',
    'auth', 'credential', 'x-amz', 'x-oss',
)

# Заголовки, чьи ЗНАЧЕНИЯ не выводятся ни при каких условиях: объединение
# обоих списков. Про такой заголовок сохраняется факт наличия и длина
# значения -- этого хватает, чтобы сказать «подпись есть и она длиной 64», и
# не хватает, чтобы её повторить.
SENSITIVE_HEADER_PARTS = SIGNATURE_HEADER_PARTS + CREDENTIAL_HEADER_PARTS

# Заголовки, по которым видно, что запрос несёт метку времени.
TIMESTAMP_HEADER_PARTS = ('timestamp', 'date', 'time', 'nonce', 'ts')

# Потолок тела запроса, которое разбирается.
MAX_REQUEST_BODY_BYTES = 1024 * 1024

# Предел ОБРАБОТКИ тела ответа после его получения.
#
# [REASON]: имя честное, и это важно. Playwright отдаёт тело только целиком --
# `response.body()` возвращает уже полученные байты, и остановить их до этого
# наблюдатель не может: он именно наблюдает, а не выполняет запрос. Значит
# тело УЖЕ побывало в памяти процесса к моменту проверки. Что даёт этот
# предел: превысившее его тело не хешируется, не декодируется, не
# классифицируется и не сохраняется -- в наблюдении остаются только его
# фактический размер и имя исхода. Называть это «потолком тела, читаемого в
# память» было бы неправдой.
MAX_PROCESSED_RESPONSE_BYTES = 32 * 1024 * 1024

# Прежнее имя. Оставлено, чтобы не ломать вызывающих одним переименованием.
MAX_RESPONSE_BYTES = MAX_PROCESSED_RESPONSE_BYTES

# Заголовок, по которому размер иногда виден ДО чтения тела.
#
# [REASON]: если кабинет прислал `Content-Length`, наблюдатель откажется от
# обработки заранее и не станет трогать тело. Но доверять этому числу как
# ФАКТИЧЕСКОМУ размеру нельзя: заголовок ставит отправитель, при chunked его
# нет вовсе, а при сжатии он описывает сжатое тело. Поэтому он используется
# только как основание для РАННЕГО ОТКАЗА, и фактический размер, когда тело
# всё же прочитано, берётся из самого тела.
CONTENT_LENGTH_HEADER = 'content-length'


def declared_response_size(headers):
    """Размер, ЗАЯВЛЕННЫЙ ответом, или None. Фактическим не считается."""
    for name, value in (headers or {}).items():
        if (name or '').lower() != CONTENT_LENGTH_HEADER:
            continue
        try:
            declared = int(str(value).strip())
        except (TypeError, ValueError):
            return None
        return declared if declared >= 0 else None
    return None

# Сколько наблюдений принимается за один прогон.
#
# [REASON]: карта перезапрашивает маршруты при каждом изменении вида, и в
# снимке 2026-06-05 три ответа пришли БАЙТ В БАЙТ одинаковыми. Предел держит
# отчёт читаемым, а дедупликация по хешу показывает, что повтор -- повтор.
MAX_OBSERVATIONS = 50

PROBE_REPORT_NAME = 'route_ui_probe.json'

_CONTROL = re.compile(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]')


def is_route_url(url):
    return bool(url) and ROUTE_ENDPOINT_MARKER in url


def is_ids_url(url):
    return bool(url) and IDS_ENDPOINT_MARKER in url


def _is_sensitive(name):
    lowered = (name or '').lower()
    return any(part in lowered for part in SENSITIVE_HEADER_PARTS)


def _is_signature_like(name):
    lowered = (name or '').lower()
    return any(part in lowered for part in SIGNATURE_HEADER_PARTS)


def _is_credential_like(name):
    lowered = (name or '').lower()
    return any(part in lowered for part in CREDENTIAL_HEADER_PARTS)


def _is_timestamp_like(name):
    lowered = (name or '').lower()
    return any(part in lowered for part in TIMESTAMP_HEADER_PARTS)


def describe_headers(headers):
    """Имена заголовков и длины значений -- но ни одного значения.

    Возвращает словарь с тремя списками и двумя флагами. Значение попадает
    сюда только как ЧИСЛО: `len`.
    """
    names = []
    sensitive = []
    for name in sorted((headers or {}).keys(), key=lambda n: n.lower()):
        value = headers.get(name)
        length = len(value) if isinstance(value, str) else 0
        names.append(name.lower())
        if _is_sensitive(name):
            sensitive.append({'name': name.lower(), 'value_length': length})
    return {
        'header_names': names,
        'sensitive_headers': sensitive,
        'carries_signature_like_header': any(_is_signature_like(n)
                                             for n in names),
        'carries_credential_like_header': any(_is_credential_like(n)
                                              for n in names),
        'carries_timestamp_like_header': any(_is_timestamp_like(n)
                                             for n in names),
    }


def summarise_request_body(text):
    """Что лежало в теле запроса -- числом, а не значением.

    Из тела берутся ровно две вещи: СКОЛЬКО идентификаторов вылетов оно
    несло и какой `data_type` кабинет попросил. Сами идентификаторы не
    печатаются и не сохраняются: они не секрет, но и не наблюдение -- они и
    так известны, а вопрос стоит о форме запроса.
    """
    summary = {'parsed': False, 'flight_id_count': None, 'data_type': None,
               'body_keys': [], 'bytes': 0, 'detail': ''}
    if text is None:
        summary['detail'] = 'the request carried no body'
        return summary
    raw = text.encode('utf-8') if isinstance(text, str) else bytes(text)
    summary['bytes'] = len(raw)
    if len(raw) > MAX_REQUEST_BODY_BYTES:
        summary['detail'] = ('%d bytes, the cap is %d; not parsed'
                             % (len(raw), MAX_REQUEST_BODY_BYTES))
        return summary
    try:
        document = json.loads(raw.decode('utf-8-sig'))
    except (ValueError, UnicodeDecodeError) as exc:
        summary['detail'] = 'not readable JSON (%s)' % type(exc).__name__
        return summary
    if not isinstance(document, dict):
        summary['detail'] = ('JSON %s, not an object'
                             % type(document).__name__)
        return summary

    summary['parsed'] = True
    summary['body_keys'] = sorted(str(key) for key in document)
    ids = document.get('flight_record_ids')
    if isinstance(ids, list):
        summary['flight_id_count'] = len(ids)
    data_type = document.get('data_type')
    if isinstance(data_type, str) and not _CONTROL.search(data_type):
        summary['data_type'] = data_type[:64]
    return summary


class RequestedIds(object):
    """Что лежало в списке `flight_record_ids`. Значения -- только в памяти."""

    __slots__ = ('ids', 'total', 'invalid', 'duplicates', 'parsed')

    def __init__(self, ids=(), total=0, invalid=0, duplicates=0, parsed=False):
        self.ids = set(ids)
        self.total = total
        self.invalid = invalid
        self.duplicates = duplicates
        self.parsed = parsed

    @property
    def is_clean(self):
        """Разобран, непуст, без мусора и без повторов."""
        return (self.parsed and bool(self.ids) and self.invalid == 0
                and self.duplicates == 0)


def read_request_ids(text):
    """Разбор списка запрошенных ID -- ТОЛЬКО в оперативной памяти.

    Возвращает `RequestedIds`: множество, сколько элементов было всего,
    сколько из них НЕ целые и сколько повторов. Ни один вызывающий не кладёт
    значения в отчёт: сверка делается здесь, наружу уходят булево и счётчики.

    [REASON]: раньше отсюда возвращалось только множество, а негодные элементы
    молча отбрасывались. Запрос `[900000001, "900000001", null]` выглядел бы
    как чистый запрос ОДНОГО идентификатора -- при том, что это запрос, формы
    которого мы не понимаем, и подтверждать по нему нечего.
    """
    if text is None:
        return RequestedIds()
    raw = text.encode('utf-8') if isinstance(text, str) else bytes(text)
    if len(raw) > MAX_REQUEST_BODY_BYTES:
        return RequestedIds()
    try:
        document = json.loads(raw.decode('utf-8-sig'))
    except (ValueError, UnicodeDecodeError):
        return RequestedIds()
    if not isinstance(document, dict):
        return RequestedIds()
    ids = document.get('flight_record_ids')
    if not isinstance(ids, list):
        return RequestedIds()

    numbers = []
    invalid = 0
    for value in ids:
        # `True` не идентификатор: `isinstance(True, int)` -- ловушка Python.
        if isinstance(value, int) and not isinstance(value, bool):
            numbers.append(value)
        else:
            invalid += 1
    unique = set(numbers)
    return RequestedIds(ids=unique, total=len(ids), invalid=invalid,
                        duplicates=len(numbers) - len(unique), parsed=True)


def compare_id_sets(requested, returned, routes_without_id=0,
                    decoded_routes=None):
    """Сверка запрошенного с вернувшимся. Наружу -- булево и счётчики.

    `requested` -- `RequestedIds`; допускается и голое множество, тогда список
    считается чистым (так удобно проверять саму сверку).

    Подтверждением считается только полностью чистая картина. Равенства
    множеств мало; нужны ВСЕ условия сразу:

    * список запрошенного разобран, непуст, без мусора и без повторов;
    * множества равны точно;
    * в вернувшемся нет повторов;
    * у каждого раскодированного маршрута есть `dji_flight_id`;
    * число раскодированных маршрутов равно числу вернувшихся ID.

    [REASON]: прежняя редакция считала совпадением `({1, 2}, [1, 2, 2])`.
    Дубль в ответе означает, что один маршрут приехал дважды, и что именно
    приехало вторым -- неизвестно; маршрут без идентификатора вообще не
    связывается с вылетом. Ни то, ни другое не может входить в
    «подтверждено».
    """
    if isinstance(requested, RequestedIds):
        request = requested
    else:
        values = set(requested or ())
        request = RequestedIds(ids=values, total=len(values), parsed=True)

    returned_list = list(returned or ())
    returned_set = set(returned_list)
    returned_duplicates = len(returned_list) - len(returned_set)
    without_id = int(routes_without_id or 0)
    if decoded_routes is None:
        decoded_routes = len(returned_list) + without_id

    match = (request.is_clean
             and bool(request.ids)
             and request.ids == returned_set
             and returned_duplicates == 0
             and without_id == 0
             and decoded_routes == len(returned_list)
             and decoded_routes == len(returned_set))

    return {
        'requested_and_returned_match': match,
        'invalid_requested_id_count': request.invalid,
        'requested_duplicate_count': request.duplicates,
        'returned_duplicate_count': returned_duplicates,
        'route_without_id_count': without_id,
        'missing_count': len(request.ids - returned_set),
        'extra_count': len(returned_set - request.ids),
    }


def request_body_fingerprint(text):
    """Отпечаток СЫРОГО тела запроса. Внутренний, в отчёт не попадает.

    [REASON]: ключ дедупликации строился на множестве идентификаторов, а
    множество не различает `[1, 2]`, `[1, 2, 2]` и `[1, 2, "2"]`. Три разных
    запроса схлопывались бы в одно наблюдение, и вопрос «на что именно ответил
    кабинет» снова терял бы ответ. Хеш сырого тела различает их все и наружу
    не выходит: он не публикуется вовсе.
    """
    if text is None:
        return 'no-body'
    raw = text.encode('utf-8') if isinstance(text, str) else bytes(text)
    if len(raw) > MAX_REQUEST_BODY_BYTES:
        raw = raw[:MAX_REQUEST_BODY_BYTES]
    return hashlib.sha256(raw).hexdigest()[:16]


def observation_id(url, sha256, request_fingerprint=''):
    """Ключ дедупликации: путь, хеш ответа и отпечаток сырого тела запроса."""
    return '%s|%s|%s' % (url.split('?', 1)[0], sha256, request_fingerprint)


class RouteUiObservation(object):
    """Один штатный запрос кабинета, увиденный со стороны."""

    __slots__ = ('host', 'path', 'method', 'http_status', 'request',
                 'response_bytes', 'response_sha256', 'payload_kind',
                 'payload_detail', 'decoded_routes', 'returned_id_count',
                 'comparison', 'preceded_by_only_all_ids', 'repeats',
                 'confirmed', 'not_confirmed_because')

    def __init__(self, host, path, method, http_status, request,
                 response_bytes, response_sha256, payload_kind,
                 payload_detail='', decoded_routes=None,
                 returned_id_count=None, comparison=None,
                 preceded_by_only_all_ids=False, confirmed=False,
                 not_confirmed_because=()):
        self.host = host
        self.path = path
        self.method = method
        self.http_status = http_status
        self.request = request
        self.response_bytes = response_bytes
        self.response_sha256 = response_sha256
        self.payload_kind = payload_kind
        self.payload_detail = payload_detail
        self.decoded_routes = decoded_routes
        self.returned_id_count = returned_id_count
        self.comparison = comparison or {}
        self.preceded_by_only_all_ids = preceded_by_only_all_ids
        self.repeats = 1
        self.confirmed = confirmed
        self.not_confirmed_because = list(not_confirmed_because)

    def as_dict(self):
        document = {
            'host': self.host,
            'path': self.path,
            'method': self.method,
            'http_status': self.http_status,
            'preceded_by_only_all_ids': self.preceded_by_only_all_ids,
            'request': dict(self.request),
            'response_bytes': self.response_bytes,
            'response_sha256': self.response_sha256,
            'payload_kind': self.payload_kind,
            'payload_detail': self.payload_detail,
            'decoded_routes': self.decoded_routes,
            'returned_id_count': self.returned_id_count,
            'repeats': self.repeats,
            'confirmed_route_post': self.confirmed,
            'not_confirmed_because': list(self.not_confirmed_because),
        }
        # Булево и счётчики -- ни одного идентификатора.
        document.update(self.comparison)
        return document


def confirmation_failures(host, path, method, http_status, payload_kind,
                          decoded_routes, comparison, expected_origin):
    """Почему это наблюдение НЕ является подтверждённым штатным запросом.

    Пустой список -- подтверждено. Проверяется всё сразу, а не до первого
    отказа: оператору полезнее увидеть все причины разом.

    [REASON]: раньше код 0 означал «увидели подходящий URL». Этого мало.
    Подтверждением считается только полный успех: тот самый origin по HTTPS,
    точный путь, POST, 2xx, двоичное тело, успешный разбор и совпадение
    МНОЖЕСТВ идентификаторов. Всё остальное -- наблюдение, но не
    подтверждение, и код выхода обязан это различать.
    """
    problems = []
    expected = (expected_origin or '').rstrip('/')
    parts = urlsplit(expected) if expected else None
    if parts is None or not parts.netloc:
        problems.append('no expected route origin was given to the probe')
    else:
        if parts.scheme != 'https':
            problems.append('the expected origin is not https')
        if host != parts.netloc:
            problems.append('the host is not the expected route API host')
    if path != ROUTE_ENDPOINT_PATH:
        problems.append('the path is not the exact route endpoint')
    if (method or '').upper() != 'POST':
        problems.append('the method is not POST')
    try:
        status = int(http_status)
    except (TypeError, ValueError):
        status = None
    if status is None or not 200 <= status < 300:
        problems.append('the HTTP status is not 2xx')
    if payload_kind != 'BINARY':
        problems.append('the payload is not a binary route payload')
    if decoded_routes is None:
        problems.append('the payload did not decode')
    if not (comparison or {}).get('requested_and_returned_match'):
        problems.append('the requested and returned id sets do not match')
    return problems


# Коды выхода режима наблюдения. Совпадают с `drone_collector.main`; здесь они
# нужны, чтобы решение принималось чистой функцией, которую видно тестам.
PROBE_EXIT_OK = 0
PROBE_EXIT_NOTHING_OBSERVED = 6
PROBE_EXIT_UNCONFIRMED = 13


def probe_exit_code(observations, confirmed, skipped_over_cap):
    """Каким кодом заканчивается прогон наблюдения.

    Ноль разрешён ровно при трёх условиях сразу: наблюдение было, КАЖДОЕ
    учтённое наблюдение подтверждено, и ни одно не выпало по лимиту.

    [REASON]: смешанный результат давал ложный ноль. Один подтверждённый POST
    рядом с неподтверждённым ответом означает, что кабинет отвечал по-разному,
    и объявлять такой прогон успешным -- это ровно тот класс ложного успеха,
    ради которого весь этот разбор и ведётся. Пропущенное по лимиту
    наблюдение -- то же самое: про него не известно ничего, а «не известно» не
    равно «подтверждено».
    """
    if int(observations or 0) <= 0:
        return PROBE_EXIT_NOTHING_OBSERVED
    if int(skipped_over_cap or 0) > 0:
        return PROBE_EXIT_UNCONFIRMED
    if int(confirmed or 0) != int(observations or 0):
        return PROBE_EXIT_UNCONFIRMED
    return PROBE_EXIT_OK


class RouteUiProbe(object):
    """Слушатель штатного запроса маршрутов.

    Подписка ставится ДО того, как человек что-либо делает в интерфейсе:
    запрос, случившийся до подписки, увидеть уже нельзя, а второго шанса на
    живом кабинете может не быть.
    """

    def __init__(self, logger=None, expected_origin=None):
        self.log = logger or log
        self.expected_origin = expected_origin
        self.observations = []
        self._seen = {}
        self.saw_only_all_ids = 0
        self._ids_seen_recently = False
        self.route_responses = 0
        self.skipped_over_cap = 0

    @property
    def confirmed_observations(self):
        return [item for item in self.observations if item.confirmed]

    # -- слушатели ------------------------------------------------------------

    def note_request(self, url):
        """Запрос ушёл. Тела и заголовков здесь не читаем.

        [REASON]: `only_all_ids` считается ИМЕННО здесь и только здесь. Раньше
        счётчик рос и на запросе, и на ответе, то есть один сетевой обмен
        считался дважды -- и «два только_все_id перед маршрутом» в отчёте
        означало один.
        """
        if is_ids_url(url):
            self.saw_only_all_ids += 1
            self._ids_seen_recently = True

    def note_response(self, response):
        """Ответ пришёл. Никогда не поднимает: мы внутри цикла Playwright."""
        try:
            self._note_response(response)
        except Exception as exc:  # pragma: no cover -- слушатель не падает
            self.log.warning('The probe could not read a response (%s)',
                             type(exc).__name__)

    def _note_response(self, response):
        url = getattr(response, 'url', '') or ''
        if is_ids_url(url):
            # Обмен уже посчитан на запросе; здесь только отметка соседства.
            self._ids_seen_recently = True
            return
        if not is_route_url(url):
            return

        self.route_responses += 1
        if len(self.observations) >= MAX_OBSERVATIONS:
            self.skipped_over_cap += 1
            return

        request = getattr(response, 'request', None)
        headers = {}
        body_text = None
        method = 'UNKNOWN'
        if request is not None:
            method = getattr(request, 'method', 'UNKNOWN')
            try:
                headers = request.all_headers()
            except Exception:
                headers = {}
            try:
                body_text = request.post_data
            except Exception:
                body_text = None

        requested = read_request_ids(body_text)
        fingerprint = request_body_fingerprint(body_text)

        # Ранний отказ по заявленному размеру, если кабинет его назвал.
        declared = None
        try:
            declared = declared_response_size(response.all_headers())
        except Exception:
            declared = None

        oversize = False
        raw = b''
        actual_bytes = None
        if declared is not None and declared > MAX_PROCESSED_RESPONSE_BYTES:
            # Тело не запрашивается вовсе: заголовок уже говорит, что
            # обрабатывать его мы не станем.
            self.log.warning('The route response declares %d bytes, over the '
                             'processing limit of %d; the body was not '
                             'requested.', declared,
                             MAX_PROCESSED_RESPONSE_BYTES)
            oversize = True
            actual_bytes = declared
        else:
            try:
                raw = bytes(response.body() or b'')
            except Exception as exc:
                self.log.warning('The route response body could not be read '
                                 '(%s)', type(exc).__name__)
                raw = b''
            actual_bytes = len(raw)
            if actual_bytes > MAX_PROCESSED_RESPONSE_BYTES:
                # [REASON]: тело УЖЕ в памяти -- Playwright отдаёт его только
                # целиком. Предел означает, что дальше оно не обрабатывается:
                # не хешируется, не декодируется, не классифицируется и не
                # сохраняется. Фактический размер при этом записывается
                # настоящий: прежняя редакция подменяла тело пустым и писала
                # `response_bytes=0`, то есть утверждала, что огромный ответ
                # был пуст.
                self.log.warning('The route response is %d bytes, over the '
                                 'processing limit of %d; its size is '
                                 'recorded, and it is neither hashed, decoded '
                                 'nor stored.', actual_bytes,
                                 MAX_PROCESSED_RESPONSE_BYTES)
                oversize = True
                raw = b''

        description = describe_headers(headers)
        description.update(summarise_request_body(body_text))
        # Заголовки и тело живут только до этой точки: дальше идёт ОПИСАНИЕ.
        headers = None
        body_text = None

        if oversize:
            digest = None
            payload_kind = 'TOO_LARGE'
            payload_detail = (
                '%d bytes%s, over the processing limit of %d; not hashed, not '
                'decoded, not stored'
                % (actual_bytes,
                   ' (declared, body not requested)' if declared is not None
                   and declared > MAX_PROCESSED_RESPONSE_BYTES else '',
                   MAX_PROCESSED_RESPONSE_BYTES))
            decoded_routes = returned = None
            returned_ids = []
            routes_without_id = 0
        else:
            digest = hashlib.sha256(raw).hexdigest()
            from drone_collector.route_payload import (PAYLOAD_BINARY,
                                                       classify_payload)
            verdict = classify_payload(raw)
            payload_kind = verdict.kind
            payload_detail = verdict.detail
            decoded_routes = returned = None
            returned_ids = []
            routes_without_id = 0
            if verdict.kind == PAYLOAD_BINARY:
                decoded_routes, returned_ids, routes_without_id = \
                    self._decode_ids(raw)
                returned = (len(set(returned_ids))
                            if decoded_routes is not None else None)

        key = observation_id(url, digest or 'not-read', fingerprint)
        if key in self._seen:
            self._seen[key].repeats += 1
            return

        comparison = compare_id_sets(requested, returned_ids,
                                     routes_without_id=routes_without_id,
                                     decoded_routes=decoded_routes)
        # Множества дальше не живут: сверка сделана, наружу идут счётчики.
        requested = returned_ids = None

        host = _host_of(url)
        path = _path_of(url)
        status = getattr(response, 'status', None)
        problems = confirmation_failures(
            host, path, method, status, payload_kind, decoded_routes,
            comparison, self.expected_origin)

        observation = RouteUiObservation(
            host=host, path=path, method=method, http_status=status,
            request=description, response_bytes=actual_bytes,
            response_sha256=digest, payload_kind=payload_kind,
            payload_detail=payload_detail, decoded_routes=decoded_routes,
            returned_id_count=returned, comparison=comparison,
            preceded_by_only_all_ids=self._ids_seen_recently,
            confirmed=not problems, not_confirmed_because=problems)
        self._ids_seen_recently = False
        self._seen[key] = observation
        self.observations.append(observation)
        self.log.info('Observed a route response: %s',
                      _safe_line(observation))

    def _decode_ids(self, raw):
        """(маршрутов, список вернувшихся id, маршрутов без id).

        [REASON]: третье число раньше не считалось вовсе. Маршрут без
        `dji_flight_id` ни с каким вылетом не связывается, и ответ, где такой
        есть, подтверждением быть не может -- а по одному только списку id он
        выглядел бы безупречно.
        """
        try:
            from drone_collector.route_decode import (RouteDecodeError,
                                                      decode_route_response)
        except ImportError:  # pragma: no cover
            return None, [], 0
        try:
            decoded = decode_route_response(raw)
        except RouteDecodeError as exc:
            self.log.warning('The observed body did not decode (%s)', exc)
            return None, [], 0
        ids = [value for value in decoded.flight_ids if value is not None]
        without_id = len(decoded.routes) - len(ids)
        return len(decoded.routes), ids, without_id

    # -- отчёт ----------------------------------------------------------------

    def report(self):
        return {
            'probe': 'route-ui',
            'route_responses_seen': self.route_responses,
            'route_observations': len(self.observations),
            'confirmed_route_posts': len(self.confirmed_observations),
            'skipped_over_cap': self.skipped_over_cap,
            'only_all_ids_seen': self.saw_only_all_ids,
            'observations': [item.as_dict() for item in self.observations],
            'nothing_was_queued': True,
            'nothing_was_sent_to_vehicle_soft': True,
            # [REASON]: НЕ «этот инструмент не сделал ни одного запроса» --
            # это было неправдой. Probe открывает кабинет через `open_records()`
            # и тем самым выполняет навигацию. Гарантия, которую он
            # действительно даёт, уже: POST к эндпоинту маршрутов он не
            # инициирует, и весь смысл режима в том, чтобы этот POST сделал
            # сам кабинет.
            'no_route_post_was_initiated_by_probe': True,
        }


def _host_of(url):
    from urllib.parse import urlsplit
    return urlsplit(url).netloc


def _path_of(url):
    from urllib.parse import urlsplit
    return urlsplit(url).path


def _safe_line(observation):
    request = observation.request
    return ('%s %s status=%s bytes=%d kind=%s confirmed=%s ids_in_request=%s '
            'data_type=%s signature_header=%s credential_header=%s '
            'timestamp_header=%s ids_match=%s missing=%s extra=%s'
            % (observation.method, observation.path, observation.http_status,
               observation.response_bytes, observation.payload_kind,
               observation.confirmed,
               request.get('flight_id_count'), request.get('data_type'),
               request.get('carries_signature_like_header'),
               request.get('carries_credential_like_header'),
               request.get('carries_timestamp_like_header'),
               observation.comparison.get('requested_and_returned_match'),
               observation.comparison.get('missing_count'),
               observation.comparison.get('extra_count')))


def _without_header_names(document):
    """Копия отчёта без списков ИМЁН заголовков.

    [REASON]: проверка на секреты ищет подстроки вроде `authorization`,
    `cookie`, `x-auth-token` -- и находит их в ИМЕНАХ заголовков, которые этот
    отчёт и существует, чтобы назвать. Имя заголовка не удостоверение;
    удостоверение -- его значение, а значений `describe_headers` не выпускает
    вовсе, и на это стоит отдельный тест. Поэтому из проверяемого текста
    убираются ровно те два поля, которые заполняет `describe_headers`, и
    больше ничего: всё остальное -- тело запроса, `data_type`, ключи, пути --
    проверяется как было.
    """
    copy = dict(document)
    observations = []
    for item in document.get('observations') or ():
        item = dict(item)
        request = dict(item.get('request') or {})
        request['header_names'] = ['<names-checked-separately>']
        request['sensitive_headers'] = [
            {'name': '<name-checked-separately>',
             'value_length': entry.get('value_length')}
            for entry in (request.get('sensitive_headers') or ())]
        item['request'] = request
        observations.append(item)
    copy['observations'] = observations
    return copy


def write_report(probe, out_dir):
    """Отчёт наблюдения. Ни одного значения заголовка и ни одного тела."""
    from pathlib import Path

    from drone_collector.outbox import find_secret_markers

    target = Path(out_dir) / PROBE_REPORT_NAME
    document = probe.report()
    # [REASON]: та же дисциплина, что у очереди и у сухого прогона. Отчёт
    # строится из описаний, а не из значений, поэтому сюда попасть нечему;
    # проверка стоит на случай, если завтра сюда добавят поле, не подумав.
    found = find_secret_markers(
        json.dumps(_without_header_names(document), ensure_ascii=False))
    if found:
        raise ValueError('the probe report would carry %s; nothing was written'
                         % ', '.join(found))
    text = json.dumps(document, ensure_ascii=False, indent=2)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open('w', encoding='utf-8') as handle:
        handle.write(text)
    return target


PROMPT_LINES = (
    '',
    'This is an OBSERVATION run. It opens the cabinet (that navigation is',
    'its own request) but it never issues the route POST itself, it queues',
    'nothing and it sends nothing to Vehicle Soft.',
    '',
    'A browser window is open on the cabinet. Please, by hand:',
    '',
    '  1. open Task History;',
    '  2. choose ONE day that you know has flights;',
    '  3. switch the view to the map;',
    '  4. wait until the routes are drawn;',
    '  5. come back here and press Enter.',
    '',
    'While you do that, this tool watches the request the cabinet makes for',
    'itself and records its SHAPE: how many ids it asked for, which data_type,',
    'which header names it carried and how long the signature-like values',
    'were. No header value, no cookie, no signature and no request id is read',
    'out of the browser into any file.',
    '',
)
