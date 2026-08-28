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
вовсе -- и тогда фактический размер остаётся НЕИЗВЕСТНЫМ. Заявленное число
кладётся в собственное поле `declared_response_bytes`, а `response_bytes`
остаётся пустым: `Content-Length` ставит отправитель, при chunked его нет, при
сжатии он про сжатое тело, и выдавать обещание отправителя за измерение
нельзя. Отдельный признак `response_body_was_read` говорит, держали ли тело в
руках вообще.

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
import threading
import time

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


# Имя типа исключения: только буквы, цифры и подчёркивание.
_SAFE_TYPE_NAME = re.compile(r'^[A-Za-z_][A-Za-z0-9_]{0,63}$')

# Чем заменяется имя типа, не прошедшее проверку.
UNNAMED_EXCEPTION = 'UnnamedError'


def safe_exception_name(exc):
    """Имя класса исключения -- и больше НИЧЕГО из него.

    [REASON]: `str(exc)` у Playwright несёт URL, куски страницы и иногда
    заголовки. В отчёт и в журнал уходит только имя класса, да и то лишь если
    оно выглядит как имя: класс можно объявить с любым `__name__`, в том числе
    собранным из данных браузера.
    """
    name = type(exc).__name__
    if isinstance(name, str) and _SAFE_TYPE_NAME.match(name):
        return name
    return UNNAMED_EXCEPTION


def monotonic_ms():
    """Монотонное время в миллисекундах. Не зависит от часов машины."""
    return int(time.monotonic() * 1000)


def pump_until(pump, done, now, deadline_ms, poll_ms):
    """Прокачивать цикл событий, пока `done()` не станет истиной.

    Возвращает True, если условие наступило в срок, и False, если вышло
    время. Бесконечного ожидания здесь нет по построению: срок проверяется
    на каждом обороте.

    [REASON]: это и есть исправление корневого дефекта. Ожидание оператора
    стояло в голом `input()`, который держит ПОТОК Playwright, -- пока человек
    смотрел на карту, ни один обработчик события не выполнялся. Они пошли в
    работу уже на выходе из `with FlightCollector`, когда target закрывался,
    и все пять `response.body()` живого прогона 2026-08-27 получили
    `TargetClosedError`. Ждать надо ТАК: коротким циклом, отдавая управление
    Playwright на каждом обороте.
    """
    started = now()
    while True:
        if done():
            return True
        if (now() - started) >= deadline_ms:
            return False
        pump(poll_ms)


def start_operator_prompt(prompt, reader=None):
    """Спросить оператора в ОТДЕЛЬНОМ потоке. Возвращает `threading.Event`.

    Поток демонический: если человек так и не ответил, он не задержит выход
    процесса. Читатель внедряется, чтобы тест не трогал настоящий stdin.
    """
    reader = reader or input
    answered = threading.Event()

    def ask():
        try:
            reader(prompt)
        except Exception:
            # Закрытый stdin -- не повод падать: сработает потолок ожидания.
            pass
        finally:
            answered.set()

    thread = threading.Thread(target=ask, name='route-ui-probe-operator',
                              daemon=True)
    thread.start()
    return answered


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


# Чем заменяется `data_type`, который нельзя ни напечатать, ни сохранить.
#
# [REASON]: метка НЕ называет найденный маркер. Назвать его -- значит положить
# в отчёт слово `authorization` или `signature=`, и проверка самого отчёта на
# секреты после этого падает на собственной метке. Что именно нашлось, видно
# по флагу `data_type_withheld`; что это было за значение -- не видно нигде, и
# в этом весь смысл.
WITHHELD_DATA_TYPE = '<withheld>'


