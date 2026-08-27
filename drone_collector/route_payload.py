# -*- coding: utf-8 -*-
"""drone_collector/route_payload.py -- что за тело прислал DJI на запрос
маршрутов, ДО того как его отдали protobuf-декодеру.

ЗАЧЕМ ЭТОТ МОДУЛЬ СУЩЕСТВУЕТ

Первый живой прогон этапа B (2026-08-27) получил на все девятнадцать пакетов
одно и то же: 135 байт JSON, а не `application/octet-stream`.

    {"status": 408, "code": 408, "msg": "...", "message": "...",
     "request_id": "..."}

Декодер protobuf, которому это отдали, честно отказался разбирать -- и прогон
записал девятнадцать «повреждённых тел» в бинарный карантин. Ни одно из них
повреждённым не было: это внятный служебный отказ поставщика, сказанный
по-человечески. Разница существенная: повреждённый protobuf -- повод изучать
байты, отказ поставщика -- повод остановиться и назвать причину.

`browser.py` знал это правило и раньше: конверт кабинета, снятый с двенадцати
настоящих ответов 2026-07-31, отвечает `{"status": 200, "code": 0}` на успех,
а на отказ `status` равен `code` -- 408 «негодное время», 101 «подписи нет».
Тракт маршрутов этого правила не применял.

ЧЕГО ЗДЕСЬ НЕТ И БЫТЬ НЕ ДОЛЖНО

`request_id` не читается, не сохраняется и не печатается. Ни один ключ, кроме
`status`, `code`, `msg` и `message`, отсюда не выходит: вердикт несёт числа,
короткий текст поставщика и хеш -- и больше ничего. Тело JSON никогда не
ложится на диск: неизвестно, что ещё кабинет положит в него завтра.
"""

import hashlib
import json
import re

# ─── Виды тела ───────────────────────────────────────────────────────────────

# Двоичное тело: его и ждёт protobuf-декодер.
PAYLOAD_BINARY = 'BINARY'
# Понятный служебный отказ кабинета: JSON-конверт с ненулевым `code`.
PAYLOAD_VENDOR_ERROR = 'JSON_VENDOR_ERROR'
# JSON-конверт с `code == 0`. Успех -- но маршруты приходят двоичными, значит
# форма ответа изменилась, и догадываться о ней нельзя.
PAYLOAD_VENDOR_OK = 'JSON_VENDOR_OK'
# JSON, не похожий на конверт кабинета: массив, объект без числового `code`.
PAYLOAD_JSON_UNKNOWN = 'JSON_UNKNOWN'
# JSON, который не разобрался.
PAYLOAD_JSON_UNREADABLE = 'JSON_UNREADABLE'
# Текст, начинающийся с `<`: страница ошибки, XML, что угодно -- но не protobuf.
PAYLOAD_TEXT_UNKNOWN = 'TEXT_UNKNOWN'
# Пусто.
PAYLOAD_EMPTY = 'EMPTY'
# Больше потолка: не разбирается вовсе.
PAYLOAD_TOO_LARGE = 'TOO_LARGE'
# В теле нашёлся маркер секрета.
PAYLOAD_SECRET = 'SECRET_IN_PAYLOAD'

# Виды, у которых тело НИКОГДА не ложится на диск.
#
# [REASON]: карантин заведён для двоичного тела, которого мы не поняли. JSON и
# текст мы понимаем достаточно, чтобы знать: в них бывает `request_id`, а
# завтра может появиться и что-то похуже. Сохранять их «на всякий случай» --
# ровно тот способ, которым идентификаторы переживают прогон.
NEVER_WRITTEN_KINDS = (PAYLOAD_VENDOR_ERROR, PAYLOAD_VENDOR_OK,
                       PAYLOAD_JSON_UNKNOWN, PAYLOAD_JSON_UNREADABLE,
                       PAYLOAD_TEXT_UNKNOWN, PAYLOAD_SECRET)

