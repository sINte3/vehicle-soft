# -*- coding: utf-8 -*-
"""drone_collector/session.py -- the saved browser session.

The collector never types credentials. A human signs in once, by hand, in a
real browser window; the resulting cookies and local storage are written to
storage_state.json and every later run reuses them.

[REASON]: this is a deliberate narrowing of the previous collector, which read
DJI_LOGIN and DJI_PASSWORD from the environment and drove the SSO form itself.
That code had no path for an e-mail or SMS confirmation code and none for a
captcha, so it broke the moment DJI asked for either -- and it kept a live
account password in a .env file on the server for the privilege. Signing in by
hand costs a few minutes per session and removes both problems. Neither this
module nor any other in the package accepts credentials as an argument or
reads them from the environment.

The saved file is a live session. It is ignored by drone_collector/.gitignore
and must never be committed, copied into a ticket, or logged.

WHY THE CHECK IS STRUCTURAL AND NOT "THE FILE IS NOT EMPTY"
----------------------------------------------------------

The first live pilot of stage B (2026-08-27) ran `--save-session`, was told the
session had been saved, got exit code 0 -- and the file was thirty bytes:

    {"cookies": [], "origins": []}

That is what Playwright writes when the context holds nothing: a syntactically
perfect storage state with no session in it. The old check was `is_file() and
st_size > 0`, so thirty bytes of emptiness passed as a working login, and the
failure surfaced much later as a puzzling redirect to /login.

So the file is now judged by what it CARRIES: at least one cookie, or at least
one localStorage item. A real session of this cabinet, measured on that same
machine, was ~83 KB with 14 cookies, 2 origins and 13 localStorage items.

NOTHING IN THIS MODULE PRINTS A VALUE
-------------------------------------

Counts, sizes and names of the failure -- never a cookie value, never a
localStorage value, never an origin. The whole file is one long credential.
"""

import json
import logging
import os
import re
import tempfile

from pathlib import Path

from urllib.parse import urlsplit, urlunsplit

log = logging.getLogger(__name__)

# Path on the SmartFarm host that shows the sign-in page. Derived from
# DJI_RECORDS_URL so that a changed host only has to be changed in one place.
LOGIN_PATH = '/login'

# Playwright's own wording for the two states we care about after navigation.
NAVIGATION_WAIT_UNTIL = 'domcontentloaded'

# Ceiling on the storage state we are willing to read.
#
# [REASON]: the real session of this cabinet measured 82 919 bytes. Eight
# megabytes is two orders of magnitude above that and still small enough that
# a wrong --DJI_STORAGE_STATE pointing at, say, a database file is refused
# instead of being parsed as JSON for a minute.
MAX_SESSION_BYTES = 8 * 1024 * 1024

# Suffix of the half-written state. Not `.json`, so nothing mistakes it for a
# session, and it lives beside the target so os.replace stays atomic.
TEMP_SUFFIX = '.partial'


class SessionMissing(Exception):
    """No usable storage_state.json. main() turns this into exit code 2."""


class SessionState(object):
    """What a storage-state file carries. Values are never held here."""

    __slots__ = ('usable', 'reason', 'bytes', 'cookies', 'origins',
                 'local_storage_items')

    def __init__(self, usable, reason='', size=0, cookies=0, origins=0,
                 local_storage_items=0):
        self.usable = usable
        self.reason = reason
        self.bytes = size
        self.cookies = cookies
        self.origins = origins
        self.local_storage_items = local_storage_items

    def describe(self):
        """Safe one-liner for a log. Counts only."""
        return ('bytes=%d cookies=%d origins=%d local_storage_items=%d'
                % (self.bytes, self.cookies, self.origins,
                   self.local_storage_items))

    def __repr__(self):
        return '<SessionState usable=%s %s>' % (self.usable, self.describe())


def _count_local_storage(origins):
    """How many localStorage entries the origins carry, in total."""
    total = 0
    for origin in origins:
        if not isinstance(origin, dict):
            continue
        items = origin.get('localStorage')
        if isinstance(items, list):
            total += len(items)
    return total