def safe_data_type(value, find_secret_markers=None):
    """(значение, отозвано ли) -- `data_type`, годный для лога и для отчёта.

    Неизвестное, но безобидное значение сохраняется КАК ЕСТЬ и не
    интерпретируется: какой `data_type` просит кабинет -- один из двух
    вопросов, ради которых режим существует.

    [REASON]: `data_type` -- единственное поле тела запроса, которое уходит
    наружу ЗНАЧЕНИЕМ, а не числом. Оно приходит из браузера, попадает в журнал
    сразу (`_safe_line`) и в отчёт -- до того, как отчёт проверят на секреты.
    Значение с маркером удостоверения или формой подписанной ссылки прошло бы
    в лог, откуда его уже ничем не убрать. Поэтому проверка стоит ЗДЕСЬ, на
    входе, до первой печати.
    """
    if not isinstance(value, str) or not value:
        return None, False
    if _CONTROL.search(value):
        # Управляющие символы не вычищаются: строка с ними отвергается целиком.
        return None, True
    if find_secret_markers is None:
        from drone_collector.outbox import find_secret_markers as _finder
        find_secret_markers = _finder
    # Проверяется ПОЛНОЕ значение, а обрезается уже проверенное: маркер за
    # 64-м символом иначе уехал бы из проверки вместе с хвостом.
    if find_secret_markers(value):
        return WITHHELD_DATA_TYPE, True
    return value[:64], False


def summarise_request_body(text, find_secret_markers=None):
    """Что лежало в теле запроса -- числом, а не значением.

    Из тела берутся ровно две вещи: СКОЛЬКО идентификаторов вылетов оно
    несло и какой `data_type` кабинет попросил. Сами идентификаторы не
    печатаются и не сохраняются: они не секрет, но и не наблюдение -- они и
    так известны, а вопрос стоит о форме запроса.
    """
    summary = {'parsed': False, 'flight_id_count': None, 'data_type': None,
               'data_type_withheld': False, 'body_keys': [], 'bytes': 0,
               'detail': ''}
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
    value, withheld = safe_data_type(document.get('data_type'),
                                     find_secret_markers)
    summary['data_type'] = value
    summary['data_type_withheld'] = withheld
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
        'id_comparison_performed': True,
        'requested_and_returned_match': match,
        'invalid_requested_id_count': request.invalid,
        'requested_duplicate_count': request.duplicates,
        'returned_duplicate_count': returned_duplicates,
        'route_without_id_count': without_id,
        'missing_count': len(request.ids - returned_set),
        'extra_count': len(returned_set - request.ids),
    }


