# -*- coding: utf-8 -*-
"""Клиент API agro-work, который умеет только читать. Только stdlib.

ЧТО ОН МОЖЕТ ОТПРАВИТЬ -- И НИЧЕГО БОЛЬШЕ

    POST /auth/token/                        вход: пара JWT
    POST /auth/token/refresh/                новый токен доступа
    GET  /applications/                      список заявок
    GET  /applications/<uuid>/history/       история статусов одной заявки
    GET  /transports/                        реестр машин
    GET  /work-types/                        виды работ

Любой другой метод или адрес -- `RefusedRequest` ДО того, как открыт сокет.
Это требование владельца от 28.09: импорт идёт под его админской учётной
записью, у которой в agro-work есть права на запись, поэтому «только чтение»
обеспечивает клиент, а не роль. Проверяет это тест, а не комментарий.

[REASON]: для GET тоже список, а не «любой GET». В B0 среди методов, меняющих
состояние, найдены `/users/{id}/reset-password/` и выгрузки `/export/`; как
именно они устроены на сервере, мы не знаем, и проверять это нашим запросом
нельзя. Адрес, которого нет в списке, не уходит ни с каким методом.

[REASON]: переходы по редиректам запрещены. urllib по умолчанию идёт по 3xx
сам, мимо этой проверки, и несёт с собой заголовок Authorization -- в том
числе на другой хост. Ответ 3xx здесь -- ошибка с кодом, а не переход.

[REASON]: страницы строятся параметром `page`, а ссылка `next` из ответа не
открывается. DRF собирает её из заголовков запроса, и за прокси без
X-Forwarded-Proto она приходит с `http://` -- переход по ней отправил бы
токен открытым текстом.

СЕКРЕТЫ. Логин, пароль и токены не печатаются и не попадают в текст
исключений: в сообщениях -- метод, адрес без параметров, код ответа, имена
ключей тела ошибки и поле `detail`, если это короткая строка. Значения
остальных полей ответа об ошибке не копируются никуда.

ТЕМП. Не чаще одного запроса в `pause` секунд, что бы ни отвечал сервер;
жёсткий срок на запрос целиком; три попытки при обрыве связи, 429 и 5xx;
Retry-After уважается, пока он разумен.
"""

import json
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from . import config

AUTH_LOGIN = '/auth/token/'
AUTH_REFRESH = '/auth/token/refresh/'

_UUID = r'[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}'
READ_PATHS = (
    re.compile(r'^/applications/$'),
    re.compile(r'^/applications/' + _UUID + r'/history/$'),
    re.compile(r'^/transports/$'),
    re.compile(r'^/work-types/$'),
)
AUTH_PATHS = (AUTH_LOGIN, AUTH_REFRESH)

# Ответы, после которых запрос повторяется: сервер занят или прокси упал.
RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})


class RefusedRequest(Exception):
    """Запрос не входит в разрешённые и не отправлен."""


class ApiError(Exception):
    """Сервер ответил не 2xx или ответ нельзя разобрать."""

    def __init__(self, status, method, path, detail=''):
        self.status = status
        self.method = method
        self.path = path
        self.detail = detail
        text = '%s %s -> %s' % (method, path, status)
        if detail:
            text += ' (%s)' % detail
        super().__init__(text)


class AuthFailed(ApiError):
    """Вход или обновление токена не удались."""


def allowed(method, path):
    """Можно ли отправить такой запрос. Единственное место этого решения."""
    if method == 'GET':
        return any(pattern.match(path) for pattern in READ_PATHS)
    if method == 'POST':
        return path in AUTH_PATHS
    return False


def error_detail(raw):
    """Что из тела ответа об ошибке можно показать человеку.

    Имена ключей и `detail`, если это короткая строка. DRF кладёт в `detail`
    фразы вида «No active account found with the given credentials», а в
    остальные ключи -- сообщения к полям: имена полей показывают, какого поля
    сервер ждёт при входе, и ничего секретного не несут.
    """
    try:
        body = json.loads(raw.decode('utf-8'))
    except (ValueError, UnicodeDecodeError, AttributeError):
        return ''
    if not isinstance(body, dict):
        return ''
    parts = []
    detail = body.get('detail')
    if isinstance(detail, str) and detail.strip() and len(detail) <= 200:
        parts.append('detail: %s' % config.ascii_only(detail.strip()))
    keys = sorted(k for k in body if isinstance(k, str) and k != 'detail')
    if keys:
        parts.append('keys: %s' % ', '.join(config.ascii_only(k)[:40]
                                            for k in keys[:20]))
    return '; '.join(parts)


