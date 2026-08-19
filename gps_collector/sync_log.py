# -*- coding: utf-8 -*-
"""Журнал приёма: одна строка на прогон коллектора. Только stdlib.

ЗАЧЕМ
После ночного прогона из приложения не видно ничего: точки лежат в помесячных
файлах вне `transport.db`, и «коллектор отработал и ничего нового не нашёл»
выглядит ровно так же, как «коллектор не запускался». Разница между этими
двумя — это разница между спокойным утром и потерянными сутками истории.

ТОЖДЕСТВО, ПО ОБРАЗЦУ `drone_sync_logs`

    messages_seen = points_written + points_duplicate + points_no_position

Каждое сообщение попало ровно в одну корзину: легло точкой, отброшено ключом
как уже известное, или не имело координат вовсе. Прежний сборщик дронов всегда
писал `records_written == records_seen` — по такому журналу нельзя было
ответить, приехало ли что-нибудь новое. Здесь корзины раздельные, и их сумма
проверяется.

ЖУРНАЛ НИКОГДА НЕ ОСТАНАВЛИВАЕТ СБОР. Он пишется ПОСЛЕ прогона и в отдельной
короткой транзакции; любая его ошибка печатается и проглатывается. Точки
важнее записи о точках: упавший журнал не должен стоить ночи сбора.

[REASON]: пишет напрямую через `sqlite3`, а не через `models`. `app =
create_app()` вызывает `db.create_all()` на импорте, и коллектор — служба со
своим venv без Flask вовсе. Таблицу создаёт миграция `GPS_SYNC_LOG_001`;
здесь её только наполняют.
"""

import os
import sqlite3
from datetime import datetime

from . import config

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(ROOT, "instance", "transport.db")

STATUS_OK = "ok"
STATUS_PARTIAL = "partial"
STATUS_ERROR = "error"


def _stamp(value):
    if value is None:
        return None
    return datetime.fromtimestamp(int(value), config.TZ).strftime(
        "%Y-%m-%d %H:%M:%S")


def status_for(summary, error=None):
    """Как закончился прогон: ok / partial / error.

    `partial` — часть объектов не прочитана. Это не отказ (их интервалы
    остались за watermark и приедут следующей ночью), но и не «всё хорошо»:
    если объект не читается неделю, это видно только по такой пометке.
    """
    if error is not None:
        return STATUS_ERROR
    if summary.units_failed:
        return STATUS_PARTIAL
    return STATUS_OK


def record(summary, started_at, finished_at, client=None, error=None,
           db_path=None, log=print):
    """Записать строку прогона. Возвращает True, если запись удалась.

    Ошибка записи не поднимается наверх: смысл журнала в том, чтобы объяснить
    прогон, а не в том, чтобы его сорвать.
    """
    path = db_path or DB_PATH
    try:
        if not os.path.exists(path):
            log("    zhurnal priyoma ne zapisan: net %s" % path)
            return False
        con = sqlite3.connect(path, timeout=30)
        try:
            present = con.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name='gps_sync_log'").fetchone()
            if not present:
                log("    zhurnal priyoma ne zapisan: net tablicy gps_sync_log "
                    "-- migraciya GPS_SYNC_LOG_001 ne primenena")
                return False
            con.execute(
                "INSERT INTO gps_sync_log ("
                "started_at, finished_at, status, units_total, units_asked, "
                "units_silent, units_uptodate, units_empty, units_failed, "
                "messages_seen, points_written, points_duplicate, "
                "points_no_position, requests, logins, abandoned, truncated, "
                "interval_from, interval_to, error) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (started_at.strftime("%Y-%m-%d %H:%M:%S"),
                 finished_at.strftime("%Y-%m-%d %H:%M:%S"),
                 status_for(summary, error),
                 summary.units_total, summary.units_asked, summary.units_silent,
                 summary.units_uptodate, summary.units_empty,
                 summary.units_failed, summary.points_seen,
                 summary.points_written, summary.points_duplicate,
                 summary.points_no_position,
                 getattr(client, "requests", 0) if client else 0,
                 getattr(client, "logins", 0) if client else 0,
                 getattr(client, "abandoned", 0) if client else 0,
                 summary.truncated,
                 _stamp(summary.watermark_from), _stamp(summary.watermark_to),
                 None if error is None else str(error)[:500]))
            con.commit()
        finally:
            con.close()
        return True
    except Exception as problem:                                   # noqa: BLE001
        # [REASON]: сюда попадает и заблокированная база, и отсутствующая
        # колонка, и что угодно ещё. Ни одно из этого не стоит ночи сбора.
        log("    zhurnal priyoma ne zapisan: %s" % type(problem).__name__)
        return False