def comparison_not_performed(requested):
    """Сверка НЕ выполнялась: ответа не было в руках.

    Счётчики СТОРОНЫ ЗАПРОСА остаются настоящими -- тело запроса мы прочитали,
    и сколько в нём было мусора и повторов, знаем. Счётчики стороны ответа --
    `None`: вернувшегося списка не существовало.

    [REASON]: прежний код на нечитаемом теле звал `compare_id_sets` с пустым
    списком вернувшихся, и отчёт писал `missing_count=39` -- то есть уверенно
    заявлял, что кабинет не вернул тридцать девять запрошенных маршрутов.
    Кабинет их, возможно, вернул: это МЫ не прочитали ответ. Отсюда явный
    флаг: сверка не выполнялась, и ни одно её число не выдумывается.
    """
    if isinstance(requested, RequestedIds):
        request = requested
    else:
        values = set(requested or ())
        request = RequestedIds(ids=values, total=len(values), parsed=True)
    return {
        'id_comparison_performed': False,
        'requested_and_returned_match': False,
        'invalid_requested_id_count': request.invalid,
        'requested_duplicate_count': request.duplicates,
        'returned_duplicate_count': None,
        'route_without_id_count': None,
        'missing_count': None,
        'extra_count': None,
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


# Вид тела, которое НЕ УДАЛОСЬ ПРОЧИТАТЬ.
#
# [REASON]: отдельный вид, а не `EMPTY`. Живой прогон 2026-08-27 получил
# `TargetClosedError` на всех пяти `response.body()`, а прежний код подставлял
# `b''` -- и отчёт написал `payload_kind=EMPTY`, `response_bytes=0`, sha256
# ПУСТОГО тела и «запрошенные ID отсутствуют». Ни одно из этих утверждений не
# было правдой: ответ не был пустым, он не был прочитан. Диагностика, которая
# уверенно говорит неправду, хуже диагностики, которая молчит.
PAYLOAD_KIND_UNREADABLE = 'UNREADABLE'

# Признак ответа, тела которого не было: хеша у него нет и быть не может.
NOT_READ = 'body-not-read'


def observation_id(host, path, method, http_status, request_fingerprint,
                   response_sha256, payload_kind, dji_response_status,
                   ids_match, body_was_read=True):
    """Ключ дедупликации: ВСЁ, от чего зависит подтверждение.

    Повтором считается только семантически одинаковый обмен. Любое различие,
    способное изменить вердикт, делает наблюдение новым.

    [REASON]: ключ был «путь + хеш ответа + отпечаток запроса», и ни метода,
    ни HTTP-статуса в нём не было. Подтверждённый `200 POST`, а следом тот же
    запрос с тем же телом, но `500` -- или тот же запрос как `GET`, -- давали
    ОДИН и тот же ключ, второе наблюдение схлопывалось в повтор первого, и
    прогон, в котором кабинет ответил по-разному, объявлялся полностью
    подтверждённым с кодом 0. Это ровно тот класс ложного успеха, ради
    которого весь режим и переписан: «увидели одинаковое тело» не значит
    «увидели одинаковый ответ».

    Хеш тела здесь не заменяет `payload_kind` и внутренний статус DJI:
    непрочитанное тело хеша не имеет вовсе, и без остальных признаков два
    разных непрочитанных ответа слились бы в один.
    """
    return '|'.join((
        (host or ''),
        (path or ''),
        (method or 'UNKNOWN').upper(),
        'http=%s' % (http_status,),
        'req=%s' % (request_fingerprint or ''),
        'body=%s' % (response_sha256 if body_was_read and response_sha256
                     else NOT_READ),
        'kind=%s' % (payload_kind or ''),
        'dji=%s' % (dji_response_status,),
        'ids=%s' % (bool(ids_match),),
    ))


class RouteUiObservation(object):
    """Один штатный запрос кабинета, увиденный со стороны."""

    __slots__ = ('host', 'path', 'method', 'http_status', 'request',
                 'response_bytes', 'declared_response_bytes', 'body_was_read',
                 'response_sha256', 'payload_kind',
                 'payload_detail', 'decoded_routes', 'returned_id_count',
                 'dji_response_status',
                 'comparison', 'preceded_by_only_all_ids', 'repeats',
                 'confirmed', 'not_confirmed_because')

    def __init__(self, host, path, method, http_status, request,
                 response_bytes, response_sha256, payload_kind,
                 payload_detail='', decoded_routes=None,
                 returned_id_count=None, comparison=None,
                 preceded_by_only_all_ids=False, confirmed=False,
                 not_confirmed_because=(), declared_response_bytes=None,
                 body_was_read=True, dji_response_status=None):
        self.host = host
        self.path = path
        self.method = method
        self.http_status = http_status
        self.request = request
        self.response_bytes = response_bytes
        self.declared_response_bytes = declared_response_bytes
        self.body_was_read = body_was_read
        self.response_sha256 = response_sha256
        self.payload_kind = payload_kind
        self.payload_detail = payload_detail
        self.decoded_routes = decoded_routes
        self.returned_id_count = returned_id_count
        self.dji_response_status = dji_response_status
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
            # [REASON]: три РАЗНЫХ поля, а не одно. `response_bytes` --
            # фактический размер прочитанного тела и только он;
            # `declared_response_bytes` -- то, что сказал `Content-Length`,
            # число отправителя, которое при chunked отсутствует, а при сжатии
            # описывает сжатое тело; `response_body_was_read` говорит, было ли
            # тело вообще получено. Прежняя редакция при раннем отказе клала
            # заявленное число в `response_bytes` -- то есть выдавала обещание
            # отправителя за измерение.
            'response_bytes': self.response_bytes,
            'declared_response_bytes': self.declared_response_bytes,
            'response_body_was_read': self.body_was_read,
            'response_sha256': self.response_sha256,
            'payload_kind': self.payload_kind,
            'payload_detail': self.payload_detail,
            'decoded_routes': self.decoded_routes,
            'returned_id_count': self.returned_id_count,
            'dji_response_status': self.dji_response_status,
            'repeats': self.repeats,
            'confirmed_route_post': self.confirmed,
            'not_confirmed_because': list(self.not_confirmed_because),
        }
        # Булево и счётчики -- ни одного идентификатора.
        document.update(self.comparison)
        return document