def find_tokens(answer):
    """(access, refresh) из ответа входа, где бы они ни лежали.

    [REASON]: B0 форму ответа входа не видел -- `/auth/token/` не вызывался.
    SimpleJWT отвечает `{"access": ..., "refresh": ...}`, самописные виды --
    `access_token`, `token`, или вкладывают пару в `tokens`/`data`. Ищется
    здесь, а не угадывается в одном месте кода: если не найдено, `--check`
    назовёт ключи ответа, и правка будет в одну строку.
    """
    def pick(node, names):
        for name in names:
            value = node.get(name)
            if isinstance(value, str) and value.strip() and ' ' not in value.strip():
                return value.strip()
        return None

    if not isinstance(answer, dict):
        return None, None
    nodes = [answer] + [answer[k] for k in ('tokens', 'data', 'token')
                        if isinstance(answer.get(k), dict)]
    access = refresh = None
    for node in nodes:
        access = access or pick(node, ('access', 'access_token', 'token'))
        refresh = refresh or pick(node, ('refresh', 'refresh_token'))
    return access, refresh


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """3xx не выполняется: urllib вернёт его ошибкой с кодом."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def build_opener(proxies=None):
    """Открыватель без перехода по редиректам.

    `proxies=None` -- прокси из окружения, как у urllib по умолчанию; `{}` --
    без прокси вовсе (тесты ходят на 127.0.0.1).
    """
    handlers = [_NoRedirect()]
    if proxies is not None:
        handlers.append(urllib.request.ProxyHandler(proxies))
    return urllib.request.build_opener(*handlers)


class Client:
    """Одна сессия чтения agro-work. Счётчики -- часть контракта.

    `requests`, `logins`, `refreshes`, `abandoned` ложатся в журнал прогона:
    «бережно к их серверу» стоит чего-то, только если что-то считает запросы.
    """

    def __init__(self, credentials, base_url=None, pause=None, log=print,
                 opener=None, sleep=time.sleep, clock=time.monotonic):
        self._login_value = credentials['login']
        self._password = credentials['password']
        self.login_field = credentials.get('login_field') or config.DEFAULT_LOGIN_FIELD
        self.auth_scheme = credentials.get('auth_scheme') or config.DEFAULT_AUTH_SCHEME
        self.base_url = (base_url or config.BASE_URL).rstrip('/')
        self.pause = config.PAUSE_S if pause is None else max(0.0, pause)
        self.log = log
        self._opener = opener or build_opener()
        self._sleep = sleep
        self._clock = clock
        self._access = None
        self._refresh = None
        self._last_call_at = None
        self.requests = 0
        self.logins = 0
        self.refreshes = 0
        self.abandoned = 0

    def __repr__(self):
        # [REASON]: объект клиента держит пароль и токены. repr по умолчанию
        # их не печатает, но отладочная печать `vars(client)` -- печатает;
        # здесь хотя бы repr остаётся безопасным.
        return '<agro_work.Client %s requests=%d>' % (self.base_url, self.requests)

    # --- темп ----------------------------------------------------------------

    def _pace(self):
        if self._last_call_at is not None:
            waiting = self.pause - (self._clock() - self._last_call_at)
            if waiting > 0:
                self._sleep(waiting)
        self._last_call_at = self._clock()

    # --- один запрос -----------------------------------------------------------

    def _fetch(self, request, box):
        try:
            with self._opener.open(request, timeout=config.TIMEOUT) as response:
                box['status'] = response.status
                box['headers'] = response.headers
                box['raw'] = response.read(config.MAX_BODY_BYTES + 1)
        except urllib.error.HTTPError as exc:
            box['status'] = exc.code
            box['headers'] = exc.headers
            try:
                box['raw'] = exc.read(64 * 1024)
            except Exception:                                      # noqa: BLE001
                box['raw'] = b''
        except Exception as exc:                                   # noqa: BLE001
            box['error'] = exc

    def _retry_after(self, headers, attempt):
        value = None
        if headers is not None:
            value = headers.get('Retry-After')
        if value is not None:
            try:
                seconds = float(value)
            except (TypeError, ValueError):
                seconds = None
            if seconds is not None:
                if seconds > config.MAX_RETRY_AFTER_S:
                    return None
                return max(seconds, 0.0)
        return float(config.RETRY_PAUSE_S[min(attempt, len(config.RETRY_PAUSE_S) - 1)])

    def send(self, method, path, params=None, body=None, auth=True):
        """Один разрешённый запрос с темпом, сроком и повторами. JSON ответа.

        Всё, что не разрешено `allowed`, отказывается здесь, до сети.
        """
        if not allowed(method, path):
            raise RefusedRequest('%s %s is not a read of agro-work: refused '
                                 'without sending' % (method, path))
        url = self.base_url + path
        if params:
            url += '?' + urllib.parse.urlencode(sorted(params.items()))
        headers = {'Accept': 'application/json'}
        data = None
        if body is not None:
            data = json.dumps(body).encode('utf-8')
            headers['Content-Type'] = 'application/json'
        if auth and self._access:
            headers['Authorization'] = '%s %s' % (self.auth_scheme, self._access)
        request = urllib.request.Request(url, data=data, headers=headers,
                                         method=method)

        last = None
        for attempt in range(config.RETRIES):
            self._pace()
            self.requests += 1
            box = {}
            worker = threading.Thread(target=self._fetch, args=(request, box),
                                      daemon=True)
            worker.start()
            worker.join(config.HARD_DEADLINE_S)
            if worker.is_alive():
                # [REASON]: поток -- демон и умрёт вместе с процессом. Бросить
                # зависший запрос -- то, что держит прогон в движении.
                self.abandoned += 1
                last = ApiError('timeout', method, path,
                                'no answer in %d s' % int(config.HARD_DEADLINE_S))
                pause = self._retry_after(None, attempt)
            elif 'error' in box:
                last = ApiError('network', method, path,
                                type(box['error']).__name__)
                pause = self._retry_after(None, attempt)
            else:
                status = box['status']
                raw = box.get('raw') or b''
                if 200 <= status < 300:
                    if len(raw) > config.MAX_BODY_BYTES:
                        raise ApiError(status, method, path,
                                       'answer larger than %d bytes'
                                       % config.MAX_BODY_BYTES)
                    try:
                        return json.loads(raw.decode('utf-8'))
                    except (ValueError, UnicodeDecodeError):
                        raise ApiError(status, method, path,
                                       'answer is not JSON') from None
                if 300 <= status < 400:
                    raise ApiError(status, method, path, 'redirect refused')
                if status not in RETRY_STATUSES:
                    raise ApiError(status, method, path, error_detail(raw))
                last = ApiError(status, method, path, error_detail(raw))
                pause = self._retry_after(box.get('headers'), attempt)
                if pause is None:
                    raise ApiError(status, method, path,
                                   'server asks to wait longer than %d s'
                                   % config.MAX_RETRY_AFTER_S)
            if attempt + 1 < config.RETRIES:
                self.log('    agro-work: %s, povtor cherez %d s'
                         % (last, int(pause)))
                self._sleep(pause)
        raise last

    # --- вход ------------------------------------------------------------------

    def login(self):
        body = {self.login_field: self._login_value, 'password': self._password}
        try:
            answer = self.send('POST', AUTH_LOGIN, body=body, auth=False)
        except ApiError as exc:
            raise AuthFailed(exc.status, exc.method, exc.path, exc.detail) from None
        access, refresh = find_tokens(answer)
        if not access:
            keys = sorted(answer) if isinstance(answer, dict) else []
            raise AuthFailed(200, 'POST', AUTH_LOGIN,
                             'no access token in the answer; keys: %s'
                             % ', '.join(config.ascii_only(k)[:40]
                                         for k in keys[:20]))
        self._access, self._refresh = access, refresh
        self.logins += 1

    def refresh_access(self):
        if not self._refresh:
            raise AuthFailed(401, 'POST', AUTH_REFRESH, 'no refresh token')
        try:
            answer = self.send('POST', AUTH_REFRESH,
                               body={'refresh': self._refresh}, auth=False)
        except ApiError as exc:
            raise AuthFailed(exc.status, exc.method, exc.path, exc.detail) from None
        access, refresh = find_tokens(answer)
        if not access:
            raise AuthFailed(200, 'POST', AUTH_REFRESH,
                             'no access token in the answer')
        self._access = access
        if refresh:
            # [REASON]: при ROTATE_REFRESH_TOKENS прежний refresh больше не
            # годится. Держать старый значит упасть на следующем обновлении.
            self._refresh = refresh
        self.refreshes += 1

    @property
    def has_refresh_token(self):
        return bool(self._refresh)

    # --- чтение ----------------------------------------------------------------

    def get(self, path, params=None):
        """GET с входом по требованию и одним обновлением токена на 401."""
        if self._access is None:
            self.login()
        try:
            return self.send('GET', path, params)
        except ApiError as exc:
            if exc.status != 401:
                raise
        # Токен доступа истёк: сначала обновление, при отказе -- новый вход.
        try:
            self.refresh_access()
        except AuthFailed:
            self.login()
        return self.send('GET', path, params)

    def pages(self, path, params=None, page_size=None):
        """(номер страницы, ответ) для каждой страницы списка DRF.

        Ответ-список (без листания) отдаётся одной страницей.
        """
        size = page_size or config.PAGE_SIZE
        page = 1
        while True:
            query = dict(params or {})
            query['page'] = page
            query['page_size'] = size
            answer = self.get(path, query)
            yield page, answer
            if not isinstance(answer, dict) or not answer.get('next'):
                return
            page += 1
            if page > config.MAX_PAGES:
                raise ApiError('pages', 'GET', path,
                               'more than %d pages, stopped' % config.MAX_PAGES)

    def history(self, application_id):
        """События истории одной заявки списком, в какой бы форме их ни отдали."""
        path = '/applications/%s/history/' % application_id
        events = []
        for _, answer in self.pages(path):
            if isinstance(answer, list):
                events.extend(answer)
            elif isinstance(answer, dict) and isinstance(answer.get('results'), list):
                events.extend(answer['results'])
            elif isinstance(answer, dict) and isinstance(answer.get('history'), list):
                events.extend(answer['history'])
            else:
                raise ApiError(200, 'GET', path, 'unexpected history shape')
        return events
