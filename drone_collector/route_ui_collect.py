# -*- coding: utf-8 -*-
"""drone_collector/route_ui_collect.py -- операторский сбор маршрутов.

DRONE-USEFUL-AREA-001, источник вертикали. Режим `--route-ui-collect`
слушает ШТАТНЫЙ запрос маршрутов, который кабинет DJI делает сам, когда
оператор открывает карту нужного дня, декодирует ответ и кладёт маршруты в
очередь. Отправку в Vehicle Soft делает отдельный явный флаг.

ПОЧЕМУ НАБЛЮДЕНИЕ, А НЕ ЗАПРОС

Собственный транспорт DJI опровергнут живым прогоном 2026-08-27: все
девятнадцать наших пакетов вернулись «negodnoe vremya», пока собственные
запросы страницы приносили 168 вылетов. Подпись покрывает строку запроса
целиком, воспроизвести её нельзя. Поэтому источник маршрута -- уже доказанный
UI response capture, и ничего нового здесь не ищется.

ЖИЗНЕННЫЙ ЦИКЛ НЕ ПЕРЕПИСЫВАЕТСЯ

`RouteQueueCapture` наследует `RouteUiProbe`, а не повторяет его. Цикл
`request -> response -> requestfinished/requestfailed` стоил треку двух живых
прогонов (`TargetClosedError` на всех пяти ответах; запрос, невидимый для
drain), и вторая реализация принесла бы обратно те же дефекты. Точка
расширения ровно одна -- `_decode_ids`, которому сырое тело достаётся уже
после классификации и проверки потолка размера.

ЧЕГО ЭТОТ РЕЖИМ НЕ ДЕЛАЕТ

Не сохраняет сырых тел, заголовков, cookie и подписанных ссылок. Не
инициирует запрос маршрутов сам. При `--dry-run` не ставит в очередь и не
отправляет ничего. Без явного флага отправки не обращается к Vehicle Soft,
даже когда URL и токен заданы.
"""

import hashlib
import json

from drone_collector.outbox import KIND_ROUTE
from drone_collector.route_ui_probe import RouteUiProbe, safe_exception_name

# Версия этого режима. Едет в диагностику записи очереди.
COLLECT_MODE_VERSION = 'route-ui-collect-1'


class RouteQueueCapture(RouteUiProbe):
    """Наблюдатель, который вдобавок ЗАПОМИНАЕТ разобранные маршруты.

    В памяти, без сырого тела. Сбой ЗАХВАТА не имеет права уронить
    НАБЛЮДЕНИЕ: наблюдение -- это подтверждение живого прогона, ради которого
    оператор сидит у браузера. Поэтому исключение здесь считается и
    называется по типу, а наблюдение продолжается.
    """

    def __init__(self, logger=None, expected_origin=None, clock=None):
        RouteUiProbe.__init__(self, logger=logger,
                              expected_origin=expected_origin, clock=clock)
        self.records = {}
        self.capture_errors = 0
        self.bodies_captured = 0
        self.decode_failures = 0

    def _decode_ids(self, raw):
        outcome = RouteUiProbe._decode_ids(self, raw)
        try:
            self._stash(raw)
        except Exception as exc:
            self.capture_errors += 1
            self.log.warning('The route body was observed but could not be '
                             'captured for the queue (%s); %d such failure(s) '
                             'so far', safe_exception_name(exc),
                             self.capture_errors)
        return outcome

    def _stash(self, raw):
        from drone_collector.route_decode import decode_route_response
        decoded = decode_route_response(raw)
        if not decoded.is_ok:
            # [REASON]: причина отказа декодера НЕ печатается вместе с телом и
            # не сохраняется. Считается только факт: тело не разобралось.
            self.decode_failures += 1
            return
        self.bodies_captured += 1
        for record in decoded.routes:
            if record.flight_id is None:
                continue
            if record.flight_id in self.records:
                # Тот же вылет во втором ответе -- не второй вылет.
                continue
            self.records[record.flight_id] = record

    def captured_records(self):
        """Маршруты в устойчивом порядке: по времени старта, затем по id."""
        return sorted(self.records.values(),
                      key=lambda item: (getattr(item, 'start_ms', None) or 0,
                                        getattr(item, 'flight_id', None) or 0))