def inspect_session(path):
    """Structural verdict on a Playwright storage state. Never raises.

    Returns a SessionState. `reason` names the defect and never quotes the
    content: a message that echoed a cookie to explain why it was malformed
    would put the credential in the log it was trying to protect.
    """
    target = Path(path)
    try:
        if not target.is_file():
            return SessionState(False, 'no such file')
        size = target.stat().st_size
    except OSError as exc:
        return SessionState(False, 'could not be read (%s)'
                            % type(exc).__name__)

    if size == 0:
        return SessionState(False, 'the file is empty', size=size)
    if size > MAX_SESSION_BYTES:
        return SessionState(False,
                            'the file is %d bytes, the cap is %d'
                            % (size, MAX_SESSION_BYTES), size=size)

    try:
        with open(str(target), encoding='utf-8') as handle:
            document = json.load(handle)
    except (ValueError, UnicodeDecodeError) as exc:
        return SessionState(False, 'not readable JSON (%s)'
                            % type(exc).__name__, size=size)
    except OSError as exc:
        return SessionState(False, 'could not be read (%s)'
                            % type(exc).__name__, size=size)
    except RecursionError:
        # [REASON]: deeply nested JSON raises RecursionError, which is a
        # RuntimeError and not a ValueError. Without this the check would
        # crash instead of refusing.
        return SessionState(False, 'nests deeper than this reader walks',
                            size=size)

    if not isinstance(document, dict):
        return SessionState(False, 'decodes to %s, not to an object'
                            % type(document).__name__, size=size)

    cookies = document.get('cookies')
    if cookies is None:
        cookies = []
    if not isinstance(cookies, list):
        return SessionState(False, '"cookies" is %s, not a list'
                            % type(cookies).__name__, size=size)

    origins = document.get('origins')
    if origins is None:
        origins = []
    if not isinstance(origins, list):
        return SessionState(False, '"origins" is %s, not a list'
                            % type(origins).__name__, size=size)

    items = _count_local_storage(origins)
    state = SessionState(False, '', size=size, cookies=len(cookies),
                         origins=len(origins), local_storage_items=items)

    # [REASON]: a storage state with neither a cookie nor a localStorage item
    # is the exact shape Playwright writes for a context that was never signed
    # in. It is well-formed and useless, and treating it as a session is what
    # cost the first pilot its run.
    if not cookies and not items:
        state.reason = ('the state carries no cookie and no localStorage item '
                        '-- the browser was never signed in')
        return state

    state.usable = True
    return state


def session_exists(path):
    """True when the file carries an actual session.

    Kept under its old name because every caller in the package asks this
    question; what changed is the answer's basis -- content, not size.
    """
    return inspect_session(path).usable


def require_session(path):
    """Return the session path or raise SessionMissing naming the defect."""
    state = inspect_session(path)
    if state.usable:
        return Path(path)
    raise SessionMissing(
        'no usable DJI session at %s: %s (%s) -- run '
        '`python -m drone_collector.main --save-session` and sign in once by '
        'hand' % (Path(path), state.reason, state.describe()))


def login_url(records_url):
    """The sign-in URL on the same host as the records page."""
    parts = urlsplit(records_url)
    return urlunsplit((parts.scheme, parts.netloc, LOGIN_PATH, '', ''))


def expected_host(records_url):
    """Host configured for the records page, cleaned. Public, not a secret."""
    return clean_host(urlsplit(records_url).hostname)


def clean_host(host):
    """Lower-cased host without a trailing root dot. '' when there is none.

    [REASON]: `urlsplit().hostname` already lower-cases, but it KEEPS the
    trailing dot of a fully qualified name -- measured: the hostname of
    `https://DJIAG.COM./records/list` is `djiag.com.`, which compares unequal
    to `djiag.com` and would refuse a perfectly good page. Lower-casing is
    repeated here anyway so the function does not depend on which parser fed
    it.
    """
    if not host:
        return ''
    cleaned = host.strip().lower()
    if cleaned.endswith('.'):
        cleaned = cleaned[:-1]
    return cleaned