# ─── Пределы ─────────────────────────────────────────────────────────────────

# Потолок тела, которое вообще рассматривается.
MAX_PAYLOAD_BYTES = 32 * 1024 * 1024

# Потолок тела, которое пробуется как JSON.
#
# [REASON]: конверт отказа -- 135 байт. Мегабайт покрывает любой мыслимый
# служебный ответ и не даёт разбирать как JSON тридцатимегабайтное тело,
# случайно начавшееся с фигурной скобки.
MAX_JSON_BYTES = 1024 * 1024

# Сколько символов текста поставщика сохраняется.
#
# [REASON]: `请求时间无效` -- шесть иероглифов. Двести символов покрывают любое
# осмысленное сообщение; всё, что длиннее, это уже не сообщение, а полезная
# нагрузка, притворяющаяся сообщением.
MAX_MESSAGE_CHARS = 200

# Ключи, которые этот модуль читает. Больше ни один.
#
# [REASON]: список исчерпывающий и намеренно короткий. `request_id` в него не
# входит и входить не должен: у отказа он есть всегда, и единственный способ
# гарантированно его не напечатать -- никогда его не брать.
READ_KEYS = ('status', 'code', 'msg', 'message')

# Управляющие символы. Текст с ними не сохраняется вовсе.
_CONTROL = re.compile(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]')


class PayloadVerdict(object):
    """Чем оказалось тело. Значений, кроме короткого текста DJI, не несёт."""

    __slots__ = ('kind', 'bytes', 'sha256', 'status', 'code', 'message',
                 'detail', 'secret_markers')

    def __init__(self, kind, size, sha256, status=None, code=None,
                 message=None, detail='', secret_markers=()):
        self.kind = kind
        self.bytes = size
        self.sha256 = sha256
        self.status = status
        self.code = code
        self.message = message
        self.detail = detail
        self.secret_markers = list(secret_markers)

    @property
    def is_binary(self):
        return self.kind == PAYLOAD_BINARY

    @property
    def is_vendor_refusal(self):
        return self.kind == PAYLOAD_VENDOR_ERROR

    @property
    def body_may_be_written(self):
        return self.kind not in NEVER_WRITTEN_KINDS

    def as_dict(self):
        """Безопасная диагностика. `request_id` тут отсутствует по построению."""
        return {'kind': self.kind, 'bytes': self.bytes, 'sha256': self.sha256,
                'status': self.status, 'code': self.code,
                'message': self.message, 'detail': self.detail,
                'secret_markers': list(self.secret_markers)}

    def describe(self):
        parts = ['kind=%s' % self.kind, 'bytes=%d' % self.bytes]
        if self.status is not None:
            parts.append('status=%s' % self.status)
        if self.code is not None:
            parts.append('code=%s' % self.code)
        if self.message:
            parts.append('message=%r' % self.message)
        if self.detail:
            parts.append('detail=%s' % self.detail)
        return ' '.join(parts)

    def __repr__(self):
        return '<PayloadVerdict %s>' % self.describe()


def safe_message(value):
    """Короткий текст поставщика -- или None, если он не текст.

    Управляющие символы не «вычищаются», а отвергают строку целиком: текст, в
    котором они есть, не сообщение, и подчищенный до читаемости он выглядел бы
    безобиднее, чем есть.
    """
    if not isinstance(value, str) or not value:
        return None
    if _CONTROL.search(value):
        return None
    if len(value) > MAX_MESSAGE_CHARS:
        return value[:MAX_MESSAGE_CHARS]
    return value