# Статус УСПЕХА во внутреннем конверте DJI.
#
# [REASON]: зеркалит `drone_collector.route_decode.STATUS_OK` и намеренно
# продублирован числом, чтобы `confirmation_failures` осталась чистой
# stdlib-функцией без импорта декодера. Совпадение двух констант держит
# отдельный тест: разойтись молча они не могут.
DJI_ENVELOPE_STATUS_OK = 200


def confirmation_failures(host, path, method, http_status, payload_kind,
                          decoded_routes, comparison, expected_origin,
                          dji_response_status=None):
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
    if payload_kind == PAYLOAD_KIND_UNREADABLE:
        # [REASON]: одна причина вместо трёх ложных. Про тело, которого не
        # было в руках, нельзя сказать ни «не двоичное», ни «не
        # декодировалось», ни «ID не совпали»: всё это утверждения о
        # содержимом, а содержимого мы не видели.
        problems.append('the response body could not be read')
    elif decoded_routes is None:
        if payload_kind != 'BINARY':
            problems.append('the payload is not a binary route payload')
        problems.append('the payload did not decode')
    elif dji_response_status != DJI_ENVELOPE_STATUS_OK:
        # [REASON]: HTTP 200 в этом API не значит «успех» -- тракт вылетов
        # выучил это ещё в июле, а `decode_route_response` прямо пишет в
        # докстринге, что не-OK статус он не поднимает, а ОТДАЁТ вызывающему,
        # и разобраться обязан вызывающий. `_decode_ids` это состояние
        # выбрасывал: конверт `status=101` «подписи нет», приехавший с
        # маршрутами внутри и совпавшими ID, объявлялся подтверждением.
        # Незнакомый статус не толкуется -- подтверждением считается ровно
        # один, известный.
        problems.append('the DJI envelope status is not the success status')
    if not (comparison or {}).get('id_comparison_performed', True):
        problems.append('the requested and returned id sets were never '
                        'compared')
    elif not (comparison or {}).get('requested_and_returned_match'):
        problems.append('the requested and returned id sets do not match')
    return problems


# Коды выхода режима наблюдения. Совпадают с `drone_collector.main`; здесь они
# нужны, чтобы решение принималось чистой функцией, которую видно тестам.
PROBE_EXIT_OK = 0
PROBE_EXIT_NOTHING_OBSERVED = 6
PROBE_EXIT_UNCONFIRMED = 13