# Допустимая форма доменного имени: буквы, цифры, дефис, точка как разделитель
# (LDH), метки 1..63, имя целиком не длиннее 253.
HOST_FORM = re.compile(
    r'^(?=.{1,253}$)[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?'
    r'(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)*$')


def host_problem(netloc, host):
    """Почему это не годное ASCII-имя домена, либо '' если годное.

    `netloc` нужен СЫРЫМ и проверяется первым.

    [REASON]: порядок здесь не косметический, он измерен. `urlsplit().hostname`
    сам приводит имя к нижнему регистру, и при этом U+212A KELVIN SIGN
    превращается в обычную ASCII-букву `k`: у адреса
    `https://www.dji<U+212A>g.com/` netloc НЕ ascii, а hostname -- уже ascii.
    Проверка `hostname.isascii()` поэтому пропускает ровно тот случай, от
    которого защищает, и спрашивать надо netloc, до нормализации.

    [REASON]: punycode (`xn--...`) сознательно НЕ раскодируется. Гомоглиф
    вроде `xn--djig-73d.com` должен отклоняться как чужая строка, а не
    приводиться к виду конфигурационного хоста.
    """
    if netloc and not netloc.isascii():
        return 'the address is not a plain ASCII domain name'
    if netloc and '@' in netloc:
        # [REASON]: отказ безусловный, даже если host после '@' допустим.
        # Живой клик такого адреса не даёт, зато user-info -- это пароль, и
        # опираться на то, что Chromium его вычистит из page.url, нечем:
        # Playwright такой нормализации не обещает.
        return 'the address carries a user-info part before the host'
    if not host:
        return 'no host'
    if not HOST_FORM.match(host):
        return 'the host is not a well-formed domain name'
    return ''


def canonical_hosts(records_url):
    """The EXACTLY TWO hosts a signed-in records page may be served from.

    DJI answers both `www.djiag.com` and the apex `djiag.com` and redirects
    between them, so a check pinned to the configured spelling refuses a
    browser that the cabinet itself moved. The pair is built by toggling the
    `www.` label of the configured host and nothing else.

    [REASON]: this is SET MEMBERSHIP, never a suffix test. Measured against
    `urlsplit`, `hostname.endswith('djiag.com')` also accepts
    `evil-djiag.com`, `login.djiag.com` and `evil.www.djiag.com` -- three
    different hosts, none of them the cabinet. Two explicitly derived names
    cannot grow into a wildcard.

    [REASON]: the host is compared through `.hostname`, not `.netloc`. Again
    measured: the hostname of `https://www.djiag.com@evil.example/...` is
    `evil.example`, because the part before `@` is user-info, not a host. A
    netloc comparison reasons about a string that is not the origin the
    browser is actually on.

    A configured host that is neither apex nor www -- a staging cabinet, say
    -- keeps working: the pair is then {staging.djiag.com,
    www.staging.djiag.com}, which is the same one-label toggle and not a
    widening to anything unrelated.
    """
    host = clean_host(urlsplit(records_url).hostname)
    if not host:
        return frozenset()
    if host.startswith('www.'):
        bare = host[4:]
        return frozenset([host, bare]) if bare else frozenset([host])
    return frozenset([host, 'www.' + host])


def sanitize_url(url):
    """scheme://host/path for a log. Query and fragment are DROPPED.

    [REASON]: the query is not safe to print. The cabinet hands back an
    authorization code in it during the SSO redirect dance, and a diagnostic
    line that echoed `?code=...` would write a live credential into the log
    this module exists to keep clean. The path is kept because `/login` versus
    `/records/list` is the whole diagnosis; the query never is.
    """
    if not url:
        return '(no URL)'
    try:
        parts = urlsplit(url)
    except ValueError:
        return '(unparsable URL)'
    host = clean_host(parts.hostname)
    scheme = parts.scheme or '(no scheme)'
    if not host:
        return '%s://(no host)%s' % (scheme, parts.path or '/')
    try:
        port = parts.port
    except ValueError:
        port = None
        host += ':(bad port)'
    if port and port not in (80, 443):
        host = '%s:%d' % (host, port)
    return '%s://%s%s' % (scheme, host, parts.path or '/')