def route_bodies(records, data_type, decoder_version):
    """[RouteRecord] -> [route_body] -- нормализованные тела для очереди.

    Формат строит `routes.route_body` и только он: приёмник
    /drones/api/route_sync читает именно эту форму, и вторая её сборка здесь
    разошлась бы с первой на первом же добавленном поле.
    """
    from drone_collector.routes import route_body
    return [route_body(record, data_type, decoder_version=decoder_version)
            for record in records]


def content_sha256(body):
    """Хеш тела маршрута -- ключ идемпотентности очереди.

    [REASON]: считается по СОДЕРЖИМОМУ маршрута, а не по всей записи. Запись
    несёт время постановки; хеш по ней менялся бы каждым прогоном, и повторное
    наблюдение того же дня клало бы в очередь второй файл того же маршрута.
    """
    text = json.dumps(body, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':'))
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def enqueue_routes(outbox, bodies, source=None, diagnostics=None):
    """Поставить маршруты в очередь. Возвращает (поставлено, дубликатов).

    Повторная постановка того же содержимого не создаёт второй записи --
    это свойство самой очереди (`dedupe_key`), и здесь оно только используется.
    """
    queued = duplicates = 0
    for body in bodies:
        identity = body.get('dji_flight_id')
        if identity is None:
            continue
        _path, was_duplicate = outbox.enqueue(
            KIND_ROUTE, identity, body, content_sha256(body),
            source=source, diagnostics=diagnostics or {})
        if was_duplicate:
            duplicates += 1
        else:
            queued += 1
    return queued, duplicates


# ─── Отправка очереди в Vehicle Soft ─────────────────────────────────────────

class DrainResult(object):
    """Что стало с очередью маршрутов за один прогон отправки."""

    __slots__ = ('envelopes', 'sent', 'left_pending', 'corrupt', 'counters')

    def __init__(self):
        self.envelopes = 0
        self.sent = 0
        self.left_pending = 0
        self.corrupt = 0
        self.counters = None

    def as_dict(self):
        base = {'envelopes': self.envelopes, 'sent': self.sent,
                'left_pending': self.left_pending, 'corrupt': self.corrupt}
        if self.counters is not None:
            base.update(self.counters.as_dict())
        return base


def drain_route_outbox(outbox, cfg, logger, send_fn=None):
    """Отправить `pending/` маршруты и перевести принятое в `sent/`.

    Правило судьбы записи -- ровно одно и оно важно:

    * пакет ПРИНЯТ -- каждая его запись переезжает в `sent/` и больше не
      отправляется;
    * ошибка сети или сервера -- записи ОСТАЮТСЯ в `pending/` и уедут
      следующим прогоном. Очередь для того и файловая, чтобы обрыв связи не
      стоил повторного похода оператора в кабинет;
    * нечитаемая запись уходит в `corrupt/` и не блокирует остальные.

    [REASON]: `mark_sent` вызывается ТОЛЬКО после успешного ответа приёмника,
    и никогда до. Обратный порядок -- пометить, потом отправить -- терял бы
    маршруты ровно в том случае, ради которого очередь существует.
    """
    from drone_collector.outbox import CorruptEnvelope
    from drone_collector.sender import send_routes

    send = send_fn or send_routes
    result = DrainResult()

    paths = [path for path in outbox.pending() if path.name.startswith('route_')]
    readable = []
    for path in paths:
        try:
            envelope = outbox.read(path)
        except CorruptEnvelope as exc:
            outbox.quarantine(path)
            result.corrupt += 1
            logger.error('Queue entry %s is unreadable (%s); moved to '
                         'corrupt/ and left out of this run.', path.name, exc)
            continue
        readable.append((path, envelope))

    result.envelopes = len(readable)
    if not readable:
        logger.info('The route queue holds nothing to send.')
        return result

    bodies = [envelope['body'] for _path, envelope in readable]
    counters = send(bodies, cfg, logger=logger)
    result.counters = counters

    for path, _envelope in readable:
        outbox.mark_sent(path)
        result.sent += 1
    result.left_pending = len(outbox.pending())
    return result
