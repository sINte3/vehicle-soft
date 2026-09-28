# -*- coding: utf-8 -*-
"""Постоянные и пути импорта agro-work. Только stdlib.

Каждое число здесь либо измерено в B0 (`docs/tracks/agro-work.md`, раздел 5),
либо взято у соседнего тракта с тем же обоснованием, и сказано, откуда.
"""

import os
from datetime import timedelta, timezone

# [REASON]: вывод в консоль только ASCII (устав): на cp1251 служебного журнала
# узбекская кириллица роняет прогон. Файлы (CSV, xlsx) хранят настоящее
# написание, консоль получает транслитерацию -- ту же, что у тракта GPS, а не
# вторую копию таблицы, которая разошлась бы с первой. gps_collector -- только
# stdlib, зависимости он сюда не приносит.
from gps_collector.config import ascii_only  # noqa: F401

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(ROOT, 'instance', 'transport.db')

BASE_URL = os.environ.get('AGRO_WORK_BASE_URL',
                          'https://api.agro-work.uz/api/v1')

# [REASON]: весь проект считает сутки по местному времени (UTC+5), и суточные
# агрегаты GPS лежат по местной дате. `created_at` agro-work приходит с поясом
# +05:00 (B0, у всех 4 785 заявок), но день берётся переводом в этот пояс, а
# не отрезанием строки: иначе ответ в UTC однажды молча сдвинул бы вечерние
# заявки на сутки.
TZ = timezone(timedelta(hours=5))

# [REASON]: B0 -- `page_size` больше 100 их API молча урезает до 100. Просить
# больше бессмысленно, а просить меньше -- лишние запросы к чужому серверу.
PAGE_SIZE = 100

# [REASON]: владелец 28.09: «бережно к их серверу, паузы между запросами».
# Две секунды -- тот же темп, что держит тракт Wialon после 14.08 (решение G9
# трека GPS): там ~2000 запросов за 40 минут совпали с потерей доступа, и
# проверять, выдержит ли agro-work больше, на их сервере нельзя. Полный обход
# списка -- около 55 запросов, то есть две минуты.
PAUSE_S = 2.0

# [REASON]: первичная загрузка истории статусов -- около 3 900 запросов
# (выполненные и отменённые, раздел 5.7). Владелец велел её растянуть: за один
# прогон берётся не больше этого числа, остальное -- следующими прогонами.
# 300 запросов при паузе 2 с -- десять минут.
MAX_HISTORY_PER_RUN = 300

# [REASON]: предел числа страниц одного списка. Нормальный обход -- 48
# страниц; ответ, у которого `next` не кончается никогда, не должен держать
# прогон сутки и нагружать их сервер.
MAX_PAGES = 500

# [REASON]: urlopen ограничивает время одной операции сокета, а не запроса
# целиком: пир, отдающий байты по одному, держит чтение вечно. Тракт Wialon
# на этом простоял сутки 16.08 -- здесь тот же жёсткий срок на весь запрос.
TIMEOUT = 60
HARD_DEADLINE_S = 150.0
RETRIES = 3
RETRY_PAUSE_S = (5, 15, 45)
# Сколько секунд из Retry-After мы готовы ждать. Больше -- прогон
# останавливается: сервер просит паузу длиннее, чем разумно держать процесс.
MAX_RETRY_AFTER_S = 120

# [REASON]: страница из 100 заявок -- порядка сотни килобайт. Ответ в десятки
# мегабайт означает, что спросили не то или отвечают не тем, и читать его в
# память целиком незачем.
MAX_BODY_BYTES = 20 * 1024 * 1024

CREDENTIALS_FILE = 'agro_work_credentials.txt'
CREDENTIALS_ENV = 'AGRO_WORK_CREDENTIALS'

# Ключи файла учётных данных. Все, кроме двух первых, необязательны.
CREDENTIAL_KEYS = ('login', 'password', 'login_field', 'auth_scheme')
DEFAULT_LOGIN_FIELD = 'username'
DEFAULT_AUTH_SCHEME = 'Bearer'


class CredentialsError(Exception):
    """Файла нет, он пуст или записан не так. Текст -- без значений."""


def credentials_candidates(explicit=None):
    """Где ищется файл: явный путь, переменная окружения, рабочая папка,
    корень репозитория -- в этом порядке. Тот же порядок, что у
    `wialon_token.txt` (`gps_collector/config.read_token`), плюс явный путь:
    на сервере файл лежит вне любой рабочей копии git.
    """
    if explicit:
        return [explicit]
    out = []
    env_path = os.environ.get(CREDENTIALS_ENV, '').strip()
    if env_path:
        out.append(env_path)
    for folder in (os.getcwd(), ROOT):
        path = os.path.join(folder, CREDENTIALS_FILE)
        if path not in out:
            out.append(path)
    return out


def _read_text(path):
    with open(path, 'rb') as fh:
        raw = fh.read()
    # [REASON]: Блокнот на сервере пишет то UTF-8 с BOM, то ANSI (cp1251) --
    # зависит от версии Windows. Пароль из латиницы одинаков в обеих, а
    # кириллический в cp1251 иначе пришёл бы искажённым.
    try:
        return raw.decode('utf-8-sig')
    except UnicodeDecodeError:
        return raw.decode('cp1251')


def parse_credentials(text):
    """Строки `ключ=значение`. Пустые строки и строки с `#` пропускаются.

    [REASON]: незнакомый ключ -- отказ, а не пропуск. Опечатка `pasword=`
    иначе превратилась бы в «пароль не задан» на входе в чужую систему, и
    причину пришлось бы угадывать. В тексте ошибки -- только имя ключа и номер
    строки, никогда значение.
    """
    values = {}
    for number, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith('#'):
            continue
        key, sep, value = stripped.partition('=')
        key = key.strip().lower()
        if not sep:
            raise CredentialsError('line %d has no "=": expected key=value'
                                   % number)
        if key not in CREDENTIAL_KEYS:
            raise CredentialsError('line %d: unknown key "%s" (known: %s)'
                                   % (number, key.encode('ascii', 'replace')
                                      .decode('ascii'),
                                      ', '.join(CREDENTIAL_KEYS)))
        if key in values:
            raise CredentialsError('line %d: key "%s" given twice'
                                   % (number, key))
        values[key] = value.strip()
    for key in ('login', 'password'):
        if not values.get(key):
            raise CredentialsError('key "%s" is missing or empty' % key)
    values.setdefault('login_field', DEFAULT_LOGIN_FIELD)
    values.setdefault('auth_scheme', DEFAULT_AUTH_SCHEME)
    if not values['login_field']:
        values['login_field'] = DEFAULT_LOGIN_FIELD
    if not values['auth_scheme']:
        values['auth_scheme'] = DEFAULT_AUTH_SCHEME
    return values


def read_credentials(explicit=None):
    """Учётные данные agro-work. Возвращает (словарь, путь). Не печатает.

    Путь в ответе -- чтобы инструмент мог сказать, КАКОЙ файл он прочёл:
    путь секретом не является, содержимое -- является.
    """
    tried = credentials_candidates(explicit)
    for path in tried:
        if os.path.isfile(path):
            return parse_credentials(_read_text(path)), path
    raise CredentialsError('credentials file not found; looked at: %s'
                           % '; '.join(tried))