def landed_where_expected(current_url, records_url):
    """(ok, reason). The browser must be on the cabinet, not on /login.

    Neither check replaces the other, and BOTH must hold before a save.
    `inspect_session` catches the empty context on the right page;
    this catches the populated context on the WRONG page -- `/login` sets its
    own cookies and localStorage, so a sign-in form left open would sail
    through the structural check and overwrite a working session.

    Success requires all three: https, a host in `canonical_hosts`, and the
    exact configured path.
    """
    if not current_url:
        return False, 'the browser reported no URL'
    try:
        parts = urlsplit(current_url)
    except ValueError:
        return False, 'the browser reported a URL this reader cannot parse'
    wanted = urlsplit(records_url)

    # [REASON]: the scheme is checked, and checked against the configured one.
    # `http://` on the right host is a different security posture entirely --
    # cookies of a session saved off a plaintext page are cookies that
    # travelled in the clear -- and the old check looked only at the host.
    if parts.scheme != wanted.scheme:
        return False, ('the browser is on %s://, the records page is %s://'
                       % (parts.scheme or '(no scheme)',
                          wanted.scheme or '(no scheme)'))
    if parts.scheme != 'https':
        return False, 'the page is not served over https'

    allowed = canonical_hosts(records_url)
    host = clean_host(parts.hostname)
    problem = host_problem(parts.netloc, host)
    if problem:
        return False, 'the browser is somewhere this check refuses: %s' % problem
    if host not in allowed:
        # The host is printed; it is a public address, not a credential.
        return False, ('the browser is on %s, not on %s'
                       % (host or '(no host)',
                          ' or '.join(sorted(allowed)) or '(no host)'))

    # [REASON]: the port is checked separately from the host. `djiag.com:8443`
    # has hostname `djiag.com` -- it passes the host set and is a different
    # service. An explicitly configured port must match; otherwise only the
    # https default is allowed.
    # [REASON]: `443 if port is None else port`, а НЕ `port or 443`. Измерено:
    # у `https://djiag.com:0/...` свойство `.port` равно 0, и `or` тихо
    # подменял его на 443 -- адрес с портом 0 проходил проверку.
    try:
        port, wanted_port = parts.port, wanted.port
    except ValueError:
        return False, 'the browser reported a URL with an unreadable port'
    effective = 443 if port is None else port
    wanted_effective = 443 if wanted_port is None else wanted_port
    if effective != wanted_effective:
        return False, ('the browser is on port %s, the records page is on %s'
                       % (effective, wanted_effective))

    # [REASON]: the PATH is checked too, not just "it is not /login". The
    # cabinet has other pages -- `/mission`, the root -- and each of them sets
    # its own cookies, so "populated context on some page of the right host"
    # was never enough. A trailing slash and a query string are allowed: the
    # records page carries filters in the query by design.
    landed = (parts.path or '/').rstrip('/') or '/'
    expected = (wanted.path or '/').rstrip('/') or '/'
    if landed != expected:
        return False, ('the browser is on %s, not on the records page %s'
                       % (landed, expected))
    return True, ''


# ─── ожидание подтверждённого входа ─────────────────────────────────────────
#
# [REASON]: почему это отдельный чистый слой, а не работа с Playwright на
# месте. Playwright в среде разработки и в CI НЕ УСТАНОВЛЕН -- весь
# drone_collector импортирует его лениво именно поэтому. Логика, вплетённая
# в вызовы браузера, осталась бы непокрытой: ровно так и вышло с прежней
# проверкой, у которой не было ни одного теста на то, ЧТО она читает.
# Здесь политика (какой URL считается входом, сколько ждать) отделена от
# водопровода (откуда взялся список URL), поэтому проверяется фейками.

# Сколько ждать ручного входа. Человеку надо успеть ввести логин, пароль и
# код из SMS, поэтому счёт идёт на минуты, а не на секунды.
DEFAULT_LOGIN_WAIT_S = 600.0

# Переменная окружения, которой таймаут можно сократить или продлить.
LOGIN_WAIT_ENV = 'DJI_LOGIN_WAIT_S'