def _as_int(value):
    """Целое -- или None. `True` целым не считается."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    return None


def _leading_byte(raw):
    """Первый непробельный байт или None."""
    for byte in raw[:64]:
        if byte not in (0x20, 0x09, 0x0a, 0x0d, 0xef, 0xbb, 0xbf):
            return byte
    return None


def classify_payload(raw, find_secret_markers=None,
                     max_bytes=MAX_PAYLOAD_BYTES):
    """Что это за тело. Ничего не поднимает и ничего не пишет.

    `find_secret_markers` внедряется, чтобы модуль не тянул очередь ради одной
    функции; по умолчанию берётся `drone_collector.outbox.find_secret_markers`.
    """
    if find_secret_markers is None:
        from drone_collector.outbox import find_secret_markers as _finder
        find_secret_markers = _finder

    raw = bytes(raw or b'')
    size = len(raw)
    digest = hashlib.sha256(raw).hexdigest()

    if size == 0:
        return PayloadVerdict(PAYLOAD_EMPTY, size, digest,
                              detail='the response body was empty')
    if size > max_bytes:
        return PayloadVerdict(PAYLOAD_TOO_LARGE, size, digest,
                              detail='%d bytes, the cap is %d'
                                     % (size, max_bytes))

    lead = _leading_byte(raw)
    if lead == ord('<'):
        return PayloadVerdict(PAYLOAD_TEXT_UNKNOWN, size, digest,
                              detail='the body starts with "<" -- markup, not '
                                     'a protobuf payload')
    if lead not in (ord('{'), ord('[')):
        return PayloadVerdict(PAYLOAD_BINARY, size, digest)

    if size > MAX_JSON_BYTES:
        return PayloadVerdict(PAYLOAD_JSON_UNKNOWN, size, digest,
                              detail='JSON-shaped but %d bytes, the JSON cap '
                                     'is %d' % (size, MAX_JSON_BYTES))

    try:
        # [REASON]: `utf-8-sig`, а не `utf-8`. Метку порядка байтов сниффер
        # пропускает при выборе ветки, и если её не снять здесь, `json.loads`
        # спотыкается о `﻿` -- внятный конверт отказа был бы объявлен
        # нечитаемым JSON и потерял бы свой код.
        text = raw.decode('utf-8-sig')
    except UnicodeDecodeError:
        return PayloadVerdict(PAYLOAD_BINARY, size, digest)

    markers = find_secret_markers(text)
    if markers:
        # Маркеры называются, значения -- нет.
        return PayloadVerdict(PAYLOAD_SECRET, size, digest,
                              detail='the body carries %s' % ', '.join(markers),
                              secret_markers=markers)

    try:
        document = json.loads(text)
    except ValueError as exc:
        return PayloadVerdict(PAYLOAD_JSON_UNREADABLE, size, digest,
                              detail='not readable JSON (%s)'
                                     % type(exc).__name__)
    except RecursionError:
        return PayloadVerdict(PAYLOAD_JSON_UNREADABLE, size, digest,
                              detail='nests deeper than this reader walks')

    if not isinstance(document, dict):
        return PayloadVerdict(PAYLOAD_JSON_UNKNOWN, size, digest,
                              detail='JSON %s, not an object'
                                     % type(document).__name__)

    code = _as_int(document.get('code'))
    status = _as_int(document.get('status'))
    message = safe_message(document.get('msg'))
    if message is None:
        message = safe_message(document.get('message'))

    if code is None:
        return PayloadVerdict(PAYLOAD_JSON_UNKNOWN, size, digest,
                              status=status, message=message,
                              detail='a JSON object with no numeric "code" -- '
                                     'not the cabinet envelope this reader '
                                     'knows')

    if code == 0:
        # [REASON]: успех, но НЕ маршруты. Маршруты приходят
        # `application/octet-stream`; JSON с `code 0` на этом эндпоинте
        # означает, что форма ответа изменилась. Догадываться о ней здесь --
        # ровно то, чего декодер не делает намеренно.
        return PayloadVerdict(PAYLOAD_VENDOR_OK, size, digest, status=status,
                              code=code, message=message,
                              detail='a JSON success envelope where a binary '
                                     'route payload was expected')

    return PayloadVerdict(PAYLOAD_VENDOR_ERROR, size, digest, status=status,
                          code=code, message=message,
                          detail='the cabinet refused the request')