def probe_exit_code(observations, confirmed, skipped_over_cap,
                    observation_errors=0, drain_timed_out=False,
                    operator_answered=True):
    """Каким кодом заканчивается прогон наблюдения.

    Ноль разрешён ровно при пяти условиях сразу: наблюдение было, КАЖДОЕ
    учтённое наблюдение подтверждено, ни одно не выпало по лимиту, ни на
    одном ответе слушатель не споткнулся и ожидание ответов после сигнала
    оператора закончилось тишиной, а не сроком.

    [REASON]: смешанный результат давал ложный ноль. Один подтверждённый POST
    рядом с неподтверждённым ответом означает, что кабинет отвечал по-разному,
    и объявлять такой прогон успешным -- это ровно тот класс ложного успеха,
    ради которого весь этот разбор и ведётся. Пропущенное по лимиту
    наблюдение -- то же самое: про него не известно ничего, а «не известно» не
    равно «подтверждено».

    Ошибка слушателя проверяется ПЕРВОЙ, до «ничего не наблюдалось»: если
    ответ пришёл, а прочитать его не удалось, сказать «ничего не было» --
    неправда, и код 6 увёл бы оператора не туда.

    [REASON]: `note_response` ловит любое исключение, чтобы не уронить цикл
    Playwright, -- и раньше на этом всё заканчивалось: предупреждение уходило
    в лог, а решение о результате об ошибке не знало. Прогон, в котором
    слушатель споткнулся на одном ответе и разобрал другой, объявлялся
    полностью подтверждённым.
    """
    if drain_timed_out:
        # [REASON]: вышедший срок означает, что какой-то ответ мог остаться
        # необработанным. Про него не известно ничего, и объявлять прогон
        # успешным нельзя -- ровно как с наблюдением, выпавшим по лимиту.
        return PROBE_EXIT_UNCONFIRMED
    if not operator_answered:
        # [REASON]: человек не сказал, что маршруты нарисованы. Значит никто
        # не подтвердил, что кабинет вообще довели до нужного состояния, и
        # объявлять такой прогон успешным нечем.
        return PROBE_EXIT_UNCONFIRMED
    if int(observation_errors or 0) > 0:
        return PROBE_EXIT_UNCONFIRMED
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

    def __init__(self, logger=None, expected_origin=None, clock=None):
        self.log = logger or log
        self.expected_origin = expected_origin
        # Часы в миллисекундах. Внедряются, чтобы «тишину» можно было
        # проверить без сна в тестах.
        self.clock = clock or monotonic_ms
        self.observations = []
        self._seen = {}
        self.saw_only_all_ids = 0
        self._ids_seen_recently = False
        self.route_responses = 0
        self.skipped_over_cap = 0
        # [REASON]: ответ, на котором слушатель споткнулся, -- не «ничего».
        # Счётчик существует, чтобы это состояние доходило до кода выхода, а
        # не оставалось строчкой в логе.
        self.observation_errors = 0
        # Сколько обработчиков ответа выполняется прямо сейчас и когда
        # последний ответ маршрутов был замечен.
        #
        # [REASON]: по этим двум величинам решается, дождался ли прогон
        # окончания уже начавшихся ответов. Закрывать браузер, пока
        # обработчик в работе, -- это и есть дефект, из-за которого пять
        # живых ответов получили `TargetClosedError`.
        self.responses_in_flight = 0
        self.last_route_activity = None

    def is_quiet(self, now_ms, quiet_ms):
        """Ответов маршрутов не было `quiet_ms` и ни один не в работе."""
        if self.responses_in_flight > 0:
            return False
        if self.last_route_activity is None:
            return True
        return (now_ms - self.last_route_activity) >= quiet_ms

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
        except Exception as exc:
            # Наружу идёт ТИП исключения и счётчик. Ни текста исключения, ни
            # значений: сообщение приходит из браузера и может нести что
            # угодно.
            self.observation_errors += 1
            # [REASON]: соседство гасится и здесь. Что это был за ответ, мы не
            # узнали, и переносить `only_all_ids` на СЛЕДУЮЩИЙ обмен нельзя:
            # отчёт утверждал бы соседство, которого никто не видел.
            self._ids_seen_recently = False
            self.log.warning('The probe could not read a response (%s); '
                             '%d such failure(s) so far',
                             type(exc).__name__, self.observation_errors)

    def _note_response(self, response):
        url = getattr(response, 'url', '') or ''
        if is_ids_url(url):
            # Обмен уже посчитан на запросе; здесь только отметка соседства.
            self._ids_seen_recently = True
            return
        if not is_route_url(url):
            return

        self.route_responses += 1
        self.last_route_activity = self.clock()
        self.responses_in_flight += 1
        try:
            self._note_route_response(response, url)
        finally:
            self.responses_in_flight -= 1
            # Отметка ставится и на выходе: обработчик, который шёл секунду,
            # держит «тишину» ненаступившей ещё `quiet_ms` после себя.
            self.last_route_activity = self.clock()

    def _note_route_response(self, response, url):
        # [REASON]: соседство с `only_all_ids` ПОТРЕБЛЯЕТСЯ здесь, на каждом
        # ответе маршрутов, а не только на том, который стал новым
        # наблюдением. Раньше флаг сбрасывался в самом конце, и обмен,
        # выпавший по лимиту или схлопнувшийся в повтор, оставлял его
        # поднятым -- следующее наблюдение получало чужое соседство.
        preceded = self._ids_seen_recently
        self._ids_seen_recently = False

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
        unreadable = False
        body_error_type = ''
        declared_over_limit = (declared is not None
                               and declared > MAX_PROCESSED_RESPONSE_BYTES)
        raw = b''
        actual_bytes = None
        body_was_read = True
        if declared_over_limit:
            # Тело не запрашивается вовсе: заголовок уже говорит, что
            # обрабатывать его мы не станем.
            #
            # [REASON]: фактический размер остаётся НЕИЗВЕСТНЫМ, и поле для
            # него остаётся пустым. Заявленное число ложится в собственное
            # поле и фактическим не притворяется: `Content-Length` ставит
            # отправитель, при chunked его нет, при сжатии он про сжатое тело,
            # а здесь тела не было в руках вовсе -- проверить нечем.
            self.log.warning('The route response declares %d bytes, over the '
                             'processing limit of %d; the body was not '
                             'requested and its real size stays unknown.',
                             declared, MAX_PROCESSED_RESPONSE_BYTES)
            oversize = True
            body_was_read = False
            actual_bytes = None
        else:
            try:
                raw = bytes(response.body() or b'')
            except Exception as exc:
                # [REASON]: НЕ подставлять `b''`. Живой прогон 2026-08-27
                # получил `TargetClosedError` на всех пяти ответах, и подмена
                # пустым телом превратила пять нечитаемых ответов в пять
                # «пустых»: `payload_kind=EMPTY`, `response_bytes=0`, sha256
                # пустоты и «тридцать девять запрошенных ID отсутствуют».
                # Каждое из этих утверждений было неправдой.
                unreadable = True
                body_was_read = False
                body_error_type = safe_exception_name(exc)
                self.observation_errors += 1
                raw = b''
                self.log.warning('The route response body could not be read '
                                 '(%s); it is recorded as UNREADABLE, not as '
                                 'empty; %d such failure(s) so far',
                                 body_error_type, self.observation_errors)
            if unreadable:
                actual_bytes = None
            else:
                actual_bytes = len(raw)
            if actual_bytes is not None \
                    and actual_bytes > MAX_PROCESSED_RESPONSE_BYTES:
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

        if unreadable:
            digest = None
            payload_kind = PAYLOAD_KIND_UNREADABLE
            # Только безопасное ИМЯ типа исключения: ни текста, ни данных
            # браузера. Имя пропущено через `safe_exception_name`.
            payload_detail = ('the response body could not be read (%s); it '
                              'was not empty -- it was never read'
                              % body_error_type)
            decoded_routes = returned = None
            returned_ids = []
            routes_without_id = 0
            dji_status = None
        elif oversize:
            digest = None
            payload_kind = 'TOO_LARGE'
            if declared_over_limit:
                payload_detail = (
                    'Content-Length declared %d bytes, over the processing '
                    'limit of %d; the body was never requested, so its real '
                    'size is unknown -- not hashed, not decoded, not stored'
                    % (declared, MAX_PROCESSED_RESPONSE_BYTES))
            else:
                payload_detail = (
                    '%d bytes measured, over the processing limit of %d; not '
                    'hashed, not decoded, not stored'
                    % (actual_bytes, MAX_PROCESSED_RESPONSE_BYTES))
            decoded_routes = returned = None
            returned_ids = []
            routes_without_id = 0
            dji_status = None
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
            dji_status = None
            if verdict.kind == PAYLOAD_BINARY:
                decoded_routes, returned_ids, routes_without_id, dji_status = \
                    self._decode_ids(raw)
                returned = (len(set(returned_ids))
                            if decoded_routes is not None else None)

        if unreadable:
            # Сверка не выполнялась. Ни `missing`, ни `extra` не выдумываются.
            comparison = comparison_not_performed(requested)
        else:
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
            comparison, self.expected_origin, dji_status)

        # [REASON]: ключ считается ПОСЛЕ сверки и вердикта, а не до них.
        # Иначе в него нечего было бы положить, кроме тела, -- а именно это и
        # схлопывало `200 POST` с `500` и с `GET` в одно наблюдение.
        key = observation_id(
            host=host, path=path, method=method, http_status=status,
            request_fingerprint=fingerprint, response_sha256=digest,
            payload_kind=payload_kind, dji_response_status=dji_status,
            ids_match=comparison.get('requested_and_returned_match'),
            body_was_read=body_was_read)
        if key in self._seen:
            self._seen[key].repeats += 1
            return

        observation = RouteUiObservation(
            host=host, path=path, method=method, http_status=status,
            request=description, response_bytes=actual_bytes,
            declared_response_bytes=declared, body_was_read=body_was_read,
            response_sha256=digest, payload_kind=payload_kind,
            payload_detail=payload_detail, decoded_routes=decoded_routes,
            returned_id_count=returned, dji_response_status=dji_status,
            comparison=comparison,
            preceded_by_only_all_ids=preceded,
            confirmed=not problems, not_confirmed_because=problems)
        self._seen[key] = observation
        self.observations.append(observation)
        self.log.info('Observed a route response: %s',
                      _safe_line(observation))

    def _decode_ids(self, raw):
        """(маршрутов, список вернувшихся id, маршрутов без id, статус DJI).

        [REASON]: третье число раньше не считалось вовсе. Маршрут без
        `dji_flight_id` ни с каким вылетом не связывается, и ответ, где такой
        есть, подтверждением быть не может -- а по одному только списку id он
        выглядел бы безупречно.

        [REASON]: четвёртое -- внутренний статус конверта. Он и раньше был у
        `RouteResponse`, и его докстринг прямо требует, чтобы вызывающий его
        посмотрел: не-OK статус декодер не поднимает, а ОТДАЁТ. Этот метод его
        выбрасывал, и отказ `status=101` «подписи нет» с маршрутами внутри
        доходил до вердикта как безупречный ответ. Статус отдаётся числом и
        здесь не толкуется.
        """
        try:
            from drone_collector.route_decode import (RouteDecodeError,
                                                      decode_route_response)
        except ImportError:  # pragma: no cover
            return None, [], 0, None
        try:
            decoded = decode_route_response(raw)
        except RouteDecodeError as exc:
            self.log.warning('The observed body did not decode (%s)', exc)
            return None, [], 0, None
        ids = [value for value in decoded.flight_ids if value is not None]
        without_id = len(decoded.routes) - len(ids)
        status = decoded.status if isinstance(decoded.status, int) else None
        if not decoded.is_ok:
            # Число, а не текст: `message` приходит от поставщика.
            self.log.warning('The observed body decoded, but the DJI envelope '
                             'status is %s, not %s', status,
                             DJI_ENVELOPE_STATUS_OK)
        return len(decoded.routes), ids, without_id, status

    # -- отчёт ----------------------------------------------------------------

    def report(self, operator_answered=None, drain_completed=None):
        """Безопасный отчёт наблюдения.

        `operator_answered` и `drain_completed` приходят снаружи: сам
        наблюдатель ничего не знает про ожидание -- он только слушает.
        """
        return {
            'probe': 'route-ui',
            'operator_answered': operator_answered,
            'response_drain_completed': drain_completed,
            'route_responses_seen': self.route_responses,
            'route_observations': len(self.observations),
            'confirmed_route_posts': len(self.confirmed_observations),
            'skipped_over_cap': self.skipped_over_cap,
            'observation_errors': self.observation_errors,
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
    """Одна строка журнала. Ни значения заголовка, ни тела, ни ID.

    [REASON]: `bytes` печатается через `%s`, а не `%d`. Фактического размера
    у ответа, тело которого не запрашивалось, НЕТ, и поле остаётся пустым;
    `%d` на `None` уронил бы слушателя ровно в тот момент, когда он обязан
    устоять. Заявленный размер печатается отдельным полем и фактическим не
    притворяется.

    `data_type` сюда приходит уже отозванным, если в нём нашёлся маркер:
    санитайз стоит в `safe_data_type`, на входе, а не здесь -- журнал пишется
    раньше, чем отчёт проверяется на секреты.
    """
    request = observation.request
    return ('%s %s status=%s bytes=%s declared=%s body_read=%s kind=%s '
            'dji_status=%s confirmed=%s ids_in_request=%s '
            'data_type=%s data_type_withheld=%s '
            'signature_header=%s credential_header=%s '
            'timestamp_header=%s ids_match=%s missing=%s extra=%s'
            % (observation.method, observation.path, observation.http_status,
               observation.response_bytes,
               observation.declared_response_bytes, observation.body_was_read,
               observation.payload_kind, observation.dji_response_status,
               observation.confirmed,
               request.get('flight_id_count'), request.get('data_type'),
               request.get('data_type_withheld'),
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


def write_report(probe, out_dir, operator_answered=None,
                 drain_completed=None):
    """Отчёт наблюдения. Ни одного значения заголовка и ни одного тела."""
    from pathlib import Path

    from drone_collector.outbox import find_secret_markers

    target = Path(out_dir) / PROBE_REPORT_NAME
    document = probe.report(operator_answered=operator_answered,
                            drain_completed=drain_completed)
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