def login_wait_seconds(default=DEFAULT_LOGIN_WAIT_S):
    """Таймаут ожидания входа из окружения, иначе значение по умолчанию.

    [REASON]: настройка живёт ЗДЕСЬ, а не в `CollectorConfig`. Она относится
    к одной интерактивной команде, её незачем протаскивать через общую
    конфигурацию прогона и печатать в сводке рядом с окном сбора. Плюс
    практическая причина: без override десятиминутное ожидание пришлось бы
    выжидать и в тестах.

    [REASON]: мусор в переменной НЕ роняет команду и НЕ превращается в
    мгновенный таймаут. Оператор, написавший `DJI_LOGIN_WAIT_S=abc`, иначе
    получил бы отказ входа вместо отказа конфигурации, а причину искал бы в
    браузере.
    """
    raw = (os.environ.get(LOGIN_WAIT_ENV) or '').strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        log.warning('%s is not a number (%r ignored); waiting %.0f s',
                    LOGIN_WAIT_ENV, raw, default)
        return default
    if value <= 0:
        log.warning('%s must be positive (%r ignored); waiting %.0f s',
                    LOGIN_WAIT_ENV, raw, default)
        return default
    return value

# Как часто спрашивать у контекста его страницы.
LOGIN_POLL_S = 0.5


class WaitOutcome(object):
    """Чем закончилось ожидание входа. URL-ов целиком здесь нет."""

    __slots__ = ('ok', 'url', 'reason', 'polls', 'pages_seen', 'seen')

    def __init__(self, ok, url=None, reason='', polls=0, pages_seen=0,
                 seen=()):
        self.ok = ok
        self.url = url
        self.reason = reason
        self.polls = polls
        self.pages_seen = pages_seen
        # Санитизированные адреса последнего опроса: scheme://host/path.
        self.seen = tuple(seen)

    def describe(self):
        """Безопасная строка для лога: счётчики и только scheme/host/path."""
        return ('pages=%d polls=%d seen=[%s]'
                % (self.pages_seen, self.polls, ', '.join(self.seen)))

    def __repr__(self):
        return '<WaitOutcome ok=%s %s>' % (self.ok, self.describe())


def authorized_url(urls, records_url):
    """Первый адрес из `urls`, который является подтверждённой страницей вылетов.

    [REASON]: перебираются ВСЕ адреса, а не только первый. Кабинет открывает
    вход в отдельной вкладке того же контекста, и после успешного входа
    страница вылетов живёт в НОВОЙ page, тогда как исходная так и остаётся на
    `/login`. Прежняя проверка читала ровно ту исходную page -- поэтому
    оператор видел `/records/list`, а программа сообщала про `/login`.
    """
    for url in urls or ():
        ok, _why = landed_where_expected(url, records_url)
        if ok:
            return url
    return None


def wait_for_records_page(list_urls, records_url, pump,
                          timeout_s=DEFAULT_LOGIN_WAIT_S,
                          poll_s=LOGIN_POLL_S, clock=None):
    """Опрашивать `list_urls()` пока одна из страниц не станет страницей вылетов.

    `list_urls` -- вызываемое, возвращающее адреса ВСЕХ живых страниц
    контекста. Оно же решает, что делать с закрытыми страницами.

    `pump(seconds)` -- ОБЯЗАТЕЛЬНЫЙ параметр: пауза, которая одновременно
    даёт диспетчеру Playwright вычитать события из трубы драйвера.

    Возвращает WaitOutcome. Никогда не печатает и не возвращает query.

    [REASON]: почему `pump` обязателен и почему это НЕ `time.sleep`. В
    синхронном API `page.url` -- не запрос к браузеру, а чтение локального
    кэша `Frame._url`. Кэш обновляется только когда петля событий,
    живущая в greenlet-фибре того же потока, вычитает из трубы событие
    `navigated`; управление она получает исключительно внутри вызовов,
    обёрнутых `_sync()`. `time.sleep` таким вызовом НЕ является: он
    блокирует главный greenlet, фибра не запускается, и опрос крутится на
    замороженном снимке -- ровно то состояние, в котором прежний код
    навсегда видел `/login`. Значение по умолчанию здесь было бы
    ловушкой: код выглядел бы работающим и не работал, а тест с
    подставной паузой прошёл бы и на неверной реализации. Поэтому
    прокачку обязан передать вызывающий, и в production это
    `page.wait_for_timeout`.

    [REASON]: опрос, а не событие. `context.on('page')` сообщает о появлении
    страницы, но не о том, что она доехала до нужного адреса, а
    `page.wait_for_url` ждёт ОДНУ заранее известную страницу -- ту самую,
    которая в этом дефекте остаётся на `/login`. Человек может открыть
    вкладку когда угодно, поэтому опрашивается весь список.

    [REASON]: пустой список страниц -- это закрытое окно, и ждать дальше
    нечего. Без этого прогон висел бы до самого таймаута после того, как
    оператор закрыл браузер.
    """
    import time

    now = clock or time.monotonic
    nap = pump
    started = now()
    polls = 0
    seen = ()
    pages = 0

    while True:
        polls += 1
        # [REASON]: прокачка идёт ПЕРЕД первым чтением, а не только между
        # опросами. К моменту входа в петлю кэш уже устарел на всё время
        # ручного входа, и первое чтение без прокачки вернуло бы `/login`
        # даже когда оператор давно на странице вылетов.
        try:
            nap(poll_s)
        except Exception as exc:                               # noqa: BLE001
            return WaitOutcome(False, reason=('the browser stopped '
                                              'responding (%s)'
                                              % type(exc).__name__),
                               polls=polls, pages_seen=pages, seen=seen)
        try:
            urls = list(list_urls() or ())
        except Exception as exc:                               # noqa: BLE001
            return WaitOutcome(False, reason=('the browser could not be '
                                              'asked for its pages (%s)'
                                              % type(exc).__name__),
                               polls=polls, pages_seen=pages, seen=seen)
        pages = len(urls)
        seen = tuple(sanitize_url(u) for u in urls)
        found = authorized_url(urls, records_url)
        if found is not None:
            return WaitOutcome(True, url=found, polls=polls,
                               pages_seen=pages, seen=seen)
        if pages == 0:
            return WaitOutcome(False, reason='the browser has no open page '
                                             'left -- the window was closed',
                               polls=polls, pages_seen=0, seen=seen)
        if (now() - started) >= timeout_s:
            return WaitOutcome(False,
                               reason=('no page reached the records page '
                                       'within %.0f s' % timeout_s),
                               polls=polls, pages_seen=pages, seen=seen)


def context_page_urls(context):
    """Адреса живых страниц контекста. Закрытые пропускаются.

    [REASON]: `page.url` у закрытой страницы бросает, а закрытая вкладка --
    норма: кабинет закрывает попап входа сам. Одна закрытая страница не
    должна обрывать ожидание, поэтому она просто выпадает из списка.
    """
    urls = []
    for page in list(getattr(context, 'pages', None) or ()):
        try:
            if getattr(page, 'is_closed', None) and page.is_closed():
                continue
        except Exception:                                      # noqa: BLE001
            continue
        try:
            url = page.url
        except Exception:                                      # noqa: BLE001
            continue
        if url:
            urls.append(url)
    return urls


def make_pump(context):
    """Пауза, которая одновременно прокачивает диспетчер Playwright.

    Возвращает `pump(seconds)`. Пауза берётся у ЖИВОЙ страницы контекста
    через `page.wait_for_timeout`, потому что этот вызов обёрнут `_sync()` и
    потому отдаёт управление петле событий -- в отличие от `time.sleep`,
    который её не запускает вовсе.

    [REASON]: страница выбирается заново на каждый вызов, а не запоминается.
    Оператор вправе закрыть исходную вкладку и остаться во второй; вызов
    `wait_for_timeout` на закрытой странице бросает, и один такой бросок
    оборвал бы ожидание при живом и вошедшем браузере.

    [REASON]: если живых страниц не осталось, прокачивать нечего и пауза
    становится пустой -- петля на следующем же чтении увидит нулевой список
    и завершится «окно закрыто», вместо того чтобы выжидать таймаут.
    """
    def pump(seconds):
        for page in list(getattr(context, 'pages', None) or ()):
            try:
                if getattr(page, 'is_closed', None) and page.is_closed():
                    continue
                page.wait_for_timeout(max(0.0, float(seconds)) * 1000.0)
                return
            except Exception:                                  # noqa: BLE001
                continue
    return pump


def context_carries_session(context):
    """(usable, счётчики) -- есть ли в контексте пригодные cookie.

    Ранний и дружелюбный отказ до любой записи на диск. Итоговым гейтом
    остаётся `save_state_atomically`: он судит то, что Playwright реально
    записал, и до замены файла.

    [REASON]: спрашивается КОЛИЧЕСТВО, значения не читаются и не
    возвращаются. `context.cookies()` отдаёт живые cookie целиком, и они не
    должны попасть ни в лог, ни в объект, который кто-нибудь потом напечатает.
    """
    # [REASON]: сбой самого опроса НЕ отказывает. Эта проверка совещательная,
    # а итоговый гейт -- файловый (`save_state_atomically`), и он безопасен по
    # построению: прежняя сессия не гибнет ни в одной его ветке. Разовая
    # ошибка Playwright здесь сожгла бы вход, который оператор только что
    # делал руками, ради проверки, которая ничего не гарантирует сверх
    # файловой. Поэтому при ошибке пропускаем дальше, к настоящему гейту.
    try:
        cookies = context.cookies()
    except Exception as exc:                                   # noqa: BLE001
        return True, ('cookies could not be counted (%s); deferring to the '
                      'file gate' % type(exc).__name__)
    count = len(list(cookies or ()))
    if count:
        return True, 'cookies=%d' % count
    return False, ('the browser context holds no cookie at all -- the sign-in '
                   'did not complete')


def _remove_quietly(path):
    try:
        os.remove(str(path))
        return True
    except OSError:
        return False


def save_state_atomically(context, target, writer=None):
    """Write the context's state beside `target`, check it, then replace.

    Returns the SessionState of what was installed.

    [REASON]: Playwright used to be pointed straight at `storage_state.json`.
    That makes every save destructive: an empty context overwrites a working
    session with thirty bytes, and the operator finds out on the next run. Now
    the new state is written to a `.partial` beside the target, judged there,
    and only a usable state replaces the file. A useless one is deleted and
    the previous session survives untouched.
    """
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)

    handle = tempfile.NamedTemporaryFile(
        prefix=target.name + '.', suffix=TEMP_SUFFIX, dir=str(target.parent),
        delete=False)
    temp_name = handle.name
    handle.close()

    write = writer or (lambda path: context.storage_state(path=path))
    try:
        write(temp_name)
    except BaseException:
        _remove_quietly(temp_name)
        raise

    state = inspect_session(temp_name)
    if not state.usable:
        _remove_quietly(temp_name)
        previous = inspect_session(target)
        note = ('the previous session at %s was left untouched (%s)'
                % (target, previous.describe()) if previous.usable
                else 'there is no previous usable session to fall back on')
        raise SessionMissing(
            'the browser produced no usable session: %s (%s). Nothing was '
            'overwritten; %s' % (state.reason, state.describe(), note))

    try:
        os.replace(temp_name, str(target))
    except OSError:
        # [REASON]: без этого `.partial` оставался лежать рядом с сессией
        # после каждого отказа замены -- на Windows её умеет отклонить и
        # антивирус, и открытый чужим процессом файл. Каталог зарастал
        # полусохранёнными состояниями, которые никто не читает и никто не
        # убирает, а прежняя сессия при этом цела и работает.
        _remove_quietly(temp_name)
        raise
    return state


def save_session_interactive(cfg, print_fn=None, wait_s=None,
                             wait_fn=None):
    """Open a real browser window, wait for a manual sign-in, save the session.

    The program decides WHEN the sign-in finished by watching the browser, not
    by asking the operator to press Enter at the right moment.

    [REASON]: the Enter gate is gone, and that is the fix for the live defect
    of 13.09.2026. It read `page.url` of the ONE page it had created, once,
    at the instant Enter arrived. The cabinet finishes its sign-in in a new
    tab, so that page was still on `/login` while the operator was looking at
    `/records/list` -- exit code 2, no session saved, and nothing the operator
    could do about it except guess a better moment. Now every live page of the
    context is polled until one of them IS the records page.

    `print_fn`, `wait_s` and `wait_fn` are injectable so the flow can be
    exercised without a console and without a real clock.
    """
    from playwright.sync_api import sync_playwright  # lazy: see module docstring

    say = print_fn or print
    wait = wait_fn or wait_for_records_page
    timeout_s = login_wait_seconds() if wait_s is None else wait_s

    target = Path(cfg.storage_state)
    target.parent.mkdir(parents=True, exist_ok=True)
    url = login_url(cfg.records_url)
    allowed = ' or '.join(sorted(canonical_hosts(cfg.records_url)))

    say('')
    say('A browser window is opening on %s' % url)
    say('')
    say('  1. Sign in with the DJI account by hand.')
    say('  2. Wait until the records page loads and shows flights.')
    say('  3. Check the region selector: an accidental click on "Other')
    say('     Regions" switches the account to an empty country and the')
    say('     collector then quietly returns zero flights.')
    say('')
    say('You do NOT have to press anything. This program watches the browser')
    say('and saves the session by itself once a tab is on %s%s'
        % (allowed, urlsplit(cfg.records_url).path or '/'))
    say('It waits up to %.0f seconds, and gives up early if you close the'
        % timeout_s)
    say('window. A new tab opened by the sign-in counts too.')
    say('')
    say('Nothing you type in the browser is read, stored or logged by this')
    say('program. Only the resulting session cookies are saved, to %s'
        % target)
    say('')

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=False)
        context = browser.new_context(locale='en-US')
        page = context.new_page()
        page.set_default_timeout(cfg.page_timeout_ms)
        page.goto(url, wait_until=NAVIGATION_WAIT_UNTIL,
                  timeout=cfg.page_timeout_ms)

        outcome = wait(lambda: context_page_urls(context), cfg.records_url,
                       make_pump(context), timeout_s=timeout_s)

        if not outcome.ok:
            # [REASON]: this REFUSES, it does not warn. A warning let the save
            # go ahead, and `/login` is not an empty page: it sets its own
            # cookies and localStorage, so the structural check would pass it
            # and a sign-in form would overwrite a working session.
            #
            # [REASON]: the diagnostic carries COUNTS and sanitized
            # scheme://host/path only -- `sanitize_url` drops the query, which
            # is where the SSO hands back an authorization code.
            browser.close()
            raise SessionMissing(
                'the browser never reached a signed-in records page: %s (%s). '
                'Nothing was saved and nothing was overwritten -- sign in, '
                'wait for the records page, and run --save-session again.'
                % (outcome.reason, outcome.describe()))

        # [REASON]: the context is asked for cookies BEFORE anything is
        # written. The authoritative gate is still `save_state_atomically`,
        # which judges what Playwright actually produced and only then
        # replaces the file; this one fails earlier and says why in a sentence
        # an operator can act on.
        carries, note = context_carries_session(context)
        if not carries:
            browser.close()
            previous = inspect_session(target)
            raise SessionMissing(
                'the browser is on the records page but %s (%s). Nothing was '
                'saved and nothing was overwritten; %s'
                % (note, outcome.describe(),
                   'the previous session at %s was left untouched (%s)'
                   % (target, previous.describe()) if previous.usable
                   else 'there is no previous usable session to fall back on'))

        # Saved from the context, not the page: cookies set on the SSO host
        # during the redirect dance belong to the context too.
        try:
            state = save_state_atomically(context, target)
        finally:
            browser.close()

    log.info('Session saved to %s (%s; %s)', target, state.describe(),
             outcome.describe())
    say('')
    say('Signed in on %s' % sanitize_url(outcome.url))
    say('Session saved to %s' % target)
    say('  %s' % state.describe())
    say('Keep it out of the repository and out of tickets: it is a live login.')
    return target
